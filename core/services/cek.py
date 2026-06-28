"""ÇEK/SENET modülü (BORDRO mantığı) — servis katmanı.

Slice 1: muhasebe hesap eşlemesi (CekHesapAyari) oku/kaydet. Her durum için çek ve
senet ayrı yaprak hesaba bağlanır; bordro işlemleri yevmiye fişini bu eşlemeden,
evrak tipine (çek/senet) bakarak üretir. Bordro motoru sonraki dilimlerde.
"""
from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (Cari, CekBordrosu, CekHesapAyari, CekSenet, HesapPlani,
                         Kur, YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr
from core.services.finans import FinansHatasi, _soft_sil, _yaprak_hesap_coz
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_iptal,
                                   fis_olustur)


class CekHatasi(ValueError):
    """Çek/senet kural ihlali (Türkçe mesaj)."""


# Ayar alanları (durum + tip). Form/DB/initial hep bu listeyi kullanır.
AYAR_ALANLARI = (
    "portfoy_cek", "portfoy_senet",
    "tahsilde_cek", "tahsilde_senet",
    "teminatta_cek", "teminatta_senet",
    "verilen_cek", "verilen_senet",
)


def hesap_ayari() -> CekHesapAyari:
    """Tekil çek/senet hesap ayarı (yoksa oluşturur)."""
    return CekHesapAyari.get()


@transaction.atomic
def hesap_ayari_kaydet(kodlar: dict, kullanici=None) -> CekHesapAyari:
    """kodlar: {alan: hesap_kodu | ""}. Dolu olanlar YAPRAK doğrulanıp atanır;
    boş olanlar None'a çekilir (o durum henüz tanımlanmadı)."""
    ayar = CekHesapAyari.get()
    for alan in AYAR_ALANLARI:
        kod = (kodlar.get(alan) or "").strip()
        if kod:
            try:
                hesap = _yaprak_hesap_coz(kod)
            except FinansHatasi as e:
                raise CekHatasi(str(e))
        else:
            hesap = None
        setattr(ayar, alan, hesap)
    ayar.updated_by = kullanici
    ayar.save()
    return ayar


# === Slice 2: Cari Giriş bordrosu (çok çek/senet → tek birleşik fiş) ===
# Durum alanı adları config matrisindeki ön ekler ("portfoy"/"tahsilde"/"teminatta"/"verilen").
_DURUM_AD = {"portfoy": "Portföydeki", "tahsilde": "Bankada Tahsildeki",
             "teminatta": "Bankada Teminattaki", "verilen": "Verilen"}


def _ayar_hesap(ayar, durum_oneki, tip):
    """Config matrisinden (durum × tip) muhasebe hesabını çöz; tanımsızsa net hata."""
    alan = f"{durum_oneki}_{'cek' if tip == CekSenet.Tip.CEK else 'senet'}"
    hesap = getattr(ayar, alan, None)
    if hesap is None:
        tipad = "çek" if tip == CekSenet.Tip.CEK else "senet"
        raise CekHatasi(f"{_DURUM_AD[durum_oneki]} {tipad} hesabı tanımlı değil; "
                        f"önce Muhasebe Hesap Kodları ekranından seçin.")
    return hesap


def _cari_coz(cari_id):
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first() if cari_id else None
    if cari is None:
        raise CekHatasi("Cari (karşı taraf) seçilmedi.")
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise CekHatasi(f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açın.")
    return cari, hesap


def _pb_coz(pb):
    pb = (pb or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise CekHatasi("Geçersiz para birimi.")
    return pb


def _kur_coz(pb, tarih):
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise CekHatasi(f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekin.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CekHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise CekHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _kalemleri_dogrula(satirlar):
    """Form satırlarını doğrula → temiz dict listesi (tip/tutar/vade/belge_no/keşideci)."""
    temiz = []
    for s in satirlar:
        tip = s.get("tip")
        if tip not in CekSenet.Tip.values:
            raise CekHatasi("Tip Çek veya Senet olmalı.")
        if not s.get("vade"):
            raise CekHatasi("Her satır için vade tarihi zorunlu.")
        temiz.append({
            "tip": tip, "tutar": _tutar(s.get("tutar")), "vade": s.get("vade"),
            "belge_no": (s.get("belge_no") or "").strip(),
            "kesideci": buyuk_harf_tr((s.get("kesideci") or "").strip()),
            "on_yuz": s.get("on_yuz"), "arka_yuz": s.get("arka_yuz"),
        })
    if not temiz:
        raise CekHatasi("En az bir çek/senet satırı girilmeli.")
    return temiz


# Giriş/çıkış bordrosu yönleri. cek_taraf = çek hesap satırının fiş tarafı (cari TERS).
#   giriş  (ALINAN): portföy çek/senet BORÇ / cari ALACAK   → durum PORTFOYDE
#   çıkış (VERİLEN): cari BORÇ / verilen çek/senet ALACAK    → durum VERILDI
GIRIS_TANIM = {
    "giris": {"tur": CekBordrosu.Tur.CARI_GIRIS, "yon": CekSenet.Yon.ALINAN,
              "durum": CekSenet.Durum.PORTFOYDE, "oneki": "portfoy", "cek_taraf": "B",
              "ack": "ÇEK/SENET GİRİŞ"},
    "cikis": {"tur": CekBordrosu.Tur.FIRMA_CIKIS, "yon": CekSenet.Yon.VERILEN,
              "durum": CekSenet.Durum.VERILDI, "oneki": "verilen", "cek_taraf": "A",
              "ack": "FİRMA ÇEK/SENET"},
}
# Bordro türü -> oluşturduğu çek/senetlerin giriş durumu (bordro_sil "işlem görmüş" kontrolü).
_GIRIS_DURUM = {t["tur"]: t["durum"] for t in GIRIS_TANIM.values()}


@transaction.atomic
def _bordro_olustur(tan, *, cari_id, tarih, para_birimi="TRY", satirlar,
                    aciklama="", kullanici=None) -> CekBordrosu:
    """Giriş/çıkış bordrosu ortak motoru: N adet CekSenet + TEK birleşik fiş.
    tan = GIRIS_TANIM['giris'|'cikis']. Hesaplar config matrisinden (evrak tipine göre)."""
    cari, cari_hesap = _cari_coz(cari_id)
    pb = _pb_coz(para_birimi)
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    temiz = _kalemleri_dogrula(satirlar)
    cek_top = sum((s["tutar"] for s in temiz if s["tip"] == CekSenet.Tip.CEK), Decimal("0"))
    senet_top = sum((s["tutar"] for s in temiz if s["tip"] == CekSenet.Tip.SENET), Decimal("0"))
    # Çek hesabı satırları (config'den; eksikse net hata — kayıt açılmadan önce).
    cek_satir = []
    if cek_top > 0:
        cek_satir.append((_ayar_hesap(ayar, tan["oneki"], CekSenet.Tip.CEK).hesap_kodu, cek_top))
    if senet_top > 0:
        cek_satir.append((_ayar_hesap(ayar, tan["oneki"], CekSenet.Tip.SENET).hesap_kodu, senet_top))
    toplam = cek_top + senet_top
    bordro = CekBordrosu.objects.create(
        tur=tan["tur"], tarih=tarih, cari=cari,
        aciklama=(aciklama.strip() or buyuk_harf_tr(f"{tan['ack']} - {cari.unvan}")),
        created_by=kullanici, updated_by=kullanici)
    for s in temiz:
        CekSenet.objects.create(
            tip=s["tip"], yon=tan["yon"], tutar=s["tutar"], para_birimi=pb,
            vade=s["vade"], belge_no=s["belge_no"], kesideci=s["kesideci"],
            durum=tan["durum"], cari=cari, giris_bordrosu=bordro,
            on_yuz=s.get("on_yuz"), arka_yuz=s.get("arka_yuz"),
            created_by=kullanici, updated_by=kullanici)
    cek_taraf = tan["cek_taraf"]
    cari_taraf = "A" if cek_taraf == "B" else "B"
    girdiler = [SatirGirdi(hesap_kodu=kod, taraf=cek_taraf, islem_tutari=tut, islem_pb=pb, islem_kuru=kur)
                for kod, tut in cek_satir]
    girdiler.append(SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
                               islem_tutari=toplam, islem_pb=pb, islem_kuru=kur))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


def cari_giris_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Cari Giriş: alınan çek/senetler portföye girer (portföy çek/senet borç / cari alacak)."""
    return _bordro_olustur(GIRIS_TANIM["giris"], **kwargs)


def firma_cikis_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Firma Çek-Senet: kendi çek/senedimizi cariye veririz (cari borç / verilen çek-senet alacak)."""
    return _bordro_olustur(GIRIS_TANIM["cikis"], **kwargs)


@transaction.atomic
def bordro_sil(bordro: CekBordrosu, kullanici=None) -> CekBordrosu:
    """Bordroyu geri al: bağlı fiş(ler) iptal + oluşturduğu çek/senetler soft-delete + bordro
    soft-delete. Evrak giriş durumundan çıkmışsa (işlem görmüşse) engellenir."""
    if bordro.silindi:
        return bordro
    cekler = bordro.cek_senetler.filter(silindi=False)
    beklenen = _GIRIS_DURUM.get(bordro.tur)
    if beklenen and cekler.exclude(durum=beklenen).exists():
        raise CekHatasi("Bu bordrodaki bazı çek/senetler işlem görmüş; önce o işlemleri geri alın.")
    for fis in bordro.fisler.filter(silindi=False):
        fis_iptal(fis, kullanici=kullanici)
    for cek in cekler:
        _soft_sil(cek, kullanici)
    return _soft_sil(bordro, kullanici)


def aktif_bordrolar():
    return (CekBordrosu.objects.filter(silindi=False)
            .select_related("cari", "banka_hesap").order_by("-tarih", "-id"))


def aktif_cek_senetler():
    return (CekSenet.objects.filter(silindi=False)
            .select_related("cari", "giris_bordrosu").order_by("vade", "-id"))


def ortalama_vade(kalemler, baz_tarih):
    """Tutara göre AĞIRLIKLI ortalama vade (çek/senet bordrosu standardı).

    kalemler: [(tutar, vade), ...] (vade = date). baz_tarih = referans (genelde bordro
    işlem tarihi). Döner: (ortalama_vade_tarihi, gun). Boş/sıfır toplamda (None, None).
    gun = baz_tarih'ten ortalama vadeye gün sayısı (ROUND_HALF_UP)."""
    kalemler = [(Decimal(t), v) for t, v in kalemler if t and v]
    toplam = sum((t for t, v in kalemler), Decimal("0"))
    if toplam <= 0:
        return None, None
    agirlikli = sum((t * (v - baz_tarih).days for t, v in kalemler), Decimal("0")) / toplam
    gun = int(agirlikli.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return baz_tarih + datetime.timedelta(days=gun), gun
