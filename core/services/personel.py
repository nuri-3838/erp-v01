"""İNSAN KAYNAKLARI > Personel Kartları servis katmanı.

Yalnız KAYIT tutar: bordro, maaş, SGK/vergi/prim ve muhasebe fişi bu modülde YOKTUR (Yemek
Takibi gibi kontrol amaçlı). Kural ve dönüşümler:

- Ad/soyad/adres/acil durum kişisi/departman/görev TR büyük harfe çevrilir; e-posta küçük harf.
- TC kimlik no boş olabilir; doluysa gerçek TC algoritmasına uymalı ve silinmemiş kartlar
  arasında benzersiz olmalı (ayrılmış personelin kartı da kapsanır).
- Telefon geçerliyse ``+90XXXXXXXXXX`` kanonik biçimine çevrilir, değilse yazıldığı gibi kalır.
- "Aktif" saklanmaz: ``isten_cikis_tarihi`` boş veya bugün/gelecekteyse personel çalışıyordur.
- Silme: soft-delete.
"""
from __future__ import annotations

import re

from django.db.models import Q
from django.utils import timezone

from core.dogrulama import tc_gecerli, telefon_kanonik
from core.metin import buyuk_harf_tr
from core.models import Personel
from core.tarih import tr_bugun

DURUMLAR = ("aktif", "ayrildi", "hepsi")
_KAN_GRUPLARI = {k for k, _ in Personel.KAN_GRUPLARI}


class PersonelHatasi(ValueError):
    """Personel kural ihlali (Türkçe mesaj)."""


def aktif_personeller():
    """Silinmemiş kartlar (ayrılanlar DAHİL) — depo/cari servislerindeki `aktif_*` adıyla aynı
    anlamda; halen çalışanlar için `calisan_personeller`."""
    return Personel.objects.filter(silindi=False).order_by("ad", "soyad")


def _calisiyor_q(bugun):
    return Q(isten_cikis_tarihi__isnull=True) | Q(isten_cikis_tarihi__gte=bugun)


def calisan_personeller(bugun=None):
    bugun = bugun or tr_bugun()
    return aktif_personeller().filter(_calisiyor_q(bugun))


def aktif_mi(personel, bugun=None) -> bool:
    """Çıkış tarihi boşsa ya da bugün/gelecekteyse (çıkış günü dahil) personel çalışıyordur."""
    bugun = bugun or tr_bugun()
    return personel.isten_cikis_tarihi is None or personel.isten_cikis_tarihi >= bugun


def personel_listele(*, ara="", durum="aktif", departman="", bugun=None):
    bugun = bugun or tr_bugun()
    qs = aktif_personeller()
    if durum == "aktif":
        qs = qs.filter(_calisiyor_q(bugun))
    elif durum == "ayrildi":
        qs = qs.filter(isten_cikis_tarihi__lt=bugun)
    for terim in buyuk_harf_tr((ara or "").strip()).split():
        q = (Q(ad__contains=terim) | Q(soyad__contains=terim)
             | Q(departman__contains=terim) | Q(gorev__contains=terim))
        rakam = re.sub(r"\D", "", terim)
        if rakam:
            q |= Q(telefon__contains=rakam.lstrip("0") or rakam)
        qs = qs.filter(q)
    departman = buyuk_harf_tr((departman or "").strip())
    if departman:
        qs = qs.filter(departman=departman)
    return qs


def departmanlar():
    return list(Personel.objects.filter(silindi=False).exclude(departman="")
                .order_by("departman").values_list("departman", flat=True).distinct())


def gorevler():
    return list(Personel.objects.filter(silindi=False).exclude(gorev="")
                .order_by("gorev").values_list("gorev", flat=True).distinct())


# === Doğrulama / normalizasyon ===

def _buyuk(deger) -> str:
    return buyuk_harf_tr(" ".join((deger or "").split()))


def _telefon(deger) -> str:
    v = (deger or "").strip()
    return telefon_kanonik(v) or v


def _tc_benzersiz(tc, *, haric_pk=None):
    if not tc:
        return
    qs = Personel.objects.filter(silindi=False, tc_kimlik_no=tc)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    mevcut = qs.first()
    if mevcut is not None:
        ek = ("" if aktif_mi(mevcut) else
              " (ayrılmış personel; o kartı düzenleyip çıkış tarihini silin)")
        raise PersonelHatasi(f"Bu TC kimlik no zaten kayıtlı: {mevcut.ad_soyad}{ek}")


def _alanlar(*, ad, soyad, tc_kimlik_no="", dogum_tarihi=None, kan_grubu="", telefon="",
             eposta="", adres="", acil_durum_kisi="", acil_durum_telefon="", departman="",
             gorev="", ise_giris_tarihi=None, isten_cikis_tarihi=None, cikis_nedeni="",
             notlar="", haric_pk=None) -> dict:
    ad, soyad = _buyuk(ad), _buyuk(soyad)
    if not ad:
        raise PersonelHatasi("Ad boş olamaz.")
    if not soyad:
        raise PersonelHatasi("Soyad boş olamaz.")

    tc = "".join((tc_kimlik_no or "").split())
    if tc and not tc_gecerli(tc):
        raise PersonelHatasi(
            "Geçersiz TC Kimlik No: 11 haneli, 0 ile başlamayan ve geçerli kontrol haneli "
            "bir numara olmalı.")
    _tc_benzersiz(tc, haric_pk=haric_pk)

    if dogum_tarihi is not None:
        if dogum_tarihi > tr_bugun():
            raise PersonelHatasi("Doğum tarihi gelecekte olamaz.")
        if dogum_tarihi.year < 1900:
            raise PersonelHatasi("Doğum tarihi geçersiz.")

    kan_grubu = (kan_grubu or "").strip()
    if kan_grubu and kan_grubu not in _KAN_GRUPLARI:
        raise PersonelHatasi("Geçersiz kan grubu.")

    if ise_giris_tarihi is None:
        raise PersonelHatasi("İşe giriş tarihi zorunlu.")
    if isten_cikis_tarihi is not None and isten_cikis_tarihi < ise_giris_tarihi:
        raise PersonelHatasi("İşten çıkış tarihi, işe giriş tarihinden önce olamaz.")

    return {
        "ad": ad, "soyad": soyad, "tc_kimlik_no": tc, "dogum_tarihi": dogum_tarihi,
        "kan_grubu": kan_grubu, "telefon": _telefon(telefon),
        "eposta": (eposta or "").strip().lower(),
        "adres": buyuk_harf_tr((adres or "").strip()),
        "acil_durum_kisi": _buyuk(acil_durum_kisi),
        "acil_durum_telefon": _telefon(acil_durum_telefon),
        "departman": _buyuk(departman), "gorev": _buyuk(gorev),
        "ise_giris_tarihi": ise_giris_tarihi, "isten_cikis_tarihi": isten_cikis_tarihi,
        "cikis_nedeni": (cikis_nedeni or "").strip(), "notlar": (notlar or "").strip(),
    }


# === CRUD ===

def personel_olustur(*, kullanici=None, **kw) -> Personel:
    veri = _alanlar(**kw)
    return Personel.objects.create(created_by=kullanici, updated_by=kullanici, **veri)


def personel_guncelle(personel: Personel, *, kullanici=None, **kw) -> Personel:
    if personel.silindi:
        raise PersonelHatasi("Silinmiş personel kartı düzenlenemez.")
    veri = _alanlar(haric_pk=personel.pk, **kw)
    for alan, deger in veri.items():
        setattr(personel, alan, deger)
    personel.updated_by = kullanici
    personel.save(update_fields=[*veri.keys(), "updated_by", "updated_at"])
    return personel


def personel_sil(personel: Personel, kullanici=None) -> Personel:
    if personel.silindi:
        return personel
    personel.silindi = True
    personel.silindi_at = timezone.now()
    personel.updated_by = kullanici
    personel.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return personel
