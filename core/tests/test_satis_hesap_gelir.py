"""Satış faturası "Hesap satırı"nda GELİR hesapları (60x / 64x yaprak): ihracat navlunu 601 vb. Satır hesaba ALACAK, KDV satırın oranına göre, cari borç tüm satırların toplamı,
dövizde TL karşılığı aynı kurla + döviz tutarı; alış tiplerinde liste/izin değişmez; mevcut gider/258 hesapları kalır."""
from decimal import Decimal

from django.urls import reverse

from core.forms import FaturaSatirForm
from core.models import Birim, Cari, FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur, Stok
from core.services import fatura as fs
from core.services.hesap_plani import gelir_hesaplari
from core.tests.test_banka_hesap_hareketi import _hesap
from core.tests import test_uretim_siparisi_kapanis as kt
from core.tests.test_satis_hesap_demirbas import D, Dc, SatisHesapDemirbasTestBase


class GelirHesapSatiriTest(SatisHesapDemirbasTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for kod, ad in (("600", "YURTİÇİ SATIŞLAR"), ("601", "YURTDIŞI SATIŞLAR"), ("602", "DİĞER GELİRLER"), ("610", "SATIŞTAN İADELER (-)")):
            _hesap(kod, ad, kalem="A", grup="GELIR_TABLOSU")
        _hesap("120.03", "ALICI EUR")
        Kur.objects.create(tarih=D(2026, 5, 5), usd_alis=Dc("30"), eur_alis=Dc("35"))
        cls.ihracat = FaturaTipi.objects.create(ad="SATIŞ FATURASI-İHRACAT", yon=FaturaTipi.Yon.SATIS)
        cls.kayitli = FaturaTipi.objects.create(ad="SATIŞ FATURASI-İHRAÇ KAYITLI", yon=FaturaTipi.Yon.SATIS)
        cls.cari_eur = Cari.objects.create(kod="C3", unvan="ALICI EUR", muhasebe_kodu="120.03", para_birimi="EUR")
        kat = Kategori.objects.create(ad="MAMUL TEST", kod="mt")
        birim = Birim.objects.create(ad="ADET X", kisa_ad="AX", ondalik=0)
        cls.stok = Stok.objects.create(kod="G-1", ad="SATILAN ÜRÜN", kategori=kat, kdv=cls.kdv20, uretim_birimi=birim, fatura_birimi=birim)
        for tip in (cls.satis, cls.ihracat, cls.kayitli):
            KategoriHesap.objects.create(kategori=kat, fatura_tipi=tip, hesap=HesapPlani.objects.get(hesap_kodu="600"))

    def test_gelir_hesaplari_kumesi_yaprak_60x_64x(self):
        kodlar = list(gelir_hesaplari().values_list("hesap_kodu", flat=True))
        self.assertEqual(kodlar, ["600", "601", "602", "649"])                                         # 610 (60x/64x dışı) ve gider hesapları yok
        self.assertTrue({"600", "601", "649"} <= set(fs._satis_hesap_kumesi().values_list("hesap_kodu", flat=True)))
        self.assertTrue({"770.01", "258"} <= set(fs._satis_hesap_kumesi().values_list("hesap_kodu", flat=True)))   # mevcut hesaplar kalır

    def test_ihracat_navlun_601_alacak_kdv_sifir_cari_borc(self):
        f = self._fatura([self._hesap_satir("601", "250", self.kdv0)], tip=self.ihracat)
        s = self._fis(f)
        self.assertEqual(s[("601", "A")][0], Dc("250.00"))
        self.assertEqual(s[("120.01", "B")][0], Dc("250.00"))
        self.assertEqual(len(s), 2)                                                                    # KDV satırı yok (%0)

    def test_stok_ve_gelir_hesap_satiri_cari_borc_toplami_ve_kdv(self):
        satirlar = [{"stok_id": self.stok.pk, "miktar": "2", "birim_fiyat": "100"},
                    self._hesap_satir("601", "50", self.kdv20)]
        f = self._fatura(satirlar, tip=self.satis)
        s = self._fis(f)
        self.assertEqual(s[("600", "A")][0], Dc("200.00"))                                             # stok satırı 600'e
        self.assertEqual(s[("601", "A")][0], Dc("50.00"))                                              # hesap satırı seçilen hesaba, aynı yön
        self.assertEqual(s[("391.20", "A")][0], Dc("50.00"))                                           # KDV: stok %20 (40) + hesap %20 (10)
        self.assertEqual(s[("120.01", "B")][0], Dc("300.00"))                                          # cari borç = stok + hesap + KDV

    def test_doviz_eur_tl_karsiligi_ayni_kurla_ve_doviz_tutari(self):
        f = self._fatura([self._hesap_satir("601", "100", self.kdv0)], tip=self.ihracat, cari=self.cari_eur, pb="EUR", tarih=D(2026, 5, 5))
        s = self._fis(f)
        self.assertEqual(s[("601", "A")], (Dc("3500.00"), "EUR", Dc("100.00")))                         # TL = 100 × 35; döviz tutarı tutuldu
        self.assertEqual(s[("120.03", "B")], (Dc("3500.00"), "EUR", Dc("100.00")))
        self.assertEqual(f.kur, Dc("35"))

    def test_649_ve_kayitli_tip(self):
        f = self._fatura([self._hesap_satir("649", "40", self.kdv0)], tip=self.kayitli)
        self.assertEqual(self._fis(f)[("649", "A")][0], Dc("40.00"))

    def test_alis_tiplerinde_gelir_hesabi_secilemez_ve_liste_degismez(self):
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("601", "10", self.kdv0)], tip=self.alis, cari=self.satici)          # alışta hesap satırı hiç yok
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("601", "10", self.kdv0)], tip=self.gider, cari=self.satici)         # gider faturasında gelir hesabı yok
        alis = FaturaSatirForm(yon="ALIS").fields["hesap"].queryset.values_list("hesap_kodu", flat=True)
        self.assertNotIn("601", alis)
        self.assertNotIn("649", alis)
        self.assertIn("770.01", alis)

    def test_gelir_disi_hesap_satista_reddedilir(self):
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("610", "10", self.kdv0)], tip=self.satis)                           # 610 gelir listesinde değil
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("253", "10", self.kdv0)], tip=self.satis)                           # duran varlık hesabı da değil

    def test_form_secenekleri_ve_etiket_satista_gelir_grubu(self):
        alan = FaturaSatirForm(yon="SATIS").fields["hesap"]
        self.assertEqual(alan.empty_label, "— hesap seç —")
        gruplar = {g: [kod for _pk, kod in ops] for g, ops in alan.widget.choices if isinstance(ops, list)}
        self.assertTrue(any("Gelir" in g for g in gruplar))
        gelir = next(v for g, v in gruplar.items() if "Gelir" in g)
        self.assertTrue(any(x.startswith("601") for x in gelir))
        self.assertTrue(any(x.startswith("649") for x in gelir))
        self.assertTrue(any("770.01" in x for v in gruplar.values() for x in v))                        # gider hesapları da duruyor

    def test_fatura_ekle_ekrani_601_listeler(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:satis_fatura_ekle"))
        self.assertContains(r, "601  YURTDIŞI SATIŞLAR")
        self.assertContains(r, "— hesap seç —")
        self.assertNotContains(self.client.get(reverse("core:alis_fatura_ekle")), "601  YURTDIŞI SATIŞLAR")     # alış ekranında gelir hesabı yok


class FaturayaCevirGelirHesapTest(kt.KapanisTaban):
    """Sipariş→Fatura ekranı (GET) satır formunu SATIŞ yönüyle kurar: hesap satırı listesinde 60x/64x gelir hesapları görünür (eskiden yön verilmediği için alış/gider listesi çıkıyordu)."""

    def setUp(self):
        super().setUp()
        _hesap("601.10", "YURTDIŞI SATIŞLAR TEST", kalem="A", grup="GELIR_TABLOSU")
        _hesap("649", "DİĞER OLAĞAN GELİRLER", kalem="E", grup="GELIR_TABLOSU")

    def test_get_formunda_gelir_hesaplari_ve_etiket(self):
        sip = self.siparis("3")
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "601.10  YURTDIŞI SATIŞLAR TEST")
        self.assertContains(r, "649  DİĞER OLAĞAN GELİRLER")
        self.assertContains(r, "Gelir Hesapları (60x / 64x)")
        self.assertContains(r, "— hesap seç —")
        self.assertNotContains(r, "Ortak Hesapları (şahsi alış)")                                      # alış/gider seçenek grupları satış formunda yok

    def test_post_navlun_hesap_satiri_601_ile_fatura_kesilir(self):
        sip = self.siparis("3")
        h601 = HesapPlani.objects.get(hesap_kodu="601.10")
        r = self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28", "para_birimi": "TRY", "depo": "",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "50", "form-0-kdv": self.kdv.pk,
            "form-1-tur": "HESAP", "form-1-hesap": h601.pk, "form-1-miktar": "1", "form-1-birim_fiyat": "40", "form-1-kdv": self.kdv.pk}, follow=True)
        sip.refresh_from_db()
        self.assertIsNotNone(sip.fatura_id, [str(m) for m in r.context["messages"]] if r.context else r.content[:300])
        satirlar = {s.hesap.hesap_kodu if s.hesap_id else "STOK": s.tutar for s in sip.fatura.satirlar.filter(silindi=False)}
        self.assertEqual(satirlar["601.10"], Decimal("40"))
