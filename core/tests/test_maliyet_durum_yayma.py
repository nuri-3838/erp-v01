"""Maliyet motoru: ``stok_ortalama.yeniden_hesapla`` çıkışın YALNIZ maliyet DURUMU (GEÇİCİ→KESİN) değiştiğinde de (tutar aynı) kartı kullanan üretimin çıktı bayrağını
(``giris_tahmini`` / ``maliyet_durumu``) ve aşağı zinciri yeniler — eskiden yalnız tutar değişince yayılıyor, bayat 'tahmini' işaretleri kalıyordu. Fişler yalnız TUTAR
değişince yeniden yazılır (durum-yalnız değişimde satır/fiş yazımı YOK); içeriği aynı olan fiş ``stok_fis._fis_senkronla``da yeniden yazılmaz."""
from datetime import date
from decimal import Decimal

from core.models import StokHareket, YevmiyeFisi, YevmiyeSatir
from core.services import stok_fis
from core.services import stok_ortalama as so
from core.services.hareket import hareket_ekle, sarf_cikis_ekle
from core.services.uretim import operasyon_kaydi_onayla
from core.models import HesapPlani
from core.tests.test_irsaliye_kalem_fiyati import IrsaliyeBase
from core.tests.test_stok_ortalama import D
from core.tests.test_uretim_yan_cikti import YanCiktiBase

DEC = Decimal
G = StokHareket.Tur.GIRIS


class UretimYayilimTest(YanCiktiBase):
    def kur(self):
        self.profil_gir(10, 1000)                                                                         # 10 BOY @ 100 (fatura tutarı)
        self.fiyatsiz = hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 2, 1), tur=G, miktar=DEC("10"))   # fiyatsız giriş: GEÇİCİ (tahmini 1000)
        self.k = self.kayit("3")
        operasyon_kaydi_onayla(self.k)

    def fiyatla_ayni_tutar(self):
        StokHareket.objects.filter(pk=self.fiyatsiz.pk).update(giris_tutar_try=DEC("1000"), giris_tutar_usd=None)   # fatura geldi: tahminle AYNI tutar (çıkış tutarı değişmez)
        return so.yeniden_hesapla(self.profil)

    def test_girdi_durumu_gecici_kesin_olunca_cikti_bayragi_yenilenir(self):
        self.kur()
        for h in self.girisler(self.k).values():
            self.assertEqual((h.giris_tahmini, h.maliyet_durumu), (True, so.GECICI))                         # girdi tahminliyken çıktı da tahmini
        cikis = StokHareket.objects.get(operasyon_kaydi=self.k, tur=StokHareket.Tur.CIKIS)
        tutar_once = cikis.tutar_try
        degisen = self.fiyatla_ayni_tutar()
        self.assertEqual([h.pk for h in degisen], [cikis.pk])                                               # tutar aynı, yalnız durum değişti → artık raporlanır
        cikis.refresh_from_db()
        self.assertEqual((cikis.tutar_try, cikis.maliyet_durumu), (tutar_once, so.KESIN))
        for h in self.girisler(self.k).values():
            self.assertEqual((h.giris_tahmini, h.maliyet_durumu), (False, so.KESIN))                         # bayat 'tahmini' bayrağı temizlendi

    def test_durum_yayilimi_fis_ve_tutarlari_degistirmez(self):
        self.kur()
        fis = YevmiyeFisi.objects.get(pk=self.k.fis_id)
        satirlar_once = YevmiyeSatir.objects.count()                                                        # silinmiş satırlar dahil
        guncel_once = fis.updated_at
        tutarlar = {h.pk: (h.tutar_try, h.giris_tutar_try, h.miktar) for h in StokHareket.objects.filter(silindi=False)}
        self.fiyatla_ayni_tutar()
        fis.refresh_from_db()
        self.assertEqual(YevmiyeSatir.objects.count(), satirlar_once)                                       # fiş satırları yeniden yazılmadı
        self.assertEqual(fis.updated_at, guncel_once)
        sonra = {h.pk: (h.tutar_try, h.giris_tutar_try, h.miktar) for h in StokHareket.objects.filter(silindi=False)}
        self.assertEqual({k: v for k, v in sonra.items() if k != self.fiyatsiz.pk}, {k: v for k, v in tutarlar.items() if k != self.fiyatsiz.pk})   # çıktı/çıkış tutarları aynı
        self.assertEqual(sonra[self.fiyatsiz.pk][1], DEC("1000"))                                           # (yalnız fiyatlanan giriş: None → 1000)

    def test_ayni_icerikli_fis_yeniden_yazilmaz_degisince_yazilir(self):
        self.kur()
        fis = YevmiyeFisi.objects.get(pk=self.k.fis_id)
        satirlar_once = YevmiyeSatir.objects.count()
        stok_fis.uretim_senkronla(self.k)                                                                   # içerik aynı → dokunma
        self.assertEqual(YevmiyeSatir.objects.count(), satirlar_once)
        # girdi maliyeti değişirse (tutar değişir) fiş yazılır
        StokHareket.objects.filter(pk=self.fiyatsiz.pk).update(giris_tutar_try=DEC("3000"), giris_tutar_usd=None)
        so.yeniden_hesapla(self.profil)
        fis.refresh_from_db()
        self.assertGreater(YevmiyeSatir.objects.count(), satirlar_once)
        borc = sum((s.borc for s in fis.satirlar.filter(silindi=False)), DEC("0"))
        self.assertEqual(borc, DEC("200.00"))                                                               # (1000 + 3000) / 20 BOY = 200 ortalama × 1 BOY


class SarfDurumTest(IrsaliyeBase):
    def test_sarf_fisi_durum_yalniz_degisince_yeniden_yazilmaz(self):
        """Fiyatsız irsaliye girişi sonrası çıkan sarf GEÇİCİdir; irsaliye tahminle AYNI tutarla fiyatlanınca çıkış KESİN olur, sarf fişi yeniden YAZILMAZ."""
        self.alis(D(2026, 6, 1), "10", "10")
        self.irsaliye(fiyat="0", miktar="10")
        karsi = HesapPlani.objects.get(hesap_kodu="770.01")
        sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 28), miktar=DEC("4"), karsi_hesap_id=karsi.pk, kullanici=self.u)
        cikis = StokHareket.objects.get(stok=self.stok, silindi=False, kaynak="SARF")
        self.assertEqual((cikis.tutar_try, cikis.maliyet_durumu), (DEC("40.00"), so.GECICI))
        fis = YevmiyeFisi.objects.get(pk=cikis.fis_id)
        satir_once, guncel_once = YevmiyeSatir.objects.count(), fis.updated_at
        StokHareket.objects.filter(kaynak="IRSALIYE").update(giris_tutar_try=DEC("100"), giris_tutar_usd=None)
        degisen = so.yeniden_hesapla(self.stok)
        cikis.refresh_from_db()
        fis.refresh_from_db()
        self.assertEqual([h.pk for h in degisen], [cikis.pk])
        self.assertEqual((cikis.tutar_try, cikis.maliyet_durumu), (DEC("40.00"), so.KESIN))
        self.assertEqual((YevmiyeSatir.objects.count(), fis.updated_at), (satir_once, guncel_once))        # sarf fişi dokunulmadı
