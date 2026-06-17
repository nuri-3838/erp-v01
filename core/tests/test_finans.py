"""FİNANS — Kasa tanımı servis + view testleri. Bakiye saklanmaz; yaprak muhasebe
hesabına bağlanır (üst hesap reddedilir), ad TR büyük + benzersiz."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, Kasa
from core.services.finans import FinansHatasi, kasa_guncelle, kasa_olustur, kasa_sil


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class KasaServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("100", "KASA")             # üst (yaprak değil)
        _hesap("100.01", "MERKEZ KASA")   # yaprak
        _hesap("100.02", "DÖVİZ KASA")    # yaprak

    def test_olustur_tr_buyuk_ve_hesap(self):
        k = kasa_olustur(ad="merkez kasa", para_birimi="TRY", muhasebe_kodu="100.01")
        self.assertEqual(k.ad, "MERKEZ KASA")
        self.assertEqual(k.muhasebe.hesap_kodu, "100.01")

    def test_ust_hesap_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="X", muhasebe_kodu="100")

    def test_hesapsiz_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="Y", muhasebe_kodu="")

    def test_ad_benzersiz(self):
        kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="ana", muhasebe_kodu="100.02")

    def test_guncelle_ve_sil(self):
        k = kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        kasa_guncelle(k, ad="ANA2", para_birimi="USD", muhasebe_kodu="100.02")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.para_birimi, k.muhasebe.hesap_kodu), ("ANA2", "USD", "100.02"))
        kasa_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)


class KasaViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        _hesap("100", "KASA")
        _hesap("100.01", "MERKEZ KASA")

    def test_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kasa_ekle"),
                             {"ad": "merkez", "para_birimi": "TRY", "muhasebe": "100.01"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Kasa.objects.filter(ad="MERKEZ", muhasebe__hesap_kodu="100.01").exists())

    def test_liste_200_ve_yetkisiz_403(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 200)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 403)
