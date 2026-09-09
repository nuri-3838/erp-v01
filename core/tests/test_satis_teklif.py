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
            satis_urunu=True, model_kodu="a21", yukleme_40hq=1000, yukleme_20dc=400,
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.c22 = stok_olustur(
            ad="cift cikisli merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="c22",
            fiyat_try="15000", kullanici=cls.yon)   # USD fiyatı ve yükleme adedi YOK
        # Seed migration'dan gelen Tanım Listesi seçenekleri
        cls.sekil = _secenek("YUKLEME_SEKLI", ad="FOB İZMİR")
        cls.kosul = _secenek("ODEME_KOSULU", ad="PEŞİN")
        cls.tip_40hq = _secenek("YUKLEME_TIPI", kod="40HQ")
        cls.tip_tir = _secenek("YUKLEME_TIPI", kod="TIR")

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
        for alan in ("yukleme_sekli", "odeme_kosulu", "yukleme_tipi", "navlun_tutari"):
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
            "yukleme_tipi": self.tip_40hq.pk, "navlun_tutari": "3.000",
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
        pdf = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertIn("-TR.pdf", pdf["Content-Disposition"])
        pdf_en = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]) + "?dil=en")
        self.assertEqual(pdf_en.status_code, 200)
        self.assertIn("-EN.pdf", pdf_en["Content-Disposition"])

    def test_pdf_ingilizce_sablon_etiketleri_ve_ad_en(self):
        """EN PDF: şablona 'en' etiket sözlüğü + seçeneklerin ad_en karşılığı gider
        (seed: TIR -> Truck, FOB İZMİR -> FOB Izmir); TR'de Türkçe kalır."""
        from django.template.loader import render_to_string
        from core.views import _PDF_ETIKET
        self.assertEqual(self.tip_tir.ad_en, "Truck")
        self.assertEqual(self.tip_tir.ad_dil("en"), "Truck")
        self.assertEqual(self.tip_tir.ad_dil("tr"), "TIR")
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        # Şablonun kendisini iki dilde HTML olarak render edip metni doğrula
        # (PDF ikilisinden metin okumak yerine).
        for dil, beklenen in (("tr", ["SATIŞ TEKLİFİ", "FOB İZMİR", "Fiyatlara", "40&#x27; HQ KONTEYNER",
                                      "navlunu dahildir"]),
                              ("en", ["QUOTATION", "FOB Izmir", "Prices include freight for",
                                     "40&#x27; HQ Container"])):
            html = render_to_string("core/satis_teklif_pdf.html", {
                "ts": ts, "kalemler": list(ts.kalemler.filter(silindi=False).select_related("stok")),
                "dil": dil, "E": _PDF_ETIKET[dil], "navlun_var": True, "hazirlayan": "Test",
                "yukleme_sekli_ad": ts.yukleme_sekli.ad_dil(dil),
                "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil(dil),
                "yukleme_tipi_ad": ts.yukleme_tipi.ad_dil(dil)})
            for m in beklenen:
                self.assertIn(m, html, f"{dil}: {m} yok")

    def test_pdf_alici_satici_ayri_kutular(self):
        """Alıcı ve Satıcı ayrı kutularda gösterilir; eski hata (Alıcı kutusunun ilk
        satırının da 'Alıcı' etiketli olması, kb+et aynı metin) artık yok."""
        from django.template.loader import render_to_string
        from core.views import _PDF_ETIKET
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        firma = FirmaBilgisi.get()
        firma.unvan = "SEMTA ALÜMİNYUM MERDİVEN SAN. TİC. A.Ş."
        firma.save()
        html = render_to_string("core/satis_teklif_pdf.html", {
            "ts": ts, "kalemler": list(ts.kalemler.filter(silindi=False).select_related("stok")),
            "dil": "tr", "E": _PDF_ETIKET["tr"], "navlun_var": True, "hazirlayan": "Nuri Özer",
            "hazirlayan_eposta": "nuri@semtahome.com", "hazirlayan_telefon": "0555 123 45 67",
            "firma": firma, "yurt_ici": True,
            "yukleme_sekli_ad": ts.yukleme_sekli.ad_dil("tr"),
            "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil("tr"),
            "yukleme_tipi_ad": ts.yukleme_tipi.ad_dil("tr")})
        self.assertIn("Satıcı", html)
        self.assertIn("SEMTA ALÜMİNYUM MERDİVEN SAN. TİC. A.Ş.", html)
        self.assertIn("nuri@semtahome.com", html)
        self.assertIn("0555 123 45 67", html)
        self.assertNotIn('<span class="et">Alıcı</span>', html)
        self.assertIn('<span class="et">Unvan</span>', html)

    def test_pdf_kdv_notu_yurt_ici_gorunur_yurtdisi_gizlenir(self):
        from django.template.loader import render_to_string
        from core.views import _PDF_ETIKET
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = self._son_teklif()
        ctx = {
            "ts": ts, "kalemler": list(ts.kalemler.filter(silindi=False).select_related("stok")),
            "dil": "tr", "E": _PDF_ETIKET["tr"], "navlun_var": False, "hazirlayan": "Test",
            "firma": None, "hazirlayan_eposta": "", "hazirlayan_telefon": "",
            "yukleme_sekli_ad": "", "odeme_kosulu_ad": "", "yukleme_tipi_ad": ""}
        html_yurtici = render_to_string("core/satis_teklif_pdf.html", {**ctx, "yurt_ici": True})
        self.assertIn("Fiyatlara KDV dahil değildir.", html_yurtici)
        html_yurtdisi = render_to_string("core/satis_teklif_pdf.html", {**ctx, "yurt_ici": False})
        self.assertNotIn("Fiyatlara KDV dahil değildir.", html_yurtdisi)

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
