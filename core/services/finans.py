"""FİNANS modülü servis katmanı — Kasa/Banka/Çek-Senet/Kredi/Kredi Kartı TANIMLARI.

Her tanım bir YAPRAK muhasebe hesabına bağlanır; bakiye SAKLANMAZ, o hesabın
yevmiyesinden hesaplanır (cari/ekstre mantığı). İşlem motoru (tahsilat/ödeme) yok.
Ad TR büyük harfe çevrilir + silinmemişler arasında benzersiz.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kasa


class FinansHatasi(ValueError):
    """Finans tanım kural ihlali (Türkçe mesaj)."""


def _yaprak_hesap_coz(hesap_kodu):
    """Verilen kodu yaprak (alt) muhasebe hesabına çözer; üst/eksik/yok ise hata."""
    from core.services.hesap_plani import yaprak_mi
    kod = (hesap_kodu or "").strip()
    if not kod:
        raise FinansHatasi("Muhasebe hesabı seçilmeli.")
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise FinansHatasi(f"Hesap bulunamadı: {kod}")
    if not yaprak_mi(h):
        raise FinansHatasi(f"{kod} bir üst hesap; yalnızca yaprak (alt) hesap seçilebilir.")
    return h


def _ad_dogrula(model, ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise FinansHatasi("Ad boş olamaz.")
    qs = model.objects.filter(silindi=False, ad=ad)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise FinansHatasi(f"Bu ad zaten kayıtlı: {ad}")
    return ad


# --- Kasa -------------------------------------------------------------------
def aktif_kasalar():
    return Kasa.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kasa_olustur(*, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    ad = _ad_dogrula(Kasa, ad)
    return Kasa.objects.create(
        ad=ad, para_birimi=(para_birimi or "TRY"),
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kasa_guncelle(k: Kasa, *, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(Kasa, ad, haric_pk=k.pk)
    k.para_birimi = (para_birimi or "TRY")
    k.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    k.updated_by = kullanici
    k.save(update_fields=["ad", "para_birimi", "muhasebe", "updated_by", "updated_at"])
    return k


def kasa_sil(k: Kasa, kullanici=None) -> Kasa:
    if k.silindi:
        return k
    k.silindi = True
    k.silindi_at = timezone.now()
    k.updated_by = kullanici
    k.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return k
