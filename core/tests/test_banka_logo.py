"""Banka logosu (ilk görsel modülü): kucult_webp (küçültme + WebP + şeffaflık),
logo yükleme ve havuz isimle otomatik atama. Geçici MEDIA_ROOT (override)."""
import io
import os
import tempfile

from django.test import TestCase, override_settings
from PIL import Image


def _png(boyut=(800, 600), renk=(200, 30, 30, 128)):
    buf = io.BytesIO()
    Image.new("RGBA", boyut, renk).save(buf, "PNG")
    buf.seek(0)
    return buf


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class BankaLogoTest(TestCase):
    def test_kucult_webp_seffaflik_korur(self):
        from core.gorsel import kucult_webp
        cf = kucult_webp(_png((800, 600)), max_kenar=512, ad="x")
        self.assertTrue(cf.name.endswith(".webp"))
        im = Image.open(io.BytesIO(cf.read()))
        self.assertEqual(im.format, "WEBP")
        self.assertLessEqual(max(im.size), 512)          # küçültüldü
        self.assertEqual(im.mode, "RGBA")                # şeffaflık korundu

    def test_banka_logo_yukle(self):
        from core.gorsel import kucult_webp
        from core.services.finans import banka_olustur
        b = banka_olustur(ad="akbank", logo=kucult_webp(_png(), ad="banka"))
        self.assertTrue(b.logo)
        self.assertTrue(b.logo.name.endswith(".webp"))

    def test_havuz_isimle_oto_atama(self):
        from django.conf import settings
        from core.gorsel import kucult_webp
        from core.services.finans import banka_olustur
        havuz = os.path.join(settings.MEDIA_ROOT, "banka_logo", "_havuz")
        os.makedirs(havuz, exist_ok=True)
        with open(os.path.join(havuz, "ziraat.webp"), "wb") as f:
            f.write(kucult_webp(_png(), ad="z").read())
        b = banka_olustur(ad="türkiye cumhuriyeti ziraat bankası a.ş.")  # logo verilmedi
        self.assertTrue(b.logo)                          # havuzdan ZİRAAT eşleşti
        b2 = banka_olustur(ad="akbank")                  # eşleşme yok
        self.assertFalse(b2.logo)
