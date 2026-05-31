"""Yetki yardımcıları — yönetici kontrolü (server tarafı)."""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied


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


def yonetici_gerekli(view):
    """Görünümü yalnızca yöneticilere açar (server tarafı; 403 verir)."""
    @wraps(view)
    @login_required
    def sarmal(request, *args, **kwargs):
        if not yonetici_mi(request.user):
            raise PermissionDenied("Bu ekran yalnızca yöneticiler içindir.")
        return view(request, *args, **kwargs)
    return sarmal
