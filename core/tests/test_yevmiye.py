"""Yevmiye servis + model testleri (spec bölüm 2-3): dengeli fiş, satır kuralları,
yabancı PB türetme, müteselsil no, kur_usd, iptal."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.models import Kur, YevmiyeFisi, YevmiyeSatir, HesapPlani
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_iptal, fis_olustur

D = datetime.date


def _try_satir(hesap_kodu, taraf, tutar, aciklama=""):
    return SatirGirdi(hesap_kodu=hesap_kodu, taraf=taraf, islem_tutari=tutar,
                      islem_pb="TRY", islem_kuru=Decimal("1"), aciklama=aciklama)


class YevmiyeTestTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")


class GecerliFisTest(YevmiyeTestTemel):
    def test_dengeli_try_fis_kaydedilir(self):
        fis = fis_olustur(
            tarih=D(2026, 3, 10),
            aciklama="kasa tahsilatı",
            satirlar=[
                _try_satir("100", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ],
        )
        self.assertEqual(fis.yil, 2026)
        self.assertEqual(fis.fis_no, 1)
        self.assertEqual(fis.aciklama, "KASA TAHSİLATI")  # TR büyük harf
        self.assertEqual(fis.satirlar.count(), 2)
        borc = fis.satirlar.get(hesap_id="100")
        self.assertEqual(borc.borc, Decimal("1000.00"))
        self.assertEqual(borc.alacak, Decimal("0.00"))

    def test_bakiye_alani_yok(self):
        # "Bakiyeler hesaplanır, saklanmaz": modelde bakiye alanı olmamalı.
        alanlar = {f.name for f in YevmiyeSatir._meta.get_fields()}
        self.assertNotIn("bakiye", alanlar)


class DengeVeSatirKurallariTest(YevmiyeTestTemel):
    def test_dengesiz_fis_reddedilir_ve_yazilmaz(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(
                tarih=D(2026, 3, 10),
                satirlar=[
                    _try_satir("100", "B", "1.000,00"),
                    _try_satir("600", "A", "900,00"),
                ],
            )
        self.assertEqual(YevmiyeFisi.objects.count(), 0)
        self.assertEqual(YevmiyeSatir.objects.count(), 0)

    def test_en_az_iki_satir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10),
                        satirlar=[_try_satir("100", "B", "1.000,00")])

    def test_sifir_tutar_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("100", "B", "0,00"),
                _try_satir("600", "A", "0,00"),
            ])

    def test_negatif_tutar_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("100", "B", "-5,00"),
                _try_satir("600", "A", "-5,00"),
            ])

    def test_try_kuru_bir_olmali(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                SatirGirdi("100", "B", "1.000,00", "TRY", Decimal("2")),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_gecersiz_kur_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                SatirGirdi("100", "B", "1.000,00", "USD", Decimal("0")),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_pasif_hesap_reddedilir(self):
        HesapPlani.objects.filter(hesap_kodu="153").update(aktif=False)
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("153", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_olmayan_hesap_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("999", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ])


class YabanciParaTest(YevmiyeTestTemel):
    def test_eur_tl_turetilir(self):
        # 1.000 EUR × 35 = 35.000 TL (borç); karşı TRY 35.000 alacak
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            SatirGirdi("153", "B", "1.000,00", "EUR", Decimal("35")),
            _try_satir("320", "A", "35.000,00"),
        ])
        satir = fis.satirlar.get(hesap_id="153")
        self.assertEqual(satir.borc, Decimal("35000.00"))
        self.assertEqual(satir.islem_pb, "EUR")
        self.assertEqual(satir.islem_tutari, Decimal("1000.00"))
        self.assertEqual(satir.islem_kuru, Decimal("35"))

    def test_tl_round_half_up(self):
        # 100,00 USD × 30,123456 = 3012,3456 -> 3012,35 (ROUND_HALF_UP)
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            SatirGirdi("100", "B", "100,00", "USD", Decimal("30.123456")),
            _try_satir("600", "A", "3.012,35"),
        ])
        self.assertEqual(fis.satirlar.get(hesap_id="100").borc, Decimal("3012.35"))


class MuteselsilNoTest(YevmiyeTestTemel):
    def test_yil_icinde_artar_yil_basinda_sifirlanir(self):
        f1 = fis_olustur(tarih=D(2026, 1, 5), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        f2 = fis_olustur(tarih=D(2026, 6, 5), satirlar=[
            _try_satir("100", "B", "20,00"), _try_satir("600", "A", "20,00")])
        f3 = fis_olustur(tarih=D(2027, 1, 2), satirlar=[
            _try_satir("100", "B", "30,00"), _try_satir("600", "A", "30,00")])
        self.assertEqual((f1.yil, f1.fis_no), (2026, 1))
        self.assertEqual((f2.yil, f2.fis_no), (2026, 2))
        self.assertEqual((f3.yil, f3.fis_no), (2027, 1))


class IptalTest(YevmiyeTestTemel):
    def test_iptal_numarayi_korur(self):
        f1 = fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        fis_iptal(f1)
        f1.refresh_from_db()
        self.assertTrue(f1.silindi)
        self.assertTrue(all(s.silindi for s in f1.satirlar.all()))
        # Yeni fiş numarayı yeniden KULLANMAZ -> 2
        f2 = fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try_satir("100", "B", "20,00"), _try_satir("600", "A", "20,00")])
        self.assertEqual(f2.fis_no, 2)


class KurUsdTest(YevmiyeTestTemel):
    def _kur(self, y, m, d, usd):
        return Kur.objects.create(tarih=D(y, m, d), usd_alis=Decimal(usd),
                                  eur_alis=Decimal("38"), gbp_alis=Decimal("44"))

    def test_otomatik_fis_tarihine_gore(self):
        self._kur(2026, 3, 10, "32.5")
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(fis.kur_usd, Decimal("32.500000"))

    def test_hafta_sonu_son_yayimlanan(self):
        self._kur(2026, 3, 13, "33")  # Cuma
        # 2026-03-14 Cumartesi -> kur yok -> son yayımlanan (Cuma) kullanılır
        fis = fis_olustur(tarih=D(2026, 3, 14), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(fis.kur_usd, Decimal("33.000000"))

    def test_kur_yoksa_bos_ama_fis_kaydedilir(self):
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertIsNone(fis.kur_usd)
        self.assertEqual(YevmiyeFisi.objects.count(), 1)

    def test_elle_override(self):
        self._kur(2026, 3, 10, "32")
        fis = fis_olustur(tarih=D(2026, 3, 10), kur_usd=Decimal("35"), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(fis.kur_usd, Decimal("35"))
