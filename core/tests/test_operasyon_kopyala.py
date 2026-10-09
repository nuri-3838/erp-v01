"""Operasyon formu tasarımı: listede Kopyala; kopya GET'i istasyon/miktar/girdiler/yan çıktılarla DOLU, ÇIKTI BOŞ açar (başlık 'Operasyon Kopyala —
kaynak: <çıktı kodu>'); kopyadan kayıt normal oluşturma servisinden geçer; sayfa başı hata özeti; form yapısı (özet kartı, koşullu bölümler)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Operasyon
from core.services.uretim import operasyon_girdileri, operasyon_yan_ciktilari
from core.tests.test_uretim_yan_cikti import YanCiktiBase

D = Decimal


class KopyalaTest(YanCiktiBase):
    def setUp(self):
        self.su = User.objects.create_superuser("kop_su", password="x")
        self.client.force_login(self.su)
        self.url = reverse("core:operasyon_ekle")

    def test_listede_kopyala_dugmesi(self):
        r = self.client.get(reverse("core:operasyon_tanimlari"))
        self.assertContains(r, f"?kopya={self.op.pk}")
        self.assertContains(r, ">Kopyala<")

    def test_kopya_get_dolu_ve_cikti_bos(self):
        r = self.client.get(self.url, {"kopya": self.op.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"Operasyon Kopyala — kaynak: {self.ana.kod}")
        b = r.context["bform"]
        self.assertEqual(b.initial["istasyon"], self.kesim.pk)
        self.assertEqual(b.initial["cikti_miktar"], self.op.cikti_miktar)
        self.assertIsNone(b.initial.get("cikti"))                                              # ÇIKTI BOŞ
        self.assertEqual([(f.initial["girdi"], f.initial["miktar"]) for f in r.context["formset"]], [(self.profil.pk, D("1.000"))])
        yan = r.context["yan_formset"]
        self.assertEqual([(f.initial["stok"], f.initial["miktar"], f.initial["boy_mm"]) for f in yan], [(self.yan.pk, D("1.000"), D("1063.53"))])
        h = r.content.decode()
        self.assertNotIn('id="boy-alan" hidden', h)                                           # yan çıktı var → ana boy alanı görünür
        self.assertIn('id="yan-bolum">', h)                                                    # yan çıktı bölümü açık

    def test_kopya_ana_boy_bos_gelir_kullanici_girer(self):
        r = self.client.get(self.url, {"kopya": self.op.pk})
        self.assertNotContains(r, "1.292,60")                                                  # kaynağın ana boyu kopyalanmaz (çıktı farklı parça)

    def test_kopyadan_kayit_normal_olusturma_servisinden_gecer(self):
        yeni_cikti = self._stok("KOPYACIKTI", self.kat_ana)
        veri = {"istasyon": self.kesim.pk, "cikti": yeni_cikti.pk, "cikti_miktar": "3", "boy_mm": "1.100,00",
                "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
                "yan-TOTAL_FORMS": "1", "yan-INITIAL_FORMS": "0", "yan-0-stok": self.yan.pk, "yan-0-miktar": "1", "yan-0-boy_mm": "1.063,53"}
        r = self.client.post(self.url + f"?kopya={self.op.pk}", veri)
        self.assertEqual(r.status_code, 302)
        yeni = Operasyon.objects.get(cikti=yeni_cikti, silindi=False)
        self.assertEqual((yeni.istasyon_id, yeni.cikti_miktar, yeni.boy_mm, yeni.tam_calistirma), (self.kesim.pk, D("3.000"), D("1100.00"), True))
        self.assertEqual([(g.girdi_id, g.miktar) for g in operasyon_girdileri(yeni)], [(self.profil.pk, D("1.000"))])
        self.assertEqual([(y.stok_id, y.boy_mm) for y in operasyon_yan_ciktilari(yeni)], [(self.yan.pk, D("1063.53"))])
        self.assertTrue(Operasyon.objects.get(pk=self.op.pk).silindi is False)                 # kaynak aynen duruyor

    def test_kopya_ayni_ciktiyla_kaydedilemez_hata_ozeti(self):
        veri = {"istasyon": self.kesim.pk, "cikti": self.ana.pk, "cikti_miktar": "3", "boy_mm": "1.292,60",
                "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
                "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0"}
        r = self.client.post(self.url + f"?kopya={self.op.pk}", veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Kaydedilemedi")                                                # sayfa başı özet
        self.assertContains(r, "zaten aktif bir operasyon tanımlı")
        self.assertContains(r, f"Operasyon Kopyala — kaynak: {self.ana.kod}")                  # başlık hata sonrası da korunur

    def test_olmayan_kopya_kaynagi_yeni_operasyon_acar(self):
        r = self.client.get(self.url, {"kopya": 99999999})
        self.assertContains(r, "Yeni Operasyon")


class FormYapisiTest(YanCiktiBase):
    def setUp(self):
        self.su = User.objects.create_superuser("yap_su", password="x")
        self.client.force_login(self.su)

    def test_yeni_form_ozet_karti_ve_kapali_yan_cikti(self):
        h = self.client.get(reverse("core:operasyon_ekle")).content.decode()
        for parca in ('id="ozet-formul"', 'id="ozet-tam"', 'id="yan-ac"', "+ Girdi ekle", "+ Yan çıktı ekle", "Birim", "1 çalıştırmada çıktı miktarı"):
            self.assertIn(parca, h)
        self.assertIn('id="yan-bolum" hidden', h)                                              # yan çıktı bölümü kapalı başlar
        self.assertIn('id="boy-alan" hidden', h)

    def test_duzenle_yan_cikti_varsa_bolum_acik(self):
        h = self.client.get(reverse("core:operasyon_duzenle", args=[self.op.pk])).content.decode()
        self.assertIn('id="yan-bolum">', h)
        self.assertIn('id="yan-ac" class="op-yan-ac" hidden', h)
        self.assertIn("Tam boy: Evet", h)

    def test_duzenle_hata_ozeti_sayfa_basinda(self):
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
                "yan-TOTAL_FORMS": "1", "yan-INITIAL_FORMS": "0", "yan-0-stok": self.yan.pk, "yan-0-miktar": "1", "yan-0-boy_mm": "100",
                "istasyon": self.kesim.pk, "cikti_miktar": "3", "boy_mm": ""}
        r = self.client.post(reverse("core:operasyon_duzenle", args=[self.op.pk]), veri)
        self.assertContains(r, "Kaydedilemedi")
        self.assertContains(r, "ana çıktının boyu")

    def test_stok_bilgi_json_birim_ve_boy(self):
        r = self.client.get(reverse("core:operasyon_ekle"))
        bilgi = r.context["stok_bilgi"]
        self.assertEqual(bilgi[self.profil.pk], {"k": "PROFIL", "b": "BOY", "boy": True})
        self.assertEqual(bilgi[self.ana.pk]["boy"], False)
        self.assertContains(r, 'id="stok-bilgi"')
