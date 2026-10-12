"""Tıklanabilir liste satırları: ``onclick="window.location=…"`` yerine ``data-href`` + base.html'deki TEK ortak dinleyici (normal tık aynı sekme,
Ctrl/Cmd+tık ve orta tuş yeni sekme, Shift+tık yeni pencere; satır içi bağlantı/düğme/form satır gezinmesini tetiklemez) ve ilk hücrede gerçek
``<a href>`` (sağ tık "yeni sekmede aç" / kopyala). Şablon taraması + gerçek liste ekranlarının çıktısı."""
import re
from datetime import date
from pathlib import Path

from django.urls import reverse

from core.models import Cari
from core.tests.test_recete_sarf_duzelt import ReceteSarfTaban, _hesap

SABLONLAR = Path(__file__).resolve().parent.parent / "templates" / "core"


class SablonTaramaTest(ReceteSarfTaban):
    def test_sablonlarda_onclick_window_location_kalmadi(self):
        kalan = []
        for p in sorted(SABLONLAR.glob("*.html")):
            metin = p.read_text(encoding="utf-8")
            if re.search(r'onclick\s*=\s*"\s*window\.location', metin) or re.search(r"window\.location\s*=\s*\w+\.dataset\.href", metin):
                kalan.append(p.name)
        self.assertEqual(kalan, [])

    def test_base_ortak_dinleyici_ve_stil(self):
        metin = (SABLONLAR / "base.html").read_text(encoding="utf-8")
        for parca in ("[data-href] { cursor:pointer; }", "closest('[data-href]')", "addEventListener('click'", "addEventListener('auxclick'",
                      "e.ctrlKey || e.metaKey", "e.shiftKey", "closest('a, button, form, input, select, textarea, label, summary, details, [data-satir-disi]')"):
            self.assertIn(parca, metin)
        self.assertEqual(metin.count("addEventListener('auxclick'"), 1)


class ListeEkranlariTest(ReceteSarfTaban):
    """Gerçek ekranlar: her tıklanabilir satırda data-href ve aynı adrese giden <a href>; onclick yok."""

    def satirlar(self, url_adi, sorgu=""):
        self.client.force_login(self.yon)
        html = self.client.get(reverse(url_adi) + sorgu).content.decode()
        self.assertNotIn('onclick="window.location', html)
        satirlar = re.findall(r'<tr[^>]*data-href="([^"]+)"[^>]*>(.*?)</tr>', html, flags=re.S)
        self.assertTrue(satirlar, url_adi)
        for href, govde in satirlar:
            self.assertIn(f'href="{href}"', govde, f"{url_adi}: satırın içinde {href} bağlantısı yok")
        return html, satirlar

    def test_stoklar_us_personel_cari_listeleri(self):
        from core.models import Personel
        Personel.objects.create(ad="AYŞE", soyad="TEST", ise_giris_tarihi=date(2026, 1, 1))
        _hesap("120.01", "MÜŞTERİ RS")
        Cari.objects.create(kod="RS-C1", unvan="MÜŞTERİ RS", muhasebe_kodu="120.01")
        html, satirlar = self.satirlar("core:stoklar")
        self.assertTrue(any(reverse("core:stok_detay", args=[self.kayis.pk]) == h for h, _g in satirlar))
        html, satirlar = self.satirlar("core:uretim_emirleri", "?durum=TUMU")                   # fixture ÜS'leri üretilip kapanmış
        self.assertIn('class="satir-bag" href="' + satirlar[0][0], html)                      # ÜS no gerçek bağlantı
        self.satirlar("core:personeller")
        self.satirlar("core:cariler")
        self.satirlar("core:operasyon_tanimlari")
        self.assertIn("base.html'de", (SABLONLAR / "operasyon_tanimlari.html").read_text(encoding="utf-8"))
