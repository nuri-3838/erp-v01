"""Satış Teklifi — bağımsız ekran (core/views.py::satis_teklif_ekle/satis_teklif_duzenle):
tüm satış ürünleri otomatik önceden dolu gelir, miktar YOK (hep 1), cari seçilince PB/
iskonto otomatik context'e gelir, yükleme şekli / ödeme koşulu / yükleme tipi Tanım
Listeleri'nden seçilir, navlun seçilen yükleme tipine sığan adede bölünerek ürün başına
dağıtılır, eski paylaşımlı teklif_siparis_duzenle URL'i buraya yönlendirir."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Cari, FirmaBilgisi, HesapPlani, KdvOrani, Kategori, TanimSecenegi, TeklifSiparis, Ulke,
)
from core.services.stok import stok_olustur


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)


def _secenek(kategori, **f):
    return TanimSecenegi.objects.filter(silindi=False, kategori=kategori, **f).first()


class SatisTeklifTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("styon", password="x")
        cls.bos = User.objects.create_user("stbos", password="x")
        _hesap("120.01", "MÜŞTERİ SATIŞ TEKLİF")
        cls.cari = Cari.objects.create(
            kod="C1", unvan="MÜŞTERİ SATIŞ TEKLİF", muhasebe_kodu="120.01",
            para_birimi="USD", iskonto_yuzdesi=Decimal("10"),
            created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="150", ad="MAMUL", created_by=cls.yon,
                                      updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="10", ad="MERDİVEN", ust=ust,
                                          created_by=cls.yon, updated_by=cls.yon)
        from core.models import Birim
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.a21 = stok_olustur(
            ad="a tipi merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="a21", basamak_sayisi=3, yukseklik="58",
            yukleme_40hq=1000, yukleme_20dc=400,
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.a21.ad_en = "Aluminium Platform Stepladder 2+1"
        cls.a21.save(update_fields=["ad_en"])
        cls.c22 = stok_olustur(
            ad="cift cikisli merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="c22", basamak_sayisi=4,
            fiyat_try="15000", kullanici=cls.yon)   # USD fiyatı ve yükleme adedi YOK
        cls.c22.ad_en = "Double-Sided Aluminium Stepladder 2+2"
        cls.c22.save(update_fields=["ad_en"])
        # Seed migration'dan gelen Tanım Listesi seçenekleri
        cls.sekil = _secenek("YUKLEME_SEKLI", ad="FOB İZMİR")
        cls.kosul = _secenek("ODEME_KOSULU", ad="PEŞİN")
        cls.tip_40hq = _secenek("YUKLEME_TIPI", kod="40HQ")
        cls.tip_tir = _secenek("YUKLEME_TIPI", kod="TIR")
        cls.teslim = _secenek("TESLIM_SURESI", ad="SİPARİŞ ONAYI SONRASI 15 İŞ GÜNÜ")

    def test_seed_secenekleri_geldi(self):
        self.assertIsNotNone(self.sekil)
        self.assertIsNotNone(self.kosul)
        self.assertEqual(
            set(TanimSecenegi.objects.filter(kategori="YUKLEME_TIPI").values_list("kod", flat=True)),
            {"20DC", "40HQ", "TIR"})

    def test_get_tum_satis_urunleri_hazir_gelir(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        self.assertEqual(r.status_code, 200)
        formset = r.context["formset"]
        self.assertEqual(len(formset.forms), 2)
        stoklar = {f.initial["stok"] for f in formset.forms}
        self.assertEqual(stoklar, {self.a21.pk, self.c22.pk})
        for f in formset.forms:
            self.assertTrue(f.initial["dahil"])

    def test_get_aciklama_yok_gecerlilik_15_gun(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        bform = r.context["bform"]
        self.assertNotIn("aciklama", bform.fields)
        self.assertEqual(bform["gecerlilik_teslim_tarihi"].value(),
                         timezone.localdate() + datetime.timedelta(days=15))
        self.assertNotContains(r, "elle değiştirebilirsiniz")
        for alan in ("yukleme_sekli", "odeme_kosulu", "yukleme_tipi", "teslim_suresi",
                    "navlun_tutari"):
            self.assertIn(alan, bform.fields)
        self.assertContains(r, "FOB İZMİR")
        self.assertContains(r, "40&#x27; HQ KONTEYNER")

    def test_stok_meta_pb_basina_dogru_ve_eksikse_none(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        meta = r.context["stok_meta"][str(self.a21.pk)]
        self.assertEqual(meta["modelKodu"], "A21")
        self.assertEqual(meta["fiyatlar"]["TRY"], 12000.0)
        self.assertEqual(meta["fiyatlar"]["USD"], 350.0)
        self.assertEqual(meta["yukleme"], {"20DC": 400, "40HQ": 1000, "TIR": None})
        meta_c22 = r.context["stok_meta"][str(self.c22.pk)]
        self.assertIsNone(meta_c22["fiyatlar"]["USD"])            # tanımsız PB -> None
        self.assertEqual(r.context["tip_kodlari"][str(self.tip_40hq.pk)], "40HQ")

    def test_cari_meta_pb_ve_iskonto_dogru(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        meta = r.context["cari_meta"][str(self.cari.pk)]
        self.assertEqual(meta["pb"], "USD")
        self.assertEqual(meta["iskonto"], 10.0)

    def _post_govde(self, **over):
        govde = {
            "cari": self.cari.pk, "tarih": "2026-09-07", "para_birimi": "TRY",
            "yukleme_sekli": self.sekil.pk, "odeme_kosulu": self.kosul.pk,
            "yukleme_tipi": self.tip_40hq.pk, "teslim_suresi": self.teslim.pk,
            "navlun_tutari": "3.000",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.a21.pk, "form-0-dahil": "on",
            "form-0-iskonto_yuzdesi": "10", "form-0-birim_fiyat": "12.000",
            "form-1-stok": self.c22.pk, "form-1-dahil": "on",
            "form-1-iskonto_yuzdesi": "0", "form-1-birim_fiyat": "15.000",
        }
        govde.update(over)
        return govde

    def _son_teklif(self):
        return TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", cari=self.cari).latest("id")

    def test_post_miktar_hep_1_iskonto_ve_secenekler_kaydedilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(ts.yukleme_sekli, self.sekil)
        self.assertEqual(ts.odeme_kosulu, self.kosul)
        self.assertEqual(ts.yukleme_tipi, self.tip_40hq)
        self.assertEqual(ts.teslim_suresi, self.teslim)
        self.assertEqual(ts.navlun_tutari, Decimal("3000.00"))
        kalemler = {k.stok_id: k for k in ts.kalemler.filter(silindi=False)}
        self.assertEqual(len(kalemler), 2)
        a21_kalem = kalemler[self.a21.pk]
        self.assertEqual(a21_kalem.miktar, Decimal("1"))
        self.assertEqual(a21_kalem.iskonto_yuzdesi, Decimal("10"))
        self.assertEqual(a21_kalem.tutar, Decimal("10800.00"))   # 12000 * 0,90

    def test_navlun_yukleme_tipine_gore_dagitilir(self):
        """40HQ navlunu 3000, a21'e 40HQ'ya 1000 adet sığıyor -> birim navlun payı 3;
        nakliye dahil = net (10800) + 3. c22'nin 40HQ adedi yok -> None."""
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        kalemler = {k.stok_id: k for k in self._son_teklif().kalemler.filter(silindi=False)}
        self.assertEqual(kalemler[self.a21.pk].navlun_payi, Decimal("3.0000"))
        self.assertEqual(kalemler[self.a21.pk].nakliye_dahil_fiyat, Decimal("10803.0000"))
        self.assertIsNone(kalemler[self.c22.pk].navlun_payi)
        self.assertIsNone(kalemler[self.c22.pk].nakliye_dahil_fiyat)

    def test_navlun_yoksa_veya_tip_yoksa_dagitim_yok(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"),
                         self._post_govde(navlun_tutari="", yukleme_tipi=""))
        ts = self._son_teklif()
        self.assertIsNone(ts.navlun_tutari)
        self.assertIsNone(ts.yukleme_tipi)
        for k in ts.kalemler.filter(silindi=False):
            self.assertIsNone(k.navlun_payi)

    def test_yanlis_kategoriden_secenek_reddedilir(self):
        """Ödeme koşulu alanına Yükleme Şekli listesinden bir pk gönderilemez
        (form queryset'i kategoriye göre kısıtlı -> alan hatası, kayıt yok)."""
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"),
                             self._post_govde(odeme_kosulu=self.sekil.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(TeklifSiparis.objects.filter(belge_tur="TEKLIF", yon="SATIS").exists())

    def test_servis_yanlis_kategori_reddeder(self):
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur="TEKLIF", yon="SATIS", cari_id=self.cari.pk,
                tarih=datetime.date(2026, 9, 7),
                satirlar=[{"stok_id": self.a21.pk, "miktar": "1", "birim_fiyat": "10"}],
                yukleme_tipi_id=self.sekil.pk, kullanici=self.yon)

    def test_negatif_navlun_reddedilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"),
                             self._post_govde(navlun_tutari="-5"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Navlun tutarı negatif olamaz")

    def test_dahil_isaretsiz_satir_kaydedilmez(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]                                # c22 dahil değil
        r = self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = self._son_teklif()
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        kalemler = list(ts.kalemler.filter(silindi=False))
        self.assertEqual(len(kalemler), 1)
        self.assertEqual(kalemler[0].stok_id, self.a21.pk)

    def test_hicbiri_dahil_degilse_hata_verir_kaydetmez(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-0-dahil"]
        del govde["form-1-dahil"]
        r = self.client.post(reverse("core:satis_teklif_ekle"), govde)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "En az bir ürün teklife dahil edilmelidir")
        self.assertFalse(TeklifSiparis.objects.filter(belge_tur="TEKLIF", yon="SATIS").exists())

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:satis_teklif_ekle")).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde()).status_code, 403)

    def test_duzenle_get_mevcut_degerleri_getirir(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]                                 # c22 ilk kayıtta dahil değil
        self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = self._son_teklif()
        r = self.client.get(reverse("core:satis_teklif_duzenle", args=[ts.pk]))
        self.assertEqual(r.status_code, 200)
        bform = r.context["bform"]
        self.assertEqual(bform["yukleme_tipi"].value(), self.tip_40hq.pk)
        self.assertEqual(bform["odeme_kosulu"].value(), self.kosul.pk)
        self.assertEqual(bform["navlun_tutari"].value(), "3.000,00")
        formset = r.context["formset"]
        durum = {f.initial["stok"]: f.initial["dahil"] for f in formset.forms}
        self.assertTrue(durum[self.a21.pk])
        self.assertFalse(durum[self.c22.pk])
        iskonto = {f.initial["stok"]: f.initial["iskonto_yuzdesi"] for f in formset.forms}
        self.assertEqual(iskonto[self.a21.pk], Decimal("10"))

    def test_duzenle_post_gunceller(self):
        self.client.force_login(self.yon)
        govde = self._post_govde()
        del govde["form-1-dahil"]
        self.client.post(reverse("core:satis_teklif_ekle"), govde)
        ts = self._son_teklif()
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 1)
        r = self.client.post(reverse("core:satis_teklif_duzenle", args=[ts.pk]),
                             self._post_govde(yukleme_tipi=self.tip_tir.pk, navlun_tutari="900"))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts.refresh_from_db()
        self.assertEqual(ts.kalemler.filter(silindi=False).count(), 2)
        self.assertEqual(ts.yukleme_tipi, self.tip_tir)
        self.assertEqual(ts.navlun_tutari, Decimal("900.00"))

    def test_teklif_siparis_duzenle_eski_url_satis_teklifini_yonlendirir(self):
        """Veri kaybı koruması: eski paylaşımlı teklif_siparis_duzenle URL'ine doğrudan
        gidilirse, bu ekranın bilmediği iskonto/liste/navlun alanları sessizce
        sıfırlanmasın diye satis_teklif_duzenle'a yönlendirilir."""
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk]))
        self.assertRedirects(r, reverse("core:satis_teklif_duzenle", args=[ts.pk]))

    def test_detay_ve_pdf_fiyat_karti_navlun_gorunur_toplam_gizlenir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(d, "Ürün Fiyat Listesi")
        self.assertContains(d, "FOB İZMİR")
        self.assertContains(d, "PEŞİN")
        self.assertContains(d, "40&#x27; HQ KONTEYNER")
        self.assertContains(d, "Navlun Payı")
        self.assertContains(d, "10.803,0000")                      # a21 nakliye dahil
        self.assertContains(d, "yükleme adedi tanımsız")            # c22
        self.assertNotContains(d, "Ödenecek")
        self.assertNotContains(d, "fk-gorsel")                      # detayda ürün görseli yok
        self.assertContains(d, "PDF (TR)")
        self.assertContains(d, "PDF (EN)")
        from urllib.parse import quote
        beklenen_ad = f"{ts.belge_no}-{self.cari.unvan}.pdf"
        beklenen_parca = f"filename*=UTF-8''{quote(beklenen_ad)}"
        pdf = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertIn(beklenen_parca, pdf["Content-Disposition"])
        self.assertNotIn("-TR.pdf", pdf["Content-Disposition"])
        pdf_en = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]) + "?dil=en")
        self.assertEqual(pdf_en.status_code, 200)
        self.assertIn(beklenen_parca, pdf_en["Content-Disposition"])
        self.assertNotIn("-EN.pdf", pdf_en["Content-Disposition"])

    def test_pdf_ingilizce_sablon_etiketleri_ve_ad_en(self):
        """EN PDF: şablona 'en' etiket sözlüğü + seçeneklerin/ürünlerin ad_en karşılığı
        gider (seed: TIR -> Truck, FOB İZMİR -> FOB Izmir); TR'de Türkçe kalır. Basamak
        gösterimi ("2+2") dile bağlı değil, ikisinde de aynı."""
        from django.template.loader import render_to_string
        from core.views import satis_teklif_pdf_baglam
        self.assertEqual(self.tip_tir.ad_en, "Truck")
        self.assertEqual(self.tip_tir.ad_dil("en"), "Truck")
        self.assertEqual(self.tip_tir.ad_dil("tr"), "TIR")
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        for dil, beklenen in (
            ("tr", ["Teklif Detayı", "FOB İZMİR", "Fiyatlara", "40&#x27; HQ KONTEYNER",
                   "navlunu dahildir", "2+2"]),
            ("en", ["Quotation Details", "FOB Izmir", "Prices include freight for",
                   "40&#x27; HQ Container", "Aluminium Platform Stepladder 2+1",
                   "Double-Sided Aluminium Stepladder 2+2", "Platform Height", "2+2"]),
        ):
            kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
            ctx = {"ts": ts, "kalemler": kalemler, "sat_teklif": True,
                  **satis_teklif_pdf_baglam(ts, kalemler, dil, self.yon)}
            html = render_to_string("core/satis_teklif_pdf.html", ctx)
            for m in beklenen:
                self.assertIn(m, html, f"{dil}: {m} yok")

    def test_pdf_materyal_ve_hs_kodu_gorunur(self):
        """Materyal (TR/EN, dile göre) ve H/S Kodu (dilden bağımsız, salt kod) teknik
        özellikler bölümünde görünür; tanımlı değilse satır hiç basılmaz."""
        from django.template.loader import render_to_string
        from core.views import satis_teklif_pdf_baglam
        self.a21.materyal = "ALÜMİNYUM"   # ORM'e doğrudan yazılıyor, buyuk_harf_tr servis
        self.a21.materyal_en = "Aluminium"  # katmanında uygulanır (bkz. test_stok.py) — burada
                                            # yalnız PDF'te dogru gösterildiği test ediliyor
        self.a21.hs_kodu = "7615.10"
        self.a21.save(update_fields=["materyal", "materyal_en", "hs_kodu"])
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        html_tr = render_to_string("core/satis_teklif_pdf.html", {
            "ts": ts, "kalemler": kalemler, "sat_teklif": True,
            **satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)})
        self.assertIn("Materyal", html_tr)
        self.assertIn("ALÜMİNYUM", html_tr)
        self.assertIn("H/S Kodu", html_tr)
        self.assertIn("7615.10", html_tr)
        kalemler_en = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        html_en = render_to_string("core/satis_teklif_pdf.html", {
            "ts": ts, "kalemler": kalemler_en, "sat_teklif": True,
            **satis_teklif_pdf_baglam(ts, kalemler_en, "en", self.yon)})
        self.assertIn("Material", html_en)
        self.assertIn(">Aluminium<", html_en)
        self.assertIn("HS Code", html_en)
        self.assertIn("7615.10", html_en)
        # c22'de materyal/hs_kodu tanımlı değil (fixture'da set edilmedi) — o kartın
        # kendi stok nesnesinde materyal_goster/hs_kodu boş kalmalı (satır basılmaz).
        c22_kalem = next(k for k in kalemler_en if k.stok_id == self.c22.pk)
        self.assertEqual(c22_kalem.materyal_goster, "")
        self.assertEqual(c22_kalem.stok.hs_kodu, "")

    def test_pdf_alici_satici_ayri_kutular(self):
        """Alıcı ve Satıcı ayrı kutularda gösterilir; eski hata (Alıcı kutusunun ilk
        satırının da 'Alıcı' etiketli olması, kb+et aynı metin) artık yok. Satıcı kutusu
        yalnız Hazırlayan/E-posta/Telefon gösterir — Ünvan/Adres/Web sitesi kullanıcı
        isteğiyle kaldırıldı (firma kimliği zaten üst kısımdaki logo ve alt bilgide var)."""
        from django.template.loader import render_to_string
        from core.views import satis_teklif_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        firma = FirmaBilgisi.get()
        firma.unvan = "SEMTA ALÜMİNYUM MERDİVEN SAN. TİC. A.Ş."
        firma.adres = "MİMARSİNAN OSB 19. CADDE NO:52 KAYSERİ"
        firma.web = "www.semtahome.com"
        firma.save()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        ctx = {"ts": ts, "kalemler": kalemler, "sat_teklif": True,
              **satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)}
        html = render_to_string("core/satis_teklif_pdf.html", ctx)
        self.assertIn("Satıcı", html)
        self.assertIn("Hazırlayan", html)
        self.assertNotIn("SEMTA ALÜMİNYUM MERDİVEN SAN. TİC. A.Ş.", html)
        self.assertNotIn("MİMARSİNAN OSB 19. CADDE NO:52 KAYSERİ", html)
        self.assertNotIn("www.semtahome.com", html)
        self.assertNotIn('<span class="et">Alıcı</span>', html)
        self.assertIn('<span class="et">Unvan</span>', html)

    def test_pdf_kdv_notu_yurt_ici_gorunur_yurtdisi_gizlenir(self):
        from core.views import satis_teklif_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        baglam_ici = satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertIn("Fiyatlara KDV dahil değildir.", baglam_ici["notlar"])
        bg = Ulke.objects.create(kod="BG", ad="BULGARİSTAN", ad_en="Bulgaria")
        ts.cari.ulke = bg
        ts.cari.save(update_fields=["ulke"])
        baglam_disi = satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertNotIn("Fiyatlara KDV dahil değildir.", baglam_disi["notlar"])

    def test_pdf_ulke_ingilizce_ceviri(self):
        from core.views import satis_teklif_pdf_baglam
        bg = Ulke.objects.create(kod="BG", ad="BULGARİSTAN", ad_en="Bulgaria")
        self.cari.ulke = bg
        self.cari.save(update_fields=["ulke"])
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        self.assertEqual(
            satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)["ulke_ad"], "BULGARİSTAN")
        self.assertEqual(
            satis_teklif_pdf_baglam(ts, kalemler, "en", self.yon)["ulke_ad"], "Bulgaria")

    def test_pdf_teslim_suresi_notlar_ve_gecerlilik_tarihli(self):
        """Teslim Süresi seçiliyse notlar/etiket dict'inde adı görünür; geçerlilik notu
        tarih varsa dinamik ('Prices are valid until ...'), yoksa varsayılan statik cümle."""
        from core.views import satis_teklif_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"),
                         self._post_govde(gecerlilik_teslim_tarihi="2026-09-24"))
        ts = self._son_teklif()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        baglam = satis_teklif_pdf_baglam(ts, kalemler, "en", self.yon)
        self.assertEqual(baglam["teslim_suresi_ad"], self.teslim.ad_dil("en"))
        self.assertIn("Prices are valid until 24.09.2026.", baglam["notlar"])
        self.assertNotIn("This quotation is binding until the validity date.", baglam["notlar"])
        ts.gecerlilik_teslim_tarihi = None
        baglam2 = satis_teklif_pdf_baglam(ts, kalemler, "en", self.yon)
        self.assertIn("This quotation is binding until the validity date.", baglam2["notlar"])

    def test_pdf_alici_ilgili_kisi_varsa_gorunur_yoksa_gizlenir(self):
        from django.template.loader import render_to_string
        from core.views import satis_teklif_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        ctx = {"ts": ts, "kalemler": kalemler, "sat_teklif": True,
              **satis_teklif_pdf_baglam(ts, kalemler, "tr", self.yon)}
        html = render_to_string("core/satis_teklif_pdf.html", ctx)
        self.assertNotIn("Adı Soyadı", html)
        self.cari.ilgili_kisi = "AYŞE YILMAZ"
        self.cari.save(update_fields=["ilgili_kisi"])
        ts2 = self._son_teklif()
        kalemler2 = list(ts2.kalemler.filter(silindi=False).select_related("stok"))
        ctx2 = {"ts": ts2, "kalemler": kalemler2, "sat_teklif": True,
               **satis_teklif_pdf_baglam(ts2, kalemler2, "tr", self.yon)}
        html2 = render_to_string("core/satis_teklif_pdf.html", ctx2)
        self.assertIn("Adı Soyadı", html2)
        self.assertIn("AYŞE YILMAZ", html2)

    def test_basamak_goster(self):
        from types import SimpleNamespace
        from core.views import _basamak_goster
        self.assertIsNone(_basamak_goster(SimpleNamespace(basamak_sayisi=None, model_kodu="C22")))
        self.assertEqual(
            _basamak_goster(SimpleNamespace(basamak_sayisi=4, model_kodu="C22")), "2+2")
        self.assertEqual(
            _basamak_goster(SimpleNamespace(basamak_sayisi=12, model_kodu="C66")), "6+6")
        self.assertEqual(
            _basamak_goster(SimpleNamespace(basamak_sayisi=3, model_kodu="A21")), "3")
        self.assertEqual(
            _basamak_goster(SimpleNamespace(basamak_sayisi=5, model_kodu="")), "5")

    def test_pdf_yurtdisi_cari_view_ucdan_uca_calisir(self):
        """Gerçek view (select_related dahil): yabancı ülkeli cari için PDF 200 dönüyor
        (cari.ulke.kod erişiminde ekstra sorgu/hata yok)."""
        bg = Ulke.objects.create(kod="BG", ad="BULGARİSTAN")
        self.cari.ulke = bg
        self.cari.save(update_fields=["ulke"])
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        r = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
