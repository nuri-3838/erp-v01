"""Doğrulayıcılar — TC Kimlik No, telefon, web adresi, şifre karmaşıklığı (Türkçe mesajlı)."""
from __future__ import annotations

import re
from collections import namedtuple

import phonenumbers
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator


# --- TC Kimlik No ---------------------------------------------------------
def tc_gecerli(tc) -> bool:
    """Standart TC Kimlik No algoritması: 11 hane, 0 ile başlamaz, checksum tutar."""
    tc = str(tc)
    if len(tc) != 11 or not tc.isdigit():
        return False
    if tc[0] == "0":
        return False
    d = [int(c) for c in tc]
    tek = d[0] + d[2] + d[4] + d[6] + d[8]   # 1.,3.,5.,7.,9. haneler
    cift = d[1] + d[3] + d[5] + d[7]         # 2.,4.,6.,8. haneler
    if (tek * 7 - cift) % 10 != d[9]:
        return False
    if sum(d[:10]) % 10 != d[10]:
        return False
    return True


def tc_dogrula(deger):
    if not tc_gecerli(deger):
        raise ValidationError(
            "Geçersiz TC Kimlik No: 11 haneli, 0 ile başlamayan ve geçerli "
            "kontrol haneli bir numara olmalı."
        )


# --- VKN / TCKN (cari) -----------------------------------------------------
def vkn_gecerli(deger) -> bool:
    """Vergi Kimlik No (10 hane) kontrol hanesi algoritması (GİB)."""
    v = str(deger or "")
    if len(v) != 10 or not v.isdigit():
        return False
    toplam = 0
    for i in range(9):
        a = (int(v[i]) + (9 - i)) % 10
        p = (a * (2 ** (9 - i))) % 9
        if a != 0 and p == 0:
            p = 9
        toplam += p
    return (10 - toplam % 10) % 10 == int(v[9])


def vkn_tckn_tipi(deger) -> str:
    """'VKN' (10 hane geçerli) / 'TCKN' (11 hane geçerli) / '' (boş ya da geçersiz)."""
    v = str(deger or "")
    if vkn_gecerli(v):
        return "VKN"
    if tc_gecerli(v):
        return "TCKN"
    return ""


def vkn_tckn_gecerli(deger) -> bool:
    return bool(vkn_tckn_tipi(deger))


VKN_TCKN_HATA = ("Geçersiz VKN/TCKN: 10 haneli vergi kimlik numarası ya da 11 haneli TC kimlik numarası olmalı; "
                 "yalnız rakam ve geçerli kontrol hanesi.")


def vkn_tckn_dogrula(deger):
    if deger and not vkn_tckn_gecerli(deger):
        raise ValidationError(VKN_TCKN_HATA)


# --- IBAN (TR) -------------------------------------------------------------
# Banka kodu (IBAN 5.–9. karakter) → banka adı (öneri; elle değiştirilebilir). Liste kapsamlı değil; bilinmeyen kod → öneri yok.
TR_BANKA_KODLARI = {
    "00001": "TCMB", "00004": "İLLER BANKASI", "00010": "ZİRAAT BANKASI", "00012": "HALKBANK", "00015": "VAKIFBANK",
    "00016": "EXIMBANK", "00017": "KALKINMA VE YATIRIM BANKASI", "00029": "BİRLEŞİK FON BANKASI", "00032": "TEB",
    "00046": "AKBANK", "00059": "ŞEKERBANK", "00062": "GARANTİ BBVA", "00064": "İŞ BANKASI", "00067": "YAPI KREDİ",
    "00091": "ARAP TÜRK BANKASI", "00092": "CİTİBANK", "00096": "TURKISH BANK", "00098": "BURGAN BANK", "00099": "ING",
    "00103": "FİBABANKA", "00108": "TURKLAND BANK", "00109": "ICBC TURKEY", "00111": "QNB", "00123": "HSBC",
    "00124": "ALTERNATİF BANK", "00125": "BURGAN BANK", "00134": "DENİZBANK", "00135": "ANADOLUBANK", "00137": "RABOBANK",
    "00142": "BANK OF CHINA", "00143": "AKTİF BANK", "00146": "ODEA BANK", "00203": "ALBARAKA TÜRK", "00205": "KUVEYT TÜRK",
    "00206": "TÜRKİYE FİNANS", "00209": "ZİRAAT KATILIM", "00210": "VAKIF KATILIM", "00211": "EMLAK KATILIM",
    "00213": "HAYAT FİNANS", "00806": "ENPARA",
}


def iban_kanonik(deger) -> str:
    """Boşlukları (ve tireleri) temizler, büyük harfe çevirir (Türkçe i → I değil: IBAN yalnız ASCII)."""
    return re.sub(r"[\s\-]", "", str(deger or "")).upper()


def _iban_mod97(iban: str) -> int:
    donmus = iban[4:] + iban[:4]
    sayi = "".join(str(ord(c) - 55) if c.isalpha() else c for c in donmus)
    return int(sayi) % 97


def iban_gecerli_tr(deger) -> bool:
    """TR ile başlayan 26 karakter, 3.–26. karakterler rakam, mod-97 == 1 (ISO 7064)."""
    iban = iban_kanonik(deger)
    if len(iban) != 26 or not iban.startswith("TR") or not iban[2:].isdigit():
        return False
    return _iban_mod97(iban) == 1


IBAN_HATA = "Geçersiz IBAN: TR ile başlayan 26 karakterlik, kontrol haneleri doğru bir IBAN girin (örn. TR12 3456 …)."


def iban_dogrula(deger):
    if deger and not iban_gecerli_tr(deger):
        raise ValidationError(IBAN_HATA)


def iban_gecerli(deger) -> bool:
    """Herhangi bir ülkenin IBAN'ı (yurtdışı tedarikçi): TR ise 26 hane + TR kuralı; diğerlerinde 15-34 karakter, ülke kodu + 2 kontrol rakamı +
    alfanümerik gövde ve mod-97 == 1."""
    iban = iban_kanonik(deger)
    if iban.startswith("TR"):
        return iban_gecerli_tr(iban)
    if not (15 <= len(iban) <= 34) or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban):
        return False
    return _iban_mod97(iban) == 1


IBAN_GENEL_HATA = "Geçersiz IBAN: ülke kodu ile başlayan, kontrol haneleri doğru bir IBAN girin (TR ise 26 karakter)."


def iban_dogrula_genel(deger):
    if deger and not iban_gecerli(deger):
        raise ValidationError(IBAN_GENEL_HATA)


def iban_bicimli(deger) -> str:
    """Ekran gösterimi: 4'lü gruplar (TR12 3456 7890 …)."""
    iban = iban_kanonik(deger)
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def iban_banka_kodu(deger) -> str:
    iban = iban_kanonik(deger)
    return iban[4:9] if len(iban) >= 9 and iban.startswith("TR") else ""


def iban_banka_adi(deger) -> str:
    """IBAN'daki banka kodundan banka adı önerisi; bilinmiyorsa ''."""
    return TR_BANKA_KODLARI.get(iban_banka_kodu(deger), "")


# --- Telefon --------------------------------------------------------------
def _sadece_rakam(deger) -> str:
    """Yalnızca rakamları bırakır (boşluk/parantez/tire/+ temizlenir)."""
    return re.sub(r"\D", "", str(deger or ""))


def telefon_kanonik(deger):
    """Kabul edilen biçimleri tek kanonik biçime indirger: ``+90XXXXXXXXXX``.

    Kabul: +905327024005 / 905327024005 / 05327024005 / 5327024005
    (boşluk, parantez, tire önce temizlenir). Geçersizse None döner.

    Yalnız Personel/Kullanıcı (Profil) telefon alanları için — CRM (AdayMusteri/Cari/
    AdayYetkili/CariYetkili) telefon alanları ``telefon_normalize`` (aşağıda, uluslararası
    ``phonenumbers`` tabanlı) kullanır."""
    r = _sadece_rakam(deger)
    if len(r) == 12 and r.startswith("90"):   # +90... / 90... (uluslararası)
        r = r[2:]
    elif len(r) == 11 and r.startswith("0"):  # 0...
        r = r[1:]
    if len(r) == 10:                          # çekirdek 10 hane
        return "+90" + r
    return None


TelefonSonuc = namedtuple("TelefonSonuc", "deger gecerli")

_TELEFON_AYIKLA = re.compile(r"[\s.\-()]")


def telefon_normalize(numara, ulke_iso2=None) -> TelefonSonuc:
    """Uluslararası telefon normalizasyonu (``phonenumbers``) — CRM (AdayMusteri/Cari/
    AdayYetkili/CariYetkili) telefon alanları için TEK yardımcı.

    Boşluk/nokta/tire/parantez temizlenir; baştaki ``00`` -> ``+``. ``+`` ile başlıyorsa
    uluslararası olarak, değilse ``ulke_iso2`` (boşsa "TR") ile parse edilir. Geçerliyse
    ``INTERNATIONAL`` biçiminde (ör. "+90 532 207 07 09"); 20 karakteri aşarsa boşluksuz
    E.164 biçiminde döner. Parse edilemiyor/geçersizse değer AYNEN (yalnız baştaki/sondaki
    boşluk kırpılmış) döner — veri kaybı yok, çağıran taraf ``gecerli=False`` iken kullanıcıya
    uyarı gösterir ama kaydı ENGELLEMEZ (spec kararı)."""
    ham = (numara or "").strip()
    if not ham:
        return TelefonSonuc("", True)
    temiz = _TELEFON_AYIKLA.sub("", ham)
    if temiz.startswith("00"):
        temiz = "+" + temiz[2:]
    bolge = None if temiz.startswith("+") else (ulke_iso2 or "TR")
    try:
        ayristirilmis = phonenumbers.parse(temiz, bolge)
    except phonenumbers.NumberParseException:
        return TelefonSonuc(ham, False)
    if not phonenumbers.is_valid_number(ayristirilmis):
        return TelefonSonuc(ham, False)
    sonuc = phonenumbers.format_number(ayristirilmis, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    if len(sonuc) > 20:
        sonuc = phonenumbers.format_number(ayristirilmis, phonenumbers.PhoneNumberFormat.E164)
    return TelefonSonuc(sonuc, True)


def telefon_dogrula(deger):
    if telefon_kanonik(deger) is None:
        raise ValidationError(
            "Geçersiz telefon. Örnek geçerli biçimler: +905327024005, "
            "05327024005 veya 5327024005 (boşluk/parantez/tire serbest)."
        )


# --- Web adresi -------------------------------------------------------------
_SEMALI = re.compile(r"^https?://", re.IGNORECASE)


def web_normalize(deger):
    """Boşsa "" döner. Şemasız adrese (``www.akc.ae``, ``akc.ae``) ``https://`` eklenir;
    baştaki/sondaki boşluk kırpılır. Geçersiz URL'de None döner (çağıran taraf Türkçe
    hata üretir) — Cari ve Aday servisleri ile formları TEK bu fonksiyonu kullanır."""
    deger = (deger or "").strip()
    if not deger:
        return ""
    if not _SEMALI.match(deger):
        deger = "https://" + deger
    try:
        URLValidator()(deger)
    except ValidationError:
        return None
    return deger


# --- Şifre karmaşıklığı (Django password validator) -----------------------
class KarmaSifreDogrulayici:
    """Şifrede en az 1 küçük harf, 1 büyük harf, 1 rakam ve 1 sembol arar.

    (Minimum uzunluk Django MinimumLengthValidator ile ayrı zorlanır.)
    """

    def validate(self, password, user=None):
        eksik = []
        if not re.search(r"[a-zçğıöşü]", password):
            eksik.append("küçük harf")
        if not re.search(r"[A-ZÇĞİÖŞÜ]", password):
            eksik.append("büyük harf")
        if not re.search(r"\d", password):
            eksik.append("rakam")
        if not re.search(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü\s]", password):
            eksik.append("sembol")
        if eksik:
            raise ValidationError(
                "Şifre şunları içermeli: " + ", ".join(eksik) + ".",
                code="sifre_karmasik",
            )

    def get_help_text(self):
        return ("Şifre en az bir küçük harf, bir büyük harf, bir rakam ve "
                "bir sembol içermeli.")
