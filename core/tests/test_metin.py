"""TR büyük harf testleri (spec 0b-c — zorunlu)."""
from django.test import SimpleTestCase

from core.metin import buyuk_harf_tr


class BuyukHarfTrTest(SimpleTestCase):
    def test_spec_ornekleri(self):
        durumlar = {
            "istanbul": "İSTANBUL",
            "ışık": "IŞIK",
            "iğne": "İĞNE",
            "çiçek": "ÇİÇEK",
        }
        for girdi, beklenen in durumlar.items():
            with self.subTest(girdi=girdi):
                self.assertEqual(buyuk_harf_tr(girdi), beklenen)

    def test_tum_tr_harfleri(self):
        self.assertEqual(buyuk_harf_tr("çğıiöşü"), "ÇĞIİÖŞÜ")

    def test_domain_kelime(self):
        self.assertEqual(
            buyuk_harf_tr("alüminyum merdiven"), "ALÜMİNYUM MERDİVEN"
        )

    def test_idempotent(self):
        self.assertEqual(buyuk_harf_tr("İSTANBUL"), "İSTANBUL")
        self.assertEqual(buyuk_harf_tr(buyuk_harf_tr("ışık")), "IŞIK")

    def test_bos_ve_isaret_korunur(self):
        self.assertEqual(buyuk_harf_tr(""), "")
        self.assertEqual(buyuk_harf_tr("kdv %20"), "KDV %20")

    def test_str_olmayan_hata(self):
        with self.assertRaises(TypeError):
            buyuk_harf_tr(None)
