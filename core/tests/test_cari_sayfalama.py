"""Cari listesi SAYFALAMA (2026-10-14 şikâyeti: "2. ve 3. sayfa 1. sayfanın aynısı"): 120 cari → 50/50/20; her sayfa farklı, birleşimi eksiksiz ve tekil;
süzgeç + sıralama birlikte; sayfa bağlantıları süzgeç/sıralamayı taşır ve bağlantıdan gidilen sayfa gerçekten diğer sayfadır."""
import datetime
import html
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Sehir, Ulke
from core.services import cari_ozet
from core.services.cari import cari_olustur
from core.tests.test_cari_faz2 import _fis, _hesaplar, _kat

D = datetime.date


class CariSayfalamaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesaplar()
        cls.su = User.objects.create_superuser("sy", password="x")
        ted, mus = _kat("320", "TEDARİKÇİLER", "10", "HAMMADDE"), _kat("120", "MÜŞTERİLER", "10", "YURTİÇİ")
        tr = Ulke.objects.create(kod="TR", ad="TÜRKİYE")
        cls.kayseri = Sehir.objects.create(ulke=tr, ad="KAYSERİ", kod="38")
        cls.ankara = Sehir.objects.create(ulke=tr, ad="ANKARA", kod="06")
        for i in range(120):
            c = cari_olustur(unvan=f"cari {i:03d}", kategori_id=(ted if i % 3 else mus).pk, para_birimi="TRY",
                             sehir_id=(cls.kayseri if i < 70 else cls.ankara).pk, ulke_id=tr.pk)
            if i % 4 == 0:                                                       # bir kısmı hareketli (bakiye/hareket sıralaması için), çoğu hareketsiz → eşit anahtarlar
                _fis(c, "A" if i % 3 else "B", str(10 + i % 7), D(2026, 3, 1 + i % 20))

    def _git(self, url):
        self.client.force_login(self.su)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return r, re.findall(r'class="kod">([^<]+)<', r.content.decode())

    def _sayfalar(self, **q):
        sayfalar = []
        for n in (1, 2, 3):
            _r, kodlar = self._git(reverse("core:cariler") + "?" + "&".join([f"{k}={v}" for k, v in {**q, "sayfa": n}.items()]))
            sayfalar.append(kodlar)
        return sayfalar

    def test_varsayilan_uc_sayfa_farkli_ve_eksiksiz(self):
        s1, s2, s3 = self._sayfalar()
        self.assertEqual((len(s1), len(s2), len(s3)), (50, 50, 20))
        self.assertNotEqual(s1, s2)
        self.assertNotEqual(s2, s3)
        self.assertFalse(set(s1) & set(s2) or set(s2) & set(s3) or set(s1) & set(s3))
        self.assertEqual(len(set(s1 + s2 + s3)), 120)
        beklenen = [s["cari"].kod for s in cari_ozet.liste_getir()]
        self.assertEqual(s1 + s2 + s3, beklenen)                                  # sayfa sırası = servis sırası

    def test_her_siralama_ve_yonde_sayfalar_tekil_ve_eksiksiz(self):
        for sirala in cari_ozet.SIRALAMA_ANAHTARLARI:
            for yon in ("asc", "desc"):
                s1, s2, s3 = self._sayfalar(sirala=sirala, yon=yon)
                self.assertEqual(len(set(s1 + s2 + s3)), 120, (sirala, yon))
                self.assertEqual(s1 + s2 + s3, [s["cari"].kod for s in cari_ozet.liste_getir(sirala=sirala, yon=yon)], (sirala, yon))

    def test_suzgec_ve_siralama_ile_sayfalama(self):
        q = dict(sehir=self.kayseri.pk, sirala="bakiye", yon="desc")
        s1, s2, s3 = self._sayfalar(**q)                                           # 70 cari → 50 + 20
        self.assertEqual((len(s1), len(s2)), (50, 20))
        self.assertFalse(set(s1) & set(s2))
        self.assertEqual(s1 + s2, [s["cari"].kod for s in cari_ozet.liste_getir(sehir_id=str(self.kayseri.pk), sirala="bakiye", yon="desc")])
        self.assertEqual(s3, s2)                                                   # taşan sayfa numarası SON sayfaya düşer (Paginator.get_page)

    def test_sayfa_baglantilari_suzgec_ve_siralamayi_tasir_ve_dogru_sayfaya_gider(self):
        self.client.force_login(self.su)
        url = reverse("core:cariler") + f"?sehir={self.kayseri.pk}&sirala=bakiye&yon=desc&tur=ticari"
        r = self.client.get(url)
        h = r.content.decode()
        sonraki = html.unescape(re.search(r'<a href="([^"]*)">›</a>', h).group(1))
        for beklenen in (f"sehir={self.kayseri.pk}", "sirala=bakiye", "yon=desc", "tur=ticari", "sayfa=2"):
            self.assertIn(beklenen, sonraki)
        r2 = self.client.get(reverse("core:cariler") + sonraki)
        h2 = r2.content.decode()
        self.assertIn("sayfa 2 /", h2)
        k1, k2 = re.findall(r'class="kod">([^<]+)<', h), re.findall(r'class="kod">([^<]+)<', h2)
        self.assertFalse(set(k1) & set(k2))
        onceki = html.unescape(re.search(r'<a href="([^"]*)">‹</a>', h2).group(1))
        self.assertIn("sirala=bakiye", onceki)
        self.assertEqual(re.findall(r'class="kod">([^<]+)<', self.client.get(reverse("core:cariler") + onceki).content.decode()), k1)

    def test_siralama_baslik_baglantisi_sayfayi_sifirlar_suzgeci_korur(self):
        self.client.force_login(self.su)
        h = self.client.get(reverse("core:cariler") + f"?sehir={self.kayseri.pk}&sayfa=2").content.decode()
        link = html.unescape(re.search(r'<a href="(\?[^"]*sirala=bakiye[^"]*)">Bakiye TL', h).group(1))
        self.assertIn(f"sehir={self.kayseri.pk}", link)
        self.assertNotIn("sayfa=", link)
