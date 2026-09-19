"""İK dosya testleri için ortak yardımcılar (test modülü DEĞİL — `test*.py` deseniyle keşfedilmez).

Özel depo (IK_OZEL_DIR) ve MEDIA_ROOT her test için geçici dizine yönlendirilir; gerçek
`ozel_dosyalar/` ve `media/` klasörlerine asla yazılmaz.
"""
import io
import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from core.services.personel import personel_olustur

BUGUN = date(2026, 9, 19)

PDF_BAYT = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def png_bayt(boyut=(40, 30), renk=(200, 30, 30)):
    tampon = io.BytesIO()
    Image.new("RGB", boyut, renk).save(tampon, format="PNG")
    return tampon.getvalue()


def jpeg_exif_yon6_bayt(boyut=(200, 100)):
    """EXIF Orientation=6 (telefon fotoğrafı yan yatık) — düzeltilirse boyut tersine döner."""
    resim = Image.new("RGB", boyut, (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = 6
    tampon = io.BytesIO()
    resim.save(tampon, format="JPEG", exif=exif)
    return tampon.getvalue()


def bmp_bayt():
    tampon = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(tampon, format="BMP")
    return tampon.getvalue()


def yuklenen(ad, bayt):
    return SimpleUploadedFile(ad, bayt)


def personel_kur(**kw):
    veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2020, 3, 15)}
    veri.update(kw)
    return personel_olustur(**veri)


class OzelDizinTestTemel(TestCase):
    """Her testte IK_OZEL_DIR ve MEDIA_ROOT ayrı geçici dizinlerdir (test sonunda silinir)."""

    def setUp(self):
        super().setUp()
        self.ozel = Path(tempfile.mkdtemp(prefix="ik_ozel_"))
        self.medya = Path(tempfile.mkdtemp(prefix="ik_medya_"))
        self.addCleanup(shutil.rmtree, self.ozel, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.medya, ignore_errors=True)
        ayar = override_settings(IK_OZEL_DIR=self.ozel, MEDIA_ROOT=self.medya)
        ayar.enable()
        self.addCleanup(ayar.disable)

    def ozel_dosyalar(self):
        return sorted(str(p.relative_to(self.ozel)) for p in self.ozel.rglob("*") if p.is_file())
