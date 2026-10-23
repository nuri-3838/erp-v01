"""Gösterim filtreleri — sayıyı İSTİSNASIZ tek formatter'dan (core.sayi) geçirir."""
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from django import template
from django.utils import dateformat

from core.sayi import format_tr, yuvarla

register = template.Library()
_TR = ZoneInfo("Europe/Istanbul")


@register.simple_tag(takes_context=True)
def mesai_ping(context):
    """Heartbeat ayarı: fabrika kısıtlı (operatör) hesapta ve kapı devredeyken {"url", "sn"}, aksi halde None (bkz. core.fabrika_kapisi)."""
    from django.urls import reverse
    from core.services import mesai_otomatik
    from core.yetki import fabrika_kisitli_mi
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not fabrika_kisitli_mi(user) or not mesai_otomatik.devrede_mi():
        return None
    return {"url": reverse("core:atolye_ping"), "sn": mesai_otomatik.ayar().heartbeat_sn}


@register.filter
def tr_zaman(value, fmt="d.m.Y H:i"):
    """UTC saklanan datetime'ı TR yerel saatine çevirip biçimlendirir. Django şablonunun kendi
    ``date`` filtresi bunu YAPMAZ: TIME_ZONE=UTC ve saat dilimi aktivasyon ara katmanı
    olmadığı için (bkz. core.tarih) ``|date:"H:i"`` ham UTC saatini gösterirdi — saat
    içeren her gösterim (mesai giriş/çıkış vb.) bu filtreyi kullanmalı."""
    if value is None:
        return ""
    return dateformat.format(value.astimezone(_TR), fmt)


@register.filter
def izin_sure(dakika):
    """Saatlik izin süresi: "2 sa 30 dk"."""
    from core.services.personel_izin import sure_metni
    return sure_metni(dakika)


@register.filter
def iban_bicimli(deger):
    """IBAN'ı 4'lü gruplarla gösterir (TR12 3456 …)."""
    from core.dogrulama import iban_bicimli as _b
    return _b(deger)


@register.filter
def sure_metni(dakika):
    """Dakika sayısını "8s 12dk" biçiminde gösterir; None ise boş."""
    if dakika is None:
        return ""
    dakika = int(dakika)
    saat, kalan = divmod(dakika, 60)
    return f"{saat}s {kalan}dk" if saat else f"{kalan}dk"


@register.filter
def get_item(d, anahtar):
    """Şablonda ``dict[degisken_anahtar]`` yapılamadığı için — bkz. yoklama.html mesai_bilgisi."""
    if not d:
        return None
    return d.get(anahtar)


@register.filter
def wa_link(telefon):
    """WhatsApp linki (``https://wa.me/<rakamlar>``) — YALNIZ ``+`` ile başlayan numaradan
    üretilir (ülke kodu belli olsun); aksi halde None döner, şablon kendi 'ülke kodu eksik'
    uyarısını gösterir (spec kararı — telefon formatı düzeltmesi ayrı adım)."""
    t = (telefon or "").strip()
    if not t.startswith("+"):
        return None
    rakamlar = re.sub(r"\D", "", t)
    return f"https://wa.me/{rakamlar}" if rakamlar else None


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
def ham_miktar(value):
    """Adres/form önerisi için sade ondalık metin (nokta ayraçlı, gereksiz sıfırsız): 12.5, 16."""
    if value is None or value == "":
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(d.normalize(), "f")


@register.filter
def tr_miktar_sade(value, basamak=3):
    """Miktar gösterimi (ÜRETİM > Ürün Ağacı): tam sayı ondalıksız ("1", "1.250"), kesirli
    ``basamak`` ondalıkla (0,056). Tam sayı kontrolü YUVARLAMADAN SONRA yapılır: zincir
    bölmeleri 9,999999… gibi değerler üretebilir, ekranda "10,000" değil "10" görünmeli.
    ``tr_kur`` değiştirilmez — 30 şablonda kur/tutar için kullanılıyor."""
    if value is None or value == "":
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    q = yuvarla(d, basamak)
    return format_tr(q, 0 if q == q.to_integral_value() else basamak)


@register.filter
def tr_miktar6(value):
    """Miktar gösterimi: en az 3, gerekirse 6 ondalık (kesirli girdi/tüketim): "50,000", "0,077",
    "0,015625". ``tr_kur:3`` miktarı 3 ondalığa yuvarlayıp 1/64 gibi değerleri bozar."""
    if value is None or value == "":
        return ""
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    q = yuvarla(d, 6)
    basamak = max(3, -q.normalize().as_tuple().exponent)
    return format_tr(q, basamak)


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
