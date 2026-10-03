"""Tek seferlik fatura_tarih_tasi komutu: dry-run kalıcı yazmaz; --uygula faturayı/vadeyi yeni
tarihe taşır, açıklamaya eski tarihi ekler (300 sınırı), fişi yeni mali yılda yeniden
oluşturup eskisini siler; hesap/proje/şahsi ayarları ve proje toplamları korunur."""
import datetime
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Cari, Fatura, FaturaTipi, HesapPlani, KdvOrani, Kur, YevmiyeFisi
from core.services.fatura import fatura_olustur
from core.services.yatirim_projesi import proje_olustur, proje_toplami

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class TarihTasiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for t in (D(2025, 12, 9), D(2026, 1, 1)):
            Kur.objects.create(tarih=t, usd_alis=Decimal("40"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "A", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        _hesap("131.01", "ORTAK")
        _hesap("602.01", "FAZLA KDV", grup="GELIR_TABLOSU", kalem="A")
        cls.kdv = KdvOrani.objects.create(
            aciklama="G", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.tip = FaturaTipi.objects.create(ad="GİDER", yon="ALIS", gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.u = User.objects.create_superuser("yon", password="x")
        cls.proje = proje_olustur(ad="hat", kullanici=cls.u)

    def setUp(self):
        self.f1 = fatura_olustur(
            tip_id=self.tip.pk, cari_id=self.cari.pk, tarih=D(2025, 12, 9), fatura_no="A-1",
            aciklama="x" * 300, vade_tarihi=D(2025, 12, 30), kullanici=self.u,
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv.pk, "yatirim_projesi_id": self.proje.pk}])
        self.f2 = fatura_olustur(
            tip_id=self.tip.pk, cari_id=self.cari.pk, tarih=D(2025, 12, 9), fatura_no="A-2",
            aciklama="kisa", sahsi_alis=True, sahsi_ortak_id="131.01", kullanici=self.u,
            satirlar=[{"hesap_id": None, "miktar": "1", "birim_fiyat": "500",
                      "kdv_id": self.kdv.pk}])

    def _komut(self, *ek):
        out = StringIO()
        call_command("fatura_tarih_tasi", "--idler", f"{self.f1.pk},{self.f2.pk}", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_kalici_yazmaz_ama_raporlar(self):
        cikti = self._komut()
        self.assertIn("DRY-RUN", cikti)
        self.assertIn("EVET", cikti)                       # f1 açıklaması kısaltılacak
        self.assertEqual(YevmiyeFisi.objects.filter(yil=2025).count(), 2)
        self.assertEqual(YevmiyeFisi.objects.filter(yil=2026).count(), 0)
        self.f1.refresh_from_db()
        self.assertEqual(self.f1.tarih, D(2025, 12, 9))

    def test_uygula(self):
        once = proje_toplami(self.proje)
        self._komut("--uygula")
        self.assertEqual(YevmiyeFisi.objects.filter(yil=2025).count(), 0)
        self.assertEqual(YevmiyeFisi.objects.filter(yil=2026).count(), 2)
        self.f1.refresh_from_db()
        self.f2.refresh_from_db()
        for f in (self.f1, self.f2):
            self.assertEqual((f.tarih, f.vade_tarihi), (D(2026, 1, 1), D(2026, 1, 1)))
            self.assertEqual(f.fis.yil, 2026)
        self.assertTrue(self.f1.aciklama.startswith("GERÇEK TARİH: 09.12.2025. "))
        self.assertEqual(len(self.f1.aciklama), 300)
        self.assertEqual(self.f2.aciklama, "GERÇEK TARİH: 09.12.2025. kisa")
        self.assertEqual(proje_toplami(self.proje), once)
        self.assertEqual(self.f1.satirlar.get().hesap_id, "258")
        self.assertEqual(self.f1.satirlar.get().yatirim_projesi_id, self.proje.pk)
        self.assertTrue(self.f2.sahsi_alis)
        kodlar = {s.hesap_id for s in self.f2.fis.satirlar.filter(silindi=False)}
        self.assertTrue({"131.01", "602.01", "191", "320.10.0001"} <= kodlar)

    def test_kur_yoksa_hata(self):
        with self.assertRaises(CommandError):
            self._komut("--tarih", "2026-02-02")
