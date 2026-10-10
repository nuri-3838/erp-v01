"""İNSAN KAYNAKLARI > Mesai Kayıtları — personelin kendi telefonundan başlat/bitir (fabrika
ağı zorunlu, sunucu saati, yoklamaya otomatik yansıma), yönetici düzeltme/ekleme/silme,
uyarı listeleri (çıkışı eksik / yoklama uyuşmazlığı / izinli günde giriş), TR saat dilimi
doğruluğu (gece yarısı sınırı) ve yetki (personel kendi mesaisi dışında hiçbir ekrana giremez;
"mesai_kayitlari" ayrı ekran yetkisi)."""
from datetime import date, datetime
from datetime import timezone as dt_timezone
from unittest import mock

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.forms import MesaiDuzeltForm
from core.ip import istemci_ip
from core.models import EkranYetki, MesaiKaydi, PersonelDevam
from core.services.mesai import (
    MesaiHatasi, acik_kayit, baslat, bitir, cikisi_eksik_kayitlar, duzelt,
    izinli_gunde_giris_uyarilari, kayitlari_listele, manuel_ekle, sil,
    yoklama_uyusmazliklari,
)
from core.services.mesai_ag import ag_ekle
from core.services.mesai_hesap import hesap_olustur
from core.services.personel_izin import izin_ekle
from core.tests.ik_yardimci import personel_kur

TR_IZINLI = "10.0.0.5"
TR_IZINSIZ = "8.8.8.8"


def _ag_ac(cidr="10.0.0.0/24"):
    return ag_ekle(cidr=cidr)


class IstemciIpTest(TestCase):
    def test_x_real_ip_oncelikli(self):
        class Sahte:
            META = {"HTTP_X_REAL_IP": "1.2.3.4", "REMOTE_ADDR": "127.0.0.1"}
        self.assertEqual(istemci_ip(Sahte()), "1.2.3.4")

    def test_x_real_ip_yoksa_remote_addr(self):
        class Sahte:
            META = {"REMOTE_ADDR": "127.0.0.1"}
        self.assertEqual(istemci_ip(Sahte()), "127.0.0.1")

    def test_hicbiri_yoksa_bos(self):
        class Sahte:
            META = {}
        self.assertEqual(istemci_ip(Sahte()), "")


class TRDateTimeFieldTest(TestCase):
    def test_naive_girdi_tr_yerel_olarak_yorumlanir(self):
        p = personel_kur()
        form = MesaiDuzeltForm(data={
            "personel": p.pk, "giris_zamani": "2026-09-14T08:00",
            "cikis_zamani": "", "duzeltme_notu": "test"})
        self.assertTrue(form.is_valid(), form.errors)
        giris = form.cleaned_data["giris_zamani"]
        self.assertEqual(giris.astimezone(dt_timezone.utc), datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))


class BaslatBitirTest(TestCase):
    def test_izinli_ip_ile_baslat_basarili(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = baslat(p, ip=TR_IZINLI)
        self.assertEqual((k.personel_id, k.kaynak, k.giris_ip), (p.pk, "PERSONEL", TR_IZINLI))
        self.assertIsNone(k.cikis_zamani)

    def test_izinsiz_ip_reddedilir(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        with self.assertRaises(MesaiHatasi):
            baslat(p, ip=TR_IZINSIZ)

    def test_liste_bossa_herkes_reddedilir(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        with self.assertRaises(MesaiHatasi):
            baslat(p, ip=TR_IZINLI)

    def test_calismayan_personel_baslatamaz(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1), isten_cikis_tarihi=date(2024, 1, 1))
        with self.assertRaises(MesaiHatasi):
            baslat(p, ip=TR_IZINLI)

    def test_cift_baslatma_reddedilir(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        baslat(p, ip=TR_IZINLI)
        with self.assertRaises(MesaiHatasi):
            baslat(p, ip=TR_IZINLI)

    def test_bitir_acik_kaydi_kapatir(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        baslat(p, ip=TR_IZINLI)
        k = bitir(p, ip=TR_IZINLI)
        self.assertIsNotNone(k.cikis_zamani)
        self.assertIsNone(acik_kayit(p))

    def test_acik_kayit_yokken_bitirilemez(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        with self.assertRaises(MesaiHatasi):
            bitir(p, ip=TR_IZINLI)

    def test_yoklama_otomatik_geldi_olusturur(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = baslat(p, ip=TR_IZINLI)
        kayit = PersonelDevam.objects.get(personel=p, tarih=k.is_tarihi, silindi=False)
        self.assertEqual((kayit.durum, kayit.notlar), ("GELDI", "Mesai girişi"))

    def test_mevcut_yoklamaya_dokunulmaz(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        bugun = date(2026, 9, 14)
        with mock.patch("django.utils.timezone.now",
                        return_value=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc)):
            PersonelDevam.objects.create(personel=p, tarih=bugun, durum="GELMEDI")
            baslat(p, ip=TR_IZINLI)
        kayit = PersonelDevam.objects.get(personel=p, tarih=bugun, silindi=False)
        self.assertEqual(kayit.durum, "GELMEDI")   # değişmedi

    def test_izinli_gunde_baslatma_engellenmez(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        bugun = date(2026, 9, 14)
        izin_ekle(p, tur="YILLIK", baslangic=bugun, bitis=bugun)
        with mock.patch("django.utils.timezone.now",
                        return_value=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc)):
            k = baslat(p, ip=TR_IZINLI)
        self.assertEqual(k.is_tarihi, bugun)

    def test_gece_yarisini_gecen_is_tarihi_giris_anina_gore(self):
        _ag_ac()
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        # UTC 22:30 (9/14) -> TR yerelde 9/15 01:30 (gün değişti)
        with mock.patch("django.utils.timezone.now",
                        return_value=datetime(2026, 9, 14, 22, 30, tzinfo=dt_timezone.utc)):
            k = baslat(p, ip=TR_IZINLI)
        self.assertEqual(k.is_tarihi, date(2026, 9, 15))

    def test_zaman_her_zaman_sunucu_saati(self):
        """baslat/bitir imzasında istemciden zaman parametresi YOKTUR — yalnız ip alınır."""
        import inspect
        self.assertNotIn("zaman", inspect.signature(baslat).parameters)
        self.assertNotIn("zaman", inspect.signature(bitir).parameters)


class MesaiDBKisitTest(TestCase):
    def test_ayni_gun_iki_acik_kayit_yasak(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        MesaiKaydi.objects.create(
            personel=p, is_tarihi=date(2026, 9, 14),
            giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))
        with self.assertRaises(IntegrityError), transaction.atomic():
            MesaiKaydi.objects.create(
                personel=p, is_tarihi=date(2026, 9, 14),
                giris_zamani=datetime(2026, 9, 14, 6, 0, tzinfo=dt_timezone.utc))

    def test_kapali_iki_kayit_ayni_gun_serbest(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        MesaiKaydi.objects.create(
            personel=p, is_tarihi=date(2026, 9, 14),
            giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
            cikis_zamani=datetime(2026, 9, 14, 8, 0, tzinfo=dt_timezone.utc))
        MesaiKaydi.objects.create(
            personel=p, is_tarihi=date(2026, 9, 14),
            giris_zamani=datetime(2026, 9, 14, 9, 0, tzinfo=dt_timezone.utc),
            cikis_zamani=datetime(2026, 9, 14, 12, 0, tzinfo=dt_timezone.utc))
        self.assertEqual(MesaiKaydi.objects.filter(personel=p).count(), 2)

    def test_cikis_giristen_once_yasak(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            MesaiKaydi.objects.create(
                personel=p, is_tarihi=date(2026, 9, 14),
                giris_zamani=datetime(2026, 9, 14, 8, 0, tzinfo=dt_timezone.utc),
                cikis_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc))


class YoneticiCrudTest(TestCase):
    def test_manuel_ekle_not_zorunlu(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        with self.assertRaises(MesaiHatasi):
            manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                       duzeltme_notu="")

    def test_manuel_ekle_kaynak_yonetici(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = manuel_ekle(
            p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
            cikis_zamani=datetime(2026, 9, 14, 14, 0, tzinfo=dt_timezone.utc),
            duzeltme_notu="unutulan giriş")
        self.assertEqual(k.kaynak, "YONETICI")

    def test_duzelt_zamanlari_gunceller(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = manuel_ekle(
            p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
            duzeltme_notu="ilk")
        duzelt(k, giris_zamani=datetime(2026, 9, 14, 6, 0, tzinfo=dt_timezone.utc),
              cikis_zamani=datetime(2026, 9, 14, 14, 0, tzinfo=dt_timezone.utc),
              duzeltme_notu="düzeltildi")
        k.refresh_from_db()
        self.assertEqual(k.duzeltme_notu, "düzeltildi")
        self.assertIsNotNone(k.cikis_zamani)

    def test_sil_soft_delete_idempotent(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = manuel_ekle(
            p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
            duzeltme_notu="ilk")
        sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        sil(k)


class UyariListeleriTest(TestCase):
    def test_cikisi_eksik_gecmis_gun(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 1, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="unutulan")
        eksik = list(cikisi_eksik_kayitlar(bugun=date(2026, 9, 14)))
        self.assertEqual(len(eksik), 1)

    def test_bugunun_acik_kaydi_eksik_sayilmaz(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="bugün")
        eksik = list(cikisi_eksik_kayitlar(bugun=date(2026, 9, 14)))
        self.assertEqual(len(eksik), 0)

    def test_yoklama_uyusmazligi_gelmedi_ile(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        gun = date(2026, 9, 14)
        PersonelDevam.objects.create(personel=p, tarih=gun, durum="GELMEDI")
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="mesai var")
        uyusmazlik = yoklama_uyusmazliklari(baslangic=gun, bitis=gun)
        self.assertEqual(len(uyusmazlik), 1)

    def test_yoklama_geldi_ile_uyusmazlik_yok(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        gun = date(2026, 9, 14)
        PersonelDevam.objects.create(personel=p, tarih=gun, durum="GELDI")
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="mesai var")
        self.assertEqual(yoklama_uyusmazliklari(baslangic=gun, bitis=gun), [])

    def test_izinli_gunde_giris_uyarisi(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        gun = date(2026, 9, 14)
        izin_ekle(p, tur="YILLIK", baslangic=gun, bitis=gun)
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="izinliyken girmiş")
        uyari = izinli_gunde_giris_uyarilari(baslangic=gun, bitis=gun)
        self.assertEqual(len(uyari), 1)

    def test_izinsiz_gunde_uyari_yok(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        gun = date(2026, 9, 14)
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc),
                   duzeltme_notu="normal gün")
        self.assertEqual(izinli_gunde_giris_uyarilari(baslangic=gun, bitis=gun), [])


class KayitlariListeleTest(TestCase):
    def test_tarih_ve_personel_filtreleri(self):
        a, b = personel_kur(ad="ahmet", ise_giris_tarihi=date(2020, 1, 1)), \
               personel_kur(ad="berk", ise_giris_tarihi=date(2020, 1, 1))
        manuel_ekle(a, giris_zamani=datetime(2026, 9, 1, 5, 0, tzinfo=dt_timezone.utc), duzeltme_notu="x")
        manuel_ekle(b, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc), duzeltme_notu="x")
        self.assertEqual(kayitlari_listele(personel_id=a.pk).count(), 1)
        self.assertEqual(kayitlari_listele(baslangic=date(2026, 9, 10)).count(), 1)


class MesaimViewTest(TestCase):
    def setUp(self):
        _ag_ac()
        self.p = personel_kur(ad="mehmet", soyad="calisan", ise_giris_tarihi=date(2020, 1, 1))
        self.u = hesap_olustur(self.p, kullanici_adi="mehmetc", sifre="cokGizli!2026")

    def test_anonim_302(self):
        self.assertEqual(self.client.get(reverse("core:mesaim")).status_code, 302)

    def test_kendi_personeli_olmayan_403(self):
        yetkili = User.objects.create_user("baskakullanici", password="x")
        self.client.force_login(yetkili)
        self.assertEqual(self.client.get(reverse("core:mesaim")).status_code, 403)

    def test_baslat_izinli_ip_ile(self):
        self.client.force_login(self.u)
        r = self.client.post(
            reverse("core:mesaim"), {"eylem": "baslat"}, HTTP_X_REAL_IP=TR_IZINLI)
        self.assertRedirects(r, reverse("core:mesaim"))
        self.assertIsNotNone(acik_kayit(self.p))

    def test_baslat_izinsiz_ip_reddedilir(self):
        self.client.force_login(self.u)
        self.client.post(reverse("core:mesaim"), {"eylem": "baslat"}, HTTP_X_REAL_IP=TR_IZINSIZ)
        self.assertIsNone(acik_kayit(self.p))

    def test_sahte_x_forwarded_for_ile_atlatilamaz(self):
        """X-Forwarded-For istemci tarafından tamamen kontrol edilir (nginx yalnız ekler,
        REMOTE_ADDR'ı değiştirmez) — bu yüzden mesai kontrolü ASLA X-Forwarded-For'a bakmaz."""
        self.client.force_login(self.u)
        self.client.post(
            reverse("core:mesaim"), {"eylem": "baslat"},
            HTTP_X_FORWARDED_FOR=TR_IZINLI, REMOTE_ADDR=TR_IZINSIZ)
        self.assertIsNone(acik_kayit(self.p))   # yalnız REMOTE_ADDR/X-Real-IP sayılır

    def test_baslat_bitir_dongusu(self):
        self.client.force_login(self.u)
        self.client.post(reverse("core:mesaim"), {"eylem": "baslat"}, HTTP_X_REAL_IP=TR_IZINLI)
        self.assertIsNotNone(acik_kayit(self.p))
        self.client.post(reverse("core:mesaim"), {"eylem": "bitir"}, HTTP_X_REAL_IP=TR_IZINLI)
        self.assertIsNone(acik_kayit(self.p))

    def test_calismayan_personel_butonu_gizli_ve_post_reddedilir(self):
        self.p.isten_cikis_tarihi = date(2020, 6, 1)
        self.p.save(update_fields=["isten_cikis_tarihi"])
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:mesaim"))
        self.assertNotContains(r, "MESAİYİ BAŞLAT")
        self.client.post(reverse("core:mesaim"), {"eylem": "baslat"}, HTTP_X_REAL_IP=TR_IZINLI)
        self.assertIsNone(acik_kayit(self.p))


class MesaiKayitlariViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("mkyon", password="x")
        cls.yetkili = User.objects.create_user("mkyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="mesai_kayitlari")
        cls.bos = User.objects.create_user("mkbos", password="x")

    def test_yetki_302_403(self):
        url = reverse("core:mesai_kayitlari")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_yetkili_liste_gorur(self):
        p = personel_kur(ad="ahmet", ise_giris_tarihi=date(2020, 1, 1))
        manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc), duzeltme_notu="x")
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:mesai_kayitlari"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AHMET")

    def test_ekle_form_post(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:mesai_kaydi_ekle"), {
            "personel": p.pk, "giris_zamani": "2026-09-14T08:00", "cikis_zamani": "",
            "duzeltme_notu": "elle girildi"})
        self.assertRedirects(r, reverse("core:mesai_kayitlari"))
        self.assertEqual(MesaiKaydi.objects.filter(personel=p).count(), 1)

    def test_duzelt_not_bos_hata(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc), duzeltme_notu="x")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:mesai_kaydi_duzenle", args=[k.pk]), {
            "giris_zamani": "2026-09-14T08:00", "cikis_zamani": "", "duzeltme_notu": ""})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "zorunlu")

    def test_sil_post(self):
        p = personel_kur(ise_giris_tarihi=date(2020, 1, 1))
        k = manuel_ekle(p, giris_zamani=datetime(2026, 9, 14, 5, 0, tzinfo=dt_timezone.utc), duzeltme_notu="x")
        self.client.force_login(self.yon)
        self.client.post(reverse("core:mesai_kaydi_sil", args=[k.pk]))
        k.refresh_from_db()
        self.assertTrue(k.silindi)


class MesaiAyarlariViewTest(TestCase):
    def test_yalniz_yonetici(self):
        yon = User.objects.create_superuser("mayon", password="x")
        bos = User.objects.create_user("mabos", password="x")
        url = reverse("core:mesai_ayarlari")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(bos)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(yon)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_ag_ekle_ve_sil(self):
        yon = User.objects.create_superuser("mayon2", password="x")
        self.client.force_login(yon)
        self.client.post(reverse("core:mesai_ag_ekle"), {"cidr": "10.0.0.0/24", "aciklama": "test"})
        from core.models import MesaiIzinliAg
        ag = MesaiIzinliAg.objects.get(cidr="10.0.0.0/24")
        self.client.post(reverse("core:mesai_ag_sil", args=[ag.pk]))
        ag.refresh_from_db()
        self.assertTrue(ag.silindi)
