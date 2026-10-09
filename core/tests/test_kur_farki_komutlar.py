"""kur_farki_geriye_donuk ve doviz_alis_satis_donustur: dry-run yazmaz; --uygula elle kur farkı fişlerini
kalıcı siler + havuzları yeniden hesaplar; TL hesaplardaki döviz sütunu temizlenir."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from core.management.commands import kur_farki_geriye_donuk as kfg
from core.models import Kur, SilmeKaydi, YevmiyeFisi, YevmiyeSatir
from core.services import kur_farki as kf
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _ham_fis(no, tarih, ack, satirlar, kaynak="MANUEL", banka=None):
    """Motoru devre dışı bırakarak (eski veri gibi) doğrudan fiş + satır yazar. pk DOĞAL (dizi) — sabit pk varsayılmaz."""
    f = YevmiyeFisi.objects.create(yil=2026, fis_no=no, tarih=tarih, aciklama=ack, kaynak=kaynak,
                                   banka_hesap=banka)
    for hesap, taraf, tl, pb, dvz, kur in satirlar:
        YevmiyeSatir.objects.create(fis=f, hesap_id=hesap, borc=tl if taraf == "B" else 0,
                                    alacak=tl if taraf == "A" else 0, islem_pb=pb, islem_tutari=dvz, islem_kuru=kur)
    return f


class KomutTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("102.01.0001", "VAKIF TL")
        _hesap("102.01.0002", "VAKIF USD")
        _hesap("131.01", "ORTAK")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="vakif")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.01.0002")

    def _komut(self, ad, *ek):
        out = StringIO()
        call_command(ad, *ek, stdout=out)
        return out.getvalue()

    def _eski_veri(self):
        # USD hesap: 100 USD @40 giriş, 100 USD @41 çıkış (TL 4.000 / 4.100) → TL kalıntı 100 (eski elle fiş 487 ile "temizlenmiş")
        _ham_fis(1, D(2026, 1, 5), "GİRİŞ", [("102.01.0002", "B", Dc("4000"), "USD", Dc("100"), Dc("40")),
                                             ("131.01", "A", Dc("4000"), "USD", Dc("100"), Dc("40"))])
        self.cikis = _ham_fis(2, D(2026, 2, 5), "ÇIKIŞ", [("131.01", "B", Dc("4100"), "USD", Dc("100"), Dc("41")),
                                                          ("102.01.0002", "A", Dc("4100"), "USD", Dc("100"), Dc("41"))])
        self.elle = _ham_fis(416, D(2026, 3, 5), "VAKIF USD HESABI KUR FARKI (USD BAKİYESİ SIFIR, TL KALINTI)",
                             [("102.01.0002", "B", Dc("100"), "TRY", Dc("100"), Dc("1")), ("646", "A", Dc("100"), "TRY", Dc("100"), Dc("1"))])
        # komutun canlı sabiti (elle kur farkı fişi id 487) bu testin gerçek pk'sine çevrilir; fiş no/açıklama sabitleri aynen kalır
        eski = [(self.elle.pk if pk == 487 else pk, yil, no, parca) for pk, yil, no, parca in kfg.MANUEL_KUR_FARKLARI]
        patcher = mock.patch.object(kfg, "MANUEL_KUR_FARKLARI", eski)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retro_dry_run_yazmaz_uygula_siler_ve_motorun_satirini_uretir(self):
        self._eski_veri()
        self.assertEqual(kf.havuz_bakiyesi("102.01.0002", "USD"), (Dc("0"), Dc("-100.00")))   # USD havuzunda TL kalıntı (elle fiş TRY)
        c = self._komut("kur_farki_geriye_donuk")
        self.assertIn("DRY-RUN", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=self.elle.pk).exists())
        self.assertFalse(SilmeKaydi.objects.exists())
        self._komut("kur_farki_geriye_donuk", "--uygula")
        self.assertFalse(YevmiyeFisi.objects.filter(pk=self.elle.pk).exists())             # elle fiş silindi
        k = SilmeKaydi.objects.get()
        self.assertEqual(k.veri["temizlik"], "kur_farki_geriye_donuk")
        cikis = YevmiyeFisi.objects.get(pk=self.cikis.pk)
        kfs = cikis.satirlar.filter(ana_satir__isnull=False)
        self.assertEqual([(x.hesap_id, x.borc, x.alacak) for x in kfs], [("646", Dc("0.00"), Dc("100.00"))])
        self.assertEqual(cikis.satirlar.get(hesap_id="102.01.0002").alacak, Dc("4000.00"))   # ortalama kur
        self.assertEqual(kf.havuz_bakiyesi("102.01.0002", "USD"), (Dc("0.00"), Dc("0.00")))
        self.assertEqual(sum(x.borc for x in cikis.satirlar.all()), sum(x.alacak for x in cikis.satirlar.all()))
        self.assertIn("DENGEDE", self._komut("kur_farki_geriye_donuk"))

    def test_retro_uymayan_fise_dokunmaz(self):
        self._eski_veri()
        YevmiyeFisi.objects.filter(pk=self.elle.pk).update(aciklama="BAŞKA BİR FİŞ")
        c = self._komut("kur_farki_geriye_donuk", "--uygula")
        self.assertIn("DOKUNULMAZ", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=self.elle.pk).exists())

    def test_donustur_dry_run_ve_uygula(self):
        # Eski hatalı kayıt: TL hesap satırı da USD işlem PB'li
        alis = _ham_fis(5, D(2026, 2, 9), "DÖVİZ ALIŞ 1.000,00 USD (KUR 43,6000) VAKIF TL → VAKIF USD",
                        [("102.01.0002", "B", Dc("43600"), "USD", Dc("1000"), Dc("43.6")),
                         ("102.01.0001", "A", Dc("43600"), "USD", Dc("1000"), Dc("43.6"))], kaynak="BANKA", banka=self.usd)
        _ham_fis(6, D(2026, 2, 10), "DÖVİZ ALIŞ KAMBİYO GİDER VERGİSİ (336,40)",
                 [("102.01.0001", "A", Dc("336.40"), "TRY", Dc("336.40"), Dc("1")),
                  ("131.01", "B", Dc("336.40"), "TRY", Dc("336.40"), Dc("1"))], kaynak="BANKA", banka=self.tl)
        c = self._komut("doviz_alis_satis_donustur")
        self.assertIn("DRY-RUN", c)
        self.assertIn("DÖVİZ ALIŞ 1.000,00 USD", c)
        self.assertEqual(YevmiyeSatir.objects.get(fis_id=alis.pk, hesap_id="102.01.0001").islem_pb, "USD")   # dry-run yazmaz
        self.assertIn("yapı uymuyor", c)                                                  # vergi fişi dokunulmaz
        self._komut("doviz_alis_satis_donustur", "--uygula")
        t = YevmiyeSatir.objects.get(fis_id=alis.pk, hesap_id="102.01.0001")
        self.assertEqual((t.islem_pb, t.islem_tutari, t.islem_kuru, t.alacak), ("TRY", Dc("43600.00"), Dc("1.000000"), Dc("43600.00")))
        d = YevmiyeSatir.objects.get(fis_id=alis.pk, hesap_id="102.01.0002")
        self.assertEqual((d.islem_pb, d.islem_tutari, d.borc), ("USD", Dc("1000.00"), Dc("43600.00")))
        self.assertIn("kalıntısı kalmadı", self._komut("doviz_alis_satis_donustur"))       # ikinci çalıştırma: iş yok
