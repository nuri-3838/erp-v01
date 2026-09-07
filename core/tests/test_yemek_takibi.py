"""DİĞER > Yemek Takibi testleri: servis (benzersizlik/doğrulama/aylık özet), view
(cari filtresi + CRUD), yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, YemekSayimi
from core.services.cari import cari_olustur
from core.services.yemek_takibi import (
    YemekTakibiHatasi, aktif_kayitlar, aylik_ozet, kayit_ekle, kayit_guncelle, kayit_sil,
    son_birim_fiyat,
)


def _cari(unvan="yemek firması"):
    return cari_olustur(unvan=unvan, para_birimi="TRY")


class YemekTakibiServisTest(TestCase):
    def test_kayit_ekle(self):
        c = _cari()
        k = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=45,
                       birim_fiyat="120,50")
        self.assertEqual((k.kisi_sayisi, k.birim_fiyat), (45, Decimal("120.50")))
        self.assertEqual(k.tutar, Decimal("5422.50"))

    def test_ayni_gun_ikinci_kayit_reddedilir(self):
        c = _cari()
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=45, birim_fiyat=100)
        with self.assertRaises(YemekTakibiHatasi):
            kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=50, birim_fiyat=100)

    def test_farkli_cari_ayni_gun_izinli(self):
        c1, c2 = _cari("a"), _cari("b")
        kayit_ekle(cari=c1, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        k2 = kayit_ekle(cari=c2, tarih=datetime.date(2026, 9, 1), kisi_sayisi=20, birim_fiyat=100)
        self.assertEqual(k2.kisi_sayisi, 20)

    def test_negatif_kisi_sayisi_reddedilir(self):
        c = _cari()
        with self.assertRaises(YemekTakibiHatasi):
            kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=-1, birim_fiyat=100)

    def test_negatif_birim_fiyat_reddedilir(self):
        c = _cari()
        with self.assertRaises(YemekTakibiHatasi):
            kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=-5)

    def test_kayit_guncelle(self):
        c = _cari()
        k = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=45, birim_fiyat=100)
        k2 = kayit_guncelle(k, tarih=datetime.date(2026, 9, 2), kisi_sayisi=50, birim_fiyat=110)
        self.assertEqual((k2.tarih, k2.kisi_sayisi, k2.birim_fiyat),
                         (datetime.date(2026, 9, 2), 50, Decimal("110")))

    def test_guncellemede_baska_kayitla_cakisirsa_reddedilir(self):
        c = _cari()
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        k2 = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 2), kisi_sayisi=20, birim_fiyat=100)
        with self.assertRaises(YemekTakibiHatasi):
            kayit_guncelle(k2, tarih=datetime.date(2026, 9, 1), kisi_sayisi=20, birim_fiyat=100)

    def test_kayit_sil_soft_delete(self):
        c = _cari()
        k = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        kayit_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertEqual(list(aktif_kayitlar(cari=c)), [])

    def test_silinen_gunun_tarihi_tekrar_kullanilabilir(self):
        c = _cari()
        k = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        kayit_sil(k)
        k2 = kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=15, birim_fiyat=100)
        self.assertEqual(k2.kisi_sayisi, 15)

    def test_son_birim_fiyat(self):
        c = _cari()
        self.assertIsNone(son_birim_fiyat(c))
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 3), kisi_sayisi=10, birim_fiyat=130)
        self.assertEqual(son_birim_fiyat(c), Decimal("130"))

    def test_aylik_ozet(self):
        c = _cari()
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10, birim_fiyat=100)
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 2), kisi_sayisi=20, birim_fiyat=100)
        gun, kisi, tutar = aylik_ozet(aktif_kayitlar(cari=c))
        self.assertEqual((gun, kisi, tutar), (2, 30, Decimal("3000")))

    def test_aktif_kayitlar_tarih_araligi_filtreler(self):
        c = _cari()
        kayit_ekle(cari=c, tarih=datetime.date(2026, 8, 31), kisi_sayisi=1, birim_fiyat=100)
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 1), kisi_sayisi=2, birim_fiyat=100)
        kayit_ekle(cari=c, tarih=datetime.date(2026, 9, 30), kisi_sayisi=3, birim_fiyat=100)
        eylul = list(aktif_kayitlar(
            cari=c, baslangic=datetime.date(2026, 9, 1), bitis=datetime.date(2026, 9, 30)))
        self.assertEqual(len(eylul), 2)


class YemekTakibiViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="yemek_takibi")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.cari = _cari("acme yemek")

    def test_liste_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:yemek_takibi")).status_code, 403)

    def test_liste_200(self):
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(reverse("core:yemek_takibi")).status_code, 200)

    def test_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yemek_sayimi_ekle"), {
            "cari": self.cari.pk, "tarih": "2026-09-01", "kisi_sayisi": "45",
            "birim_fiyat": "120,50", "notlar": ""})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(YemekSayimi.objects.filter(
            cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=45).exists())

    def test_ekle_get_cari_parametresiyle_onceki_fiyati_doldurur(self):
        kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                  birim_fiyat=150)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_sayimi_ekle"), {"cari": self.cari.pk})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["form"].initial.get("birim_fiyat"), Decimal("150"))

    def test_ayni_gun_ikinci_ekleme_form_hatasi_verir(self):
        kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                  birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yemek_sayimi_ekle"), {
            "cari": self.cari.pk, "tarih": "2026-09-01", "kisi_sayisi": "20",
            "birim_fiyat": "100", "notlar": ""})
        self.assertEqual(r.status_code, 200)  # formda kalır
        self.assertEqual(YemekSayimi.objects.filter(cari=self.cari).count(), 1)

    def test_duzenle_post(self):
        k = kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                       birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yemek_sayimi_duzenle", args=[k.pk]), {
            "cari": self.cari.pk, "tarih": "2026-09-01", "kisi_sayisi": "12",
            "birim_fiyat": "100", "notlar": "düzeltildi"})
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertEqual((k.kisi_sayisi, k.notlar), (12, "düzeltildi"))

    def test_sil_post(self):
        k = kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                       birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yemek_sayimi_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_liste_cari_filtresi(self):
        c2 = _cari("baska firma")
        kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                  birim_fiyat=100)
        kayit_ekle(cari=c2, tarih=datetime.date(2026, 9, 1), kisi_sayisi=20, birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi"), {
            "cari": self.cari.pk, "baslangic": "2026-09-01", "bitis": "2026-09-30"})
        self.assertContains(r, "ACME YEMEK")
        self.assertNotContains(r, "BASKA FIRMA")

    def test_yalniz_cari_parametresiyle_gelince_tarih_araligina_gore_de_filtreler(self):
        # Kayıt eklendikten sonra yönlendirilen "?cari=X" (tarih aralığı YOK) — form
        # tarihsiz olduğu için invalid olur; bu durumda cari filtresi SESSİZCE atılmamalı.
        c2 = self.cari
        eski = kayit_ekle(cari=c2, tarih=datetime.date(2020, 1, 1), kisi_sayisi=5,
                          birim_fiyat=100)          # bu-ay aralığının dışında
        guncel = kayit_ekle(cari=c2, tarih=datetime.date.today(), kisi_sayisi=9,
                            birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi"), {"cari": c2.pk})
        self.assertEqual(r.context["cari"], c2)
        pks = {k.pk for k in r.context["kayitlar"]}
        self.assertIn(guncel.pk, pks)
        self.assertNotIn(eski.pk, pks)             # bu-ay varsayılanı dışında kaldı

    def test_menude_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi"))
        self.assertContains(r, "Diğer")
        self.assertContains(r, "Yemek Takibi")

    def test_pdf_gercek_pdf_uretir(self):
        kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=45,
                  birim_fiyat="120,50")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi_pdf"), {
            "cari": self.cari.pk, "baslangic": "2026-09-01", "bitis": "2026-09-30"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r.content[:5], b"%PDF-")

    def test_pdf_filtresiz_de_calisir_ve_carisiz_kolon_gosterir(self):
        c2 = _cari("baska firma")
        kayit_ekle(cari=self.cari, tarih=datetime.date(2026, 9, 1), kisi_sayisi=10,
                  birim_fiyat=100)
        kayit_ekle(cari=c2, tarih=datetime.date(2026, 9, 2), kisi_sayisi=20, birim_fiyat=100)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi_pdf"), {
            "baslangic": "2026-09-01", "bitis": "2026-09-30"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")

    def test_pdf_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:yemek_takibi_pdf")).status_code, 403)

    def test_liste_pdf_linki_filtreyi_tasir(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yemek_takibi"), {
            "cari": self.cari.pk, "baslangic": "2026-09-01", "bitis": "2026-09-30"})
        self.assertContains(r, f"/diger/yemek-takibi/pdf/?cari={self.cari.pk}")
