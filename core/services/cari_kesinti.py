"""CARİ KESİNTİ / MASRAF hareketi — manuel fiş yerine. Cari kartından: tarih, tutar, gider hesabı (varsayılan
770.03), açıklama. Fiş otomatik ve dengeli (kaynak=CARI_KESINTI, fiş→cari bağı); Düzenle ve (yalnız süper
kullanıcı) kalıcı Sil vardır.

Hesap listesi: gider hesapları (7xx/65x/66x/68x), 258 YAPILMAKTA OLAN YATIRIMLAR (yatırım projesi ZORUNLU, proje "Devam
Ediyor" olmalı; satır projeye yazılır — proje toplamı tutar kadar düşer/artar, karta maliyet olarak BAĞLANMAZ) ve gelir
hesapları (64x / 67x yaprak, ör. 649, 679).
Yön cari hesabının türünden gelir (otomatik; hesap türünden bağımsız):
  120/121 (müşteri / alacak tarafı): Gider BORÇ / Cari ALACAK — müşteri ödemesinden kesilen gider (ör. güvenli ödeme
      masrafı) cari bakiyesini kapatır; kuruş farkları da böyle girilir.
  320/321 (tedarikçi / borç tarafı): Cari BORÇ / Gider ALACAK — tedarikçiden gelen kesinti/indirim.
Döviz carilerinde (ör. Formal 320.10.0001: USD+EUR+TL havuzları) para birimi seçilir (TRY/USD/EUR/GBP): döviz seçilirse kur girilir
(boşsa carinin kur tipine göre TCMB); cari satırı seçilen dövizle, karşı taraf TL karşılığıyla yazılır, kur farkı motoru havuzu işler.
TRY seçilirse cari satırı TL yazılır (carinin TL havuzu). TL carisinde yalnız TRY.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur

VARSAYILAN_GIDER = "770.03"


class CariKesintiHatasi(ValueError):
    """Cari kesinti/masraf kural ihlali (Türkçe mesaj)."""


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariKesintiHatasi("Tutar geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d <= 0:
        raise CariKesintiHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def yon_coz(cari):
    """Cari hesabına göre kesinti yönü: 'musteri' (gider borç / cari alacak) ya da 'tedarikci' (tersi)."""
    kod = cari.muhasebe_kodu or ""
    if kod.startswith(("120", "121", "500.")):        # 500.xx: ortak sermaye carisi (ortak şirket adına ödedi → gider BORÇ / ortak ALACAK)
        return "musteri"
    if kod.startswith(("320", "321")):
        return "tedarikci"
    raise CariKesintiHatasi(
        f"{cari.unvan} carisinin muhasebe hesabı ({kod or 'yok'}) 120/320/500 ailesinde değil; yön belirlenemedi.")


def ortak_cariler():
    """'Ortak ödedi' seçeneğinde karşı cari olabilen ortak sermaye carileri (muhasebe hesabı 500.xx.xxxx)."""
    from core.models import Cari
    return Cari.objects.filter(silindi=False, muhasebe_kodu__startswith="500.").order_by("unvan")


def karsi_cari_coz(cari, karsi_cari):
    """Başka bir carinin borcunu ortak ödediyse: karşı cari 500.xx ortak carisi olmalı (TL); kartın carisi 500 olamaz."""
    if karsi_cari is None:
        return None
    if (cari.muhasebe_kodu or "").startswith("500."):
        raise CariKesintiHatasi("Ortak carisinin kendi kartında 'ortak ödedi' seçilemez; gider hesabı seçin.")
    if not (karsi_cari.muhasebe_kodu or "").startswith("500.") or karsi_cari.silindi:
        raise CariKesintiHatasi("Karşı cari bir ortak sermaye carisi (500.xx hesaplı) olmalı.")
    if karsi_cari.para_birimi != "TRY":
        raise CariKesintiHatasi("Karşı cari TL olmalı.")
    return karsi_cari


def kesinti_hesap_kumesi():
    """Kesinti/masraf hesabı olarak seçilebilen TAM küme: gider hesapları + 258 ailesi + gelir hesapları (64x/67x yaprak)."""
    from django.db.models import Q
    from core.services.hesap_plani import duran_varlik_hesaplari, gider_hesaplari, yaprak_hesaplar
    gider = gider_hesaplari()
    yatirim = duran_varlik_hesaplari().filter(Q(hesap_kodu="258") | Q(hesap_kodu__startswith="258."))
    gelir = yaprak_hesaplar().filter(hesap_kodu__regex=GELIR_KOD_DESENI)
    return (gider | yatirim | gelir).distinct()


GELIR_KOD_DESENI = r"^(64\d|67\d)(\.|$)"


def gider_hesabi_coz(kod):
    h = kesinti_hesap_kumesi().filter(hesap_kodu=(kod or VARSAYILAN_GIDER)).first()
    if h is None:
        raise CariKesintiHatasi(
            f"Geçerli bir hesap seçin (gider 7xx/65x/66x/68x, 258 yatırım ya da gelir 64x/67x yaprak hesap): {kod}")
    return h


def proje_coz(hesap, yatirim_projesi_id):
    """258 ailesinde proje ZORUNLU ve 'Devam Ediyor' olmalı; diğer hesaplarda proje verilemez. proje pk ya da None."""
    from core.models import YatirimProjesi
    from core.services.hesap_plani import hesap_kodu_258_mi
    if hesap_kodu_258_mi(hesap.hesap_kodu):
        if not yatirim_projesi_id:
            raise CariKesintiHatasi("258 hesabı için yatırım projesi seçilmelidir.")
        proje = YatirimProjesi.objects.filter(pk=yatirim_projesi_id, silindi=False).first()
        if proje is None:
            raise CariKesintiHatasi("Yatırım projesi bulunamadı.")
        if proje.durum != YatirimProjesi.Durum.DEVAM:
            raise CariKesintiHatasi(f"{proje.kod} projesi aktifleşmiş; yeni kalem eklenemez.")
        return proje.pk
    if yatirim_projesi_id:
        raise CariKesintiHatasi("Yatırım projesi yalnız 258 hesabında seçilebilir.")
    return None


def _ack(cari, aciklama, yon):
    ack = buyuk_harf_tr((aciklama or "").strip())
    return ack or buyuk_harf_tr(f"KESİNTİ / MASRAF - {cari.unvan}")


def pb_kur_coz(cari, para_birimi, kur, tarih):
    """(pb, kur): TL carisinde yalnız TRY; döviz carisinde TRY ya da döviz (TRY → cari satırı TL, carinin TL havuzu). Döviz seçilip
    kur boşsa carinin kur tipine göre TCMB kuru (tarih için kayıt yoksa hata)."""
    from core.models import Kur, YevmiyeSatir
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in YevmiyeSatir.IslemPB.values:
        raise CariKesintiHatasi("Geçersiz para birimi.")
    if cari.para_birimi == "TRY" and pb != "TRY":
        raise CariKesintiHatasi(f"{cari.unvan} TL carisi; yalnız TL kesinti/masraf girilebilir.")
    if pb == "TRY":
        return "TRY", Decimal("1")
    if kur in (None, ""):
        k = Kur.objects.filter(tarih=tarih, silindi=False).first()
        deger = k.deger(pb, cari.kur_tipi) if k else None
        if not deger:
            raise CariKesintiHatasi(
                f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekin ya da kuru elle girin.")
        return pb, deger
    try:
        kur = parse_tr(kur) if not isinstance(kur, Decimal) else kur
    except SayiHatasi:
        raise CariKesintiHatasi("Kur geçerli bir sayı olmalı.")
    if kur <= 0:
        raise CariKesintiHatasi("Kur sıfırdan büyük olmalı.")
    return pb, kur


def _cari_hesabi(cari):
    cari_hesap = HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
    if cari_hesap is None:
        raise CariKesintiHatasi(f"{cari.unvan} carisinin muhasebe hesabı hesap planında yok.")
    return cari_hesap


def _satirlar(cari, gider, tutar, yon, proje_id=None, pb="TRY", kur=Decimal("1")):
    """Cari satırı seçilen para biriminde (``tutar`` döviz tutarı), karşı taraf TL karşılığıyla; kur farkı motoru havuzu işler."""
    cari_hesap = _cari_hesabi(cari)
    tl = yuvarla(tutar * kur, 2)
    gider_taraf, cari_taraf = ("B", "A") if yon == "musteri" else ("A", "B")
    return [SatirGirdi(hesap_kodu=gider.hesap_kodu, taraf=gider_taraf, islem_tutari=tl,
                       aciklama=gider.hesap_adi, yatirim_projesi_id=proje_id),
            SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf, islem_tutari=tutar, islem_pb=pb, islem_kuru=kur,
                       tl_override=(tl if pb != "TRY" else None), aciklama=cari.unvan)]


def _satirlar_karsi_cari(cari, karsi, tutar, pb="TRY", kur=Decimal("1")):
    """Ortak, carinin borcunu ödedi: cari BORÇ (seçilen para biriminde) / ortak (500.xx) ALACAK (TL karşılığı)."""
    cari_hesap = _cari_hesabi(cari)
    if not HesapPlani.objects.filter(hesap_kodu=karsi.muhasebe_kodu, silindi=False, aktif=True).exists():
        raise CariKesintiHatasi(f"{karsi.unvan} carisinin muhasebe hesabı hesap planında yok.")
    tl = yuvarla(tutar * kur, 2)
    return [SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf="B", islem_tutari=tutar, islem_pb=pb, islem_kuru=kur,
                       tl_override=(tl if pb != "TRY" else None), aciklama=cari.unvan),
            SatirGirdi(hesap_kodu=karsi.muhasebe_kodu, taraf="A", islem_tutari=tl, aciklama=karsi.unvan)]


def havuz_bakiyeleri(cari):
    """Carinin açık bakiyeleri para birimi havuzu (TL/USD/EUR…) bazında: [{pb, doviz, tl, taraf}] — 'borç'/'alacak' bakiye.
    Hangi havuzun kapatılacağına karar vermek için ekranda gösterilir."""
    from collections import defaultdict
    from core.models import YevmiyeSatir
    h = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    for s in YevmiyeSatir.objects.filter(hesap_id=cari.muhasebe_kodu, silindi=False, fis__silindi=False):
        yon = 1 if s.borc else -1                       # + borç bakiye
        h[s.islem_pb][0] += yon * s.islem_tutari
        h[s.islem_pb][1] += s.borc - s.alacak
    return [{"pb": pb, "doviz": abs(d), "tl": abs(t), "taraf": "borç" if d > 0 or (d == 0 and t > 0) else "alacak"}
            for pb, (d, t) in sorted(h.items()) if d != 0 or t != 0]


@transaction.atomic
def kesinti_olustur(*, cari, tarih, tutar, gider_kodu=None, aciklama="", yatirim_projesi_id=None,
                    kullanici=None, karsi_cari=None, para_birimi="TRY", kur=None) -> YevmiyeFisi:
    karsi = karsi_cari_coz(cari, karsi_cari)
    tut = _tutar(tutar)
    pb, kur = pb_kur_coz(cari, para_birimi, kur, tarih)
    if karsi is not None:                       # ortak, carinin borcunu ödedi
        satirlar, ack = _satirlar_karsi_cari(cari, karsi, tut, pb, kur), (
            buyuk_harf_tr((aciklama or "").strip()) or buyuk_harf_tr(f"ORTAK ÖDEMESİ - {cari.unvan} ({karsi.unvan})"))
    else:
        yon = yon_coz(cari)
        gider = gider_hesabi_coz(gider_kodu)
        proje_id = proje_coz(gider, yatirim_projesi_id)
        satirlar, ack = _satirlar(cari, gider, tut, yon, proje_id, pb, kur), _ack(cari, aciklama, yon)
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack,
                          kaynak=YevmiyeFisi.Kaynak.CARI_KESINTI, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariKesintiHatasi(str(e))
    fis.cari = cari
    fis.save(update_fields=["cari", "updated_at"])
    return fis


def duzenleme_bilgisi(fis, cari):
    """{'gider': HesapPlani, 'tutar': Decimal, 'yon': ...} — fiş bu carinin kesinti hareketi değilse hata."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_KESINTI or fis.cari_id != cari.pk or fis.silindi:
        raise CariKesintiHatasi("Bu fiş bu carinin düzenlenebilir bir kesinti/masraf hareketi değil.")
    satirlar = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True).select_related("hesap"))
    gider_s = [s for s in satirlar if s.hesap_id != cari.muhasebe_kodu]
    if len(satirlar) != 2 or len(gider_s) != 1:
        raise CariKesintiHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    if gider_s[0].hesap_id.startswith("500.") and not (cari.muhasebe_kodu or "").startswith("500."):   # "ortak ödedi"
        from core.models import Cari
        karsi = Cari.objects.filter(muhasebe_kodu=gider_s[0].hesap_id, silindi=False).first()
        if karsi is None:
            raise CariKesintiHatasi("Hareketin ortak carisi bulunamadı; düzenlemeye uygun değil.")
        ortak = True
    else:
        ortak = False
    cari_s = [s for s in satirlar if s.hesap_id == cari.muhasebe_kodu][0]
    ortak_alanlar = {"tutar": cari_s.islem_tutari, "para_birimi": cari_s.islem_pb, "kur": cari_s.islem_kuru}
    if ortak:
        return {"gider": None, "karsi_cari": karsi, "yon": "ortak", "yatirim_projesi_id": None, **ortak_alanlar}
    return {"gider": gider_s[0].hesap, "karsi_cari": None, "yon": yon_coz(cari),
            "yatirim_projesi_id": gider_s[0].yatirim_projesi_id, **ortak_alanlar}


@transaction.atomic
def kesinti_guncelle(*, fis, cari, tarih, tutar, gider_kodu=None, aciklama="", yatirim_projesi_id=None,
                     kullanici=None, karsi_cari=None, para_birimi="TRY", kur=None) -> YevmiyeFisi:
    """Tarih/tutar/para birimi/kur/hesap/proje/açıklama düzenlenir; fiş yeniden yazılır (kur farkı motoru da çalışır)."""
    duzenleme_bilgisi(fis, cari)
    karsi = karsi_cari_coz(cari, karsi_cari)
    tut = _tutar(tutar)
    pb, kur = pb_kur_coz(cari, para_birimi, kur, tarih)
    if karsi is not None:
        satirlar, ack = _satirlar_karsi_cari(cari, karsi, tut, pb, kur), (
            buyuk_harf_tr((aciklama or "").strip()) or buyuk_harf_tr(f"ORTAK ÖDEMESİ - {cari.unvan} ({karsi.unvan})"))
    else:
        yon = yon_coz(cari)
        gider = gider_hesabi_coz(gider_kodu)
        proje_id = proje_coz(gider, yatirim_projesi_id)
        satirlar, ack = _satirlar(cari, gider, tut, yon, proje_id, pb, kur), _ack(cari, aciklama, yon)
    try:
        fis_guncelle(fis, tarih=tarih, satirlar=satirlar, aciklama=ack, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariKesintiHatasi(str(e))
    return fis


@transaction.atomic
def kesinti_sil(*, fis, cari, kullanici=None):
    """Kesinti/masraf hareketini KALICI siler (yalnız süper kullanıcı; denetim kaydı yazılır)."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_KESINTI or fis.cari_id != cari.pk:
        raise CariKesintiHatasi("Bu fiş bu carinin kesinti/masraf hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.CARI_KESINTI})
    except SilmeHatasi as e:
        raise CariKesintiHatasi(str(e))
