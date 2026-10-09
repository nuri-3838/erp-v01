"""FASON dönüş maliyeti: çıktı başına fasoncunun kendi birim fiyatı × adet (bölüşüm yok, parça bazlı), döviz fiyat kayıt tarihindeki TCMB
kuruyla TL'ye çevrilir; TL/USD ayrı tutulur. ``uretim.operasyon_kaydi_onayla`` snapshot'ı yazar, ``stok_fis.uretim_senkronla`` çıktı maliyetine
ekler (muhasebe fişine GİRMEZ — 151 tarafını fasoncunun faturası besler)."""
from __future__ import annotations

from decimal import Decimal

from core.sayi import yuvarla
from core.services.fason import FasonHatasi, gecerli_fiyat
from core.services.kur_degerleme import kur_bul

SIFIR = Decimal("0")


def fason_bekliyor(kayit) -> bool:
    """Fason kaydın bedeli fasoncunun FATURASI gelene kadar 'tahmini'dir: dönüş belgesine fatura bağlı değilse True."""
    if not kayit.fason_cari_id:
        return False
    donus = kayit.fason_donus
    return not (donus is not None and getattr(donus, "fatura_id", None))


def fiyatlari_coz(kayit, ciktilar) -> dict:
    """``ciktilar``: [(anahtar, stok, miktar)] (ana + yan çıktılar). Her çıktı için kayıt tarihinde GEÇERLİ FasonFiyat bulunur:
    {anahtar: {"fiyat", "kur", "tutar_try", "tutar_usd"}}. Fiyat ya da gereken TCMB kuru yoksa (hepsi birden listelenerek) FasonHatasi."""
    eksik, sonuc = [], {}
    cari = kayit.fason_cari
    usd_kur = kur_bul("USD", kayit.tarih)[0]
    if usd_kur is None:
        eksik.append(f"{kayit.tarih:%d.%m.%Y} (ve önceki 7 gün) için TCMB USD kuru yok")
    for anahtar, stok, miktar in ciktilar:
        f = gecerli_fiyat(cari, stok, kayit.tarih)
        if f is None:
            eksik.append(f"{stok.kod} {stok.ad}: {cari.unvan} için {kayit.tarih:%d.%m.%Y} tarihinde geçerli fason fiyatı yok")
            continue
        if f.para_birimi == "TRY":
            kur = Decimal("1")
        else:
            kur = kur_bul(f.para_birimi, kayit.tarih)[0]
            if kur is None:
                eksik.append(f"{stok.kod}: fason fiyatı {f.para_birimi} — {kayit.tarih:%d.%m.%Y} için TCMB {f.para_birimi} kuru yok")
                continue
        if usd_kur is None:
            continue
        tl = yuvarla(f.birim_fiyat * kur * miktar, 2)
        sonuc[anahtar] = {"fiyat": f, "kur": kur, "tutar_try": tl, "tutar_usd": yuvarla(tl / usd_kur, 2)}
    if eksik:
        raise FasonHatasi("Fason dönüş onaylanamaz: " + "; ".join(eksik) + ".")
    return sonuc
