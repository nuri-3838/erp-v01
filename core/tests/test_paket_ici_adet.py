"""Stok kartı 'Paket İçi Adet' (paket_ici_adet, varsayılan 1, en az 1): veri migration'ı (G02 → 6, G03 → 4, G04 → 3; G02-T/G03-T/G04-T ve diğerleri 1), servis varsayılanı + kopya,
DB kısıtı; satış teklifi PDF (TR 'Paket İçi Adet' / EN 'Pcs/Pkg') ve Excel (TR/EN, tüm belge türleri ortak) çıktısı. Form testleri test_stok.py'de."""
import datetime
import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, HesapPlani, KdvOrani, Kategori, Stok, TeklifSiparis, TeklifSiparisKalem
from core.services.stok import stok_kopyala, stok_olustur


class PaketIciAdetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("paket_yon", password="x")
        ust = Kategori.objects.create(kod="152", ad="MAMUL", created_by=cls.yon, updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="30", ad="GENİŞ BASAMAKLI", ust=ust, created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel", created_by=cls.yon, updated_by=cls.yon)
        HesapPlani.objects.create(hesap_kodu="120.01", hesap_adi="MÜŞTERİ PAKET", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)
        cls.cari = Cari.objects.create(kod="PK1", unvan="MÜŞTERİ PAKET", muhasebe_kodu="120.01", para_birimi="USD", created_by=cls.yon, updated_by=cls.yon)

    def stok(self, model, ad="geniş basamaklı merdiven", **kw):
        return stok_olustur(ad=ad, kategori_id=self.kat.pk, uretim_birimi_id=self.birim.pk, fatura_birimi_id=self.birim.pk, kdv_id=self.kdv.pk,
                            satis_urunu=True, model_kodu=model, fiyat_usd="100", kullanici=self.yon, **kw)

    # --- model / migration ---
    def test_varsayilan_1_ve_kopya_korunur(self):
        s = self.stok("G09")
        self.assertEqual(s.paket_ici_adet, 1)
        s6 = self.stok("G10", paket_ici_adet=6)
        self.assertEqual(s6.paket_ici_adet, 6)
        self.assertEqual(stok_kopyala(s6, self.yon).paket_ici_adet, 6)

    def test_servis_gecersiz_deger_reddeder(self):
        from core.services.stok import StokHatasi
        for kotu in (0, "0", -2, "abc"):
            with self.assertRaises(StokHatasi, msg=str(kotu)):
                self.stok("G11", paket_ici_adet=kotu)

    def test_db_kisiti_en_az_1(self):
        s = self.stok("G12")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stok.objects.filter(pk=s.pk).update(paket_ici_adet=0)

    def test_veri_migration_g02_g03_g04_digerleri_1(self):
        kartlar = {m: self.stok(m) for m in ("G02", "G03", "G04", "G02-T", "G03-T", "G04-T", "A21")}
        self.assertEqual({m: s.paket_ici_adet for m, s in kartlar.items()}, {m: 1 for m in kartlar})            # şema varsayılanı: hepsi 1
        gecis = importlib.import_module("core.migrations.0260_stok_paket_ici_adet_veri")
        gecis.paket_ici_adet_ver(apps, None)
        sonuc = {m: Stok.objects.get(pk=s.pk).paket_ici_adet for m, s in kartlar.items()}
        self.assertEqual(sonuc, {"G02": 6, "G03": 4, "G04": 3, "G02-T": 1, "G03-T": 1, "G04-T": 1, "A21": 1})
        gecis.paket_ici_adet_ver(apps, None)                                                                      # tekrar çalışırsa aynı (idempotent)
        self.assertEqual({m: Stok.objects.get(pk=s.pk).paket_ici_adet for m, s in kartlar.items()}, sonuc)

    # --- satış teklifi PDF (kart düzeni) ---
    def _teklif(self, kalemler):
        ts = TeklifSiparis.objects.create(belge_tur="TEKLIF", yon="SATIS", cari=self.cari, tarih=datetime.date(2026, 11, 18), para_birimi="USD",
                                          belge_no="SST-PK-1", created_by=self.yon, updated_by=self.yon)
        for stok in kalemler:
            TeklifSiparisKalem.objects.create(teklif_siparis=ts, stok=stok, miktar=Decimal("1"), birim_fiyat=Decimal("100"), kdv=self.kdv,
                                              created_by=self.yon, updated_by=self.yon)
        return ts

    def test_teklif_pdf_tr_en_paket_ici_adet(self):
        from core.views import satis_teklif_pdf_baglam
        g02, g02t = self.stok("G02", paket_ici_adet=6), self.stok("G02-T")
        ts = self._teklif([g02, g02t])
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        for dil, etiket, yok in (("tr", "Paket İçi Adet", "Pcs/Pkg"), ("en", "Pcs/Pkg", "Paket İçi Adet")):
            ctx = {"ts": ts, "kalemler": kalemler, "sat_teklif": True, **satis_teklif_pdf_baglam(ts, kalemler, dil, self.yon)}
            html = render_to_string("core/satis_teklif_pdf.html", ctx)
            self.assertEqual(html.count(etiket), 2, f"{dil}: her ürün kartında bir kez")
            self.assertNotIn(yok, html)
            self.assertIn(f'<span class="et">{etiket}</span><span class="dg mono">6</span>', html)             # G02 = 6
            self.assertIn(f'<span class="et">{etiket}</span><span class="dg mono">1</span>', html)             # G02-T = 1

    def test_teklif_pdf_uretilir_ve_proforma_siparis_sablonlari_degismedi(self):
        ts = self._teklif([self.stok("G03", paket_ici_adet=4)])
        self.client.force_login(self.yon)
        for dil in ("tr", "en"):
            r = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]) + f"?dil={dil}")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.content.startswith(b"%PDF"))
        for sablon in ("satis_proforma_pdf.html", "satis_siparis_pdf.html", "teklif_siparis_pdf.html"):                 # ayrı şablonlar: dokunulmadı
            with open(f"core/templates/core/{sablon}", encoding="utf-8") as f:
                self.assertNotIn("paket_ici", f.read(), sablon)

    # --- Excel ---
    def test_teklif_excel_paket_ici_adet_sutunu(self):
        from io import BytesIO
        from openpyxl import load_workbook
        ts = self._teklif([self.stok("G04", paket_ici_adet=3)])
        self.client.force_login(self.yon)
        for dil, baslik in (("tr", "Paket İçi Adet"), ("en", "Pcs/Pkg")):
            r = self.client.get(reverse("core:teklif_siparis_xlsx", args=[ts.pk]) + f"?dil={dil}")
            self.assertEqual(r.status_code, 200)
            ws = load_workbook(BytesIO(r.content)).active
            satir = next(i for i in range(1, ws.max_row + 1) if ws.cell(i, 1).value in ("Model Kodu", "Model Code"))
            self.assertEqual(ws.cell(satir, 3).value, baslik)                                                       # Ürün Adı'ndan sonra, Adet/Fiyat'tan önce
            self.assertEqual(ws.cell(satir, 2).value in ("Ürün Adı", "Product Name"), True)
            self.assertEqual(ws.cell(satir + 1, 3).value, 3)
            self.assertEqual(ws.cell(satir + 1, 4).value, 1)                                                         # Adet

    def _belge(self, belge_tur, yon, no, stok):
        ts = TeklifSiparis.objects.create(belge_tur=belge_tur, yon=yon, cari=self.cari, tarih=datetime.date(2026, 11, 18), para_birimi="USD",
                                          belge_no=no, created_by=self.yon, updated_by=self.yon)
        TeklifSiparisKalem.objects.create(teklif_siparis=ts, stok=stok, miktar=Decimal("2"), birim_fiyat=Decimal("100"), kdv=self.kdv,
                                          created_by=self.yon, updated_by=self.yon)
        return ts

    def test_excel_paket_ici_adet_yalniz_satis_belgelerinde_alista_yok(self):
        """Paket İçi Adet sütunu yalnız SATIŞ belgelerinde (teklif / proforma / sipariş); ALIŞ (satınalma) teklif ve sipariş Excel'inde YOK (6 sütun)."""
        from io import BytesIO
        from openpyxl import load_workbook
        stok = self.stok("G02", paket_ici_adet=6)
        self.client.force_login(self.yon)

        def oku(ts, dil):
            r = self.client.get(reverse("core:teklif_siparis_xlsx", args=[ts.pk]) + f"?dil={dil}")
            self.assertEqual(r.status_code, 200)
            ws = load_workbook(BytesIO(r.content)).active
            ncol = ws.max_column                                                     # ws.cell okuması sütun yaratır: önce bak
            satir = next(i for i in range(1, ws.max_row + 1) if ws.cell(i, 1).value in ("Model Kodu", "Model Code"))
            return ncol, [ws.cell(satir, c).value for c in range(1, ncol + 1)], [ws.cell(satir + 1, c).value for c in range(1, ncol + 1)]

        for belge_tur, no in (("TEKLIF", "SST-X-1"), ("PROFORMA", "PRF-X-1"), ("SIPARIS", "SSP-X-1")):                # SATIŞ: 7 sütun
            ts = self._belge(belge_tur, "SATIS", no, stok)
            for dil, etiket in (("tr", "Paket İçi Adet"), ("en", "Pcs/Pkg")):
                ncol, baslik, satir = oku(ts, dil)
                self.assertEqual(ncol, 7, f"{belge_tur} {dil}")
                self.assertEqual(baslik[2], etiket, f"{belge_tur} {dil}")
                self.assertEqual((satir[2], satir[3]), (6, 2), f"{belge_tur} {dil}")                              # Paket İçi Adet, Adet
        for belge_tur, no in (("TEKLIF", "AST-X-1"), ("SIPARIS", "ASP-X-1")):                                         # ALIŞ: 6 sütun, etiket yok
            ts = self._belge(belge_tur, "ALIS", no, stok)
            for dil, etiket, adet_basligi in (("tr", "Paket İçi Adet", "Adet"), ("en", "Pcs/Pkg", "Qty")):
                ncol, baslik, satir = oku(ts, dil)
                self.assertEqual(ncol, 6, f"ALIŞ {belge_tur} {dil}")
                self.assertNotIn(etiket, baslik, f"ALIŞ {belge_tur} {dil}")
                self.assertEqual(baslik[2], adet_basligi)                                                          # 3. sütun Adet
                self.assertEqual(satir[2], 2)                                                                      # değerler kaymadı

