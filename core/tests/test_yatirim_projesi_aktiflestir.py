"""Yatırım Projesi aktifleştirme (DURAN VARLIK FAZ 3): 258 toplamının birden fazla
duran varlık satırına bölünmesi -> tek fiş + DuranVarlik kartları (kaynak=PROJE) +
proje durumu AKTIFLESTI; geri alma: fiş iptali + kart silme + proje DEVAM'a döner,
yalnız yönetici."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, DuranVarlik, EkranYetki, FaturaTipi, HesapPlani, KdvOrani, Kur, YatirimProjesi
from core.services.fatura import fatura_olustur
from core.services.yatirim_projesi import (
    YatirimProjesiHatasi, proje_aktiflestir, proje_geri_al, proje_olustur, proje_toplami,
)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=(grup == "BILANCO"))


class AktiflestirBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        Kur.objects.create(tarih=D(2026, 4, 1), usd_alis=Decimal("31"))
        Kur.objects.create(tarih=D(2026, 4, 2), usd_alis=Decimal("31"))
        _hesap("191", "İNDİRİLECEK KDV", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        _hesap("255", "DEMİRBAŞLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")

    def _proje_doldur(self, tutar="10000", proje=None):
        proje = proje or proje_olustur(ad="fabrika hattı")
        fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": tutar,
                      "kdv_id": self.kdv20.pk, "yatirim_projesi_id": proje.pk}])
        return proje


class ProjeAktiflestirServisTest(AktiflestirBase):
    def test_tek_satir_basariyla_aktiflestirir(self):
        proje = self._proje_doldur("10000")
        guncel = proje_aktiflestir(
            proje, tarih=D(2026, 4, 1),
            satirlar=[{"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
                      "varlik_adi": "cnc lazer", "tutar": Decimal("10000")}])
        self.assertEqual(guncel.durum, YatirimProjesi.Durum.AKTIFLESTI)
        self.assertIsNotNone(guncel.aktiflestirme_fisi_id)
        fis = guncel.aktiflestirme_fisi
        self.assertTrue(fis.satirlar.filter(hesap_id="253", borc=Decimal("10000.00")).exists())
        self.assertTrue(fis.satirlar.filter(hesap_id="258", alacak=Decimal("10000.00")).exists())
        self.assertEqual(fis.satirlar.filter(silindi=False).count(), 2)
        self.assertTrue(abs(sum(s.borc - s.alacak for s in fis.satirlar.all())) == 0)

        v = DuranVarlik.objects.get(yatirim_projesi=proje)
        self.assertEqual(v.kaynak, DuranVarlik.Kaynak.PROJE)
        self.assertEqual(v.ad, "CNC LAZER")
        self.assertEqual(v.maliyet, Decimal("10000.00"))
        self.assertEqual(v.fatura_satirlari.count(), 1)

    def test_coklu_satir_toplami_esit_olmali(self):
        proje = self._proje_doldur("10000")
        h253 = HesapPlani.objects.get(hesap_kodu="253").pk
        h255 = HesapPlani.objects.get(hesap_kodu="255").pk
        proje_aktiflestir(
            proje, tarih=D(2026, 4, 1),
            satirlar=[{"hesap_id": h253, "varlik_adi": "cnc", "tutar": Decimal("6000")},
                     {"hesap_id": h255, "varlik_adi": "masa", "tutar": Decimal("4000")}])
        self.assertEqual(DuranVarlik.objects.filter(yatirim_projesi=proje).count(), 2)
        self.assertEqual(proje_toplami(proje), Decimal("10000.00"))  # kalemler hâlâ duruyor

    def test_satir_toplami_uyusmazsa_reddedilir(self):
        proje = self._proje_doldur("10000")
        h253 = HesapPlani.objects.get(hesap_kodu="253").pk
        with self.assertRaises(YatirimProjesiHatasi):
            proje_aktiflestir(proje, tarih=D(2026, 4, 1),
                              satirlar=[{"hesap_id": h253, "varlik_adi": "cnc",
                                        "tutar": Decimal("9999")}])
        proje.refresh_from_db()
        self.assertEqual(proje.durum, YatirimProjesi.Durum.DEVAM)
        self.assertEqual(DuranVarlik.objects.count(), 0)

    def test_258_hesabi_hedef_olarak_reddedilir(self):
        proje = self._proje_doldur("10000")
        h258 = HesapPlani.objects.get(hesap_kodu="258").pk
        with self.assertRaises(YatirimProjesiHatasi):
            proje_aktiflestir(proje, tarih=D(2026, 4, 1),
                              satirlar=[{"hesap_id": h258, "varlik_adi": "cnc",
                                        "tutar": Decimal("10000")}])

    def test_bos_proje_reddedilir(self):
        proje = proje_olustur(ad="boş proje")
        with self.assertRaises(YatirimProjesiHatasi):
            proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
                {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
                 "varlik_adi": "x", "tutar": Decimal("1")}])

    def test_zaten_aktiflesmis_proje_tekrar_aktiflestirilemez(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        proje.refresh_from_db()
        with self.assertRaises(YatirimProjesiHatasi):
            proje_aktiflestir(proje, tarih=D(2026, 4, 2), satirlar=[
                {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
                 "varlik_adi": "x", "tutar": Decimal("1")}])


class ProjeGeriAlServisTest(AktiflestirBase):
    def test_geri_alma_fisi_iptal_eder_kartlari_siler_proje_devam_doner(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        proje.refresh_from_db()
        fis = proje.aktiflestirme_fisi

        proje_geri_al(proje)
        proje.refresh_from_db()
        fis.refresh_from_db()
        self.assertEqual(proje.durum, YatirimProjesi.Durum.DEVAM)
        self.assertIsNone(proje.aktiflestirme_fisi_id)
        self.assertTrue(fis.silindi)
        self.assertEqual(DuranVarlik.objects.filter(yatirim_projesi=proje, silindi=False).count(), 0)
        # proje kalemleri hâlâ duruyor -> tekrar aktiflestirilebilir
        self.assertEqual(proje_toplami(proje), Decimal("10000.00"))

    def test_devam_durumundaki_proje_geri_alinamaz(self):
        proje = self._proje_doldur("10000")
        with self.assertRaises(YatirimProjesiHatasi):
            proje_geri_al(proje)


class AktiflestirEkranTest(AktiflestirBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.yonetici = User.objects.create_superuser("yonetici", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="yatirim_projeleri")

    def test_aktiflestir_ekrani_render(self):
        proje = self._proje_doldur("10000")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yatirim_projesi_aktiflestir", args=[proje.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "10.000,00")

    def test_post_basariyla_aktiflestirir_ve_detaya_doner(self):
        proje = self._proje_doldur("10000")
        self.client.force_login(self.yetkili)
        h253 = HesapPlani.objects.get(hesap_kodu="253").pk
        r = self.client.post(reverse("core:yatirim_projesi_aktiflestir", args=[proje.pk]), {
            "tarih": "2026-04-01",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-hesap": h253, "form-0-varlik_adi": "cnc lazer", "form-0-tutar": "10.000,00",
        })
        self.assertEqual(r.status_code, 302)
        proje.refresh_from_db()
        self.assertEqual(proje.durum, YatirimProjesi.Durum.AKTIFLESTI)

    def test_aktif_olmus_projeye_tekrar_girilemez_yonlendirir(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yatirim_projesi_aktiflestir", args=[proje.pk]))
        self.assertEqual(r.status_code, 302)

    def test_geri_al_yonetici_olmayan_403(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yatirim_projesi_geri_al", args=[proje.pk]))
        self.assertEqual(r.status_code, 403)

    def test_geri_al_yonetici_basarili(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        self.client.force_login(self.yonetici)
        r = self.client.post(reverse("core:yatirim_projesi_geri_al", args=[proje.pk]))
        self.assertEqual(r.status_code, 302)
        proje.refresh_from_db()
        self.assertEqual(proje.durum, YatirimProjesi.Durum.DEVAM)

    def test_fis_iptal_ekrani_yatirim_fisini_proje_detayina_yonlendirir(self):
        proje = self._proje_doldur("10000")
        proje_aktiflestir(proje, tarih=D(2026, 4, 1), satirlar=[
            {"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk,
             "varlik_adi": "cnc", "tutar": Decimal("10000")}])
        proje.refresh_from_db()
        self.client.force_login(self.yonetici)
        r = self.client.get(reverse("core:fis_sil", args=[proje.aktiflestirme_fisi.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("core:yatirim_projesi_detay", args=[proje.pk]))
