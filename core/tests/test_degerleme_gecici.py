"""Stok Değerleme Raporu 'Mizan Karşılaştırması' geçici kuralı (2026-11-12): GEÇİCİ = henüz mizana girmemiş tahmini değerler (fiyatsız irsaliye / elle giriş, faturası gelmemiş fason
bedeli). ÜRETİM girişlerinin GEÇİCİ/tahmini bayrağı (yayılan ya da bayat) geçici SAYILMAZ — fişle mizanda; faturasız fason tahakkuklu dönüş kesin; geçici tüketildikçe DÜŞMEZ
(tüketim fişi aynı tutarı hesaptan zaten çıkarmıştır; orantılı düşülürse sahte fark çıkar). Yalnız rapor değişir: hareket/maliyet/fiş aynı."""
from decimal import Decimal

from core.models import HesapPlani, StokHareket
from core.services import stok_ortalama as so
from core.services.hareket import hareket_ekle, sarf_cikis_ekle
from core.tests.test_irsaliye_kalem_fiyati import IrsaliyeBase
from core.tests.test_stok_ortalama import D
from core.tests.test_fason_tahakkuk import FaturasizBase

DEC = Decimal


def satir(kod):
    return next(r for r in so.degerleme_raporu()["karsilastirma"] if r["kod"] == kod)


class UretimBayragiGeciciSayilmazTest(FaturasizBase):
    def test_faturasiz_fason_tahakkuklu_donus_kesin_fark_sifir(self):
        self.faturasiz()
        self.hazir()
        r = satir("151")
        self.assertEqual((r["gecici"], r["fark"], r["ham_fark"], r["fason_bekleyen"]), (DEC("0"), DEC("0.00"), DEC("0.00"), DEC("0.00")))

    def test_bayat_tahmini_bayragi_olan_uretim_girisi_geciciye_girmez(self):
        """Çıktı girişleri tahakkuktan sonra bile GEÇİCİ/tahmini işaretli kalmışsa (bayat bayrak) rapor sahte fark göstermez; hareketler DEĞİŞMEZ."""
        self.faturasiz()
        d = self.hazir()
        kayit = d.kayitlar.get()
        StokHareket.objects.filter(operasyon_kaydi=kayit, tur=StokHareket.Tur.GIRIS).update(giris_tahmini=True, maliyet_durumu=so.GECICI)
        once = sorted((h.pk, h.tutar_try, h.giris_tahmini, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False))
        r = satir("151")
        self.assertEqual((r["gecici"], r["fark"]), (DEC("0"), DEC("0.00")))
        self.assertEqual(r["stok_degeri"], r["toplam_deger"])                                              # kesin = toplam
        self.assertEqual(sorted((h.pk, h.tutar_try, h.giris_tahmini, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False)), once)   # rapor hiçbir şey yazmaz
        for kart in so.degerleme_raporu()["satirlar"]:
            self.assertEqual(kart["gecici_try"], DEC("0"))

    def test_faturali_cari_fatura_bekleyen_fason_payi_yine_gecici(self):
        """Faturalı fasoncuda (tahakkuk yok) fason bedeli fatura gelene kadar geçici — eskisi gibi; üretim girişinin malzeme payı geçici değil."""
        self.hazir()                                                                                       # salim faturasız DEĞİL
        r = satir("151")
        self.assertEqual(r["fason_bekleyen"], DEC("36.00"))
        self.assertEqual((r["gecici"], r["fark"], r["ham_fark"]), (DEC("36.00"), DEC("0.00"), DEC("36.00")))
        StokHareket.objects.filter(tur=StokHareket.Tur.GIRIS, kaynak=StokHareket.Kaynak.URETIM).update(giris_tahmini=True, maliyet_durumu=so.GECICI)
        r = satir("151")
        self.assertEqual((r["gecici"], r["fark"]), (DEC("36.00"), DEC("0.00")))                           # çıktı işareti GEÇİCİ olsa bile yalnız fason payı
        StokHareket.objects.filter(tur=StokHareket.Tur.CIKIS, kaynak=StokHareket.Kaynak.URETIM).update(maliyet_durumu=so.GECICI)   # girdi çıkışı da tahminli işaretli
        r = satir("151")
        self.assertEqual((r["gecici"], r["fark"]), (DEC("36.00"), DEC("0.00")))                           # malzeme payı fişle mizanda: geçici yine yalnız fason payı


class TuketilenGeciciDusmezTest(IrsaliyeBase):
    def test_fiyatsiz_irsaliye_tuketilince_gecici_ayni_kalir_fark_sifir(self):
        """153 grubu: 10 @ 10 fatura (mizan 100) + 10 adetlik FİYATSIZ irsaliye (tahmini 100, mizanda yok) + 4 adet sarf (40 → mizandan çıkar).
        Stok 16 × 10 = 160; geçici 100 sabit kalmalı → kesin 60 = mizan 60 (orantılı 80'e düşseydi −20 sahte fark çıkardı)."""
        self.alis(D(2026, 6, 1), "10", "10")
        self.irsaliye(fiyat="0", miktar="10")                                                              # fiyatsız irsaliye girişi (GEÇİCİ, 100 tahmini)
        karsi = HesapPlani.objects.get(hesap_kodu="770.01")
        sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 28), miktar=DEC("4"), karsi_hesap_id=karsi.pk, kullanici=self.u)
        r = satir("153")
        self.assertEqual((r["toplam_deger"], r["gecici"], r["stok_degeri"], r["mizan"], r["fark"]), (DEC("160.00"), DEC("100.00"), DEC("60.00"), DEC("60.00"), DEC("0.00")))
        kart = next(k for k in so.degerleme_raporu()["satirlar"] if k["stok"].pk == self.stok.pk)
        self.assertEqual(kart["gecici_try"], DEC("100.00"))
        # fatura gelince geçici kendiliğinden kapanır (irsaliye girişi fatura tutarıyla fiyatlanır, fiş yazılır)
        h = StokHareket.objects.get(kaynak="IRSALIYE")
        self.assertEqual(h.maliyet_durumu, so.GECICI)

    def test_fiyatsiz_elle_giris_geciciye_girer_uretim_ve_transfer_girmez(self):
        self.alis(D(2026, 6, 1), "10", "10")
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 5), tur=StokHareket.Tur.GIRIS, miktar=DEC("5"))      # fiyatsız MANUEL giriş
        r = satir("153")
        self.assertEqual(r["gecici"], DEC("50.00"))                                                        # 5 × 10 tahmini
        self.assertEqual(r["ham_fark"], DEC("50.00"))                                                      # stok − mizan = bekleyen tahmin
        self.assertEqual(r["fark"], DEC("0.00"))
