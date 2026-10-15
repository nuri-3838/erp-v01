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
