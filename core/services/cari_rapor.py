"""CARİ RAPORLARI (2026-10-14): Yaşlandırma, Vadesi geçenler, Tarih itibarıyla bakiye, Cari ciro özeti. HEPSİ SALT-OKUNUR (hiçbir kayıt yazılmaz).

Sorgu sayısı cari/fatura sayısından BAĞIMSIZDIR (test ile kilitli): açık faturalar için ``acik_faturalar_toplu`` TÜM faturalı cariler için TEK fatura sorgusu + TEK yevmiye
satırı sorgusu ile çalışır; ``cari_ozet.acik_faturalar`` (tek cari) ile AYNI FIFO kuralı (eşdeğerlik testle kilitli): cari hesabındaki ödeme/tahsilat/mahsup/fatura iadesi
(faturadan olmayan ve doğal yönün tersi satırlar) AYNI PARA BİRİMİ havuzunda en eski faturadan başlayarak düşülür. "Tarih itibarıyla": fatura tarihi ve fiş tarihi ≤ tarih.

Tanımlar (onaylı):
- Sekme: carinin kök koduna göre. 120/121/136 → ALACAK (müşteri; cari bize borçlu), diğerleri → BORÇ (tedarikçi; biz borçluyuz).
- Vade günü "vadesi gelmemiş" sayılır (mevcut "vadesi geçen" tanımıyla aynı: gecikme = tarih − vade > 0). Dilimler 1-30 / 31-60 / 61-90 / 90+; vadesi hiç bilinmeyen fatura ayrı "Vadesi yok".
- Para birimleri ASLA toplanmaz (PB başına ayrı tablo / toplam).
- Ciro: yalnız ONAYLI faturalar, fatura tarihi, KDV hariç TL (satır tutarı × fatura kuru, ``FaturaSatir.tutar_tl`` ile aynı yuvarlama). 'Yalnız KDV' tipi dışarıda (matrahı başka
  fişte yazılı: çift sayım); gider/SMM alış toplamına dahil; iade tipleri (satış iadesi: ALIŞ yönlü tip, alış iadesi: SATIŞ yönlü tip) ilgili tarafın toplamından DÜŞÜLÜR
  (brüt / iade / net ayrı)."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Round

from core.models import Cari, Fatura, FaturaSatir, FaturaTipi, YevmiyeSatir
from core.services import cari as cari_servis
from core.services import cari_ozet
from core.services import fatura_odeme
from core.services.cari_ozet import ESIK, SIFIR, TICARI_KOKLER, tr_anahtar
from core.tarih import tr_bugun

DILIMLER = (("gelmemis", "Vadesi gelmemiş"), ("d30", "1-30 gün"), ("d60", "31-60 gün"), ("d90", "61-90 gün"), ("d90p", "90+ gün"), ("vadesiz", "Vadesi yok"))
DILIM_ANAHTARLARI = tuple(k for k, _ in DILIMLER)
SEKMELER = (("alacak", "Alacak (müşteri)"), ("borc", "Borç (tedarikçi)"))
SEKME_ANAHTARLARI = tuple(k for k, _ in SEKMELER)


def _pb_sirasi(pb):
    return (pb != "TRY", pb)


def dilim_anahtari(vade, gecikme) -> str:
    """Vade bilinmiyorsa 'vadesiz'; gecikme (tarih − vade, gün) ≤ 0 → 'gelmemis' (vade günü dahil)."""
    if vade is None:
        return "vadesiz"
    if gecikme <= 0:
        return "gelmemis"
    return "d30" if gecikme <= 30 else "d60" if gecikme <= 60 else "d90" if gecikme <= 90 else "d90p"


def sekme_of(cari) -> str:
    """'alacak' (müşteri: doğal yön borç) ya da 'borc' (tedarikçi vb.)."""
    return "alacak" if cari_ozet._dogal_yon(cari) == "borc" else "borc"


# --------------------------------------------------------------------------------------------------- toplu açık faturalar
def acik_faturalar_toplu(tarih=None, kategori_id="") -> list:
    """Tarih itibarıyla açık (kalanı > 0) onaylı faturalar, TÜM cariler için: [{cari, fatura, tarih, vade, pb, tutar, kalan, gecikme, dilim, sekme}].
    ``cari_ozet.acik_faturalar`` ile aynı kural; sorgu sayısı sabit (fatura + yevmiye satırı)."""
    tarih = tarih or tr_bugun()
    fqs = (Fatura.objects.filter(silindi=False, durum=Fatura.Durum.ONAYLI, fis__isnull=False, tarih__lte=tarih, cari__silindi=False)
           .exclude(cari__muhasebe_kodu="").select_related("cari__kategori"))
    if kategori_id:
        fqs = fqs.filter(cari__kategori_id=kategori_id)
    cari_faturalari: dict = {}
    for f in fqs:
        cari_faturalari.setdefault(f.cari_id, (f.cari, {}))[1][f.fis_id] = f
    if not cari_faturalari:
        return []
    kodlar = {c.muhasebe_kodu for c, _f in cari_faturalari.values()}
    hesap_satirlari: dict = {}
    for s in (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, fis__tarih__lte=tarih, hesap_id__in=kodlar)
              .values("hesap_id", "fis_id", "islem_pb", "borc", "alacak", "islem_tutari", "fis__kaynak")):
        hesap_satirlari.setdefault(s["hesap_id"], []).append(s)
    sonuc = []
    for cari, faturalar in cari_faturalari.values():
        kalemler, _kredi = fatura_odeme.fifo_kalemler(cari, faturalar, hesap_satirlari.get(cari.muhasebe_kodu, ()))      # FIFO çekirdeği tek yerde (fatura_odeme)
        sekme = sekme_of(cari)
        for k in kalemler.values():
            if k["kalan"] < ESIK:
                continue
            k["gecikme"] = (tarih - k["vade"]).days if k["vade"] and tarih > k["vade"] else 0
            k["dilim"] = dilim_anahtari(k["vade"], k["gecikme"])
            k["cari"], k["sekme"] = cari, sekme
            sonuc.append(k)
    return sonuc


def _bos_dilimler():
    return {k: SIFIR for k in DILIM_ANAHTARLARI}


# --------------------------------------------------------------------------------------------------- 1) yaşlandırma
def yaslandirma(tarih=None, sekme="alacak", pb="", kategori_id="") -> dict:
    """{"tarih", "sekme", "tablolar": [{"pb", "satirlar": [{cari, dilimler, toplam, adet}], "toplam": {dilimler + "toplam"}}], "pb_secenekleri", "sekme_adetleri"}.
    PB başına AYRI tablo; satırlar toplam kalana göre azalan."""
    tarih = tarih or tr_bugun()
    sekme = sekme if sekme in SEKME_ANAHTARLARI else "alacak"
    tum = acik_faturalar_toplu(tarih, kategori_id)
    sekme_adetleri = {k: sum(1 for s in tum if s["sekme"] == k) for k in SEKME_ANAHTARLARI}
    secili = [s for s in tum if s["sekme"] == sekme]
    pb_secenekleri = sorted({s["pb"] for s in secili}, key=_pb_sirasi)
    if pb:
        secili = [s for s in secili if s["pb"] == pb]
    tablolar = {}
    for s in secili:
        t = tablolar.setdefault(s["pb"], {})
        r = t.setdefault(s["cari"].pk, {"cari": s["cari"], "dilimler": _bos_dilimler(), "toplam": SIFIR, "adet": 0})
        r["dilimler"][s["dilim"]] += s["kalan"]
        r["toplam"] += s["kalan"]
        r["adet"] += 1
    cikti = []
    for p in sorted(tablolar, key=_pb_sirasi):
        satirlar = sorted(tablolar[p].values(), key=lambda r: (-r["toplam"], tr_anahtar(r["cari"].unvan)))
        toplam = _bos_dilimler()
        for r in satirlar:
            for k in DILIM_ANAHTARLARI:
                toplam[k] += r["dilimler"][k]
        toplam["toplam"] = sum((r["toplam"] for r in satirlar), SIFIR)
        toplam["adet"] = sum(r["adet"] for r in satirlar)
        for r in satirlar + [{"dilimler": toplam}]:
            r["hucreler"] = [r["dilimler"][k] for k in DILIM_ANAHTARLARI]          # şablon için sıralı dilim değerleri
        toplam["hucreler"] = [toplam[k] for k in DILIM_ANAHTARLARI]
        cikti.append({"pb": p, "satirlar": satirlar, "toplam": toplam})
    return {"tarih": tarih, "sekme": sekme, "tablolar": cikti, "pb_secenekleri": pb_secenekleri, "sekme_adetleri": sekme_adetleri,
            "fatura_adedi": sum(t["toplam"]["adet"] for t in cikti)}


# --------------------------------------------------------------------------------------------------- 2) vadesi geçenler
def vadesi_gecenler(tarih=None, sekme="", pb="", kategori_id="") -> dict:
    """Tarih itibarıyla vadesi geçmiş (gecikme > 0) açık faturalar: gecikme günü azalan, sonra kalan azalan. ``sekme`` boş = ikisi birden."""
    tarih = tarih or tr_bugun()
    satirlar = [s for s in acik_faturalar_toplu(tarih, kategori_id) if s["gecikme"] > 0]
    pb_secenekleri = sorted({s["pb"] for s in satirlar}, key=_pb_sirasi)
    if sekme in SEKME_ANAHTARLARI:
        satirlar = [s for s in satirlar if s["sekme"] == sekme]
    if pb:
        satirlar = [s for s in satirlar if s["pb"] == pb]
    satirlar.sort(key=lambda s: (-s["gecikme"], -s["kalan"], s["fatura"].pk))
    toplam = {}
    for s in satirlar:
        toplam[s["pb"]] = toplam.get(s["pb"], SIFIR) + s["kalan"]
    return {"tarih": tarih, "satirlar": satirlar, "toplam": sorted(toplam.items(), key=lambda x: _pb_sirasi(x[0])), "pb_secenekleri": pb_secenekleri}


# --------------------------------------------------------------------------------------------------- 3) tarih itibarıyla bakiye
def bakiye_listesi(tarih=None, kategori_id="", tur="", bakiyesizleri_gizle=True) -> dict:
    """Seçilen tarihte (fiş tarihi ≤ tarih) tüm cari bakiyeleri: TL net + döviz havuzları. Pozitif = BORÇ (cari bize borçlu), negatif = ALACAK.
    Toplu: ``cari_ozet.bakiyeler(tarih=)`` (cari sayısından bağımsız sabit sorgu)."""
    tarih = tarih or tr_bugun()
    qs = Cari.objects.filter(silindi=False).exclude(muhasebe_kodu="").select_related("kategori__ust")
    if kategori_id:
        qs = qs.filter(kategori_id=kategori_id)
    if tur == "ticari":
        qs = qs.filter(kategori__ust__kod__in=TICARI_KOKLER)
    elif tur == "diger":
        qs = qs.exclude(kategori__ust__kod__in=TICARI_KOKLER)
    cariler = list(qs)
    bak = cari_ozet.bakiyeler(cariler, tarih=tarih)
    satirlar, dvz_toplam = [], {}
    tl_borc = tl_alacak = SIFIR
    for c in cariler:
        b = bak[c.pk]
        tl, dvz = b["tl"], sorted(b["doviz"].items())
        bakiyeli = abs(tl) >= ESIK or bool(dvz)
        if bakiyesizleri_gizle and not bakiyeli:
            continue
        satirlar.append({"cari": c, "tl": tl, "doviz": dvz, "doviz_metin": cari_ozet.doviz_satirlari(dvz), "son": b["son"]})
        if tl > 0:
            tl_borc += tl
        elif tl < 0:
            tl_alacak += -tl
        for pb, net in dvz:
            d = dvz_toplam.setdefault(pb, {"borc": SIFIR, "alacak": SIFIR})
            d["borc" if net > 0 else "alacak"] += abs(net)
    satirlar.sort(key=lambda s: tr_anahtar(s["cari"].unvan))
    return {"tarih": tarih, "satirlar": satirlar, "tl_borc": tl_borc, "tl_alacak": tl_alacak, "tl_net": tl_borc - tl_alacak,
            "doviz_toplam": sorted(((pb, d["borc"], d["alacak"], d["borc"] - d["alacak"]) for pb, d in dvz_toplam.items()), key=lambda x: _pb_sirasi(x[0]))}


# --------------------------------------------------------------------------------------------------- 4) cari ciro özeti
SIRALAMALAR = (("toplam", "Toplam ciro (satış + alış)"), ("satis", "Net satış"), ("alis", "Net alış"))
_SATIS = Q(fatura__tip__yon=FaturaTipi.Yon.SATIS) & ~Q(fatura__tip__maliyet_fisi=FaturaTipi.MaliyetFisi.ALIS_IADE)
_SATIS_IADE = Q(fatura__tip__yon=FaturaTipi.Yon.ALIS, fatura__tip__maliyet_fisi=FaturaTipi.MaliyetFisi.SATIS_IADE)
_ALIS = Q(fatura__tip__yon=FaturaTipi.Yon.ALIS) & ~Q(fatura__tip__maliyet_fisi=FaturaTipi.MaliyetFisi.SATIS_IADE)
_ALIS_IADE = Q(fatura__tip__yon=FaturaTipi.Yon.SATIS, fatura__tip__maliyet_fisi=FaturaTipi.MaliyetFisi.ALIS_IADE)


def ciro(baslangic, bitis, kategori_id="", tur="", sirala="toplam") -> dict:
    """Dönem içinde (fatura tarihi) cari bazında satış / alış: brüt, iade, net (KDV hariç TL) + belge sayısı. TEK gruplu SQL + cari satırları (cari sayısından bağımsız)."""
    sirala = sirala if sirala in {k for k, _ in SIRALAMALAR} else "toplam"
    tl = Round(Round(F("miktar") * F("birim_fiyat"), 2) * F("fatura__kur"), 2)        # = FaturaSatir.tutar_tl
    qs = (FaturaSatir.objects.filter(silindi=False, fatura__silindi=False, fatura__durum=Fatura.Durum.ONAYLI, fatura__cari__silindi=False,
                                     fatura__tarih__gte=baslangic, fatura__tarih__lte=bitis)
          .exclude(fatura__tip__yalniz_kdv=True))
    if kategori_id:
        qs = qs.filter(fatura__cari__kategori_id=kategori_id)
    if tur == "ticari":
        qs = qs.filter(fatura__cari__kategori__ust__kod__in=TICARI_KOKLER)
    elif tur == "diger":
        qs = qs.exclude(fatura__cari__kategori__ust__kod__in=TICARI_KOKLER)
    veri = list(qs.values("fatura__cari_id").annotate(
        satis=Sum(tl, filter=_SATIS), satis_iade=Sum(tl, filter=_SATIS_IADE), alis=Sum(tl, filter=_ALIS), alis_iade=Sum(tl, filter=_ALIS_IADE),
        satis_belge=Count("fatura_id", distinct=True, filter=_SATIS | _SATIS_IADE), alis_belge=Count("fatura_id", distinct=True, filter=_ALIS | _ALIS_IADE)))
    cariler = {c.pk: c for c in Cari.objects.filter(pk__in=[r["fatura__cari_id"] for r in veri]).select_related("kategori")}
    satirlar = []
    for r in veri:
        s = {k: (r[k] or SIFIR) for k in ("satis", "satis_iade", "alis", "alis_iade")}
        s.update(cari=cariler[r["fatura__cari_id"]], satis_belge=r["satis_belge"], alis_belge=r["alis_belge"])
        s["satis_net"], s["alis_net"] = s["satis"] - s["satis_iade"], s["alis"] - s["alis_iade"]
        s["toplam"] = s["satis_net"] + s["alis_net"]
        satirlar.append(s)
    anahtar = {"toplam": "toplam", "satis": "satis_net", "alis": "alis_net"}[sirala]
    satirlar.sort(key=lambda s: (-s[anahtar], tr_anahtar(s["cari"].unvan)))
    toplam = {k: sum((s[k] for s in satirlar), SIFIR) for k in ("satis", "satis_iade", "satis_net", "alis", "alis_iade", "alis_net", "toplam")}
    toplam.update(satis_belge=sum(s["satis_belge"] for s in satirlar), alis_belge=sum(s["alis_belge"] for s in satirlar))
    return {"baslangic": baslangic, "bitis": bitis, "satirlar": satirlar, "toplam": toplam, "sirala": sirala}
