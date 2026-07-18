"""Teklif & Sipariş — model + servis + view (Dilim: liste/yeni/görüntüle)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, HesapPlani, KdvOrani, Kategori, Stok

EKRANLAR = ("satinalma_teklifleri", "satinalma_siparisleri",
            "satis_teklifleri", "satis_siparisleri")
EKLE_URL = {
    "satinalma_teklifleri": "satinalma_teklif_ekle",
    "satinalma_siparisleri": "satinalma_siparis_ekle",
    "satis_teklifleri": "satis_teklif_ekle",
    "satis_siparisleri": "satis_siparis_ekle",
}


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class TeklifSiparisModelServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsmyon", password="x")
        _hesap("120.01", "MÜŞTERİ A")
        cls.cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="K1", ad="GENEL", created_by=cls.yon,
                                          updated_by=cls.yon)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(kod="S1", ad="ÜRÜN A", kategori=cls.kat, kdv=cls.kdv,
                                       uretim_birimi=cls.birim, fatura_birimi=cls.birim,
                                       created_by=cls.yon, updated_by=cls.yon)

    def test_olustur_yevmiye_ve_stok_hareketi_uretmez(self):
        import datetime
        from core.models import StokHareket, YevmiyeFisi
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "25,50"}],
            belge_no="TEK-001", kullanici=self.yon)
        self.assertEqual(ts.belge_tur, "TEKLIF")
        self.assertEqual(ts.yon, "SATIS")
        self.assertEqual(ts.kalemler.count(), 1)
        self.assertEqual(ts.ara_toplam, Decimal("255.00"))
        self.assertEqual(ts.kdv_toplam, Decimal("51.00"))
        self.assertEqual(ts.genel_toplam, Decimal("306.00"))
        self.assertFalse(YevmiyeFisi.objects.exists())          # yevmiye ÜRETMEZ
        self.assertFalse(StokHareket.objects.exists())           # stok hareketi YARATMAZ

    def test_cari_bulunamaz_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="SIPARIS", yon="ALIS", cari_id=999999,
                tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "1"}],
                kullanici=self.yon)

    def test_bos_satir_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
                tarih=datetime.date(2026, 6, 28), satirlar=[], kullanici=self.yon)

    def test_negatif_miktar_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
                tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "-1", "birim_fiyat": "10"}],
                kullanici=self.yon)

    def test_kdvsiz_stok_kdv_toplam_sifir(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        stok2 = Stok.objects.create(kod="S2", ad="ÜRÜN B (KDV YOK)", kategori=self.kat,
                                    uretim_birimi=self.birim, fatura_birimi=self.birim,
                                    created_by=self.yon, updated_by=self.yon)
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": stok2.pk, "miktar": "5", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.assertEqual(ts.kdv_toplam, Decimal("0.00"))
        self.assertEqual(ts.genel_toplam, Decimal("50.00"))

    def test_aktif_teklif_siparisler_belge_tur_yon_filtreler(self):
        import datetime
        from core.services.teklif_siparis import (aktif_teklif_siparisler,
                                                   teklif_siparis_olustur)
        teklif_siparis_olustur(belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
                               tarih=datetime.date(2026, 6, 28),
                               satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                         "birim_fiyat": "1"}], kullanici=self.yon)
        teklif_siparis_olustur(belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
                               tarih=datetime.date(2026, 6, 28),
                               satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                         "birim_fiyat": "1"}], kullanici=self.yon)
        self.assertEqual(aktif_teklif_siparisler("TEKLIF", "SATIS").count(), 1)
        self.assertEqual(aktif_teklif_siparisler("SIPARIS", "SATIS").count(), 1)
        self.assertEqual(aktif_teklif_siparisler("TEKLIF", "ALIS").count(), 0)

    def test_guncelle_kalemleri_degistirir(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_guncelle, teklif_siparis_olustur
        stok2 = Stok.objects.create(kod="S9", ad="ÜRÜN GÜNCEL", kategori=self.kat, kdv=self.kdv,
                                    uretim_birimi=self.birim, fatura_birimi=self.birim,
                                    created_by=self.yon, updated_by=self.yon)
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            belge_no="ESKI", kullanici=self.yon)
        eski_kalem_pk = ts.kalemler.get().pk
        teklif_siparis_guncelle(
            ts, cari_id=self.cari.pk, tarih=datetime.date(2026, 7, 1),
            satirlar=[{"stok_id": stok2.pk, "miktar": "3", "birim_fiyat": "20"}],
            belge_no="YENI", kullanici=self.yon)
        ts.refresh_from_db()
        self.assertEqual(ts.belge_no, "YENI")
        self.assertEqual(ts.tarih, datetime.date(2026, 7, 1))
        self.assertEqual(ts.genel_toplam, Decimal("72.00"))       # 3x20=60 + %20 KDV=12
        aktif = list(ts.kalemler.filter(silindi=False))
        self.assertEqual(len(aktif), 1)
        self.assertEqual(aktif[0].stok_id, stok2.pk)
        from core.models import TeklifSiparisKalem
        self.assertTrue(TeklifSiparisKalem.objects.get(pk=eski_kalem_pk).silindi)  # eski soft-delete
        # belge_tur/yon SABİT kalır
        self.assertEqual(ts.belge_tur, "TEKLIF")
        self.assertEqual(ts.yon, "SATIS")

    def test_iptal_edilmis_duzenlenemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_guncelle,
                                                   teklif_siparis_iptal, teklif_siparis_olustur)
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        ts.refresh_from_db()
        self.assertTrue(ts.silindi)
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_guncelle(
                ts, cari_id=self.cari.pk, tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
                kullanici=self.yon)

    def test_iptal_tekrar_cagrilinca_sessiz(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_iptal, teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)   # ikinci çağrı hata vermez
        ts.refresh_from_db()
        self.assertTrue(ts.silindi)

    def test_teklifi_siparise_cevir_kalemler_kopyalanir(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur, teklifi_siparise_cevir
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28), belge_no="TEK-1",
            satirlar=[{"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "10"}],
            kullanici=self.yon)
        siparis = teklifi_siparise_cevir(t, tarih=datetime.date(2026, 7, 1), kullanici=self.yon)
        self.assertEqual(siparis.belge_tur, "SIPARIS")
        self.assertEqual(siparis.yon, "SATIS")
        self.assertEqual(siparis.cari_id, self.cari.pk)
        self.assertEqual(siparis.para_birimi, t.para_birimi)
        self.assertEqual(siparis.kaynak_teklif_id, t.pk)
        self.assertEqual(siparis.tarih, datetime.date(2026, 7, 1))
        self.assertEqual(siparis.genel_toplam, t.genel_toplam)
        self.assertEqual(siparis.kalemler.count(), 1)
        k = siparis.kalemler.get()
        self.assertEqual(k.stok_id, self.stok.pk)
        self.assertEqual(k.miktar, Decimal("4"))
        self.assertEqual(k.birim_fiyat, Decimal("10"))
        # belge_no kopyalanmaz — yeni belgenin kendi numarası
        self.assertEqual(siparis.belge_no, "")

    def test_iki_kez_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_olustur,
                                                   teklifi_siparise_cevir)
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)

    def test_siparis_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_olustur,
                                                   teklifi_siparise_cevir)
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(sip, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)

    def test_iptal_edilmis_teklif_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_iptal,
                                                   teklif_siparis_olustur, teklifi_siparise_cevir)
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(t, kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)


class TeklifSiparisViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsvyon", password="x")
        cls.bos = User.objects.create_user("tsvbos", password="x")
        _hesap("120.02", "MÜŞTERİ B")
        cls.cari = Cari.objects.create(kod="C2", unvan="MÜŞTERİ B", muhasebe_kodu="120.02",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="K2", ad="GENEL2", created_by=cls.yon,
                                          updated_by=cls.yon)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("18"), aciklama="Genel2",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(kod="S3", ad="ÜRÜN C", kategori=cls.kat, kdv=cls.kdv,
                                       uretim_birimi=cls.birim, fatura_birimi=cls.birim,
                                       created_by=cls.yon, updated_by=cls.yon)

    def test_liste_ekranlari_200(self):
        self.client.force_login(self.yon)
        for ad in EKRANLAR:
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 200)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        for ad in EKRANLAR:
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 403)
            self.assertEqual(
                self.client.get(reverse("core:" + EKLE_URL[ad])).status_code, 403)

    def test_her_4_ekranda_olustur_ve_detay(self):
        from core.models import TeklifSiparis
        self.client.force_login(self.yon)
        kombinasyonlar = [
            ("satinalma_teklifleri", "TEKLIF", "ALIS"),
            ("satinalma_siparisleri", "SIPARIS", "ALIS"),
            ("satis_teklifleri", "TEKLIF", "SATIS"),
            ("satis_siparisleri", "SIPARIS", "SATIS"),
        ]
        for ekran, belge_tur, yon in kombinasyonlar:
            ekle_ad = EKLE_URL[ekran]
            self.assertEqual(self.client.get(reverse("core:" + ekle_ad)).status_code, 200)
            r = self.client.post(reverse("core:" + ekle_ad), {
                "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
                "belge_no": f"{ekran}-1",
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
                "form-0-stok": self.stok.pk, "form-0-miktar": "2", "form-0-birim_fiyat": "100",
            })
            ts = TeklifSiparis.objects.get(belge_no=f"{ekran}-1")
            self.assertEqual(ts.belge_tur, belge_tur)
            self.assertEqual(ts.yon, yon)
            self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
            d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
            self.assertEqual(d.status_code, 200)
            self.assertContains(d, "ÜRÜN C")
            self.assertContains(d, "236,00")                     # 200 + %18 KDV=36
            rl = self.client.get(reverse("core:" + ekran))
            self.assertContains(rl, reverse("core:teklif_siparis_detay", args=[ts.pk]))

    def test_bos_kalemle_kaydedilmez(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), {
            "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": "", "form-0-miktar": "", "form-0-birim_fiyat": "",
        })
        self.assertEqual(r.status_code, 200)                     # formset min_num -> hata, kalır

    def test_menude_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "Satınalma")
        self.assertContains(r, "Satış")
        for ad in EKRANLAR:
            self.assertContains(r, reverse("core:" + ad))

    def test_duzenle_get_ve_post(self):
        from core.models import TeklifSiparis
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            belge_no="D-1", kullanici=self.yon)
        self.client.force_login(self.yon)
        g = self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk]))
        self.assertEqual(g.status_code, 200)
        self.assertContains(g, "D-1")
        r = self.client.post(reverse("core:teklif_siparis_duzenle", args=[ts.pk]), {
            "cari": self.cari.pk, "tarih": "2026-07-01", "para_birimi": "TRY",
            "belge_no": "D-2",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "5", "form-0-birim_fiyat": "40",
        })
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts = TeklifSiparis.objects.get(pk=ts.pk)
        self.assertEqual(ts.belge_no, "D-2")
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 1)
        self.assertEqual(ts.kalemler.filter(silindi=False).first().miktar, Decimal("5"))

    def test_iptal_view_detay_hala_gorunur_liste_kaybolur(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            belge_no="I-1", kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:teklif_siparis_iptal", args=[ts.pk]))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(d.status_code, 200)                      # 404 değil — hâlâ görüntülenir
        self.assertContains(d, "iptal edilmiş")
        rl = self.client.get(reverse("core:satis_siparisleri"))
        self.assertNotContains(rl, "I-1")                          # listeden düşer

    def test_iptal_sonrasi_duzenle_404(self):
        from core.services.teklif_siparis import teklif_siparis_iptal, teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        self.client.force_login(self.yon)
        self.assertEqual(
            self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk])).status_code, 404)

    def test_duzenle_iptal_yetkisiz_403(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk])).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("core:teklif_siparis_iptal", args=[ts.pk])).status_code, 403)

    def test_view_teklif_siparise_cevir(self):
        import datetime
        from core.models import TeklifSiparis
        from core.services.teklif_siparis import teklif_siparis_olustur
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28), belge_no="TEK-V1",
            satirlar=[{"stok_id": self.stok.pk, "miktar": "2", "birim_fiyat": "100"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        d0 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertContains(d0, reverse("core:teklif_siparise_cevir", args=[t.pk]))
        r = self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk]))
        siparis = TeklifSiparis.objects.get(kaynak_teklif=t)
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertEqual(siparis.belge_tur, "SIPARIS")
        self.assertEqual(siparis.kalemler.count(), 1)
        # teklif detayında artık "Siparişe Çevir" değil, dönüştüğü siparişe link var
        d1 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertNotContains(d1, reverse("core:teklif_siparise_cevir", args=[t.pk]))
        self.assertContains(d1, reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        # sipariş detayında kaynak teklife link var
        d2 = self.client.get(reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertContains(d2, "Kaynak Teklif")
        self.assertContains(d2, reverse("core:teklif_siparis_detay", args=[t.pk]))
        # tekrar dönüştürme denemesi hata mesajıyla teklife geri döner
        r2 = self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk]))
        self.assertRedirects(r2, reverse("core:teklif_siparis_detay", args=[t.pk]))

    def test_siparis_donusturme_butonu_yok(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertNotContains(d, reverse("core:teklif_siparise_cevir", args=[sip.pk]))

    def test_donusum_yetkisiz_403(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk])).status_code, 403)

