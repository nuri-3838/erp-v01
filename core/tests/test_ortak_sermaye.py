"""Ortak (Nuri Özer) hareketlerinin 131.01'den 500-10-0001 sermaye carisine taşınması + ekranlar/servisler: cari tahsilat/ödeme, kredi
kartı harcaması, kesinti/masraf (gider yönü + 'ortak ödedi'), duran varlık kartı karşı hesap → fiş."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import Cari, DuranVarlik, Fatura, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import banka_hareket as bh
from core.services import cari_kesinti as ck
from core.services import duran_varlik as dv_servis
from core.services import hesap_plani as hp
from core.services import kredi_karti_hareket as kk
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur, kredi_karti_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class OrtakBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 6, 1), usd_alis=Dc("30"))
        _hesap("102.01.0001", "TL BANKA")
        _hesap("309.02", "KART", kalem="KVYK")
        _hesap("120.01", "ALICI")
        _hesap("320.01", "SATICI", kalem="KVYK")
        _hesap("770.03", "DİĞER GİDER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("255", "DEMİRBAŞLAR", kalem="DDV")
        hp.hesap_olustur(kod="255.01", ad="OFİS", ust_kodu="255")
        _hesap("131", "ORTAKLARDAN ALACAKLAR")
        hp.hesap_olustur(kod="131.01", ad="NURİ ÖZER", ust_kodu="131")
        _hesap("500", "SERMAYE", kalem="OZK")
        hp.hesap_olustur(kod="500.10", ad="ÖDENMİŞ SERMAYE", ust_kodu="500")
        hp.hesap_olustur(kod="500.10.0001", ad="NURİ ÖZER", ust_kodu="500.10")
        cls.su = User.objects.create_superuser("ort", password="x")
        cls.nuri = Cari.objects.create(kod="500-10-0001", unvan="NURİ ÖZER", muhasebe_kodu="500.10.0001", para_birimi="TRY")
        cls.alici = Cari.objects.create(kod="C1", unvan="ALICI", muhasebe_kodu="120.01", para_birimi="TRY")
        cls.satici = Cari.objects.create(kod="S1", unvan="SATICI", muhasebe_kodu="320.01", para_birimi="TRY")
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.kart = kredi_karti_olustur(ad="kart", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)


class CariIslemleriTest(OrtakBase):
    def test_banka_cari_tahsilat_odeme_500_cariyle(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_tahsilat", karsi=self.nuri, tutar="5000", tarih=D(2026, 6, 1),
                               kullanici=self.su)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in f.satirlar.all()},
                         {("102.01.0001", "B"), ("500.10.0001", "A")})
        bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.nuri, tutar="1200", tarih=D(2026, 6, 1),
                           kullanici=self.su)
        self.assertEqual(_bak("500.10.0001"), Dc("-3800.00"))                  # alacak bakiye
        self.assertEqual(raporlar.ekstre_devirli("500.10.0001").bakiye, Dc("-3800.00"))

    def test_kredi_karti_harcamasi_cari_500(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.nuri, tutar="800", tarih=D(2026, 6, 1), kullanici=self.su)
        self.assertEqual(f.satirlar.get(hesap_id="500.10.0001").borc, Dc("800.00"))     # karşı borç / kart alacak


class KesintiOrtakTest(OrtakBase):
    def test_ortak_carisinin_kendi_kartinda_gider_yonu_ortak_alacak(self):
        self.assertEqual(ck.yon_coz(self.nuri), "musteri")
        fis = ck.kesinti_olustur(cari=self.nuri, tarih=D(2026, 6, 1), tutar="300", gider_kodu="770.03", kullanici=self.su)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in fis.satirlar.all()},
                         {("770.03", "B"), ("500.10.0001", "A")})
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.nuri, tarih=D(2026, 6, 1), tutar="1", karsi_cari=self.nuri, kullanici=self.su)

    def test_ortak_odedi_cari_borc_ortak_alacak_duzenle_sil(self):
        fis = ck.kesinti_olustur(cari=self.satici, tarih=D(2026, 6, 1), tutar="2500", karsi_cari=self.nuri, kullanici=self.su)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in fis.satirlar.all()},
                         {("320.01", "B"), ("500.10.0001", "A")})
        self.assertEqual((_bak("320.01"), _bak("500.10.0001")), (Dc("2500.00"), Dc("-2500.00")))
        bilgi = ck.duzenleme_bilgisi(fis, self.satici)
        self.assertEqual((bilgi["karsi_cari"], bilgi["tutar"], bilgi["gider"]), (self.nuri, Dc("2500.00"), None))
        ck.kesinti_guncelle(fis=fis, cari=self.satici, tarih=D(2026, 6, 1), tutar="3000", karsi_cari=self.nuri, kullanici=self.su)
        self.assertEqual(_bak("500.10.0001"), Dc("-3000.00"))
        # normal gider hareketine geri çevrilebilir
        ck.kesinti_guncelle(fis=fis, cari=self.satici, tarih=D(2026, 6, 1), tutar="3000", gider_kodu="770.03", kullanici=self.su)
        self.assertEqual(_bak("500.10.0001"), Dc("0"))
        ck.kesinti_sil(fis=fis, cari=self.satici, kullanici=self.su)
        self.assertEqual(_bak("320.01"), Dc("0"))

    def test_karsi_cari_ortak_olmayan_ya_da_doviz_reddedilir(self):
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.satici, tarih=D(2026, 6, 1), tutar="1", karsi_cari=self.alici, kullanici=self.su)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)


class DuranVarlikKarsiHesapTest(OrtakBase):
    def _kart(self, karsi="500.10.0001", maliyet="10000"):
        return dv_servis.duran_varlik_olustur(ad="mobilya", grup_kodu="255.01", aktiflestirme_tarihi=D(2026, 6, 1),
                                              maliyet=Dc(maliyet), karsi_hesap_kodu=karsi, kullanici=self.su)

    def test_karsi_hesapli_kart_fis_olusur_guncellenir_silinir(self):
        v = self._kart()
        self.assertEqual(v.hesap_id, "255.01.0001")
        self.assertEqual(v.fis.kaynak, "DURAN_VARLIK")
        self.assertEqual(v.fis.tarih, D(2026, 6, 1))
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A", s.borc or s.alacak) for s in v.fis.satirlar.all()},
                         {("255.01.0001", "B", Dc("10000.00")), ("500.10.0001", "A", Dc("10000.00"))})
        self.assertEqual((_bak("255.01.0001"), _bak("500.10.0001")), (Dc("10000.00"), Dc("-10000.00")))
        # maliyet değişince fiş güncellenir
        dv_servis.duran_varlik_guncelle(v, ad="mobilya", maliyet=Dc("12000"), kullanici=self.su,
                                        karsi_hesap_guncelle=True, karsi_hesap_kodu="500.10.0001")
        self.assertEqual((_bak("255.01.0001"), _bak("500.10.0001")), (Dc("12000.00"), Dc("-12000.00")))
        # karşı hesap değişir: kasa/banka
        dv_servis.duran_varlik_guncelle(v, ad="mobilya", maliyet=Dc("12000"), kullanici=self.su,
                                        karsi_hesap_guncelle=True, karsi_hesap_kodu="102.01.0001")
        self.assertEqual((_bak("102.01.0001"), _bak("500.10.0001")), (Dc("-12000.00"), Dc("0")))
        # karşı hesap kalkınca fiş silinir
        dv_servis.duran_varlik_guncelle(v, ad="mobilya", maliyet=Dc("12000"), kullanici=self.su,
                                        karsi_hesap_guncelle=True, karsi_hesap_kodu=None)
        v.refresh_from_db()
        self.assertIsNone(v.fis_id)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="DURAN_VARLIK").count(), 0)
        self.assertEqual(_bak("255.01.0001"), Dc("0"))

    def test_kart_silinince_fis_de_silinir(self):
        v = self._kart()
        dv_servis.varlik_sil(v, kullanici=self.su)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="DURAN_VARLIK").count(), 0)
        self.assertEqual((_bak("255.01.0001"), _bak("500.10.0001")), (Dc("0"), Dc("0")))
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="255.01.0001", silindi=False).exists())     # hesap KALIR

    def test_karsi_hesap_kisitlari(self):
        for kod in ("320.01", "770.03", "131.01"):
            with self.assertRaises(dv_servis.DuranVarlikHatasi):
                self._kart(karsi=kod)
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            self._kart(maliyet="0")
        self.assertEqual(YevmiyeFisi.objects.count(), 0)
        v = dv_servis.duran_varlik_olustur(ad="boş", grup_kodu="255.01", aktiflestirme_tarihi=D(2026, 6, 1),
                                           maliyet=Dc("5"), kullanici=self.su)                    # karşı hesap opsiyonel
        self.assertIsNone(v.fis_id)


class OrtakHesaplariTest(OrtakBase):
    def test_ortak_hesaplari_500_10_icerir_131_01_pasifse_icermez(self):
        kodlar = set(hp.ortak_hesaplari().values_list("hesap_kodu", flat=True))
        self.assertIn("500.10.0001", kodlar)
        self.assertIn("131.01", kodlar)
        h = HesapPlani.objects.get(hesap_kodu="131.01")
        h.aktif = False
        h.save()
        self.assertNotIn("131.01", set(hp.ortak_hesaplari().values_list("hesap_kodu", flat=True)))


class TasimaKomutuTest(OrtakBase):
    def _veri(self):
        bh.hareket_olustur(banka_hesap=self.banka, tip="hesaba_odeme", karsi=None, tutar="40000", tarih=D(2026, 6, 1),
                           satirlar=[{"hesap_kodu": "131.01", "tutar": "40000"}], kullanici=self.su) \
            if False else None
        # banka girişi: ortak sermaye koydu (131.01 ALACAK) + manuel fiş + USD satırı
        fis_olustur(tarih=D(2026, 6, 1), aciklama="ortak banka", kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
            SatirGirdi(hesap_kodu="102.01.0001", taraf="B", islem_tutari="40000"),
            SatirGirdi(hesap_kodu="131.01", taraf="A", islem_tutari="40000")])
        fis_olustur(tarih=D(2026, 6, 1), aciklama="manuel", kaynak=YevmiyeFisi.Kaynak.MANUEL, satirlar=[
            SatirGirdi(hesap_kodu="131.01", taraf="B", islem_tutari="1000"),
            SatirGirdi(hesap_kodu="770.03", taraf="A", islem_tutari="1000")])
        fis_olustur(tarih=D(2026, 6, 1), aciklama="usd", kaynak=YevmiyeFisi.Kaynak.MANUEL, satirlar=[
            SatirGirdi(hesap_kodu="131.01", taraf="A", islem_tutari="100", islem_pb="USD", islem_kuru="30"),
            SatirGirdi(hesap_kodu="770.03", taraf="B", islem_tutari="3000")])

    def _komut(self, *ek):
        out = StringIO()
        from core.management.commands import ortak_131_sermaye_tasi as m
        with mock.patch.object(m, "BEKLENEN_NET", Dc("-42000.00")), mock.patch.object(m, "BEKLENEN_SATIR", 3):
            call_command("ortak_131_sermaye_tasi", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_tasir_toplamlar_korunur(self):
        self._veri()
        banka_once = _bak("102.01.0001")
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="131.01").count(), 3)
        self.assertTrue(HesapPlani.objects.get(hesap_kodu="131.01").aktif)
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="131.01").count(), 0)
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="500.10.0001").count(), 3)
        self.assertEqual((_bak("131.01"), _bak("500.10.0001")), (Dc("0"), Dc("-42000.00")))
        self.assertEqual(_bak("102.01.0001"), banka_once)                              # banka bakiyesi değişmedi
        self.assertEqual(raporlar.ekstre_devirli("500.10.0001").bakiye, Dc("-42000.00"))
        usd = YevmiyeSatir.objects.get(hesap_id="500.10.0001", islem_pb="USD")
        self.assertEqual((usd.islem_tutari, usd.alacak), (Dc("100.00"), Dc("3000.00")))   # döviz bilgisi/yön aynı
        h = HesapPlani.objects.get(hesap_kodu="131.01")
        self.assertFalse(h.aktif)                                                       # pasif ama silinmedi
        self.assertFalse(h.silindi)

    def test_kontrol_tutmazsa_uygula_iptal(self):
        self._veri()
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("ortak_131_sermaye_tasi", "--uygula", stdout=out)      # gerçek beklenen tutarlar tutmaz
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="131.01").count(), 3)
        self.assertTrue(HesapPlani.objects.get(hesap_kodu="131.01").aktif)

    def test_sahsi_alis_faturasinin_ortak_hesabi_guncellenir(self):
        from core.models import FaturaTipi
        t = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        f = Fatura.objects.create(tip=t, yon="ALIS", durum="TASLAK", cari=self.satici, tarih=D(2026, 6, 1), para_birimi="TRY",
                                  kur=1, sahsi_alis=True, sahsi_ortak_id="131.01")
        self._veri()
        self._komut("--uygula")
        f.refresh_from_db()
        self.assertEqual(f.sahsi_ortak_id, "500.10.0001")
