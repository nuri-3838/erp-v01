"""Tanım listeleri (AYARLAR) testleri: KDV + Tevkifat oranları servis CRUD + view + yetki."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, KdvOrani, TevkifatOrani
from core.services.tanim import (TanimHatasi, kdv_orani_olustur, kdv_orani_guncelle,
                                  tevkifat_orani_olustur, tevkifat_orani_guncelle)


def _hesap(kod="191", ad="İNDİRİLECEK KDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV")


class KdvOraniServisTest(TestCase):
    def test_olustur(self):
        h = _hesap()
        k = kdv_orani_olustur(aciklama="genel oran", oran="20", sira=10,
                              hesap_kodu="191")
        self.assertEqual((k.aciklama, k.oran, k.sira, k.hesap_id),
                         ("GENEL ORAN", Decimal("20.00"), 10, "191"))

    def test_aciklama_zorunlu(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="  ", oran="20")

    def test_oran_negatif_red(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="x", oran="-1")

    def test_hesap_yok_red(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="x", oran="20", hesap_kodu="999")

    def test_hesapsiz_serbest(self):
        k = kdv_orani_olustur(aciklama="x", oran="10")
        self.assertIsNone(k.hesap_id)

    def test_guncelle(self):
        k = kdv_orani_olustur(aciklama="x", oran="10")
        kdv_orani_guncelle(k, aciklama="indirimli", oran="10", sira=5, hesap_kodu="")
        k.refresh_from_db()
        self.assertEqual((k.aciklama, k.sira), ("İNDİRİMLİ", 5))


class TevkifatOraniServisTest(TestCase):
    def test_olustur(self):
        t = tevkifat_orani_olustur(kod="601", pay=5, payda=10, aciklama="yarım")
        self.assertEqual((t.kod, t.pay, t.payda, t.aciklama), ("601", 5, 10, "YARIM"))

    def test_kod_benzersiz(self):
        tevkifat_orani_olustur(kod="601", pay=5, payda=10)
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="601", pay=7, payda=10)

    def test_payda_pozitif(self):
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="x", pay=5, payda=0)

    def test_kod_zorunlu(self):
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="  ", pay=5, payda=10)


class TanimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_hub_ve_listeler(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:tanim_listeleri"))
        self.assertContains(r, "KDV Oranları")
        self.assertContains(r, "Tevkifat Oranları")
        self.assertEqual(self.client.get(reverse("core:kdv_oranlari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:tevkifat_oranlari")).status_code, 200)

    def test_kdv_ekle_post(self):
        _hesap()
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kdv_orani_ekle"),
                             {"sira": "10", "aciklama": "genel", "oran": "20", "hesap": "191"})
        self.assertEqual(r.status_code, 302)
        k = KdvOrani.objects.get(aciklama="GENEL")
        self.assertEqual((k.oran, k.hesap_id), (Decimal("20.00"), "191"))

    def test_tevkifat_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:tevkifat_orani_ekle"),
                             {"kod": "601", "pay": "5", "payda": "10", "aciklama": "yarım"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(TevkifatOrani.objects.filter(kod="601").exists())

    def test_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:tanim_listeleri")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:kdv_oranlari")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:tevkifat_oranlari")).status_code, 403)
