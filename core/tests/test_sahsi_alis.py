"""Ortak adına (şahsi) alış testleri: şirkete kesilmiş ama ortağın şahsi harcaması olan
GİDER faturası — kalem KDV DAHİL tutarla ortak hesabına (131 ailesi) borçlanır, KDV'nin
aynı tutarı "FAZLA KDV" (602.01) hesabına alacak yazılır. Bkz. core.services.fatura
(_satir_coz/_hazirla/_muhasebe_satirlari), core.models.Fatura.sahsi_alis/sahsi_ortak."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, FaturaTipi, HesapPlani, KdvOrani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_olustur,
                                  fatura_onayla, fatura_sil, fatura_taslak_olustur)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class SahsiAlisTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 8, 29), usd_alis=Decimal("40"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("360", "ÖDENECEK VERGİ VE FONLAR")
        cls.ortak = _hesap("131.01", "NURİ ÖZER")
        cls.fazla_kdv = _hesap("602.01", "FAZLA KDV", grup="GELIR_TABLOSU", kalem="A")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.satis_gider_yasak = FaturaTipi.objects.create(
            ad="SATIŞ FATURASI", yon=FaturaTipi.Yon.SATIS)
        cls.tedarikci = Cari.objects.create(kod="320-10-0001", unvan="MEDIA MARKT",
                                            para_birimi="TRY", muhasebe_kodu="320.10.0001")
        cls.u = User.objects.create_superuser("yon", password="x")

    def _satir(self, tutar="3855.83"):
        return [{"hesap_id": None, "miktar": "1", "birim_fiyat": tutar,
                "kdv_id": self.kdv20.pk}]


class SahsiAlisFisTest(SahsiAlisTemel):
    def test_fis_kullanicinin_ornegiyle_birebir(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            fatura_no="TE12026000760054", satirlar=self._satir("3855.83"),
            sahsi_alis=True, sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        self.assertTrue(f.sahsi_alis)
        self.assertEqual(f.sahsi_ortak_id, self.ortak.pk)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["131.01"], (Decimal("4627.00"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("771.17"), Decimal("0.00")))
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("4627.00")))
        self.assertEqual(sat["602.01"], (Decimal("0.00"), Decimal("771.17")))
        tb = sum(s.borc for s in f.fis.satirlar.all())
        ta = sum(s.alacak for s in f.fis.satirlar.all())
        self.assertEqual(tb, ta)
        self.assertEqual(tb, Decimal("5398.17"))

    def test_131_ve_cari_bakiyesi(self):
        fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=self._satir("3855.83"), sahsi_alis=True,
            sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        bakiye_131 = (YevmiyeSatir.objects.filter(hesap_id="131.01", silindi=False)
                     .aggregate(b=Sum("borc"))["b"])
        self.assertEqual(bakiye_131, Decimal("4627.00"))
        cari_alacak = (YevmiyeSatir.objects.filter(hesap_id="320.10.0001", silindi=False)
                      .aggregate(a=Sum("alacak"))["a"])
        self.assertEqual(cari_alacak, Decimal("4627.00"))

    def test_ortak_secilmeden_sahsi_reddedilir(self):
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(
                tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
                satirlar=self._satir("1000"), sahsi_alis=True, sahsi_ortak_id=None,
                kullanici=self.u)

    def test_sahsi_gider_disi_fatura_tipinde_reddedilir(self):
        normal_alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        _hesap("153.10", "MAL")
        with self.assertRaises(FaturaHatasi):
            fatura_taslak_olustur(
                cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29), tip_id=normal_alis.pk,
                satirlar=[{"stok_id": None, "miktar": "1", "birim_fiyat": "100"}],
                sahsi_alis=True, sahsi_ortak_id=self.ortak.pk, kullanici=self.u)


class SahsiAlisTevkifatTest(SahsiAlisTemel):
    def test_tevkifatli_sahside_191_ile_602_ayni_tutar(self):
        from core.models import TevkifatOrani
        tev_hesap = _hesap("360.10.0710", "7/10 TEVKİFAT ÖDENECEK VERGİ")
        tev = TevkifatOrani.objects.create(kod="7/10-01", pay=7, payda=10, hesap=tev_hesap)
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=[{"hesap_id": None, "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv20.pk, "tevkifat_id": tev.pk}],
            sahsi_alis=True, sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        # KDV tam (200) 191'e borç; 602.01'e AYNI (200) alacak — tevkifattan bağımsız.
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))
        self.assertEqual(sat["602.01"], (Decimal("0.00"), Decimal("200.00")))
        self.assertEqual(sat["360.10.0710"], (Decimal("0.00"), Decimal("140.00")))
        # 131 ortak hesabı KDV DAHİL (1200) borçlanır — tevkifattan bağımsız.
        self.assertEqual(sat["131.01"], (Decimal("1200.00"), Decimal("0.00")))
        tb = sum(s.borc for s in f.fis.satirlar.all())
        ta = sum(s.alacak for s in f.fis.satirlar.all())
        self.assertEqual(tb, ta)


class SahsiAlisDuzenlemeSilmeTest(SahsiAlisTemel):
    def test_duzenleme_fisi_dogru_yeniler(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=self._satir("3855.83"), sahsi_alis=True,
            sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        eski_fis_id = f.fis_id
        fatura_guncelle(
            f, tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=[{"hesap_id": None, "miktar": "1", "birim_fiyat": "2000",
                      "kdv_id": self.kdv20.pk}],
            sahsi_alis=True, sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        f.refresh_from_db()
        self.assertEqual(f.fis_id, eski_fis_id)   # yerinde güncellenir, yeni fiş açılmaz
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["131.01"], (Decimal("2400.00"), Decimal("0.00")))
        self.assertEqual(sat["602.01"], (Decimal("0.00"), Decimal("400.00")))

    def test_sahsi_isareti_kaldirilinca_fis_normale_doner(self):
        gider_hesabi = _hesap("770.01", "AĞIRLAMA GİDERİ", grup="GELIR_TABLOSU", kalem="C")
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=self._satir("3855.83"), sahsi_alis=True,
            sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        fatura_guncelle(
            f, tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=[{"hesap_id": gider_hesabi.pk, "miktar": "1", "birim_fiyat": "3855.83",
                      "kdv_id": self.kdv20.pk}],
            sahsi_alis=False, sahsi_ortak_id=None, kullanici=self.u)
        f.refresh_from_db()
        self.assertFalse(f.sahsi_alis)
        self.assertIsNone(f.sahsi_ortak_id)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertNotIn("131.01", sat)
        self.assertNotIn("602.01", sat)
        self.assertEqual(sat["770.01"], (Decimal("3855.83"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("771.17"), Decimal("0.00")))

    def test_silme_fisi_iptal_eder(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=self._satir("3855.83"), sahsi_alis=True,
            sahsi_ortak_id=self.ortak.pk, kullanici=self.u)
        fis_id = f.fis_id
        fatura_sil(f, kullanici=self.u)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis_id).exists())


class NormalGiderFaturasiRegresyonTest(SahsiAlisTemel):
    def test_sahsiz_gider_faturasi_degismez(self):
        gider_hesabi = _hesap("770.02", "ALINAN HİZMET GİDERLERİ", grup="GELIR_TABLOSU", kalem="C")
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 8, 29),
            satirlar=[{"hesap_id": gider_hesabi.pk, "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv20.pk}],
            kullanici=self.u)
        self.assertFalse(f.sahsi_alis)
        self.assertIsNone(f.sahsi_ortak_id)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["770.02"], (Decimal("1000.00"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1200.00")))
        self.assertNotIn("602.01", sat)
        self.assertNotIn("131.01", sat)
