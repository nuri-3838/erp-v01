"""DÖNEMSEL GİDER (180 Gelecek Aylara Ait Giderler) — alış gider faturası satırı.

Satır türü "Dönemsel gider": başlangıç/bitiş tarihi + gider hesabı (ör. 730.03) + 180 grubu (180.01 KİRA gibi). Fatura satırı 180 altında
faturaya özel açılan 180.01.000N hesabına (adı = fatura no + açıklama) yazılır; KDV varsa 191'e normal.
Aylık dağıtım: dönem tam aylardır (başlangıç ayın 1'i, bitiş ayın son günü); her ay sonu tarihli otomatik fiş
180.xx ALACAK / gider hesabı BORÇ (kaynak=DONEMSEL). Aylık tutar = toplam / ay sayısı (2 hane); son ay yuvarlama farkını alır → 180 dönem
sonunda tam 0. Döviz faturada: aylık döviz = toplam döviz / ay (2 hane), TL = aylık döviz × fatura kuru (son ay: toplam TL − öncekiler);
180 satırı döviz olarak (fatura kuruyla) yazılır. Fişler yalnız "bu tarihe kadar" vadesi gelen aylar için üretilir (gelecek ay üretilmez);
üretim idempotenttir. Fatura düzenlenince plan + fişler yeniden hesaplanır (daha önce üretilen aylar yeniden üretilir), silinince silinir.
"""
from __future__ import annotations

import calendar
import datetime
import re
from decimal import Decimal

from django.db import transaction

from core.models import DonemselDagitim, Fatura, HesapPlani, YevmiyeFisi
from core.sayi import yuvarla
from core.services import duran_hesap
from core.services import hesap_plani as hp
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

SIFIR = Decimal("0.00")
GRUP_180 = re.compile(r"^180\.\d{2}$")
HESAP_180 = re.compile(r"^180\.\d{2}\.\d{4}$")


class DonemselGiderHatasi(ValueError):
    """Dönemsel gider kuralı ihlali (Türkçe mesaj)."""


def ay_sonlari(baslangic, bitis):
    """[ay sonu tarihleri] — dönem tam aylar olmalı (başlangıç ayın 1'i, bitiş ayın son günü)."""
    if baslangic is None or bitis is None:
        raise DonemselGiderHatasi("Dönemsel gider için başlangıç ve bitiş tarihi girilmeli.")
    if baslangic.day != 1:
        raise DonemselGiderHatasi("Dönem başlangıcı bir ayın 1'i olmalı (tam ay).")
    if bitis.day != calendar.monthrange(bitis.year, bitis.month)[1]:
        raise DonemselGiderHatasi("Dönem bitişi bir ayın son günü olmalı (tam ay).")
    n = (bitis.year - baslangic.year) * 12 + bitis.month - baslangic.month + 1
    if n < 1:
        raise DonemselGiderHatasi("Dönem bitişi başlangıçtan önce olamaz.")
    if n > 120:
        raise DonemselGiderHatasi("Dönem en fazla 120 ay olabilir.")
    out = []
    y, m = baslangic.year, baslangic.month
    for _ in range(n):
        out.append(datetime.date(y, m, calendar.monthrange(y, m)[1]))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def plan(toplam_doviz, kur, aylar):
    """[(ay_sonu, doviz, tl)] — aylık döviz 2 haneye yuvarlanır, son ay farkı alır; TL = aylık döviz × kur (son ay: toplam TL − öncekiler)."""
    n = len(aylar)
    toplam = yuvarla(Decimal(toplam_doviz), 2)
    toplam_tl = yuvarla(toplam * kur, 2)
    aylik = yuvarla(toplam / n, 2)
    out, kalan_d, kalan_tl = [], toplam, toplam_tl
    for i, ay in enumerate(aylar):
        if i == n - 1:
            d, t = kalan_d, kalan_tl
        else:
            d, t = aylik, yuvarla(aylik * kur, 2)
        out.append((ay, d, t))
        kalan_d -= d
        kalan_tl -= t
    return out


def grup_hesaplari():
    """Seçilebilir 180 grupları (180.01 KİRA …)."""
    return HesapPlani.objects.filter(silindi=False, aktif=True, hesap_kodu__regex=GRUP_180.pattern).order_by("hesap_kodu")


def hesap_ac(grup_kodu, fatura_no, aciklama, *, kullanici=None):
    """Grubun altında faturaya özel sıradaki 000N hesabını açar (adı = fatura no + açıklama)."""
    if not GRUP_180.match(grup_kodu or "") or not HesapPlani.objects.filter(
            hesap_kodu=grup_kodu, silindi=False, aktif=True).exists():
        raise DonemselGiderHatasi("Geçerli bir 180 grubu seçin (ör. 180.01).")
    ad = f"{(fatura_no or '').strip() or 'FATURASIZ'} {(aciklama or '').strip()}".strip()[:200]
    try:
        return hp.hesap_olustur(kod=duran_hesap.sonraki_hesap_kodu(grup_kodu), ad=ad, ust_kodu=grup_kodu, kullanici=kullanici)
    except hp.HesapHatasi as e:
        raise DonemselGiderHatasi(str(e))


# ----------------------------------------------------------------------------------------------- plan senkronu
def _satirlar(fatura):
    return list(fatura.satirlar.filter(silindi=False, donem_baslangic__isnull=False).select_related("hesap", "donem_gider"))


def _fis_sil(rows):
    """Dağıtım satırlarının fişlerini siler (bağ önce çözülür; fiş kalıcı silinir)."""
    from core.services.fis_sil import fis_kalici_sil
    fisler = []
    for r in rows:
        if r.fis_id:
            fisler.append(r.fis)
            r.fis = None
            r.save(update_fields=["fis", "updated_at"])
    for f in fisler:
        fis_kalici_sil(f)


def _usd_kuru(tarih):
    from core.services.kur_degerleme import _usd_kuru
    return _usd_kuru(tarih)


def _fis_uret(r, kullanici=None):
    f = r.fatura
    ack = f"DÖNEMSEL GİDER {r.ay_sonu:%m.%Y} — {r.hesap.hesap_adi}"[:500]
    pb = r.para_birimi
    if pb == "TRY":
        k180 = SatirGirdi(hesap_kodu=r.hesap_id, taraf="A", islem_tutari=r.tl, aciklama=r.aciklama or r.hesap.hesap_adi)
    else:
        k180 = SatirGirdi(hesap_kodu=r.hesap_id, taraf="A", islem_tutari=r.doviz, islem_pb=pb, islem_kuru=r.kur,
                          tl_override=r.tl, aciklama=r.aciklama or r.hesap.hesap_adi)
    gider = SatirGirdi(hesap_kodu=r.gider_hesap_id, taraf="B", islem_tutari=r.tl, aciklama=r.aciklama or r.hesap.hesap_adi)
    try:
        fis = fis_olustur(tarih=r.ay_sonu, satirlar=[gider, k180], aciklama=ack, kur_usd=_usd_kuru(r.ay_sonu),
                          kaynak=YevmiyeFisi.Kaynak.DONEMSEL, kullanici=kullanici)
    except (YevmiyeHatasi, ValueError) as e:
        raise DonemselGiderHatasi(f"{r.hesap_id} {r.ay_sonu:%m.%Y}: {e}")
    r.fis = fis
    r.save(update_fields=["fis", "updated_at"])
    return fis


@transaction.atomic
def senkronla(fatura, kullanici=None):
    """Faturanın dönemsel satırlarından planı kurar/günceller. Yalnız ONAYLI fatura (kur belli). Plan değişmişse eski fişler silinir,
    yeni plan yazılır ve DAHA ÖNCE üretilmiş aylar yeniden üretilir; satırı kalkan 180 hesaplarının dağıtımı silinir."""
    if fatura.silindi or fatura.durum != Fatura.Durum.ONAYLI:
        return
    canli = {s.hesap_id: s for s in _satirlar(fatura)}
    mevcut = {}
    for r in DonemselDagitim.objects.filter(fatura=fatura, silindi=False).select_related("hesap", "fis"):
        mevcut.setdefault(r.hesap_id, []).append(r)
    for hesap_id, rows in list(mevcut.items()):
        if hesap_id not in canli:
            _fis_sil(rows)
            DonemselDagitim.objects.filter(pk__in=[r.pk for r in rows]).delete()
    for hesap_id, s in canli.items():
        aylar = ay_sonlari(s.donem_baslangic, s.donem_bitis)
        planlanan = plan(s.tutar, fatura.kur, aylar)
        rows = sorted(mevcut.get(hesap_id, []), key=lambda r: r.sira)
        ayni = ([(r.ay_sonu, r.doviz, r.tl, r.gider_hesap_id, r.para_birimi, r.kur) for r in rows]
                == [(a, d, t, s.donem_gider_id, fatura.para_birimi, fatura.kur) for a, d, t in planlanan])
        if ayni:
            continue
        uretilen = [r.ay_sonu for r in rows if r.fis_id]
        eski_max = max(uretilen) if uretilen else None
        _fis_sil(rows)
        DonemselDagitim.objects.filter(pk__in=[r.pk for r in rows]).delete()
        yeni = []
        for i, (ay, d, t) in enumerate(planlanan, start=1):
            yeni.append(DonemselDagitim.objects.create(
                fatura=fatura, hesap_id=hesap_id, gider_hesap_id=s.donem_gider_id, sira=i, ay_sonu=ay, doviz=d,
                para_birimi=fatura.para_birimi, kur=fatura.kur, tl=t, aciklama=s.donem_aciklama,
                created_by=kullanici, updated_by=kullanici))
        if eski_max:
            for r in yeni:
                if r.ay_sonu <= eski_max:
                    _fis_uret(r, kullanici)


@transaction.atomic
def fatura_temizle(fatura):
    """Fatura silinirken: tüm dağıtım fişleri + plan silinir (180 hesabı geçmiş için kalır)."""
    rows = list(DonemselDagitim.objects.filter(fatura=fatura).select_related("fis"))
    _fis_sil(rows)
    DonemselDagitim.objects.filter(pk__in=[r.pk for r in rows]).delete()


@transaction.atomic
def uret(tarih, kullanici=None):
    """Vadesi gelen (ay sonu <= tarih) ve henüz fişi olmayan dağıtımlar için fiş üretir; üretilen sayıyı döner. İdempotent.
    Yalnız ONAYLI, silinmemiş faturalar; gelecek aylar için fiş ÜRETİLMEZ."""
    sayi = 0
    bekleyen = (DonemselDagitim.objects.filter(silindi=False, fis__isnull=True, ay_sonu__lte=tarih,
                                               fatura__silindi=False, fatura__durum=Fatura.Durum.ONAYLI)
                .select_related("fatura", "hesap").order_by("ay_sonu", "fatura_id", "hesap_id", "sira"))
    for r in bekleyen:
        _fis_uret(r, kullanici)
        sayi += 1
    return sayi


def bekleyen_sayisi(tarih):
    return DonemselDagitim.objects.filter(silindi=False, fis__isnull=True, ay_sonu__lte=tarih, fatura__silindi=False,
                                          fatura__durum=Fatura.Durum.ONAYLI).count()


def fatura_tablolari(fatura):
    """Fatura detayı için: 180 hesabı başına [{hesap, aciklama, satirlar: [{ay_sonu, doviz, tl, fis, kalan_doviz, kalan_tl}]}]."""
    out = {}
    for r in (DonemselDagitim.objects.filter(fatura=fatura, silindi=False).select_related("hesap", "fis")
              .order_by("hesap_id", "sira")):
        g = out.setdefault(r.hesap_id, {"hesap": r.hesap, "aciklama": r.aciklama, "pb": r.para_birimi,
                                        "satirlar": [], "toplam_doviz": SIFIR, "toplam_tl": SIFIR})
        g["toplam_doviz"] += r.doviz
        g["toplam_tl"] += r.tl
        g["satirlar"].append(r)
    for g in out.values():
        kd, kt = g["toplam_doviz"], g["toplam_tl"]
        for r in g["satirlar"]:
            kd -= r.doviz
            kt -= r.tl
            r.kalan_doviz, r.kalan_tl = kd, kt
    return list(out.values())
