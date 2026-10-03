"""Stok maliyet DURUMU okuma yardımcısı.

Maliyet motoru artık HAREKETLİ AĞIRLIKLI ORTALAMA'dır (core.services.stok_ortalama). Eski FIFO
katman/tüketim tabloları (StokMaliyetKatmani/StokMaliyetTuketimi) emekli edildi: hiçbir yeni
kayıt yazılmaz; tablolar geçmiş veri olarak durur (core.models'te tanımlı)."""
from __future__ import annotations

from decimal import Decimal

SIFIR = Decimal("0.000")


def hareket_maliyet_durumu(stok_hareket) -> dict:
    """Hareket için: {'tutar_try', 'karsilanan_miktar', 'tam_mi', 'tahmini'} — ağırlıklı ortalama
    motorunun yazdığı alanlardan okunur."""
    durum = stok_hareket.maliyet_durumu
    tam_mi = durum != stok_hareket.MaliyetDurumu.YOK
    return {"tutar_try": stok_hareket.tutar_try if tam_mi else None,
            "karsilanan_miktar": stok_hareket.miktar if tam_mi else SIFIR,
            "tam_mi": tam_mi, "tahmini": durum != stok_hareket.MaliyetDurumu.KESIN}
