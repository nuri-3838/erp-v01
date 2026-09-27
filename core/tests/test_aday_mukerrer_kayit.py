"""ERP — CRM: Aday ekle/düzenle sırasında mükerrer kayıt uyarısı.

Eşleşme mantığı core.services.aday_donustur (eslesen_cariler'in YANINDA eklenen
eslesen_adaylar/eslesen_kayitlar) — burada YENİDEN YAZILMAZ, yalnız kayıt-öncesi devreye
girişi test edilir (core.services.aday.aday_musteri_olustur/guncelle +
core/views.py::aday_musteri_ekle/duzenle). Cariye Dönüştür akışının kendi eşleşme
önizlemesi (aday_cariye_donustur view'ı) test_aday_cariye_donustur.py'de zaten var,
dokunulmadı."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import AdayAsamaTanim, AdayMusteri, AdayMusteriKategori, AdayTipTanim, Cari
from core.services.aday import (
    MukerrerKayitBulunduHatasi, aday_musteri_guncelle, aday_musteri_olustur,
)
from core.services.cari import cari_olustur
from core.tests.aday_yardimci import varsayilan_kaynak_id


def _aday(unvan="test aday", **kw):
    kw.setdefault("tip_id", AdayTipTanim.objects.get(sistem_kodu="ADAY").pk)
    kw.setdefault("asama_id", AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk)
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    kw.setdefault("farkli_firma_onay", True)   # bu dosyanın KENDİ testleri hariç varsayılan
    return aday_musteri_olustur(unvan=unvan, para_birimi="TRY", **kw)


def _form_veri(**ek):
    veri = dict(
        unvan="post firma", ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
        ulke="", sehir="", kategori=varsayilan_kaynak_id(),
        tip=AdayTipTanim.objects.get(sistem_kodu="ADAY").pk, potansiyel="",
        asama=AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk,
        kapanis_nedeni="", sonraki_adim="", sonraki_adim_tarihi="",
        para_birimi="TRY", iskonto_yuzdesi="0")
    veri.update(ek)
    return veri


# --- Servis katmanı: eşleşme türleri ---------------------------------------------------------
class MukerrerServisTest(TestCase):
    def test_ayni_telefonlu_baska_aday_ile_eslesirse_onaysiz_engellenir(self):
        _aday(unvan="ilk firma", telefon="+905327024005", farkli_firma_onay=True)
        with self.assertRaises(MukerrerKayitBulunduHatasi) as ctx:
            _aday(unvan="farkli isimli firma", telefon="+905327024005", farkli_firma_onay=False)
        eslesmeler = ctx.exception.eslesmeler
        self.assertEqual(len(eslesmeler), 1)
        self.assertEqual(eslesmeler[0]["tur"], "Aday")
        self.assertEqual(eslesmeler[0]["nesne"].unvan, "İLK FİRMA")
        self.assertIn("Telefon", eslesmeler[0]["sebep"])

    def test_ayni_epostali_baska_aday_ile_eslesir(self):
        _aday(unvan="eposta bir", eposta="ayni@firma.com", farkli_firma_onay=True)
        with self.assertRaises(MukerrerKayitBulunduHatasi):
            _aday(unvan="eposta iki", eposta="AYNI@firma.com", farkli_firma_onay=False)

    def test_benzer_unvanli_aday_ile_eslesir(self):
        _aday(unvan="5G Group Plastik Metal San. ve Tic. Ltd. Şti.", farkli_firma_onay=True)
        with self.assertRaises(MukerrerKayitBulunduHatasi):
            _aday(unvan="5G Group Plastik Metal San VE Tic LTD ŞTİ", farkli_firma_onay=False)

    def test_ayni_web_alan_adli_aday_ile_eslesir(self):
        _aday(unvan="web bir", web="akc.ae", farkli_firma_onay=True)
        with self.assertRaises(MukerrerKayitBulunduHatasi):
            _aday(unvan="web iki", web="www.akc.ae", farkli_firma_onay=False)

    def test_eslesme_yoksa_onaysiz_da_kaydedilir(self):
        a = _aday(unvan="tek basina firma", telefon="+905327024005", farkli_firma_onay=False)
        self.assertIsNotNone(a.pk)

    def test_onay_ile_eslesmeye_ragmen_kaydedilir(self):
        _aday(unvan="ilk firma 2", telefon="+905327024005", farkli_firma_onay=True)
        b = _aday(unvan="farkli isim 2", telefon="+905327024005", farkli_firma_onay=True)
        self.assertIsNotNone(b.pk)

    def test_mevcut_cari_ile_eslesirse_engellenir(self):
        cari_olustur(unvan="gerçek müşteri as", para_birimi="TRY", telefon="+905327024005")
        with self.assertRaises(MukerrerKayitBulunduHatasi) as ctx:
            _aday(unvan="potansiyel firma", telefon="+905327024005", farkli_firma_onay=False)
        self.assertEqual(ctx.exception.eslesmeler[0]["tur"], "Cari")

    def test_kendisiyle_eslesmez_guncellemede(self):
        a = _aday(unvan="kendi kendine", telefon="+905327024005", farkli_firma_onay=True)
        # Aynı adayı, aynı telefonla güncellemek kendisiyle "eşleşme" saymamalı.
        guncellenen = aday_musteri_guncelle(
            a, unvan="kendi kendine yeni ad", telefon="+905327024005",
            tip_id=a.tip_id, asama_id=a.asama_id, kategori_id=a.kategori_id,
            farkli_firma_onay=False)
        self.assertEqual(guncellenen.unvan, "KENDİ KENDİNE YENİ AD")

    def test_kendi_carisiyle_eslesmez(self):
        # Dönüşmüş bir adayın (aday.cari_id dolu) kendi carisiyle eşleşmesi sayılmaz —
        # view seviyesinde dönüşmüş aday zaten düzenlenemez ama servis bunu DA garanti eder.
        cari = cari_olustur(unvan="dönüşmüş firma", para_birimi="TRY", telefon="+905327024005")
        a = _aday(unvan="donusen aday", telefon="+905327024006", farkli_firma_onay=True)
        a.cari = cari
        a.cariye_donusum_tarihi = timezone.now()
        a.save(update_fields=["cari", "cariye_donusum_tarihi"])
        guncellenen = aday_musteri_guncelle(
            a, unvan="donusen aday yeni", telefon="+905327024005",
            tip_id=a.tip_id, asama_id=a.asama_id, kategori_id=a.kategori_id,
            farkli_firma_onay=False)
        self.assertEqual(guncellenen.telefon, "+90 532 702 40 05")

    def test_baska_carinin_eslesmesi_hala_engellenir_donusum_disinda(self):
        cari_olustur(unvan="ilgisiz cari", para_birimi="TRY", telefon="+905327024009")
        a = _aday(unvan="baska aday", telefon="+905327024010", farkli_firma_onay=True)
        with self.assertRaises(MukerrerKayitBulunduHatasi):
            aday_musteri_guncelle(
                a, unvan="baska aday", telefon="+905327024009",
                tip_id=a.tip_id, asama_id=a.asama_id, kategori_id=a.kategori_id,
                farkli_firma_onay=False)


# --- View: aday_musteri_ekle/duzenle ---------------------------------------------------------
class MukerrerViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("mukerrerview", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_eslesen_kayitla_post_kaydetmez_uyari_gosterir(self):
        _aday(unvan="mevcut firma", telefon="+905327024005", farkli_firma_onay=True)
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(
            unvan="yeni firma post", telefon="+905327024005"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(AdayMusteri.objects.filter(unvan="YENİ FİRMA POST").exists())
        self.assertContains(r, "eşleşen kayıt")
        self.assertContains(r, "MEVCUT FİRMA")

    def test_onay_kutusuyla_post_kaydeder(self):
        _aday(unvan="mevcut firma 2", telefon="+905327024005", farkli_firma_onay=True)
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(
            unvan="yeni firma post 2", telefon="+905327024005", farkli_firma_onay="on"))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AdayMusteri.objects.filter(unvan="YENİ FİRMA POST 2").exists())

    def test_eslesme_yoksa_normal_kaydeder(self):
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(
            unvan="tamamen tekil firma", telefon="+905327024099"))
        self.assertEqual(r.status_code, 302)

    def test_duzenlemede_eslesen_kayit_uyarisi(self):
        _aday(unvan="rakip firma", telefon="+905327024005", farkli_firma_onay=True)
        hedef = _aday(unvan="düzenlenecek aday", farkli_firma_onay=True)
        r = self.client.post(
            reverse("core:aday_musteri_duzenle", args=[hedef.pk]),
            _form_veri(unvan="düzenlenecek aday", telefon="+905327024005",
                      kategori=hedef.kategori_id))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "RAKİP FİRMA")
        hedef.refresh_from_db()
        self.assertEqual(hedef.telefon, "")   # kaydedilmedi

    def test_kayittan_sonra_ayni_veriyle_tekrar_duzenleme_kendisiyle_eslesmiyor(self):
        hedef = _aday(unvan="sabit aday", telefon="+905327024005", farkli_firma_onay=True)
        r = self.client.post(
            reverse("core:aday_musteri_duzenle", args=[hedef.pk]),
            _form_veri(unvan="sabit aday güncellendi", telefon="+905327024005",
                      kategori=hedef.kategori_id))
        self.assertEqual(r.status_code, 302)
        hedef.refresh_from_db()
        self.assertEqual(hedef.unvan, "SABİT ADAY GÜNCELLENDİ")
