"""Cari banka hesabı + yetkili kişi (CARİLER Faz 3b) testleri: servis (varsayılan
mantığı), view (cari-scoped CRUD + detay sekmeleri), taşıma, yetki."""
import json
import tempfile

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, CariBanka, CariYetkili, EkranYetki
from core.services.cari import (CariHatasi, aktif_bankalar, banka_ekle, banka_guncelle,
                                banka_sil, cari_olustur, yetkili_ekle, yetkili_sil)


def _cari(unvan="test cari"):
    return cari_olustur(unvan=unvan, para_birimi="TRY")


class CariBankaServisTest(TestCase):
    def test_ilk_banka_varsayilan(self):
        c = _cari()
        b = banka_ekle(c, banka_adi="halkbank", iban="tr01")
        self.assertTrue(b.varsayilan)
        self.assertEqual(b.banka_adi, "HALKBANK")

    def test_ikinci_default_eskiyi_kapatir(self):
        c = _cari()
        b1 = banka_ekle(c, banka_adi="halkbank")
        b2 = banka_ekle(c, banka_adi="ziraat", varsayilan=True)
        b1.refresh_from_db()
        self.assertFalse(b1.varsayilan)
        self.assertTrue(b2.varsayilan)

    def test_varsayilan_silinince_promote(self):
        c = _cari()
        b1 = banka_ekle(c, banka_adi="halkbank")          # varsayılan
        b2 = banka_ekle(c, banka_adi="ziraat")
        banka_sil(b1)
        b2.refresh_from_db()
        self.assertTrue(b2.varsayilan)                     # kalan varsayılan oldu

    def test_yetkili_ekle_upper(self):
        c = _cari()
        y = yetkili_ekle(c, ad_soyad="ali veli", unvan="müdür")
        self.assertEqual((y.ad_soyad, y.unvan), ("ALİ VELİ", "MÜDÜR"))

    def test_yetkili_sil(self):
        c = _cari()
        y = yetkili_ekle(c, ad_soyad="ali")
        yetkili_sil(y)
        y.refresh_from_db()
        self.assertTrue(y.silindi)


class CariBankaYetkiliTasimaTest(TestCase):
    def test_tasima(self):
        cari_olustur(unvan="formal", para_birimi="TRY", kod="320-10-0001")
        veri = {
            "bankalar": [{"cari": "320-10-0001", "banka_adi": "HALKBANK",
                          "hesap_sahibi": "", "iban": "TR01", "swift": "",
                          "para_birimi": "TRY", "aciklama": "", "varsayilan": True}],
            "yetkililer": [{"cari": "320-10-0001", "ad_soyad": "ALİ VELİ",
                            "unvan": "", "telefon": "", "eposta": "", "notlar": ""}],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False)
            yol = f.name
        call_command("tasi_cari_banka_yetkili", yol)
        call_command("tasi_cari_banka_yetkili", yol)        # idempotent
        self.assertEqual(CariBanka.objects.filter(silindi=False).count(), 1)
        self.assertEqual(CariYetkili.objects.filter(silindi=False).count(), 1)


class CariBankaYetkiliViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="cariler")
        cls.bos = User.objects.create_user("bos", password="x")
        cls.cari = _cari("formal")

    def test_detay_sekmeleri(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:cari_detay", args=[self.cari.pk]))
        self.assertContains(r, "Banka Hesapları")
        self.assertContains(r, "Yetkili Kişiler")

    def test_banka_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:banka_ekle", args=[self.cari.pk]),
                             {"banka_adi": "halkbank", "para_birimi": "TRY"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CariBanka.objects.filter(cari=self.cari, banka_adi="HALKBANK").exists())

    def test_yetkili_ekle_post(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:yetkili_ekle", args=[self.cari.pk]),
                             {"ad_soyad": "ali veli"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(CariYetkili.objects.filter(cari=self.cari, ad_soyad="ALİ VELİ").exists())

    def test_banka_sil_post(self):
        b = banka_ekle(self.cari, banka_adi="halkbank")
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:banka_sil", args=[b.pk]))
        self.assertEqual(r.status_code, 302)
        b.refresh_from_db()
        self.assertTrue(b.silindi)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(
            self.client.get(reverse("core:banka_ekle", args=[self.cari.pk])).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("core:yetkili_ekle", args=[self.cari.pk])).status_code, 403)
