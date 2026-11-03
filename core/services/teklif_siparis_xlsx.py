"""Teklif / Proforma / Sipariş (ve diğer teklif-sipariş belgeleri) — resimsiz Excel dökümü.

PDF ile AYNI kaynaktan beslenir: dile göre ad (``Stok.ad_dil``), seçenek çevirileri, teslim notu ve not
satırları PDF bağlamından (``core.views.satis_*_pdf_baglam``) gelir; tutarlar kalem modelinin
property'leriyle (``tutar``, ``net_birim_fiyat``, ``kdv_tutari``) hesaplanır — yeni bir hesap kuralı yoktur.
Satış belgesinde yurt dışı alıcıya KDV uygulanmaz (proforma/sipariş PDF'iyle aynı kural).

Hiçbir değer saklanmaz; her indirmede canlı üretilir.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from core.sayi import yuvarla

_SIFIR = Decimal("0")
_PARA = "#,##0.00"
_LISTE_FIYAT = "#,##0.00####"       # alan 6 ondalıklı
_NET_FIYAT = "#,##0.00##"           # net birim fiyat 4 ondalıklı
_YUZDE = "0.00"
_TARIH = "DD.MM.YYYY"
_BASLIK_DOLGU = PatternFill("solid", fgColor="21385F")
_BASLIK_YAZI = Font(bold=True, color="FFFFFF")
_ALT_DOLGU = PatternFill("solid", fgColor="EEF1F6")
_INCE = Side(style="thin", color="B8C1D1")
_KENAR = Border(left=_INCE, right=_INCE, top=_INCE, bottom=_INCE)

_BASLIK_TR = {"TEKLIF": "Teklif", "PROFORMA": "Proforma Fatura", "SIPARIS": "Sipariş Onayı", "IRSALIYE": "İrsaliye"}
_BASLIK_EN = {"TEKLIF": "Quotation", "PROFORMA": "Proforma Invoice", "SIPARIS": "Order Confirmation", "IRSALIYE": "Delivery Note"}
_NO_TR = {"TEKLIF": "Teklif No", "PROFORMA": "Proforma No", "SIPARIS": "Sipariş No", "IRSALIYE": "İrsaliye No"}
_NO_EN = {"TEKLIF": "Quotation No", "PROFORMA": "Proforma No", "SIPARIS": "Order No", "IRSALIYE": "Delivery Note No"}

_E = {
    "tr": {
        "sira": "Sıra", "stok_kodu": "Stok kodu", "urun": "Ürün adı", "model": "Model kodu", "adet": "Adet", "birim": "Birim",
        "liste": "Liste fiyatı", "iskonto": "İskonto %", "net": "Net birim fiyat", "tutar": "Tutar", "kdv_oran": "KDV %",
        "kdv_tutar": "KDV tutarı", "kdv_dahil": "KDV dahil tutar", "nakliye": "Nakliye dahil birim fiyat",
        "musteri": "Alıcı", "tedarikci": "Tedarikçi", "tarih": "Tarih", "gecerlilik": "Geçerlilik tarihi",
        "teslim_tarihi": "Teslim tarihi", "para": "Para birimi", "kur": "Kur (TL)", "odeme": "Ödeme koşulu",
        "sekil": "Teslim / yükleme şekli", "tip": "Yükleme tipi", "sure": "Teslim süresi", "navlun": "Navlun",
        "hazirlayan": "Hazırlayan", "teslim_notu": "Teslim notu",
        "ara": "Ara toplam (iskonto sonrası)", "isk_toplam": "İskonto toplamı", "kdv_toplam": "KDV toplamı",
        "genel": "Genel toplam", "tevkifat": "Tevkifat (−)", "odenecek": "Ödenecek", "navlun_dahil": "Toplam (navlun dahil)",
        "not": "Not", "notlar": "Notlar", "varyant": "Varyant",
    },
    "en": {
        "sira": "No", "stok_kodu": "Item code", "urun": "Item", "model": "Model code", "adet": "Qty", "birim": "Unit",
        "liste": "List price", "iskonto": "Discount %", "net": "Net unit price", "tutar": "Amount", "kdv_oran": "VAT %",
        "kdv_tutar": "VAT amount", "kdv_dahil": "Amount incl. VAT", "nakliye": "Unit price incl. freight",
        "musteri": "To", "tedarikci": "Supplier", "tarih": "Date", "gecerlilik": "Valid until",
        "teslim_tarihi": "Delivery date", "para": "Currency", "kur": "Rate (TRY)", "odeme": "Payment term",
        "sekil": "Delivery term", "tip": "Transport mode", "sure": "Lead time", "navlun": "Freight",
        "hazirlayan": "Prepared by", "teslim_notu": "Delivery note",
        "ara": "Subtotal (after discount)", "isk_toplam": "Discount total", "kdv_toplam": "VAT total",
        "genel": "Grand total", "tevkifat": "Withholding (−)", "odenecek": "Total due", "navlun_dahil": "Total (incl. freight)",
        "not": "Note", "notlar": "Notes", "varyant": "Variant",
    },
}

# Sıra, Stok kodu, Ürün adı, Model kodu, Adet, Birim, Liste fiyatı, İskonto %, Net birim fiyat, Tutar, KDV %, KDV tutarı, KDV dahil tutar (+ Nakliye dahil, yalnız navlunlu satış teklifi)
_GENISLIK = (6, 16, 40, 14, 10, 9, 15, 10, 15, 15, 8, 14, 17, 18)


def _yaz(ws, satir, sutun, deger, *, bicim=None, kalin=False, hiza=None, dolgu=None, kenar=False, sar=False):
    """Hücreyi yazar. '=' ile başlayan metin formül sayılmasın diye metin tipine zorlanır."""
    h = ws.cell(row=satir, column=sutun)
    h.value = deger
    if isinstance(deger, str) and deger.startswith("="):
        h.data_type = "s"
    if bicim:
        h.number_format = bicim
    if kalin:
        h.font = Font(bold=True)
    if hiza or sar:
        h.alignment = Alignment(horizontal=hiza, vertical="center" if not sar else "top", wrap_text=sar)
    if dolgu:
        h.fill = dolgu
    if kenar:
        h.border = _KENAR
    return h


def _birlestir_yaz(ws, satir, bas, son, deger, **kw):
    if son > bas:
        ws.merge_cells(start_row=satir, start_column=bas, end_row=satir, end_column=son)
    return _yaz(ws, satir, bas, deger, **kw)


def teklif_siparis_xlsx(ts, kalemler, ctx, dil, *, firma_unvan="", nakliye_dahil=False) -> bytes:
    """``ts`` belge, ``kalemler`` (select_related stok/kdv/tevkifat) kalem listesi, ``ctx`` PDF bağlamı (alış belgelerinde ``{}``).
    ``nakliye_dahil``: PDF'te kalem fiyatı navlun dahil gösteriliyorsa (satış teklifi + navlun) son sütun olarak eklenir."""
    E = _E[dil]
    tur = ts.belge_tur
    satis = ts.yon == ts.Yon.SATIS
    yurt_ici = ctx.get("yurt_ici", True)
    ncol = 14 if nakliye_dahil else 13
    wb = Workbook()
    ws = wb.active
    ws.title = (_BASLIK_EN if dil == "en" else _BASLIK_TR).get(tur, "Belge")[:31]
    for i, g in enumerate(_GENISLIK[:ncol], start=1):
        ws.column_dimensions[get_column_letter(i)].width = g

    # --- üst blok ---
    _birlestir_yaz(ws, 1, 1, ncol, firma_unvan or "", kalin=True).font = Font(bold=True, size=14)
    baslik = (_BASLIK_EN if dil == "en" else _BASLIK_TR).get(tur, "")
    _birlestir_yaz(ws, 2, 1, ncol, f"{baslik} — {ts.belge_no or ts.pk}").font = Font(bold=True, size=12)

    ogeler = [
        (E["musteri"] if satis else E["tedarikci"], ts.taraf.unvan),
        ((_NO_EN if dil == "en" else _NO_TR).get(tur, "No"), ts.belge_no or str(ts.pk)),
        (E["tarih"], ts.tarih),
    ]
    if ts.gecerlilik_teslim_tarihi:
        ogeler.append((E["teslim_tarihi"] if tur == "SIPARIS" else E["gecerlilik"], ts.gecerlilik_teslim_tarihi))
    ogeler.append((E["para"], ts.para_birimi))
    if ts.kur and ts.para_birimi != "TRY":
        ogeler.append((E["kur"], ts.kur))
    for anahtar, ad_anahtari, secenek in (
            ("odeme", "odeme_kosulu_ad", ts.odeme_kosulu), ("sekil", "yukleme_sekli_ad", ts.yukleme_sekli),
            ("tip", "yukleme_tipi_ad", ts.yukleme_tipi), ("sure", "teslim_suresi_ad", ts.teslim_suresi)):
        ad = ctx.get(ad_anahtari) or (secenek.ad_dil(dil) if secenek else "")
        if ad:
            ogeler.append((E[anahtar], ad))
    if ts.navlun_tutari is not None:
        ogeler.append((E["navlun"], ts.navlun_tutari))
    if ctx.get("hazirlayan"):
        ogeler.append((E["hazirlayan"], ctx["hazirlayan"]))

    satir = 3
    for i in range(0, len(ogeler), 2):
        for (et, dg), (e1, e2, d1, d2) in zip(ogeler[i:i + 2], ((1, 2, 3, 5), (7, 9, 10, ncol))):
            _birlestir_yaz(ws, satir, e1, e2, et, kalin=True, dolgu=_ALT_DOLGU)
            if isinstance(dg, date):
                _birlestir_yaz(ws, satir, d1, d2, dg, bicim=_TARIH, hiza="left")
            elif isinstance(dg, Decimal):
                _birlestir_yaz(ws, satir, d1, d2, dg, bicim=_PARA, hiza="left")
            else:
                _birlestir_yaz(ws, satir, d1, d2, dg, hiza="left")
        satir += 1
    if ctx.get("teslim_notu"):
        _birlestir_yaz(ws, satir, 1, 2, E["teslim_notu"], kalin=True, dolgu=_ALT_DOLGU)
        _birlestir_yaz(ws, satir, 3, ncol, ctx["teslim_notu"], hiza="left")
        satir += 1

    # --- satır tablosu ---
    baslik_satiri = satir
    basliklar = [E[a] for a in ("sira", "stok_kodu", "urun", "model", "adet", "birim", "liste", "iskonto", "net", "tutar", "kdv_oran", "kdv_tutar", "kdv_dahil")]
    if nakliye_dahil:
        basliklar.append(E["nakliye"])
    for c, b in enumerate(basliklar, start=1):
        h = _yaz(ws, baslik_satiri, c, b, kenar=True)
        h.font, h.fill = _BASLIK_YAZI, _BASLIK_DOLGU
        h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[baslik_satiri].height = 32

    ara = isk_toplam = kdv_toplam = _SIFIR
    for sira, k in enumerate(kalemler, start=1):
        satir += 1
        ad = getattr(k, "urun_ad", None) or k.stok.ad_dil(dil)
        varyant = getattr(k, "varyant_metni", "")
        if varyant:
            ad = f"{ad} ({E['varyant']}: {varyant})"
        oran = k.kdv.oran if (k.kdv_id and yurt_ici) else _SIFIR
        kdv_tutar = yuvarla(k.tutar * oran / Decimal("100"), 2)
        liste_tutar = yuvarla(k.miktar * k.birim_fiyat, 2)
        ara += k.tutar
        isk_toplam += liste_tutar - k.tutar
        kdv_toplam += kdv_tutar
        adet_bicim = "#,##0" if k.miktar == k.miktar.to_integral_value() else "#,##0.000"
        degerler = (
            (sira, "0"), (k.stok.kod, None), (ad, None), (k.stok.model_kodu or "", None), (k.miktar, adet_bicim),
            (k.stok.fatura_birimi.kisa_ad, None), (k.birim_fiyat, _LISTE_FIYAT), (k.iskonto_yuzdesi, _YUZDE),
            (k.net_birim_fiyat, _NET_FIYAT), (k.tutar, _PARA), (oran, _YUZDE), (kdv_tutar, _PARA), (k.tutar + kdv_tutar, _PARA),
        )
        for c, (dg, bicim) in enumerate(degerler, start=1):
            _yaz(ws, satir, c, dg, bicim=bicim, kenar=True, sar=(c == 3))
        if nakliye_dahil:
            _yaz(ws, satir, ncol, k.nakliye_dahil_fiyat, bicim=_NET_FIYAT, kenar=True)
    son_satir = satir

    # --- alt blok ---
    satir += 2
    genel = ara + kdv_toplam
    toplamlar = [(E["ara"], ara), (E["isk_toplam"], isk_toplam), (E["kdv_toplam"], kdv_toplam), (E["genel"], genel)]
    tevkifat = ctx.get("tevkifat_toplam", ts.tevkifat_toplam)
    if tevkifat:
        toplamlar += [(E["tevkifat"], tevkifat), (E["odenecek"], genel - tevkifat)]
    if ctx.get("toplam_navlun_dahil") is not None:
        toplamlar.append((E["navlun_dahil"], ctx["toplam_navlun_dahil"]))
    for et, dg in toplamlar:
        _birlestir_yaz(ws, satir, 8, 12, et, kalin=True, hiza="right")
        _yaz(ws, satir, 13, dg, bicim=_PARA, kalin=True, kenar=True)          # KDV dahil tutar sütununun altı
        satir += 1
    notlar = []
    if ts.aciklama:
        notlar.append((E["not"], [ts.aciklama]))
    if ctx.get("notlar"):
        notlar.append((E["notlar"], list(ctx["notlar"])))
    for etiket, satirlar in notlar:
        satir += 1
        _yaz(ws, satir, 1, etiket, kalin=True)
        for metin in satirlar:
            satir += 1
            _birlestir_yaz(ws, satir, 1, ncol, metin, hiza="left")

    ws.freeze_panes = ws.cell(row=baslik_satiri + 1, column=1)
    ws.print_title_rows = f"{baslik_satiri}:{baslik_satiri}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.print_area = f"A1:{get_column_letter(ncol)}{satir}"
    cikti = BytesIO()
    wb.save(cikti)
    return cikti.getvalue()
