"""Mizan testleri (spec 5a) — yalnızca yevmiyeden, iptal hariç, tarih aralığı."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.services.raporlar import mizan
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date


def _satir(kod, taraf, tutar):
    return SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


def _harita(m):
    return {r.hesap_kodu: r for r in m.satirlar}


class MizanTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_mizan_tutar_ve_bakiye(self):
        fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _satir("100", "B", "1000"), _satir("600", "A", "1000")])
        m = mizan(D(2026, 1, 1), D(2026, 12, 31))
        self.assertTrue(m.hareket_denk)               # SUM(borç)=SUM(alacak)
        self.assertEqual(m.toplam_borc, Decimal("1000.00"))
        self.assertEqual(m.toplam_alacak, Decimal("1000.00"))
        s = _harita(m)
        self.assertEqual(s["100"].borc, Decimal("1000.00"))
        self.assertEqual(s["100"].borc_bakiye, Decimal("1000.00"))
        self.assertEqual(s["100"].alacak_bakiye, Decimal("0.00"))
        self.assertEqual(s["600"].alacak_bakiye, Decimal("1000.00"))

    def test_net_bakiye_borc_eksi_alacak(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _satir("100", "B", "1000"), _satir("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _satir("320", "B", "300"), _satir("100", "A", "300")])
        m = mizan(D(2026, 1, 1), D(2026, 12, 31))
        s = _harita(m)
        self.assertEqual(s["100"].borc, Decimal("1000.00"))
        self.assertEqual(s["100"].alacak, Decimal("300.00"))
        self.assertEqual(s["100"].borc_bakiye, Decimal("700.00"))  # net borç
        self.assertTrue(m.hareket_denk)
        self.assertTrue(m.bakiye_denk)
        self.assertEqual(m.toplam_borc_bakiye, m.toplam_alacak_bakiye)

    def test_iptal_edilen_haric(self):
        f = fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _satir("100", "B", "500"), _satir("600", "A", "500")])
        fis_iptal(f)
        m = mizan(D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(m.satirlar, [])
        self.assertEqual(m.toplam_borc, Decimal("0.00"))

    def test_tarih_araligi_filtreler(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _satir("100", "B", "500"), _satir("600", "A", "500")])
        self.assertEqual(mizan(D(2027, 1, 1), D(2027, 12, 31)).satirlar, [])
        self.assertEqual(len(mizan(D(2026, 1, 1), D(2026, 12, 31)).satirlar), 2)

    def test_view_varsayilan_yil(self):
        bugun = timezone.localdate()
        fis_olustur(tarih=bugun, satirlar=[
            _satir("100", "B", "250"), _satir("600", "A", "250")])
        r = self.client.get(reverse("core:mizan"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MİZAN TUTUYOR")
        self.assertContains(r, "KASA")
        self.assertContains(r, "250,00")
