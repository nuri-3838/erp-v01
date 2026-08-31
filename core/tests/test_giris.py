"""Giriş (Django auth) testleri — giriş yapmadan muhasebe ekranları kapalı."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki

KORUNAN = [
    "core:fis_ekle", "core:mizan", "core:bilanco", "core:gelir_tablosu",
    "core:mizan_usd", "core:bilanco_usd", "core:gelir_tablosu_usd",
]
# fis_ekle URL'i "fis_listesi" ekran koduna bağlı (ayrı bir "fis_ekle" ekran kodu yok —
# Fiş Gir + Fiş Listesi tek ekranda birleşti).
_EKRAN_KOD = {"fis_ekle": "fis_listesi"}


class GirisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kullanici = User.objects.create_user("ali", password="parola1234")
        for _ad in KORUNAN:
            kod = _ad.split(":")[1]
            EkranYetki.objects.create(
                kullanici=cls.kullanici, ekran_kod=_EKRAN_KOD.get(kod, kod))

    def test_login_ekrani_acik(self):
        r = self.client.get(reverse("login"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Giriş")

    def test_giris_yapmadan_ekranlar_kapali(self):
        # Server tarafında kontrol: yetkisiz URL'e elle gidince login'e yönlendirir.
        for ad in KORUNAN:
            with self.subTest(ekran=ad):
                r = self.client.get(reverse(ad))
                self.assertEqual(r.status_code, 302)
                self.assertIn("/login/", r.url)

    def test_giris_sonrasi_ekran_acik(self):
        self.client.force_login(self.kullanici)
        for ad in KORUNAN:
            with self.subTest(ekran=ad):
                self.assertEqual(self.client.get(reverse(ad)).status_code, 200)

    def test_dogru_sifreyle_giris(self):
        r = self.client.post(reverse("login"),
                             {"username": "ali", "password": "parola1234"})
        self.assertEqual(r.status_code, 302)  # başarı -> yönlendirme

    def test_yanlis_sifre_reddedilir(self):
        r = self.client.post(reverse("login"),
                             {"username": "ali", "password": "yanlis"})
        self.assertEqual(r.status_code, 200)  # formda kalır
        self.assertContains(r, "hatalı")

    def test_cikistan_sonra_kapali(self):
        self.client.force_login(self.kullanici)
        self.assertEqual(self.client.get(reverse("core:mizan")).status_code, 200)
        self.client.post(reverse("logout"))
        self.assertEqual(self.client.get(reverse("core:mizan")).status_code, 302)
