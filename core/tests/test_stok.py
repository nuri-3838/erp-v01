"""Stok kartı (STOKLAR Faz A — master) testleri: otomatik kod (ÜST-ALT-sıra),
TR büyük harf, ALT-kategori zorunlu, birim/çevirici doğrulama, KDV/tevkifat FK,
DB kısıtları, view + yetki. (Miktar/hareket bu fazda YOK.)"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, EkranYetki, KdvOrani, Stok, TevkifatOrani
from core.services.birim import birim_olustur
from core.services.kategori import kategori_olustur
from core.services.stok import (StokHatasi, sonraki_stok_kodu, stok_guncelle,
                                 stok_olustur, stok_sil)


def _veri():
    ust = kategori_olustur(ad="hammadde", kod="150")
    alt = kategori_olustur(ad="alüminyum", kod="10", ust_id=ust.pk)
    alt2 = kategori_olustur(ad="çelik", kod="20", ust_id=ust.pk)
    adet = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
    kg = birim_olustur(ad="kilogram", kisa_ad="kg", ondalik=3)
    return ust, alt, alt2, adet, kg


def _kdv(oran="20"):
    k, _ = KdvOrani.objects.get_or_create(
        oran=Decimal(oran), silindi=False, defaults={"aciklama": "ORAN"})
    return k


def _tevkifat(kod="7/10", pay=7, payda=10):
    t, _ = TevkifatOrani.objects.get_or_create(
        kod=kod, silindi=False, defaults={"pay": pay, "payda": payda})
    return t


def _cari(unvan="ACME METAL", kod="320-99-0001"):
    from core.models import Cari
    c, _ = Cari.objects.get_or_create(
        kod=kod, silindi=False, defaults={"unvan": unvan, "para_birimi": "TRY"})
    return c


class StokServisTest(TestCase):
    def _stok(self, alt, adet, kg, ad="alüminyum levha"):
        return stok_olustur(ad=ad, kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                            fatura_birimi_id=kg.pk, cevirici=Decimal("3"),
                            kdv_id=_kdv("20").pk)

    def test_kod_otomatik_sira(self):
        _, alt, alt2, adet, kg = _veri()
        s1 = self._stok(alt, adet, kg)
        s2 = self._stok(alt, adet, kg, ad="alüminyum boru")
        s3 = self._stok(alt2, adet, kg, ad="çelik sac")
        self.assertEqual(s1.kod, "150-10-0001")
        self.assertEqual(s2.kod, "150-10-0002")
        self.assertEqual(s3.kod, "150-20-0001")        # ayrı kategori, sıfırdan

    def test_sonraki_kod_fonksiyonu(self):
        _, alt, _, _, _ = _veri()
        self.assertEqual(sonraki_stok_kodu(alt), "150-10-0001")

    def test_tr_buyuk_harf(self):
        _, alt, _, adet, kg = _veri()
        self.assertEqual(self._stok(alt, adet, kg).ad, "ALÜMİNYUM LEVHA")

    def test_kdv_tevkifat_tedarikci_fk(self):
        _, alt, _, adet, kg = _veri()
        c = _cari()
        s = stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk, tevkifat_id=_tevkifat().pk,
                         kritik_stok=Decimal("100"), tedarikci_id=c.pk)
        self.assertEqual(s.kdv.oran, Decimal("20.00"))
        self.assertEqual((s.tevkifat.pay, s.tevkifat.payda), (7, 10))
        self.assertEqual(s.kritik_stok, Decimal("100.000"))
        self.assertEqual(s.tedarikci_id, c.pk)           # Cari FK

    def test_tedarikci_gecersiz_id_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         tedarikci_id=99999)

    def test_kdv_tevkifat_opsiyonel(self):
        _, alt, _, adet, kg = _veri()
        s = stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"))
        self.assertIsNone(s.kdv_id)
        self.assertIsNone(s.tevkifat_id)

    def test_kdv_gecersiz_id_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=99999)

    def test_kritik_negatif_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kritik_stok=Decimal("-1"))

    def test_ust_kategoriye_stok_red(self):
        ust, _, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=ust.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"))

    def test_cevirici_pozitif(self):
        _, alt, _, adet, kg = _veri()
        for kotu in (Decimal("0"), Decimal("-1")):
            with self.assertRaises(StokHatasi):
                stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                             fatura_birimi_id=kg.pk, cevirici=kotu)

    def test_birim_bulunamaz_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=99999,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"))

    def test_silinen_kod_tekrar_kullanilmaz(self):
        _, alt, _, adet, kg = _veri()
        self._stok(alt, adet, kg)                       # 0001
        s2 = self._stok(alt, adet, kg, ad="ikinci")     # 0002
        stok_sil(s2)
        s3 = self._stok(alt, adet, kg, ad="ucuncu")     # 0002 değil -> 0003
        self.assertEqual(s3.kod, "150-10-0003")

    def test_guncelle_kod_kategori_sabit(self):
        _, alt, _, adet, kg = _veri()
        s = self._stok(alt, adet, kg)
        eski_kod, eski_kat = s.kod, s.kategori_id
        stok_guncelle(s, ad="yeni ad", uretim_birimi_id=kg.pk,
                      fatura_birimi_id=adet.pk, cevirici=Decimal("2"),
                      kdv_id=_kdv("10").pk)
        s.refresh_from_db()
        self.assertEqual((s.kod, s.kategori_id), (eski_kod, eski_kat))   # sabit
        self.assertEqual((s.ad, s.uretim_birimi_id, s.kdv.oran),
                         ("YENİ AD", kg.pk, Decimal("10.00")))

    def test_sil_soft_delete(self):
        _, alt, _, adet, kg = _veri()
        s = self._stok(alt, adet, kg)
        stok_sil(s)
        s.refresh_from_db()
        self.assertTrue(s.silindi)
        with self.assertRaises(StokHatasi):
            stok_guncelle(s, ad="x", uretim_birimi_id=adet.pk,
                          fatura_birimi_id=kg.pk, cevirici=Decimal("1"))

    def test_db_cevirici_kisit(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-9001", ad="X", kategori=alt,
                                 uretim_birimi=adet, fatura_birimi=kg,
                                 cevirici=Decimal("0"))

    def test_db_kod_unique(self):
        _, alt, _, adet, kg = _veri()
        Stok.objects.create(kod="150-10-5000", ad="A", kategori=alt,
                            uretim_birimi=adet, fatura_birimi=kg)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-5000", ad="B", kategori=alt,
                                uretim_birimi=adet, fatura_birimi=kg)


class StokViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="stoklar")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.ust, cls.alt, cls.alt2, cls.adet, cls.kg = _veri()

    def test_liste_render_kdv_yok(self):
        stok_olustur(ad="alüminyum levha", kategori_id=self.alt.pk,
                     uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                     cevirici=Decimal("3"), kdv_id=_kdv("20").pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "150-10-0001")
        self.assertContains(r, "ALÜMİNYUM LEVHA")
        self.assertContains(r, "+ Yeni Stok")

    def test_ekle_post_otomatik_kod(self):
        k = _kdv("20")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "alüminyum levha", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "3", "kdv": str(k.pk)})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="ALÜMİNYUM LEVHA")
        self.assertEqual((s.kod, s.kategori_id, s.kdv_id),
                         ("150-10-0001", self.alt.pk, k.pk))

    def test_ekle_ust_kategori_secilemez(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "x", "kategori": str(self.ust.pk),      # üst kategori (formda yok)
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "1"})
        self.assertEqual(r.status_code, 200)              # formda kalır
        self.assertFalse(Stok.objects.filter(ad="X").exists())

    def test_duzenle_post(self):
        s = stok_olustur(ad="levha", kategori_id=self.alt.pk,
                         uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                         cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        k10 = _kdv("10")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_duzenle", args=[s.pk]), {
            "ad": "levha yeni", "uretim_birimi": str(self.kg.pk),
            "fatura_birimi": str(self.adet.pk), "cevirici": "2,5", "kdv": str(k10.pk)})
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertEqual((s.ad, s.kod, s.cevirici, s.kdv_id),
                         ("LEVHA YENİ", "150-10-0001", Decimal("2.500000"), k10.pk))

    def test_sil_post(self):
        s = stok_olustur(ad="levha", kategori_id=self.alt.pk,
                         uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                         cevirici=Decimal("1"))
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_sil", args=[s.pk]))
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertTrue(s.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:stoklar")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:stok_ekle")).status_code, 403)

    def test_kod_api(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:stok_kod_api"), {"kategori": self.alt.pk})
        self.assertEqual(r.json()["kod"], "150-10-0001")
        # üst kategori -> kod yok
        r2 = self.client.get(reverse("core:stok_kod_api"), {"kategori": self.ust.pk})
        self.assertIsNone(r2.json()["kod"])

    def _ornek_stok(self):
        return stok_olustur(ad="alüminyum levha", kategori_id=self.alt.pk,
                            uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                            cevirici=Decimal("3"), kdv_id=_kdv("20").pk)

    def test_detay_render(self):
        s = self._ornek_stok()
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "150-10-0001")
        self.assertContains(r, "ALÜMİNYUM LEVHA")
        self.assertContains(r, "Temel Bilgiler")
        self.assertContains(r, "Stok Hareketleri")
        self.assertContains(r, "Kayıt Bilgisi")
        self.assertContains(r, "ALÜMİNYUM")          # kategori adı

    def test_liste_detay_linki(self):
        s = self._ornek_stok()
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"))
        self.assertContains(r, reverse("core:stok_detay", args=[s.pk]))

    def test_detay_yetkisiz_403(self):
        s = self._ornek_stok()
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:stok_detay", args=[s.pk])).status_code, 403)
