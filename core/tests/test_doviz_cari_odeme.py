"""Döviz carilere yapılan TL ödemeler döviz borcundan düşer: banka/kasa/kart/kesinti (hangi dövize sayılacağı seçimi), döviz cariye
verilen çek (ara hesap → ödeme günü TCMB alış kuruyla düşüm; karşılıksız), ve dönüşüm komutu (Formal/Argema)."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import (BankaHesap, Cari, CekHesapAyari, CekSenet, HesapPlani, Kasa, Kur, YevmiyeFisi, YevmiyeSatir)
from core.services import banka_hareket as bh
from core.services import cari_kesinti as ck
from core.services import cek as cek_servis
from core.services import hesap_plani as hp
from core.services import kasa_hareket as kh
from core.services import kredi_karti_hareket as kk
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur, kasa_olustur, kredi_karti_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class DovizCariBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g, usd, eur in ((D(2026, 5, 22), "39", "43"), (D(2026, 6, 1), "40", "44"), (D(2026, 7, 1), "42", "46"), (D(2026, 8, 1), "45", "49")):
            Kur.objects.create(tarih=g, usd_alis=Dc(usd), eur_alis=Dc(eur), usd_efektif_satis=Dc(usd) + 5, eur_efektif_satis=Dc(eur) + 5)
        _hesap("102.01.0001", "TL BANKA")
        _hesap("100.01", "KASA")
        _hesap("309.02", "KART", kalem="KVYK")
        _hesap("320.10.0001", "FORMAL", kalem="KVYK")
        _hesap("320.01", "SATICI TL", kalem="KVYK")
        _hesap("103.01.0001", "VERİLEN ÇEKLER", kalem="KVYK")
        _hesap("321.01.0001", "VERİLEN SENETLER", kalem="KVYK")
        _hesap("770.01", "GİDER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("646", "KUR FARKI KÂRI", kalem="D", grup="GELIR_TABLOSU")
        _hesap("656", "KUR FARKI ZARARI", kalem="D", grup="GELIR_TABLOSU")
        _hesap("159", "VERİLEN SİPARİŞ AVANSLARI")
        hp.hesap_olustur(kod="159.20", ad="DÖVİZ CARİ ÇEK", ust_kodu="159")
        hp.hesap_olustur(kod="159.20.0001", ad="ARA", ust_kodu="159.20")
        _hesap("500", "SERMAYE", kalem="OZK")
        hp.hesap_olustur(kod="500.10", ad="ÖDENMİŞ SERMAYE", ust_kodu="500")
        hp.hesap_olustur(kod="500.10.0001", ad="NURİ ÖZER", ust_kodu="500.10")
        cls.su = User.objects.create_superuser("dc", password="x")
        cls.formal = Cari.objects.create(kod="320-10-0001", unvan="FORMAL", muhasebe_kodu="320.10.0001", para_birimi="USD",
                                         kur_tipi=Cari.KurTipi.EFEKTIF_SATIS)
        cls.tl_cari = Cari.objects.create(kod="S1", unvan="SATICI TL", muhasebe_kodu="320.01", para_birimi="TRY")
        cls.nuri = Cari.objects.create(kod="500-10-0001", unvan="NURİ ÖZER", muhasebe_kodu="500.10.0001", para_birimi="TRY")
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.kasa = kasa_olustur(ad="k", para_birimi="TRY", muhasebe_kodu="100.01", kullanici=cls.su)
        cls.kart = kredi_karti_olustur(ad="kart", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)
        # Formal: 1.000 USD @40 + 500 EUR @44 borcumuz (haziran)
        fis_olustur(tarih=D(2026, 6, 1), aciklama="alış usd", satirlar=[
            SatirGirdi(hesap_kodu="770.01", taraf="B", islem_tutari="40000"),
            SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="1000", islem_pb="USD", islem_kuru="40")])
        fis_olustur(tarih=D(2026, 6, 1), aciklama="alış eur", satirlar=[
            SatirGirdi(hesap_kodu="770.01", taraf="B", islem_tutari="22000"),
            SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="500", islem_pb="EUR", islem_kuru="44")])

    def _cari_satir(self, fis, kod="320.10.0001"):
        return fis.satirlar.filter(hesap_id=kod, ana_satir__isnull=True, silindi=False).get()


class TlOdemeDovizeCevrilirTest(DovizCariBase):
    def test_banka_cari_odeme_varsayilan_ana_para_birimi_usd(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="8400", tarih=D(2026, 7, 1),
                               kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("USD", Dc("200.00"), Dc("42.000000")))     # 8400 / 42 (TCMB alış)
        banka = f.satirlar.get(hesap_id="102.01.0001")
        self.assertEqual(banka.alacak, Dc("8400.00"))                                                          # TL aynı
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))
        q = sum((x.islem_tutari if x.alacak else -x.islem_tutari) for x in YevmiyeSatir.objects.filter(
            hesap_id="320.10.0001", islem_pb="USD", silindi=False, fis__silindi=False))
        self.assertEqual(q, Dc("800.00"))                                                                        # USD borç 1000 → 800

    def test_kur_farki_motoru_havuzu_isler(self):
        # borç kuru 40, ödeme günü kuru 42 → ortalama (40) kuruyla yazılır, TL fark kur farkı zararı (656)
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="8400", tarih=D(2026, 7, 1),
                               kullanici=self.su)
        self.assertEqual(self._cari_satir(f).borc, Dc("8000.00"))
        zarar = f.satirlar.filter(hesap_id="656")
        self.assertEqual(zarar.count(), 1)
        self.assertEqual(zarar.get().borc, Dc("400.00"))

    def test_hangi_doviz_secimi_ve_tl_havuzu(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_pb="EUR")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("EUR", Dc("100.00"), Dc("46.000000")))
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="1000", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_pb="TRY")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.borc), ("TRY", Dc("1000.00")))                                       # çevirme yok

    def test_tl_carisinde_degisiklik_yok_ve_tahsilat_etkilenmez(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.tl_cari, tutar="500", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_pb="USD")
        self.assertEqual(self._cari_satir(f, "320.01").islem_pb, "TRY")
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_tahsilat", karsi=self.formal, tutar="500", tarih=D(2026, 7, 1),
                               kullanici=self.su)
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")                                                  # yalnız ödeme çevrilir

    def test_kur_yoksa_hata_ve_kayit_olusmaz(self):
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(bh.BankaHareketHatasi):
            bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="100", tarih=D(2026, 7, 4),
                               kullanici=self.su)
        self.assertEqual(YevmiyeFisi.objects.count(), n)

    def test_kasa_ve_kredi_karti_harcamasi(self):
        f = kh.hareket_olustur(kasa=self.kasa, tip="cari_odeme", karsi=self.formal, tutar="9000", tarih=D(2026, 8, 1), kullanici=self.su,
                               sayilan_pb="EUR")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari), ("EUR", Dc("183.67")))
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.formal, tutar="4500", tarih=D(2026, 8, 1), kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("USD", Dc("100.00"), Dc("45.000000")))

    def test_kesinti_ortak_odedi_tl_dovize_cevrilir(self):
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 7, 1), tutar="8400", karsi_cari=self.nuri, kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari), ("USD", Dc("200.00")))
        self.assertEqual(f.satirlar.get(hesap_id="500.10.0001").alacak, Dc("8400.00"))                        # ortak TL aynı
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 7, 1), tutar="1000", karsi_cari=self.nuri, kullanici=self.su,
                               sayilan_pb="TRY")
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")


class DovizCariCekTest(DovizCariBase):
    def setUp(self):
        cek_servis.hesap_ayari_kaydet({"verilen_cek": "103.01.0001", "verilen_senet": "321.01.0001",
                                       "doviz_cari_ara": "159.20.0001"}, kullanici=self.su)

    def _cikis(self, cari, tutarlar=("790000",)):
        return cek_servis.firma_cikis_bordrosu_olustur(
            cari_id=cari.pk, tarih=D(2026, 6, 1), para_birimi="TRY", kullanici=self.su,
            satirlar=[{"tip": "CEK", "tutar": t, "vade": D(2026, 12, 31)} for t in tutarlar])

    def test_cikista_cari_borcu_dusmez_ara_hesapta_bekler(self):
        b = self._cikis(self.formal)
        s = {x.hesap_id: (x.borc, x.alacak) for x in b.fisler.get().satirlar.all()}
        self.assertEqual(s["159.20.0001"], (Dc("790000.00"), Dc("0.00")))
        self.assertEqual(s["103.01.0001"], (Dc("0.00"), Dc("790000.00")))
        self.assertNotIn("320.10.0001", s)
        self.assertTrue(b.cek_senetler.get().ara_hesapta)
        self.assertEqual(_bak("320.10.0001"), Dc("-62000.00"))                                                   # cari bakiyesi aynı

    def test_tl_carisinde_normal_akis_ara_hesap_yok(self):
        b = self._cikis(self.tl_cari)
        s = {x.hesap_id for x in b.fisler.get().satirlar.all()}
        self.assertIn("320.01", s)
        self.assertFalse(b.cek_senetler.get().ara_hesapta)

    def test_ara_hesap_tanimsizsa_hata(self):
        a = CekHesapAyari.get()
        a.doviz_cari_ara = None
        a.save()
        with self.assertRaises(cek_servis.CekHatasi):
            self._cikis(self.formal)
        self.assertEqual(CekSenet.objects.count(), 0)

    def test_odeme_gunu_alis_kuruyla_cari_doviz_borcundan_duser(self):
        b = self._cikis(self.formal, ("84000",))
        cek_ids = list(b.cek_senetler.values_list("pk", flat=True))
        ob = cek_servis.odeme_bordrosu_olustur(banka_hesap_id=self.banka.pk, tarih=D(2026, 8, 1), cek_ids=cek_ids,
                                               kullanici=self.su)
        fis = ob.fisler.get()
        cari = fis.satirlar.filter(hesap_id="320.10.0001", ana_satir__isnull=True).get()
        self.assertEqual((cari.islem_pb, cari.islem_tutari, cari.islem_kuru), ("USD", Dc("1866.67"), Dc("45.000000")))   # 84.000 / 45 (ödeme günü)
        self.assertEqual(_bak("159.20.0001"), Dc("0"))                                                          # ara hesap kapandı
        self.assertEqual(_bak("103.01.0001"), Dc("0"))
        self.assertEqual(fis.satirlar.get(hesap_id="102.01.0001").alacak, Dc("84000.00"))                       # banka TL çıkışı
        self.assertEqual(sum(x.borc for x in fis.satirlar.all()), sum(x.alacak for x in fis.satirlar.all()))

    def test_karsiliksiz_ara_hesabi_alacaklandirir_cari_dokunulmaz(self):
        b = self._cikis(self.formal)
        cek_ids = list(b.cek_senetler.values_list("pk", flat=True))
        kb = cek_servis.firma_karsiliksiz_bordrosu_olustur(tarih=D(2026, 7, 1), cek_ids=cek_ids, kullanici=self.su)
        s = {x.hesap_id: (x.borc, x.alacak) for x in kb.fisler.get().satirlar.all()}
        self.assertEqual(s["159.20.0001"], (Dc("0.00"), Dc("790000.00")))
        self.assertEqual(s["103.01.0001"], (Dc("790000.00"), Dc("0.00")))
        self.assertNotIn("320.10.0001", s)
        self.assertEqual(_bak("159.20.0001"), Dc("0"))


class DonusumKomutuTest(DovizCariBase):
    """Formal TL ödemeleri USD→EUR sırasıyla döviz borcuna sayılır; Argema EUR olur, 100.000 TL → EUR, çek bordrosu ara hesaba."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("320.30.0001", "ARGEMA", kalem="KVYK")
        cls.argema = Cari.objects.create(kod="320-30-0001", unvan="ARGEMA", muhasebe_kodu="320.30.0001", para_birimi="TRY")

    def _veri(self):
        # Formal: TL ödemeler (kart/banka) — eski kural: TL havuzu
        self.f1 = fis_olustur(tarih=D(2026, 7, 1), aciklama="ön ödeme", kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
            SatirGirdi(hesap_kodu="320.10.0001", taraf="B", islem_tutari="21000"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="21000")])
        self.f2 = fis_olustur(tarih=D(2026, 8, 1), aciklama="ödeme 2", kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
            SatirGirdi(hesap_kodu="320.10.0001", taraf="B", islem_tutari="9000"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="9000")])
        # Argema: 100.000 TL banka ödemesi + çek bordrosu (2 × 790.000) TL carisi olarak
        self.f300 = fis_olustur(tarih=D(2026, 6, 1), aciklama="argema", kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
            SatirGirdi(hesap_kodu="320.30.0001", taraf="B", islem_tutari="100000"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="100000")])
        self.b9 = cek_servis.firma_cikis_bordrosu_olustur(
            cari_id=self.argema.pk, tarih=D(2026, 6, 1), para_birimi="TRY", kullanici=self.su,
            satirlar=[{"tip": "CEK", "tutar": "790000", "vade": D(2026, 12, 31)},
                      {"tip": "CEK", "tutar": "790000", "vade": D(2027, 3, 31)}]) \
            if False else None

    def _komut(self, *ek):
        from core.management.commands import doviz_cari_odeme_donustur as m
        out = StringIO()
        with mock.patch.object(m, "ARGEMA_100K_FIS", (self.f300.yil, self.f300.fis_no)):
            call_command("doviz_cari_odeme_donustur", "--ara-hesap", "159.20.0001", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_formal_usd_argema_eur(self):
        cek_servis.hesap_ayari_kaydet({"verilen_cek": "103.01.0001", "doviz_cari_ara": "159.20.0001"}, kullanici=self.su)
        self._veri()
        # Argema çek bordrosu: eski kural (TL carisi iken) — ara hesap boşken TL cari satırı yazılır
        a = CekHesapAyari.get()
        a.doviz_cari_ara = None
        a.save()
        b9 = cek_servis.firma_cikis_bordrosu_olustur(
            cari_id=self.argema.pk, tarih=D(2026, 5, 22), para_birimi="TRY", kullanici=self.su,
            satirlar=[{"tip": "CEK", "tutar": "790000", "vade": D(2026, 12, 31)},
                      {"tip": "CEK", "tutar": "790000", "vade": D(2027, 3, 31)}])
        fis181 = b9.fisler.get()
        banka_once = _bak("102.01.0001")
        formal_tl_once = _bak("320.10.0001")
        with mock.patch("core.management.commands.doviz_cari_odeme_donustur.ARGEMA_CEK_BORDRO_FIS", (fis181.yil, fis181.fis_no)):
            c = self._komut()
            self.assertIn("DRY-RUN", c)
            self.assertEqual(Cari.objects.get(pk=self.argema.pk).para_birimi, "TRY")                         # dry-run yazmadı
            self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="320.10.0001", islem_pb="TRY").count(), 2)
            c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        # Formal: 21.000 TL @42 = 500 USD; 9.000 TL @45 = 200 USD → USD borcu 1.000 → 300
        usd = [(s.islem_tutari, s.islem_kuru) for s in YevmiyeSatir.objects.filter(
            hesap_id="320.10.0001", islem_pb="USD", borc__gt=0, ana_satir__isnull=True).order_by("fis__tarih")]
        self.assertEqual(usd, [(Dc("500.00"), Dc("42.000000")), (Dc("200.00"), Dc("45.000000"))])
        self.assertFalse(YevmiyeSatir.objects.filter(hesap_id="320.10.0001", islem_pb="TRY").exists())
        # Argema EUR + 100.000 TL → EUR + çekler ara hesapta
        arg = Cari.objects.get(pk=self.argema.pk)
        self.assertEqual(arg.para_birimi, "EUR")
        s = YevmiyeSatir.objects.get(hesap_id="320.30.0001", fis=self.f300, ana_satir__isnull=True)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru, s.borc), ("EUR", Dc("2272.73"), Dc("44.000000"), Dc("100000.00")))
        self.assertEqual(_bak("159.20.0001"), Dc("1580000.00"))
        self.assertEqual(_bak("320.30.0001"), Dc("100000.00"))                                          # yalnız 100.000 TL cari satırı kaldı
        self.assertTrue(all(c.ara_hesapta for c in CekSenet.objects.all()))
        self.assertEqual(_bak("102.01.0001"), banka_once)                                                  # banka bakiyesi aynı
        # mizan dengede
        agg = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False)
        self.assertEqual(sum(x.borc for x in agg), sum(x.alacak for x in agg))

    def test_eslesmeyen_satir_varsa_uygula_iptal(self):
        self._veri()
        a = CekHesapAyari.get()
        a.doviz_cari_ara = None
        a.save()
        out = StringIO()
        with self.assertRaises(CommandError):
            with mock.patch("core.management.commands.doviz_cari_odeme_donustur.ARGEMA_100K_FIS", (self.f300.yil, self.f300.fis_no)):
                call_command("doviz_cari_odeme_donustur", "--ara-hesap", "159.20.0001", "--uygula", stdout=out)   # çek bordrosu yok
        self.assertEqual(Cari.objects.get(pk=self.argema.pk).para_birimi, "TRY")

    def test_cari_filtresi_yalniz_argema_formal_dokunulmaz(self):
        self._veri()
        cek_servis.hesap_ayari_kaydet({"verilen_cek": "103.01.0001"}, kullanici=self.su)      # ara hesap henüz tanımsız (komut atar)
        b9 = cek_servis.firma_cikis_bordrosu_olustur(
            cari_id=self.argema.pk, tarih=D(2026, 5, 22), para_birimi="TRY", kullanici=self.su,
            satirlar=[{"tip": "CEK", "tutar": "790000", "vade": D(2026, 12, 31)},
                      {"tip": "CEK", "tutar": "790000", "vade": D(2027, 3, 31)}])
        fis181 = b9.fisler.get()
        with mock.patch("core.management.commands.doviz_cari_odeme_donustur.ARGEMA_CEK_BORDRO_FIS", (fis181.yil, fis181.fis_no)):
            c = self._komut("--cari", "320.30.0001", "--aciklama-dovizine-uy", "--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertIn("Kapsam: 320.30.0001", c)
        self.assertEqual(Cari.objects.get(pk=self.argema.pk).para_birimi, "EUR")
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="320.10.0001", islem_pb="TRY").count(), 2)      # Formal'e dokunulmadı
        self.assertFalse(YevmiyeSatir.objects.filter(hesap_id="320.10.0001", islem_pb="USD", borc__gt=0).exists())
        self.assertEqual(_bak("159.20.0001"), Dc("1580000.00"))
        self.assertTrue(all(x.ara_hesapta for x in CekSenet.objects.all()))

