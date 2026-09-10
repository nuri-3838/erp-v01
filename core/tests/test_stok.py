"""Stok kartı (STOKLAR Faz A — master) testleri: otomatik kod (ÜST-ALT-sıra),
TR büyük harf, ALT-kategori zorunlu, birim/çevirici doğrulama, KDV/tevkifat FK,
DB kısıtları, view + yetki. (Miktar/hareket bu fazda YOK.)"""
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Birim, EkranYetki, KdvOrani, Stok, StokFiyat, TevkifatOrani
from core.services.birim import birim_olustur
from core.services.cari import CariHatasi, cari_sil
from core.services.kategori import kategori_olustur
from core.services.stok import (StokHatasi, sonraki_stok_kodu, stok_guncelle,
                                 stok_kopyala, stok_olustur, stok_sil)
from core.services.tanim import (TanimHatasi, kdv_orani_sil, tevkifat_orani_sil)


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
                         kdv_id=_kdv("20").pk, tedarikci_id=99999)

    def test_kdv_zorunlu(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"))

    def test_tevkifat_opsiyonel(self):
        _, alt, _, adet, kg = _veri()
        s = stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk)
        self.assertIsNotNone(s.kdv_id)
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
                         kdv_id=_kdv("20").pk, kritik_stok=Decimal("-1"))

    def test_alis_fiyati_opsiyonel_bos_none_kalir(self):
        _, alt, _, adet, kg = _veri()
        s = stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk)
        self.assertIsNone(s.alis_fiyati)
        self.assertEqual(s.alis_fiyati_pb, "TRY")

    def test_alis_fiyati_kaydedilir(self):
        _, alt, _, adet, kg = _veri()
        s = stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk, alis_fiyati="12,50", alis_fiyati_pb="usd")
        self.assertEqual(s.alis_fiyati, Decimal("12.50"))
        self.assertEqual(s.alis_fiyati_pb, "USD")             # büyük harfe çevrilir

    def test_alis_fiyati_negatif_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk, alis_fiyati="-1")

    def test_alis_fiyati_gecersiz_pb_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("1"),
                         kdv_id=_kdv("20").pk, alis_fiyati="10", alis_fiyati_pb="XYZ")

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

    def test_kopyala_ayni_bilgiler_yeni_kod(self):
        _, alt, _, adet, kg = _veri()
        c = _cari()
        s = stok_olustur(ad="orijinal", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                         fatura_birimi_id=kg.pk, cevirici=Decimal("2.5"),
                         kdv_id=_kdv("20").pk, tevkifat_id=_tevkifat().pk,
                         kritik_stok=Decimal("50"), tedarikci_id=c.pk,
                         alis_fiyati="99,90", alis_fiyati_pb="eur")
        kopya = stok_kopyala(s, kullanici=None)
        self.assertNotEqual(kopya.pk, s.pk)
        self.assertEqual(kopya.kod, "150-10-0002")          # sıradaki numara
        self.assertEqual(kopya.ad, f"{s.ad} KOPYA")         # ad ayırt edici sonek alır
        self.assertEqual(
            (kopya.kategori_id, kopya.uretim_birimi_id, kopya.fatura_birimi_id,
             kopya.cevirici, kopya.kdv_id, kopya.tevkifat_id, kopya.kritik_stok,
             kopya.tedarikci_id, kopya.alis_fiyati, kopya.alis_fiyati_pb),
            (s.kategori_id, s.uretim_birimi_id, s.fatura_birimi_id,
             s.cevirici, s.kdv_id, s.tevkifat_id, s.kritik_stok, s.tedarikci_id,
             s.alis_fiyati, s.alis_fiyati_pb))
        s.refresh_from_db()
        self.assertEqual((s.kod, s.ad), ("150-10-0001", "ORİJİNAL"))   # orijinal değişmedi

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

    def test_db_alis_fiyati_kisit(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-9002", ad="Y", kategori=alt,
                                 uretim_birimi=adet, fatura_birimi=kg,
                                 alis_fiyati=Decimal("-5"))

    def test_db_kod_unique(self):
        _, alt, _, adet, kg = _veri()
        Stok.objects.create(kod="150-10-5000", ad="A", kategori=alt,
                            uretim_birimi=adet, fatura_birimi=kg)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-5000", ad="B", kategori=alt,
                                uretim_birimi=adet, fatura_birimi=kg)

    def test_db_agirlik_kisit(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-9003", ad="Z", kategori=alt,
                                uretim_birimi=adet, fatura_birimi=kg,
                                satis_urunu=True, agirlik=Decimal("-1"))

    def test_db_en_az_bir_grup_kisit(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.create(kod="150-10-9004", ad="W", kategori=alt,
                                uretim_birimi=adet, fatura_birimi=kg,
                                satinalma_urunu=False, uretim_urunu=False,
                                satis_urunu=False)


class StokGrupTeknikAlanTest(TestCase):
    """Ürün grubu (Satınalma/Üretim/Satış — çoklu seçim, en az biri zorunlu) +
    yalnız Satış işaretliyken anlamlı olan teklif/teknik ölçü alanları."""

    def _kur(self, alt, adet, kg, **kw):
        kw.setdefault("kdv_id", _kdv("20").pk)
        return stok_olustur(ad=kw.pop("ad", "x"), kategori_id=alt.pk,
                            uretim_birimi_id=adet.pk, fatura_birimi_id=kg.pk,
                            cevirici=Decimal("1"), **kw)

    def test_hicbir_grup_secilmezse_reddedilir(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            self._kur(alt, adet, kg, satinalma_urunu=False, uretim_urunu=False,
                      satis_urunu=False)

    def test_ad_dil(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, ad="a tipi merdiven")
        s.ad_en = "Aluminium Platform Stepladder 2+1"
        s.save(update_fields=["ad_en"])
        self.assertEqual(s.ad_dil("en"), "Aluminium Platform Stepladder 2+1")
        self.assertEqual(s.ad_dil("tr"), s.ad)

    def test_ad_dil_ad_en_bossa_ad_doner(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, ad="a tipi merdiven")
        self.assertEqual(s.ad_dil("en"), s.ad)

    def test_satis_urunu_teknik_alanlari_kaydeder(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, model_kodu="a21", basamak_sayisi=5,
                      yukseklik="100", acik_derinlik="90", taban_genisligi="43",
                      kapali_boy="171", agirlik="4,30", azami_yuk="150", cbm="0,075",
                      yukleme_20dc=440, yukleme_40hq=1010, yukleme_tir=1220,
                      ad_en="Aluminium Platform Stepladder 2+1", hs_kodu="7615.10",
                      materyal="alüminyum", materyal_en="Aluminium")
        self.assertEqual(s.model_kodu, "A21")          # TR büyük harf uygulanır
        self.assertEqual(s.basamak_sayisi, 5)
        self.assertEqual(s.yukseklik, Decimal("100.0"))
        self.assertEqual(s.agirlik, Decimal("4.30"))
        self.assertEqual(s.yukleme_40hq, 1010)
        self.assertEqual(s.hs_kodu, "7615.10")
        self.assertEqual(s.materyal, "ALÜMİNYUM")       # TR alan: büyük harf uygulanır
        # İngilizce alanlar (ad_en/materyal_en) buyuk_harf_tr'den GEÇMEZ — "i" -> Türkçe
        # "İ"ye çevrilirse İngilizce metin bozulur (bkz. Ulke/Sehir'de aynı hatanın tekrar
        # edilmemesi için 2026-09-09'da eklenen ad_dil() notu).
        self.assertEqual(s.ad_en, "Aluminium Platform Stepladder 2+1")
        self.assertEqual(s.materyal_en, "Aluminium")

    def test_satis_urunu_degilse_teknik_alanlar_temizlenir(self):
        """Satış işaretli değilken teknik alan değeri gönderilse bile kayıtta kalmaz —
        formda ne gösterilirse gösterilsin, veri tutarlılığı serviste zorlanır."""
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satinalma_urunu=True, satis_urunu=False,
                      model_kodu="A21", agirlik="4,30", basamak_sayisi=5,
                      ad_en="X", hs_kodu="7615.10", materyal="Alüminyum",
                      materyal_en="Aluminium")
        self.assertEqual(s.model_kodu, "")
        self.assertIsNone(s.agirlik)
        self.assertIsNone(s.basamak_sayisi)
        self.assertEqual(s.ad_en, "")
        self.assertEqual(s.hs_kodu, "")
        self.assertEqual(s.materyal, "")
        self.assertEqual(s.materyal_en, "")

    def test_guncellemede_satis_kapatilinca_teknik_alanlar_silinir(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, model_kodu="A21", agirlik="4,30",
                      hs_kodu="7615.10", materyal="Alüminyum")
        self.assertEqual(s.agirlik, Decimal("4.30"))
        stok_guncelle(s, ad=s.ad, uretim_birimi_id=s.uretim_birimi_id,
                     fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                     kdv_id=s.kdv_id, satinalma_urunu=True, satis_urunu=False)
        s.refresh_from_db()
        self.assertEqual(s.model_kodu, "")
        self.assertIsNone(s.agirlik)
        self.assertFalse(s.satis_urunu)
        self.assertEqual(s.hs_kodu, "")
        self.assertEqual(s.materyal, "")

    def test_materyal_dil(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, materyal="alüminyum",
                      materyal_en="Aluminium")
        self.assertEqual(s.materyal_dil("en"), "Aluminium")
        self.assertEqual(s.materyal_dil("tr"), "ALÜMİNYUM")

    def test_materyal_dil_materyal_en_bossa_materyal_doner(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, materyal="alüminyum")
        self.assertEqual(s.materyal_dil("en"), s.materyal)

    def test_negatif_agirlik_reddedilir(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            self._kur(alt, adet, kg, satis_urunu=True, agirlik="-1")

    def test_kopyala_grup_ve_teknik_alanlari_kopyalar(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, uretim_urunu=True,
                      model_kodu="A21", agirlik="4,30", basamak_sayisi=5)
        kopya = stok_kopyala(s)
        self.assertTrue(kopya.satis_urunu)
        self.assertEqual(kopya.model_kodu, "A21")
        self.assertEqual(kopya.agirlik, Decimal("4.30"))
        self.assertEqual(kopya.basamak_sayisi, 5)


class StokFiyatTest(TestCase):
    """Satış fiyat listesi (StokFiyat, PB başına en fazla bir aktif satır) — yalnız
    satis_urunu=True kartlarda anlamlı, Satış Teklifi ekranının birim fiyatları buradan
    gelir."""

    def _kur(self, alt, adet, kg, **kw):
        kw.setdefault("kdv_id", _kdv("20").pk)
        return stok_olustur(ad=kw.pop("ad", "x"), kategori_id=alt.pk,
                            uretim_birimi_id=adet.pk, fatura_birimi_id=kg.pk,
                            cevirici=Decimal("1"), **kw)

    def test_fiyat_listesi_kaydedilir(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12.000", fiyat_usd="350",
                      fiyat_eur="320", fiyat_gbp="280")
        fiyatlar = {f.para_birimi: f.fiyat for f in s.fiyatlar.filter(silindi=False)}
        self.assertEqual(fiyatlar, {
            "TRY": Decimal("12000"), "USD": Decimal("350"),
            "EUR": Decimal("320"), "GBP": Decimal("280")})

    def test_fiyat_listesi_kismi_pb_girilebilir(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12000")
        fiyatlar = {f.para_birimi: f.fiyat for f in s.fiyatlar.filter(silindi=False)}
        self.assertEqual(fiyatlar, {"TRY": Decimal("12000")})

    def test_fiyat_listesi_negatif_red(self):
        _, alt, _, adet, kg = _veri()
        with self.assertRaises(StokHatasi):
            self._kur(alt, adet, kg, satis_urunu=True, fiyat_usd="-1")

    def test_satis_urunu_degilse_fiyat_listesi_temizlenir(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12000", fiyat_usd="350")
        self.assertEqual(s.fiyatlar.filter(silindi=False).count(), 2)
        stok_guncelle(s, ad=s.ad, uretim_birimi_id=s.uretim_birimi_id,
                     fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                     kdv_id=s.kdv_id, satinalma_urunu=True, satis_urunu=False)
        self.assertEqual(s.fiyatlar.filter(silindi=False).count(), 0)

    def test_guncellemede_fiyat_degistirilir_ve_temizlenir(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12000", fiyat_usd="350")
        stok_guncelle(s, ad=s.ad, uretim_birimi_id=s.uretim_birimi_id,
                     fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                     kdv_id=s.kdv_id, satinalma_urunu=False, uretim_urunu=True,
                     satis_urunu=True, fiyat_try="13000")   # USD boş -> silinir
        fiyatlar = {f.para_birimi: f.fiyat for f in s.fiyatlar.filter(silindi=False)}
        self.assertEqual(fiyatlar, {"TRY": Decimal("13000")})

    def test_stok_kopyala_fiyat_listesini_kopyalar(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12000", fiyat_usd="350")
        kopya = stok_kopyala(s)
        fiyatlar = {f.para_birimi: f.fiyat for f in kopya.fiyatlar.filter(silindi=False)}
        self.assertEqual(fiyatlar, {"TRY": Decimal("12000"), "USD": Decimal("350")})

    def test_db_stok_fiyat_unique_pb_kisit(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True, fiyat_try="12000")
        with self.assertRaises(IntegrityError), transaction.atomic():
            StokFiyat.objects.create(stok=s, para_birimi="TRY", fiyat=Decimal("1"))

    def test_db_stok_fiyat_negatif_kisit(self):
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StokFiyat.objects.create(stok=s, para_birimi="USD", fiyat=Decimal("-1"))

    def test_ekle_post_fiyat_listesi_kaydedilir_ve_detayda_gorunur(self):
        _, alt, _, adet, kg = _veri()
        kdv = _kdv("20")
        self.client.force_login(User.objects.create_superuser("stfyon", password="x"))
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "fiyatli urun", "kategori": alt.pk, "uretim_birimi": adet.pk,
            "fatura_birimi": kg.pk, "cevirici": "1", "kdv": kdv.pk,
            "uretim_urunu": "on", "satis_urunu": "on",
            "fiyat_try": "12.000,50", "fiyat_usd": "350",
        })
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="FİYATLİ URUN")
        fiyatlar = {f.para_birimi: f.fiyat for f in s.fiyatlar.filter(silindi=False)}
        self.assertEqual(fiyatlar, {"TRY": Decimal("12000.50"), "USD": Decimal("350")})
        d = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertContains(d, "Satış Fiyat Listesi")
        self.assertContains(d, "12.000,5000")


def _png(boyut=(800, 600), renk=(200, 30, 30, 128)):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", boyut, renk).save(buf, "PNG")
    buf.seek(0)
    return buf


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StokGorselTest(TestCase):
    """Ürün görseli (yalnız satis_urunu=True kartlarda) — banka logosuyla aynı
    desen (core/gorsel.py::kucult_webp), 1600px/%80 ile çağrılır."""

    def _kur(self, alt, adet, kg, **kw):
        kw.setdefault("kdv_id", _kdv("20").pk)
        return stok_olustur(ad=kw.pop("ad", "x"), kategori_id=alt.pk,
                            uretim_birimi_id=adet.pk, fatura_birimi_id=kg.pk,
                            cevirici=Decimal("1"), **kw)

    def test_gorsel_satis_ile_yuklenir(self):
        from core.gorsel import kucult_webp
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True,
                      gorsel=kucult_webp(_png(), max_kenar=1600, kalite=80, ad="stok"))
        self.assertTrue(s.gorsel)
        self.assertTrue(s.gorsel.name.endswith(".webp"))

    def test_gorsel_satis_degilse_kaydedilmez(self):
        from core.gorsel import kucult_webp
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satinalma_urunu=True, satis_urunu=False,
                      gorsel=kucult_webp(_png(), ad="stok"))
        self.assertFalse(s.gorsel)

    def test_guncellemede_satis_kapatilinca_gorsel_silinir(self):
        from core.gorsel import kucult_webp
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True,
                      gorsel=kucult_webp(_png(), ad="stok"))
        self.assertTrue(s.gorsel)
        stok_guncelle(s, ad=s.ad, uretim_birimi_id=s.uretim_birimi_id,
                     fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                     kdv_id=s.kdv_id, satinalma_urunu=True, satis_urunu=False)
        s.refresh_from_db()
        self.assertFalse(s.gorsel)

    def test_guncellemede_yeni_dosya_yoksa_mevcut_gorsel_korunur(self):
        from core.gorsel import kucult_webp
        _, alt, _, adet, kg = _veri()
        s = self._kur(alt, adet, kg, satis_urunu=True,
                      gorsel=kucult_webp(_png(), ad="stok"))
        eski_ad = s.gorsel.name
        stok_guncelle(s, ad=s.ad, uretim_birimi_id=s.uretim_birimi_id,
                     fatura_birimi_id=s.fatura_birimi_id, cevirici=s.cevirici,
                     kdv_id=s.kdv_id, satis_urunu=True)     # gorsel=None (yeni dosya yok)
        s.refresh_from_db()
        self.assertEqual(s.gorsel.name, eski_ad)


class KullanimdaSilmeKorumaTest(TestCase):
    """#9: Stok kullandığı KDV/tevkifat/cari soft-delete edilemez."""

    def _stok(self, **kw):
        _, alt, _, adet, kg = _veri()
        kw.setdefault("kdv_id", _kdv("20").pk)
        return stok_olustur(ad="x", kategori_id=alt.pk, uretim_birimi_id=adet.pk,
                            fatura_birimi_id=kg.pk, cevirici=Decimal("1"), **kw)

    def test_kullanilan_kdv_silinemez(self):
        k = _kdv("20")
        self._stok(kdv_id=k.pk)
        with self.assertRaises(TanimHatasi):
            kdv_orani_sil(k)

    def test_kullanilan_tevkifat_silinemez(self):
        t = _tevkifat()
        self._stok(tevkifat_id=t.pk)
        with self.assertRaises(TanimHatasi):
            tevkifat_orani_sil(t)

    def test_kullanilan_cari_silinemez(self):
        c = _cari()
        self._stok(tedarikci_id=c.pk)
        with self.assertRaises(CariHatasi):
            cari_sil(c)

    def test_kullanilmayan_kdv_silinebilir(self):
        k = _kdv("20")
        kdv_orani_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)


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

    def test_liste_arama_kod(self):
        stok_olustur(ad="levha", kategori_id=self.alt.pk, uretim_birimi_id=self.adet.pk,
                     fatura_birimi_id=self.kg.pk, cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        stok_olustur(ad="boru", kategori_id=self.alt2.pk, uretim_birimi_id=self.adet.pk,
                     fatura_birimi_id=self.kg.pk, cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"), {"ara": "150-10"})
        self.assertContains(r, "LEVHA")
        self.assertNotContains(r, "BORU")

    def test_liste_arama_ad_tr_buyuk_harf(self):
        stok_olustur(ad="alüminyum profil", kategori_id=self.alt.pk,
                     uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                     cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"), {"ara": "profil"})
        self.assertContains(r, "ALÜMİNYUM PROFİL")

    def test_liste_arama_kategori_adi(self):
        stok_olustur(ad="sac levha", kategori_id=self.alt2.pk, uretim_birimi_id=self.adet.pk,
                     fatura_birimi_id=self.kg.pk, cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"), {"ara": "çelik"})
        self.assertContains(r, "SAC LEVHA")

    def test_liste_arama_sonuc_yok(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"), {"ara": "olmayan-kod-xyz"})
        self.assertContains(r, "eşleşen stok yok")

    def test_ekle_post_otomatik_kod(self):
        k = _kdv("20")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "alüminyum levha", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "3", "kdv": str(k.pk), "uretim_urunu": "on"})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="ALÜMİNYUM LEVHA")
        self.assertEqual((s.kod, s.kategori_id, s.kdv_id),
                         ("150-10-0001", self.alt.pk, k.pk))

    def test_ekle_post_alis_fiyati_kaydedilir_ve_detayda_gorunur(self):
        k = _kdv("20")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "profil", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "3", "kdv": str(k.pk), "uretim_urunu": "on",
            "alis_fiyati": "45,75", "alis_fiyati_pb": "USD"})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="PROFİL")
        self.assertEqual((s.alis_fiyati, s.alis_fiyati_pb), (Decimal("45.75"), "USD"))
        d = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertContains(d, "45,75")
        self.assertContains(d, "USD")

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
            "fatura_birimi": str(self.adet.pk), "cevirici": "2,5", "kdv": str(k10.pk),
            "uretim_urunu": "on"})
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertEqual((s.ad, s.kod, s.cevirici, s.kdv_id),
                         ("LEVHA YENİ", "150-10-0001", Decimal("2.500000"), k10.pk))

    def test_sil_post(self):
        s = stok_olustur(ad="levha", kategori_id=self.alt.pk,
                         uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.kg.pk,
                         cevirici=Decimal("1"), kdv_id=_kdv("20").pk)
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_sil", args=[s.pk]))
        self.assertEqual(r.status_code, 302)
        s.refresh_from_db()
        self.assertTrue(s.silindi)

    def test_kopyala_post(self):
        s = self._ornek_stok()
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_kopyala", args=[s.pk]))
        kopya = Stok.objects.exclude(pk=s.pk).get(ad=f"{s.ad} KOPYA")
        self.assertRedirects(r, reverse("core:stok_detay", args=[kopya.pk]))
        self.assertEqual((kopya.kategori_id, kopya.kdv_id, kopya.cevirici),
                         (s.kategori_id, s.kdv_id, s.cevirici))
        self.assertNotEqual(kopya.kod, s.kod)

    def test_kopyala_get_kopyalamiyor(self):
        s = self._ornek_stok()
        onceki = Stok.objects.count()
        self.client.force_login(self.yetkili)
        self.client.get(reverse("core:stok_kopyala", args=[s.pk]))
        self.assertEqual(Stok.objects.count(), onceki)

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

    def test_kategori_sirasi_ust_kod_once(self):
        """Alt kategori seçimi önce ÜST kodu, sonra ALT kodu ile sıralanmalı
        (yalnız alt koduna göre sıralarsa farklı üstlerin altları karışır)."""
        ust_b = kategori_olustur(ad="ust b", kod="200")
        kategori_olustur(ad="alt kucuk kod", kod="10", ust_id=ust_b.pk)
        ust_a = kategori_olustur(ad="ust a", kod="100")
        kategori_olustur(ad="alt buyuk kod", kod="20", ust_id=ust_a.pk)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:stok_ekle"))
        etiketler = [f"{k.ust.kod}-{k.kod}"
                    for k in r.context["form"].fields["kategori"].queryset]
        self.assertLess(etiketler.index("100-20"), etiketler.index("200-10"))

    def test_ekle_kdv_bos_reddedilir(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "kdvsiz", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Stok.objects.filter(ad="KDVSIZ").exists())

    def test_ekle_grupsuz_reddedilir(self):
        """Hiçbir grup (Satınalma/Üretim/Satış) işaretlenmeden kart kaydedilemez."""
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "grupsuz", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "1", "kdv": str(_kdv("20").pk)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "En az bir grup")
        self.assertFalse(Stok.objects.filter(ad="GRUPSUZ").exists())

    def test_ekle_satis_teknik_alanlari_kaydeder_ve_detayda_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "a tipi merdiven", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "1", "kdv": str(_kdv("20").pk), "satis_urunu": "on",
            "model_kodu": "a21", "basamak_sayisi": "5", "yukseklik": "100",
            "acik_derinlik": "90", "taban_genisligi": "43", "kapali_boy": "171",
            "agirlik": "4,30", "azami_yuk": "150", "cbm": "0,075", "yukleme_20dc": "440",
            "yukleme_40hq": "1010", "yukleme_tir": "1220"})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="A TİPİ MERDİVEN")
        self.assertEqual(s.model_kodu, "A21")
        self.assertEqual(s.basamak_sayisi, 5)
        self.assertEqual(s.agirlik, Decimal("4.30"))
        d = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertContains(d, "Teklif / Teknik Özellikler")
        self.assertContains(d, "A21")
        self.assertContains(d, "4,30")
        self.assertContains(d, "1010")

    def test_detay_satis_degilse_teknik_bolum_gorunmez(self):
        s = self._ornek_stok()          # yalnız uretim_urunu=True (varsayılan)
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertNotContains(r, "Teklif / Teknik Özellikler")

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_ekle_post_gorsel_yukler_ve_detayda_gorunur(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        dosya = SimpleUploadedFile("urun.png", _png().read(), content_type="image/png")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:stok_ekle"), {
            "ad": "gorselli urun", "kategori": str(self.alt.pk),
            "uretim_birimi": str(self.adet.pk), "fatura_birimi": str(self.kg.pk),
            "cevirici": "1", "kdv": str(_kdv("20").pk), "satis_urunu": "on",
            "gorsel": dosya})
        self.assertEqual(r.status_code, 302)
        s = Stok.objects.get(ad="GORSELLİ URUN")
        self.assertTrue(s.gorsel)
        self.assertTrue(s.gorsel.name.endswith(".webp"))
        d = self.client.get(reverse("core:stok_detay", args=[s.pk]))
        self.assertContains(d, s.gorsel.url)
