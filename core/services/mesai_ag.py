"""İNSAN KAYNAKLARI > Mesai Ayarları — fabrika ağı (IP/CIDR) izin listesi.

Yalnız yönetici düzenler (core.views'ta @yonetici_gerekli). Liste BOŞSA mesai başlat/bitir
özelliği KAPALI sayılır (herkes reddedilir) — bkz. core.services.mesai.baslat.
"""
from __future__ import annotations

import ipaddress

from django.utils import timezone

from core.models import MesaiIzinliAg


class MesaiAgHatasi(ValueError):
    """Mesai ağı kural ihlali (Türkçe mesaj)."""


def aktif_aglar():
    return MesaiIzinliAg.objects.filter(silindi=False).order_by("cidr")


def _agi_coz(cidr):
    try:
        return ipaddress.ip_network((cidr or "").strip(), strict=False)
    except ValueError:
        return None


def _dogrula(*, cidr, aciklama="", haric_pk=None) -> dict:
    cidr = (cidr or "").strip()
    if not cidr:
        raise MesaiAgHatasi("IP / CIDR zorunlu.")
    if _agi_coz(cidr) is None:
        raise MesaiAgHatasi(f"Geçersiz IP/CIDR: {cidr} (örnek: 5.6.7.8 veya 10.0.0.0/24).")
    qs = MesaiIzinliAg.objects.filter(silindi=False, cidr=cidr)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise MesaiAgHatasi(f"Bu aralık zaten tanımlı: {cidr}")
    return {"cidr": cidr, "aciklama": (aciklama or "").strip()}


def ag_ekle(*, cidr, aciklama="", kullanici=None) -> MesaiIzinliAg:
    veri = _dogrula(cidr=cidr, aciklama=aciklama)
    return MesaiIzinliAg.objects.create(created_by=kullanici, updated_by=kullanici, **veri)


def ag_guncelle(ag: MesaiIzinliAg, *, cidr, aciklama="", kullanici=None) -> MesaiIzinliAg:
    if ag.silindi:
        raise MesaiAgHatasi("Silinmiş kayıt düzenlenemez.")
    veri = _dogrula(cidr=cidr, aciklama=aciklama, haric_pk=ag.pk)
    ag.cidr, ag.aciklama = veri["cidr"], veri["aciklama"]
    ag.updated_by = kullanici
    ag.save(update_fields=["cidr", "aciklama", "updated_by", "updated_at"])
    return ag


def ag_sil(ag: MesaiIzinliAg, kullanici=None) -> MesaiIzinliAg:
    if ag.silindi:
        return ag
    ag.silindi = True
    ag.silindi_at = timezone.now()
    ag.updated_by = kullanici
    ag.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ag


def ip_izinli_mi(ip: str) -> bool:
    """Liste BOŞSA False (özellik kapalı); ``ip`` geçersiz/boşsa da False."""
    if not ip:
        return False
    try:
        adres = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for ag in aktif_aglar():
        agi = _agi_coz(ag.cidr)
        if agi is None:
            continue
        try:
            if adres in agi:
                return True
        except TypeError:
            continue                        # IPv4/IPv6 karışık karşılaştırma
    return False
