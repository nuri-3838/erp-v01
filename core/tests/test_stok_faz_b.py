"""STOKLAR Faz B testleri: Depo CRUD + stok hareketleri (giriş/çıkış, eldeki miktar,
yetersiz stok kontrolü, depo bazında bakiye), servis + view + yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (Birim, Depo, Kategori, Stok, StokHareket, StokMaliyetKatmani,
                         StokMaliyetTuketimi)
from core.services import stok_maliyet
from core.services.depo import DepoHatasi, depo_olustur, depo_sil
from core.services.hareket import (HareketHatasi, depo_bazinda_eldeki, eldeki_miktar,
                                   hareket_ekle, hareket_sil, toplu_eldeki)

D = datetime.date


def _stok():
    ust = Kategori.objects.create(ad="HAMMADDE", kod="150")
    alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
    kg = Birim.objects.create(ad="KİLOGRAM", kisa_ad="KG", ondalik=3)
    return Stok.objects.create(kod="150-10-0001", ad="ALÜMİNYUM LEVHA", kategori=alt,
                               uretim_birimi=kg, fatura_birimi=kg)


class DepoServisTest(TestCase):
    def test_olustur_tr_buyuk(self):
        d = depo_olustur(kod="ana", ad="ana depo")
        self.assertEqual((d.kod, d.ad), ("ANA", "ANA DEPO"))

    def test_kod_benzersiz(self):
        depo_olustur(kod="01", ad="bir")
        with self.assertRaises(DepoHatasi):
            depo_olustur(kod="01", ad="iki")

    def test_sil_hareketsiz(self):
        d = depo_olustur(kod="01", ad="depo")
        depo_sil(d)
        d.refresh_from_db()
        self.assertTrue(d.silindi)

    def test_hareketli_depo_silinemez(self):
        d = depo_olustur(kod="01", ad="depo")
        s = _stok()
        hareket_ekle(stok_id=s.pk, depo_id=d.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="10")
        with self.assertRaises(DepoHatasi):
            depo_sil(d)


class HareketServisTest(TestCase):
    def setUp(self):
        self.s = _stok()
        self.d1 = depo_olustur(kod="01", ad="ANA")
        self.d2 = depo_olustur(kod="02", ad="ÜRETİM")

    def test_giris_cikis_eldeki(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="1.000")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="300")
        self.assertEqual(eldeki_miktar(self.s), Decimal("700.000"))
        self.assertEqual(eldeki_miktar(self.s, self.d1), Decimal("700.000"))

    def test_yetersiz_stok_cikis_red(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="100")
        with self.assertRaises(HareketHatasi):
            hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                         tur="CIKIS", miktar="150")

    def test_depo_bazinda(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="100")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d2.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="40")
        bazinda = dict((d.pk, m) for d, m in depo_bazinda_eldeki(self.s))
        self.assertEqual(bazinda[self.d1.pk], Decimal("100.000"))
        self.assertEqual(bazinda[self.d2.pk], Decimal("40.000"))
        self.assertEqual(eldeki_miktar(self.s), Decimal("140.000"))

    def test_toplu_eldeki_tum_depolarin_toplami(self):
        """Tek gruplu sorgu, eldeki_miktar(stok) ile birebir aynı sonucu verir; hareketsiz
        stok sözlükte yoktur; silinmiş (soft) hareket sayılmaz."""
        ikinci = Stok.objects.create(kod="150-10-0002", ad="İKİNCİ", kategori=self.s.kategori,
                                     uretim_birimi=self.s.uretim_birimi,
                                     fatura_birimi=self.s.fatura_birimi)
        hareketsiz = Stok.objects.create(kod="150-10-0003", ad="HAREKETSİZ",
                                         kategori=self.s.kategori,
                                         uretim_birimi=self.s.uretim_birimi,
                                         fatura_birimi=self.s.fatura_birimi)
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="100")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d2.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="40")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="30")
        hareket_ekle(stok_id=ikinci.pk, depo_id=self.d2.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="7")
        silinecek = hareket_ekle(stok_id=ikinci.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                                 tur="GIRIS", miktar="500")
        hareket_sil(silinecek)
        sonuc = toplu_eldeki([self.s.pk, ikinci.pk, hareketsiz.pk])
        self.assertEqual(sonuc[self.s.pk], Decimal("110.000"))       # 100 + 40 - 30
        self.assertEqual(sonuc[ikinci.pk], Decimal("7.000"))
        self.assertNotIn(hareketsiz.pk, sonuc)
        self.assertEqual(sonuc[self.s.pk], eldeki_miktar(self.s))

    def test_giris_silme_negatife_dusuremez(self):
        g = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                         tur="GIRIS", miktar="100")
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="80")
        with self.assertRaises(HareketHatasi):   # girişi silersek eldeki -? negatif
            hareket_sil(g)


class StokMaliyetTest(TestCase):
    """Hareketli ağırlıklı ortalama maliyet — hareket servisi düzeyinde temel davranışlar
    (ayrıntılı senaryolar: core/tests/test_stok_ortalama.py)."""

    @classmethod
    def setUpTestData(cls):
        cls.s = _stok()
        cls.d1 = depo_olustur(kod="D1", ad="DEPO 1")

    def test_giris_tutari_ve_birim_maliyet(self):
        g = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                         tur="GIRIS", miktar="10", giris_tutar_try=Decimal("125.00"))
        self.assertEqual((g.tutar_try, g.birim_maliyet_try, g.maliyet_durumu),
                         (Decimal("125.00"), Decimal("12.500000"), "KESIN"))

    def test_cikis_o_anki_ortalamayla_degerlenir(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="3", giris_tutar_try=Decimal("30.00"))
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="GIRIS", miktar="10", giris_tutar_try=Decimal("200.00"))
        c = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 3),
                         tur="CIKIS", miktar="5")
        self.assertEqual(c.tutar_try, Decimal("88.46"))   # 5 x (230 / 13)

    def test_fiyatsiz_giris_gecici_cikis_gecici(self):
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="5", giris_tutar_try=Decimal("50.00"))
        g2 = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                          tur="GIRIS", miktar="5")                  # fiyatsız
        c = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 3),
                         tur="CIKIS", miktar="8")
        self.assertEqual(g2.maliyet_durumu, "GECICI")
        durum = stok_maliyet.hareket_maliyet_durumu(c)
        self.assertEqual((durum["tutar_try"], durum["tam_mi"], durum["tahmini"]),
                         (Decimal("80.00"), True, True))

    def test_giris_silinince_ortalama_ve_cikis_yeniden_hesaplanir(self):
        g1 = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                          tur="GIRIS", miktar="10", giris_tutar_try=Decimal("100.00"))
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="GIRIS", miktar="10", giris_tutar_try=Decimal("300.00"))
        c = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 3),
                         tur="CIKIS", miktar="4")
        self.assertEqual(c.tutar_try, Decimal("80.00"))             # 4 x 20
        hareket_sil(g1)
        c.refresh_from_db()
        self.assertEqual(c.tutar_try, Decimal("120.00"))            # 4 x 30

    def test_giris_silme_negatif_eldekiyi_engeller(self):
        g = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                         tur="GIRIS", miktar="10", giris_tutar_try=Decimal("100.00"))
        hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 2),
                     tur="CIKIS", miktar="10")
        with self.assertRaises(HareketHatasi):
            hareket_sil(g)

    def test_giris_tahmini_bayragi_gecici_yapar(self):
        g = hareket_ekle(stok_id=self.s.pk, depo_id=self.d1.pk, tarih=D(2026, 6, 1),
                         tur="GIRIS", miktar="10", giris_tutar_try=Decimal("100.00"), tahmini=True)
        self.assertEqual(g.maliyet_durumu, "GECICI")


class FazBViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.s = _stok()

    def test_depo_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:depo_ekle"), {"kod": "01", "ad": "ana depo"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Depo.objects.filter(kod="01", ad="ANA DEPO").exists())

    def test_hareket_ekle_post_ve_detayda_eldeki(self):
        d = depo_olustur(kod="01", ad="ANA")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:stok_hareket_ekle", args=[self.s.pk]), {
            "depo": str(d.pk), "tur": "GIRIS", "miktar": "250", "tarih": "2026-06-01",
            "aciklama": "açılış"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(eldeki_miktar(self.s), Decimal("250.000"))
        det = self.client.get(reverse("core:stok_detay", args=[self.s.pk]))
        self.assertContains(det, "250,000")

    def test_stok_listesi_grup_yerine_tum_depolarin_mevcut_miktarini_gosterir(self):
        d1 = depo_olustur(kod="01", ad="ANA")
        d2 = depo_olustur(kod="02", ad="ÜRETİM")
        hareket_ekle(stok_id=self.s.pk, depo_id=d1.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="250")
        hareket_ekle(stok_id=self.s.pk, depo_id=d2.pk, tarih=D(2026, 6, 1),
                     tur="GIRIS", miktar="40")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Mevcut Miktar")
        self.assertContains(r, "290,000")                    # 250 + 40, tüm depolar
        self.assertNotContains(r, ">Grup<")
        self.assertNotContains(r, "stk-grup-ikon")

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:depolar")).status_code, 403)
