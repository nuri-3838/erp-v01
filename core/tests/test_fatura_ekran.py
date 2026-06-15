"""Fatura ekranları (view) testleri: ekle (POST -> fatura+fiş), liste, detay, iptal, yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (Birim, Cari, EkranYetki, Fatura, FaturaTipi, HesapPlani,
                         Kategori, KategoriHesap, KdvOrani, Kur, Stok, YevmiyeFisi)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu=grup, rapor_kalemi=kalem, parasal=True)


class FaturaEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("153.10", "ALÜMİNYUM MAL")
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        cls.kdv = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        ust = Kategori.objects.create(ad="HAMMADDE", kod="153")
        alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(
            kod="153-10-0001", ad="ALÜMİNYUM LEVHA", kategori=alt,
            uretim_birimi=adet, fatura_birimi=adet, kdv=cls.kdv)
        cls.alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.alis,
                                     hesap=HesapPlani.objects.get(hesap_kodu="153.10"))
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A",
                                       para_birimi="TRY", muhasebe_kodu="320.10.0001")

    def _post_data(self, miktar="10", fiyat="100"):
        return {
            "tip": str(self.alis.pk), "cari": str(self.cari.pk),
            "tarih": "2026-03-10", "fatura_no": "A-1", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": str(self.stok.pk), "form-0-miktar": miktar,
            "form-0-birim_fiyat": fiyat,
        }

    def test_ekle_post_fatura_ve_fis(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        self.assertEqual(r.status_code, 302)
        f = Fatura.objects.get(fatura_no="A-1")
        self.assertIsNotNone(f.fis_id)
        self.assertEqual(f.fis.kaynak, YevmiyeFisi.Kaynak.FATURA)
        self.assertEqual(f.genel_toplam, Decimal("1200.00"))
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["153.10"], (Decimal("1000.00"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1200.00")))

    def test_ekle_hatali_harita_formda_kalir(self):
        # mal hesabı bağı silinince hata -> form 200, kayıt yok
        KategoriHesap.objects.all().delete()
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Fatura.objects.count(), 0)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_liste_ve_detay(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        f = Fatura.objects.get(fatura_no="A-1")
        r = self.client.get(reverse("core:alis_faturalari"))
        self.assertContains(r, "TEDARİKÇİ A")
        r2 = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "ALÜMİNYUM LEVHA")
        self.assertContains(r2, reverse("core:fis_detay", args=[f.fis.pk]))

    def test_iptal(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        f = Fatura.objects.get(fatura_no="A-1")
        fis_id = f.fis_id
        r = self.client.post(reverse("core:fatura_iptal", args=[f.pk]))
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertTrue(f.silindi)
        self.assertTrue(YevmiyeFisi.objects.get(pk=fis_id).silindi)

    def test_duzenle_post(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        f = Fatura.objects.get(fatura_no="A-1")
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]),
                             self._post_data(miktar="20"))
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(f.genel_toplam, Decimal("2400.00"))     # 20×100×1,20
        self.assertEqual(f.satirlar.filter(silindi=False).count(), 1)

    def test_fatura_fisi_dogrudan_duzenlenemez(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:alis_fatura_ekle"), self._post_data())
        f = Fatura.objects.get(fatura_no="A-1")
        r = self.client.get(reverse("core:fis_duzenle", args=[f.fis_id]))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse("core:fatura_duzenle", args=[f.pk]), r.url)

    def test_doviz_fis_duzenle_islem_tutari_gosterir(self):
        # #bug: döviz fişte düzenleme kutusu TL'yi (1500) değil işlem tutarını (50) gösterir
        from core.services.yevmiye import SatirGirdi, fis_olustur
        fis = fis_olustur(tarih=D(2026, 3, 10), kullanici=self.yon, satirlar=[
            SatirGirdi(hesap_kodu="153.10", taraf="B", islem_tutari="50",
                       islem_pb="USD", islem_kuru="30"),
            SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="50",
                       islem_pb="USD", islem_kuru="30")])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fis_duzenle", args=[fis.pk]))
        self.assertEqual(r.status_code, 200)
        ilk = r.context["formset"].forms[0].initial
        self.assertEqual(ilk["borc"], Decimal("50.00"))          # işlem tutarı, 1500 TL değil

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:alis_faturalari")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:alis_fatura_ekle")).status_code, 403)

    def test_satis_listesi_yuklenir(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:satis_faturalari")).status_code, 200)

    def test_alis_formu_yon_filtreler(self):
        # Alış ekle formundaki tip dropdown'u yalnız ALIŞ tiplerini gösterir
        satis = FaturaTipi.objects.create(ad="SATIŞ FATURASI", yon=FaturaTipi.Yon.SATIS)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        tipler = list(r.context["fform"].fields["tip"].queryset)
        self.assertIn(self.alis, tipler)
        self.assertNotIn(satis, tipler)
