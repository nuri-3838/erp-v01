"""Fatura (FATURALAR) servis katmanı — Alış/Satış faturasından OTOMATİK yevmiye.

Fatura kaydedildiğinde dengeli bir yevmiye fişi üretilir (mevcut fis_olustur ile)
ve faturaya bağlanır. Muhasebe haritası:
  - Mal/gelir hesabı  = stok kategorisi × fatura tipi (KategoriHesap).
  - KDV hesabı         = stoğun KDV oranının BORÇ (alış 191) / ALACAK (satış 391) hesabı.
  - Karşı taraf        = carinin muhasebe hesabı (320.../120... yaprak).
ALIŞ:  Borç mal + Borç KDV  / Alacak cari.
GİDER (FaturaTipi.gider, yalnız ALIŞ): kalem STOK değil doğrudan bir GİDER HESABI (yaprak
  7xx/63x...; bkz. hesap_plani.gider_hesaplari) + satırda seçilen KDV oranı; kategori haritası,
  depo ve stok hareketi YOKTUR. Borç gider hesabı + Borç 191 KDV / Alacak cari.
SATIŞ: Alacak gelir + Alacak KDV / Borç cari.

İlk dilim: TL (kur=1). Tutarlar satırlardan; her şey atomik (eksik harita -> hiç kayıt yok).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (Cari, Depo, Fatura, FaturaSatir, FaturaTipi, HesapPlani,
                         KategoriHesap, KdvOrani, Kur, Stok, StokHareket, StokMaliyetKatmani,
                         StokMaliyetTuketimi, TeklifSiparis, TevkifatOrani, YatirimProjesi,
                         YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.cari import vade_hesapla
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services.hesap_plani import (duran_varlik_hesaplari, gider_hesaplari,
                                       hesap_kodu_258_mi, hesap_kodu_duran_varlik_mi)
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_guncelle,
                                   fis_iptal, fis_olustur)

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


def _kur_coz(pb, tarih, cari=None):
    """Fatura para biriminin fiş tarihindeki TCMB kuru — carinin kur_tipi tercihine göre
    (bkz. Kur.deger); cari verilmezse MB Alış. TRY -> 1. Döviz için o tarihin KUR kaydı ve
    ilgili PB/kur tipi alanı dolu olmalı (carry-forward yok)."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    kur_tipi = cari.kur_tipi if cari else Cari.KurTipi.MB_ALIS
    deger = k.deger(pb, kur_tipi) if k else None
    if not deger:
        raise FaturaHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi çekmeden "
            f"döviz faturası kesilemez.")
    return deger


def _gider_ve_duran_varlik_hesaplari():
    """GİDER faturası kalem hesabı seçiminde izin verilen TAM küme: gider hesapları
    (7xx/65x/66x/68x) + duran varlık hesapları (253/254/255/258/260) — bkz.
    core.services.hesap_plani.gider_hesaplari / duran_varlik_hesaplari."""
    return gider_hesaplari() | duran_varlik_hesaplari()


# Ortak adına (şahsi) alış: KDV'nin (191'e borçlanan TAM tutar) karşılığı bu hesaba
# alacak yazılır — şirket KDV'yi normal indirir ama ortak faturanın KDV DAHİL tamamını
# öder, aradaki KDV "FAZLA" (şirket için fazladan gelir) olarak burada izlenir.
FAZLA_KDV_HESAP_KODU = "602.01"


def _fazla_kdv_hesabi():
    hesap = HesapPlani.objects.filter(hesap_kodu=FAZLA_KDV_HESAP_KODU, silindi=False).first()
    if hesap is None:
        raise FaturaHatasi(
            f"{FAZLA_KDV_HESAP_KODU} (FAZLA KDV) hesabı tanımlı değil; önce hesap "
            f"planında açılmalı.")
    return hesap


def _ortak_hesabi_coz(sahsi_ortak_id):
    """'Ortak adına (şahsi) alış' için seçilen hesabı çözer — bkz.
    core.services.hesap_plani.ortak_hesaplari (131 ailesi, yaprak)."""
    from core.services.hesap_plani import ortak_hesaplari
    hesap = ortak_hesaplari().filter(pk=sahsi_ortak_id).first() if sahsi_ortak_id else None
    if hesap is None:
        raise FaturaHatasi(
            "Ortak adına şahsi alış için geçerli bir ortak hesabı seçin (131 ailesi).")
    return hesap


def _tevkifat_coz(g, i, *, stok_varsayilani=None):
    """``g["tevkifat_yok"]`` True ise (kullanıcı seçicide "Yok"u AÇIKÇA seçti) stok
    kartında tanımlı olsa bile None döner. ``g["tevkifat_id"]`` doluysa o oran kullanılır.
    İkisi de yoksa (boş seçim) ``stok_varsayilani`` döner — stoklu kalemde stok kartının
    GÜNCEL tevkifatı, gider kaleminde zaten None (varsayılan yok)."""
    if g.get("tevkifat_yok"):
        return None
    tevkifat_id = g.get("tevkifat_id")
    if not tevkifat_id:
        return stok_varsayilani
    tevkifat = TevkifatOrani.objects.filter(pk=tevkifat_id, silindi=False).first()
    if tevkifat is None:
        raise FaturaHatasi(f"Satır {i}: tevkifat oranı bulunamadı.")
    return tevkifat


def _satir_coz(g, i, gider, *, sahsi_ortak=None):
    """Girdi satırını çözer -> (stok, hesap, kdv, tevkifat, proje). Gider tipinde satır bir
    GİDER HESABI veya DURAN VARLIK HESABI (+ satırda seçilen KDV oranı + opsiyonel elle
    seçilen tevkifat) taşır, diğer tiplerde STOK (KDV stoktan; tevkifat da varsayılan
    olarak stoktan gelir — ama `tevkifat_yok` AÇIKÇA seçilmişse stok kartında tanımlı olsa
    bile tevkifat uygulanmaz, `tevkifat_id` doluysa o oran esas alınır; bkz. _tevkifat_coz).
    Karışıklık reddedilir (UI'a güvenilmez). Duran varlık hesabı seçilirse
    `yatirim_projesi_id` çözülür — 258 ailesinde ZORUNLU, 253/254/255/260'ta opsiyonel;
    aktifleşmiş projeye yeni kalem eklenemez (bkz. YatirimProjesi.Durum). ``sahsi_ortak``
    doluysa (Ortak adına şahsi alış) satırın hesabı SUBMIT EDİLEN DEĞERDEN BAĞIMSIZ olarak
    doğrudan bu hesaba sabitlenir (131 ailesi) — duran varlık/proje mantığı uygulanmaz."""
    if gider:
        if g.get("stok_id"):
            raise FaturaHatasi(
                f"Satır {i}: gider faturasında stok kullanılamaz; gider hesabı seçin.")
        if sahsi_ortak is not None:
            kdv = None
            if g.get("kdv_id"):
                kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
                if kdv is None:
                    raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
            tevkifat = _tevkifat_coz(g, i)
            return None, sahsi_ortak, kdv, tevkifat, None
        hesap = (_gider_ve_duran_varlik_hesaplari().filter(pk=g.get("hesap_id")).first()
                 if g.get("hesap_id") else None)
        if hesap is None:
            raise FaturaHatasi(
                f"Satır {i}: geçerli bir gider veya duran varlık hesabı seçin "
                f"(yaprak hesap olmalı).")
        kdv = None
        if g.get("kdv_id"):
            kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
        tevkifat = _tevkifat_coz(g, i)
        proje = None
        if hesap_kodu_duran_varlik_mi(hesap.hesap_kodu):
            proje_id = g.get("yatirim_projesi_id")
            if proje_id:
                proje = YatirimProjesi.objects.filter(pk=proje_id, silindi=False).first()
                if proje is None:
                    raise FaturaHatasi(f"Satır {i}: yatırım projesi bulunamadı.")
                if proje.durum == YatirimProjesi.Durum.AKTIFLESTI:
                    raise FaturaHatasi(
                        f"Satır {i}: {proje.kod} projesi aktifleşmiş; yeni kalem eklenemez.")
            elif hesap_kodu_258_mi(hesap.hesap_kodu):
                raise FaturaHatasi(
                    f"Satır {i}: {hesap.hesap_kodu} hesabı için yatırım projesi seçimi "
                    f"zorunludur.")
        return None, hesap, kdv, tevkifat, proje
    if g.get("hesap_id"):
        raise FaturaHatasi(
            f"Satır {i}: gider hesabı yalnız gider faturası tipinde kullanılabilir.")
    stok = (Stok.objects.filter(pk=g.get("stok_id"), silindi=False)
            .select_related("kategori", "kdv", "tevkifat").first())
    if stok is None:
        raise FaturaHatasi(f"Satır {i}: stok bulunamadı.")
    tevkifat = _tevkifat_coz(g, i, stok_varsayilani=stok.tevkifat)
    return stok, None, stok.kdv, tevkifat, None


def _hazirla(*, tip_id, cari_id, tarih, satirlar, para_birimi, kur_override=None,
             sahsi_ortak_id=None):
    """Ortak hazırlık (oluştur+güncelle): doğrula, kur çöz, yevmiye satırlarını ve
    FaturaSatir verisini kur. (tip, cari, pb, kur, yevmiye_satirlari, hazir) döner.
    ``kur_override`` doluysa (kullanıcı elle girdi/değiştirdi) carinin kur_tipi'ne göre
    otomatik hesaplama YERİNE doğrudan kullanılır. ``sahsi_ortak_id`` doluysa (Ortak adına
    şahsi alış) her satırın hesabı ortak hesabına sabitlenir; mal satırı KDV DAHİL (gross)
    tutarla o hesaba borçlanır ve KDV'nin aynı tutarı FAZLA_KDV_HESAP_KODU'na alacak
    yazılır (bkz. core.services.fatura modül docstring'i ve _satir_coz)."""
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
    kur = Decimal("1") if pb == "TRY" else (kur_override or _kur_coz(pb, tarih, cari=cari))

    if sahsi_ortak_id and not (tip.gider and alis):
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_ortak_id else None
    sahsi = sahsi_ortak is not None

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}          # hesap_kodu -> KDV tutarı (PB) [alışta tam, satışta net]
    tevkifat_hesap_toplam = {}     # hesap_kodu -> tevkifat tutarı (PB) [yalnız ALIŞ -> 360]
    fazla_kdv_toplam = SIFIR       # yalnız şahsi alış: 602.01'e alacak
    borc_tl = SIFIR               # cari HARİÇ borç satırlarının TL toplamı
    alacak_tl = SIFIR             # cari HARİÇ alacak satırlarının TL toplamı
    cari_pb = SIFIR               # carinin PB tutarı = mal + (KDV − tevkifat)
    hazir = []                     # FaturaSatir için (stok, hesap, miktar, fiyat, kdv, tevkifat)

    def _ekle(taraf, tutar_pb):
        nonlocal borc_tl, alacak_tl
        tl = yuvarla(tutar_pb * kur, 2)
        if taraf == "B":
            borc_tl += tl
        else:
            alacak_tl += tl

    if tip.gider and not alis:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")

    for i, g in enumerate(satirlar, start=1):
        stok, hesap, kdv, tevkifat, proje = _satir_coz(g, i, tip.gider, sahsi_ortak=sahsi_ortak)
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")

        # Mal/gider hesabı: gider faturasında satırın kendi gider hesabı; diğer tiplerde
        # kategori × fatura tipi haritası
        if hesap is not None:
            mal_kodu, mal_ad, etiket = hesap.hesap_kodu, hesap.hesap_adi, hesap.hesap_kodu
        else:
            kh = KategoriHesap.objects.filter(
                kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
            if kh is None:
                raise FaturaHatasi(
                    f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                    f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")
            mal_kodu, mal_ad, etiket = kh.hesap.hesap_kodu, stok.ad, stok.kod

        satir_tutar = yuvarla(miktar * birim, 2)
        oran = kdv.oran if kdv else SIFIR
        satir_kdv = yuvarla(satir_tutar * oran / Decimal("100"), 2)

        # Tevkifat (varsa): KDV'nin pay/payda kadarı
        tev = SIFIR
        if tevkifat and tevkifat.payda and satir_kdv > 0:
            tev = yuvarla(satir_kdv * Decimal(tevkifat.pay) / Decimal(tevkifat.payda), 2)
        kdv_net = satir_kdv - tev      # cariye yansıyan KDV

        # Mal/gider/gelir satırı (alış: Borç, satış: Alacak). Şahsi alışta KDV DAHİL
        # (gross) tutarla ortak hesabına borçlanır — ortak faturanın tamamını öder.
        mal_taraf = "B" if alis else "A"
        mal_tutar = (satir_tutar + satir_kdv) if sahsi else satir_tutar
        _ekle(mal_taraf, mal_tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=mal_kodu, taraf=mal_taraf,
            islem_tutari=mal_tutar, islem_pb=pb, islem_kuru=kur, aciklama=mal_ad))

        # KDV hesabı — ALIŞ: 191 TAM KDV (borç); SATIŞ: 391 NET KDV (alacak)
        kdv_post = satir_kdv if alis else kdv_net
        if kdv_post > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {etiket} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + kdv_post)
            if sahsi:
                fazla_kdv_toplam += kdv_post

        # Tevkifat — yalnız ALIŞ'ta 360'a (Ödenecek) alacak yazılır
        if alis and tev > 0:
            tev_hesap = tevkifat.hesap
            if tev_hesap is None:
                raise FaturaHatasi(
                    f"Satır {i}: {tevkifat.kod} tevkifatının muhasebe hesabı tanımlı "
                    f"değil (AYARLAR > Tevkifat Oranları).")
            tevkifat_hesap_toplam[tev_hesap.hesap_kodu] = (
                tevkifat_hesap_toplam.get(tev_hesap.hesap_kodu, SIFIR) + tev)

        cari_pb += satir_tutar + kdv_net
        hazir.append((stok, hesap, miktar, birim, kdv, tevkifat, proje))

    # KDV satırları (alış: Borç, satış: Alacak)
    for hkod, tutar in kdv_hesap_toplam.items():
        kdv_taraf = "B" if alis else "A"
        _ekle(kdv_taraf, tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf=kdv_taraf,
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV"))

    # Tevkifat satırları (ALIŞ -> 360 Alacak)
    for hkod, tutar in tevkifat_hesap_toplam.items():
        _ekle("A", tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf="A",
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV TEVKİFATI"))

    # Fazla KDV (yalnız şahsi alış) — 191'e borçlanan KDV kadar 602.01'e alacak.
    if fazla_kdv_toplam > 0:
        fazla_kdv_hesap = _fazla_kdv_hesabi()
        _ekle("A", fazla_kdv_toplam)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=fazla_kdv_hesap.hesap_kodu, taraf="A",
            islem_tutari=fazla_kdv_toplam, islem_pb=pb, islem_kuru=kur,
            aciklama="FAZLA KDV (ortak adına şahsi alış)"))

    # Karşı taraf (cari): alış -> Alacak, satış -> Borç. TL'si DENGE için diğer
    # satırların TL'sinden türetilir (tl_override) -> döviz kuruş farkı oluşmaz.
    cari_taraf = "A" if alis else "B"
    cari_tl = (borc_tl - alacak_tl) if cari_taraf == "A" else (alacak_tl - borc_tl)
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
        islem_tutari=cari_pb, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan,
        tl_override=cari_tl))

    return tip, cari, pb, kur, yevmiye_satirlari, hazir


def _hazirla_taslak(*, cari_id, satirlar, para_birimi, gider=False, sahsi_ortak_id=None):
    """Taslak oluştur/güncelle ortak hazırlığı: cari + satırları doğrular — TİP'e ihtiyaç
    DUYMAZ (muhasebe haritası + yevmiye satırları onaylamaya ertelenir, bkz. fatura_onayla).
    KDV stoktan, tevkifat ise satırda elle seçilmemişse stoktan (seçilmişse formdan) bu anda
    (taslak anında) çekilip FaturaSatir'e SNAPSHOT yazılır. Gider faturasında (gider=True)
    kalemler stok değil gider/duran-varlık hesabıdır (+ satırda seçilen KDV + opsiyonel
    tevkifat, bkz. _satir_coz). ``sahsi_ortak_id`` doluysa (Ortak adına şahsi alış) her
    satırın hesabı o ortak hesabına sabitlenir. (cari, pb, hazir) döner — hazir =
    [(stok, hesap, miktar, fiyat, kdv, tevkifat, proje), ...]."""
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FaturaHatasi("Cari bulunamadı.")
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise FaturaHatasi("Geçersiz para birimi.")

    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_ortak_id else None
    hazir = []
    for i, g in enumerate(satirlar, start=1):
        stok, hesap, kdv, tevkifat, proje = _satir_coz(g, i, gider, sahsi_ortak=sahsi_ortak)
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")
        hazir.append((stok, hesap, miktar, birim, kdv, tevkifat, proje))
    return cari, pb, hazir


def _muhasebe_satirlari(fatura, tip, cari, pb, kur):
    """fatura_onayla için: yevmiye satırlarını RAM'deki girdiden değil, faturaya zaten
    YAZILMIŞ FaturaSatir satırlarının KDV/tevkifat SNAPSHOT'ından kurar — taslak ile onay
    arasında stoğun KDV oranı değişse bile kullanıcının gördüğü taslak tutarlar korunur."""
    alis = (tip.yon == FaturaTipi.Yon.ALIS)
    cari_hesap = HesapPlani.objects.filter(
        hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if cari_hesap is None:
        raise FaturaHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")

    satirlar = list(fatura.satirlar.filter(silindi=False)
                    .select_related("stok__kategori", "hesap", "kdv", "tevkifat"))
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")

    sahsi = bool(fatura.sahsi_alis and fatura.sahsi_ortak_id)
    if sahsi and not (tip.gider and alis):
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}
    tevkifat_hesap_toplam = {}
    fazla_kdv_toplam = SIFIR       # yalnız şahsi alış: 602.01'e alacak
    borc_tl = SIFIR
    alacak_tl = SIFIR
    cari_pb = SIFIR

    def _ekle(taraf, tutar_pb):
        nonlocal borc_tl, alacak_tl
        tl = yuvarla(tutar_pb * kur, 2)
        if taraf == "B":
            borc_tl += tl
        else:
            alacak_tl += tl

    if tip.gider and not alis:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")

    for i, satir in enumerate(satirlar, start=1):
        if tip.gider != (satir.hesap_id is not None):
            raise FaturaHatasi(
                f"Satır {i}: kalem türü fatura tipiyle uyuşmuyor "
                f"(gider faturasında gider hesabı, diğer tiplerde stok olmalı).")
        if satir.hesap_id:
            mal_kodu, mal_ad, etiket = satir.hesap.hesap_kodu, satir.hesap.hesap_adi, satir.hesap.hesap_kodu
        else:
            stok = satir.stok
            kh = KategoriHesap.objects.filter(
                kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
            if kh is None:
                raise FaturaHatasi(
                    f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                    f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")
            mal_kodu, mal_ad, etiket = kh.hesap.hesap_kodu, stok.ad, stok.kod

        satir_tutar = satir.tutar
        kdv = satir.kdv
        satir_kdv = satir.kdv_tutari
        tevkifat = satir.tevkifat
        tev = satir.tevkifat_tutari
        kdv_net = satir_kdv - tev

        mal_taraf = "B" if alis else "A"
        mal_tutar = (satir_tutar + satir_kdv) if sahsi else satir_tutar
        _ekle(mal_taraf, mal_tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=mal_kodu, taraf=mal_taraf,
            islem_tutari=mal_tutar, islem_pb=pb, islem_kuru=kur, aciklama=mal_ad))

        kdv_post = satir_kdv if alis else kdv_net
        if kdv_post > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {etiket} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{kdv.oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + kdv_post)
            if sahsi:
                fazla_kdv_toplam += kdv_post

        if alis and tev > 0:
            tev_hesap = tevkifat.hesap
            if tev_hesap is None:
                raise FaturaHatasi(
                    f"Satır {i}: {tevkifat.kod} tevkifatının muhasebe hesabı tanımlı "
                    f"değil (AYARLAR > Tevkifat Oranları).")
            tevkifat_hesap_toplam[tev_hesap.hesap_kodu] = (
                tevkifat_hesap_toplam.get(tev_hesap.hesap_kodu, SIFIR) + tev)

        cari_pb += satir_tutar + kdv_net

    for hkod, tutar in kdv_hesap_toplam.items():
        kdv_taraf = "B" if alis else "A"
        _ekle(kdv_taraf, tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf=kdv_taraf,
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV"))

    if fazla_kdv_toplam > 0:
        fazla_kdv_hesap = _fazla_kdv_hesabi()
        _ekle("A", fazla_kdv_toplam)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=fazla_kdv_hesap.hesap_kodu, taraf="A",
            islem_tutari=fazla_kdv_toplam, islem_pb=pb, islem_kuru=kur,
            aciklama="FAZLA KDV (ortak adına şahsi alış)"))

    for hkod, tutar in tevkifat_hesap_toplam.items():
        _ekle("A", tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf="A",
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV TEVKİFATI"))

    cari_taraf = "A" if alis else "B"
    cari_tl = (borc_tl - alacak_tl) if cari_taraf == "A" else (alacak_tl - borc_tl)
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
        islem_tutari=cari_pb, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan,
        tl_override=cari_tl))

    return yevmiye_satirlari


def _aciklama(tip, cari, fatura_no):
    return buyuk_harf_tr(f"{tip.ad} - {cari.unvan}" + (f" - {fatura_no}" if fatura_no else ""))


def _satirlari_yaz(fatura, hazir, kullanici):
    for stok, hesap, miktar, birim, kdv, tevkifat, proje in hazir:
        FaturaSatir.objects.create(
            fatura=fatura, stok=stok, hesap=hesap, miktar=miktar, birim_fiyat=birim, kdv=kdv,
            tevkifat=tevkifat, yatirim_projesi=proje,
            created_by=kullanici, updated_by=kullanici)


def _depo_coz(depo_id):
    """depo_id boşsa None (hareket üretilmez); doluysa aktif depoyu çözer."""
    if depo_id in (None, ""):
        return None
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise FaturaHatasi("Depo bulunamadı.")
    return depo


def _hareketleri_yaz(fatura, depo, *, kur, kullanici):
    """Fatura kalemleri için stok hareketi: ALIŞ→giriş, SATIŞ→çıkış. Miktar fatura
    biriminden üretim birimine çevrilir (çevirici). Çıkışta eldeki yetmezse engellenir.
    ALIŞ yönünde ayrıca bir FIFO maliyet katmanı açılır: birim_maliyet_try = birim_fiyat
    (fatura para biriminde) × kur (TL karşılığı) × cevirici (1 üretim birimi kaç fatura
    birimi ediyorsa, üretim birimi o kadar daha pahalıdır — bkz. Stok.cevirici)."""
    alis = (fatura.tip.yon == FaturaTipi.Yon.ALIS)
    tur = StokHareket.Tur.GIRIS if alis else StokHareket.Tur.CIKIS
    for satir in fatura.satirlar.filter(silindi=False, stok__isnull=False).select_related("stok"):
        cevirici = satir.stok.cevirici or Decimal("1")
        uretim_miktar = yuvarla(satir.miktar / cevirici, 3)
        if uretim_miktar <= 0:
            raise FaturaHatasi(
                f"{satir.stok.kod}: çevirici ({cevirici}) ile dönüştürülen miktar "
                f"sıfır oluyor; miktarı veya çeviriciyi düzeltin.")
        birim_maliyet_try = yuvarla(satir.birim_fiyat * kur * cevirici, 6) if alis else None
        try:
            hareket_ekle(
                stok_id=satir.stok_id, depo_id=depo.pk, tarih=fatura.tarih, tur=tur,
                miktar=uretim_miktar,
                aciklama=_aciklama(fatura.tip, fatura.cari, fatura.fatura_no),
                kaynak=StokHareket.Kaynak.FATURA, fatura_satir=satir, kullanici=kullanici,
                birim_maliyet_try=birim_maliyet_try, kaynak_pb=fatura.para_birimi,
                kaynak_birim_fiyat=satir.birim_fiyat if alis else None,
                kaynak_kur=kur if alis else None)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))


def _hareketleri_iptal(fatura, *, kullanici):
    """Faturaya bağlı silinmemiş stok hareketlerini tek tek geri alır (hareket_sil
    üzerinden — ham toplu .update() DEĞİL, çünkü bir girişin maliyeti üretimde/satışta
    zaten tüketilmişse hareket_sil bunu tespit edip engelliyor; bkz. İrsaliye iptalindeki
    aynı desen, core/services/teklif_siparis.py)."""
    for hareket in StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False):
        try:
            hareket_sil(hareket, kullanici=kullanici)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))


def _fatura_hareket_ciftleri(fatura):
    """Faturanın aktif stok hareketlerinin (stok_id, depo_id) kümesi."""
    return set(StokHareket.objects.filter(
        fatura_satir__fatura=fatura, silindi=False).values_list("stok_id", "depo_id"))


def _negatif_eldeki_dogrula(ciftler):
    """Verilen (stok_id, depo_id) çiftlerinde eldeki negatife düştüyse hata. Bir alış
    faturası, malı satıldıktan sonra aşağı düzenlenince eldekinin eksiye düşmesini engeller
    (giriş `hareket_ekle`'de kontrol edilmez; bu güncelleme-sonrası backstop o açığı kapatır)."""
    for stok_id, depo_id in ciftler:
        if eldeki_miktar(stok_id, depo_id) < 0:
            raise FaturaHatasi(
                "Bu güncelleme bir stok+depoda eldeki miktarı negatife düşürüyor; "
                "önce o stoğun bağlı çıkış/satış hareketlerini düzeltin.")


@transaction.atomic
def fatura_taslak_olustur(*, cari_id, tarih, satirlar, tip_id=None, yon=None, fatura_no="",
                          para_birimi="TRY", depo_id=None, aciklama="", vade_tarihi=None,
                          sahsi_alis=False, sahsi_ortak_id=None, kullanici=None) -> Fatura:
    """Faturayı TASLAK olarak oluşturur — fiş/stok hareketi ÜRETMEZ (bkz. fatura_onayla).
    tip_id verilirse yön ondan türetilir; verilmezse `yon` zorunludur (İrsaliye'den otomatik
    açılan, tipi henüz bilinmeyen taslaklar için). ``sahsi_alis``/``sahsi_ortak_id``: Ortak
    adına şahsi alış — yalnız alış-gider faturasında; kalemlerin hesabı ortak hesabına
    sabitlenir (bkz. _hazirla_taslak/_satir_coz)."""
    tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first() if tip_id else None
    gider = bool(tip and tip.gider)
    if sahsi_alis and not sahsi_ortak_id:
        raise FaturaHatasi("Ortak adına şahsi alış için bir ortak hesabı seçin.")
    if sahsi_alis and not gider:
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_alis else None
    cari, pb, hazir = _hazirla_taslak(
        cari_id=cari_id, satirlar=satirlar, para_birimi=para_birimi, gider=gider,
        sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None))
    if tip is not None:
        cozulen_yon = tip.yon
    elif yon in FaturaTipi.Yon.values:
        cozulen_yon = yon
    else:
        raise FaturaHatasi("Fatura yönü belirlenemedi.")
    if gider and cozulen_yon != FaturaTipi.Yon.ALIS:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")
    if sahsi_ortak is not None and cozulen_yon != FaturaTipi.Yon.ALIS:
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış yönünde olabilir.")
    depo = None if gider else _depo_coz(depo_id)        # gider faturasında depo/stok hareketi yok
    fatura_no = (fatura_no or "").strip()
    # Sunucu tarafı yedek: vade boş + carinin ödeme koşulu tanımlıysa otomatik hesapla
    # (ön yüz JS'i zaten doldurur; bu yalnız JS çalışmadıysa/atlandıysa devreye girer).
    if vade_tarihi is None:
        vade_tarihi = vade_hesapla(cari, tarih)
    fatura = Fatura.objects.create(
        tip=tip, yon=cozulen_yon, durum=Fatura.Durum.TASLAK, cari=cari, tarih=tarih,
        fatura_no=fatura_no, para_birimi=pb, kur=1, fis=None, depo=depo,
        aciklama=(aciklama or "").strip(), vade_tarihi=vade_tarihi,
        sahsi_alis=bool(sahsi_ortak), sahsi_ortak=sahsi_ortak,
        created_by=kullanici, updated_by=kullanici)
    _satirlari_yaz(fatura, hazir, kullanici)
    return fatura


@transaction.atomic
def fatura_onayla(fatura: Fatura, kullanici=None, kur_override=None) -> Fatura:
    """TASLAK → ONAYLI: muhasebe haritasını (tip artık zorunlu) çözer, dengeli yevmiye
    fişini üretir ve — bu fatura bir İRSALİYE'den doğmadıysa (o zaten stokladı, çifte
    sayım olmasın) — depo verilmişse stok hareketlerini yazar. İdempotent (zaten onaylıysa
    sessiz). ``kur_override`` doluysa (kullanıcı Fatura formunda elle girdi/değiştirdi)
    carinin kur_tipi'ne göre otomatik hesaplama YERİNE doğrudan kullanılır."""
    if fatura.silindi:
        raise FaturaHatasi("İptal edilmiş fatura onaylanamaz.")
    if fatura.durum == Fatura.Durum.ONAYLI:
        return fatura
    if fatura.tip_id is None:
        raise FaturaHatasi("Fatura tipi seçilmeden onaylanamaz.")
    if fatura.tip.yon != fatura.yon:
        raise FaturaHatasi("Fatura tipi, faturanın yönüyle uyuşmuyor.")
    tip, cari = fatura.tip, fatura.cari
    kur = (Decimal("1") if fatura.para_birimi == "TRY"
          else (kur_override or _kur_coz(fatura.para_birimi, fatura.tarih, cari=cari)))
    yevmiye_satirlari = _muhasebe_satirlari(fatura, tip, cari, fatura.para_birimi, kur)
    try:
        fis = fis_olustur(tarih=fatura.tarih, satirlar=yevmiye_satirlari,
                          aciklama=_aciklama(tip, cari, fatura.fatura_no), kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.FATURA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    fatura.fis, fatura.kur, fatura.durum = fis, kur, Fatura.Durum.ONAYLI
    fatura.updated_by = kullanici
    fatura.save(update_fields=["fis", "kur", "durum", "updated_by", "updated_at"])
    zaten_stoklandi = fatura.kaynak_siparisler.filter(
        belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, silindi=False).exists()
    if fatura.depo_id and not zaten_stoklandi:
        _hareketleri_yaz(fatura, fatura.depo, kur=kur, kullanici=kullanici)
    return fatura


@transaction.atomic
def fatura_olustur(*, tip_id, cari_id, tarih, satirlar, fatura_no="",
                   para_birimi="TRY", depo_id=None, aciklama="", vade_tarihi=None,
                   sahsi_alis=False, sahsi_ortak_id=None, kullanici=None, kur=None) -> Fatura:
    """Kolaylık sarmalayıcısı: taslak oluşturur ve tip zaten bilindiği için HEMEN onaylar —
    tek atomik blok, onaylama başarısız olursa (eksik harita/kur/vb.) taslak da geri alınır
    (eskisi gibi tam atomik: ya hepsi ya hiçbiri). Tip'in önceden bilinmediği tek durum —
    İrsaliye'den otomatik açılan taslak — bunun yerine doğrudan fatura_taslak_olustur kullanır.
    ``kur`` doluysa (Fatura formunda elle girildi/değiştirildi) otomatik hesaplama YERİNE
    doğrudan kullanılır."""
    fatura = fatura_taslak_olustur(
        cari_id=cari_id, tarih=tarih, satirlar=satirlar, tip_id=tip_id,
        fatura_no=fatura_no, para_birimi=para_birimi, depo_id=depo_id,
        aciklama=aciklama, vade_tarihi=vade_tarihi, sahsi_alis=sahsi_alis,
        sahsi_ortak_id=sahsi_ortak_id, kullanici=kullanici)
    return fatura_onayla(fatura, kullanici=kullanici, kur_override=kur)


@transaction.atomic
def fatura_guncelle(fatura: Fatura, *, tip_id=None, cari_id, tarih, satirlar,
                    fatura_no="", para_birimi="TRY", depo_id=None, aciklama="",
                    vade_tarihi=None, sahsi_alis=False, sahsi_ortak_id=None,
                    kullanici=None, kur=None) -> Fatura:
    """Faturayı günceller. TASLAK ise hafif düzenleme (fiş/hareket yok — tip dahil her şey
    serbestçe değişebilir). ONAYLI ise bugünkü mevcut davranış AYNEN (bağlı fiş+stok
    hareketleri de reverse+rewrite edilir); yalnız koşul `fis_id`'den `durum`'a çevrilir.
    ``vade_tarihi`` burada OTOMATİK HESAPLANMAZ (bkz. fatura_taslak_olustur) — boş gelirse
    boş kalır; mevcut faturalar düzenlenirken beklenmedik bir vade yazılmasın diye kasıtlı.
    ``sahsi_alis``/``sahsi_ortak_id``: Ortak adına şahsi alış — sonradan kaldırılırsa (False/
    None gelirse) fiş normal alış-gider fişine döner (bkz. _hazirla/_muhasebe_satirlari)."""
    from django.utils import timezone
    if fatura.silindi:
        raise FaturaHatasi("Silinmiş fatura düzenlenemez.")
    if sahsi_alis and not sahsi_ortak_id:
        raise FaturaHatasi("Ortak adına şahsi alış için bir ortak hesabı seçin.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_alis else None

    if fatura.durum == Fatura.Durum.TASLAK:
        tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first() if tip_id else None
        gider = bool(tip and tip.gider)
        if sahsi_ortak is not None and not gider:
            raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
        cari, pb, hazir = _hazirla_taslak(
            cari_id=cari_id, satirlar=satirlar, para_birimi=para_birimi, gider=gider,
            sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None))
        cozulen_yon = tip.yon if tip is not None else fatura.yon
        if gider and cozulen_yon != FaturaTipi.Yon.ALIS:
            raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")
        depo = None if gider else _depo_coz(depo_id)
        fatura_no = (fatura_no or "").strip()
        fatura.satirlar.filter(silindi=False).update(
            silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        fatura.tip, fatura.yon, fatura.cari, fatura.tarih = tip, cozulen_yon, cari, tarih
        fatura.fatura_no, fatura.para_birimi, fatura.depo = fatura_no, pb, depo
        fatura.aciklama = (aciklama or "").strip()
        fatura.vade_tarihi = vade_tarihi
        fatura.sahsi_alis, fatura.sahsi_ortak = bool(sahsi_ortak), sahsi_ortak
        fatura.updated_by = kullanici
        fatura.save(update_fields=["tip", "yon", "cari", "tarih", "fatura_no", "para_birimi",
                                   "depo", "aciklama", "vade_tarihi", "sahsi_alis", "sahsi_ortak",
                                   "updated_by", "updated_at"])
        _satirlari_yaz(fatura, hazir, kullanici)
        return fatura

    # ONAYLI — bugünkü mevcut mantık aynen.
    if fatura.fis_id is None or fatura.fis.silindi:
        raise FaturaHatasi("Faturanın aktif yevmiye fişi yok; düzenlenemez.")
    tip, cari, pb, kur, yevmiye_satirlari, hazir = _hazirla(
        tip_id=tip_id, cari_id=cari_id, tarih=tarih, satirlar=satirlar,
        para_birimi=para_birimi, kur_override=kur,
        sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None))
    depo = None if tip.gider else _depo_coz(depo_id)    # gider faturasında depo/stok hareketi yok
    fatura_no = (fatura_no or "").strip()
    try:
        fis_guncelle(fatura.fis, tarih=tarih, satirlar=yevmiye_satirlari,
                     aciklama=_aciklama(tip, cari, fatura_no), kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    # Etkilenen (stok, depo) çiftleri — eski + yeni; güncelleme sonrası NEGATİF eldeki backstop'u.
    etkilenen = _fatura_hareket_ciftleri(fatura)
    # Eski stok hareketleri + satırları geri al (yeni çıkış kontrolü doğru eldekiyi görsün)
    _hareketleri_iptal(fatura, kullanici=kullanici)
    fatura.satirlar.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    fatura.tip, fatura.yon, fatura.cari, fatura.tarih = tip, tip.yon, cari, tarih
    fatura.fatura_no, fatura.para_birimi, fatura.kur, fatura.depo = fatura_no, pb, kur, depo
    fatura.aciklama = (aciklama or "").strip()
    fatura.vade_tarihi = vade_tarihi
    fatura.sahsi_alis, fatura.sahsi_ortak = bool(sahsi_ortak), sahsi_ortak
    fatura.updated_by = kullanici
    fatura.save(update_fields=["tip", "yon", "cari", "tarih", "fatura_no", "para_birimi",
                               "kur", "depo", "aciklama", "vade_tarihi", "sahsi_alis",
                               "sahsi_ortak", "updated_by", "updated_at"])
    _satirlari_yaz(fatura, hazir, kullanici)
    if depo is not None:
        _hareketleri_yaz(fatura, depo, kur=kur, kullanici=kullanici)
    _negatif_eldeki_dogrula(etkilenen | _fatura_hareket_ciftleri(fatura))
    return fatura


@transaction.atomic
def fatura_sil(fatura: Fatura, kullanici=None) -> None:
    """Faturayı KALICI olarak siler — bağlı yevmiye fişi ve stok hareketleri (+ varsa FIFO
    maliyet katmanları) dahil hiçbir iz kalmaz (CLAUDE.md'nin bu ekrana özel BİLİNÇLİ
    istisnası — kullanıcı isteği, bkz. proje belleği). Girdiği stok başka bir hareketle
    (satış/üretim) zaten tüketilmişse reddedilir (hareket_sil'in mevcut güvenlik kontrolü).
    Bu fatura bir İrsaliye'den doğduysa (irsaliye.fatura), o bağlantı temizlenir —
    İrsaliye SİLİNMEZ, "Faturaya Dönüştü" rozetini kaybedip yeniden düzenlenebilir hale
    gelir (bkz. teklif_siparis.teklif_siparis_onayi_geri_al)."""
    hareketler = list(StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False))
    for h in hareketler:
        try:
            hareket_sil(h, kullanici=kullanici)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))
    if fatura.fis_id and not fatura.fis.silindi:
        fis_iptal(fatura.fis, kullanici=kullanici)
    TeklifSiparis.objects.filter(fatura=fatura).update(fatura=None)
    katman_ids = list(StokMaliyetKatmani.objects.filter(
        stok_hareket__in=hareketler).values_list("id", flat=True))
    StokMaliyetTuketimi.objects.filter(katman_id__in=katman_ids).delete()
    StokMaliyetKatmani.objects.filter(id__in=katman_ids).delete()
    StokHareket.objects.filter(id__in=[h.pk for h in hareketler]).delete()
    fis_id = fatura.fis_id
    fatura.delete()                                    # FaturaSatir CASCADE
    if fis_id:
        YevmiyeFisi.objects.filter(pk=fis_id).delete()  # YevmiyeSatir CASCADE (fis PROTECT
                                                         # olduğu için fatura'dan SONRA silinir)
