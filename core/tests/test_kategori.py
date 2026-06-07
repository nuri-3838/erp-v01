"""Kategori (STOKLAR Faz 0+2) testleri — 2 seviye + Kod (benzersiz) + ALT × fatura tipi
→ yaprak hesap HARİTASI + view + yetki + akıllı arama kaynağı."""
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, FaturaTipi, HesapPlani, Kategori, KategoriHesap
from core.services.fatura_tipi import fatura_tipi_olustur
from core.services.kategori import (KategoriHatasi, kategori_guncelle,
                                     kategori_hesaplari, kategori_hesaplari_kaydet,
                                     kategori_olustur, kategori_sil)


def _hesaplar():
    """153 (üst/ara) + 153.10 / 153.20 (yaprak) — hesap bağı testleri için."""
    ust = HesapPlani.objects.create(
        hesap_kodu="153", hesap_adi="TİCARİ MALLAR",
        rapor_grubu="BILANCO", rapor_kalemi="DV")
    y1 = HesapPlani.objects.create(
        hesap_kodu="153.10", hesap_adi="ALÜMİNYUM",
        rapor_grubu="BILANCO", rapor_kalemi="DV")
    y2 = HesapPlani.objects.create(
        hesap_kodu="153.20", hesap_adi="ÇELİK",
        rapor_grubu="BILANCO", rapor_kalemi="DV")
    return ust, y1, y2


class KategoriServisTest(TestCase):
    def test_ust_olustur_tr_buyuk_harf(self):
        k = kategori_olustur(ad="hammadde", kod="HM")
        self.assertEqual((k.ad, k.kod), ("HAMMADDE", "HM"))
        self.assertIsNone(k.ust_id)

    def test_alt_olustur(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="HM-ALU", ust_id=ust.pk)
        self.assertEqual(alt.ust_id, ust.pk)

    def test_kod_zorunlu(self):
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="hammadde", kod="  ")

    def test_kod_kok_benzersiz(self):
        kategori_olustur(ad="hammadde", kod="150")
        with self.assertRaises(KategoriHatasi):           # iki kök aynı kod
            kategori_olustur(ad="başka", kod="150")

    def test_kod_ayni_ust_altinda_benzersiz(self):
        ust = kategori_olustur(ad="hammadde", kod="150")
        kategori_olustur(ad="alüminyum", kod="10", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):           # aynı üst altında aynı kod
            kategori_olustur(ad="çelik", kod="10", ust_id=ust.pk)

    def test_kod_farkli_ust_altinda_serbest(self):
        u1 = kategori_olustur(ad="hammadde", kod="150")
        u2 = kategori_olustur(ad="yarı mamuller", kod="151")
        a1 = kategori_olustur(ad="alüminyum", kod="10", ust_id=u1.pk)
        a2 = kategori_olustur(ad="kesilmiş", kod="10", ust_id=u2.pk)   # FARKLI üst → serbest
        self.assertEqual((a1.kod, a2.kod), ("10", "10"))

    def test_db_ayni_ust_kod_unique(self):
        ust = kategori_olustur(ad="hammadde", kod="150")
        Kategori.objects.create(ad="A", kod="10", ust=ust)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Kategori.objects.create(ad="B", kod="10", ust=ust)

    def test_db_kok_kod_unique_nulls_not_distinct(self):
        Kategori.objects.create(ad="A", kod="150")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Kategori.objects.create(ad="B", kod="150")   # iki kök (ust=NULL) aynı kod

    def test_uc_seviye_engellenir(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):
            kategori_olustur(ad="6063", kod="6063", ust_id=alt.pk)

    def test_guncelle_ad_kod(self):
        k = kategori_olustur(ad="hammadde", kod="HM")
        kategori_guncelle(k, ad="hammadde grup", kod="HMG")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.kod), ("HAMMADDE GRUP", "HMG"))

    def test_sil_soft_delete(self):
        k = kategori_olustur(ad="hammadde", kod="HM")
        kategori_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_alt_kategorili_ust_silinemez(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):
            kategori_sil(ust)
        kategori_sil(alt)
        kategori_sil(ust)
        ust.refresh_from_db()
        self.assertTrue(ust.silindi)

    # --- Harita (ALT × fatura tipi → yaprak hesap) ---
    def test_harita_kaydet_yaprak(self):
        _hesaplar()
        ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: "153.10"})
        m = kategori_hesaplari(alt)
        self.assertEqual(m[ft.pk].hesap_id, "153.10")

    def test_harita_yaprak_degil_red(self):
        _hesaplar()
        ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        with self.assertRaises(KategoriHatasi):
            kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: "153"})  # ara hesap

    def test_harita_bos_soft_delete_ve_canlanma(self):
        _hesaplar()
        ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: "153.10"})
        kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: ""})        # bağ kaldır
        self.assertEqual(kategori_hesaplari(alt), {})
        self.assertEqual(KategoriHesap.objects.filter(kategori=alt).count(), 1)  # iz kalır
        kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: "153.20"})  # yeniden bağla
        m = kategori_hesaplari(alt)
        self.assertEqual(m[ft.pk].hesap_id, "153.20")
        self.assertEqual(KategoriHesap.objects.filter(kategori=alt).count(), 1)  # tek satır

    def test_harita_ust_kategoride_red(self):
        ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        ust = kategori_olustur(ad="hammadde", kod="HM")
        with self.assertRaises(KategoriHatasi):
            kategori_hesaplari_kaydet(ust, eslesmeler={ft.pk: ""})

    def test_kategori_sil_haritayi_da_soft_delete(self):
        _hesaplar()
        ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS")
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        kategori_hesaplari_kaydet(alt, eslesmeler={ft.pk: "153.10"})
        kategori_sil(alt)
        self.assertEqual(kategori_hesaplari(alt), {})


class KategoriViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="kategoriler")
        cls.bos = User.objects.create_user("bos", password="x")
        _hesaplar()
        cls.ft = fatura_tipi_olustur(ad="satış faturası", yon="SATIS", sira=10)

    def test_liste_render(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        kategori_olustur(ad="alüminyum", kod="HM-ALU", ust_id=ust.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kategoriler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "HAMMADDE")
        self.assertContains(r, "HM-ALU")
        self.assertContains(r, "+ Yeni Üst")

    def test_ekle_ust_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"),
                             {"ad": "hammadde", "kod": "HM"})
        self.assertEqual(r.status_code, 302)
        k = Kategori.objects.get(kod="HM")
        self.assertEqual((k.ad, k.ust_id), ("HAMMADDE", None))

    def test_ekle_alt_post_harita(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"), {
            "ad": "alüminyum", "kod": "ALU", "ust": str(ust.pk),
            f"hesap_{self.ft.pk}": "153.10"})
        self.assertEqual(r.status_code, 302)
        alt = Kategori.objects.get(kod="ALU")
        self.assertEqual(alt.ust_id, ust.pk)
        self.assertEqual(
            KategoriHesap.objects.get(kategori=alt, fatura_tipi=self.ft).hesap_id,
            "153.10")

    def test_ekle_kod_benzersiz_formda_kalir(self):
        kategori_olustur(ad="hammadde", kod="HM")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_ekle"),
                             {"ad": "başka", "kod": "HM"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Kategori.objects.filter(ad="BAŞKA").exists())

    def test_ekle_alt_sayfa_yaprak_hesaplari_ve_fatura_tipi(self):
        """Akıllı arama kaynağı (ALT bağlamında): select YALNIZ yaprak hesapları + fatura tipi."""
        ust = kategori_olustur(ad="hammadde", kod="HM")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:kategori_ekle") + f"?ust={ust.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'value="153.10"')      # yaprak hesap seçilebilir
        self.assertNotContains(r, 'value="153"')        # ara hesap yok
        self.assertContains(r, "SATIŞ FATURASI")        # fatura tipi satırı
        self.assertContains(r, f'name="hesap_{self.ft.pk}"')

    def test_ekle_ust_sayfada_harita_yok(self):
        """Kök (üst) ekleme sayfasında muhasebe kodları haritası RENDER EDİLMEZ."""
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:kategori_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Muhasebe Kodları")

    def test_duzenle_harita_post(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        alt = kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        kategori_hesaplari_kaydet(alt, eslesmeler={self.ft.pk: "153.10"})
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_duzenle", args=[alt.pk]), {
            "ad": "alüminyum", "kod": "ALU", f"hesap_{self.ft.pk}": "153.20"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            KategoriHesap.objects.get(kategori=alt, fatura_tipi=self.ft).hesap_id,
            "153.20")

    def test_sil_alt_kategorili_ust_engellenir(self):
        ust = kategori_olustur(ad="hammadde", kod="HM")
        kategori_olustur(ad="alüminyum", kod="ALU", ust_id=ust.pk)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_sil", args=[ust.pk]))
        self.assertEqual(r.status_code, 302)
        ust.refresh_from_db()
        self.assertFalse(ust.silindi)

    def test_sil_post_soft_delete(self):
        k = kategori_olustur(ad="hammadde", kod="HM")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:kategori_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kategoriler")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:kategori_ekle")).status_code, 403)
