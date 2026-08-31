"""Kullanıcı yönetimi + modül/menü testleri (Adım 2-2.5)."""
from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.dogrulama import tc_gecerli, telefon_dogrula, telefon_kanonik
from core.models import EkranYetki, Profil

GECERLI_TC = "10000000146"
GUCLU_SIFRE = "Abc12345!"


class TcDogrulamaTest(SimpleTestCase):
    def test_gecerli(self):
        self.assertTrue(tc_gecerli(GECERLI_TC))

    def test_gecersizler(self):
        for tc in ["12345678901", "00000000000", "1234567890",
                   "abcdefghijk", "1000000014", "100000001460"]:
            with self.subTest(tc=tc):
                self.assertFalse(tc_gecerli(tc))


class TelefonTest(SimpleTestCase):
    def test_gecerli_formatlar_kanonik_plus90(self):
        for giris in ["+905327024005", "905327024005", "05327024005",
                      "5327024005", "+90 532 702 40 05", "(0532) 702-40-05"]:
            with self.subTest(giris=giris):
                telefon_dogrula(giris)
                self.assertEqual(telefon_kanonik(giris), "+905327024005")

    def test_gecersizler(self):
        for giris in ["123", "12345678901234", "abc", ""]:
            with self.subTest(giris=giris):
                with self.assertRaises(ValidationError):
                    telefon_dogrula(giris)


class SifreKuraliTest(SimpleTestCase):
    def test_zayif_reddedilir(self):
        for s in ["abc", "abcdefgh", "ABCDEFGH", "abcd1234", "Abcd1234"]:
            with self.subTest(sifre=s):
                with self.assertRaises(ValidationError):
                    password_validation.validate_password(s)

    def test_guclu_gecer(self):
        password_validation.validate_password(GUCLU_SIFRE)


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
        self.assertEqual(self.client.get(reverse("core:kullanici_yetkileri")).status_code, 403)

    def test_yonetici_girebilir(self):
        self.client.force_login(self.yonetici)
        self.assertEqual(self.client.get(reverse("core:kullanici_listesi")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:kullanici_yetkileri")).status_code, 200)

    def test_ayarlar_modulu_yalnizca_yoneticiye_menude(self):
        # Yönetici: Ayarlar modülü + ekranları menüde
        self.client.force_login(self.yonetici)
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertContains(r, "Ayarlar")
        self.assertContains(r, "Kullanıcılar")
        self.assertContains(r, "Kullanıcı Yetkileri")
        # Normal kullanıcı (fiş ekranına yetkili): Ayarlar modülü menüde YOK
        EkranYetki.objects.create(kullanici=self.normal, ekran_kod="fis_listesi")
        self.client.force_login(self.normal)
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertNotContains(r, "Kullanıcılar")
        self.assertNotContains(r, "Kullanıcı Yetkileri")

    def test_kullanici_olusturma_isim_buyuk_email_kucuk(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"), self._veri())
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username=GECERLI_TC)
        self.assertEqual(u.first_name, "NURİ")
        self.assertEqual(u.last_name, "ÖZER")
        self.assertEqual(u.email, "nuri@x.com")
        self.assertTrue(u.check_password(GUCLU_SIFRE))
        self.assertEqual(u.profil.telefon, "+905321234567")
        self.assertTrue(u.profil.yonetici)

    def test_uluslararasi_telefon_kabul(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"),
                             self._veri(telefon="+90 532 702 40 05"))
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username=GECERLI_TC)
        self.assertEqual(u.profil.telefon, "+905327024005")

    def test_sade_telefon_da_plus90_saklanir(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"),
                             self._veri(telefon="5327024005"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            User.objects.get(username=GECERLI_TC).profil.telefon, "+905327024005"
        )

    def test_zayif_sifre_reddedilir_turkce(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_ekle"), self._veri(sifre="abc"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "içermeli")
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
        })
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertFalse(u.is_active)

    def test_menude_isim_soyisim_gorunur(self):
        u = User.objects.create_user("adsoyad", password="x",
                                     first_name="NURİ", last_name="ÖZER")
        EkranYetki.objects.create(kullanici=u, ekran_kod="fis_listesi")
        self.client.force_login(u)
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertContains(r, "NURİ ÖZER")
