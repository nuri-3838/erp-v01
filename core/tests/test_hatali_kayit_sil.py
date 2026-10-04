"""hatali_kayit_sil: dry-run hiçbir şey silmez; --uygula iptal fişleri/boş kartları/boş cariyi siler;
aktif fiş, bağlı kayıt (fatura satırı / yevmiye satırı) olanlara dokunmaz."""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import BankaHesap, Cari, DuranVarlik, HesapPlani, YevmiyeFisi, YevmiyeSatir
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
_FIS = [(260, 189, "134757.98", D(2026, 1, 5), "İCRA DAİRESİ GELEN BEDEL (TEMİNAT İADESİ)"),
        (266, 195, "6928.64", D(2026, 1, 19), "İCRA DAİRESİ GELEN BEDEL (TEMİNAT İADESİ)"),
        (263, 192, "1849.35", D(2026, 1, 6), "VADELİ HESAP FAİZ GELİRİ")]


def _fis(pk, no, tutar, tarih, ack, banka, silindi=True):
    f = YevmiyeFisi.objects.create(pk=pk, yil=2026, fis_no=no, tarih=tarih, aciklama=ack, kaynak="BANKA",
                                   banka_hesap=banka, silindi=silindi)
    t = Decimal(tutar)
    for kod, b, a in (("102.01.0001", t, 0), ("131.01", 0, t)):
        YevmiyeSatir.objects.create(fis=f, hesap_id=kod, borc=b, alacak=a, islem_tutari=t, islem_kuru=1)
    return f


class HataliKayitSilTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("102.01.0001", "BANKA")
        _hesap("131.01", "KARŞI")
        _hesap("253.01", "DV")
        _hesap("320.30.0025", "SAKARYA VD")
        b = banka_olustur(ad="vakif")
        h4 = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        h13 = banka_hesap_olustur(banka=b, ad="vadeli", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        BankaHesap.objects.filter(pk=h13.pk).update(id=13)
        BankaHesap.objects.filter(pk=h4.pk).update(id=4)
        cls.banka, cls.vadeli = BankaHesap.objects.get(pk=4), BankaHesap.objects.get(pk=13)

    def setUp(self):
        for pk, no, tutar, tarih, ack in _FIS:
            _fis(pk, no, tutar, tarih, ack, self.vadeli if pk == 263 else self.banka)
        for kod in ("DV-0023", "DV-0024"):
            DuranVarlik.objects.create(demirbas_kodu=kod, ad="X", hesap_id="253.01", durum="PASIF", maliyet=0,
                                       aktiflestirme_tarihi=D(2026, 1, 1), kaynak="ACILIS", silindi=True)
        Cari.objects.create(pk=107, kod="320-30-0025", muhasebe_kodu="320.30.0025", unvan="SAKARYA VD")

    def _komut(self, *ek):
        out = StringIO()
        call_command("hatali_kayit_sil", *ek, stdout=out)
        return out.getvalue()

    def _say(self):
        return (YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), DuranVarlik.objects.count(),
                Cari.objects.count(), HesapPlani.objects.filter(hesap_kodu="320.30.0025").count())

    def test_dry_run_hicbir_sey_silmez_uygula_siler(self):
        once = self._say()
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(self._say(), once)
        self.assertIn("DEĞİŞMEDİ", c)
        self._komut("--uygula")
        self.assertEqual(self._say(), (0, 0, 0, 0, 0))

    def test_aktif_fis_ve_bagli_kayit_silinmez(self):
        YevmiyeFisi.objects.filter(pk=260).update(silindi=False)
        # cari hesabına yevmiye satırı bağlı -> cari + hesap kalır
        f = YevmiyeFisi.objects.get(pk=266)
        YevmiyeSatir.objects.create(fis=f, hesap_id="320.30.0025", borc=1, alacak=0, islem_tutari=1, islem_kuru=1)
        c = self._komut("--uygula")
        self.assertIn("SİLİNMEZ", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=260).exists())          # aktif: dokunulmadı
        self.assertTrue(Cari.objects.filter(pk=107).exists())
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="320.30.0025").exists())
        self.assertFalse(YevmiyeFisi.objects.filter(pk=263).exists())          # temiz olan silindi
        self.assertFalse(DuranVarlik.objects.exists())

    def test_bulunamayan_raporlanir(self):
        DuranVarlik.objects.filter(demirbas_kodu="DV-0024").delete()
        self.assertIn("DV-0024: BULUNAMADI", self._komut())
