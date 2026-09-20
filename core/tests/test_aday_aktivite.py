"""Aday aktivite (görüşme/temas kaydı) + çoklu dosya eki testleri: CariAktivite ile birebir
aynı desen — servis, view, görsel küçültme/WebP (spec invariant'ı), PDF geçişi, geçersiz
dosya reddi, yetki."""
import io
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import AdayAktivite, AdayAktiviteEk, EkranYetki
from core.services.aday import (
    AdayHatasi, aday_aktivite_ek_ekle, aday_aktivite_ek_sil, aday_aktivite_ekle,
    aday_aktivite_guncelle, aday_aktivite_sil, aday_musteri_olustur, aktif_aday_aktiviteleri,
)


def _aday(unvan="test aday"):
    return aday_musteri_olustur(unvan=unvan, para_birimi="TRY")


def _png_dosya(ad="foto.png", boyut=(2400, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", boyut, "white").save(buf, "PNG")
    return SimpleUploadedFile(ad, buf.getvalue(), content_type="image/png")


def _pdf_dosya(ad="sozlesme.pdf"):
    return SimpleUploadedFile(ad, b"%PDF-1.4 sahte icerik", content_type="application/pdf")


class AdayAktiviteServisTest(TestCase):
    def test_aktivite_ekle(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.GORUSME,
                                 aciklama="fabrikada görüştük")
        self.assertEqual((akt.aday_id, akt.tur, akt.aciklama),
                         (a.pk, "GORUSME", "fabrikada görüştük"))

    def test_bos_aciklama_reddedilir(self):
        a = _aday()
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="  ")

    def test_gecersiz_tur_reddedilir(self):
        a = _aday()
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ekle(a, tarih="2026-09-01", tur="UYDURUK", aciklama="x")

    def test_aktivite_guncelle(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="ilk")
        akt2 = aday_aktivite_guncelle(akt, tarih="2026-09-02", tur=AdayAktivite.Tur.TELEFON,
                                      aciklama="güncel")
        akt2.refresh_from_db()
        self.assertEqual((akt2.tarih.isoformat(), akt2.tur, akt2.aciklama),
                         ("2026-09-02", "TELEFON", "güncel"))

    def test_silinmis_aktivite_duzenlenemez(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        aday_aktivite_sil(akt)
        with self.assertRaises(AdayHatasi):
            aday_aktivite_guncelle(akt, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                                   aciklama="y")

    def test_aktivite_sil(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        aday_aktivite_sil(akt)
        akt.refresh_from_db()
        self.assertTrue(akt.silindi)

    def test_aktif_aktiviteler_siralama_ve_filtre(self):
        a = _aday()
        aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="eski")
        yeni = aday_aktivite_ekle(a, tarih="2026-09-03", tur=AdayAktivite.Tur.NOT, aciklama="yeni")
        silinen = aday_aktivite_ekle(a, tarih="2026-09-02", tur=AdayAktivite.Tur.NOT, aciklama="sil")
        aday_aktivite_sil(silinen)
        liste = list(aktif_aday_aktiviteleri(a))
        self.assertEqual(len(liste), 2)
        self.assertEqual(liste[0].pk, yeni.pk)              # en yeni tarih önce


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IK_OZEL_DIR=tempfile.mkdtemp())
class AdayAktiviteEkServisTest(TestCase):
    def test_resim_webpye_kuculur(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        ek = aday_aktivite_ek_ekle(akt, dosya=_png_dosya())
        self.assertTrue(ek.dosya.name.endswith(".webp"))
        self.assertEqual(ek.orijinal_ad, "foto.png")
        self.assertTrue(ek.resim_mi)
        im = Image.open(ek.dosya.path)
        self.assertLessEqual(max(im.size), 1600)

    def test_pdf_oldugu_gibi_saklanir(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        ek = aday_aktivite_ek_ekle(akt, dosya=_pdf_dosya())
        self.assertTrue(ek.dosya.name.endswith(".pdf"))
        self.assertFalse(ek.resim_mi)
        self.assertEqual(ek.orijinal_ad, "sozlesme.pdf")

    def test_desteklenmeyen_uzanti_reddedilir(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        kotu = SimpleUploadedFile("virus.exe", b"x", content_type="application/octet-stream")
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ek_ekle(akt, dosya=kotu)

    def test_buyuk_dosya_reddedilir(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        buyuk = SimpleUploadedFile("buyuk.pdf", b"0" * (11 * 1024 * 1024),
                                   content_type="application/pdf")
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ek_ekle(akt, dosya=buyuk)

    def test_ek_sil(self):
        a = _aday()
        akt = aday_aktivite_ekle(a, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT, aciklama="x")
        ek = aday_aktivite_ek_ekle(akt, dosya=_pdf_dosya())
        aday_aktivite_ek_sil(ek)
        ek.refresh_from_db()
        self.assertTrue(ek.silindi)
        self.assertEqual(list(aktif_aday_aktiviteleri(a))[0].ekler.count(), 0)   # soft-delete filtrelenir


class AdayAktiviteViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("advaktyet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="aday_musteriler")
        cls.bos = User.objects.create_user("advaktbos", password="x")
        cls.aday = _aday("formal")

    def test_detay_sayfasi_aktiviteler_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertContains(r, "Aktiviteler")

    def test_aktivite_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_aktivite_ekle", args=[self.aday.pk]), {
            "tarih": "2026-09-01", "tur": "GORUSME", "aciklama": "fabrikada görüştük"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AdayAktivite.objects.filter(
            aday=self.aday, aciklama="fabrikada görüştük").exists())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IK_OZEL_DIR=tempfile.mkdtemp())
    def test_aktivite_ekle_coklu_dosyayla(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_aktivite_ekle", args=[self.aday.pk]), {
            "tarih": "2026-09-01", "tur": "TOPLANTI", "aciklama": "notlar",
            "dosyalar": [_png_dosya("a.png"), _pdf_dosya("b.pdf")],
        })
        self.assertEqual(r.status_code, 302)
        akt = AdayAktivite.objects.get(aday=self.aday, aciklama="notlar")
        self.assertEqual(AdayAktiviteEk.objects.filter(aktivite=akt, silindi=False).count(), 2)

    def test_aktivite_duzenle_post(self):
        akt = aday_aktivite_ekle(self.aday, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                                 aciklama="ilk")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_aktivite_duzenle", args=[akt.pk]), {
            "tarih": "2026-09-02", "tur": "EPOSTA", "aciklama": "güncel"})
        self.assertEqual(r.status_code, 302)
        akt.refresh_from_db()
        self.assertEqual((akt.tur, akt.aciklama), ("EPOSTA", "güncel"))

    def test_aktivite_sil_post(self):
        akt = aday_aktivite_ekle(self.aday, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                                 aciklama="x")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_aktivite_sil", args=[akt.pk]))
        self.assertEqual(r.status_code, 302)
        akt.refresh_from_db()
        self.assertTrue(akt.silindi)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IK_OZEL_DIR=tempfile.mkdtemp())
    def test_aktivite_ek_sil_post(self):
        akt = aday_aktivite_ekle(self.aday, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                                 aciklama="x")
        ek = aday_aktivite_ek_ekle(akt, dosya=_pdf_dosya())
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_aktivite_ek_sil", args=[ek.pk]))
        self.assertRedirects(r, reverse("core:aday_aktivite_duzenle", args=[akt.pk]))
        ek.refresh_from_db()
        self.assertTrue(ek.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(
                reverse("core:aday_aktivite_ekle", args=[self.aday.pk])).status_code, 403)
