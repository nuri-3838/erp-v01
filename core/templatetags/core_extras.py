"""Gösterim filtreleri — sayıyı İSTİSNASIZ tek formatter'dan (core.sayi) geçirir."""
from decimal import Decimal

from django import template

from core.sayi import format_tr

register = template.Library()


@register.filter
def tr_para(value):
    """Para gösterimi: 2 ondalık, TR (1.234,56)."""
    if value is None or value == "":
        return ""
    return format_tr(value if isinstance(value, Decimal) else Decimal(str(value)), 2)


@register.filter
def tr_kur(value, basamak=6):
    """Kur gösterimi: varsayılan 6 ondalık; ``:4`` ile 4 ondalık (TCMB kurları)."""
    if value is None or value == "":
        return ""
    return format_tr(value if isinstance(value, Decimal) else Decimal(str(value)), basamak)


@register.filter
def tr_bakiye(value):
    """Yürüyen bakiye: pozitif → '1.234,56 B', negatif → '1.234,56 A', sıfır → '0,00'."""
    if value is None or value == "":
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    if d > 0:
        return format_tr(d, 2) + " B"
    elif d < 0:
        return format_tr(-d, 2) + " A"
    return "0,00"
