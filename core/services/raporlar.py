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

from django.db.models import Q, Sum
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
    hesap_kodu: str
    hesap_adi: str
    borc: Decimal
    alacak: Decimal
    yur_bakiye: Decimal  # kümülatif borç − alacak; + = borç bakiye, − = alacak bakiye
    pb: str
    dvz_borc: Decimal
    dvz_alacak: Decimal
    dvz_yur_bakiye: Decimal


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

    @property
    def cok_hesap(self) -> bool:
        """Birden çok (alt) hesabın satırı var mı (üst hesap roll-up)?"""
        return len({s.hesap_kodu for s in self.satirlar}) > 1

    @property
    def dvz_toplamlar(self) -> dict:
        """İşlem PB başına döviz toplamı: {pb: {borc, alacak, bakiye}}."""
        d = {}
        for s in self.satirlar:
            t = d.setdefault(s.pb, {"borc": SIFIR, "alacak": SIFIR, "bakiye": SIFIR})
            t["borc"] += s.dvz_borc
            t["alacak"] += s.dvz_alacak
        for t in d.values():
            t["bakiye"] = t["borc"] - t["alacak"]
        return d


def ekstre(hesap_kodu: str, baslangic=None, bitis=None) -> Ekstre:
    """Belirtilen hesabın tarih aralığındaki hareket ekstresi, yürüyen bakiyeli.

    Yalnızca aktif (iptal edilmemiş) fişlerin satırları; fiş tarih + fis_no + satır id
    sırasında. Toplam borç/alacak/bakiye, mizandaki aynı hesabın değerleriyle birebir tutar.
    """
    baslangic, bitis = _varsayilan(baslangic, bitis)
    qs = (
        YevmiyeSatir.objects.filter(
            Q(hesap_id=hesap_kodu) | Q(hesap__hesap_kodu__startswith=hesap_kodu + "."),
            silindi=False, fis__silindi=False,
            fis__tarih__gte=baslangic, fis__tarih__lte=bitis,
        )
        .select_related("fis", "hesap")
        .order_by("fis__tarih", "fis__fis_no", "id")
    )
    satirlar = []
    kumulatif = SIFIR
    kum_dvz = {}
    for ln in qs:
        kumulatif += ln.borc - ln.alacak
        dvz_b = ln.islem_tutari if ln.borc else SIFIR
        dvz_a = ln.islem_tutari if ln.alacak else SIFIR
        kum_dvz[ln.islem_pb] = kum_dvz.get(ln.islem_pb, SIFIR) + (dvz_b - dvz_a)
        satirlar.append(EkstreSatir(
            tarih=ln.fis.tarih,
            fis_pk=ln.fis.pk,
            fis_yil=ln.fis.yil,
            fis_no=ln.fis.fis_no,
            fis_aciklama=ln.fis.aciklama,
            satir_aciklama=ln.aciklama,
            hesap_kodu=ln.hesap_id,
            hesap_adi=ln.hesap.hesap_adi,
            borc=ln.borc,
            alacak=ln.alacak,
            yur_bakiye=kumulatif,
            pb=ln.islem_pb,
            dvz_borc=dvz_b,
            dvz_alacak=dvz_a,
            dvz_yur_bakiye=kum_dvz[ln.islem_pb],
        ))
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


def _donem_sonucu(rolled_har) -> Decimal:
    """Dönem net kârı TEK kaynağı: TÜM sonuç hesapları (BILANCO olmayan) net'i
    = Σ(alacak − borç). Bilanço dönem plug'ı ve gelir tablosu net kârı bundan türer
    → iki rapor her zaman birebir tutar (7xx ve yeni eklenen hesaplar dahil)."""
    return sum((o["alacak"] - o["borc"] for o in rolled_har if o["grup"] != "BILANCO"),
               SIFIR)


def _gelir_rows(alacak_net, borc_net, donem_sonucu):
    """Standart gelir tablosu satırları — bölümler hesap planı rapor_kalemi (A..J)
    harfinden VERİ-GÜDÜMLÜ gelir (sabit kod listesi YOK; yeni GELIR hesabı doğru
    kalemle açılınca otomatik düşer). DÖNEM NET KÂRI = donem_sonucu (= bilanço dönem
    sonucu). 6'lı standart satır toplamı ile donem_sonucu arasındaki fark (7/A
    yansıtılmamış maliyet + kalemsiz/sınıflandırılmamış hesaplar) şeffaf bir satırda
    gösterilir; böylece gelir tablosu net kârı ile bilanço dönem sonucu HER ZAMAN tutar."""
    rows: list[GelirSatir] = []
    A = alacak_net("A")
    rows.append(GelirSatir("A. Brüt Satışlar", A, isaret="+"))
    B = borc_net("B")
    rows.append(GelirSatir("B. Satış İndirimleri (-)", B, isaret="-"))
    net_satislar = A - B
    rows.append(GelirSatir("Net Satışlar", net_satislar, ara=True))
    C = borc_net("C")
    rows.append(GelirSatir("C. Satışların Maliyeti (-)", C, isaret="-"))
    brut = net_satislar - C
    rows.append(GelirSatir("BRÜT SATIŞ KÂRI", brut, ara=True))
    D = borc_net("D")
    rows.append(GelirSatir("D. Faaliyet Giderleri (-)", D, isaret="-"))
    faaliyet = brut - D
    rows.append(GelirSatir("FAALİYET KÂRI", faaliyet, ara=True))
    E = alacak_net("E")
    rows.append(GelirSatir("E. Diğer Olağan Gelir ve Kârlar", E, isaret="+"))
    F = borc_net("F")
    rows.append(GelirSatir("F. Diğer Olağan Gider ve Zararlar (-)", F, isaret="-"))
    G = borc_net("G")
    rows.append(GelirSatir("G. Finansman Giderleri (-)", G, isaret="-"))
    olagan = faaliyet + E - F - G
    rows.append(GelirSatir("OLAĞAN KÂR", olagan, ara=True))
    H = alacak_net("H")
    rows.append(GelirSatir("H. Olağandışı Gelir ve Kârlar", H, isaret="+"))
    I = borc_net("I")
    rows.append(GelirSatir("I. Olağandışı Gider ve Zararlar (-)", I, isaret="-"))
    donem_kari = olagan + H - I
    rows.append(GelirSatir("DÖNEM KÂRI", donem_kari, ara=True))
    J = borc_net("J")
    rows.append(GelirSatir("J. Dönem Kârı Vergi Karşılığı (-)", J, isaret="-"))
    net6 = donem_kari - J
    yansitilmamis = donem_sonucu - net6
    if yansitilmamis != SIFIR:
        rows.append(GelirSatir("Vergi Sonrası Kâr (6'lı hesaplar)", net6, ara=True))
        rows.append(GelirSatir(
            "Yansıtılmamış maliyet ve diğer (7/A vb.)", yansitilmamis, isaret="±"))
    rows.append(GelirSatir("DÖNEM NET KÂRI", donem_sonucu, ara=True))
    return rows, donem_sonucu


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
    """Canlı gelir tablosu (TL). Bölümler hesap planından (rapor_kalemi A..J) türetilir;
    DÖNEM NET KÂRI = bilanço dönem sonucu (tüm sonuç hesapları) → iki rapor hep tutar."""
    baslangic, bitis = _varsayilan(baslangic, bitis)
    rolled = _ana_topla(_hareketler(baslangic, bitis))
    kalem = {}
    for o in rolled:
        if o["grup"] == "GELIR_TABLOSU":
            t = kalem.setdefault(o["kalem"], {"borc": SIFIR, "alacak": SIFIR})
            t["borc"] += o["borc"]
            t["alacak"] += o["alacak"]

    def alacak_net(k):
        t = kalem.get(k)
        return (t["alacak"] - t["borc"]) if t else SIFIR

    def borc_net(k):
        t = kalem.get(k)
        return (t["borc"] - t["alacak"]) if t else SIFIR

    rows, net = _gelir_rows(alacak_net, borc_net, _donem_sonucu(rolled))
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
    """USD gelir tablosu — USD mizandan (donmuş). Bölümler rapor_kalemi'nden türetilir;
    DÖNEM NET KÂRI = USD bilanço dönem sonucu → iki USD rapor hep tutar."""
    m = mizan_usd(baslangic, bitis)
    kalem = {}
    for s in m.satirlar:
        if s.grup == "GELIR_TABLOSU":
            t = kalem.setdefault(s.kalem, {"borc": SIFIR, "alacak": SIFIR})
            t["borc"] += s.usd_borc
            t["alacak"] += s.usd_alacak
    donem = sum((s.usd_alacak - s.usd_borc for s in m.satirlar if s.grup != "BILANCO"),
                SIFIR)

    def alacak_net(k):
        t = kalem.get(k)
        return (t["alacak"] - t["borc"]) if t else SIFIR

    def borc_net(k):
        t = kalem.get(k)
        return (t["borc"] - t["alacak"]) if t else SIFIR

    rows, net = _gelir_rows(alacak_net, borc_net, donem)
    return GelirTablosuUSD(m.baslangic, m.bitis, rows, net, m.haric_tl)
