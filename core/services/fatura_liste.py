"""FATURALAR › Alış / Satış LİSTE ekranı verisi (2026-10-15) — SALT-OKUNUR; kayıt mantığına (fiş, stok, cari) dokunmaz.

Sorgu sayısı fatura sayısından BAĞIMSIZDIR (test ile kilitli): faturalar + satırlar (KDV/tevkifat ile) + PDF ekleri toplu çekilir; ödeme durumu ``fatura_odeme.odeme_durumlari`` (tek FIFO
çekirdeği) ile carilerin tüm faturaları için 2 sorguda hesaplanır. Süzme/sıralama/toplam PYTHON'da (satır başına hesaplanan değerler: toplam, ödeme durumu) yapılır — liste yüz-binlerce
satıra çıkarsa DB'ye taşınmalı; bugünkü ölçek (yüzler/binler) için bilinçli sade tercih.

Tanımlar:
- Matrah = satır tutarları toplamı; KDV = satır KDV toplamı (fatura para biriminde). TL karşılığı = fatura kuru ile (TASLAK'ta elle girilen kur; yoksa belirsiz → TL boş).
- Toplam = Matrah + KDV (KDV dahil brüt). "Yalnız KDV" tipinde matrah fişe yazılmadığı için Matrah "—" ve Toplam yalnız KDV tutarıdır.
- Ödeme durumu: ``fatura_odeme`` (FIFO, hesaplanan). Taslak / iade (ters yönlü) faturada yok.
- Hızlı filtreler: açık (açık+kısmi) · vadesi geçen · bu hafta vadesi dolan (bugünden bu haftanın pazarına kadar, açık) · PDF'i eksik (ONAYLI ve PDF eki olmayan) · taslak."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Prefetch, Q

from core.metin import buyuk_harf_tr
from core.models import Cari, Fatura, FaturaEk, FaturaSatir, FaturaTipi
from core.sayi import yuvarla
from core.services import cari as cari_servis
from core.services import fatura_odeme
from core.services.cari_ozet import tr_anahtar
from core.tarih import tr_bugun

SIFIR = Decimal("0")
SAYFA_BOYUTLARI = (50, 100, 250)
HIZLI_FILTRELER = (("hepsi", "Tümü"), ("acik", "Açık"), ("gecen", "Vadesi geçen"), ("hafta", "Bu hafta vadesi dolan"), ("pdf_eksik", "PDF'i eksik"), ("taslak", "Taslak"))
HIZLI_ANAHTARLAR = tuple(k for k, _ in HIZLI_FILTRELER)
SIRALAMA_ANAHTARLARI = ("tarih", "no", "cari", "toplam", "vade")


def tip_rozeti(tip, yon) -> tuple:
    """(kısa etiket, css anahtarı): Alış / Satış / Gider / KDV / İade / İhraç / SMM — tip bayraklarından (yalnız KDV, stopajlı, maliyet fişi, ad)."""
    if tip is None:
        return "Tip yok", "yok"
    if tip.yalniz_kdv:
        return "KDV", "kdv"
    if tip.stopajli:
        return "SMM", "smm"
    if tip.maliyet_fisi in (FaturaTipi.MaliyetFisi.SATIS_IADE, FaturaTipi.MaliyetFisi.ALIS_IADE):
        return "İade", "iade"
    ad = buyuk_harf_tr(tip.ad or "")
    if "İHRAÇ" in ad or "İHRACAT" in ad:
        return "İhraç", "ihrac"
    if tip.gider:
        return "Gider", "gider"
    return ("Alış" if yon == FaturaTipi.Yon.ALIS else "Satış"), "normal"


def _satir_ozeti(f) -> dict:
    """Prefetch edilmiş satırlardan (silinmemiş) matrah / KDV / karma bilgisi — satır başına ek sorgu YOK."""
    satirlar = [s for s in f.satirlar.all() if not s.silindi]
    matrah = sum((s.tutar for s in satirlar), SIFIR)
    kdv = sum((s.kdv_tutari for s in satirlar), SIFIR)
    stok = any(s.stok_id and not s.hesap_id for s in satirlar)
    hesap = any(s.hesap_id for s in satirlar)
    adlar = []
    for x in satirlar:
        if x.satir_tipi_ad not in adlar:
            adlar.append(x.satir_tipi_ad)
    return {"matrah": matrah, "kdv": kdv, "karma": stok and hesap, "satir_adlari": adlar}


def _kur(f):
    """TL karşılığı için kur: ONAYLI → fatura kuru; TASLAK → elle girilen kur (yoksa TRY için 1, döviz için belirsiz=None)."""
    if f.durum == Fatura.Durum.ONAYLI:
        return f.kur
    if f.taslak_kur:
        return f.taslak_kur
    return Decimal("1") if f.para_birimi == "TRY" else None


def _satir_olustur(f, bugun, odeme) -> dict:
    o = _satir_ozeti(f)
    tip = f.tip
    yalniz_kdv = bool(tip and tip.yalniz_kdv)
    kur = _kur(f)
    matrah = None if yalniz_kdv else o["matrah"]
    toplam = o["kdv"] if yalniz_kdv else o["matrah"] + o["kdv"]
    tl = (lambda x: None if (kur is None or x is None) else yuvarla(x * kur, 2))
    kdv_tl = tl(o["kdv"])
    matrah_tl = None if yalniz_kdv else tl(o["matrah"])
    toplam_tl = None if kur is None else ((kdv_tl or SIFIR) + (matrah_tl or SIFIR))
    vade = f.vade_tarihi or cari_servis.vade_hesapla(f.cari, f.tarih)
    od = odeme.get(f.pk)
    if od is not None:
        vade = od["vade"]
    pdf = next(iter(getattr(f, "pdf_ekler", ())), None)
    etiket, css = tip_rozeti(tip, f.yon)
    gider = bool(tip and tip.gider)
    if o["karma"]:
        satir_alt = "Stok+Hesap"                                               # karma alış faturası
    elif "Demirbaş" in o["satir_adlari"] or ("Hesap" in o["satir_adlari"] and not gider):
        satir_alt = "Satır: " + " · ".join(o["satir_adlari"])                  # gider dışı faturada hesap/demirbaş satırı (ör. satışta demirbaş satışı)
    else:
        satir_alt = ""
    return {
        "fatura": f, "pk": f.pk, "tarih": f.tarih, "fatura_no": f.fatura_no, "cari": f.cari, "cari_kisa": f.cari.kisa_ad or f.cari.unvan, "cari_unvan": f.cari.unvan,
        "tip_ad": tip.ad if tip else "", "tip_etiket": etiket, "tip_css": css, "yalniz_kdv": yalniz_kdv, "karma": o["karma"], "satir_alt": satir_alt, "sahsi": bool(f.sahsi_alis),
        "pb": f.para_birimi, "kur": kur, "taslak": f.durum == Fatura.Durum.TASLAK, "durum_adi": f.get_durum_display(),
        "matrah": matrah, "kdv": o["kdv"], "toplam": toplam, "matrah_tl": matrah_tl, "kdv_tl": kdv_tl, "toplam_tl": toplam_tl,
        "vade": vade, "odeme": od, "pdf_ek_pk": pdf.pk if pdf else None,
    }


def _hafta_sonu(bugun):
    return bugun + datetime.timedelta(days=6 - bugun.weekday())                 # pazar


def _hizli_uyar(s, hizli, bugun) -> bool:
    od = s["odeme"]
    acik = od is not None and od["durum"] in (fatura_odeme.ACIK, fatura_odeme.KISMI)
    if hizli == "acik":
        return acik
    if hizli == "gecen":
        return acik and od["gecikme"] > 0
    if hizli == "hafta":
        return acik and s["vade"] is not None and bugun <= s["vade"] <= _hafta_sonu(bugun)
    if hizli == "pdf_eksik":
        return not s["taslak"] and s["pdf_ek_pk"] is None
    if hizli == "taslak":
        return s["taslak"]
    return True


def _siralama_anahtari(sirala):
    if sirala == "no":
        return lambda s: (tr_anahtar(s["fatura_no"]), s["pk"])
    if sirala == "cari":
        return lambda s: (tr_anahtar(s["cari_kisa"]), s["pk"])
    if sirala == "toplam":
        return lambda s: (s["toplam_tl"] if s["toplam_tl"] is not None else Decimal("-1E+18"), s["pk"])
    return lambda s: (s["tarih"], s["pk"])


def siralama(satirlar, sirala, yon):
    """Sıralar; "vade"de vadesiz faturalar her iki yönde de SONDA. Varsayılan Tarih azalan."""
    if sirala == "vade":
        var = sorted((s for s in satirlar if s["vade"] is not None), key=lambda s: (s["vade"], s["pk"]), reverse=(yon == "desc"))
        return var + [s for s in satirlar if s["vade"] is None]
    return sorted(satirlar, key=_siralama_anahtari(sirala), reverse=(yon == "desc"))


def toplamlar(satirlar) -> dict:
    """Filtrelenmiş listenin toplamı: TL (matrah, KDV, toplam), para birimi bazında döviz alt toplamları, açık bakiye (PB başına; döviz çevrilmez)."""
    tl = {"matrah": SIFIR, "kdv": SIFIR, "toplam": SIFIR}
    doviz: dict = {}
    acik: dict = {}
    belirsiz = 0
    for s in satirlar:
        if s["toplam_tl"] is None:
            belirsiz += 1
        else:
            tl["matrah"] += s["matrah_tl"] or SIFIR
            tl["kdv"] += s["kdv_tl"] or SIFIR
            tl["toplam"] += s["toplam_tl"]
        if s["pb"] != "TRY":
            d = doviz.setdefault(s["pb"], {"matrah": SIFIR, "kdv": SIFIR, "toplam": SIFIR})
            d["matrah"] += s["matrah"] or SIFIR
            d["kdv"] += s["kdv"]
            d["toplam"] += s["toplam"]
        od = s["odeme"]
        if od is not None and od["durum"] in (fatura_odeme.ACIK, fatura_odeme.KISMI):
            acik[od["pb"]] = acik.get(od["pb"], SIFIR) + od["kalan"]
    return {"tl": tl, "doviz": sorted(doviz.items()), "acik": sorted(acik.items(), key=lambda x: (x[0] != "TRY", x[0])), "belirsiz_kur": belirsiz}


def temel_sorgu(yon, *, ara="", durum="hepsi", tip_id="", bas=None, bit=None, cari_id="", pb=""):
    qs = (Fatura.objects.filter(silindi=False, yon=yon).select_related("tip", "cari")
          .prefetch_related(Prefetch("satirlar", queryset=_satir_qs()),
                            Prefetch("ekler", queryset=FaturaEk.objects.filter(silindi=False, dosya__iendswith=".pdf").order_by("id"), to_attr="pdf_ekler")))
    if durum in (Fatura.Durum.TASLAK, Fatura.Durum.ONAYLI):
        qs = qs.filter(durum=durum)
    if str(tip_id).isdigit():
        qs = qs.filter(tip_id=tip_id)
    if bas:
        qs = qs.filter(tarih__gte=bas)
    if bit:
        qs = qs.filter(tarih__lte=bit)
    if str(cari_id).isdigit():
        qs = qs.filter(cari_id=cari_id)
    if pb:
        qs = qs.filter(para_birimi=pb)
    if ara:
        b = buyuk_harf_tr(ara)
        qs = qs.filter(Q(cari__unvan__contains=b) | Q(cari__kisa_ad__contains=b) | Q(fatura_no__icontains=ara))
    return qs


def _satir_qs():
    return FaturaSatir.objects.filter(silindi=False).select_related("kdv")


def liste_getir(yon, *, hizli="hepsi", sirala="tarih", yon_siralama="desc", bugun=None, **filtre) -> dict:
    """{"satirlar": [...süzülmüş + sıralı], "chip_sayilari": {anahtar: adet}, "toplamlar": {...}, "genel_adet": n} — chip sayıları chip DIŞINDAki filtreler uygulanmış kümeden."""
    bugun = bugun or tr_bugun()
    faturalar = list(temel_sorgu(yon, **filtre))
    odeme = fatura_odeme.odeme_durumlari({f.cari_id: f.cari for f in faturalar}.values(), bugun)
    tum = [_satir_olustur(f, bugun, odeme) for f in faturalar]
    chip_sayilari = {k: sum(1 for s in tum if _hizli_uyar(s, k, bugun)) for k in HIZLI_ANAHTARLAR}
    secilen = [s for s in tum if _hizli_uyar(s, hizli, bugun)]
    secilen = siralama(secilen, sirala, yon_siralama)
    return {"satirlar": secilen, "chip_sayilari": chip_sayilari, "toplamlar": toplamlar(secilen), "genel_adet": len(tum)}


def filtre_secenekleri(yon) -> dict:
    """Cari ve para birimi süzgeç seçenekleri: yalnız bu yönde faturası olanlar."""
    qs = Fatura.objects.filter(silindi=False, yon=yon)
    cariler = sorted(Cari.objects.filter(pk__in=qs.values("cari_id")), key=lambda c: tr_anahtar(c.kisa_ad or c.unvan))
    pbler = sorted(set(qs.values_list("para_birimi", flat=True)), key=lambda p: (p != "TRY", p))
    return {"cariler": cariler, "pbler": pbler}
