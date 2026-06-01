"""Lokasyon (CARİLER — Ülke/Şehir) testleri: servis CRUD, TR büyük harf, benzersizlik,
ülke silme kuralı, view + yetki, taşıma komutu (idempotent)."""
import json
import tempfile

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Sehir, Ulke
from core.services.lokasyon import (LokasyonHatasi, sehir_olustur, sehir_sil,
                                     ulke_guncelle, ulke_olustur, ulke_sil)


class LokasyonServisTest(TestCase):
    def test_ulke_olustur_buyuk_harf(self):
        u = ulke_olustur(kod="tr", ad="türkiye", ad_en="turkey")
        self.assertEqual((u.kod, u.ad, u.ad_en), ("TR", "TÜRKİYE", "TURKEY"))

    def test_ulke_kod_benzersiz(self):
        ulke_olustur(kod="TR", ad="türkiye")
        with self.assertRaises(LokasyonHatasi):
            ulke_olustur(kod="tr", ad="başka")

    def test_ulke_kod_uzunluk(self):
        with self.assertRaises(LokasyonHatasi):
            ulke_olustur(kod="TUR", ad="x")

    def test_sehir_olustur_ve_benzersiz(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        sehir_olustur(ulke_id=u.pk, ad="kayseri", kod="38")
        s = Sehir.objects.get(ad="KAYSERİ")
        self.assertEqual((s.kod, s.ulke_id), ("38", u.pk))
        with self.assertRaises(LokasyonHatasi):
            sehir_olustur(ulke_id=u.pk, ad="KAYSERİ")          # aynı ülkede aynı ad

    def test_sehir_farkli_ulkede_serbest(self):
        tr = ulke_olustur(kod="TR", ad="türkiye")
        de = ulke_olustur(kod="DE", ad="almanya")
        sehir_olustur(ulke_id=tr.pk, ad="merkez")
        sehir_olustur(ulke_id=de.pk, ad="merkez")              # farklı ülke -> serbest
        self.assertEqual(Sehir.objects.filter(ad="MERKEZ").count(), 2)

    def test_ulke_sehirli_silinemez(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        s = sehir_olustur(ulke_id=u.pk, ad="kayseri")
        with self.assertRaises(LokasyonHatasi):
            ulke_sil(u)
        sehir_sil(s)
        ulke_sil(u)
        u.refresh_from_db()
        self.assertTrue(u.silindi)

    def test_ulke_guncelle(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        ulke_guncelle(u, kod="TR", ad="türkiye cumhuriyeti", ad_en="turkiye")
        u.refresh_from_db()
        self.assertEqual(u.ad, "TÜRKİYE CUMHURİYETİ")


class LokasyonTasimaTest(TestCase):
    def test_tasima_idempotent(self):
        veri = {
            "ulkeler": [{"kod": "TR", "ad": "TÜRKİYE", "ad_en": "TURKEY"},
                        {"kod": "DE", "ad": "ALMANYA", "ad_en": "GERMANY"}],
            "sehirler": [{"ulke": "TR", "kod": "38", "ad": "KAYSERİ", "ad_en": ""},
                         {"ulke": "DE", "kod": "", "ad": "BERLİN", "ad_en": "BERLIN"}],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False)
            yol = f.name
        call_command("tasi_lokasyon", yol)
        call_command("tasi_lokasyon", yol)                     # ikinci kez -> atlar
        self.assertEqual(Ulke.objects.filter(silindi=False).count(), 2)
        self.assertEqual(Sehir.objects.filter(silindi=False).count(), 2)
        self.assertEqual(Sehir.objects.get(ad="KAYSERİ").ulke.kod, "TR")


class LokasyonViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="lokasyonlar")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_liste_render(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        sehir_olustur(ulke_id=u.pk, ad="kayseri", kod="38")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:lokasyonlar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TÜRKİYE")
        self.assertContains(r, "KAYSERİ")
        self.assertContains(r, "+ Yeni Ülke")
        self.assertContains(r, "+ Yeni Şehir")

    def test_ulke_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:ulke_ekle"),
                             {"kod": "tr", "ad": "türkiye", "ad_en": "turkey"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Ulke.objects.filter(kod="TR", ad="TÜRKİYE").exists())

    def test_sehir_ekle_post(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:sehir_ekle"),
                             {"ulke": str(u.pk), "ad": "kayseri", "kod": "38"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Sehir.objects.filter(ad="KAYSERİ", ulke=u).exists())

    def test_ulke_sil_post(self):
        u = ulke_olustur(kod="TR", ad="türkiye")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:ulke_sil", args=[u.pk]))
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertTrue(u.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:lokasyonlar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:ulke_ekle")).status_code, 403)
