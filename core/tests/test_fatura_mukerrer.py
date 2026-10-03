"""Mükerrer ALIŞ faturası kontrolü: aynı cari + aynı fatura no (büyük/küçük harf ve boşluk
farkı yok sayılır); silinmiş fatura sayılmaz; düzenlemede fatura kendini saymaz."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, FaturaTipi, HesapPlani, KdvOrani, Kur
from core.services.fatura import (FaturaHatasi, MukerrerFaturaHatasi, fatura_guncelle,
                                  fatura_olustur, fatura_sil)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class MukerrerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 8, 10), usd_alis=Decimal("40"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "A", kalem="KVYK")
        _hesap("320.10.0002", "B", kalem="KVYK")
        _hesap("770.01", "GİDER", grup="GELIR_TABLOSU", kalem="C")
        cls.kdv0 = KdvOrani.objects.create(aciklama="KDV YOK", oran=Decimal("0"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.a = Cari.objects.create(kod="320-10-0001", unvan="A", para_birimi="TRY",
                                    muhasebe_kodu="320.10.0001")
        cls.b = Cari.objects.create(kod="320-10-0002", unvan="B", para_birimi="TRY",
                                    muhasebe_kodu="320.10.0002")
        cls.u = User.objects.create_superuser("yon", password="x")
        cls.hesap = HesapPlani.objects.get(hesap_kodu="770.01")

    def _yaz(self, cari, no, tutar="100"):
        return fatura_olustur(
            tip_id=self.gider.pk, cari_id=cari.pk, tarih=D(2026, 8, 10), fatura_no=no,
            satirlar=[{"hesap_id": self.hesap.pk, "miktar": "1", "birim_fiyat": tutar,
                      "kdv_id": self.kdv0.pk}], kullanici=self.u)

    def test_ayni_cari_ayni_no_engellenir_harf_ve_bosluk_yok_sayilir(self):
        self._yaz(self.a, "ab 123")
        with self.assertRaises(MukerrerFaturaHatasi):
            self._yaz(self.a, "AB123 ")

    def test_farkli_cari_ayni_no_serbest(self):
        self._yaz(self.a, "X-1")
        self._yaz(self.b, "X-1")

    def test_bos_no_kontrol_edilmez(self):
        self._yaz(self.a, "")
        self._yaz(self.a, "")

    def test_silinmis_fatura_sayilmaz(self):
        f = self._yaz(self.a, "S-1")
        fatura_sil(f, kullanici=self.u)
        self._yaz(self.a, "S-1")

    def test_duzenlemede_kendini_saymaz_ama_baskasini_sayar(self):
        f1 = self._yaz(self.a, "D-1")
        f2 = self._yaz(self.a, "D-2")
        kw = dict(tip_id=self.gider.pk, cari_id=self.a.pk, tarih=D(2026, 8, 10),
                  satirlar=[{"hesap_id": self.hesap.pk, "miktar": "1", "birim_fiyat": "50",
                            "kdv_id": self.kdv0.pk}], kullanici=self.u)
        fatura_guncelle(f1, fatura_no="D-1", **kw)            # kendisi: serbest
        with self.assertRaises(MukerrerFaturaHatasi):
            fatura_guncelle(f2, fatura_no="d-1", **kw)

    def test_hata_mevcut_kaydi_tasir_ve_formda_link_gorunur(self):
        f1 = self._yaz(self.a, "L-1", "250")
        try:
            self._yaz(self.a, "L-1")
        except MukerrerFaturaHatasi as e:
            self.assertEqual(e.fatura.pk, f1.pk)
            self.assertIsInstance(e, FaturaHatasi)
        from core.models import EkranYetki
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="alis_faturalari")
        self.client.force_login(self.u)
        r = self.client.post(reverse("core:alis_fatura_ekle"), {
            "tip": self.gider.pk, "cari": self.a.pk, "tarih": "2026-08-10", "fatura_no": "l-1",
            "para_birimi": "TRY", "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-hesap": self.hesap.pk, "form-0-miktar": "1",
            "form-0-birim_fiyat": "10", "form-0-kdv": self.kdv0.pk})
        self.assertContains(r, "Bu fatura zaten kayıtlı")
        self.assertContains(r, reverse("core:fatura_detay", args=[f1.pk]))
