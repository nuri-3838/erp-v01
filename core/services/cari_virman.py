"""CARİ VİRMAN — bir carinin bakiyesini başka bir cariye (özellikle ortak 500.10.0001'e) her iki yönde aktarır; manuel mahsup fişi yerine.

Cari kartından: tarih, tutar (TL), karşı cari (tüm cariler), yön:
  "alacak": Bu cari ALACAK / karşı cari BORÇ      "borc": Bu cari BORÇ / karşı cari ALACAK
Fiş otomatik ve dengeli (kaynak=CARI_VIRMAN; fiş→cari ve fiş→karşı cari bağı); iki carinin ekstresinde de görünür; Düzenle ve (yalnız süper
kullanıcı) kalıcı Sil vardır ("iptal" durumu yok). Döviz carilerde TL tutar ödeme günü TCMB alış kuruyla cari para birimine çevrilir (TL aynı;
bkz. core.services.doviz_cari; ``sayilan_pb='TRY'`` = çevirme, TL havuzu).
Örnek (Kaygun'dan elden iade): 320.30.0038 KAYGUN ALACAK 75.500 / 500.10.0001 NURİ ÖZER BORÇ 75.500.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import doviz_cari
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import YevmiyeHatasi, fis_guncelle, fis_olustur

YONLER = (("alacak", "Bu cari ALACAK / karşı cari BORÇ"), ("borc", "Bu cari BORÇ / karşı cari ALACAK"))


class CariVirmanHatasi(ValueError):
    """Cari virman kural ihlali (Türkçe mesaj)."""


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariVirmanHatasi("Tutar geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d <= 0:
        raise CariVirmanHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama):
    if karsi_cari is None or karsi_cari.pk == cari.pk:
        raise CariVirmanHatasi("Karşı cari, virman yapılan cariden farklı bir cari olmalı.")
    if yon not in ("alacak", "borc"):
        raise CariVirmanHatasi("Yön seçin (bu cari ALACAK / BORÇ).")
    for c in (cari, karsi_cari):
        if not c.muhasebe_kodu or not HesapPlani.objects.filter(hesap_kodu=c.muhasebe_kodu, silindi=False, aktif=True).exists():
            raise CariVirmanHatasi(f"{c.unvan} carisinin muhasebe hesabı yok ya da pasif.")
    tut = _tutar(tutar)
    taraf_cari = "A" if yon == "alacak" else "B"
    taraf_karsi = "B" if yon == "alacak" else "A"
    try:
        satirlar = [doviz_cari.cari_satiri(cari, tut, tarih, taraf_cari, sayilan_pb, aciklama=cari.unvan),
                    doviz_cari.cari_satiri(karsi_cari, tut, tarih, taraf_karsi, sayilan_pb, aciklama=karsi_cari.unvan)]
    except doviz_cari.DovizCariHatasi as e:
        raise CariVirmanHatasi(str(e))
    ack = buyuk_harf_tr((aciklama or "").strip()) or buyuk_harf_tr(f"CARİ VİRMAN - {cari.unvan} / {karsi_cari.unvan}")
    return satirlar, ack[:500]


@transaction.atomic
def virman_olustur(*, cari, karsi_cari, tarih, tutar, yon, aciklama="", sayilan_pb=None, kullanici=None) -> YevmiyeFisi:
    satirlar, ack = _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama)
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariVirmanHatasi(str(e))
    fis.cari, fis.karsi_cari = cari, karsi_cari
    fis.save(update_fields=["cari", "karsi_cari", "updated_at"])
    return fis


def _fis_kontrol(fis, cari):
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_VIRMAN or fis.silindi or cari.pk not in (fis.cari_id, fis.karsi_cari_id):
        raise CariVirmanHatasi("Bu fiş bu carinin düzenlenebilir bir virman hareketi değil.")


def duzenleme_bilgisi(fis, cari):
    """{'karsi_cari', 'yon', 'tutar' (TL), 'sayilan_pb'} — ``cari`` ekranının bakış açısıyla."""
    _fis_kontrol(fis, cari)
    karsi = fis.karsi_cari if cari.pk == fis.cari_id else fis.cari
    s = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True, hesap_id=cari.muhasebe_kodu))
    if len(s) != 1 or karsi is None:
        raise CariVirmanHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    s = s[0]
    tl = s.ham_tl if s.ham_tl is not None else (s.borc or s.alacak)       # kur farkı motoru satır TL'sini değiştirmiş olabilir
    return {"karsi_cari": karsi, "yon": "alacak" if s.alacak else "borc", "tutar": tl,
            "sayilan_pb": (s.islem_pb if cari.para_birimi != "TRY" else "")}


@transaction.atomic
def virman_guncelle(*, fis, cari, karsi_cari, tarih, tutar, yon, aciklama="", sayilan_pb=None, kullanici=None) -> YevmiyeFisi:
    _fis_kontrol(fis, cari)
    satirlar, ack = _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama)
    try:
        fis_guncelle(fis, tarih=tarih, satirlar=satirlar, aciklama=ack, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariVirmanHatasi(str(e))
    fis.cari, fis.karsi_cari = cari, karsi_cari
    fis.save(update_fields=["cari", "karsi_cari", "updated_at"])
    return fis


@transaction.atomic
def virman_sil(*, fis, cari, kullanici=None):
    """Virman hareketini KALICI siler (fiş dahil; yalnız süper kullanıcı; denetim kaydı yazılır)."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_VIRMAN or cari.pk not in (fis.cari_id, fis.karsi_cari_id):
        raise CariVirmanHatasi("Bu fiş bu carinin virman hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.CARI_VIRMAN})
    except SilmeHatasi as e:
        raise CariVirmanHatasi(str(e))


def cari_virman_fisleri(cari):
    """Cari ekstresinde Düzenle/Sil gösterilecek virman fişlerinin pk'leri (carinin kendisi ya da karşı carisi olduğu)."""
    from django.db.models import Q
    return set(YevmiyeFisi.objects.filter(Q(cari=cari) | Q(karsi_cari=cari), kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN,
                                          silindi=False).values_list("pk", flat=True))
