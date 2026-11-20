"""BİRİM ÇEVRİM KORUMASI (2026-10-15, SAI-2026-0015 vakası): belge satırında (irsaliye/fatura) miktar KARTIN FATURA BİRİMİNDEDİR; stoğa ÜRETİM biriminde, ``miktar / cevirici`` olarak girer.
Satırda ayrı bir birim seçimi yoktur — birim etiketi kartın fatura birimidir. Çevrim katsayısı (1 fatura birimi = 1/çevirici üretim birimi) çok büyük ya da çok küçükse
(``ASIRI_KATSAYI`` = 1.000 kat) yanlış birimle girilmiş miktar sessizce 1.000+ kat yanlış stok yazar (ör. M6 PUL: 40.000 "KG" ÷ 0,0008 = 50.000.000 AD).
Bu modül o durumu tanır; irsaliye kalemi doğrudan fatura biriminde girildiyse (``uretim_miktar`` boş) KAYDI REDDEDER, kullanıcıdan stoğa girecek gerçek miktarı
'Üretim miktarı' alanına yazarak açıkça onaylamasını ister. Fatura satırında üretim miktarı alanı olmadığından orada yalnız UYARI üretilir (bkz. ``fatura.asiri_cevrim_uyarilari``)."""
from __future__ import annotations

from decimal import Decimal

ASIRI_KATSAYI = Decimal("1000")
SIFIR = Decimal("0")


def katsayi(stok) -> Decimal:
    """1 fatura birimi kaç üretim birimi eder (= 1 / çevirici)."""
    cevirici = stok.cevirici or Decimal("1")
    return Decimal("1") / cevirici


def uretim_birim_fiyati(stok, fatura_birimi_fiyati) -> Decimal:
    """FATURA birimindeki birim fiyatı (kart alış fiyatı, belge satırı birim fiyatı) ÜRETİM birimi fiyatına çevirir. ``cevirici`` = 1 üretim birimi kaç fatura birimi
    olduğundan (1 ADET = 0,13469 KG) üretim birimi fiyatı = fatura birimi fiyatı × çevirici (2,30 USD/KG → 0,3098 USD/AD). İrsaliye girişinin maliyeti
    (``tutar ÷ (miktar ÷ çevirici)``) ile aynı çevrimdir; yuvarlama çağıranındır (tam hassasiyet döner)."""
    return Decimal(fatura_birimi_fiyati) * (stok.cevirici or Decimal("1"))


def asiri_mi(stok) -> bool:
    """Çevrim katsayısı ``ASIRI_KATSAYI`` katını aşıyor mu (büyütme ya da küçültme yönünde)?"""
    k = katsayi(stok)
    return k >= ASIRI_KATSAYI or k <= Decimal("1") / ASIRI_KATSAYI


def _sayi(d: Decimal) -> str:
    from core.sayi import format_tr
    d = Decimal(d)
    return format_tr(d, 0 if d == d.to_integral_value() else 3)


def aciklama(stok, miktar, uretim_miktar=None) -> str:
    """'40.000 KG = 50.000.000 AD (çevirici 0,0008; 1 KG = 1.250 AD)'."""
    from core.sayi import format_tr
    uretim = uretim_miktar if uretim_miktar is not None else (Decimal(miktar) / (stok.cevirici or Decimal("1")))
    return (f"{_sayi(miktar)} {stok.fatura_birimi.kisa_ad} = {_sayi(uretim)} {stok.uretim_birimi.kisa_ad} "
            f"(çevirici {format_tr(stok.cevirici, 4)}; 1 {stok.fatura_birimi.kisa_ad} = {_sayi(katsayi(stok))} {stok.uretim_birimi.kisa_ad})")


def irsaliye_kalem_hatasi(stok, miktar, uretim_miktar, *, satir=None):
    """İrsaliye kalemi için hata metni ya da None. ``uretim_miktar`` DOLUYSA (kullanıcı stoğa girecek miktarı açıkça yazdı) koruma devreye girmez."""
    if uretim_miktar is not None or not asiri_mi(stok):
        return None
    yer = f"Satır {satir}: " if satir else ""
    return (f"{yer}{stok.kod} {stok.ad} — {aciklama(stok, miktar)} stoğa girer. Bu çevrim katsayısı 1.000 katını aşıyor; miktar yanlış birimle "
            f"girilmiş olabilir. Stoğa girecek gerçek {stok.uretim_birimi.kisa_ad} miktarını 'Üretim miktarı' alanına yazın "
            f"(miktar kart birimine otomatik çevrilir) ya da kartın birimini/çeviricisini düzeltin.")


# --- BELGE BAZLI KG + ADET (2026-11-20): kart sabit çeviricisi yerine satırın GERÇEK oranı ---------------------------------------------------------------------
def farkli_birim_mi(stok) -> bool:
    """Belge satırında iki miktar (fatura birimi + stoğa giren) girilebilir mi: kartın fatura birimi stok (üretim) biriminden farklı ya da çevirici 1 değil."""
    return stok.fatura_birimi_id != stok.uretim_birimi_id or (stok.cevirici or Decimal("1")) != 1


def oran(miktar, stok_miktar):
    """Satırın gerçek oranı: 1 üretim birimi (AD) kaç fatura birimi (KG) — kartın ``cevirici`` alanıyla AYNI yönde (123,4 KG ÷ 1070 AD = 0,11533). Miktar boşsa/sıfırsa None."""
    if miktar in (None, "") or stok_miktar in (None, "") or Decimal(stok_miktar) <= 0:
        return None
    return Decimal(miktar) / Decimal(stok_miktar)


def satir_metni(stok, miktar, stok_miktar=None) -> str:
    """Gösterim: '123,4 KG = 1.070 AD (oran 0,11533)'. ``stok_miktar`` boşsa kart çeviricisiyle hesaplanan miktar ve '(kart çeviricisi 0,1027)' yazılır."""
    from core.sayi import format_tr
    fb, ub = stok.fatura_birimi.kisa_ad, stok.uretim_birimi.kisa_ad

    def kisa(d):                                      # 123,400 → 123,4 ; 1.070,000 → 1.070 (gereksiz sıfırlar atılır)
        t = format_tr(Decimal(d), 3)
        return t.rstrip("0").rstrip(",") if "," in t else t
    if stok_miktar is not None:
        o = oran(miktar, stok_miktar)
        return f"{kisa(miktar)} {fb} = {kisa(stok_miktar)} {ub} (oran {format_tr(o, 5)})"
    uretim = Decimal(miktar) / (stok.cevirici or Decimal("1"))
    return f"{kisa(miktar)} {fb} = {kisa(uretim)} {ub} (kart çeviricisi {format_tr(stok.cevirici, 5)})"
