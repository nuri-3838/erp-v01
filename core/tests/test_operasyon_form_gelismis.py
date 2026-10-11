"""Operasyon Tanımları formu (üretim sadeleştirme adım 6): varsayılan görünüm istasyon + çıktı + girdiler; PARÇALA türü, yan çıktılar, ana çıktı boyu
(mm) ve maliyet pay anahtarı "Gelişmiş ▸" bölümünde (yeni tanımda kapalı; PARÇALA tanımı / yan çıktılı tanım düzenlenirken açık)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.services.uretim import operasyon_olustur
from core.tests.test_uretim import _birim, _istasyon, _kategori, _stok

D = Decimal


class GelismisBolumTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("opgyon", password="x")
        birim, kat = _birim(), _kategori()
        cls.ist = _istasyon("LAZER")
        cls.profil = _stok(kat, birim, kod="OG-PROFIL", ad="profil", satinalma=True)
        cls.ana = _stok(kat, birim, kod="OG-ANA", ad="ana")
        cls.yan = _stok(kat, birim, kod="OG-YAN", ad="yan")
        cls.sag = _stok(kat, birim, kod="OG-SAG", ad="sağ")
        cls.sol = _stok(kat, birim, kod="OG-SOL", ad="sol")
        cls.sade = operasyon_olustur(istasyon_id=cls.ist.pk, cikti_id=cls.ana.pk, cikti_miktar=D("2"), satirlar=[(cls.profil, D("1"))])
        cls.yanli = operasyon_olustur(istasyon_id=cls.ist.pk, cikti_id=cls.yan.pk, cikti_miktar=D("3"), satirlar=[(cls.profil, D("1"))],
                                      boy_mm=D("1000"), yan_ciktilar=[(cls.sag, D("1"), D("500"))])
        cls.parcala = operasyon_olustur(istasyon_id=cls.ist.pk, satirlar=[(cls.profil, D("1"))], tur="PARCALA",
                                        ciktilar=[(cls.sol, D("4"), D("600"), None)], pay_anahtari="BOY")

    def setUp(self):
        self.client.force_login(self.yon)

    def bolum(self, html):
        """Gelişmiş bölümünün açılış etiketi ve içeriği (details … /details)."""
        bas = html.index('<details class="icerik-kart op-gelismis"')
        return html[bas:html.index("</details>", bas)]

    def test_yeni_formda_gelismis_kapali_ve_icerik_oradadir(self):
        r = self.client.get(reverse("core:operasyon_ekle"))
        html = r.content.decode()
        self.assertContains(r, '<details class="icerik-kart op-gelismis" id="gelismis">')          # kapalı (open yok)
        g = self.bolum(html)
        for parca in ('name="tur"', 'id="parcala-bolum"', 'id="yan-kart"', 'id="id_pay_anahtari"', 'name="boy_mm"', "Gelişmiş"):
            self.assertIn(parca, g, parca)
        ana = html[:html.index('<details class="icerik-kart op-gelismis"')]                      # Gelişmiş'ten önceki ana bölüm
        self.assertIn('name="istasyon"', ana)
        self.assertIn('name="cikti"', ana)
        self.assertIn('id="satir-govde"', ana)
        self.assertNotIn('name="tur"', ana)
        self.assertNotIn('name="boy_mm"', ana)

    def test_sade_tanim_duzenlerken_kapali(self):
        r = self.client.get(reverse("core:operasyon_duzenle", args=[self.sade.pk]))
        self.assertContains(r, '<details class="icerik-kart op-gelismis" id="gelismis">')
        self.assertContains(r, 'id="tur-rozet" hidden')

    def test_parcala_ve_yan_ciktili_tanimda_acik(self):
        r = self.client.get(reverse("core:operasyon_duzenle", args=[self.parcala.pk]))
        self.assertContains(r, '<details class="icerik-kart op-gelismis" id="gelismis" open>')
        self.assertContains(r, '<span class="rozet rozet-notr" id="tur-rozet">parçala</span>')
        r = self.client.get(reverse("core:operasyon_duzenle", args=[self.yanli.pk]))
        self.assertContains(r, '<details class="icerik-kart op-gelismis" id="gelismis" open>')
        self.assertContains(r, "Yan Çıktılar")
        r = self.client.get(reverse("core:operasyon_ekle") + f"?kopya={self.yanli.pk}")              # yan çıktılı kopya da açık
        self.assertContains(r, '<details class="icerik-kart op-gelismis" id="gelismis" open>')

    def test_kaydetme_davranisi_degismedi(self):
        yeni = _stok(self.ana.kategori, self.ana.uretim_birimi, kod="OG-YENI", ad="yeni mamul", satis=True)
        r = self.client.post(reverse("core:operasyon_ekle"), {
            "istasyon": self.ist.pk, "cikti": yeni.pk, "cikti_miktar": "1",
            "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.ana.pk, "satir-0-miktar": "2",
            "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0", "pc-TOTAL_FORMS": "0", "pc-INITIAL_FORMS": "0", "tur": "URET"}, follow=True)
        self.assertContains(r, "Operasyon tanımı kaydedildi")
