"""Kategori (STOKLAR) servis katmanı — 2 seviyeli hiyerarşi + muhasebe hesabı bağı.

Kurallar tek noktada (UI'a güvenilmez):
- Ad TR büyük harfe çevrilir; boş olamaz.
- En fazla 2 SEVİYE: ÜST kategori (ust=None) → ALT kategori (ust = bir ÜST). Bir alt
  kategorinin altına kategori AÇILAMAZ.
- Muhasebe hesabı bağı OPSİYONEL; verilirse hesap planından YAPRAK (fişe kesilebilir,
  alt hesabı olmayan) bir hesap olmalı.
- Silme: soft-delete (iz kalır). Aktif ALT kategorisi olan ÜST kategori silinemez.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kategori
from core.services.hesap_plani import yaprak_mi


class KategoriHatasi(ValueError):
    """Kategori kural ihlali (Türkçe mesaj)."""


def aktif_kategoriler():
    """Silinmemiş kategoriler (ad'a göre); hesap + üst birlikte çekilir."""
    return (Kategori.objects.filter(silindi=False)
            .select_related("ust", "hesap").order_by("ad"))


def ust_kategoriler():
    """ÜST (kök) kategoriler — alt kategori eklerken üst seçimi için."""
    return Kategori.objects.filter(silindi=False, ust__isnull=True).order_by("ad")


def _ad_dogrula(ad):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise KategoriHatasi("Kategori adı boş olamaz.")
    return ad


def _hesap_coz(hesap_kodu):
    """Opsiyonel muhasebe hesabı bağını çözer: boşsa None; doluysa YAPRAK hesap olmalı."""
    kod = (hesap_kodu or "").strip()
    if not kod:
        return None
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise KategoriHatasi(f"Hesap bulunamadı: {kod}")
    if not yaprak_mi(h):
        raise KategoriHatasi(
            "Yalnızca yaprak (alt hesabı olmayan) hesap bağlanabilir; "
            f"{h.hesap_kodu} alt hesabı olan bir üst/ara hesaptır."
        )
    return h


def kategori_olustur(*, ad, ust_id=None, hesap_kodu=None, kullanici=None) -> Kategori:
    """Yeni ÜST (ust_id=None) ya da ALT kategori oluşturur. KategoriHatasi yükseltebilir."""
    ad = _ad_dogrula(ad)
    ust = None
    if ust_id:
        ust = Kategori.objects.filter(pk=ust_id, silindi=False).first()
        if ust is None:
            raise KategoriHatasi("Üst kategori bulunamadı.")
        if ust.ust_id is not None:
            raise KategoriHatasi(
                "En fazla 2 seviye: bir alt kategorinin altına kategori açılamaz."
            )
    hesap = _hesap_coz(hesap_kodu)
    return Kategori.objects.create(
        ad=ad, ust=ust, hesap=hesap,
        created_by=kullanici, updated_by=kullanici,
    )


def kategori_guncelle(kategori: Kategori, *, ad, hesap_kodu, kullanici=None) -> Kategori:
    """Ad + muhasebe hesabı bağını günceller (üst kategori DEĞİŞMEZ)."""
    if kategori.silindi:
        raise KategoriHatasi("Silinmiş kategori düzenlenemez.")
    kategori.ad = _ad_dogrula(ad)
    kategori.hesap = _hesap_coz(hesap_kodu)
    kategori.updated_by = kullanici
    kategori.save(update_fields=["ad", "hesap", "updated_by", "updated_at"])
    return kategori


def kategori_sil(kategori: Kategori, kullanici=None) -> Kategori:
    """Soft-delete. Aktif alt kategorisi olan ÜST kategori silinemez."""
    if kategori.silindi:
        return kategori
    if kategori.alt_kategoriler.filter(silindi=False).exists():
        raise KategoriHatasi(
            "Bu kategorinin alt kategorisi var; önce alt kategorileri silin."
        )
    kategori.silindi = True
    kategori.silindi_at = timezone.now()
    kategori.updated_by = kullanici
    kategori.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kategori
