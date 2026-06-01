"""Stok/ürün kartı (STOKLAR — master) servis katmanı. Kurallar tek noktada.

- Kod OTOMATİK: ``ÜST.kod-ALT.kod-NNNN`` (örn. 150-10-0001); sıra her ALT kategori
  içinde ayrı ilerler. Elle girilmez/değiştirilmez. Silinen numara tekrar kullanılmaz
  (o kategorideki en büyük sıranın +1'i alınır).
- Kart yalnız ALT (yaprak değil — "alt") kategoriye bağlanır (ust dolu). Üst kategoriye
  stok açılamaz. Kategori ve kod oluşturmadan sonra DEĞİŞMEZ.
- Üretim ve fatura birimi farklı olabilir; ``cevirici`` = 1 üretim birimi kaç fatura
  birimi eder (> 0). KDV oranı ≥ 0.
- Silme: soft-delete (iz kalır).
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Birim, Kategori, Stok


class StokHatasi(ValueError):
    """Stok kural ihlali (Türkçe mesaj)."""


def aktif_stoklar():
    """Silinmemiş stoklar (kod sırasıyla); kategori + birimler birlikte çekilir."""
    return (Stok.objects.filter(silindi=False)
            .select_related("kategori", "kategori__ust", "uretim_birimi", "fatura_birimi")
            .order_by("kod"))


def sonraki_stok_kodu(kategori: Kategori) -> str:
    """Bir ALT kategori için sıradaki stok kodunu üretir: ÜST.kod-ALT.kod-NNNN."""
    if kategori.ust_id is None:
        raise StokHatasi("Stok yalnız ALT kategoriye açılabilir (üst kategori değil).")
    onek = f"{kategori.ust.kod}-{kategori.kod}-"
    son = 0
    for k in Stok.objects.filter(kategori=kategori).values_list("kod", flat=True):
        parca = k.rsplit("-", 1)[-1]
        if parca.isdigit():
            son = max(son, int(parca))
    return f"{onek}{str(son + 1).zfill(4)}"


def _ad_dogrula(ad):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise StokHatasi("Stok adı boş olamaz.")
    return ad


def _birim_coz(birim_id, etiket):
    b = Birim.objects.filter(pk=birim_id, silindi=False).first()
    if b is None:
        raise StokHatasi(f"{etiket} bulunamadı.")
    return b


def _cevirici_dogrula(deger):
    try:
        c = Decimal(deger)
    except (TypeError, ValueError, ArithmeticError):
        raise StokHatasi("Çevirici geçerli bir sayı olmalı.")
    if c <= 0:
        raise StokHatasi("Çevirici sıfırdan büyük olmalı.")
    return c


def _kdv_dogrula(deger):
    try:
        k = Decimal(deger if deger is not None else 0)
    except (TypeError, ValueError, ArithmeticError):
        raise StokHatasi("KDV oranı geçerli bir sayı olmalı.")
    if k < 0:
        raise StokHatasi("KDV oranı negatif olamaz.")
    return k


def stok_olustur(*, ad, kategori_id, uretim_birimi_id, fatura_birimi_id,
                 cevirici=Decimal("1"), kdv_orani=Decimal("0"), kullanici=None) -> Stok:
    ad = _ad_dogrula(ad)
    kategori = Kategori.objects.filter(pk=kategori_id, silindi=False).first()
    if kategori is None:
        raise StokHatasi("Kategori bulunamadı.")
    if kategori.ust_id is None:
        raise StokHatasi("Stok yalnız ALT kategoriye açılabilir (üst kategori değil).")
    uretim = _birim_coz(uretim_birimi_id, "Üretim birimi")
    fatura = _birim_coz(fatura_birimi_id, "Fatura birimi")
    c = _cevirici_dogrula(cevirici)
    kdv = _kdv_dogrula(kdv_orani)
    return Stok.objects.create(
        kod=sonraki_stok_kodu(kategori), ad=ad, kategori=kategori,
        uretim_birimi=uretim, fatura_birimi=fatura, cevirici=c, kdv_orani=kdv,
        created_by=kullanici, updated_by=kullanici,
    )


def stok_guncelle(stok: Stok, *, ad, uretim_birimi_id, fatura_birimi_id,
                  cevirici, kdv_orani, kullanici=None) -> Stok:
    """Ad, birimler, çevirici, KDV güncellenir. KOD ve KATEGORİ DEĞİŞMEZ."""
    if stok.silindi:
        raise StokHatasi("Silinmiş stok düzenlenemez.")
    stok.ad = _ad_dogrula(ad)
    stok.uretim_birimi = _birim_coz(uretim_birimi_id, "Üretim birimi")
    stok.fatura_birimi = _birim_coz(fatura_birimi_id, "Fatura birimi")
    stok.cevirici = _cevirici_dogrula(cevirici)
    stok.kdv_orani = _kdv_dogrula(kdv_orani)
    stok.updated_by = kullanici
    stok.save(update_fields=["ad", "uretim_birimi", "fatura_birimi", "cevirici",
                             "kdv_orani", "updated_by", "updated_at"])
    return stok


def stok_sil(stok: Stok, kullanici=None) -> Stok:
    """Soft-delete (iz kalır)."""
    if stok.silindi:
        return stok
    stok.silindi = True
    stok.silindi_at = timezone.now()
    stok.updated_by = kullanici
    stok.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return stok
