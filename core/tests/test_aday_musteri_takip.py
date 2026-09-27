"""Aday Müşteri ADIM 2: sonraki_adim/sonraki_adim_tarihi + eposta_gecersiz/eposta_2_gecersiz —
servis/form kuralları, liste filtreleri (takip/eposta), gecikmiş sayaç, Europe/Istanbul
gece yarısı sınırı."""
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import AdayMusteriForm
from core.models import AdayAsamaTanim, AdayPotansiyelTanim, AdayTipTanim
from core.services.aday import (
    AdayHatasi, aday_musteri_guncelle as _aday_musteri_guncelle_ham,
    aday_musteri_olustur as _aday_musteri_olustur_ham,
)
from core.tarih import tr_bugun
from core.tests.aday_yardimci import varsayilan_kaynak_id

UTC = dt_timezone.utc


def aday_musteri_olustur(*, tip=None, asama=None, **kw):
    """tip/potansiyel/asama artık FK — bu dosyada hâlâ sistem_kodu STRİNGİ ile çağrılabilsin
    diye ince bir çeviri katmanı (verilmezse eski CharField varsayılanları ADAY/YENİ)."""
    kw["tip_id"] = AdayTipTanim.objects.get(sistem_kodu=tip or "ADAY").pk
    kw["asama_id"] = AdayAsamaTanim.objects.get(sistem_kodu=asama or "YENI").pk
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    return _aday_musteri_olustur_ham(**kw)


def aday_musteri_guncelle(aday, *, tip=None, asama=None, **kw):
    """Aynı çeviri — verilmezse adayın MEVCUT tip/aşaması korunur (gerçek form akışıyla
    aynı: form her zaman tüm alanları taşır, burada testin belirtmediği alan değişmez)."""
    kw["tip_id"] = (AdayTipTanim.objects.get(sistem_kodu=tip).pk if tip else aday.tip_id)
    kw["asama_id"] = (AdayAsamaTanim.objects.get(sistem_kodu=asama).pk if asama else aday.asama_id)
    kw.setdefault("kategori_id", aday.kategori_id)
    return _aday_musteri_guncelle_ham(aday, **kw)


def _form_temel_veri(**ek):
    veri = dict(
        unvan="test firma", ilgili_kisi="", telefon="", telefon_2="",
        eposta="", eposta_gecersiz="", eposta_2="", eposta_2_gecersiz="",
        ulke="", sehir="", kategori=varsayilan_kaynak_id(), tip="ADAY", potansiyel="",
        asama="YENI", kapanis_nedeni="", sonraki_adim="", sonraki_adim_tarihi="",
        para_birimi="TRY", iskonto_yuzdesi="0")
    veri.update(ek)
    veri["tip"] = AdayTipTanim.objects.get(sistem_kodu=veri["tip"]).pk if veri["tip"] else ""
    veri["asama"] = AdayAsamaTanim.objects.get(sistem_kodu=veri["asama"]).pk if veri["asama"] else ""
    if veri["potansiyel"]:
        veri["potansiyel"] = AdayPotansiyelTanim.objects.get(sistem_kodu=veri["potansiyel"]).pk
    return veri


class AdayMusteriTakipServisTest(TestCase):
    def test_varsayilan_bos(self):
        a = aday_musteri_olustur(unvan="firma a")
        self.assertEqual(a.sonraki_adim, "")
        self.assertIsNone(a.sonraki_adim_tarihi)
        self.assertFalse(a.eposta_gecersiz)
        self.assertFalse(a.eposta_2_gecersiz)

    def test_tarih_var_metin_yok_hata(self):
        with self.assertRaises(AdayHatasi):
            aday_musteri_olustur(unvan="firma b", sonraki_adim_tarihi=date(2026, 10, 15))

    def test_metin_var_tarih_yok_serbest(self):
        a = aday_musteri_olustur(unvan="firma c", sonraki_adim="ara")
        self.assertEqual(a.sonraki_adim, "ara")
        self.assertIsNone(a.sonraki_adim_tarihi)

    def test_ikisi_de_dolu_basarili(self):
        a = aday_musteri_olustur(unvan="firma d", sonraki_adim="whatsapptan ara",
                                 sonraki_adim_tarihi=date(2026, 10, 15))
        self.assertEqual(a.sonraki_adim_tarihi, date(2026, 10, 15))

    def test_bos_epostada_isaret_zorla_false(self):
        a = aday_musteri_olustur(unvan="firma e", eposta="", eposta_gecersiz=True)
        self.assertFalse(a.eposta_gecersiz)

    def test_dolu_epostada_isaret_kaydedilir(self):
        a = aday_musteri_olustur(unvan="firma f", eposta="x@y.com", eposta_gecersiz=True)
        self.assertTrue(a.eposta_gecersiz)

    def test_guncellede_eposta_degisince_isaret_sifirlanir(self):
        a = aday_musteri_olustur(unvan="firma g", eposta="eski@x.com", eposta_gecersiz=True)
        self.assertTrue(a.eposta_gecersiz)
        aday_musteri_guncelle(a, unvan="firma g", eposta="yeni@x.com", eposta_gecersiz=True)
        a.refresh_from_db()
        self.assertEqual(a.eposta, "yeni@x.com")
        self.assertFalse(a.eposta_gecersiz)

    def test_guncellede_eposta_degismezse_isaret_korunur(self):
        a = aday_musteri_olustur(unvan="firma h", eposta="ayni@x.com", eposta_gecersiz=True)
        aday_musteri_guncelle(a, unvan="firma h yeni", eposta="ayni@x.com", eposta_gecersiz=True)
        a.refresh_from_db()
        self.assertTrue(a.eposta_gecersiz)

    def test_eposta_2_icin_ayni_kurallar(self):
        a = aday_musteri_olustur(unvan="firma i", eposta_2="eski2@x.com", eposta_2_gecersiz=True)
        aday_musteri_guncelle(a, unvan="firma i", eposta_2="yeni2@x.com", eposta_2_gecersiz=True)
        a.refresh_from_db()
        self.assertFalse(a.eposta_2_gecersiz)


class AdayMusteriTakipFormTest(TestCase):
    def test_tarih_var_metin_yok_form_hatasi(self):
        form = AdayMusteriForm(_form_temel_veri(sonraki_adim_tarihi="2026-10-15"))
        self.assertFalse(form.is_valid())
        self.assertIn("sonraki_adim", form.errors)

    def test_ikisi_de_dolu_form_gecerli(self):
        form = AdayMusteriForm(_form_temel_veri(
            sonraki_adim="ara", sonraki_adim_tarihi="2026-10-15"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_metin_var_tarih_yok_form_gecerli(self):
        form = AdayMusteriForm(_form_temel_veri(sonraki_adim="sadece metin"))
        self.assertTrue(form.is_valid(), form.errors)


class TakipDurumTest(TestCase):
    def test_gecmis_bugun_ileri(self):
        bugun = tr_bugun()
        gecmis = aday_musteri_olustur(unvan="gecmis firma", sonraki_adim="x",
                                      sonraki_adim_tarihi=bugun - timedelta(days=1))
        simdi = aday_musteri_olustur(unvan="bugun firma", sonraki_adim="x",
                                     sonraki_adim_tarihi=bugun)
        ileri = aday_musteri_olustur(unvan="ileri firma", sonraki_adim="x",
                                     sonraki_adim_tarihi=bugun + timedelta(days=1))
        yok = aday_musteri_olustur(unvan="yok firma")
        self.assertEqual(gecmis.takip_durum, "gecmis")
        self.assertEqual(simdi.takip_durum, "bugun")
        self.assertEqual(ileri.takip_durum, "ileri")
        self.assertEqual(yok.takip_durum, "")

    def test_gece_yarisi_siniri_23_30_utc(self):
        # 23:30 UTC = 02:30 TR ertesi gün -> tr_bugun() bir sonraki takvim gunu olmali
        an = datetime(2026, 9, 19, 23, 30, tzinfo=UTC)
        with mock.patch("django.utils.timezone.now", return_value=an):
            bugun_tr = tr_bugun()
            self.assertEqual(bugun_tr, date(2026, 9, 20))
            a = aday_musteri_olustur(unvan="sinir firma", sonraki_adim="x",
                                     sonraki_adim_tarihi=date(2026, 9, 20))
            self.assertEqual(a.takip_durum, "bugun")


class AdayMusteriTakipListeViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("takyon", password="x")

    def test_takip_gecmis_filtresi(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="gecikmis a", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun - timedelta(days=3))
        aday_musteri_olustur(unvan="ileri b", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=3))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "gecmis"})
        self.assertContains(r, "GECİKMİS A")
        self.assertNotContains(r, "İLERİ B")

    def test_takip_bugun_filtresi(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="tam bugun", sonraki_adim="x", sonraki_adim_tarihi=bugun)
        aday_musteri_olustur(unvan="yarin firma", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=1))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "bugun"})
        self.assertContains(r, "TAM BUGUN")
        self.assertNotContains(r, "YARİN FİRMA")

    def test_takip_hafta_filtresi_araligi(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="hafta icinde", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=7))
        aday_musteri_olustur(unvan="hafta disinda", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=8))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "hafta"})
        self.assertContains(r, "HAFTA İCİNDE")
        self.assertNotContains(r, "HAFTA DİSİNDA")

    def test_takip_planli_filtresi(self):
        aday_musteri_olustur(unvan="planli firma", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun() + timedelta(days=30))
        aday_musteri_olustur(unvan="plansiz firma")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "planli"})
        self.assertContains(r, "PLANLİ FİRMA")
        self.assertNotContains(r, "PLANSİZ FİRMA")

    def test_takip_yok_filtresi(self):
        aday_musteri_olustur(unvan="planli firma 2", sonraki_adim="x",
                             sonraki_adim_tarihi=tr_bugun() + timedelta(days=30))
        aday_musteri_olustur(unvan="plansiz firma 2")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "yok"})
        self.assertContains(r, "PLANSİZ FİRMA 2")
        self.assertNotContains(r, "PLANLİ FİRMA 2")

    def test_takip_secimi_siralamayi_degistirir(self):
        # gorunum=tumu: "siralama uzak" (+20 gün) Takibim sekmesinin (<=+7 gün) kuralı
        # dışında kalır — ikisini birden görebilmek için Tümü sekmesi seçilir (bkz.
        # core/tests/test_aday_musteri_sekme_filtre.py sekme kurallarının kendi testleri).
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="siralama uzak", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=20))
        aday_musteri_olustur(unvan="siralama yakin", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun + timedelta(days=1))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "planli", "gorunum": "tumu"})
        icerik = r.content.decode("utf-8")
        self.assertLess(icerik.index("SİRALAMA YAKİN"), icerik.index("SİRALAMA UZAK"))

    def test_eposta_gecerli_filtresi(self):
        aday_musteri_olustur(unvan="gecerli eposta", eposta="a@x.com")
        aday_musteri_olustur(unvan="gecersiz eposta", eposta="b@x.com", eposta_gecersiz=True)
        aday_musteri_olustur(unvan="epostasiz firma")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"eposta": "gecerli"})
        self.assertContains(r, "GECERLİ EPOSTA")
        self.assertNotContains(r, "GECERSİZ EPOSTA")
        self.assertNotContains(r, "EPOSTASİZ FİRMA")

    def test_eposta_gecersiz_filtresi_hepsi_gecersiz_olmali(self):
        # tek adresi var ve gecersiz -> gecersiz sayilir
        aday_musteri_olustur(unvan="tek gecersiz", eposta="a@x.com", eposta_gecersiz=True)
        # iki adresi var, biri gecerli -> gecersiz SAYILMAZ (en az biri gecerli)
        aday_musteri_olustur(unvan="kismen gecerli", eposta="a@x.com", eposta_gecersiz=True,
                             eposta_2="b@x.com", eposta_2_gecersiz=False)
        # iki adresi de gecersiz -> gecersiz sayilir
        aday_musteri_olustur(unvan="ikisi de gecersiz", eposta="a@x.com", eposta_gecersiz=True,
                             eposta_2="b@x.com", eposta_2_gecersiz=True)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"eposta": "gecersiz"})
        self.assertContains(r, "TEK GECERSİZ")
        self.assertContains(r, "İKİSİ DE GECERSİZ")
        self.assertNotContains(r, "KİSMEN GECERLİ")

    def test_eposta_yok_filtresi(self):
        aday_musteri_olustur(unvan="hicbiri yok", eposta="", eposta_2="")
        aday_musteri_olustur(unvan="biri var", eposta="a@x.com")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"eposta": "yok"})
        self.assertContains(r, "HİCBİRİ YOK")
        self.assertNotContains(r, "BİRİ VAR")

    def test_gecikmis_sayac_gosterilir_ve_link_dogru(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="gecikmis sayac", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun - timedelta(days=1))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertContains(r, "gecikmiş takip")
        # Sekmeler revizyonu: link artık hem Takibim sekmesine geçer hem Gecikmiş filtresini
        # uygular (bkz. core/tests/test_aday_musteri_sekme_filtre.py::test_gecikmis_link_gorunum_ve_takip_tasir).
        self.assertContains(r, "gorunum=takip")
        self.assertContains(r, "takip=gecmis")

    def test_gecikmis_sayac_sifirsa_gorunmez(self):
        aday_musteri_olustur(unvan="gecikmesiz firma")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertNotContains(r, "gecikmiş takip")

    def test_diger_filtrelerle_birlikte_calisir(self):
        bugun = tr_bugun()
        aday_musteri_olustur(unvan="kombine firma", tip="ARACI", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun)
        aday_musteri_olustur(unvan="kombine disi", tip="ADAY", sonraki_adim="x",
                             sonraki_adim_tarihi=bugun)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"takip": "bugun", "tip": "ARACI"})
        self.assertContains(r, "KOMBİNE FİRMA")
        self.assertNotContains(r, "KOMBİNE DİSİ")

    def test_sayfalama_ve_boyut_takip_eposta_filtresini_korur(self):
        aday_musteri_olustur(unvan="korunan firma", eposta="a@x.com")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:aday_musteriler"), {"eposta": "gecerli", "boyut": 25})
        self.assertContains(r, 'name="eposta" value="gecerli"')
