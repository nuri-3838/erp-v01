"""Şablon context processor'ları."""
from core.moduller import menu_moduller
from core.yetki import yonetici_mi


def yetki(request):
    """Tüm şablonlara yetki bayrağını ve menü modüllerini verir."""
    yonetici = yonetici_mi(getattr(request, "user", None))
    return {
        "kullanici_yonetici": yonetici,
        "menu_moduller": menu_moduller(yonetici),
    }
