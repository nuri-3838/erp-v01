"""Roll-up (Hesap Planı Aşama 2) — mizan özet/detay, bilanço/gelir alt->ana toplama,
bilanço dönem = gelir net (tutarlılık), otomatik kod (boşluk/çakışma).
Mevcut (alt hesapsız) raporlar ayrı test dosyalarında doğrulanır (değişmez)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.services.hesap_plani import hesap_olustur, hesap_sil, sonraki_alt_kod
from core.services.raporlar import bilanco, gelir_tablosu, mizan
from core.services.yevmiye import SatirGirdi, fis_olustur

D = datetime.date
YIL = (D(2026, 1, 1), D(2026, 12, 31))


class RollupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.u = User.objects.create_superuser("yon", password="x")
        # 100 (KASA, BILANCO/DV) ve 600 (Yurtiçi Satışlar, GELIR) altına alt hesaplar
        hesap_olustur(kod="100.01", ad="kasa a", ust_kodu="100", kullanici=cls.u)
        hesap_olustur(kod="100.02", ad="kasa b", ust_kodu="100", kullanici=cls.u)
        hesap_olustur(kod="600.10", ad="satis a", ust_kodu="600", kullanici=cls.u)
        hesap_olustur(kod="600.20", ad="satis b", ust_kodu="600", kullanici=cls.u)
        fis_olustur(tarih=D(2026, 3, 10), kullanici=cls.u,
                    satirlar=[SatirGirdi("100.01", "B", "1.000,00"),
                              SatirGirdi("600.10", "A", "1.000,00")])
        fis_olustur(tarih=D(2026, 3, 11), kullanici=cls.u,
                    satirlar=[SatirGirdi("100.02", "B", "500,00"),
                              SatirGirdi("600.20", "A", "500,00")])

    def test_mizan_ozet_ana_toplam(self):
        m = mizan(*YIL, detay=False)
        d = {s.hesap_kodu: s for s in m.satirlar}
        self.assertIn("100", d)
        self.assertIn("600", d)
        self.assertNotIn("100.01", d)        # özet: alt görünmez
        self.assertNotIn("600.10", d)
        self.assertEqual(d["100"].borc, Decimal("1500.00"))      # 1000+500 rolled
        self.assertEqual(d["600"].alacak, Decimal("1500.00"))
        self.assertEqual(m.toplam_borc, m.toplam_alacak)         # denk

    def test_mizan_detay_hepsi_ve_denge(self):
        m = mizan(*YIL, detay=True)
        d = {s.hesap_kodu: s for s in m.satirlar}
        for k in ("100", "100.01", "100.02", "600", "600.10", "600.20"):
            self.assertIn(k, d)
        self.assertEqual(d["100"].borc, Decimal("1500.00"))      # ana = rolled
        self.assertEqual(d["100"].seviye, 0)
        self.assertEqual(d["100.01"].borc, Decimal("1000.00"))   # alt = bireysel
        self.assertEqual(d["100.01"].seviye, 1)
        # toplam yalnız ana (seviye 0) -> çift saymaz
        self.assertEqual(m.toplam_borc, Decimal("1500.00"))
        self.assertEqual(m.toplam_borc, m.toplam_alacak)

    def test_bilanco_gelir_rollup_tutarli(self):
        b = bilanco(*YIL)
        g = gelir_tablosu(*YIL)
        self.assertTrue(b.denk_mi)                               # aktif = pasif
        self.assertEqual(b.donem_sonucu, g.donem_net_kari)       # TUTARLI
        self.assertEqual(g.deger("A."), Decimal("1500.00"))      # 600 rolled (600.10+600.20)
        self.assertEqual(g.donem_net_kari, Decimal("1500.00"))


class OtomatikKodTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.u = User.objects.create_superuser("yon", password="x")
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=cls.u)

    def test_sirali(self):
        self.assertEqual(sonraki_alt_kod("320.10"), "320.10.0001")
        hesap_olustur(kod="320.10.0001", ad="b", ust_kodu="320.10", kullanici=self.u)
        self.assertEqual(sonraki_alt_kod("320.10"), "320.10.0002")

    def test_bosluk_doldurur(self):
        hesap_olustur(kod="320.10.0001", ad="b", ust_kodu="320.10", kullanici=self.u)
        hesap_olustur(kod="320.10.0003", ad="c", ust_kodu="320.10", kullanici=self.u)
        self.assertEqual(sonraki_alt_kod("320.10"), "320.10.0002")   # boşluğu doldurur

    def test_silinmisle_cakismaz(self):
        hesap_olustur(kod="320.10.0001", ad="b", ust_kodu="320.10", kullanici=self.u)
        hesap_olustur(kod="320.10.0002", ad="c", ust_kodu="320.10", kullanici=self.u)
        hesap_sil(kod="320.10.0002", kullanici=self.u)               # soft-delete
        self.assertEqual(sonraki_alt_kod("320.10"), "320.10.0003")   # silinmişi atlar

    def test_l2_genislik(self):
        self.assertEqual(sonraki_alt_kod("320"), "320.01")           # L2: 2 hane
