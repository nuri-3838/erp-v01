"""Fiş numarası geri dönmez: en son fiş silinse bile sıradaki yeni fiş silinen numarayı almaz."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import FisNoSayaci, Kur
from core.services import fis_sil as fs
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date


def _fis(tarih=D(2026, 3, 10)):
    return fis_olustur(tarih=tarih, satirlar=[
        SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari="10"),
        SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari="10")])


class FisNoSayaciTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 3, 10), D(2027, 1, 5)):
            Kur.objects.create(tarih=g, usd_alis=Decimal("40"))
        _hesap("100.01", "KASA")
        _hesap("600.01", "SATIŞ", kalem="C", grup="GELIR_TABLOSU")
        cls.su = User.objects.create_superuser("sayac", password="x")

    def test_en_son_fis_silinince_numara_tekrar_verilmez(self):
        a, b, c = _fis(), _fis(), _fis()
        self.assertEqual([a.fis_no, b.fis_no, c.fis_no], [1, 2, 3])
        fs.fis_sil(c, kullanici=self.su)                  # EN SON fiş silindi
        d = _fis()
        self.assertEqual(d.fis_no, 4)                     # 3 geri dönmez
        fs.fis_sil(b, kullanici=self.su)                  # aradaki
        self.assertEqual(_fis().fis_no, 5)

    def test_sayac_satiri_yokken_de_silinen_numara_korunur(self):
        a, b = _fis(), _fis()
        FisNoSayaci.objects.all().delete()                # eski veri: sayaç henüz yok
        fs.fis_sil(b, kullanici=self.su)                  # silmeden önce sayaç max'a çekilir
        self.assertEqual(FisNoSayaci.objects.get(yil=2026).son_no, 2)
        self.assertEqual(_fis().fis_no, 3)

    def test_yillar_bagimsiz(self):
        _fis()
        y = _fis(D(2027, 1, 5))
        self.assertEqual(y.fis_no, 1)
        self.assertEqual(FisNoSayaci.objects.get(yil=2027).son_no, 1)

    def test_basarisiz_fis_numara_tuketmez(self):
        from core.services.yevmiye import YevmiyeHatasi
        _fis()
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari="10"),
                SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari="9")])    # dengesiz
        self.assertEqual(_fis().fis_no, 2)
