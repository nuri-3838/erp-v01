"""İNSAN KAYNAKLARI > Mesai Hesabı — self-servis hesap yaşam döngüsü (oluştur/şifre sıfırla/
kapat/aç), işten çıkınca otomatik kapanma, personel silme koruması, "mesai kullanıcısı"
tespiti (menüde yalnız "Mesaim", pano yönlendirmesi, diğer ekranlara 403)."""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki
from core.services.personel import PersonelHatasi, personel_guncelle, personel_sil
from core.services.mesai_hesap import MesaiHesapHatasi, hesap_ac, hesap_kapat, hesap_olustur, sifre_sifirla
from core.tests.ik_yardimci import personel_kur
from core.yetki import mesai_kullanicisi_mi


class HesapOlusturTest(TestCase):
    def test_olustur_baglar_ve_ilk_isim_soyisim_aktarilir(self):
        p = personel_kur(ad="ahmet", soyad="veli")
        u = hesap_olustur(p, kullanici_adi="ahmetv", sifre="cokGizli!2026")
        p.refresh_from_db()
        self.assertEqual(p.kullanici_id, u.pk)
        self.assertEqual((u.username, u.is_active), ("ahmetv", True))
        self.assertTrue(u.check_password("cokGizli!2026"))

    def test_ayni_kullanici_adi_iki_kez_olamaz(self):
        p1, p2 = personel_kur(ad="ahmet"), personel_kur(ad="berk")
        hesap_olustur(p1, kullanici_adi="ortak", sifre="cokGizli!2026")
        with self.assertRaises(MesaiHesapHatasi):
            hesap_olustur(p2, kullanici_adi="ortak", sifre="cokGizli!2026")

    def test_ikinci_hesap_acilamaz(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="birinci", sifre="cokGizli!2026")
        with self.assertRaises(MesaiHesapHatasi):
            hesap_olustur(p, kullanici_adi="ikinci", sifre="cokGizli!2026")

    def test_zayif_sifre_reddedilir(self):
        p = personel_kur()
        with self.assertRaises(Exception):
            hesap_olustur(p, kullanici_adi="test", sifre="123")

    def test_silinmis_personele_acilamaz(self):
        p = personel_kur()
        p.silindi = True
        p.save(update_fields=["silindi"])
        with self.assertRaises(MesaiHesapHatasi):
            hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")


class SifreVeDurumTest(TestCase):
    def test_sifre_sifirla(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        sifre_sifirla(p, sifre="yeniSifre!2027")
        p.kullanici.refresh_from_db()
        self.assertTrue(p.kullanici.check_password("yeniSifre!2027"))

    def test_hesabi_olmayan_sifre_sifirlanamaz(self):
        p = personel_kur()
        with self.assertRaises(MesaiHesapHatasi):
            sifre_sifirla(p, sifre="cokGizli!2026")

    def test_kapat_ve_ac(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        hesap_kapat(p)
        p.kullanici.refresh_from_db()
        self.assertFalse(p.kullanici.is_active)
        hesap_ac(p)
        p.kullanici.refresh_from_db()
        self.assertTrue(p.kullanici.is_active)

    def test_isten_ayrilan_hesap_yeniden_acilamaz(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1), isten_cikis_tarihi=date(2020, 6, 1))
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        hesap_kapat(p)
        with self.assertRaises(MesaiHesapHatasi):
            hesap_ac(p)


class OtomatikKapatmaTest(TestCase):
    def test_isten_cikis_tarihi_girilince_hesap_otomatik_kapanir(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        personel_guncelle(
            p, ad=p.ad, soyad=p.soyad, ise_giris_tarihi=p.ise_giris_tarihi,
            isten_cikis_tarihi=date(2026, 1, 15))
        p.kullanici.refresh_from_db()
        self.assertFalse(p.kullanici.is_active)

    def test_cikis_yoksa_hesaba_dokunulmaz(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        personel_guncelle(p, ad="yeni", soyad=p.soyad, ise_giris_tarihi=p.ise_giris_tarihi)
        p.kullanici.refresh_from_db()
        self.assertTrue(p.kullanici.is_active)


class PersonelSilKorumasiTest(TestCase):
    def test_aktif_mesai_hesabi_varken_silinemez(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        with self.assertRaises(PersonelHatasi):
            personel_sil(p)

    def test_hesap_kapaliysa_silinebilir(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        hesap_kapat(p)
        personel_sil(p)
        p.refresh_from_db()
        self.assertTrue(p.silindi)


class MesaiKullanicisiTespitiTest(TestCase):
    def test_baglanti_yoksa_false(self):
        u = User.objects.create_user("normal", password="x")
        self.assertFalse(mesai_kullanicisi_mi(u))

    def test_baglantili_ve_yonetici_degilse_true(self):
        p = personel_kur()
        u = hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        self.assertTrue(mesai_kullanicisi_mi(u))

    def test_yonetici_hep_false(self):
        p = personel_kur()
        u = hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        u.is_superuser = True
        u.save()
        self.assertFalse(mesai_kullanicisi_mi(u))

    def test_anonim_false(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(mesai_kullanicisi_mi(AnonymousUser()))


class MesaiKullanicisiViewTest(TestCase):
    def setUp(self):
        self.p = personel_kur(ad="mehmet", soyad="calisan", ise_giris_tarihi=date(2020, 1, 1))
        self.u = hesap_olustur(self.p, kullanici_adi="mehmetc", sifre="cokGizli!2026")

    def test_pano_mesaime_yonlendirir(self):
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:pano"))
        self.assertRedirects(r, reverse("core:mesaim"))

    def test_baska_ekrana_403(self):
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:personeller"))
        self.assertEqual(r.status_code, 403)

    def test_menude_yalniz_mesaim_gorunur(self):
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:mesaim"))
        self.assertContains(r, "Mesaim")
        self.assertNotContains(r, "Modüller")

    def test_ekranyetki_verilirse_o_da_menude_gorunur(self):
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="personel")
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:mesaim"))
        self.assertContains(r, "Mesaim")
        self.assertContains(r, "Personel Kartları")
        r2 = self.client.get(reverse("core:personeller"))
        self.assertEqual(r2.status_code, 200)


class MesaiHesapViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("mhyon", password="x")
        cls.bos = User.objects.create_user("mhbos", password="x")

    def test_yalniz_yonetici_hesap_olusturabilir(self):
        p = personel_kur()
        url = reverse("core:mesai_hesap_olustur", args=[p.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.yon)
        r = self.client.post(url, {"kullanici_adi": "yenitest", "sifre": "cokGizli!2026"})
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        p.refresh_from_db()
        self.assertTrue(p.kullanici_id)

    def test_sifre_sifirlama_formu(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        self.client.force_login(self.yon)
        r = self.client.post(
            reverse("core:mesai_sifre_sifirla", args=[p.pk]), {"sifre": "yeniSifre!2027"})
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        p.kullanici.refresh_from_db()
        self.assertTrue(p.kullanici.check_password("yeniSifre!2027"))

    def test_durum_toggle(self):
        p = personel_kur()
        hesap_olustur(p, kullanici_adi="test", sifre="cokGizli!2026")
        self.client.force_login(self.yon)
        url = reverse("core:mesai_hesap_durum", args=[p.pk])
        self.client.post(url)
        p.kullanici.refresh_from_db()
        self.assertFalse(p.kullanici.is_active)
        self.client.post(url)
        p.kullanici.refresh_from_db()
        self.assertTrue(p.kullanici.is_active)

    def test_personel_detayinda_hesap_karti_yalniz_yonetici(self):
        p = personel_kur()
        yetkili = User.objects.create_user("mhyetkili", password="x")
        EkranYetki.objects.create(kullanici=yetkili, ekran_kod="personel")
        self.client.force_login(yetkili)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertNotContains(r, "Mesai Hesabı Oluştur")
        self.client.force_login(self.yon)
        r2 = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertContains(r2, "Mesai Hesabı Oluştur")
