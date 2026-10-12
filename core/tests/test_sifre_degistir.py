"""Personel/operatör hesabı şifre değiştirme (2026-10-12): Atölye ve Mesaim'de 🔑 Şifre düğmesi; mevcut + yeni + tekrar (servis kuralları, Django
parola doğrulayıcıları Türkçe); değişince oturum açık kalır; yönetici hesap oluşturunca / şifre sıfırlayınca "ilk girişte şifre değiştirilmeli"
→ middleware her sayfayı 🔑 Şifre ekranına yönlendirir (değiştirmeden atölye/mesai yok); personel kartında "Son şifre değişikliği"."""
from datetime import date

from django.contrib.auth.models import User
from django.urls import reverse

from core.services import atolye
from core.services.mesai_hesap import MesaiHesapHatasi, hesap_olustur, sifre_degistir, sifre_degistirilmeli_mi, sifre_sifirla
from core.tests.ik_yardimci import personel_kur
from core.tests.test_atolye import AtolyeTaban

SIFRE = "cokGizli!2026"
YENI = "yeniSifre#2027"


class SifreServisTest(AtolyeTaban):
    def test_kurallar(self):
        p = self.p
        with self.assertRaisesMessage(MesaiHesapHatasi, "Mevcut şifre yanlış."):
            sifre_degistir(p, mevcut="yanlis", yeni=YENI, yeni_tekrar=YENI)
        with self.assertRaisesMessage(MesaiHesapHatasi, "Yeni şifreler birbiriyle aynı değil."):
            sifre_degistir(p, mevcut=SIFRE, yeni=YENI, yeni_tekrar=YENI + "x")
        with self.assertRaisesMessage(MesaiHesapHatasi, "Yeni şifre mevcut şifreyle aynı olamaz."):
            sifre_degistir(p, mevcut=SIFRE, yeni=SIFRE, yeni_tekrar=SIFRE)
        with self.assertRaisesMessage(MesaiHesapHatasi, "sembol"):                                   # KarmaSifreDogrulayici (Türkçe)
            sifre_degistir(p, mevcut=SIFRE, yeni="Abcdef12", yeni_tekrar="Abcdef12")
        with self.assertRaisesMessage(MesaiHesapHatasi, "çok kısa"):                                 # MinimumLengthValidator (tr)
            sifre_degistir(p, mevcut=SIFRE, yeni="Ab1!", yeni_tekrar="Ab1!")
        self.assertFalse(sifre_degistirilmeli_mi(self.u))
        u = sifre_degistir(p, mevcut=SIFRE, yeni=YENI, yeni_tekrar=YENI, yapan=self.u)
        self.assertTrue(u.check_password(YENI))
        p.refresh_from_db()
        self.assertFalse(p.sifre_degistirilmeli)
        self.assertIsNotNone(p.sifre_degisiklik_zamani)
        # yönetici sıfırlar → ilk girişte değiştirilmeli; yeni hesap da öyle
        sifre_sifirla(p, sifre=SIFRE, yapan=self.yon)
        p.refresh_from_db()
        self.assertTrue(p.sifre_degistirilmeli)
        self.assertTrue(sifre_degistirilmeli_mi(self.u))
        p3 = personel_kur(ad="yeni", soyad="kisi", ise_giris_tarihi=date(2021, 1, 1))
        u3 = hesap_olustur(p3, kullanici_adi="yenik", sifre=SIFRE)
        p3.refresh_from_db()
        self.assertTrue(p3.sifre_degistirilmeli and sifre_degistirilmeli_mi(u3))
        with self.assertRaisesMessage(MesaiHesapHatasi, "mesai hesabı yok"):
            sifre_degistir(personel_kur(ad="hesapsiz", soyad="x", ise_giris_tarihi=date(2021, 1, 1)), mevcut="a", yeni=YENI, yeni_tekrar=YENI)


class SifreEkraniTest(AtolyeTaban):
    def test_dugmeler_ve_serbest_degistirme(self):
        self.baslat()                                                                                 # operatör, mesai açık
        r = self.client.get(reverse("core:atolye") + "?sec=1")
        self.assertContains(r, 'href="%s"' % reverse("core:sifre_degistir"))
        self.assertContains(r, "🔑 Şifre")
        r = self.client.get(reverse("core:sifre_degistir"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "← Geri")                                                               # zorunlu değil → geri bağlantısı
        self.assertContains(r, reverse("core:atolye"))
        self.assertContains(r, "Mevcut şifre")
        self.assertNotContains(r, "İlk girişte şifrenizi değiştirmeniz gerekiyor")
        r = self.client.post(reverse("core:sifre_degistir"), {"mevcut": "yanlis", "yeni": YENI, "yeni_tekrar": YENI})
        self.assertContains(r, "⚠️ Mevcut şifre yanlış.")
        r = self.client.post(reverse("core:sifre_degistir"), {"mevcut": SIFRE, "yeni": "Abcdef12", "yeni_tekrar": "Abcdef12"})
        self.assertContains(r, "sembol")
        r = self.client.post(reverse("core:sifre_degistir"), {"mevcut": SIFRE, "yeni": YENI, "yeni_tekrar": YENI}, follow=True)
        self.assertRedirects(r, reverse("core:atolye"), fetch_redirect_response=False)
        self.assertIn("Şifreniz değiştirildi.", self.mesajlar(r))
        self.assertEqual(self.client.get(reverse("core:atolye") + "?sec=1").status_code, 200)         # oturum açık kaldı
        self.assertTrue(User.objects.get(pk=self.u.pk).check_password(YENI))
        # mesaim kullanıcısı (atölye yetkisi yok): düğme ve geri = mesaim
        self.client.force_login(self.u2)
        r = self.client.get(reverse("core:mesaim"))
        self.assertContains(r, "🔑 Şifre Değiştir")
        r = self.client.get(reverse("core:sifre_degistir"))
        self.assertContains(r, 'href="%s"' % reverse("core:mesaim"))
        # personel kartı olmayan hesap → 403
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:sifre_degistir")).status_code, 403)

    def test_ilk_giriste_zorunlu_yonlendirme(self):
        self.baslat()                                                                                 # operatör girişte
        sifre_sifirla(self.p, sifre=SIFRE, yapan=self.yon)                                            # yönetici sıfırladı → açık oturum düşer
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 302)
        self.assertTrue(self.client.login(username="errahman", password=SIFRE))                       # yeniden giriş → zorunlu
        for url in (reverse("core:pano"), reverse("core:atolye"), reverse("core:mesaim"), reverse("core:atolye_istasyon", args=[self.lazer.pk])):
            self.assertRedirects(self.client.get(url), reverse("core:sifre_degistir"), fetch_redirect_response=False, msg_prefix=url)
        r = self.client.post(reverse("core:atolye_mesai"), {"eylem": "baslat"}, HTTP_X_REAL_IP="10.0.0.5")
        self.assertRedirects(r, reverse("core:sifre_degistir"), fetch_redirect_response=False)       # POST de yönlenir, mesai açılmadı
        r = self.client.get(reverse("core:sifre_degistir"))
        self.assertContains(r, "İlk girişte şifrenizi değiştirmeniz gerekiyor")
        self.assertNotContains(r, "← Geri")
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)                        # çıkış serbest
        self.client.login(username="errahman", password=SIFRE)
        r = self.client.post(reverse("core:sifre_degistir"), {"mevcut": SIFRE, "yeni": YENI, "yeni_tekrar": YENI})
        self.assertRedirects(r, reverse("core:atolye"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("core:atolye")).status_code, 200)                    # artık serbest, oturum açık
        self.p.refresh_from_db()
        self.assertFalse(self.p.sifre_degistirilmeli)
        # personel kartı: son şifre değişikliği + rozet
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertContains(r, "Son şifre değişikliği")
        self.assertContains(r, self.p.sifre_degisiklik_zamani.astimezone().strftime("%d.%m.%Y"))
        self.assertNotContains(r, "ilk girişte değiştirilmeli")
        sifre_sifirla(self.p, sifre=SIFRE, yapan=self.yon)
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertContains(r, "ilk girişte değiştirilmeli")
        self.assertTrue(atolye.operator_mu(self.p))
