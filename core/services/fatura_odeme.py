"""Fatura ÖDEME DURUMU (2026-10-15) — hesaplanan, KAYIT YAZMAZ. Faturalar listesi, (ileride) fatura detayı ve vade raporları AYNI fonksiyonu kullanır.

Sistemde fatura-ödeme eşleştirmesi yoktur; kural (``cari_ozet`` / ``cari_rapor`` ile ortak, TEK uygulama burası): cari hesabındaki ödeme / tahsilat / mahsup / fatura iadesi
(faturadan olmayan ve cari hesabın doğal yönünün TERSİ satırlar) AYNI PARA BİRİMİ havuzunda en ESKİ faturadan başlayarak düşülür (FIFO) — en eski fatura önce kapanır, cari
bakiyesi kadar en YENİ faturalar açık kalır. Cari hesabın doğal yönü kök koduna göredir (120/121/136 → borç, diğerleri → alacak). Fatura tutarı = faturanın CARİ HESABINA yazılan tutar
(tevkifat/stopaj sonrası, ekstreyle aynı; "yalnız KDV" tipinde net KDV).

Durum: ``KAPANDI`` (kalan = 0) · ``KISMI`` (0 < kalan < tutar) · ``ACIK`` (hiç düşülmemiş). Vadesi geçmiş (gecikme > 0) açık/kısmi faturada ``gecikme`` gün sayısı dolu. TASLAK faturada, fişsiz faturada, carinin
muhasebe hesabı olmayan faturada ve cari hesabın doğal yönünün tersi yazan faturada (iade) durum YOK (sözlükte yer almaz)."""
from __future__ import annotations

from decimal import Decimal

from core.models import Fatura, YevmiyeSatir
from core.services import cari as cari_servis
from core.tarih import tr_bugun

SIFIR = Decimal("0")
ESIK = Decimal("0.005")
ALACAKLI_DOGAL_KOKLER = ("120", "121", "136")          # borç yönü doğal (cari bize borçlanır): müşteri / diğer alacaklar

KAPANDI, KISMI, ACIK = "KAPANDI", "KISMI", "ACIK"
DURUM_ADLARI = {KAPANDI: "Kapandı", KISMI: "Kısmi", ACIK: "Açık"}


def dogal_yon(cari) -> str:
    kok = (cari.muhasebe_kodu or "").split(".")[0]
    return "borc" if kok in ALACAKLI_DOGAL_KOKLER else "alacak"


def fifo_kalemler(cari, faturalar: dict, satirlar):
    """Çekirdek FIFO. ``faturalar``: {fiş pk: Fatura} (carinin ONAYLI, fişli faturaları); ``satirlar``: carinin hesabındaki yevmiye satırları
    (``values("fis_id", "islem_pb", "borc", "alacak", "islem_tutari")``). Döner: ({fatura pk: {fatura, pb, tutar, tarih, vade, kalan}}, {pb: artan kredi (avans/fazla ödeme)})."""
    dogal = dogal_yon(cari)
    kalemler, kredi = {}, {}
    for s in satirlar:
        yon = "borc" if s["borc"] > 0 else "alacak"
        pb, tutar = s["islem_pb"], s["islem_tutari"] or (s["borc"] or s["alacak"])
        f = faturalar.get(s["fis_id"])
        if f is not None and yon == dogal:
            k = kalemler.setdefault(f.pk, {"fatura": f, "pb": pb, "tutar": SIFIR})
            k["tutar"] += tutar
        elif yon != dogal:
            kredi[pb] = kredi.get(pb, SIFIR) + tutar                  # ödeme / tahsilat / mahsup / fatura iadesi
    for k in kalemler.values():
        f = k["fatura"]
        k["tarih"] = f.tarih
        k["vade"] = f.vade_tarihi or cari_servis.vade_hesapla(cari, f.tarih)
        k["kalan"] = k["tutar"]
    for pb in {k["pb"] for k in kalemler.values()}:
        havuz = kredi.get(pb, SIFIR)
        for k in sorted((x for x in kalemler.values() if x["pb"] == pb), key=lambda x: (x["tarih"], x["fatura"].pk)):
            dus = min(k["kalan"], havuz)
            k["kalan"] -= dus
            havuz -= dus
        kredi[pb] = havuz
    return kalemler, kredi


def odeme_durumlari(cariler, bugun=None) -> dict:
    """{fatura pk: {"durum", "tutar", "kalan", "pb", "vade", "gecikme"}} — verilen carilerin TÜM onaylı faturaları için, SABİT sorgu sayısıyla (1 fatura + 1 yevmiye satırı sorgusu).
    ``cariler`` Cari nesneleri (ya da yalnızca ``muhasebe_kodu``/``pk`` taşıyan Cari'ler); aynı carinin faturaları birlikte hesaplanır (FIFO tüm faturalara bakar)."""
    bugun = bugun or tr_bugun()
    cariler = {c.pk: c for c in cariler if c.muhasebe_kodu}
    if not cariler:
        return {}
    faturalar_cari: dict = {}
    for f in Fatura.objects.filter(cari_id__in=list(cariler), silindi=False, durum=Fatura.Durum.ONAYLI, fis__isnull=False):
        faturalar_cari.setdefault(f.cari_id, {})[f.fis_id] = f
    if not faturalar_cari:
        return {}
    kodlar = {cariler[pk].muhasebe_kodu for pk in faturalar_cari}
    hesap_satirlari: dict = {}
    for s in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=kodlar)
              .values("hesap_id", "fis_id", "islem_pb", "borc", "alacak", "islem_tutari")):
        hesap_satirlari.setdefault(s["hesap_id"], []).append(s)
    sonuc = {}
    for cari_pk, faturalar in faturalar_cari.items():
        cari = cariler[cari_pk]
        kalemler, _kredi = fifo_kalemler(cari, faturalar, hesap_satirlari.get(cari.muhasebe_kodu, ()))
        for pk, k in kalemler.items():
            kalan = k["kalan"] if k["kalan"] >= ESIK else SIFIR
            durum = KAPANDI if kalan == SIFIR else (KISMI if kalan < k["tutar"] - ESIK else ACIK)
            gecikme = (bugun - k["vade"]).days if (kalan > SIFIR and k["vade"] and bugun > k["vade"]) else 0
            sonuc[pk] = {"durum": durum, "tutar": k["tutar"], "kalan": kalan, "pb": k["pb"], "vade": k["vade"], "gecikme": gecikme}
    return sonuc
