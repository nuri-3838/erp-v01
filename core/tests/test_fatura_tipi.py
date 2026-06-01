"""Fatura tipi (STOKLAR Faz 1) testleri — servis CRUD, TR büyük harf, benzersizlik,
yön doğrulama, DB kısmi-unique, 8 seed (4+4), view + yetki."""
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, FaturaTipi
from core.services.fatura_tipi import (FaturaTipiHatasi, aktif_fatura_tipleri,
                                       fatura_tipi_guncelle, fatura_tipi_olustur,
                                       fatura_tipi_sil)


class FaturaTipiServisTest(TestCase):
    def test_olustur_tr_buyuk_harf(self):
        t = fatura_tipi_olustur(ad="satış faturası", yon="SATIS", sira=10)
        self.assertEqual((t.ad, t.yon, t.sira), ("SATIŞ FATURASI", "SATIS", 10))

    def test_yon_gecersiz_red(self):
        with self.assertRaises(FaturaTipiHatasi):
            fatura_tipi_olustur(ad="x", yon="BOS")

    def test_bos_ad_red(self):
        with self.assertRaises(FaturaTipiHatasi):
            fatura_tipi_olustur(ad="   ", yon="SATIS")

    def test_benzersiz_ad_red(self):
        fatura_tipi_olustur(ad="alış faturası", yon="ALIS")
        with self.assertRaises(FaturaTipiHatasi):
            fatura_tipi_olustur(ad="ALIŞ FATURASI", yon="ALIS")   # aynı ad

    def test_guncelle(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS", sira=10)
        fatura_tipi_guncelle(t, ad="satış faturası", yon="SATIS", sira=15, aktif=False)
        t.refresh_from_db()
        self.assertEqual((t.ad, t.sira, t.aktif), ("SATIŞ FATURASI", 15, False))

    def test_guncelle_kendi_adi_serbest(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS")
        fatura_tipi_guncelle(t, ad="satış", yon="ALIS", sira=0, aktif=True)  # aynı ad, kendi
        t.refresh_from_db()
        self.assertEqual(t.yon, "ALIS")

    def test_sil_soft_delete_ve_pasif_listede(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS")
        p = fatura_tipi_olustur(ad="alış", yon="ALIS", aktif=False)
        fatura_tipi_sil(t)
        t.refresh_from_db()
        self.assertTrue(t.silindi)
        self.assertNotIn(t, list(aktif_fatura_tipleri()))     # silinen listeden kalkar
        self.assertIn(p, list(aktif_fatura_tipleri()))        # pasif ama silinmemiş kalır
        with self.assertRaises(FaturaTipiHatasi):
            fatura_tipi_guncelle(t, ad="x", yon="SATIS", sira=0, aktif=True)

    def test_silinen_ad_yeniden_kullanilabilir(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS")
        fatura_tipi_sil(t)
        # silinen ad serbest: kısmi unique yalnız silinmemişlerde
        t2 = fatura_tipi_olustur(ad="satış", yon="SATIS")
        self.assertNotEqual(t.pk, t2.pk)

    def test_db_kismi_unique(self):
        FaturaTipi.objects.create(ad="X", yon="SATIS")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FaturaTipi.objects.create(ad="X", yon="ALIS")


class FaturaTipiSeedTest(TestCase):
    def test_8_seed(self):
        call_command("seed_fatura_tipleri")
        self.assertEqual(FaturaTipi.objects.filter(silindi=False).count(), 8)
        self.assertEqual(
            FaturaTipi.objects.filter(silindi=False, yon="SATIS").count(), 4)
        self.assertEqual(
            FaturaTipi.objects.filter(silindi=False, yon="ALIS").count(), 4)
        self.assertTrue(
            FaturaTipi.objects.filter(ad="SATIŞ FATURASI-İHRAÇ KAYITLI").exists())

    def test_idempotent(self):
        call_command("seed_fatura_tipleri")
        call_command("seed_fatura_tipleri")
        self.assertEqual(FaturaTipi.objects.filter(silindi=False).count(), 8)


class FaturaTipiViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="fatura_tipleri")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_liste_render_gruplu(self):
        fatura_tipi_olustur(ad="satış faturası", yon="SATIS", sira=10)
        fatura_tipi_olustur(ad="alış faturası", yon="ALIS", sira=50)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_tipleri"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SATIŞ FATURASI")
        self.assertContains(r, "Satış İçin")
        self.assertContains(r, "Alış İçin")
        self.assertContains(r, "+ Yeni")

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:fatura_tipi_ekle"),
                             {"ad": "satış faturası", "yon": "SATIS",
                              "sira": "10", "aktif": "on"})
        self.assertEqual(r.status_code, 302)
        t = FaturaTipi.objects.get(ad="SATIŞ FATURASI")
        self.assertEqual((t.yon, t.sira, t.aktif), ("SATIS", 10, True))

    def test_ekle_tekrar_ad_formda_kalir(self):
        fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:fatura_tipi_ekle"),
                             {"ad": "satış faturası", "yon": "ALIS", "sira": "0"})
        self.assertEqual(r.status_code, 200)        # formda kalır (kaydedilmez)
        self.assertEqual(FaturaTipi.objects.filter(silindi=False).count(), 1)

    def test_duzenle_post(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS", sira=10)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:fatura_tipi_duzenle", args=[t.pk]),
                             {"ad": "satış faturası", "yon": "SATIS", "sira": "12"})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual((t.ad, t.sira, t.aktif), ("SATIŞ FATURASI", 12, False))

    def test_sil_post_soft_delete(self):
        t = fatura_tipi_olustur(ad="satış", yon="SATIS")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:fatura_tipi_sil", args=[t.pk]))
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertTrue(t.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:fatura_tipleri")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("core:fatura_tipi_ekle")).status_code, 403)

    def test_menude_fatura_tipleri(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_tipleri"))
        self.assertContains(r, "Fatura Tipleri")
