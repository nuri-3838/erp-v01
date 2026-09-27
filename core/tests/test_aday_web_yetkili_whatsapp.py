"""ERP — Aday kartı: Web, Adres, Yetkililer, WhatsApp, aktiviteyle sonraki adım.

Kapsam: core.dogrulama.web_normalize (aday+cari ortak), core_extras.wa_link, AdayYetkili
CRUD (servis+view, CariYetkili ile birebir aynı desen), Aday liste aramasının web/yetkili
alanlarını da kapsaması (sorgu sayısı sabit). Aktiviteyle sonraki adım testleri ayrı dosyada
(test_aday_aktivite.py::AdayAktiviteSonrakiAdimTest) — burada tekrar edilmez."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.dogrulama import web_normalize
from core.models import AdayAktivite, AdayAsamaTanim, AdayTipTanim, AdayYetkili, EkranYetki
from core.services.aday import (
    AdayHatasi, aday_aktivite_ekle, aday_aktivite_sil, aday_musteri_guncelle,
    aday_musteri_olustur, aday_yetkili_ekle, aday_yetkili_guncelle, aday_yetkili_sil,
    aktif_aday_yetkilileri,
)
from core.services.cari import CariHatasi, cari_guncelle, cari_olustur
from core.templatetags.core_extras import wa_link
from core.tests.aday_yardimci import varsayilan_kaynak_id


def _aday(unvan="test aday", **kw):
    kw.setdefault("tip_id", AdayTipTanim.objects.get(sistem_kodu="ADAY").pk)
    kw.setdefault("asama_id", AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk)
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    return aday_musteri_olustur(unvan=unvan, para_birimi="TRY", **kw)


# --- web_normalize (core.dogrulama) — TEK ortak fonksiyon --------------------
class WebNormalizeTest(TestCase):
    def test_bos_bos_doner(self):
        self.assertEqual(web_normalize(""), "")
        self.assertEqual(web_normalize(None), "")
        self.assertEqual(web_normalize("   "), "")

    def test_semali_adres_oldugu_gibi_kalir(self):
        self.assertEqual(web_normalize("https://akc.ae"), "https://akc.ae")
        self.assertEqual(web_normalize("http://akc.ae"), "http://akc.ae")

    def test_www_semasiz_https_eklenir(self):
        self.assertEqual(web_normalize("www.akc.ae"), "https://www.akc.ae")

    def test_semasiz_ciplak_domain_https_eklenir(self):
        self.assertEqual(web_normalize("akc.ae"), "https://akc.ae")

    def test_bastaki_sondaki_bosluk_kirpilir(self):
        self.assertEqual(web_normalize("  akc.ae  "), "https://akc.ae")

    def test_gecersiz_url_none_doner(self):
        self.assertIsNone(web_normalize("boyle bir site yok ki bosluklu"))
        self.assertIsNone(web_normalize("https://"))


# --- wa_link (core_extras şablon filtresi) ------------------------------------
class WaLinkFiltresiTest(TestCase):
    def test_arti_ile_baslayan_numaradan_link_uretilir(self):
        self.assertEqual(wa_link("+905327024005"), "https://wa.me/905327024005")

    def test_bosluk_parantez_tire_temizlenir(self):
        self.assertEqual(wa_link("+90 (532) 702-40-05"), "https://wa.me/905327024005")

    def test_arti_yoksa_none(self):
        self.assertIsNone(wa_link("05327024005"))
        self.assertIsNone(wa_link("5327024005"))

    def test_bos_none(self):
        self.assertIsNone(wa_link(""))
        self.assertIsNone(wa_link(None))


# --- Servis: web normalizasyonu aday+cari _alanlar üzerinden -----------------
class WebServisEntegrasyonTest(TestCase):
    def test_aday_www_normalize_edilir(self):
        a = _aday(web="www.akc.ae")
        self.assertEqual(a.web, "https://www.akc.ae")

    def test_aday_gecersiz_web_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            _aday(web="gecersiz url boslukla")

    def test_aday_guncelle_web_normalize(self):
        a = _aday(web="")
        aday_musteri_guncelle(a, unvan=a.unvan, web="akc.ae",
                              tip_id=a.tip_id, asama_id=a.asama_id, kategori_id=a.kategori_id)
        a.refresh_from_db()
        self.assertEqual(a.web, "https://akc.ae")

    def test_cari_www_normalize_edilir(self):
        c = cari_olustur(unvan="cari firma", para_birimi="TRY", web="www.akc.ae")
        self.assertEqual(c.web, "https://www.akc.ae")

    def test_cari_gecersiz_web_reddedilir(self):
        with self.assertRaises(CariHatasi):
            cari_olustur(unvan="cari firma 2", para_birimi="TRY", web="gecersiz url boslukla")

    def test_aday_telefon_whatsapp_kaydedilir(self):
        a = _aday(telefon="+905327024005", telefon_whatsapp=True)
        self.assertTrue(a.telefon_whatsapp)
        self.assertFalse(a.telefon_2_whatsapp)

    def test_aday_adres_buyuk_harfe_cevrilir(self):
        a = _aday(adres="istanbul, kagithane")
        self.assertEqual(a.adres, "İSTANBUL, KAGİTHANE")


# --- AdayYetkili servis: CariYetkili ile birebir aynı desen -------------------
class AdayYetkiliServisTest(TestCase):
    def test_yetkili_ekle_upper(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali veli", unvan="müdür")
        self.assertEqual((y.ad_soyad, y.unvan), ("ALİ VELİ", "MÜDÜR"))
        self.assertEqual(y.aday_id, a.pk)

    def test_bos_ad_soyad_reddedilir(self):
        a = _aday()
        with self.assertRaises(AdayHatasi):
            aday_yetkili_ekle(a, ad_soyad="   ")

    def test_yetkili_guncelle(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali")
        y2 = aday_yetkili_guncelle(y, ad_soyad="veli", whatsapp=True, telefon="+905327024005")
        self.assertEqual(y2.ad_soyad, "VELİ")
        self.assertTrue(y2.whatsapp)

    def test_silinmis_yetkili_duzenlenemez(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali")
        aday_yetkili_sil(y)
        with self.assertRaises(AdayHatasi):
            aday_yetkili_guncelle(y, ad_soyad="veli")

    def test_yetkili_sil(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali")
        aday_yetkili_sil(y)
        y.refresh_from_db()
        self.assertTrue(y.silindi)

    def test_yetkili_sil_idempotent(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali")
        aday_yetkili_sil(y)
        aday_yetkili_sil(y)   # ikinci çağrı hata vermemeli
        y.refresh_from_db()
        self.assertTrue(y.silindi)

    def test_aktif_yetkililer_silinmisi_haric_tutar_ve_alfabetik_sirali(self):
        a = _aday()
        aday_yetkili_ekle(a, ad_soyad="zeynep")
        aday_yetkili_ekle(a, ad_soyad="ahmet")
        silinen = aday_yetkili_ekle(a, ad_soyad="mahmut")
        aday_yetkili_sil(silinen)
        liste = list(aktif_aday_yetkilileri(a))
        self.assertEqual([y.ad_soyad for y in liste], ["AHMET", "ZEYNEP"])

    def test_whatsapp_varsayilan_false(self):
        a = _aday()
        y = aday_yetkili_ekle(a, ad_soyad="ali")
        self.assertFalse(y.whatsapp)


# --- AdayYetkili view: cari yetkili view'ları ile birebir aynı desen ----------
class AdayYetkiliViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili_kul = User.objects.create_user("adyetyet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili_kul, ekran_kod="aday_musteriler")
        cls.bos = User.objects.create_user("adyetbos", password="x")
        cls.aday = _aday("formal aday")

    def test_detay_sayfasi_yetkililer_gorunur(self):
        self.client.force_login(self.yetkili_kul)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertContains(r, "Yetkili Kişiler")
        # Paylaşılan partial'ın {% comment %} bloğu HTML'e sızmamalı (bkz. bellek dersi:
        # çok satırlı {# #} strip edilmeyebilir — burada {% comment %} kullanıldı).
        self.assertNotContains(r, "Paylaşılan Yetkili Kişiler tablosu")

    def test_yetkili_ekle_post(self):
        self.client.force_login(self.yetkili_kul)
        r = self.client.post(reverse("core:aday_yetkili_ekle", args=[self.aday.pk]),
                             {"ad_soyad": "ali veli"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AdayYetkili.objects.filter(aday=self.aday, ad_soyad="ALİ VELİ").exists())

    def test_yetkili_duzenle_post(self):
        y = aday_yetkili_ekle(self.aday, ad_soyad="ali")
        self.client.force_login(self.yetkili_kul)
        r = self.client.post(reverse("core:aday_yetkili_duzenle", args=[y.pk]),
                             {"ad_soyad": "veli", "whatsapp": "on", "telefon": "+905327024005"})
        self.assertEqual(r.status_code, 302)
        y.refresh_from_db()
        self.assertEqual(y.ad_soyad, "VELİ")
        self.assertTrue(y.whatsapp)

    def test_yetkili_sil_post(self):
        y = aday_yetkili_ekle(self.aday, ad_soyad="ali")
        self.client.force_login(self.yetkili_kul)
        r = self.client.post(reverse("core:aday_yetkili_sil", args=[y.pk]))
        self.assertEqual(r.status_code, 302)
        y.refresh_from_db()
        self.assertTrue(y.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(
                reverse("core:aday_yetkili_ekle", args=[self.aday.pk])).status_code, 403)

    def test_whatsapp_linki_detayda_gorunur(self):
        aday_yetkili_ekle(self.aday, ad_soyad="ali", telefon="+905327024005", whatsapp=True)
        self.client.force_login(self.yetkili_kul)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertContains(r, "https://wa.me/905327024005")

    def test_dogrulanamayan_numarada_ulke_kodu_eksik_uyarisi(self):
        # "123" telefon_normalize ile doğrulanamaz (bkz. core.dogrulama.telefon_normalize)
        # — "+" olmadan AYNEN kalır, wa_link None döner, şablon bu uyarıyı gösterir. Geçerli
        # yerel numaralar (ör. "05327024005") artık kayıttan önce "+90 ..." biçimine
        # normalize edildiği için bu yolu artık tetiklemez (bkz. test_telefon_normalize.py).
        aday_yetkili_ekle(self.aday, ad_soyad="veli", telefon="123", whatsapp=True)
        self.client.force_login(self.yetkili_kul)
        r = self.client.get(reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertContains(r, "Ülke kodu eksik")


# --- Aday listesi araması: web + yetkili alanları, sorgu sayısı sabit ---------
class AdayAramaWebYetkiliTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili_kul = User.objects.create_user("adarawebyet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili_kul, ekran_kod="aday_musteriler")

    def setUp(self):
        self.client.force_login(self.yetkili_kul)

    def test_web_alaniyla_bulunur(self):
        _aday("alfa firma", web="akc.ae")
        _aday("beta firma")
        r = self.client.get(reverse("core:aday_musteriler"), {"ara": "akc.ae", "gorunum": "tumu"})
        self.assertContains(r, "ALFA FİRMA")
        self.assertNotContains(r, "BETA FİRMA")

    def test_yetkili_ad_soyad_ile_bulunur(self):
        hedef = _aday("gamma firma")
        aday_yetkili_ekle(hedef, ad_soyad="mehmet yilmaz")
        _aday("delta firma")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "mehmet yilmaz", "gorunum": "tumu"})
        self.assertContains(r, "GAMMA FİRMA")
        self.assertNotContains(r, "DELTA FİRMA")

    def test_yetkili_telefon_ile_bulunur(self):
        hedef = _aday("epsilon firma")
        aday_yetkili_ekle(hedef, ad_soyad="x", telefon="+905327024005")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "5327024005", "gorunum": "tumu"})
        self.assertContains(r, "EPSİLON FİRMA")

    def test_yetkili_eposta_ile_bulunur(self):
        hedef = _aday("zeta firma")
        aday_yetkili_ekle(hedef, ad_soyad="x", eposta="satinalma@ornekfirma.com")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "satinalma@ornekfirma.com", "gorunum": "tumu"})
        self.assertContains(r, "ZETA FİRMA")

    def test_coklu_yetkili_satiri_coklamaz(self):
        """Exists kullanıldığı için bir adayın birden çok eşleşen yetkilisi olsa da liste
        satırı TEK kez görünür (JOIN + distinct değil)."""
        hedef = _aday("eta firma")
        aday_yetkili_ekle(hedef, ad_soyad="ahmet demir")
        aday_yetkili_ekle(hedef, ad_soyad="ahmet yildiz")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "ahmet", "gorunum": "tumu"})
        self.assertEqual(r.context["kayitlar"].paginator.count, 1)

    def test_adres_alaniyla_bulunur(self):
        _aday("iota firma", adres="istanbul kagithane sanayi")
        _aday("kappa firma")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "kagithane", "gorunum": "tumu"})
        self.assertContains(r, "İOTA FİRMA")
        self.assertNotContains(r, "KAPPA FİRMA")

    def test_aktivite_aciklamasiyla_bulunur(self):
        hedef = _aday("lambda firma")
        aday_aktivite_ekle(hedef, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                           aciklama="Hedef pazar: Irak ve komsu ulkeler.")
        _aday("mu firma")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "Hedef pazar", "gorunum": "tumu"})
        self.assertContains(r, "LAMBDA FİRMA")
        self.assertNotContains(r, "MU FİRMA")

    def test_aktivite_aramasi_buyuk_kucuk_harf_duyarsiz(self):
        hedef = _aday("nu firma")
        aday_aktivite_ekle(hedef, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                           aciklama="CANTON fuarinda gorustuk.")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "canton", "gorunum": "tumu"})
        self.assertContains(r, "NU FİRMA")

    def test_aktivite_aramasi_turkce_karakter_duyarsiz(self):
        hedef = _aday("xi firma")
        aday_aktivite_ekle(hedef, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                           aciklama="Gümüşhane bölgesinde görüşme yapıldı.")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "gümüşhane", "gorunum": "tumu"})
        self.assertContains(r, "Xİ FİRMA")

    def test_silinmis_aktivite_aramada_gorunmez(self):
        hedef = _aday("omicron firma")
        akt = aday_aktivite_ekle(hedef, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                                 aciklama="ozel anahtar kelime XYZABC")
        aday_aktivite_sil(akt)
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "XYZABC", "gorunum": "tumu"})
        self.assertEqual(r.context["kayitlar"].paginator.count, 0)

    def test_coklu_aktivite_eslesmesi_satiri_coklamaz(self):
        hedef = _aday("pi firma")
        aday_aktivite_ekle(hedef, tarih="2026-09-01", tur=AdayAktivite.Tur.NOT,
                           aciklama="ozel terim ABCXYZ birinci")
        aday_aktivite_ekle(hedef, tarih="2026-09-02", tur=AdayAktivite.Tur.NOT,
                           aciklama="ozel terim ABCXYZ ikinci")
        r = self.client.get(reverse("core:aday_musteriler"),
                            {"ara": "ABCXYZ", "gorunum": "tumu"})
        self.assertEqual(r.context["kayitlar"].paginator.count, 1)

