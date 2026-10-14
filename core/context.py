"""Şablon context processor'ları."""
from core.yetki import kullanici_menusu, mesai_kullanicisi_mi, yonetici_mi


def yetki(request):
    """Tüm şablonlara yetki bayrağını, kullanıcıya göre filtrelenmiş menüyü ve
    o anki aktif ekran/modülü (sidebar vurgusu + accordion açıklığı için) verir."""
    user = getattr(request, "user", None)
    moduller = kullanici_menusu(user)

    rm = getattr(request, "resolver_match", None)
    aktif_view = rm.view_name if rm else ""   # ör. "core:mizan"
    aktif_modul_kod = aktif_grup_kod = ""
    aktif_ekran = aktif_view                  # menüde işaretlenecek ekranın URL adı (alt sayfalarda — ekle/düzenle — ``Ekran.ilgili`` sayesinde ekranın kendisi)
    for m in moduller:
        ekran = next((e for e in m.ekranlar if e.eslesir(aktif_view)), None)
        if ekran is not None or (m.ana_url_adi and m.ana_url_adi == aktif_view):
            aktif_modul_kod = m.kod
            if ekran is not None:
                aktif_ekran = ekran.url_adi
                if ekran.grup:
                    aktif_grup_kod = f"{m.kod}:{ekran.grup}"          # içindeki sayfadaysan alt menü AÇIK gelir
            break

    return {
        "kullanici_yonetici": yonetici_mi(user),
        "menu_moduller": moduller,
        "aktif_view": aktif_view,
        "aktif_ekran": aktif_ekran,
        "aktif_grup_kod": aktif_grup_kod,
        "aktif_modul_kod": aktif_modul_kod,
        # Mesai kullanıcısı (self-servis, EkranYetki'siz) menüde ek olarak "Mesaim" görür —
        # kullanici_menusu boş dönse bile (normal durum) bu satır ayrıca eklenir.
        "mesai_hesabi_var": mesai_kullanicisi_mi(user),
    }
