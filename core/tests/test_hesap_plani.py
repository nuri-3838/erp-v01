"""HESAP_PLANI modeli + CSV seed testleri (spec bölüm 2)."""
from django.core.management import call_command
from django.test import TestCase

from core.models import HesapPlani

# 81 TDHP hesabı + sonradan eklenen 250 Arazi ve Arsalar = 82.
TOPLAM_HESAP = 82


class SeedHesapPlaniTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_toplam_sayisi(self):
        self.assertEqual(HesapPlani.objects.count(), TOPLAM_HESAP)

    def test_her_grup_dolu(self):
        for grup in HesapPlani.RaporGrubu.values:
            with self.subTest(grup=grup):
                self.assertTrue(
                    HesapPlani.objects.filter(rapor_grubu=grup).exists()
                )

    def test_bilanco_parasal_bool(self):
        kasa = HesapPlani.objects.get(hesap_kodu="100")
        self.assertEqual(kasa.hesap_adi, "KASA")  # TR büyük harf saklanır
        self.assertEqual(kasa.rapor_grubu, HesapPlani.RaporGrubu.BILANCO)
        self.assertIs(kasa.parasal, True)
        # Parasal olmayan bilanço kalemi (ticari mallar)
        self.assertIs(HesapPlani.objects.get(hesap_kodu="153").parasal, False)

    def test_gelir_ve_maliyet_parasal_bos(self):
        self.assertIsNone(HesapPlani.objects.get(hesap_kodu="600").parasal)
        self.assertIsNone(HesapPlani.objects.get(hesap_kodu="710").parasal)

    def test_rapor_kalemi_tire_bos_string(self):
        # "-" boş string olarak saklanır; gerçek kalem korunur
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="690").rapor_kalemi, "")
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="600").rapor_kalemi, "A")

    def test_hesap_adi_tr_buyuk_harf(self):
        # Tüm adlar BÜYÜK saklanır; İ harfi doğru (Yurtiçi -> YURTİÇİ)
        self.assertEqual(
            HesapPlani.objects.get(hesap_kodu="600").hesap_adi, "YURTİÇİ SATIŞLAR"
        )
        for ad in HesapPlani.objects.values_list("hesap_adi", flat=True):
            with self.subTest(ad=ad):
                from core.metin import buyuk_harf_tr
                self.assertEqual(ad, buyuk_harf_tr(ad))

    def test_soft_delete_ve_audit_alanlari(self):
        kasa = HesapPlani.objects.get(hesap_kodu="100")
        self.assertFalse(kasa.silindi)
        self.assertIsNone(kasa.silindi_at)
        self.assertIsNotNone(kasa.created_at)
        self.assertIsNotNone(kasa.updated_at)

    def test_idempotent(self):
        call_command("seed_hesap_plani")
        self.assertEqual(HesapPlani.objects.count(), TOPLAM_HESAP)
