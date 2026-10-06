"""KREDİ KARTI — taksit planı + DÖNEM EKSTRESİ + nakit akışı.

Kart muhasebesi DEĞİŞMEZ: harcama 309.xx'e TAM tutarla tek fiş olarak yazılır (borç anında gerçek). Taksit planı (``KrediKartiTaksit``) yalnız ÖDEME TAKVİMİ
içindir; her taksit, kartın bir EKSTRE DÖNEMİne (hesap kesim tarihine) ve o ekstrenin SON ÖDEME tarihine bağlanır.

Dönem kuralları (kartta ``kesim_gunu`` ve ``son_odeme_gunu`` tanımlı olmalı):
  * Dönem = (önceki kesim, kesim]  →  kesim GÜNÜNDEKİ harcama O ekstreye DÂHİLDİR; ertesi gün sonraki ekstreye düşer.
  * Son ödeme = kesimden sonraki ilk ``son_odeme_gunu`` günü (kısa aylarda ay sonuna çekilir); hafta sonu/resmî tatile denk gelirse sonraki iş günü.
  * Harcama ilk ekstre dönemine (varsayılan: harcamanın düştüğü dönem; ``kaydirma`` ile sonraki dönemler) girer; sonraki taksitler birer ay sonraki ekstrelere.
  * Eşit taksit; kartın kurus_farki ayarı: SON (varsayılan) = taksitler kuruşa yuvarlanır, fark son taksitte; İLK = taksitler kuruş kesilerek hesaplanır, artan
    kuruş ilk taksitte. Plan satırları her seferinde bu ayara göre HESAPLANIR; ayar değişince mevcut planlar da yeniden hesaplanır — fiş/309 değişmez.
Dönem borcu (kesimdeki ekstre bakiyesi) = Σ tek çekimler(≤kesim) + Σ taksitler(kesim ≤ kesim) − Σ kart ödemeleri/iadeleri(≤kesim). 309 defter borcu =
bu tutar + kesimden sonraki tek çekim/taksitler − kesimden sonraki ödemeler (uyum tablosu ekranda). Bu modül yalnız okur / plan kaydeder; fiş yazmaz.
"""
from __future__ import annotations

import calendar
import datetime
import re
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from core.models import KrediKarti, KrediKartiTaksit, ResmiTatil, YevmiyeSatir
from core.sayi import yuvarla

SIFIR = Decimal("0.00")
TAKSIT_DESENI = re.compile(r"(\d+)\s*TAKS[İI]T")


class KkDonemHatasi(ValueError):
    """Kredi kartı dönem/taksit kural ihlali (Türkçe mesaj)."""


# --- takvim yardımcıları --------------------------------------------------------------------------------------------------
def gunler_tanimli(kart) -> bool:
    return bool(kart.kesim_gunu and kart.son_odeme_gunu)


def _gun(yil, ay, gun):
    return datetime.date(yil, ay, min(gun, calendar.monthrange(yil, ay)[1]))


def _ay_kaydir(yil, ay, n):
    t = yil * 12 + (ay - 1) + n
    return t // 12, t % 12 + 1


def kesim_tarihi(kart, yil, ay):
    return _gun(yil, ay, kart.kesim_gunu)


def donem_kesimi(kart, d):
    """``d`` tarihinin düştüğü ekstrenin kesim tarihi (kesim GÜNÜ dâhil: d == kesim → o ekstre)."""
    k = kesim_tarihi(kart, d.year, d.month)
    if k >= d:
        return k
    y, m = _ay_kaydir(d.year, d.month, 1)
    return kesim_tarihi(kart, y, m)


def kesim_ekle(kart, c, n):
    """Kesim tarihi ``c``'den ``n`` ay sonraki (önceki) kesim tarihi."""
    y, m = _ay_kaydir(c.year, c.month, n)
    return kesim_tarihi(kart, y, m)


def son_odeme_nominal(kart, c):
    t = _gun(c.year, c.month, kart.son_odeme_gunu)
    if t <= c:
        y, m = _ay_kaydir(c.year, c.month, 1)
        t = _gun(y, m, kart.son_odeme_gunu)
    return t


def vade(kart, c):
    """Kesim ``c`` ekstresinin son ödeme tarihi: nominal gün, hafta sonu/resmî tatile denk gelirse sonraki iş günü."""
    t = son_odeme_nominal(kart, c)
    tatiller = set(ResmiTatil.objects.filter(silindi=False, tarih__gte=t, tarih__lte=t + datetime.timedelta(days=14)).values_list("tarih", flat=True))
    while t.weekday() >= 5 or t in tatiller:
        t += datetime.timedelta(days=1)
    return t


def ilk_vade_onerisi(kart, harcama_tarihi, kaydirma=0):
    """Harcamanın düştüğü ekstre (+ ``kaydirma`` ay sonraki) için ilk taksitin SON ÖDEME tarihi."""
    if not gunler_tanimli(kart):
        raise KkDonemHatasi(f"{kart.ad} kartında hesap kesim günü ve son ödeme günü tanımlı değil.")
    return vade(kart, kesim_ekle(kart, donem_kesimi(kart, harcama_tarihi), int(kaydirma or 0)))


def bolme(toplam, adet, ilk=False):
    """Eşit taksitler. ``ilk=False`` (varsayılan, "Son taksite"): taksitler kuruşa YUVARLANIR, bölünemeyen fark SON taksite eklenir. ``ilk=True`` ("İlk taksite"):
    taksitler kuruş KESİLEREK (aşağı) hesaplanır, artan kuruş İLK taksite eklenir (bazı bankaların ekstre usulü)."""
    if ilk:
        her = (toplam / adet).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return [toplam - her * (adet - 1), *([her] * (adet - 1))]
    her = yuvarla(toplam / adet, 2)
    kalan = [her] * (adet - 1)
    return [*kalan, toplam - sum(kalan, SIFIR)]


# --- taksit planı -----------------------------------------------------------------------------------------------------------
def ilk_kesim_plan(kart, plan):
    """Planın 1. taksitinin ekstre kesim tarihi: saklı ``ilk_vade`` (ilk taksitin son ödeme tarihi) en yakın ekstreye eşlenir."""
    en = None
    for ofs in range(-3, 3):
        y, m = _ay_kaydir(plan.ilk_vade.year, plan.ilk_vade.month, ofs)
        c = kesim_tarihi(kart, y, m)
        fark = abs((vade(kart, c) - plan.ilk_vade).days)
        if en is None or fark < en[0]:
            en = (fark, c)
    return en[1]


def plan_satirlari(kart, plan):
    """[{'sira', 'kesim', 'vade', 'tutar'}] — kartta kesim+son ödeme günü varsa ekstre/son ödeme günlerinden; yoksa eski aylık takvim (kesim=None)."""
    tut = bolme(plan.toplam_tutar, plan.taksit_adedi, ilk=getattr(kart, "kurus_farki", "SON") == "ILK")
    out = []
    if gunler_tanimli(kart):
        c0 = ilk_kesim_plan(kart, plan)
        for k in range(plan.taksit_adedi):
            c = kesim_ekle(kart, c0, k)
            out.append({"sira": k + 1, "kesim": c, "vade": vade(kart, c), "tutar": tut[k]})
    else:
        for k in range(plan.taksit_adedi):
            y, m = _ay_kaydir(plan.ilk_vade.year, plan.ilk_vade.month, k)
            out.append({"sira": k + 1, "kesim": None, "vade": _gun(y, m, plan.ilk_vade.day), "tutar": tut[k]})
    return out


def _kart_alacagi(fis, kart):
    return sum((s.alacak for s in fis.satirlar.filter(silindi=False, hesap_id=kart.muhasebe_id, ana_satir__isnull=True)), SIFIR)


def aktif_plan(fis):
    return fis.taksit_planlari.filter(silindi=False).first()


@transaction.atomic
def plan_ayarla(*, fis, kart, adet, ilk_vade=None, kaydirma=0, kullanici=None):
    """Harcama fişinin taksit planını kurar/günceller/siler. ``adet`` ≤ 1 → plan silinir (pasifleşir). ``ilk_vade`` verilmezse kartın kesim/son ödeme
    günlerinden (harcamanın düştüğü ekstre + ``kaydirma``) hesaplanır. Fişe/muhasebeye DOKUNMAZ."""
    from core.models import YevmiyeFisi
    if fis.kaynak != YevmiyeFisi.Kaynak.KREDI_KARTI or fis.kredi_karti_id != kart.pk or fis.silindi:
        raise KkDonemHatasi("Bu fiş bu kartın bir harcaması değil.")
    adet = int(adet or 1)
    mevcut = aktif_plan(fis)
    if adet <= 1:
        if mevcut:
            mevcut.silindi, mevcut.silindi_at, mevcut.updated_by = True, timezone.now(), kullanici
            mevcut.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        return None
    if not (2 <= adet <= 60):
        raise KkDonemHatasi("Taksit sayısı 1 ile 60 arasında olmalı.")
    if kart.para_birimi != "TRY":
        raise KkDonemHatasi("Taksit planı yalnız TL kartlarda tutulur.")
    toplam = _kart_alacagi(fis, kart)
    if toplam <= 0:
        raise KkDonemHatasi("Taksit planı yalnız harcamalarda (kart ALACAK) kurulabilir.")
    if ilk_vade is None:
        if not gunler_tanimli(kart):
            raise KkDonemHatasi("Kartta hesap kesim günü ve son ödeme günü tanımlı değilse ilk taksit tarihini girin.")
        ilk_vade = ilk_vade_onerisi(kart, fis.tarih, kaydirma)
    if mevcut:
        mevcut.taksit_adedi, mevcut.ilk_vade, mevcut.toplam_tutar, mevcut.updated_by = adet, ilk_vade, toplam, kullanici
        mevcut.save(update_fields=["taksit_adedi", "ilk_vade", "toplam_tutar", "updated_by", "updated_at"])
        return mevcut
    return KrediKartiTaksit.objects.create(kart=kart, fis=fis, taksit_adedi=adet, ilk_vade=ilk_vade, toplam_tutar=toplam,
                                           para_birimi=kart.para_birimi, created_by=kullanici, updated_by=kullanici)


def mevcut_kaydirma(kart, fis, plan):
    """Düzenleme ekranı için: planın 1. taksiti, harcamanın düştüğü ekstreden kaç ay sonra (0..3; eşleşmezse 0)."""
    if plan is None or not gunler_tanimli(kart):
        return 0
    c0, c = donem_kesimi(kart, fis.tarih), ilk_kesim_plan(kart, plan)
    n = (c.year - c0.year) * 12 + (c.month - c0.month)
    return n if 0 <= n <= 3 else 0


# --- ekstre verisi ----------------------------------------------------------------------------------------------------------
def _veri(kart):
    """Kartın tek çekimleri (plansız harcama), taksit satırları ve ödeme/iadeleri (309 borç)."""
    planlar = {p.fis_id: p for p in KrediKartiTaksit.objects.filter(kart=kart, silindi=False, fis__silindi=False).select_related("fis")}
    cekim, odeme = [], []
    for s in (YevmiyeSatir.objects.filter(hesap_id=kart.muhasebe_id, silindi=False, fis__silindi=False, ana_satir__isnull=True)
              .select_related("fis").order_by("fis__tarih", "id")):
        ack = s.fis.aciklama or ""
        if s.alacak:
            if s.fis_id not in planlar:
                cekim.append({"tarih": s.fis.tarih, "tutar": s.alacak, "fis_pk": s.fis_id, "fis_no": f"{s.fis.yil}/{s.fis.fis_no}", "aciklama": ack})
        elif s.borc:
            odeme.append({"tarih": s.fis.tarih, "tutar": s.borc, "fis_pk": s.fis_id, "fis_no": f"{s.fis.yil}/{s.fis.fis_no}", "aciklama": ack})
    taksit = []
    for p in planlar.values():
        for r in plan_satirlari(kart, p):
            if r["kesim"] is not None:
                taksit.append({**r, "adet": p.taksit_adedi, "fis_pk": p.fis_id, "fis_no": f"{p.fis.yil}/{p.fis.fis_no}", "aciklama": p.fis.aciklama or "",
                               "harcama_tarihi": p.fis.tarih})
    taksit.sort(key=lambda x: (x["kesim"], x["fis_pk"], x["sira"]))
    return {"cekim": cekim, "taksit": taksit, "odeme": odeme}


def _kapanis(v, c):
    return (sum((x["tutar"] for x in v["cekim"] if x["tarih"] <= c), SIFIR)
            + sum((x["tutar"] for x in v["taksit"] if x["kesim"] <= c), SIFIR)
            - sum((x["tutar"] for x in v["odeme"] if x["tarih"] <= c), SIFIR))


def kesim_secenekleri(kart, bugun, geri=12, ileri=6):
    """Seçilebilir kesim tarihleri (eskiden yeniye): bugünün ekstresinden ``geri`` ay öncesi ... ``ileri`` ay sonrası."""
    c = donem_kesimi(kart, bugun)
    return [kesim_ekle(kart, c, n) for n in range(-geri, ileri + 1)]


def son_kesilmis_kesim(kart, bugun):
    c = donem_kesimi(kart, bugun)
    return c if c <= bugun else kesim_ekle(kart, c, -1)


def donem_ekstresi(kart, c, bugun=None):
    """``c`` kesim tarihli ekstre: devir + dönem hareketleri → dönem borcu, son ödeme tarihi, kesimden sonraki ödemeler ve 309 uyum bileşenleri."""
    if kart.para_birimi != "TRY":
        raise KkDonemHatasi("Dönem ekstresi yalnız TL kartlar için hesaplanır.")
    if not gunler_tanimli(kart):
        raise KkDonemHatasi("Kartta hesap kesim günü ve son ödeme günü tanımlı olmalı (Kart Düzenle).")
    bugun = bugun or datetime.date.today()
    p = kesim_ekle(kart, c, -1)
    v = _veri(kart)
    devir = _kapanis(v, p)
    d_cekim = [x for x in v["cekim"] if p < x["tarih"] <= c]
    d_taksit = [x for x in v["taksit"] if x["kesim"] == c]
    d_odeme = [x for x in v["odeme"] if p < x["tarih"] <= c]
    t_cekim = sum((x["tutar"] for x in d_cekim), SIFIR)
    t_taksit = sum((x["tutar"] for x in d_taksit), SIFIR)
    t_odeme = sum((x["tutar"] for x in d_odeme), SIFIR)
    borc = devir + t_cekim + t_taksit - t_odeme
    son_odeme = vade(kart, c)
    sonra_odeme = [x for x in v["odeme"] if c < x["tarih"] <= son_odeme]
    t_sonra = sum((x["tutar"] for x in sonra_odeme), SIFIR)
    sonraki = {
        "cekim": sum((x["tutar"] for x in v["cekim"] if x["tarih"] > c), SIFIR),
        "taksit": sum((x["tutar"] for x in v["taksit"] if x["kesim"] > c), SIFIR),
        "odeme": sum((x["tutar"] for x in v["odeme"] if x["tarih"] > c), SIFIR),
    }
    defter = (sum((x["tutar"] for x in v["cekim"]), SIFIR) + sum((x["tutar"] for x in v["taksit"]), SIFIR)
              - sum((x["tutar"] for x in v["odeme"]), SIFIR))
    return {"kart": kart, "kesim": c, "onceki_kesim": p, "son_odeme": son_odeme, "devir": devir, "cekimler": d_cekim, "taksitler": d_taksit,
            "odemeler": d_odeme, "t_cekim": t_cekim, "t_taksit": t_taksit, "t_odeme": t_odeme, "donem_borcu": borc,
            "sonra_odemeler": sonra_odeme, "t_sonra_odeme": t_sonra, "kalan": borc - t_sonra, "sonraki": sonraki, "defter_borcu": defter,
            "uyum": borc + sonraki["cekim"] + sonraki["taksit"] - sonraki["odeme"], "kesilmis": c <= bugun}


# --- nakit akışı ------------------------------------------------------------------------------------------------------------
def nakit_akisi(bugun=None, ay_sayisi=12):
    """Tüm TL kartların gelecek son ödeme tarihleri ve tutarları: kesilmiş (ödenmemiş kalan) ekstre + açık dönem + gelecek taksit dönemleri; ay bazında özet."""
    bugun = bugun or datetime.date.today()
    satirlar, atlanan = [], []
    for kart in KrediKarti.objects.filter(silindi=False).order_by("ad"):
        if kart.para_birimi != "TRY":
            continue
        if not gunler_tanimli(kart):
            atlanan.append(kart)
            continue
        v = _veri(kart)
        c_kapali = son_kesilmis_kesim(kart, bugun)
        c_acik = kesim_ekle(kart, c_kapali, 1)
        odenen = sum((x["tutar"] for x in v["odeme"] if c_kapali < x["tarih"] <= bugun), SIFIR)
        kalan_kapali = _kapanis(v, c_kapali) - odenen
        if kalan_kapali > 0:
            satirlar.append({"kart": kart, "kesim": c_kapali, "vade": vade(kart, c_kapali), "tutar": kalan_kapali, "tur": "Kesilmiş ekstre"})
        fazla = -kalan_kapali if kalan_kapali < 0 else SIFIR
        acik = (sum((x["tutar"] for x in v["cekim"] if c_kapali < x["tarih"] <= c_acik), SIFIR)
                + sum((x["tutar"] for x in v["taksit"] if x["kesim"] == c_acik), SIFIR) - fazla)
        if acik > 0:
            satirlar.append({"kart": kart, "kesim": c_acik, "vade": vade(kart, c_acik), "tutar": acik, "tur": "Açık dönem"})
        for n in range(1, ay_sayisi):
            c = kesim_ekle(kart, c_acik, n)
            t = sum((x["tutar"] for x in v["taksit"] if x["kesim"] == c), SIFIR)
            if t > 0:
                satirlar.append({"kart": kart, "kesim": c, "vade": vade(kart, c), "tutar": t, "tur": "Taksit"})
    satirlar.sort(key=lambda r: (r["vade"], r["kart"].ad))
    aylar = {}
    for r in satirlar:
        a = aylar.setdefault((r["vade"].year, r["vade"].month), {"yil": r["vade"].year, "ay": r["vade"].month, "kartlar": {}, "toplam": SIFIR})
        a["kartlar"][r["kart"].ad] = a["kartlar"].get(r["kart"].ad, SIFIR) + r["tutar"]
        a["toplam"] += r["tutar"]
    return {"satirlar": satirlar, "aylar": [aylar[k] for k in sorted(aylar)], "atlanan": atlanan,
            "toplam": sum((r["tutar"] for r in satirlar), SIFIR)}


# --- mevcut harcamalar için toplu taksit önerisi ----------------------------------------------------------------------------
def oneriler():
    """Açıklamasında 'N TAKSİT' geçen, taksit planı OLMAYAN TL kart harcamaları: [{'fis', 'kart', 'adet', 'tutar', 'ilk_vade'|None, 'neden'}]."""
    from core.models import YevmiyeFisi
    out = []
    qs = (YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.KREDI_KARTI, silindi=False, kredi_karti__isnull=False)
          .select_related("kredi_karti").order_by("tarih", "id"))
    for f in qs:
        m = TAKSIT_DESENI.search(f.aciklama or "")
        if not m or aktif_plan(f):
            continue
        adet = int(m.group(1))
        kart = f.kredi_karti
        if not (2 <= adet <= 60) or kart.para_birimi != "TRY":
            continue
        tutar = _kart_alacagi(f, kart)
        if tutar <= 0:
            continue                                            # harcama değil (ödeme/iade)
        ilk, neden = None, ""
        if gunler_tanimli(kart):
            ilk = ilk_vade_onerisi(kart, f.tarih, 0)
        else:
            neden = "Kartta kesim/son ödeme günü tanımlı değil"
        out.append({"fis": f, "kart": kart, "adet": adet, "tutar": tutar, "ilk_vade": ilk, "neden": neden})
    return out


@transaction.atomic
def oneri_uygula(fis_pkleri, *, kullanici=None):
    """Seçilen önerileri uygular (plan kaydeder; fiş/muhasebe değişmez). Uygulanan plan sayısını döner; uygun olmayan seçim hata verir."""
    secili = {int(x) for x in fis_pkleri}
    n = 0
    for o in oneriler():
        if o["fis"].pk not in secili:
            continue
        if o["ilk_vade"] is None:
            raise KkDonemHatasi(f"{o['fis'].yil}/{o['fis'].fis_no}: {o['neden']}.")
        plan_ayarla(fis=o["fis"], kart=o["kart"], adet=o["adet"], ilk_vade=o["ilk_vade"], kullanici=kullanici)
        n += 1
    if n != len(secili):
        raise KkDonemHatasi("Seçimdeki bazı harcamalar artık öneri listesinde değil; sayfayı yenileyin.")
    return n
