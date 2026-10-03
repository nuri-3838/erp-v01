"""Stok SARF çıkışı (hesaba/yatırım projesine çıkış) testleri: muhasebe fişi, FIFO
maliyet, 258+yatırım projesi zorunluluğu, iptal/fiş geri alma, mevcut fişsiz çıkışın
davranışının değişmediği regresyon. Bkz. core.services.hareket.sarf_cikis_ekle."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (Birim, FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur,
                         Stok, StokHareket, YatirimProjesi, YevmiyeFisi)
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil, sarf_cikis_ekle
from core.services import yatirim_projesi as yp_servis

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu=grup, rapor_kalemi=kalem,
                                     parasal=True, aktif=True)


class SarfTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 6, 1), usd_alis=Decimal("30"))
        cls.stok_hesabi = _hesap("150.20", "40X40X2 PROFİL DKP STOK HESABI")
        cls.hesap_253 = _hesap("253", "TESİS MAKİNE CİHAZLAR")
        cls.hesap_255 = _hesap("255", "DEMİRBAŞLAR")
        cls.hesap_258 = _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        cls.hesap_730 = _hesap("730", "GENEL ÜRETİM GİDERLERİ",
                               grup="GELIR_TABLOSU", kalem="C")
        ust = Kategori.objects.create(ad="HAMMADDE", kod="150")
        alt = Kategori.objects.create(ad="PROFİL", kod="20", ust=ust)
        cls.kategori = alt
        kg = Birim.objects.create(ad="BOY", kisa_ad="BY", ondalik=0)
        cls.stok = Stok.objects.create(kod="150-20-0002", ad="40X40X2 PROFİL DKP",
                                       kategori=alt, uretim_birimi=kg, fatura_birimi=kg)
        cls.alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.alis, hesap=cls.stok_hesabi)
        cls.depo = __import__("core.services.depo", fromlist=["depo_olustur"]).depo_olustur(
            kod="01", ad="ANA")
        cls.u = User.objects.create_superuser("yon", password="x")

    def setUp(self):
        # Her testte 100 boy @ 453,20 girişi (kullanıcının örneği).
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                    tur="GIRIS", miktar="100", birim_maliyet_try=Decimal("453.200000"),
                    giris_tutar_try=Decimal("45320.00"))


class SarfCikisServisTest(SarfTemel):
    def test_255_hesabina_sarf_fis_dogru(self):
        h = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_255.pk, kullanici=self.u)
        self.assertEqual(h.kaynak, StokHareket.Kaynak.SARF)
        self.assertEqual(h.tur, StokHareket.Tur.CIKIS)
        self.assertIsNotNone(h.fis_id)
        self.assertEqual(h.fis.kaynak, YevmiyeFisi.Kaynak.STOK_SARF)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in h.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["255"], (Decimal("4532.00"), Decimal("0.00")))     # karşı borç
        self.assertEqual(sat["150.20"], (Decimal("0.00"), Decimal("4532.00")))  # stok alacak
        tb = sum(s.borc for s in h.fis.satirlar.all())
        ta = sum(s.alacak for s in h.fis.satirlar.all())
        self.assertEqual(tb, ta)

    def test_730_gidere_sarf_proje_gerekmez(self):
        h = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="5", karsi_hesap_id=self.hesap_730.pk, kullanici=self.u)
        self.assertIsNone(h.yatirim_projesi_id)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in h.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["730"], (Decimal("2266.00"), Decimal("0.00")))

    def test_258_karsi_hesapta_proje_zorunlu(self):
        with self.assertRaises(HareketHatasi):
            sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_258.pk, kullanici=self.u)
        self.assertFalse(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.SARF).exists())

    def test_258_karsi_hesapla_proje_ile_calisir_ve_toplama_dahil(self):
        proje = yp_servis.proje_olustur(ad="İmalat Tezgahları", kullanici=self.u)
        h = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_258.pk,
                            yatirim_projesi_id=proje.pk, kullanici=self.u)
        self.assertEqual(h.yatirim_projesi_id, proje.pk)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in h.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["258"], (Decimal("4532.00"), Decimal("0.00")))
        self.assertEqual(yp_servis.proje_toplami(proje), Decimal("4532.00"))

    def test_proje_disi_hesapta_proje_secilemez(self):
        proje = yp_servis.proje_olustur(ad="X", kullanici=self.u)
        with self.assertRaises(HareketHatasi):
            sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_255.pk,
                            yatirim_projesi_id=proje.pk, kullanici=self.u)

    def test_eldeki_miktardan_fazla_engellenir(self):
        with self.assertRaises(HareketHatasi):
            sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="1000", karsi_hesap_id=self.hesap_255.pk, kullanici=self.u)
        self.assertEqual(eldeki_miktar(self.stok), Decimal("100.000"))

    def test_iptal_fisi_geri_alir_ve_katmani_yukler(self):
        h = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_255.pk, kullanici=self.u)
        fis = h.fis
        hareket_sil(h, kullanici=self.u)
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)
        self.assertEqual(eldeki_miktar(self.stok), Decimal("100.000"))   # geri geldi

    def test_mevcut_fissiz_cikis_davranisi_degismez(self):
        """Bugünkü '+ Hareket > Çıkış' (hareket_ekle) hâlâ fiş oluşturmuyor — regresyon."""
        c = hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 2),
                         tur="CIKIS", miktar="10")
        self.assertEqual(c.kaynak, StokHareket.Kaynak.MANUEL)
        self.assertIsNone(c.fis_id)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)


class SarfCikisViewTest(SarfTemel):
    def test_sarf_ekle_post_fis_olusturur(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse("core:stok_sarf_ekle", args=[self.stok.pk]), {
            "depo": str(self.depo.pk), "miktar": "10", "tarih": "2026-06-01",
            "karsi_hesap": str(self.hesap_255.pk), "aciklama": "test sarf"})
        self.assertEqual(r.status_code, 302)
        h = StokHareket.objects.get(kaynak=StokHareket.Kaynak.SARF)
        self.assertIsNotNone(h.fis_id)

    def test_fis_iptal_ekrani_sarf_fisini_engeller(self):
        self.client.force_login(self.u)
        h = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1),
                            miktar="10", karsi_hesap_id=self.hesap_255.pk, kullanici=self.u)
        r = self.client.post(reverse("core:fis_iptal", args=[h.fis_id]))
        self.assertEqual(r.status_code, 302)
        h.fis.refresh_from_db()
        self.assertFalse(h.fis.silindi)   # iptal edilmedi, yönlendirildi
