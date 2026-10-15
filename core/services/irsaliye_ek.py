"""İrsaliye ekleri (YALNIZ PDF ve resim; 2026-10-15). Kurallar fatura ekiyle AYNI (uzantı beyaz listesi, XML reddi, 10 MB, içerik imzası, resim WebP,
özel depo, UUID ad — doğrulama ``fatura_ek.dosya_hazirla`` ortak). Fark: silme KALICIDIR — kayıt fiziksel silinir ve dosya diskten kalkar (soft-delete yok).
Faturaya dönüşümde ekler irsaliyede kalır (faturaya kopyalanmaz). Muhasebeye/stoğa dokunmaz; onaylı irsaliyeye de ek eklenebilir/silinebilir."""
from __future__ import annotations

import logging

from django.db import transaction

from core.models import FaturaEk, IrsaliyeEk, TeklifSiparis
from core.services import fatura_ek as fatura_ek_servis
from core.services.fatura_ek import FaturaEkHatasi

log = logging.getLogger("core.irsaliye_ek")


class IrsaliyeEkHatasi(FaturaEkHatasi):
    """İrsaliye eki kural ihlali (Türkçe mesaj)."""


def _irsaliye_mi(ts: TeklifSiparis) -> bool:
    return ts.belge_tur == TeklifSiparis.BelgeTur.IRSALIYE


def ek_listele(irsaliye: TeklifSiparis):
    return IrsaliyeEk.objects.filter(irsaliye=irsaliye, silindi=False)


def ek_ekle(irsaliye: TeklifSiparis, *, dosya, kullanici=None) -> IrsaliyeEk:
    """İrsaliyeye tek dosya ekler (çoklu yükleme view katmanında döngüyle çağırır)."""
    if not _irsaliye_mi(irsaliye):
        raise IrsaliyeEkHatasi("Ek yalnız irsaliyeye eklenebilir.")
    if irsaliye.silindi:
        raise IrsaliyeEkHatasi("Silinmiş irsaliyeye dosya eklenemez.")
    try:
        saklanan, ad = fatura_ek_servis.dosya_hazirla(dosya)
    except FaturaEkHatasi as e:
        raise IrsaliyeEkHatasi(str(e))
    return IrsaliyeEk.objects.create(
        irsaliye=irsaliye, dosya=saklanan, orijinal_ad=ad[:255], created_by=kullanici, updated_by=kullanici)


def dosya_baska_kayitta_mi(ad: str, haric_pk=None) -> bool:
    """Aynı depolama yolunu başka bir ek kaydı (irsaliye/fatura) hâlâ kullanıyor mu? (Paylaşılan dosya yanlışlıkla silinmesin.)"""
    ir = IrsaliyeEk.objects.filter(dosya=ad)
    if haric_pk is not None:
        ir = ir.exclude(pk=haric_pk)
    return ir.exists() or FaturaEk.objects.filter(dosya=ad).exists()


def ek_sil(ek: IrsaliyeEk, kullanici=None) -> str:
    """KALICI silme: kayıt fiziksel silinir, dosya diskten kaldırılır (kayıt işlemi geri alınırsa dosya silinmez: disk işlemi commit sonrası).
    Başka bir kayıt aynı dosyayı kullanıyorsa dosya korunur. Dönen: silinen dosyanın depolama adı."""
    ad = ek.dosya.name
    depo = ek.dosya.storage
    pk, irsaliye_id, orijinal = ek.pk, ek.irsaliye_id, ek.orijinal_ad
    with transaction.atomic():
        ek.delete()
        paylasimli = dosya_baska_kayitta_mi(ad)

        def _diskten_sil():
            if paylasimli or not ad:
                return
            try:
                depo.delete(ad)
            except OSError:
                log.exception("İrsaliye eki dosyası diskten silinemedi: %s", ad)

        transaction.on_commit(_diskten_sil)
    log.info("İrsaliye eki kalıcı silindi: ek=%s irsaliye=%s ad=%r dosya=%s kullanici=%s", pk, irsaliye_id, orijinal, ad, getattr(kullanici, "pk", None))
    return ad
