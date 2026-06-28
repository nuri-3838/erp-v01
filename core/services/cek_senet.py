"""ÇEK/SENET yaşam döngüsü motoru → kıymetli evraktan OTOMATİK yevmiye.

Slice 1 — GİRİŞ (oluşturma). Çek/senet kaydedilince otomatik dengeli giriş fişi
üretilir (kaynak=CEK_SENET, evraka bağlı):
  ALINAN : çek hesabı (101) borç / cari alacak    (carinin alacağı evraka döner)
  VERİLEN: cari borç / çek hesabı (103) alacak     (cariye evrakla ödeme)
Cari ZORUNLU. Para birimi = evrakın PB'si + TCMB kuru (TRY=1). Giriş fişinin tarihi
işlemin kaydedildiği gün (vade ≠ fiş tarihi). Düzenleme yalnız PORTFÖYDE iken
(giriş fişi yeniden yazılır); işlem görmüşse kilit. Tahsil/Ciro/Karşılıksız/İade/
Ödendi → sonraki dilimler.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Cari, CekSenet, HesapPlani, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr
from core.services.finans import _cek_alanlar, _soft_sil, _yaprak_hesap_coz
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_guncelle,
                                   fis_iptal, fis_olustur)


class CekSenetHatasi(ValueError):
    """Çek/senet kural ihlali (Türkçe mesaj)."""


def _kur_coz(pb, tarih):
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise CekSenetHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; döviz evrak için Kurlar ekranından çekin.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CekSenetHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise CekSenetHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _cari_coz(cari_id):
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first() if cari_id else None
    if cari is None:
        raise CekSenetHatasi("Çek/senet için cari (karşı taraf) zorunlu.")
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise CekSenetHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return cari, hesap


def _pb_coz(para_birimi):
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise CekSenetHatasi("Geçersiz para birimi.")
    return pb


def _giris_satirlari(cs, cari_kod, kur):
    """Giriş fişi satırları (evrakın para biriminde). ALINAN: çek borç/cari alacak."""
    cek_taraf = "B" if cs.yon == CekSenet.Yon.ALINAN else "A"
    cari_taraf = "A" if cek_taraf == "B" else "B"
    return [
        SatirGirdi(hesap_kodu=cs.muhasebe.hesap_kodu, taraf=cek_taraf,
                   islem_tutari=cs.tutar, islem_pb=cs.para_birimi, islem_kuru=kur),
        SatirGirdi(hesap_kodu=cari_kod, taraf=cari_taraf,
                   islem_tutari=cs.tutar, islem_pb=cs.para_birimi, islem_kuru=kur),
    ]


def _aciklama(cs):
    yon = "GİRİŞ" if cs.yon == CekSenet.Yon.ALINAN else "ÇIKIŞ"
    tip = "ÇEK" if cs.tip == CekSenet.Tip.CEK else "SENET"
    return buyuk_harf_tr(f"{tip} {yon} - {cs.cari.unvan}")


def _giris_fisi(cs):
    return cs.fisler.filter(kaynak=YevmiyeFisi.Kaynak.CEK_SENET,
                            silindi=False).order_by("id").first()


@transaction.atomic
def cek_senet_olustur(*, tip, yon, tutar, vade, para_birimi="TRY", kesideci="",
                      belge_no="", muhasebe_kodu, cari_id, kullanici=None) -> CekSenet:
    """Çek/senet oluşturur + otomatik GİRİŞ fişi üretir (kaynak=CEK_SENET)."""
    tip, yon, _ = _cek_alanlar(tip, yon, "")
    cari, cari_hesap = _cari_coz(cari_id)
    hesap = _yaprak_hesap_coz(muhasebe_kodu)
    pb = _pb_coz(para_birimi)
    bugun = timezone.localdate()
    kur = _kur_coz(pb, bugun)
    cs = CekSenet.objects.create(
        tip=tip, yon=yon, tutar=_tutar(tutar), vade=vade, para_birimi=pb,
        kesideci=buyuk_harf_tr((kesideci or "").strip()), belge_no=(belge_no or "").strip(),
        durum=CekSenet.Durum.PORTFOYDE, muhasebe=hesap, cari=cari,
        created_by=kullanici, updated_by=kullanici)
    try:
        fis = fis_olustur(tarih=bugun, satirlar=_giris_satirlari(cs, cari_hesap.hesap_kodu, kur),
                          aciklama=_aciklama(cs), kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekSenetHatasi(str(e))
    fis.cek_senet = cs
    fis.save(update_fields=["cek_senet", "updated_at"])
    return cs


@transaction.atomic
def cek_senet_guncelle(cs: CekSenet, *, tip, yon, tutar, vade, para_birimi="TRY",
                       kesideci="", belge_no="", muhasebe_kodu, cari_id, kullanici=None) -> CekSenet:
    """Yalnız PORTFÖYDE iken düzenlenir; giriş fişi yeniden yazılır."""
    if cs.silindi:
        raise CekSenetHatasi("Silinmiş kayıt düzenlenemez.")
    if cs.durum != CekSenet.Durum.PORTFOYDE:
        raise CekSenetHatasi("İşlem görmüş çek/senet düzenlenemez; "
                             "gerekirse iptal edip yeniden girin.")
    tip, yon, _ = _cek_alanlar(tip, yon, "")
    cari, cari_hesap = _cari_coz(cari_id)
    hesap = _yaprak_hesap_coz(muhasebe_kodu)
    pb = _pb_coz(para_birimi)
    cs.tip, cs.yon = tip, yon
    cs.tutar = _tutar(tutar)
    cs.vade = vade
    cs.para_birimi = pb
    cs.kesideci = buyuk_harf_tr((kesideci or "").strip())
    cs.belge_no = (belge_no or "").strip()
    cs.muhasebe = hesap
    cs.cari = cari
    cs.updated_by = kullanici
    cs.save(update_fields=["tip", "yon", "tutar", "vade", "para_birimi", "kesideci",
                           "belge_no", "muhasebe", "cari", "updated_by", "updated_at"])
    fis = _giris_fisi(cs)
    tarih = fis.tarih if fis else timezone.localdate()
    kur = _kur_coz(pb, tarih)
    satirlar = _giris_satirlari(cs, cari_hesap.hesap_kodu, kur)
    try:
        if fis:
            fis_guncelle(fis, tarih=tarih, satirlar=satirlar, aciklama=_aciklama(cs),
                         kullanici=kullanici)
        else:                                  # fişsiz eski kayıt → giriş fişi üret
            f = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=_aciklama(cs),
                            kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
            f.cek_senet = cs
            f.save(update_fields=["cek_senet", "updated_at"])
    except YevmiyeHatasi as e:
        raise CekSenetHatasi(str(e))
    return cs


@transaction.atomic
def cek_senet_sil(cs: CekSenet, kullanici=None) -> CekSenet:
    """Çek/senet soft-delete + bağlı tüm fişleri iptal eder."""
    if cs.silindi:
        return cs
    for fis in cs.fisler.filter(silindi=False):
        fis_iptal(fis, kullanici=kullanici)
    return _soft_sil(cs, kullanici)
