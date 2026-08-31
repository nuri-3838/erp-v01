"""Birim (STOKLAR) servis katmanı — kurallar tek noktada (UI'a güvenilmez).

- Ad ve Kısa Ad TR büyük harfe çevrilir (tek fonksiyon: buyuk_harf_tr).
- Ad ve Kısa Ad silinmemişler arasında BENZERSİZ (DB'de de kısmi unique kısıtı var).
- Ondalık hane 0-6 arası tam sayı (KG=3, ADET=0...). DB'de de CHECK kısıtı var.
- Silme: soft-delete (iz kalır). Aktif stok kartı (üretim/fatura birimi olarak) kullanıyorsa engellenir.
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


def _ad_dogrula(ad, kisa_ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    kisa_ad = buyuk_harf_tr((kisa_ad or "").strip())
    if not ad:
        raise BirimHatasi("Birim adı boş olamaz.")
    if not kisa_ad:
        raise BirimHatasi("Kısa ad boş olamaz.")
    ad_cak = Birim.objects.filter(silindi=False, ad=ad)
    kisa_cak = Birim.objects.filter(silindi=False, kisa_ad=kisa_ad)
    if haric_pk is not None:
        ad_cak = ad_cak.exclude(pk=haric_pk)
        kisa_cak = kisa_cak.exclude(pk=haric_pk)
    if ad_cak.exists():
        raise BirimHatasi(f"Bu ad zaten kayıtlı: {ad}")
    if kisa_cak.exists():
        raise BirimHatasi(f"Bu kısa ad zaten kayıtlı: {kisa_ad}")
    return ad, kisa_ad


def aktif_birimler():
    """Silinmemiş birimler (ad'a göre)."""
    return Birim.objects.filter(silindi=False).order_by("ad")


def birim_olustur(*, ad, kisa_ad, ondalik=0, kullanici=None) -> Birim:
    ad, kisa_ad = _ad_dogrula(ad, kisa_ad)
    o = _ondalik_dogrula(ondalik)
    return Birim.objects.create(
        ad=ad, kisa_ad=kisa_ad, ondalik=o,
        created_by=kullanici, updated_by=kullanici,
    )


def birim_guncelle(birim: Birim, *, ad, kisa_ad, ondalik, kullanici=None) -> Birim:
    if birim.silindi:
        raise BirimHatasi("Silinmiş birim düzenlenemez.")
    ad, kisa_ad = _ad_dogrula(ad, kisa_ad, haric_pk=birim.pk)
    o = _ondalik_dogrula(ondalik)
    birim.ad = ad
    birim.kisa_ad = kisa_ad
    birim.ondalik = o
    birim.updated_by = kullanici
    birim.save(update_fields=["ad", "kisa_ad", "ondalik", "updated_by", "updated_at"])
    return birim


def birim_sil(birim: Birim, kullanici=None) -> Birim:
    """Soft-delete (iz kalır). Üretim veya fatura birimi olarak kullanan aktif stok kartı
    varsa silinemez."""
    if birim.silindi:
        return birim
    if (birim.uretim_stoklari.filter(silindi=False).exists()
            or birim.fatura_stoklari.filter(silindi=False).exists()):
        raise BirimHatasi(
            "Bu birim stoklarda kullanılıyor; önce ilgili stoklardan kaldırın."
        )
    birim.silindi = True
    birim.silindi_at = timezone.now()
    birim.updated_by = kullanici
    birim.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return birim
