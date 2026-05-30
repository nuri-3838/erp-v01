"""Gelir tablosu testleri (spec 5b) — yalnızca 6'lı, 7'liler hariç, harita doğru."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.services.raporlar import gelir_tablosu
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date
YIL = (D(2026, 1, 1), D(2026, 12, 31))


def _s(kod, taraf, tutar):
    return SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


class GelirTablosuTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_satis_ve_gider_net_kar(self):
        # Satış 1.000 (600), gider 300 (632 Genel Yönetim)
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("632", "B", "300"), _s("100", "A", "300")])
        gt = gelir_tablosu(*YIL)
        self.assertEqual(gt.deger("A."), Decimal("1000.00"))
        self.assertEqual(gt.deger("Net Satışlar"), Decimal("1000.00"))
        self.assertEqual(gt.deger("D."), Decimal("300.00"))
        self.assertEqual(gt.deger("FAALİYET KÂRI"), Decimal("700.00"))
        self.assertEqual(gt.donem_net_kari, Decimal("700.00"))

    def test_7li_hesaplar_rapora_girmez(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        # 710 Direkt İlk Madde (7'li) — gelir tablosuna GİRMEMELİ
        fis_olustur(tarih=D(2026, 3, 3), satirlar=[_s("710", "B", "500"), _s("100", "A", "500")])
        gt = gelir_tablosu(*YIL)
        self.assertEqual(gt.donem_net_kari, Decimal("1000.00"))  # 710 etkilemez

    def test_satis_iadeleri_dusulur(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        # 610 Satıştan İadeler (-): borç
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("610", "B", "100"), _s("100", "A", "100")])
        gt = gelir_tablosu(*YIL)
        self.assertEqual(gt.deger("B."), Decimal("100.00"))
        self.assertEqual(gt.deger("Net Satışlar"), Decimal("900.00"))
        self.assertEqual(gt.donem_net_kari, Decimal("900.00"))

    def test_iptal_haric(self):
        f = fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        fis_iptal(f)
        gt = gelir_tablosu(*YIL)
        self.assertEqual(gt.donem_net_kari, Decimal("0.00"))

    def test_view(self):
        bugun = timezone.localdate()
        fis_olustur(tarih=bugun, satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        r = self.client.get(reverse("core:gelir_tablosu"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DÖNEM NET KÂRI")
        self.assertContains(r, "1.000,00")
