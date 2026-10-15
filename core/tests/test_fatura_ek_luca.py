"""UBL okuyucu + fatura_ek_luca_yukle komutu: XML/PDF eşleştirme (fatura no + VKN, yalnız no,
eşleşmedi, çok adaylı), dry-run yazmaz, --uygula yükler, yeniden çalışınca atlar, tutar/tarih
farkı işaretlenir ama yüklenir, fatura kayıtları DEĞİŞMEZ."""
import datetime
import os
import shutil
import tempfile
import zipfile
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from core.models import (Cari, EkranYetki, Fatura, FaturaEk, FaturaTipi, HesapPlani, KdvOrani,
                         Kur)
from core.services import fatura_ek as ek_servis
from core.services.fatura import fatura_olustur
from core.services.ubl_fatura import UblHatasi, ubl_oku
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, yuklenen

D = datetime.date


def ubl(no, vkn="1234567890", tarih="2026-03-05", tutar="120.00", para="TRY", unvan="ACME A.Ş."):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
 xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:ID>{no}</cbc:ID><cbc:IssueDate>{tarih}</cbc:IssueDate>
 <cbc:DocumentCurrencyCode>{para}</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party>
  <cac:PartyIdentification><cbc:ID schemeID="VKN">{vkn}</cbc:ID></cac:PartyIdentification>
  <cac:PartyName><cbc:Name>{unvan}</cbc:Name></cac:PartyName></cac:Party></cac:AccountingSupplierParty>
 <cac:LegalMonetaryTotal><cbc:TaxInclusiveAmount currencyID="{para}">{tutar}</cbc:TaxInclusiveAmount>
  <cbc:PayableAmount currencyID="{para}">{tutar}</cbc:PayableAmount></cac:LegalMonetaryTotal>
</Invoice>""".encode("utf-8")


_sayac = [0]


def _hesaplar():
    for kod, ad, kalem in (("191", "KDV", "DV"), ("391", "HKDV", "KVYK"),
                           ("320.10.0001", "A", "KVYK"), ("770.01", "GIDER", "DV")):
        if not HesapPlani.objects.filter(hesap_kodu=kod).exists():
            HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO",
                                      rapor_kalemi=kalem, parasal=True, aktif=True)


def _cari(vkn):
    _hesaplar()
    _sayac[0] += 1
    return Cari.objects.create(kod=f"C{_sayac[0]}", unvan=f"CARI {_sayac[0]}", para_birimi="TRY",
                               muhasebe_kodu="320.10.0001", vkn_tckn=vkn)


def _fatura_yap(no, cari, tarih=D(2026, 3, 5), birim_fiyat="100", aciklama=""):
    _hesaplar()
    Kur.objects.get_or_create(tarih=tarih, defaults={"usd_alis": Decimal("40")})
    kdv = KdvOrani.objects.first() or KdvOrani.objects.create(
        aciklama="G", oran=Decimal("20"),
        hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
        hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
    tip = FaturaTipi.objects.filter(ad="GİDER").first() or FaturaTipi.objects.create(
        ad="GİDER", yon="ALIS", gider=True)
    return fatura_olustur(
        tip_id=tip.pk, cari_id=cari.pk, tarih=tarih, fatura_no=no, aciklama=aciklama,
        satirlar=[{"hesap_id": "770.01", "miktar": "1", "birim_fiyat": birim_fiyat,
                   "kdv_id": kdv.pk}])


class UblOkuTest(OzelDizinTestTemel):
    def test_alanlar(self):
        s = ubl_oku(ubl("ABC2026000000001", tutar="1513.96"))
        self.assertEqual(s["fatura_no"], "ABC2026000000001")
        self.assertEqual((s["vkn"], s["tarih"], s["para_birimi"]),
                         ("1234567890", D(2026, 3, 5), "TRY"))
        self.assertEqual(s["odenecek"], Decimal("1513.96"))
        self.assertEqual(s["unvan"], "ACME A.Ş.")

    def test_gecersiz_xml_reddedilir(self):
        for bayt in (b"degil xml", b"<a/>", b'<!DOCTYPE x [<!ENTITY a "b">]><x/>'):
            with self.assertRaises(UblHatasi):
                ubl_oku(bayt)

    def test_xml_ek_olarak_kabul_edilmez_acik_hata_yalniz_pdf_ve_gorsel(self):
        # 2026-10-15 kararı: ek olarak yalnız PDF/görsel. Geçerli UBL XML de, geçersiz XML de AÇIK hata ile reddedilir; kayıt/dosya oluşmaz.
        f = _fatura_yap("EK-X", _cari("1234567890"))
        for ad, bayt in (("a.xml", ubl("EK-X")), ("b.XML", b"<html/>")):
            with self.assertRaises(ek_servis.FaturaEkHatasi) as cm:
                ek_servis.ek_ekle(f, dosya=yuklenen(ad, bayt))
            self.assertIn("XML dosyaları ek olarak kabul edilmiyor (yalnız PDF ve görsel)", str(cm.exception))
            self.assertIn(ad, str(cm.exception))
        self.assertEqual(FaturaEk.objects.count(), 0)
        self.assertEqual(sorted(ek_servis.IZINLI_UZANTI), [".gif", ".jpeg", ".jpg", ".pdf", ".png", ".webp"])
        ek = ek_servis.ek_ekle(f, dosya=yuklenen("a.pdf", PDF_BAYT))                         # PDF hâlâ kabul
        self.assertTrue(ek.dosya.name.endswith(".pdf"))

    def test_ekran_xml_yukleme_hata_mesaji_ve_secici_filtresi(self):
        f = _fatura_yap("EK-E", _cari("1234567890"))
        u = User.objects.create_superuser("xe", password="x")
        EkranYetki.objects.create(kullanici=u, ekran_kod="alis_faturalari")
        self.client.force_login(u)
        detay = self.client.get(reverse("core:fatura_detay", args=[f.pk])).content.decode()
        self.assertIn('accept="image/*,application/pdf"', detay)                              # seçici XML önermez
        self.assertNotIn(".xml", detay.split('name="dosyalar"')[1].split(">")[0])
        self.assertIn("Yalnız PDF veya resim (XML kabul edilmez)", detay)
        r = self.client.post(reverse("core:fatura_ek_ekle", args=[f.pk]), {"dosyalar": [yuklenen("a.xml", ubl("EK-E")), yuklenen("b.pdf", PDF_BAYT)]}, follow=True)
        self.assertContains(r, "XML dosyaları ek olarak kabul edilmiyor")
        self.assertEqual(list(f.ekler.values_list("orijinal_ad", flat=True)), ["b.pdf"])       # XML reddedildi, PDF yüklendi

    def test_xml_indirme_olarak_sunulur(self):
        f = _fatura_yap("EK-Y", _cari("1234567890"))
        ek = FaturaEk.objects.create(fatura=f, dosya=ContentFile(ubl("EK-Y"), name="a.xml"), orijinal_ad="a.xml")     # eski kayıt (yükleme artık XML almaz)
        u = User.objects.create_superuser("y", password="x")
        EkranYetki.objects.create(kullanici=u, ekran_kod="alis_faturalari")
        self.client.force_login(u)
        r = self.client.get(reverse("core:fatura_ek_indir", args=[ek.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/xml")
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")


class LucaYukleTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.cari = _cari("1234567890")
        self.f1 = _fatura_yap("ABC2026000000001", self.cari)                      # 120,00 KDV dahil
        self.f2 = _fatura_yap("XYZ2026000000002", _cari("9999999999"))            # VKN uyuşmaz
        self.f3 = _fatura_yap("FARK2026000000003", self.cari, birim_fiyat="200")  # 240,00
        c_a, c_b = _cari("1111111111"), _cari("2222222222")
        self.f4 = _fatura_yap("DUP2026000000004", c_a)
        self.f5 = _fatura_yap("DUP2026000000004", c_b)
        self.f6 = _fatura_yap("TASI2026000000005", self.cari, tarih=D(2026, 1, 1),
                              aciklama="GERÇEK TARİH: 05.03.2026. x")
        self.dizin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dizin, True)

    def _zip(self, ad, dosyalar):
        yol = os.path.join(self.dizin, ad)
        with zipfile.ZipFile(yol, "w") as z:
            for n, b in dosyalar.items():
                bilgi = zipfile.ZipInfo(n)
                bilgi.flag_bits |= 0x800
                z.writestr(bilgi, b)
        return yol

    def _calistir(self, *ek):
        xmlz = self._zip("x.zip", {
            "ABC2026000000001_1234567890_ACME.xml": ubl("ABC2026000000001"),
            "XYZ2026000000002_8888888888_BASKA.xml": ubl("XYZ2026000000002", vkn="8888888888"),
            "FARK2026000000003_1234567890_ACME.xml": ubl("FARK2026000000003", tutar="999.00"),
            "YOK2026000000009_7777777777_YOK.xml": ubl("YOK2026000000009", vkn="7777777777"),
            "DUP2026000000004_5555555555_DUP.xml": ubl("DUP2026000000004", vkn="5555555555"),
            "TASI2026000000005_1234567890_ACME.xml": ubl("TASI2026000000005"),
        })
        pdfz = self._zip("p.zip", {
            "ABC2026000000001_1234567890_ACME.pdf": PDF_BAYT,
            "FARK2026000000003_1234567890_ACME.pdf": PDF_BAYT,
        })
        out = StringIO()
        call_command("fatura_ek_luca_yukle", "--zip", xmlz, "--zip", pdfz, *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_raporlar_yazmaz(self):
        c = self._calistir()
        self.assertIn("DRY-RUN", c)
        self.assertEqual(FaturaEk.objects.count(), 0)
        self.assertIn("Eşleşen (fatura no + VKN)   : 3", c)      # f1, f3, f6
        self.assertIn("Eşleşen (yalnız fatura no)  : 1", c)      # f2
        self.assertIn("Eşleşmeyen                  : 1", c)      # YOK
        self.assertIn("Birden fazla adaylı         : 1", c)      # DUP
        self.assertIn("YOK2026000000009", c)
        self.assertIn("999.00", c)                               # f3 tutar farkı raporda
        self.assertIn("VKN uyuşmuyor", c)

    def test_gercek_tarih_farki_sayilmaz(self):
        c = self._calistir()
        self.assertNotIn(f"fatura {self.f6.pk} TASI", c)         # f6 farklı/dikkat listesinde yok
        self.assertIn(f"fatura {self.f3.pk} FARK", c)

    def test_uygula_yukler_tekrarda_atlar_faturayi_degistirmez(self):
        onceki = {f.pk: (f.tarih, f.aciklama, f.fatura_no, f.fis_id, f.genel_toplam)
                  for f in Fatura.objects.all()}
        self._calistir("--uygula")
        ekler = FaturaEk.objects.filter(silindi=False)
        # XML EK OLARAK YÜKLENMEZ (yalnız eşleştirme için okunur): yalnız PDF'i olan f1 ve f3 (tutar farkına rağmen) ek alır
        self.assertEqual(ekler.count(), 2)
        self.assertEqual(ekler.filter(dosya__endswith=".xml").count(), 0)
        self.assertEqual(ekler.filter(fatura=self.f3).count(), 1)
        self.assertEqual(ekler.filter(fatura__in=[self.f4, self.f5]).count(), 0)
        c2 = self._calistir("--uygula")
        self.assertEqual(FaturaEk.objects.filter(silindi=False).count(), 2)   # tekrarda eklenmedi
        self.assertIn("atlanan: 2", c2)
        sonra = {f.pk: (f.tarih, f.aciklama, f.fatura_no, f.fis_id, f.genel_toplam)
                 for f in Fatura.objects.all()}
        self.assertEqual(onceki, sonra)
