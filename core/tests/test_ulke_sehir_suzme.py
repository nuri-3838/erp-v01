"""Ülke + şehir alanı olan formlar: şehir seçilen ülkeye ait olmalı (sunucu kuralı) + ortak JS'in ihtiyaç duyduğu işaretler.

Kapsam: core.forms.SehirSelect (seçeneklere data-ulke), core.forms.UlkeSehirKurali (CariForm / AdayCariyeYeniCariForm /
AdayMusteriForm / CariSevkAdresiForm), core/templates/core/_ulke_sehir.html (ortak süzme betiği tüm formlara bağlı).
Süzmenin kendisi (ülke → şehir listesi) tarayıcıda çalışır; burada HTML tarafındaki sözleşme (data-ulke, selected, include) sınanır."""
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import AdayCariyeYeniCariForm, AdayMusteriForm, CariForm, CariSevkAdresiForm
from core.models import AdayAsamaTanim, AdayMusteri, AdayTipTanim, Cari, CariKategori, CariSevkAdresi, Sehir, Ulke
from core.services.aday import aday_musteri_olustur
from core.services.cari import cari_olustur
from core.tests.aday_yardimci import varsayilan_kaynak_id

UYUMSUZ = "Seçilen şehir bu ülkeye ait değil"


def _lokasyon():
    tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
    de = Ulke.objects.create(kod="DE", ad="ALMANYA")
    kayseri = Sehir.objects.create(ulke=tr, ad="KAYSERİ", kod="38")
    ankara = Sehir.objects.create(ulke=tr, ad="ANKARA", kod="06")
    berlin = Sehir.objects.create(ulke=de, ad="BERLİN")
    return tr, de, kayseri, ankara, berlin


def _sehir_secenekleri(html):
    """Şehir <select>'indeki (değer, data-ulke, selected) üçlüleri."""
    sel = re.search(r'<select[^>]*name="sehir"[^>]*>(.*?)</select>', html, re.S)
    assert sel, "şehir select'i yok"
    sonuc = []
    for etiket in re.findall(r"<option\b[^>]*>", sel.group(1)):
        deger = re.search(r'value="([^"]*)"', etiket)
        ulke = re.search(r'data-ulke="([^"]*)"', etiket)
        sonuc.append((deger.group(1) if deger else "", ulke.group(1) if ulke else None, "selected" in etiket))
    return sonuc


def _cari_veri(kat, **ek):
    veri = {"unvan": "ulke sehir cari", "tip": "TUZEL", "kategori": str(kat.pk), "para_birimi": "TRY"}
    veri.update(ek)
    return veri


def _aday_veri(**ek):
    veri = dict(
        unvan="ulke sehir aday", ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
        ulke="", sehir="", kategori=varsayilan_kaynak_id(),
        tip=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk, potansiyel="",
        asama=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk,
        kapanis_nedeni="", sonraki_adim="", sonraki_adim_tarihi="",
        para_birimi="TRY", iskonto_yuzdesi="0")
    veri.update(ek)
    return veri


class SehirSeceneklerindeUlkeIsaretiTest(TestCase):
    """Süzme sözleşmesi: şehir alanı data-ulke-bagli, her şehir seçeneği data-ulke=<ülke pk>, boş seçenekte işaret yok."""

    def setUp(self):
        self.tr, self.de, self.kayseri, self.ankara, self.berlin = _lokasyon()

    def test_her_formda_seceneklere_data_ulke_eklenir(self):
        for form_sinifi in (CariForm, AdayCariyeYeniCariForm, AdayMusteriForm, CariSevkAdresiForm):
            with self.subTest(form=form_sinifi.__name__):
                html = str(form_sinifi()["sehir"])
                self.assertIn("data-ulke-bagli", html)
                secenekler = _sehir_secenekleri(html)
                esle = {d: u for d, u, _ in secenekler if d}
                self.assertEqual(esle, {str(self.kayseri.pk): str(self.tr.pk), str(self.ankara.pk): str(self.tr.pk),
                                        str(self.berlin.pk): str(self.de.pk)})
                self.assertEqual([u for d, u, _ in secenekler if not d], [None])      # boş seçenek ülkesiz

    def test_ulke_alani_isaretsiz(self):
        self.assertNotIn("data-ulke-bagli", str(CariForm()["ulke"]))


class SehirUlkeSunucuKuraliTest(TestCase):
    """Şehir seçilen ülkeye ait değilse form hata verir; uyumlu / boş kombinasyonlar geçer."""

    def setUp(self):
        self.tr, self.de, self.kayseri, self.ankara, self.berlin = _lokasyon()
        self.kat = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="320")
        self.alt = CariKategori.objects.create(ad="HAMMADDE", kod="10", ust=self.kat)

    def _formlar(self, ulke, sehir):
        cari = CariForm(_cari_veri(self.alt, ulke=ulke, sehir=sehir))
        yeni = AdayCariyeYeniCariForm(_cari_veri(self.alt, ulke=ulke, sehir=sehir, vkn_tckn="1234567890", vergi_dairesi="X"))
        aday = AdayMusteriForm(_aday_veri(ulke=ulke, sehir=sehir))
        sevk = CariSevkAdresiForm({"ad": "depo", "ulke": ulke, "sehir": sehir})
        return {"cari": cari, "aday-cariye": yeni, "aday": aday, "sevk": sevk}

    def test_uyumsuz_sehir_hata(self):
        for ad, form in self._formlar(self.tr.pk, self.berlin.pk).items():
            with self.subTest(form=ad):
                self.assertFalse(form.is_valid())
                self.assertIn(UYUMSUZ, " ".join(form.errors.get("sehir", [])))

    def test_uyumlu_sehir_gecer(self):
        for ad, form in self._formlar(self.de.pk, self.berlin.pk).items():
            with self.subTest(form=ad):
                form.is_valid()
                self.assertNotIn("sehir", form.errors)
                self.assertEqual(form.cleaned_data["sehir"], self.berlin)

    def test_ulke_ve_sehir_bos_gecer(self):
        for ad, form in self._formlar("", "").items():
            with self.subTest(form=ad):
                form.is_valid()
                self.assertNotIn("sehir", form.errors)
                self.assertNotIn("ulke", form.errors)

    def test_ulkesiz_sehir_hata(self):
        for ad, form in self._formlar("", self.kayseri.pk).items():
            with self.subTest(form=ad):
                self.assertFalse(form.is_valid())
                self.assertIn("önce ülke seçin", " ".join(form.errors.get("sehir", [])))

    def test_yalniz_ulke_gecer(self):
        for ad, form in self._formlar(self.de.pk, "").items():
            with self.subTest(form=ad):
                form.is_valid()
                self.assertNotIn("sehir", form.errors)


class UlkeSehirViewTest(TestCase):
    """Gerçek ekranlar: kaydetmeyi engeller, hatayı gösterir, düzenlemede şehri korur, ortak betik sayfada."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("ulkesehir", password="x")
        cls.tr, cls.de, cls.kayseri, cls.ankara, cls.berlin = _lokasyon()
        ust = CariKategori.objects.create(ad="TEDARİKÇİLER", kod="320")
        cls.alt = CariKategori.objects.create(ad="HAMMADDE", kod="10", ust=ust)

    def setUp(self):
        self.client.force_login(self.yon)

    # --- Cari ---
    def test_cari_ekle_uyumsuz_sehir_kaydetmez(self):
        r = self.client.post(reverse("core:cari_ekle"), _cari_veri(self.alt, ulke=self.tr.pk, sehir=self.berlin.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, UYUMSUZ)
        self.assertFalse(Cari.objects.filter(unvan="ULKE SEHİR CARİ").exists())

    def test_cari_ekle_uyumlu_sehir_kaydeder(self):
        r = self.client.post(reverse("core:cari_ekle"), _cari_veri(self.alt, ulke=self.tr.pk, sehir=self.kayseri.pk))
        self.assertEqual(r.status_code, 302)
        c = Cari.objects.get(unvan="ULKE SEHİR CARİ")
        self.assertEqual((c.ulke_id, c.sehir_id), (self.tr.pk, self.kayseri.pk))

    def test_cari_duzenle_secili_sehir_korunur(self):
        c = cari_olustur(unvan="kayitli cari", para_birimi="TRY", kategori_id=self.alt.pk,
                         ulke_id=self.tr.pk, sehir_id=self.kayseri.pk)
        url = reverse("core:cari_duzenle", args=[c.pk])
        html = self.client.get(url).content.decode()
        self.assertIn(f'<option value="{self.tr.pk}" selected', html)                     # kayıtlı ülke seçili
        secili = [d for d, _, s in _sehir_secenekleri(html) if s]
        self.assertEqual(secili, [str(self.kayseri.pk)])                                  # kayıtlı şehir seçili (listede)
        r = self.client.post(url, _cari_veri(self.alt, unvan="kayitli cari", ulke=self.tr.pk, sehir=self.kayseri.pk))
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.sehir_id, self.kayseri.pk)

    def test_cari_duzenle_ulke_degisip_sehir_kalirsa_hata(self):
        c = cari_olustur(unvan="kayitli cari 2", para_birimi="TRY", kategori_id=self.alt.pk,
                         ulke_id=self.tr.pk, sehir_id=self.kayseri.pk)
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]),
                             _cari_veri(self.alt, unvan="kayitli cari 2", ulke=self.de.pk, sehir=self.kayseri.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, UYUMSUZ)
        c.refresh_from_db()
        self.assertEqual((c.ulke_id, c.sehir_id), (self.tr.pk, self.kayseri.pk))           # kayıt değişmedi

    # --- Aday müşteri ---
    def test_aday_ekle_uyumsuz_sehir_kaydetmez(self):
        r = self.client.post(reverse("core:aday_musteri_ekle"), _aday_veri(ulke=self.de.pk, sehir=self.kayseri.pk))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, UYUMSUZ)
        self.assertFalse(AdayMusteri.objects.filter(unvan="ULKE SEHİR ADAY").exists())

    def test_aday_duzenle_secili_sehir_korunur(self):
        a = aday_musteri_olustur(
            unvan="kayitli aday", para_birimi="TRY", kategori_id=varsayilan_kaynak_id(),
            tip_id=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk,
            asama_id=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk, farkli_firma_onay=True,
            ulke_id=self.de.pk, sehir_id=self.berlin.pk)
        url = reverse("core:aday_musteri_duzenle", args=[a.pk])
        html = self.client.get(url).content.decode()
        self.assertIn(f'<option value="{self.de.pk}" selected', html)
        self.assertEqual([d for d, _, s in _sehir_secenekleri(html) if s], [str(self.berlin.pk)])
        r = self.client.post(url, _aday_veri(unvan="kayitli aday", ulke=self.de.pk, sehir=self.berlin.pk))
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertEqual((a.ulke_id, a.sehir_id), (self.de.pk, self.berlin.pk))

    # --- Sevk adresi ---
    def test_sevk_adresi_uyumsuz_sehir_kaydetmez(self):
        c = cari_olustur(unvan="sevk cari", para_birimi="TRY", kategori_id=self.alt.pk)
        r = self.client.post(reverse("core:sevk_adresi_ekle", args=[c.pk]),
                             {"ad": "depo", "ulke": self.tr.pk, "sehir": self.berlin.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, UYUMSUZ)
        self.assertFalse(CariSevkAdresi.objects.filter(cari=c).exists())

    def test_sevk_adresi_duzenle_secili_sehir_korunur(self):
        c = cari_olustur(unvan="sevk cari 2", para_birimi="TRY", kategori_id=self.alt.pk)
        s = CariSevkAdresi.objects.create(cari=c, ad="DEPO", ulke=self.tr, sehir=self.ankara, varsayilan=True)
        html = self.client.get(reverse("core:sevk_adresi_duzenle", args=[s.pk])).content.decode()
        self.assertEqual([d for d, _, sec in _sehir_secenekleri(html) if sec], [str(self.ankara.pk)])

    # --- Ortak betik her ekranda ---
    def test_ortak_betik_butun_ekranlarda_bagli(self):
        c = cari_olustur(unvan="betik cari", para_birimi="TRY", kategori_id=self.alt.pk)
        a = aday_musteri_olustur(
            unvan="betik aday", para_birimi="TRY", kategori_id=varsayilan_kaynak_id(),
            tip_id=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk,
            asama_id=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk, farkli_firma_onay=True)
        sayfalar = [reverse("core:cari_ekle"), reverse("core:cari_duzenle", args=[c.pk]),
                    reverse("core:aday_musteri_ekle"), reverse("core:aday_musteri_duzenle", args=[a.pk]),
                    reverse("core:sevk_adresi_ekle", args=[c.pk]),
                    reverse("core:aday_cariye_donustur", args=[a.pk])]
        for url in sayfalar:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn("select[data-ulke-bagli]", html)                              # ortak betik (tek kopya) sayfada
                self.assertIn("data-ulke-bagli", html)
                self.assertEqual(html.count("select[data-ulke-bagli]"), 1)
