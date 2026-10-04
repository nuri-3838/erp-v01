"""Hareketli yaprak hesaba alt hesap: uyarı + taşıma (hesap planı ekranı), kural bazlı taşıma ve hesap_126_detaylandir komutu."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import hesap_plani as hp
from core.services import hesap_tasima as ht
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _odeme(banka, hesap, tutar, ack, taraf="B"):
    """126 gibi bir hesaba banka çıkışı/girişi (banka hareketi gibi fiş)."""
    ters = "A" if taraf == "B" else "B"
    f = fis_olustur(tarih=D(2026, 3, 10), aciklama=ack, kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
        SatirGirdi(hesap_kodu=hesap, taraf=taraf, islem_tutari=tutar),
        SatirGirdi(hesap_kodu=banka.muhasebe_id, taraf=ters, islem_tutari=tutar)])
    f.banka_hesap = banka
    f.save(update_fields=["banka_hesap"])
    return f


class TasimaTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Dc("40"))
        _hesap("102.01", "BANKA")
        cls.h126 = _hesap("126", "VERİLEN DEPOZİTO VE TEMİNATLAR")
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01")
        cls.su = User.objects.create_superuser("ht", password="x")

    def _veri(self):
        _odeme(self.banka, "126", "1000", "UYAP İHALE TEMİNATI 1")
        _odeme(self.banka, "126", "1000", "UYAP İHALE TEMİNATI İADESİ", taraf="A")
        _odeme(self.banka, "126", "14500", "MİMARSİNAN OSB SU ABONELİK TEMİNATI")
        _odeme(self.banka, "126", "500", "UYAP İHALE TEMİNATI 2")


class HesapPlaniHareketliUstTest(TasimaTestBase):
    def test_hareketsiz_yaprakta_alt_hesap_normal(self):
        h = hp.hesap_olustur(kod="126.01", ad="UYAP", ust_kodu="126")
        self.assertEqual(h.hesap_kodu, "126.01")

    def test_hareketli_yaprakta_reddedilir_tasima_ile_acilir(self):
        self._veri()
        with self.assertRaises(hp.HareketliUstHesapHatasi) as e:
            hp.hesap_olustur(kod="126.01", ad="UYAP", ust_kodu="126")
        self.assertEqual(e.exception.satir_sayisi, 4)
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="126.01").exists())
        h = hp.hesap_olustur(kod="126.01", ad="UYAP", ust_kodu="126", mevcut_hareketleri_tasi=True)
        self.assertEqual(h.tasinan_satir_sayisi, 4)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 0)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126.01").count(), 4)
        # ikinci alt hesap artık normal (126 zaten üst)
        self.assertEqual(hp.hesap_olustur(kod="126.02", ad="DİĞER", ust_kodu="126").hesap_kodu, "126.02")

    def test_ekran_uyarir_ve_tasima_sunar(self):
        self._veri()
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="hesap_plani")
        self.client.force_login(self.su)
        veri = {"kod": "126.01", "ad": "uyap", "ust_kodu": "126"}
        r = self.client.post(reverse("core:hesap_ekle"), veri)
        self.assertContains(r, "126 hesabında hareket var")
        self.assertContains(r, "4</strong> yevmiye satırı")
        self.assertContains(r, "taşı")
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="126.01").exists())          # onaysız açılmadı
        r = self.client.post(reverse("core:hesap_ekle"), {**veri, "tasi": "1"})
        self.assertRedirects(r, reverse("core:hesap_plani"))
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126.01").count(), 4)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 0)


class KuralTasimaTest(TasimaTestBase):
    def test_plan_eslesen_eslesmeyen_ve_belirsiz(self):
        self._veri()
        _odeme(self.banka, "126", "7", "BAŞKA TEMİNAT")
        _odeme(self.banka, "126", "9", "UYAP VE MİMARSİNAN OSB KARIŞIK")
        hp.hesap_olustur(kod="126.01", ad="UYAP", ust_kodu="126", hareketli_ust_izin=True)
        hp.hesap_olustur(kod="126.02", ad="OSB", ust_kodu="126", hareketli_ust_izin=True)
        p = ht.plan("126", [("126.01", ["UYAP"]), ("126.02", ["MİMARSİNAN OSB"])])
        self.assertEqual({h: len(v) for h, v in p.eslesen.items()}, {"126.01": 3, "126.02": 1})
        self.assertEqual(len(p.eslesmeyen), 2)                      # "BAŞKA" ve çift kurala uyan
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 6)     # plan yazmaz
        with self.assertRaises(ht.TasimaHatasi):
            ht.plan("126", [("126.99", ["X"])])                     # hedef yok

    def test_uygula_hesabi_degistirir_tutar_ve_banka_ayni(self):
        self._veri()
        hp.hesap_olustur(kod="126.01", ad="UYAP", ust_kodu="126", hareketli_ust_izin=True)
        hp.hesap_olustur(kod="126.02", ad="OSB", ust_kodu="126", hareketli_ust_izin=True)
        from core.services.raporlar import _devir
        once = _devir("126", D(2100, 1, 1))[0]
        banka_once = _devir("102.01", D(2100, 1, 1))[0]
        p = ht.plan("126", [("126.01", ["UYAP"]), ("126.02", ["MİMARSİNAN OSB"])])
        self.assertEqual(ht.uygula(p), 4)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 0)
        self.assertEqual(_devir("126.01", D(2100, 1, 1))[0], Dc("500.00"))     # 1000 − 1000 + 500
        self.assertEqual(_devir("126.02", D(2100, 1, 1))[0], Dc("14500.00"))
        self.assertEqual(_devir("126", D(2100, 1, 1))[0], once)                 # aile toplamı değişmedi
        self.assertEqual(_devir("102.01", D(2100, 1, 1))[0], banka_once)


class Hesap126KomutTest(TasimaTestBase):
    def _komut(self, *ek):
        out = StringIO()
        call_command("hesap_126_detaylandir", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_boler(self):
        self._veri()
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="126.01").exists())
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 4)
        self.assertIn("HEPSİ DEĞİŞMEDİ", c)
        self.assertIn("DENGEDE", c)
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 0)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126.02").count(), 1)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126.01").count(), 3)
        self.assertIn("126'nın kendisinde kalan satır: 0", c)
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="126.01").rapor_kalemi, "DV")      # üstten miras

    def test_eslesmeyen_satir_varsa_uygula_iptal_hicbir_sey_yazilmaz(self):
        self._veri()
        _odeme(self.banka, "126", "7", "BAŞKA TEMİNAT")
        c = self._komut()
        self.assertIn("Eşleşmeyen/belirsiz satır: 1", c)
        with self.assertRaises(CommandError):
            self._komut("--uygula")
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu__startswith="126.").exists())
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="126").count(), 5)
