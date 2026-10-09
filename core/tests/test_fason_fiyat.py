"""FASON > Fason Fiyatları: parça adedi başına fiyat listesi; verilen tarihte geçerli fiyat seçimi (en son başlangıç ≤ tarih, aktif)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, EkranYetki, FasonFiyat, Kategori, Stok
from core.services import fason as fs

D = Decimal


class FiyatBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("ff_y", password="x")
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")
        cls.cari2 = Cari.objects.create(kod="320-10-0002", unvan="DİĞER FASON", muhasebe_kodu="320.10.0002")
        birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        kat = Kategori.objects.create(kod="FF", ad="FASON TEST")
        cls.parca = Stok.objects.create(kod="151-10-0001", ad="PARÇA 1", kategori=kat, uretim_birimi=birim, fatura_birimi=birim, uretim_urunu=True)
        cls.sag = Stok.objects.create(kod="151-10-0013", ad="SAĞ", kategori=kat, uretim_birimi=birim, fatura_birimi=birim, uretim_urunu=True)
        cls.hammadde = Stok.objects.create(kod="150-10-0001", ad="PROFIL", kategori=kat, uretim_birimi=birim, fatura_birimi=birim,
                                           uretim_urunu=False, satinalma_urunu=True)

    def fiyat(self, tarih, tutar, cari=None, stok=None, pb="TRY", **kw):
        return fs.fiyat_olustur(cari_id=(cari or self.cari).pk, stok_id=(stok or self.parca).pk, birim_fiyat=D(str(tutar)), para_birimi=pb,
                                gecerlilik_baslangic=tarih, **kw)


class GecerliFiyatTest(FiyatBase):
    def test_tarihe_gore_en_son_baslangic(self):
        self.fiyat(date(2026, 1, 1), "20")
        self.fiyat(date(2026, 6, 1), "25.04")
        self.fiyat(date(2026, 12, 1), "30")                                    # ileri tarihli
        g = lambda t: fs.gecerli_fiyat(self.cari, self.parca, t)
        self.assertIsNone(g(date(2025, 12, 31)))                               # hiçbir fiyat henüz geçerli değil
        self.assertEqual(g(date(2026, 1, 1)).birim_fiyat, D("20"))
        self.assertEqual(g(date(2026, 5, 31)).birim_fiyat, D("20"))
        self.assertEqual(g(date(2026, 6, 1)).birim_fiyat, D("25.04"))
        self.assertEqual(g(date(2026, 11, 30)).birim_fiyat, D("25.04"))
        self.assertEqual(g(date(2027, 1, 1)).birim_fiyat, D("30"))

    def test_pasif_ve_silinen_atlanir(self):
        eski = self.fiyat(date(2026, 1, 1), "20")
        yeni = self.fiyat(date(2026, 6, 1), "25", aktif=False)
        self.assertEqual(fs.gecerli_fiyat(self.cari, self.parca, date(2026, 7, 1)).pk, eski.pk)
        fs.fiyat_sil(eski)
        self.assertIsNone(fs.gecerli_fiyat(self.cari, self.parca, date(2026, 7, 1)))
        self.assertEqual(yeni.pk, FasonFiyat.objects.get(aktif=False).pk)

    def test_cari_ve_stok_bazinda_ayri(self):
        self.fiyat(date(2026, 1, 1), "11.38", stok=self.sag)
        self.fiyat(date(2026, 1, 1), "25.04")
        self.fiyat(date(2026, 1, 1), "99", cari=self.cari2)
        self.assertEqual(fs.gecerli_fiyat(self.cari, self.sag, date(2026, 2, 1)).birim_fiyat, D("11.38"))
        self.assertEqual(fs.gecerli_fiyat(self.cari2, self.parca, date(2026, 2, 1)).birim_fiyat, D("99"))
        self.assertIsNone(fs.gecerli_fiyat(self.cari2, self.sag, date(2026, 2, 1)))
        self.assertEqual(fs.gecerli_fiyat(self.cari.pk, self.parca.pk, date(2026, 2, 1)).birim_fiyat, D("25.04"))   # pk ile de çağrılır

    def test_para_birimi_ve_fasoncu_kodu(self):
        f = self.fiyat(date(2026, 1, 1), "1.5", pb="USD", fasoncu_kodu=" GZ-P-00041 ")
        self.assertEqual((f.para_birimi, f.fasoncu_kodu), ("USD", "GZ-P-00041"))


class FiyatServisTest(FiyatBase):
    def test_ayni_baslangic_tekrar_edilemez(self):
        self.fiyat(date(2026, 1, 1), "20")
        with self.assertRaises(fs.FasonHatasi):
            self.fiyat(date(2026, 1, 1), "21")
        self.fiyat(date(2026, 1, 2), "21")                                      # farklı tarih serbest

    def test_dogrulamalar(self):
        for kw in (dict(birim_fiyat=D("-1")), dict(birim_fiyat="abc"), dict(para_birimi="XYZ"), dict(stok_id=self.hammadde.pk),
                   dict(cari_id=999999), dict(stok_id=999999)):
            ortak = dict(cari_id=self.cari.pk, stok_id=self.parca.pk, birim_fiyat=D("1"), para_birimi="TRY", gecerlilik_baslangic=date(2026, 1, 1))
            ortak.update(kw)
            with self.assertRaises(fs.FasonHatasi, msg=str(kw)):
                fs.fiyat_olustur(**ortak)

    def test_guncelle_ve_cakisma(self):
        a = self.fiyat(date(2026, 1, 1), "20")
        self.fiyat(date(2026, 2, 1), "22")
        fs.fiyat_guncelle(a, cari_id=self.cari.pk, stok_id=self.parca.pk, birim_fiyat=D("21"), para_birimi="TRY",
                          gecerlilik_baslangic=date(2026, 1, 1))
        a.refresh_from_db()
        self.assertEqual(a.birim_fiyat, D("21"))
        with self.assertRaises(fs.FasonHatasi):
            fs.fiyat_guncelle(a, cari_id=self.cari.pk, stok_id=self.parca.pk, birim_fiyat=D("21"), para_birimi="TRY",
                              gecerlilik_baslangic=date(2026, 2, 1))


class FiyatEkranTest(FiyatBase):
    def setUp(self):
        self.client.force_login(self.yon)

    def test_liste_ekle_duzenle_sil(self):
        r = self.client.get(reverse("core:fason_fiyatlari"))
        self.assertContains(r, "Henüz fason fiyatı yok")
        r = self.client.post(reverse("core:fason_fiyat_ekle"), {
            "cari": self.cari.pk, "stok": self.parca.pk, "fasoncu_kodu": "GZ-P-00041", "birim_fiyat": "25,04", "para_birimi": "TRY",
            "gecerlilik_baslangic": "2026-01-01", "aktif": "on"})
        self.assertRedirects(r, reverse("core:fason_fiyatlari"))
        f = FasonFiyat.objects.get()
        self.assertEqual((f.birim_fiyat, f.fasoncu_kodu), (D("25.04"), "GZ-P-00041"))
        r = self.client.get(reverse("core:fason_fiyatlari"))
        self.assertContains(r, "GZ-P-00041")
        self.assertContains(r, "25,0400 TRY")
        r = self.client.post(reverse("core:fason_fiyat_duzenle", args=[f.pk]), {
            "cari": self.cari.pk, "stok": self.parca.pk, "fasoncu_kodu": "", "birim_fiyat": "26", "para_birimi": "USD",
            "gecerlilik_baslangic": "2026-01-01"})
        self.assertRedirects(r, reverse("core:fason_fiyatlari"))
        f.refresh_from_db()
        self.assertEqual((f.birim_fiyat, f.para_birimi, f.aktif), (D("26"), "USD", False))
        self.client.post(reverse("core:fason_fiyat_sil", args=[f.pk]))
        f.refresh_from_db()
        self.assertTrue(f.silindi)

    def test_hatali_form(self):
        r = self.client.post(reverse("core:fason_fiyat_ekle"), {
            "cari": self.cari.pk, "stok": self.hammadde.pk, "birim_fiyat": "-5", "para_birimi": "TRY", "gecerlilik_baslangic": "2026-01-01"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(FasonFiyat.objects.count(), 0)

    def test_ekran_yetkisi(self):
        u = User.objects.create_user("ff_u", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:fason_fiyatlari")).status_code, 403)
        EkranYetki.objects.create(kullanici=u, ekran_kod="fason_fiyatlari")
        self.assertEqual(self.client.get(reverse("core:fason_fiyatlari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:fason_fiyat_ekle")).status_code, 403)       # ekleme yönetici işi

    def test_menude_gorunur(self):
        self.assertContains(self.client.get(reverse("core:fason_fiyatlari")), "Fason Fiyatları")
