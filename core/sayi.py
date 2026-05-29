"""
Sayı / para — TEK parser + TEK formatter (spec 0b-a, KRİTİK).

Değişmez kurallar:
- İç temsil her zaman ``Decimal`` (asla float), nokta-ondalıklı ve ayraçsızdır.
- TR biçim: **nokta = binlik ayracı, virgül = ondalık ayracı.**
- Yuvarlama: ROUND_HALF_UP, tek merkezi fonksiyon (:func:`yuvarla`).
- Locale yalnızca iki kenarda yaşar: :func:`parse_tr` (giriş) ve
  :func:`format_tr` (gösterim). Çekirdek/DB locale bilmez.

Hiçbir yerde ``float``/``parseFloat``/string matematiği YOK.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

__all__ = ["SayiHatasi", "parse_tr", "yuvarla", "format_tr"]


class SayiHatasi(ValueError):
    """Geçersiz sayı/para girdisi."""


# Binlik gruplu tam sayı: 1.234.567  (ilk grup 1-3 hane, sonrakiler tam 3 hane)
_BINLIK = re.compile(r"^\d{1,3}(\.\d{3})+$")
# Kanonik / sade ondalık: 10.35, 1234.5  (tek nokta, binlik kalıbına uymayan)
_KANONIK = re.compile(r"^\d+\.\d+$")
# Sadece rakam
_TAMSAYI = re.compile(r"^\d+$")


def parse_tr(deger) -> Decimal:
    """Kullanıcı girdisini / kanonik metni ``Decimal``'e çevirir. TEK giriş kapısı.

    Kabul edilen biçimler::

        "10,35"            -> 10.35       (virgül = ondalık)
        "1.035,00"         -> 1035.00     (nokta = binlik, virgül = ondalık)
        "1.035"            -> 1035        (nokta = binlik)
        "0,5"              -> 0.5
        "-1.234.567,89"    -> -1234567.89
        "1000000"          -> 1000000
        "10.35"  (kanonik) -> 10.35       (binlik kalıbına uymayan tek nokta = ondalık)
        Decimal / int      -> Decimal'e sarılır

    Boş, ``None`` veya geçersiz girdi :class:`SayiHatasi` yükseltir.
    """
    if isinstance(deger, Decimal):
        return deger
    if isinstance(deger, int) and not isinstance(deger, bool):
        return Decimal(deger)
    if not isinstance(deger, str):
        raise SayiHatasi(f"Metin bekleniyordu: {deger!r}")

    s = deger.strip()
    if not s:
        raise SayiHatasi("Boş değer sayı değildir.")

    isaret = ""
    if s[0] in "+-":
        if s[0] == "-":
            isaret = "-"
        s = s[1:]
    if not s:
        raise SayiHatasi(f"Geçersiz sayı: {deger!r}")

    govde = _coz_govde(s, deger)
    try:
        return Decimal(isaret + govde)
    except InvalidOperation:
        raise SayiHatasi(f"Geçersiz sayı: {deger!r}")


def _coz_govde(s: str, orijinal) -> str:
    """İşaretsiz gövdeyi kanonik (nokta-ondalıklı, ayraçsız) stringe çevirir."""
    if "," in s:
        # TR biçim: tek virgül = ondalık; varsa noktalar = binlik
        if s.count(",") > 1:
            raise SayiHatasi(f"Birden çok virgül: {orijinal!r}")
        tam, _, kesir = s.partition(",")
        if not _TAMSAYI.fullmatch(kesir):
            raise SayiHatasi(f"Geçersiz ondalık kısım: {orijinal!r}")
        tam_temiz = tam.replace(".", "")
        if not _TAMSAYI.fullmatch(tam_temiz):
            raise SayiHatasi(f"Geçersiz tam kısım: {orijinal!r}")
        return f"{tam_temiz}.{kesir}"

    # Virgül yok
    if _TAMSAYI.fullmatch(s):
        return s
    if _BINLIK.fullmatch(s):
        return s.replace(".", "")
    if _KANONIK.fullmatch(s):
        return s
    raise SayiHatasi(f"Geçersiz sayı: {orijinal!r}")


def yuvarla(deger: Decimal, basamak: int = 2) -> Decimal:
    """``deger``'i ``basamak`` ondalığa **ROUND_HALF_UP** ile yuvarlar.

    Tek yuvarlama kapısı. KDV/kur çevriminde 1 kuruş tutarsızlıklarını önler.
    """
    kuant = Decimal(1).scaleb(-basamak)  # basamak=2 -> Decimal("0.01")
    return deger.quantize(kuant, rounding=ROUND_HALF_UP)


def format_tr(deger, basamak: int = 2) -> str:
    """``Decimal``'i TR biçimde gösterir: nokta = binlik, virgül = ondalık.

    Her zaman ``basamak`` ondalık gösterir (ROUND_HALF_UP)::

        Decimal("1234567.89")    -> "1.234.567,89"
        Decimal("0.5"),  2       -> "0,50"
        Decimal("-1234567.89")   -> "-1.234.567,89"
        Decimal("1000000"), 0    -> "1.000.000"
    """
    if not isinstance(deger, Decimal):
        deger = parse_tr(deger)

    q = yuvarla(deger, basamak)
    isaret = "-" if q < 0 else ""
    q = abs(q)

    s = str(q)
    if "." in s:
        tam_str, kesir = s.split(".")
    else:
        tam_str, kesir = s, ""
    kesir = (kesir + "0" * basamak)[:basamak]

    tam_gruplu = format(int(tam_str), ",").replace(",", ".")
    if basamak > 0:
        return f"{isaret}{tam_gruplu},{kesir}"
    return f"{isaret}{tam_gruplu}"
