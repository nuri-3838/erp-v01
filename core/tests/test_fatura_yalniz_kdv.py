"""Fatura tipi 'Yalnız KDV' (yalnız ALIŞ): kalemler normal girilir (belge/KDV matrah + KDV gösterir) ama yevmiye fişine YALNIZ KDV yazılır
(191 B / cari A; tevkifatta 360 A, cari net KDV); matrah satırı, stok hareketi ve kategori-hesap eşlemesi yok; düzenle/sil yalnız kendi fişini etkiler."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Depo, Fatura, FaturaTipi, HesapPlani, StokHareket, YevmiyeFisi
from core.services import fatura_tipi as ft
from core.services.fatura import fatura_guncelle, fatura_olustur, fatura_onayla, fatura_sil, fatura_taslak_olustur
from core.tests.test_fatura import D, FaturaTestTemel, _hesap

Dc = Decimal


class YalnizKdvBase(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kdvli = FaturaTipi.objects.create(ad="ALIŞ FATURASI-KDV", yon=FaturaTipi.Yon.ALIS, yalniz_kdv=True)   # kategori-hesap eşlemesi YOK
        cls.depo = Depo.objects.create(kod="D1", ad="DEPO")

    def fatura(self, **kw):
        return fatura_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="K-1", satirlar=self._satir(), **kw)

    def sat(self, f):
        return {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}


class FisTest(YalnizKdvBase):
    def test_fis_yalniz_kdv_191_borc_cari_alacak_matrah_yok(self):
        f = self.fatura()
        self.assertEqual(self.sat(f), {"191": (Dc("200.00"), Dc("0.00")), "320.10.0001": (Dc("0.00"), Dc("200.00"))})
        self.assertEqual(f.fis.satirlar.filter(silindi=False).count(), 2)               # matrah için satır yok, 153.10 yok
        # belge matrah + KDV'yi doğru gösterir
        self.assertEqual((f.ara_toplam, f.kdv_toplam, f.genel_toplam), (Dc("1000.00"), Dc("200.00"), Dc("1200.00")))
        self.assertEqual(f.odenecek, Dc("200.00"))                                      # cariye yalnız KDV
        self.assertEqual(f.satirlar.filter(silindi=False).count(), 1)

    def test_depo_verilse_de_stok_hareketi_yok(self):
        f = self.fatura(depo_id=self.depo.pk)
        self.assertIsNone(f.depo_id)
        self.assertFalse(StokHareket.objects.filter(silindi=False).exists())

    def test_taslak_onay_yolu(self):
        f = fatura_taslak_olustur(tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir(), depo_id=self.depo.pk)
        self.assertIsNone(f.fis_id)
        fatura_onayla(f)
        f.refresh_from_db()
        self.assertEqual(self.sat(f), {"191": (Dc("200.00"), Dc("0.00")), "320.10.0001": (Dc("0.00"), Dc("200.00"))})
        self.assertFalse(StokHareket.objects.filter(silindi=False).exists())

    def test_tevkifatli(self):
        from core.models import Stok, TevkifatOrani
        h = _hesap("360.10", "ÖDENECEK KDV TEVKİFATI", kalem="KVYK")
        tev = TevkifatOrani.objects.create(kod="5/10", pay=5, payda=10, hesap=h)
        Stok.objects.filter(pk=self.stok.pk).update(tevkifat=tev)
        f = self.fatura()
        self.assertEqual(self.sat(f), {"191": (Dc("200.00"), Dc("0.00")), "360.10": (Dc("0.00"), Dc("100.00")),
                                       "320.10.0001": (Dc("0.00"), Dc("100.00"))})
        self.assertEqual(f.odenecek, Dc("100.00"))
        self.assertEqual(sum(s.borc for s in f.fis.satirlar.filter(silindi=False)), sum(s.alacak for s in f.fis.satirlar.filter(silindi=False)))

    def test_duzenle_ve_sil_yalniz_kendi_fisini_etkiler(self):
        diger = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="N-1", satirlar=self._satir())
        f = self.fatura()
        fatura_guncelle(f, tip_id=self.kdvli.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="K-1", satirlar=self._satir(fiyat="200"))
        f.refresh_from_db()
        self.assertEqual(self.sat(f), {"191": (Dc("400.00"), Dc("0.00")), "320.10.0001": (Dc("0.00"), Dc("400.00"))})
        fis_pk = f.fis_id
        fatura_sil(f)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis_pk).exists() and YevmiyeFisi.objects.get(pk=fis_pk).silindi is False)
        diger.refresh_from_db()
        self.assertFalse(diger.fis.silindi)
        self.assertEqual(self.sat(diger)["153.10"], (Dc("1000.00"), Dc("0.00")))         # diğer faturanın fişi aynı

    def test_diger_tipler_degismedi_matrah_yazilir(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="N-2", satirlar=self._satir())
        self.assertEqual(self.sat(f)["153.10"], (Dc("1000.00"), Dc("0.00")))
        self.assertEqual(f.odenecek, Dc("1200.00"))


class TipKuraliTest(YalnizKdvBase):
    def test_yalniz_alis_ve_gider_stopajliyla_birlikte_olmaz(self):
        with self.assertRaisesMessage(ft.FaturaTipiHatasi, "yalnız Alış"):
            ft.fatura_tipi_olustur(ad="X1", yon="SATIS", yalniz_kdv=True)
        with self.assertRaisesMessage(ft.FaturaTipiHatasi, "birlikte seçilemez"):
            ft.fatura_tipi_olustur(ad="X2", yon="ALIS", gider=True, yalniz_kdv=True)
        with self.assertRaises(ft.FaturaTipiHatasi):
            ft.fatura_tipi_olustur(ad="X3", yon="ALIS", gider=True, stopajli=True, yalniz_kdv=True)
        t = ft.fatura_tipi_olustur(ad="X4", yon="ALIS", yalniz_kdv=True)
        self.assertTrue(t.yalniz_kdv)

    def test_faturasi_olan_tipte_isaret_degismez(self):
        self.fatura()
        with self.assertRaisesMessage(ft.FaturaTipiHatasi, "değiştirilemez"):
            ft.fatura_tipi_guncelle(self.kdvli, ad=self.kdvli.ad, yon="ALIS", sira=0, yalniz_kdv=False)

    def test_form_ve_liste(self):
        self.client.force_login(User.objects.create_superuser("yk_y", password="x"))
        r = self.client.get(reverse("core:fatura_tipi_ekle"))
        self.assertContains(r, "Yalnız KDV")
        self.assertContains(r, 'name="yalniz_kdv"')
        r = self.client.post(reverse("core:fatura_tipi_ekle"), {"ad": "YENİ KDV", "yon": "ALIS", "sira": "55", "yalniz_kdv": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(FaturaTipi.objects.get(ad="YENİ KDV").yalniz_kdv)
        self.assertContains(self.client.get(reverse("core:fatura_tipleri")), "Yalnız KDV")
        r = self.client.post(reverse("core:fatura_tipi_ekle"), {"ad": "SATIŞ KDV", "yon": "SATIS", "sira": "1", "yalniz_kdv": "on"})
        self.assertContains(r, "yalnız Alış yönünde")
