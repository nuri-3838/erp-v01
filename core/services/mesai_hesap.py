"""İNSAN KAYNAKLARI > Mesai Hesabı — personelin kendi mesai (giriş/çıkış) hesabının yaşam
döngüsü. Bu hesaplara HİÇBİR EkranYetki verilmez; yalnız core.yetki.mesai_kullanicisi_mi
ile tanınır ve menüde tek "Mesaim" bağlantısı görünür. Şifre kuralı Kullanıcılar ekranıyla
aynıdır (Django standart password_validation). Personel işten çıkınca (isten_cikis_tarihi
kaydedilince) bağlı hesap OTOMATİK is_active=False olur — bkz. core.services.personel.
"""
from __future__ import annotations

from django.contrib.auth import password_validation
from django.contrib.auth.models import User

from core.models import Personel
from core.services.personel import aktif_mi


class MesaiHesapHatasi(ValueError):
    """Mesai hesabı kural ihlali (Türkçe mesaj)."""


def _kullanici_adi_dogrula(kullanici_adi: str) -> str:
    kullanici_adi = (kullanici_adi or "").strip()
    if not kullanici_adi:
        raise MesaiHesapHatasi("Kullanıcı adı zorunlu.")
    if User.objects.filter(username=kullanici_adi).exists():
        raise MesaiHesapHatasi(f"Bu kullanıcı adı zaten kullanılıyor: {kullanici_adi}")
    return kullanici_adi


def hesap_olustur(personel: Personel, *, kullanici_adi, sifre, yapan=None) -> User:
    if personel.silindi:
        raise MesaiHesapHatasi("Silinmiş personele mesai hesabı açılamaz.")
    if personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin zaten bir mesai hesabı var.")
    kullanici_adi = _kullanici_adi_dogrula(kullanici_adi)
    password_validation.validate_password(sifre)
    u = User(username=kullanici_adi, first_name=personel.ad, last_name=personel.soyad,
            is_active=True)
    u.set_password(sifre)
    u.save()
    personel.kullanici = u
    personel.updated_by = yapan
    personel.save(update_fields=["kullanici", "updated_by", "updated_at"])
    return u


def sifre_sifirla(personel: Personel, *, sifre, yapan=None) -> User:
    if not personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin mesai hesabı yok.")
    password_validation.validate_password(sifre, personel.kullanici)
    personel.kullanici.set_password(sifre)
    personel.kullanici.save(update_fields=["password"])
    return personel.kullanici


def hesap_kapat(personel: Personel, *, yapan=None) -> User:
    if not personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin mesai hesabı yok.")
    u = personel.kullanici
    u.is_active = False
    u.save(update_fields=["is_active"])
    return u


def hesap_ac(personel: Personel, *, yapan=None) -> User:
    """Kapatılmış hesabı yeniden açar — personel hâlâ ÇALIŞMIYORSA reddedilir (işten
    ayrılmış birinin hesabı sessizce yeniden açılmasın)."""
    if not personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin mesai hesabı yok.")
    if not aktif_mi(personel):
        raise MesaiHesapHatasi(
            "Bu personel artık çalışmıyor; mesai hesabı yeniden açılamaz.")
    u = personel.kullanici
    u.is_active = True
    u.save(update_fields=["is_active"])
    return u
