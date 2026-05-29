"""Yevmiye servis katmanı — fiş oluşturma/iptal kuralları tek noktada (spec 3).

Tüm değişmezler burada zorlanır; UI'ya güvenilmez:
- Fişte en az 2 satır.
- Her satırda borç/alacaktan yalnızca biri pozitif (diğeri 0).
- Tutarlar ≥ 0; islem_kuru > 0; TRY ise islem_kuru = 1.
- Hesap var, aktif ve silinmemiş olmalı.
- TL (borç/alacak) = yuvarla(islem_tutari × islem_kuru); TRY'de TL = islem_tutari.
- Dengeli fiş: SUM(borç) = SUM(alacak), aksi halde fiş kaydedilmez (atomik).
- fis_no mali yıl içinde müteselsil/boşluksuz; iptal numarayı korur (yeniden
  kullanılmaz). kur_usd fiş tarihine göre KUR'dan doldurulur (yoksa son yayımlanan;
  o da yoksa boş bırakılır, fiş yine kaydedilir), elle override edilebilir.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.sayi import parse_tr, yuvarla

SIFIR = Decimal("0.00")


class YevmiyeHatasi(ValueError):
    """Geçersiz fiş/satır verisi (kural ihlali)."""


@dataclass
class SatirGirdi:
    """Tek satır girdisi. TL tutarı bu veriden TÜRETİLİR (dışarıdan verilmez)."""

    hesap_kodu: str
    taraf: str  # "B" = borç, "A" = alacak
    islem_tutari: object  # Decimal | int | str (TR biçim) — float YASAK
    islem_pb: str = "TRY"
    islem_kuru: object = Decimal("1")
    aciklama: str = ""


def _dec(deger, alan: str) -> Decimal:
    """Decimal/int/str(TR) → Decimal. float kabul edilmez (kayan nokta yasak)."""
    if isinstance(deger, Decimal):
        return deger
    if isinstance(deger, int) and not isinstance(deger, bool):
        return Decimal(deger)
    if isinstance(deger, str):
        return parse_tr(deger)
    raise YevmiyeHatasi(f"{alan}: sayı Decimal/int/metin olmalı, gelen {type(deger).__name__}")


def kur_usd_bul(tarih: _dt.date):
    """Fiş tarihine göre USD alış kuru: o tarih yoksa son yayımlanan (≤ tarih)."""
    k = (
        Kur.objects.filter(tarih__lte=tarih, silindi=False)
        .order_by("-tarih")
        .first()
    )
    return k.usd_alis if k else None


@transaction.atomic
def fis_olustur(*, tarih, satirlar, aciklama="", kur_usd=None,
                kaynak=YevmiyeFisi.Kaynak.MANUEL, kullanici=None) -> YevmiyeFisi:
    """Dengeli bir yevmiye fişini satırlarıyla atomik oluşturur.

    `satirlar`: `SatirGirdi` listesi. Kurallardan biri ihlal edilirse hiçbir şey
    yazılmaz (transaction geri alınır) ve :class:`YevmiyeHatasi` yükselir.
    """
    if not isinstance(tarih, _dt.date):
        raise YevmiyeHatasi("tarih bir date olmalı.")
    if len(satirlar) < 2:
        raise YevmiyeHatasi("Fişte en az 2 satır olmalı.")

    hazir = []
    toplam_borc = SIFIR
    toplam_alacak = SIFIR

    for i, g in enumerate(satirlar, start=1):
        taraf = (g.taraf or "").strip().upper()
        if taraf not in ("B", "A"):
            raise YevmiyeHatasi(f"Satır {i}: taraf 'B' veya 'A' olmalı.")

        pb = (g.islem_pb or "TRY").strip().upper()
        if pb not in YevmiyeSatir.IslemPB.values:
            raise YevmiyeHatasi(f"Satır {i}: geçersiz işlem PB {pb!r}.")

        tutar = _dec(g.islem_tutari, f"Satır {i} islem_tutari")
        kur = _dec(g.islem_kuru, f"Satır {i} islem_kuru")
        if tutar < 0:
            raise YevmiyeHatasi(f"Satır {i}: tutar negatif olamaz.")
        if kur <= 0:
            raise YevmiyeHatasi(f"Satır {i}: islem_kuru > 0 olmalı.")
        if pb == "TRY" and kur != Decimal("1"):
            raise YevmiyeHatasi(f"Satır {i}: TRY için islem_kuru 1 olmalı.")

        tl = yuvarla(tutar * kur, 2)
        if tl <= 0:
            raise YevmiyeHatasi(f"Satır {i}: TL tutarı 0'dan büyük olmalı.")

        hesap = (
            HesapPlani.objects.filter(
                hesap_kodu=(g.hesap_kodu or "").strip(), aktif=True, silindi=False
            ).first()
        )
        if hesap is None:
            raise YevmiyeHatasi(
                f"Satır {i}: hesap bulunamadı/aktif değil: {g.hesap_kodu!r}"
            )

        if taraf == "B":
            borc, alacak = tl, SIFIR
        else:
            borc, alacak = SIFIR, tl
        toplam_borc += borc
        toplam_alacak += alacak

        hazir.append(
            dict(hesap=hesap, borc=borc, alacak=alacak, islem_pb=pb,
                 islem_tutari=yuvarla(tutar, 2), islem_kuru=kur,
                 aciklama=buyuk_harf_tr((g.aciklama or "").strip()))
        )

    if yuvarla(toplam_borc, 2) != yuvarla(toplam_alacak, 2):
        raise YevmiyeHatasi(
            f"Fiş dengesiz: borç {toplam_borc} ≠ alacak {toplam_alacak}."
        )

    yil = tarih.year
    # Mali yıl içinde müteselsil; iptal edilenler dahil (numara korunur, yeniden
    # kullanılmaz). Tek kullanıcılı v0.1'de select_for_update + unique kısıt yeterli.
    son = (
        YevmiyeFisi.objects.select_for_update()
        .filter(yil=yil)
        .aggregate(m=Max("fis_no"))["m"]
        or 0
    )
    fis_no = son + 1

    if kur_usd is None:
        kur_usd = kur_usd_bul(tarih)
    elif not isinstance(kur_usd, Decimal):
        kur_usd = _dec(kur_usd, "kur_usd")

    fis = YevmiyeFisi.objects.create(
        yil=yil, fis_no=fis_no, tarih=tarih,
        aciklama=buyuk_harf_tr((aciklama or "").strip()),
        kaynak=kaynak, kur_usd=kur_usd,
        created_by=kullanici, updated_by=kullanici,
    )
    for s in hazir:
        YevmiyeSatir.objects.create(
            fis=fis, created_by=kullanici, updated_by=kullanici, **s
        )
    return fis


@transaction.atomic
def fis_iptal(fis: YevmiyeFisi, kullanici=None) -> YevmiyeFisi:
    """Fişi soft-delete ile iptal eder. Numara korunur (yeniden kullanılmaz)."""
    if fis.silindi:
        return fis
    fis.silindi = True
    fis.silindi_at = timezone.now()
    fis.updated_by = kullanici
    fis.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    fis.satirlar.update(silindi=True, silindi_at=timezone.now())
    return fis
