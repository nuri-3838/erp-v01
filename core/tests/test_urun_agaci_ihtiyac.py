"""Ürün Ağacı 4. görünüm "İhtiyaç (çoklu ürün)" (üretim sadeleştirme adım 5): eski İhtiyaç Hesapla ekranı buraya taşındı (menüden kalktı, eski
adres kalıcı yönlenir); ağaç düğümlerinde ✎ → operasyon tanımı düzenleme (yalnız operasyon_tanimlari yetkisi olana)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki
from core.moduller import MODULLER
from core.tests.test_urun_agaci import ZincirTaban

D = Decimal


class IhtiyacGorunumTest(ZincirTaban):
    def setUp(self):
        self.client.force_login(self.yon)
        self.url = reverse("core:urun_agaci") + "?gorunum=ihtiyac"

    def govde(self, *kalemler):
        v = {"satir-TOTAL_FORMS": str(len(kalemler)), "satir-INITIAL_FORMS": "0"}
        for i, (stok, miktar) in enumerate(kalemler):
            v[f"satir-{i}-hedef"], v[f"satir-{i}-miktar"] = stok.pk, miktar
        return v

    def test_segment_ve_form(self):
        r = self.client.get(reverse("core:urun_agaci"))
        self.assertContains(r, "İhtiyaç (çoklu ürün)")
        self.assertContains(r, 'href="?gorunum=ihtiyac"')
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="ua-seg aktif">İhtiyaç (çoklu ürün)')
        self.assertContains(r, "+ Ürün Ekle")
        self.assertContains(r, 'name="stok_dus"')
        self.assertNotContains(r, "Zincir</h2>")

    def test_coklu_urun_hesaplar_kayit_acmaz(self):
        from core.models import OperasyonKaydi, UretimEmri
        n = (UretimEmri.objects.count(), OperasyonKaydi.objects.count())
        r = self.client.post(self.url, self.govde((self.mamul, "2"), (self.kesilmis, "5")))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Zincir</h2>")
        self.assertContains(r, "Özet (istasyon bazında, toplanmış)")
        self.assertEqual(len(r.context["sonuc"]["agac"]), 2)
        self.assertEqual((UretimEmri.objects.count(), OperasyonKaydi.objects.count()), n)
        self.assertEqual(r.context["gorunum"], "ihtiyac")
        r = self.client.post(self.url, {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-hedef": "", "satir-0-miktar": ""}, follow=True)
        self.assertContains(r, "En az bir hedef ürün satırı girin")

    def test_eski_adres_yonlenir_ve_menude_yok(self):
        r = self.client.get(reverse("core:ihtiyac_hesapla"))
        self.assertRedirects(r, self.url, status_code=301, fetch_redirect_response=False)
        r = self.client.post(reverse("core:ihtiyac_hesapla"), self.govde((self.mamul, "1")))
        self.assertEqual(r.status_code, 301)
        kodlar = [e.kod for m in MODULLER if m.kod == "URETIM" for e in m.ekranlar]
        self.assertNotIn("ihtiyac_hesapla", kodlar)
        self.assertIn("urun_agaci", kodlar)
        self.assertContains(self.client.get(reverse("core:uretim_emirleri")), "?gorunum=ihtiyac")     # ÜS listesindeki düğme yeni yere

    def test_duzenle_ikonu_yetkiye_gore(self):
        duzenle = reverse("core:operasyon_duzenle", args=[self.op_montaj.pk])
        r = self.client.get(reverse("core:urun_agaci"), {"urun": self.mamul.pk, "miktar": "1"})
        self.assertContains(r, 'class="ua-duzenle ua-ekran"')
        self.assertContains(r, f'href="{duzenle}"')
        self.assertContains(r, "✎")
        r = self.client.post(self.url, self.govde((self.mamul, "1")))
        self.assertContains(r, f'href="{duzenle}"')                                                    # ihtiyaç zincirinde de ✎
        self.client.force_login(self.yetkili)                                                           # yalnız urun_agaci yetkisi: ✎ yok
        r = self.client.get(reverse("core:urun_agaci"), {"urun": self.mamul.pk, "miktar": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'class="ua-duzenle')                                                  # (CSS kuralı kalır, bağlantı yok)
        self.assertNotContains(r, f'href="{duzenle}"')
        EkranYetki.objects.create(kullanici=self.yetkili, ekran_kod="operasyon_tanimlari")
        self.assertContains(self.client.get(reverse("core:urun_agaci"), {"urun": self.mamul.pk, "miktar": "1"}), 'class="ua-duzenle')
