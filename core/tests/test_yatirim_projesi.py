"""Yatırım Projesi (DURAN VARLIK FAZ 1) — model/servis: otomatik kod (YP-NNNN), CRUD,
toplam/fatura-sayısı hesabı; ekranlar: liste (+ inline ekle), detay, yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, Depo, EkranYetki, FaturaTipi, HesapPlani, KdvOrani, Kur, YatirimProjesi
from core.services.fatura import fatura_olustur
from core.services.yatirim_projesi import (
    YatirimProjesiHatasi, aktif_projeler, proje_fatura_sayisi, proje_olustur, proje_toplami,
    sonraki_proje_kodu,
)

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=(grup == "BILANCO"))


class ProjeKodUretimiTest(TestCase):
    def test_ilk_kod_yp_0001(self):
        self.assertEqual(sonraki_proje_kodu(), "YP-0001")

    def test_sirali_kod_uretir(self):
        proje_olustur(ad="a")
        self.assertEqual(sonraki_proje_kodu(), "YP-0002")
        proje_olustur(ad="b")
        self.assertEqual(sonraki_proje_kodu(), "YP-0003")

    def test_bosluk_doldurur(self):
        p1 = proje_olustur(ad="a")
        proje_olustur(ad="b")
        p1.silindi = True
        p1.save(update_fields=["silindi"])
        # YP-0001 soft-delete edildi ama kod benzersizliği silindi=False koşullu olduğu
        # için (uq_yatirim_projesi_kod_aktif) boşluk yeniden kullanılabilir hale gelir.
        self.assertEqual(sonraki_proje_kodu(), "YP-0001")


class ProjeCrudTest(TestCase):
    def test_olustur_buyuk_harf_ve_kod(self):
        p = proje_olustur(ad="fabrika hava tesisatı", aciklama="not")
        self.assertEqual(p.kod, "YP-0001")
        self.assertEqual(p.ad, "FABRİKA HAVA TESİSATI")
        self.assertEqual(p.durum, YatirimProjesi.Durum.DEVAM)
        self.assertEqual(p.aciklama, "not")

    def test_bos_ad_reddedilir(self):
        with self.assertRaises(YatirimProjesiHatasi):
            proje_olustur(ad="  ")

    def test_aktif_projeler_silinmisi_haric_tutar(self):
        p1 = proje_olustur(ad="a")
        p2 = proje_olustur(ad="b")
        p2.silindi = True
        p2.save(update_fields=["silindi"])
        kodlar = set(aktif_projeler().values_list("pk", flat=True))
        self.assertIn(p1.pk, kodlar)
        self.assertNotIn(p2.pk, kodlar)


class ProjeToplamFaturaSayisiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        cls.kdv0 = KdvOrani.objects.create(aciklama="KDV YOK", oran=Decimal("0"))
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")

    def _fatura(self, proje, tutar="1000", no="G-1"):
        return fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no=no,
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": tutar,
                      "kdv_id": self.kdv20.pk, "yatirim_projesi_id": proje.pk}])

    def test_toplam_ve_fatura_sayisi_dogru(self):
        proje = proje_olustur(ad="proje x")
        self._fatura(proje, "1000", "G-1")
        self._fatura(proje, "500", "G-2")
        self.assertEqual(proje_toplami(proje), Decimal("1500.00"))
        self.assertEqual(proje_fatura_sayisi(proje), 2)

    def test_bos_proje_toplami_sifir(self):
        proje = proje_olustur(ad="boş proje")
        self.assertEqual(proje_toplami(proje), Decimal("0.00"))
        self.assertEqual(proje_fatura_sayisi(proje), 0)


class YatirimProjesiEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="yatirim_projeleri")
        cls.kisitli = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=cls.kisitli, ekran_kod="mizan")

    def test_yetkisiz_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:yatirim_projeleri")).status_code, 403)

    def test_liste_bos(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yatirim_projeleri"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Bu filtreyle eşleşen proje yok.")

    def test_ekle_view(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yatirim_projesi_ekle"),
                             {"ad": "fabrika hava tesisatı", "aciklama": "x"})
        self.assertEqual(r.status_code, 302)
        p = YatirimProjesi.objects.get(kod="YP-0001")
        self.assertEqual(p.ad, "FABRİKA HAVA TESİSATI")

    def test_liste_toplam_ve_fatura_sayisi_gosterir(self):
        p = proje_olustur(ad="proje x")
        fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv20.pk, "yatirim_projesi_id": p.pk}])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yatirim_projeleri"))
        self.assertContains(r, "YP-0001")
        self.assertContains(r, "1.000,00")   # toplam KDV hariç

    def test_detay_kalemleri_ve_toplami_gosterir(self):
        p = proje_olustur(ad="proje x")
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv20.pk, "yatirim_projesi_id": p.pk}])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[p.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TEDARİKÇİ A")
        self.assertContains(r, "G-1")
        self.assertContains(r, "1.000,00")
