"""Aday Müşteri: yeni Tip/Potansiyel/Aşama/Kapanış nedeni alanları — servis doğrulaması,
form clean() kuralı, liste filtreleri, detay/form şablon çıktısı."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import AdayMusteriForm
from core.services.aday import AdayHatasi, aday_musteri_guncelle, aday_musteri_olustur


class AdayMusteriTipServisTest(TestCase):
    def test_varsayilan_aday_yeni(self):
        a = aday_musteri_olustur(unvan="firma a")
        self.assertEqual(a.tip, "ADAY")
        self.assertEqual(a.asama, "YENI")
        self.assertEqual(a.potansiyel, "")
        self.assertEqual(a.kapanis_nedeni, "")

    def test_gecersiz_tip_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma b", tip="OLMAYAN_TIP")

    def test_gecersiz_potansiyel_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma c", potansiyel="COKYUKSEK")

    def test_kapali_asamada_kapanis_nedeni_zorunlu(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma d", asama="KAPALI")

    def test_kapali_asamada_kapanis_nedeni_ile_basarili(self):
        a = aday_musteri_olustur(unvan="firma e", asama="KAPALI", kapanis_nedeni="ILGISIZ")
        self.assertEqual(a.asama, "KAPALI")
        self.assertEqual(a.kapanis_nedeni, "ILGISIZ")

    def test_kapali_degilse_kapanis_nedeni_temizlenir(self):
        a = aday_musteri_olustur(unvan="firma f", asama="TEMAS", kapanis_nedeni="ILGISIZ")
        self.assertEqual(a.kapanis_nedeni, "")

    def test_guncelle_kapaliya_gecerse_nedensiz_reddedilir(self):
        a = aday_musteri_olustur(unvan="firma g")
        with self.assertRaises(AdayHatasi):
            aday_musteri_guncelle(a, unvan="firma g", asama="KAPALI")


def _form_temel_veri(**ek):
    veri = dict(
        unvan="test firma", ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
        ulke="", sehir="", kategori="", tip="ADAY", potansiyel="", asama="YENI",
        kapanis_nedeni="", para_birimi="TRY", iskonto_yuzdesi="0")
    veri.update(ek)
    return veri


class AdayMusteriFormCleanTest(TestCase):
    def test_kapali_nedensiz_gecersiz(self):
        form = AdayMusteriForm(_form_temel_veri(asama="KAPALI"))
        self.assertFalse(form.is_valid())
        self.assertIn("kapanis_nedeni", form.errors)

    def test_kapali_nedenle_gecerli(self):
        form = AdayMusteriForm(_form_temel_veri(asama="KAPALI", kapanis_nedeni="RAKIP"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["kapanis_nedeni"], "RAKIP")

    def test_kapali_degilse_nedeni_sessizce_temizler(self):
        form = AdayMusteriForm(_form_temel_veri(asama="TEMAS", kapanis_nedeni="RAKIP"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["kapanis_nedeni"], "")


class AdayMusteriTipListeViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tipyon", password="x")

    def test_tip_filtresi(self):
        aday_musteri_olustur(unvan="eski musteri x", tip="ESKI_MUSTERI")
        aday_musteri_olustur(unvan="aday y", tip="ADAY")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"tip": "ESKI_MUSTERI"})
        self.assertContains(r, "ESKİ MUSTERİ X")
        self.assertNotContains(r, "ADAY Y")

    def test_potansiyel_belirlenmedi_filtresi(self):
        aday_musteri_olustur(unvan="belirsiz z")
        aday_musteri_olustur(unvan="yuksek k", potansiyel="YUKSEK")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"potansiyel": "BOS"})
        self.assertContains(r, "BELİRSİZ Z")
        self.assertNotContains(r, "YUKSEK K")

    def test_asama_filtresi_kapali_kayitlar_varsayilanda_gorunur(self):
        a = aday_musteri_olustur(unvan="kapanan firma")
        aday_musteri_guncelle(a, unvan="kapanan firma", asama="KAPALI", kapanis_nedeni="DIGER")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "KAPANAN FİRMA")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"asama": "KAPALI"})
        self.assertContains(r2, "KAPANAN FİRMA")
        r3 = self.client.get(reverse("core:aday_musteriler"), {"asama": "YENI"})
        self.assertNotContains(r3, "KAPANAN FİRMA")

    def test_sayfa_boyutu_formu_filtreyi_korur(self):
        aday_musteri_olustur(unvan="filtreli firma", tip="ARACI")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"tip": "ARACI"})
        self.assertContains(r, 'name="tip" value="ARACI"')


class AdayMusteriTipDetayFormViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tipyon2", password="x")

    def test_detay_rozetleri_gosterir(self):
        a = aday_musteri_olustur(unvan="detay firma", tip="ARACI", potansiyel="ORTA")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[a.pk]))
        self.assertContains(r, "Aracı / Komisyoncu")
        self.assertContains(r, "Orta")

    def test_kapali_detayda_kapanis_nedeni_gosterir(self):
        a = aday_musteri_olustur(unvan="kapali firma")
        aday_musteri_guncelle(a, unvan="kapali firma", asama="KAPALI", kapanis_nedeni="KAPANMIS")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[a.pk]))
        self.assertContains(r, "Firma kapanmış")

    def test_ekle_formu_yeni_alanlari_icerir(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteri_ekle"))
        for alan in ('name="tip"', 'name="potansiyel"', 'name="asama"', 'name="kapanis_nedeni"'):
            self.assertContains(r, alan)

    def test_post_ile_yeni_alanlar_kaydedilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_temel_veri(
            unvan="post firma", tip="RAKIP", potansiyel="DUSUK", asama="TEMAS"))
        self.assertEqual(r.status_code, 302)
        from core.models import AdayMusteri
        a = AdayMusteri.objects.get(unvan="POST FİRMA")
        self.assertEqual((a.tip, a.potansiyel, a.asama), ("RAKIP", "DUSUK", "TEMAS"))
