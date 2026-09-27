"""İNSAN KAYNAKLARI > Personel Ücretleri — YALNIZ KAYIT (bordro/brüt/SGK/vergi hesabı YOK).
Model kısıtları (tip↔tutar, (personel,başlangıç) benzersizliği), `gecerli_ucret` tarih
seçimi/zam senaryosu, CRUD doğrulama, personel silme koruması ve view/yetki (ayrı
`personel_ucret` ekranı — `personel` yetkisi tek başına ücreti göstermez)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Personel, PersonelUcret
from core.services.personel import PersonelHatasi, personel_olustur, personel_sil
from core.services.personel_ucret import (
    PersonelUcretHatasi, gecerli_ucret, gecerli_ucretler, ucret_ekle, ucret_guncelle, ucret_sil,
)


def _kur(**kw):
    veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2020, 3, 15)}
    veri.update(kw)
    return personel_olustur(**veri)


class UcretDBKisitTest(TestCase):
    def test_asgari_ise_tutar_dolu_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelUcret.objects.create(
                personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI",
                net_tutar=Decimal("100"))

    def test_net_ise_tutar_bos_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelUcret.objects.create(
                personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=None)

    def test_net_ise_tutar_sifir_veya_negatif_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelUcret.objects.create(
                personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=Decimal("0"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelUcret.objects.create(
                personel=p, gecerlilik_baslangic=date(2026, 1, 2), tip="NET", net_tutar=Decimal("-5"))

    def test_ayni_personel_ayni_baslangic_iki_aktif_kayit_yasak(self):
        p = _kur()
        PersonelUcret.objects.create(personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelUcret.objects.create(
                personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")

    def test_silinmis_kayit_benzersizligi_engellemez(self):
        p = _kur()
        u = PersonelUcret.objects.create(personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        u.silindi = True
        u.save(update_fields=["silindi"])
        PersonelUcret.objects.create(personel=p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")


class GecerliUcretTest(TestCase):
    def test_tarihten_once_kayit_yoksa_none(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 6, 1), tip="ASGARI")
        self.assertIsNone(gecerli_ucret(p, date(2026, 5, 31)))

    def test_en_son_baslayan_kayit_secilir(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 6, 1), tip="NET", net_tutar=Decimal("30000"))
        self.assertEqual(gecerli_ucret(p, date(2026, 3, 1)).tip, "ASGARI")
        u2 = gecerli_ucret(p, date(2026, 6, 1))
        self.assertEqual((u2.tip, u2.net_tutar), ("NET", Decimal("30000.00")))
        self.assertEqual(gecerli_ucret(p, date(2027, 1, 1)).pk, u2.pk)

    def test_zam_senaryosu_gecmis_kayit_degismez(self):
        p = _kur()
        eski = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET",
                          net_tutar=Decimal("20000"))
        yeni = ucret_ekle(p, gecerlilik_baslangic=date(2026, 7, 1), tip="NET",
                          net_tutar=Decimal("25000"))
        self.assertEqual(gecerli_ucret(p, date(2026, 6, 30)).pk, eski.pk)
        self.assertEqual(gecerli_ucret(p, date(2026, 7, 1)).pk, yeni.pk)
        eski.refresh_from_db()
        self.assertEqual(eski.net_tutar, Decimal("20000.00"))

    def test_hic_kayit_yoksa_none(self):
        self.assertIsNone(gecerli_ucret(_kur()))

    def test_gecerli_ucretler_toplu_tek_sorgu(self):
        a, b = _kur(ad="ahmet"), _kur(ad="berk")
        ucret_ekle(a, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        with self.assertNumQueries(1):
            harita = gecerli_ucretler([a, b], tarih=date(2026, 6, 1))
        self.assertEqual(harita[a.pk].tip, "ASGARI")
        self.assertIsNone(harita[b.pk])

    def test_gecerli_ucretler_bos_liste(self):
        self.assertEqual(gecerli_ucretler([]), {})


class UcretEkleGuncelleSilTest(TestCase):
    def test_baslangic_ise_giristen_once_olamaz(self):
        p = _kur(ise_giris_tarihi=date(2026, 1, 1))
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2025, 12, 31), tip="ASGARI")

    def test_net_tutarsiz_hata(self):
        p = _kur()
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET")

    def test_net_tutar_sifir_hata(self):
        p = _kur()
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=Decimal("0"))

    def test_asgari_tutar_verilse_bile_yok_sayilir(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI",
                       net_tutar=Decimal("999"))
        self.assertIsNone(u.net_tutar)

    def test_gecersiz_tip_hata(self):
        p = _kur()
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="SAÇMA")

    def test_ayni_baslangic_tekrar_eklenemez_servis_mesaji(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")

    def test_guncelle_kendi_tarihini_cakisma_saymaz(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        ucret_guncelle(u, gecerlilik_baslangic=date(2026, 1, 1), tip="NET",
                       net_tutar=Decimal("22000"))
        u.refresh_from_db()
        self.assertEqual((u.tip, u.net_tutar), ("NET", Decimal("22000.00")))

    def test_silinmis_kisiye_eklenemez(self):
        p = _kur()
        p.silindi = True
        p.save(update_fields=["silindi"])
        with self.assertRaises(PersonelUcretHatasi):
            ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")

    def test_sil_soft_delete_idempotent(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        ucret_sil(u)
        u.refresh_from_db()
        self.assertTrue(u.silindi)
        ucret_sil(u)                                        # ikinci çağrı hata vermemeli

    def test_silinmis_kayit_duzenlenemez(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        ucret_sil(u)
        with self.assertRaises(PersonelUcretHatasi):
            ucret_guncelle(u, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")


class PersonelSilKorumasiTest(TestCase):
    def test_ucret_kaydi_varsa_personel_silinemez(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        with self.assertRaises(PersonelHatasi):
            personel_sil(p)


class UcretViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("ucyon", password="x")
        cls.ucretci = User.objects.create_user("ucretci", password="x")
        EkranYetki.objects.create(kullanici=cls.ucretci, ekran_kod="personel_ucret")
        cls.personelci = User.objects.create_user("ucpersonelci", password="x")
        EkranYetki.objects.create(kullanici=cls.personelci, ekran_kod="personel")
        cls.ikisi = User.objects.create_user("ucikisi", password="x")
        EkranYetki.objects.create(kullanici=cls.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=cls.ikisi, ekran_kod="personel_ucret")
        cls.bos = User.objects.create_user("ucbos", password="x")

    def _govde(self, p, **ek):
        veri = {"personel": p.pk, "gecerlilik_baslangic": "2026-06-01", "tip": "ASGARI",
                "net_tutar": "", "aciklama": ""}
        veri.update(ek)
        return veri

    def test_url_403_ve_anonim_302(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        urller = [reverse("core:ucret_ekle"), reverse("core:ucret_duzenle", args=[u.pk])]
        for url in urller:
            self.assertEqual(self.client.get(url).status_code, 302, url)
        for kullanici in (self.bos, self.personelci):
            self.client.force_login(kullanici)
            for url in urller:
                self.assertEqual(self.client.get(url).status_code, 403, (kullanici.username, url))
            self.assertEqual(
                self.client.post(reverse("core:ucret_sil", args=[u.pk])).status_code, 403)
        u.refresh_from_db()
        self.assertFalse(u.silindi)

    def test_personel_yetkisi_tek_basina_ucreti_gostermez(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=Decimal("40000"))
        self.client.force_login(self.personelci)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "40.000,00")
        self.assertNotContains(r, "Ücret Ekle")
        r2 = self.client.get(reverse("core:personeller"))
        self.assertNotContains(r2, "40.000,00")

    def test_yetkili_detayda_ve_listede_ucreti_gorur(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="NET", net_tutar=Decimal("40000"))
        self.client.force_login(self.ikisi)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertContains(r, "40.000,00 TL net")
        r2 = self.client.get(reverse("core:personeller"))
        self.assertContains(r2, "40.000,00 TL")

    def test_yonetici_de_gorur(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertContains(r, "Asgari Ücret")

    def test_ekle_post_ve_duzenle(self):
        # "personel" ekranı da gerekli — dönüş personel kartına yapılır (bkz. ucret_ekle/_duzenle).
        p = _kur()
        self.client.force_login(self.ikisi)
        r = self.client.post(reverse("core:ucret_ekle"), self._govde(p))
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        u = PersonelUcret.objects.get(personel=p)
        self.assertEqual((u.tip, u.created_by), ("ASGARI", self.ikisi))
        r2 = self.client.post(reverse("core:ucret_duzenle", args=[u.pk]), {
            "gecerlilik_baslangic": "2026-06-01", "tip": "NET", "net_tutar": "35.000,00",
            "aciklama": "zam"})
        self.assertRedirects(r2, reverse("core:personel_detay", args=[p.pk]))
        u.refresh_from_db()
        self.assertEqual((u.tip, u.net_tutar, u.updated_by), ("NET", Decimal("35000.00"), self.ikisi))

    def test_net_secilip_tutar_bos_form_hatasi(self):
        p = _kur()
        self.client.force_login(self.ucretci)
        r = self.client.post(reverse("core:ucret_ekle"), self._govde(p, tip="NET", net_tutar=""))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "zorunlu")
        self.assertEqual(PersonelUcret.objects.count(), 0)

    def test_cakisan_baslangic_formda_gosterilir(self):
        p = _kur()
        ucret_ekle(p, gecerlilik_baslangic=date(2026, 6, 1), tip="ASGARI")
        self.client.force_login(self.ucretci)
        r = self.client.post(reverse("core:ucret_ekle"), self._govde(p))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "zaten var")
        self.assertEqual(PersonelUcret.objects.count(), 1)

    def test_sil_post_soft_delete_get_silmez(self):
        p = _kur()
        u = ucret_ekle(p, gecerlilik_baslangic=date(2026, 1, 1), tip="ASGARI")
        self.client.force_login(self.ikisi)
        self.client.get(reverse("core:ucret_sil", args=[u.pk]))
        u.refresh_from_db()
        self.assertFalse(u.silindi)
        r = self.client.post(reverse("core:ucret_sil", args=[u.pk]))
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        u.refresh_from_db()
        self.assertTrue(u.silindi)
