"""xml_ek_temizle: fatura eklerindeki XML'i siler (yalnız PDF kalsın). Kurallar: belgede GEÇERLİ PDF varsa XML silinir; PDF yoksa/okunamıyorsa XML'e dokunulmaz ve raporlanır;
varsayılan önizleme yazmaz; --uygula ek kaydını + diskteki dosyayı siler (yetim kalmaz); fatura/fiş/stok dokunulmaz; ikinci çalıştırmada 0 iş."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management import call_command

from core.management.commands.xml_ek_temizle import yetim_xml_dosyalari
from core.models import Fatura, FaturaEk, StokHareket, YevmiyeFisi, YevmiyeSatir
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel
from core.tests.test_satis_hesap_demirbas import SatisHesapDemirbasTestBase

XML = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'


class XmlEkTemizleTest(SatisHesapDemirbasTestBase, OzelDizinTestTemel):
    def setUp(self):
        OzelDizinTestTemel.setUp(self)
        self.log = tempfile.mkdtemp()
        self.f1 = self._fatura([self._hesap_satir("770.01", "100")], tip=self.satis, no="SAT-1")
        self.f2 = self._fatura([self._hesap_satir("770.01", "50")], tip=self.gider, cari=self.satici, no="ALS-1")
        self.f3 = self._fatura([self._hesap_satir("770.01", "30")], tip=self.gider, cari=self.satici, no="ALS-2")
        self.f4 = self._fatura([self._hesap_satir("770.01", "20")], tip=self.gider, cari=self.satici, no="ALS-3")

    def ek(self, fatura, ad, bayt, silindi=False):
        return FaturaEk.objects.create(fatura=fatura, dosya=ContentFile(bayt, name=ad), orijinal_ad=ad, silindi=silindi)

    def calistir(self, *args):
        cikti = StringIO()
        call_command("xml_ek_temizle", *args, "--log-dizini", self.log, stdout=cikti)
        return cikti.getvalue()

    def diskte(self, ek):
        return ek.dosya.storage.exists(ek.dosya.name)

    def _kur(self):
        self.p1, self.x1 = self.ek(self.f1, "SAT-1.pdf", PDF_BAYT), self.ek(self.f1, "SAT-1.xml", XML)                 # satış: PDF var
        self.p2, self.x2 = self.ek(self.f2, "ALS-1.pdf", PDF_BAYT), self.ek(self.f2, "ALS-1.xml", XML)                 # alış: PDF var
        self.x3 = self.ek(self.f3, "ALS-2.xml", XML)                                                                   # PDF YOK
        self.p4, self.x4, self.x4b = self.ek(self.f4, "ALS-3.pdf", PDF_BAYT), self.ek(self.f4, "ALS-3.xml", XML), self.ek(self.f4, "IRS-9.xml", XML)   # 2 XML + 1 PDF

    def test_onizleme_hicbir_sey_yazmaz_ve_liste_verir(self):
        self._kur()
        r = self.calistir()
        self.assertIn("ÖNİZLEME", r)
        self.assertIn("Silinecek XML      : 4", r)
        self.assertIn("ALIŞ 3 · SATIŞ 1", r)
        self.assertIn("Atlanan (PDF yok)  : 1", r)
        self.assertIn("ALS-2.xml", r)                                                    # PDF'siz belge ayrı listede
        self.assertIn("PDF: YOK", r)
        self.assertEqual(FaturaEk.objects.count(), 8)
        self.assertTrue(all(self.diskte(e) for e in (self.x1, self.x2, self.x3, self.x4, self.x4b)))
        self.assertEqual(list(Path(self.log).iterdir()), [])                             # denetim dökümü yok (silme olmadı)

    def test_uygula_xml_kaydi_ve_dosyasi_silinir_pdf_ve_pdfsiz_belge_kalir(self):
        self._kur()
        yollar = {e.pk: e.dosya.name for e in (self.x1, self.x2, self.x4, self.x4b)}
        diger = (Fatura.objects.count(), YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), StokHareket.objects.count())
        r = self.calistir("--uygula")
        self.assertIn("UYGULANDI: 4 XML ek kaydı + dosyası silindi", r)
        self.assertIn("Yetim XML dosyası  : 0", r)
        self.assertEqual(FaturaEk.objects.filter(pk__in=yollar).count(), 0)              # kayıtlar fiziksel silindi
        storage = self.p1.dosya.storage
        self.assertFalse(any(storage.exists(y) for y in yollar.values()))                # dosyalar diskten gitti
        for p in (self.p1, self.p2, self.p4):                                            # PDF'ler duruyor
            self.assertTrue(FaturaEk.objects.filter(pk=p.pk).exists() and self.diskte(p))
        self.assertTrue(FaturaEk.objects.filter(pk=self.x3.pk).exists() and self.diskte(self.x3))   # PDF'siz belgenin XML'i DOKUNULMADI
        self.assertEqual(diger, (Fatura.objects.count(), YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count(), StokHareket.objects.count()))
        self.assertEqual(yetim_xml_dosyalari(), [])

    def test_ikinci_calistirma_sifir_is(self):
        self._kur()
        self.calistir("--uygula")
        r = self.calistir("--uygula")
        self.assertIn("Silinecek XML      : 0", r)
        self.assertIn("Atlanan (PDF yok)  : 1", r)                                       # PDF'siz belge hâlâ atlanır
        self.assertIn("UYGULANDI: 0 XML ek", r)
        self.assertEqual(FaturaEk.objects.filter(silindi=False).count(), 4)

    def test_denetim_dokumu_yazilir(self):
        self._kur()
        self.calistir("--uygula")
        (log,) = list(Path(self.log).iterdir())
        kayitlar = json.loads(log.read_text(encoding="utf-8"))
        self.assertEqual(sorted(k["orijinal_ad"] for k in kayitlar), ["ALS-1.xml", "ALS-3.xml", "IRS-9.xml", "SAT-1.xml"])
        self.assertTrue(all(len(k["sha256"]) == 64 and k["boyut"] == len(XML) for k in kayitlar))

    def test_gecersiz_pdf_guvenlik_kurali_xml_korunur(self):
        self.ek(self.f1, "SAT-1.pdf", b"bu bir pdf degil")                              # imzasız → geçerli PDF sayılmaz
        x = self.ek(self.f1, "SAT-1.xml", XML)
        self.ek(self.f2, "ALS-1.pdf", b"")                                              # boş dosya
        y = self.ek(self.f2, "ALS-1.xml", XML)
        r = self.calistir("--uygula")
        self.assertIn("UYGULANDI: 0 XML ek", r)
        self.assertTrue(all(FaturaEk.objects.filter(pk=e.pk).exists() and self.diskte(e) for e in (x, y)))

    def test_kayitli_pdf_diskten_kaybolmussa_xml_korunur(self):
        p = self.ek(self.f1, "SAT-1.pdf", PDF_BAYT)
        x = self.ek(self.f1, "SAT-1.xml", XML)
        p.dosya.storage.delete(p.dosya.name)                                            # kayıt var, dosya yok
        self.calistir("--uygula")
        self.assertTrue(FaturaEk.objects.filter(pk=x.pk).exists() and self.diskte(x))

    def test_silinmis_xml_kayitlari_yalniz_bayrakla_temizlenir(self):
        self.ek(self.f1, "SAT-1.pdf", PDF_BAYT)
        sd = self.ek(self.f1, "ESKI.xml", XML, silindi=True)                            # kullanıcı eskiden soft-delete etmiş, dosya diskte
        self.calistir("--uygula")
        self.assertTrue(FaturaEk.objects.filter(pk=sd.pk).exists() and self.diskte(sd))     # bayraksız dokunulmaz
        r = self.calistir("--uygula", "--silinmis")
        self.assertIn("Soft-delete XML ek : 1", r)
        self.assertFalse(FaturaEk.objects.filter(pk=sd.pk).exists())
        self.assertFalse(sd.dosya.storage.exists(sd.dosya.name))

    def test_yetim_dosya_raporlanir_silinmez(self):
        self.ek(self.f1, "SAT-1.pdf", PDF_BAYT)
        self.ek(self.f1, "SAT-1.xml", XML)
        depo = self.f1.ekler.first().dosya.storage
        yol = depo.save("fatura_ek/kayitsiz.xml", ContentFile(XML))
        r = self.calistir("--uygula")
        self.assertIn("Yetim XML dosyası  : 1", r)
        self.assertIn(yol, r)
        self.assertTrue(depo.exists(yol))                                               # yetimlere dokunulmaz, yalnız raporlanır

    def test_silinmis_faturanin_ekleri_kapsam_disi(self):
        self.ek(self.f1, "SAT-1.pdf", PDF_BAYT)
        x = self.ek(self.f1, "SAT-1.xml", XML)
        Fatura.objects.filter(pk=self.f1.pk).update(silindi=True)
        r = self.calistir("--uygula")
        self.assertIn("UYGULANDI: 0 XML ek", r)
        self.assertTrue(FaturaEk.objects.filter(pk=x.pk).exists())
