"""ERP — CRM: Kaynak (AdayMusteriKategori) zorunlu + yalnız YAPRAK (alt kaynağı olmayan)
kaynaklar adaya atanabilir.

Kapsam: core.services.aday._kategori (servis doğrulaması), core.forms.AdayMusteriForm
(kategori alanı zorunlu + hiyerarşik <optgroup> seçenekleri + clean() kuralı),
core.services.aday_kategori.aday_kategori_olustur (adayı olan kaynağa alt eklenemez).
Hiyerarşik liste FİLTRESİ (core/views.py::aday_musteriler) değişmedi — bkz.
test_aday_tanim_ekranlari.py::HiyerarsikKaynakFiltresiTest."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import AdayMusteriForm
from core.models import AdayAsamaTanim, AdayMusteri, AdayMusteriKategori, AdayTipTanim
from core.services.aday import AdayHatasi, aday_musteri_olustur
from core.services.aday_kategori import AdayKategoriHatasi, aday_kategori_olustur
from core.tests.aday_yardimci import varsayilan_kaynak_id


def _aday(unvan="test aday", **kw):
    kw.setdefault("tip_id", AdayTipTanim.objects.get(sistem_kodu="ADAY").pk)
    kw.setdefault("asama_id", AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk)
    kw.setdefault("farkli_firma_onay", True)
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


# --- Servis katmanı: kaynak zorunlu + yalnız yaprak ----------------------------------------
class KaynakServisKuralTest(TestCase):
    def test_kaynaksiz_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            _aday(unvan="kaynaksiz aday", kategori_id=None)

    def test_kaynak_bos_string_de_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            _aday(unvan="kaynaksiz aday 2", kategori_id="")

    def test_ust_kaynak_secilirse_reddedilir(self):
        ust = aday_kategori_olustur(ad="GRUP BAŞLIK", kod="90")
        aday_kategori_olustur(ad="ALT KAYNAK", kod="01", ust_id=ust.pk)
        with self.assertRaises(AdayHatasi) as ctx:
            _aday(unvan="ust secilen aday", kategori_id=ust.pk)
        self.assertIn("alt kaynak", str(ctx.exception))

    def test_alt_kaynagi_olmayan_kok_secilebilir(self):
        # Bugünkü "02 FUARLAR"/"03 TAVSİYE"/"04 ZİYARET" gibi — kendisi bir yaprak.
        yaprak_kok = aday_kategori_olustur(ad="FUARLAR", kod="91")
        a = _aday(unvan="fuar aday", kategori_id=yaprak_kok.pk)
        self.assertEqual(a.kategori_id, yaprak_kok.pk)

    def test_yaprak_alt_kaynak_secilebilir(self):
        ust = aday_kategori_olustur(ad="DATA", kod="92")
        alt = aday_kategori_olustur(ad="ESKİ FİRMA", kod="01", ust_id=ust.pk)
        a = _aday(unvan="alt secilen aday", kategori_id=alt.pk)
        self.assertEqual(a.kategori_id, alt.pk)

    def test_bulunamayan_kaynak_reddedilir(self):
        with self.assertRaises(AdayHatasi):
            _aday(unvan="hayali kaynak", kategori_id=999999)


# --- Form katmanı: aynı kurallar sunucu tarafında (form.clean) ----------------------------
class KaynakFormKuralTest(TestCase):
    def test_kaynak_bos_form_gecersiz(self):
        form = AdayMusteriForm(_form_veri(kategori=""))
        self.assertFalse(form.is_valid())
        self.assertIn("kategori", form.errors)

    def test_ust_kaynak_secilince_form_hatasi(self):
        ust = aday_kategori_olustur(ad="GRUP BAŞLIK 2", kod="93")
        aday_kategori_olustur(ad="ALT", kod="01", ust_id=ust.pk)
        form = AdayMusteriForm(_form_veri(kategori=ust.pk))
        self.assertFalse(form.is_valid())
        self.assertIn("Lütfen alt kaynak seçin", str(form.errors["kategori"]))

    def test_yaprak_kaynakla_form_gecerli(self):
        form = AdayMusteriForm(_form_veri(kategori=varsayilan_kaynak_id()))
        self.assertTrue(form.is_valid(), form.errors)

    def test_optgroup_grup_basligi_secilemez_yaprak_secilebilir_secenek_listesinde(self):
        """<select> render'ında üst başlık grup ETİKETİ (optgroup label) olarak, alt
        kaynak SEÇİLEBİLİR <option> olarak görünür — spec: '<optgroup> ya da disabled'."""
        ust = aday_kategori_olustur(ad="OPTGROUP ÜST", kod="94")
        alt = aday_kategori_olustur(ad="OPTGROUP ALT", kod="01", ust_id=ust.pk)
        form = AdayMusteriForm()
        html = str(form["kategori"])
        self.assertIn('<optgroup label="94  OPTGROUP ÜST">', html)
        self.assertIn(f'value="{alt.pk}"', html)
        # Üst başlığın kendi pk'sı BİR option value'su olarak GÖRÜNMEMELİ.
        self.assertNotIn(f'value="{ust.pk}"', html)


# --- View: aday_musteri_ekle POST akışı -----------------------------------------------------
class KaynakViewKuralTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("kaynakview", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_kaynaksiz_post_kaydetmez(self):
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(kategori=""))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(AdayMusteri.objects.filter(unvan="POST FİRMA").exists())

    def test_ust_kaynakla_post_kaydetmez(self):
        ust = aday_kategori_olustur(ad="GRUP BAŞLIK 3", kod="95")
        aday_kategori_olustur(ad="ALT 3", kod="01", ust_id=ust.pk)
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(kategori=ust.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(AdayMusteri.objects.filter(unvan="POST FİRMA").exists())

    def test_yaprak_kaynakla_post_kaydeder(self):
        k = varsayilan_kaynak_id()
        r = self.client.post(reverse("core:aday_musteri_ekle"), _form_veri(kategori=k))
        self.assertEqual(r.status_code, 302)
        a = AdayMusteri.objects.get(unvan="POST FİRMA")
        self.assertEqual(a.kategori_id, k)


# --- Kaynaklar ekranı: adayı olan kaynağa alt eklenemez ------------------------------------
class AdayiOlanKaynagaAltEklenemezTest(TestCase):
    def test_dogrudan_adayi_olan_kaynaga_alt_eklenemez(self):
        yaprak = aday_kategori_olustur(ad="TEK BAŞINA", kod="96")
        _aday(unvan="bu kaynaktaki aday", kategori_id=yaprak.pk)
        with self.assertRaises(AdayKategoriHatasi) as ctx:
            aday_kategori_olustur(ad="YENİ ALT", kod="01", ust_id=yaprak.pk)
        self.assertIn("1 aday var", str(ctx.exception))

    def test_birden_fazla_adayi_olan_kaynakta_sayi_dogru(self):
        yaprak = aday_kategori_olustur(ad="ÇOK ADAYLI", kod="97")
        _aday(unvan="aday bir", kategori_id=yaprak.pk)
        _aday(unvan="aday iki", kategori_id=yaprak.pk)
        with self.assertRaises(AdayKategoriHatasi) as ctx:
            aday_kategori_olustur(ad="YENİ ALT 2", kod="01", ust_id=yaprak.pk)
        self.assertIn("2 aday var", str(ctx.exception))

    def test_adayi_olmayan_kaynaga_alt_eklenebilir(self):
        bos = aday_kategori_olustur(ad="BOŞ KAYNAK", kod="98")
        alt = aday_kategori_olustur(ad="YENİ ALT 3", kod="01", ust_id=bos.pk)
        self.assertEqual(alt.ust_id, bos.pk)

    def test_silinmis_adaylar_sayilmaz(self):
        from core.services.aday import aday_musteri_sil
        yaprak = aday_kategori_olustur(ad="SİLİNMİŞ ADAYLI", kod="99a")
        a = _aday(unvan="silinecek aday", kategori_id=yaprak.pk)
        aday_musteri_sil(a)
        alt = aday_kategori_olustur(ad="YENİ ALT 4", kod="01", ust_id=yaprak.pk)
        self.assertEqual(alt.ust_id, yaprak.pk)
