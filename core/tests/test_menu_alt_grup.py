"""Sol menü 3. seviye (açılır alt menü): ``Ekran.grup`` → ``Modul.ogeler`` → base.html. Şimdilik yalnız Cariler > Tanımlar (Cari Kategorileri, Ülke / Şehir,
Vergi Daireleri). Yetki kuralları aynen: yetkisiz öğe görünmez, içi boşalan alt menü de görünmez; alt menü varsayılan kapalı, içindeki sayfada AÇIK + aktif işaretli."""
import re
from dataclasses import replace

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.moduller import MODULLER, Ekran, MenuGrup, Modul
from core.models import EkranYetki
from core.yetki import kullanici_menusu


def _cariler(kullanici):
    return next(m for m in kullanici_menusu(kullanici) if m.kod == "CARILER")


class MenuYapisiTest(TestCase):
    def test_cariler_tanimlar_alt_menusu(self):
        m = next(m for m in MODULLER if m.kod == "CARILER")
        ogeler = m.ogeler
        self.assertEqual([o.ad for o in ogeler], ["Cariler", "Tanımlar"])
        self.assertIsInstance(ogeler[1], MenuGrup)
        self.assertEqual([e.kod for e in ogeler[1].ekranlar], ["cari_kategoriler", "lokasyonlar", "vergi_daireleri"])
        self.assertEqual(ogeler[1].kod, "CARILER:Tanımlar")
        self.assertFalse(ogeler[0].alt_menu)
        self.assertTrue(ogeler[1].alt_menu)
        # düz liste (yetki ekranı / testler) aynen: 4 ekran
        self.assertEqual([e.kod for e in m.ekranlar], ["cariler", "cari_kategoriler", "lokasyonlar", "vergi_daireleri"])

    def test_genel_yapi_birden_cok_grup_sira_ve_bos_grup(self):
        m = Modul("X", "X", (Ekran("a", "A", "core:a"), Ekran("b", "B", "core:b", grup="G1"), Ekran("c", "C", "core:c"),
                             Ekran("d", "D", "core:d", grup="G2"), Ekran("e", "E", "core:e", grup="G1")))
        self.assertEqual([(o.ad, [e.kod for e in o.ekranlar] if o.alt_menu else None) for o in m.ogeler],
                         [("A", None), ("G1", ["b", "e"]), ("C", None), ("G2", ["d"])])         # grup, ilk üyesinin yerinde
        suzulmus = replace(m, ekranlar=tuple(e for e in m.ekranlar if e.kod in ("a", "c")))      # yetki süzgeci sonrası: grup üyesi kalmadı
        self.assertEqual([o.ad for o in suzulmus.ogeler], ["A", "C"])                          # boş grup üretilmez

    def test_ilgili_sayfalar(self):
        e = next(e for m in MODULLER for e in m.ekranlar if e.kod == "vergi_daireleri")
        self.assertTrue(e.eslesir("core:vergi_daireleri"))
        self.assertTrue(e.eslesir("core:vergi_dairesi_ekle"))
        self.assertFalse(e.eslesir("core:cariler"))
        self.assertFalse(e.eslesir(""))

    def test_diger_moduller_alt_menusuz(self):
        for m in MODULLER:
            if m.kod != "CARILER":
                self.assertFalse(any(o.alt_menu for o in m.ogeler), m.kod)


class MenuYetkiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.sadece_liste = User.objects.create_user("liste", password="x")
        cls.tanim = User.objects.create_user("tanim", password="x")
        cls.hepsi = User.objects.create_user("hepsi", password="x")
        cls.hic = User.objects.create_user("hic", password="x")
        EkranYetki.objects.create(kullanici=cls.sadece_liste, ekran_kod="cariler")
        EkranYetki.objects.create(kullanici=cls.tanim, ekran_kod="vergi_daireleri")
        for kod in ("cariler", "cari_kategoriler", "lokasyonlar", "vergi_daireleri"):
            EkranYetki.objects.create(kullanici=cls.hepsi, ekran_kod=kod)

    def test_yonetici_hepsini_gorur(self):
        self.assertEqual([o.ad for o in _cariler(self.yon).ogeler], ["Cariler", "Tanımlar"])

    def test_yetkisiz_oge_gorunmez_ve_bos_tanimlar_gorunmez(self):
        self.assertEqual([o.ad for o in _cariler(self.sadece_liste).ogeler], ["Cariler"])            # Tanımlar yok
        ogeler = _cariler(self.tanim).ogeler                                                         # yalnız bir tanım ekranı
        self.assertEqual([o.ad for o in ogeler], ["Tanımlar"])
        self.assertEqual([e.kod for e in ogeler[0].ekranlar], ["vergi_daireleri"])
        self.assertEqual([e.kod for e in _cariler(self.hepsi).ogeler[1].ekranlar], ["cari_kategoriler", "lokasyonlar", "vergi_daireleri"])
        self.assertFalse([m for m in kullanici_menusu(self.hic) if m.kod == "CARILER"])


class MenuRenderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon2", password="x")
        cls.dar = User.objects.create_user("dar", password="x")
        EkranYetki.objects.create(kullanici=cls.dar, ekran_kod="cariler")

    def _menu(self, url, kullanici=None):
        self.client.force_login(kullanici or self.yon)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        h = r.content.decode()
        return h[h.index('<nav class="yan-menu">'):h.index("</nav>", h.index('<nav class="yan-menu">'))]

    def _alt(self, menu):
        m = re.search(r'<details class="alt-menu[^"]*"[^>]*>.*?</details>', menu, re.S)
        return m.group(0) if m else ""

    def test_varsayilan_kapali_ve_uc_baglanti(self):
        alt = self._alt(self._menu(reverse("core:pano")))
        self.assertIn("Tanımlar", alt)
        self.assertNotRegex(alt.split(">")[0] + ">", r"\bopen\b")                                    # kapalı
        for url in ("core:cari_kategoriler", "core:lokasyonlar", "core:vergi_daireleri"):
            self.assertIn(f'href="{reverse(url)}"', alt)
        self.assertNotIn('class="aktif"', alt)

    def test_cariler_listesindeyken_tanimlar_kapali_modul_acik(self):
        menu = self._menu(reverse("core:cariler"))
        self.assertNotRegex(self._alt(menu).split(">")[0] + ">", r"\bopen\b")
        self.assertRegex(menu, r'<details class="modul" open>(?s:.)*?Cariler')

    def test_tanimlar_icindeki_sayfada_acik_ve_aktif(self):
        for view, ad in (("core:cari_kategoriler", "Cari Kategorileri"), ("core:lokasyonlar", "Ülke / Şehir"), ("core:vergi_daireleri", "Vergi Daireleri")):
            alt = self._alt(self._menu(reverse(view)))
            self.assertRegex(alt.split(">")[0] + ">", r"\bopen\b", view)
            self.assertRegex(alt, rf'<a href="{re.escape(reverse(view))}" class="aktif">{re.escape(ad)}</a>', view)
            self.assertEqual(alt.count('class="aktif"'), 1, view)

    def test_alt_sayfada_da_acik_ve_ekran_aktif(self):
        alt = self._alt(self._menu(reverse("core:vergi_dairesi_ekle")))
        self.assertRegex(alt.split(">")[0] + ">", r"\bopen\b")
        self.assertRegex(alt, rf'<a href="{re.escape(reverse("core:vergi_daireleri"))}" class="aktif">')

    def test_yetkisiz_kullanicida_tanimlar_hic_yok(self):
        menu = self._menu(reverse("core:cariler"), self.dar)
        self.assertNotIn("alt-menu", menu)
        self.assertNotIn("Tanımlar", menu)
        self.assertIn("Cariler", menu)

    def test_diger_menu_ogeleri_bozulmadi(self):
        menu = self._menu(reverse("core:mizan"))
        self.assertRegex(menu, rf'<a href="{re.escape(reverse("core:mizan"))}" class="aktif">Mizan</a>')
        self.assertEqual(menu.count('<details class="alt-menu'), 1)
