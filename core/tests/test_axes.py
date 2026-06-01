"""django-axes brute-force kilidi testleri.

Prod'da (DEBUG=False) AXES_ENABLED=True; WSL/dev ve normal test (DEBUG=True) KAPALI.
Bu testler axes davranışını override_settings(AXES_ENABLED=True) ile, küçük eşikle
doğrular: 5 (burada 3) hatalı deneme -> kilit + Türkçe sayfa; başarılı giriş çalışır
ve sayacı sıfırlar. Her testte axes durumu reset() ile temizlenir.
"""
from axes.utils import reset
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    AXES_ENABLED=True,
    AXES_FAILURE_LIMIT=3,
    AXES_LOCKOUT_PARAMETERS=[["username", "ip_address"]],
    AXES_RESET_ON_SUCCESS=True,
    AXES_LOCKOUT_TEMPLATE="registration/kilitlendi.html",
)
class AxesKilitTest(TestCase):
    def setUp(self):
        reset()
        User.objects.create_user("denek", password="Denek!2026x")

    def tearDown(self):
        reset()

    def _giris(self, sifre):
        return self.client.post(reverse("login"),
                                {"username": "denek", "password": sifre})

    def test_dogru_giris_calisir(self):
        r = self._giris("Denek!2026x")
        self.assertEqual(r.status_code, 302)            # başarılı -> yönlendirme

    def test_esik_asilinca_kilit_ve_turkce_mesaj(self):
        for _ in range(3):                              # eşik = 3 hatalı
            self._giris("yanlis")
        r = self._giris("yanlis")                       # kilitli
        self.assertEqual(r.status_code, 429)
        self.assertContains(r, "Çok fazla hatalı", status_code=429)
        self.assertContains(r, "30 dakika", status_code=429)
        # Kilitliyken DOĞRU şifre bile girilemez
        r2 = self._giris("Denek!2026x")
        self.assertEqual(r2.status_code, 429)

    def test_basarili_giris_sayaci_sifirlar(self):
        self._giris("yanlis")
        self._giris("yanlis")                           # 2 hata (eşik 3, henüz kilit yok)
        self.assertEqual(self._giris("Denek!2026x").status_code, 302)  # başarı -> sıfır
        self.client.logout()
        # Sayaç sıfırlandı: tek hata kilitlemez
        self.assertNotEqual(self._giris("yanlis").status_code, 429)

    def test_elle_reset_kilidi_acar(self):
        for _ in range(4):
            self._giris("yanlis")
        self.assertEqual(self._giris("yanlis").status_code, 429)       # kilitli
        reset()                                         # = manage.py axes_reset
        self.assertEqual(self._giris("Denek!2026x").status_code, 302)  # açıldı


class AxesVarsayilanKapaliTest(TestCase):
    """DEBUG=True (test ortamı) varsayılanında axes KAPALI: normal giriş bozulmaz."""
    def test_normal_test_ortaminda_kilit_yok(self):
        User.objects.create_user("u2", password="Parola!2026")
        for _ in range(8):  # axes kapalı olduğundan çok hata bile kilitlemez
            self.client.post(reverse("login"), {"username": "u2", "password": "x"})
        r = self.client.post(reverse("login"), {"username": "u2", "password": "Parola!2026"})
        self.assertEqual(r.status_code, 302)            # giriş hâlâ çalışıyor
