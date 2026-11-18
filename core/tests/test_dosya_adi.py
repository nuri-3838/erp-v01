"""core.dosya_adi — belge indirme dosya adı + Content-Disposition (teklif/proforma/sipariş PDF ve Excel ortak kuralı)."""
from urllib.parse import quote

from django.test import SimpleTestCase

from core.dosya_adi import MAKS_UZUNLUK, ascii_ad, belge_dosya_adi, content_disposition


class DosyaAdiTest(SimpleTestCase):
    def test_bicim_belge_no_alt_cizgi_cari_adi(self):
        self.assertEqual(belge_dosya_adi("SST-2026-0045", "4E TEKNİK ENDÜSTRİYEL YAPI MARKET", "pdf"),
                         "SST-2026-0045_4E TEKNİK ENDÜSTRİYEL YAPI MARKET.pdf")                          # Türkçe asıl ad korunur
        self.assertEqual(belge_dosya_adi("SSP-2026-0005", "ACME", ".XLSX"), "SSP-2026-0005_ACME.xlsx")     # uzantı: nokta/büyük harf fark etmez

    def test_ascii_yedekte_turkce_harfler_cevrilir_silinmez(self):
        self.assertEqual(ascii_ad("ÇĞİÖŞÜ çğıöşü.pdf"), "CGIOSU cgiosu.pdf")
        self.assertEqual(ascii_ad("SST-2026-0045_GÖKRAY PLASTİK İNŞAAT.pdf"), "SST-2026-0045_GOKRAY PLASTIK INSAAT.pdf")
        self.assertEqual(ascii_ad("ŞŞŞ"), "SSS")                                                         # eskiden 'ignore' ile boşalıp 'belge.pdf' olurdu
        self.assertEqual(ascii_ad("日本"), "belge")                                                      # çevrilemeyen tamamen: güvenli yedek

    def test_sondaki_nokta_ve_bosluk_temizlenir_cift_nokta_olusmaz(self):
        for unvan in ("GÖKRAY PLASTİK LTD. ŞTİ.", "GÖKRAY PLASTİK LTD. ŞTİ. ", "GÖKRAY PLASTİK LTD. ŞTİ..  ", "  GÖKRAY ŞTİ.  "):
            ad = belge_dosya_adi("SST-2026-0045", unvan, "pdf")
            self.assertNotIn("..", ad, ad)
            self.assertTrue(ad.endswith("ŞTİ.pdf"), ad)
            self.assertNotIn(" .", ad)
        self.assertEqual(belge_dosya_adi("SST-1", "A   B", "pdf"), "SST-1_A B.pdf")                       # tekrarlı boşluk tekilleşir

    def test_dosya_sisteminde_gecersiz_karakterler_temizlenir(self):
        ad = belge_dosya_adi("SST/1", 'AB/C\\D:E*F?G"H<I>J|K\tL\nM', "pdf")
        for c in '\\/:*?"<>|\t\n':
            self.assertNotIn(c, ad)
        self.assertEqual(ad, "SST1_ABCDEFGHIJKLM.pdf")

    def test_en_fazla_100_karakter(self):
        ad = belge_dosya_adi("SST-2026-0045", "Ş" * 300, "pdf")
        self.assertLessEqual(len(ad), MAKS_UZUNLUK)
        self.assertTrue(ad.startswith("SST-2026-0045_"))
        self.assertTrue(ad.endswith(".pdf"))
        # kesim noktası nokta/boşluk üstüne düşse bile ".." / " ." kalmaz
        ad2 = belge_dosya_adi("SST-2026-0045", "A" * (MAKS_UZUNLUK - len("SST-2026-0045_") - len(".pdf") - 1) + ". B", "pdf")
        self.assertNotIn("..", ad2)
        self.assertLessEqual(len(ad2), MAKS_UZUNLUK)

    def test_bos_cari_adi_ve_bos_ad(self):
        self.assertEqual(belge_dosya_adi("SST-1", "", "pdf"), "SST-1.pdf")
        self.assertEqual(belge_dosya_adi("SST-1", None, "xlsx"), "SST-1.xlsx")
        self.assertEqual(belge_dosya_adi("", "", "pdf"), "belge.pdf")
        self.assertEqual(belge_dosya_adi(12, "ACME", "pdf"), "12_ACME.pdf")                              # belge no yoksa pk

    def test_content_disposition_attachment_ve_inline(self):
        ad = "SST-2026-0045_GÖKRAY PLASTİK İNŞAAT LTD. ŞTİ.pdf"
        d = content_disposition(ad)
        self.assertTrue(d.startswith("attachment; "))
        self.assertIn('filename="SST-2026-0045_GOKRAY PLASTIK INSAAT LTD. STI.pdf"', d)                  # ASCII yedek
        self.assertIn(f"filename*=UTF-8''{quote(ad, safe='')}", d)                                       # Türkçe asıl ad
        self.assertTrue(content_disposition(ad, "inline").startswith("inline; "))
        d.encode("latin-1")                                                                              # başlık latin-1'e sığar (Django başlık kuralı)
