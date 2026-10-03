"""UBL-TR e-fatura/e-arşiv XML okuyucu (yalnız OKUR; imzalı belgeyi değiştirmez).

`ubl_oku(bayt)` → fatura no, satıcı VKN/TCKN + unvan, fatura tarihi, para birimi, ödenecek tutar
(PayableAmount) ve KDV dahil toplam (TaxInclusiveAmount). Dış varlık/DOCTYPE içeren belge
reddedilir (XXE / entity patlaması); boyut sınırı çağıranda uygulanır."""
from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

NS = {
    "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}


class UblHatasi(ValueError):
    """XML UBL fatura olarak okunamadı (Türkçe mesaj)."""


def _metin(kok, yol):
    el = kok.find(yol, NS)
    return (el.text or "").strip() if el is not None and el.text else ""


def _tutar(kok, yol):
    s = _metin(kok, yol)
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        raise UblHatasi(f"Tutar okunamadı: {yol}")


def ubl_oku(bayt: bytes) -> dict:
    if b"<!DOCTYPE" in bayt[:4096].upper() or b"<!ENTITY" in bayt.upper():
        raise UblHatasi("DOCTYPE/ENTITY içeren XML kabul edilmez.")
    try:
        kok = ET.fromstring(bayt)
    except ET.ParseError as e:
        raise UblHatasi(f"XML ayrışmadı: {e}")
    if kok.tag != "{%s}Invoice" % NS["inv"]:
        raise UblHatasi("Kök öğe UBL Invoice değil.")
    fatura_no = _metin(kok, "cbc:ID")
    tarih_s = _metin(kok, "cbc:IssueDate")
    if not fatura_no or not tarih_s:
        raise UblHatasi("Fatura no / tarih yok.")
    try:
        tarih = datetime.date.fromisoformat(tarih_s)
    except ValueError:
        raise UblHatasi(f"Tarih okunamadı: {tarih_s}")
    satici = kok.find("cac:AccountingSupplierParty/cac:Party", NS)
    vkn, unvan = "", ""
    if satici is not None:
        for pid in satici.findall("cac:PartyIdentification/cbc:ID", NS):
            if (pid.get("schemeID") or "").upper() in ("VKN", "TCKN") and (pid.text or "").strip():
                vkn = pid.text.strip()
                break
        unvan = _metin(satici, "cac:PartyName/cbc:Name")
        if not unvan:
            ad = _metin(satici, "cac:Person/cbc:FirstName")
            soyad = _metin(satici, "cac:Person/cbc:FamilyName")
            unvan = f"{ad} {soyad}".strip()
    para = _metin(kok, "cbc:DocumentCurrencyCode") or "TRY"
    odenecek = _tutar(kok, "cac:LegalMonetaryTotal/cbc:PayableAmount")
    kdv_dahil = _tutar(kok, "cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount")
    if odenecek is None and kdv_dahil is None:
        raise UblHatasi("Tutar yok.")
    return {"fatura_no": fatura_no, "tarih": tarih, "vkn": vkn, "unvan": unvan,
            "para_birimi": para, "odenecek": odenecek, "kdv_dahil": kdv_dahil}
