"""Satış faturasında HESAP satırı (alış iadesi: gider/258 alacak), DEMİRBAŞ satışı (kâr 649 / zarar 770.04 /
257 amortisman; kart Satıldı; fatura silinince geri) ve satır tipi görünürlüğü."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (Cari, DuranVarlik, EkranYetki, Fatura, FaturaTipi, HesapPlani, KdvOrani, Kur,
                         YevmiyeFisi)
from core.services import duran_varlik as dv_servis
from core.services import fatura as fs
from core.services.yatirim_projesi import proje_olustur, proje_toplami
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class SatisHesapDemirbasTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 3, 10), D(2026, 4, 10)):
            Kur.objects.create(tarih=g, usd_alis=Dc("30"))
        for kod, ad in (("191.20", "İND KDV 20"), ("391.20", "HESAPLANAN KDV 20"), ("191.01", "İND KDV 1"),
                        ("391.01", "HESAPLANAN KDV 1"), ("120.01", "ALICI"), ("120.02", "ALICI USD"),
                        ("320.01", "SATICI"), ("770.01", "GENEL GİDER"), ("257", "BİRİKMİŞ AMORTİSMAN (-)"),
                        ("253", "TESİS MAKİNE"), ("771", "DİĞER")):
            _hesap(kod, ad)
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("649", "DİĞER OLAĞAN GELİR VE KÂRLAR", kalem="E", grup="GELIR_TABLOSU")
        _hesap("770.04", "DEMİRBAŞ SATIŞ ZARARI", kalem="C", grup="GELIR_TABLOSU")
        cls.kdv0 = KdvOrani.objects.create(aciklama="%00", oran=Dc("0"))
        cls.kdv1 = KdvOrani.objects.create(aciklama="%01", oran=Dc("1"),
                                           hesap_borc=HesapPlani.objects.get(hesap_kodu="191.01"),
                                           hesap_alacak=HesapPlani.objects.get(hesap_kodu="391.01"))
        cls.kdv20 = KdvOrani.objects.create(aciklama="%20", oran=Dc("20"),
                                            hesap_borc=HesapPlani.objects.get(hesap_kodu="191.20"),
                                            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391.20"))
        cls.satis = FaturaTipi.objects.create(ad="SATIŞ FATURASI", yon=FaturaTipi.Yon.SATIS)
        cls.iade = FaturaTipi.objects.create(ad="SATIŞ FATURASI-ALIŞ İADE", yon=FaturaTipi.Yon.SATIS)
        cls.alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI", yon=FaturaTipi.Yon.ALIS)
        cls.gider = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="C1", unvan="ALICI A", muhasebe_kodu="120.01", para_birimi="TRY")
        cls.cari_usd = Cari.objects.create(kod="C2", unvan="ALICI USD", muhasebe_kodu="120.02", para_birimi="USD")
        cls.satici = Cari.objects.create(kod="S1", unvan="SATICI", muhasebe_kodu="320.01", para_birimi="TRY")
        cls.su = User.objects.create_superuser("shd", password="x")

    def _fatura(self, satirlar, tip=None, cari=None, pb="TRY", tarih=D(2026, 3, 10), no=""):
        return fs.fatura_olustur(tip_id=(tip or self.satis).pk, cari_id=(cari or self.cari).pk, tarih=tarih,
                                 fatura_no=no, para_birimi=pb, satirlar=satirlar, kullanici=self.su)

    def _hesap_satir(self, hesap, tutar, kdv=None, proje=None, miktar="1"):
        return {"tur": "HESAP", "hesap_id": hesap, "miktar": miktar, "birim_fiyat": tutar,
                "kdv_id": (kdv or self.kdv0).pk, "yatirim_projesi_id": proje.pk if proje else None}

    def _fis(self, fatura):
        return {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak, s.islem_pb, s.islem_tutari)
                for s in fatura.fis.satirlar.filter(silindi=False)}

    def _demirbas(self, maliyet="67000", amort="0", hesap="253"):
        v = dv_servis.duran_varlik_olustur(ad="AYD TESTERE", hesap_id=hesap, aktiflestirme_tarihi=D(2026, 1, 1),
                                           maliyet=Dc(maliyet), kullanici=self.su)
        if Dc(amort):
            DuranVarlik.objects.filter(pk=v.pk).update(birikmis_amortisman=Dc(amort))
            v.refresh_from_db()
        return v

    def _dv_satir(self, v, bedel, kdv=None):
        return {"tur": "DEMIRBAS", "demirbas_id": v.pk, "miktar": "1", "birim_fiyat": bedel,
                "kdv_id": (kdv or self.kdv20).pk}


class HesapSatiriTest(SatisHesapDemirbasTestBase):
    def test_gider_hesap_satiri_alacak_kdv_391_cari_borc(self):
        f = self._fatura([self._hesap_satir("770.01", "1000", self.kdv20)], tip=self.iade)
        s = self._fis(f)
        self.assertEqual(s[("770.01", "A")][0], Dc("1000.00"))            # gideri azaltır
        self.assertEqual(s[("391.20", "A")][0], Dc("200.00"))             # KDV 391'e
        self.assertEqual(s[("120.01", "B")][0], Dc("1200.00"))            # cari borç
        self.assertEqual(f.satirlar.get().satir_tipi, "HESAP")

    def test_kdv_yuzde_1(self):
        f = self._fatura([self._hesap_satir("770.01", "1000", self.kdv1)], tip=self.iade)
        s = self._fis(f)
        self.assertEqual((s[("391.01", "A")][0], s[("120.01", "B")][0]), (Dc("10.00"), Dc("1010.00")))

    def test_doviz_faturada_hesap_satiri_kdv_sifir(self):
        f = self._fatura([self._hesap_satir("770.01", "100", self.kdv0)], tip=self.iade, cari=self.cari_usd, pb="USD")
        s = self._fis(f)
        self.assertEqual(s[("770.01", "A")], (Dc("3000.00"), "USD", Dc("100.00")))
        self.assertEqual(s[("120.02", "B")], (Dc("3000.00"), "USD", Dc("100.00")))
        self.assertEqual(f.kur, Dc("30"))

    def test_stok_ve_hesap_satiri_ayni_faturada(self):
        # stoksuz test: iki hesap satırı + KDV karışımı
        f = self._fatura([self._hesap_satir("770.01", "500", self.kdv20), self._hesap_satir("771", "100", self.kdv0)],
                         tip=self.iade)
        s = self._fis(f)
        self.assertEqual(s[("120.01", "B")][0], Dc("700.00"))

    def test_258_proje_zorunlu_aktiflesmis_secilemez_ve_proje_toplami_duser(self):
        proje = proje_olustur(ad="hat", kullanici=self.su)
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("258", "1000")], tip=self.iade)               # projesiz
        # alış (gider) faturasıyla 5.000 maliyet
        fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=D(2026, 3, 10), fatura_no="G1",
                          satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "5000", "kdv_id": self.kdv0.pk,
                                     "yatirim_projesi_id": proje.pk}])
        self.assertEqual(proje_toplami(proje), Dc("5000.00"))
        f = self._fatura([self._hesap_satir("258", "1000", self.kdv0, proje)], tip=self.iade)   # alış iadesi
        self.assertEqual(self._fis(f)[("258", "A")][0], Dc("1000.00"))
        self.assertEqual(proje_toplami(proje), Dc("4000.00"))                             # iade kadar düştü
        from core.models import YatirimProjesi
        YatirimProjesi.objects.filter(pk=proje.pk).update(durum="AKTIFLESTI")
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._hesap_satir("258", "1", self.kdv0, proje)], tip=self.iade)

    def test_gecersiz_hesap_ve_yon_kurallari(self):
        with self.assertRaises(fs.FaturaHatasi):                       # 253 hesap satırı olamaz (demirbaş satırı kullan)
            self._fatura([self._hesap_satir("253", "100")], tip=self.iade)
        with self.assertRaises(fs.FaturaHatasi):                       # alış (gider olmayan) faturada hesap satırı
            self._fatura([self._hesap_satir("770.01", "100")], tip=self.alis, cari=self.satici)

    def test_silinince_fis_gider(self):
        f = self._fatura([self._hesap_satir("770.01", "1000", self.kdv20)], tip=self.iade)
        fs.fatura_sil(f, kullanici=self.su)
        self.assertFalse(Fatura.objects.filter(pk=f.pk).exists())


class DemirbasSatisiTest(SatisHesapDemirbasTestBase):
    def test_zarar_770_04_kart_satildi_fatura_baglanir(self):
        v = self._demirbas("67000")
        f = self._fatura([self._dv_satir(v, "58333.33")])
        s = self._fis(f)
        self.assertEqual(s[("120.01", "B")][0], Dc("70000.00"))        # 58.333,33 + %20 KDV 11.666,67
        self.assertEqual(s[("391.20", "A")][0], Dc("11666.67"))
        self.assertEqual(s[("253", "A")][0], Dc("67000.00"))           # maliyetle alacak
        self.assertEqual(s[("770.04", "B")][0], Dc("8666.67"))         # zarar
        self.assertNotIn(("649", "A"), s)
        self.assertEqual(sum(x.borc for x in f.fis.satirlar.all()), sum(x.alacak for x in f.fis.satirlar.all()))
        v.refresh_from_db()
        self.assertEqual((v.durum, v.satis_tarihi, v.satis_faturasi_id), ("SATILDI", D(2026, 3, 10), f.pk))
        self.assertEqual(f.satirlar.get().satir_tipi, "DEMIRBAS")

    def test_kar_649(self):
        v = self._demirbas("67000")
        f = self._fatura([self._dv_satir(v, "80000", self.kdv0)])
        s = self._fis(f)
        self.assertEqual(s[("649", "A")][0], Dc("13000.00"))
        self.assertNotIn(("770.04", "B"), s)

    def test_birikmis_amortismanli_kart_257_borc(self):
        v = self._demirbas("67000", amort="20000")                     # defter değeri 47.000
        f = self._fatura([self._dv_satir(v, "58333.33", self.kdv0)])
        s = self._fis(f)
        self.assertEqual(s[("253", "A")][0], Dc("67000.00"))
        self.assertEqual(s[("257", "B")][0], Dc("20000.00"))
        self.assertEqual(s[("649", "A")][0], Dc("11333.33"))           # 58.333,33 − 47.000
        self.assertEqual(sum(x.borc for x in f.fis.satirlar.all()), sum(x.alacak for x in f.fis.satirlar.all()))

    def test_satilamayan_kartlar_ve_cift_satis(self):
        v = self._demirbas()
        DuranVarlik.objects.filter(pk=v.pk).update(durum="PASIF")
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._dv_satir(v, "100")])
        DuranVarlik.objects.filter(pk=v.pk).update(durum="AKTIF")
        self._fatura([self._dv_satir(v, "100")])
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._dv_satir(v, "100")])                                  # zaten satıldı
        with self.assertRaises(fs.FaturaHatasi):                                      # alış faturasında demirbaş satışı yok
            self._fatura([self._dv_satir(self._demirbas(), "100")], tip=self.alis, cari=self.satici)

    def test_fatura_silinince_kart_eski_durumuna_doner(self):
        v = self._demirbas()
        f = self._fatura([self._dv_satir(v, "58333.33")])
        fs.fatura_sil(f, kullanici=self.su)
        v.refresh_from_db()
        self.assertEqual((v.durum, v.satis_tarihi, v.satis_faturasi_id), ("AKTIF", None, None))
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="FATURA").exists())
        self._fatura([self._dv_satir(v, "100")])                                      # tekrar satılabilir

    def test_duzenleyince_kart_yeniden_isaretlenir_degisen_kart_geri_doner(self):
        v1, v2 = self._demirbas(), self._demirbas("10000")
        f = self._fatura([self._dv_satir(v1, "58333.33")])
        fs.fatura_guncelle(f, tip_id=self.satis.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10),
                           satirlar=[self._dv_satir(v1, "60000")], kullanici=self.su)   # aynı kart, yeni bedel
        v1.refresh_from_db()
        self.assertEqual(v1.durum, "SATILDI")
        self.assertEqual(self._fis(f)["120.01", "B"][0], Dc("72000.00"))
        fs.fatura_guncelle(f, tip_id=self.satis.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10),
                           satirlar=[self._dv_satir(v2, "12000")], kullanici=self.su)   # kart değişti
        v1.refresh_from_db(), v2.refresh_from_db()
        self.assertEqual((v1.durum, v2.durum, v2.satis_faturasi_id), ("AKTIF", "SATILDI", f.pk))

    def test_taslakta_satilmaz_onayda_satilir(self):
        v = self._demirbas()
        t = fs.fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 3, 10), tip_id=self.satis.pk,
                                     satirlar=[self._dv_satir(v, "58333.33")], kullanici=self.su)
        v.refresh_from_db()
        self.assertEqual(v.durum, "AKTIF")
        fs.fatura_onayla(t, kullanici=self.su)
        v.refresh_from_db()
        self.assertEqual(v.durum, "SATILDI")

    def test_satilmis_kart_pasife_alinamaz_silinemez_kontrol_raporu_hariç(self):
        v = self._demirbas()
        self._fatura([self._dv_satir(v, "58333.33")])
        v.refresh_from_db()
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.durum_degistir(v, durum="PASIF")
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.varlik_sil(v)
        self.assertFalse(dv_servis.silinebilir_mi(v))

    def test_amortisman_alani_duzenlenir(self):
        v = self._demirbas()
        dv_servis.duran_varlik_guncelle(v, ad="x", maliyet=Dc("67000"), birikmis_amortisman=Dc("5000"))
        v.refresh_from_db()
        self.assertEqual(v.birikmis_amortisman, Dc("5000.00"))
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.duran_varlik_guncelle(v, ad="x", maliyet=Dc("67000"), birikmis_amortisman=Dc("70000"))


class SatisFaturaEkranTest(SatisHesapDemirbasTestBase):
    def _post(self, satirlar_post):
        veri = {"tip": self.iade.pk, "cari": self.cari.pk, "tarih": "2026-03-10", "para_birimi": "TRY",
                "form-TOTAL_FORMS": str(len(satirlar_post)), "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, sat in enumerate(satirlar_post):
            for k, vv in sat.items():
                veri[f"form-{i}-{k}"] = vv
        return self.client.post(reverse("core:satis_fatura_ekle"), veri)

    def test_form_hesap_ve_demirbas_satiri_kaydeder_liste_ve_detayda_satir_tipi(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="satis_faturalari")
        self.client.force_login(self.su)
        v = self._demirbas()
        r = self.client.get(reverse("core:satis_fatura_ekle"))
        self.assertContains(r, "Demirbaş satışı")                      # satır türü seçicisi
        self.assertContains(r, "Hesap satırı")
        r = self._post([{"tur": "HESAP", "hesap": "770.01", "miktar": "1,000", "birim_fiyat": "1.000,0000",
                         "kdv": self.kdv20.pk},
                        {"tur": "DEMIRBAS", "demirbas": v.pk, "miktar": "1", "birim_fiyat": "58.333,3300",
                         "kdv": self.kdv20.pk}])
        f = Fatura.objects.latest("pk")
        self.assertRedirects(r, reverse("core:fatura_detay", args=[f.pk]))
        self.assertEqual({s.satir_tipi for s in f.satirlar.all()}, {"HESAP", "DEMIRBAS"})
        d = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(d, "Hesap")
        self.assertContains(d, "Demirbaş")
        self.assertContains(d, v.demirbas_kodu)
        liste = self.client.get(reverse("core:satis_faturalari"))
        self.assertContains(liste, "Satır: Hesap · Demirbaş")

    def test_form_hesap_satirinda_hesap_zorunlu_ve_258_proje_zorunlu(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="satis_faturalari")
        self.client.force_login(self.su)
        r = self._post([{"tur": "HESAP", "miktar": "1", "birim_fiyat": "100", "kdv": self.kdv0.pk}])
        self.assertContains(r, "Hesap satırı için bir hesap seçin")
        r = self._post([{"tur": "HESAP", "hesap": "258", "miktar": "1", "birim_fiyat": "100", "kdv": self.kdv0.pk}])
        self.assertContains(r, "yatırım projesi seçimi zorunludur")
        r = self._post([{"tur": "DEMIRBAS", "miktar": "1", "birim_fiyat": "100"}])
        self.assertContains(r, "satılacak kartı seçin")
        self.assertFalse(Fatura.objects.exists())


class AktiflestirmeSonrasiDemirbasTest(SatisHesapDemirbasTestBase):
    """Projeli alış iadesi olan proje aktifleştirilince: kart toplamı iade düşülmüş tutar, karta yalnız alış kalemi
    bağlanır; oluşan kart (254) satış faturasında demirbaş satırı olarak seçilebilir."""

    def test_iadeli_proje_aktiflestirme_ve_kartin_satilmasi(self):
        from core.services.yatirim_projesi import proje_aktiflestir
        _hesap("254", "TAŞITLAR")
        proje = proje_olustur(ad="araç", kullanici=self.su)
        fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=D(2026, 3, 10), fatura_no="G1",
                          satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "5000", "kdv_id": self.kdv0.pk,
                                     "yatirim_projesi_id": proje.pk}])
        self._fatura([self._hesap_satir("258", "1000", self.kdv0, proje)], tip=self.iade, cari=self.satici)
        self.assertEqual(proje_toplami(proje), Dc("4000.00"))
        h254 = HesapPlani.objects.get(hesap_kodu="254")
        proje_aktiflestir(proje, tarih=D(2026, 4, 10), kullanici=self.su,
                          satirlar=[{"hesap_id": h254.pk, "varlik_adi": "megane", "tutar": Dc("4000.00")}])
        kart = DuranVarlik.objects.get(kaynak="PROJE")
        self.assertEqual((kart.hesap_id, kart.maliyet, kart.durum), ("254", Dc("4000.00"), "AKTIF"))
        self.assertEqual(kart.fatura_satirlari.count(), 1)                       # yalnız alış kalemi bağlı
        self.assertEqual(dv_servis.baglanti_toplami(kart), Dc("5000.00"))        # alış kalemi (iade ayrı düşülmüştü)
        # kart satış faturasında seçilebilir ve satılır
        f = self._fatura([self._dv_satir(kart, "3000", self.kdv0)], cari=self.cari)
        kart.refresh_from_db()
        self.assertEqual(kart.durum, "SATILDI")
        self.assertEqual(self._fis(f)[("254", "A")][0], Dc("4000.00"))
        self.assertEqual(self._fis(f)[("770.04", "B")][0], Dc("1000.00"))        # 3.000 − 4.000
