"""Teklif / Proforma / Sipariş Excel dökümü (core/services/teklif_siparis_xlsx.py, view teklif_siparis_xlsx):
PDF ile aynı veri kaynağı, TR/EN, sayısal hücre tipleri, toplamların PDF hesabıyla eşitliği, yetki."""
import datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import (
    Birim, Cari, HesapPlani, KdvOrani, Kategori, TanimSecenegi, TeklifSiparis, TeklifSiparisKalem, Ulke,
)
from core.services.stok import stok_olustur
from core.views import satis_proforma_pdf_baglam, satis_siparis_pdf_baglam


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)


def _secenek(kategori, **f):
    return TanimSecenegi.objects.filter(silindi=False, kategori=kategori, **f).first()


class TeklifSiparisXlsxTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("xlyon", password="x")
        cls.bos = User.objects.create_user("xlbos", password="x")
        _hesap("120.01", "MÜŞTERİ XLSX")
        _hesap("120.02", "YURTDIŞI MÜŞTERİ XLSX")
        cls.de = Ulke.objects.filter(kod="DE").first() or Ulke.objects.create(kod="DE", ad="Almanya", ad_en="Germany", created_by=cls.yon, updated_by=cls.yon)
        cls.cari = Cari.objects.create(kod="X1", unvan="MÜŞTERİ XLSX", muhasebe_kodu="120.01", para_birimi="USD",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.cari_dis = Cari.objects.create(kod="X2", unvan="YURTDIŞI MÜŞTERİ XLSX", muhasebe_kodu="120.02", para_birimi="USD",
                                           ulke=cls.de, created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="152", ad="MAMUL", created_by=cls.yon, updated_by=cls.yon)
        kat = Kategori.objects.create(kod="10", ad="MERDİVEN", ust=ust, created_by=cls.yon, updated_by=cls.yon)
        birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel", created_by=cls.yon, updated_by=cls.yon)
        cls.a21 = stok_olustur(ad="a tipi merdiven", kategori_id=kat.pk, uretim_birimi_id=birim.pk, fatura_birimi_id=birim.pk,
                               kdv_id=cls.kdv.pk, satis_urunu=True, model_kodu="a21", basamak_sayisi=3, yukseklik="58",
                               yukleme_40hq=1000, yukleme_20dc=400, fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.a21.ad_en = "Aluminium Platform Stepladder 2+1"
        cls.a21.save(update_fields=["ad_en"])
        cls.c22 = stok_olustur(ad="cift cikisli merdiven", kategori_id=kat.pk, uretim_birimi_id=birim.pk, fatura_birimi_id=birim.pk,
                               kdv_id=cls.kdv.pk, satis_urunu=True, model_kodu="c22", basamak_sayisi=4, fiyat_try="15000", kullanici=cls.yon)
        cls.c22.ad_en = "Double-Sided Aluminium Stepladder 2+2"
        cls.c22.save(update_fields=["ad_en"])
        cls.sekil = _secenek("YUKLEME_SEKLI", ad="FOB İZMİR")
        cls.kosul = _secenek("ODEME_KOSULU", ad="PEŞİN")
        cls.tip_40hq = _secenek("YUKLEME_TIPI", kod="40HQ")
        cls.teslim = _secenek("TESLIM_SURESI", ad="SİPARİŞ ONAYI SONRASI 15 İŞ GÜNÜ")

    # --- yardımcılar ---
    def _teklif_olustur(self, **over):
        govde = {
            "cari": self.cari.pk, "tarih": "2026-09-07", "para_birimi": "TRY",
            "yukleme_sekli": self.sekil.pk, "odeme_kosulu": self.kosul.pk,
            "yukleme_tipi": self.tip_40hq.pk, "teslim_suresi": self.teslim.pk, "navlun_tutari": "3.000",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.a21.pk, "form-0-dahil": "on", "form-0-iskonto_yuzdesi": "10", "form-0-birim_fiyat": "12.000",
            "form-1-stok": self.c22.pk, "form-1-dahil": "on", "form-1-iskonto_yuzdesi": "0", "form-1-birim_fiyat": "15.000",
        }
        govde.update(over)
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), govde)
        return TeklifSiparis.objects.filter(belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")

    def _belge(self, belge_tur, cari, no, kalemler):
        ts = TeklifSiparis.objects.create(
            belge_tur=belge_tur, yon="SATIS", cari=cari, tarih=datetime.date(2026, 9, 7), para_birimi="USD", belge_no=no,
            gecerlilik_teslim_tarihi=datetime.date(2026, 10, 1), created_by=self.yon, updated_by=self.yon)
        for stok, miktar, fiyat, isk in kalemler:
            TeklifSiparisKalem.objects.create(teklif_siparis=ts, stok=stok, miktar=Decimal(miktar), birim_fiyat=Decimal(fiyat),
                                              iskonto_yuzdesi=Decimal(isk), kdv=self.kdv, created_by=self.yon, updated_by=self.yon)
        return ts

    def _xlsx(self, ts, dil=None, kullanici=None):
        self.client.force_login(kullanici or self.yon)
        url = reverse("core:teklif_siparis_xlsx", args=[ts.pk]) + (f"?dil={dil}" if dil else "")
        return self.client.get(url)

    def _sayfa(self, yanit):
        wb = load_workbook(BytesIO(yanit.content))
        ws = wb.active
        for r in range(1, ws.max_row + 1):
            if ws.cell(r, 1).value in ("Sıra", "No") and ws.cell(r, 2).value in ("Stok kodu", "Item code"):
                return ws, r
        self.fail("başlık satırı bulunamadı")

    def _satirlar(self, ws, baslik):
        sonuc, r = [], baslik + 1
        while isinstance(ws.cell(r, 1).value, (int, float)):
            sonuc.append([ws.cell(r, c).value for c in range(1, 14)])
            r += 1
        return sonuc, r

    def _etiketli(self, ws, etiket):
        """Başlık satırından sonraki 'etiket' hücresinin yanındaki değer (alt blok: etiket I:L birleşik, değer M)."""
        for satir in ws.iter_rows():
            for h in satir:
                if h.value == etiket:
                    return ws.cell(h.row, 13).value
        return None

    # --- testler ---
    def test_teklif_tr_icerik_tipler_ve_toplamlar(self):
        ts = self._teklif_olustur()
        r = self._xlsx(ts, "tr")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn(f'filename="teklif_{ts.belge_no}_tr.xlsx"', r["Content-Disposition"])
        ws, baslik = self._sayfa(r)
        basliklar = [ws.cell(baslik, c).value for c in range(1, 15)]
        self.assertEqual(basliklar, ["Sıra", "Stok kodu", "Ürün adı", "Model kodu", "Adet", "Birim", "Liste fiyatı", "İskonto %",
                                     "Net birim fiyat", "Tutar", "KDV %", "KDV tutarı", "KDV dahil tutar", "Nakliye dahil birim fiyat"])
        satirlar, _ = self._satirlar(ws, baslik)
        kalemler = list(ts.kalemler.filter(silindi=False).order_by("id"))
        self.assertEqual(len(satirlar), len(kalemler))
        # ad_dil('tr'), sayısal hücreler SAYI (metin değil), değerler kalem property'leriyle birebir
        for s, k in zip(satirlar, kalemler):
            self.assertEqual(s[1], k.stok.kod)
            self.assertEqual(s[2], k.stok.ad)
            self.assertEqual(s[3], k.stok.model_kodu)
            for sutun in (4, 6, 7, 8, 9, 10, 11, 12):
                self.assertIsInstance(s[sutun], (int, float), f"sütun {sutun} sayı değil: {s[sutun]!r}")
            self.assertEqual(Decimal(str(s[9])), k.tutar)
            self.assertEqual(Decimal(str(s[8])), k.net_birim_fiyat)
            self.assertEqual(Decimal(str(s[11])), k.kdv_tutari)
            self.assertEqual(Decimal(str(s[12])), k.genel_toplam)
            self.assertEqual(s[10], 20)
        self.assertEqual(ws.cell(baslik + 1, 10).number_format, "#,##0.00")
        self.assertEqual(ws.cell(baslik + 1, 12).number_format, "#,##0.00")
        self.assertTrue(ws.cell(baslik, 3).font.bold)
        self.assertEqual(ws.freeze_panes, f"A{baslik + 1}")
        self.assertGreater(ws.column_dimensions["C"].width, 30)
        # alt blok = modelin (PDF'in kullandığı) toplamları
        self.assertEqual(Decimal(str(self._etiketli(ws, "Ara toplam (iskonto sonrası)"))), ts.ara_toplam)
        self.assertEqual(Decimal(str(self._etiketli(ws, "KDV toplamı"))), ts.kdv_toplam)
        self.assertEqual(Decimal(str(self._etiketli(ws, "Genel toplam"))), ts.genel_toplam)
        liste = sum(k.miktar * k.birim_fiyat for k in kalemler)
        self.assertEqual(Decimal(str(self._etiketli(ws, "İskonto toplamı"))), liste - ts.ara_toplam)
        # nakliye dahil birim fiyat PDF'tekiyle aynı (a21: 10800 + 3 = 10803; c22'nin yükleme adedi yok -> boş)
        nak = {k.stok_id: ws.cell(baslik + 1 + i, 14).value for i, k in enumerate(kalemler)}
        self.assertEqual(Decimal(str(nak[self.a21.pk])), Decimal("10803"))
        self.assertIsNone(nak[self.c22.pk])
        # üst blok: firma + belge no + koşullar; görsel yok
        metin = " | ".join(str(h.value) for satir in ws.iter_rows(max_row=baslik) for h in satir if h.value is not None)
        for beklenen in (ts.belge_no, "MÜŞTERİ XLSX", "PEŞİN", "FOB İZMİR", "SİPARİŞ ONAYI SONRASI 15 İŞ GÜNÜ", "TRY"):
            self.assertIn(beklenen, metin)
        self.assertEqual(len(ws._images), 0)

    def test_teklif_en_ad_en_ve_etiketler(self):
        ts = self._teklif_olustur()
        r = self._xlsx(ts, "en")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'filename="teklif_{ts.belge_no}_en.xlsx"', r["Content-Disposition"])
        ws, baslik = self._sayfa(r)
        self.assertEqual([ws.cell(baslik, c).value for c in (1, 2, 3, 5, 9, 13)],
                         ["No", "Item code", "Item", "Qty", "Net unit price", "Amount incl. VAT"])
        adlar = {ws.cell(baslik + i, 3).value for i in (1, 2)}
        self.assertEqual(adlar, {"Aluminium Platform Stepladder 2+1", "Double-Sided Aluminium Stepladder 2+2"})
        self.assertIsNotNone(self._etiketli(ws, "Grand total"))
        metin = " | ".join(str(h.value) for satir in ws.iter_rows(max_row=baslik) for h in satir if h.value is not None)
        self.assertIn(self.sekil.ad_dil("en"), metin)             # seçenekler PDF'teki gibi EN karşılıklarıyla
        self.assertIn(self.kosul.ad_dil("en"), metin)

    def test_dil_yoksa_turkce(self):
        ts = self._teklif_olustur()
        ws, baslik = self._sayfa(self._xlsx(ts))
        self.assertEqual(ws.cell(baslik, 2).value, "Stok kodu")

    def test_yetkisiz_kullanici_indiremez(self):
        ts = self._teklif_olustur()
        self.assertNotEqual(self._xlsx(ts, "tr", self.bos).status_code, 200)
        self.client.logout()
        self.assertNotEqual(self.client.get(reverse("core:teklif_siparis_xlsx", args=[ts.pk])).status_code, 200)

    def test_detay_ekraninda_excel_dugmeleri(self):
        ts = self._teklif_olustur()
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "Excel (TR)")
        self.assertContains(d, "Excel (EN)")
        self.assertContains(d, reverse("core:teklif_siparis_xlsx", args=[ts.pk]) + "?dil=en")

    def test_proforma_toplamlar_pdf_baglamiyla_esit_miktarli_yurt_ici(self):
        ts = self._belge("PROFORMA", self.cari, "PRF-XL-1", [(self.a21, "3", "350", "10"), (self.c22, "2.5", "100.123456", "0")])
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        ctx = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        ws, baslik = self._sayfa(self._xlsx(ts, "tr"))
        satirlar, _ = self._satirlar(ws, baslik)
        self.assertEqual(len(satirlar), 2)
        self.assertEqual(Decimal(str(self._etiketli(ws, "KDV toplamı"))), ctx["kdv_toplam"])
        self.assertEqual(Decimal(str(self._etiketli(ws, "Genel toplam"))), ctx["genel_toplam"])
        self.assertEqual(Decimal(str(self._etiketli(ws, "Ara toplam (iskonto sonrası)"))), ts.ara_toplam)
        self.assertEqual(satirlar[0][4], 3)                         # tam sayı adet
        self.assertEqual(satirlar[1][4], 2.5)                       # ondalıklı adet
        self.assertEqual(Decimal(str(satirlar[0][8])), Decimal("315.0000"))     # 350 * 0,90
        self.assertEqual(ws.cell(baslik + 2, 5).number_format, "#,##0.000")

    def test_proforma_yurt_disi_kdv_sifir_pdf_gibi(self):
        ts = self._belge("PROFORMA", self.cari_dis, "PRF-XL-2", [(self.a21, "2", "350", "0")])
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        ctx = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertEqual(ctx["kdv_toplam"], Decimal("0"))
        ws, baslik = self._sayfa(self._xlsx(ts, "tr"))
        satirlar, _ = self._satirlar(ws, baslik)
        self.assertEqual(satirlar[0][10], 0)
        self.assertEqual(satirlar[0][11], 0)
        self.assertEqual(Decimal(str(satirlar[0][12])), Decimal("700.00"))
        self.assertEqual(Decimal(str(self._etiketli(ws, "KDV toplamı"))), Decimal("0"))
        self.assertEqual(Decimal(str(self._etiketli(ws, "Genel toplam"))), ctx["genel_toplam"])

    def test_siparis_ekraninda_da_calisir_dosya_adi_ve_teslim_tarihi(self):
        ts = self._belge("SIPARIS", self.cari, "SIP-XL-1", [(self.a21, "4", "350", "5")])
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        ctx = satis_siparis_pdf_baglam(ts, kalemler, "en", self.yon)
        r = self._xlsx(ts, "en")
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="siparis_SIP-XL-1_en.xlsx"', r["Content-Disposition"])
        ws, baslik = self._sayfa(r)
        self.assertEqual(Decimal(str(self._etiketli(ws, "Grand total"))), ctx["odenecek"])        # tevkifatsız: ödenecek = genel
        metin = " | ".join(str(h.value) for satir in ws.iter_rows(max_row=baslik) for h in satir if h.value is not None)
        self.assertIn("Delivery date", metin)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "Excel (EN)")

    def test_alis_belgesi_de_indirilir(self):
        ts = TeklifSiparis.objects.create(
            belge_tur="SIPARIS", yon="ALIS", cari=self.cari, tarih=datetime.date(2026, 9, 7), para_birimi="TRY", belge_no="SAS-XL-1",
            created_by=self.yon, updated_by=self.yon)
        TeklifSiparisKalem.objects.create(teklif_siparis=ts, stok=self.a21, miktar=Decimal("2"), birim_fiyat=Decimal("100"),
                                          kdv=self.kdv, created_by=self.yon, updated_by=self.yon)
        r = self._xlsx(ts)
        self.assertEqual(r.status_code, 200)
        ws, baslik = self._sayfa(r)
        satirlar, _ = self._satirlar(ws, baslik)
        self.assertEqual(Decimal(str(satirlar[0][12])), Decimal("240.00"))
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "📊 Excel")
