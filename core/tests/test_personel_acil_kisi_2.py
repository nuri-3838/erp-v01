"""Personel kartı: ikinci acil durum kişisi (ad + telefon). Birinci kişi gibi büyük harf/telefon normalizasyonu; boş bırakılabilir;
kart (Genel sekmesi) ve düzenleme formunda görünür; mevcut kayıtlar boş açılır, birinci kişi değişmez."""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Personel
from core.services.personel import personel_guncelle, personel_olustur


def _kur(**kw):
    veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2020, 1, 1)}
    veri.update(kw)
    return personel_olustur(**veri)


class AcilKisi2ServisTest(TestCase):
    def test_varsayilan_bos_ve_birinci_kisi_ayri(self):
        p = _kur(acil_durum_kisi="ayşe yılmaz", acil_durum_telefon="0532 111 22 33")
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("", ""))
        self.assertEqual((p.acil_durum_kisi, p.acil_durum_telefon), ("AYŞE YILMAZ", "+905321112233"))

    def test_ikinci_kisi_buyuk_harf_ve_telefon_kanonik(self):
        p = _kur(acil_durum_kisi="ayşe yılmaz", acil_durum_telefon="0532 111 22 33",
                 acil_durum_kisi_2="  mehmet   çelik ", acil_durum_telefon_2="(0555) 444 33 22")
        p.refresh_from_db()
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("MEHMET ÇELİK", "+905554443322"))
        self.assertEqual((p.acil_durum_kisi, p.acil_durum_telefon), ("AYŞE YILMAZ", "+905321112233"))

    def test_guncelle_ekler_degistirir_ve_siler_birinci_kisi_degismez(self):
        p = _kur(acil_durum_kisi="ayşe", acil_durum_telefon="05321112233")
        tam = dict(ad="ali", soyad="veli", ise_giris_tarihi=date(2020, 1, 1), acil_durum_kisi="ayşe", acil_durum_telefon="05321112233")
        personel_guncelle(p, **tam, acil_durum_kisi_2="fatma", acil_durum_telefon_2="05554443322")
        p.refresh_from_db()
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("FATMA", "+905554443322"))
        personel_guncelle(p, **tam, acil_durum_kisi_2="hasan", acil_durum_telefon_2="")
        p.refresh_from_db()
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("HASAN", ""))
        personel_guncelle(p, **tam)                                                           # alan verilmezse boşalır (formdan gelen boş değer)
        p.refresh_from_db()
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("", ""))
        self.assertEqual((p.acil_durum_kisi, p.acil_durum_telefon), ("AYŞE", "+905321112233"))


class AcilKisi2EkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user("akyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.u, ekran_kod="personel")

    def setUp(self):
        self.client.force_login(self.u)

    def govde(self, **ek):
        veri = {"ad": "ahmet", "soyad": "yılmaz", "ise_giris_tarihi": "2024-03-01", "acil_durum_kisi": "ayşe", "acil_durum_telefon": "05321112233"}
        veri.update(ek)
        return veri

    def test_ekleme_ve_duzenleme_formlarinda_alanlar_var(self):
        r = self.client.get(reverse("core:personel_ekle"))
        for ad in ("acil_durum_kisi_2", "acil_durum_telefon_2"):
            self.assertContains(r, f'name="{ad}"')
        self.assertContains(r, "2. Acil Durumda Aranacak Kişi")
        self.assertContains(r, "2. Acil Durum Telefonu")
        p = _kur(acil_durum_kisi_2="mehmet", acil_durum_telefon_2="05554443322")
        r = self.client.get(reverse("core:personel_duzenle", args=[p.pk]))
        self.assertContains(r, 'value="MEHMET"')
        self.assertContains(r, 'value="+905554443322"')

    def test_ekle_ve_duzenle_post_ikinci_kisiyi_kaydeder(self):
        r = self.client.post(reverse("core:personel_ekle"), self.govde(acil_durum_kisi_2="mehmet çelik", acil_durum_telefon_2="0555 444 33 22"))
        p = Personel.objects.get(ad="AHMET")
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("MEHMET ÇELİK", "+905554443322"))
        r = self.client.post(reverse("core:personel_duzenle", args=[p.pk]), self.govde(acil_durum_kisi_2="fatma", acil_durum_telefon_2=""))
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("FATMA", ""))
        self.assertEqual((p.acil_durum_kisi, p.acil_durum_telefon), ("AYŞE", "+905321112233"))

    def test_ikinci_kisi_zorunlu_degil(self):
        r = self.client.post(reverse("core:personel_ekle"), self.govde())
        p = Personel.objects.get(ad="AHMET")
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        self.assertEqual((p.acil_durum_kisi_2, p.acil_durum_telefon_2), ("", ""))

    def test_kartta_genel_sekmesinde_iki_kisi_gorunur(self):
        p = _kur(acil_durum_kisi="ayşe", acil_durum_telefon="05321112233", acil_durum_kisi_2="mehmet çelik", acil_durum_telefon_2="05554443322")
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        for metin in ("1. Aranacak Kişi", "2. Aranacak Kişi", "AYŞE", "MEHMET ÇELİK", "+905321112233", "+905554443322"):
            self.assertContains(r, metin)
        bos = _kur(ad="boş", soyad="kişi")
        r = self.client.get(reverse("core:personel_detay", args=[bos.pk]))
        self.assertContains(r, "2. Aranacak Kişi")                                            # alan her kartta var, boşsa "—"
        self.assertContains(r, "2. Telefon")
