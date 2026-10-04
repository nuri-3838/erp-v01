"""CARİ KESİNTİ / MASRAF hareketi — manuel fiş yerine. Cari kartından: tarih, tutar, gider hesabı (varsayılan
770.03), açıklama. Fiş otomatik ve dengeli (kaynak=CARI_KESINTI, fiş→cari bağı); Düzenle ve (yalnız süper
kullanıcı) kalıcı Sil vardır.

Yön cari hesabının türünden gelir (otomatik):
  120/121 (müşteri / alacak tarafı): Gider BORÇ / Cari ALACAK — müşteri ödemesinden kesilen gider (ör. güvenli ödeme
      masrafı) cari bakiyesini kapatır; kuruş farkları da böyle girilir.
  320/321 (tedarikçi / borç tarafı): Cari BORÇ / Gider ALACAK — tedarikçiden gelen kesinti/indirim.
Yalnız TL carilerde (döviz carilerde kur gerekir; manuel fiş kullanın).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur

VARSAYILAN_GIDER = "770.03"


class CariKesintiHatasi(ValueError):
    """Cari kesinti/masraf kural ihlali (Türkçe mesaj)."""


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariKesintiHatasi("Tutar geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d <= 0:
        raise CariKesintiHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def yon_coz(cari):
    """Cari hesabına göre kesinti yönü: 'musteri' (gider borç / cari alacak) ya da 'tedarikci' (tersi)."""
    kod = cari.muhasebe_kodu or ""
    if kod.startswith(("120", "121")):
        return "musteri"
    if kod.startswith(("320", "321")):
        return "tedarikci"
    raise CariKesintiHatasi(
        f"{cari.unvan} carisinin muhasebe hesabı ({kod or 'yok'}) 120/320 ailesinde değil; yön belirlenemedi.")


def gider_hesabi_coz(kod):
    from core.services.hesap_plani import gider_hesaplari
    h = gider_hesaplari().filter(hesap_kodu=(kod or VARSAYILAN_GIDER)).first()
    if h is None:
        raise CariKesintiHatasi(f"Geçerli bir gider hesabı seçin (yaprak 7xx/65x/66x/68x): {kod}")
    return h


def _ack(cari, aciklama, yon):
    ack = buyuk_harf_tr((aciklama or "").strip())
    return ack or buyuk_harf_tr(f"KESİNTİ / MASRAF - {cari.unvan}")


def _satirlar(cari, gider, tutar, yon):
    if cari.para_birimi != "TRY":
        raise CariKesintiHatasi("Döviz carilerde kesinti/masraf hareketi girilemez (kur gerekir); manuel fiş kullanın.")
    cari_hesap = HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
    if cari_hesap is None:
        raise CariKesintiHatasi(f"{cari.unvan} carisinin muhasebe hesabı hesap planında yok.")
    gider_taraf, cari_taraf = ("B", "A") if yon == "musteri" else ("A", "B")
    return [SatirGirdi(hesap_kodu=gider.hesap_kodu, taraf=gider_taraf, islem_tutari=tutar,
                       aciklama=gider.hesap_adi),
            SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf, islem_tutari=tutar,
                       aciklama=cari.unvan)]


@transaction.atomic
def kesinti_olustur(*, cari, tarih, tutar, gider_kodu=None, aciklama="", kullanici=None) -> YevmiyeFisi:
    yon = yon_coz(cari)
    gider = gider_hesabi_coz(gider_kodu)
    tut = _tutar(tutar)
    try:
        fis = fis_olustur(tarih=tarih, satirlar=_satirlar(cari, gider, tut, yon), aciklama=_ack(cari, aciklama, yon),
                          kaynak=YevmiyeFisi.Kaynak.CARI_KESINTI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariKesintiHatasi(str(e))
    fis.cari = cari
    fis.save(update_fields=["cari", "updated_at"])
    return fis


def duzenleme_bilgisi(fis, cari):
    """{'gider': HesapPlani, 'tutar': Decimal, 'yon': ...} — fiş bu carinin kesinti hareketi değilse hata."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_KESINTI or fis.cari_id != cari.pk or fis.silindi:
        raise CariKesintiHatasi("Bu fiş bu carinin düzenlenebilir bir kesinti/masraf hareketi değil.")
    yon = yon_coz(cari)
    satirlar = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True).select_related("hesap"))
    gider_s = [s for s in satirlar if s.hesap_id != cari.muhasebe_kodu]
    if len(satirlar) != 2 or len(gider_s) != 1:
        raise CariKesintiHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    return {"gider": gider_s[0].hesap, "tutar": gider_s[0].borc or gider_s[0].alacak, "yon": yon}


@transaction.atomic
def kesinti_guncelle(*, fis, cari, tarih, tutar, gider_kodu=None, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Tarih/tutar/gider hesabı/açıklama düzenlenir; fiş yeniden yazılır (kur farkı motoru da çalışır)."""
    duzenleme_bilgisi(fis, cari)
    yon = yon_coz(cari)
    gider = gider_hesabi_coz(gider_kodu)
    tut = _tutar(tutar)
    try:
        fis_guncelle(fis, tarih=tarih, satirlar=_satirlar(cari, gider, tut, yon),
                     aciklama=_ack(cari, aciklama, yon), kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariKesintiHatasi(str(e))
    return fis


@transaction.atomic
def kesinti_sil(*, fis, cari, kullanici=None):
    """Kesinti/masraf hareketini KALICI siler (yalnız süper kullanıcı; denetim kaydı yazılır)."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_KESINTI or fis.cari_id != cari.pk:
        raise CariKesintiHatasi("Bu fiş bu carinin kesinti/masraf hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.CARI_KESINTI})
    except SilmeHatasi as e:
        raise CariKesintiHatasi(str(e))
