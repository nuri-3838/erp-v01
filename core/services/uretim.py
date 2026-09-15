"""ÜRETİM modülü servis katmanı — Ürün Ağacı (BOM) tanımları + Üretim Emirleri.

Bağımsız, sıfırdan kurulan bir Stok↔Stok reçetesi ve numaralı üretim kaydı — FASON
modülündeki (kesilmiş parça / kesildigi_profil) kavramlarla hiçbir ilişkisi yoktur. Üretim
Emri onaylandığında yalnızca miktar/stok hareketi (core.services.hareket) üretir; hiçbir
YevmiyeFisi/YevmiyeSatir oluşturmaz — maliyetin muhasebeye yansıtılması ay sonu mali
müşavirin elle yapacağı ayrı bir iştir (bkz. docs/ERP_v0.1_kapsam.md v0.4)."""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from core.models import (
    Depo, Stok, StokHareket, UretimEmri, UretimEmriSatir, UrunAgaci, UrunAgaciSatir,
)
from core.sayi import SayiHatasi, parse_tr
from core.services.hareket import HareketHatasi, hareket_ekle


class UretimHatasi(ValueError):
    """Üretim modülü kural ihlali (Türkçe mesaj)."""


def _sayi_coz(deger, hata_mesaji):
    if isinstance(deger, Decimal):
        return deger
    try:
        return parse_tr(deger)
    except SayiHatasi:
        raise UretimHatasi(hata_mesaji)


# === Ürün Ağacı Tanımları ===

def aktif_urun_agaclari():
    return (UrunAgaci.objects.filter(silindi=False)
            .select_related("mamul")
            .annotate(satir_sayisi=Count("satirlar", filter=Q(satirlar__silindi=False)))
            .order_by("mamul__kod"))


def urun_agaci_satirlari(urun_agaci):
    return (urun_agaci.satirlar.filter(silindi=False)
            .select_related("bilesen").order_by("sira", "pk"))


def urun_agaci_olan_mamul_idler():
    return aktif_urun_agaclari().values_list("mamul_id", flat=True)


def _mamul_coz(mamul_id):
    mamul = Stok.objects.filter(pk=mamul_id, silindi=False, satis_urunu=True).first()
    if not mamul:
        raise UretimHatasi("Mamul bulunamadı (yalnız satış ürünleri için ürün ağacı tanımlanır).")
    return mamul


def _bilesen_satirlarini_dogrula(mamul, satirlar):
    """satirlar: [(Stok, Decimal), ...]. En az 1 satır, bileşen tekrarsız, mamul kendi
    bileşeni olamaz, miktar > 0."""
    if not satirlar:
        raise UretimHatasi("En az bir bileşen satırı gerekli.")
    gorulen = set()
    for bilesen, miktar in satirlar:
        if bilesen.pk == mamul.pk:
            raise UretimHatasi("Mamul kendi bileşeni olamaz.")
        if bilesen.pk in gorulen:
            raise UretimHatasi(f"{bilesen.kod} birden fazla satırda tekrarlanamaz.")
        gorulen.add(bilesen.pk)
        if miktar <= 0:
            raise UretimHatasi("Bileşen miktarı sıfırdan büyük olmalı.")


@transaction.atomic
def urun_agaci_olustur(*, mamul_id, satirlar, aciklama="", kullanici=None) -> UrunAgaci:
    mamul = _mamul_coz(mamul_id)
    if UrunAgaci.objects.filter(silindi=False, mamul=mamul).exists():
        raise UretimHatasi("Bu mamul için zaten aktif bir ürün ağacı tanımlı.")
    _bilesen_satirlarini_dogrula(mamul, satirlar)
    agac = UrunAgaci.objects.create(
        mamul=mamul, aciklama=(aciklama or "").strip(),
        created_by=kullanici, updated_by=kullanici)
    for i, (bilesen, miktar) in enumerate(satirlar, start=1):
        UrunAgaciSatir.objects.create(
            urun_agaci=agac, bilesen=bilesen, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return agac


@transaction.atomic
def urun_agaci_guncelle(agac: UrunAgaci, *, satirlar, aciklama="", kullanici=None) -> UrunAgaci:
    if agac.silindi:
        raise UretimHatasi("Silinmiş ürün ağacı düzenlenemez.")
    _bilesen_satirlarini_dogrula(agac.mamul, satirlar)
    agac.satirlar.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    agac.aciklama = (aciklama or "").strip()
    agac.updated_by = kullanici
    agac.save(update_fields=["aciklama", "updated_by", "updated_at"])
    for i, (bilesen, miktar) in enumerate(satirlar, start=1):
        UrunAgaciSatir.objects.create(
            urun_agaci=agac, bilesen=bilesen, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return agac


def urun_agaci_sil(agac: UrunAgaci, kullanici=None) -> UrunAgaci:
    if agac.silindi:
        return agac
    if agac.uretim_emirleri.filter(silindi=False).exists():
        raise UretimHatasi("Bu ürün ağacına bağlı üretim emri var; silinemez.")
    agac.silindi = True
    agac.silindi_at = timezone.now()
    agac.updated_by = kullanici
    agac.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return agac


# === Üretim Emirleri ===

def _sonraki_sira(yil):
    m = UretimEmri.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


def emri_satirlari(emir):
    return (emir.satirlar.filter(silindi=False)
            .select_related("bilesen").order_by("sira", "pk"))


@transaction.atomic
def uretim_emri_olustur(*, mamul_id, planlanan_miktar, depo_id, tarih, aciklama="",
                        kullanici=None) -> UretimEmri:
    mamul = _mamul_coz(mamul_id)
    agac = UrunAgaci.objects.filter(silindi=False, mamul=mamul).first()
    if not agac:
        raise UretimHatasi("Bu mamul için tanımlı bir ürün ağacı yok; önce tanımlayın.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if not depo:
        raise UretimHatasi("Depo bulunamadı.")
    miktar = _sayi_coz(planlanan_miktar, "Planlanan miktar geçerli bir sayı olmalı.")
    if miktar <= 0:
        raise UretimHatasi("Planlanan miktar sıfırdan büyük olmalı.")
    bom_satirlari = list(urun_agaci_satirlari(agac))
    if not bom_satirlari:
        raise UretimHatasi("Ürün ağacında hiç bileşen satırı yok.")

    yil = tarih.year
    emir = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(yil)
                emir = UretimEmri.objects.create(
                    yil=yil, sira=sira, no=f"UE-{yil}-{sira:04d}",
                    mamul=mamul, urun_agaci=agac, depo=depo, tarih=tarih,
                    planlanan_miktar=miktar, aciklama=(aciklama or "").strip(),
                    durum=UretimEmri.Durum.TASLAK,
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if emir is None:
        raise UretimHatasi("Emir numarası üretilemedi; tekrar deneyin.")

    for i, satir in enumerate(bom_satirlari, start=1):
        gerekli = satir.miktar * miktar
        UretimEmriSatir.objects.create(
            emir=emir, bilesen=satir.bilesen, planlanan_miktar=gerekli,
            gerceklesen_miktar=gerekli, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return emir


@transaction.atomic
def uretim_emri_satir_guncelle(satir: UretimEmriSatir, *, gerceklesen_miktar, kullanici=None):
    if satir.emir.durum != UretimEmri.Durum.TASLAK:
        raise UretimHatasi("Yalnız taslak emrin satırları düzenlenebilir.")
    m = _sayi_coz(gerceklesen_miktar, "Gerçekleşen miktar geçerli bir sayı olmalı.")
    if m < 0:
        raise UretimHatasi("Gerçekleşen miktar negatif olamaz.")
    satir.gerceklesen_miktar = m
    satir.updated_by = kullanici
    satir.save(update_fields=["gerceklesen_miktar", "updated_by", "updated_at"])
    return satir


@transaction.atomic
def uretim_emri_onayla(emir: UretimEmri, kullanici=None) -> UretimEmri:
    """TASLAK → ONAYLI: her bileşen için depo ÇIKIŞ + mamul için depo GİRİŞ StokHareket'i
    (Kaynak=URETIM) yazar; hiçbir YevmiyeFisi/YevmiyeSatir üretmez. İdempotent (zaten
    onaylıysa sessiz döner). Yetersiz stok varsa tüm işlem geri alınır (atomic)."""
    if emir.silindi:
        raise UretimHatasi("İptal edilmiş emir onaylanamaz.")
    if emir.durum == UretimEmri.Durum.ONAYLI:
        return emir
    satirlar = list(emri_satirlari(emir))
    for satir in satirlar:
        try:
            hareket_ekle(
                stok_id=satir.bilesen_id, depo_id=emir.depo_id, tarih=emir.tarih,
                tur=StokHareket.Tur.CIKIS, miktar=satir.gerceklesen_miktar,
                aciklama=f"Üretim emri {emir.no}", kaynak=StokHareket.Kaynak.URETIM,
                uretim_emri_satir=satir, kullanici=kullanici)
        except HareketHatasi as e:
            raise UretimHatasi(str(e))
    hareket_ekle(
        stok_id=emir.mamul_id, depo_id=emir.depo_id, tarih=emir.tarih,
        tur=StokHareket.Tur.GIRIS, miktar=emir.planlanan_miktar,
        aciklama=f"Üretim emri {emir.no}", kaynak=StokHareket.Kaynak.URETIM,
        kullanici=kullanici)
    emir.durum = UretimEmri.Durum.ONAYLI
    emir.updated_by = kullanici
    emir.save(update_fields=["durum", "updated_by", "updated_at"])
    return emir


def uretim_emri_sil(emir: UretimEmri, kullanici=None) -> UretimEmri:
    if emir.silindi:
        return emir
    if emir.durum == UretimEmri.Durum.ONAYLI:
        raise UretimHatasi("Onaylı emir iptal edilemez.")
    emir.silindi = True
    emir.silindi_at = timezone.now()
    emir.updated_by = kullanici
    emir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return emir
