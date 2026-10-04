"""KREDİ KARTI hareket motoru — her hareket = bir DENGELİ yevmiye fişi (kaynak=KREDI_KARTI).

Kredi kartı bir YÜKÜMLÜLÜK: Harcama borcu ARTIRIR (kart alacak), Ödeme/İade AZALTIR (kart borç).
Karşı taraf: Harcama/İade → Cari VEYA Gider (yaprak hesap); Ödeme → Banka VEYA Kasa.
Fiş kartın para biriminde tek para; Banka/Kasa PB'si kartla aynı olmalı (çapraz kur kapsam dışı).
"""
from __future__ import annotations

import calendar
import datetime
from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (BankaHesap, Cari, HesapPlani, Kasa, KrediKarti, KrediKartiTaksit,
                         Kur, YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur


class KrediKartiHareketHatasi(ValueError):
    """Kredi kartı hareketi kural ihlali (Türkçe mesaj)."""


# kk = kredi kartı satırının fiş tarafı. Harcama → kart ALACAK (borç artar);
# Ödeme/İade → kart BORÇ (borç azalır). karsi = kabul edilen karşı taraf türleri.
HAREKET = {
    "harcama": {"ad": "Harcama", "kk": "A", "karsi": ("cari", "gider"), "yon": "artis",
                "ikon": "🛒", "ack": "HARCAMA",
                "aciklama": "Kartla harcama — karşı (Cari/Gider) borç / Kredi kartı alacak. Borç artar."},
    "odeme": {"ad": "Kart Ödeme", "kk": "B", "karsi": ("banka", "kasa"), "yon": "azalis",
              "ikon": "💸", "ack": "ÖDEME",
              "aciklama": "Kart borcu ödeme — Kredi kartı borç / Banka·Kasa alacak. Borç azalır."},
    "iade": {"ad": "Harcama İade", "kk": "B", "karsi": ("cari", "gider"), "yon": "azalis",
             "ikon": "↩️", "ack": "İADE",
             "aciklama": "Harcama iadesi — Kredi kartı borç / karşı (Cari/Gider) alacak. Borç azalır."},
}


def _kur_coz(pb, tarih, cari=None):
    """Kartın para biriminin fiş tarihindeki TCMB kuru — karşı taraf bir Cari'yse onun
    kur_tipi tercihine göre (bkz. Kur.deger), değilse MB Alış. TRY -> 1."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    kur_tipi = cari.kur_tipi if cari else Cari.KurTipi.MB_ALIS
    deger = k.deger(pb, kur_tipi) if k else None
    if not deger:
        raise KrediKartiHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekmeden döviz kartı "
            f"hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KrediKartiHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise KrediKartiHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _cari_hesap(cari):
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise KrediKartiHareketHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return hesap


def _nakit_pb_kontrol(karsi, kart, ad):
    if karsi.muhasebe_id is None:
        raise KrediKartiHareketHatasi(f"{ad} muhasebe hesabı tanımlı değil.")
    if karsi.para_birimi != kart.para_birimi:
        raise KrediKartiHareketHatasi(
            f"{ad} ({karsi.para_birimi}) ile kart ({kart.para_birimi}) para birimi farklı; "
            f"çapraz kurlu işlem desteklenmiyor.")


def _karsi_coz(tip, kart, karsi):
    """Karşı taraf nesnesinden (Cari / HesapPlani[gider] / BankaHesap / Kasa) muhasebe hesabı
    kodu + ad döner; türü hareketin izin verdiğiyle uyumlu olmalı. Banka/Kasa'da PB kartla aynı."""
    izin = HAREKET[tip]["karsi"]
    if isinstance(karsi, Cari):
        if "cari" not in izin:
            raise KrediKartiHareketHatasi("Bu hareket için cari seçilemez.")
        return _cari_hesap(karsi).hesap_kodu, karsi.unvan
    if isinstance(karsi, HesapPlani):
        if "gider" not in izin:
            raise KrediKartiHareketHatasi("Bu hareket için gider hesabı seçilemez.")
        from core.services.finans import FinansHatasi, _yaprak_hesap_coz
        try:
            hesap = _yaprak_hesap_coz(karsi.hesap_kodu)
        except FinansHatasi as e:
            raise KrediKartiHareketHatasi(str(e))
        if kart.muhasebe_id == hesap.pk:
            raise KrediKartiHareketHatasi("Karşı hesap kartın kendi hesabı olamaz.")
        return hesap.hesap_kodu, hesap.hesap_adi
    if isinstance(karsi, BankaHesap):
        if "banka" not in izin:
            raise KrediKartiHareketHatasi("Bu hareket için banka hesabı seçilemez.")
        _nakit_pb_kontrol(karsi, kart, "Banka hesabının")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if isinstance(karsi, Kasa):
        if "kasa" not in izin:
            raise KrediKartiHareketHatasi("Bu hareket için kasa seçilemez.")
        _nakit_pb_kontrol(karsi, kart, "Kasanın")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise KrediKartiHareketHatasi("Geçersiz karşı taraf.")


def _proje_coz(karsi_kod, yatirim_projesi_id):
    """Karşı hesap 258 ailesindeyse yatırım projesi ZORUNLU (DEVAM); değilse proje verilemez.
    Doğrulanmış proje id'sini (ya da None) döner."""
    from core.services.hesap_plani import hesap_kodu_258_mi
    if hesap_kodu_258_mi(karsi_kod):
        if not yatirim_projesi_id:
            raise KrediKartiHareketHatasi("258 hesabı için yatırım projesi seçilmelidir.")
        from core.models import YatirimProjesi
        if not YatirimProjesi.objects.filter(pk=yatirim_projesi_id, silindi=False,
                                             durum=YatirimProjesi.Durum.DEVAM).exists():
            raise KrediKartiHareketHatasi("Yatırım projesi bulunamadı ya da 'Devam Ediyor' durumunda değil.")
        return int(yatirim_projesi_id)
    if yatirim_projesi_id:
        raise KrediKartiHareketHatasi("Yatırım projesi yalnız 258 hesabında seçilebilir.")
    return None


@transaction.atomic
def hareket_olustur(*, kart, tip, karsi, tutar, tarih, aciklama="", kullanici=None,
                    kur_override=None, yatirim_projesi_id=None) -> YevmiyeFisi:
    """Bir kredi kartı hareketinden otomatik DENGELİ yevmiye fişi üretir (kaynak=KREDI_KARTI,
    fiş→kart FK). Kart satırı tan['kk'] tarafına, karşı ters tarafa; ikisi de kartın PB'sinde.
    Kural ihlalinde hiçbir şey kaydedilmez (transaction geri alınır). ``kur_override`` doluysa
    (kullanıcı formda elle girdi/değiştirdi) carinin kur_tipi'ne göre otomatik hesaplama
    YERİNE doğrudan kullanılır."""
    if tip not in HAREKET:
        raise KrediKartiHareketHatasi("Geçersiz hareket tipi.")
    if kart.muhasebe_id is None:
        raise KrediKartiHareketHatasi("Kartın muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    karsi_kod, karsi_ad = _karsi_coz(tip, kart, karsi)
    proje_id = _proje_coz(karsi_kod, yatirim_projesi_id)
    tut = _tutar(tutar)
    pb = kart.para_birimi
    if pb == "TRY":
        kur = Decimal("1")
    else:
        cari_karsi = karsi if isinstance(karsi, Cari) else None
        kur = kur_override or _kur_coz(pb, tarih, cari=cari_karsi)
    kk_taraf = tan["kk"]
    karsi_taraf = "A" if kk_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"KREDİ KARTI {tan['ack']} - {karsi_ad}"))
    satirlar = [
        SatirGirdi(hesap_kodu=kart.muhasebe.hesap_kodu, taraf=kk_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur),
        SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur, yatirim_projesi_id=proje_id),
    ]
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.KREDI_KARTI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KrediKartiHareketHatasi(str(e))
    fis.kredi_karti = kart
    fis.save(update_fields=["kredi_karti", "updated_at"])
    return fis


@transaction.atomic
def hareket_sil(*, fis, kart, kullanici=None):
    """Kredi kartı hareketini (kaynak=KREDI_KARTI) KALICI siler: fiş + satırlar + varsa taksit planı
    (yalnız süper kullanıcı, denetim kaydı yazılır, bağlı kayıt varsa reddedilir). Fiş bu kartın
    hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KREDI_KARTI or fis.kredi_karti_id != kart.pk:
        raise KrediKartiHareketHatasi("Bu fiş bu kartın hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.KREDI_KARTI})
    except SilmeHatasi as e:
        raise KrediKartiHareketHatasi(str(e))


def _ay_ekle(tarih, n):
    """tarih + n ay (yıl taşması + gün kırpma: 31 Oca + 1 ay → 28/29 Şub)."""
    ay = tarih.month - 1 + n
    yil = tarih.year + ay // 12
    ay = ay % 12 + 1
    gun = min(tarih.day, calendar.monthrange(yil, ay)[1])
    return datetime.date(yil, ay, gun)


@transaction.atomic
def harcama_olustur(*, kart, karsi, tutar, tarih, taksit_adedi=1, ilk_vade=None,
                    aciklama="", kullanici=None, kur_override=None, yatirim_projesi_id=None) -> YevmiyeFisi:
    """Harcama (peşin ya da taksitli). Muhasebe HER ZAMAN tam tutar tek fiş (borç anında gerçek);
    taksit_adedi>1 ise ayrıca BİLGİ amaçlı KrediKartiTaksit planı oluşur (ledger'ı etkilemez)."""
    fis = hareket_olustur(kart=kart, tip="harcama", karsi=karsi, tutar=tutar, tarih=tarih,
                          aciklama=aciklama, kullanici=kullanici, kur_override=kur_override,
                          yatirim_projesi_id=yatirim_projesi_id)
    adet = int(taksit_adedi or 1)
    if adet > 1:
        if not ilk_vade:
            raise KrediKartiHareketHatasi("Taksitli harcamada ilk taksit tarihi zorunlu.")
        KrediKartiTaksit.objects.create(
            kart=kart, fis=fis, taksit_adedi=adet, ilk_vade=ilk_vade,
            toplam_tutar=_tutar(tutar), para_birimi=kart.para_birimi,
            created_by=kullanici, updated_by=kullanici)
    return fis


def taksit_takvimi(plan):
    """plan → [{sira, vade, tutar}] eşit taksit; son taksit yuvarlama farkını üstlenir."""
    adet = plan.taksit_adedi
    her = yuvarla(plan.toplam_tutar / adet, 2)
    satirlar, biriken = [], Decimal("0")
    for k in range(adet):
        tutar = (plan.toplam_tutar - biriken) if k == adet - 1 else her
        if k != adet - 1:
            biriken += her
        satirlar.append({"sira": k + 1, "vade": _ay_ekle(plan.ilk_vade, k), "tutar": tutar})
    return satirlar


def kart_taksit_takvimi(kart):
    """Kartın AKTİF taksit planlarının tüm taksitleri, vade artan. Her satır: vade, tutar, sira,
    adet, fis, pb, aciklama. Kart detayında 'Taksit Takvimi' bölümü için."""
    hepsi = []
    for p in (KrediKartiTaksit.objects.filter(kart=kart, silindi=False)
              .select_related("fis").order_by("ilk_vade", "id")):
        for t in taksit_takvimi(p):
            hepsi.append({**t, "adet": p.taksit_adedi, "fis": p.fis, "pb": p.para_birimi,
                          "aciklama": p.fis.aciklama})
    hepsi.sort(key=lambda x: (x["vade"], x["fis"].pk, x["sira"]))
    return hepsi


def duzenleme_bilgisi(fis, kart):
    """Hareket düzenleme ekranı için: {'gider_duzenlenebilir', 'gider_hesap', 'kart_satiri', 'karsi_satiri'}.
    Gider hesabı yalnız karşı taraf bir GİDER (yaprak) hesabıysa değiştirilebilir; cari/banka/kasa hesabı değil."""
    if fis.kaynak != YevmiyeFisi.Kaynak.KREDI_KARTI or fis.kredi_karti_id != kart.pk or fis.silindi:
        raise KrediKartiHareketHatasi("Bu fiş bu kartın düzenlenebilir bir hareketi değil.")
    ana = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True).select_related("hesap").order_by("id"))
    kart_s = [x for x in ana if x.hesap_id == kart.muhasebe_id]
    karsi_s = [x for x in ana if x.hesap_id != kart.muhasebe_id]
    if len(ana) != 2 or len(kart_s) != 1 or len(karsi_s) != 1:
        raise KrediKartiHareketHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    kod = karsi_s[0].hesap_id
    baska = (Cari.objects.filter(muhasebe_kodu=kod).exists()
             or BankaHesap.objects.filter(muhasebe_id=kod).exists() or Kasa.objects.filter(muhasebe_id=kod).exists())
    return {"gider_duzenlenebilir": not baska, "gider_hesap": karsi_s[0].hesap,
            "kart_satiri": kart_s[0], "karsi_satiri": karsi_s[0]}


@transaction.atomic
def hareket_guncelle(*, fis, kart, aciklama=None, gider=None, yatirim_projesi_id=None, kullanici=None):
    """Kredi kartı hareketini DÜZENLER: açıklama, gider hesabı (karşı taraf gider hesabıysa) ve yatırım
    projesi (258'de zorunlu). Tutar/tarih/kur değişmez. Fiş ``fis_guncelle`` ile yeniden yazılır; kur farkı
    motoru yeniden çalışır. Hatada hiçbir şey değişmez."""
    from core.services.yevmiye import fis_guncelle
    bilgi = duzenleme_bilgisi(fis, kart)
    kart_s, karsi_s = bilgi["kart_satiri"], bilgi["karsi_satiri"]
    karsi_kod = karsi_s.hesap_id
    if gider is not None and gider.hesap_kodu != karsi_kod:
        if not bilgi["gider_duzenlenebilir"]:
            raise KrediKartiHareketHatasi("Bu hareketin karşı tarafı gider hesabı değil; hesap değiştirilemez.")
        karsi_kod, _ad = _karsi_coz("harcama", kart, gider)
    proje_id = _proje_coz(karsi_kod, yatirim_projesi_id)
    yeni_ack = fis.aciklama if aciklama is None else buyuk_harf_tr((aciklama or "").strip())
    if not yeni_ack:
        yeni_ack = fis.aciklama

    def _satir(ln, hesap_kodu, proje=None):
        return SatirGirdi(hesap_kodu=hesap_kodu, taraf="B" if ln.borc else "A", islem_tutari=ln.islem_tutari,
                          islem_pb=ln.islem_pb, islem_kuru=ln.islem_kuru, yatirim_projesi_id=proje)
    try:
        fis_guncelle(fis, tarih=fis.tarih, aciklama=yeni_ack, kur_usd=fis.kur_usd, kullanici=kullanici,
                     satirlar=[_satir(kart_s, kart_s.hesap_id), _satir(karsi_s, karsi_kod, proje_id)])
    except YevmiyeHatasi as e:
        raise KrediKartiHareketHatasi(str(e))
    return fis
