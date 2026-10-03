"""Yatırım Projesi (DURAN VARLIK FAZ 1-3) servis katmanı.

258 (Yapılmakta Olan Yatırımlar) hesabında biriken alış gider faturası kalemlerinin
gruplandığı proje kartı — CRUD + toplam hesaplama + aktifleştirme (FAZ 3: tek fişle
253/254/255/260'a aktarım + DuranVarlik kartı üretimi) + geri alma.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import DuranVarlik, HesapPlani, YatirimProjesi, YevmiyeFisi

SIFIR = Decimal("0.00")


class YatirimProjesiHatasi(ValueError):
    """Yatırım projesi kural ihlali (Türkçe mesaj)."""


def aktif_projeler():
    return YatirimProjesi.objects.filter(silindi=False).order_by("-created_at")


def sonraki_proje_kodu() -> str:
    n = 1
    while YatirimProjesi.objects.filter(kod=f"YP-{str(n).zfill(4)}", silindi=False).exists():
        n += 1
    return f"YP-{str(n).zfill(4)}"


def proje_olustur(*, ad, aciklama="", kullanici=None) -> YatirimProjesi:
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise YatirimProjesiHatasi("Proje adı boş olamaz.")
    return YatirimProjesi.objects.create(
        kod=sonraki_proje_kodu(), ad=ad, aciklama=(aciklama or "").strip(),
        created_by=kullanici, updated_by=kullanici)


def proje_toplami(proje: YatirimProjesi) -> Decimal:
    """Projeye bağlı tüm (silinmemiş faturadaki silinmemiş) kalemlerin KDV HARİÇ toplamı."""
    toplam = SIFIR
    for s in proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False):
        toplam += s.tutar
    return toplam


def proje_fatura_sayisi(proje: YatirimProjesi) -> int:
    return (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
            .values("fatura_id").distinct().count())


def proje_aktiflestir(proje: YatirimProjesi, *, tarih, satirlar, kullanici=None) -> YatirimProjesi:
    """258 toplamını birden fazla duran varlık satırına böler; tek transaction'da:
    tek yevmiye fişi (her satır: hedef hesap borç / 258 alacak) + her satır için
    kaynak=PROJE bir DuranVarlik kartı (projenin tüm fatura kalemlerine bağlı) üretir,
    proje durumu AKTIFLESTI'ye geçer. ``satirlar``: [{"hesap_id", "varlik_adi", "tutar"}, ...]
    — toplamı proje toplamına kuruşuna eşit olmalı."""
    from core.services.duran_varlik import sonraki_demirbas_kodu
    from core.services.hesap_plani import duran_varlik_karti_hesaplari
    from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

    if proje.silindi or proje.durum != YatirimProjesi.Durum.DEVAM:
        raise YatirimProjesiHatasi("Yalnız 'Devam Ediyor' durumundaki bir proje aktifleştirilebilir.")
    toplam = proje_toplami(proje)
    if toplam <= SIFIR:
        raise YatirimProjesiHatasi("Projede aktifleştirilecek tutar yok.")
    if not satirlar:
        raise YatirimProjesiHatasi("En az bir satır girilmelidir.")

    izinli_hesap_pk = set(duran_varlik_karti_hesaplari().values_list("pk", flat=True))
    hazir = []
    satir_toplam = SIFIR
    for i, s in enumerate(satirlar, start=1):
        hesap_id = s.get("hesap_id")
        varlik_adi = buyuk_harf_tr((s.get("varlik_adi") or "").strip())
        tutar = s.get("tutar")
        if hesap_id not in izinli_hesap_pk:
            raise YatirimProjesiHatasi(
                f"Satır {i}: geçerli bir duran varlık hesabı seçin (253/254/255/260).")
        if not varlik_adi:
            raise YatirimProjesiHatasi(f"Satır {i}: varlık adı boş olamaz.")
        if tutar is None or tutar <= SIFIR:
            raise YatirimProjesiHatasi(f"Satır {i}: tutar sıfırdan büyük olmalı.")
        hazir.append({"hesap_id": hesap_id, "varlik_adi": varlik_adi, "tutar": tutar})
        satir_toplam += tutar

    if satir_toplam != toplam:
        raise YatirimProjesiHatasi(
            f"Satır toplamı ({satir_toplam}) proje toplamına ({toplam}) kuruşuna eşit olmalı.")

    proje_satirlari = list(proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False))
    kaynak_kodlari = {s.hesap.hesap_kodu for s in proje_satirlari}
    if len(kaynak_kodlari) != 1:
        raise YatirimProjesiHatasi(
            "Proje kalemleri birden fazla 258 alt hesabına dağılmış; aktifleştirme tek hesap bekler.")
    kaynak_258_kodu = kaynak_kodlari.pop()

    hesap_cache = {h.pk: h for h in HesapPlani.objects.filter(
        pk__in=[r["hesap_id"] for r in hazir])}

    with transaction.atomic():
        fis_satirlari = []
        for row in hazir:
            hedef_hesap = hesap_cache[row["hesap_id"]]
            aciklama = f"{proje.kod} — {proje.ad} / {row['varlik_adi']}"
            fis_satirlari.append(SatirGirdi(hesap_kodu=hedef_hesap.hesap_kodu, taraf="B",
                                            islem_tutari=row["tutar"], aciklama=aciklama))
            fis_satirlari.append(SatirGirdi(hesap_kodu=kaynak_258_kodu, taraf="A",
                                            islem_tutari=row["tutar"], aciklama=aciklama))
        try:
            fis = fis_olustur(
                tarih=tarih, satirlar=fis_satirlari,
                aciklama=f"{proje.kod} — {proje.ad} aktifleştirme",
                kaynak=YevmiyeFisi.Kaynak.YATIRIM, kullanici=kullanici)
        except YevmiyeHatasi as e:
            raise YatirimProjesiHatasi(str(e))

        for row in hazir:
            varlik = DuranVarlik.objects.create(
                demirbas_kodu=sonraki_demirbas_kodu(), ad=row["varlik_adi"],
                hesap_id=row["hesap_id"], aktiflestirme_tarihi=tarih, maliyet=row["tutar"],
                durum=DuranVarlik.Durum.AKTIF, kaynak=DuranVarlik.Kaynak.PROJE,
                yatirim_projesi=proje, created_by=kullanici, updated_by=kullanici)
            if proje_satirlari:
                varlik.fatura_satirlari.set(proje_satirlari)

        proje.durum = YatirimProjesi.Durum.AKTIFLESTI
        proje.aktiflestirme_fisi = fis
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "aktiflestirme_fisi", "updated_by", "updated_at"])

    return proje


def proje_geri_al(proje: YatirimProjesi, *, kullanici=None) -> YatirimProjesi:
    """Aktifleştirmeyi geri alır: fişi iptal eder, bu aktifleştirmeden üretilen
    DuranVarlik kartlarını siler, proje DEVAM'a döner. Yetki kontrolü (yalnız
    yönetici) ÇAĞIRAN katmanda (view) yapılır — bu fonksiyon yalnız iş kuralını
    zorlar."""
    from django.utils import timezone

    from core.services.yevmiye import fis_iptal

    if proje.durum != YatirimProjesi.Durum.AKTIFLESTI:
        raise YatirimProjesiHatasi("Yalnız 'Aktifleşti' durumundaki bir proje geri alınabilir.")
    if not proje.aktiflestirme_fisi_id:
        raise YatirimProjesiHatasi("Projeye bağlı bir aktifleştirme fişi bulunamadı.")

    with transaction.atomic():
        fis_iptal(proje.aktiflestirme_fisi, kullanici=kullanici)
        DuranVarlik.objects.filter(yatirim_projesi=proje, silindi=False).update(
            silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        proje.durum = YatirimProjesi.Durum.DEVAM
        proje.aktiflestirme_fisi = None
        proje.updated_by = kullanici
        proje.save(update_fields=["durum", "aktiflestirme_fisi", "updated_by", "updated_at"])

    return proje
