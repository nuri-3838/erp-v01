"""Kategori (STOKLAR) servis katmanı — 2 seviyeli hiyerarşi + Kod + muhasebe hesabı
HARİTASI (ALT kategori × fatura tipi → yaprak hesap).

Kurallar tek noktada (UI'a güvenilmez):
- Ad TR büyük harfe çevrilir; boş olamaz.
- Kod elle girilir; boş olamaz; silinmemişler arasında BENZERSİZ (DB'de de kısmi unique).
- En fazla 2 SEVİYE: ÜST (ust=None) → ALT (ust = bir ÜST). Alt'ın altına açılamaz.
- Muhasebe hesabı haritası YALNIZ ALT kategoride; her fatura tipi için opsiyonel bir
  YAPRAK (fişe kesilebilir) hesap. "Bağ kaldır" = soft-delete; yeniden bağlanınca canlanır.
- Silme: soft-delete; aktif ALT kategorisi olan ÜST silinemez (harita satırları da soft-delete).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import FaturaTipi, HesapPlani, Kategori, KategoriHesap
from core.services.hesap_plani import yaprak_mi


class KategoriHatasi(ValueError):
    """Kategori kural ihlali (Türkçe mesaj)."""


def aktif_kategoriler():
    """Silinmemiş kategoriler (ad'a göre); üst birlikte çekilir."""
    return Kategori.objects.filter(silindi=False).select_related("ust").order_by("ad")


def ust_kategoriler():
    """ÜST (kök) kategoriler — alt kategori eklerken üst seçimi için."""
    return Kategori.objects.filter(silindi=False, ust__isnull=True).order_by("ad")


def _ad_dogrula(ad):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise KategoriHatasi("Kategori adı boş olamaz.")
    return ad


def _kod_dogrula(kod, *, haric_pk=None):
    kod = (kod or "").strip()
    if not kod:
        raise KategoriHatasi("Kategori kodu boş olamaz.")
    cak = Kategori.objects.filter(silindi=False, kod=kod)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise KategoriHatasi(f"Bu kod zaten kayıtlı: {kod}")
    return kod


def _yaprak_hesap_coz(hesap_kodu):
    """Harita girdisi: boşsa None; doluysa hesap planından YAPRAK hesap olmalı."""
    kod = (hesap_kodu or "").strip()
    if not kod:
        return None
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise KategoriHatasi(f"Hesap bulunamadı: {kod}")
    if not yaprak_mi(h):
        raise KategoriHatasi(
            "Yalnızca yaprak (alt hesabı olmayan) hesap bağlanabilir; "
            f"{h.hesap_kodu} alt hesabı olan bir üst/ara hesaptır."
        )
    return h


def kategori_olustur(*, ad, kod, ust_id=None, kullanici=None) -> Kategori:
    """Yeni ÜST (ust_id=None) ya da ALT kategori oluşturur. KategoriHatasi yükseltebilir."""
    ad = _ad_dogrula(ad)
    kod = _kod_dogrula(kod)
    ust = None
    if ust_id:
        ust = Kategori.objects.filter(pk=ust_id, silindi=False).first()
        if ust is None:
            raise KategoriHatasi("Üst kategori bulunamadı.")
        if ust.ust_id is not None:
            raise KategoriHatasi(
                "En fazla 2 seviye: bir alt kategorinin altına kategori açılamaz."
            )
    return Kategori.objects.create(
        ad=ad, kod=kod, ust=ust,
        created_by=kullanici, updated_by=kullanici,
    )


def kategori_guncelle(kategori: Kategori, *, ad, kod, kullanici=None) -> Kategori:
    """Ad + Kod günceller (üst kategori DEĞİŞMEZ)."""
    if kategori.silindi:
        raise KategoriHatasi("Silinmiş kategori düzenlenemez.")
    kategori.ad = _ad_dogrula(ad)
    kategori.kod = _kod_dogrula(kod, haric_pk=kategori.pk)
    kategori.updated_by = kullanici
    kategori.save(update_fields=["ad", "kod", "updated_by", "updated_at"])
    return kategori


def kategori_sil(kategori: Kategori, kullanici=None) -> Kategori:
    """Soft-delete. Aktif alt kategorisi olan ÜST silinemez. Harita satırları da soft-delete."""
    if kategori.silindi:
        return kategori
    if kategori.alt_kategoriler.filter(silindi=False).exists():
        raise KategoriHatasi(
            "Bu kategorinin alt kategorisi var; önce alt kategorileri silin."
        )
    simdi = timezone.now()
    kategori.hesap_baglari.filter(silindi=False).update(
        silindi=True, silindi_at=simdi, updated_by=kullanici, updated_at=simdi)
    kategori.silindi = True
    kategori.silindi_at = simdi
    kategori.updated_by = kullanici
    kategori.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kategori


# --- Muhasebe hesabı haritası (ALT kategori × fatura tipi → yaprak hesap) ----------
def kategori_hesaplari(kategori: Kategori) -> dict:
    """{fatura_tipi_id: KategoriHesap} — yalnız aktif (silinmemiş) bağlar."""
    return {kh.fatura_tipi_id: kh for kh in
            kategori.hesap_baglari.filter(silindi=False)
            .select_related("hesap", "fatura_tipi")}


def kategori_hesaplari_kaydet(kategori: Kategori, *, eslesmeler: dict, kullanici=None):
    """``eslesmeler`` = {fatura_tipi_id: hesap_kodu_or_empty}. Yalnız ALT kategori.

    Dolu tip → update_or_create (yaprak hesap doğrulanır; soft-deleted satır canlanır);
    boş tip → varsa mevcut bağ soft-delete edilir.
    """
    if kategori.ust_id is None:
        raise KategoriHatasi("Muhasebe hesabı haritası yalnız alt kategorilerde tutulur.")
    aktif_ft = set(
        FaturaTipi.objects.filter(silindi=False).values_list("pk", flat=True))
    simdi = timezone.now()
    for ft_id, hesap_kodu in eslesmeler.items():
        if ft_id not in aktif_ft:
            continue
        hesap = _yaprak_hesap_coz(hesap_kodu)
        mevcut = KategoriHesap.objects.filter(
            kategori=kategori, fatura_tipi_id=ft_id).first()
        if hesap is None:
            if mevcut is not None and not mevcut.silindi:
                mevcut.silindi = True
                mevcut.silindi_at = simdi
                mevcut.updated_by = kullanici
                mevcut.save(update_fields=["silindi", "silindi_at",
                                           "updated_by", "updated_at"])
            continue
        if mevcut is not None:
            mevcut.hesap = hesap
            mevcut.silindi = False
            mevcut.silindi_at = None
            mevcut.updated_by = kullanici
            mevcut.save(update_fields=["hesap", "silindi", "silindi_at",
                                       "updated_by", "updated_at"])
        else:
            KategoriHesap.objects.create(
                kategori=kategori, fatura_tipi_id=ft_id, hesap=hesap,
                created_by=kullanici, updated_by=kullanici)
