"""Migration 0126 (Aday Müşteri unvan -> tip taşıma) testleri: saf ayrıştırma fonksiyonları
gerçek prod verisinden alınan örneklerle + uçtan uca RunPython (ileri/geri) davranışı."""
import importlib

from django.test import TestCase

from core.models import AdayMusteri
from core.services.aday import aday_musteri_olustur

_mod = importlib.import_module("core.migrations.0126_aday_musteri_tip_veri_tasima")
_tip_belirle = _mod._tip_belirle
_etiket_temizle = _mod._etiket_temizle
tipleri_ata = _mod.tipleri_ata
tipleri_geri_al = _mod.tipleri_geri_al


class _SahteApps:
    """RunPython fonksiyonları apps.get_model() kullanır — testte gerçek modeli döndürür
    (alanlar/anlamları bu migration'dan sonra hiç değişmedi, historical model'e gerek yok)."""

    def get_model(self, app_label, model_adi):
        return AdayMusteri


class TipBelirleTest(TestCase):
    def test_musteri_etiketi(self):
        self.assertEqual(_tip_belirle("SIM D.O.O. NOVİ SAD [MÜŞTERİ] ⚠ ALÜMİNYUM TALEBİ VAR"),
                         "ESKI_MUSTERI")
        self.assertEqual(_tip_belirle("KADDAH BLDG CLEANING EQUIP TR CO LLC - AKC GROUP [MÜŞTERİ]"),
                         "ESKI_MUSTERI")

    def test_aday_etiketi(self):
        self.assertEqual(_tip_belirle("PERDOMO DOO [ADAY] — PERDOMO PLAST + PERDEMO BİRLEŞTİRİLDİ"),
                         "ADAY")

    def test_araci_gumruk_ayrimi(self):
        self.assertEqual(_tip_belirle("DAR ČAČAK (GÜMRÜK MÜŞAVİRLİĞİ) [ARACI]"), "GUMRUK")
        self.assertEqual(_tip_belirle("MD COMPANY (GÜMRÜK / SERBEST BÖLGE) [ARACI]"), "GUMRUK")
        self.assertEqual(_tip_belirle("SIRADAN BİR ARACI FİRMA [ARACI]"), "ARACI")

    def test_pazar_bilgisi_etiketi(self):
        self.assertEqual(_tip_belirle("IRAK İÇİN FİYAT LİSTESİ 2022 [PAZAR BİLGİSİ]"),
                         "PAZAR_BILGISI")

    def test_etiketsiz_varsayilan_aday(self):
        self.assertEqual(_tip_belirle("ERTEKPA BANYO AKSESUARLARI"), "ADAY")

    def test_tehnoalat_istisnasi(self):
        self.assertEqual(_tip_belirle("TEHNOALAT D.O.O."), "ESKI_MUSTERI")


class EtiketTemizleTest(TestCase):
    def test_sonda_olan_etiket_silinir(self):
        self.assertEqual(
            _etiket_temizle("KADDAH BLDG CLEANING EQUIP TR CO LLC - AKC GROUP [MÜŞTERİ]"),
            "KADDAH BLDG CLEANING EQUIP TR CO LLC - AKC GROUP")

    def test_ortadaki_etiket_silinir_bosluk_tek_boslukla_degisir(self):
        self.assertEqual(
            _etiket_temizle("PERDOMO DOO [ADAY] — PERDOMO PLAST + PERDEMO BİRLEŞTİRİLDİ"),
            "PERDOMO DOO — PERDOMO PLAST + PERDEMO BİRLEŞTİRİLDİ")

    def test_ek_metinli_etiket_silinir(self):
        self.assertEqual(
            _etiket_temizle("SIM D.O.O. NOVİ SAD [MÜŞTERİ] ⚠ ALÜMİNYUM TALEBİ VAR"),
            "SIM D.O.O. NOVİ SAD ⚠ ALÜMİNYUM TALEBİ VAR")

    def test_etiketsiz_degismez(self):
        self.assertEqual(_etiket_temizle("ERTEKPA BANYO AKSESUARLARI"),
                         "ERTEKPA BANYO AKSESUARLARI")


class RunPythonUctanUcaTest(TestCase):
    def _olustur(self, unvan):
        return aday_musteri_olustur(unvan=unvan)

    def test_ileri_musteri(self):
        a = self._olustur("KADDAH BLDG [MÜŞTERİ]")
        tipleri_ata(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.tip, "ESKI_MUSTERI")
        self.assertEqual(a.unvan, "KADDAH BLDG")
        self.assertEqual(a.asama, "YENI")
        self.assertEqual(a.potansiyel, "")
        self.assertEqual(a.kapanis_nedeni, "")

    def test_ileri_araci_gumruk(self):
        a = self._olustur("DAR ÇAÇAK (GÜMRÜK MÜŞAVİRLİĞİ) [ARACI]")
        tipleri_ata(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.tip, "GUMRUK")
        self.assertEqual(a.unvan, "DAR ÇAÇAK (GÜMRÜK MÜŞAVİRLİĞİ)")

    def test_ileri_etiketsiz_varsayilan(self):
        a = self._olustur("SIRADAN FIRMA")
        tipleri_ata(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.tip, "ADAY")
        self.assertEqual(a.unvan, "SIRADAN FIRMA")

    def test_geri_alma_etiketi_sona_ekler(self):
        a = self._olustur("KADDAH BLDG")
        a.tip = "ESKI_MUSTERI"
        a.save(update_fields=["tip"])
        tipleri_geri_al(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.unvan, "KADDAH BLDG [MÜŞTERİ]")

    def test_geri_alma_gumruk_araciya_doner(self):
        a = self._olustur("DAR ÇAÇAK (GÜMRÜK MÜŞAVİRLİĞİ)")
        a.tip = "GUMRUK"
        a.save(update_fields=["tip"])
        tipleri_geri_al(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.unvan, "DAR ÇAÇAK (GÜMRÜK MÜŞAVİRLİĞİ) [ARACI]")

    def test_geri_alma_zaten_etiketliyse_tekrar_eklemez(self):
        a = self._olustur("KADDAH BLDG [MÜŞTERİ]")
        a.tip = "ESKI_MUSTERI"
        a.save(update_fields=["tip"])
        tipleri_geri_al(_SahteApps(), None)
        a.refresh_from_db()
        self.assertEqual(a.unvan, "KADDAH BLDG [MÜŞTERİ]")
