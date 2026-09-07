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
- Ürün grubu (satınalma/üretim/satış) birden çok seçilebilir, en az biri ZORUNLU.
  Satış işaretli değilse teklif/katalog teknik alanları (ölçü/ağırlık/yükleme adedi)
  serviste None'a sabitlenir — formda ne gönderilirse gönderilsin kayıtta kalmaz.
- Silme: soft-delete (iz kalır).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Birim, Cari, Kategori, KdvOrani, Stok, StokFiyat, TevkifatOrani
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


def _grup_dogrula(satinalma_urunu, uretim_urunu, satis_urunu):
    if not (satinalma_urunu or uretim_urunu or satis_urunu):
        raise StokHatasi("En az bir grup (Satınalma/Üretim/Satış) seçilmelidir.")


def _tam_sayi_opsiyonel(deger, etiket):
    """≥ 0 tam sayı veya None — boş girilirse alan hiç doldurulmamış demektir."""
    if deger in (None, ""):
        return None
    try:
        d = int(deger)
    except (TypeError, ValueError):
        raise StokHatasi(f"{etiket} geçerli bir tam sayı olmalı.")
    if d < 0:
        raise StokHatasi(f"{etiket} negatif olamaz.")
    return d


# SEMTA ürün kataloğu teknik ölçü tablosuyla birebir; yalnız satis_urunu=True
# kartlarda anlamlı — kart bu grupla işaretli değilse bu alanların HEPSİ None'a
# sabitlenir (Satınalma-yalnız bir kartta eski/yanlış teklif verisi kalmasın).
_SATIS_ALAN_ADLARI = (
    "basamak_sayisi", "yukseklik", "acik_derinlik", "taban_genisligi", "kapali_boy",
    "agirlik", "azami_yuk", "cbm", "yukleme_20dc", "yukleme_40hq", "yukleme_tir",
)
# model_kodu ayrı tutulur: CharField (null=True DEĞİL), temizlenince None değil "" olur.
_SATIS_METIN_ALAN_ADLARI = ("model_kodu",)


def _satis_alanlarini_coz(satis_urunu, *, model_kodu="", basamak_sayisi=None,
                          yukseklik=None, acik_derinlik=None, taban_genisligi=None,
                          kapali_boy=None, agirlik=None, azami_yuk=None, cbm=None,
                          yukleme_20dc=None, yukleme_40hq=None, yukleme_tir=None):
    if not satis_urunu:
        return {**dict.fromkeys(_SATIS_ALAN_ADLARI, None), "model_kodu": ""}
    return {
        "model_kodu": buyuk_harf_tr((model_kodu or "").strip()),
        "basamak_sayisi": _tam_sayi_opsiyonel(basamak_sayisi, "Basamak sayısı"),
        "yukseklik": _tutar_opsiyonel(yukseklik, "Yükseklik"),
        "acik_derinlik": _tutar_opsiyonel(acik_derinlik, "Açık derinlik"),
        "taban_genisligi": _tutar_opsiyonel(taban_genisligi, "Taban genişliği"),
        "kapali_boy": _tutar_opsiyonel(kapali_boy, "Kapalı boy"),
        "agirlik": _tutar_opsiyonel(agirlik, "Ağırlık"),
        "azami_yuk": _tutar_opsiyonel(azami_yuk, "Azami yük"),
        "cbm": _tutar_opsiyonel(cbm, "CBM"),
        "yukleme_20dc": _tam_sayi_opsiyonel(yukleme_20dc, "20' DC yükleme adedi"),
        "yukleme_40hq": _tam_sayi_opsiyonel(yukleme_40hq, "40' HQ yükleme adedi"),
        "yukleme_tir": _tam_sayi_opsiyonel(yukleme_tir, "TIR yükleme adedi"),
    }


def _fiyat_listesini_coz(satis_urunu, *, fiyat_try=None, fiyat_usd=None,
                         fiyat_eur=None, fiyat_gbp=None):
    if not satis_urunu:
        return {"TRY": None, "USD": None, "EUR": None, "GBP": None}
    return {
        "TRY": _tutar_opsiyonel(fiyat_try, "TRY satış fiyatı"),
        "USD": _tutar_opsiyonel(fiyat_usd, "USD satış fiyatı"),
        "EUR": _tutar_opsiyonel(fiyat_eur, "EUR satış fiyatı"),
        "GBP": _tutar_opsiyonel(fiyat_gbp, "GBP satış fiyatı"),
    }


def _fiyat_listesini_yaz(stok, fiyatlar, kullanici=None):
    """``fiyatlar``: {"TRY": Decimal|None, ...}. None -> aktif satır varsa soft-delete;
    sayı -> upsert (fiziksel silme yok, diğer satış alanlarıyla aynı invariant)."""
    mevcutlar = {f.para_birimi: f for f in stok.fiyatlar.filter(silindi=False)}
    for pb, deger in fiyatlar.items():
        mevcut = mevcutlar.get(pb)
        if deger is None:
            if mevcut is not None:
                mevcut.silindi = True
                mevcut.silindi_at = timezone.now()
                mevcut.updated_by = kullanici
                mevcut.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        elif mevcut is not None:
            if mevcut.fiyat != deger:
                mevcut.fiyat = deger
                mevcut.updated_by = kullanici
                mevcut.save(update_fields=["fiyat", "updated_by", "updated_at"])
        else:
            StokFiyat.objects.create(stok=stok, para_birimi=pb, fiyat=deger,
                                     created_by=kullanici, updated_by=kullanici)


def satis_urunleri_fiyatlariyla():
    """satis_urunu=True stoklar + aktif fiyat listesi (prefetch) — Satış Teklifi ekranı
    için: her stok, ``.fiyatlar.all()`` üzerinden PB başına en fazla bir aktif satır taşır."""
    return (Stok.objects.filter(silindi=False, satis_urunu=True)
            .select_related("kdv")
            .prefetch_related(Prefetch("fiyatlar", queryset=StokFiyat.objects.filter(silindi=False)))
            .order_by("kod"))


@transaction.atomic
def stok_olustur(*, ad, kategori_id, uretim_birimi_id, fatura_birimi_id,
                 cevirici=Decimal("1"), kdv_id=None, tevkifat_id=None,
                 kritik_stok=Decimal("0"), tedarikci_id=None,
                 alis_fiyati=None, alis_fiyati_pb="TRY",
                 satinalma_urunu=False, uretim_urunu=True, satis_urunu=False,
                 model_kodu="", basamak_sayisi=None, yukseklik=None, acik_derinlik=None,
                 taban_genisligi=None, kapali_boy=None, agirlik=None, azami_yuk=None,
                 cbm=None, yukleme_20dc=None, yukleme_40hq=None, yukleme_tir=None,
                 gorsel=None, fiyat_try=None, fiyat_usd=None, fiyat_eur=None, fiyat_gbp=None,
                 kullanici=None) -> Stok:
    ad = _ad_dogrula(ad)
    kategori = Kategori.objects.filter(pk=kategori_id, silindi=False).first()
    if kategori is None:
        raise StokHatasi("Kategori bulunamadı.")
    if kategori.ust_id is None:
        raise StokHatasi("Stok yalnız ALT kategoriye açılabilir (üst kategori değil).")
    uretim = _birim_coz(uretim_birimi_id, "Üretim birimi")
    fatura = _birim_coz(fatura_birimi_id, "Fatura birimi")
    _grup_dogrula(satinalma_urunu, uretim_urunu, satis_urunu)
    satis_alanlari = _satis_alanlarini_coz(
        satis_urunu, model_kodu=model_kodu, basamak_sayisi=basamak_sayisi,
        yukseklik=yukseklik, acik_derinlik=acik_derinlik, taban_genisligi=taban_genisligi,
        kapali_boy=kapali_boy, agirlik=agirlik, azami_yuk=azami_yuk, cbm=cbm,
        yukleme_20dc=yukleme_20dc, yukleme_40hq=yukleme_40hq, yukleme_tir=yukleme_tir)
    fiyat_listesi = _fiyat_listesini_coz(
        satis_urunu, fiyat_try=fiyat_try, fiyat_usd=fiyat_usd,
        fiyat_eur=fiyat_eur, fiyat_gbp=fiyat_gbp)
    stok = Stok.objects.create(
        kod=sonraki_stok_kodu(kategori), ad=ad, kategori=kategori,
        uretim_birimi=uretim, fatura_birimi=fatura,
        cevirici=_cevirici_dogrula(cevirici),
        kdv=_kdv_coz(kdv_id), tevkifat=_tevkifat_coz(tevkifat_id),
        kritik_stok=_negatif_olmaz(kritik_stok, "Kritik stok seviyesi"),
        tedarikci=_tedarikci_coz(tedarikci_id),
        alis_fiyati=_tutar_opsiyonel(alis_fiyati, "Alış fiyatı"),
        alis_fiyati_pb=_pb_dogrula(alis_fiyati_pb),
        satinalma_urunu=bool(satinalma_urunu), uretim_urunu=bool(uretim_urunu),
        satis_urunu=bool(satis_urunu),
        gorsel=(gorsel if satis_urunu else None),
        created_by=kullanici, updated_by=kullanici,
        **satis_alanlari,
    )
    _fiyat_listesini_yaz(stok, fiyat_listesi, kullanici)
    return stok


def stok_kopyala(stok: Stok, kullanici=None) -> Stok:
    """Var olan bir stok kartının birebir kopyasını oluşturur. ``kod`` farklıdır
    (aynı kategoride sıradaki numarayı otomatik alır); ``ad`` sonuna " KOPYA"
    eklenir (hangisinin kopya olduğu ayırt edilsin diye) — kategori, birimler,
    çevirici, KDV/tevkifat, kritik stok, tedarikçi, alış fiyatı, ürün grubu,
    satış/teklif alanları, fiyat listesi ve görsel aynen kopyalanır.
    """
    fiyatlar = {f.para_birimi: f.fiyat for f in stok.fiyatlar.filter(silindi=False)}
    return stok_olustur(
        ad=f"{stok.ad} KOPYA", kategori_id=stok.kategori_id,
        uretim_birimi_id=stok.uretim_birimi_id, fatura_birimi_id=stok.fatura_birimi_id,
        cevirici=stok.cevirici, kdv_id=stok.kdv_id, tevkifat_id=stok.tevkifat_id,
        kritik_stok=stok.kritik_stok, tedarikci_id=stok.tedarikci_id,
        alis_fiyati=stok.alis_fiyati, alis_fiyati_pb=stok.alis_fiyati_pb,
        satinalma_urunu=stok.satinalma_urunu, uretim_urunu=stok.uretim_urunu,
        satis_urunu=stok.satis_urunu, model_kodu=stok.model_kodu,
        basamak_sayisi=stok.basamak_sayisi,
        yukseklik=stok.yukseklik, acik_derinlik=stok.acik_derinlik,
        taban_genisligi=stok.taban_genisligi, kapali_boy=stok.kapali_boy,
        agirlik=stok.agirlik, azami_yuk=stok.azami_yuk, cbm=stok.cbm,
        yukleme_20dc=stok.yukleme_20dc, yukleme_40hq=stok.yukleme_40hq,
        yukleme_tir=stok.yukleme_tir, gorsel=(stok.gorsel if stok.gorsel else None),
        fiyat_try=fiyatlar.get("TRY"), fiyat_usd=fiyatlar.get("USD"),
        fiyat_eur=fiyatlar.get("EUR"), fiyat_gbp=fiyatlar.get("GBP"),
        kullanici=kullanici)


@transaction.atomic
def stok_guncelle(stok: Stok, *, ad, uretim_birimi_id, fatura_birimi_id,
                  cevirici, kdv_id=None, tevkifat_id=None,
                  kritik_stok=Decimal("0"), tedarikci_id=None,
                  alis_fiyati=None, alis_fiyati_pb="TRY",
                  satinalma_urunu=False, uretim_urunu=True, satis_urunu=False,
                  model_kodu="", basamak_sayisi=None, yukseklik=None, acik_derinlik=None,
                  taban_genisligi=None, kapali_boy=None, agirlik=None, azami_yuk=None,
                  cbm=None, yukleme_20dc=None, yukleme_40hq=None, yukleme_tir=None,
                  gorsel=None, fiyat_try=None, fiyat_usd=None, fiyat_eur=None, fiyat_gbp=None,
                  kullanici=None) -> Stok:
    """Ad, birimler, çevirici, vergi/stok/grup/teklif/görsel/fiyat listesi alanları
    güncellenir. KOD ve KATEGORİ DEĞİŞMEZ. ``gorsel=None`` + Satış işaretliyse mevcut
    görsel korunur (yeni dosya yüklenmedi demektir); Satış işareti kaldırılırsa görsel
    de temizlenir (bkz. diğer satış/teklif alanları)."""
    if stok.silindi:
        raise StokHatasi("Silinmiş stok düzenlenemez.")
    _grup_dogrula(satinalma_urunu, uretim_urunu, satis_urunu)
    satis_alanlari = _satis_alanlarini_coz(
        satis_urunu, model_kodu=model_kodu, basamak_sayisi=basamak_sayisi,
        yukseklik=yukseklik, acik_derinlik=acik_derinlik, taban_genisligi=taban_genisligi,
        kapali_boy=kapali_boy, agirlik=agirlik, azami_yuk=azami_yuk, cbm=cbm,
        yukleme_20dc=yukleme_20dc, yukleme_40hq=yukleme_40hq, yukleme_tir=yukleme_tir)
    fiyat_listesi = _fiyat_listesini_coz(
        satis_urunu, fiyat_try=fiyat_try, fiyat_usd=fiyat_usd,
        fiyat_eur=fiyat_eur, fiyat_gbp=fiyat_gbp)
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
    stok.satinalma_urunu = bool(satinalma_urunu)
    stok.uretim_urunu = bool(uretim_urunu)
    stok.satis_urunu = bool(satis_urunu)
    for alan, deger in satis_alanlari.items():
        setattr(stok, alan, deger)
    if satis_urunu:
        if gorsel is not None:             # yalnız yeni dosya yüklendiyse değiştir
            stok.gorsel = gorsel
    else:
        stok.gorsel = None
    stok.updated_by = kullanici
    stok.save(update_fields=[
        "ad", "uretim_birimi", "fatura_birimi", "cevirici", "kdv", "tevkifat",
        "kritik_stok", "tedarikci", "alis_fiyati", "alis_fiyati_pb",
        "satinalma_urunu", "uretim_urunu", "satis_urunu",
        *_SATIS_ALAN_ADLARI, *_SATIS_METIN_ALAN_ADLARI,
        "gorsel", "updated_by", "updated_at"])
    _fiyat_listesini_yaz(stok, fiyat_listesi, kullanici)
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
