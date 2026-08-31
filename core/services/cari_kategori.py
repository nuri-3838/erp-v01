"""Cari kategori (CARİLER) servis katmanı — 2 seviyeli hiyerarşi.

- Ad ve Kod TR büyük harfe çevrilir; ikisi de bağlı olduğu ÜST grup içinde benzersiz
  (kök kategoriler kendi arasında). Boş olamaz.
- En fazla 2 SEVİYE: ÜST (ust=None) → ALT. Alt'ın altına açılamaz.
- Silme: soft-delete; aktif alt kategorisi olan ÜST silinemez.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import CariKategori


class CariKategoriHatasi(ValueError):
    """Cari kategori kural ihlali (Türkçe mesaj)."""


def aktif_cari_kategoriler():
    return (CariKategori.objects.filter(silindi=False)
            .select_related("ust").order_by("kod"))


def _ad_dogrula(ad, ust_id, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise CariKategoriHatasi("Kategori adı boş olamaz.")
    cak = CariKategori.objects.filter(silindi=False, ust_id=ust_id, ad=ad)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        yer = "kök kategoriler arasında" if ust_id is None else "bu üst kategori altında"
        raise CariKategoriHatasi(f"Bu ad {yer} zaten kayıtlı: {ad}")
    return ad


def _kod_dogrula(kod, ust_id, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    if not kod:
        raise CariKategoriHatasi("Kategori kodu boş olamaz.")
    cak = CariKategori.objects.filter(silindi=False, ust_id=ust_id, kod=kod)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        yer = "kök kategoriler arasında" if ust_id is None else "bu üst kategori altında"
        raise CariKategoriHatasi(f"Bu kod {yer} zaten kayıtlı: {kod}")
    return kod


def cari_kategori_olustur(*, ad, kod, ust_id=None, kullanici=None) -> CariKategori:
    ust = None
    if ust_id:
        ust = CariKategori.objects.filter(pk=ust_id, silindi=False).first()
        if ust is None:
            raise CariKategoriHatasi("Üst kategori bulunamadı.")
        if ust.ust_id is not None:
            raise CariKategoriHatasi(
                "En fazla 2 seviye: bir alt kategorinin altına kategori açılamaz.")
    ad = _ad_dogrula(ad, ust.pk if ust else None)
    kod = _kod_dogrula(kod, ust.pk if ust else None)
    return CariKategori.objects.create(
        ad=ad, kod=kod, ust=ust,
        created_by=kullanici, updated_by=kullanici)


def cari_kategori_guncelle(kategori: CariKategori, *, ad, kod,
                           kullanici=None) -> CariKategori:
    """Ad + Kod günceller (üst kategori DEĞİŞMEZ)."""
    if kategori.silindi:
        raise CariKategoriHatasi("Silinmiş kategori düzenlenemez.")
    kategori.ad = _ad_dogrula(ad, kategori.ust_id, haric_pk=kategori.pk)
    kategori.kod = _kod_dogrula(kod, kategori.ust_id, haric_pk=kategori.pk)
    kategori.updated_by = kullanici
    kategori.save(update_fields=["ad", "kod", "updated_by", "updated_at"])
    return kategori


def cari_kategori_sil(kategori: CariKategori, kullanici=None) -> CariKategori:
    if kategori.silindi:
        return kategori
    if kategori.alt_kategoriler.filter(silindi=False).exists():
        raise CariKategoriHatasi(
            "Bu kategorinin alt kategorisi var; önce alt kategorileri silin.")
    if kategori.cariler.filter(silindi=False).exists():
        raise CariKategoriHatasi(
            "Bu kategoriye bağlı aktif cari var; önce carileri başka kategoriye taşıyın.")
    kategori.silindi = True
    kategori.silindi_at = timezone.now()
    kategori.updated_by = kullanici
    kategori.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kategori
