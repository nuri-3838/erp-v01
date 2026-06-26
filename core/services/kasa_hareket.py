"""KASA hareket motoru — kasa hareketinden OTOMATİK dengeli yevmiye fişi (Slice 2).

Mimari: her kasa hareketi = bir yevmiye fişi (kaynak=KASA, kaynak kasaya bağlı).
YENİ bakiye/hareket modeli YOK; kasa bakiyesi/ekstresi bağlı muhasebe hesabının
yevmiyesinden gelir. Fiş hem kasanın hem karşı tarafın (cari) hesabını işlediği
için kasa ekstresinde de cari ekstresinde de otomatik görünür.

Slice 2: **Cari Tahsilat** (Kasa borç / Cari alacak). İlk dilim TL; döviz kasa
işlem PB + TCMB kuruyla (fatura `_kur_coz` deseni). Diğer 4 tip Slice 3.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_iptal,
                                   fis_olustur)


class KasaHareketHatasi(ValueError):
    """Kasa hareketi kural ihlali (Türkçe mesaj)."""


def _kur_coz(pb, tarih):
    """Kasanın para biriminin fiş tarihindeki TCMB alış kuru. TRY -> 1.
    Döviz için o tarihin KUR kaydı ve ilgili PB alanı dolu olmalı (carry-forward yok)."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise KasaHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi "
            f"çekmeden döviz kasası hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KasaHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise KasaHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _cari_hesap(cari):
    """Carinin yaprak muhasebe hesabı (HesapPlani) — yoksa hata."""
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise KasaHareketHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return hesap


@transaction.atomic
def cari_tahsilat(*, kasa, cari, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Cari Tahsilat: müşteriden kasaya giriş. **Kasa borç / Cari alacak.**

    Dengeli bir yevmiye fişi üretir (kaynak=KASA, kaynak kasa=`kasa`). Tutar kasanın
    para biriminde; TL değeri fiş tarihinin kuruyla. Kural ihlalinde hiçbir şey
    kaydedilmez (transaction geri alınır)."""
    if kasa.muhasebe_id is None:
        raise KasaHareketHatasi("Kasanın muhasebe hesabı tanımlı değil.")
    cari_hesap = _cari_hesap(cari)
    tut = _tutar(tutar)
    pb = kasa.para_birimi
    kur = _kur_coz(pb, tarih)
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KASA TAHSİLAT - {cari.unvan}"))

    # Satır açıklaması boş: açıklama fiş başlığında ("KASA TAHSİLAT - <cari>" ya da
    # kullanıcının girdiği) tutulur; ekstrede tekrar (cari adı iki kez) olmasın.
    satirlar = [
        SatirGirdi(hesap_kodu=kasa.muhasebe.hesap_kodu, taraf="B",
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf="A",
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
    ]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.KASA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KasaHareketHatasi(str(e))
    fis.kasa = kasa
    fis.save(update_fields=["kasa", "updated_at"])
    return fis


def hareket_iptal(*, fis, kasa, kullanici=None):
    """Kasa hareketi (kaynak=KASA) iptali → bağlı fişi soft-delete eder.
    Fiş bu kasaya ait bir kasa hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KASA or fis.kasa_id != kasa.pk:
        raise KasaHareketHatasi("Bu fiş bu kasanın hareketi değil.")
    if fis.silindi:
        return fis
    return fis_iptal(fis, kullanici=kullanici)
