"""FASON > Mutabakat (fasoncu cari bazında): fasoncuya gönderilen ham profil (transfer + doğrudan alış), açılış, dönüşlerde tüketilen, geri alınan, fason depoda kalan (miktar + değer);
fasoncudan gelen parça adetleri; fason bedeli: faturalanmış / faturası bekleyen dönüşler ve tutarları. Salt-okunur."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Q, Sum

from core.models import FasonDonus, OperasyonKaydi, OperasyonKaydiCikti, Stok, StokHareket
from core.services import depo as depo_servis
from core.services import fason_donus as fd

SIFIR = Decimal("0")
GIRIS, CIKIS = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
TRANSFER = StokHareket.Kaynak.TRANSFER


def fasoncular():
    """Mutabakat alınabilecek fasoncular: aktif fason deposu bağlı cariler."""
    from core.models import Cari
    return Cari.objects.filter(silindi=False, fason_depolari__silindi=False).distinct().order_by("unvan")


def _topla(qs):
    return {r["stok_id"]: (r["m"] or SIFIR, r["t"] or SIFIR) for r in qs.values("stok_id").annotate(m=Sum("miktar"), t=Sum("tutar_try"))}


def mutabakat(cari, baslangic=None, bitis=None) -> dict:
    depo = depo_servis.fason_deposu(cari)
    if depo is None:
        return {"cari": cari, "depo": None}

    def tarihli(qs, alan="tarih"):
        if baslangic:
            qs = qs.filter(**{f"{alan}__gte": baslangic})
        if bitis:
            qs = qs.filter(**{f"{alan}__lte": bitis})
        return qs

    hareket = StokHareket.objects.filter(silindi=False, depo=depo)
    tuketim_q = Q(operasyon_kaydi__fason_cari=cari, operasyon_kaydi__silindi=False)       # dönüş (fason kesim) tüketimi
    # Gönderilen = fason depoya TÜM girişler: transfer (gelen) + doğrudan alış (irsaliye/fatura) + diğer girişler
    transfer = _topla(tarihli(hareket.filter(tur=GIRIS, kaynak=TRANSFER)))
    dogrudan = _topla(tarihli(hareket.filter(tur=GIRIS).exclude(kaynak=TRANSFER)))
    # Geri alınan = transferle çıkış + alış iadesi vb. diğer çıkışlar (dönüş tüketimi HARİÇ)
    geri = _topla(tarihli(hareket.filter(tur=CIKIS).exclude(tuketim_q)))
    tuketilen = _topla(tarihli(hareket.filter(tur=CIKIS).filter(tuketim_q)))

    def bakiye(qs):
        d = {}
        for r in qs.values("stok_id", "tur").annotate(m=Sum("miktar")):
            d[r["stok_id"]] = d.get(r["stok_id"], SIFIR) + (r["m"] if r["tur"] == GIRIS else -r["m"])
        return d
    # açılış = başlangıçtan ÖNCEKİ kalan (başlangıç yoksa 0); kalan = bitiş tarihi itibarıyla (bitiş yoksa fason depodaki GÜNCEL eldeki)
    acilis = bakiye(hareket.filter(tarih__lt=baslangic)) if baslangic else {}
    kalan = bakiye(hareket.filter(tarih__lte=bitis) if bitis else hareket)
    idler = set(transfer) | set(dogrudan) | set(geri) | set(tuketilen) | {k for k, v in kalan.items() if v != 0} | {k for k, v in acilis.items() if v != 0}
    stoklar = {s.pk: s for s in Stok.objects.filter(pk__in=idler).select_related("uretim_birimi")}
    profiller = []
    for pk in sorted(idler, key=lambda p: stoklar[p].kod):
        s = stoklar[pk]
        tr, dg = transfer.get(pk, (SIFIR, SIFIR))[0], dogrudan.get(pk, (SIFIR, SIFIR))[0]
        ge, t = geri.get(pk, (SIFIR, SIFIR)), tuketilen.get(pk, (SIFIR, SIFIR))
        a_, k = acilis.get(pk, SIFIR), kalan.get(pk, SIFIR)
        ort = s.ort_maliyet_try
        profiller.append({
            "stok": s, "birim": s.uretim_birimi.kisa_ad or s.uretim_birimi.ad, "acilis": a_,
            "transfer": tr, "dogrudan": dg, "gonderilen": tr + dg, "geri": ge[0], "tuketilen": t[0], "kalan": k,
            "kalan_deger": (k * ort) if ort is not None else None, "tuketilen_deger": t[1],
            "fark": a_ + tr + dg - ge[0] - t[0] - k})     # ≠ 0 ise fason depoda hesap dışı hareket var (silinmiş/elle düzeltilmiş kayıt vb.)

    # fasoncudan gelen parçalar + fason bedeli (onaylı kayıtların çıktı satırları)
    donusler = list(tarihli(FasonDonus.objects.filter(silindi=False, cari=cari)).select_related("fatura", "tahakkuk_fis").order_by("yil", "sira"))
    ciktilar = OperasyonKaydiCikti.objects.filter(
        silindi=False, kayit__silindi=False, kayit__durum=OperasyonKaydi.Durum.ONAYLI, kayit__fason_donus__in=donusler)
    parcalar = {}
    donus_tutar = {}
    for c in ciktilar.select_related("stok", "kayit"):
        p = parcalar.setdefault(c.stok_id, {"stok": c.stok, "adet": SIFIR, "beklenen": SIFIR, "fire": SIFIR, "tutar_try": SIFIR, "tutar_usd": SIFIR})
        p["adet"] += c.miktar
        p["beklenen"] += c.beklenen_miktar if c.beklenen_miktar is not None else c.miktar
        p["fire"] += (c.beklenen_miktar - c.miktar) if c.beklenen_miktar is not None else SIFIR
        p["tutar_try"] += c.fason_tutar or SIFIR
        p["tutar_usd"] += c.fason_tutar_usd or SIFIR
        d = donus_tutar.setdefault(c.kayit.fason_donus_id, [SIFIR, SIFIR])
        d[0] += c.fason_tutar or SIFIR
        d[1] += c.fason_tutar_usd or SIFIR
    donus_satirlari, toplam = [], {"try": SIFIR, "usd": SIFIR}
    faturali = {"try": SIFIR, "usd": SIFIR, "adet": 0}
    bekleyen = {"try": SIFIR, "usd": SIFIR, "adet": 0}
    tahakkuk = {"try": SIFIR, "usd": SIFIR, "adet": 0}              # faturasız fasoncu: cariye tahakkuk eden
    fatura_farki = SIFIR
    for d in donuslar_onayli(donusler):
        t_try, t_usd = donus_tutar.get(d.pk, [SIFIR, SIFIR])
        onayli_fatura = bool(d.fatura_id and not d.fatura.silindi and d.fatura.durum == "ONAYLI")
        tahakkuklu = fd.tahakkuk_var_mi(d)
        donus_satirlari.append({"donus": d, "tutar_try": t_try, "tutar_usd": t_usd, "fatura": d.fatura if d.fatura_id else None,
                                "faturali": onayli_fatura, "tahakkuklu": tahakkuklu, "tahakkuk_fis": d.tahakkuk_fis if tahakkuklu else None})
        toplam["try"] += t_try
        toplam["usd"] += t_usd
        grup = tahakkuk if tahakkuklu else (faturali if onayli_fatura else bekleyen)
        grup["try"] += t_try
        grup["usd"] += t_usd
        grup["adet"] += 1
    for f in {d.fatura for d in donusler if d.fatura_id and not d.fatura.silindi and d.fatura.durum == "ONAYLI"}:
        fatura_farki += fd.fatura_karsilastirma(f)["fark"]
    return {"cari": cari, "depo": depo, "profiller": profiller,
            "parcalar": sorted(parcalar.values(), key=lambda p: p["stok"].kod), "donusler": donus_satirlari,
            "taslak_sayisi": len(donusler) - len(donus_satirlari),
            "toplam_fire": sum((p["fire"] for p in parcalar.values()), SIFIR),
            "toplam": toplam, "faturali": faturali, "bekleyen": bekleyen, "tahakkuk": tahakkuk, "fatura_farki": fatura_farki,
            "faturasiz": bool(cari.fason_faturasiz),
            "kalan_deger": sum((p["kalan_deger"] or SIFIR for p in profiller), SIFIR)}


def donuslar_onayli(donusler):
    """Yalnız ONAYLI dönüşler (taslaklar bedel/stok üretmez)."""
    sonuc = []
    for d in donusler:
        if d.kayitlar.filter(silindi=False).exists() and not d.kayitlar.filter(silindi=False).exclude(durum=OperasyonKaydi.Durum.ONAYLI).exists():
            sonuc.append(d)
    return sonuc
