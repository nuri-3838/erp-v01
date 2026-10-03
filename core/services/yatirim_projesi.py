"""Yatırım Projesi (DURAN VARLIK FAZ 1) servis katmanı.

258 (Yapılmakta Olan Yatırımlar) hesabında biriken alış gider faturası kalemlerinin
gruplandığı proje kartı — CRUD + toplam hesaplama. Aktifleştirme (FAZ 3: tek fişle
253/254/255/260'a aktarım + DuranVarlik kartı üretimi) burada henüz YOK.
"""
from __future__ import annotations

from decimal import Decimal

from core.metin import buyuk_harf_tr
from core.models import YatirimProjesi

SIFIR = Decimal("0.00")


class YatirimProjesiHatasi(ValueError):
    """Yatırım projesi kural ihlali (Türkçe mesaj)."""


def aktif_projeler():
    return YatirimProjesi.objects.filter(silindi=False).order_by("-created_at")


def sonraki_proje_kodu() -> str:
    n = 1
    while YatirimProjesi.objects.filter(kod=f"YP-{str(n).zfill(4)}", silindi=False).exists():
        n += 1
    return f"YP-{str(n).zfill(4)}"


def proje_olustur(*, ad, aciklama="", kullanici=None) -> YatirimProjesi:
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise YatirimProjesiHatasi("Proje adı boş olamaz.")
    return YatirimProjesi.objects.create(
        kod=sonraki_proje_kodu(), ad=ad, aciklama=(aciklama or "").strip(),
        created_by=kullanici, updated_by=kullanici)


def proje_toplami(proje: YatirimProjesi) -> Decimal:
    """Projeye bağlı tüm (silinmemiş faturadaki silinmemiş) kalemlerin KDV HARİÇ toplamı."""
    toplam = SIFIR
    for s in proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False):
        toplam += s.tutar
    return toplam


def proje_fatura_sayisi(proje: YatirimProjesi) -> int:
    return (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
            .values("fatura_id").distinct().count())
