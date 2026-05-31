"""Şablon context processor'ları."""
from core.yetki import kullanici_menusu, yonetici_mi


def yetki(request):
    """Tüm şablonlara yetki bayrağını ve kullanıcıya göre filtrelenmiş menüyü verir."""
    user = getattr(request, "user", None)
    return {
        "kullanici_yonetici": yonetici_mi(user),
        "menu_moduller": kullanici_menusu(user),
    }
