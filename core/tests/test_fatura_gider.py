"""Alış GİDER faturası: kalem = gider hesabı (yaprak 7xx/65x/66x/68x; önce 730.x/770.x), depo/stok hareketi
yok, kaydedince otomatik yevmiye (Borç gider + varsa 191 KDV / Alacak cari). Servis, DB kısıtı, seçici
sıralaması, fatura tipi işareti, migration fonksiyonları ve ekranlar."""
import datetime
import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.forms import FaturaSatirForm
from core.models import (Birim, Cari, Depo, Fatura, FaturaSatir, FaturaTipi, HesapPlani, Kategori,
                         KdvOrani, Kur, Stok, StokHareket, YevmiyeFisi)
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_olustur, fatura_onayla,
                                  fatura_sil, fatura_taslak_olustur)
from core.services.fatura_tipi import FaturaTipiHatasi, fatura_tipi_guncelle, fatura_tipi_olustur
from core.services.hesap_plani import gider_hesaplari

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV", aktif=True):
    return HesapPlani.objects.create(
        hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem,
        parasal=True if grup == "BILANCO" else None, aktif=aktif)


def _maliyet(kod, ad, aktif=True):
    return _hesap(kod, ad, grup="MALIYET", kalem="", aktif=aktif)


class GiderTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        _hesap("153.10", "ALÜMİNYUM MAL")
        _hesap("600", "YURTİÇİ SATIŞLAR", grup="GELIR_TABLOSU", kalem="A")
        _hesap("632", "GENEL YÖNETİM GİDERLERİ (6XX)", grup="GELIR_TABLOSU", kalem="D")
        _hesap("656", "KAMBİYO ZARARLARI", grup="GELIR_TABLOSU", kalem="G")
        _maliyet("710", "DİREKT İLK MADDE")
        _maliyet("731", "GENEL ÜRETİM GİDERLERİ YANSITMA")
        _maliyet("730", "GENEL ÜRETİM GİDERLERİ")
        _maliyet("730.01", "ELEKTRİK GİDERİ")
        _maliyet("730.02", "SU GİDERİ")
        _maliyet("730.09", "PASİF GİDER", aktif=False)
        _maliyet("770", "GENEL YÖNETİM GİDERLERİ")
        _maliyet("770.03", "BANKA MASRAF GİDERLERİ")
        cls.kdv0 = KdvOrani.objects.create(aciklama="KDV YOK", oran=Decimal("0"))
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        cls.satis_gider = FaturaTipi.objects.create(
            ad="SATIŞ-GİDER-YANLIŞ", yon=FaturaTipi.Yon.SATIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.depo = Depo.objects.create(kod="01", ad="ANA DEPO")

    def _sat(self, hesap="730.01", miktar="1", fiyat="1000", kdv=None):
        return {"hesap_id": hesap, "miktar": miktar, "birim_fiyat": fiyat,
                "kdv_id": (kdv or self.kdv20).pk}

    def _kes(self, satirlar, **kw):
        veri = dict(tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10),
                    fatura_no="G-1", satirlar=satirlar)
        veri.update(kw)
        return fatura_olustur(**veri)


class GiderFaturaServisTest(GiderTemel):
    def test_otomatik_fis_borc_gider_ve_kdv_alacak_cari(self):
        f = self._kes([self._sat("730.01", "1", "1000"),
                       self._sat("770.03", "2", "50", kdv=self.kdv0)])
        self.assertEqual(f.durum, Fatura.Durum.ONAYLI)
        self.assertEqual(f.fis.kaynak, YevmiyeFisi.Kaynak.FATURA)
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["730.01"], (Decimal("1000.00"), Decimal("0.00")))     # gider borç
        self.assertEqual(sat["770.03"], (Decimal("100.00"), Decimal("0.00")))      # KDV'siz gider
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))          # yalnız 730.01'in KDV'si
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1300.00")))  # cari alacak
        self.assertEqual(sum(s.borc for s in f.fis.satirlar.all()),
                         sum(s.alacak for s in f.fis.satirlar.all()))
        self.assertEqual(f.genel_toplam, Decimal("1300.00"))
        self.assertEqual(f.odenecek, Decimal("1300.00"))

    def test_kalemler_stoksuz_hesapli_ve_fis_aciklamasi(self):
        f = self._kes([self._sat()])
        (s,) = f.satirlar.all()
        self.assertIsNone(s.stok_id)
        self.assertEqual(s.hesap_id, "730.01")
        self.assertEqual(s.kdv, self.kdv20)
        self.assertIsNone(s.tevkifat_id)
        self.assertIn("ALIŞ FATURASI-GİDER", f.fis.aciklama)
        satir_ack = {x.hesap_id: x.aciklama for x in f.fis.satirlar.all()}
        self.assertEqual(satir_ack["730.01"], "ELEKTRİK GİDERİ")

    def test_ayni_kdv_tek_191_satiri(self):
        f = self._kes([self._sat("730.01", "1", "100"), self._sat("730.02", "1", "300")])
        kdv_satirlari = [s for s in f.fis.satirlar.all() if s.hesap_id == "191"]
        self.assertEqual(len(kdv_satirlari), 1)
        self.assertEqual(kdv_satirlari[0].borc, Decimal("80.00"))

    def test_kdv_belirtilmezse_ya_da_sifirsa_kdv_satiri_yok(self):
        f = self._kes([{"hesap_id": "730.01", "miktar": "1", "birim_fiyat": "500"},
                       self._sat("730.02", "1", "500", kdv=self.kdv0)])
        self.assertFalse(any(s.hesap_id == "191" for s in f.fis.satirlar.all()))
        sat = {s.hesap_id: s.alacak for s in f.fis.satirlar.all()}
        self.assertEqual(sat["320.10.0001"], Decimal("1000.00"))

    def test_depo_verilse_bile_yok_sayilir_stok_hareketi_yok(self):
        f = self._kes([self._sat()], depo_id=self.depo.pk)
        self.assertIsNone(f.depo_id)
        self.assertEqual(StokHareket.objects.count(), 0)

    def test_doviz_gider_faturasi(self):
        f = self._kes([self._sat("730.01", "1", "100")], para_birimi="USD")
        sat = {s.hesap_id: s for s in f.fis.satirlar.all()}
        self.assertEqual(sat["730.01"].borc, Decimal("3000.00"))
        self.assertEqual(sat["730.01"].islem_pb, "USD")
        self.assertEqual(sat["191"].borc, Decimal("600.00"))
        self.assertEqual(sat["320.10.0001"].alacak, Decimal("3600.00"))
        self.assertEqual(f.kur, Decimal("30"))

    def test_stok_kalemi_gider_tipinde_reddedilir_ve_atomik(self):
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        ust = Kategori.objects.create(ad="HAMMADDE", kod="153")
        alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
        stok = Stok.objects.create(kod="153-10-0001", ad="LEVHA", kategori=alt,
                                   uretim_birimi=adet, fatura_birimi=adet, kdv=self.kdv20)
        with self.assertRaisesMessage(FaturaHatasi, "gider faturasında stok kullanılamaz"):
            self._kes([{"stok_id": stok.pk, "miktar": "1", "birim_fiyat": "10"}])
        self.assertEqual(Fatura.objects.count(), 0)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_gider_hesabi_normal_tipte_reddedilir(self):
        with self.assertRaisesMessage(FaturaHatasi, "yalnız gider faturası tipinde"):
            self._kes([self._sat()], tip_id=self.alis.pk)
        self.assertEqual(Fatura.objects.count(), 0)

    def test_gider_kumesinde_olmayan_hesaplar_reddedilir(self):
        for kod, neden in (("153.10", "bilanço"), ("600", "gelir"), ("632", "63x yansıtmalı"),
                           ("731", "yansıtma"), ("730", "üst hesap"), ("770", "üst hesap"),
                           ("730.09", "pasif"), ("YOK", "olmayan")):
            with self.subTest(neden), self.assertRaisesMessage(FaturaHatasi, "geçerli bir gider hesabı"):
                self._kes([self._sat(kod)])
        with self.assertRaisesMessage(FaturaHatasi, "geçerli bir gider hesabı"):
            self._kes([{"miktar": "1", "birim_fiyat": "10"}])                    # hesap hiç yok
        self.assertEqual(Fatura.objects.count(), 0)

    def test_gider_tipi_yalniz_alis_yonunde(self):
        with self.assertRaisesMessage(FaturaHatasi, "yalnız alış yönünde"):
            self._kes([self._sat()], tip_id=self.satis_gider.pk)
        self.assertEqual(Fatura.objects.count(), 0)

    def test_bilinmeyen_kdv_reddedilir(self):
        with self.assertRaisesMessage(FaturaHatasi, "KDV oranı bulunamadı"):
            self._kes([{"hesap_id": "730.01", "miktar": "1", "birim_fiyat": "10", "kdv_id": 999999}])

    def test_kdv_var_ama_borc_hesabi_yoksa_hata(self):
        kdv10 = KdvOrani.objects.create(aciklama="YARIM", oran=Decimal("10"))    # hesap_borc yok
        with self.assertRaisesMessage(FaturaHatasi, "hesabı tanımlı değil"):
            self._kes([self._sat(kdv=kdv10)])
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_guncelle_onayli_gider_faturasi_fisi_yeniler(self):
        f = self._kes([self._sat("730.01", "1", "1000")])
        fatura_guncelle(f, tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10),
                        fatura_no="G-1", satirlar=[self._sat("730.02", "1", "500", kdv=self.kdv0)])
        f.refresh_from_db()
        sat = {s.hesap_id: s.borc for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat, {"730.02": Decimal("500.00"), "320.10.0001": Decimal("0.00")})
        self.assertEqual(f.satirlar.filter(silindi=False).count(), 1)
        self.assertEqual(f.satirlar.filter(silindi=False).get().hesap_id, "730.02")

    def test_taslak_gider_faturasi_fis_yok_onaylayinca_kesilir(self):
        f = fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 3, 10), tip_id=self.gider.pk,
                                  satirlar=[self._sat()], depo_id=self.depo.pk)
        self.assertEqual((f.durum, f.fis_id, f.depo_id), (Fatura.Durum.TASLAK, None, None))
        fatura_onayla(f)
        f.refresh_from_db()
        self.assertEqual(f.durum, Fatura.Durum.ONAYLI)
        self.assertEqual(sum(s.alacak for s in f.fis.satirlar.all()), Decimal("1200.00"))

    def test_tip_ve_kalem_turu_uyusmazligi_onayda_yakalanir(self):
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        ust = Kategori.objects.create(ad="HAMMADDE", kod="153")
        alt = Kategori.objects.create(ad="ALÜMİNYUM", kod="10", ust=ust)
        stok = Stok.objects.create(kod="153-10-0001", ad="LEVHA", kategori=alt,
                                   uretim_birimi=adet, fatura_birimi=adet, kdv=self.kdv20)
        f = fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 3, 10), yon="ALIS",
                                  satirlar=[{"stok_id": stok.pk, "miktar": "1", "birim_fiyat": "10"}])
        f.tip = self.gider                       # (formu atlayıp) stok kalemli taslağa gider tipi
        f.save(update_fields=["tip"])
        with self.assertRaisesMessage(FaturaHatasi, "kalem türü fatura tipiyle uyuşmuyor"):
            fatura_onayla(f)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_sil_gider_faturasi_fisle_birlikte(self):
        f = self._kes([self._sat()])
        fis_id = f.fis_id
        fatura_sil(f)
        self.assertFalse(Fatura.objects.exists())
        self.assertFalse(FaturaSatir.objects.exists())
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis_id).exists())

    def test_db_kisiti_kalem_ya_stok_ya_hesap(self):
        f = self._kes([self._sat()])
        hesap = HesapPlani.objects.get(hesap_kodu="730.01")
        with self.assertRaises(IntegrityError), transaction.atomic():
            FaturaSatir.objects.create(fatura=f, stok=None, hesap=None, miktar=1, birim_fiyat=1)
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        ust = Kategori.objects.create(ad="H", kod="153")
        alt = Kategori.objects.create(ad="A", kod="10", ust=ust)
        stok = Stok.objects.create(kod="153-10-0009", ad="X", kategori=alt,
                                   uretim_birimi=adet, fatura_birimi=adet, kdv=self.kdv20)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FaturaSatir.objects.create(fatura=f, stok=stok, hesap=hesap, miktar=1, birim_fiyat=1)


class GiderHesapSeciciTest(GiderTemel):
    def test_yaprak_gider_hesaplari_ve_sira(self):
        kodlar = list(gider_hesaplari().values_list("hesap_kodu", flat=True))
        # önce 730.x/770.x alt hesapları, sonra diğer 7xx, sonra 65x/66x/68x
        self.assertEqual(kodlar, ["730.01", "730.02", "770.03", "710", "656"])

    def test_kapsam_disi_hesaplar_yok(self):
        kodlar = set(gider_hesaplari().values_list("hesap_kodu", flat=True))
        for yok in ("153.10", "600", "632", "731", "730", "770", "730.09", "191", "320.10.0001"):
            self.assertNotIn(yok, kodlar)

    def test_kalem_formunda_secici_ve_varsayilan_kdv(self):
        form = FaturaSatirForm(yon="ALIS")
        self.assertEqual([h.hesap_kodu for h in form.fields["hesap"].queryset][:3],
                         ["730.01", "730.02", "770.03"])
        self.assertEqual(form.fields["hesap"].label_from_instance(
            HesapPlani.objects.get(hesap_kodu="730.01")), "730.01  ELEKTRİK GİDERİ")
        self.assertEqual(form.fields["kdv"].initial, self.kdv20.pk)         # gider kaleminde %20 öntanımlı
        self.assertEqual(form.fields["kdv"].label_from_instance(self.kdv20), "KDV %20")
        self.assertIsNone(form.fields["kdv"].empty_label)

    def test_kalem_formu_dogrulamasi(self):
        def form(**veri):
            f = FaturaSatirForm(data=veri, yon="ALIS")
            f.is_valid()
            return f
        bos = form(stok="", hesap="", miktar="", birim_fiyat="", kdv=str(self.kdv20.pk))
        self.assertTrue(bos.is_valid())
        self.assertFalse(bos.dolu_mu())                                        # yalnız varsayılan KDV = boş satır
        ok = form(hesap="730.01", miktar="2", birim_fiyat="50", kdv=str(self.kdv20.pk))
        self.assertTrue(ok.is_valid())
        self.assertTrue(ok.dolu_mu())
        self.assertEqual(ok.cleaned_data["hesap"].hesap_kodu, "730.01")
        self.assertEqual(ok.cleaned_data["kdv"], self.kdv20)
        self.assertFalse(form(hesap="", miktar="2", birim_fiyat="50").is_valid())        # kalem türü yok
        self.assertFalse(form(hesap="153.10", miktar="2", birim_fiyat="50").is_valid())  # gider kümesinde değil
        self.assertFalse(form(hesap="730.01", miktar="0", birim_fiyat="50").is_valid())
        self.assertFalse(form(hesap="730.01", miktar="1", birim_fiyat="").is_valid())


class FaturaTipiGiderIsaretiTest(GiderTemel):
    def test_olustur_ve_yon_kurali(self):
        t = fatura_tipi_olustur(ad="alış gider 2", yon="ALIS", gider=True)
        self.assertTrue(t.gider)
        with self.assertRaisesMessage(FaturaTipiHatasi, "yalnız Alış yönünde"):
            fatura_tipi_olustur(ad="satış gider", yon="SATIS", gider=True)
        self.assertFalse(fatura_tipi_olustur(ad="normal", yon="ALIS").gider)     # varsayılan kapalı

    def test_guncelle_isaret_degisimi_fatura_varsa_engellenir(self):
        t = fatura_tipi_olustur(ad="alış gider 3", yon="ALIS", gider=True)
        fatura_tipi_guncelle(t, ad="alış gider 3b", yon="ALIS", sira=5, gider=True)   # değişmedi: serbest
        self.assertEqual((t.ad, t.sira, t.gider), ("ALIŞ GİDER 3B", 5, True))
        fatura_tipi_guncelle(t, ad="alış gider 3b", yon="ALIS", sira=5, gider=False)   # fatura yok: serbest
        self.assertFalse(t.gider)
        fatura_tipi_guncelle(t, ad="alış gider 3b", yon="ALIS", sira=5, gider=True)
        fatura_olustur(tip_id=t.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10),
                       satirlar=[self._sat()])
        with self.assertRaisesMessage(FaturaTipiHatasi, "işareti değiştirilemez"):
            fatura_tipi_guncelle(t, ad="alış gider 3b", yon="ALIS", sira=5, gider=False)
        t.refresh_from_db()
        self.assertTrue(t.gider)

    def test_guncelle_satis_yonune_gecip_gider_kalamaz(self):
        t = fatura_tipi_olustur(ad="alış gider 4", yon="ALIS", gider=True)
        with self.assertRaisesMessage(FaturaTipiHatasi, "yalnız Alış yönünde"):
            fatura_tipi_guncelle(t, ad="alış gider 4", yon="SATIS", sira=0, gider=True)


class MigrationFonksiyonlariTest(TestCase):
    """0123: gider tipini işaretle; 0124: 730/770 gider alt hesaplarını aç (idempotent, miras)."""

    def _modul(self, ad):
        return importlib.import_module(f"core.migrations.{ad}")

    def _ustler(self):
        _maliyet("730", "GENEL ÜRETİM GİDERLERİ")
        _maliyet("770", "GENEL YÖNETİM GİDERLERİ")

    def test_alt_hesaplar_acilir_ad_kod_ve_miras(self):
        m = self._modul("0124_gider_alt_hesaplari")
        self._ustler()
        m.alt_hesaplari_ac(django_apps, None)
        a730 = list(HesapPlani.objects.filter(hesap_kodu__startswith="730.").order_by("hesap_kodu"))
        a770 = list(HesapPlani.objects.filter(hesap_kodu__startswith="770.").order_by("hesap_kodu"))
        self.assertEqual([h.hesap_kodu for h in a730], [f"730.{n:02d}" for n in range(1, 9)])
        self.assertEqual([h.hesap_kodu for h in a770], [f"770.{n:02d}" for n in range(1, 13)])
        self.assertEqual([h.hesap_adi for h in a730], [
            "ELEKTRİK GİDERİ", "SU GİDERİ", "KİRA GİDERİ", "MUTFAK GİDERİ", "TEMİZLİK GİDERİ",
            "PERSONEL MAAŞ GİDERİ", "ARAÇ GİDERLERİ", "PERSONEL GİDERLERİ"])
        self.assertEqual([h.hesap_adi for h in a770], [
            "AĞIRLAMA GİDERİ", "ALINAN HİZMET GİDERLERİ", "BANKA MASRAF GİDERLERİ",
            "DEMİRBAŞ SATIŞ ZARARI", "FİNANSMAN GİDERİ", "KARGO GİDERİ", "NUMUNE GİDERLERİ",
            "OFİS MALZEMELERİ GİDERİ", "RESMİ EVRAK GİDERLERİ", "SEYAHAT GİDERİ",
            "VERGİ ÖDEMELERİ", "YAZILIM GİDERLERİ"])
        for h in a730 + a770:
            self.assertEqual((h.rapor_grubu, h.rapor_kalemi, h.parasal, h.aktif),
                             ("MALIYET", "", None, True))
        # 730/770 artık üst hesap; seçicide yaprak alt hesaplar görünür
        kodlar = [h.hesap_kodu for h in gider_hesaplari()]
        self.assertEqual(kodlar[:2], ["730.01", "730.02"])
        self.assertNotIn("730", kodlar)

    def test_idempotent(self):
        m = self._modul("0124_gider_alt_hesaplari")
        self._ustler()
        m.alt_hesaplari_ac(django_apps, None)
        once = HesapPlani.objects.count()
        m.alt_hesaplari_ac(django_apps, None)
        self.assertEqual(HesapPlani.objects.count(), once)

    def test_ayni_adli_varsa_atlar_kod_cakisirsa_siradaki_bos_kod(self):
        m = self._modul("0124_gider_alt_hesaplari")
        self._ustler()
        _maliyet("730.05", "ELEKTRİK GİDERİ")                # elle açılmış aynı ad -> yeniden açılmaz
        _maliyet("730.01", "BAŞKA BİR GİDER")                # kod çakışması -> ELEKTRİK zaten var; SU 730.02...
        m.alt_hesaplari_ac(django_apps, None)
        adlar = list(HesapPlani.objects.filter(hesap_adi="ELEKTRİK GİDERİ").values_list("hesap_kodu", flat=True))
        self.assertEqual(adlar, ["730.05"])
        su = HesapPlani.objects.get(hesap_adi="SU GİDERİ")
        self.assertEqual(su.hesap_kodu, "730.02")           # 01 dolu, 05 dolu -> ilk boş 02
        self.assertEqual(HesapPlani.objects.filter(hesap_kodu__startswith="730.").count(), 2 + 7)

    def test_ust_hesap_yoksa_hicbir_sey_yapmaz(self):
        m = self._modul("0124_gider_alt_hesaplari")
        m.alt_hesaplari_ac(django_apps, None)
        self.assertEqual(HesapPlani.objects.count(), 0)
        _maliyet("770", "GENEL YÖNETİM GİDERLERİ")           # yalnız 770 varsa yalnız 770 altına açılır
        m.alt_hesaplari_ac(django_apps, None)
        self.assertEqual(HesapPlani.objects.filter(hesap_kodu__startswith="770.").count(), 12)
        self.assertEqual(HesapPlani.objects.filter(hesap_kodu__startswith="730.").count(), 0)

    def test_geri_alma_kullanilmayan_hesaplari_siler(self):
        m = self._modul("0124_gider_alt_hesaplari")
        self._ustler()
        m.alt_hesaplari_ac(django_apps, None)
        from core.services.yevmiye import SatirGirdi, fis_olustur
        _hesap("100", "KASA")
        fis_olustur(tarih=D(2026, 3, 1), kur_usd=Decimal("30"), satirlar=[
            SatirGirdi(hesap_kodu="730.01", taraf="B", islem_tutari=Decimal("10")),
            SatirGirdi(hesap_kodu="100", taraf="A", islem_tutari=Decimal("10"))])
        m.alt_hesaplari_kaldir(django_apps, None)
        kalan = list(HesapPlani.objects.filter(hesap_kodu__regex=r"^(730|770)\.").values_list("hesap_kodu", flat=True))
        self.assertEqual(kalan, ["730.01"])                    # yevmiyesi olan silinmedi

    def test_gider_tipini_isaretle(self):
        m = self._modul("0123_fatura_gider_kalemi")
        hedef = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon="ALIS")
        FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon="ALIS")
        satis = FaturaTipi.objects.create(ad="SATIŞ-X", yon="SATIS")
        m.gider_tipini_isaretle(django_apps, None)
        self.assertEqual(set(FaturaTipi.objects.filter(gider=True).values_list("pk", flat=True)), {hedef.pk})
        m.gider_isaretini_kaldir(django_apps, None)
        self.assertFalse(FaturaTipi.objects.filter(gider=True).exists())
        self.assertFalse(FaturaTipi.objects.get(pk=satis.pk).gider)
        FaturaTipi.objects.all().delete()
        m.gider_tipini_isaretle(django_apps, None)             # tip yokken de sorunsuz (taze kurulum)


class GiderFaturaEkranTest(GiderTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.yon = User.objects.create_superuser("gyon", password="x")

    def _veri(self, tip=None, **satir):
        veri = {
            "tip": str((tip or self.gider).pk), "cari": str(self.cari.pk), "tarih": "2026-03-10",
            "fatura_no": "G-9", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-hesap": "730.01", "form-0-kdv": str(self.kdv20.pk),
            "form-0-miktar": "1", "form-0-birim_fiyat": "1000"}
        veri.update({f"form-0-{k}": v for k, v in satir.items()})
        return veri

    def test_form_sayfasi_gider_bayragi_secici_ve_kdv(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["tip_gider"], {str(self.gider.pk): True, str(self.alis.pk): False})
        self.assertEqual(r.context["kdv_oran"][str(self.kdv20.pk)], 20.0)
        html = r.content.decode()
        self.assertIn('id="tip-gider"', html)
        self.assertIn('name="form-0-hesap"', html)
        self.assertIn('name="form-0-kdv"', html)
        self.assertIn("730.01  ELEKTRİK GİDERİ", html)
        self.assertIn('id="id_depo"', html)                       # JS gider tipinde gizler/boşaltır
        self.assertLess(html.index("730.01  ELEKTRİK"), html.index("770.03  BANKA MASRAF"))
        self.assertLess(html.index("770.03  BANKA MASRAF"), html.index("710  DİREKT"))
        self.assertIn("gider-modu", html)                         # mod CSS/JS'i şablonda

    def test_gider_faturasi_kaydedilir_fis_kesilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._veri(), follow=True)
        self.assertEqual(r.status_code, 200)
        f = Fatura.objects.get(fatura_no="G-9")
        self.assertEqual((f.tip, f.durum, f.depo_id), (self.gider, Fatura.Durum.ONAYLI, None))
        sat = {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["730.01"], (Decimal("1000.00"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))
        self.assertEqual(sat["320.10.0001"], (Decimal("0.00"), Decimal("1200.00")))
        self.assertContains(r, f"fiş {f.fis.yil}/{f.fis.fis_no} oluştu")
        self.assertNotContains(r, "Depo seçilmedi")               # gider faturasında depo uyarısı anlamsız

    def test_gider_tipinde_depo_gonderilse_bile_kaydedilmez(self):
        self.client.force_login(self.yon)
        veri = self._veri()
        veri["depo"] = str(self.depo.pk)
        self.client.post(reverse("core:alis_fatura_ekle"), veri)
        self.assertIsNone(Fatura.objects.get(fatura_no="G-9").depo_id)
        self.assertEqual(StokHareket.objects.count(), 0)

    def test_hatalar_formda_kalir_kayit_olusmaz(self):
        self.client.force_login(self.yon)
        # gider tipi + gider kümesinde olmayan hesap
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._veri(hesap="153.10"))
        self.assertEqual(r.status_code, 200)
        # normal tip + gider hesabı
        r2 = self.client.post(reverse("core:alis_fatura_ekle"), self._veri(tip=self.alis))
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "yalnız gider faturası tipinde")
        # aynı satırda hem stok hem hesap
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        ust = Kategori.objects.create(ad="H", kod="153")
        alt = Kategori.objects.create(ad="A", kod="10", ust=ust)
        stok = Stok.objects.create(kod="153-10-0002", ad="Y", kategori=alt,
                                   uretim_birimi=adet, fatura_birimi=adet, kdv=self.kdv20)
        r3 = self.client.post(reverse("core:alis_fatura_ekle"), self._veri(stok=str(stok.pk)))
        self.assertEqual(r3.status_code, 200)
        self.assertContains(r3, "ikisi birden olmaz")
        self.assertEqual(Fatura.objects.count(), 0)
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_bos_ikinci_satir_yalniz_varsayilan_kdv_ile_hataya_yol_acmaz(self):
        self.client.force_login(self.yon)
        veri = self._veri()
        veri.update({"form-TOTAL_FORMS": "2", "form-1-hesap": "", "form-1-miktar": "",
                     "form-1-birim_fiyat": "", "form-1-kdv": str(self.kdv0.pk)})
        r = self.client.post(reverse("core:alis_fatura_ekle"), veri)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Fatura.objects.get(fatura_no="G-9").satirlar.count(), 1)

    def test_duzenle_formu_ilk_degerler_ve_guncelleme(self):
        f = self._kes([self._sat("730.01", "1", "1000"), self._sat("770.03", "1", "200", kdv=self.kdv0)])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual(r.status_code, 200)
        ilk = [(fr.initial["hesap"], fr.initial["kdv"], fr.initial["stok"]) for fr in r.context["formset"].forms]
        self.assertEqual(ilk, [("730.01", self.kdv20.pk, None), ("770.03", self.kdv0.pk, None)])
        veri = self._veri(hesap="730.02", miktar="3", birim_fiyat="100")
        veri["fatura_no"] = "G-1"
        r2 = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), veri)
        self.assertEqual(r2.status_code, 302)
        f.refresh_from_db()
        sat = {s.hesap_id: s.borc for s in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual(sat["730.02"], Decimal("300.00"))
        self.assertEqual(sat["191"], Decimal("60.00"))
        self.assertNotIn("730.01", sat)

    def test_detay_sayfasi_gider_hesabi_ve_depo_metni(self):
        f = self._kes([self._sat("730.01", "1", "1000")])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(r, "Gider Hesabı")
        self.assertContains(r, "730.01")
        self.assertContains(r, "ELEKTRİK GİDERİ")
        self.assertContains(r, "gider faturası; stok hareketi yok")
        self.assertNotContains(r, "<th>Stok</th>")

    def test_normal_fatura_detayi_degismedi(self):
        f = fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 3, 10), yon="ALIS",
                                  satirlar=self._stok_satiri())
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(r, "<th>Stok</th>")
        self.assertContains(r, "153-10-0003")

    def _stok_satiri(self):
        adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        ust = Kategori.objects.create(ad="H", kod="153")
        alt = Kategori.objects.create(ad="A", kod="10", ust=ust)
        stok = Stok.objects.create(kod="153-10-0003", ad="Z", kategori=alt,
                                   uretim_birimi=adet, fatura_birimi=adet, kdv=self.kdv20)
        return [{"stok_id": stok.pk, "miktar": "2", "birim_fiyat": "10"}]

    def test_fatura_tipleri_ekrani_gider_rozeti_ve_form(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fatura_tipleri"))
        self.assertContains(r, ">Gider<")
        self.assertEqual(r.content.decode().count(">Gider<"), 1)       # yalnız gider tipi işaretli
        r2 = self.client.post(reverse("core:fatura_tipi_ekle"),
                              {"ad": "alış hizmet gideri", "yon": "ALIS", "sira": "90", "gider": "on"})
        self.assertEqual(r2.status_code, 302)
        self.assertTrue(FaturaTipi.objects.get(ad="ALIŞ HİZMET GİDERİ").gider)
        r3 = self.client.post(reverse("core:fatura_tipi_ekle"),
                              {"ad": "satış x gider", "yon": "SATIS", "sira": "1", "gider": "on"})
        self.assertEqual(r3.status_code, 200)
        self.assertContains(r3, "yalnız Alış yönünde")
        r4 = self.client.get(reverse("core:fatura_tipi_duzenle", args=[self.gider.pk]))
        self.assertTrue(r4.context["form"].initial["gider"])


class SeedGiderTipiTest(TestCase):
    def test_seed_yalniz_gider_tipini_isaretler(self):
        from django.core.management import call_command
        call_command("seed_fatura_tipleri", verbosity=0)
        self.assertEqual(
            set(FaturaTipi.objects.filter(gider=True).values_list("ad", flat=True)),
            {"ALIŞ FATURASI-GİDER"})
        # tekrar çalıştırma idempotent; elle işaretlenmiş başka bir tipe dokunmaz
        FaturaTipi.objects.filter(ad="ALIŞ FATURASI").update(gider=True)
        call_command("seed_fatura_tipleri", verbosity=0)
        self.assertEqual(FaturaTipi.objects.filter(gider=True).count(), 2)
