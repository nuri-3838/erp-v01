"""Fatura -> stok hareketi entegrasyonu: alış→giriş, satış→çıkış, çevirici, yetersiz
stok engeli (atomik), iptal/güncelleme geri alma, deposuz fatura (hareket yok)."""
import datetime
from decimal import Decimal

from core.models import Birim, Depo, Fatura, Stok, StokHareket
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_olustur,
                                  fatura_sil)
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.tests.test_fatura import FaturaTestTemel

D = datetime.date


class FaturaStokTest(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.depo = Depo.objects.create(kod="01", ad="ANA DEPO")

    def test_alis_giris_yapar(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                           depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("10.000"))
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(h.tur, StokHareket.Tur.GIRIS)
        self.assertEqual(h.kaynak, StokHareket.Kaynak.FATURA)
        self.assertEqual(h.depo_id, self.depo.pk)

    def test_satis_cikis_yapar(self):
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                       depo_id=self.depo.pk)
        fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="4"),
                       depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("6.000"))

    def test_satis_yetersiz_stok_engellenir_atomik(self):
        n0 = Fatura.objects.count()
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="5"),
                           depo_id=self.depo.pk)
        self.assertEqual(Fatura.objects.count(), n0)            # atomik: fatura yok
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("0.000"))

    def test_cevirici_uygulanir(self):
        # çevirici=2 (1 üretim = 2 fatura birimi) -> fatura 10 -> üretim 5
        adet2 = Birim.objects.create(ad="ADET2", kisa_ad="AD2", ondalik=0)
        st = Stok.objects.create(kod="153-10-0002", ad="ÇEVİRİCİLİ", kategori=self.alt,
                                 uretim_birimi=adet2, fatura_birimi=adet2,
                                 cevirici=Decimal("2"), kdv=self.kdv)
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10),
                       satirlar=[{"stok_id": st.pk, "miktar": "10", "birim_fiyat": "100"}],
                       depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(st, self.depo), Decimal("5.000"))

    def test_sil_hareketi_kalici_siler(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                           depo_id=self.depo.pk)
        fatura_id = f.pk
        fatura_sil(f)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("0.000"))
        self.assertFalse(StokHareket.objects.filter(fatura_satir__fatura_id=fatura_id).exists())
        self.assertFalse(Fatura.objects.filter(pk=fatura_id).exists())

    def test_guncelle_hareketi_yeniler(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                           depo_id=self.depo.pk)
        fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                        tarih=D(2026, 3, 10), satirlar=self._satir(miktar="25"),
                        depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("25.000"))
        self.assertEqual(StokHareket.objects.filter(
            fatura_satir__fatura=f, silindi=False).count(), 1)

    def test_deposuz_fatura_hareket_uretmez(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"))
        self.assertIsNone(f.depo_id)
        self.assertFalse(StokHareket.objects.filter(fatura_satir__fatura=f).exists())

    def test_alis_dusurme_satilmis_stoku_negatife_dusuremez(self):
        # Alış 10 -> satış 8 (eldeki 2). Alış'ı 5'e düşürmek eldekiyi -3 yapardı -> ENGEL.
        fa = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                            tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                            depo_id=self.depo.pk)
        fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="8"),
                       depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("2.000"))
        with self.assertRaises(FaturaHatasi):
            fatura_guncelle(fa, tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                            tarih=D(2026, 3, 10), satirlar=self._satir(miktar="5"),
                            depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("2.000"))  # rollback

    def test_depo_degisimi_eski_depoyu_bosaltir(self):
        d2 = Depo.objects.create(kod="02", ad="ÜRETİM")
        fa = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                            tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                            depo_id=self.depo.pk)
        fatura_guncelle(fa, tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                        tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"), depo_id=d2.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("0.000"))
        self.assertEqual(eldeki_miktar(self.stok, d2), Decimal("10.000"))

    def test_alis_fifo_katman_kur_ve_cevirici_ile_dogru(self):
        # cevirici=2 (1 uretim birimi = 2 fatura birimi), doviz USD, kur=30 (setUpTestData).
        # birim_maliyet_try = birim_fiyat(100) x kur(30) x cevirici(2) = 6000.
        adet2 = Birim.objects.create(ad="ADET3", kisa_ad="AD3", ondalik=0)
        st = Stok.objects.create(kod="153-10-0003", ad="MALIYETLI", kategori=self.alt,
                                 uretim_birimi=adet2, fatura_birimi=adet2,
                                 cevirici=Decimal("2"), kdv=self.kdv)
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), para_birimi="USD",
                           satirlar=[{"stok_id": st.pk, "miktar": "10", "birim_fiyat": "100"}],
                           depo_id=self.depo.pk)
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        katman = h.maliyet_katmani
        self.assertEqual(katman.birim_maliyet_try, Decimal("6000.000000"))
        self.assertEqual(katman.kaynak_pb, "USD")
        self.assertEqual(katman.kaynak_kur, Decimal("30"))

    def test_satis_katmandan_fifo_tuketir(self):
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                       depo_id=self.depo.pk)
        fs = fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk,
                            tarih=D(2026, 3, 10), satirlar=self._satir(miktar="4"),
                            depo_id=self.depo.pk)
        h = StokHareket.objects.get(fatura_satir__fatura=fs, silindi=False)
        tuketimler = list(h.maliyet_tuketimleri.all())
        self.assertEqual(len(tuketimler), 1)
        self.assertEqual(tuketimler[0].miktar, Decimal("4.000"))
        self.assertEqual(tuketimler[0].tutar_try, Decimal("400.00"))   # 4 x 100 TL

    def test_guncelleme_uretimde_tuketilmis_katmani_engeller(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                           depo_id=self.depo.pk)
        # Üretim gibi başka bir hareketin bu girişin maliyetini tüketmesini simüle et.
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 3, 11),
                    tur=StokHareket.Tur.CIKIS, miktar="3", kaynak=StokHareket.Kaynak.URETIM)
        with self.assertRaises(FaturaHatasi):
            fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                            tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                            depo_id=self.depo.pk)

    def test_cevirici_sifira_yuvarlarsa_hata(self):
        adet = Birim.objects.create(ad="ADET9", kisa_ad="AD9", ondalik=0)
        st = Stok.objects.create(kod="153-10-0009", ad="MİKRO", kategori=self.alt,
                                 uretim_birimi=adet, fatura_birimi=adet,
                                 cevirici=Decimal("100000"), kdv=self.kdv)
        with self.assertRaises(FaturaHatasi):
            fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10),
                           satirlar=[{"stok_id": st.pk, "miktar": "1", "birim_fiyat": "1"}],
                           depo_id=self.depo.pk)
