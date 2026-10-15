"""BİRİM ÇEVRİM KORUMASI (SAI-2026-0015 / M6 PUL vakası): kart AD (üretim) ↔ KG (fatura), çevirici 0,0008 → 40.000 "KG" yazılan miktar 50.000.000 AD stok girişi oluyordu.
Belge satırında ayrı birim seçimi yok; birim = kartın fatura birimi. Çevrim katsayısı 1.000 katını aşarsa: IRSALİYE kalemi doğrudan fatura biriminde girildiyse KAYIT REDDEDİLİR
('Üretim miktarı' açıkça yazılırsa kabul), son kapı onayda da aynı; FATURA satırında (üretim miktarı alanı yok) açık UYARI; formlarda canlı çevrim notu."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Birim, Kur, Stok, StokHareket, TeklifSiparisKalem
from core.services import birim_cevrim
from core.services.depo import depo_olustur
from core.services.fatura import asiri_cevrim_uyarilari, fatura_olustur
from core.services.hareket import eldeki_miktar
from core.services.teklif_siparis import (TeklifSiparisHatasi, irsaliyeyi_faturaya_cevir, teklif_siparis_olustur, teklif_siparis_onayla)
from core.tests.test_fatura import FaturaTestTemel

D = datetime.date
Dc = Decimal


class CevrimTemel(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Kur.objects.create(tarih=D(2026, 10, 8), usd_alis=Dc("40"))                  # fiş USD kuru için
        cls.kg = Birim.objects.create(ad="KİLOGRAM", kisa_ad="KG", ondalik=3)
        cls.adet_b = cls.stok.uretim_birimi                                            # ADET (AD)
        cls.depo = depo_olustur(kod="DA", ad="DEPO A")
        cls.pul = Stok.objects.create(kod="153-10-0050", ad="M6 PUL", kategori=cls.alt, uretim_birimi=cls.adet_b, fatura_birimi=cls.kg, cevirici=Dc("0.0008"), kdv=cls.kdv)
        cls.profil = Stok.objects.create(kod="153-10-0051", ad="PROFİL", kategori=cls.alt, uretim_birimi=cls.adet_b, fatura_birimi=cls.kg, cevirici=Dc("1.8139"), kdv=cls.kdv)
        cls.su = User.objects.create_superuser("cevrim", password="x")

    def _irsaliye(self, stok, miktar, uretim=None, onayla=True):
        satir = {"stok_id": stok.pk, "miktar": miktar, "birim_fiyat": "0.11"}
        if uretim is not None:
            satir["uretim_miktar"] = uretim
        ir = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 10, 8), depo_id=self.depo.pk, satirlar=[satir], kullanici=self.su)
        if onayla:
            teklif_siparis_onayla(ir, kullanici=self.su)
        return ir


class KatsayiTest(CevrimTemel):
    def test_asiri_katsayi_siniri(self):
        class K:                                           # yalnız cevirici taşıyan kart taklidi
            def __init__(self, c):
                self.cevirici = Dc(c)
        for c, beklenen in (("0.0008", True), ("0.001", True), ("0.0011", False), ("1", False), ("1.8139", False), ("0.025", False), ("1000", True), ("999", False), ("5000", True)):
            self.assertEqual(birim_cevrim.asiri_mi(K(c)), beklenen, c)

    def test_aciklama_metni(self):
        m = birim_cevrim.aciklama(self.pul, Dc("40000"))
        self.assertEqual(m, "40.000 KG = 50.000.000 AD (çevirici 0,0008; 1 KG = 1.250 AD)")


class IrsaliyeKorumasiTest(CevrimTemel):
    def test_pul_40000_kg_reddedilir_ve_mesaj_aciklayici(self):
        with self.assertRaises(TeklifSiparisHatasi) as cm:
            self._irsaliye(self.pul, "40000")
        m = str(cm.exception)
        for parca in ("M6 PUL", "40.000 KG = 50.000.000 AD", "çevirici 0,0008", "1.000 katını aşıyor", "'Üretim miktarı'"):
            self.assertIn(parca, m)
        self.assertEqual((StokHareket.objects.count(), TeklifSiparisKalem.objects.count()), (0, 0))        # atomik: hiçbir kayıt yok

    def test_uretim_miktari_acikca_yazilirsa_kabul_ve_stok_o_miktar(self):
        ir = self._irsaliye(self.pul, "32", uretim="40000")                                                    # 32 KG = 40.000 AD (açıkça onaylandı)
        (h,) = StokHareket.objects.filter(teklif_siparis_kalem__teklif_siparis=ir)
        self.assertEqual(h.miktar, Dc("40000"))
        self.assertEqual(eldeki_miktar(self.pul.pk, self.depo.pk), Dc("40000"))

    def test_normal_cevrimli_kart_etkilenmez(self):
        self._irsaliye(self.profil, "1000")                                                                     # katsayı 0,55 → normal
        self.assertEqual(eldeki_miktar(self.profil.pk, self.depo.pk), (Dc("1000") / Dc("1.8139")).quantize(Dc("0.001")))
        self._irsaliye(self.stok, "40000")                                                                      # çevirici 1
        self.assertEqual(eldeki_miktar(self.stok.pk, self.depo.pk), Dc("40000"))

    def test_son_kapi_onayda_ayni_koruma(self):
        ir = self._irsaliye(self.pul, "32", uretim="40000", onayla=False)
        TeklifSiparisKalem.objects.filter(teklif_siparis=ir).update(miktar=Dc("40000"), uretim_miktar=None)    # siparişten/eski kayıttan gelmiş kalem gibi
        with self.assertRaisesMessage(TeklifSiparisHatasi, "50.000.000 AD"):
            teklif_siparis_onayla(ir, kullanici=self.su)
        self.assertEqual(StokHareket.objects.count(), 0)

    def test_guncelle_de_ayni_koruma(self):
        from core.services.teklif_siparis import teklif_siparis_guncelle
        ir = self._irsaliye(self.profil, "100", onayla=False)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "1.000 katını aşıyor"):
            teklif_siparis_guncelle(ir, cari_id=self.tedarikci.pk, tarih=D(2026, 10, 8), depo_id=self.depo.pk, kullanici=self.su,
                                    satirlar=[{"stok_id": self.pul.pk, "miktar": "40000", "birim_fiyat": "0.11"}])

    def test_siparis_ve_teklif_belgesi_bu_korumaya_takilmaz(self):
        ts = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 10, 8),
                                    satirlar=[{"stok_id": self.pul.pk, "miktar": "40000", "birim_fiyat": "0.11"}], kullanici=self.su)
        self.assertTrue(ts.pk)                                                                                  # stok yazmayan belge; irsaliyeye dönüşünce onayda yakalanır


class FaturaUyarisiTest(CevrimTemel):
    def _fatura(self, stok, miktar, depo=True):
        return fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 10, 8), fatura_no=f"F-{stok.pk}-{miktar}",
                              satirlar=[{"stok_id": stok.pk, "miktar": miktar, "birim_fiyat": "0.11"}], depo_id=self.depo.pk if depo else None, kullanici=self.su)

    def test_asiri_cevrimli_faturada_uyari_metni(self):
        f = self._fatura(self.pul, "40000")
        (u,) = asiri_cevrim_uyarilari(f)
        self.assertIn("40.000 KG = 50.000.000 AD", u)
        self.assertIn("birim doğru mu", u)

    def test_normal_depo_yok_ve_irsaliyeli_faturada_uyari_yok(self):
        self.assertEqual(asiri_cevrim_uyarilari(self._fatura(self.profil, "1000")), [])
        self.assertEqual(asiri_cevrim_uyarilari(self._fatura(self.pul, "40001", depo=False)), [])              # stok hareketi yazılmadı
        ir = self._irsaliye_taslagi()
        self.assertEqual(asiri_cevrim_uyarilari(ir), [])                                                       # stoğu irsaliye yazdı

    def _irsaliye_taslagi(self):
        irs = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 10, 8), depo_id=self.depo.pk,
                                     satirlar=[{"stok_id": self.pul.pk, "miktar": "32", "birim_fiyat": "137.5", "uretim_miktar": "40000"}], kullanici=self.su)
        teklif_siparis_onayla(irs, kullanici=self.su)
        irsaliyeyi_faturaya_cevir(irs, kullanici=self.su)
        irs.refresh_from_db()
        return irs.fatura


class EkranTest(CevrimTemel):
    def test_fatura_formu_cevrim_meta_ve_etiket(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertContains(r, 'id="stok-cevrim"')
        meta = r.context["stok_cevrim"]
        self.assertEqual((meta[str(self.pul.pk)]["asiri"], meta[str(self.profil.pk)]["asiri"], str(self.stok.pk) in meta), (True, False, False))     # çevirici 1 kartlar yok
        self.assertEqual(meta[str(self.pul.pk)]["uretim"], "AD")
        self.assertContains(r, "cevrim-etiket")

    def test_irsaliye_formu_cevrim_notu_ve_hata_formda_kalir(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:satinalma_irsaliye_ekle"))
        self.assertContains(r, "cevrim-etiket")
        self.assertContains(r, "function cevrimNotu")
        veri = {"cari": self.tedarikci.pk, "tarih": "2026-10-08", "depo": self.depo.pk, "para_birimi": "TRY", "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000", "form-0-stok": self.pul.pk, "form-0-miktar": "40.000", "form-0-birim_fiyat": "0,11"}
        r = self.client.post(reverse("core:satinalma_irsaliye_ekle"), veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "50.000.000 AD")
        self.assertEqual(TeklifSiparisKalem.objects.count(), 0)
        veri.update({"form-0-uretim_miktar": "40.000", "form-0-miktar": "32"})
        r = self.client.post(reverse("core:satinalma_irsaliye_ekle"), veri)
        self.assertEqual(r.status_code, 302)

    def test_faturada_kayit_sonrasi_uyari_mesaji(self):
        self.client.force_login(self.su)
        veri = {"tip": self.alis.pk, "cari": self.tedarikci.pk, "tarih": "2026-10-08", "fatura_no": "U-1", "para_birimi": "TRY", "depo": self.depo.pk,
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
                "form-0-tur": "STOK", "form-0-stok": self.pul.pk, "form-0-miktar": "40.000", "form-0-birim_fiyat": "0,11"}
        r = self.client.post(reverse("core:alis_fatura_ekle"), veri, follow=True)
        self.assertContains(r, "Birim çevrimi")
        self.assertContains(r, "50.000.000 AD")
