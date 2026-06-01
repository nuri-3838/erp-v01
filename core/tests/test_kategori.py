"""Kategori (STOKLAR) testleri — servis CRUD + 2 seviye kuralı + yaprak hesap bağı +
view + yetki + akıllı arama kaynağı (yaprak hesap listesi)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, HesapPlani, Kategori
from core.services.kategori import (KategoriHatasi, kategori_guncelle,
                                     kategori_olustur, kategori_sil)


def _hesaplar():
    """153 (üst/ara) + 153.10 (yaprak) — hesap bağı testleri için."""
    ust = HesapPlani.objects.create(
        hesap_kodu="153", hesap_adi="TİCARİ MALLAR",
        rapor_grubu="BILANCO", rapor_kalemi="DV")
    yaprak = HesapPlani.objects.create(
        hesap_kodu="153.10", hesap_adi="ALÜMİNYUM",
        rapor_grubu="BILANCO", rapor_kalemi="DV", ust_hesap=ust)
    return ust, yaprak


class KategoriServisTest(TestCase):
    def test_ust_olustur_tr_buyuk_harf(self):
        k = kategori_olustur(ad="hammadde")
        self.assertEqual(k.ad, "HAMMADDE")
        self.assertIsNone(k.ust_id)

    def test_alt_olustur(self):
        ust = kategori_olustur(ad="hammadde")
        alt = kategori_olustur(ad="alüminyum", ust_id=ust.pk)
        self.assertEqual(alt.ad, "ALÜMİNYUM")
        self.assertEqual(alt.ust_id, ust.pk)

    def test_uc_seviye_engellenir(self):
        ust = kategori_olustur(ad="hammadde")
        alt = kategori_olustur(ad="alüminyum", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="6063", ust_id=alt.pk)   # alt'ın altına olmaz

    def test_ust_yok_red(self):
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="x", ust_id=9999)

    def test_bos_ad_red(self):
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="   ")

    def test_hesap_bagla_yaprak(self):
        _, yaprak = _hesaplar()
        k = kategori_olustur(ad="alüminyum", hesap_kodu="153.10")
        self.assertEqual(k.hesap_id, yaprak.hesap_kodu)

    def test_hesap_yaprak_degil_red(self):
        _hesaplar()
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="x", hesap_kodu="153")   # 153 alt hesabı olan ara hesap

    def test_hesap_yok_red(self):
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="x", hesap_kodu="999")

    def test_guncelle_ad_ve_hesap(self):
        _, yaprak = _hesaplar()
        k = kategori_olustur(ad="alüminyum")
        kategori_guncelle(k, ad="alüminyum profil", hesap_kodu="153.10")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.hesap_id), ("ALÜMİNYUM PROFİL", "153.10"))
        kategori_guncelle(k, ad="alüminyum profil", hesap_kodu="")  # bağ kaldır
        k.refresh_from_db()
        self.assertIsNone(k.hesap_id)

    def test_sil_soft_delete(self):
        k = kategori_olustur(ad="hammadde")
        kategori_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        with self.assertRaises(KategoriHatasi):       # silinmiş düzenlenemez
            kategori_guncelle(k, ad="x", hesap_kodu="")

    def test_alt_kategorili_ust_silinemez(self):
        ust = kategori_olustur(ad="hammadde")
        alt = kategori_olustur(ad="alüminyum", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):
            kategori_sil(ust)                          # alt kategorisi var
        kategori_sil(alt)                              # önce alt
        kategori_sil(ust)                              # sonra üst -> serbest
        ust.refresh_from_db()
        self.assertTrue(ust.silindi)


class KategoriViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="kategoriler")
        cls.bos = User.objects.create_user("bos", password="x")
        _hesaplar()

    def test_liste_render(self):
        ust = kategori_olustur(ad="hammadde")
        kategori_olustur(ad="alüminyum", ust_id=ust.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kategoriler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "HAMMADDE")
        self.assertContains(r, "ALÜMİNYUM")
        self.assertContains(r, "+ Yeni Üst")

    def test_ekle_ust_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"), {"ad": "hammadde"})
        self.assertEqual(r.status_code, 302)
        k = Kategori.objects.get(ad="HAMMADDE")
        self.assertIsNone(k.ust_id)

    def test_ekle_alt_post(self):
        ust = kategori_olustur(ad="hammadde")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"),
                             {"ad": "alüminyum", "ust": str(ust.pk)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Kategori.objects.get(ad="ALÜMİNYUM").ust_id, ust.pk)

    def test_ekle_hesap_bagla_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"),
                             {"ad": "alüminyum", "hesap": "153.10"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Kategori.objects.get(ad="ALÜMİNYUM").hesap_id, "153.10")

    def test_ekle_sayfa_yaprak_hesaplari_listeler(self):
        """Akıllı arama kaynağı: hesap seçimi YALNIZCA yaprak hesapları sunar."""
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:kategori_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="153.10"')      # yaprak hesap seçilebilir
        self.assertNotContains(r, 'value="153"')       # ara hesap listede yok

    def test_duzenle_post(self):
        k = kategori_olustur(ad="hammadde")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_duzenle", args=[k.pk]),
                             {"ad": "hammadde grup", "hesap": "153.10"})
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertEqual((k.ad, k.hesap_id), ("HAMMADDE GRUP", "153.10"))

    def test_sil_post_soft_delete(self):
        k = kategori_olustur(ad="hammadde")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_sil_alt_kategorili_ust_engellenir(self):
        ust = kategori_olustur(ad="hammadde")
        kategori_olustur(ad="alüminyum", ust_id=ust.pk)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_sil", args=[ust.pk]))
        self.assertEqual(r.status_code, 302)
        ust.refresh_from_db()
        self.assertFalse(ust.silindi)                  # silinemedi (alt var)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kategoriler")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:kategori_ekle")).status_code, 403)
