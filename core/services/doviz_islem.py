"""DÖVİZ ALIŞ / SATIŞ — TL banka/kasa ↔ döviz banka/kasa arası çapraz kurlu virman (tek fiş).

- Döviz ALIŞ  (kaynak TL hesap → hedef döviz hesap): döviz hesabı BORÇ (döviz × kur), TL hesabı
  ALACAK (TL tutar + masraf); masraf/kambiyo vergisi satırları BORÇ.
- Döviz SATIŞ (kaynak döviz hesap → hedef TL hesap): TL hesabı BORÇ (TL tutar − masraf), masraf
  satırları BORÇ, döviz hesabı ALACAK — döviz satırı kur farkı motoru (core.services.kur_farki)
  tarafından hesabın ORTALAMA kuruyla yazılır, fark aynı fişe 646 KAMBİYO KÂRI / 656 KAMBİYO
  ZARARI satırı olur.

Fiş kaynağı: taraflardan biri banka hesabıysa BANKA (kaynak banka hesabı kaynak taraf, değilse
hedef), ikisi de kasaysa KASA. Böylece fiş ilgili ekrandan (Sil dahil) yönetilir.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import BankaHesap, Cari, HesapPlani, Kasa, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur


class DovizIslemHatasi(ValueError):
    """Döviz alış/satış kural ihlali (Türkçe mesaj)."""


def hesap_adi(obj):
    return f"{obj.banka.ad} - {obj.ad}" if isinstance(obj, BankaHesap) else obj.ad


def _tutar(deger, alan, sifir_olabilir=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise DovizIslemHatasi(f"{alan} geçerli bir sayı olmalı.")
    if d < 0 or (d == 0 and not sifir_olabilir):
        raise DovizIslemHatasi(f"{alan} sıfırdan büyük olmalı.")
    return d


def _kur(pb, tarih, kur):
    if kur not in (None, ""):
        d = _tutar(kur, "Kur")
        return d
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    deger = k.deger(pb, Cari.KurTipi.MB_ALIS) if k else None
    if not deger:
        raise DovizIslemHatasi(f"{tarih:%d.%m.%Y} için {pb} kuru yok; kuru elle girin ya da Kurlar "
                               f"ekranından çekin.")
    return deger


@transaction.atomic
def doviz_islem_olustur(*, kaynak, hedef, doviz_tutari, tarih, kur=None, aciklama="",
                        masraflar=(), kullanici=None) -> YevmiyeFisi:
    """``kaynak``/``hedef``: BankaHesap ya da Kasa. ``masraflar``: [{"hesap_kodu", "tutar"(TL),
    "aciklama"}] — TL hesaptan ödenen kambiyo vergisi/komisyon vb. satırları."""
    if kaynak == hedef:
        raise DovizIslemHatasi("Kaynak ve hedef aynı hesap olamaz.")
    pb_k, pb_h = kaynak.para_birimi, hedef.para_birimi
    if pb_k == "TRY" and pb_h != "TRY":
        alis, tl_hesap, dvz_hesap, pb = True, kaynak, hedef, pb_h
    elif pb_k != "TRY" and pb_h == "TRY":
        alis, tl_hesap, dvz_hesap, pb = False, hedef, kaynak, pb_k
    else:
        raise DovizIslemHatasi(
            "Döviz alış/satışta taraflardan biri TL, diğeri döviz hesabı olmalı "
            "(döviz-döviz çapraz işlem desteklenmez).")
    d = _tutar(doviz_tutari, "Döviz tutarı")
    kur = _kur(pb, tarih, kur)
    tl = yuvarla(d * kur, 2)

    masraf_satirlari, masraf_toplam = [], Decimal("0.00")
    for m in masraflar or ():
        tut = _tutar(m.get("tutar"), "Masraf tutarı", sifir_olabilir=True)
        if not m.get("hesap_kodu") or tut == 0:
            continue
        if not HesapPlani.objects.filter(hesap_kodu=m["hesap_kodu"], silindi=False, aktif=True).exists():
            raise DovizIslemHatasi(f"Masraf hesabı bulunamadı: {m['hesap_kodu']}.")
        masraf_toplam += yuvarla(tut, 2)
        masraf_satirlari.append(SatirGirdi(
            hesap_kodu=m["hesap_kodu"], taraf="B", islem_tutari=yuvarla(tut, 2),
            aciklama=m.get("aciklama") or "KAMBİYO VERGİSİ / MASRAF"))
    if not alis and masraf_toplam >= tl:
        raise DovizIslemHatasi("Masraf toplamı satış tutarından küçük olmalı.")

    satirlar = [
        SatirGirdi(hesap_kodu=dvz_hesap.muhasebe_id, taraf="B" if alis else "A",
                   islem_tutari=d, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=tl_hesap.muhasebe_id, taraf="A" if alis else "B",
                   islem_tutari=(tl + masraf_toplam) if alis else (tl - masraf_toplam)),
        *masraf_satirlari,
    ]
    ack = (f"DÖVİZ {'ALIŞ' if alis else 'SATIŞ'} {format_tr(d)} {pb} (KUR {format_tr(kur, 4)}) "
           f"{hesap_adi(kaynak)} → {hesap_adi(hedef)}")
    if (aciklama or "").strip():
        ack += f" — {aciklama.strip()}"
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=buyuk_harf_tr(ack),
                          kaynak=(YevmiyeFisi.Kaynak.KASA if isinstance(kaynak, Kasa) and isinstance(hedef, Kasa)
                                  else YevmiyeFisi.Kaynak.BANKA),
                          kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise DovizIslemHatasi(str(e))
    sahip = kaynak if isinstance(kaynak, BankaHesap) else (hedef if isinstance(hedef, BankaHesap) else None)
    if sahip is not None:
        fis.banka_hesap = sahip
        fis.save(update_fields=["banka_hesap", "updated_at"])
    else:
        fis.kasa = kaynak
        fis.save(update_fields=["kasa", "updated_at"])
    return fis
