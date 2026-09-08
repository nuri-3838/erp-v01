"""AYARLAR > Firma Bilgileri — tekil kayıt (get_or_create) + serbest sayıda banka
hesabı satırı. TR büyük harf (e-posta/web hariç), IBAN normalizasyonu, resave'de
banka satırlarının tamamen yenilenmesi, logo yükleme, yönetici-only erişim."""
import io
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import FirmaBanka, FirmaBilgisi
from core.services.firma import FirmaHatasi, firma_bilgisi_getir, firma_bilgisi_kaydet


def _png(boyut=(400, 300), renk=(30, 90, 200, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", boyut, renk).save(buf, "PNG")
    buf.seek(0)
    return buf


class FirmaBilgisiServisTest(TestCase):
    def test_tekil_kayit_get_or_create(self):
        f1 = firma_bilgisi_getir()
        f2 = firma_bilgisi_getir()
        self.assertEqual(f1.pk, f2.pk)
        self.assertEqual(FirmaBilgisi.objects.count(), 1)

    def test_kaydet_tr_buyuk_harf_eposta_web_haric(self):
        f = firma_bilgisi_kaydet(
            unvan="semta alüminyum merdiven imalatı", vergi_dairesi="kayseri vergi dairesi",
            vergi_no="1234567890", adres="anbar mah. istanbul", telefon="0352 000 00 00",
            eposta="Info@Semtahome.com", web="www.SemtaHome.com")
        self.assertEqual(f.unvan, "SEMTA ALÜMİNYUM MERDİVEN İMALATI")
        self.assertEqual(f.vergi_dairesi, "KAYSERİ VERGİ DAİRESİ")
        self.assertEqual(f.adres, "ANBAR MAH. İSTANBUL")
        self.assertEqual(f.vergi_no, "1234567890")
        self.assertEqual(f.telefon, "0352 000 00 00")
        self.assertEqual(f.eposta, "Info@Semtahome.com")           # İSTİSNA: dönüştürülmez
        self.assertEqual(f.web, "www.SemtaHome.com")               # İSTİSNA: dönüştürülmez

    def test_ikinci_kaydet_tekil_kaydi_gunceller_yeni_olusturmaz(self):
        firma_bilgisi_kaydet(unvan="ilk")
        firma_bilgisi_kaydet(unvan="ikinci")
        self.assertEqual(FirmaBilgisi.objects.count(), 1)
        self.assertEqual(firma_bilgisi_getir().unvan, "İKİNCİ")

    def test_banka_satirlari_yazilir_tr_buyuk_harf_ve_iban_normalize(self):
        f = firma_bilgisi_kaydet(unvan="x", bankalar=[
            {"banka_adi": "iş bankası", "sube": "kayseri", "hesap_sahibi": "semta a.ş.",
             "iban": "tr33 0006 1005 1978 6457 8413 26", "para_birimi": "EUR"},
        ])
        b = f.bankalar.get(silindi=False)
        self.assertEqual(b.banka_adi, "İŞ BANKASI")
        self.assertEqual(b.sube, "KAYSERİ")
        self.assertEqual(b.hesap_sahibi, "SEMTA A.Ş.")
        self.assertEqual(b.iban, "TR330006100519786457841326")     # boşluksuz + büyük
        self.assertEqual(b.para_birimi, "EUR")
        self.assertEqual(b.sira, 0)

    def test_bos_banka_adi_satiri_atlanir(self):
        f = firma_bilgisi_kaydet(unvan="x", bankalar=[
            {"banka_adi": "", "iban": "TR1"}, {"banka_adi": "gerçek banka", "para_birimi": "TRY"},
        ])
        self.assertEqual(f.bankalar.filter(silindi=False).count(), 1)

    def test_gecersiz_para_birimi_reddedilir(self):
        with self.assertRaises(FirmaHatasi):
            firma_bilgisi_kaydet(unvan="x", bankalar=[
                {"banka_adi": "banka", "para_birimi": "XYZ"}])

    def test_yeniden_kaydetmek_eski_banka_satirlarini_yenisiyle_degistirir(self):
        f = firma_bilgisi_kaydet(unvan="x", bankalar=[
            {"banka_adi": "eski banka", "para_birimi": "TRY"}])
        eski = f.bankalar.get(silindi=False)
        firma_bilgisi_kaydet(unvan="x", bankalar=[
            {"banka_adi": "yeni banka 1", "para_birimi": "USD"},
            {"banka_adi": "yeni banka 2", "para_birimi": "EUR"}])
        eski.refresh_from_db()
        self.assertTrue(eski.silindi)
        aktif = list(f.bankalar.filter(silindi=False).order_by("sira").values_list("banka_adi", flat=True))
        self.assertEqual(aktif, ["YENİ BANKA 1", "YENİ BANKA 2"])

    def test_bankalar_bos_liste_tum_satirlari_kaldirir(self):
        f = firma_bilgisi_kaydet(unvan="x", bankalar=[{"banka_adi": "banka", "para_birimi": "TRY"}])
        firma_bilgisi_kaydet(unvan="x", bankalar=[])
        self.assertEqual(f.bankalar.filter(silindi=False).count(), 0)

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_logo_none_ise_mevcut_korunur_dosya_verilince_degisir(self):
        from core.gorsel import kucult_webp
        f = firma_bilgisi_kaydet(unvan="x", logo=kucult_webp(_png(), ad="firma"))
        ilk_logo = f.logo.name
        firma_bilgisi_kaydet(unvan="x", logo=None)                 # yeni dosya yok — korunmalı
        f.refresh_from_db()
        self.assertEqual(f.logo.name, ilk_logo)
        firma_bilgisi_kaydet(unvan="x", logo=kucult_webp(_png(), ad="firma2"))
        f.refresh_from_db()
        self.assertNotEqual(f.logo.name, ilk_logo)


class FirmaBilgisiViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fbyon", password="x")
        cls.bos = User.objects.create_user("fbbos", password="x")

    def test_anonim_login_sayfasina_yonlenir(self):
        r = self.client.get(reverse("core:firma_bilgileri"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:firma_bilgileri")).status_code, 403)

    def test_get_bos_formu_gosterir(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:firma_bilgileri"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Firma Bilgileri")
        self.assertContains(r, "Banka Hesapları")

    def _formset_govde(self, satirlar):
        veri = {"banka-TOTAL_FORMS": str(len(satirlar)), "banka-INITIAL_FORMS": "0",
                "banka-MIN_NUM_FORMS": "0", "banka-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            for alan in ("banka_adi", "sube", "hesap_sahibi", "iban", "para_birimi"):
                veri[f"banka-{i}-{alan}"] = s.get(alan, "")
        return veri

    def test_post_kaydeder_ve_geri_gelir(self):
        self.client.force_login(self.yon)
        gövde = {"unvan": "test firma", "vergi_dairesi": "kayseri", "vergi_no": "123",
                 "telefon": "0352", "eposta": "info@test.com", "web": "test.com",
                 "adres": "test adres"}
        gövde.update(self._formset_govde([
            {"banka_adi": "iş bankası", "iban": "TR330006100519786457841326", "para_birimi": "EUR"},
        ]))
        r = self.client.post(reverse("core:firma_bilgileri"), gövde)
        self.assertEqual(r.status_code, 302)
        f = firma_bilgisi_getir()
        self.assertEqual(f.unvan, "TEST FİRMA")
        self.assertEqual(f.eposta, "info@test.com")
        self.assertEqual(f.bankalar.filter(silindi=False).count(), 1)
        d = self.client.get(reverse("core:firma_bilgileri"))
        self.assertContains(d, "TEST FİRMA")
        self.assertContains(d, "İŞ BANKASI")

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_post_logo_yukler(self):
        self.client.force_login(self.yon)
        dosya = SimpleUploadedFile("logo.png", _png().read(), content_type="image/png")
        gövde = {"unvan": "logolu firma"}
        gövde.update(self._formset_govde([]))
        gövde["logo"] = dosya
        r = self.client.post(reverse("core:firma_bilgileri"), gövde)
        self.assertEqual(r.status_code, 302)
        f = firma_bilgisi_getir()
        self.assertTrue(f.logo)
        self.assertTrue(f.logo.name.endswith(".webp"))
        d = self.client.get(reverse("core:firma_bilgileri"))
        self.assertContains(d, f.logo.url)

    def test_menude_firma_bilgileri_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertContains(r, "Firma Bilgileri")
        self.assertContains(r, reverse("core:firma_bilgileri"))

    def test_bankasiz_bos_satirlar_hata_vermeden_atlanir(self):
        self.client.force_login(self.yon)
        gövde = {"unvan": "firma"}
        gövde.update(self._formset_govde([{"banka_adi": "", "iban": ""}]))
        r = self.client.post(reverse("core:firma_bilgileri"), gövde)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(firma_bilgisi_getir().bankalar.filter(silindi=False).count(), 0)

    def test_banka_adisiz_dolu_satir_form_hatasi_verir(self):
        self.client.force_login(self.yon)
        gövde = {"unvan": "firma"}
        gövde.update(self._formset_govde([{"banka_adi": "", "iban": "TR1"}]))
        r = self.client.post(reverse("core:firma_bilgileri"), gövde)
        self.assertEqual(r.status_code, 200)                       # form hatasıyla tekrar render
        self.assertContains(r, "Banka adı girin.")
