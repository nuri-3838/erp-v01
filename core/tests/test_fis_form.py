"""Manuel fiş ekranı revizyonu — kur_usd form alanı kaldırıldı (otomatik), kur API."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Kur, YevmiyeFisi

D = datetime.date


def _payload(**ek):
    data = {
        "tarih": "2026-03-10", "aciklama": "kasa",
        "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "2", "form-MAX_NUM_FORMS": "1000",
        "form-0-hesap": "100", "form-0-islem_pb": "TRY",
        "form-0-borc": "1.000,00", "form-0-alacak": "",
        "form-0-islem_kuru": "1", "form-0-aciklama": "",
        "form-1-hesap": "600", "form-1-islem_pb": "TRY",
        "form-1-borc": "", "form-1-alacak": "1.000,00",
        "form-1-islem_kuru": "1", "form-1-aciklama": "",
    }
    data.update(ek)
    return data


class FisFormRevizyonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        cls.u = User.objects.create_superuser("yon", password="x")

    def setUp(self):
        self.client.force_login(self.u)

    def test_form_usd_kuru_alani_yok(self):
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "USD KURU")
        self.assertNotContains(r, 'name="kur_usd"')

    def test_kur_usd_otomatik_dolar(self):
        Kur.objects.create(tarih=D(2026, 3, 9), usd_alis=Decimal("32.5000"))
        r = self.client.post(reverse("core:fis_ekle"), _payload(tarih="2026-03-10"))
        self.assertEqual(r.status_code, 302)
        fis = YevmiyeFisi.objects.latest("id")
        self.assertEqual(fis.kur_usd, Decimal("32.5000"))      # KUR tablosundan otomatik

    def test_kur_yoksa_bos(self):
        r = self.client.post(reverse("core:fis_ekle"), _payload(tarih="2030-01-01"))
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(YevmiyeFisi.objects.latest("id").kur_usd)

    def test_kur_api(self):
        Kur.objects.create(tarih=D(2026, 3, 9), usd_alis=Decimal("32.5000"))
        r = self.client.get(reverse("core:kur_usd_api"), {"tarih": "2026-03-10"})
        self.assertEqual(r.json()["kur"], "32.500000")
        self.assertIsNone(self.client.get(reverse("core:kur_usd_api"),
                                          {"tarih": "2020-01-01"}).json()["kur"])

    def test_kur_api_yetkisiz(self):
        self.client.logout()
        r = self.client.get(reverse("core:kur_usd_api"), {"tarih": "2026-03-10"})
        self.assertEqual(r.status_code, 302)                   # login'e yönlenir
