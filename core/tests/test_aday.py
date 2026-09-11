"""Aday Müşteri (CRM) testleri: servis CRUD + aktivite + Cariye dönüştürme + view/yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import AdayMusteri, Cari, CariKategori, EkranYetki, HesapPlani, TanimSecenegi
from core.services.aday import (
    AdayHatasi, aday_aktivite_ekle, aday_aktivite_guncelle, aday_aktivite_sil,
    aday_cariye_donustur, aday_musteri_guncelle, aday_musteri_olustur, aday_musteri_sil,
    aktif_aday_aktiviteleri, aktif_aday_musteriler,
)


def _kaynak(ad="REFERANS"):
    return TanimSecenegi.objects.filter(
        kategori="ADAY_KAYNAGI", ad=ad, silindi=False).first()


class AdayMusteriServisTest(TestCase):
    def test_olustur_tr_buyuk_harf(self):
        a = aday_musteri_olustur(unvan="acme ltd", ilgili_kisi="ayşe yılmaz")
        self.assertEqual(a.unvan, "ACME LTD")
        self.assertEqual(a.ilgili_kisi, "AYŞE YILMAZ")
        self.assertEqual(a.asama, AdayMusteri.Asama.YENI)
        self.assertEqual(a.para_birimi, "TRY")

    def test_unvan_zorunlu(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="   ")

    def test_gecersiz_asama_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", asama="YOK_BOYLE")

    def test_gecersiz_para_birimi_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", para_birimi="XYZ")

    def test_kaynak_baska_kategoriden_olamaz(self):
        # Teslim Süresi kategorisinden bir pk, Aday Kaynağı için geçersiz olmalı.
        yanlis = TanimSecenegi.objects.filter(kategori="TESLIM_SURESI", silindi=False).first()
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", kaynak_id=yanlis.pk if yanlis else 999999)

    def test_kaynak_dogru_kategoriden_kabul(self):
        kaynak = _kaynak()
        a = aday_musteri_olustur(unvan="x", kaynak_id=kaynak.pk)
        self.assertEqual(a.kaynak_id, kaynak.pk)

    def test_tahmini_deger_negatif_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", tahmini_deger="-5")

    def test_tahmini_deger_bos_none_kalir(self):
        a = aday_musteri_olustur(unvan="x")
        self.assertIsNone(a.tahmini_deger)

    def test_guncelle(self):
        a = aday_musteri_olustur(unvan="x")
        aday_musteri_guncelle(a, unvan="y", asama=AdayMusteri.Asama.ILETISIMDE)
        a.refresh_from_db()
        self.assertEqual(a.unvan, "Y")
        self.assertEqual(a.asama, AdayMusteri.Asama.ILETISIMDE)

    def test_silinmis_guncellenemez(self):
        a = aday_musteri_olustur(unvan="x")
        aday_musteri_sil(a)
        with self.assertRaises(AdayHatasi):
            aday_musteri_guncelle(a, unvan="y")

    def test_sil_soft_delete(self):
        a = aday_musteri_olustur(unvan="x")
        aday_musteri_sil(a)
        a.refresh_from_db()
        self.assertTrue(a.silindi)
        self.assertNotIn(a, aktif_aday_musteriler())


class AdayCariyeDonusturTest(TestCase):
    def test_donusturur_ve_iz_birakir(self):
        a = aday_musteri_olustur(
            unvan="beta gmbh", ilgili_kisi="hans", telefon="+491234", eposta="hans@beta.de",
            para_birimi="EUR", notlar="ilk temas iyi geçti")
        cari = aday_cariye_donustur(a)
        self.assertEqual(cari.unvan, "BETA GMBH")
        self.assertEqual(cari.telefon, "+491234")
        self.assertEqual(cari.para_birimi, "EUR")
        a.refresh_from_db()
        self.assertEqual(a.donusen_cari_id, cari.pk)
        self.assertEqual(a.asama, AdayMusteri.Asama.KAZANILDI)
        self.assertFalse(a.silindi)   # aday kaydı silinmez, iz kalır

    def test_kategori_ile_donusturur(self):
        ust = CariKategori.objects.create(ad="MÜŞTERİLER", kod="120")
        alt = CariKategori.objects.create(ad="YURTİÇİ", kod="10", ust=ust)
        a = aday_musteri_olustur(unvan="gamma")
        cari = aday_cariye_donustur(a, kategori_id=alt.pk)
        self.assertEqual(cari.kategori_id, alt.pk)
        self.assertTrue(cari.kod.startswith("120-10-"))

    def test_iki_kez_donusturulemez(self):
        a = aday_musteri_olustur(unvan="delta")
        aday_cariye_donustur(a)
        with self.assertRaises(AdayHatasi):
            aday_cariye_donustur(a)

    def test_silinmis_aday_donusturulemez(self):
        a = aday_musteri_olustur(unvan="epsilon")
        aday_musteri_sil(a)
        with self.assertRaises(AdayHatasi):
            aday_cariye_donustur(a)


class AdayAktiviteServisTest(TestCase):
    def setUp(self):
        self.aday = aday_musteri_olustur(unvan="zeta")

    def test_ekle_ve_listele(self):
        aday_aktivite_ekle(self.aday, tarih=datetime.date(2026, 9, 1), tur="TELEFON",
                           aciklama="ilk arama")
        self.assertEqual(aktif_aday_aktiviteleri(self.aday).count(), 1)

    def test_aciklama_bos_red(self):
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ekle(self.aday, tarih=datetime.date(2026, 9, 1), tur="NOT",
                              aciklama="   ")

    def test_gecersiz_tur_red(self):
        with self.assertRaises(AdayHatasi):
            aday_aktivite_ekle(self.aday, tarih=datetime.date(2026, 9, 1), tur="YOK_BOYLE",
                              aciklama="x")

    def test_guncelle_ve_sil(self):
        akt = aday_aktivite_ekle(self.aday, tarih=datetime.date(2026, 9, 1), tur="NOT",
                                 aciklama="ilk not")
        aday_aktivite_guncelle(akt, tarih=datetime.date(2026, 9, 2), tur="TOPLANTI",
                               aciklama="güncellendi")
        akt.refresh_from_db()
        self.assertEqual((akt.tur, akt.aciklama), ("TOPLANTI", "güncellendi"))
        aday_aktivite_sil(akt)
        self.assertEqual(aktif_aday_aktiviteleri(self.aday).count(), 0)


class AdayMusteriViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("advyon", password="x")
        cls.yetkili = User.objects.create_user("advyet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="aday_musteriler")
        cls.bos = User.objects.create_user("advbos", password="x")

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:aday_musteriler")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:aday_musteri_ekle")).status_code, 403)

    def test_liste_ve_ara(self):
        aday_musteri_olustur(unvan="acme ltd")
        aday_musteri_olustur(unvan="boyçelik", asama=AdayMusteri.Asama.ILETISIMDE)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "ACME LTD")
        self.assertContains(r, "BOYÇELİK")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"ara": "acme"})
        self.assertContains(r2, "ACME LTD")
        self.assertNotContains(r2, "BOYÇELİK")

    def test_asama_sekme_sayaci_dogru(self):
        """Regresyon: _ts_liste'deki JOIN-şişme hatasının bir benzeri burada da oluşmasın —
        sekme sayaçları gerçek kayıt sayısını göstermeli."""
        aday_musteri_olustur(unvan="a1")
        aday_musteri_olustur(unvan="a2")
        aday_musteri_olustur(unvan="a3", asama=AdayMusteri.Asama.ILETISIMDE)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "Tümü <span class=\"n\">3</span>")
        self.assertContains(r, "Yeni <span class=\"n\">2</span>")
        self.assertContains(r, "İletişimde <span class=\"n\">1</span>")

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_musteri_ekle"), {
            "unvan": "yeni aday", "asama": "YENI", "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AdayMusteri.objects.filter(unvan="YENİ ADAY").exists())

    def test_unvansiz_ekle_hata_doner(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_musteri_ekle"),
                             {"asama": "YENI", "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 200)

    def test_duzenle_post(self):
        a = aday_musteri_olustur(unvan="eski ad")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_musteri_duzenle", args=[a.pk]), {
            "unvan": "yeni ad", "asama": "MUZAKERE", "para_birimi": "USD"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.unvan, a.asama, a.para_birimi), ("YENİ AD", "MUZAKERE", "USD"))

    def test_detay_ve_sil(self):
        a = aday_musteri_olustur(unvan="silinecek")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[a.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SİLİNECEK")
        r2 = self.client.post(reverse("core:aday_musteri_sil", args=[a.pk]))
        self.assertEqual(r2.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.silindi)

    def test_cariye_donustur_view(self):
        a = aday_musteri_olustur(unvan="donusecek")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_cariye_donustur", args=[a.pk]))
        self.assertEqual(r.status_code, 200)
        r2 = self.client.post(reverse("core:aday_cariye_donustur", args=[a.pk]), {})
        self.assertEqual(r2.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.donusen_cari_id)
        cari = Cari.objects.get(pk=a.donusen_cari_id)
        self.assertEqual(cari.unvan, "DONUSECEK")
        # ikinci kez dönüştürme denemesi -> mevcut cariye yönlendirir, yeni Cari açmaz
        onceki_sayisi = Cari.objects.count()
        r3 = self.client.get(reverse("core:aday_cariye_donustur", args=[a.pk]))
        self.assertRedirects(r3, reverse("core:cari_detay", args=[cari.pk]))
        self.assertEqual(Cari.objects.count(), onceki_sayisi)

    def test_aktivite_ekle_duzenle_sil_view(self):
        a = aday_musteri_olustur(unvan="aktiviteli")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_aktivite_ekle", args=[a.pk]), {
            "tarih": "2026-09-11", "tur": "GORUSME", "aciklama": "toplantı yapıldı"})
        self.assertEqual(r.status_code, 302)
        akt = a.aktiviteler.get()
        r2 = self.client.get(reverse("core:aday_musteri_detay", args=[a.pk]))
        self.assertContains(r2, "toplantı yapıldı")
        r3 = self.client.post(reverse("core:aday_aktivite_duzenle", args=[akt.pk]), {
            "tarih": "2026-09-12", "tur": "NOT", "aciklama": "güncellendi"})
        self.assertEqual(r3.status_code, 302)
        r4 = self.client.post(reverse("core:aday_aktivite_sil", args=[akt.pk]))
        self.assertEqual(r4.status_code, 302)
        akt.refresh_from_db()
        self.assertTrue(akt.silindi)

    def test_tanim_listeleri_aday_kaynagi_karti(self):
        """Aday Kaynağı, Tanım Listeleri yönetim ekranında da erişilebilir olmalı (bkz.
        Teslim Süresi'nin unutulup sonra düzeltildiği hata — aynı yanlış tekrarlanmasın)."""
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:tanim_listeleri"))
        self.assertContains(r, "Aday Kaynağı")
        self.assertEqual(
            self.client.get(reverse("core:secenek_listesi", args=["aday-kaynagi"])).status_code,
            200)
