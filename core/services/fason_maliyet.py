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
    """Fason kaydın bedeli fasoncunun FATURASI gelene kadar 'tahmini'dir: dönüş belgesine ONAYLI bir fatura bağlı değilse True.
    Faturasız fasoncuda (Cari.fason_faturasiz) bedel, tahakkuk fişi yazılınca kesinleşir (fatura aranmaz)."""
    if not kayit.fason_cari_id:
        return False
    donus = kayit.fason_donus
    if donus is not None and donus.tahakkuk_fis_id and not donus.tahakkuk_fis.silindi:
        return False                                     # faturasız fasoncu: bedel cariye tahakkuk etti → kesin
    fatura = donus.fatura if (donus is not None and donus.fatura_id) else None
    return not (fatura is not None and not fatura.silindi and fatura.durum == "ONAYLI")


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


def bekleyen_ciktilar() -> list:
    """Faturası gelmemiş (bağlı ONAYLI fatura yok) fason kayıtlarının fason bedelli ÇIKTI satırları:
    [{"cikti": OperasyonKaydiCikti, "hareket": giriş StokHareket | None, "malzeme_tahmini": bool}]. Değerleme raporu bu bedeli açıklamak için kullanır."""
    from core.models import OperasyonKaydi, OperasyonKaydiCikti, StokHareket
    adaylar = [c for c in OperasyonKaydiCikti.objects.filter(
        silindi=False, fason_tutar__gt=0, kayit__silindi=False, kayit__durum=OperasyonKaydi.Durum.ONAYLI, kayit__fason_cari__isnull=False)
        .select_related("kayit__fason_donus__fatura", "kayit__fason_donus__tahakkuk_fis", "stok")]
    adaylar = [c for c in adaylar if fason_bekliyor(c.kayit)]
    if not adaylar:
        return []
    kayit_idler = {c.kayit_id for c in adaylar}
    hareketler = {}
    for h in StokHareket.objects.filter(operasyon_kaydi_id__in=kayit_idler, silindi=False):
        hareketler[(h.operasyon_kaydi_id, h.stok_id, h.tur)] = h
    malzeme_tahmini = {}
    for h in hareketler.values():
        if h.tur == StokHareket.Tur.CIKIS and h.maliyet_durumu != StokHareket.MaliyetDurumu.KESIN:
            malzeme_tahmini[h.operasyon_kaydi_id] = True
    return [{"cikti": c, "hareket": hareketler.get((c.kayit_id, c.stok_id, StokHareket.Tur.GIRIS)),
             "malzeme_tahmini": malzeme_tahmini.get(c.kayit_id, False)} for c in adaylar]
