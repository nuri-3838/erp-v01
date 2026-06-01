"""Birim (STOKLAR ilk aşama) testleri — servis CRUD, ondalık doğrulama, TR büyük harf,
DB kısıtı, 5 seed birimi, view + yetki."""
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, EkranYetki
from core.services.birim import (BirimHatasi, aktif_birimler, birim_guncelle,
                                  birim_olustur, birim_sil)


class BirimServisTest(TestCase):
    def test_olustur_tr_buyuk_harf(self):
        b = birim_olustur(ad="kilogram", kisa_ad="kg", ondalik=3)
        self.assertEqual((b.ad, b.kisa_ad, b.ondalik), ("KİLOGRAM", "KG", 3))
        self.assertEqual(birim_olustur(ad="litre", kisa_ad="lt", ondalik=2).ad, "LİTRE")

    def test_ondalik_dogrulama(self):
        birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)    # 0 geçerli
        birim_olustur(ad="metre", kisa_ad="mt", ondalik=6)   # 6 geçerli
        for i, kotu in enumerate((7, -1, "abc", None)):
            with self.assertRaises(BirimHatasi):
                birim_olustur(ad=f"kotu{i}", kisa_ad="x", ondalik=kotu)

    def test_bos_ad_red(self):
        with self.assertRaises(BirimHatasi):
            birim_olustur(ad="   ", kisa_ad="kg", ondalik=0)
        with self.assertRaises(BirimHatasi):
            birim_olustur(ad="kg", kisa_ad="", ondalik=0)

    def test_guncelle(self):
        b = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        birim_guncelle(b, ad="adet yeni", kisa_ad="adt", ondalik=1, aktif=False)
        b.refresh_from_db()
        self.assertEqual((b.ad, b.kisa_ad, b.ondalik, b.aktif),
                         ("ADET YENİ", "ADT", 1, False))

    def test_sil_soft_delete(self):
        b = birim_olustur(ad="kutu", kisa_ad="kt", ondalik=0)
        birim_sil(b)
        b.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertNotIn(b, list(aktif_birimler()))          # listeden kalkar
        with self.assertRaises(BirimHatasi):                 # silinmiş düzenlenemez
            birim_guncelle(b, ad="x", kisa_ad="x", ondalik=0, aktif=True)

    def test_pasif_birim_listede_kalir(self):
        b = birim_olustur(ad="boy", kisa_ad="boy", ondalik=0, aktif=False)
        self.assertIn(b, list(aktif_birimler()))             # pasif ama silinmemiş -> listede

    def test_db_kisit_ondalik_7_red(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Birim.objects.create(ad="X", kisa_ad="X", ondalik=7)


class BirimSeedTest(TestCase):
    def test_5_seed_birimi(self):
        call_command("seed_birimler")
        self.assertEqual(set(Birim.objects.values_list("ad", flat=True)),
                         {"ADET", "BOY", "KİLOGRAM", "KUTU", "METRE"})
        kg = Birim.objects.get(ad="KİLOGRAM")
        self.assertEqual((kg.kisa_ad, kg.ondalik), ("KG", 3))
        self.assertEqual(Birim.objects.get(ad="ADET").ondalik, 0)

    def test_idempotent(self):
        call_command("seed_birimler")
        call_command("seed_birimler")
        self.assertEqual(Birim.objects.filter(silindi=False).count(), 5)


class BirimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="birimler")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_liste_render(self):
        birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:birimler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ADET")
        self.assertContains(r, "+ Yeni")

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:birim_ekle"),
                             {"ad": "kilogram", "kisa_ad": "kg", "ondalik": "3", "aktif": "on"})
        self.assertEqual(r.status_code, 302)
        b = Birim.objects.get(ad="KİLOGRAM")
        self.assertEqual((b.kisa_ad, b.ondalik, b.aktif), ("KG", 3, True))

    def test_ekle_gecersiz_ondalik(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:birim_ekle"),
                             {"ad": "x", "kisa_ad": "x", "ondalik": "9", "aktif": "on"})
        self.assertEqual(r.status_code, 200)                 # formda kalır (kaydedilmez)
        self.assertFalse(Birim.objects.filter(ad="X").exists())

    def test_duzenle_post_aktif_kapanir(self):
        b = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:birim_duzenle", args=[b.pk]),
                             {"ad": "adet", "kisa_ad": "adt", "ondalik": "0"})  # aktif yok
        self.assertEqual(r.status_code, 302)
        b.refresh_from_db()
        self.assertEqual((b.kisa_ad, b.aktif), ("ADT", False))

    def test_sil_post_soft_delete(self):
        b = birim_olustur(ad="kutu", kisa_ad="kt", ondalik=0)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:birim_sil", args=[b.pk]))
        self.assertEqual(r.status_code, 302)
        b.refresh_from_db()
        self.assertTrue(b.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:birimler")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:stoklar")).status_code, 403)

    def test_placeholder_ekran(self):
        EkranYetki.objects.create(kullanici=self.bos, ekran_kod="stoklar")
        self.client.force_login(self.bos)
        r = self.client.get(reverse("core:stoklar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Yakında")

    def test_menude_stoklar_modulu(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:birimler"))
        self.assertContains(r, "Stoklar")                    # modül başlığı
        self.assertContains(r, "Birimler")
