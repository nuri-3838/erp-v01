"""STOKLAR Faz B testleri: Depo CRUD + stok hareketleri (giriş/çıkış, eldeki miktar,
yetersiz stok kontrolü, depo bazında bakiye), servis + view + yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Depo, Kategori, Stok, StokHareket
from core.services.depo import DepoHatasi, depo_olustur, depo_sil
from core.services.hareket import (HareketHatasi, depo_bazinda_eldeki, eldeki_miktar,
                                   hareket_ekle, hareket_sil)

D = datetime.date


def _stok():
    ust = Kategori.objects.create(ad="HAMMADDE", kod="150")
    alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
    kg = Birim.objects.create(ad="KİLOGRAM", kisa_ad="KG", ondalik=3)
    return Stok.objects.create(kod="150-10-0001", ad="ALÜMİNYUM LEVHA", kategori=alt,
                               uretim_birimi=kg, fatura_birimi=kg)


class DepoServisTest(TestCase):
    def test_olustur_tr_buyuk(self):
        d = depo_olustur(kod="ana", ad="ana depo")
        self.assertEqual((d.kod, d.ad), ("ANA", "ANA DEPO"))

    def test_kod_benzersiz(self):
        depo_olustur(kod="01", ad="bir")
        with self.assertRaises(DepoHatasi):
            depo_olustur(kod="01", ad="iki")

    def test_sil_hareketsiz(self):
        d = depo_olustur(kod="01", ad="depo")
        depo_sil(d)
        d.refresh_from_db()
        self.assertTrue(d.silindi)

    def test_hareketli_depo_silinemez(self):
        d = depo_olustur(kod="01", ad="depo")
        s = _stok()
        hareket_ekle(stok_id=s.pk, depo_id=d.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="10")
        with self.assertRaises(DepoHatasi):
            depo_sil(d)


class HareketServisTest(TestCase):
    def setUp(self):
        self.s = _stok()
        self.d1 = depo_olustur(kod="01", ad="ANA")
        self.d2 = depo_olustur(kod="02", ad="ÜRETİM")

    def test_giris_cikis_eldeki(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="1.000")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="300")
        self.assertEqual(eldeki_miktar(self.s), Decimal("700.000"))
        self.assertEqual(eldeki_miktar(self.s, self.d1), Decimal("700.000"))

    def test_yetersiz_stok_cikis_red(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="100")
        with self.assertRaises(HareketHatasi):
            hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                         tur="CIKIS", miktar="150")

    def test_depo_bazinda(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="100")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d2.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="40")
        bazinda = dict((d.pk, m) for d, m in depo_bazinda_eldeki(self.s))
        self.assertEqual(bazinda[self.d1.pk], Decimal("100.000"))
        self.assertEqual(bazinda[self.d2.pk], Decimal("40.000"))
        self.assertEqual(eldeki_miktar(self.s), Decimal("140.000"))

    def test_giris_silme_negatife_dusuremez(self):
        g = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                         tur="GIRIS", miktar="100")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="80")
        with self.assertRaises(HareketHatasi):   # girişi silersek eldeki -? negatif
            hareket_sil(g)


class FazBViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.s = _stok()

    def test_depo_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:depo_ekle"), {"kod": "01", "ad": "ana depo"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Depo.objects.filter(kod="01", ad="ANA DEPO").exists())

    def test_hareket_ekle_post_ve_detayda_eldeki(self):
        d = depo_olustur(kod="01", ad="ANA")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:stok_hareket_ekle", args=[self.s.pk]), {
            "depo": str(d.pk), "tur": "GIRIS", "miktar": "250", "tarih": "2026-06-01",
            "aciklama": "açılış"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(eldeki_miktar(self.s), Decimal("250.000"))
        det = self.client.get(reverse("core:stok_detay", args=[self.s.pk]))
        self.assertContains(det, "250,000")

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:depolar")).status_code, 403)
