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

from core.dogrulama import tc_dogrula, telefon_dogrula, telefon_kanonik
from core.metin import buyuk_harf_tr
from core.models import (
    Birim, Cari, CariKategori, FaturaTipi, HesapPlani, Kategori, Profil, Sehir,
    Ulke, YevmiyeSatir,
)
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


def _aktif_hesaplar():
    from core.services.hesap_plani import yaprak_hesaplar
    return yaprak_hesaplar()


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
# Kullanıcı yönetimi formları (Adım 2) — tarayıcı otomatik tamamlama KAPALI
# ---------------------------------------------------------------------------
_KAPALI = {"autocomplete": "off"}


class KullaniciEkleForm(forms.Form):
    tc = forms.CharField(
        label="TC Kimlik No", max_length=11, validators=[tc_dogrula],
        widget=forms.TextInput(attrs={**_KAPALI, "inputmode": "numeric"}),
    )
    isim = forms.CharField(
        label="İsim", max_length=150, widget=forms.TextInput(attrs=_KAPALI),
    )
    soyisim = forms.CharField(
        label="Soyisim", max_length=150, widget=forms.TextInput(attrs=_KAPALI),
    )
    email = forms.EmailField(
        label="E-posta", required=False, widget=forms.EmailInput(attrs=_KAPALI),
    )
    telefon = forms.CharField(
        label="Telefon", validators=[telefon_dogrula],
        widget=forms.TextInput(attrs={**_KAPALI, "inputmode": "tel"}),
    )
    yonetici = forms.BooleanField(label="Yönetici", required=False)
    sifre = forms.CharField(
        label="Şifre",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

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
            kullanici=u, telefon=telefon_kanonik(cd["telefon"]),
            yonetici=cd["yonetici"],
        )
        return u


class KullaniciDuzenleForm(forms.Form):
    isim = forms.CharField(
        label="İsim", max_length=150, widget=forms.TextInput(attrs=_KAPALI),
    )
    soyisim = forms.CharField(
        label="Soyisim", max_length=150, widget=forms.TextInput(attrs=_KAPALI),
    )
    email = forms.EmailField(
        label="E-posta", required=False, widget=forms.EmailInput(attrs=_KAPALI),
    )
    telefon = forms.CharField(
        label="Telefon", validators=[telefon_dogrula],
        widget=forms.TextInput(attrs={**_KAPALI, "inputmode": "tel"}),
    )
    yonetici = forms.BooleanField(label="Yönetici", required=False)
    aktif = forms.BooleanField(label="Aktif", required=False)
    sifre = forms.CharField(
        label="Yeni şifre (boş = değiştirme)", required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
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
        profil.telefon = telefon_kanonik(cd["telefon"])
        profil.yonetici = cd["yonetici"]
        profil.save()
        return u


class BirimForm(forms.Form):
    """Birim ekle/düzenle formu (STOKLAR). TR büyük harf + ondalık doğrulama serviste."""

    ad = forms.CharField(
        label="Ad", max_length=50,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kisa_ad = forms.CharField(
        label="Kısa Ad", max_length=10,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ondalik = forms.IntegerField(
        label="Ondalık hane (0-6)", min_value=0, max_value=6, initial=0,
        widget=forms.NumberInput(attrs={"min": 0, "max": 6, "inputmode": "numeric"}))


class KategoriForm(forms.Form):
    """Kategori ekle/düzenle (STOKLAR). Yalnız Ad + Kod; TR büyük harf/benzersizlik
    serviste. Üst kategori formda DEĞİL — ekleme giriş noktasıyla belirlenir
    (kök: "+ Yeni Üst"; alt: bir üstün "+ Alt"'ından, ``?ust=`` ile). Muhasebe hesabı
    haritası da şablonda ``hesap_<fatura_tipi_id>`` select'leriyle gelir.
    """

    ad = forms.CharField(
        label="Ad", max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kod = forms.CharField(
        label="Kod", max_length=30,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))


class FaturaTipiForm(forms.Form):
    """Fatura tipi ekle/düzenle (STOKLAR). Ad TR büyük harf + benzersizlik serviste."""

    ad = forms.CharField(
        label="Ad", max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    yon = forms.ChoiceField(label="Yön", choices=FaturaTipi.Yon.choices)
    sira = forms.IntegerField(
        label="Sıra", min_value=0, initial=0,
        widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}))


class StokForm(forms.Form):
    """Stok kartı ekle/düzenle (STOKLAR). Kod OTOMATİK (formda yok). Kategori yalnız
    eklemede (akıllı arama; düzenlemede ``duzenle=True`` ile kaldırılır — kod/kategori
    sabit). Ad TR büyük harf + doğrulamalar serviste.
    """

    ad = forms.CharField(
        label="Ad", max_length=200,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kategori = forms.ModelChoiceField(
        label="Alt Kategori", queryset=Kategori.objects.none(),
        empty_label="— alt kategori seç —")
    uretim_birimi = forms.ModelChoiceField(
        label="Üretim Birimi", queryset=Birim.objects.none(),
        empty_label="— birim seç —")
    fatura_birimi = forms.ModelChoiceField(
        label="Fatura Birimi", queryset=Birim.objects.none(),
        empty_label="— birim seç —")
    cevirici = TRDecimalField(label="Çevirici", basamak=4, initial=Decimal("1"))
    kdv_orani = TRDecimalField(label="KDV oranı (%)", basamak=2, initial=Decimal("20"))
    tevkifat_orani = TRDecimalField(
        label="Tevkifat oranı (%)", basamak=2, initial=Decimal("0"), required=False)
    kritik_stok = TRDecimalField(
        label="Kritik stok seviyesi", basamak=3, initial=Decimal("0"), required=False)
    tedarikci = forms.CharField(
        label="Tedarikçi (Cari)", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, duzenle: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.birim import aktif_birimler
        b = aktif_birimler()
        self.fields["uretim_birimi"].queryset = b
        self.fields["fatura_birimi"].queryset = b
        if duzenle:
            self.fields.pop("kategori")
        else:
            self.fields["kategori"].queryset = (
                Kategori.objects.filter(silindi=False, ust__isnull=False)
                .select_related("ust").order_by("kod"))
            self.fields["kategori"].label_from_instance = (
                lambda o: f"{o.ust.kod}-{o.kod}  {o.ust.ad} › {o.ad}")


class UlkeForm(forms.Form):
    """Ülke ekle/düzenle (CARİLER). Kod (ISO 2 harf) + ad TR büyük harf serviste."""

    kod = forms.CharField(
        label="ISO Kod (2 harf)", max_length=2,
        widget=forms.TextInput(attrs={"autocomplete": "off", "style": "text-transform:uppercase"}))
    ad = forms.CharField(
        label="Ad", max_length=80, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ad_en = forms.CharField(
        label="İngilizce Ad", max_length=80, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))


class SehirForm(forms.Form):
    """Şehir ekle/düzenle (CARİLER). Ad ülke içinde benzersiz (serviste)."""

    ulke = forms.ModelChoiceField(
        label="Ülke", queryset=Ulke.objects.none(), empty_label="— ülke seç —")
    ad = forms.CharField(
        label="Ad", max_length=80, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kod = forms.CharField(
        label="Plaka / Kod", max_length=10, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ad_en = forms.CharField(
        label="İngilizce Ad", max_length=80, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.lokasyon import aktif_ulkeler
        self.fields["ulke"].queryset = aktif_ulkeler()


class CariKategoriForm(forms.Form):
    """Cari kategori ekle/düzenle (CARİLER). Ad+Kod TR büyük harf + benzersizlik serviste.
    Üst kategori formda değil — ekleme giriş noktasıyla belirlenir (kök / +Alt)."""

    ad = forms.CharField(
        label="Ad", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kod = forms.CharField(
        label="Kod", max_length=10, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    aciklama = forms.CharField(
        label="Açıklama", required=False,
        widget=forms.Textarea(attrs={"rows": 2, "autocomplete": "off"}))


class CariForm(forms.Form):
    """Cari kartı ekle/düzenle (CARİLER). Kod OTOMATİK (formda yok). Ödeme şekli/vade YOK.
    Büyük harf/benzersizlik/sevk temizliği serviste."""

    _K = {"autocomplete": "off"}
    # Kimlik
    unvan = forms.CharField(label="Unvan / Ad Soyad", max_length=200,
                            widget=forms.TextInput(attrs=_K))
    kisa_ad = forms.CharField(label="Kısa Ad", max_length=80, required=False,
                              widget=forms.TextInput(attrs=_K))
    kategori = forms.ModelChoiceField(label="Kategori", queryset=CariKategori.objects.none(),
                                      required=False, empty_label="— kategori seç —")
    # Vergi
    vergi_dairesi = forms.CharField(label="Vergi Dairesi", max_length=100, required=False,
                                    widget=forms.TextInput(attrs=_K))
    vkn_tckn = forms.CharField(label="VKN / TCKN", max_length=15, required=False,
                               widget=forms.TextInput(attrs={**_K, "inputmode": "numeric"}))
    tax_id = forms.CharField(label="Tax ID (yurtdışı)", max_length=30, required=False,
                             widget=forms.TextInput(attrs=_K))
    # İletişim
    telefon = forms.CharField(label="Telefon", max_length=20, required=False,
                              widget=forms.TextInput(attrs={**_K, "inputmode": "tel"}))
    telefon_2 = forms.CharField(label="Telefon 2", max_length=20, required=False,
                                widget=forms.TextInput(attrs={**_K, "inputmode": "tel"}))
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs=_K))
    web = forms.URLField(label="Web", required=False, assume_scheme="https",
                         widget=forms.URLInput(attrs=_K))
    kep_adresi = forms.CharField(label="KEP", max_length=100, required=False,
                                 widget=forms.TextInput(attrs=_K))
    # Ana adres
    ulke = forms.ModelChoiceField(label="Ülke", queryset=Ulke.objects.none(),
                                  required=False, empty_label="— ülke seç —")
    sehir = forms.ModelChoiceField(label="Şehir", queryset=Sehir.objects.none(),
                                   required=False, empty_label="— şehir seç —")
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 2, **_K}))
    posta_kodu = forms.CharField(label="Posta Kodu", max_length=15, required=False,
                                 widget=forms.TextInput(attrs=_K))
    # Sevk
    sevk_farkli = forms.BooleanField(label="Sevk adresi farklı", required=False)
    sevk_ulke = forms.ModelChoiceField(label="Sevk Ülke", queryset=Ulke.objects.none(),
                                       required=False, empty_label="— ülke seç —")
    sevk_sehir = forms.ModelChoiceField(label="Sevk Şehir", queryset=Sehir.objects.none(),
                                        required=False, empty_label="— şehir seç —")
    sevk_adres = forms.CharField(label="Sevk Adres", required=False,
                                 widget=forms.Textarea(attrs={"rows": 2, **_K}))
    sevk_posta_kodu = forms.CharField(label="Sevk Posta Kodu", max_length=15, required=False,
                                      widget=forms.TextInput(attrs=_K))
    # Ticari
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    kredi_limiti = TRDecimalField(label="Kredi/Risk Limiti", basamak=2,
                                  initial=Decimal("0"), required=False)
    iskonto_yuzdesi = TRDecimalField(label="Varsayılan İskonto %", basamak=2,
                                     initial=Decimal("0"), required=False)
    notlar = forms.CharField(label="Notlar", required=False,
                             widget=forms.Textarea(attrs={"rows": 3, **_K}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari_kategori import aktif_cari_kategoriler
        from core.services.lokasyon import aktif_sehirler, aktif_ulkeler
        self.fields["kategori"].queryset = aktif_cari_kategoriler()
        self.fields["kategori"].label_from_instance = lambda o: f"{o.kod_yolu}  {o.ad}"
        ulk = aktif_ulkeler()
        seh = aktif_sehirler()
        for f in ("ulke", "sevk_ulke"):
            self.fields[f].queryset = ulk
        for f in ("sehir", "sevk_sehir"):
            self.fields[f].queryset = seh
            self.fields[f].label_from_instance = lambda o: f"{o.ad} ({o.ulke.kod})"
        for f in ("kategori", "ulke", "sehir", "sevk_ulke", "sevk_sehir"):
            self.fields[f].widget.attrs["class"] = "akilli-sec"


class CariBankaForm(forms.Form):
    """Cari banka hesabı ekle/düzenle. TR büyük harf serviste."""

    banka_adi = forms.CharField(label="Banka", max_length=100,
                                widget=forms.TextInput(attrs={"autocomplete": "off"}))
    hesap_sahibi = forms.CharField(label="Hesap Sahibi", max_length=200, required=False,
                                   widget=forms.TextInput(attrs={"autocomplete": "off"}))
    iban = forms.CharField(label="IBAN", max_length=34, required=False,
                           widget=forms.TextInput(attrs={"autocomplete": "off"}))
    swift = forms.CharField(label="SWIFT/BIC", max_length=15, required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "off"}))
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    aciklama = forms.CharField(label="Açıklama", max_length=200, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    varsayilan = forms.BooleanField(label="Varsayılan", required=False)


class CariYetkiliForm(forms.Form):
    """Cari yetkili kişi ekle/düzenle. TR büyük harf serviste."""

    ad_soyad = forms.CharField(label="Ad Soyad", max_length=120,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    unvan = forms.CharField(label="Görev / Unvan", max_length=80, required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "off"}))
    telefon = forms.CharField(label="Telefon", max_length=20, required=False,
                              widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "tel"}))
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    notlar = forms.CharField(label="Notlar", max_length=200, required=False,
                             widget=forms.TextInput(attrs={"autocomplete": "off"}))
