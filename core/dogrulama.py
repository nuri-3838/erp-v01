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
