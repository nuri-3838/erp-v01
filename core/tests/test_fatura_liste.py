"""Faturalar › Alış/Satış liste ekranı yeniden tasarımı (2026-10-15): kolonlar + TL karşılığı, "Yalnız KDV" gösterimi, tip rozetleri, FIFO ödeme durumu (kapandı / kısmi / açık / vadesi geçti,
tek fonksiyon: core.services.fatura_odeme), hızlı filtre çipleri, cari/PB/tarih/arama süzgeçleri (URL), sütun sıralaması, sayfa boyutu, filtrelenmiş toplam satırı, PDF ikonu, Excel,
sabit sorgu sayısı, yetki. Liste SALT-OKUNUR: fiş/stok/cari kaydı değişmez."""
import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import connection
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from core.models import Cari, EkranYetki, Fatura, FaturaEk, FaturaTipi, StokHareket, YevmiyeFisi, YevmiyeSatir
from core.services import cari_ozet, fatura_liste as fl, fatura_odeme
from core.services.fatura import fatura_olustur, fatura_taslak_olustur
from core.services.finans import kasa_olustur
from core.services.kasa_hareket import hareket_olustur as kasa_hareket
from core.tarih import tr_bugun
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, png_bayt
from core.tests.test_fatura import FaturaTestTemel, _hesap

D = datetime.date
Dc = Decimal


class ListeTaban(OzelDizinTestTemel, FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.su = User.objects.create_superuser("ftlist", password="x")
        _hesap("100.01", "KASA")
        _hesap("320.10.0002", "USD TEDARİKÇİ", kalem="KVYK")
        cls.kasa = kasa_olustur(ad="K1", muhasebe_kodu="100.01")
        Cari.objects.filter(pk=cls.tedarikci.pk).update(kisa_ad="TEDARİK A")
        cls.tedarikci.refresh_from_db()
        cls.usd = Cari.objects.create(kod="320-10-0002", unvan="USD TEDARİKÇİ LİMİTED ŞİRKETİ", para_birimi="USD", muhasebe_kodu="320.10.0002")
        cls.kdvli = FaturaTipi.objects.create(ad="ALIŞ FATURASI-KDV", yon=FaturaTipi.Yon.ALIS, yalniz_kdv=True)
        for gun in range(1, 32):
            from core.models import Kur
            Kur.objects.get_or_create(tarih=D(2026, 3, gun), defaults={"usd_alis": Dc("30")})

    def a_fatura(self, tarih=D(2026, 3, 10), miktar="10", fiyat="100", no="", cari=None, **kw):
        return fatura_olustur(tip_id=self.alis.pk, cari_id=(cari or self.tedarikci).pk, tarih=tarih, fatura_no=no,
                              satirlar=self._satir(miktar, fiyat), **kw)

    def odeme(self, tutar, tarih=D(2026, 3, 20)):
        kasa_hareket(kasa=self.kasa, tip="cari_odeme", karsi=self.tedarikci, tutar=tutar, tarih=tarih)

    def liste(self, yon="alis", **q):
        self.client.force_login(self.su)
        return self.client.get(reverse(f"core:{yon}_faturalari"), q)

    def satirlar(self, **q):
        return {s["pk"]: s for s in self.liste(**q).context["satirlar"]}


class SatirGosterimTest(ListeTaban):
    def test_kolonlar_kisa_ad_tl_karsiligi_ve_fatura_no(self):
        f = self.a_fatura(no="A-100")
        r = self.liste()
        self.assertContains(r, "A-100")
        self.assertContains(r, 'title="TEDARİKÇİ A"')                         # üzerine gelince tam ünvan
        self.assertContains(r, ">TEDARİK A<")                                 # kısa ad
        s = r.context["satirlar"][0]
        self.assertEqual((s["matrah_tl"], s["kdv_tl"], s["toplam"], s["toplam_tl"]), (Dc("1000.00"), Dc("200.00"), Dc("1200.00"), Dc("1200.00")))
        self.assertEqual(s["tip_etiket"], "Alış")
        self.assertEqual(s["pk"], f.pk)

    def test_doviz_faturada_toplam_doviz_ustte_tl_karsiligi_altta(self):
        f = self.a_fatura(miktar="1", fiyat="100", cari=self.usd, para_birimi="USD")
        r = self.liste()
        s = r.context["satirlar"][0]
        self.assertEqual((s["pb"], s["matrah"], s["toplam"]), ("USD", Dc("100.00"), Dc("120.00")))
        self.assertEqual((s["matrah_tl"], s["kdv_tl"], s["toplam_tl"]), (Dc("3000.00"), Dc("600.00"), Dc("3600.00")))   # fatura kuru 30
        self.assertContains(r, "120,00 USD")
        self.assertContains(r, "≈ 3.600,00 TL")
        self.assertEqual(f.kur, Dc("30"))
        self.assertContains(r, "USD TEDARİKÇİ LİMİTED ŞİRKETİ")                # kısa ad yoksa ünvan

    def test_yalniz_kdv_matrah_tire_toplam_yalniz_kdv_ve_rozet(self):
        f = fatura_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="K-1", satirlar=self._satir())
        r = self.liste()
        s = r.context["satirlar"][0]
        self.assertEqual((s["tip_etiket"], s["yalniz_kdv"], s["matrah"], s["matrah_tl"], s["kdv_tl"], s["toplam"], s["toplam_tl"]),
                         ("KDV", True, None, None, Dc("200.00"), Dc("200.00"), Dc("200.00")))            # 12.000 değil 2.000'lik KDV
        self.assertContains(r, "Yalnız KDV")
        self.assertNotContains(r, "1.200,00")
        self.assertEqual(f.genel_toplam, Dc("1200.00"))                                               # belge toplamı değişmedi; yalnız liste gösterimi

    def test_tip_rozetleri(self):
        def t(**kw):
            return fl.tip_rozeti(FaturaTipi(ad=kw.pop("ad", "X"), yon=kw.pop("yon", "ALIS"), **kw), kw.get("yon", "ALIS"))[0]
        self.assertEqual(t(), "Alış")
        self.assertEqual(fl.tip_rozeti(FaturaTipi(ad="SATIŞ FATURASI", yon="SATIS"), "SATIS")[0], "Satış")
        self.assertEqual(t(gider=True), "Gider")
        self.assertEqual(t(yalniz_kdv=True), "KDV")
        self.assertEqual(t(maliyet_fisi="ALIS_IADE", yon="SATIS"), "İade")
        self.assertEqual(t(ad="ALIŞ FATURASI-İHRAÇ KAYITLI"), "İhraç")
        self.assertEqual(fl.tip_rozeti(FaturaTipi(ad="SATIŞ FATURASI-İHRACAT", yon="SATIS"), "SATIS")[0], "İhraç")
        self.assertEqual(t(gider=True, stopajli=True, ad="SERBEST MESLEK MAKBUZU (SMM)"), "SMM")
        self.assertEqual(fl.tip_rozeti(None, "ALIS")[0], "Tip yok")

    def test_karma_faturada_stok_hesap_alt_etiketi(self):
        from core.models import HesapPlani
        _hesap("770.01", "GENEL GİDER", kalem="F")
        tip = FaturaTipi.objects.get(ad="ALIŞ FATURASI")
        satirlar = self._satir() + [{"tur": "HESAP", "hesap_id": "770.01", "miktar": "1", "birim_fiyat": "50", "kdv_id": self.kdv.pk}]
        from core.models import Depo
        depo = Depo.objects.create(kod="D1", ad="DEPO")
        f = fatura_olustur(tip_id=tip.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=satirlar, depo_id=depo.pk)
        s = self.liste().context["satirlar"][0]
        self.assertTrue(s["karma"])
        self.assertEqual(s["satir_alt"], "Stok+Hesap")
        self.assertContains(self.liste(), "Stok+Hesap")
        self.assertEqual(f.pk, s["pk"])

    def test_pdf_ikonu_yalniz_pdf_eki_olan_faturada(self):
        f1, f2, f3 = self.a_fatura(no="P-1"), self.a_fatura(no="P-2", tarih=D(2026, 3, 11)), self.a_fatura(no="P-3", tarih=D(2026, 3, 12))
        ek = FaturaEk.objects.create(fatura=f1, dosya=ContentFile(PDF_BAYT, name="a.pdf"), orijinal_ad="a.pdf")
        FaturaEk.objects.create(fatura=f2, dosya=ContentFile(png_bayt(), name="b.webp"), orijinal_ad="b.webp")      # resim: PDF sayılmaz
        r = self.liste()
        self.assertContains(r, reverse("core:fatura_ek_indir", args=[ek.pk]))
        self.assertContains(r, 'target="_blank"')
        s = {x["pk"]: x for x in r.context["satirlar"]}
        self.assertEqual((s[f1.pk]["pdf_ek_pk"], s[f2.pk]["pdf_ek_pk"], s[f3.pk]["pdf_ek_pk"]), (ek.pk, None, None))

    def test_liste_tam_genislik_ve_salt_okunur(self):
        self.a_fatura()
        oncesi = (Fatura.objects.count(), YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), StokHareket.objects.count())
        r = self.liste()
        self.assertContains(r, "govde govde-tam")
        self.assertEqual((Fatura.objects.count(), YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), StokHareket.objects.count()), oncesi)


class OdemeDurumuTest(ListeTaban):
    def kur(self):
        """1.200 / 600 / 1.200 (en eski → yeni), ödeme 1.500 → f1 Kapandı, f2 Kısmi (kalan 300), f3 Açık."""
        self.f1 = self.a_fatura(tarih=D(2026, 3, 10), miktar="10", fiyat="100", no="F1")
        self.f2 = self.a_fatura(tarih=D(2026, 3, 11), miktar="5", fiyat="100", no="F2")
        self.f3 = self.a_fatura(tarih=D(2026, 3, 12), miktar="10", fiyat="100", no="F3")
        self.odeme("1500")

    def test_fifo_kapandi_kismi_acik(self):
        self.kur()
        d = fatura_odeme.odeme_durumlari([self.tedarikci], bugun=D(2026, 4, 30))
        self.assertEqual((d[self.f1.pk]["durum"], d[self.f1.pk]["kalan"]), ("KAPANDI", Dc("0")))
        self.assertEqual((d[self.f2.pk]["durum"], d[self.f2.pk]["kalan"], d[self.f2.pk]["tutar"]), ("KISMI", Dc("300.00"), Dc("600.00")))
        self.assertEqual((d[self.f3.pk]["durum"], d[self.f3.pk]["kalan"]), ("ACIK", Dc("1200.00")))
        self.assertEqual(sum(v["kalan"] for v in d.values()), Dc("1500.00"))                         # cari bakiyesi kadar en yeni faturalar açık

    def test_cari_ozet_ile_ayni_sonuc(self):
        self.kur()
        d = fatura_odeme.odeme_durumlari([self.tedarikci], bugun=D(2026, 4, 30))
        af = cari_ozet.acik_faturalar(self.tedarikci, bugun=D(2026, 4, 30))
        self.assertEqual({k["fatura"].pk: k["kalan"] for k in af["satirlar"]}, {pk: v["kalan"] for pk, v in d.items() if v["kalan"]})

    def test_liste_rozetleri_kapandi_kismi_acik(self):
        self.kur()
        r = self.liste()
        s = {x["pk"]: x for x in r.context["satirlar"]}
        self.assertEqual([s[f.pk]["odeme"]["durum"] for f in (self.f1, self.f2, self.f3)], ["KAPANDI", "KISMI", "ACIK"])
        self.assertContains(r, "ft-odeme kapandi")
        self.assertContains(r, "ft-odeme kismi")
        self.assertContains(r, "ft-odeme acik")
        self.assertContains(r, "kalan 300,00 TRY")                                                    # kısmi: kalan tutarla

    def test_vadesi_gecti_kirmizi_gun_sayisiyla(self):
        self.kur()
        bugun = tr_bugun()
        Fatura.objects.filter(pk=self.f3.pk).update(vade_tarihi=bugun - datetime.timedelta(days=5))
        Fatura.objects.filter(pk=self.f2.pk).update(vade_tarihi=bugun + datetime.timedelta(days=3))
        r = self.liste()
        self.assertContains(r, "Vadesi geçti (5 gün)")
        self.assertContains(r, "ft-odeme gecti")
        s = {x["pk"]: x for x in r.context["satirlar"]}
        self.assertEqual((s[self.f3.pk]["odeme"]["gecikme"], s[self.f2.pk]["odeme"]["gecikme"]), (5, 0))
        Fatura.objects.filter(pk=self.f1.pk).update(vade_tarihi=bugun - datetime.timedelta(days=40))   # kapanmış fatura vadesi geçti sayılmaz
        self.assertEqual(self.satirlar()[self.f1.pk]["odeme"]["gecikme"], 0)

    def test_taslak_ve_iade_faturada_odeme_durumu_yok(self):
        self.kur()
        t = fatura_taslak_olustur(tip_id=FaturaTipi.objects.get(ad="ALIŞ FATURASI").pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 13), satirlar=self._satir())
        s = self.satirlar()
        self.assertTrue(s[t.pk]["taslak"])
        self.assertIsNone(s[t.pk]["odeme"])
        self.assertContains(self.liste(), "Taslak")
        # satış (müşteri) faturası: alıcı cari doğal yönü borç; alış carisi yönünde yazılan iade tipi ters yönde → durum yok
        iade = FaturaTipi.objects.create(ad="SATIŞ FATURASI-ALIŞ İADE", yon="SATIS")
        from core.models import KategoriHesap, HesapPlani
        KategoriHesap.objects.create(kategori=self.alt, fatura_tipi=iade, hesap=HesapPlani.objects.get(hesap_kodu="600"))
        fi = fatura_olustur(tip_id=iade.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 14), satirlar=self._satir("1", "100"))
        self.assertNotIn(fi.pk, fatura_odeme.odeme_durumlari([self.tedarikci]))

    def test_cari_hesabi_olmayan_veya_bos_kume(self):
        self.assertEqual(fatura_odeme.odeme_durumlari([]), {})


class FiltreTest(ListeTaban):
    def kur(self):
        bugun = tr_bugun()
        self.f1 = self.a_fatura(tarih=D(2026, 3, 10), miktar="10", fiyat="100", no="F1")      # 1.200
        self.f2 = self.a_fatura(tarih=D(2026, 3, 11), miktar="5", fiyat="100", no="F2")       # 600
        self.f3 = self.a_fatura(tarih=D(2026, 3, 12), miktar="10", fiyat="100", no="F3")      # 1.200
        self.odeme("1500")                                                                  # f1 kapandı, f2 kısmi, f3 açık
        pazar = bugun + datetime.timedelta(days=6 - bugun.weekday())
        Fatura.objects.filter(pk=self.f2.pk).update(vade_tarihi=bugun - datetime.timedelta(days=2))          # vadesi geçmiş (kısmi)
        Fatura.objects.filter(pk=self.f3.pk).update(vade_tarihi=pazar)                                        # bu hafta dolan (açık)
        self.taslak = fatura_taslak_olustur(tip_id=FaturaTipi.objects.get(ad="ALIŞ FATURASI").pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 13), satirlar=self._satir())
        FaturaEk.objects.create(fatura=self.f1, dosya=ContentFile(PDF_BAYT, name="a.pdf"), orijinal_ad="a.pdf")
        FaturaEk.objects.create(fatura=self.f2, dosya=ContentFile(PDF_BAYT, name="b.pdf"), orijinal_ad="b.pdf")

    def test_hizli_filtreler_ve_cip_sayilari(self):
        self.kur()
        r = self.liste()
        self.assertEqual(r.context["toplam_adet"], 4)
        sayilar = {c["kod"]: c["sayi"] for c in r.context["cipler"]}
        self.assertEqual(sayilar, {"hepsi": 4, "acik": 2, "gecen": 1, "hafta": 1, "pdf_eksik": 1, "taslak": 1})
        for hizli, beklenen in (("acik", {self.f2.pk, self.f3.pk}), ("gecen", {self.f2.pk}), ("hafta", {self.f3.pk}),
                                ("pdf_eksik", {self.f3.pk}), ("taslak", {self.taslak.pk}), ("hepsi", {self.f1.pk, self.f2.pk, self.f3.pk, self.taslak.pk})):
            self.assertEqual(set(self.satirlar(hizli=hizli)), beklenen, hizli)
        self.assertContains(self.liste(hizli="gecen"), "ft-cip aktif")
        self.assertEqual(self.liste(hizli="saçma").context["hizli"], "hepsi")                         # geçersiz değer güvenle yok sayılır

    def test_mevcut_ve_yeni_filtreler_url_parametresiyle(self):
        self.kur()
        self.assertEqual(set(self.satirlar(durum="TASLAK")), {self.taslak.pk})
        self.assertEqual(set(self.satirlar(durum="ONAYLI")), {self.f1.pk, self.f2.pk, self.f3.pk})
        self.assertEqual(set(self.satirlar(bas="2026-03-11", bit="2026-03-12")), {self.f2.pk, self.f3.pk})
        self.assertEqual(set(self.satirlar(ara="f2")), {self.f2.pk})
        self.assertEqual(set(self.satirlar(ara="tedarik")), set(self.satirlar()))                      # kısa ad / ünvan araması
        self.assertEqual(set(self.satirlar(cari=str(self.usd.pk))), set())
        self.assertEqual(set(self.satirlar(cari=str(self.tedarikci.pk))), set(self.satirlar()))
        self.assertEqual(set(self.satirlar(tip=str(FaturaTipi.objects.get(ad="ALIŞ FATURASI").pk))), set(self.satirlar()))
        u = self.a_fatura(miktar="1", fiyat="10", cari=self.usd, para_birimi="USD")
        self.assertEqual(set(self.satirlar(pb="USD")), {u.pk})
        self.assertEqual(self.liste(pb="USD").context["secili_pb"], "USD")
        self.assertEqual(set(self.satirlar(cari=str(self.usd.pk))), {u.pk})
        # çipler ve sıralama bağlantıları mevcut süzgeçleri taşır (URL'de tutulur)
        r = self.liste(cari=str(self.tedarikci.pk), ara="f", hizli="acik")
        self.assertIn(f"cari={self.tedarikci.pk}", r.context["siralama"]["tarih"]["url"])
        self.assertIn("hizli=acik", r.context["siralama"]["toplam"]["url"])
        self.assertTrue(all("ara=f" in c["url"] for c in r.context["cipler"]))
        self.assertNotIn("hizli=", [c for c in r.context["cipler"] if c["kod"] == "hepsi"][0]["url"])

    def test_filtre_degisince_cip_sayilari_kumeye_gore(self):
        self.kur()
        sayilar = {c["kod"]: c["sayi"] for c in self.liste(durum="ONAYLI").context["cipler"]}
        self.assertEqual((sayilar["hepsi"], sayilar["taslak"]), (3, 0))


class SiralamaSayfaTest(ListeTaban):
    def kur(self):
        self.a = self.a_fatura(tarih=D(2026, 3, 10), miktar="10", fiyat="100", no="B-2")       # 1.200
        self.b = self.a_fatura(tarih=D(2026, 3, 12), miktar="1", fiyat="100", no="A-9", cari=self.usd, para_birimi="USD")   # 120 USD = 3.600 TL
        self.c = self.a_fatura(tarih=D(2026, 3, 11), miktar="2", fiyat="100", no="C-1")        # 240
        Fatura.objects.filter(pk=self.a.pk).update(vade_tarihi=D(2026, 5, 1))
        Fatura.objects.filter(pk=self.b.pk).update(vade_tarihi=D(2026, 4, 1))

    def sira(self, **q):
        return [s["pk"] for s in self.liste(**q).context["satirlar"]]

    def test_varsayilan_tarih_azalan_ve_kolon_siralari(self):
        self.kur()
        self.assertEqual(self.sira(), [self.b.pk, self.c.pk, self.a.pk])                            # tarih azalan
        self.assertEqual(self.sira(sirala="tarih", dir="asc"), [self.a.pk, self.c.pk, self.b.pk])
        self.assertEqual(self.sira(sirala="no", dir="asc"), [self.b.pk, self.a.pk, self.c.pk])      # A-9, B-2, C-1
        self.assertEqual(self.sira(sirala="toplam", dir="desc"), [self.b.pk, self.a.pk, self.c.pk])  # TL: 3.600 / 1.200 / 240
        self.assertEqual(self.sira(sirala="toplam", dir="asc"), [self.c.pk, self.a.pk, self.b.pk])
        self.assertEqual(self.sira(sirala="vade", dir="asc"), [self.b.pk, self.a.pk, self.c.pk])    # vadesizler sonda
        self.assertEqual(self.sira(sirala="vade", dir="desc"), [self.a.pk, self.b.pk, self.c.pk])   # ters yönde de vadesiz sonda
        self.assertEqual(self.sira(sirala="yok"), self.sira())

    def test_cari_siralamasi_turkce_kisa_ada_gore(self):
        self.kur()
        ilk = self.liste(sirala="cari", dir="asc").context["satirlar"]
        self.assertEqual([s["cari_kisa"] for s in ilk], sorted([s["cari_kisa"] for s in ilk], key=lambda x: cari_ozet.tr_anahtar(x)))

    def test_baslik_linkleri_yonu_degistirir(self):
        self.kur()
        r = self.liste()
        self.assertIn("sirala=tarih", r.context["siralama"]["tarih"]["url"])
        self.assertIn("dir=asc", r.context["siralama"]["tarih"]["url"])                              # tarih azalan iken tıklayınca artan
        r = self.liste(sirala="toplam", dir="asc")
        self.assertIn("dir=desc", r.context["siralama"]["toplam"]["url"])
        self.assertEqual(r.context["siralama"]["toplam"]["isaret"], " ▲")

    def test_sayfa_boyutu_secimi_ve_sayfalama(self):
        self.kur()
        self.assertEqual(self.liste().context["boyut"], 50)
        self.assertEqual(self.liste(boyut="100").context["boyut"], 100)
        self.assertEqual(self.liste(boyut="250").context["boyut"], 250)
        self.assertEqual(self.liste(boyut="77").context["boyut"], 50)                               # listede olmayan → varsayılan
        with patch.object(fl, "SAYFA_BOYUTLARI", (1, 100, 250)):
            r = self.liste(boyut="1")
            self.assertEqual((r.context["boyut"], r.context["sayfa"].paginator.num_pages, len(r.context["satirlar"])), (1, 3, 1))
            self.assertEqual(r.context["toplam_adet"], 3)
            self.assertContains(r, "boyut=1&amp;sayfa=2")                                          # sayfa bağlantıları süzgeç/boyutu taşır
            p2 = self.liste(boyut="1", sayfa="2").context["satirlar"][0]["pk"]
            self.assertEqual(p2, self.c.pk)


class ToplamExcelTest(ListeTaban):
    def kur(self):
        self.f1 = self.a_fatura(tarih=D(2026, 3, 10), miktar="10", fiyat="100", no="T-1")                          # 1.000 + 200 = 1.200
        self.f2 = self.a_fatura(tarih=D(2026, 3, 11), miktar="1", fiyat="100", no="T-2", cari=self.usd, para_birimi="USD")   # 100 + 20 USD (3.000 + 600 TL)
        self.f3 = fatura_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 12), fatura_no="T-3", satirlar=self._satir())   # yalnız KDV: 200
        self.odeme("500")

    def test_toplam_satiri_tl_doviz_alt_toplam_ve_acik_bakiye(self):
        self.kur()
        t = self.liste().context["toplamlar"]
        self.assertEqual((t["tl"]["matrah"], t["tl"]["kdv"], t["tl"]["toplam"]), (Dc("4000.00"), Dc("1000.00"), Dc("5000.00")))   # 1.000+3.000 · 200+600+200
        self.assertEqual(t["doviz"], [("USD", {"matrah": Dc("100.00"), "kdv": Dc("20.00"), "toplam": Dc("120.00")})])
        acik = dict(t["acik"])
        self.assertEqual(acik["TRY"], Dc("1200.00") + Dc("200.00") - Dc("500.00"))                 # tedarikçi: 1.200 + 200 KDV − 500 ödeme
        self.assertEqual(acik["USD"], Dc("120.00"))
        r = self.liste()
        self.assertContains(r, "USD alt toplam")
        self.assertContains(r, "Açık bakiye")
        # toplam satırı SAYFAYA değil süzülmüş TÜM listeye aittir
        with patch.object(fl, "SAYFA_BOYUTLARI", (1, 100, 250)):
            r = self.liste(boyut="1")
            self.assertEqual(r.context["toplamlar"]["tl"]["toplam"], Dc("5000.00"))
        t = self.liste(pb="USD").context["toplamlar"]
        self.assertEqual((t["tl"]["toplam"], t["acik"]), (Dc("3600.00"), [("USD", Dc("120.00"))]))

    def test_liste_kdv_toplami_191_borc_hareketiyle_tutarli(self):
        """Alış listesinin (tüm tipler, dönem) KDV toplamı ↔ 191 hesabının o dönemdeki borç hareketi (mizan tutarlılığı); döviz ve yalnız-KDV dahil."""
        self.kur()
        t = self.liste(bas="2026-03-01", bit="2026-03-31").context["toplamlar"]
        borc191 = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id="191", fis__tarih__gte=D(2026, 3, 1), fis__tarih__lte=D(2026, 3, 31)).aggregate(b=Sum("borc"))["b"]
        self.assertEqual(t["tl"]["kdv"], borc191)
        self.assertEqual(borc191, Dc("1000.00"))

    def test_taslak_dovizde_kur_yoksa_tl_belirsiz(self):
        t = fatura_taslak_olustur(tip_id=FaturaTipi.objects.get(ad="ALIŞ FATURASI").pk, cari_id=self.usd.pk, tarih=D(2026, 3, 13), satirlar=self._satir("1", "100"), para_birimi="USD")
        s = self.satirlar()[t.pk]
        self.assertIsNone(s["toplam_tl"])
        self.assertEqual(self.liste().context["toplamlar"]["belirsiz_kur"], 1)

    def test_excel_filtrelenmis_listenin_tamami_kolonlar_ve_toplam(self):
        self.kur()
        r = self.liste(xlsx="1", hizli="acik")
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        ws = load_workbook(BytesIO(r.content)).active
        basliklar = [c.value for c in ws[4]]
        self.assertEqual(basliklar[:4], ["Tarih", "Fatura No", "Cari", "Tip"])
        self.assertIn("Matrah (TL)", basliklar)
        self.assertIn("Toplam (TL)", basliklar)
        satir = [[c.value for c in row] for row in ws.iter_rows(min_row=5)]
        veri = [s for s in satir if s[1]]
        self.assertEqual(sorted(s[1] for s in veri), ["T-1", "T-2", "T-3"])                         # acik: üçü de kalanı olan (yalnız KDV dahil)
        d = {s[1]: s for s in veri}
        self.assertEqual((d["T-2"][4], d["T-2"][5], d["T-2"][6], d["T-2"][7], d["T-2"][9]), (3000.0, 600.0, 120.0, "USD", 3600.0))
        self.assertEqual(d["T-3"][3], "KDV (yalnız KDV)")
        self.assertIsNone(d["T-3"][4])
        self.assertIn("TOPLAM (TL karşılığı)", [s[2] for s in satir])
        self.assertIn("USD alt toplam (fatura para birimi)", [s[2] for s in satir])
        # tam liste (süzgeçsiz) de aynı kolonlarla; süzgeç uygulanırsa satır sayısı azalır
        tum = [row for row in load_workbook(BytesIO(self.liste(xlsx="1").content)).active.iter_rows(min_row=5, values_only=True) if row[1]]
        dar = [row for row in load_workbook(BytesIO(self.liste(xlsx="1", cari=str(self.usd.pk)).content)).active.iter_rows(min_row=5, values_only=True) if row[1]]
        self.assertEqual((len(tum), len(dar)), (3, 1))
        self.assertIn("filename=\"alis_faturalari_", r["Content-Disposition"])

    def test_excel_linki_mevcut_suzgecleri_tasir(self):
        self.kur()
        r = self.liste(hizli="acik", ara="t")
        self.assertIn("xlsx=1", r.context["xlsx_url"])
        self.assertIn("hizli=acik", r.context["xlsx_url"])


class SatisVeYetkiTest(ListeTaban):
    def test_satis_listesi_ayni_yapi_ve_fifo(self):
        from core.models import HesapPlani
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="S-1", satirlar=self._satir())
        kasa_hareket(kasa=self.kasa, tip="cari_tahsilat", karsi=self.musteri, tutar="400", tarih=D(2026, 3, 15))
        r = self.liste(yon="satis")
        s = r.context["satirlar"][0]
        self.assertEqual((s["pk"], s["tip_etiket"], s["toplam"], s["odeme"]["durum"], s["odeme"]["kalan"]), (f.pk, "Satış", Dc("1200.00"), "KISMI", Dc("800.00")))
        self.assertContains(r, "Excel")
        self.assertContains(r, "S-1")
        self.assertNotContains(r, "A-1")

    def test_yon_ayrimi_alis_listesinde_satis_yok(self):
        a = self.a_fatura(no="A-1")
        fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 10), fatura_no="S-1", satirlar=self._satir())
        self.assertEqual(set(self.satirlar()), {a.pk})

    def test_yetki(self):
        self.a_fatura()
        sade = User.objects.create_user("ftsade", password="x")
        self.client.force_login(sade)
        self.assertEqual(self.client.get(reverse("core:alis_faturalari")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:alis_faturalari"), {"xlsx": "1"}).status_code, 403)
        EkranYetki.objects.create(kullanici=sade, ekran_kod="alis_faturalari")
        self.assertEqual(self.client.get(reverse("core:alis_faturalari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:satis_faturalari")).status_code, 403)

    def test_bos_liste_ve_mesaj(self):
        r = self.liste()
        self.assertContains(r, "Henüz fatura yok")
        r = self.liste(ara="yok-boyle-bir-sey")
        self.assertContains(r, "Bu süzgeçle eşleşen fatura yok")


class SorguSayisiTest(ListeTaban):
    def sayi(self):
        self.client.force_login(self.su)
        self.client.get(reverse("core:alis_faturalari"))                                           # ısınma (oturum/izin önbelleği)
        with CaptureQueriesContext(connection) as q:
            r = self.client.get(reverse("core:alis_faturalari"))
        self.assertEqual(r.status_code, 200)
        return len(q)

    def test_sorgu_sayisi_fatura_sayisindan_bagimsiz(self):
        for i in range(3):
            self.a_fatura(tarih=D(2026, 3, 10 + i), no=f"Q-{i}")
        self.odeme("700")
        az = self.sayi()
        for i in range(8):
            self.a_fatura(tarih=D(2026, 3, 13 + i), no=f"R-{i}")
        FaturaEk.objects.create(fatura=Fatura.objects.first(), dosya=ContentFile(PDF_BAYT, name="a.pdf"), orijinal_ad="a.pdf")
        cok = self.sayi()
        self.assertEqual(az, cok, f"sorgu sayısı faturaya bağlı: {az} → {cok}")
