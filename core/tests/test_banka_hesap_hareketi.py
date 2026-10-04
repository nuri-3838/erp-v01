"""Banka hesabı > Hesaba Ödeme (çıkış) / Hesaptan Giriş (giriş): karşı taraf hesap planından bir ya da
birden çok muavin hesap; fiş yönleri, çok satır (taksit: anapara + faiz), döviz hesap (işlem dövizi +
kur, kuruş farkı), 258'de yatırım projesi zorunluluğu, toplam uyuşmazlığı, ekran ve iptal akışı."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import banka_hareket as bh
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yatirim_projesi import proje_olustur

D = datetime.date


def _hesap(kod, ad, kalem="DV", grup="BILANCO"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class BankaHesapHareketiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 6, 1), usd_alis=Decimal("30"))
        _hesap("102.01.0001", "TL HESAP")
        _hesap("102.02.0001", "USD HESAP")
        _hesap("300.01", "KREDİ ANAPARA", kalem="KVYK")
        _hesap("780.01", "FİNANSMAN GİDERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("131.01", "ORTAKTAN ALACAK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        _hesap("360", "ÖDENECEK VERGİ")           # üst hesap (alt hesabı var)
        _hesap("360.10", "STOPAJ", kalem="KVYK")
        cls.u = User.objects.create_superuser("yon", password="x")
        b = banka_olustur(ad="test banka")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        cls.proje = proje_olustur(ad="hat", kullanici=cls.u)

    def _satirlar(self, fis):
        return [(s.hesap_id, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False).order_by("id")]

    def _odeme(self, satirlar, tutar="1000", hesap=None, tip="hesaba_odeme", tarih=D(2026, 6, 1), **kw):
        return bh.hareket_olustur(banka_hesap=hesap or self.tl, tip=tip, karsi=None, tutar=tutar,
                                  tarih=tarih, satirlar=satirlar, kullanici=self.u, **kw)

    def test_hesaba_odeme_tek_satir_karsi_borc_banka_alacak(self):
        fis = self._odeme([{"hesap_kodu": "131.01", "tutar": "1000"}])
        self.assertEqual(self._satirlar(fis), [("102.01.0001", Decimal("0.00"), Decimal("1000.00")),
                                               ("131.01", Decimal("1000.00"), Decimal("0.00"))])
        self.assertEqual((fis.kaynak, fis.banka_hesap_id), (YevmiyeFisi.Kaynak.BANKA, self.tl.pk))

    def test_tek_satirda_tutar_bossa_tamami_yazilir(self):
        fis = self._odeme([{"hesap_kodu": "131.01", "tutar": None}], tutar="250")
        self.assertEqual(self._satirlar(fis)[1], ("131.01", Decimal("250.00"), Decimal("0.00")))

    def test_hesaptan_giris_banka_borc_karsi_alacak(self):
        fis = self._odeme([{"hesap_kodu": "300.01", "tutar": "5000"}], tutar="5000", tip="hesaptan_giris")
        self.assertEqual(self._satirlar(fis), [("102.01.0001", Decimal("5000.00"), Decimal("0.00")),
                                               ("300.01", Decimal("0.00"), Decimal("5000.00"))])

    def test_cok_satir_kredi_taksidi_anapara_ve_faiz(self):
        fis = self._odeme([{"hesap_kodu": "300.01", "tutar": "900", "aciklama": "anapara"},
                           {"hesap_kodu": "780.01", "tutar": "100", "aciklama": "faiz"}])
        s = self._satirlar(fis)
        self.assertEqual(s[0], ("102.01.0001", Decimal("0.00"), Decimal("1000.00")))
        self.assertEqual(sorted(s[1:]), [("300.01", Decimal("900.00"), Decimal("0.00")),
                                         ("780.01", Decimal("100.00"), Decimal("0.00"))])
        self.assertEqual(sum(x[1] for x in s), sum(x[2] for x in s))             # dengeli

    def test_toplam_uyusmazliginda_hata_ve_fis_yok(self):
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(bh.BankaHareketHatasi) as e:
            self._odeme([{"hesap_kodu": "300.01", "tutar": "900"}, {"hesap_kodu": "780.01", "tutar": "50"}])
        self.assertIn("eşit olmalı", str(e.exception))
        self.assertEqual(YevmiyeFisi.objects.count(), n)

    def test_dovizli_hesap_islem_dovizi_kur_ve_kurus_farki(self):
        fis = self._odeme([{"hesap_kodu": "300.01", "tutar": "333.33"},
                           {"hesap_kodu": "780.01", "tutar": "333.33"},
                           {"hesap_kodu": "131.01", "tutar": "333.34"}], hesap=self.usd)
        satirlar = list(fis.satirlar.filter(silindi=False).order_by("id"))
        self.assertTrue(all(s.islem_pb == "USD" and s.islem_kuru == Decimal("30.000000") for s in satirlar))
        banka_tl = satirlar[0].alacak
        self.assertEqual(banka_tl, Decimal("30000.00"))                           # 1000 USD x 30
        self.assertEqual(sum(s.borc for s in satirlar), banka_tl)                 # kuruş farkı son satırda

    def test_kur_override_ve_kur_yoksa_hata(self):
        fis = self._odeme([{"hesap_kodu": "131.01", "tutar": "100"}], tutar="100", hesap=self.usd,
                          kur_override=Decimal("31"))
        self.assertEqual(fis.satirlar.filter(silindi=False).first().alacak, Decimal("3100.00"))
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([{"hesap_kodu": "131.01", "tutar": "100"}], tutar="100", hesap=self.usd,
                        tarih=D(2026, 6, 2))                                       # o gün kur yok

    def test_258_proje_zorunlu_ve_baska_hesapta_proje_yasak(self):
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([{"hesap_kodu": "258", "tutar": "1000"}])
        fis = self._odeme([{"hesap_kodu": "258", "tutar": "1000", "yatirim_projesi_id": self.proje.pk}])
        satir = fis.satirlar.get(hesap_id="258")
        self.assertEqual(satir.yatirim_projesi_id, self.proje.pk)
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([{"hesap_kodu": "131.01", "tutar": "1000", "yatirim_projesi_id": self.proje.pk}])

    def test_ust_hesap_ve_banka_hesabinin_kendisi_reddedilir(self):
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([{"hesap_kodu": "360", "tutar": "1000"}])                   # alt hesabı var
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([{"hesap_kodu": "102.01.0001", "tutar": "1000"}])
        with self.assertRaises(bh.BankaHareketHatasi):
            self._odeme([])

    def test_iptal_akisi_diger_turlerle_ayni(self):
        fis = self._odeme([{"hesap_kodu": "131.01", "tutar": "1000"}])
        bh.hareket_iptal(fis=fis, banka_hesap=self.tl, kullanici=self.u)
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)

    def test_ekranlar_ve_ekstre(self):
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="banka")
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:banka_hesap_detay", args=[self.tl.pk]))
        self.assertContains(r, "Hesaba Ödeme")
        self.assertContains(r, "Hesaptan Giriş")
        r = self.client.get(reverse("core:banka_hareket_ekle", args=[self.tl.pk, "hesaba_odeme"]))
        self.assertContains(r, "Karşı Hesap Satırları")
        veri = {"tutar": "1.000,00", "tarih": "2026-06-01", "aciklama": "",
                "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-hesap": "300.01", "form-0-tutar": "900,00",
                "form-1-hesap": "780.01", "form-1-tutar": "100,00"}
        r = self.client.post(reverse("core:banka_hareket_ekle", args=[self.tl.pk, "hesaba_odeme"]), veri)
        self.assertEqual(r.status_code, 302)
        fis = YevmiyeFisi.objects.filter(kaynak="BANKA").latest("pk")
        self.assertEqual(len(self._satirlar(fis)), 3)
        # ekstrede görünür (banka hesabının muhasebe hesabından)
        r = self.client.get(reverse("core:banka_hesap_detay", args=[self.tl.pk]))
        self.assertContains(r, f"{fis.yil}/{fis.fis_no}")
        # toplam uyuşmazlığı ekranda hata verir, fiş oluşmaz
        veri["form-1-tutar"] = "50,00"
        n = YevmiyeFisi.objects.count()
        r = self.client.post(reverse("core:banka_hareket_ekle", args=[self.tl.pk, "hesaba_odeme"]), veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")
        self.assertEqual(YevmiyeFisi.objects.count(), n)
