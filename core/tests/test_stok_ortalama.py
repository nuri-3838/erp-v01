"""Hareketli ağırlıklı ortalama maliyet motoru (core.services.stok_ortalama): saf hesap,
fatura/irsaliye bağlantısı (maliyeti FATURA belirler), geç gelen fatura → çıkış fişi güncelleme,
"sıfırdan yeniden hesap = saklanan değer" değişmezi, değerleme raporu ve mizan eşitliği,
irsaliyeden açılan fatura taslağında iskonto korunması."""
import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from core.models import (Birim, Cari, Depo, Fatura, FaturaTipi, HesapPlani, Kategori,
                         KategoriHesap, KdvOrani, Kur, Stok, StokHareket)
from core.services import stok_ortalama as so
from core.services.fatura import fatura_guncelle, fatura_olustur, fatura_onayla, fatura_sil
from core.services.hareket import hareket_ekle, sarf_cikis_ekle
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla

D = datetime.date
G, C = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS


def K(tur, miktar, tutar=None, usd=None, tahmini=False):
    return SimpleNamespace(tur=tur, miktar=Decimal(miktar),
                           giris_tutar_try=None if tutar is None else Decimal(tutar),
                           giris_tutar_usd=None if usd is None else Decimal(usd),
                           giris_tahmini=tahmini)


class SafHesapTest(SimpleTestCase):
    def test_ortalama_ve_cikis_degeri(self):
        s = so.hesapla([K(G, "10", "1000"), K(G, "10", "2000"), K(C, "5")])
        self.assertEqual(s[1].sonrasi_ort_try, Decimal("150.000000"))
        self.assertEqual((s[2].tutar_try, s[2].durum), (Decimal("750.00"), so.KESIN))
        self.assertEqual((s[2].sonrasi_miktar, s[2].sonrasi_deger_try),
                         (Decimal("15"), Decimal("2250.00")))

    def test_son_parca_kalan_degerin_tamamini_alir(self):
        s = so.hesapla([K(G, "3", "100"), K(C, "1"), K(C, "1"), K(C, "1")])
        toplam = sum(x.tutar_try for x in s[1:])
        self.assertEqual(toplam, Decimal("100.00"))               # yuvarlama kalıntısı yok
        self.assertEqual(s[-1].sonrasi_deger_try, Decimal("0.00"))
        self.assertIsNone(s[-1].sonrasi_ort_try)

    def test_fiyatsiz_giris_ortalamayi_degistirmez_ve_gecici_isaretlenir(self):
        s = so.hesapla([K(G, "10", "1000"), K(G, "5"), K(C, "3")])
        self.assertEqual(s[1].durum, so.GECICI)
        self.assertEqual(s[1].sonrasi_ort_try, Decimal("100.000000"))     # ortalama aynı
        self.assertEqual(s[2].durum, so.GECICI)                           # fiyatsız giriş var
        self.assertEqual(s[2].tutar_try, Decimal("300.00"))

    def test_ilk_giris_fiyatsizsa_maliyetsiz(self):
        s = so.hesapla([K(G, "10"), K(C, "2")])
        self.assertEqual((s[0].durum, s[0].tutar_try), (so.YOK, Decimal("0")))
        self.assertEqual(s[1].durum, so.YOK)

    def test_tahmini_giris_gecici_sayilir_ve_miras_kalir(self):
        s = so.hesapla([K(G, "10", "1000", tahmini=True), K(C, "2")])
        self.assertEqual((s[0].durum, s[1].durum), (so.GECICI, so.GECICI))

    def test_usd_ortalamasi(self):
        s = so.hesapla([K(G, "10", "1000", "25"), K(C, "4")])
        self.assertEqual(s[1].tutar_usd, Decimal("10.00"))


class OrtalamaTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_superuser("yon", password="x")
        for t in (D(2026, 6, 1), D(2026, 6, 5), D(2026, 6, 10), D(2026, 6, 28)):
            Kur.objects.create(tarih=t, usd_alis=Decimal("30"))
        for kod, ad, kalem in (("153.20", "TİCARİ MAL", "DV"), ("191", "KDV", "DV"),
                               ("391", "HKDV", "KVYK"), ("320.30.0001", "TED", "KVYK"),
                               ("770.01", "GİDER", "DV")):
            HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO",
                                      rapor_kalemi=kalem, parasal=True, aktif=True)
        cls.kdv = KdvOrani.objects.create(
            aciklama="G", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.kat = Kategori.objects.create(kod="KO1", ad="ORT KAT")
        cls.adet = Birim.objects.create(ad="ADET-O", kisa_ad="ADT", ondalik=0)
        cls.kg = Birim.objects.create(ad="KG-O", kisa_ad="KGO", ondalik=3)
        cls.stok = Stok.objects.create(kod="SO1", ad="ÜRÜN", kategori=cls.kat, kdv=cls.kdv,
                                       uretim_birimi=cls.adet, fatura_birimi=cls.adet)
        cls.tip = FaturaTipi.objects.create(ad="ALIŞ O", yon="ALIS")
        KategoriHesap.objects.create(kategori=cls.kat, fatura_tipi=cls.tip,
                                     hesap=HesapPlani.objects.get(hesap_kodu="153.20"))
        cls.cari = Cari.objects.create(kod="CO1", unvan="TED O", para_birimi="TRY",
                                       muhasebe_kodu="320.30.0001")
        cls.depo = Depo.objects.create(kod="DO1", ad="DEPO O")
        cls.no = [0]

    def alis(self, tarih, miktar, fiyat, stok=None):
        self.no[0] += 1
        return fatura_olustur(
            tip_id=self.tip.pk, cari_id=self.cari.pk, tarih=tarih, fatura_no=f"F{self.no[0]}",
            satirlar=[{"stok_id": (stok or self.stok).pk, "miktar": miktar, "birim_fiyat": fiyat}],
            depo_id=self.depo.pk, kullanici=self.u)

    def hareketler(self, stok=None):
        return list(so.kart_hareketleri(stok or self.stok))

    def kontrol_yeniden_hesap_esit(self, stok=None):
        """DEĞİŞMEZ: sıfırdan hesap = saklanan hareket + Stok önbelleği."""
        stok = stok or self.stok
        hs = self.hareketler(stok)
        saf = so.hesapla(hs)
        for h, s in zip(hs, saf):
            self.assertEqual((h.maliyet_durumu, h.tutar_try, h.sonrasi_miktar,
                              h.sonrasi_deger_try, h.sonrasi_ort_try),
                             (s.durum, s.tutar_try, s.sonrasi_miktar, s.sonrasi_deger_try,
                              s.sonrasi_ort_try), msg=f"hareket {h.pk}")
        stok.refresh_from_db()
        if saf:
            self.assertEqual((stok.maliyet_miktar, stok.maliyet_deger_try, stok.ort_maliyet_try),
                             (saf[-1].sonrasi_miktar, saf[-1].sonrasi_deger_try,
                              saf[-1].sonrasi_ort_try))


class FaturaGirisTest(OrtalamaTemel):
    def test_giris_tutari_fatura_tutaridir_kdv_haric_usd_dahil(self):
        self.alis(D(2026, 6, 1), "10", "100")
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, Decimal("1000.00"))           # KDV (%20) hariç
        self.assertEqual(h.giris_tutar_usd, Decimal("33.33"))             # 1000 / 30
        self.assertEqual((h.maliyet_durumu, h.birim_maliyet_try), (so.KESIN, Decimal("100.000000")))
        self.assertEqual(h.maliyet_fatura_satir.fatura.pk, Fatura.objects.get().pk)
        self.kontrol_yeniden_hesap_esit()

    def test_ortalama_iki_alisla_degisir(self):
        self.alis(D(2026, 6, 1), "10", "100")
        self.alis(D(2026, 6, 5), "10", "200")
        self.stok.refresh_from_db()
        self.assertEqual((self.stok.maliyet_miktar, self.stok.maliyet_deger_try,
                          self.stok.ort_maliyet_try),
                         (Decimal("20.000"), Decimal("3000.00"), Decimal("150.000000")))
        self.kontrol_yeniden_hesap_esit()

    def test_fatura_birimi_farkliysa_cevirici_uygulanir(self):
        boy = Stok.objects.create(kod="SO2", ad="PROFİL", kategori=self.kat, kdv=self.kdv,
                                  uretim_birimi=self.adet, fatura_birimi=self.kg,
                                  cevirici=Decimal("3.852"))
        self.alis(D(2026, 6, 1), "3852", "2", stok=boy)                   # 3852 KG @ 2 TL
        h, = self.hareketler(boy)
        self.assertEqual(h.miktar, Decimal("1000.000"))                   # BOY
        self.assertEqual(h.giris_tutar_try, Decimal("7704.00"))
        self.assertEqual(h.birim_maliyet_try, Decimal("7.704000"))        # TL / BOY

    def test_iptal_edilen_fatura_ortalamadan_cikar(self):
        f1 = self.alis(D(2026, 6, 1), "10", "100")
        self.alis(D(2026, 6, 5), "10", "200")
        fatura_sil(f1, kullanici=self.u)
        self.stok.refresh_from_db()
        self.assertEqual((self.stok.maliyet_miktar, self.stok.ort_maliyet_try),
                         (Decimal("10.000"), Decimal("200.000000")))


class GecGelenFaturaTest(OrtalamaTemel):
    def test_geriye_tarihli_alis_cikis_ve_sarf_fisini_yeniden_hesaplar(self):
        self.alis(D(2026, 6, 1), "10", "100")
        hesap_770 = HesapPlani.objects.get(hesap_kodu="770.01")
        sarf = sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 10),
                               miktar="5", karsi_hesap_id=hesap_770.pk, kullanici=self.u)
        self.assertEqual(sarf.tutar_try, Decimal("500.00"))
        self.assertEqual(sarf.fis.satirlar.filter(silindi=False).first().borc
                         + sarf.fis.satirlar.filter(silindi=False).last().borc, Decimal("500.00"))
        # Geç gelen fatura: sarftan ÖNCEKİ tarihli, daha pahalı alış
        self.alis(D(2026, 6, 5), "10", "200")
        sarf.refresh_from_db()
        self.assertEqual(sarf.tutar_try, Decimal("750.00"))               # 5 x 150
        borc = sum(s.borc for s in sarf.fis.satirlar.filter(silindi=False))
        alacak = sum(s.alacak for s in sarf.fis.satirlar.filter(silindi=False))
        self.assertEqual((borc, alacak), (Decimal("750.00"), Decimal("750.00")))
        self.kontrol_yeniden_hesap_esit()

    def test_degerleme_raporu_mizanla_tutar(self):
        self.alis(D(2026, 6, 1), "10", "100")
        hesap_770 = HesapPlani.objects.get(hesap_kodu="770.01")
        sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 10),
                        miktar="4", karsi_hesap_id=hesap_770.pk, kullanici=self.u)
        r = so.degerleme_raporu()
        satir, = r["satirlar"]
        self.assertEqual((satir["miktar"], satir["deger_try"]), (Decimal("6.000"), Decimal("600.00")))
        k153 = next(x for x in r["karsilastirma"] if x["kod"] == "153")
        self.assertEqual((k153["stok_degeri"], k153["mizan"], k153["fark"]),
                         (Decimal("600.00"), Decimal("600.00"), Decimal("0.00")))


class IrsaliyeliFaturaTest(OrtalamaTemel):
    def irsaliye_ve_fatura(self, satirlar=None):
        teklif = teklif_siparis_olustur(
            belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk, tarih=D(2026, 6, 28),
            satirlar=satirlar or [{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "100"}],
            kullanici=self.u)
        teklif_siparis_onayla(teklif, kullanici=self.u)
        siparis = teklif.donusen_belgeler.get()
        teklif_siparis_onayla(siparis, kullanici=self.u)
        irsaliye = siparis.donusen_irsaliyeler.get()
        teklif_siparis_onayla(irsaliye, kullanici=self.u)
        irsaliye.refresh_from_db()
        return irsaliye, irsaliye.fatura

    def onayla(self, fatura):
        fatura.tip = self.tip
        fatura.save(update_fields=["tip"])
        fatura_onayla(fatura, kullanici=self.u)
        fatura.refresh_from_db()

    def test_irsaliye_girisi_once_maliyetsiz_fatura_fiyatlar(self):
        irsaliye, fatura = self.irsaliye_ve_fatura()
        h, = self.hareketler()
        self.assertEqual((h.kaynak, h.giris_tutar_try, h.maliyet_durumu), ("IRSALIYE", None, so.YOK))
        self.onayla(fatura)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.giris_tutar_usd, h.maliyet_durumu),
                         (Decimal("1000.00"), Decimal("33.33"), so.KESIN))
        self.assertEqual(h.maliyet_fatura_satir.fatura_id, fatura.pk)
        self.assertEqual(StokHareket.objects.filter(silindi=False).count(), 1)   # çifte giriş yok
        self.kontrol_yeniden_hesap_esit()

    def test_ikinci_irsaliye_girisi_fiyatsizken_ortalama_bozulmaz(self):
        self.alis(D(2026, 6, 1), "10", "100")
        self.irsaliye_ve_fatura()                                        # fatura taslak: fiyatsız
        self.stok.refresh_from_db()
        self.assertEqual((self.stok.maliyet_miktar, self.stok.ort_maliyet_try),
                         (Decimal("20.000"), Decimal("100.000000")))
        gecici = StokHareket.objects.get(kaynak="IRSALIYE")
        self.assertEqual(gecici.maliyet_durumu, so.GECICI)

    def test_fatura_duzenlenince_yeniden_fiyatlanir_silinince_fiyatsiz_olur(self):
        irsaliye, fatura = self.irsaliye_ve_fatura()
        self.onayla(fatura)
        fatura_guncelle(
            fatura, tip_id=self.tip.pk, cari_id=self.cari.pk, tarih=D(2026, 6, 28),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "110"}],
            depo_id=self.depo.pk, kullanici=self.u)
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, Decimal("1100.00"))
        fatura_sil(fatura, kullanici=self.u)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu), (None, so.YOK))

    def test_ayni_stok_iki_irsaliye_kalemi_fatura_tutari_miktar_oraninda_dagitilir(self):
        irsaliye, fatura = self.irsaliye_ve_fatura(satirlar=[
            {"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "100"},
            {"stok_id": self.stok.pk, "miktar": "6", "birim_fiyat": "100"}])
        self.onayla(fatura)
        hs = self.hareketler()
        self.assertEqual(sorted(h.giris_tutar_try for h in hs), [Decimal("400.00"), Decimal("600.00")])
        self.assertEqual(sum(h.giris_tutar_try for h in hs), Decimal("1000.00"))   # fatura toplamı
        self.kontrol_yeniden_hesap_esit()

    def test_irsaliye_iskontosu_fatura_taslagina_net_fiyatla_tasinir(self):
        irsaliye, fatura = self.irsaliye_ve_fatura(satirlar=[
            {"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "100",
             "iskonto_yuzdesi": "10"}])
        satir = fatura.satirlar.get(silindi=False)
        self.assertEqual(satir.birim_fiyat, Decimal("90.000000"))
        self.onayla(fatura)
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, Decimal("900.00"))


class TekSeferlikKomutVeRaporTest(IrsaliyeliFaturaTest):
    """stok_maliyet_hesapla: mevcut (fiyatsız kalmış) hareketleri faturalarından fiyatlar;
    dry-run yazmaz; rapor ekranı açılır."""

    def _eski_duruma_getir(self):
        """Canlıdaki gibi: irsaliyeli fatura onaylı ama irsaliye girişinde maliyet YOK."""
        irsaliye, fatura = self.irsaliye_ve_fatura()
        self.onayla(fatura)
        StokHareket.objects.update(giris_tutar_try=None, giris_tutar_usd=None,
                                   maliyet_fatura_satir=None)
        so.yeniden_hesapla(self.stok)
        return fatura

    def _komut(self, *ek):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("stok_maliyet_hesapla", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_fiyatlar(self):
        self._eski_duruma_getir()
        h, = self.hareketler()
        self.assertEqual(h.maliyet_durumu, so.YOK)
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertIn("irsaliye 1", c)
        h.refresh_from_db()
        self.assertEqual(h.maliyet_durumu, so.YOK)                      # geri alındı
        self._komut("--uygula")
        h.refresh_from_db()
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu), (Decimal("1000.00"), so.KESIN))
        self.kontrol_yeniden_hesap_esit()

    def test_taslak_faturali_irsaliye_girisi_sorunlu_listelenir(self):
        self.irsaliye_ve_fatura()                                        # fatura taslak
        c = self._komut()
        self.assertIn("MALIYETSIZ / GECICI HAREKETLER (1)", c)
        self.assertIn("taslak", c)

    def test_rapor_ekrani_acilir(self):
        from django.urls import reverse
        from core.models import EkranYetki
        self.alis(D(2026, 6, 1), "10", "100")
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="stoklar")
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:stok_degerleme_raporu"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "SO1")
        self.assertContains(r, "Mizan Karşılaştırması")
        d = self.client.get(reverse("core:stok_detay", args=[self.stok.pk]))
        self.assertContains(d, "Ortalama Maliyet")
