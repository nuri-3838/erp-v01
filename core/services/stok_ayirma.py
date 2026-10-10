"""Üretim siparişi STOK AYIRMA (rezervasyon) katmanı — bkz. docs/uretim-siparisi-plan.md (adım 1).

Ayırma stok hareketi DEĞİLDİR: eldekiyi değiştirmez. Tek kural: ``kullanılabilir = eldeki − açık üretim siparişlerinin ayrılan toplamı``.
Kural YUMUŞAKTIR — hiçbir çıkış engellenmez; ayrılmış stok başka yerde tüketilmişse kullanılabilir EKSİYE düşer ve ekranlar uyarır.
Bu modül (sipariş, stok) ayırmasının TEK yazma noktasıdır (``ayirma_ayarla`` / ``ayirma_ekle``); sonraki adımların açılış / revize /
kayıt onayı / sevk akışları buradan geçer. Ayırma depo bazında değildir (tüm depolar)."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import StokAyirma
from core.services.hareket import toplu_eldeki

SIFIR = Decimal("0")


class AyirmaHatasi(ValueError):
    """Stok ayırma kural ihlali (Türkçe mesaj)."""


def _pk(x):
    return getattr(x, "pk", x)


def _sayi(miktar) -> Decimal:
    return miktar if isinstance(miktar, Decimal) else Decimal(str(miktar))


def acik_ayirmalar():
    """Kullanılabilir hesabına giren ayırmalar: silinmemiş satır, silinmemiş üretim siparişi.
    (Adım 3'te sipariş durumu gelince yalnız AÇIK siparişler sayılacak — kapalı/iptal siparişin ayırması zaten kapanışta sıfırlanır.)"""
    return StokAyirma.objects.filter(silindi=False, uretim_emri__silindi=False)


def emir_ayirmalari(emir):
    """Bir üretim siparişinin aktif ayırma satırları (stok koduna göre)."""
    return acik_ayirmalar().filter(uretim_emri_id=_pk(emir)).select_related("stok__uretim_birimi").order_by("stok__kod")


def ayrilan_miktar(emir, stok) -> Decimal:
    satir = acik_ayirmalar().filter(uretim_emri_id=_pk(emir), stok_id=_pk(stok)).first()
    return satir.miktar if satir else SIFIR


def ayirma_haritasi(stok_idler, haric_emir=None) -> dict:
    """{stok id: ayrılan toplam} — TEK gruplu sorgu; ``haric_emir`` verilirse o siparişin kendi ayırmaları sayılmaz (revize/yeniden planlamada
    sipariş kendi ayırdığını kullanılabilir görür). Ayırması olmayan stok sözlükte yoktur."""
    idler = list(stok_idler)
    if not idler:
        return {}
    qs = acik_ayirmalar().filter(stok_id__in=idler)
    if haric_emir is not None:
        qs = qs.exclude(uretim_emri_id=_pk(haric_emir))
    return {r["stok_id"]: r["t"] for r in qs.values("stok_id").annotate(t=Sum("miktar"))}


def toplu_kullanilabilir(stok_idler, haric_emir=None, eldeki=None) -> dict:
    """{stok id: {"eldeki", "ayrilan", "kullanilabilir"}} — kullanılabilir = eldeki − ayrılan; EKSİ olabilir (ayrılmış stok fiilen tüketilmiş:
    yumuşak kural, ekran uyarır). ``eldeki`` verilirse (ör. depo filtreli harita) yeniden sorgulanmaz; verilmezse tüm depolar (``toplu_eldeki``).
    Sabit 1 (+1) sorgu."""
    idler = list(stok_idler)
    eldeki = toplu_eldeki(idler) if eldeki is None else eldeki
    ayrilan = ayirma_haritasi(idler, haric_emir)
    sonuc = {}
    for pk in idler:
        e, a = eldeki.get(pk, SIFIR), ayrilan.get(pk, SIFIR)
        sonuc[pk] = {"eldeki": e, "ayrilan": a, "kullanilabilir": e - a}
    return sonuc


def kullanilabilir(stok, haric_emir=None) -> Decimal:
    return toplu_kullanilabilir([_pk(stok)], haric_emir)[_pk(stok)]["kullanilabilir"]


@transaction.atomic
def ayirma_ayarla(emir, stok, miktar, kullanici=None):
    """(sipariş, stok) ayırmasını ``miktar``a ÇEKER (artış/azalış fark etmez). 0 → aktif satır kapatılır (soft-delete), None döner.
    Negatif reddedilir. Satır kilitlenir (eş zamanlı iki olay üst üste yazmasın)."""
    miktar = _sayi(miktar)
    if miktar < 0:
        raise AyirmaHatasi("Ayrılan miktar negatif olamaz.")
    emir_id, stok_id = _pk(emir), _pk(stok)
    satir = (StokAyirma.objects.select_for_update().filter(uretim_emri_id=emir_id, stok_id=stok_id, silindi=False).first())
    if miktar == 0:
        if satir is not None:
            satir.silindi = True
            satir.silindi_at = timezone.now()
            satir.updated_by = kullanici
            satir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        return None
    if satir is None:
        return StokAyirma.objects.create(uretim_emri_id=emir_id, stok_id=stok_id, miktar=miktar, created_by=kullanici, updated_by=kullanici)
    if satir.miktar != miktar:
        satir.miktar = miktar
        satir.updated_by = kullanici
        satir.save(update_fields=["miktar", "updated_by", "updated_at"])
    return satir


@transaction.atomic
def ayirma_ekle(emir, stok, fark, kullanici=None):
    """Mevcut ayırmaya ``fark`` ekler (negatif = düşer); sıfırın altına inmez, sıfırda satır kapanır. Döner: yeni satır ya da None."""
    yeni = ayrilan_miktar(emir, stok) + _sayi(fark)
    return ayirma_ayarla(emir, stok, max(SIFIR, yeni), kullanici)
