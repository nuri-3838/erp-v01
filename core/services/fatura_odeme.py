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


# ------------------------------------------------------------------ FATURA DETAYI: bu faturayı kapatan hareketler
KAYNAK_ADLARI = {"BANKA": "Banka", "KREDI_KARTI": "Kredi kartı", "CARI_VIRMAN": "Cari virman", "CEK_SENET": "Çek / senet", "KASA": "Kasa",
                 "CARI_KESINTI": "Cari kesinti / masraf", "MANUEL": "Mahsup (manuel fiş)", "FATURA": "Karşı fatura (mahsup / iade)", "KREDI": "Kredi"}


def _kaynak_baglantisi(s):
    """Hareketin kaynak belgesine (banka hesabı / kredi kartı / çek bordrosu / kasa) giden (rota adı, pk) ya da None."""
    kaynak = s["fis__kaynak"]
    if kaynak == "BANKA" and s["fis__banka_hesap_id"]:
        return "core:banka_hesap_detay", s["fis__banka_hesap_id"]
    if kaynak == "KREDI_KARTI" and s["fis__kredi_karti_id"]:
        return "core:kredi_karti_detay", s["fis__kredi_karti_id"]
    if kaynak == "CEK_SENET" and s["fis__cek_bordrosu_id"]:
        return "core:cek_bordro_detay", s["fis__cek_bordrosu_id"]
    if kaynak == "KASA" and s["fis__kasa_id"]:
        return "core:kasa_detay", s["fis__kasa_id"]
    return None


def fatura_odemeleri(fatura, bugun=None):
    """FATURA DETAYI için: bu faturayı FIFO ile kapatan hareketler. KAYIT YAZMAZ. Rakamlar (durum, tutar, kalan, gecikme) ``odeme_durumlari`` ile BİREBİR aynıdır (aynı fonksiyon çağrılır);
    hareket dağılımı aynı sırayla (en eski ödeme en eski faturadan başlar) hesaplanır ve toplamı ``tutar - kalan`` ile tutmak ZORUNDADIR (testle kilitli).
    Taslak / fişsiz / iade (ters yönlü) fatura ya da carinin muhasebe hesabı yoksa None döner (kart gösterilmez).

    Döner: {"durum", "tutar", "odenen", "kalan", "pb", "vade", "gecikme", "hareketler": [{"tarih", "kaynak", "kaynak_ad", "aciklama", "tutar" (bu faturaya düşen), "hareket_tutari" (hareketin tamamı),
    "kismi", "tl", "fis_pk", "fis_no", "kaynak_url_adi", "kaynak_pk"}]}."""
    if fatura.silindi or fatura.durum != Fatura.Durum.ONAYLI or not fatura.fis_id:
        return None
    cari = fatura.cari
    if not cari.muhasebe_kodu:
        return None
    durum = odeme_durumlari([cari], bugun).get(fatura.pk)
    if durum is None:
        return None
    faturalar = {f.fis_id: f for f in Fatura.objects.filter(cari_id=cari.pk, silindi=False, durum=Fatura.Durum.ONAYLI, fis__isnull=False)}
    satirlar = list(YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id=cari.muhasebe_kodu)
                    .values("pk", "fis_id", "islem_pb", "borc", "alacak", "islem_tutari", "fis__tarih", "fis__kaynak", "fis__yil", "fis__fis_no", "fis__aciklama",
                            "fis__banka_hesap_id", "fis__kredi_karti_id", "fis__cek_bordrosu_id", "fis__kasa_id"))
    kalemler, _kredi = fifo_kalemler(cari, faturalar, satirlar)
    k = kalemler.get(fatura.pk)
    if k is None:
        return None
    dogal = dogal_yon(cari)
    pb = k["pb"]
    krediler = sorted(((s, s["islem_tutari"] or (s["borc"] or s["alacak"])) for s in satirlar
                       if ("borc" if s["borc"] > 0 else "alacak") != dogal and s["islem_pb"] == pb),
                      key=lambda x: (x[0]["fis__tarih"], x[0]["fis_id"], x[0]["pk"]))
    kalan_kredi = [[s, t] for s, t in krediler]
    hareketler = []
    for kalem in sorted((x for x in kalemler.values() if x["pb"] == pb), key=lambda x: (x["tarih"], x["fatura"].pk)):
        ihtiyac = kalem["tutar"]
        for kr in kalan_kredi:
            if ihtiyac <= SIFIR:
                break
            if kr[1] <= SIFIR:
                continue
            al = min(ihtiyac, kr[1])
            kr[1] -= al
            ihtiyac -= al
            if kalem["fatura"].pk == fatura.pk:
                s = kr[0]
                tam = s["islem_tutari"] or (s["borc"] or s["alacak"])
                baglanti = _kaynak_baglantisi(s)
                if baglanti is None and s["fis__kaynak"] == "FATURA" and s["fis_id"] in faturalar:
                    baglanti = ("core:fatura_detay", faturalar[s["fis_id"]].pk)                   # mahsup eden karşı faturanın kendisi
                hareketler.append({
                    "tarih": s["fis__tarih"], "kaynak": s["fis__kaynak"], "kaynak_ad": KAYNAK_ADLARI.get(s["fis__kaynak"], s["fis__kaynak"].replace("_", " ").title()),
                    "aciklama": s["fis__aciklama"], "tutar": al, "hareket_tutari": tam, "kismi": al < tam - ESIK,
                    "tl": (s["borc"] or s["alacak"]), "fis_pk": s["fis_id"], "fis_no": f"{s['fis__yil']}/{s['fis__fis_no']}",
                    "kaynak_url_adi": baglanti[0] if baglanti else None, "kaynak_pk": baglanti[1] if baglanti else None})
    return {"durum": durum["durum"], "tutar": durum["tutar"], "odenen": durum["tutar"] - durum["kalan"], "kalan": durum["kalan"], "pb": durum["pb"], "vade": durum["vade"],
            "gecikme": durum["gecikme"], "hareketler": hareketler}
