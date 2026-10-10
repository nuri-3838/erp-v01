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


def tum_depolar():
    """Depo yönetim listesi: pasifler dahil, silinmemiş tüm depolar."""
    return Depo.objects.filter(silindi=False).select_related("fason_cari").order_by("kod")


def aktif_depolar():
    """Seçim listeleri / yeni kayıtlar: yalnız AKTİF depolar (pasifler çıkmaz)."""
    return tum_depolar().filter(aktif=True)


VARSAYILAN_DEPO_ADI = "SEMTA DEPO"


def varsayilan_depo(depolar=None):
    """Yeni kayıtlarda ön-seçili depo: SEMTA DEPO (aktifse), yoksa eski ANA DEPO, yoksa listedeki ilk fason-dışı depo, o da yoksa ilk depo."""
    depolar = aktif_depolar() if depolar is None else depolar
    return (depolar.filter(ad=VARSAYILAN_DEPO_ADI).first() or depolar.filter(ad="ANA DEPO").first()
            or depolar.filter(fason_cari__isnull=True).first() or depolar.first())


def depo_eldeki_stoklar(depo) -> dict:
    """{stok id: eldeki miktar} — yalnız eldeki > 0 olanlar (silinmemiş hareketlerden; hizmet kartı hareketi olmaz)."""
    from collections import defaultdict
    from django.db.models import Sum
    from core.models import StokHareket
    toplam = defaultdict(lambda: 0)
    for r in StokHareket.objects.filter(depo=depo, silindi=False).values("stok_id", "tur").annotate(s=Sum("miktar")):
        toplam[r["stok_id"]] += r["s"] if r["tur"] == StokHareket.Tur.GIRIS else -r["s"]
    return {k: v for k, v in toplam.items() if v > 0}


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


def depo_olustur(*, kod, ad, fason_cari=None, aktif=True, kullanici=None) -> Depo:
    kod, ad = _dogrula(kod, ad)
    cari = _fason_cari_coz(fason_cari)
    return Depo.objects.create(kod=kod, ad=ad, fason_cari=cari, aktif=bool(aktif), created_by=kullanici, updated_by=kullanici)


def depo_guncelle(depo: Depo, *, kod, ad, fason_cari=None, aktif=None, kullanici=None) -> Depo:
    """``aktif`` None = değişmez. Aktif depo pasif yapılırken eldeki stok kalmamış olmalı (önce depo transferiyle taşınır)."""
    if depo.silindi:
        raise DepoHatasi("Silinmiş depo düzenlenemez.")
    kod, ad = _dogrula(kod, ad, haric_pk=depo.pk)
    cari = _fason_cari_coz(fason_cari, haric_pk=depo.pk)
    if aktif is not None and depo.aktif and not aktif and depo_eldeki_stoklar(depo):
        raise DepoHatasi(f"{depo.kod} deposunda eldeki stok var; pasif yapmadan önce stoğu başka depoya taşıyın.")
    depo.kod, depo.ad, depo.fason_cari = kod, ad, cari
    if aktif is not None:
        depo.aktif = bool(aktif)
    depo.updated_by = kullanici
    depo.save(update_fields=["kod", "ad", "fason_cari", "aktif", "updated_by", "updated_at"])
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
