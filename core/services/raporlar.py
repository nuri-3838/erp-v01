"""Rapor servisleri — hepsi YALNIZCA yevmiye satırlarından hesaplanır.

Değişmez (spec 3b): saklanan bakiye YOK; mizan/bilanço/gelir tablosu her zaman
``YevmiyeSatir``'dan türetilir. İptal (soft-delete) fiş/satır hesaba katılmaz.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import YevmiyeSatir

SIFIR = Decimal("0.00")


def mali_yil_araligi(tarih: datetime.date | None = None):
    """İçinde bulunulan mali yıl (takvim yılı) [1 Ocak .. 31 Aralık]."""
    t = tarih or timezone.localdate()
    return datetime.date(t.year, 1, 1), datetime.date(t.year, 12, 31)


@dataclass
class MizanSatir:
    hesap_kodu: str
    hesap_adi: str
    borc: Decimal          # dönem borç hareket toplamı
    alacak: Decimal        # dönem alacak hareket toplamı

    @property
    def borc_bakiye(self) -> Decimal:
        net = self.borc - self.alacak
        return net if net > 0 else SIFIR

    @property
    def alacak_bakiye(self) -> Decimal:
        net = self.alacak - self.borc
        return net if net > 0 else SIFIR


@dataclass
class Mizan:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list = field(default_factory=list)

    @property
    def toplam_borc(self) -> Decimal:
        return sum((s.borc for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak(self) -> Decimal:
        return sum((s.alacak for s in self.satirlar), SIFIR)

    @property
    def toplam_borc_bakiye(self) -> Decimal:
        return sum((s.borc_bakiye for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak_bakiye(self) -> Decimal:
        return sum((s.alacak_bakiye for s in self.satirlar), SIFIR)

    @property
    def hareket_denk(self) -> bool:
        """SUM(borç) = SUM(alacak) — mizan tutuyor mu?"""
        return self.toplam_borc == self.toplam_alacak

    @property
    def bakiye_denk(self) -> bool:
        return self.toplam_borc_bakiye == self.toplam_alacak_bakiye


def mizan(baslangic: datetime.date | None = None,
          bitis: datetime.date | None = None) -> Mizan:
    """Tarih aralığındaki (varsayılan: içinde bulunulan mali yıl) mizanı üretir.

    Yalnızca hareket gören hesaplar listelenir; iptal edilmiş fiş/satır hariç.
    """
    vb, vs = mali_yil_araligi()
    if baslangic is None:
        baslangic = vb
    if bitis is None:
        bitis = vs

    qs = (
        YevmiyeSatir.objects.filter(
            silindi=False,
            fis__silindi=False,
            fis__tarih__gte=baslangic,
            fis__tarih__lte=bitis,
        )
        .values("hesap_id", "hesap__hesap_adi")
        .annotate(borc=Sum("borc"), alacak=Sum("alacak"))
        .order_by("hesap_id")
    )
    satirlar = [
        MizanSatir(
            hesap_kodu=r["hesap_id"],
            hesap_adi=r["hesap__hesap_adi"],
            borc=r["borc"] or SIFIR,
            alacak=r["alacak"] or SIFIR,
        )
        for r in qs
    ]
    return Mizan(baslangic=baslangic, bitis=bitis, satirlar=satirlar)
