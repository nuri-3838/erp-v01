"""Rapor servisleri — hepsi YALNIZCA yevmiye satırlarından hesaplanır.

Değişmez (spec 3b): saklanan bakiye YOK; mizan/bilanço/gelir tablosu (TL ve USD)
her zaman ``YevmiyeSatir``'dan türetilir. İptal (soft-delete) fiş/satır hariç.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import YevmiyeSatir
from core.services.yevmiye import kur_usd_bul

SIFIR = Decimal("0.00")


def mali_yil_araligi(tarih: datetime.date | None = None):
    """İçinde bulunulan mali yıl (takvim yılı) [1 Ocak .. 31 Aralık]."""
    t = tarih or timezone.localdate()
    return datetime.date(t.year, 1, 1), datetime.date(t.year, 12, 31)


def _varsayilan(baslangic, bitis):
    vb, vs = mali_yil_araligi()
    return (baslangic or vb), (bitis or vs)


def _hareketler(baslangic: datetime.date, bitis: datetime.date) -> list[dict]:
    """Tarih aralığında hesap bazında borç/alacak toplamları (iptal hariç)."""
    qs = (
        YevmiyeSatir.objects.filter(
            silindi=False, fis__silindi=False,
            fis__tarih__gte=baslangic, fis__tarih__lte=bitis,
        )
        .values("hesap_id", "hesap__hesap_adi",
                "hesap__rapor_grubu", "hesap__rapor_kalemi")
        .annotate(borc=Sum("borc"), alacak=Sum("alacak"))
        .order_by("hesap_id")
    )
    return [
        dict(kod=r["hesap_id"], ad=r["hesap__hesap_adi"],
             grup=r["hesap__rapor_grubu"], kalem=r["hesap__rapor_kalemi"],
             borc=r["borc"] or SIFIR, alacak=r["alacak"] or SIFIR)
        for r in qs
    ]


def _satirlar(baslangic: datetime.date, bitis: datetime.date):
    """Ham yevmiye satırları (iptal hariç) — satır bazlı (tarihi kur) çevrim için."""
    return (
        YevmiyeSatir.objects.filter(
            silindi=False, fis__silindi=False,
            fis__tarih__gte=baslangic, fis__tarih__lte=bitis,
        ).select_related("fis", "hesap")
    )


# ---------------------------------------------------------------------------
# MİZAN (5a)
# ---------------------------------------------------------------------------
@dataclass
class MizanSatir:
    hesap_kodu: str
    hesap_adi: str
    borc: Decimal
    alacak: Decimal

    @property
    def borc_bakiye(self) -> Decimal:
        net = self.borc - self.alacak
        return net if net > 0 else SIFIR

    @property
    def alacak_bakiye(self) -> Decimal:
        net = self.alacak - self.borc
        return net if net > 0 else SIFIR


@dataclass
class Mizan:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list = field(default_factory=list)

    @property
    def toplam_borc(self) -> Decimal:
        return sum((s.borc for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak(self) -> Decimal:
        return sum((s.alacak for s in self.satirlar), SIFIR)

    @property
    def toplam_borc_bakiye(self) -> Decimal:
        return sum((s.borc_bakiye for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak_bakiye(self) -> Decimal:
        return sum((s.alacak_bakiye for s in self.satirlar), SIFIR)

    @property
    def hareket_denk(self) -> bool:
        return self.toplam_borc == self.toplam_alacak

    @property
    def bakiye_denk(self) -> bool:
        return self.toplam_borc_bakiye == self.toplam_alacak_bakiye


def mizan(baslangic=None, bitis=None) -> Mizan:
    """Tarih aralığındaki (varsayılan: mali yıl) mizanı üretir. İptal hariç."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    satirlar = [
        MizanSatir(h["kod"], h["ad"], h["borc"], h["alacak"])
        for h in _hareketler(baslangic, bitis)
    ]
    return Mizan(baslangic=baslangic, bitis=bitis, satirlar=satirlar)


# ---------------------------------------------------------------------------
# BİLANÇO (5b/5c) — sınıf 1-5; rapor_kalemi'ne göre gruplu
# ---------------------------------------------------------------------------
AKTIF_KALEM = [("DV", "Dönen Varlıklar"), ("DDV", "Duran Varlıklar")]
PASIF_KALEM = [
    ("KVYK", "Kısa Vadeli Yabancı Kaynaklar"),
    ("UVYK", "Uzun Vadeli Yabancı Kaynaklar"),
    ("OZK", "Özkaynaklar"),
]


@dataclass
class BilancoSatir:
    kod: str
    ad: str
    tutar: Decimal


@dataclass
class BilancoGrup:
    kod: str
    ad: str
    satirlar: list = field(default_factory=list)

    @property
    def toplam(self) -> Decimal:
        return sum((s.tutar for s in self.satirlar), SIFIR)


@dataclass
class Bilanco:
    baslangic: datetime.date
    bitis: datetime.date
    aktif: list
    pasif: list
    donem_sonucu: Decimal

    @property
    def aktif_toplam(self) -> Decimal:
        return sum((g.toplam for g in self.aktif), SIFIR)

    @property
    def pasif_toplam(self) -> Decimal:
        return sum((g.toplam for g in self.pasif), SIFIR)

    @property
    def denk_mi(self) -> bool:
        return self.aktif_toplam == self.pasif_toplam


def bilanco(baslangic=None, bitis=None) -> Bilanco:
    """Canlı bilanço (TL). Aktif = Pasif; dönem sonucu (sınıf 6+7 neti)
    Özkaynaklar'a 'Dönem Net Kârı/Zararı' olarak eklenir."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    aktif = {k: BilancoGrup(k, ad) for k, ad in AKTIF_KALEM}
    pasif = {k: BilancoGrup(k, ad) for k, ad in PASIF_KALEM}
    donem_sonucu = SIFIR

    for h in _hareketler(baslangic, bitis):
        if h["grup"] == "BILANCO":
            net = h["borc"] - h["alacak"]
            kalem = h["kalem"]
            if kalem in aktif:
                aktif[kalem].satirlar.append(BilancoSatir(h["kod"], h["ad"], net))
            elif kalem in pasif:
                pasif[kalem].satirlar.append(BilancoSatir(h["kod"], h["ad"], -net))
        else:
            donem_sonucu += h["alacak"] - h["borc"]

    pasif["OZK"].satirlar.append(
        BilancoSatir("—", "DÖNEM NET KÂRI/ZARARI (HESAPLANAN)", donem_sonucu)
    )
    return Bilanco(baslangic, bitis, list(aktif.values()), list(pasif.values()),
                   donem_sonucu)


# ---------------------------------------------------------------------------
# GELİR TABLOSU (5b/5c) — YALNIZCA 6'lı; spec bölüm 4 haritası
# ---------------------------------------------------------------------------
@dataclass
class GelirSatir:
    etiket: str
    tutar: Decimal
    ara: bool = False
    isaret: str = ""


def _gelir_rows(alacak_net, borc_net):
    """A→Dönem Net Kârı satırlarını verilen net fonksiyonlarıyla kurar."""
    rows: list[GelirSatir] = []
    A = alacak_net(["600", "601", "602"])
    rows.append(GelirSatir("A. Brüt Satışlar", A, isaret="+"))
    B = borc_net(["610", "611", "612"])
    rows.append(GelirSatir("B. Satış İndirimleri (-)", B, isaret="-"))
    net_satislar = A - B
    rows.append(GelirSatir("Net Satışlar", net_satislar, ara=True))
    C = borc_net(["620", "621", "622", "623"])
    rows.append(GelirSatir("C. Satışların Maliyeti (-)", C, isaret="-"))
    brut = net_satislar - C
    rows.append(GelirSatir("BRÜT SATIŞ KÂRI", brut, ara=True))
    D = borc_net(["630", "631", "632"])
    rows.append(GelirSatir("D. Faaliyet Giderleri (-)", D, isaret="-"))
    faaliyet = brut - D
    rows.append(GelirSatir("FAALİYET KÂRI", faaliyet, ara=True))
    E = alacak_net(["640", "642", "645", "646", "647", "649"])
    rows.append(GelirSatir("E. Diğer Olağan Gelir ve Kârlar", E, isaret="+"))
    F = borc_net(["653", "654", "655", "656", "657", "659"])
    rows.append(GelirSatir("F. Diğer Olağan Gider ve Zararlar (-)", F, isaret="-"))
    G = borc_net(["660", "661"])
    rows.append(GelirSatir("G. Finansman Giderleri (-)", G, isaret="-"))
    olagan = faaliyet + E - F - G
    rows.append(GelirSatir("OLAĞAN KÂR", olagan, ara=True))
    H = alacak_net(["671", "679"])
    rows.append(GelirSatir("H. Olağandışı Gelir ve Kârlar", H, isaret="+"))
    I = borc_net(["680", "681", "689"])
    rows.append(GelirSatir("I. Olağandışı Gider ve Zararlar (-)", I, isaret="-"))
    donem_kari = olagan + H - I
    rows.append(GelirSatir("DÖNEM KÂRI", donem_kari, ara=True))
    J = borc_net(["691"])
    rows.append(GelirSatir("J. Dönem Kârı Vergi Karşılığı (-)", J, isaret="-"))
    net = donem_kari - J
    rows.append(GelirSatir("DÖNEM NET KÂRI", net, ara=True))
    return rows, net


@dataclass
class GelirTablosu:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list
    donem_net_kari: Decimal

    def deger(self, etiket_baslangic: str):
        for s in self.satirlar:
            if s.etiket.startswith(etiket_baslangic):
                return s.tutar
        return None


def gelir_tablosu(baslangic=None, bitis=None) -> GelirTablosu:
    """Canlı gelir tablosu (TL). Yalnızca 6'lı; 7'liler GİRMEZ."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    har = {h["kod"]: h for h in _hareketler(baslangic, bitis)}

    def alacak_net(kodlar):
        return sum((har[k]["alacak"] - har[k]["borc"] for k in kodlar if k in har), SIFIR)

    def borc_net(kodlar):
        return sum((har[k]["borc"] - har[k]["alacak"] for k in kodlar if k in har), SIFIR)

    rows, net = _gelir_rows(alacak_net, borc_net)
    return GelirTablosu(baslangic, bitis, rows, net)


# ---------------------------------------------------------------------------
# USD GELİR TABLOSU (5c) — her hareket KENDİ fişinin kur_usd'siyle (tarihi kur)
# ---------------------------------------------------------------------------
@dataclass
class GelirTablosuUSD:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list
    donem_net_kari: Decimal
    haric_tl: Decimal     # kur_usd'si girilmemiş, USD'ye dahil edilemeyen tutar (TL)

    def deger(self, etiket_baslangic: str):
        for s in self.satirlar:
            if s.etiket.startswith(etiket_baslangic):
                return s.tutar
        return None


def gelir_tablosu_usd(baslangic=None, bitis=None) -> GelirTablosuUSD:
    """USD gelir tablosu: her 6'lı hareket USD = TL ÷ fiş.kur_usd (tarihi kur).
    kur_usd'si boş fişlerin hareketleri DAHİL EDİLMEZ; toplamı ``haric_tl``'de."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    usd_borc: dict[str, Decimal] = {}
    usd_alacak: dict[str, Decimal] = {}
    haric = SIFIR

    for ln in _satirlar(baslangic, bitis):
        kod = ln.hesap_id
        if not kod.startswith("6"):
            continue
        kur = ln.fis.kur_usd
        if not kur or kur <= 0:
            haric += ln.borc + ln.alacak
            continue
        usd_borc[kod] = usd_borc.get(kod, SIFIR) + ln.borc / kur
        usd_alacak[kod] = usd_alacak.get(kod, SIFIR) + ln.alacak / kur

    def alacak_net(kodlar):
        return sum((usd_alacak.get(k, SIFIR) - usd_borc.get(k, SIFIR) for k in kodlar), SIFIR)

    def borc_net(kodlar):
        return sum((usd_borc.get(k, SIFIR) - usd_alacak.get(k, SIFIR) for k in kodlar), SIFIR)

    rows, net = _gelir_rows(alacak_net, borc_net)
    return GelirTablosuUSD(baslangic, bitis, rows, net, haric)


# ---------------------------------------------------------------------------
# USD BİLANÇO (5c) — parasal: rapor tarihi kuru; parasal değil: tarihi kur
# ---------------------------------------------------------------------------
@dataclass
class BilancoUSD:
    baslangic: datetime.date
    bitis: datetime.date
    rapor_tarihi: datetime.date
    kur: Decimal | None        # rapor tarihi (kapanış) USD kuru
    aktif: list
    pasif: list
    cevrim_farki: Decimal
    kur_yok: bool = False

    @property
    def aktif_toplam(self) -> Decimal:
        return sum((g.toplam for g in self.aktif), SIFIR)

    @property
    def pasif_toplam(self) -> Decimal:
        return sum((g.toplam for g in self.pasif), SIFIR)

    @property
    def denk_mi(self) -> bool:
        return self.aktif_toplam == self.pasif_toplam


def bilanco_usd(baslangic=None, bitis=None) -> BilancoUSD:
    """USD bilanço (parasal revalüasyon). ``bitis`` = rapor tarihi.

    - parasal=evet kalemler: rapor tarihi (kapanış) kuruyla (TL ÷ kapanış kuru).
    - parasal=hayır kalemler: tarihi kurla (her hareket kendi fiş.kur_usd'siyle).
    - Aradaki denge plug'ı 'kur çevrim farkı' (TL tutmaktan doğan USD kâr/zararı).
    - Rapor tarihinde kur yoksa son yayımlanan TCMB kuru; o da yoksa kur_yok=True.
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    kapanis = kur_usd_bul(bitis)
    if not kapanis or kapanis <= 0:
        return BilancoUSD(
            baslangic, bitis, bitis, None,
            [BilancoGrup(k, ad) for k, ad in AKTIF_KALEM],
            [BilancoGrup(k, ad) for k, ad in PASIF_KALEM],
            SIFIR, kur_yok=True,
        )

    tl: dict[str, Decimal] = {}        # hesap TL net (borç-alacak)
    hist: dict[str, Decimal] = {}      # hesap tarihi USD net (Σ net/kur)
    meta: dict[str, tuple] = {}        # kod -> (ad, grup, kalem, parasal)
    donem_hist = SIFIR                 # sınıf 6+7 tarihi USD sonucu (kâr=alacak-borç)

    for ln in _satirlar(baslangic, bitis):
        kod = ln.hesap_id
        meta[kod] = (ln.hesap.hesap_adi, ln.hesap.rapor_grubu,
                     ln.hesap.rapor_kalemi, ln.hesap.parasal)
        kur = ln.fis.kur_usd
        if kod.startswith("6") or kod.startswith("7"):
            if kur and kur > 0:
                donem_hist += (ln.alacak - ln.borc) / kur
            continue
        net_tl = ln.borc - ln.alacak
        tl[kod] = tl.get(kod, SIFIR) + net_tl
        if kur and kur > 0:
            hist[kod] = hist.get(kod, SIFIR) + net_tl / kur

    aktif = {k: BilancoGrup(k, ad) for k, ad in AKTIF_KALEM}
    pasif = {k: BilancoGrup(k, ad) for k, ad in PASIF_KALEM}

    for kod, (ad, grup, kalem, parasal) in sorted(meta.items()):
        if grup != "BILANCO":
            continue
        if parasal:                              # parasal -> kapanış kuru
            usd = tl.get(kod, SIFIR) / kapanis
        else:                                    # parasal değil -> tarihi kur
            usd = hist.get(kod, SIFIR)
        if kalem in aktif:
            aktif[kalem].satirlar.append(BilancoSatir(kod, ad, usd))
        elif kalem in pasif:
            pasif[kalem].satirlar.append(BilancoSatir(kod, ad, -usd))

    pasif["OZK"].satirlar.append(
        BilancoSatir("—", "DÖNEM NET KÂRI/ZARARI (HESAPLANAN)", donem_hist)
    )

    aktif_top = sum((g.toplam for g in aktif.values()), SIFIR)
    pasif_acc = sum((g.toplam for g in pasif.values()), SIFIR)
    cevrim = aktif_top - pasif_acc               # dengeyi kuran çevrim farkı
    pasif["OZK"].satirlar.append(
        BilancoSatir("—", "KUR ÇEVRİM FARKI", cevrim)
    )

    return BilancoUSD(
        baslangic, bitis, bitis, kapanis,
        list(aktif.values()), list(pasif.values()), cevrim,
    )
