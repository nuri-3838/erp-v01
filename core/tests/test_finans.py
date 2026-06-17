"""FİNANS — Kasa tanımı servis + view testleri. Bakiye saklanmaz; yaprak muhasebe
hesabına bağlanır (üst hesap reddedilir), ad TR büyük + benzersiz."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, Kasa
from core.services.finans import FinansHatasi, kasa_guncelle, kasa_olustur, kasa_sil


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class KasaServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("100", "KASA")             # üst (yaprak değil)
        _hesap("100.01", "MERKEZ KASA")   # yaprak
        _hesap("100.02", "DÖVİZ KASA")    # yaprak

    def test_olustur_tr_buyuk_ve_hesap(self):
        k = kasa_olustur(ad="merkez kasa", para_birimi="TRY", muhasebe_kodu="100.01")
        self.assertEqual(k.ad, "MERKEZ KASA")
        self.assertEqual(k.muhasebe.hesap_kodu, "100.01")

    def test_ust_hesap_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="X", muhasebe_kodu="100")

    def test_hesapsiz_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="Y", muhasebe_kodu="")

    def test_ad_benzersiz(self):
        kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="ana", muhasebe_kodu="100.02")

    def test_guncelle_ve_sil(self):
        k = kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        kasa_guncelle(k, ad="ANA2", para_birimi="USD", muhasebe_kodu="100.02")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.para_birimi, k.muhasebe.hesap_kodu), ("ANA2", "USD", "100.02"))
        kasa_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_bagli_hesap_silinemez(self):
        """Kasa bağlı bir muhasebe hesabı soft-delete edilemez (öksüz kalmasın)."""
        from core.services.hesap_plani import HesapHatasi, hesap_sil
        kasa_olustur(ad="ANA KASA", muhasebe_kodu="100.01")
        with self.assertRaises(HesapHatasi):
            hesap_sil(kod="100.01")

    def test_yaprak_olmayan_hesap_duzenlemede_kalir(self):
        """100.01'e alt hesap eklenince yaprak olmaktan çıkar; düzenleme formu yine de
        bağlı hesabı listede tutmalı (yoksa kayıt düzenlenemez). Ekleme formu tutmaz."""
        from core.forms import KasaForm
        from core.services.hesap_plani import yaprak_hesaplar
        _hesap("100.01.0001", "ALT")          # 100.01 artık yaprak değil
        self.assertFalse(yaprak_hesaplar().filter(hesap_kodu="100.01").exists())
        duz = KasaForm(mevcut_hesap="100.01")
        self.assertTrue(duz.fields["muhasebe"].queryset.filter(hesap_kodu="100.01").exists())
        ekle = KasaForm()
        self.assertFalse(ekle.fields["muhasebe"].queryset.filter(hesap_kodu="100.01").exists())


class KasaViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        _hesap("100", "KASA")
        _hesap("100.01", "MERKEZ KASA")

    def test_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kasa_ekle"),
                             {"ad": "merkez", "para_birimi": "TRY", "muhasebe": "100.01"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Kasa.objects.filter(ad="MERKEZ", muhasebe__hesap_kodu="100.01").exists())

    def test_liste_200_ve_yetkisiz_403(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 200)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 403)


class FinansDigerServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("102.01", "İŞ BANKASI")
        _hesap("300.01", "KREDİ HESABI")
        _hesap("309.01", "KREDİ KARTI")
        _hesap("101.01", "ALINAN ÇEKLER")

    def test_banka_kurum_ve_hesap(self):
        from core.services.finans import (banka_hesap_olustur, banka_hesaplari,
                                          banka_olustur)
        b = banka_olustur(ad="akbank", kisa_ad="ak", sube="merkez",
                          swift_kod="akbktris")
        self.assertEqual(b.ad, "AKBANK")
        self.assertEqual(b.swift_kod, "AKBKTRIS")
        h = banka_hesap_olustur(banka=b, ad="tl mevduat", iban="tr12 0006 4000",
                                muhasebe_kodu="102.01")
        self.assertEqual(h.ad, "TL MEVDUAT")
        self.assertEqual(h.iban, "TR1200064000")          # boşluksuz + büyük
        self.assertEqual(h.muhasebe.hesap_kodu, "102.01")
        self.assertEqual(list(banka_hesaplari(b)), [h])

    def test_banka_hesapli_silinemez(self):
        from core.services.finans import (FinansHatasi, banka_hesap_olustur,
                                          banka_olustur, banka_sil)
        b = banka_olustur(ad="garanti")
        banka_hesap_olustur(banka=b, ad="tl", muhasebe_kodu="102.01")
        with self.assertRaises(FinansHatasi):
            banka_sil(b)

    def test_para_birimi_servis_dogrular(self):
        """Geçersiz para birimi servis katmanında reddedilir (UI'a güvenilmez)."""
        from core.services.finans import (FinansHatasi, banka_hesap_olustur,
                                          banka_olustur, kasa_olustur)
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="GEÇERSİZ PB", para_birimi="XYZ", muhasebe_kodu="102.01")
        b = banka_olustur(ad="finansbank")
        with self.assertRaises(FinansHatasi):
            banka_hesap_olustur(banka=b, ad="usd", para_birimi="usd",
                                muhasebe_kodu="102.01")   # küçük harf → geçersiz

    def test_kredi_karti_gun_araligi_ve_limit(self):
        from decimal import Decimal
        from core.services.finans import FinansHatasi, kredi_karti_olustur
        with self.assertRaises(FinansHatasi):
            kredi_karti_olustur(ad="X", kesim_gunu=40, muhasebe_kodu="309.01")
        k = kredi_karti_olustur(ad="WORLD", limit="10.000,50", kesim_gunu=15,
                                muhasebe_kodu="309.01")
        self.assertEqual(k.limit, Decimal("10000.50"))
        self.assertEqual(k.kesim_gunu, 15)

    def test_kredi_olustur(self):
        from decimal import Decimal
        from core.services.finans import kredi_olustur
        k = kredi_olustur(ad="TAŞIT", anapara="250.000", faiz_orani="3,75",
                          muhasebe_kodu="300.01")
        self.assertEqual(k.anapara, Decimal("250000"))
        self.assertEqual(k.faiz_orani, Decimal("3.75"))

    def test_cek_senet_olustur_ve_tutar_kontrol(self):
        import datetime
        from core.models import CekSenet
        from core.services.finans import FinansHatasi, cek_senet_olustur
        cs = cek_senet_olustur(tip="CEK", yon="ALINAN", tutar="5.000",
                               vade=datetime.date(2026, 9, 1), kesideci="ahmet",
                               muhasebe_kodu="101.01")
        self.assertEqual(cs.tip, "CEK")
        self.assertEqual(cs.durum, CekSenet.Durum.PORTFOYDE)   # varsayılan
        self.assertEqual(cs.kesideci, "AHMET")
        with self.assertRaises(FinansHatasi):
            cek_senet_olustur(tip="CEK", yon="ALINAN", tutar="0",
                              vade=datetime.date(2026, 9, 1), muhasebe_kodu="101.01")


class FinansDigerViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fyon", password="x")
        _hesap("102.01", "İŞ BANKASI")
        _hesap("101.01", "ALINAN ÇEKLER")

    def test_listeler_200(self):
        self.client.force_login(self.yon)
        for ad in ("bankalar", "kredi_kartlari", "krediler", "cek_senetler"):
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 200)

    def test_banka_kurum_ve_hesap_post(self):
        from core.models import Banka, BankaHesap
        from core.services.finans import banka_olustur
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:banka_kurum_ekle"),
                             {"ad": "akbank", "kisa_ad": "", "sube": "",
                              "swift_kod": "", "adres": ""})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Banka.objects.filter(ad="AKBANK").exists())
        b = banka_olustur(ad="garanti")
        r2 = self.client.post(reverse("core:banka_hesap_ekle", args=[b.pk]),
                              {"ad": "tl", "para_birimi": "TRY", "muhasebe": "102.01"})
        self.assertEqual(r2.status_code, 302)
        self.assertTrue(BankaHesap.objects.filter(banka=b, ad="TL").exists())

    def test_cek_senet_ekle_post(self):
        from decimal import Decimal
        from core.models import CekSenet
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:cek_senet_ekle"), {
            "tip": "CEK", "yon": "ALINAN", "tutar": "1.500", "para_birimi": "TRY",
            "vade": "2026-09-01", "kesideci": "veli", "belge_no": "A123",
            "durum": "PORTFOYDE", "muhasebe": "101.01"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CekSenet.objects.filter(belge_no="A123", tutar=Decimal("1500")).exists())
