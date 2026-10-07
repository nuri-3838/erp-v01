"""Döviz carilere yapılan TL ödemeler döviz borcundan düşer (genel kural).

Cari para birimi döviz olan carilerde (ör. Formal USD/EUR, Argema EUR) TL hareketlerde (banka/kasa Cari Ödeme, kredi kartı harcaması,
Kesinti 'karşı cari = ortak') cari satırı ÖDEME GÜNÜNDEKİ TCMB döviz ALIŞ kuruyla dövize çevrilip döviz olarak yazılır; TL tutar AYNI
kalır (cari satırının TL'si = ödenen TL; karşı satır TL). Birden fazla döviz havuzu varsa hangi dövize sayılacağı seçilir
(varsayılan: carinin ana para birimi; 'TRY' = çevirme, TL havuzu). Kur farkı motoru havuzu normal işler (646/656; yatırım carisi
kuralı hep geçerli). Döviz banka/kasa hesabıyla yapılan hareketler bu kuraldan ETKİLENMEZ (zaten dövizle yazılır).
"""
from __future__ import annotations

from decimal import Decimal

from core.models import Cari, HesapPlani, Kur
from core.sayi import yuvarla
from core.services.yevmiye import SatirGirdi


class DovizCariHatasi(ValueError):
    """Döviz cari çevirisi kural ihlali (Türkçe mesaj)."""


DOVIZLER = ("USD", "EUR", "GBP")
# Ekranda 'hangi dövize sayılsın' seçimi: boş = carinin ana para birimi; TRY = çevirme (TL havuzu).
SECENEKLER = [("", "Carinin ana para birimi (varsayılan)"), ("TRY", "TL (çevirme — TL havuzu)"),
              ("USD", "USD"), ("EUR", "EUR"), ("GBP", "GBP")]


def alis_kuru(pb, tarih):
    """Ödeme günündeki TCMB döviz ALIŞ kuru (carinin kur tipinden BAĞIMSIZ). Kayıt yoksa hata."""
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    deger = k.deger(pb, Cari.KurTipi.MB_ALIS) if k else None
    if not deger:
        raise DovizCariHatasi(
            f"{tarih:%d.%m.%Y} için {pb} TCMB alış kuru yok; Kurlar ekranından bu tarihi çekmeden döviz carisine "
            f"ödeme çevrilemez (TL havuzunda tutmak için 'TL (çevirme)' seçin).")
    return deger


def hedef_pb(cari, sayilan_pb=None):
    """Cari satırının yazılacağı para birimi."""
    sayilan = (sayilan_pb or "").strip().upper()
    if cari.para_birimi == "TRY":
        return "TRY"                                   # TL carisi: çevirme yok
    if sayilan in ("", None):
        return cari.para_birimi                        # varsayılan: carinin ana para birimi
    if sayilan != "TRY" and sayilan not in DOVIZLER:
        raise DovizCariHatasi("Geçersiz para birimi seçimi.")
    return sayilan


def donusum_gerekli_mi(cari, sayilan_pb=None):
    return isinstance(cari, Cari) and hedef_pb(cari, sayilan_pb) != "TRY"


def elle_kur_satiri(cari, doviz_tutar, kur, taraf, pb, aciklama="", yatirim_projesi_id=None):
    """ELLE kurlu döviz cari satırı: döviz tutar sabit, TL = döviz tutar × girilen kur (TCMB kuru kullanılmaz)."""
    hesap = HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if hesap is None:
        raise DovizCariHatasi(f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    dvz = yuvarla(Decimal(doviz_tutar), 2)
    kur = yuvarla(Decimal(kur), 6)
    tl = yuvarla(dvz * kur, 2)
    if dvz <= 0 or kur <= 0 or tl <= 0:
        raise DovizCariHatasi("Döviz tutarı ve kur sıfırdan büyük olmalı.")
    return SatirGirdi(hesap_kodu=hesap.hesap_kodu, taraf=taraf, islem_tutari=dvz, islem_pb=pb, islem_kuru=kur,
                      tl_override=tl, aciklama=aciklama, yatirim_projesi_id=yatirim_projesi_id)


def cari_satiri(cari, tl_tutar, tarih, taraf, sayilan_pb=None, aciklama="", yatirim_projesi_id=None, doviz_tutar=None):
    """TL ödemenin cari satırı: döviz carisinde dövize çevrilmiş (TL aynı), aksi halde düz TL satır.

    ``doviz_tutar`` (karşı tarafın saydığı döviz tutarı) verilirse kur = TL tutar / döviz tutarı (TCMB alış kuru kullanılmaz);
    döviz tutarı yalnız hedef para birimi döviz iken girilebilir."""
    hesap = HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if hesap is None:
        raise DovizCariHatasi(f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    pb = hedef_pb(cari, sayilan_pb)
    tl = yuvarla(Decimal(tl_tutar), 2)
    elle_dvz = doviz_tutar not in (None, "")
    if elle_dvz and pb == "TRY":
        raise DovizCariHatasi("Sayılan döviz tutarı için sayılan para birimi döviz olmalı (cari TL ise ya da 'TL (çevirme)' "
                              "seçiliyse döviz tutarı girilemez).")
    if pb == "TRY":
        return SatirGirdi(hesap_kodu=hesap.hesap_kodu, taraf=taraf, islem_tutari=tl, aciklama=aciklama,
                          yatirim_projesi_id=yatirim_projesi_id)
    if elle_dvz:
        dvz = yuvarla(Decimal(doviz_tutar), 2)
        if dvz <= 0 or tl <= 0:
            raise DovizCariHatasi("Sayılan döviz tutarı sıfırdan büyük olmalı.")
        kur = yuvarla(tl / dvz, 6)
        if kur <= 0:
            raise DovizCariHatasi("Sayılan döviz tutarı TL tutara göre çok büyük; kur hesaplanamadı.")
    else:
        kur = alis_kuru(pb, tarih)
        dvz = yuvarla(tl / kur, 2)
    if dvz <= 0:
        raise DovizCariHatasi("Tutar döviz karşılığına çevrilemeyecek kadar küçük.")
    return SatirGirdi(hesap_kodu=hesap.hesap_kodu, taraf=taraf, islem_tutari=dvz, islem_pb=pb, islem_kuru=kur,
                      tl_override=tl, aciklama=aciklama, yatirim_projesi_id=yatirim_projesi_id)
