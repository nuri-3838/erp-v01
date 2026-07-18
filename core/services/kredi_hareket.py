"""KREDİ hareket motoru — her hareket = bir DENGELİ yevmiye fişi (kaynak=KREDI).

Kredi bir YÜKÜMLÜLÜK. Kullandırım: anapara nakit hesaba girer → Banka/Kasa borç / Kredi alacak
(borç doğar). Geri ödeme (Dilim 2): Kredi borç(anapara) + Faiz gideri borç(faiz) / nakit alacak.
Fiş kredinin para biriminde tek para; Banka/Kasa PB'si kredi ile aynı olmalı (çapraz kur kapsam dışı).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Max

from core.metin import buyuk_harf_tr
from core.models import BankaHesap, Kasa, Kredi, KrediTaksit, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_iptal, fis_olustur


class KrediHareketHatasi(ValueError):
    """Kredi hareketi kural ihlali (Türkçe mesaj)."""


# kredi = kredi satırının fiş tarafı. Kullandırım → kredi ALACAK (borç doğar).
HAREKET = {
    "kullandirim": {"ad": "Kullandırım", "kredi": "A", "karsi": ("banka", "kasa"), "yon": "artis",
                    "ikon": "💰", "ack": "KULLANDIRIM",
                    "aciklama": "Kredi kullanımı — anapara nakit hesaba girer (Banka·Kasa borç / "
                                "Kredi alacak). Borç doğar."},
}


def _kur_coz(pb, tarih):
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise KrediHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekmeden döviz kredi "
            f"hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KrediHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise KrediHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _nakit_coz(karsi, kredi):
    """Banka hesabı VEYA Kasa'dan muhasebe hesabı kodu + ad; PB kredi ile aynı olmalı."""
    if isinstance(karsi, BankaHesap):
        if karsi.muhasebe_id is None:
            raise KrediHareketHatasi("Banka hesabının muhasebe hesabı tanımlı değil.")
        if karsi.para_birimi != kredi.para_birimi:
            raise KrediHareketHatasi(
                f"Banka hesabı ({karsi.para_birimi}) ile kredi ({kredi.para_birimi}) para birimi "
                f"farklı; çapraz kurlu işlem desteklenmiyor.")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if isinstance(karsi, Kasa):
        if karsi.muhasebe_id is None:
            raise KrediHareketHatasi("Kasanın muhasebe hesabı tanımlı değil.")
        if karsi.para_birimi != kredi.para_birimi:
            raise KrediHareketHatasi(
                f"Kasa ({karsi.para_birimi}) ile kredi ({kredi.para_birimi}) para birimi farklı; "
                f"çapraz kurlu işlem desteklenmiyor.")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise KrediHareketHatasi("Nakit hesabı Banka VEYA Kasa olmalı.")


@transaction.atomic
def hareket_olustur(*, kredi, tip, karsi, tutar, tarih, aciklama="", kullanici=None) -> YevmiyeFisi:
    """Bir kredi hareketinden otomatik DENGELİ yevmiye fişi üretir (kaynak=KREDI, fiş→kredi FK).
    Dilim 1: kullandırım (kredi alacak / nakit borç). Kural ihlalinde hiçbir şey kaydedilmez."""
    if tip not in HAREKET:
        raise KrediHareketHatasi("Geçersiz hareket tipi.")
    if kredi.muhasebe_id is None:
        raise KrediHareketHatasi("Kredinin muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    karsi_kod, karsi_ad = _nakit_coz(karsi, kredi)
    tut = _tutar(tutar)
    pb = kredi.para_birimi
    kur = _kur_coz(pb, tarih)
    kredi_taraf = tan["kredi"]
    karsi_taraf = "A" if kredi_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KREDİ {tan['ack']} - {karsi_ad}"))
    satirlar = [
        SatirGirdi(hesap_kodu=kredi.muhasebe.hesap_kodu, taraf=kredi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
    ]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.KREDI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KrediHareketHatasi(str(e))
    fis.kredi = kredi
    fis.save(update_fields=["kredi", "updated_at"])
    return fis


def _tutar0(deger, ad):
    """0 dahil negatif olmayan tutar (faiz için; boş → 0)."""
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KrediHareketHatasi(f"{ad} geçerli bir sayı olmalı.")
    if d < 0:
        raise KrediHareketHatasi(f"{ad} negatif olamaz.")
    return d


@transaction.atomic
def geri_odeme_olustur(*, kredi, karsi, anapara, faiz=0, faiz_hesap=None, tarih,
                       aciklama="", kullanici=None) -> YevmiyeFisi:
    """Geri Ödeme (taksit): anapara + faiz ELLE girilir (amortisman planı üretilmez — bilinçli
    karar). Kredi BORÇ (anapara, borç kapanır) + Faiz gideri BORÇ (faiz>0 ise; yaprak gider
    hesabı zorunlu) / nakit (Banka·Kasa) ALACAK (toplam). Dövizde nakit satırı tl_override ile
    borç satırlarının yuvarlanmış TL toplamına denklenir (dengeli fiş invariant'ı)."""
    if kredi.muhasebe_id is None:
        raise KrediHareketHatasi("Kredinin muhasebe hesabı tanımlı değil.")
    karsi_kod, karsi_ad = _nakit_coz(karsi, kredi)
    ana = _tutar(anapara)
    fz = _tutar0(faiz, "Faiz")
    pb = kredi.para_birimi
    kur = _kur_coz(pb, tarih)
    girdiler = [SatirGirdi(hesap_kodu=kredi.muhasebe.hesap_kodu, taraf="B",
                           islem_tutari=ana, islem_pb=pb, islem_kuru=kur)]
    borc_tl = yuvarla(ana * kur, 2)
    if fz > 0:
        if faiz_hesap is None:
            raise KrediHareketHatasi("Faiz girildiyse faiz gider hesabı seçilmeli.")
        from core.services.finans import FinansHatasi, _yaprak_hesap_coz
        try:
            fh = _yaprak_hesap_coz(getattr(faiz_hesap, "hesap_kodu", faiz_hesap))
        except FinansHatasi as e:
            raise KrediHareketHatasi(str(e))
        if fh.pk == kredi.muhasebe_id:
            raise KrediHareketHatasi("Faiz gider hesabı kredinin kendi hesabı olamaz.")
        girdiler.append(SatirGirdi(hesap_kodu=fh.hesap_kodu, taraf="B",
                                   islem_tutari=fz, islem_pb=pb, islem_kuru=kur))
        borc_tl += yuvarla(fz * kur, 2)
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KREDİ GERİ ÖDEME - {karsi_ad}"))
    # Nakit DENGE satırı: toplam işlem tutarı, TL'si borç satırları toplamı (döviz yuvarlaması).
    girdiler.append(SatirGirdi(hesap_kodu=karsi_kod, taraf="A", islem_tutari=ana + fz,
                               islem_pb=pb, islem_kuru=kur, tl_override=borc_tl))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.KREDI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KrediHareketHatasi(str(e))
    fis.kredi = kredi
    fis.save(update_fields=["kredi", "updated_at"])
    return fis


@transaction.atomic
def hareket_iptal(*, fis, kredi, kullanici=None):
    """Kredi hareketi (kaynak=KREDI) iptali → bağlı fişi soft-delete eder; fiş bir ödeme fişiyse
    ödediği taksitler BEKLİYOR'a döner. Fiş bu kredinin bir hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KREDI or fis.kredi_id != kredi.pk:
        raise KrediHareketHatasi("Bu fiş bu kredinin hareketi değil.")
    if fis.silindi:
        return fis
    for t in KrediTaksit.objects.filter(odeme_fisi=fis, silindi=False):
        t.durum = KrediTaksit.Durum.BEKLIYOR
        t.odeme_fisi = None
        t.updated_by = kullanici
        t.save(update_fields=["durum", "odeme_fisi", "updated_by", "updated_at"])
    return fis_iptal(fis, kullanici=kullanici)


# ===== Elle geri ödeme planı + taksit seçip ödeme =====
@transaction.atomic
def taksit_plani_ekle(*, kredi, satirlar, kullanici=None):
    """Elle plana taksit(ler) ekler. satirlar: [{vade, anapara, faiz}]. Sıra mevcut en yüksekten
    devam eder; her satır: vade zorunlu, anapara>0, faiz>=0. En az bir dolu satır gerekir."""
    mevcut = kredi.taksitler.filter(silindi=False).aggregate(m=Max("sira"))["m"] or 0
    olusan = []
    for r in satirlar:
        vade = r.get("vade")
        if not vade:
            raise KrediHareketHatasi("Her taksit için vade zorunlu.")
        anapara = _tutar(r.get("anapara"))
        faiz = _tutar0(r.get("faiz"), "Faiz")
        mevcut += 1
        olusan.append(KrediTaksit.objects.create(
            kredi=kredi, sira=mevcut, vade=vade, anapara=anapara, faiz=faiz,
            durum=KrediTaksit.Durum.BEKLIYOR, created_by=kullanici, updated_by=kullanici))
    if not olusan:
        raise KrediHareketHatasi("En az bir taksit girilmeli.")
    return olusan


def taksit_sil(taksit, kullanici=None):
    """Bekleyen taksiti sil (soft). Ödenmiş taksit silinemez (önce ödemeyi iptal et)."""
    if taksit.durum == KrediTaksit.Durum.ODENDI:
        raise KrediHareketHatasi("Ödenmiş taksit silinemez; önce ödeme fişini iptal edin.")
    if taksit.silindi:
        return taksit
    taksit.silindi = True
    taksit.updated_by = kullanici
    taksit.save(update_fields=["silindi", "updated_by", "updated_at"])
    return taksit


def _taksit_secim_coz(kredi, taksit_ids):
    try:
        ids = [int(x) for x in taksit_ids if str(x).strip()]
    except (TypeError, ValueError):
        raise KrediHareketHatasi("Geçersiz taksit seçimi.")
    if not ids:
        raise KrediHareketHatasi("En az bir taksit seçilmeli.")
    taksitler = list(KrediTaksit.objects.filter(pk__in=set(ids), kredi=kredi, silindi=False))
    if len(taksitler) != len(set(ids)):
        raise KrediHareketHatasi("Seçilen taksitlerden bazıları bulunamadı.")
    for t in taksitler:
        if t.durum != KrediTaksit.Durum.BEKLIYOR:
            raise KrediHareketHatasi(
                f"{t.sira}. taksit zaten ödenmiş; yalnız bekleyen taksitler seçilebilir.")
    return taksitler


@transaction.atomic
def taksitleri_ode(*, kredi, taksit_ids, karsi, faiz_hesap=None, tarih, aciklama="",
                   kullanici=None) -> YevmiyeFisi:
    """Seçilen BEKLİYOR taksitleri TEK geri ödeme fişiyle öder: Σanapara Kredi borç + Σfaiz Faiz
    gideri borç / nakit (Banka·Kasa) alacak Σ(anapara+faiz). Taksitler ODENDI + fişe bağlanır."""
    taksitler = _taksit_secim_coz(kredi, taksit_ids)
    toplam_ana = sum((t.anapara for t in taksitler), Decimal("0"))
    toplam_faiz = sum((t.faiz for t in taksitler), Decimal("0"))
    fis = geri_odeme_olustur(kredi=kredi, karsi=karsi, anapara=toplam_ana, faiz=toplam_faiz,
                             faiz_hesap=faiz_hesap, tarih=tarih, aciklama=aciklama,
                             kullanici=kullanici)
    for t in taksitler:
        t.durum = KrediTaksit.Durum.ODENDI
        t.odeme_fisi = fis
        t.updated_by = kullanici
        t.save(update_fields=["durum", "odeme_fisi", "updated_by", "updated_at"])
    return fis


def taksit_ozet(kredi):
    """Kredi detayı için: taksitler + bekleyen/ödenen toplam ve bekleyen sayısı."""
    taksitler = list(kredi.taksitler.filter(silindi=False)
                     .select_related("odeme_fisi").order_by("sira", "id"))
    bekleyen = sum((t.toplam for t in taksitler if t.durum == KrediTaksit.Durum.BEKLIYOR),
                   Decimal("0"))
    odenen = sum((t.toplam for t in taksitler if t.durum == KrediTaksit.Durum.ODENDI),
                 Decimal("0"))
    return {"taksitler": taksitler, "bekleyen": bekleyen, "odenen": odenen,
            "bekleyen_sayi": sum(1 for t in taksitler
                                 if t.durum == KrediTaksit.Durum.BEKLIYOR)}
