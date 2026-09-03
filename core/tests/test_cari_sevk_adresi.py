"""Cari sevk (teslimat) adresi (çoklu, CariBanka ile aynı varsayılan-yönetimi deseni)
testleri: servis, view (cari-scoped CRUD + detay sekmesi), yetki."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import CariSevkAdresi, EkranYetki, Sehir, Ulke
from core.services.cari import (
    CariHatasi, aktif_sevk_adresleri, cari_olustur, sevk_adresi_ekle, sevk_adresi_guncelle,
    sevk_adresi_sil,
)


def _cari(unvan="test cari"):
    return cari_olustur(unvan=unvan, para_birimi="TRY")


def _lokasyon():
    tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
    kayseri = Sehir.objects.create(ulke=tr, ad="KAYSERİ", kod="38")
    return tr, kayseri


class CariSevkAdresiServisTest(TestCase):
    def test_ilk_adres_varsayilan(self):
        c = _cari()
        s = sevk_adresi_ekle(c, ad="depo 1")
        self.assertTrue(s.varsayilan)
        self.assertEqual(s.ad, "DEPO 1")

    def test_ikinci_default_eskiyi_kapatir(self):
        c = _cari()
        s1 = sevk_adresi_ekle(c, ad="depo 1")
        s2 = sevk_adresi_ekle(c, ad="depo 2", varsayilan=True)
        s1.refresh_from_db()
        self.assertFalse(s1.varsayilan)
        self.assertTrue(s2.varsayilan)

    def test_varsayilan_silinince_promote(self):
        c = _cari()
        s1 = sevk_adresi_ekle(c, ad="depo 1")          # varsayılan
        s2 = sevk_adresi_ekle(c, ad="depo 2")
        sevk_adresi_sil(s1)
        s2.refresh_from_db()
        self.assertTrue(s2.varsayilan)

    def test_ad_bos_reddedilir(self):
        c = _cari()
        with self.assertRaises(CariHatasi):
            sevk_adresi_ekle(c, ad="  ")

    def test_ulke_sehir_opsiyonel(self):
        c = _cari()
        s = sevk_adresi_ekle(c, ad="depo 1", adres="bir yer")
        self.assertIsNone(s.ulke_id)
        self.assertIsNone(s.sehir_id)
        self.assertEqual(s.adres, "BİR YER")

    def test_ulke_sehir_ile(self):
        c = _cari()
        tr, kayseri = _lokasyon()
        s = sevk_adresi_ekle(c, ad="depo 1", ulke_id=tr.pk, sehir_id=kayseri.pk)
        self.assertEqual(s.ulke_id, tr.pk)
        self.assertEqual(s.sehir_id, kayseri.pk)

    def test_guncelle(self):
        c = _cari()
        s = sevk_adresi_ekle(c, ad="depo 1")
        s2 = sevk_adresi_guncelle(s, ad="depo 1 güncel", adres="yeni adres")
        self.assertEqual((s2.ad, s2.adres), ("DEPO 1 GÜNCEL", "YENİ ADRES"))

    def test_silinmis_duzenlenemez(self):
        c = _cari()
        s = sevk_adresi_ekle(c, ad="depo 1")
        sevk_adresi_sil(s)
        with self.assertRaises(CariHatasi):
            sevk_adresi_guncelle(s, ad="x")

    def test_aktif_sevk_adresleri_siralama(self):
        c = _cari()
        sevk_adresi_ekle(c, ad="b depo")
        sevk_adresi_ekle(c, ad="a depo", varsayilan=True)
        liste = list(aktif_sevk_adresleri(c))
        self.assertEqual(liste[0].ad, "A DEPO")            # varsayılan önce


class CariSevkAdresiViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="cariler")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.cari = _cari("formal")

    def test_detay_sekmesi_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:cari_detay", args=[self.cari.pk]))
        self.assertContains(r, "Sevk Adresleri")

    def test_sevk_adresi_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:sevk_adresi_ekle", args=[self.cari.pk]),
                             {"ad": "depo 1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CariSevkAdresi.objects.filter(cari=self.cari, ad="DEPO 1").exists())

    def test_birden_fazla_adres_eklenebilir(self):
        self.client.force_login(self.yetkili)
        self.client.post(reverse("core:sevk_adresi_ekle", args=[self.cari.pk]), {"ad": "depo 1"})
        self.client.post(reverse("core:sevk_adresi_ekle", args=[self.cari.pk]), {"ad": "depo 2"})
        self.client.post(reverse("core:sevk_adresi_ekle", args=[self.cari.pk]), {"ad": "depo 3"})
        self.assertEqual(
            CariSevkAdresi.objects.filter(cari=self.cari, silindi=False).count(), 3)

    def test_sevk_adresi_duzenle_post(self):
        s = sevk_adresi_ekle(self.cari, ad="depo 1")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:sevk_adresi_duzenle", args=[s.pk]),
                             {"ad": "depo 1 güncel"})
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertEqual(s.ad, "DEPO 1 GÜNCEL")

    def test_sevk_adresi_sil_post(self):
        s = sevk_adresi_ekle(self.cari, ad="depo 1")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:sevk_adresi_sil", args=[s.pk]))
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertTrue(s.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(
                reverse("core:sevk_adresi_ekle", args=[self.cari.pk])).status_code, 403)
