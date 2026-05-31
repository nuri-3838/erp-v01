"""Şablon context processor'ları."""
from core.yetki import yonetici_mi


def yetki(request):
    """Tüm şablonlara `kullanici_yonetici` bayrağını verir (menüde kullanılır)."""
    return {"kullanici_yonetici": yonetici_mi(getattr(request, "user", None))}
