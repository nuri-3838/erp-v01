"""KASA hareket motoru — kasa hareketinden OTOMATİK dengeli yevmiye fişi.

Mimari: her kasa hareketi = bir yevmiye fişi (kaynak=KASA, kaynak kasaya bağlı).
YENİ bakiye/hareket modeli YOK; bakiye/ekstre bağlı muhasebe hesabının
yevmiyesinden gelir. Fiş hem kasanın hem karşı tarafın hesabını işlediği için
ilgili tüm ekstrelerde otomatik görünür.

5 hareket tipi — kasa perspektifi (yönler KİLİTLİ):
  cari_tahsilat  Kasa B / Cari A        (giriş, karşı=Cari)
  cari_odeme     Kasa A / Cari B         (çıkış, karşı=Cari)
  banka_yatan    Kasa A / Banka B        (çıkış, karşı=BankaHesap)  kasadan bankaya
  banka_cekilen  Kasa B / Banka A        (giriş, karşı=BankaHesap)  bankadan kasaya
  kasa_virman    Kaynak Kasa A / Hedef Kasa B  (transfer, karşı=Kasa)

İlk dilim: kasanın para birimi + TCMB kuru (fatura `_kur_coz` deseni). Banka/virman
karşı tarafı kasayla AYNI para biriminde olmalı (çapraz kur sonraki dilim).
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


# tip -> davranış. kasa: kasanın tarafı (B=giriş/A=çıkış); karsi: karşı taraf tipi;
# yon: gösterim (giris/cikis/notr); ikon + ack (fiş açıklaması ön eki) + aciklama (form ipucu).
HAREKET = {
    "cari_tahsilat": {"ad": "Cari Tahsilat", "kasa": "B", "karsi": "cari", "yon": "giris",
                      "ikon": "📥", "ack": "TAHSİLAT",
                      "aciklama": "Müşteriden kasaya giriş — Kasa borç / Cari alacak."},
    "cari_odeme": {"ad": "Cari Ödeme", "kasa": "A", "karsi": "cari", "yon": "cikis",
                   "ikon": "📤", "ack": "ÖDEME",
                   "aciklama": "Tedarikçiye kasadan ödeme — Cari borç / Kasa alacak."},
    "banka_yatan": {"ad": "Banka Yatan", "kasa": "A", "karsi": "banka", "yon": "cikis",
                    "ikon": "🏦", "ack": "BANKAYA YATAN",
                    "aciklama": "Kasadan bankaya — Banka borç / Kasa alacak."},
    "banka_cekilen": {"ad": "Banka Çekilen", "kasa": "B", "karsi": "banka", "yon": "giris",
                      "ikon": "🏧", "ack": "BANKADAN ÇEKİLEN",
                      "aciklama": "Bankadan kasaya — Kasa borç / Banka alacak."},
    "kasa_virman": {"ad": "Kasa Virman", "kasa": "A", "karsi": "kasa", "yon": "notr",
                    "ikon": "🔁", "ack": "VİRMAN",
                    "aciklama": "Kasadan kasaya transfer — Hedef kasa borç / Kaynak kasa alacak."},
}


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
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise KasaHareketHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return hesap


def _karsi_coz(tip, kasa, karsi):
    """Karşı taraf nesnesinden (Cari/BankaHesap/Kasa) muhasebe hesabı + ad döner;
    banka/virman'da para birimi kasayla aynı olmalı (tek-para fiş, ilk dilim)."""
    tur = HAREKET[tip]["karsi"]
    if tur == "cari":
        return _cari_hesap(karsi).hesap_kodu, karsi.unvan
    if tur == "banka":
        if karsi.para_birimi != kasa.para_birimi:
            raise KasaHareketHatasi(
                f"Banka hesabı ({karsi.para_birimi}) ile kasa ({kasa.para_birimi}) para "
                f"birimi farklı; çapraz kurlu hareket sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if tur == "kasa":
        if karsi.pk == kasa.pk:
            raise KasaHareketHatasi("Virman aynı kasaya yapılamaz.")
        if karsi.para_birimi != kasa.para_birimi:
            raise KasaHareketHatasi(
                f"Hedef kasa ({karsi.para_birimi}) ile kaynak kasa ({kasa.para_birimi}) "
                f"para birimi farklı; çapraz kurlu virman sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise KasaHareketHatasi("Geçersiz hareket tipi.")


@transaction.atomic
def hareket_olustur(*, kasa, tip, karsi, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Bir kasa hareketinden otomatik DENGELİ yevmiye fişi üretir (kaynak=KASA,
    kaynak kasa=`kasa`). `karsi` tipe göre Cari / BankaHesap / (hedef) Kasa.
    Kural ihlalinde hiçbir şey kaydedilmez (transaction geri alınır)."""
    if tip not in HAREKET:
        raise KasaHareketHatasi("Geçersiz hareket tipi.")
    if kasa.muhasebe_id is None:
        raise KasaHareketHatasi("Kasanın muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    karsi_kod, karsi_ad = _karsi_coz(tip, kasa, karsi)
    tut = _tutar(tutar)
    pb = kasa.para_birimi
    kur = _kur_coz(pb, tarih)
    kasa_taraf = tan["kasa"]
    karsi_taraf = "A" if kasa_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KASA {tan['ack']} - {karsi_ad}"))

    satirlar = [
        SatirGirdi(hesap_kodu=kasa.muhasebe.hesap_kodu, taraf=kasa_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
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


def cari_tahsilat(*, kasa, cari, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Cari Tahsilat kısayolu (Kasa borç / Cari alacak)."""
    return hareket_olustur(kasa=kasa, tip="cari_tahsilat", karsi=cari, tutar=tutar,
                           tarih=tarih, aciklama=aciklama, kullanici=kullanici)


def hareket_iptal(*, fis, kasa, kullanici=None):
    """Kasa hareketi (kaynak=KASA) iptali → bağlı fişi soft-delete eder.
    Fiş bu kasaya ait bir kasa hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KASA or fis.kasa_id != kasa.pk:
        raise KasaHareketHatasi("Bu fiş bu kasanın hareketi değil.")
    if fis.silindi:
        return fis
    return fis_iptal(fis, kullanici=kullanici)
