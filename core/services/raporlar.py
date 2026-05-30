"""Rapor servisleri — hepsi YALNIZCA yevmiye satırlarından hesaplanır.

Değişmez (spec 3b): saklanan bakiye YOK; mizan/bilanço/gelir tablosu her zaman
``YevmiyeSatir``'dan türetilir. İptal (soft-delete) fiş/satır hesaba katılmaz.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import YevmiyeSatir

SIFIR = Decimal("0.00")


def mali_yil_araligi(tarih: datetime.date | None = None):
    """İçinde bulunulan mali yıl (takvim yılı) [1 Ocak .. 31 Aralık]."""
    t = tarih or timezone.localdate()
    return datetime.date(t.year, 1, 1), datetime.date(t.year, 12, 31)


def _varsayilan(baslangic, bitis):
    vb, vs = mali_yil_araligi()
    return (baslangic or vb), (bitis or vs)


def _hareketler(baslangic: datetime.date, bitis: datetime.date) -> list[dict]:
    """Tarih aralığında hesap bazında borç/alacak toplamları (iptal hariç).

    Tüm raporların ortak ham verisi. Saklanan bakiye yoktur; her çağrıda
    yevmiye satırlarından toplanır.
    """
    qs = (
        YevmiyeSatir.objects.filter(
            silindi=False,
            fis__silindi=False,
            fis__tarih__gte=baslangic,
            fis__tarih__lte=bitis,
        )
        .values(
            "hesap_id", "hesap__hesap_adi",
            "hesap__rapor_grubu", "hesap__rapor_kalemi",
        )
        .annotate(borc=Sum("borc"), alacak=Sum("alacak"))
        .order_by("hesap_id")
    )
    return [
        dict(
            kod=r["hesap_id"],
            ad=r["hesap__hesap_adi"],
            grup=r["hesap__rapor_grubu"],
            kalem=r["hesap__rapor_kalemi"],
            borc=r["borc"] or SIFIR,
            alacak=r["alacak"] or SIFIR,
        )
        for r in qs
    ]


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


def mizan(baslangic: datetime.date | None = None,
          bitis: datetime.date | None = None) -> Mizan:
    """Tarih aralığındaki (varsayılan: mali yıl) mizanı üretir. İptal hariç."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    satirlar = [
        MizanSatir(h["kod"], h["ad"], h["borc"], h["alacak"])
        for h in _hareketler(baslangic, bitis)
    ]
    return Mizan(baslangic=baslangic, bitis=bitis, satirlar=satirlar)


# ---------------------------------------------------------------------------
# BİLANÇO (5b) — sınıf 1-5 hesap bakiyeleri, rapor_kalemi'ne göre gruplu
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
    tutar: Decimal   # grubun doğal yönünde işaretli (aktif: borç+, pasif: alacak+)


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
    aktif: list   # BilancoGrup listesi
    pasif: list   # BilancoGrup listesi (Özkaynaklar dönem sonucu dahil)
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


def bilanco(baslangic: datetime.date | None = None,
            bitis: datetime.date | None = None) -> Bilanco:
    """Canlı bilanço (TL). Aktif = Pasif; dönem sonucu (sınıf 6+7 neti)
    Özkaynaklar'a 'Dönem Net Kârı/Zararı' olarak eklenir, böylece denge kurulur.

    Not: Mizan her zaman dengeli olduğundan (Σborç=Σalacak), sınıf 1-5 bakiyeleri
    ile sınıf 6-7 sonucu birlikte Aktif=Pasif eşitliğini garanti eder.
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    aktif = {k: BilancoGrup(k, ad) for k, ad in AKTIF_KALEM}
    pasif = {k: BilancoGrup(k, ad) for k, ad in PASIF_KALEM}
    donem_sonucu = SIFIR

    for h in _hareketler(baslangic, bitis):
        if h["grup"] == "BILANCO":
            net = h["borc"] - h["alacak"]            # borç-pozitif
            kalem = h["kalem"]
            if kalem in aktif:
                aktif[kalem].satirlar.append(BilancoSatir(h["kod"], h["ad"], net))
            elif kalem in pasif:
                pasif[kalem].satirlar.append(BilancoSatir(h["kod"], h["ad"], -net))
        else:
            # Sınıf 6 + 7 (gelir/maliyet) -> dönem sonucu (kâr = alacak-borç)
            donem_sonucu += h["alacak"] - h["borc"]

    pasif["OZK"].satirlar.append(
        BilancoSatir("—", "DÖNEM NET KÂRI/ZARARI (HESAPLANAN)", donem_sonucu)
    )
    return Bilanco(
        baslangic=baslangic, bitis=bitis,
        aktif=list(aktif.values()), pasif=list(pasif.values()),
        donem_sonucu=donem_sonucu,
    )


# ---------------------------------------------------------------------------
# GELİR TABLOSU (5b) — YALNIZCA 6'lı hesaplardan; spec bölüm 4 haritası
# ---------------------------------------------------------------------------
@dataclass
class GelirSatir:
    etiket: str
    tutar: Decimal
    ara: bool = False      # ara/sonuç toplamı mı (kalın gösterilir)
    isaret: str = ""       # "+" / "-" / "" (görsel ipucu)


@dataclass
class GelirTablosu:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list
    donem_net_kari: Decimal

    def deger(self, etiket_baslangic: str) -> Decimal | None:
        for s in self.satirlar:
            if s.etiket.startswith(etiket_baslangic):
                return s.tutar
        return None


def gelir_tablosu(baslangic: datetime.date | None = None,
                  bitis: datetime.date | None = None) -> GelirTablosu:
    """Canlı gelir tablosu (TL). Yalnızca 6'lı hesaplar; 7'liler rapora GİRMEZ.

    7/A yansıtması ay sonu MANUEL yapılır; ay içinde maliyet tarafı kapanışa
    kadar eksik görünebilir — bu normaldir.
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    har = {h["kod"]: h for h in _hareketler(baslangic, bitis)}

    def alacak_net(kodlar) -> Decimal:
        return sum((har[k]["alacak"] - har[k]["borc"] for k in kodlar if k in har), SIFIR)

    def borc_net(kodlar) -> Decimal:
        return sum((har[k]["borc"] - har[k]["alacak"] for k in kodlar if k in har), SIFIR)

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

    return GelirTablosu(baslangic, bitis, rows, net)
