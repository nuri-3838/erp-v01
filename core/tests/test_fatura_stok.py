"""Fatura -> stok hareketi entegrasyonu: alış→giriş, satış→çıkış, çevirici, yetersiz
stok engeli (atomik), iptal/güncelleme geri alma, deposuz fatura (hareket yok)."""
import datetime
from decimal import Decimal

from core.models import Birim, Depo, Fatura, Stok, StokHareket
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_iptal,
                                  fatura_olustur)
from core.services.hareket import eldeki_miktar
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

    def test_iptal_hareketi_geri_alir(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                           tarih=D(2026, 3, 10), satirlar=self._satir(miktar="10"),
                           depo_id=self.depo.pk)
        fatura_iptal(f)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), Decimal("0.000"))
        self.assertFalse(StokHareket.objects.filter(
            fatura_satir__fatura=f, silindi=False).exists())

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
