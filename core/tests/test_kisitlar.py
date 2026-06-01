"""DB seviyesi CHECK kısıtları — servis (kapı) atlanırsa son savunma hattı (kasa kilidi).

Servisi BİLEREK atlayıp doğrudan ORM ile geçersiz kayıt yazmaya çalışır; veritabanı
reddetmeli (IntegrityError).
"""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir

D = datetime.date


class DBKisitTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        cls.fis = YevmiyeFisi.objects.create(
            yil=2026, fis_no=1, tarih=D(2026, 3, 1), kur_usd=Decimal("30"))
        cls.h = HesapPlani.objects.get(hesap_kodu="100")

    def _satir(self, **kw):
        ortak = dict(fis=self.fis, hesap=self.h, islem_pb="TRY",
                     islem_tutari=Decimal("10"), islem_kuru=Decimal("1"),
                     borc=Decimal("0"), alacak=Decimal("0"))
        ortak.update(kw)
        return YevmiyeSatir.objects.create(**ortak)

    def test_gecerli_satir_kaydedilir(self):
        s = self._satir(borc=Decimal("10"))
        self.assertEqual(s.borc, Decimal("10"))

    def test_borc_ve_alacak_birden_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._satir(borc=Decimal("10"), alacak=Decimal("10"))

    def test_negatif_borc_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._satir(borc=Decimal("-5"))

    def test_islem_kuru_sifir_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._satir(borc=Decimal("10"), islem_kuru=Decimal("0"))

    def test_fis_kur_usd_sifir_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            YevmiyeFisi.objects.create(
                yil=2026, fis_no=2, tarih=D(2026, 3, 1), kur_usd=Decimal("0"))

    def test_fis_kur_usd_bos_olabilir(self):
        f = YevmiyeFisi.objects.create(
            yil=2026, fis_no=3, tarih=D(2026, 3, 1), kur_usd=None)
        self.assertIsNone(f.kur_usd)

    def test_kur_usd_alis_sifir_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Kur.objects.create(tarih=D(2030, 1, 1), usd_alis=Decimal("0"))
