"""iptal_fis_temizle: dry-run silmez; --uygula iptal fişleri (+kredi kartı taksit planını) siler,
bağlı kaydı olan fişe ve aktif fişlere dokunmaz; mizan/bakiyeler değişmez."""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import HesapPlani, Kur, KrediKartiTaksit, SilmeKaydi, YatirimProjesi, YevmiyeFisi
from core.services.fis_sil import fis_ozeti
from core.services.finans import kredi_karti_olustur
from core.services.kredi_karti_hareket import harcama_olustur
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date


def _fis(tutar, tarih=D(2026, 6, 28)):
    return fis_olustur(tarih=tarih, aciklama="x", satirlar=[
        SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari=tutar),
        SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari=tutar)])


class IptalFisTemizleTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 6, 28), usd_alis=Decimal("40"))
        _hesap("100.01", "KASA")
        _hesap("600.01", "SATIŞ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("309.01", "KART", kalem="KVYK")
        _hesap("770.01", "GİDER", kalem="C", grup="GELIR_TABLOSU")

    def _komut(self, *ek):
        out = StringIO()
        call_command("iptal_fis_temizle", *ek, stdout=out)
        return out.getvalue()

    def setUp(self):
        self.aktif = _fis("100")
        self.bos = _fis("200")
        fis_iptal(self.bos)
        self.bagli = _fis("300")
        YatirimProjesi.objects.create(kod="YP-1", ad="hat", aktiflestirme_fisi=self.bagli)
        fis_iptal(self.bagli)
        kart = kredi_karti_olustur(ad="kart", para_birimi="TRY", muhasebe_kodu="309.01")
        self.kk = harcama_olustur(kart=kart, karsi=HesapPlani.objects.get(hesap_kodu="770.01"),
                                  tutar=Decimal("600"), tarih=D(2026, 6, 28), taksit_adedi=2,
                                  ilk_vade=D(2026, 7, 28))
        fis_iptal(self.kk)

    def test_dry_run_yazmaz(self):
        n = YevmiyeFisi.objects.count()
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertIn("DEĞİŞMEDİ", c)
        self.assertEqual(YevmiyeFisi.objects.count(), n)
        self.assertFalse(SilmeKaydi.objects.exists())

    def test_uygula_baglisiz_ve_kk_planini_siler_baglilara_dokunmaz(self):
        bos_pk, bagli_pk, kk_pk, aktif_pk = self.bos.pk, self.bagli.pk, self.kk.pk, self.aktif.pk
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=bos_pk).exists())
        self.assertFalse(YevmiyeFisi.objects.filter(pk=kk_pk).exists())
        self.assertFalse(KrediKartiTaksit.objects.exists())
        self.assertTrue(YevmiyeFisi.objects.filter(pk=bagli_pk).exists())     # yatırım projesine bağlı
        self.assertTrue(YevmiyeFisi.objects.filter(pk=aktif_pk).exists())     # aktif fiş
        self.assertIn("DOKUNULMAZ", c)
        self.assertIn("yatırım projesi", c)
        self.assertEqual(SilmeKaydi.objects.count(), 2)
        self.assertTrue(all(k.veri["temizlik"] == "iptal_fis_temizle" for k in SilmeKaydi.objects.all()))
        self.assertIn("HEPSİ DEĞİŞMEDİ", c)
        self.assertEqual(SilmeKaydi.objects.get(tutar=Decimal("200.00")).kaynak, "MANUEL")   # iptal fişin tutarı
        self.assertEqual(SilmeKaydi.objects.get(tutar=Decimal("600.00")).kaynak, "KREDI_KARTI")
