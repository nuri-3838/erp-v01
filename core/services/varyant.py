"""VARYANT tanımları (AYARLAR > Varyantlar) + varyantlı stok kartının varyant kombinasyonları (StokVaryant).

Kurallar (docs/varyant_kararlari.md):
- ``Varyant`` değerleri tek listedir (SEMTA, YOK, MR. MOP …); kod TR büyük harf, aktif kayıtta benzersiz. Tam olarak BİR değer
  ``varsayilan`` olabilir (SEMTA) — varyantlı kartın STANDART varyantı tüm boyutlarda bu değerdir.
- Varyantlı kart (``Stok.varyantli``) en az bir boyut (``Stok.varyant_boyutlari``) taşır; boyut sırası sabittir (BOYUT_SIRASI).
  Kart varyantlı işaretlenince standart ``StokVaryant`` satırı otomatik açılır.
- Bu adımda (E-1) motor/hareket/ayırma/planlama DEĞİŞMEZ: StokVaryant yalnız tanımdır; adım 2+ hareket/ayırma/özet buna bağlanır.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Stok, StokVaryant, Varyant

BOYUT_SIRASI = [b.value for b in Stok.VaryantBoyutu]
BOYUT_ETIKETI = dict(Stok.VaryantBoyutu.choices)
_BOYUT_ALANI = {"MARKALAMA": "markalama", "KILAVUZ": "kilavuz", "AFIS": "afis", "YAN_ETIKET": "yan_etiket"}


class VaryantHatasi(ValueError):
    """Varyant kural ihlali (Türkçe mesaj)."""


# === Varyant değerleri ===================================================================

def aktif_varyantlar():
    return Varyant.objects.filter(silindi=False).order_by("sira", "kod")


def secilebilir_varyantlar():
    """Ekranlarda seçilebilen (aktif) değerler."""
    return aktif_varyantlar().filter(aktif=True)


def varsayilan_varyant() -> Varyant | None:
    return Varyant.objects.filter(silindi=False, varsayilan=True).first()


def _dogrula(kod, ad, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    ad = buyuk_harf_tr((ad or "").strip())
    if not kod:
        raise VaryantHatasi("Kod boş olamaz.")
    if not ad:
        raise VaryantHatasi("Ad boş olamaz.")
    qs = Varyant.objects.filter(silindi=False, kod=kod)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise VaryantHatasi(f"Bu kod zaten kayıtlı: {kod}")
    return kod, ad


def _diger_varsayilanlari_kapat(haric_pk=None, kullanici=None):
    """Yeni varsayılan KAYDEDİLMEDEN ÖNCE çağrılır (partial unique index hemen kontrol edilir)."""
    qs = Varyant.objects.filter(silindi=False, varsayilan=True)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    qs.update(varsayilan=False, updated_by=kullanici)


@transaction.atomic
def varyant_olustur(*, kod, ad, ad_en="", sira=0, aktif=True, varsayilan=False, kullanici=None) -> Varyant:
    kod, ad = _dogrula(kod, ad)
    if varsayilan:
        _diger_varsayilanlari_kapat(kullanici=kullanici)
    return Varyant.objects.create(kod=kod, ad=ad, ad_en=(ad_en or "").strip(), sira=int(sira or 0), aktif=bool(aktif),
                                  varsayilan=bool(varsayilan), created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def varyant_guncelle(v: Varyant, *, kod, ad, ad_en="", sira=0, aktif=True, varsayilan=False, kullanici=None) -> Varyant:
    if v.silindi:
        raise VaryantHatasi("Silinmiş kayıt düzenlenemez.")
    v.kod, v.ad = _dogrula(kod, ad, haric_pk=v.pk)
    v.ad_en = (ad_en or "").strip()
    v.sira = int(sira or 0)
    varsayilan = bool(varsayilan)
    if varsayilan:
        _diger_varsayilanlari_kapat(haric_pk=v.pk, kullanici=kullanici)
        aktif = True                                          # varsayılan pasif olamaz
    elif v.varsayilan:
        raise VaryantHatasi("Varsayılan işareti kaldırılamaz; başka bir değeri varsayılan yapın.")
    v.aktif = bool(aktif)
    v.varsayilan = varsayilan
    v.updated_by = kullanici
    v.save(update_fields=["kod", "ad", "ad_en", "sira", "aktif", "varsayilan", "updated_by", "updated_at"])
    return v


@transaction.atomic
def varyant_varsayilan_yap(v: Varyant, kullanici=None) -> Varyant:
    """Listeden hızlı işaretleme: bu değer varsayılan olur, öncekinin işareti kalkar. Kaldırma yok (tam bir varsayılan olmalı)."""
    if v.silindi:
        raise VaryantHatasi("Silinmiş kayıt düzenlenemez.")
    if not v.varsayilan:
        _diger_varsayilanlari_kapat(haric_pk=v.pk, kullanici=kullanici)
        v.varsayilan, v.aktif, v.updated_by = True, True, kullanici
        v.save(update_fields=["varsayilan", "aktif", "updated_by", "updated_at"])
    return v


def varyant_kullanimda_mi(v: Varyant) -> bool:
    from django.db.models import Q
    return StokVaryant.objects.filter(silindi=False).filter(
        Q(markalama=v) | Q(kilavuz=v) | Q(afis=v) | Q(yan_etiket=v)).exists()


def varyant_sil(v: Varyant, kullanici=None) -> Varyant:
    if v.silindi:
        return v
    if v.varsayilan:
        raise VaryantHatasi("Varsayılan (standart) varyant silinemez.")
    if varyant_kullanimda_mi(v):
        raise VaryantHatasi("Bu varyant stok kartlarında kullanılıyor; silinemez (pasif yapabilirsiniz).")
    v.silindi, v.silindi_at, v.updated_by = True, timezone.now(), kullanici
    v.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return v


# === Stok boyutları ve StokVaryant =========================================================

def boyutlari_dogrula(varyantli, boyutlar) -> list:
    """Kartın boyut listesini sabit sıraya sokar; varyantlı kartta en az bir boyut zorunlu, varyantsız kartta liste boş."""
    secim = [b for b in BOYUT_SIRASI if b in set(boyutlar or [])]
    gecersiz = set(boyutlar or []) - set(BOYUT_SIRASI)
    if gecersiz:
        raise VaryantHatasi(f"Geçersiz varyant boyutu: {', '.join(sorted(gecersiz))}")
    if varyantli and not secim:
        raise VaryantHatasi("Varyantlı kartta en az bir varyant boyutu seçilmeli (markalama, kılavuz, afiş, yan etiket).")
    return secim if varyantli else []


def stok_boyutlari(stok: Stok) -> list:
    return [b for b in BOYUT_SIRASI if b in (stok.varyant_boyutlari or [])]


def anahtar_uret(stok: Stok, secimler: dict) -> str:
    """``secimler``: {boyut kodu: Varyant|pk|None}; yalnız kartın boyutları yazılır (sabit sıra)."""
    parcalar = []
    for b in stok_boyutlari(stok):
        v = secimler.get(b)
        pk = getattr(v, "pk", v)
        parcalar.append(f"{b}={pk if pk else ''}")
    return "|".join(parcalar)


def stok_varyantlari(stok: Stok):
    return (StokVaryant.objects.filter(stok=stok, silindi=False)
            .select_related("markalama", "kilavuz", "afis", "yan_etiket").order_by("-standart", "anahtar"))


def varyant_etiketi(sv: StokVaryant) -> str:
    """'Markalama: SEMTA · Afiş: MR. MOP' — kartın boyut sırasıyla (boş = YOK anlamı: boyut seçilmedi)."""
    parcalar = []
    for b in stok_boyutlari(sv.stok):
        v = getattr(sv, _BOYUT_ALANI[b])
        parcalar.append(f"{BOYUT_ETIKETI[b]}: {v.kod if v else '—'}")
    return " · ".join(parcalar) or "—"


def _standart_mi(stok: Stok, secimler: dict) -> bool:
    vs = varsayilan_varyant()
    if vs is None:
        return False
    for b in stok_boyutlari(stok):
        v = secimler.get(b)
        if getattr(v, "pk", v) != vs.pk:
            return False
    return True


@transaction.atomic
def stok_varyanti_bul_veya_olustur(stok: Stok, secimler: dict, kullanici=None) -> StokVaryant:
    """Kartın (boyut → Varyant) kombinasyonu için aktif StokVaryant satırını döner, yoksa açar. Varyantsız kartta hata."""
    if not stok.varyantli:
        raise VaryantHatasi(f"{stok.kod} varyantlı bir kart değil.")
    for b in stok_boyutlari(stok):
        v = secimler.get(b)
        if v is not None and not isinstance(v, Varyant):
            v = Varyant.objects.filter(pk=v, silindi=False).first()
            if v is None:
                raise VaryantHatasi("Varyant bulunamadı.")
            secimler[b] = v
    anahtar = anahtar_uret(stok, secimler)
    mevcut = StokVaryant.objects.filter(stok=stok, anahtar=anahtar, silindi=False).first()
    if mevcut is not None:
        return mevcut
    alanlar = {_BOYUT_ALANI[b]: secimler.get(b) for b in stok_boyutlari(stok)}
    return StokVaryant.objects.create(stok=stok, anahtar=anahtar, standart=_standart_mi(stok, secimler),
                                      created_by=kullanici, updated_by=kullanici, **alanlar)


def standart_varyant(stok: Stok, kullanici=None) -> StokVaryant | None:
    """Kartın standart varyantı (tüm boyutlar varsayılan değer); yoksa açar. Varsayılan Varyant tanımlı değilse None."""
    vs = varsayilan_varyant()
    if vs is None or not stok.varyantli:
        return None
    return stok_varyanti_bul_veya_olustur(stok, {b: vs for b in stok_boyutlari(stok)}, kullanici)


@transaction.atomic
def stok_varyantlarini_esitle(stok: Stok, kullanici=None) -> None:
    """Kartın varyantlı/boyut ayarı değişince çağrılır: varyantsız olduysa aktif StokVaryant satırları kapanır; varyantlı ise mevcut
    satırların anahtarı/standart işareti yeni boyut sırasına göre yeniden yazılır ve standart varyant garanti edilir."""
    satirlar = list(StokVaryant.objects.filter(stok=stok, silindi=False))
    if not stok.varyantli:
        for sv in satirlar:
            sv.silindi, sv.silindi_at, sv.updated_by = True, timezone.now(), kullanici
            sv.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        return
    gorulen = set()
    for sv in satirlar:
        secimler = {b: getattr(sv, _BOYUT_ALANI[b]) for b in stok_boyutlari(stok)}
        anahtar = anahtar_uret(stok, secimler)
        if anahtar in gorulen:                              # boyut kaldırılınca iki satır aynı kombinasyona düştü → ikincisi kapanır
            sv.silindi, sv.silindi_at, sv.updated_by = True, timezone.now(), kullanici
            sv.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
            continue
        gorulen.add(anahtar)
        standart = _standart_mi(stok, secimler)
        if (sv.anahtar, sv.standart) != (anahtar, standart):
            sv.anahtar, sv.standart, sv.updated_by = anahtar, standart, kullanici
            sv.save(update_fields=["anahtar", "standart", "updated_by", "updated_at"])
    standart_varyant(stok, kullanici)
