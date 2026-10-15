"""FİNANS › Raporlar › ÖDEME TAKVİMİ (2026-10-16) — SALT-OKUNUR: hiçbir kayıt yazmaz, muhasebe mantığına dokunmaz; mevcut servisleri (``fatura_odeme`` FIFO, ``kk_donem.nakit_akisi``, ``finans_ozet``) kullanır.

Her satır bir ÇIKIŞ ya da GİRİŞ kalemidir (tarih, kaynak türü, cari/açıklama, belge no, döviz tutarı, TL karşılığı, kayda link). Kaynaklar:
  ÇIKIŞ  · Alış faturası açık kalanı (FIFO, vade) · Faturasız cari borcu (vadesiz: cari bakiyesinin faturaya bağlanamayan kısmı, ör. fason tahakkuku) · Kredi kartı (ekstre/taksit son ödeme tarihi,
           KK nakit akışıyla aynı satırlar) · Verilen çek/senet · Vergi/SGK (kayıtlı vade yok → YASAL VADE kuralla türetilir, "kural ile") · Emanet (336 cari kartındaki geri ödeme vadesi) · Kredi taksidi.
  GİRİŞ  · Satış faturası açık kalanı · Alınan çek/senet · POS bloke (108.* bakiyesi).
Vergi/SGK: 360.10.* = KDV (tevkifat / KDV-2) → sonraki ayın 28'i; 360.20 (muhtasar/GV stopajı) ve damga → sonraki ayın 26'sı; 361 (SGK) → sonraki ayın SON günü; hafta sonu/resmî tatilde sonraki iş günü. Tahakkuk
AY'a göre (fiş tarihi) birikir, ödemeler (borç) en eski aydan düşülür (FIFO); alt hesaplar GRUP olarak birlikte alınır (ödeme tek alt hesaba yazılmış olabilir). Net KDV (391 − 191 − 190) pozitifse ayrıca "KDV (ödenecek)" satırı.
TL karşılığı BUGÜNKÜ kurla (son TCMB alış, ``finans_ozet``). Vadesi geçmiş satırlar "Gecikmiş" grubundadır; vadesiz satırlar kümülatife dahil DEĞİL, ayrı gösterilir.
Mutabakat: takvim toplamı ilgili hesap bakiyeleriyle (320, 309 KK, 103, 360/361, 336, 300) cari/kart/hesap bazında karşılaştırılır; fark kalem kalem listelenir."""
from __future__ import annotations

import calendar
import datetime
import re
from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Q

from core.models import Cari, CekSenet, Fatura, HesapPlani, Kredi, KrediKarti, KrediTaksit, ResmiTatil, YevmiyeSatir
from core.services import fatura_odeme, finans_ozet, kk_donem, raporlar
from core.services.cari import vade_hesapla  # noqa: F401  (FIFO vadesi fatura_odeme içinde çözülür)

SIFIR = Decimal("0.00")
ESIK = Decimal("0.005")

CIKIS, GIRIS = "CIKIS", "GIRIS"
KAYNAKLAR = {                                                      # anahtar: (etiket, yön, css)
    "FATURA_ALIS": ("Alış faturası", CIKIS, "fatura"),
    "CARI_BORC": ("Faturasız cari borcu", CIKIS, "cari"),
    "KK": ("Kredi kartı", CIKIS, "kk"),
    "CEK_VERILEN": ("Verilen çek/senet", CIKIS, "cek"),
    "VERGI": ("Vergi / SGK", CIKIS, "vergi"),
    "EMANET": ("Emanet (336)", CIKIS, "emanet"),
    "KREDI": ("Kredi taksidi", CIKIS, "kredi"),
    "FATURA_SATIS": ("Satış faturası", GIRIS, "fatura"),
    "CEK_ALINAN": ("Alınan çek/senet", GIRIS, "cek"),
    "POS": ("POS bloke", GIRIS, "pos"),
}
GRUPLAMALAR = (("gun", "Gün"), ("hafta", "Hafta"), ("ay", "Ay"))
VERGI_KOKLERI = ("360", "361")
ATLANAN_CARI_KOKLERI = ("335", "336", "500")                       # personel / emanet (ayrı kaynak) / ortak: "faturasız cari borcu"na girmez


# ----------------------------------------------------------------------------------------------------------------------------
def ay_ekle(tarih: datetime.date, n: int) -> datetime.date:
    t = tarih.year * 12 + (tarih.month - 1) + n
    y, m = t // 12, t % 12 + 1
    return datetime.date(y, m, min(tarih.day, calendar.monthrange(y, m)[1]))


def is_gunu(t: datetime.date) -> datetime.date:
    """Hafta sonu / resmî tatile denk gelen tarihi sonraki iş gününe çeker (KK vade kuralıyla aynı)."""
    tatiller = set(ResmiTatil.objects.filter(silindi=False, tarih__gte=t, tarih__lte=t + datetime.timedelta(days=14)).values_list("tarih", flat=True))
    while t.weekday() >= 5 or t in tatiller:
        t += datetime.timedelta(days=1)
    return t


def yasal_vade(kural: str, yil: int, ay: int) -> datetime.date:
    """``yil/ay`` dönemine ait beyan/ödeme vadesi: KDV → sonraki ayın 28'i · muhtasar/damga → 26'sı · SGK → sonraki ayın son günü (iş gününe kaydırmalı)."""
    y, m = (yil + ay // 12, ay % 12 + 1)
    son = calendar.monthrange(y, m)[1]
    gun = {"kdv": 28, "muhtasar": 26, "damga": 26, "sgk": son}.get(kural)
    return is_gunu(datetime.date(y, m, min(gun, son))) if gun else None


def _tl(tutar, pb, kurlar):
    if pb == "TRY":
        return tutar
    kur = kurlar.get(pb)
    return (tutar * kur).quantize(SIFIR) if kur else None


def _satir(kaynak, *, tarih, bugun, tutar, pb, kurlar, baslik, aciklama="", belge_no="", cari=None, url=None, kural=False, ek=""):
    etiket, yon, css = KAYNAKLAR[kaynak]
    tl = _tl(tutar, pb, kurlar)
    return {"tarih": tarih, "yon": yon, "kaynak": kaynak, "kaynak_ad": etiket, "css": css, "baslik": baslik, "aciklama": aciklama, "belge_no": belge_no,
            "cari_id": cari.pk if cari is not None else None, "cari_ad": (cari.kisa_ad or cari.unvan) if cari is not None else "", "pb": pb, "tutar": tutar,
            "tl": tl if tl is not None else SIFIR, "tl_yok": tl is None, "url": url, "kural": kural, "ek": ek, "gecikmis": bool(tarih is not None and tarih < bugun)}


# ----------------------------------------------------------------------------------------------------------------------------
IADE_KAYNAKLARI = ("BANKA", "KASA", "KREDI_KARTI", "CEK_SENET")      # cariden gelen para (ödemenin iadesi): faturaları kapatmış sayılmaz


def _cari_satirlari(bugun, kurlar) -> list:
    """Cari bazında tek FIFO geçişi (``fatura_odeme.fifo_kalemler``, iade kuralıyla): alış faturası açık kalanı (çıkış, vadeye göre), satış faturası açık kalanı (giriş) ve
    — doğal yönü alacak olan 320 tipi carilerde — faturaya bağlanamayan bakiye (vadesiz çıkış; ör. fason tahakkuku). Faturasız bakiye = faturasız alacak hareketleri − artan ödeme/avans."""
    faturali = defaultdict(dict)
    for f in Fatura.objects.filter(silindi=False, durum=Fatura.Durum.ONAYLI, fis__isnull=False).select_related("tip"):
        faturali[f.cari_id][f.fis_id] = f
    caridler = list(Cari.objects.filter(silindi=False).exclude(muhasebe_kodu=""))
    hareketler = defaultdict(list)
    for s in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=[c.muhasebe_kodu for c in caridler])
              .values("hesap_id", "fis_id", "islem_pb", "borc", "alacak", "islem_tutari", "fis__kaynak")):
        hareketler[s["hesap_id"]].append(s)
    satirlar = []
    for cari in caridler:
        hareket = hareketler.get(cari.muhasebe_kodu)
        if not hareket:
            continue
        faturalar = faturali.get(cari.pk, {})
        ozel = cari.muhasebe_kodu.split(".")[0] in ATLANAN_CARI_KOKLERI           # ortak / personel / emanet carileri: banka hareketleri sermaye/avans olabilir → iade kuralı UYGULANMAZ (liste FIFO'su)
        kalemler, kredi = fatura_odeme.fifo_kalemler(cari, faturalar, hareket, () if ozel else IADE_KAYNAKLARI)
        for k in sorted(kalemler.values(), key=lambda x: (x["tarih"], x["fatura"].pk)):
            if k["kalan"] < ESIK:
                continue
            f = k["fatura"]
            kaynak = "FATURA_ALIS" if f.yon == "ALIS" else "FATURA_SATIS"
            kismi = k["kalan"] < k["tutar"] - ESIK
            satirlar.append(_satir(kaynak, tarih=k["vade"], bugun=bugun, tutar=k["kalan"], pb=k["pb"], kurlar=kurlar, baslik=cari.kisa_ad or cari.unvan,
                                   aciklama=(f.tip.ad if f.tip_id else "Fatura") + (f" · kısmi ödenmiş (fatura tutarı {k['tutar']:,.2f} {k['pb']})" if kismi else ""),
                                   belge_no=f.fatura_no or ("Faturasız" if f.faturasiz else "—"), cari=cari, url=("core:fatura_detay", f.pk), ek=f"{f.tarih:%d.%m.%Y} tarihli"))
        if fatura_odeme.dogal_yon(cari) == "alacak" and cari.muhasebe_kodu.split(".")[0] not in ATLANAN_CARI_KOKLERI:
            faturasiz, kaynaklar = defaultdict(lambda: SIFIR), defaultdict(Counter)
            for s in hareket:
                if s["alacak"] > 0 and s["fis_id"] not in faturalar and s["fis__kaynak"] not in IADE_KAYNAKLARI:
                    pb = s["islem_pb"]
                    faturasiz[pb] += s["islem_tutari"] or s["alacak"]
                    kaynaklar[pb][s["fis__kaynak"]] += 1
            for pb, tutar in faturasiz.items():
                avans = kredi.get(pb, SIFIR)
                net = tutar - avans
                if net > ESIK:
                    ozet = ", ".join(f"{n} adet {k.replace('_', ' ').lower()}" for k, n in kaynaklar[pb].most_common(3))
                    satirlar.append(_satir("CARI_BORC", tarih=None, bugun=bugun, tutar=net, pb=pb, kurlar=kurlar, baslik=cari.kisa_ad or cari.unvan,
                                           aciklama=f"Faturaya bağlı olmayan bakiye — faturasız alacak hareketleri {tutar:,.2f} {pb}" + (f" − faturalara düşmeyen ödeme/avans {avans:,.2f}" if avans > 0 else "") + f" ({ozet})",
                                           cari=cari, url=("core:cari_detay", cari.pk)))
    return satirlar


def kk_satirlari(bugun, kurlar, ay_sayisi=36):
    """``kk_donem.nakit_akisi`` satırları (aynı rakam) + atlanan (kesim/son ödeme günü tanımsız) kartlar."""
    o = kk_donem.nakit_akisi(bugun, ay_sayisi=ay_sayisi)
    satirlar = []
    for r in o["satirlar"]:
        kart = r["kart"]
        satirlar.append(_satir("KK", tarih=r["vade"], bugun=bugun, tutar=r["tutar"], pb="TRY", kurlar=kurlar, baslik=f"{kart.ad} — {r['tur']}",
                               aciklama=f"Kesim {r['kesim']:%d.%m.%Y}", url=("core:kredi_karti_donem_ekstresi", kart.pk), ek=kart.ad))
    return satirlar, o["atlanan"]


def _cek_satirlari(bugun, kurlar) -> list:
    D, Y = CekSenet.Durum, CekSenet.Yon
    satirlar = []
    sorgu = (CekSenet.objects.filter(silindi=False).select_related("cari")
             .filter(Q(yon=Y.VERILEN, durum=D.VERILDI) | Q(yon=Y.ALINAN, durum__in=[D.PORTFOYDE, D.TAHSILDE, D.TEMINATTA])).order_by("vade", "id"))
    for c in sorgu:
        verilen = c.yon == Y.VERILEN
        cari_ad = c.cari.unvan if c.cari_id else (c.kesideci or "")
        satirlar.append(_satir("CEK_VERILEN" if verilen else "CEK_ALINAN", tarih=c.vade, bugun=bugun, tutar=c.tutar, pb=c.para_birimi, kurlar=kurlar,
                               baslik=f"{c.get_tip_display()} — {cari_ad}", aciklama=c.get_durum_display(), belge_no=c.belge_no or "", cari=c.cari if c.cari_id else None,
                               url=(("core:cek_bordro_detay", c.giris_bordrosu_id) if c.giris_bordrosu_id else None)))
    return satirlar


def _vergi_grubu(hesap_kodu: str, ad: str):
    """(grup anahtarı, grup adı, vade kuralı)."""
    if hesap_kodu.startswith("361"):
        return "sgk", "SGK prim borcu (361)", "sgk"
    buyuk = (ad or "").upper()
    if "DAMGA" in buyuk:
        return "damga", "Damga vergisi (360)", "damga"
    if hesap_kodu.startswith("360.10") or "KDV" in buyuk or "TEVK" in buyuk:
        return "kdv", "KDV tevkifatı / KDV-2 (360.10)", "kdv"
    if hesap_kodu.startswith("360.20") or "GELİR VERGİSİ" in buyuk or "STOPAJ" in buyuk or "MUHTASAR" in buyuk:
        return "muhtasar", "Muhtasar — gelir vergisi stopajı (360.20)", "muhtasar"
    return "diger", "Diğer ödenecek vergi (360)", None


def vergi_satirlari(bugun, kurlar) -> list:
    """360/361 hesaplarındaki ödenmemiş tahakkuklar: ay bazında FIFO (ödeme en eski aydan düşülür), yasal vadeyle."""
    hesaplar = {h.hesap_kodu: h.hesap_adi for h in HesapPlani.objects.filter(silindi=False, hesap_kodu__regex=r"^36[01]")}
    gruplar = defaultdict(lambda: {"kodlar": [], "ad": "", "kural": None})
    for kod, ad in hesaplar.items():
        anahtar, grup_ad, kural = _vergi_grubu(kod, ad)
        g = gruplar[anahtar]
        g["kodlar"].append(kod)
        g["ad"], g["kural"] = grup_ad, kural
    satirlar = []
    for anahtar, g in gruplar.items():
        alacak, odeme = defaultdict(lambda: SIFIR), SIFIR
        for s in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap_id__in=g["kodlar"]).select_related("fis").order_by("fis__tarih", "id")):
            if s.alacak:
                alacak[(s.fis.tarih.year, s.fis.tarih.month)] += s.alacak
            if s.borc:
                odeme += s.borc
        kalan = {}
        for ay in sorted(alacak):                                           # ödemeler en eski tahakkuktan düşülür
            dus = min(alacak[ay], odeme)
            odeme -= dus
            if alacak[ay] - dus > ESIK:
                kalan[ay] = alacak[ay] - dus
        for (yil, ay), tutar in kalan.items():
            vade = yasal_vade(g["kural"], yil, ay) if g["kural"] else None
            satirlar.append(_satir("VERGI", tarih=vade, bugun=bugun, tutar=tutar, pb="TRY", kurlar=kurlar, baslik=g["ad"], aciklama=f"{ay:02d}/{yil} dönemi tahakkuku",
                                   kural=bool(g["kural"]), url=("core:hesap_ekstresi", {"kdv": "360.10", "muhtasar": "360.20", "sgk": "361"}.get(anahtar, "360")), ek=g["ad"]))
    # net KDV (hesaplanan − indirilecek − devreden)
    bak = lambda on: raporlar._devir(on, datetime.date(2100, 1, 1))[0]            # noqa: E731  (borç − alacak)
    net_kdv = -bak("391") - bak("191") - bak("190")
    if net_kdv > ESIK:
        satirlar.append(_satir("VERGI", tarih=yasal_vade("kdv", bugun.year, bugun.month), bugun=bugun, tutar=net_kdv, pb="TRY", kurlar=kurlar, baslik="KDV (ödenecek)",
                               aciklama="Hesaplanan KDV (391) − indirilecek KDV (191) − devreden KDV (190)", kural=True, url=("core:hesap_ekstresi", "391"), ek="KDV (ödenecek)"))
    return satirlar


_VADE_DESENI = re.compile(r"VADE\w*\s*[:：]\s*(\d{1,2})[./](\d{1,2})[./](\d{4})", re.IGNORECASE)


def emanet_vadesi(notlar: str):
    """Cari kartı notlarındaki 'GERİ ÖDEME VADESİ: 31.10.2026' → date (yoksa None)."""
    m = _VADE_DESENI.search((notlar or "").replace("İ", "I").replace("ı", "i"))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _emanet_satirlari(bugun, kurlar) -> list:
    satirlar = []
    for c in Cari.objects.filter(silindi=False, muhasebe_kodu__startswith="336").order_by("kod"):
        tl_defter, dvz = finans_ozet._bakiye(c.muhasebe_kodu)
        pb = c.para_birimi
        net = tl_defter if pb == "TRY" else dvz.get(pb, SIFIR)             # borç − alacak
        tutar = -net
        if tutar <= ESIK:
            continue
        satirlar.append(_satir("EMANET", tarih=emanet_vadesi(c.notlar), bugun=bugun, tutar=tutar, pb=pb, kurlar=kurlar, baslik=f"Emanet — {c.unvan}",
                               aciklama=(c.notlar or "")[:120], cari=c, url=("core:cari_detay", c.pk)))
    return satirlar


def _kredi_satirlari(bugun, kurlar) -> list:
    satirlar = []
    for t in KrediTaksit.objects.filter(silindi=False, durum=KrediTaksit.Durum.BEKLIYOR).select_related("kredi").order_by("vade", "sira"):
        satirlar.append(_satir("KREDI", tarih=t.vade, bugun=bugun, tutar=t.anapara + t.faiz, pb=t.kredi.para_birimi, kurlar=kurlar, baslik=f"{t.kredi.ad} — {t.sira}. taksit",
                               aciklama=f"Anapara {t.anapara:,.2f} + faiz {t.faiz:,.2f}", url=("core:kredi_detay", t.kredi_id)))
    return satirlar


def _pos_satirlari(bugun, kurlar) -> list:
    satirlar = []
    for h in HesapPlani.objects.filter(silindi=False, aktif=True, hesap_kodu__regex=r"^108\.").order_by("hesap_kodu"):
        tl_defter, _dvz = finans_ozet._bakiye(h.hesap_kodu)
        if tl_defter > ESIK:
            satirlar.append(_satir("POS", tarih=bugun, bugun=bugun, tutar=tl_defter, pb="TRY", kurlar=kurlar, baslik=f"{h.hesap_adi} ({h.hesap_kodu})",
                                   aciklama="Bloke POS/sanal pos tutarı (bankaya geçmeyi bekleyen)", url=("core:hesap_ekstresi", h.hesap_kodu)))
    return satirlar


# ----------------------------------------------------------------------------------------------------------------------------
def tum_satirlar(bugun=None) -> dict:
    """Tüm kaynaklardan filtresiz satırlar + bugünkü nakit/kur + atlanan kartlar. Sıralı: tarih (vadesizler sonda), çıkış önce."""
    bugun = bugun or datetime.date.today()
    fo = finans_ozet.ozet(bugun)
    kurlar = fo["kurlar"]
    satirlar = []
    satirlar += _cari_satirlari(bugun, kurlar)
    kk, atlanan_kartlar = kk_satirlari(bugun, kurlar)
    satirlar += kk
    satirlar += _cek_satirlari(bugun, kurlar)
    satirlar += vergi_satirlari(bugun, kurlar)
    satirlar += _emanet_satirlari(bugun, kurlar)
    satirlar += _kredi_satirlari(bugun, kurlar)
    satirlar += _pos_satirlari(bugun, kurlar)
    satirlar.sort(key=lambda r: (r["tarih"] is None, r["tarih"] or datetime.date.max, r["yon"] != CIKIS, r["kaynak"], r["baslik"]))
    return {"bugun": bugun, "satirlar": satirlar, "nakit_tl": fo["nakit_tl"], "kurlar": kurlar, "kur_tarihi": fo["kur_tarihi"], "atlanan_kartlar": atlanan_kartlar,
            "nakit_hesaplari": [*fo["kasalar"], *fo["banka"]]}


def filtrele(satirlar, *, yon="", kaynaklar=(), cari_id=None):
    s = satirlar
    if yon in (CIKIS, GIRIS):
        s = [r for r in s if r["yon"] == yon]
    if kaynaklar:
        s = [r for r in s if r["kaynak"] in kaynaklar]
    if cari_id:
        s = [r for r in s if r["cari_id"] == cari_id]
    return s


def _grup_anahtari(t: datetime.date, grup: str):
    if grup == "hafta":
        pazartesi = t - datetime.timedelta(days=t.weekday())
        return pazartesi, f"{pazartesi:%d.%m} – {pazartesi + datetime.timedelta(days=6):%d.%m.%Y}"
    if grup == "ay":
        return datetime.date(t.year, t.month, 1), f"{t.month:02d}.{t.year}"
    return t, f"{t:%d.%m.%Y}"


def grupla(satirlar, *, bugun, bas, bit, grup="gun", acilis=SIFIR) -> dict:
    """Gruplu tablo: Gecikmiş (en üstte) → tarih grupları (bas..bit) → Vadesiz (kümülatife dahil değil). Kümülatif bakiye: açılış (bugünkü banka+kasa) + (gecikmiş + bas öncesi) + grup net'leri."""
    gecikmis = [r for r in satirlar if r["tarih"] is not None and r["tarih"] < bugun]
    vadesiz = [r for r in satirlar if r["tarih"] is None]
    tarihli = [r for r in satirlar if r["tarih"] is not None and r["tarih"] >= bugun]
    net = lambda rows: sum((r["tl"] if r["yon"] == GIRIS else -r["tl"] for r in rows), SIFIR)      # noqa: E731
    bakiye = acilis + net([r for r in tarihli if r["tarih"] < bas])                           # gecikmiş grubunun net'i paket() içinde eklenir
    gruplar = []

    def paket(ad, rows, anahtar=None):
        nonlocal bakiye
        cikis = sum((r["tl"] for r in rows if r["yon"] == CIKIS), SIFIR)
        giris = sum((r["tl"] for r in rows if r["yon"] == GIRIS), SIFIR)
        bakiye += giris - cikis
        return {"ad": ad, "anahtar": anahtar, "satirlar": rows, "cikis": cikis, "giris": giris, "net": giris - cikis, "kumulatif": bakiye, "negatif": bakiye < 0}

    if gecikmis:
        g = paket("Gecikmiş", gecikmis, "gecikmis")
        g["gecikmis"] = True
        gruplar.append(g)
    kovalar = {}
    for r in tarihli:
        if bas <= r["tarih"] <= bit:
            k, ad = _grup_anahtari(r["tarih"], grup)
            kovalar.setdefault(k, (ad, []))[1].append(r)
    for k in sorted(kovalar):
        gruplar.append(paket(kovalar[k][0], kovalar[k][1], k))
    vs = None
    if vadesiz:
        vs = {"ad": "Vadesiz", "satirlar": vadesiz, "cikis": sum((r["tl"] for r in vadesiz if r["yon"] == CIKIS), SIFIR), "giris": sum((r["tl"] for r in vadesiz if r["yon"] == GIRIS), SIFIR)}
    donem = [r for g in gruplar for r in g["satirlar"]]
    cikis = sum((r["tl"] for r in donem if r["yon"] == CIKIS), SIFIR)
    giris = sum((r["tl"] for r in donem if r["yon"] == GIRIS), SIFIR)
    en_dusuk = min([g["kumulatif"] for g in gruplar] + [acilis])
    return {"gruplar": gruplar, "vadesiz": vs, "cikis": cikis, "giris": giris, "gecikmis_cikis": sum((r["tl"] for r in gecikmis if r["yon"] == CIKIS), SIFIR),
            "net_ihtiyac": -en_dusuk if en_dusuk < 0 else SIFIR, "donem_sonu": gruplar[-1]["kumulatif"] if gruplar else acilis, "acilis": acilis}


# ---------------------------------------------------------------------------------------------- MUTABAKAT
def _toplam_tl(rows):
    return sum((r["tl"] for r in rows), SIFIR)


def mutabakat(tum, kk_atlanan=()) -> list:
    """Takvim toplamı (TÜM vadeler, filtresiz) ↔ ilgili hesap bakiyeleri. Her blok: {ad, defter, takvim, fark, kalemler: [{ad, defter, takvim, fark, not}]}.
    Tutarlar TL; döviz carilerde defter = yevmiyedeki TL, takvim = bugünkü kur → fark 'kur farkı' olarak açıklanır."""
    satirlar = tum["satirlar"]
    bloklar = []

    def blok(ad, kalemler, aciklama=""):
        kalemler = [k for k in kalemler if abs(k["defter"]) > ESIK or abs(k["takvim"]) > ESIK]
        defter = sum((k["defter"] for k in kalemler), SIFIR)
        takvim = sum((k["takvim"] for k in kalemler), SIFIR)
        for k in kalemler:
            k["fark"] = k["defter"] - k["takvim"]
        bloklar.append({"ad": ad, "defter": defter, "takvim": takvim, "fark": defter - takvim, "kalemler": kalemler, "aciklama": aciklama,
                        "farkli": [k for k in kalemler if abs(k["fark"]) > ESIK]})

    # 320 ticari borçlar: cari bazında
    cariler = {c.pk: c for c in Cari.objects.filter(silindi=False, muhasebe_kodu__startswith="320")}
    kal = []
    for c in sorted(cariler.values(), key=lambda c: c.kod):
        tl_defter, _dvz = finans_ozet._bakiye(c.muhasebe_kodu)
        defter = -tl_defter
        takvim = _toplam_tl([r for r in satirlar if r["cari_id"] == c.pk and r["kaynak"] in ("FATURA_ALIS", "CARI_BORC")])
        nt = ""
        if defter < -ESIK:
            nt = "Cari lehine (borç) bakiye / avans — çıkış kalemi değil"
        elif c.para_birimi != "TRY" and abs(defter - takvim) > ESIK:
            nt = f"Döviz cari: defter TL (kayıtlı kur/kur farkı) ≠ bugünkü kur ({c.para_birimi})"
        kal.append({"ad": f"{c.kod} {c.unvan[:40]}", "defter": defter, "takvim": takvim, "not": nt, "url": ("core:cari_detay", c.pk)})
    blok("320 — Ticari borçlar (alış faturası açık kalanı + faturasız bakiye)", kal, "Takvim: alış faturalarının FIFO açık kalanı + faturaya bağlanamayan bakiye. Fark: avanslar ve döviz kur etkisi.")

    # 309 Kredi kartları
    kal = []
    kart_toplam = defaultdict(lambda: SIFIR)
    for r in satirlar:
        if r["kaynak"] == "KK":
            kart_toplam[r["ek"]] += r["tl"]
    atlanan = {k.pk for k in kk_atlanan}
    for k in KrediKarti.objects.filter(silindi=False).order_by("ad"):
        tl_defter, dvz = finans_ozet._bakiye(k.muhasebe_id)
        defter = -(tl_defter if k.para_birimi == "TRY" else (dvz.get(k.para_birimi, SIFIR)))
        nt = ""
        if k.para_birimi != "TRY":
            nt = "Döviz kart: nakit akışı yalnız TL kartları kapsar"
        elif k.pk in atlanan:
            nt = "Kesim/son ödeme günü tanımsız → nakit akışına girmedi"
        kal.append({"ad": k.ad, "defter": defter, "takvim": kart_toplam.get(k.ad, SIFIR), "not": nt, "url": ("core:kredi_karti_detay", k.pk)})
    blok("309 — Kredi kartları (ekstre + taksit nakit akışı)", kal, "Defter borcu = harcamanın tamamı (taksit planı dahil); takvim = KK nakit akışı satırları. Fark: ödenmemiş/plansız kısım ya da plan dışı kalan.")

    # 103 Verilen çekler
    ck = _toplam_tl([r for r in satirlar if r["kaynak"] == "CEK_VERILEN"])
    tl_103, _d = finans_ozet._bakiye("103")                                   # alt hesaplar dahil (raporlar._devir üst hesaba toplar)
    blok("103 — Verilen çekler", [{"ad": "103.* verilen çekler", "defter": -tl_103, "takvim": ck, "not": "", "url": ("core:hesap_ekstresi", "103")}], "Takvim: durumu 'Verildi' olan verilen çek/senetler (vade bazlı).")

    # 360/361 vergi / SGK
    kal = []
    for kok, ad in (("360.10", "360.10 KDV tevkifatı"), ("360.20", "360.20 Muhtasar"), ("361", "361 SGK")):
        defter = -finans_ozet._bakiye(kok)[0]                                   # alt hesaplar dahil
        anahtar = {"360.10": "KDV tevkifatı / KDV-2 (360.10)", "360.20": "Muhtasar — gelir vergisi stopajı (360.20)", "361": "SGK prim borcu (361)"}[kok]
        takvim = _toplam_tl([r for r in satirlar if r["kaynak"] == "VERGI" and r["ek"] == anahtar])
        kal.append({"ad": ad, "defter": defter, "takvim": takvim, "not": "", "url": ("core:hesap_ekstresi", kok)})
    kdv_odenecek = _toplam_tl([r for r in satirlar if r["kaynak"] == "VERGI" and r["ek"] == "KDV (ödenecek)"])
    if kdv_odenecek:
        kal.append({"ad": "KDV (391−191−190)", "defter": kdv_odenecek, "takvim": kdv_odenecek, "not": "", "url": ("core:hesap_ekstresi", "391")})
    blok("360 / 361 — Ödenecek vergi ve SGK", kal, "Takvim: tahakkuk − ödeme (aylık FIFO), yasal vadeyle (kural ile).")

    # 336 emanetler
    kal = []
    for c in Cari.objects.filter(silindi=False, muhasebe_kodu__startswith="336").order_by("kod"):
        tl_defter, _dvz = finans_ozet._bakiye(c.muhasebe_kodu)
        takvim = _toplam_tl([r for r in satirlar if r["kaynak"] == "EMANET" and r["cari_id"] == c.pk])
        kal.append({"ad": f"{c.kod} {c.unvan}", "defter": -tl_defter, "takvim": takvim, "not": ("Döviz emanet: defter TL ≠ bugünkü kur" if c.para_birimi != "TRY" else ""), "url": ("core:cari_detay", c.pk)})
    blok("336 — Emanetler", kal, "Takvim: cari kartındaki geri ödeme vadesi; bugünkü kurla TL.")

    # 300 krediler
    kal = []
    for k in Kredi.objects.filter(silindi=False).order_by("ad"):
        tl_defter, dvz = finans_ozet._bakiye(k.muhasebe_id)
        defter = -(tl_defter if k.para_birimi == "TRY" else dvz.get(k.para_birimi, SIFIR))
        takvim = _toplam_tl([r for r in satirlar if r["kaynak"] == "KREDI" and r["baslik"].startswith(k.ad)])
        kal.append({"ad": k.ad, "defter": defter, "takvim": takvim, "not": ("Taksit planı girilmemiş" if takvim == 0 and defter > ESIK else ""), "url": ("core:kredi_detay", k.pk)})
    blok("300 — Banka kredileri", kal, "Takvim: kredi ekranındaki BEKLİYOR taksitler (anapara + faiz).")
    return bloklar
