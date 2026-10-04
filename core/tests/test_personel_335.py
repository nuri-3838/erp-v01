"""Personel carileri (kategori 335-10 PERSONELE BORÇLAR): 335 → 335.10.000N bölme/taşıma komutu ve ekranlar (Cari Ödeme, Kesinti
'ortak ödedi')."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, CariKategori, EkranYetki, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import banka_hareket as bh
from core.services import cari as cari_servis
from core.services import cari_kesinti as ck
from core.services import hesap_plani as hp
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class PersonelBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 5, 11), usd_alis=Dc("40"))
        _hesap("102.01.0001", "TL BANKA")
        _hesap("335", "PERSONELE BORÇLAR", kalem="KVYK")
        _hesap("500", "SERMAYE", kalem="OZK")
        hp.hesap_olustur(kod="500.10", ad="ÖDENMİŞ SERMAYE", ust_kodu="500")
        hp.hesap_olustur(kod="500.10.0001", ad="NURİ ÖZER", ust_kodu="500.10")
        ust = CariKategori.objects.create(ad="PERSONELLER", kod="335")
        cls.kat = CariKategori.objects.create(ad="PERSONELE BORÇLAR", kod="10", ust=ust)
        cls.su = User.objects.create_superuser("pr", password="x")
        cls.nuri = Cari.objects.create(kod="500-10-0001", unvan="NURİ ÖZER", muhasebe_kodu="500.10.0001")
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")

    def _maas(self, tutar, ack, tarih=D(2026, 5, 11)):
        f = fis_olustur(tarih=tarih, aciklama=ack, kaynak=YevmiyeFisi.Kaynak.BANKA, satirlar=[
            SatirGirdi(hesap_kodu="335", taraf="B", islem_tutari=tutar),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari=tutar)])
        f.banka_hesap = self.banka
        f.save(update_fields=["banka_hesap"])
        return f


class Personel335KomutTest(PersonelBase):
    def _komut(self, *ek):
        from core.management.commands import personel_335_detaylandir as m
        out = StringIO()
        with mock.patch.object(m, "BEKLENEN_TOPLAM", Dc("77486.80")), mock.patch.object(
                m, "BEKLENEN", {"335.10.0001": Dc("70000.00"), "335.10.0002": Dc("7486.80"), "335.10.0003": Dc("0.00")}):
            call_command("personel_335_detaylandir", "--fettah", "FETTAH YILMAZ", "--haydar", "HAYDAR KAYA", *ek, stdout=out)
        return out.getvalue()

    def _veri(self):
        self._maas("35000", "NİSAN AYI MAAŞ ÖDEMESİ (ERRAHMAN) + FAST MASRAFI")
        self._maas("35000", "HAZİRAN MAAŞ ÖDEMESİ (ERRAHMAN ALTITOK)")
        self._maas("7486.80", "AĞUSTOS MAAŞ ÖDEMESİ (FETTAH)")

    def test_dry_run_yazmaz_uygula_boler_ve_tasir(self):
        self._veri()
        banka_once = _bak("102.01.0001")
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertFalse(Cari.objects.filter(muhasebe_kodu__startswith="335.").exists())
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="335").count(), 3)
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertIn("Eşleşmeyen/belirsiz satır: 0", c)
        cariler = {x.muhasebe_kodu: (x.kod, x.unvan, x.kategori_id, x.para_birimi) for x in Cari.objects.filter(muhasebe_kodu__startswith="335.")}
        self.assertEqual(cariler["335.10.0001"][:2], ("335-10-0001", "ERRAHMAN ALTITOK"))
        self.assertEqual(cariler["335.10.0002"][1], "FETTAH YILMAZ")
        self.assertEqual(cariler["335.10.0003"][1], "HAYDAR KAYA")
        self.assertEqual(HesapPlani.objects.get(hesap_kodu="335.10.0001").hesap_adi, "ERRAHMAN ALTITOK")
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="335").count(), 0)                      # üst hesapta satır kalmadı
        self.assertEqual((_bak("335.10.0001"), _bak("335.10.0002"), _bak("335.10.0003")), (Dc("70000.00"), Dc("7486.80"), Dc("0")))
        self.assertEqual(_bak("335"), Dc("77486.80"))                                                  # 335 toplamı değişmedi
        self.assertEqual(_bak("102.01.0001"), banka_once)                                              # banka bakiyesi aynı
        self.assertEqual(raporlar.ekstre_devirli("335.10.0001").bakiye, Dc("70000.00"))                # personel ekstresi
        self.assertIn("UYGULANDI", self._komut("--uygula"))                                            # tekrar çalıştırmak zararsız

    def test_eslesmeyen_satir_varsa_uygula_iptal_hicbir_sey_yazilmaz(self):
        self._veri()
        self._maas("100", "BİLİNMEYEN KİŞİ MAAŞ")
        with self.assertRaises(CommandError):
            self._komut("--uygula")
        self.assertFalse(Cari.objects.filter(muhasebe_kodu__startswith="335.").exists())
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu__startswith="335.").exists())
        self.assertEqual(YevmiyeSatir.objects.filter(hesap_id="335").count(), 4)

    def test_uygula_soyadsiz_unvanla_reddedilir(self):
        self._veri()
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("personel_335_detaylandir", "--uygula", stdout=out)      # varsayılan FETTAH/HAYDAR soyadsız
        self.assertFalse(Cari.objects.filter(muhasebe_kodu__startswith="335.").exists())


class PersonelEkranlarTest(PersonelBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        hp.hesap_olustur(kod="335.10", ad="PERSONELE BORÇLAR", ust_kodu="335")
        cls.errahman = cari_servis.cari_olustur(unvan="ERRAHMAN ALTITOK", kategori_id=cls.kat.pk, para_birimi="TRY")

    def test_cari_odeme_personel_cari_ile(self):
        self.assertEqual(self.errahman.muhasebe_kodu, "335.10.0001")
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.errahman, tutar="35000", tarih=D(2026, 5, 11),
                               kullanici=self.su)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in f.satirlar.all()},
                         {("335.10.0001", "B"), ("102.01.0001", "A")})
        self.assertEqual(_bak("335.10.0001"), Dc("35000.00"))

    def test_kesinti_ortak_odedi_personel_borc_ortak_alacak(self):
        self.assertEqual(ck.yon_coz(self.errahman), "tedarikci")
        fis = ck.kesinti_olustur(cari=self.errahman, tarih=D(2026, 5, 11), tutar="35000", karsi_cari=self.nuri, kullanici=self.su)
        self.assertEqual({(s.hesap_id, "B" if s.borc else "A") for s in fis.satirlar.all()},
                         {("335.10.0001", "B"), ("500.10.0001", "A")})
        self.assertEqual((_bak("335.10.0001"), _bak("500.10.0001")), (Dc("35000.00"), Dc("-35000.00")))
        bilgi = ck.duzenleme_bilgisi(fis, self.errahman)
        self.assertEqual(bilgi["karsi_cari"], self.nuri)

    def test_kesinti_ekrani_personel_carisinde_acilir(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[self.errahman.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Karşı cari")
        r = self.client.post(reverse("core:cari_kesinti_ekle", args=[self.errahman.pk]), {
            "tarih": "2026-05-11", "tutar": "1.000,00", "gider": "", "karsi_cari": self.nuri.pk, "aciklama": ""})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.errahman.pk]))
