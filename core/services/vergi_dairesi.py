"""Vergi dairesi tanımları (CARİLER, 2026-10-13; GİB resmî listesi 2026-10-15) — cari formundaki aranabilir liste. Ad TR büyük harf; (ad, il) silinmemişler arası benzersiz;
``kod`` (GİB listesindeki 5 haneli muhasebe birimi / vergi dairesi kodu) doluysa silinmemişler arası benzersiz.
Cari kartında yine yalnız ``ad`` metni saklanır (``Cari.vergi_dairesi``); tanım silinse/pasife alınsa bile kartlardaki değer değişmez. Resmî listeyi yükleyen komut:
``manage.py vergi_daireleri_yukle`` (docs/vergi_daireleri_gib.csv)."""
from __future__ import annotations

import re

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import VergiDairesi


class VergiDairesiHatasi(ValueError):
    """Vergi dairesi kural ihlali (Türkçe mesaj)."""


def etiket(v) -> str:
    """Seçim listesi / tablo etiketi: 'İL – AD (KOD)'. Ad zaten '(İL)' ile bitiyorsa (aynı kısa adın birden fazla ilde bulunduğu durum) tekrar yazılmaz; il/kod boşsa atlanır."""
    ad = v.ad
    if v.il and ad.endswith(f" ({v.il})"):
        ad = ad[: -len(f" ({v.il})")]
    metin = f"{v.il} – {ad}" if v.il else ad
    return f"{metin} ({v.kod})" if v.kod else metin


def _sira(v):
    return (not v.il, v.il, v.ad)                      # il bilgisi olmayan (eski/elle) kayıtlar sonda


def aktif_vergi_daireleri() -> list:
    """Cari formunda seçilebilen (silinmemiş + aktif) daireler: il, ad sırasıyla."""
    return sorted(VergiDairesi.objects.filter(silindi=False, aktif=True), key=_sira)


def tum_vergi_daireleri():
    """Tanımlar ekranı: silinmemiş tüm kayıtlar (pasifler dahil)."""
    return VergiDairesi.objects.filter(silindi=False).order_by("il", "ad")


def secenekler(mevcut_deger: str = "") -> list:
    """Cari formu <select> seçenekleri [(değer=ad, etiket)]; ``mevcut_deger`` (kartta kayıtlı ama aktif listede olmayan) 'listede yok' olarak korunur. Değerler tekildir."""
    sonuc, goruldu = [("", "— vergi dairesi seç —")], set()
    for v in aktif_vergi_daireleri():
        if v.ad not in goruldu:
            goruldu.add(v.ad)
            sonuc.append((v.ad, etiket(v)))
    mevcut = (mevcut_deger or "").strip()
    if mevcut and mevcut not in goruldu:
        sonuc.append((mevcut, f"{mevcut} (listede yok)"))
    return sonuc


def _dogrula(ad, il, kod="", haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    il = buyuk_harf_tr((il or "").strip())
    kod = (kod or "").strip()
    if not ad:
        raise VergiDairesiHatasi("Vergi dairesi adı boş olamaz.")
    if kod and not re.fullmatch(r"\d{5}", kod):
        raise VergiDairesiHatasi("GİB kodu 5 haneli rakam olmalı (ör. 34224).")
    cak = VergiDairesi.objects.filter(silindi=False, ad=ad, il=il)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise VergiDairesiHatasi(f"Bu vergi dairesi zaten kayıtlı: {ad}" + (f" ({il})" if il else ""))
    if kod:
        kk = VergiDairesi.objects.filter(silindi=False, kod=kod)
        if haric_pk is not None:
            kk = kk.exclude(pk=haric_pk)
        if kk.exists():
            raise VergiDairesiHatasi(f"Bu GİB kodu zaten kayıtlı: {kod}")
    return ad, il, kod


def vergi_dairesi_olustur(*, ad, il="", kod="", aktif=True, kullanici=None) -> VergiDairesi:
    ad, il, kod = _dogrula(ad, il, kod)
    return VergiDairesi.objects.create(ad=ad, il=il, kod=kod, aktif=bool(aktif), created_by=kullanici, updated_by=kullanici)


def vergi_dairesi_guncelle(vd: VergiDairesi, *, ad, il="", kod="", aktif=True, kullanici=None) -> VergiDairesi:
    if vd.silindi:
        raise VergiDairesiHatasi("Silinmiş vergi dairesi düzenlenemez.")
    vd.ad, vd.il, vd.kod = _dogrula(ad, il, kod, haric_pk=vd.pk)
    vd.aktif = bool(aktif)
    vd.updated_by = kullanici
    vd.save(update_fields=["ad", "il", "kod", "aktif", "updated_by", "updated_at"])
    return vd


def vergi_dairesi_sil(vd: VergiDairesi, kullanici=None) -> VergiDairesi:
    if vd.silindi:
        return vd
    vd.silindi = True
    vd.silindi_at = timezone.now()
    vd.updated_by = kullanici
    vd.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return vd
