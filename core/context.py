"""Şablon context processor'ları."""
from core.yetki import kullanici_menusu, yonetici_mi


def yetki(request):
    """Tüm şablonlara yetki bayrağını, kullanıcıya göre filtrelenmiş menüyü ve
    o anki aktif ekran/modülü (sidebar vurgusu + accordion açıklığı için) verir."""
    user = getattr(request, "user", None)
    moduller = kullanici_menusu(user)

    rm = getattr(request, "resolver_match", None)
    aktif_view = rm.view_name if rm else ""   # ör. "core:mizan"
    aktif_modul_kod = ""
    for m in moduller:
        if any(e.url_adi == aktif_view for e in m.ekranlar):
            aktif_modul_kod = m.kod
            break

    return {
        "kullanici_yonetici": yonetici_mi(user),
        "menu_moduller": moduller,
        "aktif_view": aktif_view,
        "aktif_modul_kod": aktif_modul_kod,
    }
