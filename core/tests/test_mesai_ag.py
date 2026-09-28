"""İNSAN KAYNAKLARI > Mesai Ayarları — fabrika ağı (IP/CIDR) izin listesi servis testleri."""
from django.test import TestCase

from core.services.mesai_ag import (
    MesaiAgHatasi, ag_ekle, ag_guncelle, ag_sil, ip_izinli_mi,
)


class AgCrudTest(TestCase):
    def test_ekle_ve_benzersizlik(self):
        ag_ekle(cidr="10.0.0.0/24", aciklama="fabrika")
        with self.assertRaises(MesaiAgHatasi):
            ag_ekle(cidr="10.0.0.0/24")

    def test_gecersiz_cidr_reddedilir(self):
        with self.assertRaises(MesaiAgHatasi):
            ag_ekle(cidr="saçma-deger")

    def test_bos_cidr_reddedilir(self):
        with self.assertRaises(MesaiAgHatasi):
            ag_ekle(cidr="  ")

    def test_tekil_ip_kabul_edilir(self):
        ag = ag_ekle(cidr="5.6.7.8")
        self.assertEqual(ag.cidr, "5.6.7.8")

    def test_guncelle_ve_sil(self):
        ag = ag_ekle(cidr="10.0.0.0/24")
        ag_guncelle(ag, cidr="10.0.1.0/24", aciklama="yeni")
        ag.refresh_from_db()
        self.assertEqual(ag.cidr, "10.0.1.0/24")
        ag_sil(ag)
        ag.refresh_from_db()
        self.assertTrue(ag.silindi)
        ag_sil(ag)                                  # idempotent

    def test_silinmis_duzenlenemez(self):
        ag = ag_ekle(cidr="10.0.0.0/24")
        ag_sil(ag)
        with self.assertRaises(MesaiAgHatasi):
            ag_guncelle(ag, cidr="10.0.0.0/24")


class IpIzinliMiTest(TestCase):
    def test_liste_bossa_daima_false(self):
        self.assertFalse(ip_izinli_mi("10.0.0.5"))

    def test_cidr_araliginda_true(self):
        ag_ekle(cidr="10.0.0.0/24")
        self.assertTrue(ip_izinli_mi("10.0.0.5"))
        self.assertTrue(ip_izinli_mi("10.0.0.254"))

    def test_cidr_disinda_false(self):
        ag_ekle(cidr="10.0.0.0/24")
        self.assertFalse(ip_izinli_mi("10.0.1.5"))

    def test_tekil_ip_yalniz_kendisi(self):
        ag_ekle(cidr="5.6.7.8")
        self.assertTrue(ip_izinli_mi("5.6.7.8"))
        self.assertFalse(ip_izinli_mi("5.6.7.9"))

    def test_gecersiz_ip_false(self):
        ag_ekle(cidr="10.0.0.0/24")
        self.assertFalse(ip_izinli_mi("bu-bir-ip-degil"))
        self.assertFalse(ip_izinli_mi(""))

    def test_silinmis_ag_sayilmaz(self):
        ag = ag_ekle(cidr="10.0.0.0/24")
        ag_sil(ag)
        self.assertFalse(ip_izinli_mi("10.0.0.5"))

    def test_ipv6_ile_ipv4_karisik_karsilastirma_hata_vermez(self):
        ag_ekle(cidr="10.0.0.0/24")
        self.assertFalse(ip_izinli_mi("::1"))
