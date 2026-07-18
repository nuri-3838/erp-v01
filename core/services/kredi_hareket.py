"""KREDİ hareket motoru — her hareket = bir DENGELİ yevmiye fişi (kaynak=KREDI).

Kredi bir YÜKÜMLÜLÜK. Kullandırım: anapara nakit hesaba girer → Banka/Kasa borç / Kredi alacak
(borç doğar). Geri ödeme (Dilim 2): Kredi borç(anapara) + Faiz gideri borç(faiz) / nakit alacak.
Fiş kredinin para biriminde tek para; Banka/Kasa PB'si kredi ile aynı olmalı (çapraz kur kapsam dışı).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import BankaHesap, Kasa, Kredi, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_iptal, fis_olustur


class KrediHareketHatasi(ValueError):
    """Kredi hareketi kural ihlali (Türkçe mesaj)."""


# kredi = kredi satırının fiş tarafı. Kullandırım → kredi ALACAK (borç doğar).
HAREKET = {
    "kullandirim": {"ad": "Kullandırım", "kredi": "A", "karsi": ("banka", "kasa"), "yon": "artis",
                    "ikon": "💰", "ack": "KULLANDIRIM",
                    "aciklama": "Kredi kullanımı — anapara nakit hesaba girer (Banka·Kasa borç / "
                                "Kredi alacak). Borç doğar."},
}


def _kur_coz(pb, tarih):
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise KrediHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekmeden döviz kredi "
            f"hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KrediHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise KrediHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _nakit_coz(karsi, kredi):
    """Banka hesabı VEYA Kasa'dan muhasebe hesabı kodu + ad; PB kredi ile aynı olmalı."""
    if isinstance(karsi, BankaHesap):
        if karsi.muhasebe_id is None:
            raise KrediHareketHatasi("Banka hesabının muhasebe hesabı tanımlı değil.")
        if karsi.para_birimi != kredi.para_birimi:
            raise KrediHareketHatasi(
                f"Banka hesabı ({karsi.para_birimi}) ile kredi ({kredi.para_birimi}) para birimi "
                f"farklı; çapraz kurlu işlem desteklenmiyor.")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if isinstance(karsi, Kasa):
        if karsi.muhasebe_id is None:
            raise KrediHareketHatasi("Kasanın muhasebe hesabı tanımlı değil.")
        if karsi.para_birimi != kredi.para_birimi:
            raise KrediHareketHatasi(
                f"Kasa ({karsi.para_birimi}) ile kredi ({kredi.para_birimi}) para birimi farklı; "
                f"çapraz kurlu işlem desteklenmiyor.")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise KrediHareketHatasi("Nakit hesabı Banka VEYA Kasa olmalı.")


@transaction.atomic
def hareket_olustur(*, kredi, tip, karsi, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Bir kredi hareketinden otomatik DENGELİ yevmiye fişi üretir (kaynak=KREDI, fiş→kredi FK).
    Dilim 1: kullandırım (kredi alacak / nakit borç). Kural ihlalinde hiçbir şey kaydedilmez."""
    if tip not in HAREKET:
        raise KrediHareketHatasi("Geçersiz hareket tipi.")
    if kredi.muhasebe_id is None:
        raise KrediHareketHatasi("Kredinin muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    karsi_kod, karsi_ad = _nakit_coz(karsi, kredi)
    tut = _tutar(tutar)
    pb = kredi.para_birimi
    kur = _kur_coz(pb, tarih)
    kredi_taraf = tan["kredi"]
    karsi_taraf = "A" if kredi_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KREDİ {tan['ack']} - {karsi_ad}"))
    satirlar = [
        SatirGirdi(hesap_kodu=kredi.muhasebe.hesap_kodu, taraf=kredi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
    ]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.KREDI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KrediHareketHatasi(str(e))
    fis.kredi = kredi
    fis.save(update_fields=["kredi", "updated_at"])
    return fis


def hareket_iptal(*, fis, kredi, kullanici=None):
    """Kredi hareketi (kaynak=KREDI) iptali → bağlı fişi soft-delete eder.
    Fiş bu kredinin bir hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KREDI or fis.kredi_id != kredi.pk:
        raise KrediHareketHatasi("Bu fiş bu kredinin hareketi değil.")
    if fis.silindi:
        return fis
    return fis_iptal(fis, kullanici=kullanici)
