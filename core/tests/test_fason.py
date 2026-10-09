"""FASON > Kesim Listesi Hesapla (ekran yetkisi, hesap, PDF) — liste Operasyon Tanımları'ndan (İstasyon 10) hesaplanır; Kesim Tanımları/Kesim Kayıtları kaldırıldı."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.models import Birim, EkranYetki, IsIstasyonu, Kategori, Stok
from core.moduller import MODULLER
from core.services.uretim import operasyon_olustur


def _stok(kategori, birim, *, satinalma=False, satis=False, **kw):
    return Stok.objects.create(
        kod=kw.pop("kod"), ad=kw.pop("ad"), kategori=kategori,
        uretim_birimi=birim, fatura_birimi=birim,
        satinalma_urunu=satinalma, uretim_urunu=not satinalma, satis_urunu=satis, **kw)


class FasonViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fasyon", password="x")
        cls.bos = User.objects.create_user("fasbos", password="x")
        cls.yetkili = User.objects.create_user("fasyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="fason_hesapla")
        birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        kat = Kategori.objects.create(kod="FK", ad="FASON TEST")
        cls.ham_on = _stok(kat, birim, satinalma=True, kod="150-TEST-ON", ad="7378-ön ayak 20x40 (2+1)-5480mm")
        cls.parca_on = _stok(kat, birim, kod="151-TEST-ON", ad="kesilmiş a tipi ön ayak 2+1")
        cls.a21 = _stok(kat, birim, satis=True, kod="A21", ad="a tipi 2+1")
        lazer = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        montaj = IsIstasyonu.objects.create(kod="70", ad="MONTAJ")
        operasyon_olustur(istasyon_id=lazer.pk, cikti_id=cls.parca_on.pk, cikti_miktar=Decimal("2"), satirlar=[(cls.ham_on, Decimal("1"))])
        operasyon_olustur(istasyon_id=montaj.pk, cikti_id=cls.a21.pk, cikti_miktar=Decimal("1"), satirlar=[(cls.parca_on, Decimal("1"))])

    def _govde(self, satirlar, eylem="hesapla"):
        veri = {"eylem": eylem, "satir-TOTAL_FORMS": str(len(satirlar)), "satir-INITIAL_FORMS": "0",
                "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            veri[f"satir-{i}-urun"] = s.get("urun", "")
            veri[f"satir-{i}-miktar"] = s.get("miktar", "")
        return veri

    def test_anonim_login_yonlenir(self):
        r = self.client.get(reverse("core:fason_hesapla"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:fason_hesapla")).status_code, 403)

    def test_yetkili_kullanici_girebilir_ve_eski_linkler_yok(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:fason_hesapla"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Kesim Kayıtları")
        self.assertNotContains(r, "Kesim Tanımları")

    def test_post_sonucu_gosterir_ve_kayit_olusturmaz(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:fason_hesapla"), self._govde([{"urun": self.a21.pk, "miktar": "10"}]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.parca_on.ad)
        self.assertNotContains(r, "151-TEST-ON")
        self.assertContains(r, self.ham_on.ad)

    def test_pdf_indirir_kayit_olusturmaz(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:fason_hesapla"), self._govde([{"urun": self.a21.pk, "miktar": "10"}], "pdf"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertGreater(len(r.content), 500)
        self.assertEqual(r["Content-Disposition"], 'inline; filename="fason-kesim-listesi.pdf"')

    def test_bos_satir_hata(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:fason_hesapla"), self._govde([{}]))
        self.assertContains(r, "En az bir ürün satırı girin")

    def test_kaldirilan_ekranlar_yok(self):
        for ad in ("fason_kesim_tanimlari", "fason_kesim_ekle", "fason_kayitlari", "fason_kaydi_detay"):
            with self.assertRaises(NoReverseMatch):
                reverse(f"core:{ad}", args=[1] if ad == "fason_kaydi_detay" else None)
        self.client.force_login(self.yon)
        for yol in ("/fason/kesim-tanimlari/", "/fason/kayitlar/", "/fason/kayitlar/1/"):
            self.assertEqual(self.client.get(yol).status_code, 404)

    def test_fason_menusu(self):
        fason = next(m for m in MODULLER if m.kod == "FASON")
        self.assertEqual([e.ad for e in fason.ekranlar],
                         ["Kesim Listesi Hesapla", "Fason Fiyatları", "Fason Dönüşler", "Fason Mutabakat"])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertContains(r, "Fason Dönüşler")
        self.assertNotContains(r, "Kesim Kayıtları")
        self.assertNotContains(r, "Kesim Tanımları")
