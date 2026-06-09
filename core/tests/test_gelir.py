"""Gelir tablosu testleri (spec 5b) — yalnızca 6'lı, 7'liler hariç, harita doğru."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
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
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.kullanici = User.objects.create_superuser("test", password="parola1234")

    def setUp(self):
        self.client.force_login(self.kullanici)

    def test_satis_ve_gider_net_kar(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("632", "B", "300"), _s("100", "A", "300")])
        gt = gelir_tablosu(*YIL)
        self.assertEqual(gt.deger("A."), Decimal("1000.00"))
        self.assertEqual(gt.deger("Net Satışlar"), Decimal("1000.00"))
        self.assertEqual(gt.deger("D."), Decimal("300.00"))
        self.assertEqual(gt.deger("FAALİYET KÂRI"), Decimal("700.00"))
        self.assertEqual(gt.donem_net_kari, Decimal("700.00"))

    def test_7xx_yansitilmamis_seffaf_ve_bilanco_ile_tutarli(self):
        # 600 satış 1000; 710 (7/A maliyet) 500 -> standart satırlara karışmaz ama
        # "yansıtılmamış" satırında şeffaf görünür ve net kârı düşürür (bilanço ile aynı).
        from core.services.raporlar import bilanco
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 3), satirlar=[_s("710", "B", "500"), _s("100", "A", "500")])
        gt = gelir_tablosu(*YIL)
        b = bilanco(YIL[1])
        self.assertEqual(gt.deger("Vergi Sonrası Kâr"), Decimal("1000.00"))   # 6'lı kârı
        self.assertEqual(gt.deger("Yansıtılmamış"), Decimal("-500.00"))        # 7xx şeffaf
        self.assertEqual(gt.donem_net_kari, Decimal("500.00"))                 # 1000 - 500
        self.assertEqual(b.donem_sonucu, gt.donem_net_kari)                    # HER ZAMAN eşit

    def test_haritada_olmayan_yeni_6xx_otomatik_girer(self):
        # Eski sabit kod listesinde OLMAYAN yeni bir GELIR hesabı (603, kalem A) açılır;
        # veri-güdümlü gelir tablosu onu otomatik A bölümüne alır, bilanço ile tutar.
        from core.services.hesap_plani import hesap_olustur
        from core.services.raporlar import bilanco
        hesap_olustur(kod="603", ad="özel satış geliri",
                      rapor_grubu="GELIR_TABLOSU", rapor_kalemi="A")
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "700"), _s("603", "A", "700")])
        gt = gelir_tablosu(*YIL)
        b = bilanco(YIL[1])
        self.assertEqual(gt.deger("A."), Decimal("700.00"))     # otomatik A bölümünde
        self.assertEqual(gt.donem_net_kari, Decimal("700.00"))
        self.assertEqual(b.donem_sonucu, gt.donem_net_kari)     # bilanço = gelir

    def test_bilanco_gelir_karisik_senaryoda_esit(self):
        from core.services.raporlar import bilanco
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "5000"), _s("600", "A", "5000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[_s("632", "B", "800"), _s("100", "A", "800")])
        fis_olustur(tarih=D(2026, 3, 3), satirlar=[_s("770", "B", "600"), _s("100", "A", "600")])
        gt = gelir_tablosu(*YIL)
        b = bilanco(YIL[1])
        self.assertEqual(b.donem_sonucu, gt.donem_net_kari)
        self.assertTrue(b.denk_mi)

    def test_usd_bilanco_gelir_esit(self):
        from core.services.raporlar import bilanco_usd, gelir_tablosu_usd
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "3000"), _s("600", "A", "3000")])
        fis_olustur(tarih=D(2026, 3, 3), satirlar=[_s("710", "B", "900"), _s("100", "A", "900")])
        gu = gelir_tablosu_usd(*YIL)
        bu = bilanco_usd(YIL[1])
        self.assertEqual(bu.donem_sonucu, gu.donem_net_kari)   # USD'de de her zaman eşit

    def test_satis_iadeleri_dusulur(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[_s("100", "B", "1000"), _s("600", "A", "1000")])
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
