"""TEKLİF & SİPARİŞ servis katmanı — Satınalma/Satış teklifi ve siparişi.

TİCARİ belge: yevmiye fişi ÜRETMEZ, stok hareketi YARATMAZ (muhasebe ve stok her
zaman faturayla girer). belge_tur (Teklif/Sipariş) × yon (Alış/Satış) — dört ekranı
tek modelden besler. Kapsam: liste + oluştur + düzenle + iptal + görüntüle
(teklif→sipariş/sipariş→fatura dönüşümü ve durum akışı sonraki dilimde).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.models import Cari, KdvOrani, Stok, TeklifSiparis, TeklifSiparisKalem
from core.sayi import SayiHatasi, parse_tr


class TeklifSiparisHatasi(ValueError):
    """Teklif/Sipariş kural ihlali (Türkçe mesaj)."""


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise TeklifSiparisHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise TeklifSiparisHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if d < 0:
        raise TeklifSiparisHatasi(f"{etiket} negatif olamaz.")
    return d


def aktif_teklif_siparisler(belge_tur, yon):
    return (TeklifSiparis.objects.filter(silindi=False, belge_tur=belge_tur, yon=yon)
            .select_related("cari"))


def _hazirla(*, cari_id, satirlar):
    """Ortak hazırlık (oluştur): cari + satırları doğrula. (cari, hazir) döner —
    hazir = [(stok, miktar, birim_fiyat, kdv), ...]."""
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise TeklifSiparisHatasi("Cari bulunamadı.")
    if not satirlar:
        raise TeklifSiparisHatasi("En az bir kalem olmalı.")
    hazir = []
    for s in satirlar:
        stok = Stok.objects.filter(pk=s["stok_id"], silindi=False).select_related("kdv").first()
        if stok is None:
            raise TeklifSiparisHatasi("Stok bulunamadı.")
        miktar = _sayi(s.get("miktar"), "Miktar", pozitif=True)
        birim_fiyat = _sayi(s.get("birim_fiyat"), "Birim fiyat")
        hazir.append((stok, miktar, birim_fiyat, stok.kdv))
    return cari, hazir


def _pb_dogrula(para_birimi):
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise TeklifSiparisHatasi("Geçersiz para birimi.")
    return pb


def _kalemleri_yaz(ts, hazir, kullanici):
    for stok, miktar, birim_fiyat, kdv in hazir:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=ts, stok=stok, miktar=miktar, birim_fiyat=birim_fiyat, kdv=kdv,
            created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def teklif_siparis_olustur(*, belge_tur, yon, cari_id, tarih, satirlar,
                           gecerlilik_teslim_tarihi=None, belge_no="", para_birimi="TRY",
                           aciklama="", kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş başlığı + kalemlerini oluşturur. Yevmiye/stok hareketi ÜRETMEZ."""
    if belge_tur not in TeklifSiparis.BelgeTur.values:
        raise TeklifSiparisHatasi("Geçersiz belge türü.")
    if yon not in TeklifSiparis.Yon.values:
        raise TeklifSiparisHatasi("Geçersiz yön.")
    cari, hazir = _hazirla(cari_id=cari_id, satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    ts = TeklifSiparis.objects.create(
        belge_tur=belge_tur, yon=yon, cari=cari, tarih=tarih,
        gecerlilik_teslim_tarihi=gecerlilik_teslim_tarihi,
        belge_no=(belge_no or "").strip(), para_birimi=pb, aciklama=(aciklama or "").strip(),
        created_by=kullanici, updated_by=kullanici)
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


@transaction.atomic
def teklif_siparis_guncelle(ts: TeklifSiparis, *, cari_id, tarih, satirlar,
                            gecerlilik_teslim_tarihi=None, belge_no="", para_birimi="TRY",
                            aciklama="", kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş başlığı + kalemlerini günceller (belge_tur/yon SABİT — hangi ekrana
    ait olduğunu belirler, değişmez). Eski kalemler soft-delete edilir, yenileri yazılır."""
    from django.utils import timezone
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge düzenlenemez.")
    cari, hazir = _hazirla(cari_id=cari_id, satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    ts.kalemler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    ts.cari, ts.tarih = cari, tarih
    ts.gecerlilik_teslim_tarihi = gecerlilik_teslim_tarihi
    ts.belge_no, ts.para_birimi = (belge_no or "").strip(), pb
    ts.aciklama = (aciklama or "").strip()
    ts.updated_by = kullanici
    ts.save(update_fields=["cari", "tarih", "gecerlilik_teslim_tarihi", "belge_no",
                           "para_birimi", "aciklama", "updated_by", "updated_at"])
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


def teklif_siparis_iptal(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """Belgeyi soft-delete eder (kalemler kalır; geçmiş görüntüleme için)."""
    from django.utils import timezone
    if ts.silindi:
        return ts
    ts.silindi = True
    ts.silindi_at = timezone.now()
    ts.updated_by = kullanici
    ts.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ts
