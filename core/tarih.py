"""Tarih yardımcıları — Türkiye günü (TIME_ZONE=UTC olduğundan timezone.localdate() UTC
tarihini verir; TR saatiyle 00:00-03:00 arası bir önceki gün görünür)."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from django.utils import timezone

_TR = ZoneInfo("Europe/Istanbul")


def tr_bugun():
    """Türkiye saatine göre bugünün tarihi. Form `initial` olarak callable de kullanılır."""
    return timezone.now().astimezone(_TR).date()
