"""ÜRÜN SERİSİ DEFTERİ — bitmiş ürün (mamul) serileri TEK yerde tanımlıdır; Operasyon Tanımları (etiket, filtre, dağılım) ve Ürün Ağacı (ürün seçici
grupları, Karşılaştır serisi, Nerede kullanılıyor) buradan okur. YENİ SERİ eklemek = ``SERILER``'e bir satır (kod, etiket, Ürün Ağacı adı, seçici adı, kök
öneki); başka hiçbir yerde seri sabiti yoktur.

Kök öneki: bitmiş ürünün stok kodu önekidir (152-10-* A tipi, 152-22-* çift çıkış, 152-23-* CS). Bir operasyon tanımının serisi, çıktısından zincir YUKARI
izlenip ulaşılan bitmiş ürünlerin serisidir: tek seri → o seri, iki ya da daha fazla seri → ORTAK, serisi tanımsız başka 152-* ürün → DIGER, hiç bitmiş ürün
yok → BAGLANTISIZ."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Seri:
    kod: str            # kısa anahtar (URL ?seri=, filtre, dağılım)
    etiket: str         # Operasyon Tanımları rozeti / filtresi
    urun_ad: str        # Ürün Ağacı grup adı ("Bitmiş ürünler – …", Nerede kullanılıyor başlığı)
    secici_ad: str      # Karşılaştır seri seçici metni
    onek: str           # bitmiş ürün stok kodu öneki


SERILER = (
    Seri("A", "A tipi", "A tipi", "A tipi (152-10)", "152-10-"),
    Seri("C", "C tipi", "Çift çıkış", "Çift çıkış (152-22)", "152-22-"),
    Seri("CS", "CS tipi", "CS tipi", "CS tipi – çelik ayaklı (152-23)", "152-23-"),
)

ORTAK, DIGER, BAGLANTISIZ = "ORTAK", "DIGER", "BAGLANTISIZ"
KODLAR = tuple(s.kod for s in SERILER)
ONEK = {s.kod: s.onek for s in SERILER}                                       # seri kodu → kök öneki
URUN_AD = {**{s.kod: s.urun_ad for s in SERILER}, DIGER: "Diğer"}             # Ürün Ağacı grup adları
URUN_SIRA = KODLAR + (DIGER,)                                                 # Ürün Ağacı grup sırası
LISTE_ETIKET = {**{s.kod: s.etiket for s in SERILER}, ORTAK: "Ortak", DIGER: "Diğer", BAGLANTISIZ: "Bağlantısız"}   # Operasyon Tanımları (dağılım sırası)
FILTRE_KODLARI = KODLAR + (ORTAK, BAGLANTISIZ)                                # ?seri= değerleri


def seri_of_kod(kod: str) -> str:
    """Bitmiş ürün stok kodunun serisi (öneki tanımlı değilse DIGER)."""
    for s in SERILER:
        if (kod or "").startswith(s.onek):
            return s.kod
    return DIGER


def filtre_secenekleri() -> list:
    """Operasyon Tanımları 'seri' filtresi: [("", "Tüm seriler"), ("A", "A tipi"), …, ("ORTAK", "Ortak"), ("BAGLANTISIZ", "Bağlantısız")]."""
    return [("", "Tüm seriler")] + [(k, LISTE_ETIKET[k]) for k in FILTRE_KODLARI]


def karsilastir_secenekleri() -> list:
    """Karşılaştır 'seri' seçici: [(kod, metin), …] (elle seçim seçeneği şablonda)."""
    return [(s.kod, s.secici_ad) for s in SERILER]


def sinifla(bitmis_urun_kodlari) -> str:
    """Zincirin ulaştığı bitmiş ürün (152-*) kodlarından operasyon serisi: tek seri → kodu; ≥2 seri → ORTAK; tanımsız 152-* → DIGER; yok → BAGLANTISIZ.
    Tanımlı bir seriyle birlikte tanımsız bir 152-* ürüne de ulaşılıyorsa tanımlı seri esas alınır (eskiden olduğu gibi)."""
    kodlar = list(bitmis_urun_kodlari)
    seriler = {seri_of_kod(k) for k in kodlar} - {DIGER}
    if len(seriler) >= 2:
        return ORTAK
    if seriler:
        return next(iter(seriler))
    return DIGER if kodlar else BAGLANTISIZ
