"""İNSAN KAYNAKLARI > Personel Kartları — servis (büyük harf, TC doğrulama/benzersizlik,
telefon, aktif/ayrıldı, arama/filtre, soft delete) + view/yetki + menü. Bordro/maaş YOK."""
from datetime import date

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Personel
from core.moduller import MODULLER
from core.services.personel import (
    PersonelHatasi, aktif_mi, calisan_personeller, departmanlar, gorevler, personel_guncelle,
    personel_listele, personel_olustur, personel_sil,
)

GECERLI_TC = "10000000146"
BUGUN = date(2026, 9, 19)


def _kur(**kw):
    veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2020, 1, 1)}
    veri.update(kw)
    return personel_olustur(**veri)


class PersonelServisTest(TestCase):
    def test_ad_soyad_tr_buyuk_harf_ve_bosluk_sadelesir(self):
        p = _kur(ad="  ışık   can ", soyad="çiçek")
        self.assertEqual((p.ad, p.soyad), ("IŞIK CAN", "ÇİÇEK"))
        self.assertEqual(p.ad_soyad, "IŞIK CAN ÇİÇEK")

    def test_adres_departman_gorev_acil_kisi_buyuk_harf(self):
        p = _kur(adres="şişli mah. istiklal cad. no:1", departman="üretim", gorev="kaynakçı",
                 acil_durum_kisi="ayşe yılmaz")
        self.assertEqual(p.adres, "ŞİŞLİ MAH. İSTİKLAL CAD. NO:1")
        self.assertEqual((p.departman, p.gorev, p.acil_durum_kisi),
                         ("ÜRETİM", "KAYNAKÇI", "AYŞE YILMAZ"))

    def test_ad_ve_soyad_zorunlu(self):
        with self.assertRaises(PersonelHatasi):
            _kur(ad="   ")
        with self.assertRaises(PersonelHatasi):
            _kur(soyad="")

    def test_ise_giris_tarihi_zorunlu(self):
        with self.assertRaises(PersonelHatasi):
            _kur(ise_giris_tarihi=None)

    def test_tc_gecersiz_reddedilir_gecerli_kabul_edilir(self):
        with self.assertRaises(PersonelHatasi):
            _kur(tc_kimlik_no="12345678901")
        with self.assertRaises(PersonelHatasi):
            _kur(tc_kimlik_no="123")
        self.assertEqual(_kur(tc_kimlik_no=GECERLI_TC).tc_kimlik_no, GECERLI_TC)

    def test_tc_benzersiz_mesajda_mevcut_kart_adi(self):
        _kur(ad="ahmet", soyad="yılmaz", tc_kimlik_no=GECERLI_TC)
        with self.assertRaises(PersonelHatasi) as h:
            _kur(ad="başka", soyad="kişi", tc_kimlik_no=GECERLI_TC)
        self.assertIn("AHMET YILMAZ", str(h.exception))
        self.assertNotIn("ayrılmış", str(h.exception))

    def test_tc_ayrilmis_kart_mesaji_yonlendirir(self):
        _kur(ad="ahmet", soyad="yılmaz", tc_kimlik_no=GECERLI_TC,
             isten_cikis_tarihi=date(2021, 1, 1))
        with self.assertRaises(PersonelHatasi) as h:
            _kur(ad="ahmet", soyad="yılmaz", tc_kimlik_no=GECERLI_TC)
        self.assertIn("ayrılmış personel", str(h.exception))
        self.assertIn("çıkış tarihini silin", str(h.exception))

    def test_tc_soft_delete_sonrasi_yeniden_kullanilir(self):
        ilk = _kur(tc_kimlik_no=GECERLI_TC)
        personel_sil(ilk)
        self.assertEqual(_kur(ad="yeni", tc_kimlik_no=GECERLI_TC).tc_kimlik_no, GECERLI_TC)

    def test_iki_bos_tc_serbest(self):
        _kur(ad="bir")
        _kur(ad="iki")
        self.assertEqual(Personel.objects.filter(tc_kimlik_no="").count(), 2)

    def test_tc_guncellemede_kendisiyle_cakismaz(self):
        p = _kur(tc_kimlik_no=GECERLI_TC)
        personel_guncelle(p, ad="ali", soyad="veli", tc_kimlik_no=GECERLI_TC,
                          ise_giris_tarihi=date(2020, 1, 1), notlar="güncel")
        p.refresh_from_db()
        self.assertEqual(p.notlar, "güncel")

    def test_db_seviyesi_ayni_tc_iki_aktif_kart_yasak(self):
        _kur(tc_kimlik_no=GECERLI_TC)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Personel.objects.create(ad="X", soyad="Y", tc_kimlik_no=GECERLI_TC,
                                    ise_giris_tarihi=date(2020, 1, 1))

    def test_db_seviyesi_cikis_giristen_once_olamaz(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Personel.objects.create(ad="X", soyad="Y", ise_giris_tarihi=date(2020, 5, 1),
                                    isten_cikis_tarihi=date(2020, 4, 1))

    def test_servis_cikis_giristen_once_reddedilir(self):
        with self.assertRaises(PersonelHatasi):
            _kur(ise_giris_tarihi=date(2020, 5, 1), isten_cikis_tarihi=date(2020, 4, 30))
        _kur(ise_giris_tarihi=date(2020, 5, 1), isten_cikis_tarihi=date(2020, 5, 1))  # aynı gün serbest

    def test_telefon_kanonik_gecersiz_oldugu_gibi(self):
        p = _kur(telefon="0532 702 40 05", acil_durum_telefon="(0555) 111 22 33")
        self.assertEqual(p.telefon, "+905327024005")
        self.assertEqual(p.acil_durum_telefon, "+905551112233")
        q = _kur(ad="iki", telefon="dahili 12")
        self.assertEqual(q.telefon, "dahili 12")

    def test_eposta_kucuk_harf(self):
        self.assertEqual(_kur(eposta="  Ali.Veli@Firma.COM ").eposta, "ali.veli@firma.com")

    def test_dogum_tarihi_dogrulama(self):
        with self.assertRaises(PersonelHatasi):
            _kur(dogum_tarihi=date(2999, 1, 1))
        with self.assertRaises(PersonelHatasi):
            _kur(dogum_tarihi=date(1899, 12, 31))
        self.assertEqual(_kur(dogum_tarihi=date(1985, 3, 5)).dogum_tarihi, date(1985, 3, 5))

    def test_kan_grubu_dogrulama(self):
        with self.assertRaises(PersonelHatasi):
            _kur(kan_grubu="C+")
        self.assertEqual(_kur(kan_grubu="AB+").kan_grubu, "AB+")

    def test_aktif_mi_sinirlari(self):
        self.assertTrue(aktif_mi(_kur(), BUGUN))                                   # çıkış yok
        self.assertTrue(aktif_mi(_kur(ad="b", isten_cikis_tarihi=BUGUN), BUGUN))   # çıkış günü dahil
        self.assertTrue(aktif_mi(_kur(ad="c", isten_cikis_tarihi=date(2026, 9, 25)), BUGUN))
        self.assertFalse(aktif_mi(_kur(ad="d", isten_cikis_tarihi=date(2026, 9, 18)), BUGUN))

    def test_liste_durum_filtreleri(self):
        a = _kur(ad="ahmet", soyad="yılmaz", departman="üretim")
        b = _kur(ad="ayşe", soyad="kaya", departman="ofis")
        c = _kur(ad="mehmet", soyad="demir", isten_cikis_tarihi=date(2025, 1, 1))
        self.assertEqual(set(personel_listele(bugun=BUGUN)), {a, b})
        self.assertEqual(set(personel_listele(durum="ayrildi", bugun=BUGUN)), {c})
        self.assertEqual(set(personel_listele(durum="hepsi", bugun=BUGUN)), {a, b, c})
        self.assertEqual(set(calisan_personeller(BUGUN)), {a, b})
        self.assertEqual(set(personel_listele(departman="üretim", bugun=BUGUN)), {a})

    def test_liste_arama_ad_soyad_ayri_alanlarda_ve_tr_harf(self):
        a = _kur(ad="ahmet", soyad="yılmaz", gorev="kaynakçı", telefon="0532 702 40 05")
        b = _kur(ad="ışık", soyad="çiçek", departman="depo")
        self.assertEqual(set(personel_listele(ara="ahmet yılmaz", bugun=BUGUN)), {a})
        self.assertEqual(set(personel_listele(ara="ışık", bugun=BUGUN)), {b})
        self.assertEqual(set(personel_listele(ara="kaynakçı", bugun=BUGUN)), {a})
        self.assertEqual(set(personel_listele(ara="depo", bugun=BUGUN)), {b})
        self.assertEqual(set(personel_listele(ara="0532 702", bugun=BUGUN)), {a})
        self.assertEqual(set(personel_listele(ara="ahmet çiçek", bugun=BUGUN)), set())   # AND

    def test_departmanlar_ve_gorevler_distinct_bos_haric_silinmis_haric(self):
        _kur(ad="a", departman="üretim", gorev="kaynakçı")
        _kur(ad="b", departman="üretim", gorev="")
        _kur(ad="c", departman="depo", gorev="forklift")
        silinen = _kur(ad="d", departman="hayalet", gorev="hayalet")
        personel_sil(silinen)
        self.assertEqual(departmanlar(), ["DEPO", "ÜRETİM"])
        self.assertEqual(gorevler(), ["FORKLİFT", "KAYNAKÇI"])

    def test_sil_soft_idempotent_ve_silinmis_guncellenemez(self):
        p = _kur()
        personel_sil(p)
        p.refresh_from_db()
        self.assertTrue(p.silindi)
        self.assertIsNotNone(p.silindi_at)
        personel_sil(p)                                   # ikinci çağrı sessiz
        with self.assertRaises(PersonelHatasi):
            personel_guncelle(p, ad="x", soyad="y", ise_giris_tarihi=date(2020, 1, 1))

    def test_kullanici_audit_alanlari(self):
        u = User.objects.create_user("audit", password="x")
        p = _kur(kullanici=u)
        self.assertEqual((p.created_by, p.updated_by), (u, u))


class PersonelViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("pyon", password="x")
        cls.yetkili = User.objects.create_user("pyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="personel")
        cls.yemekci = User.objects.create_user("pyemekci", password="x")
        EkranYetki.objects.create(kullanici=cls.yemekci, ekran_kod="yemek_takibi")
        cls.bos = User.objects.create_user("pbos", password="x")

    def _govde(self, **ek):
        veri = {"ad": "ahmet", "soyad": "yılmaz", "ise_giris_tarihi": "2024-03-01"}
        veri.update(ek)
        return veri

    def test_yetkisiz_403_anonim_302(self):
        p = _kur()
        urller = [reverse("core:personeller"), reverse("core:personel_ekle"),
                  reverse("core:personel_detay", args=[p.pk]),
                  reverse("core:personel_duzenle", args=[p.pk])]
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 302, u)
            self.assertIn("/login/", self.client.get(u).url)
        for kullanici in (self.bos, self.yemekci):
            self.client.force_login(kullanici)
            for u in urller + [reverse("core:personel_sil", args=[p.pk])]:
                self.assertEqual(self.client.get(u).status_code, 403, (kullanici.username, u))
            self.assertEqual(
                self.client.post(reverse("core:personel_sil", args=[p.pk])).status_code, 403)
        p.refresh_from_db()
        self.assertFalse(p.silindi)

    def test_liste_tc_gostermez_detay_gosterir(self):
        p = _kur(ad="ahmet", soyad="yılmaz", tc_kimlik_no=GECERLI_TC, telefon="0532 702 40 05")
        self.client.force_login(self.yetkili)
        liste = self.client.get(reverse("core:personeller"))
        self.assertEqual(liste.status_code, 200)
        self.assertContains(liste, "AHMET YILMAZ")
        self.assertContains(liste, "+905327024005")
        self.assertNotContains(liste, GECERLI_TC)
        detay = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertContains(detay, GECERLI_TC)
        self.assertIn("no-store", detay["Cache-Control"])

    def test_ekle_get_ve_post(self):
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(reverse("core:personel_ekle")).status_code, 200)
        r = self.client.post(reverse("core:personel_ekle"), self._govde(
            tc_kimlik_no=GECERLI_TC, departman="üretim", gorev="kaynakçı", kan_grubu="A+",
            telefon="05327024005", eposta="Ahmet@Firma.com"))
        p = Personel.objects.get(tc_kimlik_no=GECERLI_TC)
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        self.assertEqual((p.ad, p.soyad, p.departman), ("AHMET", "YILMAZ", "ÜRETİM"))
        self.assertEqual((p.telefon, p.eposta), ("+905327024005", "ahmet@firma.com"))
        self.assertEqual(p.created_by, self.yetkili)

    def test_ekle_post_hatalari_formda_gosterilir(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:personel_ekle"), self._govde(tc_kimlik_no="12345678901"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Geçersiz TC Kimlik No")
        self.assertFalse(Personel.objects.exists())
        _kur(tc_kimlik_no=GECERLI_TC)
        r2 = self.client.post(reverse("core:personel_ekle"), self._govde(tc_kimlik_no=GECERLI_TC))
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "zaten kayıtlı")
        self.assertEqual(Personel.objects.count(), 1)

    def test_ekle_post_giris_tarihi_eksik_hata(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:personel_ekle"), {"ad": "a", "soyad": "b"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Personel.objects.exists())

    def test_duzenle_get_dolu_gelir_post_gunceller(self):
        p = _kur(ad="ahmet", soyad="yılmaz", gorev="usta")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:personel_duzenle", args=[p.pk]))
        self.assertContains(r, "AHMET")
        self.assertContains(r, "USTA")
        r2 = self.client.post(reverse("core:personel_duzenle", args=[p.pk]), self._govde(
            ad="ahmet", soyad="yılmaz", gorev="kalite şefi", ise_giris_tarihi="2020-01-01"))
        self.assertRedirects(r2, reverse("core:personel_detay", args=[p.pk]))
        p.refresh_from_db()
        self.assertEqual(p.gorev, "KALİTE ŞEFİ")
        self.assertEqual(p.updated_by, self.yetkili)

    def test_sil_post_soft_delete_get_silmez(self):
        p = _kur()
        self.client.force_login(self.yetkili)
        r0 = self.client.get(reverse("core:personel_sil", args=[p.pk]))
        self.assertRedirects(r0, reverse("core:personel_detay", args=[p.pk]))
        p.refresh_from_db()
        self.assertFalse(p.silindi)
        r = self.client.post(reverse("core:personel_sil", args=[p.pk]))
        self.assertRedirects(r, reverse("core:personeller"))
        p.refresh_from_db()
        self.assertTrue(p.silindi)
        self.assertEqual(self.client.get(reverse("core:personel_detay", args=[p.pk])).status_code, 404)

    def test_liste_ayrilanlar_filtresi(self):
        _kur(ad="ercan")
        _kur(ad="murat", isten_cikis_tarihi=date(2021, 6, 1))
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:personeller"))
        self.assertContains(r, "ERCAN")
        self.assertNotContains(r, "MURAT")
        r2 = self.client.get(reverse("core:personeller"), {"durum": "ayrildi"})
        self.assertContains(r2, "MURAT")
        self.assertNotContains(r2, "ERCAN")
        self.assertContains(r2, "Ayrıldı")
        r3 = self.client.get(reverse("core:personeller"), {"durum": "hepsi"})
        self.assertContains(r3, "ERCAN")
        self.assertContains(r3, "MURAT")

    def test_liste_arama_ve_departman_filtresi_ve_gecersiz_durum(self):
        _kur(ad="ahmet", soyad="yılmaz", departman="üretim")
        _kur(ad="ayşe", soyad="kaya", departman="ofis")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:personeller"), {"ara": "ayşe"})
        self.assertContains(r, "AYŞE KAYA")
        self.assertNotContains(r, "AHMET YILMAZ")
        r2 = self.client.get(reverse("core:personeller"), {"departman": "ÜRETİM"})
        self.assertContains(r2, "AHMET YILMAZ")
        self.assertNotContains(r2, "AYŞE KAYA")
        r3 = self.client.get(reverse("core:personeller"), {"durum": "saçma"})
        self.assertEqual(r3.status_code, 200)               # geçersiz durum aktife düşer
        self.assertContains(r3, "AHMET YILMAZ")

    def test_liste_bos_durum_mesaji(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:personeller"))
        self.assertContains(r, "Henüz personel kartı yok")
        r2 = self.client.get(reverse("core:personeller"), {"ara": "yokboyle"})
        self.assertContains(r2, "eşleşen personel yok")

    def test_menu_ik_modulu_ve_yemek_takibi_yeri(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:personeller"))
        self.assertContains(r, '<span class="ad">İnsan Kaynakları</span>')
        self.assertContains(r, "Personel Kartları")
        self.assertContains(r, "Yemek Takibi")
        self.assertNotContains(r, '<span class="ad">Diğer</span>')
        # yalnız yemek yetkisi olan kullanıcı İK başlığı altında yalnız Yemek Takibi'ni görür
        self.client.force_login(self.yemekci)
        r2 = self.client.get(reverse("core:yemek_takibi"))
        self.assertContains(r2, '<span class="ad">İnsan Kaynakları</span>')
        self.assertNotContains(r2, "Personel Kartları")


class ModullerTutarlilikTest(TestCase):
    def test_ekran_kodlari_benzersiz_ve_url_adlari_cozulur(self):
        kodlar = [e.kod for m in MODULLER for e in m.ekranlar]
        self.assertEqual(len(kodlar), len(set(kodlar)), "ekran kodları benzersiz olmalı")
        for m in MODULLER:
            for e in m.ekranlar:
                reverse(e.url_adi)                                   # NoReverseMatch fırlatmamalı

    def test_ik_modulu_ve_diger_modulu_kalkti(self):
        koddan = {m.kod: m for m in MODULLER}
        self.assertNotIn("DIGER", koddan)
        ik = koddan["IK"]
        self.assertEqual(ik.ad, "İnsan Kaynakları")
        self.assertIn("personel", [e.kod for e in ik.ekranlar])
        self.assertIn("yemek_takibi", [e.kod for e in ik.ekranlar])
        self.assertFalse(ik.yonetici_modulu)
