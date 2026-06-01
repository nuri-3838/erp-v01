"""Manuel fiş giriş ekranı (view) testleri — TR sayı parse/format + denge + Borç/Alacak sütunları."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import YevmiyeFisi, YevmiyeSatir


def _payload(**degis):
    data = {
        "tarih": "2026-03-10",
        "aciklama": "kasa tahsilatı",
        "kur_usd": "",
        "form-TOTAL_FORMS": "2",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "2",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-hesap": "100", "form-0-islem_pb": "TRY",
        "form-0-borc": "1.000,00", "form-0-alacak": "",
        "form-0-islem_kuru": "1", "form-0-aciklama": "",
        "form-1-hesap": "600", "form-1-islem_pb": "TRY",
        "form-1-borc": "", "form-1-alacak": "1.000,00",
        "form-1-islem_kuru": "1", "form-1-aciklama": "",
    }
    data.update(degis)
    return data


class FisEkleViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.kullanici = User.objects.create_superuser("test", password="parola1234")

    def setUp(self):
        self.client.force_login(self.kullanici)

    def test_get_form_acilir(self):
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Manuel Fiş Girişi")

    def test_tr_sayi_parse_edilip_saklanir(self):
        r = self.client.post(reverse("core:fis_ekle"), _payload())
        self.assertEqual(r.status_code, 302)  # PRG -> detay
        self.assertEqual(YevmiyeFisi.objects.count(), 1)
        fis = YevmiyeFisi.objects.get()
        self.assertEqual(fis.aciklama, "KASA TAHSİLATI")  # buyuk_harf_tr
        borc = fis.satirlar.get(hesap_id="100")
        self.assertEqual(borc.borc, Decimal("1000.00"))
        self.assertEqual(borc.alacak, Decimal("0.00"))
        alacak = fis.satirlar.get(hesap_id="600")
        self.assertEqual(alacak.alacak, Decimal("1000.00"))

    def test_detay_tr_formatli_gosterir(self):
        self.client.post(reverse("core:fis_ekle"), _payload())
        fis = YevmiyeFisi.objects.get()
        r = self.client.get(reverse("core:fis_detay", args=[fis.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "1.000,00")

    def test_dengesiz_dostca_uyari_ve_kayit_yok(self):
        r = self.client.post(reverse("core:fis_ekle"), _payload(**{
            "form-1-alacak": "900,00",
        }))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "dengesiz")
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_iki_taraf_dolu_reddedilir(self):
        r = self.client.post(reverse("core:fis_ekle"), _payload(**{
            "form-0-alacak": "1.000,00",
        }))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "yalnızca Borç veya Alacak")
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_gecersiz_sayi_alan_hatasi(self):
        r = self.client.post(reverse("core:fis_ekle"), _payload(**{
            "form-0-borc": "abc",
        }))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Geçersiz sayı")
        self.assertEqual(YevmiyeFisi.objects.count(), 0)
