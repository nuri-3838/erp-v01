"""Fatura (FATURALAR) servis katmanı — Alış/Satış faturasından OTOMATİK yevmiye.

Fatura kaydedildiğinde dengeli bir yevmiye fişi üretilir (mevcut fis_olustur ile)
ve faturaya bağlanır. Muhasebe haritası:
  - Mal/gelir hesabı  = stok kategorisi × fatura tipi (KategoriHesap).
  - KDV hesabı         = stoğun KDV oranının BORÇ (alış 191) / ALACAK (satış 391) hesabı.
  - Karşı taraf        = carinin muhasebe hesabı (320.../120... yaprak).
ALIŞ:  Borç mal + Borç KDV  / Alacak cari.
SATIŞ: Alacak gelir + Alacak KDV / Borç cari.

İlk dilim: TL (kur=1). Tutarlar satırlardan; her şey atomik (eksik harita -> hiç kayıt yok).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (Cari, Fatura, FaturaSatir, FaturaTipi, HesapPlani,
                         KategoriHesap, Stok, YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_iptal,
                                   fis_olustur)

SIFIR = Decimal("0.00")


class FaturaHatasi(ValueError):
    """Fatura kural ihlali (Türkçe mesaj)."""


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise FaturaHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise FaturaHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if not pozitif and d < 0:
        raise FaturaHatasi(f"{etiket} negatif olamaz.")
    return d


def aktif_faturalar():
    return (Fatura.objects.filter(silindi=False)
            .select_related("tip", "cari", "fis").order_by("-tarih", "-id"))


@transaction.atomic
def fatura_olustur(*, tip_id, cari_id, tarih, satirlar, fatura_no="",
                   para_birimi="TRY", kur=Decimal("1"), kullanici=None) -> Fatura:
    """Faturayı + otomatik dengeli yevmiye fişini atomik oluşturur.

    `satirlar`: dict listesi [{stok_id, miktar, birim_fiyat}]. Eksik muhasebe
    haritası / dengesizlik -> FaturaHatasi (hiçbir şey kaydedilmez).
    """
    tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first()
    if tip is None:
        raise FaturaHatasi("Fatura tipi bulunamadı.")
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FaturaHatasi("Cari bulunamadı.")
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")

    # Carinin muhasebe (yaprak) hesabı
    cari_hesap = HesapPlani.objects.filter(
        hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if cari_hesap is None:
        raise FaturaHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")

    alis = (tip.yon == FaturaTipi.Yon.ALIS)
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise FaturaHatasi("Geçersiz para birimi.")
    kur = _sayi(kur, "Kur", pozitif=True)

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}          # hesap_kodu -> KDV tutarı (PB)
    toplam_mal = SIFIR
    toplam_kdv = SIFIR
    hazir = []                     # FaturaSatir için (stok, miktar, fiyat, kdv)

    for i, g in enumerate(satirlar, start=1):
        stok = Stok.objects.filter(pk=g.get("stok_id"), silindi=False).first()
        if stok is None:
            raise FaturaHatasi(f"Satır {i}: stok bulunamadı.")
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")

        # Mal/gelir hesabı: kategori × fatura tipi
        kh = KategoriHesap.objects.filter(
            kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
        if kh is None:
            raise FaturaHatasi(
                f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")

        satir_tutar = yuvarla(miktar * birim, 2)
        kdv = stok.kdv
        oran = kdv.oran if kdv else SIFIR
        satir_kdv = yuvarla(satir_tutar * oran / Decimal("100"), 2)
        toplam_mal += satir_tutar
        toplam_kdv += satir_kdv

        # Mal/gelir satırı (alış: Borç, satış: Alacak)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=kh.hesap.hesap_kodu, taraf=("B" if alis else "A"),
            islem_tutari=satir_tutar, islem_pb=pb, islem_kuru=kur, aciklama=stok.ad))

        # KDV hesabı (alış: borç hesabı 191, satış: alacak hesabı 391)
        if satir_kdv > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {stok.kod} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + satir_kdv)

        hazir.append((stok, miktar, birim, kdv))

    # KDV satırları (alış: Borç, satış: Alacak)
    for hkod, tutar in kdv_hesap_toplam.items():
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf=("B" if alis else "A"),
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV"))

    # Karşı taraf (cari): alış -> Alacak, satış -> Borç
    genel = toplam_mal + toplam_kdv
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=("A" if alis else "B"),
        islem_tutari=genel, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan))

    fatura_no = (fatura_no or "").strip()
    aciklama = buyuk_harf_tr(f"{tip.ad} - {cari.unvan}" + (f" - {fatura_no}" if fatura_no else ""))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=yevmiye_satirlari, aciklama=aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.FATURA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))

    fatura = Fatura.objects.create(
        tip=tip, cari=cari, tarih=tarih, fatura_no=fatura_no, para_birimi=pb,
        kur=kur, fis=fis, created_by=kullanici, updated_by=kullanici)
    for stok, miktar, birim, kdv in hazir:
        FaturaSatir.objects.create(
            fatura=fatura, stok=stok, miktar=miktar, birim_fiyat=birim, kdv=kdv,
            created_by=kullanici, updated_by=kullanici)
    return fatura


@transaction.atomic
def fatura_iptal(fatura: Fatura, kullanici=None) -> Fatura:
    """Faturayı soft-delete eder ve bağlı yevmiye fişini de iptal eder (ters değil, iptal)."""
    from django.utils import timezone
    if fatura.silindi:
        return fatura
    if fatura.fis_id and not fatura.fis.silindi:
        fis_iptal(fatura.fis, kullanici=kullanici)
    fatura.silindi = True
    fatura.silindi_at = timezone.now()
    fatura.updated_by = kullanici
    fatura.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return fatura
