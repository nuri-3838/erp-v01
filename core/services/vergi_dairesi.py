"""Vergi dairesi tanımları (CARİLER, 2026-10-13) — cari formundaki aranabilir liste. Ad TR büyük harf; (ad, il) silinmemişler arası benzersiz.
Cari kartında yine yalnız ad metni saklanır; tanım silinse bile kartlardaki değer değişmez."""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import VergiDairesi


class VergiDairesiHatasi(ValueError):
    """Vergi dairesi kural ihlali (Türkçe mesaj)."""


def aktif_vergi_daireleri():
    return VergiDairesi.objects.filter(silindi=False).order_by("ad", "il")


def _dogrula(ad, il, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    il = buyuk_harf_tr((il or "").strip())
    if not ad:
        raise VergiDairesiHatasi("Vergi dairesi adı boş olamaz.")
    cak = VergiDairesi.objects.filter(silindi=False, ad=ad, il=il)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise VergiDairesiHatasi(f"Bu vergi dairesi zaten kayıtlı: {ad}" + (f" ({il})" if il else ""))
    return ad, il


def vergi_dairesi_olustur(*, ad, il="", kullanici=None) -> VergiDairesi:
    ad, il = _dogrula(ad, il)
    return VergiDairesi.objects.create(ad=ad, il=il, created_by=kullanici, updated_by=kullanici)


def vergi_dairesi_guncelle(vd: VergiDairesi, *, ad, il="", kullanici=None) -> VergiDairesi:
    if vd.silindi:
        raise VergiDairesiHatasi("Silinmiş vergi dairesi düzenlenemez.")
    vd.ad, vd.il = _dogrula(ad, il, haric_pk=vd.pk)
    vd.updated_by = kullanici
    vd.save(update_fields=["ad", "il", "updated_by", "updated_at"])
    return vd


def vergi_dairesi_sil(vd: VergiDairesi, kullanici=None) -> VergiDairesi:
    if vd.silindi:
        return vd
    vd.silindi = True
    vd.silindi_at = timezone.now()
    vd.updated_by = kullanici
    vd.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return vd
