"""Cari aktivite (görüşme/temas kaydı) + çoklu dosya eki testleri: servis, view,
görsel küçültme/WebP (spec invariant'ı), PDF geçişi, geçersiz dosya reddi, yetki."""
import io
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import CariAktivite, CariAktiviteEk, EkranYetki
from core.services.cari import (
    CariHatasi, aktif_aktiviteler, aktivite_ek_ekle, aktivite_ek_sil, aktivite_ekle,
    aktivite_guncelle, aktivite_sil, cari_olustur,
)


def _cari(unvan="test cari"):
    return cari_olustur(unvan=unvan, para_birimi="TRY")


def _png_dosya(ad="foto.png", boyut=(2400, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", boyut, "white").save(buf, "PNG")
    return SimpleUploadedFile(ad, buf.getvalue(), content_type="image/png")


def _pdf_dosya(ad="sozlesme.pdf"):
    return SimpleUploadedFile(ad, b"%PDF-1.4 sahte icerik", content_type="application/pdf")


class CariAktiviteServisTest(TestCase):
    def test_aktivite_ekle(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.GORUSME,
                          aciklama="fabrikada görüştük")
        self.assertEqual((a.cari_id, a.tur, a.aciklama), (c.pk, "GORUSME", "fabrikada görüştük"))

    def test_bos_aciklama_reddedilir(self):
        c = _cari()
        with self.assertRaises(CariHatasi):
            aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="  ")

    def test_gecersiz_tur_reddedilir(self):
        c = _cari()
        with self.assertRaises(CariHatasi):
            aktivite_ekle(c, tarih="2026-09-01", tur="UYDURUK", aciklama="x")

    def test_aktivite_guncelle(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="ilk")
        a2 = aktivite_guncelle(a, tarih="2026-09-02", tur=CariAktivite.Tur.TELEFON,
                               aciklama="güncel")
        a2.refresh_from_db()
        self.assertEqual((a2.tarih.isoformat(), a2.tur, a2.aciklama),
                         ("2026-09-02", "TELEFON", "güncel"))

    def test_silinmis_aktivite_duzenlenemez(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        aktivite_sil(a)
        with self.assertRaises(CariHatasi):
            aktivite_guncelle(a, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="y")

    def test_aktivite_sil(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        aktivite_sil(a)
        a.refresh_from_db()
        self.assertTrue(a.silindi)

    def test_aktif_aktiviteler_siralama_ve_filtre(self):
        c = _cari()
        aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="eski")
        yeni = aktivite_ekle(c, tarih="2026-09-03", tur=CariAktivite.Tur.NOT, aciklama="yeni")
        silinen = aktivite_ekle(c, tarih="2026-09-02", tur=CariAktivite.Tur.NOT, aciklama="sil")
        aktivite_sil(silinen)
        liste = list(aktif_aktiviteler(c))
        self.assertEqual(len(liste), 2)
        self.assertEqual(liste[0].pk, yeni.pk)              # en yeni tarih önce


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class CariAktiviteEkServisTest(TestCase):
    def test_resim_webpye_kuculur(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        ek = aktivite_ek_ekle(a, dosya=_png_dosya())
        self.assertTrue(ek.dosya.name.endswith(".webp"))
        self.assertEqual(ek.orijinal_ad, "foto.png")
        self.assertTrue(ek.resim_mi)
        im = Image.open(ek.dosya.path)
        self.assertLessEqual(max(im.size), 1600)

    def test_pdf_oldugu_gibi_saklanir(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        ek = aktivite_ek_ekle(a, dosya=_pdf_dosya())
        self.assertTrue(ek.dosya.name.endswith(".pdf"))
        self.assertFalse(ek.resim_mi)
        self.assertEqual(ek.orijinal_ad, "sozlesme.pdf")

    def test_desteklenmeyen_uzanti_reddedilir(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        kotu = SimpleUploadedFile("virus.exe", b"x", content_type="application/octet-stream")
        with self.assertRaises(CariHatasi):
            aktivite_ek_ekle(a, dosya=kotu)

    def test_buyuk_dosya_reddedilir(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        buyuk = SimpleUploadedFile("buyuk.pdf", b"0" * (11 * 1024 * 1024),
                                   content_type="application/pdf")
        with self.assertRaises(CariHatasi):
            aktivite_ek_ekle(a, dosya=buyuk)

    def test_ek_sil(self):
        c = _cari()
        a = aktivite_ekle(c, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        ek = aktivite_ek_ekle(a, dosya=_pdf_dosya())
        aktivite_ek_sil(ek)
        ek.refresh_from_db()
        self.assertTrue(ek.silindi)
        self.assertEqual(list(aktif_aktiviteler(c))[0].ekler.count(), 0)   # soft-delete filtrelenir


class CariAktiviteViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="cariler")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.cari = _cari("formal")

    def test_detay_sekmesi_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:cari_detay", args=[self.cari.pk]))
        self.assertContains(r, "Aktiviteler")

    def test_aktivite_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aktivite_ekle", args=[self.cari.pk]), {
            "tarih": "2026-09-01", "tur": "GORUSME", "aciklama": "fabrikada görüştük"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CariAktivite.objects.filter(
            cari=self.cari, aciklama="fabrikada görüştük").exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_aktivite_ekle_coklu_dosyayla(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aktivite_ekle", args=[self.cari.pk]), {
            "tarih": "2026-09-01", "tur": "TOPLANTI", "aciklama": "notlar",
            "dosyalar": [_png_dosya("a.png"), _pdf_dosya("b.pdf")],
        })
        self.assertEqual(r.status_code, 302)
        a = CariAktivite.objects.get(cari=self.cari, aciklama="notlar")
        self.assertEqual(CariAktiviteEk.objects.filter(aktivite=a, silindi=False).count(), 2)

    def test_aktivite_duzenle_post(self):
        a = aktivite_ekle(self.cari, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="ilk")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aktivite_duzenle", args=[a.pk]), {
            "tarih": "2026-09-02", "tur": "EPOSTA", "aciklama": "güncel"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.tur, a.aciklama), ("EPOSTA", "güncel"))

    def test_aktivite_sil_post(self):
        a = aktivite_ekle(self.cari, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aktivite_sil", args=[a.pk]))
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.silindi)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_aktivite_ek_sil_post(self):
        a = aktivite_ekle(self.cari, tarih="2026-09-01", tur=CariAktivite.Tur.NOT, aciklama="x")
        ek = aktivite_ek_ekle(a, dosya=_pdf_dosya())
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aktivite_ek_sil", args=[ek.pk]))
        self.assertRedirects(r, reverse("core:aktivite_duzenle", args=[a.pk]))
        ek.refresh_from_db()
        self.assertTrue(ek.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:aktivite_ekle", args=[self.cari.pk])).status_code, 403)
