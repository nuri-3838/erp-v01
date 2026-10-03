"""Luca gelen e-irsaliye ekleri (fatura_ek_luca_yukle --irsaliye-zip): faturaya bağlama (XML referansı,
ERP irsaliye no, yazım hatası raporu, çok adaylı, hariç), dry-run yazmaz, tekrar çalışınca atlar,
fatura/stok kayıtları değişmez."""
import datetime
import os
import shutil
import tempfile
import zipfile
from io import StringIO

from django.core.management import call_command

from core.models import Cari, Depo, Fatura, FaturaEk, StokHareket, TeklifSiparis
from core.services.fatura import fatura_olustur
from core.services.teklif_siparis import teklif_siparis_olustur
from core.services.ubl_fatura import irsaliye_oku, ubl_oku
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel
from core.tests.test_fatura import FaturaTestTemel
from core.tests.test_fatura_ek_luca import ubl

D = datetime.date
VKN = "1234567890"


def fatura_xml(no, refler=(), vkn=VKN):
    """ubl() çıktısına DespatchDocumentReference ekler."""
    xml = ubl(no, vkn=vkn).decode("utf-8")
    ref_xml = "".join(
        f"<cac:DespatchDocumentReference><cbc:ID>{r}</cbc:ID></cac:DespatchDocumentReference>"
        for r in refler)
    return xml.replace("<cac:AccountingSupplierParty>", ref_xml + "<cac:AccountingSupplierParty>").encode("utf-8")


def irsaliye_xml(no, vkn=VKN, tarih="2026-03-04", unvan="ACME A.Ş."):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>{no}</cbc:ID><cbc:IssueDate>{tarih}</cbc:IssueDate>
 <cac:DespatchSupplierParty><cac:Party>
  <cac:PartyIdentification><cbc:ID schemeID="VKN">{vkn}</cbc:ID></cac:PartyIdentification>
  <cac:PartyName><cbc:Name>{unvan}</cbc:Name></cac:PartyName></cac:Party></cac:DespatchSupplierParty>
</DespatchAdvice>""".encode("utf-8")


class UblIrsaliyeOkuTest(OzelDizinTestTemel):
    def test_irsaliye_ve_fatura_referanslari_okunur(self):
        s = irsaliye_oku(irsaliye_xml("IRS-1"))
        self.assertEqual((s["irsaliye_no"], s["vkn"], s["tarih"]), ("IRS-1", VKN, D(2026, 3, 4)))
        f = ubl_oku(fatura_xml("F-1", refler=("IRS-1", "IRS-2")))
        self.assertEqual(f["irsaliye_refleri"], ["IRS-1", "IRS-2"])


class IrsaliyeEkTest(OzelDizinTestTemel, FaturaTestTemel):
    def setUp(self):
        super().setUp()
        Cari.objects.filter(pk=self.tedarikci.pk).update(vkn_tckn=VKN)
        self.dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dizin, True)
        self.depo = Depo.objects.create(kod="DX", ad="DEPO X")
        self.f1 = self._fatura("F-IR-1")
        self.f2 = self._fatura("F-IR-2")
        self._erp_irsaliye("IRS-0002", self.f2)

    def _fatura(self, no):
        return fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
                              fatura_no=no, satirlar=self._satir())

    def _erp_irsaliye(self, no, fatura):
        t = teklif_siparis_olustur(
            belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 4),
            depo_id=self.depo.pk, irsaliye_no=no, satirlar=self._satir())
        TeklifSiparis.objects.filter(pk=t.pk).update(fatura=fatura)
        return t

    def _zip(self, ad, dosyalar):
        yol = os.path.join(self.dizin, ad)
        with zipfile.ZipFile(yol, "w") as z:
            for n, b in dosyalar.items():
                bilgi = zipfile.ZipInfo(n)
                bilgi.flag_bits |= 0x800
                z.writestr(bilgi, b)
        return yol

    def _calistir(self, *ek):
        fz = self._zip("f.zip", {
            "F-IR-1_1234567890_ACME.xml": fatura_xml("F-IR-1", refler=("IRS-0001", "IRS-0005")),
            # F-IR-2'nin XML'inde irsaliye no yazım hatalı: IRS-002 (doğrusu IRS-0002)
            "F-IR-2_1234567890_ACME.xml": fatura_xml("F-IR-2", refler=("IRS-002", "IRS-0005")),
        })
        ix = {f"{no}_{VKN}_ACME.xml": irsaliye_xml(no) for no in
              ("IRS-0001", "IRS-0002", "IRS-0003", "IRS-0004", "IRS-0005")}
        ip = {f"{no}_{VKN}_ACME.pdf": PDF_BAYT for no in
              ("IRS-0001", "IRS-0002", "IRS-0003", "IRS-0004", "IRS-0005")}
        out = StringIO()
        call_command("fatura_ek_luca_yukle", "--zip", fz, "--irsaliye-zip", self._zip("ix.zip", ix),
                     "--irsaliye-zip", self._zip("ip.zip", ip), "--haric-irsaliye", "IRS-0004", *ek,
                     stdout=out)
        return out.getvalue()

    def test_dry_run_raporu_ve_yazmamasi(self):
        c = self._calistir()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(FaturaEk.objects.count(), 0)
        self.assertIn("Eşleşen                : 2", c)            # IRS-0001 (XML ref) + IRS-0002 (ERP irsaliye)
        self.assertIn("Eşleşmeyen             : 1", c)            # IRS-0003
        self.assertIn("Birden fazla adaylı    : 1", c)            # IRS-0005 (iki faturanın XML'inde)
        self.assertIn("Hariç tutulan          : 1", c)            # IRS-0004
        self.assertIn("IRS-0001", c)
        self.assertIn("FATURA XML", c)
        self.assertIn("ERP İRSALİYE", c)
        self.assertIn("YAZIM HATALI", c)
        self.assertIn("'IRS-002' — doğrusu 'IRS-0002'", c)
        self.assertIn("Yüklenecek dosya : 4", c)

    def test_uygula_yukler_tekrarda_atlar_fatura_ve_stok_degismez(self):
        onceki = {f.pk: (f.tarih, f.fatura_no, f.fis_id, f.genel_toplam) for f in Fatura.objects.all()}
        hareket = StokHareket.objects.count()
        self._calistir("--uygula")
        self.assertEqual(FaturaEk.objects.filter(fatura=self.f1, silindi=False).count(), 2)
        self.assertEqual(FaturaEk.objects.filter(fatura=self.f2, silindi=False).count(), 2)
        adlar = set(FaturaEk.objects.values_list("orijinal_ad", flat=True))
        self.assertIn("IRS-0001_1234567890_ACME.xml", adlar)
        self.assertNotIn("IRS-0003_1234567890_ACME.pdf", adlar)      # eşleşmeyen
        self.assertNotIn("IRS-0004_1234567890_ACME.pdf", adlar)      # hariç
        self.assertNotIn("IRS-0005_1234567890_ACME.pdf", adlar)      # çok adaylı
        c2 = self._calistir("--uygula")
        self.assertEqual(FaturaEk.objects.count(), 4)                # tekrarda eklenmedi
        self.assertIn("atlanan: 4", c2)
        sonra = {f.pk: (f.tarih, f.fatura_no, f.fis_id, f.genel_toplam) for f in Fatura.objects.all()}
        self.assertEqual(onceki, sonra)
        self.assertEqual(StokHareket.objects.count(), hareket)
