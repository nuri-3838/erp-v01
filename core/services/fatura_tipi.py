"""Fatura tipi (STOKLAR) servis katmanı — yönetilebilir liste.

Kurallar tek noktada (UI'a güvenilmez):
- Ad TR büyük harfe çevrilir; boş olamaz; silinmemişler arasında BENZERSİZ (DB'de de
  kısmi unique kısıtı var).
- Yön yalnız SATIS / ALIS olabilir.
- Silme: soft-delete (iz kalır). Faz 2'de bir kategori haritasında kullanılıyorsa
  engellenecek (şimdilik harita yok → serbest).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import FaturaTipi


class FaturaTipiHatasi(ValueError):
    """Fatura tipi kural ihlali (Türkçe mesaj)."""


def aktif_fatura_tipleri():
    """Silinmemiş fatura tipleri (sıra, ad)."""
    return FaturaTipi.objects.filter(silindi=False).order_by("sira", "ad")


def _yon_dogrula(yon):
    if yon not in FaturaTipi.Yon.values:
        raise FaturaTipiHatasi("Yön yalnız Satış ya da Alış olabilir.")
    return yon


def _ad_dogrula(ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise FaturaTipiHatasi("Fatura tipi adı boş olamaz.")
    cak = FaturaTipi.objects.filter(silindi=False, ad=ad)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise FaturaTipiHatasi(f"Bu ad zaten kayıtlı: {ad}")
    return ad


def fatura_tipi_olustur(*, ad, yon, sira=0, aktif=True, kullanici=None) -> FaturaTipi:
    yon = _yon_dogrula(yon)
    ad = _ad_dogrula(ad)
    return FaturaTipi.objects.create(
        ad=ad, yon=yon, sira=int(sira or 0), aktif=bool(aktif),
        created_by=kullanici, updated_by=kullanici,
    )


def fatura_tipi_guncelle(tip: FaturaTipi, *, ad, yon, sira, aktif,
                         kullanici=None) -> FaturaTipi:
    if tip.silindi:
        raise FaturaTipiHatasi("Silinmiş fatura tipi düzenlenemez.")
    tip.yon = _yon_dogrula(yon)
    tip.ad = _ad_dogrula(ad, haric_pk=tip.pk)
    tip.sira = int(sira or 0)
    tip.aktif = bool(aktif)
    tip.updated_by = kullanici
    tip.save(update_fields=["ad", "yon", "sira", "aktif", "updated_by", "updated_at"])
    return tip


def fatura_tipi_sil(tip: FaturaTipi, kullanici=None) -> FaturaTipi:
    """Soft-delete (iz kalır)."""
    if tip.silindi:
        return tip
    tip.silindi = True
    tip.silindi_at = timezone.now()
    tip.updated_by = kullanici
    tip.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return tip
