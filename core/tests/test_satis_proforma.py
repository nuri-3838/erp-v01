"""Satış Proforması — bağımsız ekran (core/views.py::satis_proforma_ekle/satis_proforma_
duzenle): Satış Teklifi'nin aksine miktar GERÇEK ve elle girilir, ürünler varsayılan HARİÇ
(müşterinin istediği satırlar işaretlenir). Zincir: Teklif → Proforma → Sipariş (aday
müşteride Sipariş'e geçiş engellenir, bkz. test_satis_teklif_aday_musteri.py)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, HesapPlani, KdvOrani, Kategori, TanimSecenegi, TeklifSiparis, Ulke
from core.services.stok import stok_olustur


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)


def _secenek(kategori, **f):
    return TanimSecenegi.objects.filter(silindi=False, kategori=kategori, **f).first()


class SatisProformaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("spyon", password="x")
        cls.bos = User.objects.create_user("spbos", password="x")
        _hesap("120.03", "MÜŞTERİ SATIŞ PROFORMA")
        cls.cari = Cari.objects.create(
            kod="C3", unvan="MÜŞTERİ SATIŞ PROFORMA", muhasebe_kodu="120.03",
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
            satis_urunu=True, model_kodu="a21", basamak_sayisi=3,
            agirlik="12,5", cbm="0,850",     # TR sayı biçimi: virgül=ondalık (bkz. core.sayi)
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.c22 = stok_olustur(
            ad="cift cikisli merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="c22", basamak_sayisi=4,
            fiyat_try="15000", kullanici=cls.yon)
        cls.sekil = _secenek("YUKLEME_SEKLI", ad="FOB İZMİR")
        cls.kosul = _secenek("ODEME_KOSULU", ad="PEŞİN")

    def test_get_tum_urunler_hazir_ama_dahil_degil(self):
        """Teklif'in aksine — müşteri hangi ürünü/adedi istediğini belirtmiştir, hiçbir
        satır varsayılan olarak işaretli gelmez."""
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_proforma_ekle"))
        self.assertEqual(r.status_code, 200)
        formset = r.context["formset"]
        self.assertEqual(len(formset.forms), 2)
        for f in formset.forms:
            self.assertFalse(f.initial["dahil"])
        self.assertIn("miktar", formset.forms[0].fields)

    def _post_govde(self, **over):
        govde = {
            "karsi_taraf_tip": "cari", "cari": self.cari.pk,
            "tarih": "2026-09-15", "para_birimi": "USD",
            "yukleme_sekli": self.sekil.pk, "odeme_kosulu": self.kosul.pk,
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.a21.pk, "form-0-dahil": "on",
            "form-0-miktar": "50", "form-0-iskonto_yuzdesi": "10",
            "form-0-birim_fiyat": "350",
            "form-1-stok": self.c22.pk, "form-1-dahil": "",
            "form-1-miktar": "", "form-1-iskonto_yuzdesi": "0", "form-1-birim_fiyat": "500",
        }
        govde.update(over)
        return govde

    def _son_proforma(self):
        return TeklifSiparis.objects.filter(
            belge_tur="PROFORMA", yon="SATIS", cari=self.cari).latest("id")

    def test_post_gercek_miktar_kaydedilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertTrue(ts.belge_no.startswith("SSP-2026-"))
        self.assertEqual(ts.kalemler.count(), 1)                 # yalnız dahil işaretli
        k = ts.kalemler.get()
        self.assertEqual(k.stok_id, self.a21.pk)
        self.assertEqual(k.miktar, Decimal("50"))
        self.assertEqual(k.iskonto_yuzdesi, Decimal("10"))
        # 50 x 350 x 0.9 = 15750
        self.assertEqual(k.tutar, Decimal("15750.00"))
        self.assertEqual(ts.ara_toplam, Decimal("15750.00"))

    def test_hicbiri_dahil_degilse_hata(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde(
            **{"form-0-dahil": ""}))
        self.assertContains(r, "En az bir ürün proformaya dahil edilmelidir")

    def test_miktarsiz_dahil_satir_reddedilir(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde(
            **{"form-0-miktar": ""}))
        self.assertContains(r, "Miktar sıfırdan büyük olmalı")

    def test_cari_meta_ve_aday_meta_context(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_proforma_ekle"))
        self.assertEqual(r.context["cari_meta"][str(self.cari.pk)],
                         {"pb": "USD", "iskonto": 10.0})
        self.assertIn("aday_meta", r.context)

    def test_duzenle_get_ve_post_miktar_guncellenir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        rg = self.client.get(reverse("core:satis_proforma_duzenle", args=[ts.pk]))
        self.assertEqual(rg.status_code, 200)
        formset = rg.context["formset"]
        dolu = [f for f in formset.forms if f.initial.get("dahil")]
        self.assertEqual(len(dolu), 1)
        self.assertEqual(dolu[0].initial["miktar"], Decimal("50"))
        rp = self.client.post(reverse("core:satis_proforma_duzenle", args=[ts.pk]),
                              self._post_govde(**{"form-0-miktar": "75"}))
        self.assertRedirects(rp, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts.refresh_from_db()
        self.assertEqual(ts.kalemler.filter(silindi=False).get().miktar, Decimal("75"))

    def test_eski_url_satis_proformasini_yonlendirir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[ts.pk]))
        self.assertRedirects(r, reverse("core:satis_proforma_duzenle", args=[ts.pk]))

    def test_detay_sayfasi_miktar_ve_genel_toplam_gosterir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(r, "50,000")            # miktar (3 basamak)
        self.assertContains(r, "Genel Toplam")

    def test_pdf_ikisi_de_calisir_tr_ve_en(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        for dil in ("tr", "en"):
            r = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]) + f"?dil={dil}")
            self.assertEqual(r.status_code, 200, dil)
            self.assertEqual(r["Content-Type"], "application/pdf")

    def test_pdf_baglam_agirlik_ve_cbm_toplam_hesaplar(self):
        """Kullanıcı isteği: proformada adet/net fiyat yanında ağırlık ve CBM bilgisi de
        olsun — kalem başına MİKTAR × stoğun birim ağırlığı/CBM'si (toplam sevkiyat
        değeri, birim değeri değil)."""
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        baglam = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        k = kalemler[0]
        self.assertEqual(k.agirlik_toplam, Decimal("50") * Decimal("12.5"))     # 625.0
        self.assertEqual(k.cbm_toplam, Decimal("50") * Decimal("0.850"))        # 42.500
        self.assertEqual(baglam["toplam_agirlik"], k.agirlik_toplam)
        self.assertEqual(baglam["toplam_cbm"], k.cbm_toplam)

    def test_pdf_kdv_yurt_ici_gorunur_ihracatta_sifirlanir(self):
        """Kullanıcı isteği: ihracat proformasında KDV olmamalı — kdv_toplam/genel_toplam
        MODEL property'leri kalemin kendi (alıcı ülkesinden bağımsız) kdv FK'sına göre
        hesaplandığı için, PDF context'i bunları yurt dışı alıcıda sıfırlayarak ayrıca
        hesaplar (bkz. satis_proforma_pdf_baglam)."""
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        baglam_ici = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertTrue(baglam_ici["yurt_ici"])
        self.assertEqual(baglam_ici["kdv_toplam"], ts.kdv_toplam)
        self.assertEqual(baglam_ici["genel_toplam"], ts.genel_toplam)
        self.assertNotIn("İhracat teslimleri KDV'den istisnadır.", baglam_ici["notlar"])

        bg = Ulke.objects.create(kod="BG", ad="BULGARİSTAN", ad_en="Bulgaria")
        ts.cari.ulke = bg
        ts.cari.save(update_fields=["ulke"])
        baglam_disi = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertFalse(baglam_disi["yurt_ici"])
        self.assertEqual(baglam_disi["kdv_toplam"], Decimal("0"))
        self.assertEqual(baglam_disi["genel_toplam"], ts.ara_toplam)   # KDV eklenmez
        self.assertIn("İhracat teslimleri KDV'den istisnadır.", baglam_disi["notlar"])

    def test_pdf_html_ihracatta_kdv_sutunu_gizlenir(self):
        from django.template.loader import render_to_string
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        bg = Ulke.objects.create(kod="BG", ad="BULGARİSTAN", ad_en="Bulgaria")
        ts.cari.ulke = bg
        ts.cari.save(update_fields=["ulke"])
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok"))
        ctx = {"ts": ts, "kalemler": kalemler,
              **satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)}
        html = render_to_string("core/satis_proforma_pdf.html", ctx)
        self.assertNotIn(">KDV<", html)
        self.assertIn("Ağırlık (kg)", html)
        self.assertIn("CBM (m³)", html)
        self.assertIn("İhracat teslimleri KDV", html)

    def test_pdf_html_sade_kolonlar_ve_toplam_satiri(self):
        """Kullanıcı isteği: Birim Fiyat/İskonto sütunları kalksın, Net Fiyat yerine sade
        'Fiyat' etiketi kullanılsın; tablonun altında TOPLAM satırında miktar/tutar/ağırlık/
        CBM toplamları görünsün."""
        from django.template.loader import render_to_string
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        baglam = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertEqual(baglam["toplam_miktar"], Decimal("50"))
        ctx = {"ts": ts, "kalemler": kalemler, **baglam}
        html = render_to_string("core/satis_proforma_pdf.html", ctx)
        self.assertNotIn("Birim Fiyat", html)
        self.assertNotIn("İskonto", html)
        self.assertNotIn("Net Fiyat", html)
        self.assertIn(">Fiyat<", html)
        self.assertIn(">TOPLAM<", html)
        self.assertIn("<tfoot>", html)

    def test_liste_odenecek_sutunu_gosterir_teklif_gibi_sadelestirilmez(self):
        """Proforma'da gerçek miktar var -> gerçek 'ödenecek' tutar anlamlı; Satış Teklifi'nin
        aksine liste sadeleştirilmez (bkz. teklif_siparis_listesi.html sat_teklif bayrağı)."""
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        r = self.client.get(reverse("core:satis_proformalari"))
        self.assertContains(r, "Ödenecek")
        self.assertContains(r, "İşlem")

    def test_tam_zincir_teklif_proformaya_siparise(self):
        """Uçtan uca: Satış Teklifi → Proformaya Çevir → Siparişe Çevir."""
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        teklif = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            cari_id=self.cari.pk, tarih=datetime.date(2026, 9, 15),
            satirlar=[{"stok_id": self.a21.pk, "miktar": "1", "birim_fiyat": "350",
                      "iskonto_yuzdesi": "10"}],
            kullanici=self.yon)
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        self.client.force_login(self.yon)
        r1 = self.client.post(reverse("core:teklif_proformaya_cevir", args=[teklif.pk]))
        proforma = TeklifSiparis.objects.get(kaynak_teklif=teklif)
        self.assertRedirects(r1, reverse("core:teklif_siparis_detay", args=[proforma.pk]))
        self.assertEqual(proforma.belge_tur, "PROFORMA")
        # müşteri gerçekte 20 adet istedi -> proforma Düzenle'den miktar güncellenir
        self.client.post(reverse("core:satis_proforma_duzenle", args=[proforma.pk]), {
            "karsi_taraf_tip": "cari", "cari": self.cari.pk,
            "tarih": "2026-09-15", "para_birimi": "USD",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.a21.pk, "form-0-dahil": "on",
            "form-0-miktar": "20", "form-0-iskonto_yuzdesi": "10", "form-0-birim_fiyat": "350",
            "form-1-stok": self.c22.pk, "form-1-dahil": "", "form-1-miktar": "",
            "form-1-iskonto_yuzdesi": "0", "form-1-birim_fiyat": "500",
        })
        proforma.refresh_from_db()
        self.assertEqual(proforma.kalemler.filter(silindi=False).get().miktar, Decimal("20"))
        self.client.post(reverse("core:teklif_siparis_onayla", args=[proforma.pk]))
        r2 = self.client.post(reverse("core:proforma_siparise_cevir", args=[proforma.pk]))
        siparis = TeklifSiparis.objects.get(kaynak_proforma=proforma)
        self.assertRedirects(r2, reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertEqual(siparis.belge_tur, "SIPARIS")
        self.assertEqual(siparis.cari_id, self.cari.pk)
        self.assertEqual(siparis.kalemler.get().miktar, Decimal("20"))

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:satis_proforma_ekle")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("core:satis_proformalari")).status_code, 403)


class SatisProformaBankaHesabiTest(TestCase):
    """Kullanıcı isteği: Proforma içinde banka seçilebilsin; yalnız proformanın PARA BİRİMİYLE
    eşleşen hesaplar listelensin (JS ile filtrelenir, sunucu tarafında _banka_coz ile
    doğrulanır); seçilen bankanın detayları hem PDF'te (satis_proforma_pdf_baglam) hem de
    detay sayfasında (teklif_siparis_detay.html) gösterilsin. Kaynak: FİNANS > Banka'daki AÇIK
    (silindi=False) gerçek hesaplar (core.models.BankaHesap) — AYARLAR > Firma Bilgileri'ndeki
    statik FirmaBanka DEĞİL (o boş kalabilir, üretimde hiç kullanılmıyor)."""

    @classmethod
    def setUpTestData(cls):
        from core.services.finans import banka_hesap_olustur, banka_olustur
        from core.models import Birim

        cls.yon = User.objects.create_superuser("spbyon", password="x")
        _hesap("120.04", "MÜŞTERİ SATIŞ PROFORMA BANKA")
        cls.cari = Cari.objects.create(
            kod="C4", unvan="MÜŞTERİ SATIŞ PROFORMA BANKA", muhasebe_kodu="120.04",
            para_birimi="USD", created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="151", ad="MAMUL2", created_by=cls.yon,
                                      updated_by=cls.yon)
        kat = Kategori.objects.create(kod="11", ad="MERDİVEN2", ust=ust,
                                      created_by=cls.yon, updated_by=cls.yon)
        birim = Birim.objects.create(ad="ADET2", kisa_ad="AD2", ondalik=0)
        kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel2",
                                      created_by=cls.yon, updated_by=cls.yon)
        cls.urun = stok_olustur(
            ad="banka test merdiveni", kategori_id=kat.pk, uretim_birimi_id=birim.pk,
            fatura_birimi_id=birim.pk, kdv_id=kdv.pk,
            satis_urunu=True, model_kodu="bt1", basamak_sayisi=3,
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        _hesap("102.94", "GARANTI BBVA USD")
        _hesap("102.95", "IS BANKASI TL")
        banka1 = banka_olustur(ad="garanti bbva", sube="kayseri", swift_kod="garaabbva",
                              kullanici=cls.yon)
        banka2 = banka_olustur(ad="iş bankası", sube="kayseri", kullanici=cls.yon)
        cls.banka_usd = banka_hesap_olustur(
            banka=banka1, ad="usd hesabı", iban="TR000000000000000000000001",
            para_birimi="USD", muhasebe_kodu="102.94", kullanici=cls.yon)
        cls.banka_try = banka_hesap_olustur(
            banka=banka2, ad="tl hesabı", iban="TR000000000000000000000002",
            para_birimi="TRY", muhasebe_kodu="102.95", kullanici=cls.yon)

    def _post_govde(self, **over):
        govde = {
            "karsi_taraf_tip": "cari", "cari": self.cari.pk,
            "tarih": "2026-09-15", "para_birimi": "USD",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.urun.pk, "form-0-dahil": "on",
            "form-0-miktar": "10", "form-0-iskonto_yuzdesi": "0",
            "form-0-birim_fiyat": "350",
        }
        govde.update(over)
        return govde

    def _son_proforma(self):
        return TeklifSiparis.objects.filter(
            belge_tur="PROFORMA", yon="SATIS", cari=self.cari).latest("id")

    def test_form_queryset_tum_aktif_bankalari_icerir(self):
        """Sunucu tarafı queryset filtrelenmez — PB'ye göre daraltma JS ile yapılır."""
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_proforma_ekle"))
        secenekler = list(r.context["bform"].fields["banka_hesabi"].queryset)
        self.assertIn(self.banka_usd, secenekler)
        self.assertIn(self.banka_try, secenekler)

    def test_banka_meta_context_pb_bilgisi_dogru(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_proforma_ekle"))
        self.assertEqual(r.context["banka_meta"][str(self.banka_usd.pk)], {"pb": "USD"})
        self.assertEqual(r.context["banka_meta"][str(self.banka_try.pk)], {"pb": "TRY"})

    def test_post_eslesen_banka_ile_proforma_olusturulur(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_proforma_ekle"),
                             self._post_govde(**{"banka_hesabi": self.banka_usd.pk}))
        ts = self._son_proforma()
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(ts.banka_hesabi_id, self.banka_usd.pk)

    def test_post_banka_secilmezse_banka_hesabi_bos_kalir(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        self.assertIsNone(ts.banka_hesabi_id)

    def test_post_uyusmayan_pb_bankasi_reddedilir(self):
        """_banka_coz: seçilen bankanın para birimi proformanın kendi para birimiyle
        uyuşmuyorsa hata — JS zaten yalnız eşleşenleri listeler ama sunucu tarafı da
        güvence altına alır."""
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_proforma_ekle"),
                             self._post_govde(**{"banka_hesabi": self.banka_try.pk}))
        self.assertContains(r, "para birimiyle uyuşmuyor")
        self.assertFalse(TeklifSiparis.objects.filter(
            belge_tur="PROFORMA", yon="SATIS", cari=self.cari).exists())

    def test_duzenle_initial_banka_hesabi_ve_guncelleme(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"),
                         self._post_govde(**{"banka_hesabi": self.banka_usd.pk}))
        ts = self._son_proforma()
        rg = self.client.get(reverse("core:satis_proforma_duzenle", args=[ts.pk]))
        self.assertEqual(rg.context["bform"].initial["banka_hesabi"], self.banka_usd.pk)
        # banka hesabı kaldırılır (boş bırakılır)
        rp = self.client.post(reverse("core:satis_proforma_duzenle", args=[ts.pk]),
                              self._post_govde(**{"banka_hesabi": ""}))
        self.assertRedirects(rp, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts.refresh_from_db()
        self.assertIsNone(ts.banka_hesabi_id)

    def test_pdf_baglam_bankalar_yalniz_secili_hesabi_icerir(self):
        """bankalar: BankaHesap+Banka'dan PDF şablonunun beklediği düz alanlara çevrilir
        (bkz. _banka_hesap_pdf_goster) — banka_adi/sube üst kurumdan, hesap_adi kendisinden."""
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"),
                         self._post_govde(**{"banka_hesabi": self.banka_usd.pk}))
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        baglam = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertEqual(len(baglam["bankalar"]), 1)
        b = baglam["bankalar"][0]
        self.assertEqual(b["banka_adi"], "GARANTİ BBVA")
        self.assertEqual(b["sube"], "KAYSERİ")
        self.assertEqual(b["hesap_adi"], "USD HESABI")
        self.assertEqual(b["iban"], "TR000000000000000000000001")
        self.assertEqual(b["para_birimi"], "USD")
        self.assertEqual(b["swift_kod"], "GARAABBVA")

    def test_pdf_html_banka_kutusu_belge_sutununda_ayri_satirlarla(self):
        """Kullanıcı isteği: Banka/Banka Şubesi ayrı satırlarda gösterilsin, en altta Swift
        Kodu olsun, ve banka kutusu Belge kutusunun altına (Alıcı'nın sağındaki boşluğa)
        taşınsın — artık altbolum'da değil, üst taraf-kolon içinde."""
        from django.template.loader import render_to_string
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"),
                         self._post_govde(**{"banka_hesabi": self.banka_usd.pk}))
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        baglam = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        html = render_to_string("core/satis_proforma_pdf.html",
                                {"ts": ts, "kalemler": kalemler, **baglam})
        self.assertIn("Banka Şubesi", html)
        self.assertIn("Swift Kodu", html)
        self.assertIn("GARAABBVA", html)
        # banka kutusu artık belge-kutu ile aynı taraf-kolon içinde, altbolum'da DEĞİL
        taraf_satir = html.split('<div class="taraf-satir">')[1].split('<table class="kalemler">')[0]
        self.assertIn("banka-kutu", taraf_satir)
        altbolum = html.split('<div class="altbolum">')[1]
        self.assertNotIn("banka-kutu", altbolum)

    def test_pdf_baglam_banka_secilmemisse_bankalar_bos(self):
        from core.views import satis_proforma_pdf_baglam
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv"))
        baglam = satis_proforma_pdf_baglam(ts, kalemler, "tr", self.yon)
        self.assertEqual(baglam["bankalar"], [])

    def test_detay_sayfasi_secili_banka_detaylarini_gosterir(self):
        """Kullanıcı isteği: 'Ben onu seçtiğimde Proforma detayının altında banka
        detaylarımız yazsın.'"""
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"),
                         self._post_govde(**{"banka_hesabi": self.banka_usd.pk}))
        ts = self._son_proforma()
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertContains(r, "GARANTİ BBVA")
        self.assertContains(r, "USD HESABI")
        self.assertContains(r, "TR000000000000000000000001")

    def test_detay_sayfasi_banka_secilmemisse_bolum_gosterilmez(self):
        self.client.force_login(self.yon)
        self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde())
        ts = self._son_proforma()
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertNotContains(r, "Banka Bilgileri")
