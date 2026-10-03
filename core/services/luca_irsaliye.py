"""Luca GELEN e-irsaliye paketlerini (XML+PDF) ERP alış faturalarına EK olarak bağlar.

Eşleştirme (irsaliye no + satıcı VKN ile):
  1) Faturanın XML'indeki irsaliye referansı (DespatchDocumentReference / kalem düzeyi) — fatura XML'i
     önce ERP faturasına (fatura no + VKN) çözülür;
  2) bulunamazsa ERP satınalma irsaliyesindeki irsaliye no → o irsaliyenin bağlı faturası.
Birden fazla aday varsa YÜKLENMEZ. Fatura XML'indeki irsaliye no yazım hatalıysa (ör. bir hane eksik)
raporda ayrıca gösterilir; benzer-ad (düzenleme mesafesi ≤2) eşleşmeleri ASLA otomatik yüklenmez,
yalnız öneri olarak listelenir. Hiçbir fatura/stok kaydı değiştirilmez, yalnız FaturaEk eklenir."""
from __future__ import annotations

from collections import defaultdict

from django.core.files.base import ContentFile

from core.models import Fatura, FaturaEk, FaturaTipi, TeklifSiparis
from core.services import fatura_ek as ek_servis
from core.services.fatura import _fatura_no_anahtar
from core.services.luca_paket import paketleri_oku
from core.services.ubl_fatura import UblHatasi, irsaliye_oku, ubl_oku


def mesafe(a: str, b: str) -> int:
    """Levenshtein düzenleme mesafesi."""
    onceki = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        simdiki = [i]
        for j, cb in enumerate(b, 1):
            simdiki.append(min(onceki[j] + 1, simdiki[j - 1] + 1, onceki[j - 1] + (ca != cb)))
        onceki = simdiki
    return onceki[-1]


def eslestir(fatura_zipleri, irsaliye_zipleri, *, haric=(), uygula=False) -> dict:
    haric_anahtar = {_fatura_no_anahtar(x) for x in haric}
    # --- ERP alış faturaları: normalize fatura no -> [Fatura]
    indeks = defaultdict(list)
    for f in (Fatura.objects.filter(yon=FaturaTipi.Yon.ALIS, silindi=False).exclude(fatura_no="")
              .select_related("cari")):
        indeks[_fatura_no_anahtar(f.fatura_no)].append(f)

    # --- fatura XML'leri: ERP faturasına çöz, irsaliye referanslarını indeksle
    fgrup, _t, _tk = paketleri_oku(fatura_zipleri)
    ref_indeks = defaultdict(set)          # (ref_anahtar, satici_vkn) -> {fatura pk}
    ref_ham = {}                           # (ref_anahtar, vkn, fatura pk) -> ham ref (yazım dahil)
    fatura_nesne = {}
    for (no, vkn), d in fgrup.items():
        if "xml" not in d:
            continue
        try:
            x = ubl_oku(d["xml"][1])
        except UblHatasi:
            continue
        satici = x["vkn"] or vkn
        adaylar = indeks.get(_fatura_no_anahtar(x["fatura_no"]), [])
        tam = [f for f in adaylar if f.cari.vkn_tckn and f.cari.vkn_tckn == satici]
        f = tam[0] if len(tam) == 1 else (adaylar[0] if not tam and len(adaylar) == 1 else None)
        if f is None:
            continue
        fatura_nesne[f.pk] = f
        for ref in x["irsaliye_refleri"]:
            k = _fatura_no_anahtar(ref)
            ref_indeks[(k, satici)].add(f.pk)
            ref_ham[(k, satici, f.pk)] = ref

    # --- ERP satınalma irsaliyeleri: normalize irsaliye no -> [TeklifSiparis]
    erp_ir = defaultdict(list)
    for t in (TeklifSiparis.objects.filter(
            belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, yon=TeklifSiparis.Yon.ALIS, silindi=False)
            .exclude(irsaliye_no="").select_related("cari", "fatura")):
        erp_ir[_fatura_no_anahtar(t.irsaliye_no)].append(t)

    # --- irsaliye paketleri
    igrup, tanimayan, tekrar = paketleri_oku(irsaliye_zipleri)
    eslesen, eslesmeyen, cok_adayli, okunamadi, haric_liste = [], [], [], [], []
    for (no, vkn), d in sorted(igrup.items()):
        ad0 = next(iter(d.values()))[0]
        xml = None
        if "xml" in d:
            try:
                xml = irsaliye_oku(d["xml"][1])
            except UblHatasi as e:
                okunamadi.append((d["xml"][0], str(e)))
                continue
        gosterim_no = xml["irsaliye_no"] if xml else no
        if no in haric_anahtar:
            haric_liste.append((gosterim_no, vkn, xml, ad0))
            continue
        adaylar = ref_indeks.get((no, vkn), set())
        if len(adaylar) > 1:
            cok_adayli.append((gosterim_no, vkn, [fatura_nesne[p] for p in sorted(adaylar)]))
            continue
        if len(adaylar) == 1:
            f = fatura_nesne[next(iter(adaylar))]
            eslesen.append({"no": gosterim_no, "vkn": vkn, "fatura": f, "kaynak": "FATURA XML",
                            "dosyalar": d, "xml": xml})
            continue
        ts = [t for t in erp_ir.get(no, []) if (not t.cari.vkn_tckn) or t.cari.vkn_tckn == vkn]
        bagli = {t.fatura_id: t for t in ts if t.fatura_id and not t.fatura.silindi}
        if len(bagli) > 1:
            cok_adayli.append((gosterim_no, vkn, [t.fatura for t in bagli.values()]))
        elif len(bagli) == 1:
            t = next(iter(bagli.values()))
            eslesen.append({"no": gosterim_no, "vkn": vkn, "fatura": t.fatura,
                            "kaynak": f"ERP İRSALİYE {t.belge_no}", "dosyalar": d, "xml": xml})
        else:
            neden = (f"ERP irsaliyesi {ts[0].belge_no} var ama faturası yok" if ts
                     else "ERP'de bu irsaliye no yok ve hiçbir fatura XML'i bu irsaliyeye atıf yapmıyor")
            oneri = [(ham, fatura_nesne[p]) for (k, v, p), ham in ref_ham.items()
                     if v == vkn and (k, v) not in igrup and mesafe(k, no) <= 2]
            eslesmeyen.append((gosterim_no, vkn, xml, neden, oneri))

    # --- fatura XML'inde yazım hatalı irsaliye no (benzer ama aynı olmayan) raporu
    yazim_hatalari = []
    for (k, v), pkler in ref_indeks.items():
        if (k, v) in igrup:
            continue
        for (ik, iv) in igrup:
            if iv == v and mesafe(k, ik) <= 2:
                dogru = next((e for e in eslesen if _fatura_no_anahtar(e["no"]) == ik and e["vkn"] == iv), None)
                for p in sorted(pkler):
                    yazim_hatalari.append({"fatura": fatura_nesne[p], "xml_ref": ref_ham[(k, v, p)],
                                           "dogru_no": ik, "sonuc": dogru})

    # --- yükleme planı / uygulama
    plan, yuklenen_sayisi, atlanan_sayisi = [], 0, 0
    for e in eslesen:
        f = e["fatura"]
        mevcut = set(FaturaEk.objects.filter(fatura=f, silindi=False).values_list("orijinal_ad", flat=True))
        yuk, atl = [], []
        for uz in ("xml", "pdf"):
            if uz not in e["dosyalar"]:
                continue
            ad, bayt = e["dosyalar"][uz]
            if ad in mevcut:
                atl.append(ad)
                atlanan_sayisi += 1
                continue
            if uygula:
                ek_servis.ek_ekle(f, dosya=ContentFile(bayt, name=ad))
            yuk.append(ad)
            yuklenen_sayisi += 1
        plan.append((e, yuk, atl))
    return {"eslesen": plan, "eslesmeyen": eslesmeyen, "cok_adayli": cok_adayli,
            "okunamadi": okunamadi, "haric": haric_liste, "yazim_hatalari": yazim_hatalari,
            "tanimayan": tanimayan, "tekrar": tekrar, "grup_sayisi": len(igrup),
            "yuklenen": yuklenen_sayisi, "atlanan": atlanan_sayisi}
