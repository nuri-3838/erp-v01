"""Fatura -> otomatik yevmiye (motor) testleri: alış/satış dengeli fiş, muhasebe
haritası çözümü, eksik harita/cari hesabı hataları, atomiklik, iptal."""
import datetime
from decimal import Decimal

from django.test import TestCase

from core.models import (Birim, Cari, Fatura, FaturaSatir, FaturaTipi, HesapPlani,
                         Kategori, KategoriHesap, KdvOrani, Kur, Stok, TevkifatOrani,
                         YevmiyeFisi)
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_iptal,
                                  fatura_olustur)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu=grup, rapor_kalemi=kalem, parasal=True)


class FaturaTestTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"),
                           eur_alis=Decimal("35.123456"))
        _hesap("153.10", "ALÜMİNYUM MAL")
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("600", "YURTİÇİ SATIŞLAR", grup="GELIR_TABLOSU", kalem="A")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("120.10.0001", "MÜŞTERİ A")
        cls.kdv = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        ust = Kategori.objects.create(ad="HAMMADDE", kod="153")
        alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
        cls.alt = alt
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(
            kod="153-10-0001", ad="ALÜMİNYUM LEVHA", kategori=alt,
            uretim_birimi=adet, fatura_birimi=adet, kdv=cls.kdv)
        cls.alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        cls.satis = FaturaTipi.objects.create(ad="SATIŞ FATURASI", yon=FaturaTipi.Yon.SATIS)
        cls.alis_gider = FaturaTipi.objects.create(ad="ALIŞ-GIDER", yon=FaturaTipi.Yon.ALIS)
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.alis,
                                     hesap=HesapPlani.objects.get(hesap_kodu="153.10"))
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.satis,
                                     hesap=HesapPlani.objects.get(hesap_kodu="600"))
        cls.tedarikci = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A",
                                            para_birimi="TRY", muhasebe_kodu="320.10.0001")
        cls.musteri = Cari.objects.create(kod="120-10-0001", unvan="MÜŞTERİ A",
                                          para_birimi="TRY", muhasebe_kodu="120.10.0001")

    def _satir(self, miktar="10", fiyat="100"):
        return [{"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": fiyat}]


class AlisFaturaTest(FaturaTestTemel):
    def test_alis_otomatik_fis(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), fatura_no="A-1", satirlar=self._satir())
        self.assertIsNotNone(f.fis_id)
        self.assertEqual(f.fis.kaynak, YevmiyeFisi.Kaynak.FATURA)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["153.10"], (Decimal("1000.00"), Decimal("0.00")))   # mal borç
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))       # KDV borç
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1200.00")))  # cari alacak
        # dengeli
        tb = sum(s.borc for s in f.fis.satirlar.all())
        ta = sum(s.alacak for s in f.fis.satirlar.all())
        self.assertEqual(tb, ta)
        self.assertEqual(f.satirlar.count(), 1)

    def test_mal_hesabi_yoksa_hata_ve_atomik(self):
        # alis_gider için KategoriHesap yok -> hata, hiçbir şey kaydedilmez
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.alis_gider.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        self.assertEqual(Fatura.objects.count(), 0)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)
        self.assertEqual(FaturaSatir.objects.count(), 0)

    def test_cari_muhasebe_yoksa_hata(self):
        c = Cari.objects.create(kod="320-10-0009", unvan="HESAPSIZ", para_birimi="TRY")
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.alis.pk, cari_id=c.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        self.assertEqual(YevmiyeFisi.objects.count(), 0)


class SatisFaturaTest(FaturaTestTemel):
    def test_satis_otomatik_fis(self):
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                           tarih=D(2026, 3, 10), fatura_no="S-1", satirlar=self._satir())
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["600"], (Decimal("0.00"), Decimal("1000.00")))      # gelir alacak
        self.assertEqual(sat["391"], (Decimal("0.00"), Decimal("200.00")))       # KDV alacak
        self.assertEqual(sat["120.10.0001"], (Decimal("1200.00"), Decimal("0.00")))  # cari borç
        tb = sum(s.borc for s in f.fis.satirlar.all())
        ta = sum(s.alacak for s in f.fis.satirlar.all())
        self.assertEqual(tb, ta)


class DovizFaturaTest(FaturaTestTemel):
    def test_eur_kurus_yuvarlama_dengeli(self):
        # 33,33 EUR mal + %20 KDV, kur 35.123456 -> satır TL'leri yuvarlanınca
        # genel×kur ile 1 kuruş fark eder; denge satırı (tl_override) bunu giderir.
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), para_birimi="EUR",
                           satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                      "birim_fiyat": "33,33"}])
        self.assertEqual(f.para_birimi, "EUR")
        self.assertEqual(f.kur, Decimal("35.123456"))
        sat = {s.hesap_id: (s.borc, s.alacak, s.islem_pb, s.islem_tutari)
               for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["153.10"][:2], (Decimal("1170.66"), Decimal("0.00")))
        self.assertEqual(sat["191"][:2], (Decimal("234.27"), Decimal("0.00")))
        # cari TL = mal+KDV TL = 1404.93 (genel×kur=1404.94 DEĞİL) -> denge
        self.assertEqual(sat["320.10.0001"][:2], (Decimal("0.00"), Decimal("1404.93")))
        self.assertEqual(sat["320.10.0001"][2], "EUR")
        self.assertEqual(sat["320.10.0001"][3], Decimal("40.00"))   # döviz tutarı korunur
        tb = sum(s.borc for s in f.fis.satirlar.all())
        ta = sum(s.alacak for s in f.fis.satirlar.all())
        self.assertEqual(tb, ta)                                    # DENGELİ

    def test_kur_yoksa_hata(self):
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), para_birimi="GBP",   # gbp_alis yok
                           satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                      "birim_fiyat": "100"}])


class TevkifatTest(FaturaTestTemel):
    def _tev(self, hesapli=True):
        h = _hesap("360.10", "ÖDENECEK KDV TEVKİFATI", kalem="KVYK") if hesapli else None
        tev = TevkifatOrani.objects.create(kod="5/10", pay=5, payda=10, hesap=h)
        Stok.objects.filter(pk=self.stok.pk).update(tevkifat=tev)
        return tev

    def test_alis_tevkifat(self):
        # 1000 mal, 200 KDV (%20), 5/10 tevkifat -> 100 tevkifat
        self._tev()
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["153.10"], (Decimal("1000.00"), Decimal("0.00")))   # mal
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))       # TAM KDV indirilir
        self.assertEqual(sat["360.10"], (Decimal("0.00"), Decimal("100.00")))    # tevkifat -> 360
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1100.00")))  # cari = 1000+100
        tb = sum(s.borc for s in f.fis.satirlar.filter(silindi=False))
        ta = sum(s.alacak for s in f.fis.satirlar.filter(silindi=False))
        self.assertEqual(tb, ta)
        self.assertEqual(f.tevkifat_toplam, Decimal("100.00"))
        self.assertEqual(f.odenecek, Decimal("1100.00"))

    def test_satis_tevkifat(self):
        self._tev()
        f = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["600"], (Decimal("0.00"), Decimal("1000.00")))      # gelir
        self.assertEqual(sat["391"], (Decimal("0.00"), Decimal("100.00")))       # NET KDV (200-100)
        self.assertEqual(sat["120.10.0001"], (Decimal("1100.00"), Decimal("0.00")))  # cari borç
        self.assertNotIn("360.10", sat)                                          # satışta 360 yok
        tb = sum(s.borc for s in f.fis.satirlar.filter(silindi=False))
        ta = sum(s.alacak for s in f.fis.satirlar.filter(silindi=False))
        self.assertEqual(tb, ta)

    def test_alis_tevkifat_hesabi_yoksa_hata(self):
        self._tev(hesapli=False)
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_doviz_tevkifat_dengeli(self):
        self._tev()
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), para_birimi="EUR",
                           satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                      "birim_fiyat": "33,33"}])
        tb = sum(s.borc for s in f.fis.satirlar.filter(silindi=False))
        ta = sum(s.alacak for s in f.fis.satirlar.filter(silindi=False))
        self.assertEqual(tb, ta)                       # döviz + tevkifat dengeli
        self.assertEqual(f.para_birimi, "EUR")


class FaturaGuncelleTest(FaturaTestTemel):
    def test_guncelle_fisi_yeniler(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), fatura_no="A-1", satirlar=self._satir())
        fis_pk = f.fis_id
        fis_no = f.fis.fis_no
        eski_ids = list(f.satirlar.values_list("id", flat=True))
        fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                        tarih=D(2026, 3, 10), fatura_no="A-1",
                        satirlar=[{"stok_id": self.stok.pk, "miktar": "20",
                                   "birim_fiyat": "100"}])
        f.refresh_from_db()
        self.assertEqual(f.fis_id, fis_pk)               # aynı fiş (no korunur)
        self.assertEqual(f.fis.fis_no, fis_no)
        self.assertEqual(f.genel_toplam, Decimal("2400.00"))   # 20×100×1,20
        self.assertEqual(f.satirlar.filter(silindi=False).count(), 1)
        eski = FaturaSatir.objects.filter(id__in=eski_ids)
        self.assertTrue(all(s.silindi for s in eski))    # eski satırlar soft-delete
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["153.10"], (Decimal("2000.00"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("400.00"), Decimal("0.00")))
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("2400.00")))
        tb = sum(s.borc for s in f.fis.satirlar.filter(silindi=False))
        ta = sum(s.alacak for s in f.fis.satirlar.filter(silindi=False))
        self.assertEqual(tb, ta)


class FaturaIptalTest(FaturaTestTemel):
    def test_iptal_fisi_de_iptal_eder(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir())
        fis_id = f.fis_id
        fatura_iptal(f)
        f.refresh_from_db()
        self.assertTrue(f.silindi)
        fis = YevmiyeFisi.objects.get(pk=fis_id)
        self.assertTrue(fis.silindi)
        self.assertTrue(all(s.silindi for s in fis.satirlar.all()))
