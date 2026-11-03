"""Teklif / Proforma / Sipariş Excel dökümü (core/services/teklif_siparis_xlsx.py, view teklif_siparis_xlsx): sade 6 sütun
(Model Kodu, Ürün Adı, Adet, Net Birim Fiyat, Birim Brüt KG, Birim CBM), toplam satırı YOK, PDF ile aynı veri kaynağı, TR/EN, sayısal hücre tipleri, yetki."""
import datetime
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import (
    Birim, Cari, HesapPlani, KdvOrani, Kategori, TanimSecenegi, TeklifSiparis, TeklifSiparisKalem,
)
from core.services.stok import stok_olustur

_BASLIK_TR = ["Model Kodu", "Ürün Adı", "Adet", "Net Birim Fiyat (TRY)", "Birim Brüt KG", "Birim CBM"]
_TOPLAM_ETIKETLERI = {
    "Ara toplam", "Ara toplam (iskonto sonrası)", "İskonto toplamı", "KDV toplamı", "Genel toplam", "Tevkifat (−)", "Ödenecek",
    "Subtotal", "Subtotal (after discount)", "Discount total", "VAT total", "Grand total",
}


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
        cls.cari = Cari.objects.create(kod="X1", unvan="MÜŞTERİ XLSX", muhasebe_kodu="120.01", para_birimi="USD",
                                       created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="152", ad="MAMUL", created_by=cls.yon, updated_by=cls.yon)
        kat = Kategori.objects.create(kod="10", ad="MERDİVEN", ust=ust, created_by=cls.yon, updated_by=cls.yon)
        birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel", created_by=cls.yon, updated_by=cls.yon)
        cls.a21 = stok_olustur(ad="a tipi merdiven", kategori_id=kat.pk, uretim_birimi_id=birim.pk, fatura_birimi_id=birim.pk,
                               kdv_id=cls.kdv.pk, satis_urunu=True, model_kodu="a21", basamak_sayisi=3, yukseklik="58",
                               yukleme_40hq=1000, yukleme_20dc=400, fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.a21.ad_en = "Aluminium Platform Stepladder 2+1"
        cls.a21.agirlik, cls.a21.cbm = Decimal("6.5"), Decimal("0.012")
        cls.a21.save(update_fields=["ad_en", "agirlik", "cbm"])
        cls.c22 = stok_olustur(ad="cift cikisli merdiven", kategori_id=kat.pk, uretim_birimi_id=birim.pk, fatura_birimi_id=birim.pk,
                               kdv_id=cls.kdv.pk, satis_urunu=True, model_kodu="c22", basamak_sayisi=4, fiyat_try="15000", kullanici=cls.yon)
        cls.c22.ad_en = "Double-Sided Aluminium Stepladder 2+2"
        cls.c22.save(update_fields=["ad_en"])                       # ağırlık / CBM / yükleme adedi YOK
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

    def _belge(self, belge_tur, no, kalemler, yon="SATIS"):
        ts = TeklifSiparis.objects.create(
            belge_tur=belge_tur, yon=yon, cari=self.cari, tarih=datetime.date(2026, 9, 7), para_birimi="USD", belge_no=no,
            gecerlilik_teslim_tarihi=datetime.date(2026, 10, 1), created_by=self.yon, updated_by=self.yon)
        for stok, miktar, fiyat, isk in kalemler:
            TeklifSiparisKalem.objects.create(teklif_siparis=ts, stok=stok, miktar=Decimal(miktar), birim_fiyat=Decimal(fiyat),
                                              iskonto_yuzdesi=Decimal(isk), kdv=self.kdv, created_by=self.yon, updated_by=self.yon)
        return ts

    def _xlsx(self, ts, dil=None, kullanici=None):
        self.client.force_login(kullanici or self.yon)
        return self.client.get(reverse("core:teklif_siparis_xlsx", args=[ts.pk]) + (f"?dil={dil}" if dil else ""))

    def _sayfa(self, yanit):
        """(sayfa, başlık satırı numarası, veri satırları [6'lı liste]) — veri satırları ürün adı dolu olduğu sürece."""
        ws = load_workbook(BytesIO(yanit.content)).active
        for r in range(1, ws.max_row + 1):
            if ws.cell(r, 1).value in ("Model Kodu", "Model Code") and ws.cell(r, 2).value in ("Ürün Adı", "Product Name"):
                satirlar, i = [], r + 1
                while ws.cell(i, 2).value is not None:
                    satirlar.append([ws.cell(i, c).value for c in range(1, 7)])
                    i += 1
                return ws, r, satirlar
        self.fail("başlık satırı bulunamadı")

    # --- testler ---
    def test_teklif_tr_alti_sutun_sira_tipler_ve_toplam_yok(self):
        ts = self._teklif_olustur()
        r = self._xlsx(ts, "tr")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn(f'filename="teklif_{ts.belge_no}_tr.xlsx"', r["Content-Disposition"])
        ws, baslik, satirlar = self._sayfa(r)
        self.assertEqual(ws.max_column, 6)                                                                # tam 6 sütun (ws.cell okuması sütun yaratır; önce bak)
        self.assertEqual([ws.cell(baslik, c).value for c in range(1, 7)], _BASLIK_TR)                     # bu sırada
        self.assertEqual(len(satirlar), 2)
        a21, c22 = (next(s for s in satirlar if s[0] == m) for m in ("A21", "C22"))
        self.assertEqual(a21[1], self.a21.ad_dil("tr"))                                                   # ad_dil('tr') (kart adı büyük harfe normalize)
        self.assertEqual(a21[2], 1)
        self.assertIsInstance(a21[2], int)                                                                # adet tam sayı
        self.assertEqual(Decimal(str(a21[3])), Decimal("10803"))                                          # navlunlu teklif: nakliye dahil (PDF fiyatı)
        self.assertEqual(Decimal(str(c22[3])), Decimal("15000"))                                          # yükleme adedi yok -> PDF gibi net fiyat
        self.assertEqual(Decimal(str(a21[4])), Decimal("6.5"))
        self.assertEqual(Decimal(str(a21[5])), Decimal("0.012"))
        for s in satirlar:
            for sutun in (2, 3):
                self.assertIsInstance(s[sutun], (int, float), f"sayı değil: {s[sutun]!r}")
        self.assertIsNone(c22[4])                                                                         # boş değer = boş hücre
        self.assertIsNone(c22[5])
        satir_a21 = baslik + 1 + satirlar.index(a21)
        self.assertEqual([ws.cell(satir_a21, c).number_format for c in (3, 4, 5, 6)], ["#,##0", "#,##0.00##", "#,##0.00", "0.0000"])
        self.assertTrue(all(ws.cell(baslik, c).font.bold for c in range(1, 7)))                            # başlık kalın
        self.assertEqual(ws.freeze_panes, f"A{baslik + 1}")                                               # başlık dondurulmuş
        # toplam satırı / ek sütun yok: 6 sütun, toplam etiketi yok, tablonun altında sayı yok
        self.assertEqual(ws.max_column, 6)
        tum = [h.value for satir in ws.iter_rows() for h in satir if h.value is not None]
        self.assertFalse(_TOPLAM_ETIKETLERI & {v for v in tum if isinstance(v, str)})
        for satir in ws.iter_rows(min_row=baslik + 1 + len(satirlar)):
            for h in satir:
                self.assertNotIsInstance(h.value, (int, float, Decimal), f"tablo altında sayı: {h.coordinate}")
        # üst blok + not satırları kalır, görsel yok
        metin = " | ".join(str(v) for v in tum)
        for beklenen in (ts.belge_no, "MÜŞTERİ XLSX", "PEŞİN", "FOB İZMİR", "SİPARİŞ ONAYI SONRASI 15 İŞ GÜNÜ", "TRY", "Notlar",
                         "Ürün ve ambalaj ağırlıklarında"):
            self.assertIn(beklenen, metin)
        self.assertEqual(len(ws._images), 0)

    def test_teklif_en_basliklar_ve_ad_en(self):
        ts = self._teklif_olustur()
        r = self._xlsx(ts, "en")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'filename="teklif_{ts.belge_no}_en.xlsx"', r["Content-Disposition"])
        ws, baslik, satirlar = self._sayfa(r)
        self.assertEqual([ws.cell(baslik, c).value for c in range(1, 7)],
                         ["Model Code", "Product Name", "Qty", "Net Unit Price (TRY)", "Unit Gross KG", "Unit CBM"])
        self.assertEqual({s[1] for s in satirlar}, {"Aluminium Platform Stepladder 2+1", "Double-Sided Aluminium Stepladder 2+2"})
        self.assertEqual(ws.max_column, 6)
        metin = " | ".join(str(h.value) for satir in ws.iter_rows() for h in satir if h.value is not None)
        self.assertIn(self.sekil.ad_dil("en"), metin)              # seçenekler PDF'teki gibi EN karşılıklarıyla
        self.assertIn(self.kosul.ad_dil("en"), metin)
        self.assertFalse(_TOPLAM_ETIKETLERI & {h.value for satir in ws.iter_rows() for h in satir if isinstance(h.value, str)})

    def test_navlunsuz_teklif_net_birim_fiyat(self):
        ts = self._teklif_olustur(yukleme_tipi="", navlun_tutari="")
        _, _, satirlar = self._sayfa(self._xlsx(ts, "tr"))
        kalemler = {k.stok.model_kodu.upper(): k for k in ts.kalemler.filter(silindi=False)}
        for s in satirlar:
            self.assertEqual(Decimal(str(s[3])), kalemler[s[0]].net_birim_fiyat)
        self.assertEqual(Decimal(str(next(s for s in satirlar if s[0] == "A21")[3])), Decimal("10800"))      # 12000 - %10

    def test_dil_yoksa_turkce(self):
        ts = self._teklif_olustur()
        ws, baslik, _ = self._sayfa(self._xlsx(ts))
        self.assertEqual(ws.cell(baslik, 2).value, "Ürün Adı")

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

    def test_proforma_miktarli_adet_tam_sayi_ve_ondalikli_fiyat_net(self):
        ts = self._belge("PROFORMA", "PRF-XL-1", [(self.a21, "3", "350", "10"), (self.c22, "2.5", "100.123456", "0")])
        ws, baslik, satirlar = self._sayfa(self._xlsx(ts, "tr"))
        self.assertEqual([ws.cell(baslik, c).value for c in range(1, 7)],
                         ["Model Kodu", "Ürün Adı", "Adet", "Net Birim Fiyat (USD)", "Birim Brüt KG", "Birim CBM"])      # para birimi başlıkta
        a21, c22 = (next(s for s in satirlar if s[0] == m) for m in ("A21", "C22"))
        self.assertEqual((a21[2], type(a21[2])), (3, int))
        self.assertEqual(c22[2], 2.5)                                                                     # ondalıklı adet kırpılmaz
        self.assertEqual(Decimal(str(a21[3])), Decimal("315.0000"))                                       # 350 * 0,90 — proformada navlun dağıtılmaz
        self.assertEqual(Decimal(str(c22[3])), Decimal("100.1235"))
        self.assertEqual(ws.max_column, 6)

    def test_siparis_ekraninda_da_calisir_dosya_adi_ve_teslim_tarihi(self):
        ts = self._belge("SIPARIS", "SIP-XL-1", [(self.a21, "4", "350", "5")])
        r = self._xlsx(ts, "en")
        self.assertEqual(r.status_code, 200)
        self.assertIn('filename="siparis_SIP-XL-1_en.xlsx"', r["Content-Disposition"])
        ws, baslik, satirlar = self._sayfa(r)
        self.assertEqual(len(satirlar), 1)
        self.assertEqual(Decimal(str(satirlar[0][3])), Decimal("332.5"))
        metin = " | ".join(str(h.value) for satir in ws.iter_rows() for h in satir if h.value is not None)
        self.assertIn("Delivery date", metin)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "Excel (EN)")

    def test_alis_belgesi_de_indirilir(self):
        ts = self._belge("SIPARIS", "SAS-XL-1", [(self.a21, "2", "100", "0")], yon="ALIS")
        r = self._xlsx(ts)
        self.assertEqual(r.status_code, 200)
        ws, baslik, satirlar = self._sayfa(r)
        self.assertEqual(Decimal(str(satirlar[0][3])), Decimal("100"))
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "📊 Excel")
