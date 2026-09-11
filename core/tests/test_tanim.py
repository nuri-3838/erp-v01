"""Tanım listeleri (AYARLAR) testleri: KDV + Tevkifat oranları servis CRUD + view + yetki."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, KdvOrani, TanimSecenegi, TevkifatOrani
from core.services.tanim import (TanimHatasi, kdv_orani_olustur, kdv_orani_guncelle,
                                  secenek_guncelle, secenek_olustur, secenek_sil,
                                  tevkifat_orani_olustur, tevkifat_orani_guncelle)


def _hesap(kod="191", ad="İNDİRİLECEK KDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV")


class KdvOraniServisTest(TestCase):
    def test_olustur(self):
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV")
        k = kdv_orani_olustur(aciklama="genel oran", oran="20", sira=10,
                              hesap_borc_kodu="191", hesap_alacak_kodu="391")
        self.assertEqual((k.aciklama, k.oran, k.sira, k.hesap_borc_id, k.hesap_alacak_id),
                         ("GENEL ORAN", Decimal("20.00"), 10, "191", "391"))

    def test_aciklama_zorunlu(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="  ", oran="20")

    def test_oran_negatif_red(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="x", oran="-1")

    def test_hesap_yok_red(self):
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="x", oran="20", hesap_borc_kodu="999")

    def test_hesapsiz_serbest(self):
        k = kdv_orani_olustur(aciklama="x", oran="10")
        self.assertIsNone(k.hesap_borc_id)
        self.assertIsNone(k.hesap_alacak_id)

    def test_guncelle(self):
        k = kdv_orani_olustur(aciklama="x", oran="10")
        kdv_orani_guncelle(k, aciklama="indirimli", oran="10", sira=5,
                           hesap_borc_kodu="", hesap_alacak_kodu="")
        k.refresh_from_db()
        self.assertEqual((k.aciklama, k.sira), ("İNDİRİMLİ", 5))

    def test_oran_tr_format(self):
        # #8: servis TR biçimli string'i de tek parser ile çözer (virgül = ondalık)
        k = kdv_orani_olustur(aciklama="ondalik", oran="8,5")
        self.assertEqual(k.oran, Decimal("8.5"))

    def test_oran_benzersiz(self):
        kdv_orani_olustur(aciklama="genel", oran="20")
        with self.assertRaises(TanimHatasi):
            kdv_orani_olustur(aciklama="ikinci yirmi", oran="20")

    def test_oran_benzersiz_guncellemede(self):
        kdv_orani_olustur(aciklama="genel", oran="20")
        k = kdv_orani_olustur(aciklama="indirimli", oran="10")
        with self.assertRaises(TanimHatasi):
            kdv_orani_guncelle(k, aciklama="indirimli", oran="20")
        # kendi oranını koruyarak güncelleme serbest
        kdv_orani_guncelle(k, aciklama="indirimli2", oran="10")
        k.refresh_from_db()
        self.assertEqual(k.aciklama, "İNDİRİMLİ2")


class TevkifatOraniServisTest(TestCase):
    def test_olustur(self):
        t = tevkifat_orani_olustur(kod="601", pay=5, payda=10, aciklama="yarım")
        self.assertEqual((t.kod, t.pay, t.payda, t.aciklama), ("601", 5, 10, "YARIM"))

    def test_kod_benzersiz(self):
        tevkifat_orani_olustur(kod="601", pay=5, payda=10)
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="601", pay=7, payda=10)

    def test_payda_pozitif(self):
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="x", pay=5, payda=0)

    def test_kod_zorunlu(self):
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_olustur(kod="  ", pay=5, payda=10)


class TanimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")

    def test_hub_ve_listeler(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:tanim_listeleri"))
        self.assertContains(r, "KDV Oranları")
        self.assertContains(r, "Tevkifat Oranları")
        self.assertEqual(self.client.get(reverse("core:kdv_oranlari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:tevkifat_oranlari")).status_code, 200)

    def test_kdv_ekle_post(self):
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kdv_orani_ekle"),
                             {"sira": "10", "aciklama": "genel", "oran": "20",
                              "hesap_borc": "191", "hesap_alacak": "391"})
        self.assertEqual(r.status_code, 302)
        k = KdvOrani.objects.get(aciklama="GENEL")
        self.assertEqual((k.oran, k.hesap_borc_id, k.hesap_alacak_id),
                         (Decimal("20.00"), "191", "391"))

    def test_tevkifat_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:tevkifat_orani_ekle"),
                             {"kod": "601", "pay": "5", "payda": "10", "aciklama": "yarım"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(TevkifatOrani.objects.filter(kod="601").exists())

    def test_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:tanim_listeleri")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:kdv_oranlari")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:tevkifat_oranlari")).status_code, 403)


class TanimSecenegiTest(TestCase):
    """Yükleme Şekli / Ödeme Koşulu / Yükleme Tipi — tek model, kategoriye göre; seed
    migration ile örnek değerler gelir; Satış Teklifi'nde kullanılan seçenek silinemez."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsyon", password="x")
        cls.bos = User.objects.create_user("tsbos", password="x")

    def test_seed_uc_kategoriye_gelir(self):
        say = {k: TanimSecenegi.objects.filter(silindi=False, kategori=k).count()
               for k in ("YUKLEME_SEKLI", "ODEME_KOSULU", "YUKLEME_TIPI")}
        self.assertEqual(say, {"YUKLEME_SEKLI": 7, "ODEME_KOSULU": 7, "YUKLEME_TIPI": 3})
        self.assertEqual(
            set(TanimSecenegi.objects.filter(kategori="YUKLEME_TIPI").values_list("kod", flat=True)),
            {"20DC", "40HQ", "TIR"})

    def test_olustur_tr_buyuk_harf_ve_benzersiz(self):
        s = secenek_olustur("ODEME_KOSULU", ad="90 gün vade", sira=5)
        self.assertEqual((s.ad, s.kod, s.sira), ("90 GÜN VADE", "", 5))
        with self.assertRaises(TanimHatasi):
            secenek_olustur("ODEME_KOSULU", ad="90 GÜN VADE")        # aynı kategoride ad tekrar
        secenek_olustur("YUKLEME_SEKLI", ad="90 GÜN VADE")            # başka kategoride serbest

    def test_yukleme_tipinde_kod_zorunlu_ve_benzersiz(self):
        with self.assertRaises(TanimHatasi):
            secenek_olustur("YUKLEME_TIPI", ad="45 HQ")
        with self.assertRaises(TanimHatasi):
            secenek_olustur("YUKLEME_TIPI", ad="ikinci kırk", kod="40hq")   # seed'deki 40HQ
        s = secenek_olustur("YUKLEME_TIPI", ad="45' HQ", kod="45hq")
        self.assertEqual(s.kod, "45HQ")

    def test_ad_bos_ve_gecersiz_kategori_red(self):
        with self.assertRaises(TanimHatasi):
            secenek_olustur("ODEME_KOSULU", ad="  ")
        with self.assertRaises(TanimHatasi):
            secenek_olustur("YOK_BOYLE", ad="x")

    def test_guncelle(self):
        s = secenek_olustur("YUKLEME_SEKLI", ad="deneme")
        secenek_guncelle(s, ad="deneme 2", sira=3)
        s.refresh_from_db()
        self.assertEqual((s.ad, s.sira), ("DENEME 2", 3))

    def test_teklifte_kullanilan_secenek_silinemez(self):
        import datetime
        from core.models import Birim, Cari, Kategori, Stok
        from core.services.teklif_siparis import teklif_siparis_olustur
        _hesap("120.09", "MÜŞTERİ")
        cari = Cari.objects.create(kod="C9", unvan="MÜŞTERİ", muhasebe_kodu="120.09")
        kat = Kategori.objects.create(kod="K9", ad="GENEL")
        birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        stok = Stok.objects.create(kod="S9", ad="X", kategori=kat, uretim_birimi=birim,
                                   fatura_birimi=birim)
        kosul = TanimSecenegi.objects.get(kategori="ODEME_KOSULU", ad="PEŞİN")
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=cari.pk, tarih=datetime.date(2026, 9, 7),
            satirlar=[{"stok_id": stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            odeme_kosulu_id=kosul.pk)
        with self.assertRaises(TanimHatasi):
            secenek_sil(kosul)
        from core.services.teklif_siparis import teklif_siparis_iptal
        teklif_siparis_iptal(ts)
        secenek_sil(kosul)                                            # iptal edilince serbest
        self.assertTrue(TanimSecenegi.objects.get(pk=kosul.pk).silindi)

    def test_view_liste_ekle_duzenle_sil(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:tanim_listeleri"))
        for ad in ("Yükleme Şekli", "Ödeme Koşulları", "Yükleme Tipi", "Teslim Süresi"):
            self.assertContains(r, ad)
        for slug in ("yukleme-sekli", "odeme-kosulu", "yukleme-tipi", "teslim-suresi"):
            self.assertEqual(
                self.client.get(reverse("core:secenek_listesi", args=[slug])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("core:secenek_listesi", args=["yok-boyle"])).status_code, 404)
        r = self.client.post(reverse("core:secenek_ekle", args=["odeme-kosulu"]),
                             {"sira": "1", "ad": "120 gün vade"})
        self.assertEqual(r.status_code, 302)
        s = TanimSecenegi.objects.get(kategori="ODEME_KOSULU", ad="120 GÜN VADE")
        r = self.client.post(reverse("core:secenek_duzenle", args=["odeme-kosulu", s.pk]),
                             {"sira": "2", "ad": "150 gün vade"})
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertEqual(s.ad, "150 GÜN VADE")
        r = self.client.post(reverse("core:secenek_sil", args=["odeme-kosulu", s.pk]))
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertTrue(s.silindi)
        # Yükleme tipinde kod alanı var ve zorunlu
        r = self.client.post(reverse("core:secenek_ekle", args=["yukleme-tipi"]),
                             {"sira": "9", "ad": "45' HQ", "kod": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "kod zorunlu")
        # Yanlış kategorinin pk'sı başka slug'dan düzenlenemez (404)
        sekil = TanimSecenegi.objects.filter(kategori="YUKLEME_SEKLI").first()
        self.assertEqual(
            self.client.get(reverse("core:secenek_duzenle", args=["odeme-kosulu", sekil.pk])).status_code,
            404)

    def test_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:secenek_listesi", args=["yukleme-tipi"])).status_code, 403)
