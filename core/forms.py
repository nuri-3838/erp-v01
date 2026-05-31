"""Formlar.

Sayı alanları İSTİSNASIZ tek parser/formatter'dan geçer (core.sayi).
İsim/soyisim TR büyük harfe çevrilir; e-posta küçük; TC/telefon doğrulanır.
"""
from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.utils import timezone

from core.dogrulama import tc_dogrula, telefon_dogrula, telefon_normalize
from core.metin import buyuk_harf_tr
from core.models import HesapPlani, Profil, YevmiyeSatir
from core.sayi import SayiHatasi, format_tr, parse_tr


class TRDecimalField(forms.CharField):
    """TR biçimli ondalık alan: parse_tr ile çözer, format_tr ile gösterir."""

    def __init__(self, *args, basamak: int = 2, **kwargs):
        self.basamak = basamak
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal"}))
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value is None:
            return None
        value = value.strip()
        if value == "":
            return None
        try:
            return parse_tr(value)
        except SayiHatasi:
            raise forms.ValidationError("Geçersiz sayı biçimi (örn. 1.234,56).")

    def prepare_value(self, value):
        if isinstance(value, Decimal):
            return format_tr(value, self.basamak)
        return value


class FisForm(forms.Form):
    tarih = forms.DateField(
        label="Muhasebe tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate,
    )
    aciklama = forms.CharField(
        label="Açıklama", required=False,
        widget=forms.TextInput(attrs={"maxlength": 500}),
    )
    kur_usd = TRDecimalField(
        label="USD kuru (boşsa KUR tablosundan)", basamak=6, required=False,
    )


def _aktif_hesaplar():
    return HesapPlani.objects.filter(aktif=True, silindi=False).order_by("hesap_kodu")


class SatirForm(forms.Form):
    """Klasik yevmiye satırı: tutar BORÇ veya ALACAK sütununa yazılır."""

    hesap = forms.ModelChoiceField(
        label="Hesap", queryset=_aktif_hesaplar(), to_field_name="hesap_kodu",
        empty_label="— hesap seç —", required=False,
    )
    islem_pb = forms.ChoiceField(
        label="İşlem PB", choices=YevmiyeSatir.IslemPB.choices, initial="TRY",
        required=False,
    )
    borc = TRDecimalField(label="Borç", basamak=2, required=False)
    alacak = TRDecimalField(label="Alacak", basamak=2, required=False)
    islem_kuru = TRDecimalField(
        label="İşlem kuru", basamak=6, initial=Decimal("1"), required=False,
    )
    aciklama = forms.CharField(label="Satır açıklaması", required=False)

    def clean(self):
        cd = super().clean()
        hesap = cd.get("hesap")
        borc = cd.get("borc")
        alacak = cd.get("alacak")
        if not hesap and not borc and not alacak:
            return cd
        if borc and alacak:
            raise forms.ValidationError(
                "Bir satırda yalnızca Borç veya Alacak dolu olabilir."
            )
        if not borc and not alacak:
            raise forms.ValidationError("Borç veya Alacak tutarı girin.")
        if not hesap:
            raise forms.ValidationError("Hesap seçin.")
        if borc:
            cd["taraf"], cd["islem_tutari"] = "B", borc
        else:
            cd["taraf"], cd["islem_tutari"] = "A", alacak
        return cd

    def temiz_mi(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("taraf"))


class MizanFiltreForm(forms.Form):
    baslangic = forms.DateField(
        label="Başlangıç",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    bitis = forms.DateField(
        label="Bitiş",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def clean(self):
        cd = super().clean()
        b, s = cd.get("baslangic"), cd.get("bitis")
        if b and s and b > s:
            raise forms.ValidationError("Başlangıç, bitişten sonra olamaz.")
        return cd


# ---------------------------------------------------------------------------
# Kullanıcı yönetimi formları (Adım 2)
# ---------------------------------------------------------------------------
class KullaniciEkleForm(forms.Form):
    tc = forms.CharField(label="TC Kimlik No", max_length=11, validators=[tc_dogrula])
    isim = forms.CharField(label="İsim", max_length=150)
    soyisim = forms.CharField(label="Soyisim", max_length=150)
    email = forms.EmailField(label="E-posta", required=False)
    telefon = forms.CharField(label="Telefon", validators=[telefon_dogrula])
    yonetici = forms.BooleanField(label="Yönetici", required=False)
    sifre = forms.CharField(label="Şifre", widget=forms.PasswordInput)

    def clean_tc(self):
        tc = self.cleaned_data["tc"].strip()
        if User.objects.filter(username=tc).exists():
            raise forms.ValidationError("Bu TC ile kayıtlı kullanıcı zaten var.")
        return tc

    def clean_sifre(self):
        sifre = self.cleaned_data["sifre"]
        password_validation.validate_password(sifre)
        return sifre

    def kaydet(self) -> User:
        cd = self.cleaned_data
        u = User(
            username=cd["tc"],
            first_name=buyuk_harf_tr(cd["isim"].strip()),
            last_name=buyuk_harf_tr(cd["soyisim"].strip()),
            email=(cd.get("email") or "").strip().lower(),
            is_active=True,
        )
        u.set_password(cd["sifre"])
        u.save()
        Profil.objects.create(
            kullanici=u, telefon=telefon_normalize(cd["telefon"]),
            yonetici=cd["yonetici"],
        )
        return u


class KullaniciDuzenleForm(forms.Form):
    isim = forms.CharField(label="İsim", max_length=150)
    soyisim = forms.CharField(label="Soyisim", max_length=150)
    email = forms.EmailField(label="E-posta", required=False)
    telefon = forms.CharField(label="Telefon", validators=[telefon_dogrula])
    yonetici = forms.BooleanField(label="Yönetici", required=False)
    aktif = forms.BooleanField(label="Aktif", required=False)
    sifre = forms.CharField(
        label="Yeni şifre (boş = değiştirme)", widget=forms.PasswordInput,
        required=False,
    )

    def __init__(self, *args, kullanici=None, **kwargs):
        self.kullanici = kullanici
        if kullanici is not None and not args and "data" not in kwargs:
            try:
                profil = kullanici.profil
            except Profil.DoesNotExist:
                profil = None
            kwargs.setdefault("initial", {
                "isim": kullanici.first_name,
                "soyisim": kullanici.last_name,
                "email": kullanici.email,
                "telefon": profil.telefon if profil else "",
                "yonetici": profil.yonetici if profil else False,
                "aktif": kullanici.is_active,
            })
        super().__init__(*args, **kwargs)

    def clean_sifre(self):
        sifre = self.cleaned_data.get("sifre")
        if sifre:
            password_validation.validate_password(sifre, self.kullanici)
        return sifre

    def kaydet(self) -> User:
        cd = self.cleaned_data
        u = self.kullanici
        u.first_name = buyuk_harf_tr(cd["isim"].strip())
        u.last_name = buyuk_harf_tr(cd["soyisim"].strip())
        u.email = (cd.get("email") or "").strip().lower()
        u.is_active = cd["aktif"]
        if cd.get("sifre"):
            u.set_password(cd["sifre"])
        u.save()
        profil, _ = Profil.objects.get_or_create(kullanici=u)
        profil.telefon = telefon_normalize(cd["telefon"])
        profil.yonetici = cd["yonetici"]
        profil.save()
        return u
