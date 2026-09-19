"""Tarih yardımcıları — Türkiye günü (TIME_ZONE=UTC olduğundan timezone.localdate() UTC
tarihini verir; TR saatiyle 00:00-03:00 arası bir önceki gün görünür) + yıl dönümü/kıdem
hesapları (python-dateutil kurulu DEĞİL; saf Python)."""
from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from django.utils import timezone

_TR = ZoneInfo("Europe/Istanbul")


def tr_bugun():
    """Türkiye saatine göre bugünün tarihi. Form `initial` olarak callable de kullanılır."""
    return timezone.now().astimezone(_TR).date()


def yil_donumu(d: date, n: int) -> date:
    """d tarihinin n. yıl dönümü. 29 Şubat, artık olmayan yılda 28 Şubat'a düşer."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return date(d.year + n, 2, 28)


def tamamlanan_yil(baslangic: date, tarih: date) -> int:
    """baslangic'tan tarih'e tamamlanan tam yıl sayısı (en büyük k: yil_donumu(baslangic,k)
    <= tarih). Yaş ve kıdem hesabının TEK kuralı; tarih < baslangic ise 0."""
    if tarih < baslangic:
        return 0
    k = tarih.year - baslangic.year
    while k > 0 and yil_donumu(baslangic, k) > tarih:
        k -= 1
    return k


def kidem_metni(giris: date, tarih: date) -> str:
    """'3 yıl 4 ay' / '8 ay' / '1 aydan az' — giriş tarihinden tarih'e geçen süre."""
    if tarih < giris:
        return "Henüz başlamadı"
    yil = tamamlanan_yil(giris, tarih)
    son = yil_donumu(giris, yil)
    ay = (tarih.year - son.year) * 12 + tarih.month - son.month
    if tarih.day < son.day:
        ay -= 1
    ay = max(ay, 0)
    parcalar = []
    if yil:
        parcalar.append(f"{yil} yıl")
    if ay:
        parcalar.append(f"{ay} ay")
    return " ".join(parcalar) or "1 aydan az"
