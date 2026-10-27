"""İNSAN KAYNAKLARI > Mesai Hesabı — personelin kendi mesai (giriş/çıkış) hesabının yaşam
döngüsü. Bu hesaplara HİÇBİR EkranYetki verilmez; yalnız core.yetki.mesai_kullanicisi_mi
ile tanınır ve menüde tek "Mesaim" bağlantısı görünür. Şifre kuralı Kullanıcılar ekranıyla
aynıdır (Django standart password_validation). Personel işten çıkınca (isten_cikis_tarihi
kaydedilince) bağlı hesap OTOMATİK is_active=False olur — bkz. core.services.personel.

Şifre disiplini (2026-10-12): hesap oluşturma ve yönetici şifre sıfırlama hesabı "ilk girişte şifre değiştirilmeli" işaretler
(``Personel.sifre_degistirilmeli``); ``core.middleware.SifreDegistirmeMiddleware`` o kullanıcıyı 🔑 Şifre ekranına yönlendirir, personel
``sifre_degistir`` ile kendi şifresini değiştirince işaret kalkar. Her şifre yazımı ``sifre_degisiklik_zamani``nı günceller.
"""
from __future__ import annotations

from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone

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


def _sifre_dogrula(sifre, user=None) -> None:
    """Django parola doğrulayıcıları (settings.AUTH_PASSWORD_VALIDATORS); kural ihlali ``MesaiHesapHatasi`` (Türkçe, tek metin) olur — ekran 500 yerine formda gösterir."""
    try:
        password_validation.validate_password(sifre, user)
    except ValidationError as e:
        raise MesaiHesapHatasi(" ".join(e.messages))


def hesap_olustur(personel: Personel, *, kullanici_adi, sifre, yapan=None, ilk_giris_sifre=True) -> User:
    """``ilk_giris_sifre``: personel ilk girişte şifresini değiştirmek zorunda (varsayılan; testlerde kapatılabilir)."""
    if personel.silindi:
        raise MesaiHesapHatasi("Silinmiş personele mesai hesabı açılamaz.")
    if personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin zaten bir mesai hesabı var.")
    kullanici_adi = _kullanici_adi_dogrula(kullanici_adi)
    _sifre_dogrula(sifre)
    u = User(username=kullanici_adi, first_name=personel.ad, last_name=personel.soyad,
            is_active=True)
    u.set_password(sifre)
    u.save()
    personel.kullanici = u
    personel.sifre_degistirilmeli = bool(ilk_giris_sifre)
    personel.sifre_degisiklik_zamani = timezone.now()
    personel.updated_by = yapan
    personel.save(update_fields=["kullanici", "sifre_degistirilmeli", "sifre_degisiklik_zamani", "updated_by", "updated_at"])
    return u


def sifre_sifirla(personel: Personel, *, sifre, yapan=None, ilk_giris_sifre=True) -> User:
    """Yönetici şifre sıfırlar → hesap "ilk girişte şifre değiştirilmeli" olur."""
    if not personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin mesai hesabı yok.")
    _sifre_dogrula(sifre, personel.kullanici)
    personel.kullanici.set_password(sifre)
    personel.kullanici.save(update_fields=["password"])
    personel.sifre_degistirilmeli = bool(ilk_giris_sifre)
    personel.sifre_degisiklik_zamani = timezone.now()
    personel.updated_by = yapan
    personel.save(update_fields=["sifre_degistirilmeli", "sifre_degisiklik_zamani", "updated_by", "updated_at"])
    return personel.kullanici


def sifre_degistir(personel: Personel, *, mevcut, yeni, yeni_tekrar, yapan=None) -> User:
    """Personelin KENDİ şifresini değiştirmesi (Atölye/Mesaim › 🔑 Şifre): mevcut şifre doğru, yeni şifre iki kez aynı ve mevcuttan farklı,
    Django parola doğrulayıcıları (mesajlar Türkçe, tek metin). İlk giriş zorunluluğu kalkar, zaman yazılır. Oturumun açık kalması çağıranın
    işi (``update_session_auth_hash``)."""
    if not personel.kullanici_id:
        raise MesaiHesapHatasi("Bu personelin mesai hesabı yok.")
    u = personel.kullanici
    if not (mevcut or "") or not u.check_password(mevcut):
        raise MesaiHesapHatasi("Mevcut şifre yanlış.")
    if not (yeni or ""):
        raise MesaiHesapHatasi("Yeni şifre boş olamaz.")
    if yeni != yeni_tekrar:
        raise MesaiHesapHatasi("Yeni şifreler birbiriyle aynı değil.")
    if yeni == mevcut:
        raise MesaiHesapHatasi("Yeni şifre mevcut şifreyle aynı olamaz.")
    _sifre_dogrula(yeni, u)
    u.set_password(yeni)
    u.save(update_fields=["password"])
    personel.sifre_degistirilmeli = False
    personel.sifre_degisiklik_zamani = timezone.now()
    personel.updated_by = yapan
    personel.save(update_fields=["sifre_degistirilmeli", "sifre_degisiklik_zamani", "updated_by", "updated_at"])
    return u


def sifre_degistirilmeli_mi(user) -> bool:
    """Giriş anında (core.middleware ``user_logged_in`` alıcısı): kullanıcıya bağlı personel kartı "ilk girişte şifre değiştirilmeli" mi?"""
    if not getattr(user, "is_authenticated", False):
        return False
    return Personel.objects.filter(kullanici_id=user.pk, silindi=False, sifre_degistirilmeli=True).exists()


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
