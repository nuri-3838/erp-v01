"""Aday Müşteri: Tip/Potansiyel/Aşama artık AdayTipTanim/AdayPotansiyelTanim/AdayAsamaTanim
tanım tablolarına FK — servis doğrulaması, form clean() kuralı, liste filtreleri, detay/form
şablon çıktısı. Tanım kayıtları migration 0132'nin tohum verisiyle her test DB'sinde hazır
gelir (sistem_kodu ile bulunur, id sabitlenmez)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import AdayMusteriForm
from core.models import AdayAsamaTanim, AdayPotansiyelTanim, AdayTipTanim
from core.services.aday import AdayHatasi, aday_musteri_guncelle, aday_musteri_olustur
from core.tests.aday_yardimci import varsayilan_kaynak_id


def _tip(kod):
    return AdayTipTanim.objects.get(sistem_kodu=kod).pk


def _potansiyel(kod):
    return AdayPotansiyelTanim.objects.get(sistem_kodu=kod).pk


def _asama(kod):
    return AdayAsamaTanim.objects.get(sistem_kodu=kod).pk


class AdayMusteriTipServisTest(TestCase):
    def test_varsayilan_aday_yeni(self):
        a = aday_musteri_olustur(unvan="firma a", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                                 kategori_id=varsayilan_kaynak_id())
        self.assertEqual(a.tip.sistem_kodu, "ADAY")
        self.assertEqual(a.asama.sistem_kodu, "YENI")
        self.assertIsNone(a.potansiyel)
        self.assertEqual(a.kapanis_nedeni, "")

    def test_gecersiz_tip_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma b", tip_id=999999, asama_id=_asama("YENI"),
                                 kategori_id=varsayilan_kaynak_id())

    def test_gecersiz_potansiyel_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma c", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                                 kategori_id=varsayilan_kaynak_id(), potansiyel_id=999999)

    def test_kapali_asamada_kapanis_nedeni_zorunlu(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma d", tip_id=_tip("ADAY"), asama_id=_asama("KAPALI"),
                                 kategori_id=varsayilan_kaynak_id())

    def test_kapali_asamada_kapanis_nedeni_ile_basarili(self):
        a = aday_musteri_olustur(unvan="firma e", tip_id=_tip("ADAY"), asama_id=_asama("KAPALI"),
                                 kategori_id=varsayilan_kaynak_id(), kapanis_nedeni="ILGISIZ")
        self.assertEqual(a.asama.sistem_kodu, "KAPALI")
        self.assertEqual(a.kapanis_nedeni, "ILGISIZ")

    def test_kapali_degilse_kapanis_nedeni_temizlenir(self):
        a = aday_musteri_olustur(unvan="firma f", tip_id=_tip("ADAY"), asama_id=_asama("TEMAS"),
                                 kategori_id=varsayilan_kaynak_id(), kapanis_nedeni="ILGISIZ")
        self.assertEqual(a.kapanis_nedeni, "")

    def test_guncelle_kapaliya_gecerse_nedensiz_reddedilir(self):
        a = aday_musteri_olustur(unvan="firma g", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                                 kategori_id=varsayilan_kaynak_id())
        with self.assertRaises(AdayHatasi):
            aday_musteri_guncelle(a, unvan="firma g", tip_id=_tip("ADAY"),
                                  asama_id=_asama("KAPALI"), kategori_id=a.kategori_id)


def _form_temel_veri(**ek):
    veri = dict(
        unvan="test firma", ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
        ulke="", sehir="", kategori=varsayilan_kaynak_id(), tip="ADAY", potansiyel="",
        asama="YENI", kapanis_nedeni="", para_birimi="TRY", iskonto_yuzdesi="0")
    veri.update(ek)
    # Kısa okunabilirlik için sistem_kodu STRİNGİ olarak verilir (ör. "KAPALI"), burada
    # ModelChoiceField'in beklediği pk'ya çözülür.
    if veri["tip"]:
        veri["tip"] = _tip(veri["tip"])
    if veri["potansiyel"]:
        veri["potansiyel"] = _potansiyel(veri["potansiyel"])
    if veri["asama"]:
        veri["asama"] = _asama(veri["asama"])
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
        aday_musteri_olustur(unvan="eski musteri x", tip_id=_tip("ESKI_MUSTERI"),
                             asama_id=_asama("YENI"), kategori_id=varsayilan_kaynak_id())
        aday_musteri_olustur(unvan="aday y", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                             kategori_id=varsayilan_kaynak_id())
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"tip": "ESKI_MUSTERI"})
        self.assertContains(r, "ESKİ MUSTERİ X")
        self.assertNotContains(r, "ADAY Y")

    def test_potansiyel_belirlenmedi_filtresi(self):
        aday_musteri_olustur(unvan="belirsiz z", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                             kategori_id=varsayilan_kaynak_id())
        aday_musteri_olustur(unvan="yuksek k", tip_id=_tip("ADAY"), asama_id=_asama("YENI"),
                             kategori_id=varsayilan_kaynak_id(), potansiyel_id=_potansiyel("YUKSEK"))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"potansiyel": "BOS"})
        self.assertContains(r, "BELİRSİZ Z")
        self.assertNotContains(r, "YUKSEK K")

    def test_asama_filtresi_kapali_kayitlar_varsayilanda_gorunur(self):
        a = aday_musteri_olustur(unvan="kapanan firma", tip_id=_tip("ADAY"),
                                 asama_id=_asama("YENI"), kategori_id=varsayilan_kaynak_id())
        aday_musteri_guncelle(a, unvan="kapanan firma", tip_id=_tip("ADAY"),
                              asama_id=_asama("KAPALI"), kategori_id=a.kategori_id,
                              kapanis_nedeni="DIGER")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "KAPANAN FİRMA")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"asama": "KAPALI"})
        self.assertContains(r2, "KAPANAN FİRMA")
        r3 = self.client.get(reverse("core:aday_musteriler"), {"asama": "YENI"})
        self.assertNotContains(r3, "KAPANAN FİRMA")

    def test_sayfa_boyutu_formu_filtreyi_korur(self):
        aday_musteri_olustur(unvan="filtreli firma", tip_id=_tip("ARACI"),
                             asama_id=_asama("YENI"), kategori_id=varsayilan_kaynak_id())
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"tip": "ARACI"})
        # Eski metin kodu (?tip=ARACI) hâlâ çalışır ama gizli alan artık ÇÖZÜLMÜŞ pk taşır
        # (spec: sistem_kodu üzerinden çalışsın — URL'in kendisi, yeniden render edilen
        # değer değil).
        self.assertContains(r, f'name="tip" value="{_tip("ARACI")}"')


class AdayMusteriTipDetayFormViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tipyon2", password="x")

    def test_detay_rozetleri_gosterir(self):
        a = aday_musteri_olustur(unvan="detay firma", tip_id=_tip("ARACI"),
                                 asama_id=_asama("YENI"), kategori_id=varsayilan_kaynak_id(),
                                 potansiyel_id=_potansiyel("ORTA"))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[a.pk]))
        self.assertContains(r, "Aracı / Komisyoncu")
        self.assertContains(r, "Orta")

    def test_kapali_detayda_kapanis_nedeni_gosterir(self):
        a = aday_musteri_olustur(unvan="kapali firma", tip_id=_tip("ADAY"),
                                 asama_id=_asama("YENI"), kategori_id=varsayilan_kaynak_id())
        aday_musteri_guncelle(a, unvan="kapali firma", tip_id=_tip("ADAY"),
                              asama_id=_asama("KAPALI"), kategori_id=a.kategori_id,
                              kapanis_nedeni="KAPANMIS")
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
        self.assertEqual((a.tip.sistem_kodu, a.potansiyel.sistem_kodu, a.asama.sistem_kodu),
                         ("RAKIP", "DUSUK", "TEMAS"))
