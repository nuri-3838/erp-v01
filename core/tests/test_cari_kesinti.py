"""Cari Kesinti / Masraf hareketi: yön cari hesabından (120 → gider borç/cari alacak, 320 → tersi), düzenleme, silme
(yalnız süper kullanıcı + denetim), ekstre bakiyesi kapanır, ekranlar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, EkranYetki, Kur, SilmeKaydi, YevmiyeFisi
from core.services import cari_kesinti as ck
from core.services.raporlar import ekstre_devirli
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class CariKesintiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 9, 1), usd_alis=Dc("40"))
        Kur.objects.create(tarih=D(2026, 9, 2), usd_alis=Dc("40"))
        for kod, ad in (("120.10.0003", "ASLAN GİRAY TAŞÇI"), ("320.01", "SATICI"), ("120.02", "ALICI USD"),
                        ("102.01", "BANKA"), ("600.01", "SATIŞ"), ("770.01", "DİĞER GİDER"), ("131.01", "ORTAK")):
            _hesap(kod, ad)
        _hesap("770.03", "BANKA MASRAF GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        cls.musteri = Cari.objects.create(kod="C1", unvan="ASLAN GİRAY TAŞÇI", muhasebe_kodu="120.10.0003")
        cls.tedarikci = Cari.objects.create(kod="S1", unvan="SATICI", muhasebe_kodu="320.01")
        cls.usd = Cari.objects.create(kod="C2", unvan="USD", muhasebe_kodu="120.02", para_birimi="USD")
        cls.su = User.objects.create_superuser("ck", password="x")
        cls.sade = User.objects.create_user("sade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="cariler")

    def _s(self, fis):
        return {(s.hesap_id, "B" if s.borc else "A"): s.borc or s.alacak for s in fis.satirlar.filter(silindi=False)}

    def test_musteri_gider_borc_cari_alacak(self):
        f = ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="243,84", aciklama="güvenli ödeme",
                               kullanici=self.su)
        self.assertEqual(self._s(f), {("770.03", "B"): Dc("243.84"), ("120.10.0003", "A"): Dc("243.84")})
        self.assertEqual((f.kaynak, f.cari_id, f.aciklama), ("CARI_KESINTI", self.musteri.pk, "GÜVENLİ ÖDEME"))

    def test_tedarikci_yon_tersi(self):
        f = ck.kesinti_olustur(cari=self.tedarikci, tarih=D(2026, 9, 1), tutar="100", gider_kodu="770.01")
        self.assertEqual(self._s(f), {("770.01", "A"): Dc("100.00"), ("320.01", "B"): Dc("100.00")})

    def test_aslan_giray_senaryosu_cari_kapanir_ve_kurus_farki(self):
        # satış 1.290.000 (cari borç), alıcı 1.289.756,16 ödedi (banka), 243,84 gider → bakiye 0
        fis_olustur(tarih=D(2026, 9, 1), satirlar=[
            SatirGirdi(hesap_kodu="120.10.0003", taraf="B", islem_tutari="1290000"),
            SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari="1290000")])
        fis_olustur(tarih=D(2026, 9, 1), satirlar=[
            SatirGirdi(hesap_kodu="102.01", taraf="B", islem_tutari="1289756.16"),
            SatirGirdi(hesap_kodu="120.10.0003", taraf="A", islem_tutari="1289756.16")])
        ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="243,84")
        e = ekstre_devirli("120.10.0003", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(e.kapanis_bakiye, Dc("0.00"))
        # kuruş farkı: 0,01
        fis_olustur(tarih=D(2026, 9, 2), satirlar=[
            SatirGirdi(hesap_kodu="120.10.0003", taraf="B", islem_tutari="100.01"),
            SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari="100.01")])
        fis_olustur(tarih=D(2026, 9, 2), satirlar=[
            SatirGirdi(hesap_kodu="102.01", taraf="B", islem_tutari="100"),
            SatirGirdi(hesap_kodu="120.10.0003", taraf="A", islem_tutari="100")])
        ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 2), tutar="0,01")
        self.assertEqual(ekstre_devirli("120.10.0003", D(2026, 1, 1), D(2026, 12, 31)).kapanis_bakiye, Dc("0.00"))

    def test_gecersiz_girdiler(self):
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="1", para_birimi="USD", kur="40")   # TL carisinda döviz
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="0")
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="1", gider_kodu="102.01")   # gider değil
        self.assertFalse(YevmiyeFisi.objects.exists())

    def test_duzenle_tutar_tarih_hesap_aciklama(self):
        f = ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="10")
        ck.kesinti_guncelle(fis=f, cari=self.musteri, tarih=D(2026, 9, 2), tutar="12,50", gider_kodu="770.01",
                            aciklama="düzeltme", kullanici=self.su)
        f.refresh_from_db()
        self.assertEqual((f.tarih, f.aciklama), (D(2026, 9, 2), "DÜZELTME"))
        self.assertEqual(self._s(f), {("770.01", "B"): Dc("12.50"), ("120.10.0003", "A"): Dc("12.50")})
        with self.assertRaises(ck.CariKesintiHatasi):                                        # başka carinin fişi
            ck.kesinti_guncelle(fis=f, cari=self.tedarikci, tarih=D(2026, 9, 2), tutar="1")

    def test_sil_yalniz_superuser_denetim_kaydi(self):
        f = ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 9, 1), tutar="10")
        pk = f.pk
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_sil(fis=f, cari=self.musteri, kullanici=self.sade)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=pk).exists())
        ck.kesinti_sil(fis=f, cari=self.musteri, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=pk).exists())
        self.assertEqual(SilmeKaydi.objects.get().tutar, Dc("10.00"))

    def test_ekranlar(self):
        self.client.force_login(self.su)
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[self.musteri.pk])), "Kesinti / Masraf")
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[self.musteri.pk]))
        self.assertContains(r, "Gider BORÇ / Cari ALACAK")
        self.assertContains(r, "770.03")                                                    # varsayılan gider
        r = self.client.post(reverse("core:cari_kesinti_ekle", args=[self.musteri.pk]),
                             {"tarih": "2026-09-01", "tutar": "243,84", "gider": "770.03", "aciklama": "x"})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.musteri.pk]))
        fis = YevmiyeFisi.objects.get(kaynak="CARI_KESINTI")
        e = self.client.get(reverse("core:cari_ekstresi", args=[self.musteri.pk]),
                            {"baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertContains(e, reverse("core:cari_kesinti_duzenle", args=[self.musteri.pk, fis.pk]))
        self.assertContains(e, reverse("core:cari_kesinti_sil", args=[self.musteri.pk, fis.pk]))
        # düzenle
        d_url = reverse("core:cari_kesinti_duzenle", args=[self.musteri.pk, fis.pk])
        self.assertContains(self.client.get(d_url), "Kesinti / Masraf Düzenle")
        self.client.post(d_url, {"tarih": "2026-09-01", "tutar": "250,00", "gider": "770.03", "aciklama": ""})
        self.assertEqual(self._s(YevmiyeFisi.objects.get(pk=fis.pk))[("770.03", "B")], Dc("250.00"))
        # ham fiş ekranları yönlendirir
        self.assertRedirects(self.client.get(reverse("core:fis_duzenle", args=[fis.pk])),
                             reverse("core:cari_ekstresi", args=[self.musteri.pk]))
        # sil (onay sayfası + POST)
        s_url = reverse("core:cari_kesinti_sil", args=[self.musteri.pk, fis.pk])
        self.assertContains(self.client.get(s_url), "Kalıcı olarak sil")
        self.client.post(s_url)
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").exists())

    def test_hesap_degilse_cari_ayri_uyari(self):
        self.client.force_login(self.su)
        c = Cari.objects.create(kod="X", unvan="X", muhasebe_kodu="")
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[c.pk]))
        self.assertRedirects(r, reverse("core:cari_detay", args=[c.pk]))


class KesintiHesapKumesiTest(TestCase):
    """258 (proje zorunlu) ve gelir (64x/67x) hesapları; 770.03 davranışı değişmedi."""

    @classmethod
    def setUpTestData(cls):
        from core.models import FaturaTipi, KdvOrani
        for g in (D(2026, 3, 10), D(2026, 4, 10)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        for kod, ad in (("320.30.0024", "ÖMER ORHAN"), ("102.01", "BANKA"), ("254", "TAŞITLAR"),
                        ("120.10.0003", "MÜŞTERİ")):
            _hesap(kod, ad)
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("770.03", "BANKA MASRAF GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("649", "DİĞER OLAĞAN GELİR VE KÂRLAR", kalem="E", grup="GELIR_TABLOSU")
        _hesap("679", "DİĞER OLAĞANDIŞI GELİR VE KÂRLAR", kalem="H", grup="GELIR_TABLOSU")
        cls.kdv0 = KdvOrani.objects.create(aciklama="%00", oran=Dc("0"))
        cls.gider = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.omer = Cari.objects.create(kod="S1", unvan="ÖMER ORHAN", muhasebe_kodu="320.30.0024")
        cls.musteri = Cari.objects.create(kod="C1", unvan="MÜŞTERİ", muhasebe_kodu="120.10.0003")
        cls.su = User.objects.create_superuser("kh", password="x")

    def _proje_ve_fatura(self, tutar="5000"):
        from core.services import fatura as fs
        from core.services.yatirim_projesi import proje_olustur
        proje = proje_olustur(ad="araç", kullanici=self.su)
        fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.omer.pk, tarih=D(2026, 3, 10), fatura_no="F1",
                          satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": tutar, "kdv_id": self.kdv0.pk,
                                     "yatirim_projesi_id": proje.pk}])
        return proje

    def _s(self, fis):
        return {(s.hesap_id, "B" if s.borc else "A"): s.borc or s.alacak for s in fis.satirlar.filter(silindi=False)}

    def test_320_cari_258_proje_cari_borc_258_alacak_proje_toplami_duser(self):
        from core.services.yatirim_projesi import proje_toplami
        proje = self._proje_ve_fatura("5000")
        f = ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="2.770,00", gider_kodu="258",
                               yatirim_projesi_id=proje.pk, aciklama="fazla kesilen fatura farkı", kullanici=self.su)
        self.assertEqual(self._s(f), {("320.30.0024", "B"): Dc("2770.00"), ("258", "A"): Dc("2770.00")})
        self.assertEqual(f.satirlar.get(hesap_id="258").yatirim_projesi_id, proje.pk)
        self.assertEqual(proje_toplami(proje), Dc("2230.00"))                     # 5.000 − 2.770

    def test_aktiflestirme_kesintiyi_karta_maliyet_baglamaz_toplamdan_dusurur(self):
        from core.models import DuranVarlik, HesapPlani
        from core.services.yatirim_projesi import proje_aktiflestir, proje_toplami
        proje = self._proje_ve_fatura("5000")
        ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1000", gider_kodu="258",
                           yatirim_projesi_id=proje.pk)
        self.assertEqual(proje_toplami(proje), Dc("4000.00"))
        proje_aktiflestir(proje, tarih=D(2026, 4, 10), kullanici=self.su,
                          satirlar=[{"hesap_id": HesapPlani.objects.get(hesap_kodu="254").pk, "varlik_adi": "megane",
                                     "tutar": Dc("4000.00")}])
        kart = DuranVarlik.objects.get(kaynak="PROJE")
        self.assertEqual(kart.maliyet, Dc("4000.00"))
        self.assertEqual(kart.fatura_satirlari.count(), 1)                         # yalnız alış faturası kalemi
        proje.refresh_from_db()
        self.assertEqual(proje.durum, "AKTIFLESTI")

    def test_258_proje_zorunlu_aktiflesmis_ve_258_disi_proje_reddedilir(self):
        from core.models import YatirimProjesi
        proje = self._proje_ve_fatura()
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="258")
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="770.03",
                               yatirim_projesi_id=proje.pk)
        YatirimProjesi.objects.filter(pk=proje.pk).update(durum="AKTIFLESTI")
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="258",
                               yatirim_projesi_id=proje.pk)
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").exists())

    def test_320_cari_649_ve_679_gelir_hesabi_alacak(self):
        f = ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="12,34", gider_kodu="649")
        self.assertEqual(self._s(f), {("320.30.0024", "B"): Dc("12.34"), ("649", "A"): Dc("12.34")})
        f2 = ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="679")
        self.assertEqual(self._s(f2)[("679", "A")], Dc("1.00"))

    def test_gelir_ve_gider_disindaki_hesaplar_reddedilir(self):
        with self.assertRaises(ck.CariKesintiHatasi):                               # banka seçilemez
            ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="102.01")
        with self.assertRaises(ck.CariKesintiHatasi):                               # 254 duran varlık (258 hariç) seçilemez
            ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1", gider_kodu="254")

    def test_770_03_davranisi_ayni_ve_musteri_yonu(self):
        f = ck.kesinti_olustur(cari=self.musteri, tarih=D(2026, 4, 10), tutar="243,84")
        self.assertEqual(self._s(f), {("770.03", "B"): Dc("243.84"), ("120.10.0003", "A"): Dc("243.84")})
        self.assertIsNone(f.satirlar.get(hesap_id="770.03").yatirim_projesi_id)
        f2 = ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="10")
        self.assertEqual(self._s(f2), {("770.03", "A"): Dc("10.00"), ("320.30.0024", "B"): Dc("10.00")})

    def test_duzenle_projeyi_degistirir_ve_ekranlar(self):
        from core.models import EkranYetki
        from django.urls import reverse
        from core.services.yatirim_projesi import proje_olustur, proje_toplami
        p1 = self._proje_ve_fatura("5000")
        p2 = proje_olustur(ad="makine", kullanici=self.su)
        f = ck.kesinti_olustur(cari=self.omer, tarih=D(2026, 4, 10), tutar="1000", gider_kodu="258",
                               yatirim_projesi_id=p1.pk)
        ck.kesinti_guncelle(fis=f, cari=self.omer, tarih=D(2026, 4, 10), tutar="1500", gider_kodu="258",
                            yatirim_projesi_id=p2.pk)
        self.assertEqual((proje_toplami(p1), proje_toplami(p2)), (Dc("5000.00"), Dc("-1500.00")))
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        url = reverse("core:cari_kesinti_ekle", args=[self.omer.pk])
        r = self.client.get(url)
        self.assertContains(r, "Yatırım projesi")
        r = self.client.post(url, {"tarih": "2026-04-10", "tutar": "2.770,00", "gider": "258"})
        self.assertContains(r, "258 hesabı için yatırım projesi seçilmelidir")
        r = self.client.post(url, {"tarih": "2026-04-10", "tutar": "2.770,00", "gider": "258", "yatirim_projesi": p1.pk})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.omer.pk]))
        yeni = YevmiyeFisi.objects.filter(kaynak="CARI_KESINTI").latest("pk")
        d = reverse("core:cari_kesinti_duzenle", args=[self.omer.pk, yeni.pk])
        self.assertContains(self.client.get(d), "Yatırım projesi")
