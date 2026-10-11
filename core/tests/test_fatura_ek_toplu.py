"""fatura_ek_toplu: PDF dosya adının ilk parçası = fatura_no (alış+satış, birebir); kuru çalıştırma yazmaz; aynı adlı ek atlanır;
eşleşmeyen dosya raporlanır; fatura tutarı/fişi/durumu değişmez."""
import io
import zipfile
from io import StringIO

from django.core.management import call_command

from core.models import Fatura, FaturaEk
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel
from core.tests.test_satis_hesap_demirbas import D, SatisHesapDemirbasTestBase


class FaturaEkTopluTest(SatisHesapDemirbasTestBase, OzelDizinTestTemel):
    def setUp(self):
        OzelDizinTestTemel.setUp(self)
        self.f1 = self._fatura([self._hesap_satir("770.01", "100")], tip=self.satis, no="SMT2026000000007")
        self.f2 = self._fatura([self._hesap_satir("770.01", "50")], tip=self.satis, no="GHE2026000018702")

    def zip_yaz(self, yol, adlar):
        with zipfile.ZipFile(yol, "w") as z:
            for ad in adlar:
                z.writestr(ad, PDF_BAYT)
        return str(yol)

    def calistir(self, *kaynaklar, uygula=False):
        cikti = StringIO()
        args = [a for k in kaynaklar for a in ("--kaynak", k)] + (["--uygula"] if uygula else [])
        call_command("fatura_ek_toplu", *args, stdout=cikti)
        return cikti.getvalue()

    def test_kuru_calistirma_yazmaz_uygula_ekler_tekrar_atlar(self):
        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        z = self.zip_yaz(tmp / "a.zip", ["SMT2026000000007_649_OTTO.pdf", "GHE2026000018702_111_X.pdf", "YOK2026000000001_1_Z.pdf"])
        r = self.calistir(z)
        self.assertIn("KURU ÇALIŞTIRMA", r)
        self.assertIn("Eklenecek             : 2 ek / 2 fatura", r)
        self.assertIn("YOK2026000000001_1_Z.pdf", r)                       # eşleşmeyen raporlanır
        self.assertEqual(FaturaEk.objects.count(), 0)
        r = self.calistir(z, uygula=True)
        self.assertIn("UYGULANDI", r)
        self.assertEqual(FaturaEk.objects.filter(silindi=False).count(), 2)
        self.assertEqual(set(self.f1.ekler.values_list("orijinal_ad", flat=True)), {"SMT2026000000007_649_OTTO.pdf"})
        r = self.calistir(z, uygula=True)                                   # tekrar: mükerrer atlanır
        self.assertEqual(FaturaEk.objects.filter(silindi=False).count(), 2)
        self.assertIn("Zaten var (atlanan)     : 2", r)

    def test_tek_dosya_alt_cizgisiz_ad_ve_kaynaklar_arasi_tekrar(self):
        from pathlib import Path
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        tek = tmp / "SMT2026000000007.pdf"
        tek.write_bytes(PDF_BAYT)
        z1 = self.zip_yaz(tmp / "a.zip", ["GHE2026000018702_1_X.pdf"])
        z2 = self.zip_yaz(tmp / "b.zip", ["GHE2026000018702_1_X.pdf"])       # aynı dosya iki zip'te
        r = self.calistir(str(tek), z1, z2, uygula=True)
        self.assertEqual(FaturaEk.objects.filter(silindi=False).count(), 2)
        self.assertIn("Kaynaklar arası tekrar  : 1", r)
        self.assertEqual(self.f1.ekler.get().orijinal_ad, "SMT2026000000007.pdf")

    def test_fatura_tutar_fis_durum_degismez(self):
        from pathlib import Path
        import tempfile
        once = [(f.pk, f.durum, f.fis_id, f.odenecek, f.updated_at) for f in Fatura.objects.order_by("pk")]
        tmp = Path(tempfile.mkdtemp())
        self.calistir(self.zip_yaz(tmp / "a.zip", ["SMT2026000000007_1_A.pdf"]), uygula=True)
        self.assertEqual(once, [(f.pk, f.durum, f.fis_id, f.odenecek, f.updated_at) for f in Fatura.objects.order_by("pk")])

    def test_birden_fazla_aday_yuklenmez(self):
        from pathlib import Path
        import tempfile
        self._fatura([self._hesap_satir("770.01", "5")], tip=self.satis, cari=self.cari, no="SMT2026000000007")
        tmp = Path(tempfile.mkdtemp())
        r = self.calistir(self.zip_yaz(tmp / "a.zip", ["SMT2026000000007_1_A.pdf"]), uygula=True)
        self.assertIn("Birden fazla adaylı     : 1", r)
        self.assertEqual(FaturaEk.objects.count(), 0)
