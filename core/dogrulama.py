"""Doğrulayıcılar — TC Kimlik No, telefon, şifre karmaşıklığı (Türkçe mesajlı)."""
from __future__ import annotations

import re

from django.core.exceptions import ValidationError


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
def telefon_normalize(deger) -> str:
    return re.sub(r"\D", "", str(deger or ""))


def telefon_dogrula(deger):
    rakam = telefon_normalize(deger)
    if len(rakam) not in (10, 11):
        raise ValidationError(
            "Geçersiz telefon: 10 veya 11 haneli olmalı (örn. 0532 123 45 67)."
        )
    if len(rakam) == 11 and rakam[0] != "0":
        raise ValidationError("11 haneli telefon 0 ile başlamalı.")


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
