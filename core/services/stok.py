"""Stok/ürün kartı (STOKLAR — master) servis katmanı. Kurallar tek noktada.

- Kod OTOMATİK: ``ÜST.kod-ALT.kod-NNNN`` (örn. 150-10-0001); sıra her ALT kategori
  içinde ayrı ilerler. Elle girilmez/değiştirilmez. Silinen numara tekrar kullanılmaz
  (o kategorideki en büyük sıranın +1'i alınır).
- Kart yalnız ALT (yaprak değil — "alt") kategoriye bağlanır (ust dolu). Üst kategoriye
  stok açılamaz. Kategori ve kod oluşturmadan sonra DEĞİŞMEZ.
- Üretim ve fatura birimi farklı olabilir; ``cevirici`` = 1 üretim birimi kaç fatura
  birimi eder (> 0). KDV oranı ZORUNLU (tevkifat opsiyonel kalır).
- Alış fiyatı (tutar + para birimi) tamamen opsiyonel, yalnız BİLGİ amaçlı — fatura/
  teklif-sipariş fiyatını ETKİLEMEZ, muhasebeye yansımaz.
- Silme: soft-delete (iz kalır).
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Birim, Cari, Kategori, KdvOrani, Stok, TevkifatOrani
from core.sayi import SayiHatasi, parse_tr


class StokHatasi(ValueError):
    """Stok kural ihlali (Türkçe mesaj)."""


def aktif_stoklar():
    """Silinmemiş stoklar (kod sırasıyla); kategori + birimler birlikte çekilir."""
    return (Stok.objects.filter(silindi=False)
            .select_related("kategori", "kategori__ust", "uretim_birimi",
                            "fatura_birimi", "kdv", "tevkifat", "tedarikci")
            .order_by("kod"))


def sonraki_stok_kodu(kategori: Kategori) -> str:
    """Bir ALT kategori için sıradaki stok kodunu üretir: ÜST.kod-ALT.kod-NNNN."""
    if kategori.ust_id is None:
        raise StokHatasi("Stok yalnız ALT kategoriye açılabilir (üst kategori değil).")
    onek = f"{kategori.ust.kod}-{kategori.kod}-"
    son = 0
    for k in Stok.objects.filter(kategori=kategori).values_list("kod", flat=True):
        parca = k.rsplit("-", 1)[-1]
        if parca.isdigit():
            son = max(son, int(parca))
    return f"{onek}{str(son + 1).zfill(4)}"


def _ad_dogrula(ad):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise StokHatasi("Stok adı boş olamaz.")
    return ad


def _birim_coz(birim_id, etiket):
    b = Birim.objects.filter(pk=birim_id, silindi=False).first()
    if b is None:
        raise StokHatasi(f"{etiket} bulunamadı.")
    return b


def _kdv_coz(kdv_id):
    """Zorunlu KDV oranı FK çözümü."""
    if not kdv_id:
        raise StokHatasi("KDV oranı seçilmelidir.")
    k = KdvOrani.objects.filter(pk=kdv_id, silindi=False).first()
    if k is None:
        raise StokHatasi("KDV oranı bulunamadı.")
    return k


def _tevkifat_coz(tevkifat_id):
    """Opsiyonel tevkifat oranı FK çözümü (boşsa None)."""
    if not tevkifat_id:
        return None
    t = TevkifatOrani.objects.filter(pk=tevkifat_id, silindi=False).first()
    if t is None:
        raise StokHatasi("Tevkifat oranı bulunamadı.")
    return t


def _tedarikci_coz(tedarikci_id):
    """Opsiyonel tedarikçi (Cari) FK çözümü (boşsa None)."""
    if not tedarikci_id:
        return None
    c = Cari.objects.filter(pk=tedarikci_id, silindi=False).first()
    if c is None:
        raise StokHatasi("Tedarikçi cari bulunamadı.")
    return c


def _cevirici_dogrula(deger):
    try:
        c = parse_tr(deger)
    except SayiHatasi:
        raise StokHatasi("Çevirici geçerli bir sayı olmalı.")
    if c <= 0:
        raise StokHatasi("Çevirici sıfırdan büyük olmalı.")
    return c


def _negatif_olmaz(deger, etiket) -> Decimal:
    """≥ 0 ondalık doğrular; boş/None -> 0 (alanlar opsiyonel, varsayılan 0)."""
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise StokHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise StokHatasi(f"{etiket} negatif olamaz.")
    return d


def _tutar_opsiyonel(deger, etiket):
    """≥ 0 ondalık veya None — boş girilirse "bilinmiyor" demektir, 0 değil."""
    if deger in (None, ""):
        return None
    try:
        d = parse_tr(deger)
    except SayiHatasi:
        raise StokHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise StokHatasi(f"{etiket} negatif olamaz.")
    return d


def _pb_dogrula(para_birimi):
    from core.models import YevmiyeSatir
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(YevmiyeSatir.IslemPB.choices):
        raise StokHatasi("Geçersiz para birimi.")
    return pb


def stok_olustur(*, ad, kategori_id, uretim_birimi_id, fatura_birimi_id,
                 cevirici=Decimal("1"), kdv_id=None, tevkifat_id=None,
                 kritik_stok=Decimal("0"), tedarikci_id=None,
                 alis_fiyati=None, alis_fiyati_pb="TRY", kullanici=None) -> Stok:
    ad = _ad_dogrula(ad)
    kategori = Kategori.objects.filter(pk=kategori_id, silindi=False).first()
    if kategori is None:
        raise StokHatasi("Kategori bulunamadı.")
    if kategori.ust_id is None:
        raise StokHatasi("Stok yalnız ALT kategoriye açılabilir (üst kategori değil).")
    uretim = _birim_coz(uretim_birimi_id, "Üretim birimi")
    fatura = _birim_coz(fatura_birimi_id, "Fatura birimi")
    return Stok.objects.create(
        kod=sonraki_stok_kodu(kategori), ad=ad, kategori=kategori,
        uretim_birimi=uretim, fatura_birimi=fatura,
        cevirici=_cevirici_dogrula(cevirici),
        kdv=_kdv_coz(kdv_id), tevkifat=_tevkifat_coz(tevkifat_id),
        kritik_stok=_negatif_olmaz(kritik_stok, "Kritik stok seviyesi"),
        tedarikci=_tedarikci_coz(tedarikci_id),
        alis_fiyati=_tutar_opsiyonel(alis_fiyati, "Alış fiyatı"),
        alis_fiyati_pb=_pb_dogrula(alis_fiyati_pb),
        created_by=kullanici, updated_by=kullanici,
    )


def stok_kopyala(stok: Stok, kullanici=None) -> Stok:
    """Var olan bir stok kartının birebir kopyasını oluşturur. ``kod`` farklıdır
    (aynı kategoride sıradaki numarayı otomatik alır); ``ad`` sonuna " KOPYA"
    eklenir (hangisinin kopya olduğu ayırt edilsin diye) — kategori, birimler,
    çevirici, KDV/tevkifat, kritik stok, tedarikçi ve alış fiyatı aynen kopyalanır.
    """
    return stok_olustur(
        ad=f"{stok.ad} KOPYA", kategori_id=stok.kategori_id,
        uretim_birimi_id=stok.uretim_birimi_id, fatura_birimi_id=stok.fatura_birimi_id,
        cevirici=stok.cevirici, kdv_id=stok.kdv_id, tevkifat_id=stok.tevkifat_id,
        kritik_stok=stok.kritik_stok, tedarikci_id=stok.tedarikci_id,
        alis_fiyati=stok.alis_fiyati, alis_fiyati_pb=stok.alis_fiyati_pb,
        kullanici=kullanici)


def stok_guncelle(stok: Stok, *, ad, uretim_birimi_id, fatura_birimi_id,
                  cevirici, kdv_id=None, tevkifat_id=None,
                  kritik_stok=Decimal("0"), tedarikci_id=None,
                  alis_fiyati=None, alis_fiyati_pb="TRY", kullanici=None) -> Stok:
    """Ad, birimler, çevirici, vergi/stok alanları güncellenir. KOD ve KATEGORİ DEĞİŞMEZ."""
    if stok.silindi:
        raise StokHatasi("Silinmiş stok düzenlenemez.")
    stok.ad = _ad_dogrula(ad)
    stok.uretim_birimi = _birim_coz(uretim_birimi_id, "Üretim birimi")
    stok.fatura_birimi = _birim_coz(fatura_birimi_id, "Fatura birimi")
    stok.cevirici = _cevirici_dogrula(cevirici)
    stok.kdv = _kdv_coz(kdv_id)
    stok.tevkifat = _tevkifat_coz(tevkifat_id)
    stok.kritik_stok = _negatif_olmaz(kritik_stok, "Kritik stok seviyesi")
    stok.tedarikci = _tedarikci_coz(tedarikci_id)
    stok.alis_fiyati = _tutar_opsiyonel(alis_fiyati, "Alış fiyatı")
    stok.alis_fiyati_pb = _pb_dogrula(alis_fiyati_pb)
    stok.updated_by = kullanici
    stok.save(update_fields=["ad", "uretim_birimi", "fatura_birimi", "cevirici",
                             "kdv", "tevkifat", "kritik_stok", "tedarikci",
                             "alis_fiyati", "alis_fiyati_pb", "updated_by", "updated_at"])
    return stok


def stok_sil(stok: Stok, kullanici=None) -> Stok:
    """Soft-delete (iz kalır)."""
    if stok.silindi:
        return stok
    stok.silindi = True
    stok.silindi_at = timezone.now()
    stok.updated_by = kullanici
    stok.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return stok
