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
def tr_kur(value):
    """Kur gösterimi: 6 ondalık, TR (30,123456)."""
    if value is None or value == "":
        return ""
    return format_tr(value if isinstance(value, Decimal) else Decimal(str(value)), 6)
