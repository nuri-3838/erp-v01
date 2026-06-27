"""BANKA hesabı hareket motoru — banka hareketinden OTOMATİK dengeli yevmiye fişi.

Kasa motorunun (`kasa_hareket.py`) banka hesabı karşılığı: her hareket = bir yevmiye
fişi (kaynak=BANKA, kaynak banka hesabına bağlı). Yeni bakiye/hareket modeli YOK;
bakiye/ekstre bağlı muhasebe hesabının (102.x) yevmiyesinden gelir. Fiş hem banka
hesabının hem karşı tarafın hesabını işlediği için ilgili tüm ekstrelerde görünür.

5 hareket tipi — banka hesabı perspektifi (yönler KİLİTLİ; kasa modülüyle TUTARLI,
aynı işlem her iki ekrandan da kaydedilebilir):
  cari_tahsilat  Banka B / Cari A         (giriş, karşı=Cari)
  cari_odeme     Banka A / Cari B          (çıkış, karşı=Cari)
  banka_virman   Kaynak Banka A / Hedef Banka B  (transfer, karşı=BankaHesap)
  banka_yatan    Banka B / Kasa A          (giriş, karşı=Kasa)  kasadan bankaya nakit
  banka_cekilen  Banka A / Kasa B          (çıkış, karşı=Kasa)  bankadan kasaya nakit

İlk dilim: banka hesabının para birimi + TCMB kuru. Karşı taraf (banka/kasa) banka
hesabıyla AYNI para biriminde olmalı (çapraz kur sonraki dilim).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_iptal,
                                   fis_olustur)


class BankaHareketHatasi(ValueError):
    """Banka hareketi kural ihlali (Türkçe mesaj)."""


# tip -> davranış. banka: banka hesabının tarafı (B=giriş/A=çıkış); karsi: karşı taraf
# tipi; yon: gösterim; ikon + ack (fiş açıklaması ön eki) + aciklama (form ipucu).
HAREKET = {
    "cari_tahsilat": {"ad": "Cari Tahsilat", "banka": "B", "karsi": "cari", "yon": "giris",
                      "ikon": "📥", "ack": "TAHSİLAT",
                      "aciklama": "Müşteriden bankaya — Banka borç / Cari alacak."},
    "cari_odeme": {"ad": "Cari Ödeme", "banka": "A", "karsi": "cari", "yon": "cikis",
                   "ikon": "📤", "ack": "ÖDEME",
                   "aciklama": "Bankadan tedarikçiye — Cari borç / Banka alacak."},
    "banka_virman": {"ad": "Banka Virman", "banka": "A", "karsi": "banka", "yon": "notr",
                     "ikon": "🔁", "ack": "VİRMAN",
                     "aciklama": "Bankadan bankaya — Hedef banka borç / Kaynak banka alacak."},
    "banka_yatan": {"ad": "Banka Yatan", "banka": "B", "karsi": "kasa", "yon": "giris",
                    "ikon": "🏦", "ack": "BANKAYA YATAN",
                    "aciklama": "Kasadan bankaya nakit — Banka borç / Kasa alacak."},
    "banka_cekilen": {"ad": "Banka Çekilen", "banka": "A", "karsi": "kasa", "yon": "cikis",
                      "ikon": "🏧", "ack": "BANKADAN ÇEKİLEN",
                      "aciklama": "Bankadan kasaya nakit — Kasa borç / Banka alacak."},
}


def _kur_coz(pb, tarih):
    """Banka hesabının para biriminin fiş tarihindeki TCMB alış kuru. TRY -> 1."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise BankaHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi "
            f"çekmeden döviz banka hesabı hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise BankaHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise BankaHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _cari_hesap(cari):
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise BankaHareketHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return hesap


def _karsi_coz(tip, banka_hesap, karsi):
    """Karşı taraf nesnesinden (Cari/BankaHesap/Kasa) muhasebe hesabı + ad döner;
    banka/kasa karşı tarafı banka hesabıyla aynı para biriminde olmalı."""
    tur = HAREKET[tip]["karsi"]
    if tur == "cari":
        return _cari_hesap(karsi).hesap_kodu, karsi.unvan
    if tur == "banka":
        if karsi.pk == banka_hesap.pk:
            raise BankaHareketHatasi("Virman aynı banka hesabına yapılamaz.")
        if karsi.para_birimi != banka_hesap.para_birimi:
            raise BankaHareketHatasi(
                f"Hedef hesap ({karsi.para_birimi}) ile kaynak hesap "
                f"({banka_hesap.para_birimi}) para birimi farklı; çapraz kurlu virman "
                f"sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if tur == "kasa":
        if karsi.para_birimi != banka_hesap.para_birimi:
            raise BankaHareketHatasi(
                f"Kasa ({karsi.para_birimi}) ile banka hesabı ({banka_hesap.para_birimi}) "
                f"para birimi farklı; çapraz kurlu hareket sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise BankaHareketHatasi("Geçersiz hareket tipi.")


@transaction.atomic
def hareket_olustur(*, banka_hesap, tip, karsi, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Bir banka hesabı hareketinden otomatik DENGELİ yevmiye fişi üretir
    (kaynak=BANKA, kaynak banka hesabı=`banka_hesap`). `karsi` tipe göre
    Cari / (hedef) BankaHesap / Kasa. İhlalde hiçbir şey kaydedilmez."""
    if tip not in HAREKET:
        raise BankaHareketHatasi("Geçersiz hareket tipi.")
    if banka_hesap.muhasebe_id is None:
        raise BankaHareketHatasi("Banka hesabının muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    karsi_kod, karsi_ad = _karsi_coz(tip, banka_hesap, karsi)
    tut = _tutar(tutar)
    pb = banka_hesap.para_birimi
    kur = _kur_coz(pb, tarih)
    banka_taraf = tan["banka"]
    karsi_taraf = "A" if banka_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"BANKA {tan['ack']} - {karsi_ad}"))

    satirlar = [
        SatirGirdi(hesap_kodu=banka_hesap.muhasebe.hesap_kodu, taraf=banka_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
    ]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.BANKA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise BankaHareketHatasi(str(e))
    fis.banka_hesap = banka_hesap
    fis.save(update_fields=["banka_hesap", "updated_at"])
    return fis


def hareket_iptal(*, fis, banka_hesap, kullanici=None):
    """Banka hareketi (kaynak=BANKA) iptali → bağlı fişi soft-delete eder.
    Fiş bu banka hesabının hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.BANKA or fis.banka_hesap_id != banka_hesap.pk:
        raise BankaHareketHatasi("Bu fiş bu banka hesabının hareketi değil.")
    if fis.silindi:
        return fis
    return fis_iptal(fis, kullanici=kullanici)
