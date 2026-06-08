"""Depo (STOKLAR Faz B) servis katmanı — kurallar tek noktada.

- Kod + Ad TR büyük harfe çevrilir; ikisi de silinmemişler arasında BENZERSİZ.
- Silme: soft-delete. Depo'da hareket varsa silinemez (B2'de zorlanır).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Depo


class DepoHatasi(ValueError):
    """Depo kural ihlali (Türkçe mesaj)."""


def aktif_depolar():
    return Depo.objects.filter(silindi=False).order_by("kod")


def _dogrula(kod, ad, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    ad = buyuk_harf_tr((ad or "").strip())
    if not kod:
        raise DepoHatasi("Depo kodu boş olamaz.")
    if not ad:
        raise DepoHatasi("Depo adı boş olamaz.")
    for alan, deger, etiket in (("kod", kod, "kod"), ("ad", ad, "ad")):
        qs = Depo.objects.filter(silindi=False, **{alan: deger})
        if haric_pk is not None:
            qs = qs.exclude(pk=haric_pk)
        if qs.exists():
            raise DepoHatasi(f"Bu {etiket} zaten kayıtlı: {deger}")
    return kod, ad


def depo_olustur(*, kod, ad, kullanici=None) -> Depo:
    kod, ad = _dogrula(kod, ad)
    return Depo.objects.create(kod=kod, ad=ad, created_by=kullanici, updated_by=kullanici)


def depo_guncelle(depo: Depo, *, kod, ad, kullanici=None) -> Depo:
    if depo.silindi:
        raise DepoHatasi("Silinmiş depo düzenlenemez.")
    kod, ad = _dogrula(kod, ad, haric_pk=depo.pk)
    depo.kod, depo.ad = kod, ad
    depo.updated_by = kullanici
    depo.save(update_fields=["kod", "ad", "updated_by", "updated_at"])
    return depo


def depo_sil(depo: Depo, kullanici=None) -> Depo:
    if depo.silindi:
        return depo
    if depo.hareketler.filter(silindi=False).exists():
        raise DepoHatasi("Bu depoda stok hareketi var; silinemez.")
    depo.silindi = True
    depo.silindi_at = timezone.now()
    depo.updated_by = kullanici
    depo.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return depo
