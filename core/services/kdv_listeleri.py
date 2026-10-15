"""MUHASEBE › Raporlar › KDV LİSTELERİ (2026-10-16) — SALT-OKUNUR: hiçbir kayıt yazmaz, muhasebe mantığına dokunmaz. İki liste (GİB KDV beyannamesi ekleri):
  · İNDİRİLECEK KDV LİSTESİ (alışlar) — onaylı ALIŞ faturaları (alış, gider, karma, SMM, yalnız KDV, demirbaş/duran varlık, ihraç kayıtlı, satış iadesi tipi) + faturasız 191 hareketleri;
  · SATIŞLARA AİT FATURA LİSTESİ (satışlar) — onaylı SATIŞ faturaları (normal / iade / ihraç kayıtlı ayrı) + faturasız 391 hareketleri.

Dönem = fatura (= yevmiye fişi) tarihinin yıl/ayı; "KDV'nin indirildiği dönem" bu ay. Belge tarihi = açıklamada "GERÇEK TARİH: gg.aa.yyyy" varsa o (2025 faturaları 01.01.2026'da girildi), yoksa fatura tarihi.
Tutarlar TL (döviz faturada FİŞ KURUYLA; fişle aynı kuruş yuvarlaması: KDV hesabı başına toplam × kur, matrah satır başına). KDV KDV-hariç/KDV ayrı: matrah = satır tutarları; KDV = satır KDV'leri. TEVKİFAT: alışta 191'e KDV'nin TAMAMI yazılır (indirilecek = KDV tamamı),
tevkifat kısmı 360'a alacak (sorumlu sıfatıyla beyan) → ayrı kolonda gösterilir; satışta 391'e KDV − tevkifat (net) yazılır. "Yalnız KDV" tipinde faturadaki GERÇEK matrah gösterilir (fişe yazılmasa da belge bedelidir; rozetle işaretli).
Mutabakat: liste KDV'si (faturalardan hesaplanan) + faturasız hareketler = 191 (391) dönem borç (alacak) hareketi; faturadan hesaplanan KDV ile fişteki 191/391 farklıysa fatura bazında listelenir."""
from __future__ import annotations

import calendar
import datetime
import re
from collections import defaultdict
from decimal import Decimal

from core.metin import buyuk_harf_tr
from core.models import Fatura, FaturaTipi, HesapPlani, YevmiyeSatir
from core.sayi import yuvarla
from core.services import fatura_liste
from core.services.fatura import _fatura_no_anahtar

SIFIR = Decimal("0.00")
ESIK = Decimal("0.005")
GERCEK_TARIH = re.compile(r"GER[ÇC]EK\s+TAR[İI]H\s*:\s*(\d{1,2})[./](\d{1,2})[./](\d{4})", re.IGNORECASE)
ALICI_DESENI = re.compile(r"al[ıi]c[ıi]\s+(.+?)\s*(?:·|\||,|;|$)", re.IGNORECASE)
TCKN_DESENI = re.compile(r"\(?\s*(?:TCKN|TC\s*KN|T\.?C\.?\s*NO)\s*[:\-]?\s*(\d{11})\s*\)?", re.IGNORECASE)
E_FATURA_NO = re.compile(r"^([A-Za-z0-9]{3})(\d{13})$")
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")


def donem(yil: int, ay: int):
    return datetime.date(yil, ay, 1), datetime.date(yil, ay, calendar.monthrange(yil, ay)[1])


def gercek_tarih(aciklama: str):
    m = GERCEK_TARIH.search(aciklama or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def seri_sira(no: str):
    """E-fatura biçimi (3 karakter seri + 13 hane sıra) → (seri, sıra); değilse seri boş, sıra = numaranın tamamı."""
    m = E_FATURA_NO.match((no or "").strip())
    return (m.group(1), m.group(2)) if m else ("", (no or "").strip())


def alici_adi(aciklama: str):
    """Açıklamadaki 'alıcı ADI SOYADI [· ...]' → (ad, TCKN): ad içinde '(TCKN 12345678901)' varsa ayrılır; yoksa TCKN ''."""
    m = ALICI_DESENI.search(aciklama or "")
    if not m:
        return "", ""
    ham = m.group(1).strip()
    t = TCKN_DESENI.search(ham)
    return (TCKN_DESENI.sub("", ham).strip(" -·"), t.group(1)) if t else (ham, "")


def _kur(f):
    return f.kur or Decimal("1")


def _kdv_hesaplari(prefix):
    return list(HesapPlani.objects.filter(silindi=False, hesap_kodu__startswith=prefix).values_list("hesap_kodu", flat=True))


def _satirlarini_hazirla(faturalar):
    """Fatura başına: matrah_tl, kdv_tl (indirilecek: tam KDV · satış: net), tevkifat_tl (fişteki yuvarlamayla) + cins/miktar özeti."""
    sonuc = {}
    for f in faturalar:
        satirlar = [s for s in f.satirlar.all() if not s.silindi]
        kur = _kur(f)
        alis = f.yon == "ALIS"
        matrah = sum((yuvarla(s.tutar * kur, 2) for s in satirlar), SIFIR)
        kdv_h, tev_h = defaultdict(lambda: SIFIR), defaultdict(lambda: SIFIR)
        for s in satirlar:
            kdv = s.kdv_tutari
            tev = s.tevkifat_tutari
            if s.kdv_id and s.kdv:
                hesap = s.kdv.hesap_borc_id if alis else s.kdv.hesap_alacak_id
                kdv_h[hesap] += (kdv if alis else kdv - tev)
            if tev > 0 and alis and s.tevkifat_id and s.tevkifat.hesap_id:
                tev_h[s.tevkifat.hesap_id] += tev
        kdv_tl = sum((yuvarla(v * kur, 2) for v in kdv_h.values() if v > 0), SIFIR)
        tevkifat_tl = sum((yuvarla(v * kur, 2) for v in tev_h.values()), SIFIR) if alis else sum((yuvarla(s.tevkifat_tutari * kur, 2) for s in satirlar), SIFIR)
        adlar, birimler, miktar = [], set(), SIFIR
        for s in satirlar:
            if s.stok_id:
                ad = s.stok.ad_satinalma() if alis else s.stok.ad
                birimler.add(s.stok.fatura_birimi.kisa_ad if s.stok.fatura_birimi_id else "")
                miktar += s.miktar
            elif s.demirbas_id:
                ad = s.demirbas.ad
            else:
                ad = s.hesap.hesap_adi if s.hesap_id else ""
            if ad and ad not in adlar:
                adlar.append(ad)
        cins = "; ".join(adlar[:2]) + (f" (+{len(adlar) - 2})" if len(adlar) > 2 else "")
        stoklu = any(s.stok_id for s in satirlar)
        miktar_metin = (f"{miktar.normalize():f}" + (f" {next(iter(birimler))}" if len(birimler) == 1 and next(iter(birimler)) else "")) if stoklu else ""
        sonuc[f.pk] = {"matrah_tl": matrah, "kdv_tl": kdv_tl, "tevkifat_tl": tevkifat_tl, "cins": cins, "miktar": miktar_metin, "satir_adedi": len(satirlar)}
    return sonuc


def _faturalar(yon, bas, bit):
    from django.db.models import Prefetch
    from core.models import FaturaSatir
    satirlar = FaturaSatir.objects.filter(silindi=False).select_related("kdv", "tevkifat", "stok__fatura_birimi", "hesap", "demirbas")
    return list(Fatura.objects.filter(silindi=False, durum=Fatura.Durum.ONAYLI, yon=yon, tarih__gte=bas, tarih__lte=bit, fis__isnull=False)
                .select_related("tip", "cari", "fis").prefetch_related(Prefetch("satirlar", queryset=satirlar)).order_by("tarih", "fatura_no", "pk"))


def _fis_kdv(fis_idleri, kodlar):
    """{fiş pk: (borç, alacak)} — fişlerin KDV hesaplarındaki (191.* ya da 391.*) toplamları."""
    d = defaultdict(lambda: [SIFIR, SIFIR])
    for s in YevmiyeSatir.objects.filter(silindi=False, fis_id__in=list(fis_idleri), hesap_id__in=kodlar).values("fis_id", "borc", "alacak"):
        d[s["fis_id"]][0] += s["borc"]
        d[s["fis_id"]][1] += s["alacak"]
    return d


def _faturasiz(bas, bit, kodlar, fatura_fisleri):
    """KDV hesaplarında (191.* / 391.*) dönem içi, FATURA fişine ait OLMAYAN hareketler — fiş bazında."""
    d = {}
    for s in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=kodlar, fis__tarih__gte=bas, fis__tarih__lte=bit)
              .exclude(fis_id__in=list(fatura_fisleri)).select_related("fis", "hesap").order_by("fis__tarih", "fis_id", "id")):
        r = d.setdefault(s.fis_id, {"fis": s.fis, "tarih": s.fis.tarih, "kaynak": s.fis.get_kaynak_display(), "kaynak_kod": s.fis.kaynak, "aciklama": s.fis.aciklama, "borc": SIFIR, "alacak": SIFIR, "hesaplar": set()})
        r["borc"] += s.borc
        r["alacak"] += s.alacak
        r["hesaplar"].add(s.hesap_id)
    return list(d.values())


def _tum_fatura_fisleri():
    return set(Fatura.objects.filter(silindi=False, fis__isnull=False).values_list("fis_id", flat=True))


def _satir_ortak(f, hazir, sira_no):
    gercek = gercek_tarih(f.aciklama)
    belge_tarihi = gercek or f.tarih
    seri, sira = seri_sira(f.fatura_no)
    rozet = []
    if gercek:
        rozet.append(("gercek", f"Gerçek tarih kullanıldı (kayıt {f.tarih:%d.%m.%Y})", "gerçek tarih"))
    elif f.tarih.month == 1 and f.tarih.day == 1:
        rozet.append(("tarih", "01.01 girişli — gerçek tarih açıklamada yok", "01.01 girişli"))
    if not f.fatura_no:
        rozet.append(("no", "Fatura no yok" + (" (faturasız işaretli)" if f.faturasiz else ""), "no yok"))
    return {"sira": sira_no, "fatura": f, "belge_tarihi": belge_tarihi, "gercek_tarih": gercek, "tarih_isaretli": (not gercek and f.tarih.month == 1 and f.tarih.day == 1),
            "belge_no": f.fatura_no, "seri": seri, "sira_no": sira, "matrah_tl": hazir["matrah_tl"], "kdv_tl": hazir["kdv_tl"], "tevkifat_tl": hazir["tevkifat_tl"],
            "cins": hazir["cins"], "miktar": hazir["miktar"], "pb": f.para_birimi, "kur": f.kur, "donem": f"{f.tarih.month:02d}/{f.tarih.year}", "rozetler": rozet, "fis_pk": f.fis_id}


def _tip_adi(f):
    return f.tip.ad if f.tip_id else ""


def uyarilar(satirlar, *, kimlik_alani="vkn"):
    """Eksik VKN/TCKN, eksik fatura no, mükerrer belge no (aynı cari + aynı numara), gerçek tarih bilgileri."""
    eksik_vkn = [r for r in satirlar if r.get("vkn_eksik")]
    eksik_no = [r for r in satirlar if not r["belge_no"]]
    sayac = defaultdict(list)
    for r in satirlar:
        if r["belge_no"]:
            sayac[(r["fatura"].cari_id, _fatura_no_anahtar(r["belge_no"]))].append(r)
    mukerrer = [rs for rs in sayac.values() if len(rs) > 1]
    gercek = [r for r in satirlar if r["gercek_tarih"]]
    tarihsiz = [r for r in satirlar if r["tarih_isaretli"]]
    return {"eksik_vkn": eksik_vkn, "eksik_no": eksik_no, "mukerrer": mukerrer, "gercek_tarih": gercek, "tarih_isaretli": tarihsiz,
            "adet": len(eksik_vkn) + len(eksik_no) + len(mukerrer) + len(tarihsiz)}


# ---------------------------------------------------------------------------------------------------------- İNDİRİLECEK KDV
def indirilecek(yil: int, ay: int) -> dict:
    bas, bit = donem(yil, ay)
    faturalar = _faturalar("ALIS", bas, bit)
    hazir = _satirlarini_hazirla(faturalar)
    fis_kdv = _fis_kdv({f.fis_id for f in faturalar}, _kdv_hesaplari("191"))
    satirlar = []
    for n, f in enumerate(sorted(faturalar, key=lambda f: (gercek_tarih(f.aciklama) or f.tarih, f.fatura_no, f.pk)), start=1):
        h = hazir[f.pk]
        r = _satir_ortak(f, h, n)
        c = f.cari
        r.update({"satici": c.unvan, "taraf": c.unvan, "cari": c, "vkn": (c.vkn_tckn or "").strip(), "vkn_eksik": not (c.vkn_tckn or "").strip(), "tip_adi": _tip_adi(f), "indirilecek_tl": h["kdv_tl"],
                  "fis_kdv": fis_kdv[f.fis_id][0] - fis_kdv[f.fis_id][1] if f.fis_id in fis_kdv else SIFIR})
        if f.tip_id and f.tip.yalniz_kdv:
            r["rozetler"].append(("kdv", "Yalnız KDV tipi: matrah fişe yazılmadı (faturadaki matrah gösterilir)", "yalnız KDV"))
        if f.sahsi_alis:
            r["rozetler"].append(("sahsi", "Ortak adına (şahsi) alış", "şahsi alış"))
        etiket, css = fatura_liste.tip_rozeti(f.tip, f.yon)
        if css == "iade":
            r["rozetler"].append(("iade", "Satış iadesi tipi", "iade tipi"))
        if h["tevkifat_tl"] > 0:
            r["rozetler"].append(("tevkifat", f"Tevkifatlı (KDV tevkifatı {h['tevkifat_tl']:,.2f})", "tevkifatlı"))
        satirlar.append(r)
    faturasiz = _faturasiz(bas, bit, _kdv_hesaplari("191"), _tum_fatura_fisleri())
    toplam = {"matrah": sum((r["matrah_tl"] for r in satirlar), SIFIR), "kdv": sum((r["kdv_tl"] for r in satirlar), SIFIR), "tevkifat": sum((r["tevkifat_tl"] for r in satirlar), SIFIR),
              "adet": len(satirlar), "faturasiz_borc": sum((x["borc"] for x in faturasiz), SIFIR), "faturasiz_alacak": sum((x["alacak"] for x in faturasiz), SIFIR)}
    return {"yil": yil, "ay": ay, "bas": bas, "bit": bit, "satirlar": satirlar, "faturasiz": faturasiz, "toplam": toplam, "uyarilar": uyarilar(satirlar),
            "mutabakat": _mutabakat(satirlar, faturasiz, bas, bit, "191", "borc")}


# ------------------------------------------------------------------------------------------------------------- SATIŞ LİSTESİ
def satislar(yil: int, ay: int) -> dict:
    bas, bit = donem(yil, ay)
    faturalar = _faturalar("SATIS", bas, bit)
    hazir = _satirlarini_hazirla(faturalar)
    fis_kdv = _fis_kdv({f.fis_id for f in faturalar}, _kdv_hesaplari("391"))
    normal, iade, ihrac = [], [], []
    tum = []
    for n, f in enumerate(sorted(faturalar, key=lambda f: (gercek_tarih(f.aciklama) or f.tarih, f.fatura_no, f.pk)), start=1):
        h = hazir[f.pk]
        r = _satir_ortak(f, h, n)
        c = f.cari
        alici, vkn = c.unvan, (c.vkn_tckn or "").strip()
        if "TRENDYOL" in buyuk_harf_tr(c.unvan):
            kisi, tckn = alici_adi(f.aciklama)
            if kisi:
                alici, vkn = kisi, tckn                                             # e-arşiv bireysel alıcı: cari (Trendyol) yerine açıklamadaki kişi; TCKN açıklamada yoksa boş (işaretli)
                r["rozetler"].append(("alici", "Alıcı açıklamadan (e-arşiv)", "alıcı açıklamadan"))
        r.update({"alici": alici, "taraf": alici, "cari": c, "vkn": vkn, "vkn_eksik": not vkn, "tip_adi": _tip_adi(f), "hesaplanan_tl": h["kdv_tl"],
                  "fis_kdv": fis_kdv[f.fis_id][1] - fis_kdv[f.fis_id][0] if f.fis_id in fis_kdv else SIFIR})
        if h["tevkifat_tl"] > 0:
            r["rozetler"].append(("tevkifat", f"Tevkifatlı (KDV tevkifatı {h['tevkifat_tl']:,.2f}; 391'e net yazıldı)", "tevkifatlı"))
        etiket, css = fatura_liste.tip_rozeti(f.tip, f.yon)
        r["tur"] = css
        (iade if css == "iade" else ihrac if css == "ihrac" else normal).append(r)
        tum.append(r)
    faturasiz = _faturasiz(bas, bit, _kdv_hesaplari("391"), _tum_fatura_fisleri())
    t = lambda rows, k: sum((r[k] for r in rows), SIFIR)                  # noqa: E731
    toplam = {"normal_matrah": t(normal, "matrah_tl"), "normal_kdv": t(normal, "kdv_tl"), "iade_matrah": t(iade, "matrah_tl"), "iade_kdv": t(iade, "kdv_tl"),
              "ihrac_matrah": t(ihrac, "matrah_tl"), "ihrac_kdv": t(ihrac, "kdv_tl"), "adet": len(tum), "kdv": t(tum, "kdv_tl"), "matrah": t(tum, "matrah_tl"),
              "faturasiz_borc": sum((x["borc"] for x in faturasiz), SIFIR), "faturasiz_alacak": sum((x["alacak"] for x in faturasiz), SIFIR)}
    return {"yil": yil, "ay": ay, "bas": bas, "bit": bit, "normal": normal, "iade": iade, "ihrac": ihrac, "satirlar": tum, "faturasiz": faturasiz, "toplam": toplam,
            "uyarilar": uyarilar(tum), "mutabakat": _mutabakat(tum, faturasiz, bas, bit, "391", "alacak")}


# ----------------------------------------------------------------------------------------------------------------- MUTABAKAT
def _mutabakat(satirlar, faturasiz, bas, bit, kok, taraf):
    """Liste KDV toplamı (faturalardan HESAPLANAN) + faturasız hareketlerin ilgili tarafı = {kok} dönem {taraf} hareketi. Fark ≠ 0 ise fatura bazında (hesaplanan ↔ fişteki) kalem kalem."""
    kodlar = _kdv_hesaplari(kok)
    q = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=kodlar, fis__tarih__gte=bas, fis__tarih__lte=bit)
    donem_borc = sum((s["borc"] for s in q.values("borc")), SIFIR)
    donem_alacak = sum((s["alacak"] for s in q.values("alacak")), SIFIR)
    hedef = donem_borc if taraf == "borc" else donem_alacak
    liste = sum((r["kdv_tl"] for r in satirlar), SIFIR)
    fsz = sum((x[taraf] for x in faturasiz), SIFIR)
    fark = hedef - (liste + fsz)
    kalemler = []
    for r in satirlar:
        fark_f = r["fis_kdv"] - r["kdv_tl"]
        if abs(fark_f) > ESIK:
            kalemler.append({"ad": f"{r['belge_no'] or '(no yok)'} — {(r.get('satici') or r.get('alici') or '')[:36]}", "hesaplanan": r["kdv_tl"], "fis": r["fis_kdv"], "fark": fark_f, "fatura": r["fatura"]})
    karsi = donem_alacak if taraf == "borc" else donem_borc
    return {"hesap": f"{kok}.*", "taraf": "borç" if taraf == "borc" else "alacak", "liste": liste, "faturasiz": fsz, "toplam": liste + fsz, "hareket": hedef, "fark": fark,
            "karsi_taraf": karsi, "karsi_ad": "alacak" if taraf == "borc" else "borç", "kalemler": kalemler, "tamam": abs(fark) <= ESIK and not kalemler}
