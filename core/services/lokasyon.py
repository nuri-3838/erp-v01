"""Lokasyon (CARİLER) servis katmanı — Ülke / Şehir master data.

- Ad/kod TR büyük harfe çevrilir. Ülke kodu (ISO, 2 harf) silinmemişler arası benzersiz.
- Şehir adı, bağlı olduğu ülke içinde benzersiz.
- Silme: soft-delete. Aktif şehri olan ülke silinemez.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Sehir, Ulke


class LokasyonHatasi(ValueError):
    """Lokasyon kural ihlali (Türkçe mesaj)."""


def aktif_ulkeler():
    return Ulke.objects.filter(silindi=False).order_by("ad")


def aktif_sehirler(ulke=None):
    qs = Sehir.objects.filter(silindi=False).select_related("ulke")
    if ulke is not None:
        qs = qs.filter(ulke=ulke)
    return qs.order_by("ulke__ad", "ad")


# --- Ülke ---------------------------------------------------------------------
def _ulke_kod_dogrula(kod, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    if not kod:
        raise LokasyonHatasi("Ülke kodu boş olamaz.")
    if len(kod) > 2:
        raise LokasyonHatasi("Ülke kodu en fazla 2 harf olmalı (ISO, örn. TR).")
    cak = Ulke.objects.filter(silindi=False, kod=kod)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise LokasyonHatasi(f"Bu ülke kodu zaten kayıtlı: {kod}")
    return kod


def _ad_dogrula(ad, etiket="Ad"):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise LokasyonHatasi(f"{etiket} boş olamaz.")
    return ad


def ulke_olustur(*, kod, ad, ad_en="", kullanici=None) -> Ulke:
    kod = _ulke_kod_dogrula(kod)
    ad = _ad_dogrula(ad, "Ülke adı")
    return Ulke.objects.create(
        kod=kod, ad=ad, ad_en=buyuk_harf_tr((ad_en or "").strip()),
        created_by=kullanici, updated_by=kullanici)


def ulke_guncelle(ulke: Ulke, *, kod, ad, ad_en="", kullanici=None) -> Ulke:
    if ulke.silindi:
        raise LokasyonHatasi("Silinmiş ülke düzenlenemez.")
    ulke.kod = _ulke_kod_dogrula(kod, haric_pk=ulke.pk)
    ulke.ad = _ad_dogrula(ad, "Ülke adı")
    ulke.ad_en = buyuk_harf_tr((ad_en or "").strip())
    ulke.updated_by = kullanici
    ulke.save(update_fields=["kod", "ad", "ad_en", "updated_by", "updated_at"])
    return ulke


def ulke_sil(ulke: Ulke, kullanici=None) -> Ulke:
    if ulke.silindi:
        return ulke
    if ulke.sehirler.filter(silindi=False).exists():
        raise LokasyonHatasi("Bu ülkenin şehirleri var; önce şehirleri silin.")
    ulke.silindi = True
    ulke.silindi_at = timezone.now()
    ulke.updated_by = kullanici
    ulke.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ulke


# --- Şehir --------------------------------------------------------------------
def _sehir_ad_dogrula(ulke, ad, *, haric_pk=None):
    ad = _ad_dogrula(ad, "Şehir adı")
    cak = Sehir.objects.filter(silindi=False, ulke=ulke, ad=ad)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise LokasyonHatasi(f"Bu şehir bu ülkede zaten kayıtlı: {ad}")
    return ad


def _ulke_coz(ulke_id):
    u = Ulke.objects.filter(pk=ulke_id, silindi=False).first()
    if u is None:
        raise LokasyonHatasi("Ülke bulunamadı.")
    return u


def sehir_olustur(*, ulke_id, ad, kod="", ad_en="", kullanici=None) -> Sehir:
    ulke = _ulke_coz(ulke_id)
    ad = _sehir_ad_dogrula(ulke, ad)
    return Sehir.objects.create(
        ulke=ulke, ad=ad, kod=buyuk_harf_tr((kod or "").strip()),
        ad_en=buyuk_harf_tr((ad_en or "").strip()),
        created_by=kullanici, updated_by=kullanici)


def sehir_guncelle(sehir: Sehir, *, ulke_id, ad, kod="", ad_en="", kullanici=None) -> Sehir:
    if sehir.silindi:
        raise LokasyonHatasi("Silinmiş şehir düzenlenemez.")
    ulke = _ulke_coz(ulke_id)
    ad = _sehir_ad_dogrula(ulke, ad, haric_pk=sehir.pk)
    sehir.ulke = ulke
    sehir.ad = ad
    sehir.kod = buyuk_harf_tr((kod or "").strip())
    sehir.ad_en = buyuk_harf_tr((ad_en or "").strip())
    sehir.updated_by = kullanici
    sehir.save(update_fields=["ulke", "ad", "kod", "ad_en", "updated_by", "updated_at"])
    return sehir


def sehir_sil(sehir: Sehir, kullanici=None) -> Sehir:
    if sehir.silindi:
        return sehir
    sehir.silindi = True
    sehir.silindi_at = timezone.now()
    sehir.updated_by = kullanici
    sehir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return sehir
