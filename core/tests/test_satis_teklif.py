"""Satış Teklifi — bağımsız ekran (core/views.py::satis_teklif_ekle/satis_teklif_duzenle):
tüm satış ürünleri otomatik önceden dolu gelir, miktar YOK (hep 1), cari seçilince PB/
iskonto otomatik context'e gelir, teslim şekli/ödeme koşulu kaydedilir, eski paylaşımlı
teklif_siparis_duzenle URL'i buraya yönlendirir (veri kaybı koruması)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, HesapPlani, KdvOrani, Kategori, Stok, TeklifSiparis
from core.services.stok import stok_olustur


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)


class SatisTeklifTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("styon", password="x")
        cls.bos = User.objects.create_user("stbos", password="x")
        _hesap("120.01", "MÜŞTERİ SATIŞ TEKLİF")
        cls.cari = Cari.objects.create(
            kod="C1", unvan="MÜŞTERİ SATIŞ TEKLİF", muhasebe_kodu="120.01",
            para_birimi="USD", iskonto_yuzdesi=Decimal("10"),
            created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="150", ad="MAMUL", created_by=cls.yon,
                                      updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="10", ad="MERDİVEN", ust=ust,
                                          created_by=cls.yon, updated_by=cls.yon)
        from core.models import Birim
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.a21 = stok_olustur(
            ad="a tipi merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="a21",
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.c22 = stok_olustur(
            ad="cift cikisli merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="c22",
            fiyat_try="15000", kullanici=cls.yon)   # USD fiyatı YOK -> eksik uyarısı

    def test_get_tum_satis_urunleri_hazir_gelir(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        self.assertEqual(r.status_code, 200)
        formset = r.context["formset"]
        self.assertEqual(len(formset.forms), 2)
        stoklar = {f.initial["stok"] for f in formset.forms}
        self.assertEqual(stoklar, {self.a21.pk, self.c22.pk})
        for f in formset.forms:
            self.assertTrue(f.initial["dahil"])

    def test_stok_meta_pb_basina_dogru_ve_eksikse_none(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        meta = r.context["stok_meta"][str(self.a21.pk)]
        self.assertEqual(meta["modelKodu"], "A21")
        self.assertEqual(meta["fiyatlar"]["TRY"], 12000.0)
        self.assertEqual(meta["fiyatlar"]["USD"], 350.0)
        meta_c22 = r.context["stok_meta"][str(self.c22.pk)]
        self.assertIsNone(meta_c22["fiyatlar"]["USD"])            # tanımsız PB -> None

    def test_cari_meta_pb_ve_iskonto_dogru(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        meta = r.context["cari_meta"][str(self.cari.pk)]
        self.assertEqual(meta["pb"], "USD")
        self.assertEqual(meta["iskonto"], 10.0)

    def _post_govde(self, **over):
        govde = {
            "cari": self.cari.pk, "tarih": "2026-09-07", "para_birimi": "TRY",
            "teslim_sekli": "Nakliye Dahil", "odeme_kosulu": "%50 peşin + %50 sevkiyatta",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.a21.pk, "form-0-dahil": "on",
            "form-0-iskonto_yuzdesi": "10", "form-0-birim_fiyat": "12.000",
            "form-1-stok": self.c22.pk, "form-1-dahil": "on",
            "form-1-iskonto_yuzdesi": "0", "form-1-birim_fiyat": "15.000",
        }
        govde.update(over)
        return govde

    def test_post_miktar_hep_1_ve_iskonto_tutara_yansir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(ts.teslim_sekli, "Nakliye Dahil")         # aciklama gibi TR büyük harfe çevrilmez
        self.assertEqual(ts.odeme_kosulu, "%50 peşin + %50 sevkiyatta")
        kalemler = {k.stok_id: k for k in ts.kalemler.filter(silindi=False)}
        self.assertEqual(len(kalemler), 2)
        a21_kalem = kalemler[self.a21.pk]
        self.assertEqual(a21_kalem.miktar, Decimal("1"))
        self.assertEqual(a21_kalem.iskonto_yuzdesi, Decimal("10"))
        self.assertEqual(a21_kalem.tutar, Decimal("10800.00"))   # 12000 * 0,90

    def test_dahil_isaretsiz_satir_kaydedilmez(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]                                # c22 dahil değil
        r = self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        kalemler = list(ts.kalemler.filter(silindi=False))
        self.assertEqual(len(kalemler), 1)
        self.assertEqual(kalemler[0].stok_id, self.a21.pk)

    def test_hicbiri_dahil_degilse_hata_verir_kaydetmez(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-0-dahil"]
        del govde["form-1-dahil"]
        r = self.client.post(reverse("core:satis_teklif_ekle"), govde)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "En az bir ürün teklife dahil edilmelidir")
        self.assertFalse(TeklifSiparis.objects.filter(belge_tur="TEKLIF", yon="SATIS").exists())

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:satis_teklif_ekle")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde()).status_code, 403)

    def test_duzenle_get_mevcut_degerleri_getirir(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]                                 # c22 ilk kayıtta dahil değil
        self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        r = self.client.get(reverse("core:satis_teklif_duzenle", args=[ts.pk]))
        self.assertEqual(r.status_code, 200)
        formset = r.context["formset"]
        durum = {f.initial["stok"]: f.initial["dahil"] for f in formset.forms}
        self.assertTrue(durum[self.a21.pk])
        self.assertFalse(durum[self.c22.pk])                      # ilk kayıtta dahil değildi
        iskonto = {f.initial["stok"]: f.initial["iskonto_yuzdesi"] for f in formset.forms}
        self.assertEqual(iskonto[self.a21.pk], Decimal("10"))

    def test_duzenle_post_gunceller(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]
        self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 1)
        r = self.client.post(reverse("core:satis_teklif_duzenle", args=[ts.pk]),
                             self._post_govde())                  # şimdi ikisi de dahil
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts.refresh_from_db()
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 2)

    def test_teklif_siparis_duzenle_eski_url_satis_teklifini_yonlendirir(self):
        """Veri kaybı koruması: eski paylaşımlı teklif_siparis_duzenle URL'ine doğrudan
        gidilirse, bu ekranın bilmediği iskonto/teslim/ödeme alanları sessizce
        sıfırlanmasın diye satis_teklif_duzenle'a yönlendirilir."""
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk]))
        self.assertRedirects(r, reverse("core:satis_teklif_duzenle", args=[ts.pk]))

    def test_detay_ve_pdf_fiyat_karti_gorunur_toplam_gizlenir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "Ürün Fiyat Listesi")
        self.assertContains(d, "Net Fiyat")
        self.assertContains(d, "Nakliye Dahil")
        self.assertNotContains(d, "Ödenecek")
        pdf = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
