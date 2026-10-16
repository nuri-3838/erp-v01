"""CARİLER Faz 2 (2026-10-13): liste için TOPLU bakiye / son hareket (cari sayısından bağımsız sabit sorgu), liste filtre-sıralama-arama, detay özet şeridi,
açık faturalar (FIFO eşleştirme) ve belgeler listesi.

Bakiye gösterimi cari ekstresiyle AYNI: borç − alacak; pozitif = BORÇ (cari bize borçlu), negatif = ALACAK (biz cariye borçluyuz).

Açık fatura kalanı için sistemde fatura-ödeme eşleştirmesi yoktur; bu yüzden cari hesabındaki ödeme/tahsilat/mahsup (faturadan olmayan ve ters yöndeki satırlar,
fatura iadeleri dahil) AYNI PARA BİRİMİ havuzunda en eski faturadan başlayarak (FIFO) düşülür; artan kısım "eşleşmemiş" (avans / fazla ödeme) olarak ayrı gösterilir.
Cari hesabın doğal yönü kök koduna göredir: 120/121/136 → borç (alacağımız), diğerleri → alacak (borcumuz)."""
from __future__ import annotations

import datetime
import re
from decimal import Decimal

from django.db.models import Count, Max, Prefetch, Q, Sum, Value
from django.db.models.functions import Replace
from django.urls import reverse

from core.metin import buyuk_harf_tr
from core.models import (Cari, CariYetkili, Fatura, FaturaSatir, TeklifSiparis, TeklifSiparisKalem, YevmiyeSatir)
from core.services import cari as cari_servis
from core.services import fatura_odeme
from core.tarih import tr_bugun

SIFIR = Decimal("0")
ESIK = Decimal("0.005")
TICARI_KOKLER = ("120", "121", "320", "321")
ALACAKLI_DOGAL_KOKLER = ("120", "121", "136")          # borç yönü doğal (cari bize borçlanır): müşteri / diğer alacaklar

TUR_SECENEKLERI = (("", "Tümü"), ("ticari", "Ticari (müşteri/tedarikçi)"), ("diger", "Diğer (personel, ortak, emanet…)"))
BAKIYE_SECENEKLERI = (("", "Tüm bakiyeler"), ("bakiyeli", "Bakiyeli"), ("borclu", "Borçlu"), ("alacakli", "Alacaklı"))
SIRALAMA_ANAHTARLARI = ("cari", "kategori", "sehir", "bakiye", "hareket")


# --------------------------------------------------------------------------------------------------- toplu bakiye
def bakiyeler(cariler, tarih=None) -> dict:
    """{cari pk: {"tl", "doviz": {pb: net}, "son", "adet"}} — TÜM cariler için TEK gruplu sorgu + döviz için bir sorgu (N+1 yok).
    ``tarih`` verilirse yalnız fiş tarihi ≤ tarih olan hareketler (TARİH İTİBARIYLA bakiye; cari raporları)."""
    cariler = list(cariler)
    sonuc = {c.pk: {"tl": SIFIR, "doviz": {}, "son": None, "adet": 0} for c in cariler}
    kodlar = {c.muhasebe_kodu: c.pk for c in cariler if c.muhasebe_kodu}
    if not kodlar:
        return sonuc
    temel = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=list(kodlar))
    if tarih is not None:
        temel = temel.filter(fis__tarih__lte=tarih)
    for r in temel.values("hesap_id").annotate(tl_borc=Sum("borc"), tl_alacak=Sum("alacak"), son=Max("fis__tarih"), adet=Count("id")):
        d = sonuc[kodlar[r["hesap_id"]]]
        d["tl"] = (r["tl_borc"] or SIFIR) - (r["tl_alacak"] or SIFIR)
        d["son"], d["adet"] = r["son"], r["adet"]
    for r in (temel.exclude(islem_pb="TRY").values("hesap_id", "islem_pb")
              .annotate(d_borc=Sum("islem_tutari", filter=Q(borc__gt=0)), d_alacak=Sum("islem_tutari", filter=Q(alacak__gt=0)))):
        net = (r["d_borc"] or SIFIR) - (r["d_alacak"] or SIFIR)
        if abs(net) >= ESIK:
            sonuc[kodlar[r["hesap_id"]]]["doviz"][r["islem_pb"]] = net
    return sonuc


# --------------------------------------------------------------------------------------------------- liste
_ALFABE = "ABCÇDEFGĞHIİJKLMNOÖPQRSŞTUÜVWXYZ"


def tr_anahtar(metin) -> list:
    """Türkçe alfabe sırası (A B C Ç D … G Ğ … I İ … O Ö … S Ş … U Ü …); rakamlar harflerden önce."""
    anahtar = []
    for ch in (metin or "").upper().replace("i", "İ"):
        if ch.isdigit():
            anahtar.append(ord(ch) - 48)
        elif ch in _ALFABE:
            anahtar.append(10 + _ALFABE.index(ch))
        else:
            anahtar.append(100 + ord(ch))
    return anahtar


def _rakam(ara) -> list:
    """Telefon araması: boşluk/parantez/tire/+ atılır; baştaki tek 0 (yerel biçim) ayrıca denenir."""
    r = re.sub(r"[\s().+\-]", "", ara or "")
    if len(r) < 3 or not r.isdigit():
        return []
    return [r, r[1:]] if r.startswith("0") and not r.startswith("00") and len(r) > 3 else [r]


def _telefon_q(alanlar, rakamlar, onek=""):
    """Telefon alanlarında boşluk/+ atılmış karşılaştırma için Q + gerekli anotasyonlar."""
    q, ano = Q(), {}
    for alan in alanlar:
        ad = f"_t_{alan}"
        ano[ad] = Replace(Replace(onek + alan, Value(" "), Value("")), Value("+"), Value(""))
        for r in rakamlar:
            q |= Q(**{f"{ad}__contains": r})
    return q, ano


def _arama_idleri(ara):
    """Arama: kod, ünvan, kısa ad, VKN/TCKN/Tax ID, telefon(lar), e-posta, şehir ve yetkili (ad/telefon/e-posta) — bu alanlar listede sütun olarak görünmez."""
    ara = (ara or "").strip()
    if not ara:
        return None
    buyuk, rakamlar = buyuk_harf_tr(ara), _rakam(ara)
    q = (Q(unvan__contains=buyuk) | Q(kisa_ad__contains=buyuk) | Q(kod__icontains=ara) | Q(vkn_tckn__contains=ara) | Q(tax_id__icontains=ara)
         | Q(eposta__icontains=ara) | Q(sehir__ad__contains=buyuk))
    ano = {}
    if rakamlar:
        tq, ano = _telefon_q(("telefon", "telefon_2"), rakamlar)
        q |= tq
    ids = set(Cari.objects.filter(silindi=False).annotate(**ano).filter(q).values_list("pk", flat=True))
    yq = Q(ad_soyad__contains=buyuk) | Q(eposta__icontains=ara)
    yano = {}
    if rakamlar:
        ytq, yano = _telefon_q(("telefon",), rakamlar)
        yq |= ytq
    ids |= set(CariYetkili.objects.filter(silindi=False, cari__silindi=False).annotate(**yano).filter(yq).values_list("cari_id", flat=True))
    return ids


def doviz_satirlari(doviz) -> list:
    """[(pb, net)] → ['1.234,56 USD B', '500,00 EUR A'] (B = borç, A = alacak; ekstreyle aynı gösterim)."""
    from core.templatetags.core_extras import tr_para
    return [f"{tr_para(abs(n))} {pb} {'B' if n > 0 else 'A'}" for pb, n in doviz]


def liste_getir(*, ara="", kategori_id="", sehir_id="", ulke_id="", tur="", bakiye="", doviz=False, pasif=False, sirala="cari", yon="asc") -> list:
    """Filtrelenmiş + sıralanmış cari satırları: [{"cari", "tl", "doviz": [(pb, net)], "son", "adet"}]. Sayfalama/Excel çağıranın işi."""
    qs = cari_servis.aktif_cariler().select_related("kategori__ust")
    if not pasif:
        qs = qs.filter(aktif=True)
    if kategori_id:
        qs = qs.filter(kategori_id=kategori_id)
    if sehir_id:
        qs = qs.filter(sehir_id=sehir_id)
    if ulke_id:
        qs = qs.filter(ulke_id=ulke_id)
    if tur == "ticari":
        qs = qs.filter(kategori__ust__kod__in=TICARI_KOKLER)
    elif tur == "diger":
        qs = qs.exclude(kategori__ust__kod__in=TICARI_KOKLER)
    ids = _arama_idleri(ara)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    cariler = list(qs)
    bak = bakiyeler(cariler)
    satirlar = []
    for c in cariler:
        b = bak[c.pk]
        tl = b["tl"]
        dvz = sorted(b["doviz"].items())
        bakiyeli = abs(tl) >= ESIK or bool(dvz)
        yon_tl = tl if abs(tl) >= ESIK else (sum((n for _pb, n in dvz), SIFIR) if dvz else SIFIR)
        if bakiye == "bakiyeli" and not bakiyeli:
            continue
        if bakiye == "borclu" and not yon_tl > 0:
            continue
        if bakiye == "alacakli" and not yon_tl < 0:
            continue
        if doviz and not (c.para_birimi != "TRY" or dvz):
            continue
        satirlar.append({"cari": c, "tl": tl, "doviz": dvz, "son": b["son"], "adet": b["adet"], "doviz_metin": doviz_satirlari(dvz)})
    sirala = sirala if sirala in SIRALAMA_ANAHTARLARI else "cari"
    ters = yon == "desc"
    anahtarlar = {
        "cari": lambda s: tr_anahtar(s["cari"].unvan),
        "kategori": lambda s: (tr_anahtar(s["cari"].kategori.kod_yolu if s["cari"].kategori_id else "~"), tr_anahtar(s["cari"].unvan)),
        "sehir": lambda s: (tr_anahtar(s["cari"].sehir.ad if s["cari"].sehir_id else "~"), tr_anahtar(s["cari"].unvan)),
        "bakiye": lambda s: (s["tl"], tr_anahtar(s["cari"].unvan)),
        "hareket": lambda s: (s["son"] or datetime.date.min, tr_anahtar(s["cari"].unvan)),
    }
    satirlar.sort(key=anahtarlar[sirala], reverse=ters)
    if sirala == "hareket":                                   # hareketsizler (tarih yok) her yönde sonda
        satirlar.sort(key=lambda s: s["son"] is None)
    return satirlar


# --------------------------------------------------------------------------------------------------- açık faturalar
def _dogal_yon(cari) -> str:
    return fatura_odeme.dogal_yon(cari)


def acik_faturalar(cari, bugun=None) -> dict:
    """{"satirlar": [{fatura, tarih, vade, pb, tutar, kalan, gecikme}], "toplam": {pb: kalan}, "vadesi_gecen": {pb: tutar}, "esitlenmemis": {pb: tutar}}.
    Yalnız ONAYLI faturalar; tutar = faturanın CARİ HESABINA yazılan tutar (tevkifat/stopaj sonrası, ekstreyle aynı). Eşleştirme: modül başlığındaki FIFO kuralı."""
    bugun = bugun or tr_bugun()
    bos = {"satirlar": [], "toplam": {}, "vadesi_gecen": {}, "esitlenmemis": {}}
    if not cari.muhasebe_kodu:
        return bos
    faturalar = {f.fis_id: f for f in Fatura.objects.filter(cari=cari, silindi=False, durum=Fatura.Durum.ONAYLI, fis__isnull=False).select_related("tip")}
    satirlar = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id=cari.muhasebe_kodu)
                .values("fis_id", "islem_pb", "borc", "alacak", "islem_tutari", "fis__kaynak"))
    kalemler, kredi = fatura_odeme.fifo_kalemler(cari, faturalar, satirlar)           # FIFO çekirdeği tek yerde (fatura_odeme)
    acik = sorted((k for k in kalemler.values() if k["kalan"] >= ESIK), key=lambda x: (x["vade"] or x["tarih"], x["fatura"].pk))
    toplam, gecen = {}, {}
    for k in acik:
        k["gecikme"] = (bugun - k["vade"]).days if k["vade"] and bugun > k["vade"] else 0
        toplam[k["pb"]] = toplam.get(k["pb"], SIFIR) + k["kalan"]
        if k["gecikme"] > 0:
            gecen[k["pb"]] = gecen.get(k["pb"], SIFIR) + k["kalan"]
    return {"satirlar": acik, "toplam": toplam, "vadesi_gecen": gecen, "esitlenmemis": {pb: t for pb, t in kredi.items() if t >= ESIK}}


# --------------------------------------------------------------------------------------------------- detay özeti
def ozet(cari, bugun=None) -> dict:
    """Detay üst şeridi: TL bakiye, döviz bakiyeleri, açık fatura toplamı (PB başına, vadesi geçen ayrı), son hareket (tarih + açıklama + fiş)."""
    b = bakiyeler([cari])[cari.pk]
    af = acik_faturalar(cari, bugun)
    son = None
    if cari.muhasebe_kodu and b["adet"]:
        s = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id=cari.muhasebe_kodu).select_related("fis")
             .order_by("-fis__tarih", "-fis__fis_no", "-id").first())
        if s is not None:
            son = {"tarih": s.fis.tarih, "fis_pk": s.fis_id, "fis_no": f"{s.fis.yil}/{s.fis.fis_no}", "aciklama": (s.aciklama or s.fis.aciklama or "")[:80]}
    return {"tl": b["tl"], "doviz": sorted(b["doviz"].items()), "adet": b["adet"], "son": son, "acik": af, "acik_fatura_adedi": len(af["satirlar"])}


# --------------------------------------------------------------------------------------------------- belgeler
def belgeler(cari) -> list:
    """Cariye ait teklif / proforma / sipariş / irsaliye ve faturalar TEK listede, tarihe göre (yeni üstte): [{tarih, tur, no, durum, tutar, pb, url, fatura}].
    Tutarlar kalemlerden hesaplanır (KDV dahil, belge para biriminde); kalemler toplu çekilir (belge sayısından bağımsız sabit sorgu)."""
    satirlar = []
    ts_qs = (TeklifSiparis.objects.filter(cari=cari, silindi=False)
             .prefetch_related(Prefetch("kalemler", queryset=TeklifSiparisKalem.objects.filter(silindi=False).select_related("kdv", "tevkifat"))))
    for t in ts_qs:
        tutar = sum((k.tutar + k.kdv_tutari for k in t.kalemler.all()), SIFIR)
        satirlar.append({"tarih": t.tarih, "tur": f"{t.get_belge_tur_display()} ({t.get_yon_display()})", "no": t.belge_no or "—",
                         "durum": t.get_durum_display(), "tutar": tutar, "pb": t.para_birimi, "fatura": False,
                         "url": reverse("core:teklif_siparis_detay", args=[t.pk]), "sira": t.pk})
    fat_qs = (Fatura.objects.filter(cari=cari, silindi=False).select_related("tip")
              .prefetch_related(Prefetch("satirlar", queryset=FaturaSatir.objects.filter(silindi=False).select_related("kdv", "tevkifat"))))
    for f in fat_qs:
        tutar = sum((s.tutar + s.kdv_tutari for s in f.satirlar.all()), SIFIR)
        satirlar.append({"tarih": f.tarih, "tur": f"Fatura ({f.tip.ad})" if f.tip_id else f"Fatura ({f.get_yon_display() if f.yon else '—'})",
                         "no": f.fatura_no or "—", "durum": f.get_durum_display(), "tutar": tutar, "pb": f.para_birimi, "fatura": True,
                         "url": reverse("core:fatura_detay", args=[f.pk]), "sira": f.pk})
    satirlar.sort(key=lambda s: (s["tarih"], s["sira"]), reverse=True)
    return satirlar
