"""Fatura ekleri (PDF/resim): özel depoda UUID adlı, yetkili görünümle sunulur; geçersiz/büyük
dosya reddedilir; soft-delete; ek eklemek muhasebeyi etkilemez."""
import datetime
import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from core.models import (Cari, EkranYetki, Fatura, FaturaEk, FaturaTipi, HesapPlani, KdvOrani,
                         Kur, YevmiyeSatir)
from core.services import fatura_ek as ek_servis
from core.services.fatura import fatura_olustur
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, bmp_bayt, png_bayt, yuklenen

D = datetime.date


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO",
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class FaturaEkTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        Kur.objects.create(tarih=D(2026, 1, 1), usd_alis=Decimal("40"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "A", kalem="KVYK")
        _hesap("770.01", "GENEL GİDER")
        kdv = KdvOrani.objects.create(
            aciklama="G", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        tip = FaturaTipi.objects.create(ad="GİDER", yon="ALIS", gider=True)
        cari = Cari.objects.create(kod="320-10-0001", unvan="A", para_birimi="TRY",
                                   muhasebe_kodu="320.10.0001")
        self.u = User.objects.create_superuser("yon", password="x")
        self.fatura = fatura_olustur(
            tip_id=tip.pk, cari_id=cari.pk, tarih=D(2026, 1, 1), fatura_no="EK-1",
            kullanici=self.u,
            satirlar=[{"hesap_id": "770.01", "miktar": "1", "birim_fiyat": "100",
                       "kdv_id": kdv.pk}])

    def _satir_sayisi(self):
        return YevmiyeSatir.objects.filter(fis=self.fatura.fis).count()

    def test_pdf_ve_resim_ozel_depoda_uuid_adli(self):
        pdf = ek_servis.ek_ekle(self.fatura, dosya=yuklenen("Fatura A.pdf", PDF_BAYT),
                                kullanici=self.u)
        png = ek_servis.ek_ekle(self.fatura, dosya=yuklenen("foto.png", png_bayt()),
                                kullanici=self.u)
        self.assertTrue(re.match(r"^fatura_ek/[0-9a-f]{32}\.pdf$", pdf.dosya.name))
        self.assertTrue(re.match(r"^fatura_ek/[0-9a-f]{32}\.webp$", png.dosya.name))   # WebP'ye
        self.assertEqual(pdf.orijinal_ad, "Fatura A.pdf")
        self.assertTrue((self.ozel / pdf.dosya.name).exists())
        with self.assertRaises(ValueError):
            pdf.dosya.url                                  # özel depo URL üretmez

    def test_buyuk_resim_2500_pxe_kadar_korunur(self):
        from PIL import Image
        ek = ek_servis.ek_ekle(
            self.fatura, dosya=yuklenen("makbuz.png", png_bayt(boyut=(3200, 1800))))
        with Image.open(self.ozel / ek.dosya.name) as im:
            self.assertEqual(max(im.size), 2500)
        kucuk = ek_servis.ek_ekle(
            self.fatura, dosya=yuklenen("k.png", png_bayt(boyut=(2000, 1000))))
        with Image.open(self.ozel / kucuk.dosya.name) as im:
            self.assertEqual(im.size, (2000, 1000))               # küçük resim büyütülmez

    def test_gecersiz_ve_buyuk_dosya_reddedilir(self):
        for ad, bayt in (("x.exe", b"MZ"), ("sahte.pdf", b"merhaba"), ("kotu.png", b"degil"),
                         ("a.bmp", bmp_bayt())):
            with self.assertRaises(ek_servis.FaturaEkHatasi, msg=ad):
                ek_servis.ek_ekle(self.fatura, dosya=yuklenen(ad, bayt))
        with self.assertRaises(ek_servis.FaturaEkHatasi):
            ek_servis.ek_ekle(self.fatura,
                              dosya=yuklenen("b.pdf", PDF_BAYT + b"0" * (11 * 1024 * 1024)))
        self.assertEqual(FaturaEk.objects.count(), 0)

    def test_ek_muhasebeyi_etkilemez_ve_silme_soft(self):
        once = self._satir_sayisi()
        ek = ek_servis.ek_ekle(self.fatura, dosya=yuklenen("a.pdf", PDF_BAYT), kullanici=self.u)
        self.assertEqual(self._satir_sayisi(), once)
        ek_servis.ek_sil(ek, kullanici=self.u)
        ek.refresh_from_db()
        self.assertTrue(ek.silindi)
        self.assertTrue((self.ozel / ek.dosya.name).exists())      # dosya diskte kalır
        self.assertEqual(ek_servis.ek_listele(self.fatura).count(), 0)

    def test_gorunumler_yetki_yukleme_indirme_silme(self):
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="alis_faturalari")
        self.client.force_login(self.u)
        r = self.client.post(
            reverse("core:fatura_ek_ekle", args=[self.fatura.pk]),
            {"dosyalar": [yuklenen("a.pdf", PDF_BAYT), yuklenen("b.png", png_bayt())]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(FaturaEk.objects.filter(fatura=self.fatura).count(), 2)
        ek = FaturaEk.objects.filter(dosya__endswith=".pdf").get()
        r = self.client.get(reverse("core:fatura_ek_indir", args=[ek.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertIn("no-store", r["Cache-Control"])
        detay = self.client.get(reverse("core:fatura_detay", args=[self.fatura.pk]))
        self.assertContains(detay, "a.pdf")
        self.client.post(reverse("core:fatura_ek_sil", args=[ek.pk]))
        self.assertEqual(
            self.client.get(reverse("core:fatura_ek_indir", args=[ek.pk])).status_code, 404)

    def test_yetkisiz_kullanici_ve_anonim_indiremez(self):
        ek = ek_servis.ek_ekle(self.fatura, dosya=yuklenen("a.pdf", PDF_BAYT))
        url = reverse("core:fatura_ek_indir", args=[ek.pk])
        self.assertEqual(self.client.get(url).status_code, 302)           # girişe
        diger = User.objects.create_user("diger", password="x")
        self.client.force_login(diger)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_fatura_silinince_ek_kayitlari_de_gider(self):
        from core.services.fatura import fatura_sil
        ek_servis.ek_ekle(self.fatura, dosya=yuklenen("a.pdf", PDF_BAYT))
        fatura_sil(self.fatura, kullanici=self.u)
        self.assertEqual(FaturaEk.objects.count(), 0)
        self.assertFalse(Fatura.objects.filter(pk=self.fatura.pk).exists())
