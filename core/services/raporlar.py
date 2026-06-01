"""Rapor servisleri — hepsi YALNIZCA yevmiye satırlarından hesaplanır.

Değişmez (spec 3b): saklanan bakiye YOK. İptal (soft-delete) fiş/satır hariç.

USD modeli (TARİHSEL/donmuş): her satırın USD karşılığı, KENDİ fişinin tarihindeki
kurla hesaplanıp donar. Rapor anında tek kurla bölme veya kapanış-kuru revalüasyonu
YOKTUR. USD bilanço/gelir tablosu, USD mizandan TL ile AYNI mantıkla türetilir.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.models import HesapPlani, YevmiyeSatir

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
    """Ham yevmiye satırları (iptal hariç) — satır bazlı USD çevrimi için."""
    return (
        YevmiyeSatir.objects.filter(
            silindi=False, fis__silindi=False,
            fis__tarih__gte=baslangic, fis__tarih__lte=bitis,
        ).select_related("fis", "hesap")
    )


def _ana_kod(kod: str) -> str:
    """Hesabın ANA (üst) hesabı = ilk segment (320.10.0001 -> 320)."""
    return kod.split(".")[0]


def _ana_topla(har: list) -> list:
    """Per-hesap hareketleri ANA hesaba toplar (roll-up). grup/kalem ana hesaptan.
    Alt hesapsız planda her hesap kendi anasıdır -> sonuç değişmez."""
    grup = {}
    for h in har:
        ak = _ana_kod(h["kod"])
        d = grup.get(ak)
        if d is None:
            d = grup[ak] = dict(borc=SIFIR, alacak=SIFIR)
        d["borc"] += h["borc"]
        d["alacak"] += h["alacak"]
    metalar = {x.hesap_kodu: x for x in
               HesapPlani.objects.filter(hesap_kodu__in=list(grup))}
    sonuc = []
    for ak in sorted(grup):
        m = metalar.get(ak)
        d = grup[ak]
        sonuc.append(dict(
            kod=ak, ad=(m.hesap_adi if m else ak),
            grup=(m.rapor_grubu if m else ""), kalem=(m.rapor_kalemi if m else ""),
            borc=d["borc"], alacak=d["alacak"]))
    return sonuc


def _mizan_detay_satirlar(har: list) -> list:
    """Muavin: her ana hesap (rolled, seviye 0) + altındaki hesaplar (seviye>0)."""
    har_map = {h["kod"]: h for h in har}
    gruplar = {}
    for h in har:
        gruplar.setdefault(_ana_kod(h["kod"]), []).append(h["kod"])
    metalar = {x.hesap_kodu: x for x in
               HesapPlani.objects.filter(hesap_kodu__in=list(gruplar))}
    satirlar = []
    for ak in sorted(gruplar):
        kodlar = gruplar[ak]
        b = sum((har_map[k]["borc"] for k in kodlar), SIFIR)
        a = sum((har_map[k]["alacak"] for k in kodlar), SIFIR)
        m = metalar.get(ak)
        satirlar.append(MizanSatir(ak, m.hesap_adi if m else ak, b, a, seviye=0))
        for k in sorted(kodlar):
            if k == ak:
                continue   # ana hesabın doğrudan hareketi başlıkta toplandı
            h = har_map[k]
            satirlar.append(MizanSatir(k, h["ad"], h["borc"], h["alacak"],
                                       seviye=k.count(".")))
    return satirlar


# ---------------------------------------------------------------------------
# MİZAN (TL) — 5a
# ---------------------------------------------------------------------------
@dataclass
class MizanSatir:
    hesap_kodu: str
    hesap_adi: str
    borc: Decimal
    alacak: Decimal
    seviye: int = 0

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
    detay: bool = False

    @property
    def _ana(self):
        # Toplamlar yalnız ANA (seviye 0, rolled) satırlardan -> detay’da çift sayma yok.
        return [s for s in self.satirlar if getattr(s, "seviye", 0) == 0]

    @property
    def toplam_borc(self) -> Decimal:
        return sum((s.borc for s in self._ana), SIFIR)

    @property
    def toplam_alacak(self) -> Decimal:
        return sum((s.alacak for s in self._ana), SIFIR)

    @property
    def toplam_borc_bakiye(self) -> Decimal:
        return sum((s.borc_bakiye for s in self._ana), SIFIR)

    @property
    def toplam_alacak_bakiye(self) -> Decimal:
        return sum((s.alacak_bakiye for s in self._ana), SIFIR)

    @property
    def hareket_denk(self) -> bool:
        return self.toplam_borc == self.toplam_alacak

    @property
    def bakiye_denk(self) -> bool:
        return self.toplam_borc_bakiye == self.toplam_alacak_bakiye


def mizan(baslangic=None, bitis=None, detay=False) -> Mizan:
    """TL mizan. detay=False -> ÖZET (ana hesaplar, alt bakiyeler toplanmış);
    detay=True -> MUAVİN (ana + alt hiyerarşik). İkisinde de denge tutar."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    har = _hareketler(baslangic, bitis)
    if detay:
        satirlar = _mizan_detay_satirlar(har)
    else:
        satirlar = [MizanSatir(o["kod"], o["ad"], o["borc"], o["alacak"])
                    for o in _ana_topla(har)]
    return Mizan(baslangic=baslangic, bitis=bitis, satirlar=satirlar, detay=detay)


# ---------------------------------------------------------------------------
# HESAP EKSTRESİ (TL) — belirtilen hesabın hareketleri + yürüyen bakiye
# ---------------------------------------------------------------------------
@dataclass
class EkstreSatir:
    tarih: datetime.date
    fis_pk: int
    fis_yil: int
    fis_no: int
    fis_aciklama: str
    satir_aciklama: str
    borc: Decimal
    alacak: Decimal
    yur_bakiye: Decimal  # kümülatif borç − alacak; + = borç bakiye, − = alacak bakiye


@dataclass
class Ekstre:
    hesap_kodu: str
    hesap_adi: str
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list  # list[EkstreSatir]

    @property
    def toplam_borc(self) -> Decimal:
        return sum((s.borc for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak(self) -> Decimal:
        return sum((s.alacak for s in self.satirlar), SIFIR)

    @property
    def bakiye(self) -> Decimal:
        """Net borç pozisyonu: pozitif = borç bakiye, negatif = alacak bakiye."""
        return self.toplam_borc - self.toplam_alacak


def ekstre(hesap_kodu: str, baslangic=None, bitis=None) -> Ekstre:
    """Belirtilen hesabın tarih aralığındaki hareket ekstresi, yürüyen bakiyeli.

    Yalnızca aktif (iptal edilmemiş) fişlerin satırları; fiş tarih + fis_no + satır id
    sırasında. Toplam borç/alacak/bakiye, mizandaki aynı hesabın değerleriyle birebir tutar.
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    qs = (
        YevmiyeSatir.objects.filter(
            hesap_id=hesap_kodu,
            silindi=False, fis__silindi=False,
            fis__tarih__gte=baslangic, fis__tarih__lte=bitis,
        )
        .select_related("fis", "hesap")
        .order_by("fis__tarih", "fis__fis_no", "id")
    )
    hesap_adi = ""
    satirlar = []
    kumulatif = SIFIR
    for ln in qs:
        if not hesap_adi:
            hesap_adi = ln.hesap.hesap_adi
        kumulatif += ln.borc - ln.alacak
        satirlar.append(EkstreSatir(
            tarih=ln.fis.tarih,
            fis_pk=ln.fis.pk,
            fis_yil=ln.fis.yil,
            fis_no=ln.fis.fis_no,
            fis_aciklama=ln.fis.aciklama,
            satir_aciklama=ln.aciklama,
            borc=ln.borc,
            alacak=ln.alacak,
            yur_bakiye=kumulatif,
        ))
    if not hesap_adi:
        h = HesapPlani.objects.filter(hesap_kodu=hesap_kodu).first()
        hesap_adi = h.hesap_adi if h else hesap_kodu
    return Ekstre(
        hesap_kodu=hesap_kodu,
        hesap_adi=hesap_adi,
        baslangic=baslangic,
        bitis=bitis,
        satirlar=satirlar,
    )


# ---------------------------------------------------------------------------
# Gruplama / harita yardımcıları (TL ve USD ortak)
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


def _bilanco_kur(satir_listesi):
    """(kod, ad, grup, kalem, net) tuple'larından Aktif/Pasif gruplarını + dönem
    sonucunu kurar. TL ve USD bilançonun ORTAK mantığı (tutarlar dışında aynı)."""
    aktif = {k: BilancoGrup(k, ad) for k, ad in AKTIF_KALEM}
    pasif = {k: BilancoGrup(k, ad) for k, ad in PASIF_KALEM}
    donem = SIFIR
    for kod, ad, grup, kalem, net in satir_listesi:
        if grup == "BILANCO":
            if kalem in aktif:
                aktif[kalem].satirlar.append(BilancoSatir(kod, ad, net))
            elif kalem in pasif:
                pasif[kalem].satirlar.append(BilancoSatir(kod, ad, -net))
        else:
            donem += -net  # net = borç-alacak; kâr = alacak-borç = -net
    pasif["OZK"].satirlar.append(
        BilancoSatir("—", "DÖNEM NET KÂRI/ZARARI (HESAPLANAN)", donem)
    )
    return list(aktif.values()), list(pasif.values()), donem


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
    """Canlı bilanço (TL). Aktif = Pasif; dönem sonucu Özkaynaklar'a eklenir."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    satirlar = [
        (o["kod"], o["ad"], o["grup"], o["kalem"], o["borc"] - o["alacak"])
        for o in _ana_topla(_hareketler(baslangic, bitis))
    ]
    aktif, pasif, donem = _bilanco_kur(satirlar)
    return Bilanco(baslangic, bitis, aktif, pasif, donem)


# ---------------------------------------------------------------------------
# GELİR TABLOSU haritası (TL ve USD ortak)
# ---------------------------------------------------------------------------
@dataclass
class GelirSatir:
    etiket: str
    tutar: Decimal
    ara: bool = False
    isaret: str = ""


def _gelir_rows(alacak_net, borc_net):
    """A→Dönem Net Kârı satırları (spec bölüm 4); verilen net fonksiyonlarıyla."""
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
    har = {o["kod"]: o for o in _ana_topla(_hareketler(baslangic, bitis))}

    def alacak_net(kodlar):
        return sum((har[k]["alacak"] - har[k]["borc"] for k in kodlar if k in har), SIFIR)

    def borc_net(kodlar):
        return sum((har[k]["borc"] - har[k]["alacak"] for k in kodlar if k in har), SIFIR)

    rows, net = _gelir_rows(alacak_net, borc_net)
    return GelirTablosu(baslangic, bitis, rows, net)


# ===========================================================================
# USD MİZAN — tarihsel/donmuş (5c, yeniden yazıldı)
# ===========================================================================
@dataclass
class MizanUSDSatir:
    hesap_kodu: str
    hesap_adi: str
    grup: str
    kalem: str
    usd_borc: Decimal
    usd_alacak: Decimal

    @property
    def borc_bakiye(self) -> Decimal:
        n = self.usd_borc - self.usd_alacak
        return n if n > 0 else SIFIR

    @property
    def alacak_bakiye(self) -> Decimal:
        n = self.usd_alacak - self.usd_borc
        return n if n > 0 else SIFIR


@dataclass
class MizanUSD:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list
    haric_tl: Decimal     # kur_usd'si girilmemiş fişlerin USD'ye giremeyen tutarı (TL)

    @property
    def toplam_borc(self) -> Decimal:
        return sum((s.usd_borc for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak(self) -> Decimal:
        return sum((s.usd_alacak for s in self.satirlar), SIFIR)

    @property
    def toplam_borc_bakiye(self) -> Decimal:
        return sum((s.borc_bakiye for s in self.satirlar), SIFIR)

    @property
    def toplam_alacak_bakiye(self) -> Decimal:
        return sum((s.alacak_bakiye for s in self.satirlar), SIFIR)

    @property
    def hareket_denk(self) -> bool:
        return self.toplam_borc == self.toplam_alacak


def mizan_usd(baslangic=None, bitis=None) -> MizanUSD:
    """USD mizan (tarihsel/donmuş). Her satır KENDİ fişinin kuruyla USD'ye çevrilir:

    - TÜM satırlar: USD = satırın TL tutarı ÷ fiş.kur_usd (USD işlem satırı dahil).
    - kur_usd boş fiş (eski/legacy) -> USD'ye giremez; tutarı haric_tl'ye eklenir.
      (Yeni fişlerde kur zorunlu olduğundan bu durum yalnız eski veride olur.)

    Rapor günü kuru sonucu ETKİLEMEZ (donmuş değerlerin toplamı).
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    acc: dict[str, dict] = {}       # ANA hesap koduna göre (roll-up)
    haric = SIFIR

    for ln in _satirlar(baslangic, bitis):
        kur = ln.fis.kur_usd
        if not kur or kur <= 0:
            haric += ln.borc
            continue
        ana = _ana_kod(ln.hesap_id)
        d = acc.get(ana)
        if d is None:
            d = acc[ana] = dict(ub=SIFIR, ua=SIFIR)
        # TÜM satırlar: USD = satırın TL tutarı ÷ fiş.kur_usd (USD işlem satırı dahil).
        d["ub"] += ln.borc / kur
        d["ua"] += ln.alacak / kur

    metalar = {x.hesap_kodu: x for x in
               HesapPlani.objects.filter(hesap_kodu__in=list(acc))}
    satirlar = [
        MizanUSDSatir(kod, (metalar[kod].hesap_adi if kod in metalar else kod),
                      (metalar[kod].rapor_grubu if kod in metalar else ""),
                      (metalar[kod].rapor_kalemi if kod in metalar else ""),
                      v["ub"], v["ua"])
        for kod, v in sorted(acc.items())
    ]
    return MizanUSD(baslangic, bitis, satirlar, haric)


# ---------------------------------------------------------------------------
# USD BİLANÇO — USD mizandan, TL ile AYNI gruplama (revalüasyon/çevrim farkı YOK)
# ---------------------------------------------------------------------------
@dataclass
class BilancoUSD:
    baslangic: datetime.date
    bitis: datetime.date
    aktif: list
    pasif: list
    donem_sonucu: Decimal
    haric_tl: Decimal

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
    """USD bilanço — USD mizandan türetilir; TL bilançoyla AYNI mantık, tutarlar USD."""
    m = mizan_usd(baslangic, bitis)
    satirlar = [
        (s.hesap_kodu, s.hesap_adi, s.grup, s.kalem, s.usd_borc - s.usd_alacak)
        for s in m.satirlar
    ]
    aktif, pasif, donem = _bilanco_kur(satirlar)
    return BilancoUSD(m.baslangic, m.bitis, aktif, pasif, donem, m.haric_tl)


# ---------------------------------------------------------------------------
# USD GELİR TABLOSU — USD mizandan, spec bölüm 4 haritası (yalnızca 6'lı)
# ---------------------------------------------------------------------------
@dataclass
class GelirTablosuUSD:
    baslangic: datetime.date
    bitis: datetime.date
    satirlar: list
    donem_net_kari: Decimal
    haric_tl: Decimal

    def deger(self, etiket_baslangic: str):
        for s in self.satirlar:
            if s.etiket.startswith(etiket_baslangic):
                return s.tutar
        return None


def gelir_tablosu_usd(baslangic=None, bitis=None) -> GelirTablosuUSD:
    """USD gelir tablosu — USD mizandan türetilir (donmuş USD'ler). Yalnızca 6'lı."""
    m = mizan_usd(baslangic, bitis)
    by = {s.hesap_kodu: s for s in m.satirlar}

    def alacak_net(kodlar):
        return sum((by[k].usd_alacak - by[k].usd_borc for k in kodlar if k in by), SIFIR)

    def borc_net(kodlar):
        return sum((by[k].usd_borc - by[k].usd_alacak for k in kodlar if k in by), SIFIR)

    rows, net = _gelir_rows(alacak_net, borc_net)
    return GelirTablosuUSD(m.baslangic, m.bitis, rows, net, m.haric_tl)
