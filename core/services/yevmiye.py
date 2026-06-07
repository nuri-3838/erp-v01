"""Yevmiye servis katmanı — fiş oluşturma/güncelleme/iptal kuralları tek noktada (spec 3).

Tüm değişmezler burada zorlanır; UI'ya güvenilmez:
- Fişte en az 2 satır.
- Her satırda borç/alacaktan yalnızca biri pozitif (diğeri 0).
- Tutarlar ≥ 0; islem_kuru > 0; TRY ise islem_kuru = 1.
- Hesap var, aktif ve silinmemiş olmalı.
- TL (borç/alacak) = yuvarla(islem_tutari × islem_kuru); TRY'de TL = islem_tutari.
- Dengeli fiş: SUM(borç) = SUM(alacak), aksi halde fiş kaydedilmez (atomik).
- fis_no mali yıl içinde müteselsil/boşluksuz; iptal numarayı korur (yeniden
  kullanılmaz). kur_usd fiş tarihine göre KUR'dan doldurulur (yoksa son yayımlanan;
  o da yoksa boş bırakılır, fiş yine kaydedilir), elle override edilebilir.
- Düzenleme (fis_guncelle): yıl/fiş no korunur; satırlar değiştirilir (eski satır
  kayıtları fiziksel silinip yenileri yazılır — kullanıcı kararı; fiş başlığı yine
  asla fiziksel silinmez, audit updated_by/at korunur). İptal edilmiş fiş düzenlenemez.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.sayi import parse_tr, yuvarla

SIFIR = Decimal("0.00")


class YevmiyeHatasi(ValueError):
    """Geçersiz fiş/satır verisi (kural ihlali)."""


@dataclass
class SatirGirdi:
    """Tek satır girdisi. TL tutarı bu veriden TÜRETİLİR (dışarıdan verilmez)."""

    hesap_kodu: str
    taraf: str  # "B" = borç, "A" = alacak
    islem_tutari: object  # Decimal | int | str (TR biçim) — float YASAK
    islem_pb: str = "TRY"
    islem_kuru: object = Decimal("1")
    aciklama: str = ""


def _dec(deger, alan: str) -> Decimal:
    """Decimal/int/str(TR) → Decimal. float kabul edilmez (kayan nokta yasak)."""
    if isinstance(deger, Decimal):
        return deger
    if isinstance(deger, int) and not isinstance(deger, bool):
        return Decimal(deger)
    if isinstance(deger, str):
        return parse_tr(deger)
    raise YevmiyeHatasi(f"{alan}: sayı Decimal/int/metin olmalı, gelen {type(deger).__name__}")


def kur_usd_bul(tarih: _dt.date):
    """Fiş tarihine göre USD alış kuru: o tarih yoksa son yayımlanan (≤ tarih)."""
    k = (
        Kur.objects.filter(tarih__lte=tarih, silindi=False)
        .order_by("-tarih")
        .first()
    )
    return k.usd_alis if k else None


def kur_usd_birebir(tarih: _dt.date):
    """Yalnız o tarihe ait (BİREBİR) USD alış kuru; yoksa None. Carry-forward YOK —
    fiş kaydı için fiş tarihinin kuru fiilen KUR tablosunda bulunmalıdır."""
    k = Kur.objects.filter(tarih=tarih, silindi=False, usd_alis__isnull=False).first()
    return k.usd_alis if k else None


def _kur_usd_coz(kur_usd, tarih: _dt.date):
    """kur_usd None ise fiş tarihine göre KUR'dan doldur; verilmişse Decimal'e çevir.
    Çözülemezse (o tarih için kur yok) HATA: her fişin USD karşılığı zorunludur."""
    if kur_usd is None:
        bulunan = kur_usd_birebir(tarih)
        if bulunan is None:
            raise YevmiyeHatasi(
                f"{tarih:%d.%m.%Y} için USD kuru yok; Kurlar ekranından bu tarihi çekmeden "
                f"fiş kaydedilemez (her fişin USD karşılığı zorunludur)."
            )
        return bulunan
    if not isinstance(kur_usd, Decimal):
        return _dec(kur_usd, "kur_usd")
    return kur_usd


def _satirlari_dogrula(satirlar) -> list[dict]:
    """Satırları doğrular, TL'leri türetir ve dengeyi denetler (oluştur/güncelle ortak).

    Geçerliyse her satır için ``YevmiyeSatir`` alanlarını içeren dict listesi döner;
    bir kural ihlalinde :class:`YevmiyeHatasi` yükseltir (hiçbir DB değişikliği yapmaz).
    """
    if len(satirlar) < 2:
        raise YevmiyeHatasi("Fişte en az 2 satır olmalı.")

    hazir = []
    toplam_borc = SIFIR
    toplam_alacak = SIFIR

    for i, g in enumerate(satirlar, start=1):
        taraf = (g.taraf or "").strip().upper()
        if taraf not in ("B", "A"):
            raise YevmiyeHatasi(f"Satır {i}: taraf 'B' veya 'A' olmalı.")

        pb = (g.islem_pb or "TRY").strip().upper()
        if pb not in YevmiyeSatir.IslemPB.values:
            raise YevmiyeHatasi(f"Satır {i}: geçersiz işlem PB {pb!r}.")

        tutar = _dec(g.islem_tutari, f"Satır {i} islem_tutari")
        kur = _dec(g.islem_kuru, f"Satır {i} islem_kuru")
        if tutar < 0:
            raise YevmiyeHatasi(f"Satır {i}: tutar negatif olamaz.")
        if kur <= 0:
            raise YevmiyeHatasi(f"Satır {i}: islem_kuru > 0 olmalı.")
        if pb == "TRY" and kur != Decimal("1"):
            raise YevmiyeHatasi(f"Satır {i}: TRY için islem_kuru 1 olmalı.")

        tl = yuvarla(tutar * kur, 2)
        if tl <= 0:
            raise YevmiyeHatasi(f"Satır {i}: TL tutarı 0'dan büyük olmalı.")

        hesap = (
            HesapPlani.objects.filter(
                hesap_kodu=(g.hesap_kodu or "").strip(), aktif=True, silindi=False
            ).first()
        )
        if hesap is None:
            raise YevmiyeHatasi(
                f"Satır {i}: hesap bulunamadı/aktif değil: {g.hesap_kodu!r}"
            )
        if hesap.alt_hesaplar.filter(silindi=False).exists():
            raise YevmiyeHatasi(
                f"Satır {i}: {hesap.hesap_kodu} üst hesaptır; fiş yalnızca yaprak hesaba kesilir."
            )

        if taraf == "B":
            borc, alacak = tl, SIFIR
        else:
            borc, alacak = SIFIR, tl
        toplam_borc += borc
        toplam_alacak += alacak

        hazir.append(
            dict(hesap=hesap, borc=borc, alacak=alacak, islem_pb=pb,
                 islem_tutari=yuvarla(tutar, 2), islem_kuru=kur,
                 aciklama=buyuk_harf_tr((g.aciklama or "").strip()))
        )

    if yuvarla(toplam_borc, 2) != yuvarla(toplam_alacak, 2):
        raise YevmiyeHatasi(
            f"Fiş dengesiz: borç {toplam_borc} ≠ alacak {toplam_alacak}."
        )
    return hazir


def _sonraki_fis_no(yil: int) -> int:
    """Mali yıl içinde sıradaki fiş no (iptaller dahil; numara yeniden kullanılmaz)."""
    return (YevmiyeFisi.objects.filter(yil=yil).aggregate(m=Max("fis_no"))["m"] or 0) + 1


@transaction.atomic
def fis_olustur(*, tarih, satirlar, aciklama="", kur_usd=None,
                kaynak=YevmiyeFisi.Kaynak.MANUEL, kullanici=None) -> YevmiyeFisi:
    """Dengeli bir yevmiye fişini satırlarıyla atomik oluşturur.

    `satirlar`: `SatirGirdi` listesi. Kurallardan biri ihlal edilirse hiçbir şey
    yazılmaz (transaction geri alınır) ve :class:`YevmiyeHatasi` yükselir.

    Numara çakışması (eşzamanlı/otomatik fiş): (yıl, fiş_no) benzersiz kısıtı
    çarparsa savepoint geri alınır ve bir sonraki numarayla yeniden denenir.
    """
    if not isinstance(tarih, _dt.date):
        raise YevmiyeHatasi("tarih bir date olmalı.")

    hazir = _satirlari_dogrula(satirlar)
    yil = tarih.year
    kur = _kur_usd_coz(kur_usd, tarih)
    aciklama = buyuk_harf_tr((aciklama or "").strip())

    son_hata = None
    for _ in range(10):
        try:
            with transaction.atomic():   # savepoint: çakışmada yalnız bunu geri al
                fis = YevmiyeFisi.objects.create(
                    yil=yil, fis_no=_sonraki_fis_no(yil), tarih=tarih,
                    aciklama=aciklama, kaynak=kaynak, kur_usd=kur,
                    created_by=kullanici, updated_by=kullanici,
                )
                for s in hazir:
                    YevmiyeSatir.objects.create(
                        fis=fis, created_by=kullanici, updated_by=kullanici, **s
                    )
            return fis
        except IntegrityError as e:
            son_hata = e   # numara kapılmış olabilir; bir sonrakiyle tekrar dene
    raise YevmiyeHatasi(
        "Fiş numarası üretilemedi (çok fazla eşzamanlı kayıt). "
        f"Lütfen tekrar deneyin. ({son_hata})"
    )


@transaction.atomic
def fis_guncelle(fis: YevmiyeFisi, *, tarih, satirlar, aciklama="",
                 kur_usd=None, kullanici=None) -> YevmiyeFisi:
    """Mevcut (iptal edilmemiş) fişi atomik günceller.

    - Yıl ve fiş no DEĞİŞMEZ (müteselsil/boşluksuz numara korunur); bu yüzden yeni
      tarihin yılı fişin yılıyla aynı olmalıdır.
    - Denge (borç=alacak) ve tüm satır kuralları yeniden zorlanır.
    - Satırlar değiştirilir: eski satır kayıtları fiziksel silinip yenileri yazılır.
    - Audit: updated_by/updated_at güncellenir; created_by/at korunur.

    Kural ihlalinde hiçbir şey değişmez (transaction geri alınır).
    """
    if fis.silindi:
        raise YevmiyeHatasi("İptal edilmiş fiş düzenlenemez.")
    if not isinstance(tarih, _dt.date):
        raise YevmiyeHatasi("tarih bir date olmalı.")
    if tarih.year != fis.yil:
        raise YevmiyeHatasi(
            f"Düzenlemede fişin mali yılı ({fis.yil}) değiştirilemez; "
            f"tarih {fis.yil} yılı içinde olmalı."
        )

    hazir = _satirlari_dogrula(satirlar)

    fis.tarih = tarih
    fis.aciklama = buyuk_harf_tr((aciklama or "").strip())
    fis.kur_usd = _kur_usd_coz(kur_usd, tarih)
    fis.updated_by = kullanici
    fis.save(update_fields=["tarih", "aciklama", "kur_usd", "updated_by", "updated_at"])

    # Eski satırlar fiziksel silinir, yenileri yazılır (kullanıcı kararı).
    fis.satirlar.all().delete()
    for s in hazir:
        YevmiyeSatir.objects.create(
            fis=fis, created_by=kullanici, updated_by=kullanici, **s
        )
    return fis


@transaction.atomic
def fis_iptal(fis: YevmiyeFisi, kullanici=None) -> YevmiyeFisi:
    """Fişi soft-delete ile iptal eder. Numara korunur (yeniden kullanılmaz)."""
    if fis.silindi:
        return fis
    fis.silindi = True
    fis.silindi_at = timezone.now()
    fis.updated_by = kullanici
    fis.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    fis.satirlar.update(silindi=True, silindi_at=timezone.now())
    return fis
