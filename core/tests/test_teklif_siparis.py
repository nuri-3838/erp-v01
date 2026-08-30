"""Teklif & Sipariş — model + servis + view: liste/yeni/düzenle/iptal/görüntüle +
teklif→sipariş/sipariş→fatura dönüşümü + durum akışı (Taslak→Onaylı) + otomatik
(müteselsil) belge no + PDF."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, HesapPlani, KdvOrani, Kategori, Stok

EKRANLAR = ("satinalma_teklifleri", "satinalma_siparisleri",
            "satis_teklifleri", "satis_siparisleri")
EKLE_URL = {
    "satinalma_teklifleri": "satinalma_teklif_ekle",
    "satinalma_siparisleri": "satinalma_siparis_ekle",
    "satis_teklifleri": "satis_teklif_ekle",
    "satis_siparisleri": "satis_siparis_ekle",
}


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu=grup, rapor_kalemi=kalem, parasal=True)


class TeklifSiparisModelServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsmyon", password="x")
        _hesap("120.01", "MÜŞTERİ A")
        cls.cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="K1", ad="GENEL", created_by=cls.yon,
                                          updated_by=cls.yon)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(kod="S1", ad="ÜRÜN A", kategori=cls.kat, kdv=cls.kdv,
                                       uretim_birimi=cls.birim, fatura_birimi=cls.birim,
                                       created_by=cls.yon, updated_by=cls.yon)

    def test_olustur_yevmiye_ve_stok_hareketi_uretmez(self):
        import datetime
        from core.models import StokHareket, YevmiyeFisi
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "25,50"}],
            kullanici=self.yon)
        self.assertEqual(ts.belge_tur, "TEKLIF")
        self.assertEqual(ts.yon, "SATIS")
        self.assertEqual(ts.durum, "TASLAK")
        self.assertEqual(ts.kalemler.count(), 1)
        self.assertEqual(ts.ara_toplam, Decimal("255.00"))
        self.assertEqual(ts.kdv_toplam, Decimal("51.00"))
        self.assertEqual(ts.genel_toplam, Decimal("306.00"))
        self.assertFalse(YevmiyeFisi.objects.exists())          # yevmiye ÜRETMEZ
        self.assertFalse(StokHareket.objects.exists())           # stok hareketi YARATMAZ

    def test_cari_bulunamaz_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="SIPARIS", yon="ALIS", cari_id=999999,
                tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "1"}],
                kullanici=self.yon)

    def test_bos_satir_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
                tarih=datetime.date(2026, 6, 28), satirlar=[], kullanici=self.yon)

    def test_negatif_miktar_reddedilir(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
                tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "-1", "birim_fiyat": "10"}],
                kullanici=self.yon)

    def test_kdvsiz_stok_kdv_toplam_sifir(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        stok2 = Stok.objects.create(kod="S2", ad="ÜRÜN B (KDV YOK)", kategori=self.kat,
                                    uretim_birimi=self.birim, fatura_birimi=self.birim,
                                    created_by=self.yon, updated_by=self.yon)
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": stok2.pk, "miktar": "5", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.assertEqual(ts.kdv_toplam, Decimal("0.00"))
        self.assertEqual(ts.genel_toplam, Decimal("50.00"))

    def test_aktif_teklif_siparisler_belge_tur_yon_filtreler(self):
        import datetime
        from core.services.teklif_siparis import (aktif_teklif_siparisler,
                                                   teklif_siparis_olustur)
        teklif_siparis_olustur(belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
                               tarih=datetime.date(2026, 6, 28),
                               satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                         "birim_fiyat": "1"}], kullanici=self.yon)
        teklif_siparis_olustur(belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
                               tarih=datetime.date(2026, 6, 28),
                               satirlar=[{"stok_id": self.stok.pk, "miktar": "1",
                                         "birim_fiyat": "1"}], kullanici=self.yon)
        self.assertEqual(aktif_teklif_siparisler("TEKLIF", "SATIS").count(), 1)
        self.assertEqual(aktif_teklif_siparisler("SIPARIS", "SATIS").count(), 1)
        self.assertEqual(aktif_teklif_siparisler("TEKLIF", "ALIS").count(), 0)

    def test_guncelle_kalemleri_degistirir(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_guncelle, teklif_siparis_olustur
        stok2 = Stok.objects.create(kod="S9", ad="ÜRÜN GÜNCEL", kategori=self.kat, kdv=self.kdv,
                                    uretim_birimi=self.birim, fatura_birimi=self.birim,
                                    created_by=self.yon, updated_by=self.yon)
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        eski_belge_no = ts.belge_no
        eski_kalem_pk = ts.kalemler.get().pk
        teklif_siparis_guncelle(
            ts, cari_id=self.cari.pk, tarih=datetime.date(2026, 7, 1),
            satirlar=[{"stok_id": stok2.pk, "miktar": "3", "birim_fiyat": "20"}],
            kullanici=self.yon)
        ts.refresh_from_db()
        self.assertEqual(ts.belge_no, eski_belge_no)               # belge no SABİT (düzenlemede değişmez)
        self.assertEqual(ts.tarih, datetime.date(2026, 7, 1))
        self.assertEqual(ts.genel_toplam, Decimal("72.00"))       # 3x20=60 + %20 KDV=12
        aktif = list(ts.kalemler.filter(silindi=False))
        self.assertEqual(len(aktif), 1)
        self.assertEqual(aktif[0].stok_id, stok2.pk)
        from core.models import TeklifSiparisKalem
        self.assertTrue(TeklifSiparisKalem.objects.get(pk=eski_kalem_pk).silindi)  # eski soft-delete
        # belge_tur/yon SABİT kalır
        self.assertEqual(ts.belge_tur, "TEKLIF")
        self.assertEqual(ts.yon, "SATIS")

    def test_iptal_edilmis_duzenlenemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_guncelle,
                                                   teklif_siparis_iptal, teklif_siparis_olustur)
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        ts.refresh_from_db()
        self.assertTrue(ts.silindi)
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_guncelle(
                ts, cari_id=self.cari.pk, tarih=datetime.date(2026, 6, 28),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
                kullanici=self.yon)

    def test_iptal_tekrar_cagrilinca_sessiz(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_iptal, teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)   # ikinci çağrı hata vermez
        ts.refresh_from_db()
        self.assertTrue(ts.silindi)

    def test_teklifi_siparise_cevir_kalemler_kopyalanir(self):
        import datetime
        from core.services.teklif_siparis import (teklif_siparis_olustur, teklif_siparis_onayla,
                                                   teklifi_siparise_cevir)
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(t, kullanici=self.yon)
        siparis = teklifi_siparise_cevir(t, tarih=datetime.date(2026, 7, 1), kullanici=self.yon)
        self.assertEqual(siparis.belge_tur, "SIPARIS")
        self.assertEqual(siparis.yon, "SATIS")
        self.assertEqual(siparis.durum, "TASLAK")                  # yeni belge kendi onayından geçer
        self.assertEqual(siparis.cari_id, self.cari.pk)
        self.assertEqual(siparis.para_birimi, t.para_birimi)
        self.assertEqual(siparis.kaynak_teklif_id, t.pk)
        self.assertEqual(siparis.tarih, datetime.date(2026, 7, 1))
        self.assertEqual(siparis.genel_toplam, t.genel_toplam)
        self.assertEqual(siparis.kalemler.count(), 1)
        k = siparis.kalemler.get()
        self.assertEqual(k.stok_id, self.stok.pk)
        self.assertEqual(k.miktar, Decimal("4"))
        self.assertEqual(k.birim_fiyat, Decimal("10"))
        # belge_no otomatik üretilir, teklifinkinden bağımsız kendi numarası olur
        self.assertNotEqual(siparis.belge_no, t.belge_no)
        self.assertTrue(siparis.belge_no.startswith("SSS-2026-"))

    def test_taslak_teklif_siparise_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur, teklifi_siparise_cevir
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)

    def test_iki_kez_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_olustur,
                                                   teklif_siparis_onayla, teklifi_siparise_cevir)
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(t, kullanici=self.yon)
        teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)

    def test_siparis_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_olustur,
                                                   teklif_siparis_onayla, teklifi_siparise_cevir)
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(sip, kullanici=self.yon)              # onaylı olsa bile SIPARIS çevrilemez
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(sip, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)

    def test_iptal_edilmis_teklif_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_iptal,
                                                   teklif_siparis_olustur, teklif_siparis_onayla,
                                                   teklifi_siparise_cevir)
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(t, kullanici=self.yon)
        teklif_siparis_iptal(t, kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklifi_siparise_cevir(t, tarih=datetime.date(2026, 6, 28), kullanici=self.yon)


class TeklifSiparisDurumBelgeNoPdfTest(TestCase):
    """Durum akışı (Taslak→Onaylı) + otomatik (müteselsil) belge no + PDF çıktısı."""
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsdyon", password="x")
        cls.bos = User.objects.create_user("tsdbos", password="x")
        _hesap("120.03", "MÜŞTERİ D")
        cls.cari = Cari.objects.create(kod="C3", unvan="MÜŞTERİ D", muhasebe_kodu="120.03",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="K3", ad="GENEL3", created_by=cls.yon,
                                          updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET3", kisa_ad="AD3", ondalik=0)
        cls.stok = Stok.objects.create(kod="S5", ad="ÜRÜN D", kategori=cls.kat,
                                       uretim_birimi=cls.birim, fatura_birimi=cls.birim,
                                       created_by=cls.yon, updated_by=cls.yon)

    def _teklif(self, belge_tur="TEKLIF", yon="SATIS"):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        return teklif_siparis_olustur(
            belge_tur=belge_tur, yon=yon, cari_id=self.cari.pk,
            tarih=datetime.date(2026, 7, 19),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)

    def test_belge_no_otomatik_format_ve_artan_sira(self):
        t1 = self._teklif(belge_tur="TEKLIF", yon="SATIS")
        t2 = self._teklif(belge_tur="TEKLIF", yon="SATIS")
        self.assertTrue(t1.belge_no.startswith("SST-2026-"))
        self.assertEqual(t1.sira, 1)
        self.assertEqual(t2.sira, 2)
        self.assertNotEqual(t1.belge_no, t2.belge_no)

    def test_belge_no_tur_yon_bazinda_ayri_sayac(self):
        t = self._teklif(belge_tur="TEKLIF", yon="ALIS")
        s = self._teklif(belge_tur="SIPARIS", yon="ALIS")
        self.assertEqual(t.sira, 1)
        self.assertEqual(s.sira, 1)                                # ayrı sayaç, ikisi de 1'den başlar
        self.assertTrue(t.belge_no.startswith("SAT-"))
        self.assertTrue(s.belge_no.startswith("SAS-"))

    def test_onayla_servis_idempotent(self):
        from core.services.teklif_siparis import teklif_siparis_onayla
        t = self._teklif()
        teklif_siparis_onayla(t, kullanici=self.yon)
        t.refresh_from_db()
        self.assertEqual(t.durum, "ONAYLI")
        teklif_siparis_onayla(t, kullanici=self.yon)                # idempotent
        t.refresh_from_db()
        self.assertEqual(t.durum, "ONAYLI")

    def test_onayla_iptal_edilmis_hata(self):
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_iptal,
                                                   teklif_siparis_onayla)
        t = self._teklif()
        teklif_siparis_iptal(t, kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_onayla(t, kullanici=self.yon)

    def test_onayi_geri_al_servis_idempotent(self):
        from core.services.teklif_siparis import teklif_siparis_onayi_geri_al, teklif_siparis_onayla
        t = self._teklif()
        teklif_siparis_onayla(t, kullanici=self.yon)
        teklif_siparis_onayi_geri_al(t, kullanici=self.yon)
        t.refresh_from_db()
        self.assertEqual(t.durum, "TASLAK")
        teklif_siparis_onayi_geri_al(t, kullanici=self.yon)         # idempotent
        t.refresh_from_db()
        self.assertEqual(t.durum, "TASLAK")

    def test_onayi_geri_al_donusturulmus_engellenir(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_onayi_geri_al,
                                                   teklif_siparis_onayla, teklifi_siparise_cevir)
        t = self._teklif()
        teklif_siparis_onayla(t, kullanici=self.yon)
        teklifi_siparise_cevir(t, tarih=datetime.date(2026, 7, 19), kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_onayi_geri_al(t, kullanici=self.yon)

    def test_onayli_belge_duzenlenemez(self):
        import datetime
        from core.services.teklif_siparis import (TeklifSiparisHatasi, teklif_siparis_guncelle,
                                                   teklif_siparis_onayla)
        t = self._teklif()
        teklif_siparis_onayla(t, kullanici=self.yon)
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_guncelle(
                t, cari_id=self.cari.pk, tarih=datetime.date(2026, 7, 19),
                satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
                kullanici=self.yon)

    def test_view_onayla_ve_geri_al_buton_gorunurlugu(self):
        self.client.force_login(self.yon)
        t = self._teklif()
        d0 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertContains(d0, reverse("core:teklif_siparis_onayla", args=[t.pk]))
        self.assertContains(d0, "Taslak")
        self.assertContains(d0, reverse("core:teklif_siparis_duzenle", args=[t.pk]))
        r = self.client.post(reverse("core:teklif_siparis_onayla", args=[t.pk]))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual(t.durum, "ONAYLI")
        d1 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertContains(d1, reverse("core:teklif_siparis_onayi_geri_al", args=[t.pk]))
        self.assertContains(d1, "Onaylı")
        self.assertNotContains(d1, reverse("core:teklif_siparis_duzenle", args=[t.pk]))
        r2 = self.client.post(reverse("core:teklif_siparis_onayi_geri_al", args=[t.pk]))
        self.assertRedirects(r2, reverse("core:teklif_siparis_detay", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual(t.durum, "TASLAK")

    def test_view_onayli_duzenleme_post_reddedilir(self):
        from core.services.teklif_siparis import teklif_siparis_onayla
        t = self._teklif()
        teklif_siparis_onayla(t, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:teklif_siparis_duzenle", args=[t.pk]), {
            "cari": self.cari.pk, "tarih": "2026-07-19", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "9", "form-0-birim_fiyat": "9",
        })
        self.assertEqual(r.status_code, 200)                       # redirect değil; form hatalı geri döner
        self.assertContains(r, "Onaylı belge düzenlenemez")
        t.refresh_from_db()
        self.assertEqual(t.kalemler.filter(silindi=False).first().miktar, Decimal("1"))  # değişmedi

    def test_pdf_gercek_pdf_uretir(self):
        t = self._teklif()
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:teklif_siparis_pdf", args=[t.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(r.content[:5], b"%PDF-")

    def test_pdf_yetkisiz_403(self):
        t = self._teklif()
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:teklif_siparis_pdf", args=[t.pk])).status_code, 403)


class TeklifSiparisViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsvyon", password="x")
        cls.bos = User.objects.create_user("tsvbos", password="x")
        _hesap("120.02", "MÜŞTERİ B")
        cls.cari = Cari.objects.create(kod="C2", unvan="MÜŞTERİ B", muhasebe_kodu="120.02",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="K2", ad="GENEL2", created_by=cls.yon,
                                          updated_by=cls.yon)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("18"), aciklama="Genel2",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.stok = Stok.objects.create(kod="S3", ad="ÜRÜN C", kategori=cls.kat, kdv=cls.kdv,
                                       uretim_birimi=cls.birim, fatura_birimi=cls.birim,
                                       created_by=cls.yon, updated_by=cls.yon)

    def test_liste_ekranlari_200(self):
        self.client.force_login(self.yon)
        for ad in EKRANLAR:
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 200)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        for ad in EKRANLAR:
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 403)
            self.assertEqual(
                self.client.get(reverse("core:" + EKLE_URL[ad])).status_code, 403)

    def test_her_4_ekranda_olustur_ve_detay(self):
        from core.models import TeklifSiparis
        self.client.force_login(self.yon)
        kombinasyonlar = [
            ("satinalma_teklifleri", "TEKLIF", "ALIS"),
            ("satinalma_siparisleri", "SIPARIS", "ALIS"),
            ("satis_teklifleri", "TEKLIF", "SATIS"),
            ("satis_siparisleri", "SIPARIS", "SATIS"),
        ]
        for ekran, belge_tur, yon in kombinasyonlar:
            ekle_ad = EKLE_URL[ekran]
            self.assertEqual(self.client.get(reverse("core:" + ekle_ad)).status_code, 200)
            r = self.client.post(reverse("core:" + ekle_ad), {
                "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
                "form-0-stok": self.stok.pk, "form-0-miktar": "2", "form-0-birim_fiyat": "100",
            })
            ts = TeklifSiparis.objects.filter(belge_tur=belge_tur, yon=yon).latest("id")
            self.assertEqual(ts.durum, "TASLAK")
            self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
            d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
            self.assertEqual(d.status_code, 200)
            self.assertContains(d, "ÜRÜN C")
            self.assertContains(d, "236,00")                     # 200 + %18 KDV=36
            rl = self.client.get(reverse("core:" + ekran))
            self.assertContains(rl, reverse("core:teklif_siparis_detay", args=[ts.pk]))

    def test_liste_arama_cari_ve_belge_no(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(ts, kullanici=self.yon)   # belge_no yalnız onaylıda üretilir
        ts.refresh_from_db()
        self.client.force_login(self.yon)
        r_cari = self.client.get(reverse("core:satis_teklifleri"), {"ara": "müşteri b"})
        self.assertContains(r_cari, ts.belge_no)
        r_belge = self.client.get(reverse("core:satis_teklifleri"), {"ara": ts.belge_no})
        self.assertContains(r_belge, ts.belge_no)
        r_yok = self.client.get(reverse("core:satis_teklifleri"), {"ara": "olmayan-xyz"})
        self.assertNotContains(r_yok, ts.belge_no)
        self.assertContains(r_yok, "eşleşen kayıt yok")

    def test_bos_kalemle_kaydedilmez(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), {
            "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": "", "form-0-miktar": "", "form-0-birim_fiyat": "",
        })
        self.assertEqual(r.status_code, 200)                     # formset min_num -> hata, kalır

    def test_menude_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "Satınalma")
        self.assertContains(r, "Satış")
        for ad in EKRANLAR:
            self.assertContains(r, reverse("core:" + ad))

    def test_duzenle_get_ve_post(self):
        from core.models import TeklifSiparis
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        eski_belge_no = ts.belge_no
        self.client.force_login(self.yon)
        g = self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk]))
        self.assertEqual(g.status_code, 200)
        r = self.client.post(reverse("core:teklif_siparis_duzenle", args=[ts.pk]), {
            "cari": self.cari.pk, "tarih": "2026-07-01", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "5", "form-0-birim_fiyat": "40",
        })
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts = TeklifSiparis.objects.get(pk=ts.pk)
        self.assertEqual(ts.belge_no, eski_belge_no)                # belge no düzenlemede değişmez
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 1)
        self.assertEqual(ts.kalemler.filter(silindi=False).first().miktar, Decimal("5"))

    def test_iptal_view_detay_hala_gorunur_liste_kaybolur(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        belge_no = ts.belge_no
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:teklif_siparis_iptal", args=[ts.pk]))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(d.status_code, 200)                      # 404 değil — hâlâ görüntülenir
        self.assertContains(d, "iptal edilmiş")
        rl = self.client.get(reverse("core:satis_siparisleri"))
        self.assertNotContains(rl, belge_no)                       # listeden düşer

    def test_iptal_sonrasi_duzenle_404(self):
        from core.services.teklif_siparis import teklif_siparis_iptal, teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        teklif_siparis_iptal(ts, kullanici=self.yon)
        self.client.force_login(self.yon)
        self.assertEqual(
            self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk])).status_code, 404)

    def test_duzenle_iptal_yetkisiz_403(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=__import__("datetime").date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk])).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("core:teklif_siparis_iptal", args=[ts.pk])).status_code, 403)

    def test_view_teklif_siparise_cevir(self):
        import datetime
        from core.models import TeklifSiparis
        from core.services.teklif_siparis import teklif_siparis_olustur
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "2", "birim_fiyat": "100"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        self.client.post(reverse("core:teklif_siparis_onayla", args=[t.pk]))
        d0 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertContains(d0, reverse("core:teklif_siparise_cevir", args=[t.pk]))
        r = self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk]))
        siparis = TeklifSiparis.objects.get(kaynak_teklif=t)
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertEqual(siparis.belge_tur, "SIPARIS")
        self.assertEqual(siparis.kalemler.count(), 1)
        # teklif detayında artık "Siparişe Çevir" değil, dönüştüğü siparişe link var
        d1 = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertNotContains(d1, reverse("core:teklif_siparise_cevir", args=[t.pk]))
        self.assertContains(d1, reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        # sipariş detayında kaynak teklife link var
        d2 = self.client.get(reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertContains(d2, "Kaynak Teklif")
        self.assertContains(d2, reverse("core:teklif_siparis_detay", args=[t.pk]))
        # tekrar dönüştürme denemesi hata mesajıyla teklife geri döner
        r2 = self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk]))
        self.assertRedirects(r2, reverse("core:teklif_siparis_detay", args=[t.pk]))

    def test_taslak_teklifte_donusturme_butonu_yok(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[t.pk]))
        self.assertNotContains(d, reverse("core:teklif_siparise_cevir", args=[t.pk]))

    def test_siparis_donusturme_butonu_yok(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertNotContains(d, reverse("core:teklif_siparise_cevir", args=[sip.pk]))

    def test_donusum_yetkisiz_403(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        t = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.post(reverse("core:teklif_siparise_cevir", args=[t.pk])).status_code, 403)


class TeklifSiparisFaturayaCevirTest(TestCase):
    """Sipariş→Fatura dönüşümü: Fatura ekle formunu (Fatura'nın kendi şablonu) sipariş
    verileriyle ön-doldurur; kullanıcı tip/depo seçip onaylar (tam otomatik değil)."""
    @classmethod
    def setUpTestData(cls):
        import datetime
        from decimal import Decimal
        from core.models import (Birim, FaturaTipi, Kategori, KategoriHesap, Kur)
        cls.yon = User.objects.create_superuser("tsfyon", password="x")
        cls.bos = User.objects.create_user("tsfbos", password="x")
        Kur.objects.create(tarih=datetime.date(2026, 6, 28), usd_alis=Decimal("40"))
        _hesap("153.10", "ALÜMİNYUM MAL")
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("600", "YURTİÇİ SATIŞLAR", grup="GELIR_TABLOSU", kalem="A")
        _hesap("320.20.0001", "TEDARİKÇİ B", kalem="KVYK")
        _hesap("120.20.0001", "MÜŞTERİ B")
        cls.kdv = KdvOrani.objects.create(
            aciklama="GENEL2", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        ust = Kategori.objects.create(ad="HAMMADDE2", kod="153f")
        alt = Kategori.objects.create(ad="ALÜMİNYUM2", kod="10f", ust=ust)
        birim = Birim.objects.create(ad="ADET2", kisa_ad="AD2", ondalik=0)
        cls.stok = Stok.objects.create(kod="Sf1", ad="ÜRÜN FATURA", kategori=alt, kdv=cls.kdv,
                                       uretim_birimi=birim, fatura_birimi=birim,
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.alis_tip = FaturaTipi.objects.create(ad="ALIŞ FATURASI2", yon=FaturaTipi.Yon.ALIS)
        cls.satis_tip = FaturaTipi.objects.create(ad="SATIŞ FATURASI2", yon=FaturaTipi.Yon.SATIS)
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.alis_tip,
                                     hesap=HesapPlani.objects.get(hesap_kodu="153.10"))
        KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.satis_tip,
                                     hesap=HesapPlani.objects.get(hesap_kodu="600"))
        cls.tedarikci = Cari.objects.create(kod="C-F1", unvan="TEDARİKÇİ B",
                                            muhasebe_kodu="320.20.0001",
                                            created_by=cls.yon, updated_by=cls.yon)
        cls.musteri = Cari.objects.create(kod="C-F2", unvan="MÜŞTERİ B",
                                          muhasebe_kodu="120.20.0001",
                                          created_by=cls.yon, updated_by=cls.yon)

    def _siparis(self, yon="SATIS", cari=None):
        """ONAYLI bir sipariş — bu sınıftaki her senaryo faturaya çevirmeyi hedefler."""
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon=yon, cari_id=(cari or self.musteri).pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "3", "birim_fiyat": "50"}],
            kullanici=self.yon)
        return teklif_siparis_onayla(sip, kullanici=self.yon)

    def test_get_form_on_doldurulmus(self):
        sip = self._siparis()
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"Sipariş {sip.pk} kaynaklı")
        self.assertContains(r, "MÜŞTERİ B")

    def test_post_fatura_olusturur_ve_baglar(self):
        from core.models import Fatura, TeklifSiparis
        sip = self._siparis()
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28",
            "para_birimi": "TRY", "depo": "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "50",
        })
        fatura = Fatura.objects.get(cari=self.musteri, tip=self.satis_tip)
        self.assertRedirects(r, reverse("core:fatura_detay", args=[fatura.pk]))
        sip = TeklifSiparis.objects.get(pk=sip.pk)
        self.assertEqual(sip.fatura_id, fatura.pk)
        self.assertEqual(fatura.satirlar.filter(silindi=False).count(), 1)
        self.assertIsNotNone(fatura.fis_id)                        # yevmiye fişi de üretildi

    def test_taslak_siparis_faturaya_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        sip = teklif_siparis_olustur(
            belge_tur="SIPARIS", yon="SATIS", cari_id=self.musteri.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)                                    # ONAYLANMADI (TASLAK kalır)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[sip.pk]))

    def test_ikinci_kez_cevrilemez_faturaya_yonlenir(self):
        from core.models import Fatura
        sip = self._siparis()
        self.client.force_login(self.yon)
        self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28",
            "para_birimi": "TRY", "depo": "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "50",
        })
        fatura = Fatura.objects.get(cari=self.musteri, tip=self.satis_tip)
        r2 = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertRedirects(r2, reverse("core:fatura_detay", args=[fatura.pk]))

    def test_teklif_dogrudan_faturaya_cevrilemez(self):
        import datetime
        from core.services.teklif_siparis import teklif_siparis_olustur
        teklif = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="SATIS", cari_id=self.musteri.pk,
            tarih=datetime.date(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "10"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        self.assertEqual(
            self.client.get(reverse("core:siparis_faturaya_cevir", args=[teklif.pk])).status_code, 404)

    def test_buton_gorunurluk_ve_fatura_detay_kaynak_linki(self):
        from core.models import Fatura
        sip = self._siparis()
        self.client.force_login(self.yon)
        d0 = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertContains(d0, reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28",
            "para_birimi": "TRY", "depo": "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "50",
        })
        fatura = Fatura.objects.get(cari=self.musteri, tip=self.satis_tip)
        # sipariş detayında artık buton değil, Fatura linki
        d1 = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertNotContains(d1, reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertContains(d1, reverse("core:fatura_detay", args=[fatura.pk]))
        # fatura detayında kaynak sipariş linki
        d2 = self.client.get(reverse("core:fatura_detay", args=[fatura.pk]))
        self.assertContains(d2, "Kaynak Sipariş")
        self.assertContains(d2, reverse("core:teklif_siparis_detay", args=[sip.pk]))

    def test_yetkisiz_403(self):
        sip = self._siparis()
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk])).status_code, 403)
