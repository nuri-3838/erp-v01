"""Bilanço testleri (spec 5b) — Aktif=Pasif, gruplama, kontra, iptal hariç."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.services.raporlar import bilanco
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date
YIL = (D(2026, 1, 1), D(2026, 12, 31))


def _s(kod, taraf, tutar):
    return SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


def _grup(bil, kod):
    for g in list(bil.aktif) + list(bil.pasif):
        if g.kod == kod:
            return g
    return None


def _tutar(grup, hesap_kod):
    for s in grup.satirlar:
        if s.kod == hesap_kod:
            return s.tutar
    return None


class BilancoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _Kur.objects.get_or_create(tarih=_dtk.date(2020, 1, 1), defaults={"usd_alis": _Dec("30")})
        cls.kullanici = User.objects.create_superuser("test", password="parola1234")

    def setUp(self):
        self.client.force_login(self.kullanici)

    def test_acilis_dengeli(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "5000"), _s("500", "A", "5000")])
        b = bilanco(*YIL)
        self.assertTrue(b.denk_mi)
        self.assertEqual(b.aktif_toplam, Decimal("5000.00"))
        self.assertEqual(b.pasif_toplam, Decimal("5000.00"))
        self.assertEqual(b.donem_sonucu, Decimal("0.00"))
        self.assertEqual(_tutar(_grup(b, "DV"), "100"), Decimal("5000.00"))
        self.assertEqual(_tutar(_grup(b, "OZK"), "500"), Decimal("5000.00"))

    def test_kar_pasife_yansir_ve_denge_korunur(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "5000"), _s("500", "A", "5000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        b = bilanco(*YIL)
        self.assertEqual(b.donem_sonucu, Decimal("1000.00"))
        self.assertEqual(b.aktif_toplam, Decimal("6000.00"))
        self.assertEqual(b.pasif_toplam, Decimal("6000.00"))
        self.assertTrue(b.denk_mi)

    def test_kontra_negatif_ve_gider_dengeyi_bozmaz(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "10000"), _s("500", "A", "10000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("632", "B", "1000"), _s("257", "A", "1000")])
        b = bilanco(*YIL)
        self.assertEqual(_tutar(_grup(b, "DDV"), "257"), Decimal("-1000.00"))
        self.assertEqual(b.donem_sonucu, Decimal("-1000.00"))
        self.assertEqual(b.aktif_toplam, Decimal("9000.00"))
        self.assertEqual(b.pasif_toplam, Decimal("9000.00"))
        self.assertTrue(b.denk_mi)

    def test_iptal_haric(self):
        f = fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "5000"), _s("500", "A", "5000")])
        fis_iptal(f)
        b = bilanco(*YIL)
        self.assertEqual(b.aktif_toplam, Decimal("0.00"))
        self.assertEqual(b.pasif_toplam, Decimal("0.00"))
        self.assertTrue(b.denk_mi)

    def test_view(self):
        bugun = timezone.localdate()
        fis_olustur(tarih=bugun, satirlar=[_s("100", "B", "5000"), _s("500", "A", "5000")])
        r = self.client.get(reverse("core:bilanco"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AKTİF = PASİF")
        self.assertContains(r, "KASA")
        self.assertContains(r, "5.000,00")
