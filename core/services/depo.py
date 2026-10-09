"""Depo (STOKLAR Faz B) servis katmanı — kurallar tek noktada.

- Kod + Ad TR büyük harfe çevrilir; ikisi de silinmemişler arasında BENZERSİZ.
- Silme: soft-delete. Depo'da hareket varsa silinemez (B2'de zorlanır).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Cari, Depo


class DepoHatasi(ValueError):
    """Depo kural ihlali (Türkçe mesaj)."""


def aktif_depolar():
    return Depo.objects.filter(silindi=False).select_related("fason_cari").order_by("kod")


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


def _fason_cari_coz(fason_cari, *, haric_pk=None):
    """``fason_cari`` (Cari ya da pk; boş = fason deposu değil) → Cari | None. Bir cariye en çok bir aktif fason deposu bağlanır."""
    if fason_cari in (None, ""):
        return None
    cari = fason_cari if isinstance(fason_cari, Cari) else Cari.objects.filter(pk=fason_cari, silindi=False).first()
    if cari is None or cari.silindi:
        raise DepoHatasi("Fasoncu (cari) bulunamadı.")
    qs = Depo.objects.filter(silindi=False, fason_cari=cari)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    var = qs.first()
    if var is not None:
        raise DepoHatasi(f"{cari.unvan} için zaten bir fason deposu bağlı: {var.kod}.")
    return cari


def fason_deposu(cari):
    """Carinin aktif fason deposu (yoksa None)."""
    return Depo.objects.filter(silindi=False, fason_cari=getattr(cari, "pk", cari)).first()


def depo_olustur(*, kod, ad, fason_cari=None, kullanici=None) -> Depo:
    kod, ad = _dogrula(kod, ad)
    cari = _fason_cari_coz(fason_cari)
    return Depo.objects.create(kod=kod, ad=ad, fason_cari=cari, created_by=kullanici, updated_by=kullanici)


def depo_guncelle(depo: Depo, *, kod, ad, fason_cari=None, kullanici=None) -> Depo:
    if depo.silindi:
        raise DepoHatasi("Silinmiş depo düzenlenemez.")
    kod, ad = _dogrula(kod, ad, haric_pk=depo.pk)
    cari = _fason_cari_coz(fason_cari, haric_pk=depo.pk)
    depo.kod, depo.ad, depo.fason_cari = kod, ad, cari
    depo.updated_by = kullanici
    depo.save(update_fields=["kod", "ad", "fason_cari", "updated_by", "updated_at"])
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
