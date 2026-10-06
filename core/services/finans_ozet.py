"""FİNANS ÖZETİ (dashboard): kasa/banka bakiyeleri, kredi kartı borç-limit, kredi kalan borç, çek/senet portföyü ve yaklaşan vadeler.

Bakiyeler muhasebe hesaplarından HESAPLANIR (saklanmaz; bkz. raporlar._devir). Döviz hesaplarda bakiye hesabın kendi para biriminde,
TL karşılığı iki biçimde verilir: DEFTER değeri (ortalama maliyet, yevmiyedeki TL) ve GÜNCEL değer (son TCMB alış kuru). Toplamlar güncel
kurla (kur yoksa defter değeriyle) TL'dir. Bu modül yalnız okur; hiçbir kaydı değiştirmez.
"""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal

from core.models import BankaHesap, CekSenet, Kasa, Kredi, KrediKarti, Kur
from core.services import raporlar

SIFIR = Decimal("0.00")
YAKIN_GUN = 30
DOVIZLER = ("USD", "EUR", "GBP")


def _bakiye(hesap_kodu):
    """(TL defter bakiyesi [borç−alacak], {para birimi: döviz bakiyesi [borç−alacak]})."""
    return raporlar._devir(hesap_kodu, datetime.date(2100, 1, 1))


def _guncel_kurlar():
    """{pb: son TCMB döviz alış kuru} ve kur tarihi."""
    k = Kur.objects.filter(silindi=False).order_by("-tarih").first()
    if k is None:
        return {}, None
    return {pb: v for pb in DOVIZLER if (v := getattr(k, f"{pb.lower()}_alis", None))}, k.tarih


def _tl(tutar, pb, kurlar):
    if pb == "TRY":
        return tutar
    kur = kurlar.get(pb)
    return (tutar * kur).quantize(SIFIR) if kur else None


def _hesap_satiri(ad, alt, pb, kod, kurlar):
    tl_defter, dvz = _bakiye(kod)
    if pb == "TRY":
        bakiye, defter, guncel = tl_defter, tl_defter, tl_defter
    else:
        bakiye = dvz.get(pb, SIFIR)
        defter = tl_defter
        guncel = _tl(bakiye, pb, kurlar)
        if guncel is None:
            guncel = defter
    return {"ad": ad, "alt": alt, "pb": pb, "kod": kod, "bakiye": bakiye, "defter": defter, "guncel": guncel}


def _sonraki_tarih(gun, bugun):
    """Ayın ``gun``. gününe göre bugünden sonraki (bugün dahil) ilk tarih (kısa aylarda ay sonuna çekilir)."""
    if not gun:
        return None
    for ek in (0, 1):
        ay = bugun.month - 1 + ek
        yil, ay = bugun.year + ay // 12, ay % 12 + 1
        t = datetime.date(yil, ay, min(gun, calendar.monthrange(yil, ay)[1]))
        if t >= bugun:
            return t
    return None


def ozet(bugun=None):
    bugun = bugun or datetime.date.today()
    kurlar, kur_tarihi = _guncel_kurlar()

    # --- Kasalar ve banka hesapları -------------------------------------------------------------------------------------
    kasalar = [_hesap_satiri(k.ad, "Kasa", k.para_birimi, k.muhasebe_id, kurlar)
               for k in Kasa.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")]
    banka = [_hesap_satiri(h.banka.kisa_ad or h.banka.ad, h.ad, h.para_birimi, h.muhasebe_id, kurlar)
             for h in BankaHesap.objects.filter(silindi=False).select_related("banka", "muhasebe").order_by("banka__ad", "ad")
             if h.muhasebe_id]
    nakit = [*kasalar, *banka]
    for r in nakit:
        r["bos"] = r["bakiye"] == 0 and r["defter"] == 0          # bakiyesi sıfır hesaplar tabloda varsayılan gizli
    pb_toplam = {}
    for r in nakit:
        t = pb_toplam.setdefault(r["pb"], {"pb": r["pb"], "bakiye": SIFIR, "defter": SIFIR, "guncel": SIFIR, "adet": 0})
        t["bakiye"] += r["bakiye"]
        t["defter"] += r["defter"]
        t["guncel"] += r["guncel"]
        t["adet"] += 1
    pb_toplam = sorted(pb_toplam.values(), key=lambda t: (t["pb"] != "TRY", t["pb"]))
    nakit_tl = sum((t["guncel"] for t in pb_toplam), SIFIR)

    # --- Kredi kartları ---------------------------------------------------------------------------------------------------
    kartlar = []
    for k in KrediKarti.objects.filter(silindi=False).select_related("banka").order_by("ad"):
        tl_defter, dvz = _bakiye(k.muhasebe_id)
        net = tl_defter if k.para_birimi == "TRY" else dvz.get(k.para_birimi, SIFIR)
        borc = -net if net < 0 else SIFIR                       # yükümlülük: alacak yönlü bakiye = borç
        kartlar.append({
            "ad": k.ad, "banka": (k.banka.kisa_ad or k.banka.ad) if k.banka else "", "son4": k.kart_son4, "pb": k.para_birimi,
            "limit": k.limit, "borc": borc, "kullanilabilir": k.limit - borc,
            "doluluk": int(min(100, (borc / k.limit * 100))) if k.limit else None,
            "kesim": k.kesim_gunu, "son_odeme": _sonraki_tarih(k.son_odeme_gunu, bugun), "pk": k.pk})
    kart_borc_tl = sum((_tl(r["borc"], r["pb"], kurlar) or r["borc"] for r in kartlar), SIFIR)

    # --- Krediler -------------------------------------------------------------------------------------------------------
    krediler = []
    for k in Kredi.objects.filter(silindi=False).select_related("banka").order_by("ad"):
        tl_defter, dvz = _bakiye(k.muhasebe_id)
        net = tl_defter if k.para_birimi == "TRY" else dvz.get(k.para_birimi, SIFIR)
        kalan = -net if net < 0 else SIFIR
        krediler.append({"ad": k.ad, "banka": (k.banka.kisa_ad or k.banka.ad) if k.banka else "", "pb": k.para_birimi,
                         "anapara": k.anapara, "kalan": kalan, "pk": k.pk})
    kredi_tl = sum((_tl(r["kalan"], r["pb"], kurlar) or r["kalan"] for r in krediler), SIFIR)

    # --- Çek / Senet ---------------------------------------------------------------------------------------------------
    D = CekSenet.Durum
    Y = CekSenet.Yon
    def _grup(qs):
        out = {}
        for c in qs:
            g = out.setdefault(c.para_birimi, {"pb": c.para_birimi, "adet": 0, "tutar": SIFIR})
            g["adet"] += 1
            g["tutar"] += c.tutar
        return sorted(out.values(), key=lambda g: (g["pb"] != "TRY", g["pb"]))

    def _tl_toplam(gruplar):
        return sum(((_tl(g["tutar"], g["pb"], kurlar) or g["tutar"]) for g in gruplar), SIFIR)

    cekler = CekSenet.objects.filter(silindi=False)
    alinan_acik = cekler.filter(yon=Y.ALINAN, durum__in=[D.PORTFOYDE, D.TAHSILDE, D.TEMINATTA])
    verilen_acik = cekler.filter(yon=Y.VERILEN, durum=D.VERILDI)
    yakin_son = bugun + datetime.timedelta(days=YAKIN_GUN)
    cek = {
        "portfoy": _grup(alinan_acik.filter(durum=D.PORTFOYDE)),
        "tahsilde": _grup(alinan_acik.filter(durum=D.TAHSILDE)),
        "teminatta": _grup(alinan_acik.filter(durum=D.TEMINATTA)),
        "alinan_vadesi_gecmis": _grup(alinan_acik.filter(vade__lt=bugun)),
        "alinan_yakin": _grup(alinan_acik.filter(vade__gte=bugun, vade__lte=yakin_son)),
        "verilen": _grup(verilen_acik),
        "verilen_vadesi_gecmis": _grup(verilen_acik.filter(vade__lt=bugun)),
        "verilen_yakin": _grup(verilen_acik.filter(vade__gte=bugun, vade__lte=yakin_son)),
        "karsiliksiz": _grup(cekler.filter(durum=D.KARSILIKSIZ)),
    }
    alinan_tl, verilen_tl = _tl_toplam(_grup(alinan_acik)), _tl_toplam(cek["verilen"])
    yaklasan = list(cekler.filter(durum__in=[D.PORTFOYDE, D.TAHSILDE, D.TEMINATTA, D.VERILDI], vade__lte=yakin_son)
                    .select_related("cari").order_by("vade", "id")[:15])

    return {
        "bugun": bugun, "kur_tarihi": kur_tarihi, "kurlar": kurlar,
        "kasalar": kasalar, "banka": banka, "pb_toplam": pb_toplam, "nakit_tl": nakit_tl, "bos_hesap": sum(1 for r in nakit if r["bos"]),
        "kartlar": kartlar, "kart_borc_tl": kart_borc_tl, "kart_limit_tl": sum((r["limit"] for r in kartlar if r["pb"] == "TRY"), SIFIR),
        "krediler": krediler, "kredi_tl": kredi_tl,
        "cek": cek, "cek_alinan_tl": alinan_tl, "cek_verilen_tl": verilen_tl, "yaklasan": yaklasan, "yakin_gun": YAKIN_GUN,
        "net_pozisyon": nakit_tl + alinan_tl - kart_borc_tl - kredi_tl - verilen_tl,
    }
