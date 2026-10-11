"""VARYANT tanımları (AYARLAR > Varyantlar) + varyantlı stok kartının varyant kombinasyonları (StokVaryant).

Kurallar (docs/varyant_kararlari.md):
- ``Varyant`` değerleri tek listedir (SEMTA, YOK, MR. MOP …); kod TR büyük harf, aktif kayıtta benzersiz. Tam olarak BİR değer
  ``varsayilan`` olabilir (SEMTA) — varyantlı kartın STANDART varyantı tüm boyutlarda bu değerdir.
- Varyantlı kart (``Stok.varyantli``) en az bir boyut (``Stok.varyant_boyutlari``) taşır; boyut sırası sabittir (BOYUT_SIRASI).
  Kart varyantlı işaretlenince standart ``StokVaryant`` satırı otomatik açılır.
- Bu adımda (E-1) motor/hareket/ayırma/planlama DEĞİŞMEZ: StokVaryant yalnız tanımdır; adım 2+ hareket/ayırma/özet buna bağlanır.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Stok, StokHareket, StokVaryant, Varyant

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
    """Kartın standart varyantı (tüm boyutlar varsayılan değer); yoksa açar. Varsayılan Varyant tanımlı değilse None.
    Kartın ``varsayilan_varyant``ı boşsa standart varyant yazılır."""
    vs = varsayilan_varyant()
    if vs is None or not stok.varyantli:
        return None
    sv = stok_varyanti_bul_veya_olustur(stok, {b: vs for b in stok_boyutlari(stok)}, kullanici)
    if stok.varsayilan_varyant_id is None:
        stok.varsayilan_varyant = sv
        Stok.objects.filter(pk=stok.pk).update(varsayilan_varyant=sv)
    return sv


def varyant_kisa(sv) -> str:
    """Kısa etiket: boyut değer kodları '/' ile ('SEMTA', 'YOK', 'MR. MOP/SEMTA/SEMTA/SEMTA'); boş boyut '—'."""
    if sv is None:
        return ""
    if not isinstance(sv, StokVaryant):
        sv = StokVaryant.objects.select_related("stok", "markalama", "kilavuz", "afis", "yan_etiket").get(pk=sv)
    return "/".join((getattr(sv, _BOYUT_ALANI[b]).kod if getattr(sv, _BOYUT_ALANI[b]) else "—") for b in stok_boyutlari(sv.stok)) or "—"


def hareket_varyanti(stok: Stok, varyant=None) -> StokVaryant | None:
    """Hareket/ayırma yazılırken varyantın çözümü (E-2): varyantsız kartta None (varyant verilirse hata); varyantlı kartta verilen
    StokVaryant (karta ait ve aktif olmalı) ya da kartın ``varsayilan_varyant``ı (boşsa standart varyant açılır ve varsayılan yapılır)."""
    if not stok.varyantli:
        if varyant is not None:
            raise VaryantHatasi(f"{stok.kod} varyantlı bir kart değil; varyant verilemez.")
        return None
    if varyant is not None:
        if isinstance(varyant, StokVaryant):
            sv = varyant
        else:
            sv = StokVaryant.objects.filter(pk=varyant, silindi=False).first()
        if sv is None or sv.silindi or sv.stok_id != stok.pk:
            raise VaryantHatasi(f"{stok.kod}: geçersiz varyant.")
        return sv
    if stok.varsayilan_varyant_id:
        sv = StokVaryant.objects.filter(pk=stok.varsayilan_varyant_id, silindi=False).first()
        if sv is not None:
            return sv
    sv = standart_varyant(stok)
    if sv is None:
        raise VaryantHatasi(f"{stok.kod}: varyantlı kart için varsayılan varyant yok (AYARLAR › Varyantlar'da varsayılan tanımlayın).")
    return sv


def secim_degeri(sv) -> str:
    """Form gizli alanı için kodlanmış değer: 'v:MARKALAMA=3|AFIS=' (boş boyut = seçilmedi). None → ''."""
    if sv is None:
        return ""
    if not isinstance(sv, StokVaryant):
        sv = StokVaryant.objects.select_related("stok").filter(pk=sv).first()
        if sv is None:
            return ""
    return "v:" + "|".join(f"{b}={getattr(sv, _BOYUT_ALANI[b] + '_id') or ''}" for b in stok_boyutlari(sv.stok))


def secim_coz(stok: Stok, deger, kullanici=None) -> StokVaryant | None:
    """Ekran/servis girdisinden StokVaryant: None/'' → None (çağıran kartın varsayılanını kullanır); StokVaryant / pk / 'sv:<pk>' → o satır
    (karta ait olmalı); 'v:BOYUT=pk|…' ya da {boyut: pk} → kombinasyon bulunur ya da AÇILIR (alış faturasında yeni varyant, ör. Afiş: MR. MOP).
    Varyantsız kartta dolu değer hata."""
    if deger in (None, ""):
        return None
    if not stok.varyantli:
        raise VaryantHatasi(f"{stok.kod} varyantlı bir kart değil; varyant verilemez.")
    if isinstance(deger, StokVaryant):
        sv = deger
    elif isinstance(deger, int) or (isinstance(deger, str) and deger.isdigit()):
        sv = StokVaryant.objects.filter(pk=int(deger), silindi=False).first()
    elif isinstance(deger, str) and deger.startswith("sv:"):
        sv = StokVaryant.objects.filter(pk=int(deger[3:] or 0), silindi=False).first()
    elif isinstance(deger, (str, dict)):
        if isinstance(deger, str):
            if not deger.startswith("v:"):
                raise VaryantHatasi(f"{stok.kod}: varyant değeri çözümlenemedi.")
            secimler = {}
            for parca in deger[2:].split("|"):
                k, _, v = parca.partition("=")
                if k:
                    secimler[k.strip()] = int(v) if v.strip() else None
        else:
            secimler = {k: (int(getattr(v, "pk", v)) if v not in (None, "") else None) for k, v in deger.items()}
        bilinmeyen = set(secimler) - set(stok_boyutlari(stok))
        if bilinmeyen:
            raise VaryantHatasi(f"{stok.kod}: kartta olmayan varyant boyutu: {', '.join(sorted(bilinmeyen))}")
        for b in stok_boyutlari(stok):
            secimler.setdefault(b, None)
        return stok_varyanti_bul_veya_olustur(stok, secimler, kullanici)
    else:
        raise VaryantHatasi(f"{stok.kod}: varyant değeri çözümlenemedi.")
    if sv is None or sv.stok_id != stok.pk:
        raise VaryantHatasi(f"{stok.kod}: geçersiz varyant.")
    return sv


def varyant_meta(stok_pkler=None) -> dict:
    """Satır ekranları JS'i (core/_varyant_js.html): aktif değerler, boyut etiketleri, varyantlı kartların boyutları + varsayılan seçimleri."""
    qs = Stok.objects.filter(silindi=False, varyantli=True).select_related("varsayilan_varyant")
    if stok_pkler is not None:
        qs = qs.filter(pk__in=list(stok_pkler))
    stoklar = {}
    for s in qs:
        vs = s.varsayilan_varyant
        stoklar[str(s.pk)] = {"boyutlar": stok_boyutlari(s),
                              "varsayilan": {b: (getattr(vs, _BOYUT_ALANI[b] + "_id") if vs else None) for b in stok_boyutlari(s)}}
    return {"etiket": BOYUT_ETIKETI, "degerler": [[v.pk, v.kod] for v in secilebilir_varyantlar()], "stoklar": stoklar}


def varyant_ozeti(stok: Stok) -> list:
    """Stok kartı varyant kırılımı: [{"varyant", "etiket", "eldeki", "ayrilan", "kullanilabilir", "ort_try", "deger_try"}]."""
    from core.services.hareket import toplu_eldeki_varyant
    from core.services.stok_ayirma import ayirma_varyant_haritasi
    eldeki = toplu_eldeki_varyant([stok.pk])
    ayrilan = ayirma_varyant_haritasi(stok)
    satirlar = []
    for sv in stok_varyantlari(stok):
        e, a = eldeki.get((stok.pk, sv.pk), Decimal("0")), ayrilan.get(sv.pk, Decimal("0"))
        satirlar.append({"varyant": sv, "etiket": varyant_etiketi(sv), "eldeki": e, "ayrilan": a, "kullanilabilir": e - a,
                         "ort_try": sv.ort_maliyet_try, "ort_usd": sv.ort_maliyet_usd, "deger_try": sv.maliyet_deger_try,
                         "varsayilan": sv.pk == stok.varsayilan_varyant_id})
    return satirlar


@transaction.atomic
def stok_varyantlarini_esitle(stok: Stok, kullanici=None) -> None:
    """Kartın varyantlı/boyut ayarı değişince çağrılır: varyantsız olduysa aktif StokVaryant satırları kapanır; varyantlı ise mevcut
    satırların anahtarı/standart işareti yeni boyut sırasına göre yeniden yazılır ve standart varyant garanti edilir."""
    satirlar = list(StokVaryant.objects.filter(stok=stok, silindi=False))
    if not stok.varyantli:
        if StokHareket.objects.filter(stok=stok, varyant__isnull=False).exists():
            raise VaryantHatasi(f"{stok.kod}: varyantlı hareketi olan kart varyantsız yapılamaz.")
        for sv in satirlar:
            sv.silindi, sv.silindi_at, sv.updated_by = True, timezone.now(), kullanici
            sv.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        if stok.varsayilan_varyant_id:
            stok.varsayilan_varyant = None
            Stok.objects.filter(pk=stok.pk).update(varsayilan_varyant=None)
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
