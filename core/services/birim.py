"""Birim (STOKLAR) servis katmanı — kurallar tek noktada (UI'a güvenilmez).

- Ad ve Kısa Ad TR büyük harfe çevrilir (tek fonksiyon: buyuk_harf_tr).
- Ondalık hane 0-6 arası tam sayı (KG=3, ADET=0...). DB'de de CHECK kısıtı var.
- Silme: soft-delete (iz kalır). İleride stok kartı kullanıyorsa engellenecek.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Birim


class BirimHatasi(ValueError):
    """Birim kural ihlali (Türkçe mesaj)."""


def _ondalik_dogrula(deger) -> int:
    try:
        o = int(deger)
    except (TypeError, ValueError):
        raise BirimHatasi("Ondalık hane 0 ile 6 arasında bir tam sayı olmalı.")
    if not 0 <= o <= 6:
        raise BirimHatasi("Ondalık hane 0 ile 6 arasında olmalı.")
    return o


def _ad_dogrula(ad, kisa_ad):
    ad = buyuk_harf_tr((ad or "").strip())
    kisa_ad = buyuk_harf_tr((kisa_ad or "").strip())
    if not ad:
        raise BirimHatasi("Birim adı boş olamaz.")
    if not kisa_ad:
        raise BirimHatasi("Kısa ad boş olamaz.")
    return ad, kisa_ad


def aktif_birimler():
    """Silinmemiş birimler (pasif olanlar da listede; yalnız silinenler hariç)."""
    return Birim.objects.filter(silindi=False).order_by("ad")


def birim_olustur(*, ad, kisa_ad, ondalik=0, aktif=True, kullanici=None) -> Birim:
    ad, kisa_ad = _ad_dogrula(ad, kisa_ad)
    o = _ondalik_dogrula(ondalik)
    return Birim.objects.create(
        ad=ad, kisa_ad=kisa_ad, ondalik=o, aktif=bool(aktif),
        created_by=kullanici, updated_by=kullanici,
    )


def birim_guncelle(birim: Birim, *, ad, kisa_ad, ondalik, aktif, kullanici=None) -> Birim:
    if birim.silindi:
        raise BirimHatasi("Silinmiş birim düzenlenemez.")
    ad, kisa_ad = _ad_dogrula(ad, kisa_ad)
    o = _ondalik_dogrula(ondalik)
    birim.ad = ad
    birim.kisa_ad = kisa_ad
    birim.ondalik = o
    birim.aktif = bool(aktif)
    birim.updated_by = kullanici
    birim.save(update_fields=["ad", "kisa_ad", "ondalik", "aktif",
                              "updated_by", "updated_at"])
    return birim


def birim_sil(birim: Birim, kullanici=None) -> Birim:
    """Soft-delete (iz kalır). Şimdilik stok kartı yok -> serbest; ileride kullanımdaysa
    engellenecek."""
    if birim.silindi:
        return birim
    birim.silindi = True
    birim.silindi_at = timezone.now()
    birim.updated_by = kullanici
    birim.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return birim
