"""İNSAN KAYNAKLARI > İşe giriş > MUHASEBE PAKETİ (Dilim 3).

Resmî işe girişi (SGK 4/A işe giriş bildirgesi) muhasebe/mali müşavir yapar; ERP resmî kanala girmez. Bu servis muhasebeye iletilecek bilgileri
personel kartından toplar: "hazır mı?" kontrolü (eksik zorunlu alanlar + işe girişe kalan gün uyarısı), Excel/PDF bilgi paketi ve "muhasebeye
gönderildi" işareti. Hiçbir değer saklanmaz; her indirmede canlı üretilir (yalnız gönderim tarihi ``IseGirisKaydi``'ne yazılır).
İÇERİK bilinçli olarak SADELEŞTİRİLMİŞTİR (2026-11-01): yalnız bildirgeyi düzenlemek için gereken İŞVEREN / SİGORTALI / İŞE GİRİŞ alanları (+ yetkiliye
ÜCRET). Cinsiyet, medeni durum, çocuk sayısı, e-posta, banka/IBAN, deneme süresi, çalışma günleri, ücret geçerlilik ve ödeme günü YOKTUR.
Brüt ücret YAZILMAZ (net→brüt çevirisi yapılmaz; kullanıcı kararı): ücret bölümünde net tutar + "asgari ücret: evet/hayır".
Ücret yalnız ``ucret_gorebilir`` (personel_ucret yetkisi) olana dahil edilir.
"""
from __future__ import annotations

from io import BytesIO

from django.db import transaction
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins

from core.metin import buyuk_harf_tr
from core.models import IkAyari, Personel
from core.sayi import format_tr
from core.services import ise_giris as ig
from core.tarih import tr_bugun

# (alan, görünen ad) — bunlar yoksa SGK işe giriş bildirgesi hazırlanamaz; "hazır" bunlara bağlıdır.
ZORUNLU_ALANLAR = (
    ("tc_kimlik_no", "TC kimlik no"), ("dogum_tarihi", "Doğum tarihi"), ("dogum_yeri", "Doğum yeri"), ("baba_adi", "Baba adı"),
    ("ana_adi", "Ana adı"), ("adres", "Adres"), ("gorev", "Görev"), ("ise_giris_tarihi", "İşe giriş tarihi"), ("meslek_kodu", "Meslek kodu"),
)
# Önerilen (eksikse paket hazır SAYILIR, yalnız uyarı çıkar): iletişim.
ONERILEN_ALANLAR = (("telefon", "Telefon"),)
ALT_NOT = "SGK işe giriş bildirgesi işe başlama tarihinden en geç 1 gün önce verilmelidir."

_TR_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_BASLIK_DOLGU = PatternFill("solid", fgColor="21385F")
_BOLUM_DOLGU = PatternFill("solid", fgColor="DDE3EE")
_EKSIK_DOLGU = PatternFill("solid", fgColor="FFCDD2")


def _bos(deger) -> bool:
    return deger is None or str(deger).strip() == ""


def _kismi_saat_eksik(kayit) -> bool:
    """Kısmi süreli seçilmiş ama bu personel için haftalık çalışma saati girilmemiş (firma varsayılanı tam süreyi gösterir; kısmi için anlamsız)."""
    return kayit.calisma_sekli == kayit.CalismaSekli.KISMI and kayit.haftalik_calisma_saati is None


def hazirlik(personel: Personel, kayit, *, bugun=None) -> dict:
    """Muhasebe için hazır mı? {"hazir", "eksikler": [ad], "onerilen_eksikler": [ad], "kalan_gun", "durum": "gecmis|acil|normal|belirsiz", "uyari",
    "gonderildi": date|None}. Eksik bilgi paketi indirmeyi ENGELLEMEZ (eksikler Excel'de işaretlenir)."""
    bugun = bugun or tr_bugun()
    eksikler = [ad for alan, ad in ZORUNLU_ALANLAR if _bos(getattr(personel, alan))]
    if _kismi_saat_eksik(kayit):
        eksikler.append("Haftalık çalışma saati (kısmi süreli)")
    onerilen = [ad for alan, ad in ONERILEN_ALANLAR if _bos(getattr(personel, alan))]
    kalan = (personel.ise_giris_tarihi - bugun).days if personel.ise_giris_tarihi else None
    gonderildi = kayit.muhasebe_gonderim_tarihi
    if kalan is None:
        durum, uyari = "belirsiz", ""
    elif kalan < 0:
        durum, uyari = "gecmis", f"Personel {-kalan} gün önce işe başladı. İşe giriş bildirgesi işe başlamadan önce verilmeli; muhasebeye hemen iletin."
    elif kalan <= 1:
        durum, uyari = "acil", "İşe giriş bildirgesi işe başlamadan önce verilmeli; muhasebeye bugün iletin." if kalan == 0 else \
            "İşe başlamaya 1 gün kaldı. İşe giriş bildirgesi işe başlamadan önce verilmeli; muhasebeye bugün iletin."
    else:
        durum, uyari = "normal", f"İşe girişe {kalan} gün var. İşe giriş bildirgesi işe başlamadan önce verilmeli."
    if gonderildi:                                                      # gönderilmişse süre baskısı gösterilmez
        uyari = ""
    return {"hazir": not eksikler, "eksikler": eksikler, "onerilen_eksikler": onerilen, "kalan_gun": kalan, "durum": durum, "uyari": uyari,
            "gonderildi": gonderildi}


def _ucret_satirlari(personel: Personel, bugun) -> list:
    """Aylık net ücret + "asgari ücret: evet/hayır". Brüt YAZILMAZ. Ücret kaydı yoksa ikisi de boş."""
    from core.services import personel_ucret as ucret_servis
    tarih = max(bugun, personel.ise_giris_tarihi) if personel.ise_giris_tarihi else bugun
    u = ucret_servis.gecerli_ucret(personel, tarih)
    if u is None:
        return [("Aylık net ücret", "", False), ("Asgari ücret", "", False)]
    if u.tip == "ASGARI":
        return [("Aylık net ücret", "Asgari ücret", False), ("Asgari ücret", "Evet", False)]
    return [("Aylık net ücret", ig.ucret_metni(personel, bugun=bugun), False), ("Asgari ücret", "Hayır", False)]


def _calisma_sekli(kayit) -> str:
    """Tam süreli ya da "Kısmi süreli (haftalık X saat)" (X = bu personel için girilen haftalık saat)."""
    if kayit.calisma_sekli != kayit.CalismaSekli.KISMI:
        return "Tam süreli"
    saat = kayit.haftalik_calisma_saati
    return f"Kısmi süreli (haftalık {format_tr(saat, 1).replace(',0', '')} saat)" if saat is not None else "Kısmi süreli (haftalık saat girilmedi)"


def bolumler(personel: Personel, firma, kayit, *, ucret_gorebilir=False, bugun=None, ayar=None) -> list:
    """SGK 4/A işe giriş bildirgesi için SADE içerik: [(bölüm adı, [(etiket, değer, zorunlu_mu)])] — İŞVEREN, SİGORTALI, İŞE GİRİŞ (+ ÜCRET, yetkiliye).
    Boş değer "" kalır (Excel'de işaretlenir, PDF'te noktalı çizgi)."""
    bugun = bugun or tr_bugun()
    gun = lambda d: d.strftime("%d.%m.%Y") if d else ""
    secenek = lambda alan: getattr(personel, f"get_{alan}_display")() if getattr(personel, alan) else ""
    zorunlu = {alan for alan, _ in ZORUNLU_ALANLAR}
    z = lambda alan: alan in zorunlu
    gorev_departman = " / ".join(x for x in (personel.gorev, personel.departman) if x) if personel.gorev else ""
    out = [
        ("İşveren", [("Unvan", firma.unvan, False), ("SGK işyeri sicil no", firma.sgk_isyeri_sicil_no, False),
                     ("Vergi dairesi", firma.vergi_dairesi, False), ("Vergi no", firma.vergi_no, False)]),
        ("Sigortalı", [
            ("T.C. Kimlik No", personel.tc_kimlik_no, z("tc_kimlik_no")), ("Adı", personel.ad, False), ("Soyadı", personel.soyad, False),
            ("Doğum tarihi", gun(personel.dogum_tarihi), z("dogum_tarihi")), ("Doğum yeri", personel.dogum_yeri, z("dogum_yeri")),
            ("Baba adı", personel.baba_adi, z("baba_adi")), ("Ana adı", personel.ana_adi, z("ana_adi")),
            ("Uyruk", personel.uyruk or "T.C.", False), ("Öğrenim durumu", secenek("ogrenim_durumu"), False),
            ("Adres", " ".join((personel.adres or "").split()), z("adres")), ("Telefon", personel.telefon, False)]),
        ("İşe Giriş", [
            ("İşe giriş tarihi", gun(personel.ise_giris_tarihi), z("ise_giris_tarihi")), ("Meslek kodu", personel.meslek_kodu, z("meslek_kodu")),
            ("Meslek adı", personel.meslek_adi, False), ("Görev / Departman", gorev_departman, z("gorev")),
            ("Sigorta kolu", "SGDP (02)" if personel.emekli_sgdp else "Tüm sigorta kolları (01)", False),
            ("Çalışma şekli", _calisma_sekli(kayit), False), ("Sözleşme türü", "Belirsiz süreli", False)]),
    ]
    if ucret_gorebilir:
        out.append(("Ücret", _ucret_satirlari(personel, bugun)))
    return out


def dosya_adi(personel: Personel, uzanti: str, bugun=None) -> str:
    bugun = bugun or tr_bugun()
    temiz = "_".join("".join(c if c.isalnum() else " " for c in personel.ad_soyad.translate(_TR_ASCII)).split()).lower()
    return f"ise_giris_muhasebe_{temiz or personel.pk}_{bugun:%Y%m%d}.{uzanti}"


def excel(personel: Personel, firma, kayit, *, ucret_gorebilir=False, bugun=None, ayar=None) -> bytes:
    """Tek sayfa, TEK SATIR: üstte bölüm bandı (İşveren / Sigortalı / İşe Giriş / Ücret), altında alan başlıkları, altında personelin tek veri satırı.
    Zorunlu olup boş alanlar kırmızı "EKSİK". Altta SGK bildirge süresi notu. A4 yatay, yazdırılabilir."""
    bugun = bugun or tr_bugun()
    kitap = Workbook()
    ws = kitap.active
    ws.title = "SGK 4A İşe Giriş"
    ws.cell(row=1, column=1, value=f"SGK 4/A İŞE GİRİŞ BİLDİRGESİ BİLGİLERİ — {personel.ad_soyad}").font = Font(bold=True, size=13)
    ws.cell(row=2, column=1, value=f"Hazırlanma tarihi: {bugun:%d.%m.%Y}").font = Font(italic=True)
    sutun = 1
    for baslik, alanlar in bolumler(personel, firma, kayit, ucret_gorebilir=ucret_gorebilir, bugun=bugun, ayar=ayar):
        ilk = sutun
        for etiket, deger, zorunlu in alanlar:
            e = ws.cell(row=5, column=sutun, value=etiket)
            e.font, e.fill = Font(bold=True), _BOLUM_DOLGU
            e.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            v = ws.cell(row=6, column=sutun, value=deger if not _bos(deger) else ("EKSİK" if zorunlu else ""))
            v.number_format = "@"                                       # TC/telefon/tarih metin kalsın (baştaki sıfır, bilimsel gösterim yok)
            v.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            if zorunlu and _bos(deger):
                v.fill, v.font = _EKSIK_DOLGU, Font(bold=True, color="B71C1C")
            ws.column_dimensions[get_column_letter(sutun)].width = 46 if etiket == "Adres" else max(14, min(30, len(etiket) + 6, max(len(str(deger or "")) + 2, 14)))
            sutun += 1
        for k in range(ilk, sutun):
            h = ws.cell(row=4, column=k, value=buyuk_harf_tr(baslik) if k == ilk else None)
            h.font, h.fill = Font(bold=True, color="FFFFFF"), _BASLIK_DOLGU
        if sutun - 1 > ilk:
            ws.merge_cells(start_row=4, start_column=ilk, end_row=4, end_column=sutun - 1)
        ws.cell(row=4, column=ilk).alignment = Alignment(horizontal="center")
    ws.cell(row=8, column=1, value=ALT_NOT).font = Font(italic=True, bold=True)
    ws.row_dimensions[5].height = 32
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.6, bottom=0.6)
    buf = BytesIO()
    kitap.save(buf)
    return buf.getvalue()


def pdf_belgesi(personel: Personel, firma, kayit, *, ucret_gorebilir=False, bugun=None, ayar=None) -> dict:
    """Evrak paketi PDF şablonu için tek belge (imzasız): ``ise_giris.paket_hazirla`` çıktısıyla aynı sözlük biçimi."""
    from core.services.belge_sablon import BOS
    ps = [(b, [(e, d if not _bos(d) else BOS) for e, d, _z in a])
          for b, a in bolumler(personel, firma, kayit, ucret_gorebilir=ucret_gorebilir, bugun=bugun, ayar=ayar)]
    return {"kod": "MUHASEBE_BILGI", "baslik": "İŞE GİRİŞ BİLGİ FORMU (SGK 4/A — MUHASEBE İÇİN)", "bolumler": ps,
            "paragraflar": [ALT_NOT], "imza": "yok", "imza_notu": ""}


@transaction.atomic
def gonderildi_isaretle(personel: Personel, *, tarih=None, geri_al=False, kullanici=None, bugun=None):
    """"Muhasebeye gönderildi" tarihini yazar (yalnız o alan); ``geri_al`` ise temizler. Gelecek tarih reddedilir."""
    bugun = bugun or tr_bugun()
    if personel.silindi:
        raise ig.IseGirisHatasi("Silinmiş personel için işlem yapılamaz.")
    if not geri_al:
        tarih = tarih or bugun
        if tarih > bugun:
            raise ig.IseGirisHatasi("Gönderim tarihi gelecekte olamaz.")
    kayit = ig.kayit_oku(personel)
    kayit.muhasebe_gonderim_tarihi = None if geri_al else tarih
    kayit.updated_by = kullanici
    if kayit.pk is None:
        kayit.created_by = kullanici
        kayit.save()
    else:
        kayit.save(update_fields=["muhasebe_gonderim_tarihi", "updated_by", "updated_at"])
    return kayit
