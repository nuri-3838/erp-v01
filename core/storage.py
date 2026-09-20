"""Özel (gizli) dosya deposu — İNSAN KAYNAKLARI evrakları + personel fotoğrafları (KVKK hassas),
cari/aday aktivite ekleri ve çek/senet görselleri.

Dosyalar MEDIA_ROOT DIŞINDA (settings.IK_OZEL_DIR) tutulur: prod nginx `/media/` yolunu KİMLİK
DOĞRULAMASIZ sunar. Bu depo URL üretmez (`.url` her zaman hata verir — yanlışlıkla
`{{ belge.dosya.url }}` yazılırsa sessizce `/media/...` üretmesin diye); dosyalar yalnız
yetkili Django görünümleriyle (core.views.belge_indir / personel_foto / cari_ek_indir /
aday_ek_indir / cek_gorsel) sunulur.

Hangi yüklemenin GİZLİ, hangisinin girişsiz sunulabilir (genel) olduğu tek yerde: aşağıdaki
`GENEL_MEDYA_ONEKLERI` dışındaki her FileField bu depoyu kullanmak ZORUNDADIR
(core/tests/test_ozel_dosya_guvenlik.py bunu tüm modellerde denetler).
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


# MEDIA_ROOT altında nginx'in girişsiz sunmasına İZİN VERİLEN klasörler (stok görselleri, banka
# ve firma logoları). Bunların dışındaki her yükleme özel depoya gider; scripts/
# nginx_media_kisitla.py'nin izin listesi bu değerlerle aynı olmak zorundadır (testle denetlenir).
GENEL_MEDYA_ONEKLERI = ("stok_gorsel", "banka_logo", "firma_logo")
# Eskiden MEDIA_ROOT'ta duran, şimdi özel depoda tutulan klasörler (tasi_ozel_dosyalar komutu
# mevcut dosyaları bunlardan taşır; DB'deki göreli yollar aynı kalır).
OZELE_TASINAN_ONEKLER = ("cari_aktivite", "aday_aktivite", "cek_senet")

_depo = OzelDepo()


def ozel_depo():
    """FileField(storage=...) çağrılabilir referansı (migration'da yol olarak yazılır) — bütün
    özel yüklemeler (İK, cari/aday ekleri, çek/senet görselleri) bu tek depoyu paylaşır."""
    return _depo


# İK alanlarının mevcut migration'ları bu adı yazar; aynı fonksiyonun eski adı olarak kalır.
ik_ozel_depo = ozel_depo


def _uuid_yolu(klasor, ad):
    return f"{klasor}/{uuid.uuid4().hex}{os.path.splitext(ad or '')[1].lower()}"


def personel_belge_yolu(instance, ad):
    """UUID adlı dosya — tahmin edilemez; özgün ad yalnız `orijinal_ad` kolonunda tutulur."""
    return _uuid_yolu("personel_belge", ad)


def personel_foto_yolu(instance, ad):
    return f"personel_foto/{uuid.uuid4().hex}.webp"


def cari_ek_yolu(instance, ad):
    return _uuid_yolu("cari_aktivite", ad)


def aday_ek_yolu(instance, ad):
    return _uuid_yolu("aday_aktivite", ad)


def cek_gorsel_yolu(instance, ad):
    return _uuid_yolu("cek_senet", ad)
