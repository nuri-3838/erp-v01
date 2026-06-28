"""ÇEK/SENET (yeniden inşa, bordro mantığı) — Slice 1: muhasebe hesap eşlemesi
(CekHesapAyari, tekil) + ana sayfa iskeleti. Bordro motoru sonraki dilimlerde."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import CekHesapAyari, HesapPlani


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class CekHesapAyariServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("cayon", password="x")
        _hesap("101.01", "ALINAN ÇEKLER")
        _hesap("121.01", "ALACAK SENETLERİ")
        _hesap("103.01", "VERİLEN ÇEKLER")
        _hesap("321.01", "BORÇ SENETLERİ")

    def test_kaydet_ve_oku(self):
        from core.services.cek import hesap_ayari, hesap_ayari_kaydet
        hesap_ayari_kaydet({"portfoy_cek": "101.01", "portfoy_senet": "121.01",
                            "verilen_cek": "103.01", "verilen_senet": "321.01"},
                           kullanici=self.yon)
        a = hesap_ayari()
        self.assertEqual(a.portfoy_cek.hesap_kodu, "101.01")
        self.assertEqual(a.portfoy_senet.hesap_kodu, "121.01")
        self.assertEqual(a.verilen_cek.hesap_kodu, "103.01")
        self.assertEqual(a.verilen_senet.hesap_kodu, "321.01")
        self.assertIsNone(a.tahsilde_cek)                 # boş bırakılan
        self.assertEqual(CekHesapAyari.objects.count(), 1)   # tekil kayıt

    def test_bos_alani_none_yapar(self):
        from core.services.cek import hesap_ayari, hesap_ayari_kaydet
        hesap_ayari_kaydet({"portfoy_cek": "101.01"}, kullanici=self.yon)
        self.assertEqual(hesap_ayari().portfoy_cek.hesap_kodu, "101.01")
        hesap_ayari_kaydet({"portfoy_cek": ""}, kullanici=self.yon)   # geri boşalt
        self.assertIsNone(hesap_ayari().portfoy_cek)

    def test_yaprak_olmayan_reddedilir(self):
        from core.services.cek import CekHatasi, hesap_ayari_kaydet
        _hesap("400", "ÜST HESAP")
        _hesap("400.01", "ALT HESAP")          # 400 artık üst (yaprak değil)
        with self.assertRaises(CekHatasi):
            hesap_ayari_kaydet({"portfoy_cek": "400"}, kullanici=self.yon)


class CekSayfaViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("csyon", password="x")
        cls.bos = User.objects.create_user("csbos", password="x")
        _hesap("101.01", "ALINAN ÇEKLER")

    def test_ana_sayfa_200(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cek_senetler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Muhasebe Hesap Kodları")
        self.assertContains(r, "Cari Giriş")
        self.assertContains(r, "Banka Teminat")
        self.assertContains(r, "Bordrolar")

    def test_ana_sayfa_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:cek_senetler")).status_code, 403)

    def test_hesap_ayari_get_ve_post(self):
        from core.services.cek import hesap_ayari
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:cek_hesap_ayari")).status_code, 200)
        r = self.client.post(reverse("core:cek_hesap_ayari"), {"portfoy_cek": "101.01"})
        self.assertRedirects(r, reverse("core:cek_senetler"))
        self.assertEqual(hesap_ayari().portfoy_cek.hesap_kodu, "101.01")

    def test_menude_cek_senet_var(self):
        from core.moduller import MODULLER
        finans = next(m for m in MODULLER if m.kod == "FINANS")
        self.assertIn("cek_senet", [e.kod for e in finans.ekranlar])
