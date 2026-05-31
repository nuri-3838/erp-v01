"""USD raporlama testleri (5c — tarihsel/donmuş model).

Her satırın USD'si KENDİ fişinin kuruyla donar; rapor günü kuru sonucu değiştirmez.
USD bilanço/gelir tablosu USD mizandan, TL ile aynı mantıkla türetilir.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.sayi import yuvarla
from core.services.raporlar import bilanco_usd, gelir_tablosu_usd, mizan_usd
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date
YIL = (D(2026, 1, 1), D(2026, 12, 31))


def _s(kod, taraf, tutar):
    return SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


def _harita(m):
    return {s.hesap_kodu: s for s in m.satirlar}


class _Girisli(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        cls.kullanici = User.objects.create_user("test", password="parola1234")

    def setUp(self):
        self.client.force_login(self.kullanici)


class MizanUSDTest(_Girisli):
    def test_donmus_usd_farkli_kurlar_toplanir(self):
        fis_olustur(tarih=D(2026, 1, 15), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])  # 1000 USD
        fis_olustur(tarih=D(2026, 6, 15), kur_usd=Decimal("40"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])  # 750 USD
        m = mizan_usd(*YIL)
        d = _harita(m)
        self.assertEqual(yuvarla(d["600"].usd_alacak, 2), Decimal("1750.00"))
        self.assertEqual(yuvarla(d["100"].usd_borc, 2), Decimal("1750.00"))
        self.assertTrue(m.hareket_denk)

    def test_rapor_gunu_kuru_etkilemez(self):
        fis_olustur(tarih=D(2026, 2, 1), kur_usd=Decimal("25"),
                    satirlar=[_s("100", "B", "25000"), _s("600", "A", "25000")])
        m1 = mizan_usd(D(2026, 1, 1), D(2026, 6, 30))
        m2 = mizan_usd(D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(_harita(m1)["600"].usd_alacak, _harita(m2)["600"].usd_alacak)
        self.assertEqual(yuvarla(_harita(m1)["600"].usd_alacak, 2), Decimal("1000.00"))

    def test_usd_islem_satiri_orijinal_usd_kullanir(self):
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("44"), satirlar=[
            SatirGirdi("120", "B", Decimal("1000"), "USD", Decimal("45")),
            _s("601", "A", "45000"),
        ])
        d = _harita(mizan_usd(*YIL))
        self.assertEqual(yuvarla(d["120"].usd_borc, 2), Decimal("1000.00"))

    def test_kursuz_fis_haric(self):
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=None,
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        fis_olustur(tarih=D(2026, 3, 2), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "15000"), _s("600", "A", "15000")])
        m = mizan_usd(*YIL)
        self.assertEqual(yuvarla(m.haric_tl, 2), Decimal("30000.00"))
        self.assertEqual(yuvarla(_harita(m)["600"].usd_alacak, 2), Decimal("500.00"))

    def test_iptal_haric(self):
        f = fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"),
                        satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        fis_iptal(f)
        m = mizan_usd(*YIL)
        self.assertEqual(m.satirlar, [])
        self.assertEqual(m.haric_tl, Decimal("0.00"))


class GelirUSDTest(_Girisli):
    def test_farkli_kurlu_faturalar_toplami(self):
        fis_olustur(tarih=D(2026, 1, 15), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        fis_olustur(tarih=D(2026, 6, 15), kur_usd=Decimal("40"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        gt = gelir_tablosu_usd(*YIL)
        self.assertEqual(yuvarla(gt.deger("A."), 2), Decimal("1750.00"))
        self.assertEqual(yuvarla(gt.donem_net_kari, 2), Decimal("1750.00"))

    def test_kursuz_haric(self):
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=None,
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        gt = gelir_tablosu_usd(*YIL)
        self.assertEqual(yuvarla(gt.haric_tl, 2), Decimal("30000.00"))
        self.assertEqual(gt.donem_net_kari, Decimal("0.00"))

    def test_view(self):
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("600", "A", "30000")])
        r = self.client.get(reverse("core:gelir_tablosu_usd"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "DÖNEM NET KÂRI")
        self.assertContains(r, "1.000,00")


class BilancoUSDTest(_Girisli):
    def test_usd_mizandan_dengeli(self):
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "100000"), _s("500", "A", "100000")])
        b = bilanco_usd(*YIL)
        self.assertTrue(b.denk_mi)
        kasa = [s for g in b.aktif for s in g.satirlar if s.kod == "100"][0]
        sermaye = [s for g in b.pasif for s in g.satirlar if s.kod == "500"][0]
        self.assertEqual(yuvarla(kasa.tutar, 2), Decimal("3333.33"))
        self.assertEqual(yuvarla(sermaye.tutar, 2), Decimal("3333.33"))

    def test_kar_pasife_yansir(self):
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "30000"), _s("500", "A", "30000")])
        fis_olustur(tarih=D(2026, 2, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "3000"), _s("600", "A", "3000")])
        b = bilanco_usd(*YIL)
        self.assertEqual(yuvarla(b.donem_sonucu, 2), Decimal("100.00"))
        self.assertTrue(b.denk_mi)

    def test_view(self):
        fis_olustur(tarih=D(2026, 1, 1), kur_usd=Decimal("30"),
                    satirlar=[_s("100", "B", "100000"), _s("500", "A", "100000")])
        r = self.client.get(reverse("core:bilanco_usd"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AKTİF = PASİF")
        self.assertContains(r, "3.333,33")
