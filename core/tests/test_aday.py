"""Aday Müşteri (CRM) testleri: servis CRUD + kategori + aktivite + Cariye dönüştürme +
view/yetki."""
import datetime
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    AdayMusteri, AdayMusteriKategori, Cari, CariKategori, EkranYetki, Sehir, TanimSecenegi,
    Ulke,
)
from core.services.aday import (
    AdayHatasi, aday_aktivite_ek_ekle, aday_aktivite_ekle, aday_aktivite_guncelle,
    aday_aktivite_sil, aday_cariye_donustur, aday_musteri_guncelle, aday_musteri_olustur,
    aday_musteri_sil, aktif_aday_aktiviteleri, aktif_aday_musteriler,
)
from core.services.aday_kategori import (
    AdayKategoriHatasi, aday_kategori_guncelle, aday_kategori_olustur, aday_kategori_sil,
)


class AdayMusteriKategoriServisTest(TestCase):
    def test_olustur_tr_buyuk_harf_ve_benzersiz(self):
        k = aday_kategori_olustur(ad="sıcak aday", kod="scr")
        self.assertEqual((k.ad, k.kod, k.ust_id), ("SICAK ADAY", "SCR", None))
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_olustur(ad="SICAK ADAY", kod="baska")
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_olustur(ad="başka", kod="SCR")

    def test_ad_bos_red(self):
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_olustur(ad="  ", kod="x")

    def test_alt_kategori_ve_kod_yolu(self):
        ust = aday_kategori_olustur(ad="kurumsal", kod="KRM")
        alt = aday_kategori_olustur(ad="inşaat", kod="INS", ust_id=ust.pk)
        self.assertEqual(alt.kod_yolu, "KRM-INS")
        self.assertEqual(ust.kod_yolu, "KRM")

    def test_ust_ustune_acilamaz(self):
        ust = aday_kategori_olustur(ad="kurumsal", kod="KRM")
        alt = aday_kategori_olustur(ad="inşaat", kod="INS", ust_id=ust.pk)
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_olustur(ad="daha alt", kod="X", ust_id=alt.pk)

    def test_guncelle(self):
        k = aday_kategori_olustur(ad="eski", kod="ESK")
        aday_kategori_guncelle(k, ad="yeni", kod="YEN")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.kod), ("YENİ", "YEN"))

    def test_alt_kategorisi_olan_silinemez(self):
        ust = aday_kategori_olustur(ad="kurumsal", kod="KRM")
        aday_kategori_olustur(ad="inşaat", kod="INS", ust_id=ust.pk)
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_sil(ust)

    def test_bagli_aday_varsa_silinemez(self):
        k = aday_kategori_olustur(ad="kurumsal", kod="KRM")
        aday_musteri_olustur(unvan="x", kategori_id=k.pk)
        with self.assertRaises(AdayKategoriHatasi):
            aday_kategori_sil(k)

    def test_sil_soft_delete(self):
        k = aday_kategori_olustur(ad="silinecek", kod="SLN")
        aday_kategori_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)


class AdayMusteriServisTest(TestCase):
    def test_olustur_tr_buyuk_harf(self):
        a = aday_musteri_olustur(unvan="acme ltd", ilgili_kisi="ayşe yılmaz")
        self.assertEqual(a.unvan, "ACME LTD")
        self.assertEqual(a.ilgili_kisi, "AYŞE YILMAZ")
        self.assertEqual(a.para_birimi, "TRY")
        self.assertEqual(a.iskonto_yuzdesi, 0)

    def test_unvan_zorunlu(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="   ")

    def test_gecersiz_para_birimi_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", para_birimi="XYZ")

    def test_kategori_gecersizse_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", kategori_id=999999)

    def test_kategori_ile_olusturur(self):
        k = aday_kategori_olustur(ad="sıcak", kod="SIC")
        a = aday_musteri_olustur(unvan="x", kategori_id=k.pk)
        self.assertEqual(a.kategori_id, k.pk)

    def test_iskonto_negatif_red(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="x", iskonto_yuzdesi="-5")

    def test_guncelle(self):
        a = aday_musteri_olustur(unvan="x")
        aday_musteri_guncelle(a, unvan="y", iskonto_yuzdesi="10")
        a.refresh_from_db()
        self.assertEqual(a.unvan, "Y")
        self.assertEqual(a.iskonto_yuzdesi, 10)

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
            para_birimi="EUR", iskonto_yuzdesi="5")
        cari = aday_cariye_donustur(a)
        self.assertEqual(cari.unvan, "BETA GMBH")
        self.assertEqual(cari.telefon, "+491234")
        self.assertEqual(cari.para_birimi, "EUR")
        self.assertEqual(cari.iskonto_yuzdesi, 5)
        a.refresh_from_db()
        self.assertEqual(a.donusen_cari_id, cari.pk)
        self.assertFalse(a.silindi)   # aday kaydı silinmez, iz kalır

    def test_kategori_ile_donusturur(self):
        """kategori_id burada Cari'nin KENDİ kategorisi (CariKategori) - AdayMusteriKategori
        ile karışmaz, ayrı ağaçlardır."""
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

    def test_donusturulen_aday_aktif_listede_gorunmez(self):
        """Kullanıcı isteği: cariye dönüştürülen aday Aday Müşteriler listesinden çıkmalı —
        kaydın kendisi silinmez (bkz. test_donusturur_ve_iz_birakir), yalnız aktif liste
        queryset'inden (aktif_aday_musteriler) hariç tutulur."""
        a = aday_musteri_olustur(unvan="zeta gmbh")
        self.assertIn(a, aktif_aday_musteriler())
        aday_cariye_donustur(a)
        self.assertNotIn(a, aktif_aday_musteriler())

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_donusturur_aktiviteleri_cariye_kopyalar(self):
        """Kullanıcı isteği: 'Aktiviteleri gelmedi, onun gelmesi lazım' — adayın aktiviteleri
        (+ ekli dosyaları) yeni Cari'ye kopyalanmalı; aday tarafındaki aktiviteler de
        SİLİNMEZ (iz kalır)."""
        a = aday_musteri_olustur(unvan="eta gmbh")
        akt1 = aday_aktivite_ekle(a, tarih=datetime.date(2026, 9, 1), tur="TELEFON",
                                  aciklama="ilk arama")
        aday_aktivite_ekle(a, tarih=datetime.date(2026, 9, 5), tur="TOPLANTI",
                           aciklama="fabrikada görüştük")
        pdf = SimpleUploadedFile("sozlesme.pdf", b"%PDF-1.4 sahte icerik",
                                 content_type="application/pdf")
        aday_aktivite_ek_ekle(akt1, dosya=pdf)

        cari = aday_cariye_donustur(a)

        cari_aktiviteler = list(cari.aktiviteler.filter(silindi=False).order_by("tarih"))
        self.assertEqual(len(cari_aktiviteler), 2)
        self.assertEqual(
            [(k.tarih, k.tur, k.aciklama) for k in cari_aktiviteler],
            [(datetime.date(2026, 9, 1), "TELEFON", "ilk arama"),
             (datetime.date(2026, 9, 5), "TOPLANTI", "fabrikada görüştük")])
        telefon = cari_aktiviteler[0]
        ekler = list(telefon.ekler.filter(silindi=False))
        self.assertEqual(len(ekler), 1)
        self.assertEqual(ekler[0].orijinal_ad, "sozlesme.pdf")
        self.assertTrue(ekler[0].dosya.name.endswith(".pdf"))
        # aday tarafındaki aktiviteler SİLİNMEZ — iz kalır
        self.assertEqual(aktif_aday_aktiviteleri(a).count(), 2)


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
        aday_musteri_olustur(unvan="boyçelik")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "ACME LTD")
        self.assertContains(r, "BOYÇELİK")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"ara": "acme"})
        self.assertContains(r2, "ACME LTD")
        self.assertNotContains(r2, "BOYÇELİK")

    def test_liste_kategori_filtresi(self):
        k1 = aday_kategori_olustur(ad="sıcak", kod="SIC")
        k2 = aday_kategori_olustur(ad="soğuk", kod="SOG")
        aday_musteri_olustur(unvan="sıcak aday", kategori_id=k1.pk)
        aday_musteri_olustur(unvan="soğuk aday", kategori_id=k2.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"kategori": k1.pk})
        self.assertContains(r, "SICAK ADAY")
        self.assertNotContains(r, "SOĞUK ADAY")

    def test_liste_sehir_ve_ulke_filtresi(self):
        tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
        de = Ulke.objects.create(kod="DE", ad="ALMANYA")
        kayseri = Sehir.objects.create(ulke=tr, ad="KAYSERİ", kod="38")
        aday_musteri_olustur(unvan="yerli aday", ulke_id=tr.pk, sehir_id=kayseri.pk)
        aday_musteri_olustur(unvan="yabanci aday", ulke_id=de.pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"ulke": tr.pk})
        self.assertContains(r, "YERLİ ADAY")
        self.assertNotContains(r, "YABANCI ADAY")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"sehir": kayseri.pk})
        self.assertContains(r2, "YERLİ ADAY")
        self.assertNotContains(r2, "YABANCI ADAY")
        # filtre seçenekleri yalnız fiilen kullanılan ülke/şehirlerden oluşmalı
        self.assertContains(r, "TÜRKİYE")
        self.assertContains(r, "ALMANYA")

    def test_liste_iletisim_ve_islem_kolonlari_kaldirildi(self):
        """Kullanıcı isteği: liste ekranından İletişim ve İşlem (Düzenle/Sil) kolonları
        kaldırıldı - detay sayfası üzerinden erişilir."""
        a = aday_musteri_olustur(unvan="test aday", telefon="05551112233",
                                 eposta="test@example.com")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertNotContains(r, "05551112233")
        self.assertNotContains(r, "test@example.com")
        self.assertNotContains(r, reverse("core:aday_musteri_duzenle", args=[a.pk]))
        self.assertNotContains(r, reverse("core:aday_musteri_sil", args=[a.pk]))
        self.assertContains(r, reverse("core:aday_musteri_detay", args=[a.pk]))

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:aday_musteri_ekle"), {
            "unvan": "yeni aday", "para_birimi": "TRY", "iskonto_yuzdesi": "0"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AdayMusteri.objects.filter(unvan="YENİ ADAY").exists())

    def test_unvansiz_ekle_hata_doner(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_musteri_ekle"),
                             {"para_birimi": "TRY", "iskonto_yuzdesi": "0"})
        self.assertEqual(r.status_code, 200)

    def test_duzenle_post(self):
        a = aday_musteri_olustur(unvan="eski ad")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_musteri_duzenle", args=[a.pk]), {
            "unvan": "yeni ad", "para_birimi": "USD", "iskonto_yuzdesi": "7,5"})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.unvan, a.para_birimi, a.iskonto_yuzdesi),
                         ("YENİ AD", "USD", 7.5))

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


class AdayMusteriKategoriViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("advkyon", password="x")
        cls.bos = User.objects.create_user("advkbos", password="x")

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:aday_kategoriler")).status_code, 403)

    def test_liste_ekle_duzenle_sil(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:aday_kategori_ekle"),
                             {"ad": "kurumsal", "kod": "krm"})
        self.assertEqual(r.status_code, 302)
        k = AdayMusteriKategori.objects.get(kod="KRM")
        r2 = self.client.get(reverse("core:aday_kategoriler"))
        self.assertContains(r2, "KURUMSAL")
        # alt kategori ekle (ust, +Alt formundaki gizli alandan POST edilir - GET query
        # string'i yalnız GET isteğinde okunur, bkz. aday_kategori_ekle view'ı)
        r3 = self.client.post(
            reverse("core:aday_kategori_ekle"),
            {"ad": "inşaat", "kod": "nsa", "ust": k.pk})
        self.assertEqual(r3.status_code, 302)
        alt = AdayMusteriKategori.objects.get(kod="NSA")
        self.assertEqual(alt.ust_id, k.pk)
        r4 = self.client.post(reverse("core:aday_kategori_duzenle", args=[k.pk]),
                              {"ad": "kurumsal 2", "kod": "krm"})
        self.assertEqual(r4.status_code, 302)
        k.refresh_from_db()
        self.assertEqual(k.ad, "KURUMSAL 2")
        r5 = self.client.post(reverse("core:aday_kategori_sil", args=[alt.pk]))
        self.assertEqual(r5.status_code, 302)
        alt.refresh_from_db()
        self.assertTrue(alt.silindi)

    def test_tanim_listeleri_aday_kaynagi_karti_kaldirildi(self):
        """Aday Kaynağı kavramı (Tanım Listesi) kullanıcı isteğiyle kaldırıldı; artık ne
        kartı ne de kategorisi kalmalı."""
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:tanim_listeleri"))
        self.assertNotContains(r, "Aday Kaynağı")
        self.assertEqual(
            self.client.get(reverse("core:secenek_listesi", args=["aday-kaynagi"])).status_code,
            404)
        self.assertNotIn("ADAY_KAYNAGI", dict(TanimSecenegi.Kategori.choices))
