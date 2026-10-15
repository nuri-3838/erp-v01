"""Cariler FAZ 3 (2026-10-13): ülke/şehir adı Türkçe büyük harf, Kredi/Risk Limiti ekrandan kalktı (DB alanı kalır, güncelleme değeri ezmez), cari tipi
(Tüzel VKN 10 / Şahıs TCKN 11, form + servis doğrulaması), kategori zorunlu, vergi dairesi aranabilir liste (tanım ekranı + form), kur alanları yalnız
TL dışı / döviz hareketli / varsayılan dışı ayarda görünür, "Yetkili" etiketi, cari adresi → "MERKEZ ADRES" sevk adresi senkronu, elle son temas tarihi."""
import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import CariForm
from core.models import Cari, CariSevkAdresi, EkranYetki, Sehir, Ulke, VergiDairesi
from core.services import cari as cs
from core.services import lokasyon, vergi_dairesi as vds
from core.services.cari import CariHatasi, cari_guncelle, cari_olustur
from core.tests.test_cari_faz1 import TC, _kategori, gecerli_vkn

D = datetime.date


class LokasyonYazimTest(TestCase):
    def test_yeni_ulke_ve_sehir_turkce_buyuk_harf(self):
        u = lokasyon.ulke_olustur(kod="ZZ", ad="kazakistan")
        self.assertEqual(u.ad, "KAZAKİSTAN")
        self.assertEqual(lokasyon.ulke_olustur(kod="ZY", ad="ısparta ülkesi").ad, "ISPARTA ÜLKESİ")
        self.assertEqual(lokasyon.sehir_olustur(ulke_id=u.pk, ad="şimkent").ad, "ŞİMKENT")
        s = lokasyon.sehir_guncelle(Sehir.objects.get(ad="ŞİMKENT"), ulke_id=u.pk, ad="ıssız kent")
        self.assertEqual(s.ad, "ISSIZ KENT")


class Taban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.kat = _kategori()
        cls.su = User.objects.create_superuser("f3su", password="x")
        cls.sade = User.objects.create_user("f3sade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="cariler")
        cls.tr, _ = Ulke.objects.get_or_create(kod="TR", defaults={"ad": "TÜRKİYE"})
        cls.kayseri = Sehir.objects.create(ulke=cls.tr, ad="KAYSERİ")
        cls.ankara = Sehir.objects.create(ulke=cls.tr, ad="ANKARA")

    def veri(self, **ek):
        v = {"tip": "TUZEL", "unvan": "deneme firma", "kategori": str(self.kat.pk), "para_birimi": "TRY", "kur_tipi": "MB_ALIS", "iskonto_yuzdesi": "0"}
        v.update(ek)
        return v

    def cari(self, **kw):
        kw.setdefault("unvan", "mevcut firma")
        return cari_olustur(kategori_id=self.kat.pk, para_birimi=kw.pop("para_birimi", "TRY"), **kw)


class KrediLimitiTest(Taban):
    def test_formda_ve_detayda_yok_guncelleme_degeri_ezmez(self):
        self.assertNotIn("kredi_limiti", CariForm().fields)
        c = self.cari(kredi_limiti=150)
        self.assertEqual(c.kredi_limiti, 150)
        cari_guncelle(c, unvan="mevcut firma", kategori_id=self.kat.pk, para_birimi="TRY")
        c.refresh_from_db()
        self.assertEqual(c.kredi_limiti, 150)                                                       # DB alanı kalır, değer ezilmez
        self.client.force_login(self.su)
        self.assertNotContains(self.client.get(reverse("core:cari_detay", args=[c.pk])), "Kredi/Risk")
        self.assertNotContains(self.client.get(reverse("core:cari_duzenle", args=[c.pk])), "Kredi/Risk")
        self.assertNotContains(self.client.get(reverse("core:cari_ekle")), "Kredi/Risk")


class CariTipiTest(Taban):
    def test_tip_zorunlu(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip=""))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cari.objects.filter(unvan="DENEME FİRMA").exists())

    def test_tuzel_vkn_sahis_tckn_dogrulamasi(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip="TUZEL", vkn_tckn=TC))
        self.assertContains(r, "Tüzel cari için 10 haneli VKN")
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip="SAHIS", vkn_tckn=gecerli_vkn()))
        self.assertContains(r, "Şahıs cari için 11 haneli TCKN")
        self.assertFalse(Cari.objects.filter(unvan="DENEME FİRMA").exists())
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip="SAHIS", vkn_tckn=TC, unvan="ali veli"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Cari.objects.get(unvan="ALİ VELİ").tip, "SAHIS")
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip="TUZEL", vkn_tckn=gecerli_vkn(), unvan="firma a.ş."))
        self.assertEqual(r.status_code, 302)
        c = Cari.objects.get(unvan="FİRMA A.Ş.")
        self.assertEqual(c.tip, "TUZEL")
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[c.pk])), "Tüzel")

    def test_vkn_bos_tip_yeterli_yurtdisi(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(tip="TUZEL", tax_id="DE999", unvan="yurtdisi gmbh"))
        self.assertEqual(r.status_code, 302)

    def test_servis_katmaninda_uyumsuzluk_reddedilir(self):
        with self.assertRaisesMessage(CariHatasi, "Şahıs cari için 11 haneli"):
            self.cari(unvan="x", tip="SAHIS", vkn_tckn=gecerli_vkn())
        c = self.cari(unvan="y", tip="TUZEL", vkn_tckn=gecerli_vkn())
        self.assertEqual(c.tip, "TUZEL")
        cari_guncelle(c, unvan="y", kategori_id=self.kat.pk, para_birimi="TRY", vkn_tckn=gecerli_vkn())        # tip verilmedi → mevcut korunur
        c.refresh_from_db()
        self.assertEqual(c.tip, "TUZEL")
        with self.assertRaisesMessage(CariHatasi, "Geçersiz cari tipi"):
            cari_guncelle(c, unvan="y", kategori_id=self.kat.pk, para_birimi="TRY", tip="DIGER")

    def test_etiketler_ve_yetkili(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_ekle"))
        self.assertContains(r, "Cari Tipi")
        self.assertContains(r, 'id="yetkili-alan"')
        self.assertContains(r, ">Yetkili<")
        self.assertNotContains(r, "Adı Soyadı")
        c = self.cari(ilgili_kisi="ayşe")
        r = self.client.get(reverse("core:cari_detay", args=[c.pk]))
        self.assertContains(r, "Yetkili")
        self.assertNotContains(r, "Adı Soyadı")


class KategoriZorunluTest(Taban):
    def test_kategorisiz_kayit_reddedilir(self):
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_ekle"), self.veri(kategori=""))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cari.objects.filter(unvan="DENEME FİRMA").exists())
        self.assertEqual(self.client.post(reverse("core:cari_ekle"), self.veri()).status_code, 302)

    def test_duzenlemede_kategori_bos_birakilamaz(self):
        c = self.cari()
        self.client.force_login(self.sade)
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), self.veri(kategori=""))
        self.assertEqual(r.status_code, 200)


class VergiDairesiTest(Taban):
    def test_tanim_servisi(self):
        v = vds.vergi_dairesi_olustur(ad="mimarsinan", il="kayseri")
        self.assertEqual((v.ad, v.il), ("MİMARSİNAN", "KAYSERİ"))
        with self.assertRaises(vds.VergiDairesiHatasi):
            vds.vergi_dairesi_olustur(ad="MİMARSİNAN", il="KAYSERİ")
        vds.vergi_dairesi_olustur(ad="MİMARSİNAN", il="")                                            # farklı il → ayrı kayıt
        vds.vergi_dairesi_sil(v)
        self.assertEqual(VergiDairesi.objects.filter(silindi=False).count(), 1)

    def test_tanim_ekranlari(self):
        self.client.force_login(self.su)
        self.assertEqual(self.client.get(reverse("core:vergi_daireleri")).status_code, 200)
        r = self.client.post(reverse("core:vergi_dairesi_ekle"), {"ad": "gevher nesibe", "il": "kayseri"})
        self.assertEqual(r.status_code, 302)
        v = VergiDairesi.objects.get()
        self.assertEqual(v.ad, "GEVHER NESİBE")
        self.assertContains(self.client.get(reverse("core:vergi_daireleri")), "GEVHER NESİBE")
        self.client.post(reverse("core:vergi_dairesi_duzenle", args=[v.pk]), {"ad": "erciyes", "il": "kayseri"})
        v.refresh_from_db()
        self.assertEqual(v.ad, "ERCİYES")
        self.client.post(reverse("core:vergi_dairesi_sil", args=[v.pk]))
        v.refresh_from_db()
        self.assertTrue(v.silindi)
        self.client.force_login(self.sade)                                                          # ekran yetkisi yok
        self.assertEqual(self.client.get(reverse("core:vergi_daireleri")).status_code, 403)

    def test_form_yalniz_listeden_secim(self):
        vds.vergi_dairesi_olustur(ad="erciyes", il="kayseri")
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_ekle"))
        self.assertContains(r, "KAYSERİ – ERCİYES")
        r = self.client.post(reverse("core:cari_ekle"), self.veri(vergi_dairesi="LİSTEDE YOK"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Cari.objects.filter(unvan="DENEME FİRMA").exists())
        r = self.client.post(reverse("core:cari_ekle"), self.veri(vergi_dairesi="ERCİYES"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Cari.objects.get(unvan="DENEME FİRMA").vergi_dairesi, "ERCİYES")

    def test_listede_olmayan_mevcut_deger_korunur(self):
        c = self.cari(vergi_dairesi="gevhernesibe")
        self.assertEqual(c.vergi_dairesi, "GEVHERNESİBE")
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:cari_duzenle", args=[c.pk]))
        self.assertContains(r, "GEVHERNESİBE (listede yok)")
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), self.veri(vergi_dairesi="GEVHERNESİBE", unvan="mevcut firma"))
        self.assertEqual(r.status_code, 302)


class KurAlanlariGorunurlukTest(Taban):
    def test_sunucu_kurali(self):
        tl = self.cari(unvan="tl firma")
        self.assertFalse(cs.kur_alanlari_gorunur(tl))
        usd = self.cari(unvan="usd firma", para_birimi="USD")
        self.assertTrue(cs.kur_alanlari_gorunur(usd))
        tl_ayarli = self.cari(unvan="ayarli", kur_degerleme="HER_ZAMAN")
        self.assertTrue(cs.kur_alanlari_gorunur(tl_ayarli))
        self.assertTrue(cs.kur_alanlari_gorunur(tl, doviz_hareketli=True))

    def test_detay_ve_form(self):
        self.client.force_login(self.su)
        tl = self.cari(unvan="tl firma")
        r = self.client.get(reverse("core:cari_detay", args=[tl.pk]))
        self.assertNotContains(r, "Cari Kur Yöntemi")
        self.assertNotContains(r, "Kur Değerlemesi")
        self.assertContains(self.client.get(reverse("core:cari_duzenle", args=[tl.pk])), "kurZorunluGorunur = false")
        with patch("core.services.cari.cari_doviz_hareketli_mi", return_value=True):
            self.assertContains(self.client.get(reverse("core:cari_detay", args=[tl.pk])), "Cari Kur Yöntemi")
            self.assertContains(self.client.get(reverse("core:cari_duzenle", args=[tl.pk])), "kurZorunluGorunur = true")
        usd = self.cari(unvan="usd firma", para_birimi="USD")
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[usd.pk])), "Cari Kur Yöntemi")
        ayarli = self.cari(unvan="ayarli", kur_farki_hedefi="HESAP_646_656")
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[ayarli.pk])), "Kur Farkı Hedefi")
        self.assertContains(self.client.get(reverse("core:cari_ekle")), 'class="alan kur-alan"')

    def test_gizli_kur_ayari_form_kaydinda_korunur(self):
        c = self.cari(unvan="ayarli", kur_degerleme="HER_ZAMAN")
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:cari_duzenle", args=[c.pk]), self.veri(unvan="ayarli", kur_degerleme="HER_ZAMAN"))
        self.assertEqual(r.status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.kur_degerleme, "HER_ZAMAN")


class MerkezAdresSenkronTest(Taban):
    def adresli(self, **kw):
        return self.cari(ulke_id=self.tr.pk, sehir_id=self.kayseri.pk, adres="eski adres", **kw)

    def guncelle(self, c, **kw):
        veri = dict(unvan=c.unvan, kategori_id=self.kat.pk, para_birimi="TRY", ulke_id=self.tr.pk, sehir_id=self.kayseri.pk, adres="eski adres")
        veri.update(kw)
        return cari_guncelle(c, **veri)

    def test_adres_degisince_yalniz_merkez_adres_guncellenir(self):
        c = self.adresli()
        merkez = c.sevk_adresleri.get()
        self.assertEqual(merkez.ad, "MERKEZ ADRES")
        depo = cs.sevk_adresi_ekle(c, ad="depo", ulke_id=self.tr.pk, sehir_id=self.kayseri.pk, adres="depo adresi")
        self.guncelle(c, sehir_id=self.ankara.pk, adres="yeni adres")
        merkez.refresh_from_db(); depo.refresh_from_db()
        self.assertEqual((merkez.sehir_id, merkez.adres), (self.ankara.pk, "YENİ ADRES"))
        self.assertEqual((depo.sehir_id, depo.adres), (self.kayseri.pk, "DEPO ADRESİ"))               # diğer sevk adresine dokunulmaz

    def test_adres_degismezse_elle_duzenlenmis_merkez_adres_ezilmez(self):
        c = self.adresli()
        merkez = c.sevk_adresleri.get()
        cs.sevk_adresi_guncelle(merkez, ad="MERKEZ ADRES", ulke_id=self.tr.pk, sehir_id=self.kayseri.pk, adres="elle yazilan")
        self.guncelle(c, unvan="yeni unvan")                                                        # adres aynı
        merkez.refresh_from_db()
        self.assertEqual(merkez.adres, "ELLE YAZİLAN")

    def test_merkez_adres_yoksa_acilmaz_bos_adres_sevki_bosaltmaz(self):
        c = self.cari(unvan="adressiz")
        self.guncelle(c, adres="ilk adres")
        self.assertEqual(c.sevk_adresleri.filter(silindi=False).count(), 0)
        c2 = self.cari(unvan="b firma", ulke_id=self.tr.pk, sehir_id=self.kayseri.pk, adres="adres b")
        self.guncelle(c2, ulke_id=None, sehir_id=None, adres="")
        self.assertEqual(c2.sevk_adresleri.get().adres, "ADRES B")


class SonTemasTest(Taban):
    def test_kaydet_temizle_gelecek_gecersiz(self):
        c = self.cari()
        self.client.force_login(self.sade)
        url = reverse("core:cari_son_temas", args=[c.pk])
        self.assertEqual(self.client.post(url, {"tarih": "2026-01-05"}).status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.son_temas_tarihi, D(2026, 1, 5))
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[c.pk])), "05.01.2026")
        self.client.post(url, {"tarih": "2999-01-01"})                                              # gelecek tarih
        self.client.post(url, {"tarih": "abc"})
        c.refresh_from_db()
        self.assertEqual(c.son_temas_tarihi, D(2026, 1, 5))
        self.client.post(url, {"temizle": "1"})
        c.refresh_from_db()
        self.assertIsNone(c.son_temas_tarihi)

    def test_yetkisiz_kullanici_giremez_listede_filtre_yok(self):
        c = self.cari()
        d = User.objects.create_user("f3yok", password="x")
        self.client.force_login(d)
        self.assertEqual(self.client.post(reverse("core:cari_son_temas", args=[c.pk]), {"tarih": "2026-01-05"}).status_code, 403)
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cariler"))
        self.assertNotContains(r, "son_temas")
