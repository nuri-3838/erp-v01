"""Migration 0126 (Aday Müşteri unvan -> tip taşıma) testleri: saf ayrıştırma fonksiyonları
gerçek prod verisinden alınan örneklerle.

Not: bu dosya eskiden 0126'nın RunPython'larını (tipleri_ata/tipleri_geri_al) LİVE
AdayMusteri modeli üzerinden uçtan uca da çalıştırıyordu ("alanlar hiç değişmedi, historical
model'e gerek yok" varsayımıyla) — migration 0132 (Kategori→Kaynak + Tip/Potansiyel/Aşama
tanım tabloları) tam olarak bu alanları (tip/potansiyel/asama) CharField'dan FK'ye çevirip
sütunları yeniden adlandırdığı için o varsayım artık GEÇERSİZ: 0126'nın kodu hâlâ
``a.tip = "ESKI_MUSTERI"`` yazıp ``update_fields=["tip",...]`` ile kaydetmeye çalışıyor, ama
canlı şemada böyle bir sütun/atama biçimi yok. 0126 kendisi donmuş/tarihsel bir migration
(canlıda çoktan uygulandı, bir daha çalışmayacak) — saf ayrıştırma fonksiyonları (aşağıda)
hâlâ test ediliyor, yalnızca artık çalıştırılamaz olan uçtan-uca RunPython testi kaldırıldı."""
import importlib

from django.test import TestCase

_mod = importlib.import_module("core.migrations.0126_aday_musteri_tip_veri_tasima")
_tip_belirle = _mod._tip_belirle
_etiket_temizle = _mod._etiket_temizle


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

# Uçtan uca RunPython (ileri/geri) testi kaldırıldı — bkz. dosya başı not.
