"""Yetki yardımcıları — yönetici kontrolü + kullanıcı bazlı EKRAN yetkisi (Adım 3)."""
from dataclasses import replace
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from core.moduller import MODULLER


def yonetici_mi(user) -> bool:
    """Kullanıcı yönetici mi? (superuser VEYA profil.yonetici)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    try:
        return bool(user.profil.yonetici)
    except ObjectDoesNotExist:
        return False


def _izinli_kodlar(user):
    """Kullanıcının açık ekran kodları (yönetici => None = hepsi)."""
    from core.models import EkranYetki  # geç import (model yüklensin)
    if yonetici_mi(user):
        return None
    if not getattr(user, "is_authenticated", False):
        return set()
    return set(
        EkranYetki.objects.filter(kullanici=user, silindi=False)
        .values_list("ekran_kod", flat=True)
    )


def ekran_gorebilir(user, ekran_kod) -> bool:
    """Kullanıcı bu ekranı görebilir mi? (yönetici => evet; aksi => yetki satırı var mı)."""
    kodlar = _izinli_kodlar(user)
    return kodlar is None or ekran_kod in kodlar


def kullanici_menusu(user):
    """Kullanıcının görebildiği ekranlarla menü modüllerini üretir.

    - Yönetici: tüm modüller/ekranlar.
    - Diğer: yönetici-modülleri gizli; MUHASEBE'de yalnızca izinli ekranlar;
      hiç ekranı kalmayan modül menüde görünmez.
    """
    yonetici = yonetici_mi(user)
    kodlar = _izinli_kodlar(user)
    menu = []
    for m in MODULLER:
        if m.yonetici_modulu and not yonetici:
            continue
        if yonetici or m.yonetici_modulu:
            ekranlar = m.ekranlar
        else:
            ekranlar = tuple(e for e in m.ekranlar if e.kod in kodlar)
        if ekranlar:
            menu.append(replace(m, ekranlar=ekranlar))
    return menu


# --- Decorator'lar (server tarafı zorlama) ---------------------------------
def yonetici_gerekli(view):
    """Görünümü yalnızca yöneticilere açar (403)."""
    @wraps(view)
    @login_required
    def sarmal(request, *args, **kwargs):
        if not yonetici_mi(request.user):
            raise PermissionDenied("Bu ekran yalnızca yöneticiler içindir.")
        return view(request, *args, **kwargs)
    return sarmal


def ekran_gerekli(ekran_kod):
    """Görünümü yalnızca o ekrana yetkili kullanıcılara açar (403). Yönetici hep girer."""
    def dekorator(view):
        @wraps(view)
        @login_required
        def sarmal(request, *args, **kwargs):
            if not ekran_gorebilir(request.user, ekran_kod):
                raise PermissionDenied("Bu ekran için yetkiniz yok.")
            return view(request, *args, **kwargs)
        return sarmal
    return dekorator


def ekran_gerekli_herhangi(*ekran_kodlar):
    """Görünümü, verilen ekranlardan EN AZ BİRİNE yetkili kullanıcılara açar (403).

    Fiş detayı gibi birden çok akışın (Fiş Gir sonrası yönlendirme + Fiş Listesi'nden
    görüntüleme) paylaştığı ekranlar için. Yönetici hep girer.
    """
    def dekorator(view):
        @wraps(view)
        @login_required
        def sarmal(request, *args, **kwargs):
            if not any(ekran_gorebilir(request.user, k) for k in ekran_kodlar):
                raise PermissionDenied("Bu ekran için yetkiniz yok.")
            return view(request, *args, **kwargs)
        return sarmal
    return dekorator
