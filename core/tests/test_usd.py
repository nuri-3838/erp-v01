"""USD görünümü testleri (spec 5c) — tarihi kur çevrimi, kursuz fiş hariç,
parasal=kapanış / parasal değil=tarihi kur, kur çevrim farkı."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Kur
from core.sayi import yuvarla
from core.services.raporlar import bilanco_usd, gelir_tablosu_usd
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date
YIL = (D(2026, 1, 1), D(2026, 12, 31))


def _s(kod, taraf, tutar):
    return SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


def _b_tutar(bil, kod):
    for g in list(bil.aktif) + list(bil.pasif):
        for s in g.satirlar:
            if s.kod == kod:
                return s.tutar
    return None


class GelirUSDTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_cevrim_dogru(self):
        # 30.000 TL satış, fiş kuru 30 -> 1.000 USD
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        gt = gelir_tablosu_usd(*YIL)
        self.assertEqual(yuvarla(gt.deger("A."), 2), Decimal("1000.00"))
        self.assertEqual(yuvarla(gt.donem_net_kari, 2), Decimal("1000.00"))
        self.assertEqual(gt.haric_tl, Decimal("0.00"))

    def test_kursuz_fis_haric(self):
        # Fiş 1: kuru YOK -> USD'ye girmez, haric_tl'ye eklenir
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=None,
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        # Fiş 2: kuru 30 -> A = 500 USD
        fis_olustur(tarih=D(2026, 3, 2), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "15000"), _s("600", "A", "15000")])
        gt = gelir_tablosu_usd(*YIL)
        self.assertEqual(yuvarla(gt.deger("A."), 2), Decimal("500.00"))
        self.assertEqual(yuvarla(gt.donem_net_kari, 2), Decimal("500.00"))
        self.assertEqual(yuvarla(gt.haric_tl, 2), Decimal("30000.00"))

    def test_iptal_haric(self):
        f = fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"),
                        satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        fis_iptal(f)
        gt = gelir_tablosu_usd(*YIL)
        self.assertEqual(gt.donem_net_kari, Decimal("0.00"))
        self.assertEqual(gt.haric_tl, Decimal("0.00"))

    def test_view(self):
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        r = self.client.get(reverse("core:gelir_tablosu_usd"),
                             {"baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DÖNEM NET KÂRI")
        self.assertContains(r, "1.000,00")


class BilancoUSDTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_spec_ornegi_parasal_vs_tarihi(self):
        # Açılış: Kasa(parasal) 100.000 / Sermaye(parasal değil) 100.000, fiş kuru 30
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "100000"), _s("500", "A", "100000")])
        # Rapor tarihi kuru (kapanış) = 40
        Kur.objects.create(tarih=D(2026, 3, 31), usd_alis=Decimal("40"),
                           eur_alis=Decimal("44"), gbp_alis=Decimal("50"))
        b = bilanco_usd(D(2026, 1, 1), D(2026, 3, 31))
        self.assertEqual(b.kur, Decimal("40.000000"))
        # Parasal (Kasa) kapanış kuruyla: 100.000 / 40 = 2.500
        self.assertEqual(yuvarla(_b_tutar(b, "100"), 2), Decimal("2500.00"))
        # Parasal değil (Sermaye) tarihi kurla: 100.000 / 30 = 3.333,33
        self.assertEqual(yuvarla(_b_tutar(b, "500"), 2), Decimal("3333.33"))
        # Kur çevrim farkı: 2.500 - 3.333,33 = -833,33 (USD kayıp)
        self.assertEqual(yuvarla(b.cevrim_farki, 2), Decimal("-833.33"))
        self.assertEqual(yuvarla(b.aktif_toplam, 2), Decimal("2500.00"))
        self.assertTrue(b.denk_mi)

    def test_kur_yoksa_isaretlenir(self):
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=None,
                    satirlar=[_s("100", "B", "100000"), _s("500", "A", "100000")])
        b = bilanco_usd(D(2026, 1, 1), D(2026, 3, 31))   # KUR tablosu boş
        self.assertTrue(b.kur_yok)

    def test_view(self):
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "100000"), _s("500", "A", "100000")])
        Kur.objects.create(tarih=D(2026, 3, 31), usd_alis=Decimal("40"),
                           eur_alis=Decimal("44"), gbp_alis=Decimal("50"))
        r = self.client.get(reverse("core:bilanco_usd"),
                             {"baslangic": "2026-01-01", "bitis": "2026-03-31"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AKTİF = PASİF")
        self.assertContains(r, "KUR ÇEVRİM FARKI")
