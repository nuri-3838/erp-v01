"""BAYAT MALİYET BAYRAKLARI TEMİZLİĞİ (tek seferlik; bkz. core/management/commands/maliyet_bayrak_duzelt.py).

Girdi çıkışı GEÇİCİ→KESİN olduğunda (fasoncu tahakkuku, fiyat/fatura gelince; tutar aynı) motor eskiden sonraki üretimi yenilemiyordu: üretim girişlerinde ``giris_tahmini`` /
``maliyet_durumu`` bayat 'tahmini' işaretleri kaldı. Motor artık durum değişimini de yayıyor (stok_ortalama.yeniden_hesapla); bu komut GEÇMİŞTE kalmış bayatları temizler:
iki adım turlanır (bayrak kümesi duruncaya kadar): (1) her onaylı üretim kaydının çıktı girişi ``giris_tahmini`` bayrağı, girdi çıkışlarının güncel durumundan
(beklenen = girdi çıkışlarından biri KESİN değil VEYA faturasız/tahakkuksuz fason bedeli bekliyor) yazılır; (2) her kartın hareket DURUMLARI saf ``hesapla`` ile bellekte
hesaplanır, yalnız ``maliyet_durumu`` farkı yazılır. Tutar/fiş/kart önbelleğine HİÇ yazılmaz (tam yeniden hesap, USD önbelleğinde ±0,01 yuvarlama farkı üretiyordu —
canlı kuru çalıştırmada 83 hareket — bu yüzden kullanılmıyor).

YALNIZ bayraklar değişir. Tek atomik işlem; ``uygula=False`` ise GERİ ALINIR. Doğrulama (biri tutmazsa MaliyetBayrakHatasi, hiçbir şey kaydedilmez): hiçbir hareketin miktar/tutar/
giriş tutarı/sonrası alanı değişmedi, aktif fiş satırları (hesap/borç/alacak) ve fiş kümesi aynı, mizan aynı, kart miktar-değer-ortalama aynı, bayat bayrak kalmadı."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum

from core.models import OperasyonKaydi, Stok, StokHareket, YevmiyeFisi, YevmiyeSatir
from core.services import stok_ortalama
from core.services.fason_maliyet import fason_bekliyor

SIFIR = Decimal("0")
GIRIS, CIKIS, KESIN = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS, StokHareket.MaliyetDurumu.KESIN
EN_FAZLA_TUR = 24                   # zincir derinliği kadar tur (ham → kesilmiş → boyalı → birleştirme → paketsiz → mamul …); her tur bir seviye yayar


class MaliyetBayrakHatasi(ValueError):
    """Doğrulama başarısızlığı (Türkçe mesaj); işlem geri alınır."""


def duzelt(*, uygula=False) -> dict:
    """Tek transaction: bayrakları yenile → doğrula → rapor; ``uygula=False`` ise GERİ ALINIR."""
    with transaction.atomic():
        rapor = _isle()
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    return rapor


# ------------------------------------------------------------------------------------------------------------ görüntüler

def _sabit_alanlar():
    """Değişmemesi gereken hareket alanları: {pk: (...)}."""
    return {h.pk: (h.tur, h.stok_id, h.miktar, h.tarih, h.depo_id, h.tutar_try, h.tutar_usd, h.giris_tutar_try, h.giris_tutar_usd, h.birim_maliyet_try,
                   h.sonrasi_miktar, h.sonrasi_deger_try) for h in StokHareket.objects.filter(silindi=False)}


def _bayraklar():
    return {h.pk: (h.giris_tahmini, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False)}


def _fis_icerik():
    d = defaultdict(list)
    for fis_id, hesap_id, borc, alacak in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).values_list("fis_id", "hesap_id", "borc", "alacak"):
        d[fis_id].append((hesap_id, borc, alacak))
    return {k: sorted(v) for k, v in d.items()}


def _fis_yazimi_olcer():
    return {"satir_max": YevmiyeSatir.objects.aggregate(m=Max("pk"))["m"] or 0, "fis_max": YevmiyeFisi.objects.aggregate(m=Max("pk"))["m"] or 0,
            "guncel": {f.pk: f.updated_at for f in YevmiyeFisi.objects.all().only("pk", "updated_at")}}


def _mizan():
    return {r["hesap__hesap_kodu"]: (r["b"] or SIFIR, r["a"] or SIFIR)
            for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).values("hesap__hesap_kodu").annotate(b=Sum("borc"), a=Sum("alacak"))}


def _kartlar():
    return {s.pk: (s.maliyet_miktar, s.maliyet_deger_try, s.ort_maliyet_try) for s in Stok.objects.filter(silindi=False)}


def beklenen_bayraklar() -> list:
    """Onaylı üretim kayıtlarının çıktı girişleri için [(hareket pk, stok kodu, kayıt no, saklanan giris_tahmini, beklenen)]; beklenen = girdi çıkışlarından biri KESİN değil
    VEYA (fason bedeli faturasız/tahakkuksuz bekliyor ve bu çıktının fason bedeli > 0) — ``stok_fis.uretim_senkronla`` ile aynı kural."""
    sonuc = []
    kayitlar = (OperasyonKaydi.objects.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI)
                .select_related("fason_donus__fatura", "fason_donus__tahakkuk_fis").order_by("tarih", "pk"))
    for k in kayitlar:
        hs = list(StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).exclude(kaynak=StokHareket.Kaynak.DUZELTME).select_related("stok"))
        girdi_tahmini = any(h.maliyet_durumu != KESIN for h in hs if h.tur == CIKIS)
        bekliyor = fason_bekliyor(k) if k.fason_cari_id else False
        fason = {c.stok_id: (c.fason_tutar or SIFIR) for c in k.ciktilar.filter(silindi=False)} if k.fason_cari_id else {}
        for h in hs:
            if h.tur != GIRIS:
                continue
            beklenen = girdi_tahmini or (bekliyor and fason.get(h.stok_id, SIFIR) > 0)
            sonuc.append((h.pk, h.stok.kod, k.no, bool(h.giris_tahmini), beklenen))
    return sonuc


def bayat_bayraklar() -> list:
    """Bayrağı BEKLENENDEN farklı üretim girişleri (bkz. ``beklenen_bayraklar``): [(hareket pk, stok kodu, kayıt no, saklanan, beklenen)]."""
    return [x for x in beklenen_bayraklar() if x[3] != x[4]]


def _bayraklari_yaz() -> int:
    """Çıktı girişi ``giris_tahmini`` bayraklarını beklenene çeker (yalnız o alan; updated_at'e dokunmaz). Değişen sayısı."""
    bayat = bayat_bayraklar()
    for beklenen in (True, False):
        idler = [x[0] for x in bayat if x[4] is beklenen]
        if idler:
            StokHareket.objects.filter(pk__in=idler).update(giris_tahmini=beklenen)
    return len(bayat)


def _durumlari_yaz() -> int:
    """Her kartın hareket DURUMLARINI saf ``hesapla`` ile bellekte hesaplar; yalnız ``maliyet_durumu`` farkını yazar (tutar/önbellek yazılmaz). Değişen sayısı."""
    hedef = defaultdict(list)                                # yeni durum -> [hareket pk]
    for stok in Stok.objects.filter(silindi=False):
        hareketler = stok_ortalama.kart_hareketleri(stok)
        for _varyant, grup in stok_ortalama._varyant_gruplari(stok, hareketler):
            for h, s in zip(grup, stok_ortalama.hesapla(grup)):
                if h.maliyet_durumu != s.durum:
                    hedef[s.durum].append(h.pk)
    for durum, idler in hedef.items():
        StokHareket.objects.filter(pk__in=idler).update(maliyet_durumu=durum)
    return sum(len(v) for v in hedef.values())


# ------------------------------------------------------------------------------------------------------------ işlem

def _isle() -> dict:
    once_sabit, once_bayrak, once_fis, once_mizan, once_kart = _sabit_alanlar(), _bayraklar(), _fis_icerik(), _mizan(), _kartlar()
    olcer = _fis_yazimi_olcer()
    bayat_once = bayat_bayraklar()
    from core.services.stok_ortalama import degerleme_raporu
    deger_once = {k["kod"]: (k["toplam_deger"], k["gecici"], k["mizan"], k["fark"]) for k in degerleme_raporu()["karsilastirma"]}
    turlar, onceki = [], once_bayrak
    for tur in range(1, EN_FAZLA_TUR + 1):
        _bayraklari_yaz()                                   # 1) çıktı bayrağı ← girdi çıkışlarının güncel durumu
        _durumlari_yaz()                                    # 2) kart durumları ← bayraklar (saf hesapla; yalnız durum yazılır)
        simdiki = _bayraklar()
        turlar.append(sum(1 for pk, v in simdiki.items() if onceki.get(pk) != v))
        if simdiki == onceki:
            break
        onceki = simdiki
    sonra_bayrak = _bayraklar()
    bayat_sonra = bayat_bayraklar()
    sonra_sabit, sonra_fis, sonra_mizan, sonra_kart = _sabit_alanlar(), _fis_icerik(), _mizan(), _kartlar()
    deger_sonra = {k["kod"]: (k["toplam_deger"], k["gecici"], k["mizan"], k["fark"]) for k in degerleme_raporu()["karsilastirma"]}
    # --- fiş yazımı ölçümü (satır eklendi ya da fiş updated_at değişti) ---
    yazilan = set(YevmiyeSatir.objects.filter(pk__gt=olcer["satir_max"]).values_list("fis_id", flat=True))
    yazilan |= {f.pk for f in YevmiyeFisi.objects.all().only("pk", "updated_at") if f.pk in olcer["guncel"] and olcer["guncel"][f.pk] != f.updated_at}
    yeni_fis = YevmiyeFisi.objects.filter(pk__gt=olcer["fis_max"]).count()
    # --- bayrak değişimleri ---
    degisen = [(pk, once_bayrak[pk], sonra_bayrak[pk]) for pk in once_bayrak if pk in sonra_bayrak and once_bayrak[pk] != sonra_bayrak[pk]]
    hareket = {h.pk: h for h in StokHareket.objects.filter(pk__in=[d[0] for d in degisen]).select_related("stok")}
    tahmini_gecis = Counter((d[1][0], d[2][0]) for d in degisen if d[1][0] != d[2][0])
    durum_gecis = Counter((hareket[d[0]].tur, d[1][1], d[2][1]) for d in degisen if d[1][1] != d[2][1])
    kaynak_dagilim = Counter((hareket[d[0]].tur, hareket[d[0]].kaynak) for d in degisen)
    kart_dagilim = Counter(hareket[d[0]].stok.kod for d in degisen)
    rapor = {"turlar": turlar, "degisen": degisen, "tahmini_gecis": tahmini_gecis, "durum_gecis": durum_gecis, "kaynak_dagilim": kaynak_dagilim, "kart_dagilim": kart_dagilim,
             "bayat_once": bayat_once, "bayat_sonra": bayat_sonra, "fis_yazilan": sorted(yazilan), "yeni_fis": yeni_fis, "hareket_sayisi": len(once_sabit),
             "deger_once": deger_once, "deger_sonra": deger_sonra, "kontrol": []}
    kontrol = rapor["kontrol"]

    def kontrol_et(ad, tamam, ayrinti=""):
        kontrol.append((ad, bool(tamam), ayrinti))
        if not tamam:
            raise MaliyetBayrakHatasi(f"Doğrulama başarısız — {ad}: {ayrinti}")

    fark = [pk for pk in once_sabit if once_sabit[pk] != sonra_sabit.get(pk)]
    kontrol_et("hareket sayısı aynı", set(once_sabit) == set(sonra_sabit), f"{len(once_sabit)} → {len(sonra_sabit)}")
    kontrol_et("hiçbir hareketin miktar/tutar/giriş tutarı/sonrası değeri değişmedi", not fark, f"{len(fark)} hareket değişti: {fark[:10]}")
    kontrol_et("aktif fiş satırları (hesap/borç/alacak) ve fiş kümesi aynı", once_fis == sonra_fis,
               f"{len([k for k in set(once_fis) | set(sonra_fis) if once_fis.get(k) != sonra_fis.get(k)])} fiş farklı")
    kontrol_et("yeni fiş açılmadı", yeni_fis == 0, f"{yeni_fis}")
    kontrol_et("mizan (hesap bazında borç/alacak) aynı", once_mizan == sonra_mizan)
    kontrol_et("kart miktar/değer/ortalama aynı", once_kart == sonra_kart, f"{len([k for k in once_kart if once_kart[k] != sonra_kart.get(k)])} kart farklı")
    kontrol_et("bayat bayrak kalmadı", not bayat_sonra, f"{len(bayat_sonra)} kalan")
    kontrol_et("değerleme raporu (toplam değer / mizan) aynı", {k: (v[0], v[2]) for k, v in deger_once.items()} == {k: (v[0], v[2]) for k, v in deger_sonra.items()})
    return rapor


def rapor_metni(r: dict) -> str:
    def say(x):
        return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    s = [f"MALİYET BAYRAK DÜZELT — {'UYGULANDI' if r.get('uygulandi') else 'KURU ÇALIŞTIRMA (geri alındı)'}", ""]
    s.append(f"Turlar (her turda değişen bayrak sayısı): {r['turlar']}  → bayrak kümesi {'durdu' if r['turlar'] and r['turlar'][-1] == 0 else 'son turda hâlâ değişiyordu'}")
    s.append(f"Bayat bayrak: önce {len(r['bayat_once'])} → sonra {len(r['bayat_sonra'])} (beklenen bayraktan farklı üretim girişi; 0 olmalı)")
    s += ["", f"DÜZELEN HAREKET: {len(r['degisen'])} (toplam {r['hareket_sayisi']} aktif hareket)"]
    ad = lambda b: "tahmini" if b else "kesin"
    for (e, y), n in sorted(r["tahmini_gecis"].items()):
        s.append(f"  giris_tahmini {ad(e)} → {ad(y)}: {n}")
    for (tur, e, y), n in sorted(r["durum_gecis"].items()):
        s.append(f"  maliyet_durumu {tur} {e} → {y}: {n}")
    s.append("  tür/kaynak dağılımı: " + ", ".join(f"{t}/{k}: {n}" for (t, k), n in sorted(r["kaynak_dagilim"].items())))
    s.append(f"  etkilenen kart: {len(r['kart_dagilim'])} → " + ", ".join(f"{k} ({n})" for k, n in r["kart_dagilim"].most_common(12)) + (" …" if len(r["kart_dagilim"]) > 12 else ""))
    s += ["", "TUTAR / FİŞ / MİZAN EŞİTLİĞİ (hiçbirinin değişmemesi gerekir):",
          f"  fiş yazımı (satır eklenen ya da updated_at değişen fiş): {len(r['fis_yazilan'])}  (0 olmalı)" + (f" → {r['fis_yazilan'][:15]}" if r["fis_yazilan"] else ""),
          f"  yeni fiş: {r['yeni_fis']} (0 olmalı)", "", "DEĞERLEME (hesap: toplam değer | geçici | mizan | fark) önce → sonra:"]
    for kod in sorted(r["deger_once"]):
        o, n = r["deger_once"][kod], r["deger_sonra"][kod]
        s.append(f"  {kod}: {say(o[0])} | {say(o[1])} | {say(o[2])} | {say(o[3])}  →  {say(n[0])} | {say(n[1])} | {say(n[2])} | {say(n[3])}")
    s += ["", "DOĞRULAMALAR:"]
    for ad_, tamam, ayr in r["kontrol"]:
        s.append(f"  [{'OK' if tamam else 'HATA'}] {ad_}" + (f" — {ayr}" if ayr and not tamam else ""))
    return "\n".join(s)
