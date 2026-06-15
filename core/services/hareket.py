"""Stok hareketi (STOKLAR Faz B) servis katmanı — miktar defteri.

Eldeki miktar SAKLANMAZ; hareketlerden hesaplanır (Σgiriş − Σçıkış). Muhasebeden
bağımsız (TL tarafını fatura işler). Çıkış, o stok+depo eldeki miktarından fazla olamaz.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.metin import buyuk_harf_tr
from core.models import Depo, Stok, StokHareket
from core.sayi import SayiHatasi, parse_tr

SIFIR = Decimal("0.000")


class HareketHatasi(ValueError):
    """Stok hareketi kural ihlali (Türkçe mesaj)."""


def eldeki_miktar(stok, depo=None) -> Decimal:
    """Stok (opsiyonel depo) için eldeki miktar = Σgiriş − Σçıkış (silinmemiş)."""
    qs = StokHareket.objects.filter(stok=stok, silindi=False)
    if depo is not None:
        qs = qs.filter(depo=depo)
    g = qs.filter(tur=StokHareket.Tur.GIRIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
    c = qs.filter(tur=StokHareket.Tur.CIKIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
    return g - c


def depo_bazinda_eldeki(stok):
    """[(depo, miktar)] — stoğun hareket gördüğü depolar bazında eldeki (≠0 dahil hepsi)."""
    depo_ids = (StokHareket.objects.filter(stok=stok, silindi=False)
                .values_list("depo_id", flat=True).distinct())
    sonuc = []
    for d in Depo.objects.filter(pk__in=list(depo_ids), silindi=False).order_by("kod"):
        sonuc.append((d, eldeki_miktar(stok, d)))
    return sonuc


def stok_hareketleri(stok):
    return (StokHareket.objects.filter(stok=stok, silindi=False)
            .select_related("depo").order_by("-tarih", "-id"))


@transaction.atomic
def hareket_ekle(*, stok_id, depo_id, tarih, tur, miktar, aciklama="",
                 kaynak=StokHareket.Kaynak.MANUEL, fatura_satir=None,
                 kullanici=None) -> StokHareket:
    stok = Stok.objects.filter(pk=stok_id, silindi=False).first()
    if stok is None:
        raise HareketHatasi("Stok bulunamadı.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise HareketHatasi("Depo bulunamadı.")
    if tur not in StokHareket.Tur.values:
        raise HareketHatasi("Hareket türü Giriş veya Çıkış olmalı.")
    try:
        m = parse_tr(miktar)
    except SayiHatasi:
        raise HareketHatasi("Miktar geçerli bir sayı olmalı.")
    if m <= 0:
        raise HareketHatasi("Miktar sıfırdan büyük olmalı.")
    if tur == StokHareket.Tur.CIKIS:
        mevcut = eldeki_miktar(stok, depo)
        if m > mevcut:
            raise HareketHatasi(
                f"Yetersiz stok: {depo.kod} deposunda {stok.kod} için eldeki {mevcut}, "
                f"çıkış {m} olamaz.")
    return StokHareket.objects.create(
        stok=stok, depo=depo, tarih=tarih, tur=tur, miktar=m,
        aciklama=buyuk_harf_tr((aciklama or "").strip()), kaynak=kaynak,
        fatura_satir=fatura_satir, created_by=kullanici, updated_by=kullanici)


def hareket_sil(hareket: StokHareket, kullanici=None) -> StokHareket:
    """Soft-delete. Giriş silinince eldeki azalır; negatife düşürmemeli."""
    from django.utils import timezone
    if hareket.silindi:
        return hareket
    if hareket.tur == StokHareket.Tur.GIRIS:
        # Bu girişi geri alınca eldeki negatif olur mu?
        if eldeki_miktar(hareket.stok, hareket.depo) - hareket.miktar < 0:
            raise HareketHatasi(
                "Bu giriş silinemez: depodaki eldeki miktar negatife düşer (önce çıkışları düzeltin).")
    hareket.silindi = True
    hareket.silindi_at = timezone.now()
    hareket.updated_by = kullanici
    hareket.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return hareket
