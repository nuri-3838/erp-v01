"""Kullanıcı bazlı ekran yetkisi testleri (Adım 3)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki

MUHASEBE_EKRANLAR = [
    "core:fis_ekle", "core:mizan", "core:bilanco", "core:gelir_tablosu",
    "core:mizan_usd", "core:bilanco_usd", "core:gelir_tablosu_usd",
]


def _yetki_ver(user, *kodlar):
    for k in kodlar:
        EkranYetki.objects.get_or_create(kullanici=user, ekran_kod=k)


class EkranYetkiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yonetici = User.objects.create_superuser("yon", password="x")
        cls.kisitli = User.objects.create_user(
            "kis", password="x", first_name="AYŞE", last_name="DEMİR")
        _yetki_ver(cls.kisitli, "mizan")          # yalnızca Mizan açık
        cls.bos = User.objects.create_user("bos", password="x")  # hiç yetki yok

    # --- Server tarafı URL kontrolü ---
    def test_yetkili_ekran_acik(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:mizan")).status_code, 200)

    def test_yetkisiz_ekran_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:bilanco")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:fis_ekle")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:gelir_tablosu_usd")).status_code, 403)

    def test_varsayilan_kapali(self):
        # Yeni kullanıcı (hiç yetki yok) -> tüm ekranlar kapalı (güvenli varsayılan)
        self.client.force_login(self.bos)
        for ad in MUHASEBE_EKRANLAR:
            with self.subTest(ekran=ad):
                self.assertEqual(self.client.get(reverse(ad)).status_code, 403)

    def test_yonetici_hepsini_gorur(self):
        self.client.force_login(self.yonetici)
        for ad in MUHASEBE_EKRANLAR:
            with self.subTest(ekran=ad):
                self.assertEqual(self.client.get(reverse(ad)).status_code, 200)

    # --- Menü (yalnızca yetkili ekranlar) ---
    def test_menude_sadece_yetkili_ekranlar(self):
        self.client.force_login(self.kisitli)
        r = self.client.get(reverse("core:mizan"))
        self.assertContains(r, "AYŞE DEMİR")        # menüde isim soyisim
        self.assertContains(r, "Mizan")             # açık ekran
        self.assertNotContains(r, "Bilanço")        # kapalı ekran menüde yok
        self.assertNotContains(r, "Fiş Gir")
        self.assertNotContains(r, "Gelir Tablosu")
        self.assertNotContains(r, "Kullanıcılar")   # kısıtlı kullanıcı yönetici değil

    def test_yonetici_menude_hepsi(self):
        self.client.force_login(self.yonetici)
        r = self.client.get(reverse("core:fis_ekle"))
        for ad in ["Yevmiye Fişleri", "Mizan", "Bilanço", "Gelir Tablosu",
                   "Bilanço (USD)", "Kullanıcılar", "Kullanıcı Yetkileri"]:
            with self.subTest(metin=ad):
                self.assertContains(r, ad)

    # --- Yetki ekranı (Ayarlar) ---
    def test_yetki_ekrani_yonetici_olmayana_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(
            self.client.get(reverse("core:kullanici_yetkileri")).status_code, 403)

    def test_yetki_ekrani_kaydet(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:kullanici_yetkileri"), {
            "kullanici": self.kisitli.pk,
            "ekranlar": ["bilanco", "gelir_tablosu"],
        })
        self.assertEqual(r.status_code, 302)
        kodlar = set(EkranYetki.objects.filter(kullanici=self.kisitli, silindi=False)
                     .values_list("ekran_kod", flat=True))
        self.assertEqual(kodlar, {"bilanco", "gelir_tablosu"})
        # Kaldırılan yetki FİZİKSEL silinmez, soft-delete edilir (CLAUDE.md audit kuralı).
        self.assertTrue(EkranYetki.objects.filter(
            kullanici=self.kisitli, ekran_kod="mizan", silindi=True).exists())
        # Yeni yetkiyle erişim değişti: mizan kapandı, bilanco açıldı
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:bilanco")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:mizan")).status_code, 403)

    def test_yetki_ekrani_gecersiz_kod_yok_sayilir(self):
        self.client.force_login(self.yonetici)
        self.client.post(reverse("core:kullanici_yetkileri"), {
            "kullanici": self.bos.pk,
            "ekranlar": ["mizan", "kullanicilar", "uyduruk"],  # son ikisi geçersiz
        })
        kodlar = set(EkranYetki.objects.filter(kullanici=self.bos)
                     .values_list("ekran_kod", flat=True))
        self.assertEqual(kodlar, {"mizan"})  # yalnızca geçerli MUHASEBE ekranı
