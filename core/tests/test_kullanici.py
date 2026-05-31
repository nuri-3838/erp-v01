"""Kullanıcı yönetimi testleri (Adım 2) — TC, şifre kuralı, yönetici yetkisi, isim."""
from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.dogrulama import tc_gecerli
from core.models import Profil

GECERLI_TC = "10000000146"   # standart algoritmaya uyan geçerli örnek
GUCLU_SIFRE = "Abc12345!"


class TcDogrulamaTest(SimpleTestCase):
    def test_gecerli(self):
        self.assertTrue(tc_gecerli(GECERLI_TC))

    def test_gecersizler(self):
        for tc in ["12345678901", "00000000000", "1234567890",
                   "abcdefghijk", "1000000014", "100000001460"]:
            with self.subTest(tc=tc):
                self.assertFalse(tc_gecerli(tc))


class SifreKuraliTest(SimpleTestCase):
    def test_zayif_reddedilir(self):
        for s in ["abc", "abcdefgh", "ABCDEFGH", "abcd1234", "Abcd1234"]:
            with self.subTest(sifre=s):
                with self.assertRaises(ValidationError):
                    password_validation.validate_password(s)

    def test_guclu_gecer(self):
        password_validation.validate_password(GUCLU_SIFRE)  # hata yükseltmemeli


class KullaniciYonetimTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yonetici = User.objects.create_superuser("yonetici", password="x")
        cls.normal = User.objects.create_user("normal", password="x")

    def _veri(self, **degis):
        data = {
            "tc": GECERLI_TC, "isim": "nuri", "soyisim": "özer",
            "email": "NURI@X.COM", "telefon": "0532 123 45 67",
            "sifre": GUCLU_SIFRE, "yonetici": "on",
        }
        data.update(degis)
        return data

    def test_giris_yapmadan_kapali(self):
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_yonetici_olmayan_giremez(self):
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("core:kullanici_listesi")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:kullanici_ekle")).status_code, 403)

    def test_yonetici_girebilir(self):
        self.client.force_login(self.yonetici)
        self.assertEqual(self.client.get(reverse("core:kullanici_listesi")).status_code, 200)

    def test_kullanici_olusturma_isim_buyuk_email_kucuk(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"), self._veri())
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username=GECERLI_TC)
        self.assertEqual(u.first_name, "NURİ")     # TR büyük harf (i->İ)
        self.assertEqual(u.last_name, "ÖZER")
        self.assertEqual(u.email, "nuri@x.com")    # küçük
        self.assertTrue(u.check_password(GUCLU_SIFRE))
        self.assertEqual(u.profil.telefon, "05321234567")  # normalize
        self.assertTrue(u.profil.yonetici)

    def test_zayif_sifre_reddedilir_turkce(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"), self._veri(sifre="abc"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "içermeli")  # Türkçe şifre kuralı mesajı
        self.assertFalse(User.objects.filter(username=GECERLI_TC).exists())

    def test_gecersiz_tc_reddedilir(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"), self._veri(tc="12345678901"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Geçersiz TC")
        self.assertEqual(User.objects.filter(username="12345678901").count(), 0)

    def test_pasif_yapma(self):
        self.client.force_login(self.yonetici)
        self.client.post(reverse("core:kullanici_ekle"), self._veri())
        u = User.objects.get(username=GECERLI_TC)
        r = self.client.post(reverse("core:kullanici_duzenle", args=[u.pk]), {
            "isim": "nuri", "soyisim": "özer", "email": "nuri@x.com",
            "telefon": "05321234567", "yonetici": "on", "sifre": "",
            # "aktif" gönderilmiyor -> pasif
        })
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertFalse(u.is_active)

    def test_menude_isim_soyisim_gorunur(self):
        u = User.objects.create_user("adsoyad", password="x",
                                     first_name="NURİ", last_name="ÖZER")
        self.client.force_login(u)
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertContains(r, "NURİ ÖZER")
