"""Depo ↔ fasoncu bağı (Depo.fason_cari) ve ham profil sevki (mevcut depo transferi): maliyet transferle taşınır, değer değişmez."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, Depo, FaturaTipi, HesapPlani, KategoriHesap, Kategori, Stok, StokHareket
from core.services import depo as depo_servis
from core.services.depo_transfer import depo_transferi_yap
from core.services.hareket import eldeki_miktar, hareket_ekle

D = Decimal


class FasonDepoTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fd_y", password="x")
        cls.salim = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")
        cls.diger = Cari.objects.create(kod="320-10-0002", unvan="DİĞER FASON", muhasebe_kodu="320.10.0002")

    def test_olustur_ve_baglama(self):
        d = depo_servis.depo_olustur(kod="999", ad="FASON-SALİM", fason_cari=self.salim.pk)
        self.assertEqual(d.fason_cari, self.salim)
        self.assertEqual(depo_servis.fason_deposu(self.salim), d)
        self.assertIsNone(depo_servis.fason_deposu(self.diger))
        normal = depo_servis.depo_olustur(kod="100", ad="ANA DEPO")
        self.assertIsNone(normal.fason_cari)

    def test_bir_cariye_tek_fason_deposu(self):
        depo_servis.depo_olustur(kod="999", ad="FASON-SALİM", fason_cari=self.salim)
        with self.assertRaises(depo_servis.DepoHatasi):
            depo_servis.depo_olustur(kod="998", ad="FASON-SALİM-2", fason_cari=self.salim)
        d2 = depo_servis.depo_olustur(kod="998", ad="FASON-DIGER", fason_cari=self.diger)
        with self.assertRaises(depo_servis.DepoHatasi):
            depo_servis.depo_guncelle(d2, kod="998", ad="FASON-DIGER", fason_cari=self.salim)
        depo_servis.depo_guncelle(d2, kod="998", ad="FASON-DIGER", fason_cari=self.diger)           # kendi bağını koruyabilir

    def test_baglantiyi_kaldir_ve_silince_serbest(self):
        d = depo_servis.depo_olustur(kod="999", ad="FASON-SALİM", fason_cari=self.salim)
        depo_servis.depo_guncelle(d, kod="999", ad="FASON-SALİM", fason_cari=None)
        self.assertIsNone(depo_servis.fason_deposu(self.salim))
        depo_servis.depo_olustur(kod="998", ad="YENI", fason_cari=self.salim)                       # artık başka depoya bağlanabilir

    def test_gecersiz_cari(self):
        with self.assertRaises(depo_servis.DepoHatasi):
            depo_servis.depo_olustur(kod="999", ad="X", fason_cari=999999)

    def test_ekranlar(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:depo_ekle"), {"kod": "999", "ad": "fason-salim", "fason_cari": self.salim.pk})
        self.assertRedirects(r, reverse("core:depolar"))
        d = Depo.objects.get(kod="999")
        self.assertEqual(d.fason_cari, self.salim)
        liste = self.client.get(reverse("core:depolar"))
        self.assertContains(liste, "SALİM FASON")
        self.assertContains(liste, "Fason")
        r = self.client.get(reverse("core:depo_duzenle", args=[d.pk]))
        self.assertEqual(r.context["form"].initial["fason_cari"], self.salim.pk)
        self.client.post(reverse("core:depo_duzenle", args=[d.pk]), {"kod": "999", "ad": "FASON-SALİM", "fason_cari": ""})
        d.refresh_from_db()
        self.assertIsNone(d.fason_cari_id)


class SevkMaliyetTest(TestCase):
    """Ham profil ana depodan fason depoya transfer: miktar yer değiştirir, ortalama maliyet/stok değeri DEĞİŞMEZ, muhasebe fişi yok."""

    @classmethod
    def setUpTestData(cls):
        cls.salim = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")
        cls.ana = Depo.objects.create(kod="100", ad="ANA DEPO")
        cls.fason = Depo.objects.create(kod="999", ad="FASON-SALİM", fason_cari=cls.salim)
        birim = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        kat = Kategori.objects.create(kod="SV", ad="SEVK TEST")
        hesap = HesapPlani.objects.create(hesap_kodu="150.10", hesap_adi="HAMMADDE", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
        KategoriHesap.objects.create(kategori=kat, hesap=hesap, fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ SV", yon="ALIS"))
        cls.profil = Stok.objects.create(kod="150-10-0001", ad="PROFIL", kategori=kat, uretim_birimi=birim, fatura_birimi=birim,
                                         satinalma_urunu=True)

    def test_transfer_maliyeti_tasir(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.ana.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("10"),
                     giris_tutar_try=D("1000"), giris_tutar_usd=D("25"))
        self.profil.refresh_from_db()
        once = (self.profil.ort_maliyet_try, self.profil.maliyet_deger_try, self.profil.maliyet_deger_usd)
        depo_transferi_yap(stok_id=self.profil.pk, kaynak_depo_id=self.ana.pk, hedef_depo_id=self.fason.pk, tarih=date(2026, 1, 5), miktar=D("4"))
        self.profil.refresh_from_db()
        self.assertEqual((self.profil.ort_maliyet_try, self.profil.maliyet_deger_try, self.profil.maliyet_deger_usd), once)
        self.assertEqual((eldeki_miktar(self.profil, self.ana), eldeki_miktar(self.profil, self.fason)), (D("6"), D("4")))
        self.assertEqual(self.profil.ort_maliyet_try, D("100"))
