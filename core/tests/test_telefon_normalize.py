"""ERP — CRM telefon formatı: core.dogrulama.telefon_normalize (saf fonksiyon), dört
tabloda (AdayMusteri/Cari/AdayYetkili/CariYetkili) servis entegrasyonu, migration 0136'nın
veri taşıması + geri alması.

Kapsam DIŞI: Personel/Kullanıcı (Profil) telefon alanları — onlar hâlâ telefon_kanonik
kullanır (yalnız TR), bu değişiklikten etkilenmedi (bkz. core/dogrulama.py dosya başı notu)."""
import importlib
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.apps import apps as canli_apps
from django.test import TestCase

from core.dogrulama import telefon_normalize
from core.models import (
    AdayAsamaTanim, AdayMusteri, AdayMusteriKategori, AdayTipTanim, AdayYetkili, Cari,
    CariYetkili, Sehir, Ulke,
)
from core.services.aday import aday_musteri_olustur, aday_yetkili_ekle
from core.services.cari import cari_olustur, yetkili_ekle as cari_yetkili_ekle


# --- core.dogrulama.telefon_normalize — saf fonksiyon --------------------------------------
class TelefonNormalizeTest(TestCase):
    def test_tr_yerel_formatlar_uluslararasi_bicime_donusur(self):
        for ham in ("05322070709", "5322070709", "0352 322 25 53", "00905050250485"):
            sonuc = telefon_normalize(ham, "TR")
            self.assertTrue(sonuc.gecerli, f"{ham} geçersiz sayıldı")
            self.assertTrue(sonuc.deger.startswith("+90 "), sonuc.deger)

    def test_ornek_degerler_tam_eslesme(self):
        self.assertEqual(telefon_normalize("05322070709", "TR"),
                         ("+90 532 207 07 09", True))
        self.assertEqual(telefon_normalize("00905050250485", "TR"),
                         ("+90 505 025 04 85", True))

    def test_yabanci_ulke_kodu_ile_dogru_calisir(self):
        self.assertEqual(telefon_normalize("+964 750 448 2012"),
                         ("+964 750 448 2012", True))
        self.assertEqual(telefon_normalize("+381641234567"),
                         ("+381 64 1234567", True))

    def test_arti_ile_baslayan_ulke_parametresini_yok_sayar(self):
        # "+" ile başlıyorsa uluslararası parse edilir — ulke_iso2 parametresi kaydın
        # ülkesi TR bile olsa yabancı numarayı yanlış yorumlamaz.
        sonuc = telefon_normalize("+964 750 448 2012", "TR")
        self.assertEqual(sonuc, ("+964 750 448 2012", True))

    def test_gecersiz_deger_aynen_korunur(self):
        sonuc = telefon_normalize("123", "TR")
        self.assertFalse(sonuc.gecerli)
        self.assertEqual(sonuc.deger, "123")   # veri kaybı yok — değiştirilmedi

    def test_parse_edilemeyen_deger_aynen_korunur(self):
        sonuc = telefon_normalize("abc telefon degil", "TR")
        self.assertFalse(sonuc.gecerli)
        self.assertEqual(sonuc.deger, "abc telefon degil")

    def test_bos_deger_bos_ve_gecerli(self):
        self.assertEqual(telefon_normalize(""), ("", True))
        self.assertEqual(telefon_normalize(None), ("", True))
        self.assertEqual(telefon_normalize("   "), ("", True))

    def test_ulke_verilmezse_varsayilan_tr(self):
        sonuc_bossuz = telefon_normalize("05322070709")
        sonuc_tr = telefon_normalize("05322070709", "TR")
        self.assertEqual(sonuc_bossuz, sonuc_tr)

    def test_20_karakter_sinirini_asarsa_bosluksuz_e164(self):
        # Gerçek numaralarda INTERNATIONAL biçimi neredeyse hiç 20 karakteri aşmıyor (spec'in
        # yine de öngördüğü bir güvenlik ağı) — dalı deterministik test etmek için
        # format_number'ın INTERNATIONAL çağrısı kasıtlı uzun bir sahte değerle yamanır.
        import phonenumbers
        gercek = phonenumbers.format_number

        def sahte(sayi, bicim):
            if bicim == phonenumbers.PhoneNumberFormat.INTERNATIONAL:
                return "+90 532 207 07 09 EK-123"   # kasıtlı > 20 karakter
            return gercek(sayi, bicim)

        with mock.patch("phonenumbers.format_number", side_effect=sahte):
            sonuc = telefon_normalize("05322070709", "TR")
        self.assertTrue(sonuc.gecerli)
        self.assertEqual(sonuc.deger, "+905322070709")   # E.164'e düşürüldü
        self.assertLessEqual(len(sonuc.deger), 20)

    def test_boslukla_yazilan_00_koddan_arti_uretir(self):
        sonuc = telefon_normalize("00 90 532 207 07 09", "TR")
        self.assertEqual(sonuc, ("+90 532 207 07 09", True))


# --- 4 tabloda servis entegrasyonu: form kaydında normalize edilir --------------------------
class TelefonServisEntegrasyonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
        cls.irak = Ulke.objects.create(kod="IQ", ad="IRAK")
        cls.kaynak = AdayMusteriKategori.objects.create(ad="TEST KAYNAK", kod="99")

    def test_aday_musteri_telefon_normalize_edilir(self):
        a = aday_musteri_olustur(
            unvan="aday firma", para_birimi="TRY", ulke_id=self.tr.pk,
            kategori_id=self.kaynak.pk,
            tip_id=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk,
            asama_id=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk,
            telefon="05322070709", telefon_2="+964 750 448 2012")
        self.assertEqual(a.telefon, "+90 532 207 07 09")
        self.assertEqual(a.telefon_2, "+964 750 448 2012")

    def test_aday_musteri_telefon_gecersizse_uyari_kaydedilir_engellemez(self):
        a = aday_musteri_olustur(
            unvan="aday firma 2", para_birimi="TRY", ulke_id=self.tr.pk,
            kategori_id=self.kaynak.pk,
            tip_id=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk,
            asama_id=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk,
            telefon="123")
        self.assertEqual(a.telefon, "123")   # değiştirilmedi
        self.assertEqual(len(a.telefon_uyarilari), 1)
        self.assertIn("doğrulanamadı", a.telefon_uyarilari[0])

    def test_cari_telefon_normalize_edilir(self):
        c = cari_olustur(unvan="cari firma", para_birimi="TRY", ulke_id=self.tr.pk,
                         telefon="5322070709")
        self.assertEqual(c.telefon, "+90 532 207 07 09")
        self.assertEqual(c.telefon_uyarilari, [])

    def test_cari_ulkesi_yoksa_varsayilan_tr_ile_normalize_edilir(self):
        c = cari_olustur(unvan="ulkesiz cari", para_birimi="TRY", telefon="05322070709")
        self.assertEqual(c.telefon, "+90 532 207 07 09")

    def test_aday_yetkili_telefonu_adayin_ulkesiyle_normalize_edilir(self):
        a = aday_musteri_olustur(
            unvan="irak aday", para_birimi="TRY", ulke_id=self.irak.pk,
            kategori_id=self.kaynak.pk,
            tip_id=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk,
            asama_id=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk)
        y = aday_yetkili_ekle(a, ad_soyad="veli", telefon="+964 750 448 2012")
        self.assertEqual(y.telefon, "+964 750 448 2012")

    def test_cari_yetkili_telefonu_carinin_ulkesiyle_normalize_edilir(self):
        c = cari_olustur(unvan="yetkilili cari", para_birimi="TRY", ulke_id=self.tr.pk)
        y = cari_yetkili_ekle(c, ad_soyad="ali", telefon="05322070709")
        self.assertEqual(y.telefon, "+90 532 207 07 09")


# --- Migration 0136: veri taşıma + geri alma (gerçek servis çağrısı OLMADAN, doğrudan
#     ORM ile "eski format" veri yazılıp migration fonksiyonları çağrılır) ------------------
class Migrasyon0136Test(TestCase):
    """0136 saf DML (şema değişikliği yok) olduğu için gerçek migrate/rollback yerine
    migration modülünün _tasi/_geri_al fonksiyonları doğrudan (canlı apps registry ile)
    çağrılabilir — TransactionTestCase/serialized_rollback gerekmez (bkz. 0132 testinin
    aksine, burada CREATE/DROP TABLE yok)."""

    @classmethod
    def setUpTestData(cls):
        cls.tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
        cls.tip = AdayTipTanim.objects.get(sistem_kodu="ADAY")
        cls.asama = AdayAsamaTanim.objects.get(sistem_kodu="YENI")

    def _migrasyon(self):
        return importlib.import_module("core.migrations.0136_aday_cari_telefon_normalize")

    def test_veri_tasima_ve_geri_alma(self):
        migrasyon = self._migrasyon()
        # Servis katmanını BİLEREK atlayıp doğrudan ORM ile "eski format" (normalize
        # edilmemiş) veri yazılır — migration'ın gerçek üretim senaryosunu (2026-09-27
        # öncesi kayıtlar) simüle etmesi için.
        aday = AdayMusteri.objects.create(
            unvan="ESKİ FORMAT ADAY", tip=self.tip, asama=self.asama, ulke=self.tr,
            para_birimi="TRY", telefon="05322070709", telefon_2="123")
        cari = Cari.objects.create(
            kod="CAR-9001", unvan="ESKİ FORMAT CARİ", ulke=self.tr, para_birimi="TRY",
            telefon="+905327024005")
        yetkili = CariYetkili.objects.create(cari=cari, ad_soyad="VELİ", telefon="5322070709")

        with tempfile.TemporaryDirectory() as tmp:
            yedek_yolu = Path(tmp) / "yedek.json"
            with mock.patch.object(migrasyon, "_YEDEK_YOLU", yedek_yolu):
                migrasyon._tasi(canli_apps, None)

                aday.refresh_from_db()
                cari.refresh_from_db()
                yetkili.refresh_from_db()
                self.assertEqual(aday.telefon, "+90 532 207 07 09")
                self.assertEqual(aday.telefon_2, "123")           # doğrulanamadı, değişmedi
                self.assertEqual(cari.telefon, "+90 532 702 40 05")
                self.assertEqual(yetkili.telefon, "+90 532 207 07 09")

                self.assertTrue(yedek_yolu.exists())
                yedek = json.loads(yedek_yolu.read_text(encoding="utf-8"))
                self.assertEqual(yedek["AdayMusteri.telefon"][str(aday.pk)], "05322070709")
                self.assertNotIn(str(aday.pk), yedek.get("AdayMusteri.telefon_2", {}))

                migrasyon._geri_al(canli_apps, None)
                aday.refresh_from_db()
                cari.refresh_from_db()
                yetkili.refresh_from_db()
                self.assertEqual(aday.telefon, "05322070709")
                self.assertEqual(cari.telefon, "+905327024005")
                self.assertEqual(yetkili.telefon, "5322070709")

    def test_yedek_dosyasi_yoksa_geri_alma_sessizce_atlar(self):
        migrasyon = self._migrasyon()
        with tempfile.TemporaryDirectory() as tmp:
            yedek_yolu = Path(tmp) / "hic-olusmayan.json"
            with mock.patch.object(migrasyon, "_YEDEK_YOLU", yedek_yolu):
                migrasyon._geri_al(canli_apps, None)   # hata fırlatmamalı
