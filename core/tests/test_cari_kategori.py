"""Cari kategori (CARİLER Faz 2) testleri: 2 seviye + kod/ad üst-grup-içi benzersiz +
kod yolu + view + yetki + taşıma komutu."""
import json
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import CariKategori, EkranYetki
from core.services.cari_kategori import (CariKategoriHatasi, cari_kategori_guncelle,
                                         cari_kategori_olustur, cari_kategori_sil)


class CariKategoriServisTest(TestCase):
    def test_ust_olustur_buyuk_harf(self):
        k = cari_kategori_olustur(ad="müşteriler", kod="120")
        self.assertEqual((k.ad, k.kod), ("MÜŞTERİLER", "120"))
        self.assertIsNone(k.ust_id)

    def test_alt_ve_kod_yolu(self):
        ust = cari_kategori_olustur(ad="tedarikçiler", kod="320")
        alt = cari_kategori_olustur(ad="hammadde", kod="10", ust_id=ust.pk)
        self.assertEqual(alt.kod_yolu, "320-10")
        self.assertEqual(ust.kod_yolu, "320")

    def test_uc_seviye_engellenir(self):
        ust = cari_kategori_olustur(ad="t", kod="320")
        alt = cari_kategori_olustur(ad="h", kod="10", ust_id=ust.pk)
        with self.assertRaises(CariKategoriHatasi):
            cari_kategori_olustur(ad="x", kod="1", ust_id=alt.pk)

    def test_kod_ayni_ust_benzersiz_farkli_ust_serbest(self):
        u1 = cari_kategori_olustur(ad="müşteriler", kod="120")
        u2 = cari_kategori_olustur(ad="tedarikçiler", kod="320")
        cari_kategori_olustur(ad="yurtiçi", kod="10", ust_id=u1.pk)
        cari_kategori_olustur(ad="hammadde", kod="10", ust_id=u2.pk)   # farklı üst -> ok
        with self.assertRaises(CariKategoriHatasi):
            cari_kategori_olustur(ad="başka", kod="10", ust_id=u1.pk)  # aynı üst -> red

    def test_ad_ayni_ust_benzersiz(self):
        u = cari_kategori_olustur(ad="müşteriler", kod="120")
        cari_kategori_olustur(ad="yurtiçi", kod="10", ust_id=u.pk)
        with self.assertRaises(CariKategoriHatasi):
            cari_kategori_olustur(ad="yurtiçi", kod="20", ust_id=u.pk)

    def test_guncelle(self):
        k = cari_kategori_olustur(ad="müşteriler", kod="120")
        cari_kategori_guncelle(k, ad="müşteriler grup", kod="121")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.kod), ("MÜŞTERİLER GRUP", "121"))

    def test_alt_kategorili_ust_silinemez(self):
        ust = cari_kategori_olustur(ad="t", kod="320")
        alt = cari_kategori_olustur(ad="h", kod="10", ust_id=ust.pk)
        with self.assertRaises(CariKategoriHatasi):
            cari_kategori_sil(ust)
        cari_kategori_sil(alt)
        cari_kategori_sil(ust)
        ust.refresh_from_db()
        self.assertTrue(ust.silindi)


class CariKategoriTasimaTest(TestCase):
    def test_tasima_idempotent_ust_alt(self):
        veri = {"kategoriler": [
            {"id": 16, "ust": None, "kod": "320", "ad": "TEDARİKÇİLER", "aciklama": ""},
            {"id": 17, "ust": 16, "kod": "10", "ad": "HAMMADDE", "aciklama": ""},
            {"id": 13, "ust": None, "kod": "120", "ad": "MÜŞTERİLER", "aciklama": ""},
        ]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False)
            yol = f.name
        from django.core.management import call_command
        call_command("tasi_cari_kategori", yol)
        call_command("tasi_cari_kategori", yol)
        self.assertEqual(CariKategori.objects.filter(silindi=False).count(), 3)
        alt = CariKategori.objects.get(kod="10")
        self.assertEqual(alt.ust.kod, "320")
        self.assertEqual(alt.kod_yolu, "320-10")


class CariKategoriViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="cari_kategoriler")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_liste_render(self):
        ust = cari_kategori_olustur(ad="tedarikçiler", kod="320")
        cari_kategori_olustur(ad="hammadde", kod="10", ust_id=ust.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cari_kategoriler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TEDARİKÇİLER")
        self.assertContains(r, "320-10")
        self.assertContains(r, "+ Yeni Üst")

    def test_ekle_ust_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_kategori_ekle"),
                             {"ad": "müşteriler", "kod": "120"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(CariKategori.objects.get(kod="120").ust_id)

    def test_ekle_alt_post(self):
        ust = cari_kategori_olustur(ad="tedarikçiler", kod="320")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_kategori_ekle"),
                             {"ad": "hammadde", "kod": "10", "ust": str(ust.pk)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(CariKategori.objects.get(kod="10").ust_id, ust.pk)

    def test_sil_post(self):
        k = cari_kategori_olustur(ad="müşteriler", kod="120")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:cari_kategori_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:cari_kategoriler")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("core:cari_kategori_ekle")).status_code, 403)
