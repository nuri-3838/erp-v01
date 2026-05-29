"""Sayı parser/formatter testleri (spec 0b-a — zorunlu)."""
from decimal import Decimal

from django.test import SimpleTestCase

from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla


class ParseTrTest(SimpleTestCase):
    def test_spec_zorunlu_girdiler(self):
        durumlar = {
            "10,35": Decimal("10.35"),
            "1.035,00": Decimal("1035.00"),
            "1.035": Decimal("1035"),
            "0,5": Decimal("0.5"),
            "-1.234.567,89": Decimal("-1234567.89"),
            "1000000": Decimal("1000000"),
            "10.35": Decimal("10.35"),  # kanonik
        }
        for girdi, beklenen in durumlar.items():
            with self.subTest(girdi=girdi):
                self.assertEqual(parse_tr(girdi), beklenen)

    def test_bos_deger_hata(self):
        for girdi in ("", "   ", "\t"):
            with self.subTest(girdi=girdi):
                with self.assertRaises(SayiHatasi):
                    parse_tr(girdi)

    def test_bosluk_kirpilir(self):
        self.assertEqual(parse_tr("  10,35  "), Decimal("10.35"))

    def test_arti_isareti(self):
        self.assertEqual(parse_tr("+1.035,00"), Decimal("1035.00"))

    def test_decimal_ve_int_gecer(self):
        self.assertEqual(parse_tr(Decimal("3.14")), Decimal("3.14"))
        self.assertEqual(parse_tr(1000000), Decimal("1000000"))

    def test_gecersiz_girdiler_hata(self):
        for girdi in ("abc", "1,2,3", "1.23.45", "10,3,5", "1..2", "12,", ",5", "1.2.3"):
            with self.subTest(girdi=girdi):
                with self.assertRaises(SayiHatasi):
                    parse_tr(girdi)

    def test_float_kabul_edilmez(self):
        # float yanlışlıkla geçirilirse net hata; sessizce kabul edilmez.
        with self.assertRaises(SayiHatasi):
            parse_tr(10.35)


class YuvarlaTest(SimpleTestCase):
    def test_round_half_up(self):
        self.assertEqual(yuvarla(Decimal("10.345")), Decimal("10.35"))
        self.assertEqual(yuvarla(Decimal("10.344")), Decimal("10.34"))
        self.assertEqual(yuvarla(Decimal("10.355")), Decimal("10.36"))
        # Klasik float-hatası vakası: 2.675 -> 2.68 (Decimal doğru yapar)
        self.assertEqual(yuvarla(Decimal("2.675")), Decimal("2.68"))

    def test_negatif_half_up(self):
        # ROUND_HALF_UP eşitlikte sıfırdan UZAĞA yuvarlar: -2.675 -> -2.68
        self.assertEqual(yuvarla(Decimal("-2.675")), Decimal("-2.68"))

    def test_kur_6_basamak(self):
        self.assertEqual(yuvarla(Decimal("30.1234565"), 6), Decimal("30.123457"))


class FormatTrTest(SimpleTestCase):
    def test_spec_ornekleri(self):
        self.assertEqual(format_tr(Decimal("1234567.89")), "1.234.567,89")
        self.assertEqual(format_tr(Decimal("0.5")), "0,50")
        self.assertEqual(format_tr(Decimal("-1234567.89")), "-1.234.567,89")
        self.assertEqual(format_tr(Decimal("1035")), "1.035,00")

    def test_basamak_sifir(self):
        self.assertEqual(format_tr(Decimal("1000000"), 0), "1.000.000")

    def test_yuvarlayarak_gosterir(self):
        self.assertEqual(format_tr(Decimal("2.675")), "2,68")

    def test_negatif_sifir_olmaz(self):
        self.assertEqual(format_tr(Decimal("-0.001")), "0,00")

    def test_kucuk_sayi(self):
        self.assertEqual(format_tr(Decimal("7")), "7,00")


class RoundTripTest(SimpleTestCase):
    def test_parse_format_dongu(self):
        for girdi, beklenen in (
            ("1.234.567,89", "1.234.567,89"),
            ("10,35", "10,35"),
            ("1.035", "1.035,00"),
        ):
            with self.subTest(girdi=girdi):
                self.assertEqual(format_tr(parse_tr(girdi)), beklenen)
