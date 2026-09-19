"""Özel (KVKK hassas) dosya deposu — İNSAN KAYNAKLARI evrakları + personel fotoğrafları.

Dosyalar MEDIA_ROOT DIŞINDA (settings.IK_OZEL_DIR) tutulur: prod nginx `/media/` yolunu KİMLİK
DOĞRULAMASIZ sunar. Bu depo URL üretmez (`.url` her zaman hata verir — yanlışlıkla
`{{ belge.dosya.url }}` yazılırsa sessizce `/media/...` üretmesin diye); dosyalar yalnız
yetkili Django görünümleriyle (core.views.belge_indir / personel_foto) sunulur.
"""
from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.functional import cached_property


class OzelDepo(FileSystemStorage):
    def __init__(self):
        super().__init__(file_permissions_mode=0o600, directory_permissions_mode=0o700)

    @cached_property
    def base_location(self):
        # Tembel: override_settings(IK_OZEL_DIR=...) testlerde etkili olsun.
        return settings.IK_OZEL_DIR

    def _clear_cached_properties(self, setting, **kwargs):
        super()._clear_cached_properties(setting, **kwargs)
        if setting == "IK_OZEL_DIR":
            self.__dict__.pop("base_location", None)
            self.__dict__.pop("location", None)

    def url(self, name):
        raise ValueError(
            "Özel dosyalar URL ile sunulmaz; yetkili indirme görünümünü kullanın.")


_depo = OzelDepo()


def ik_ozel_depo():
    """FileField(storage=...) çağrılabilir referansı (migration'da yol olarak yazılır)."""
    return _depo


def personel_belge_yolu(instance, ad):
    """UUID adlı dosya — tahmin edilemez; özgün ad yalnız `orijinal_ad` kolonunda tutulur."""
    return f"personel_belge/{uuid.uuid4().hex}{os.path.splitext(ad or '')[1].lower()}"


def personel_foto_yolu(instance, ad):
    return f"personel_foto/{uuid.uuid4().hex}.webp"
