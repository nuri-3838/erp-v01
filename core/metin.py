"""
Metin — TR büyük harf normalizasyonu (spec 0b-c, KRİTİK). TEK fonksiyon.

Standart ``str.upper()`` Türkçe ``i`` harfini bozar (``i`` -> ``I``). Doğru TR
kuralı: ``i`` -> ``İ`` ve ``ı`` -> ``I``.

İstisna alanlar — bu fonksiyondan **geçirilmez** (çağrı yeri sorumludur):
şifre/parola, e-posta, web/URL ve sistem/dış kimlikler (API anahtarı, token,
dosya yolu, büyük-küçük duyarlı seri/dış referans).
"""
from __future__ import annotations

__all__ = ["buyuk_harf_tr"]

# TR'ye özgü küçük -> büyük eşlemesi. Önce bunlar uygulanır, sonra .upper().
# (ç, ğ, ö, ş, ü harflerini Python upper() zaten doğru çevirir.)
_TR_OZEL = str.maketrans({"i": "İ", "ı": "I"})


def buyuk_harf_tr(metin: str) -> str:
    """Metni Türkçe kurallara göre büyük harfe çevirir::

        "istanbul"          -> "İSTANBUL"
        "ışık"              -> "IŞIK"
        "iğne"              -> "İĞNE"
        "çiçek"             -> "ÇİÇEK"
        "alüminyum merdiven"-> "ALÜMİNYUM MERDİVEN"
    """
    if not isinstance(metin, str):
        raise TypeError(f"Metin bekleniyordu: {metin!r}")
    return metin.translate(_TR_OZEL).upper()
