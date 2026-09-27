"""ERP — "Cariye Dönüştür" akışının yeniden yazılması: (A) yeni cari aç, (B) mevcut cariye
bağla, eşleşme bulma, dönüşmüş adayın salt okunurluğu + liste davranışı.

Kapsam: core.services.aday_donustur (servis), core/views.py::aday_cariye_donustur (view),
core/models.py::AdayMusteri.cari/cariye_donusum_tarihi. Kategori/hesap planı fixture'ı tüm
sınıflarda ortak (bkz. _kategori_hesap_agaci) — spec tablosundaki 5 kod_yolu."""
import datetime
import io
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import (
    AdayAktivite, AdayMusteri, AdayTip, AdayYetkili, Cari, CariKategori, CariYetkili,
    EkranYetki, HesapPlani, Sehir, Ulke,
)
from core.services import aday_donustur
from core.services.aday import (
    aday_aktivite_ek_ekle, aday_aktivite_ekle, aday_musteri_olustur, aday_yetkili_ekle,
)
from core.services.aday_donustur import AdayDonusturHatasi
from core.services.cari import CariHatasi


def _kategori_hesap_agaci():
    """Spec tablosundaki 5 kategori (120-10/120-20/320-10/320-30/320-40) + karşılık gelen
    HesapPlani kökleri (120/320) — kategori önerisi + gerçek dönüşüm (muhasebe hesabı)
    testleri ortak kullanır."""
    for kok, kok_ad in (("120", "ALICILAR"), ("320", "SATICILAR")):
        HesapPlani.objects.get_or_create(
            hesap_kodu=kok, defaults=dict(hesap_adi=kok_ad, rapor_grubu="BILANCO",
                                          rapor_kalemi="DV", parasal=True))
    for ust_kod, ust_ad, alt_kod, alt_ad in (
        ("120", "MÜŞTERİLER", "10", "YURTİÇİ"),
        ("120", "MÜŞTERİLER", "20", "YURTDIŞI"),
        ("320", "TEDARİKÇİLER", "10", "HAMMADDE"),
        ("320", "TEDARİKÇİLER", "30", "HİZMET"),
        ("320", "TEDARİKÇİLER", "40", "LOJİSTİK"),
    ):
        ust, _ = CariKategori.objects.get_or_create(kod=ust_kod, ust=None, defaults={"ad": ust_ad})
        CariKategori.objects.get_or_create(kod=alt_kod, ust=ust, defaults={"ad": ust_ad if False else alt_ad})


def _tr():
    return Ulke.objects.get_or_create(kod="TR", defaults={"ad": "TÜRKİYE"})[0]


def _bae():
    return Ulke.objects.get_or_create(kod="AE", defaults={"ad": "BAE"})[0]


def _aday(unvan="test aday", **kw):
    return aday_musteri_olustur(unvan=unvan, para_birimi="TRY", **kw)


def _png_dosya(ad="foto.png"):
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buf, "PNG")
    return SimpleUploadedFile(ad, buf.getvalue(), content_type="image/png")


# --- Kategori önerisi ----------------------------------------------------------------
class KategoriOnerisiTest(TestCase):
    def setUp(self):
        _kategori_hesap_agaci()

    def test_eski_musteri_turkiye_120_10(self):
        a = _aday(tip=AdayTip.ESKI_MUSTERI, ulke_id=_tr().pk)
        self.assertEqual(aday_donustur.kategori_onerisi(a).kod, "10")
        self.assertEqual(aday_donustur.kategori_onerisi(a).ust.kod, "120")

    def test_eski_musteri_yurtdisi_120_20(self):
        a = _aday(tip=AdayTip.ESKI_MUSTERI, ulke_id=_bae().pk)
        self.assertEqual(aday_donustur.kategori_onerisi(a).kod, "20")
        self.assertEqual(aday_donustur.kategori_onerisi(a).ust.kod, "120")

    def test_ulke_bos_yurtdisi_sayilir(self):
        a = _aday(tip=AdayTip.ADAY)
        self.assertEqual(aday_donustur.kategori_onerisi(a).kod, "20")

    def test_lojistik_320_40_her_iki_ulkede(self):
        for ulke in (_tr(), _bae()):
            a = _aday(tip=AdayTip.LOJISTIK, ulke_id=ulke.pk)
            k = aday_donustur.kategori_onerisi(a)
            self.assertEqual((k.ust.kod, k.kod), ("320", "40"))

    def test_gumruk_320_30(self):
        a = _aday(tip=AdayTip.GUMRUK, ulke_id=_tr().pk)
        k = aday_donustur.kategori_onerisi(a)
        self.assertEqual((k.ust.kod, k.kod), ("320", "30"))

    def test_tedarikci_320_10(self):
        a = _aday(tip=AdayTip.TEDARIKCI, ulke_id=_bae().pk)
        k = aday_donustur.kategori_onerisi(a)
        self.assertEqual((k.ust.kod, k.kod), ("320", "10"))

    def test_kategori_yoksa_none(self):
        a = _aday(tip=AdayTip.ESKI_MUSTERI, ulke_id=_tr().pk)
        CariKategori.objects.filter(ust__isnull=False).delete()   # yaprak önce (PROTECT)
        CariKategori.objects.filter(ust__isnull=True).delete()
        self.assertIsNone(aday_donustur.kategori_onerisi(a))

    def test_gercek_prod_ornegi_aday10_kaddah(self):
        """Canlı örnek: ESKI_MUSTERI + BAE (yurtdışı) -> 120-20."""
        a = _aday(unvan="KADDAH BLDG CLEANING EQUIP TR CO LLC - AKC GROUP",
                  tip=AdayTip.ESKI_MUSTERI, ulke_id=_bae().pk)
        k = aday_donustur.kategori_onerisi(a)
        self.assertEqual((k.ust.kod, k.kod), ("120", "20"))


# --- Dönüştürme engeli (Rakip / Pazar bilgisi) ----------------------------------------
class DonusturmeEngeliTest(TestCase):
    def test_rakip_engelli(self):
        a = _aday(tip=AdayTip.RAKIP)
        self.assertIsNotNone(aday_donustur.donusturme_engeli_var_mi(a))
        with self.assertRaises(AdayDonusturHatasi):
            aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")

    def test_pazar_bilgisi_engelli(self):
        a = _aday(tip=AdayTip.PAZAR_BILGISI)
        self.assertIsNotNone(aday_donustur.donusturme_engeli_var_mi(a))

    def test_normal_tip_engelsiz(self):
        for tip in (AdayTip.ADAY, AdayTip.ESKI_MUSTERI, AdayTip.ARACI, AdayTip.LOJISTIK,
                   AdayTip.GUMRUK, AdayTip.TEDARIKCI):
            a = _aday(unvan=f"firma {tip}", tip=tip)
            self.assertIsNone(aday_donustur.donusturme_engeli_var_mi(a))


# --- Eşleşme bulma (unvan/telefon/eposta/web) -----------------------------------------
class EslesenCarilerTest(TestCase):
    def test_gercek_prod_ornegi_aday239_cari40(self):
        """Canlı örnek: aday 239 '5G GROUP ... (ELMOP)' <-> cari 40 '5G GROUP ... LTD ŞTİ.'
        (unvan normalize + telefon son 9 hane eşleşiyor)."""
        cari = Cari.objects.create(
            kod="320-10-0008", unvan="5G GROUP PLASTİK METAL SAN VE TİC LTD ŞTİ.",
            telefon="03523222553", para_birimi="TRY")
        aday = _aday(unvan="5G GROUP PLASTİK METAL SAN. VE TİC. LTD. ŞTİ. (ELMOP)",
                    telefon="0352 3222553", eposta="hamdi@elmop.com")
        sonuc = aday_donustur.eslesen_cariler(aday)
        self.assertEqual(len(sonuc), 1)
        self.assertEqual(sonuc[0]["cari"].pk, cari.pk)
        self.assertIn("Unvan benzer", sonuc[0]["sebep"])
        self.assertIn("Telefon eşleşiyor", sonuc[0]["sebep"])

    def test_alakasiz_cari_eslesmez(self):
        Cari.objects.create(kod="C1", unvan="TAMAMEN BAŞKA FİRMA", para_birimi="TRY")
        aday = _aday(unvan="5G GROUP PLASTİK METAL")
        self.assertEqual(aday_donustur.eslesen_cariler(aday), [])

    def test_eposta_eslesmesi(self):
        cari = Cari.objects.create(kod="C2", unvan="X FİRMA", eposta="satis@ornek.com",
                                   para_birimi="TRY")
        aday = _aday(unvan="alakasiz unvan", eposta="SATIS@ORNEK.COM")
        sonuc = aday_donustur.eslesen_cariler(aday)
        self.assertEqual(sonuc[0]["cari"].pk, cari.pk)
        self.assertIn("E-posta eşleşiyor", sonuc[0]["sebep"])

    def test_gecersiz_eposta_eslesmeye_katilmaz(self):
        Cari.objects.create(kod="C3", unvan="Y FİRMA", eposta="satis@ornek2.com", para_birimi="TRY")
        aday = _aday(unvan="alakasiz", eposta="satis@ornek2.com", eposta_gecersiz=True)
        self.assertEqual(aday_donustur.eslesen_cariler(aday), [])

    def test_web_alan_adi_eslesmesi(self):
        cari = Cari.objects.create(kod="C4", unvan="Z FİRMA", web="https://www.akc.ae",
                                   para_birimi="TRY")
        aday = _aday(unvan="alakasiz", web="akc.ae")
        sonuc = aday_donustur.eslesen_cariler(aday)
        self.assertEqual(sonuc[0]["cari"].pk, cari.pk)
        self.assertIn("Web adresi eşleşiyor", sonuc[0]["sebep"])

    def test_silinmis_cari_eslesmeye_girmez(self):
        cari = Cari.objects.create(kod="C5", unvan="5G GROUP PLASTİK METAL", para_birimi="TRY")
        cari.silindi = True
        cari.save()
        aday = _aday(unvan="5G GROUP PLASTİK METAL")
        self.assertEqual(aday_donustur.eslesen_cariler(aday), [])


# --- E-posta başlangıç değeri (geçersiz taşınmaz, eposta_2 notlara düşer) -------------
class EpostaBaslangicDegerleriTest(TestCase):
    def test_gecerli_ilk_eposta_secilir(self):
        a = _aday(eposta="a@x.com", eposta_2="b@x.com")
        v = aday_donustur.yeni_cari_baslangic_degerleri(a)
        self.assertEqual(v["eposta"], "a@x.com")
        self.assertEqual(v["notlar"], "İkinci e-posta: b@x.com")

    def test_ilk_gecersiz_ikinci_gecerliyse_ikinci_secilir_not_yok(self):
        a = _aday(eposta="a@x.com", eposta_gecersiz=True, eposta_2="b@x.com")
        v = aday_donustur.yeni_cari_baslangic_degerleri(a)
        self.assertEqual(v["eposta"], "b@x.com")
        self.assertEqual(v["notlar"], "")

    def test_ikisi_de_gecersiz_ikisi_de_tasinmaz(self):
        a = _aday(eposta="a@x.com", eposta_gecersiz=True,
                  eposta_2="b@x.com", eposta_2_gecersiz=True)
        v = aday_donustur.yeni_cari_baslangic_degerleri(a)
        self.assertEqual(v["eposta"], "")
        self.assertEqual(v["notlar"], "")

    def test_yalniz_ilk_varsa_not_eklenmez(self):
        a = _aday(eposta="a@x.com")
        v = aday_donustur.yeni_cari_baslangic_degerleri(a)
        self.assertEqual(v["eposta"], "a@x.com")
        self.assertEqual(v["notlar"], "")


# --- (A) Yeni cari aç — servis ---------------------------------------------------------
class YeniCariAcServisTest(TestCase):
    def setUp(self):
        _kategori_hesap_agaci()
        self.yon = User.objects.create_superuser("donyon", password="x")

    def test_muhasebe_hesabi_acilir(self):
        a = _aday(unvan="beta gmbh", tip=AdayTip.TEDARIKCI, ulke_id=_tr().pk)
        kat = aday_donustur.kategori_onerisi(a)
        cari = aday_donustur.yeni_cari_ac(
            a, kullanici=self.yon, unvan=a.unvan, kategori_id=kat.pk,
            vkn_tckn="1234567890", vergi_dairesi="KADIKÖY", para_birimi="TRY")
        self.assertTrue(cari.muhasebe_kodu)
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu).exists())
        a.refresh_from_db()
        self.assertEqual(a.cari_id, cari.pk)
        self.assertIsNotNone(a.cariye_donusum_tarihi)

    def test_yetkililer_kopyalanir(self):
        a = _aday(unvan="gamma ltd")
        aday_yetkili_ekle(a, ad_soyad="ali veli", unvan="müdür", telefon="+90111",
                          whatsapp=True)
        cari = aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")
        (y,) = CariYetkili.objects.filter(cari=cari, silindi=False)
        self.assertEqual((y.ad_soyad, y.unvan, y.telefon, y.whatsapp),
                         ("ALİ VELİ", "MÜDÜR", "+90111", True))

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp(), IK_OZEL_DIR=tempfile.mkdtemp())
    def test_aktiviteler_ve_ekler_kopyalanir_ayri_dosya(self):
        a = _aday(unvan="delta ltd")
        akt = aday_aktivite_ekle(a, tarih=datetime.date(2026, 9, 1), tur="TELEFON",
                                 aciklama="ilk arama")
        aday_aktivite_ek_ekle(akt, dosya=_png_dosya())
        cari = aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")
        cari_aktiviteler = list(cari.aktiviteler.filter(silindi=False).order_by("tarih"))
        # + 1 "Not" aktivitesi (dönüşüm kaydı)
        self.assertEqual(len(cari_aktiviteler), 2)
        kopya = [k for k in cari_aktiviteler if k.tur == "TELEFON"][0]
        ek_aday = akt.ekler.filter(silindi=False).get()
        (ek_cari,) = kopya.ekler.filter(silindi=False)
        self.assertNotEqual(ek_cari.dosya.name, ek_aday.dosya.name)   # ayrı dosya kopyası
        # aday tarafı silinmez
        self.assertTrue(AdayAktivite.objects.filter(pk=akt.pk, silindi=False).exists())

    def test_not_aktivitesi_eklenir(self):
        a = _aday(unvan="epsilon ltd")
        cari = aday_donustur.yeni_cari_ac(a, kullanici=self.yon, unvan=a.unvan, para_birimi="TRY")
        not_akt = cari.aktiviteler.get(tur="NOT")
        self.assertIn(f"Aday #{a.pk}", not_akt.aciklama)
        self.assertIn("EPSİLON LTD", not_akt.aciklama)

    def test_merkez_adres_acilir_adres_doluysa(self):
        tr = _tr()
        a = _aday(unvan="zeta ltd", adres="istanbul merkez", ulke_id=tr.pk)
        cari = aday_donustur.yeni_cari_ac(a, unvan=a.unvan, adres=a.adres, ulke_id=tr.pk,
                                          para_birimi="TRY")
        sevk = cari.sevk_adresleri.filter(silindi=False).get()
        self.assertEqual(sevk.ad, "MERKEZ ADRES")
        self.assertTrue(sevk.varsayilan)

    def test_adressiz_merkez_adres_acilmaz(self):
        a = _aday(unvan="eta ltd")
        cari = aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")
        self.assertFalse(cari.sevk_adresleri.filter(silindi=False).exists())

    def test_ikinci_kez_donusturulemez(self):
        a = _aday(unvan="theta ltd")
        aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")
        with self.assertRaises(AdayDonusturHatasi):
            aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")

    def test_silinmis_aday_donusturulemez(self):
        from core.services.aday import aday_musteri_sil
        a = _aday(unvan="iota ltd")
        aday_musteri_sil(a)
        with self.assertRaises(AdayDonusturHatasi):
            aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")

    def test_transaction_hata_hicbir_sey_kaydetmez(self):
        """Aktivite kopyalama adımında hata olursa (mock) cari + aday değişikliği de geri
        alınır — tek transaction (spec madde 3)."""
        a = _aday(unvan="kappa ltd")
        aday_yetkili_ekle(a, ad_soyad="test yetkili")
        onceki_cari_sayisi = Cari.objects.count()
        onceki_yetkili_sayisi = CariYetkili.objects.count()
        with mock.patch("core.services.aday_donustur._aktiviteleri_kopyala",
                        side_effect=RuntimeError("beklenmedik hata")):
            with self.assertRaises(RuntimeError):
                aday_donustur.yeni_cari_ac(a, unvan=a.unvan, para_birimi="TRY")
        a.refresh_from_db()
        self.assertIsNone(a.cari_id)
        self.assertEqual(Cari.objects.count(), onceki_cari_sayisi)
        self.assertEqual(CariYetkili.objects.count(), onceki_yetkili_sayisi)


# --- (B) Mevcut cariye bağla — servis ---------------------------------------------------
class MevcutCariyeBaglaServisTest(TestCase):
    def setUp(self):
        self.yon = User.objects.create_superuser("baglayon", password="x")

    def test_bos_alanlar_doldurulur(self):
        cari = Cari.objects.create(kod="C10", unvan="BOŞ CARİ", para_birimi="TRY")
        a = _aday(unvan="alakasiz", telefon="+90111", telefon_whatsapp=True,
                  eposta="dolan@x.com", web="akc.ae", adres="istanbul", ilgili_kisi="ahmet")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari.refresh_from_db()
        self.assertEqual(cari.telefon, "+90111")
        self.assertTrue(cari.telefon_whatsapp)
        self.assertEqual(cari.eposta, "dolan@x.com")
        self.assertEqual(cari.web, "https://akc.ae")
        self.assertEqual(cari.adres, "İSTANBUL")
        self.assertEqual(cari.ilgili_kisi, "AHMET")

    def test_dolu_alanlarin_uzerine_yazilmaz(self):
        cari = Cari.objects.create(kod="C11", unvan="DOLU CARİ", telefon="+90999",
                                   eposta="mevcut@x.com", para_birimi="TRY")
        a = _aday(unvan="alakasiz2", telefon="+90111", eposta="yeni@x.com")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari.refresh_from_db()
        self.assertEqual(cari.telefon, "+90999")      # DEĞİŞMEDİ
        self.assertEqual(cari.eposta, "mevcut@x.com")  # DEĞİŞMEDİ

    def test_gecersiz_eposta_doldurmaz(self):
        cari = Cari.objects.create(kod="C12", unvan="X", para_birimi="TRY")
        a = _aday(unvan="alakasiz3", eposta="gecersiz@x.com", eposta_gecersiz=True)
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari.refresh_from_db()
        self.assertEqual(cari.eposta, "")

    def test_yetkili_tekrar_atlanir(self):
        cari = Cari.objects.create(kod="C13", unvan="Y", para_birimi="TRY")
        CariYetkili.objects.create(cari=cari, ad_soyad="ALİ VELİ", telefon="+90111")
        a = _aday(unvan="alakasiz4")
        aday_yetkili_ekle(a, ad_soyad="ali veli", telefon="+90111")     # aynı ad+telefon
        aday_yetkili_ekle(a, ad_soyad="yeni kisi", telefon="+90222")    # farklı
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        self.assertEqual(CariYetkili.objects.filter(cari=cari, silindi=False).count(), 2)
        self.assertTrue(CariYetkili.objects.filter(cari=cari, ad_soyad="YENİ KİSİ").exists())

    def test_muhasebe_hesabi_acilmaz_kategori_degismez(self):
        kat = CariKategori.objects.create(ad="MEVCUT KATEGORİ", kod="99")
        cari = Cari.objects.create(kod="C14", unvan="Z", kategori=kat, para_birimi="TRY")
        onceki_muh = cari.muhasebe_kodu
        a = _aday(unvan="alakasiz5", tip=AdayTip.TEDARIKCI)
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari.refresh_from_db()
        self.assertEqual(cari.muhasebe_kodu, onceki_muh)
        self.assertEqual(cari.kategori_id, kat.pk)

    def test_merkez_adres_yalniz_hic_sevk_adresi_yoksa(self):
        cari = Cari.objects.create(kod="C15", unvan="W", para_birimi="TRY")
        a = _aday(unvan="alakasiz6", adres="ankara")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        self.assertTrue(cari.sevk_adresleri.filter(silindi=False, ad="MERKEZ ADRES").exists())

    def test_var_olan_sevk_adresine_dokunulmaz(self):
        from core.services.cari import sevk_adresi_ekle
        cari = Cari.objects.create(kod="C16", unvan="V", para_birimi="TRY")
        sevk_adresi_ekle(cari, ad="ÖZEL ADRES", adres="eski adres", varsayilan=True)
        a = _aday(unvan="alakasiz7", adres="yeni adres")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        self.assertEqual(cari.sevk_adresleri.filter(silindi=False).count(), 1)
        self.assertEqual(cari.sevk_adresleri.get().ad, "ÖZEL ADRES")

    def test_doldurulan_alanlar_not_aktivitesine_yazilir(self):
        cari = Cari.objects.create(kod="C17", unvan="U", para_birimi="TRY")
        a = _aday(unvan="alakasiz8", telefon="+90111")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        not_akt = cari.aktiviteler.get(tur="NOT")
        self.assertIn("Doldurulan alanlar", not_akt.aciklama)
        self.assertIn("Telefon", not_akt.aciklama)

    def test_ikinci_kez_donusturulemez(self):
        cari = Cari.objects.create(kod="C18", unvan="T", para_birimi="TRY")
        a = _aday(unvan="alakasiz9")
        aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari2 = Cari.objects.create(kod="C19", unvan="S", para_birimi="TRY")
        with self.assertRaises(AdayDonusturHatasi):
            aday_donustur.mevcut_cariye_bagla(a, cari2, kullanici=self.yon)

    def test_rakip_engelli(self):
        cari = Cari.objects.create(kod="C20", unvan="R", para_birimi="TRY")
        a = _aday(unvan="rakip firma", tip=AdayTip.RAKIP)
        with self.assertRaises(AdayDonusturHatasi):
            aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)

    def test_transaction_hata_hicbir_sey_kaydetmez(self):
        cari = Cari.objects.create(kod="C21", unvan="Q", para_birimi="TRY")
        a = _aday(unvan="alakasiz10", telefon="+90111")
        with mock.patch("core.services.aday_donustur._aktiviteleri_kopyala",
                        side_effect=RuntimeError("hata")):
            with self.assertRaises(RuntimeError):
                aday_donustur.mevcut_cariye_bagla(a, cari, kullanici=self.yon)
        cari.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual(cari.telefon, "")   # geri alındı
        self.assertIsNone(a.cari_id)


# --- Dönüşmüş adayın davranışı: salt okunur + liste ------------------------------------
class DonusmusAdayGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("guardyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)
        self.cari = Cari.objects.create(kod="G1", unvan="GUARD CARİ", para_birimi="TRY")
        self.aday = _aday(unvan="guard aday")
        aday_donustur.mevcut_cariye_bagla(self.aday, self.cari, kullanici=self.yon)

    def test_detay_bandi_ve_buton_gizli(self):
        r = self.client.get(reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertContains(r, "Cari oldu")
        self.assertNotContains(r, "Cariye Dönüştür")
        self.assertNotContains(r, reverse("core:aday_musteri_duzenle", args=[self.aday.pk]))

    def test_duzenle_engellenir(self):
        r = self.client.get(reverse("core:aday_musteri_duzenle", args=[self.aday.pk]))
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        r2 = self.client.post(reverse("core:aday_musteri_duzenle", args=[self.aday.pk]),
                              {"unvan": "degisti"})
        self.aday.refresh_from_db()
        self.assertNotEqual(self.aday.unvan, "DEGİSTİ")

    def test_sil_engellenir(self):
        r = self.client.post(reverse("core:aday_musteri_sil", args=[self.aday.pk]))
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.aday.refresh_from_db()
        self.assertFalse(self.aday.silindi)

    def test_yetkili_ekle_engellenir(self):
        r = self.client.post(reverse("core:aday_yetkili_ekle", args=[self.aday.pk]),
                             {"ad_soyad": "yeni"})
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertFalse(AdayYetkili.objects.filter(aday=self.aday).exists())

    def test_aktivite_ekle_engellenir(self):
        r = self.client.post(reverse("core:aday_aktivite_ekle", args=[self.aday.pk]),
                             {"tarih": "2026-09-01", "tur": "NOT", "aciklama": "x"})
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        self.assertFalse(AdayAktivite.objects.filter(aday=self.aday).exists())

    def test_aktivite_duzenle_sil_engellenir(self):
        # aday henüz dönüşmeden önce eklenmiş bir aktivite olsaydı bile düzenle/sil kilitli
        # olmalı — kopyalama sonrası aday tarafına yeni aktivite EKLENEMEDİĞİ için burada
        # doğrudan servis ile (view bypass) bir aktivite oluşturup view guard'ını test ederiz.
        from core.services.aday import aday_aktivite_ekle as _ekle
        akt = _ekle(self.aday, tarih=datetime.date(2026, 9, 1), tur="NOT", aciklama="x")
        r = self.client.post(reverse("core:aday_aktivite_duzenle", args=[akt.pk]),
                             {"tarih": "2026-09-02", "tur": "NOT", "aciklama": "y"})
        self.assertRedirects(r, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        akt.refresh_from_db()
        self.assertEqual(akt.aciklama, "x")
        r2 = self.client.post(reverse("core:aday_aktivite_sil", args=[akt.pk]))
        self.assertRedirects(r2, reverse("core:aday_musteri_detay", args=[self.aday.pk]))
        akt.refresh_from_db()
        self.assertFalse(akt.silindi)

    def test_donustur_sayfasi_zaten_donusmus_yonlendirir(self):
        r = self.client.get(reverse("core:aday_cariye_donustur", args=[self.aday.pk]))
        self.assertRedirects(r, reverse("core:cari_detay", args=[self.cari.pk]))


class AdayListesiCariDurumuTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("listeyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_donusmus_aday_takip_sicak_temasyok_sekmelerinde_yok_tumude_var(self):
        cari = Cari.objects.create(kod="L1", unvan="L CARİ", para_birimi="TRY")
        from core.models import AdayAsama, AdayPotansiyel
        aday = _aday(unvan="donusmus liste aday", asama=AdayAsama.YENI,
                     potansiyel=AdayPotansiyel.YUKSEK, sonraki_adim="ara",
                     sonraki_adim_tarihi=datetime.date(2026, 12, 31))
        aday_donustur.mevcut_cariye_bagla(aday, cari, kullanici=self.yon)
        for gorunum in ("takip", "sicak", "temas_yok"):
            r = self.client.get(reverse("core:aday_musteriler"), {"gorunum": gorunum})
            self.assertNotContains(r, "DONUSMUS LİSTE ADAY")
        r = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "tumu"})
        self.assertContains(r, "DONUSMUS LİSTE ADAY")
        self.assertContains(r, ">Cari<")

    def test_cari_filtresi(self):
        cari = Cari.objects.create(kod="L2", unvan="L2 CARİ", para_birimi="TRY")
        donusmus = _aday(unvan="filtre donusmus")
        aday_donustur.mevcut_cariye_bagla(donusmus, cari, kullanici=self.yon)
        _aday(unvan="filtre normal")
        r = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "tumu", "cari": "var"})
        self.assertContains(r, "FİLTRE DONUSMUS")
        self.assertNotContains(r, "FİLTRE NORMAL")
        r2 = self.client.get(reverse("core:aday_musteriler"), {"gorunum": "tumu", "cari": "yok"})
        self.assertContains(r2, "FİLTRE NORMAL")
        self.assertNotContains(r2, "FİLTRE DONUSMUS")

    def test_gecikmis_sayac_donusmus_adayi_saymaz(self):
        cari = Cari.objects.create(kod="L3", unvan="L3 CARİ", para_birimi="TRY")
        gecmis = datetime.date(2020, 1, 1)
        donusmus = _aday(unvan="gecikmis donusmus", sonraki_adim="ara",
                         sonraki_adim_tarihi=gecmis)
        aday_donustur.mevcut_cariye_bagla(donusmus, cari, kullanici=self.yon)
        r = self.client.get(reverse("core:aday_musteriler"))
        self.assertNotContains(r, "gecikmiş takip")


class CariDetayKaynakAdayTest(TestCase):
    def test_kaynak_aday_linki(self):
        yon = User.objects.create_superuser("kaynakyon", password="x")
        cari = Cari.objects.create(kod="K1", unvan="KAYNAK CARİ", para_birimi="TRY")
        aday = _aday(unvan="kaynak aday")
        aday_donustur.mevcut_cariye_bagla(aday, cari, kullanici=yon)
        self.client.force_login(yon)
        r = self.client.get(reverse("core:cari_detay", args=[cari.pk]))
        self.assertContains(r, "Kaynak Aday")
        self.assertContains(r, f"#{aday.pk} KAYNAK ADAY")


# --- View: mod A/B, eşleşme listesi, önizleme -------------------------------------------
class DonusturViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("viewyon", password="x")

    def setUp(self):
        self.client.force_login(self.yon)
        _kategori_hesap_agaci()

    def test_get_200_ve_eslesme_gorunur(self):
        cari = Cari.objects.create(kod="V1", unvan="5G GROUP PLASTİK METAL SAN VE TİC LTD ŞTİ.",
                                   telefon="03523222553", para_birimi="TRY")
        aday = _aday(unvan="5G GROUP PLASTİK METAL SAN. VE TİC. LTD. ŞTİ. (ELMOP)",
                    telefon="0352 3222553")
        r = self.client.get(reverse("core:aday_cariye_donustur", args=[aday.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, cari.unvan)
        self.assertContains(r, "Bu cariye bağla")
        # A/B panelleri aynı <form>'da, karşılıklı dışlayıcı zorunlu alanlar (form_a.unvan/
        # form_b.cari) içeriyor — tarayıcının HTML5 doğrulaması GİZLİ panelin alanını da
        # zorunlu sayıp gönderimi engelleyebiliyordu (tarayıcıda bulunup düzeltildi);
        # novalidate + sunucu tarafı doğrulama TEK kaynak olmalı.
        self.assertContains(r, "novalidate")

    def test_mod_a_post_yeni_cari_acar(self):
        aday = _aday(unvan="mod a firma", ulke_id=_bae().pk, tip=AdayTip.TEDARIKCI)
        r = self.client.post(reverse("core:aday_cariye_donustur", args=[aday.pk]), {
            "mod": "A", "unvan": aday.unvan, "kisa_ad": "", "vergi_dairesi": "",
            "vkn_tckn": "", "tax_id": "", "telefon": "", "telefon_whatsapp": "",
            "telefon_2": "", "telefon_2_whatsapp": "", "eposta": "", "web": "",
            "ilgili_kisi": "", "kep_adresi": "", "ulke": "", "sehir": "", "adres": "",
            "para_birimi": "TRY", "kur_tipi": "MB_ALIS", "kredi_limiti": "0",
            "iskonto_yuzdesi": "0", "notlar": "",
        })
        self.assertEqual(r.status_code, 302)
        aday.refresh_from_db()
        self.assertIsNotNone(aday.cari_id)
        self.assertEqual(aday.cari.unvan, "MOD A FİRMA")

    def test_mod_a_turkiye_vkn_zorunlu_form_hatasi(self):
        aday = _aday(unvan="vknsiz firma", ulke_id=_tr().pk)
        r = self.client.post(reverse("core:aday_cariye_donustur", args=[aday.pk]), {
            "mod": "A", "unvan": aday.unvan, "kisa_ad": "", "vergi_dairesi": "",
            "vkn_tckn": "", "tax_id": "", "telefon": "", "telefon_whatsapp": "",
            "telefon_2": "", "telefon_2_whatsapp": "", "eposta": "", "web": "",
            "ilgili_kisi": "", "kep_adresi": "", "ulke": _tr().pk, "sehir": "", "adres": "",
            "para_birimi": "TRY", "kur_tipi": "MB_ALIS", "kredi_limiti": "0",
            "iskonto_yuzdesi": "0", "notlar": "",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "VKN/TCKN zorunlu")
        aday.refresh_from_db()
        self.assertIsNone(aday.cari_id)

    def test_mod_b_post_baglar(self):
        cari = Cari.objects.create(kod="V2", unvan="V2 CARİ", para_birimi="TRY")
        aday = _aday(unvan="mod b firma")
        r = self.client.post(reverse("core:aday_cariye_donustur", args=[aday.pk]),
                             {"mod": "B", "cari": cari.pk})
        self.assertEqual(r.status_code, 302)
        aday.refresh_from_db()
        self.assertEqual(aday.cari_id, cari.pk)

    def test_mevcut_cari_onizleme_gosterilir(self):
        cari = Cari.objects.create(kod="V3", unvan="V3 CARİ", para_birimi="TRY")
        aday = _aday(unvan="onizleme firma", telefon="+90111")
        r = self.client.get(reverse("core:aday_cariye_donustur", args=[aday.pk]),
                            {"mevcut_cari": cari.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "doldurulacak")
        self.assertContains(r, "Telefon")
