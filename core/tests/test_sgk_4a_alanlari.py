"""SGK 4/A muhasebe paketi için yeni alanlar (2026-11-01): personel kartında uyruk (varsayılan T.C.), meslek kodu/adı, Emekli (SGDP) onay kutusu;
işe giriş kaydında çalışma şekli (tam/kısmi süreli). Form, servis ve kart gösterimi."""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from core.models import IseGirisKaydi, Personel
from core.services import ise_giris as ig
from core.services import ise_giris_muhasebe as mu
from core.services.firma import firma_bilgisi_getir
from core.services.personel import PersonelHatasi, personel_guncelle, personel_olustur
from core.tests.ik_yardimci import OzelDizinTestTemel
from core.tests.test_ise_giris_paket import kullanici


class ServisTest(TestCase):
    def kur(self, ad="ali", **ek):
        return personel_olustur(ad=ad, soyad="veli", ise_giris_tarihi=date(2026, 11, 2), **ek)

    def test_varsayilanlar(self):
        p = self.kur()
        self.assertEqual((p.uyruk, p.meslek_kodu, p.meslek_adi, p.emekli_sgdp), ("T.C.", "", "", False))

    def test_degerler_normalize_edilir(self):
        p = self.kur(uyruk="  alman ", meslek_kodu=" 7212.01 ", meslek_adi="kaynakçı", emekli_sgdp=True)
        self.assertEqual((p.uyruk, p.meslek_kodu, p.meslek_adi, p.emekli_sgdp), ("ALMAN", "7212.01", "KAYNAKÇI", True))
        self.assertEqual(self.kur(ad="b", uyruk="").uyruk, "T.C.")                             # boş uyruk → T.C.

    def test_meslek_kodu_dogrulama(self):
        for gecersiz in ("abc", "72", "7212-01", "7212.01.", "A7212", "7212,01"):
            with self.assertRaises(PersonelHatasi, msg=gecersiz):
                self.kur(ad="x", meslek_kodu=gecersiz)
        for gecerli in ("7212", "7212.01", "212.05"):
            self.assertEqual(self.kur(ad="y" + gecerli.replace(".", ""), meslek_kodu=gecerli).meslek_kodu, gecerli)

    def test_guncelle(self):
        p = self.kur()
        personel_guncelle(p, ad="ali", soyad="veli", ise_giris_tarihi=date(2026, 11, 2), meslek_kodu="7212.01", meslek_adi="kaynakçı", emekli_sgdp=True, uyruk="alman")
        p.refresh_from_db()
        self.assertEqual((p.meslek_kodu, p.meslek_adi, p.emekli_sgdp, p.uyruk), ("7212.01", "KAYNAKÇI", True, "ALMAN"))
        personel_guncelle(p, ad="ali", soyad="veli", ise_giris_tarihi=date(2026, 11, 2), emekli_sgdp=False)
        p.refresh_from_db()
        self.assertEqual((p.emekli_sgdp, p.uyruk, p.meslek_kodu), (False, "T.C.", ""))        # form her zaman tüm alanları gönderir


class FormVeKartTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.kisi = kullanici("sgkpersonel", "personel", "personel_belgeleri")
        self.client.force_login(self.kisi)

    def veri(self, **ek):
        return {"ad": "ahmet", "soyad": "yılmaz", "ise_giris_tarihi": "2026-11-02", "uyruk": "T.C.", "meslek_kodu": "7212.01", "meslek_adi": "kaynakçı", **ek}

    def test_form_alanlari_ve_kayit(self):
        r = self.client.get(reverse("core:personel_ekle"))
        for ad in ("uyruk", "meslek_kodu", "meslek_adi", "emekli_sgdp"):
            self.assertContains(r, f'name="{ad}"')
        self.assertContains(r, "Emekli (SGDP")
        self.assertContains(r, 'value="T.C."')                                                  # varsayılan uyruk
        self.client.post(reverse("core:personel_ekle"), self.veri(emekli_sgdp="on"))
        p = Personel.objects.get(ad="AHMET")
        self.assertEqual((p.meslek_kodu, p.meslek_adi, p.emekli_sgdp, p.uyruk), ("7212.01", "KAYNAKÇI", True, "T.C."))
        kart = self.client.get(reverse("core:personel_detay", args=[p.pk])).content.decode()
        for metin in ("Meslek (SGK)", "7212.01", "KAYNAKÇI", "Sigorta Kolu", "SGDP (02)", "Uyruk"):
            self.assertIn(metin, kart)

    def test_gecersiz_meslek_kodu_kaydetmez(self):
        r = self.client.post(reverse("core:personel_ekle"), self.veri(meslek_kodu="abc"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Meslek kodu yalnız rakam ve tek bir nokta")
        self.assertFalse(Personel.objects.filter(ad="AHMET").exists())

    def test_varsayilan_sigorta_kolu_kartta(self):
        self.client.post(reverse("core:personel_ekle"), self.veri())
        p = Personel.objects.get(ad="AHMET")
        self.assertFalse(p.emekli_sgdp)
        self.assertContains(self.client.get(reverse("core:personel_detay", args=[p.pk])), "Tüm sigorta kolları (01)")


class CalismaSekliTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.kisi = kullanici("sgkisgiris", "personel", "personel_belgeleri")
        self.p = personel_olustur(ad="ali", soyad="veli", ise_giris_tarihi=date(2026, 11, 2), meslek_kodu="7212.01", gorev="x", adres="a", dogum_yeri="k",
                                  baba_adi="b", ana_adi="c", tc_kimlik_no="10000000146", dogum_tarihi=date(1990, 1, 1))
        self.url = reverse("core:ise_giris_kaydet", args=[self.p.pk])
        self.sekme = reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=ise_giris"
        self.client.force_login(self.kisi)

    def test_alan_formda_ve_varsayilan_tam(self):
        r = self.client.get(self.sekme)
        self.assertContains(r, 'name="calisma_sekli"')
        self.assertContains(r, "Kısmi süreli")
        self.client.post(self.url, {"deneme_suresi_ay": "2"})                                   # alan gelmezse tam süreli
        self.assertEqual(IseGirisKaydi.objects.get(personel=self.p).calisma_sekli, "TAM")

    def test_kismi_sureli_kaydedilir_ve_muhasebe_formuna_gecer(self):
        self.client.post(self.url, {"calisma_sekli": "KISMI", "haftalik_calisma_saati": "20"})
        k = IseGirisKaydi.objects.get(personel=self.p)
        self.assertEqual((k.calisma_sekli, str(k.haftalik_calisma_saati)), ("KISMI", "20.0"))
        b = {e: d for _x, a in mu.bolumler(self.p, firma_bilgisi_getir(), k) for e, d, _z in a}
        self.assertEqual(b["Çalışma şekli"], "Kısmi süreli (haftalık 20 saat)")
        kart = self.client.get(self.sekme)
        self.assertNotContains(kart, "Haftalık çalışma saati (kısmi süreli)")                  # saat girildi: eksik uyarısı yok

    def test_kismi_saatsiz_muhasebe_uyarisi(self):
        self.client.post(self.url, {"calisma_sekli": "KISMI"})
        kart = self.client.get(self.sekme)
        self.assertContains(kart, "Haftalık çalışma saati (kısmi süreli)")
        self.assertContains(kart, "Eksik bilgi var")

    def test_gecersiz_deger_reddedilir(self):
        self.client.post(self.url, {"calisma_sekli": "TAM", "deneme_suresi_ay": "2"})
        r = self.client.post(self.url, {"calisma_sekli": "YARIM"}, follow=True)
        self.assertContains(r, "Kayıt yapılamadı")
        self.assertEqual(IseGirisKaydi.objects.get(personel=self.p).calisma_sekli, "TAM")
        with self.assertRaises(ig.IseGirisHatasi):
            ig.kayit_kaydet(self.p, calisma_sekli="YARIM")
