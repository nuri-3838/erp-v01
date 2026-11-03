"""Teklif / Proforma / Sipariş (ve diğer teklif-sipariş belgeleri) — sade, resimsiz Excel dökümü.

Tablo yalnız 6 sütun: Model Kodu, Ürün Adı, Adet, Net Birim Fiyat, Birim Brüt KG, Birim CBM. Toplam satırı YOKTUR.
PDF ile AYNI kaynaktan beslenir: dile göre ad (``Stok.ad_dil``), seçenek çevirileri, teslim notu ve not satırları PDF bağlamından
(``core.views.satis_*_pdf_baglam``) gelir; fiyat kalem modelinin property'leriyle hesaplanır (``net_birim_fiyat``, navlunlu satış
teklifinde PDF'teki gibi ``nakliye_dahil_fiyat``) — yeni bir hesap kuralı yoktur. Hiçbir değer saklanmaz; her indirmede canlı üretilir.
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

_PARA = "#,##0.00"
_FIYAT = "#,##0.00##"               # net birim fiyat 4 ondalıklı (nakliye dahil de)
_KG = "#,##0.00"
_CBM = "0.0000"
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
        "model": "Model Kodu", "urun": "Ürün Adı", "adet": "Adet", "net": "Net Birim Fiyat", "kg": "Birim Brüt KG", "cbm": "Birim CBM",
        "musteri": "Alıcı", "tedarikci": "Tedarikçi", "tarih": "Tarih", "gecerlilik": "Geçerlilik tarihi",
        "teslim_tarihi": "Teslim tarihi", "para": "Para birimi", "kur": "Kur (TL)", "odeme": "Ödeme koşulu",
        "sekil": "Teslim / yükleme şekli", "tip": "Yükleme tipi", "sure": "Teslim süresi", "navlun": "Navlun",
        "hazirlayan": "Hazırlayan", "teslim_notu": "Teslim notu", "not": "Not", "notlar": "Notlar", "varyant": "Varyant",
    },
    "en": {
        "model": "Model Code", "urun": "Product Name", "adet": "Qty", "net": "Net Unit Price", "kg": "Unit Gross KG", "cbm": "Unit CBM",
        "musteri": "To", "tedarikci": "Supplier", "tarih": "Date", "gecerlilik": "Valid until",
        "teslim_tarihi": "Delivery date", "para": "Currency", "kur": "Rate (TRY)", "odeme": "Payment term",
        "sekil": "Delivery term", "tip": "Transport mode", "sure": "Lead time", "navlun": "Freight",
        "hazirlayan": "Prepared by", "teslim_notu": "Delivery note", "not": "Note", "notlar": "Notes", "varyant": "Variant",
    },
}

NCOL = 6
_GENISLIK = (24, 46, 10, 22, 18, 16)


def _yaz(ws, satir, sutun, deger, *, bicim=None, kalin=False, hiza=None, dolgu=None, kenar=False, sar=False):
    """Hücreyi yazar. '=' ile başlayan metin formül sayılmasın diye metin tipine zorlanır; None -> boş hücre."""
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


def _adet(miktar):
    """Tam sayı adet tam sayı hücre olarak; ondalıklı (nadir) miktar ondalığıyla."""
    if miktar == miktar.to_integral_value():
        return int(miktar), "#,##0"
    return miktar, "#,##0.###"


def teklif_siparis_xlsx(ts, kalemler, ctx, dil, *, firma_unvan="", navlunlu=False) -> bytes:
    """``ts`` belge, ``kalemler`` (select_related stok) kalem listesi, ``ctx`` PDF bağlamı (alış belgelerinde ``{}``).
    ``navlunlu``: satış teklifinde navlun varsa fiyat sütunu PDF'teki gibi nakliye dahil birim fiyattır (hesaplanamayan kalemde net fiyat)."""
    E = _E[dil]
    tur = ts.belge_tur
    satis = ts.yon == ts.Yon.SATIS
    wb = Workbook()
    ws = wb.active
    ws.title = (_BASLIK_EN if dil == "en" else _BASLIK_TR).get(tur, "Belge")[:31]
    for i, g in enumerate(_GENISLIK, start=1):
        ws.column_dimensions[get_column_letter(i)].width = g

    # --- üst blok ---
    _birlestir_yaz(ws, 1, 1, NCOL, firma_unvan or "", kalin=True).font = Font(bold=True, size=14)
    baslik = (_BASLIK_EN if dil == "en" else _BASLIK_TR).get(tur, "")
    _birlestir_yaz(ws, 2, 1, NCOL, f"{baslik} — {ts.belge_no or ts.pk}").font = Font(bold=True, size=12)

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
        for (et, dg), (e1, e2, d1, d2) in zip(ogeler[i:i + 2], ((1, 1, 2, 2), (3, 4, 5, 6))):
            _birlestir_yaz(ws, satir, e1, e2, et, kalin=True, dolgu=_ALT_DOLGU)
            if isinstance(dg, date):
                _birlestir_yaz(ws, satir, d1, d2, dg, bicim=_TARIH, hiza="left")
            elif isinstance(dg, Decimal):
                _birlestir_yaz(ws, satir, d1, d2, dg, bicim=_PARA, hiza="left")
            else:
                _birlestir_yaz(ws, satir, d1, d2, dg, hiza="left", sar=True)
        satir += 1
    if ctx.get("teslim_notu"):
        _yaz(ws, satir, 1, E["teslim_notu"], kalin=True, dolgu=_ALT_DOLGU)
        _birlestir_yaz(ws, satir, 2, NCOL, ctx["teslim_notu"], hiza="left")
        satir += 1

    # --- satır tablosu (6 sütun; toplam satırı yok) ---
    baslik_satiri = satir
    basliklar = (E["model"], E["urun"], E["adet"], f"{E['net']} ({ts.para_birimi})", E["kg"], E["cbm"])
    for c, b in enumerate(basliklar, start=1):
        h = _yaz(ws, baslik_satiri, c, b, kenar=True)
        h.font, h.fill = _BASLIK_YAZI, _BASLIK_DOLGU
        h.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[baslik_satiri].height = 32

    for k in kalemler:
        satir += 1
        ad = getattr(k, "urun_ad", None) or k.stok.ad_dil(dil)
        varyant = getattr(k, "varyant_metni", "")
        if varyant:
            ad = f"{ad} ({E['varyant']}: {varyant})"
        fiyat = k.nakliye_dahil_fiyat if navlunlu else None
        if fiyat is None:
            fiyat = k.net_birim_fiyat
        adet, adet_bicim = _adet(k.miktar)
        degerler = (
            (k.stok.model_kodu or None, None), (ad, None), (adet, adet_bicim), (fiyat, _FIYAT),
            (k.stok.agirlik, _KG), (k.stok.cbm, _CBM),
        )
        for c, (dg, bicim) in enumerate(degerler, start=1):
            _yaz(ws, satir, c, dg, bicim=bicim, kenar=True, sar=(c == 2))

    # --- notlar ---
    notlar = []
    if ts.aciklama:
        notlar.append((E["not"], [ts.aciklama]))
    if ctx.get("notlar"):
        notlar.append((E["notlar"], list(ctx["notlar"])))
    satir += 1
    for etiket, satirlar in notlar:
        satir += 1
        _yaz(ws, satir, 1, etiket, kalin=True)
        for metin in satirlar:
            satir += 1
            _birlestir_yaz(ws, satir, 1, NCOL, metin, hiza="left", sar=True)

    ws.freeze_panes = ws.cell(row=baslik_satiri + 1, column=1)
    ws.print_title_rows = f"{baslik_satiri}:{baslik_satiri}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.print_area = f"A1:{get_column_letter(NCOL)}{satir}"
    cikti = BytesIO()
    wb.save(cikti)
    return cikti.getvalue()
