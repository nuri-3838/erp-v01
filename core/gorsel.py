"""Görsel işleme — yüklenen görseli küçült + WebP'ye çevir (TEK formatter).

CLAUDE.md invariant'ı (yüklemede en uzun kenar küçültülür, ~%80 WebP/JPEG): bu
ERP'nin İLK görsel modülü banka logoları. WebP **şeffaflığı korur** (JPEG'in
aksine), bu yüzden logoların transparan PNG'leri bozulmaz. Logo için 512px,
ileride foto eklenirse 1600px ile çağrılır — hep bu fonksiyondan geçer.
"""
from __future__ import annotations

import io

from django.core.files.base import ContentFile
from PIL import Image


def kucult_webp(dosya, *, max_kenar=512, kalite=82, ad="logo") -> ContentFile:
    """`dosya` (yüklenen dosya / yol / file nesnesi) → en uzun kenarı `max_kenar`'a
    küçültülmüş, WebP (şeffaflık korunur) ``ContentFile`` döner; adı ``<ad>.webp``."""
    img = Image.open(dosya)
    if img.mode in ("P", "LA"):
        img = img.convert("RGBA")          # palet/gri+alfa → şeffaflığı koru
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    if max(img.size) > max_kenar:
        img.thumbnail((max_kenar, max_kenar), Image.LANCZOS)
    tampon = io.BytesIO()
    img.save(tampon, format="WEBP", quality=kalite, method=6)
    return ContentFile(tampon.getvalue(), name=f"{ad}.webp")
