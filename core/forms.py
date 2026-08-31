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
    Banka, BankaHesap, Birim, Cari, CariKategori, CekSenet, Depo, FaturaTipi, HesapPlani, Kasa,
    Kategori, KdvOrani,
    Profil, Sehir, Stok, StokHareket, TevkifatOrani, Ulke, YevmiyeSatir,
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


class BilancoTarihForm(forms.Form):
    """Bilanço TEK tarihtir (o tarihteki anlık durum), aralık değil."""
    tarih = forms.DateField(
        label="Bilanço Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))


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
    kdv = forms.ModelChoiceField(
        label="KDV Oranı", queryset=KdvOrani.objects.none(),
        empty_label="— KDV oranı seç —")
    tevkifat = forms.ModelChoiceField(
        label="Tevkifat Oranı", queryset=TevkifatOrani.objects.none(), required=False,
        empty_label="— yok —")
    kritik_stok = TRDecimalField(
        label="Kritik stok seviyesi", basamak=3, initial=Decimal("0"), required=False)
    tedarikci = forms.ModelChoiceField(
        label="Tedarikçi (Cari)", queryset=Cari.objects.none(), required=False,
        empty_label="— tedarikçi seç —")
    alis_fiyati_pb = forms.ChoiceField(
        label="Alış Fiyatı Para Birimi", choices=Cari.PARA_CHOICES, required=False,
        initial="TRY")
    alis_fiyati = TRDecimalField(
        label="Alış Fiyatı", basamak=6, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "— girilmedi —"}))

    def __init__(self, *args, duzenle: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.birim import aktif_birimler
        b = aktif_birimler()
        self.fields["uretim_birimi"].queryset = b
        self.fields["fatura_birimi"].queryset = b
        self.fields["kdv"].queryset = KdvOrani.objects.filter(silindi=False).order_by("oran")
        self.fields["kdv"].label_from_instance = lambda o: f"%{o.oran:g} {o.aciklama}"
        self.fields["tevkifat"].queryset = (
            TevkifatOrani.objects.filter(silindi=False).order_by("kod"))
        self.fields["tevkifat"].label_from_instance = (
            lambda o: f"{o.pay}/{o.payda} {o.aciklama}".strip())
        self.fields["tedarikci"].queryset = (
            Cari.objects.filter(silindi=False).order_by("unvan"))
        self.fields["tedarikci"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["tedarikci"].widget.attrs["class"] = "akilli-sec"
        if duzenle:
            self.fields.pop("kategori")
        else:
            self.fields["kategori"].queryset = (
                Kategori.objects.filter(silindi=False, ust__isnull=False)
                .select_related("ust").order_by("ust__kod", "kod"))
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
                            widget=forms.Textarea(attrs={"rows": 4, **_K}))
    posta_kodu = forms.CharField(label="Posta Kodu", max_length=15, required=False,
                                 widget=forms.TextInput(attrs=_K))
    # Sevk
    sevk_farkli = forms.BooleanField(label="Sevk adresi farklı", required=False)
    sevk_ulke = forms.ModelChoiceField(label="Sevk Ülke", queryset=Ulke.objects.none(),
                                       required=False, empty_label="— ülke seç —")
    sevk_sehir = forms.ModelChoiceField(label="Sevk Şehir", queryset=Sehir.objects.none(),
                                        required=False, empty_label="— şehir seç —")
    sevk_adres = forms.CharField(label="Sevk Adres", required=False,
                                 widget=forms.Textarea(attrs={"rows": 4, **_K}))
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
        # Cari yalnız ALT kategoriye bağlanır; üst (ana) kategoriler seçilemez.
        self.fields["kategori"].queryset = aktif_cari_kategoriler().filter(ust__isnull=False)
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


def _yaprak_hesap_alani(label):
    """Tanım listeleri için ortak: yaprak hesap akıllı-arama seçimi (opsiyonel)."""
    return forms.ModelChoiceField(
        label=label, queryset=HesapPlani.objects.none(), required=False,
        to_field_name="hesap_kodu", empty_label="— hesap seç (opsiyonel) —")


class KdvOraniForm(forms.Form):
    """KDV oranı ekle/düzenle (AYARLAR > Tanım Listeleri)."""

    sira = forms.IntegerField(label="Sıra", min_value=0, initial=0,
                              widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}))
    aciklama = forms.CharField(label="Açıklama", max_length=100,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    oran = TRDecimalField(label="KDV Oranı (%)", basamak=2)
    hesap_borc = _yaprak_hesap_alani("Borç Hesabı (İndirilecek KDV)")
    hesap_alacak = _yaprak_hesap_alani("Alacak Hesabı (Hesaplanan KDV)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        yp = yaprak_hesaplar()
        for f in ("hesap_borc", "hesap_alacak"):
            self.fields[f].queryset = yp
            self.fields[f].widget.attrs["class"] = "akilli-sec"


class TevkifatOraniForm(forms.Form):
    """Tevkifat oranı ekle/düzenle (AYARLAR > Tanım Listeleri)."""

    kod = forms.CharField(label="Kod", max_length=20,
                          widget=forms.TextInput(attrs={"autocomplete": "off"}))
    pay = forms.IntegerField(label="Pay", min_value=0,
                             widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}))
    payda = forms.IntegerField(label="Payda", min_value=1,
                               widget=forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}))
    aciklama = forms.CharField(label="Açıklama", max_length=200, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    hesap = _yaprak_hesap_alani("Muhasebe Hesap Kodu")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        self.fields["hesap"].queryset = yaprak_hesaplar()
        self.fields["hesap"].widget.attrs["class"] = "akilli-sec"


class KasaForm(forms.Form):
    ad = forms.CharField(label="Kasa Adı", max_length=100)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    muhasebe = forms.ModelChoiceField(
        label="Muhasebe Hesabı", queryset=HesapPlani.objects.none(),
        to_field_name="hesap_kodu", empty_label="— hesap seç —")

    def __init__(self, *args, mevcut_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        _muhasebe_kur(self, mevcut_hesap)


def _muhasebe_alani():
    return forms.ModelChoiceField(
        label="Muhasebe Hesabı", queryset=HesapPlani.objects.none(),
        to_field_name="hesap_kodu", empty_label="— hesap seç —")


def _muhasebe_kur(form, mevcut_kod=None):
    """Yaprak hesap dropdown'u. Düzenlemede mevcut bağlı hesap yaprak olmaktan
    çıkmışsa (sonradan alt hesap eklenmiş) ya da soft-delete edilmişse bile queryset'e
    dahil et — yoksa form boş render olur ve kayıt düzenlenemez hâle gelir. Yaprak
    olmayan hesap seçili bırakılırsa servis katmanı (_yaprak_hesap_coz) net hatayla reddeder."""
    from django.db.models import Q

    from core.services.hesap_plani import yaprak_hesaplar
    qs = yaprak_hesaplar()
    if mevcut_kod and not qs.filter(hesap_kodu=mevcut_kod).exists():
        leaf_pks = list(qs.values_list("pk", flat=True))
        qs = (HesapPlani.objects.filter(silindi=False)
              .filter(Q(pk__in=leaf_pks) | Q(hesap_kodu=mevcut_kod))
              .order_by("hesap_kodu"))
    form.fields["muhasebe"].queryset = qs
    form.fields["muhasebe"].widget.attrs["class"] = "akilli-sec"


class BankaForm(forms.Form):
    ad = forms.CharField(label="Banka Adı", max_length=150)
    kisa_ad = forms.CharField(label="Kısa Ad", max_length=50, required=False)
    sube = forms.CharField(label="Şube", max_length=100, required=False)
    swift_kod = forms.CharField(label="SWIFT/BIC", max_length=11, required=False)
    musteri_no = forms.CharField(label="Müşteri No", max_length=50, required=False)
    adres = forms.CharField(label="Adres", max_length=255, required=False)
    logo = forms.ImageField(label="Logo", required=False)


class BankaHesapForm(forms.Form):
    ad = forms.CharField(label="Hesap Adı", max_length=100)
    hesap_no = forms.CharField(label="Hesap No", max_length=40, required=False)
    iban = forms.CharField(label="IBAN", max_length=34, required=False)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    muhasebe = _muhasebe_alani()

    def __init__(self, *args, mevcut_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        _muhasebe_kur(self, mevcut_hesap)


class BankaKisaChoiceField(forms.ModelChoiceField):
    """Banka açılır listesi etiketi = kısa ad (yoksa tam ad)."""

    def label_from_instance(self, obj):
        return obj.kisa_ad or obj.ad


class KrediKartiForm(forms.Form):
    ad = forms.CharField(label="Kart Adı", max_length=100)
    banka = BankaKisaChoiceField(
        label="Banka", required=False, empty_label="— seçiniz —",
        queryset=Banka.objects.filter(silindi=False).order_by("ad"))
    kart_son4 = forms.CharField(label="Kart No (Son 4)", max_length=4, required=False)
    limit = TRDecimalField(label="Kart Limiti", basamak=2, required=False)
    kesim_gunu = forms.IntegerField(label="Hesap Kesim Günü", min_value=1, max_value=31, required=False)
    son_odeme_gunu = forms.IntegerField(label="Son Ödeme Günü", min_value=1, max_value=31, required=False)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    muhasebe = _muhasebe_alani()

    def __init__(self, *args, mevcut_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        _muhasebe_kur(self, mevcut_hesap)


class KrediForm(forms.Form):
    ad = forms.CharField(label="Kredi Adı", max_length=100)
    banka = BankaKisaChoiceField(
        label="Banka", required=False, empty_label="— seçiniz —",
        queryset=Banka.objects.filter(silindi=False).order_by("ad"))
    anapara = TRDecimalField(label="Anapara", basamak=2, required=False)
    faiz_orani = TRDecimalField(label="Aylık Faiz Oranı (%)", basamak=4, required=False)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    muhasebe = _muhasebe_alani()

    def __init__(self, *args, mevcut_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        _muhasebe_kur(self, mevcut_hesap)


class CekHesapAyariForm(forms.Form):
    """Çek/Senet muhasebe hesap eşlemesi: her durum için çek + senet hesabı (opsiyonel)."""
    portfoy_cek = _muhasebe_alani()
    portfoy_senet = _muhasebe_alani()
    tahsilde_cek = _muhasebe_alani()
    tahsilde_senet = _muhasebe_alani()
    teminatta_cek = _muhasebe_alani()
    teminatta_senet = _muhasebe_alani()
    verilen_cek = _muhasebe_alani()
    verilen_senet = _muhasebe_alani()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cek import AYAR_ALANLARI
        from core.services.hesap_plani import yaprak_hesaplar
        from django.db.models import Q
        qs = yaprak_hesaplar()
        leaf_pks = None
        for ad in AYAR_ALANLARI:
            f = self.fields[ad]
            f.required = False
            # Düzenlemede mevcut hesap yaprak olmaktan çıkmış/silinmişse bile koru.
            mevcut = (self.initial.get(ad) or self.data.get(ad) or "")
            if mevcut and not qs.filter(hesap_kodu=mevcut).exists():
                if leaf_pks is None:
                    leaf_pks = list(yaprak_hesaplar().values_list("pk", flat=True))
                f.queryset = (HesapPlani.objects.filter(silindi=False)
                              .filter(Q(pk__in=leaf_pks) | Q(hesap_kodu=mevcut))
                              .order_by("hesap_kodu"))
            else:
                f.queryset = qs
            f.widget.attrs["class"] = "akilli-sec"


class BordroBaslikForm(forms.Form):
    """Çek/senet bordrosu başlığı: cari + işlem tarihi + para birimi (giriş ve çıkış ortak)."""
    cari = forms.ModelChoiceField(label="Cari", queryset=Cari.objects.none(),
                                  empty_label="— cari seç —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")

    def __init__(self, *args, cari_label="Cari", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].label = cari_label
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"


class CariCiroForm(forms.Form):
    """Cari Ciro başlığı: ciro edilen cari (yeni hamil) + işlem tarihi. (Evrak seçimi
    şablonda checkbox listesiyle; PB seçilen evraktan türer.)"""
    cari = forms.ModelChoiceField(label="Ciro Edilen Cari (yeni hamil)",
                                  queryset=Cari.objects.none(), empty_label="— cari seç —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"


class BankaIslemForm(forms.Form):
    """Banka Tahsil/Teminat başlığı: banka hesabı + işlem tarihi. (Evrak seçimi şablonda
    checkbox listesiyle; PB seçilen evraktan türer.)"""
    banka_hesap = forms.ModelChoiceField(label="Banka Hesabı", queryset=BankaHesap.objects.none(),
                                         empty_label="— banka hesabı seç —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banka_hesap"].queryset = (BankaHesap.objects.filter(silindi=False)
                                               .select_related("banka").order_by("banka__ad", "ad"))
        self.fields["banka_hesap"].label_from_instance = (
            lambda o: f"{o.banka.ad} - {o.ad} ({o.para_birimi})")
        self.fields["banka_hesap"].widget.attrs["class"] = "akilli-sec"


class IslemTarihForm(forms.Form):
    """Yalnız işlem tarihi — hedef zaten evraktan/duruma bellidir (Banka Tahsil/Teminat İade,
    Cari İade). Portföy-seçim ekranında hedef seçici gerektirmeyen işlemler için."""
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)


class CekNakitForm(forms.Form):
    """Nakit gerçekleşme başlığı (Tahsil / Firma Çek Ödeme): nakit hesabı Banka hesabı VEYA
    Kasa (yalnız biri) + tarih. (Evrak seçimi şablonda checkbox listesiyle; PB evraktan türer.)"""
    banka_hesap = forms.ModelChoiceField(
        label="Banka Hesabı", required=False, queryset=BankaHesap.objects.none(),
        empty_label="— banka hesabı —")
    kasa = forms.ModelChoiceField(
        label="Kasa", required=False, queryset=Kasa.objects.none(), empty_label="— kasa —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        bh = self.fields["banka_hesap"]
        bh.queryset = (BankaHesap.objects.filter(silindi=False)
                       .select_related("banka").order_by("banka__ad", "ad"))
        bh.label_from_instance = lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})"
        bh.widget.attrs["class"] = "akilli-sec"
        ks = self.fields["kasa"]
        ks.queryset = Kasa.objects.filter(silindi=False).order_by("ad")
        ks.label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"
        ks.widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        if bool(cd.get("banka_hesap")) == bool(cd.get("kasa")):
            raise forms.ValidationError(
                "Nakit hesabı olarak Banka hesabı VEYA Kasa (yalnız biri) seçin.")
        return cd


class CekKalemForm(forms.Form):
    """Bordro satırı: bir çek/senet (tip + tutar + vade + belge no + keşideci + ön/arka görsel)."""
    tip = forms.ChoiceField(label="Tip", choices=CekSenet.Tip.choices)
    tutar = TRDecimalField(label="Tutar", basamak=2, required=False)
    vade = forms.DateField(
        label="Vade", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    belge_no = forms.CharField(label="Belge No", max_length=50, required=False)
    kesideci = forms.CharField(label="Keşideci", max_length=200, required=False)
    on_yuz = forms.ImageField(
        label="Ön Yüz", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))
    arka_yuz = forms.ImageField(
        label="Arka Yüz", required=False,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))

    def dolu_mu(self):
        cd = getattr(self, "cleaned_data", {})
        return bool(cd.get("tutar"))

    def clean(self):
        cd = super().clean()
        if cd.get("tutar") and not cd.get("vade"):
            self.add_error("vade", "Tutar girilen satırda vade zorunludur.")
        return cd


# ---------------------------------------------------------------------------
# FATURALAR — Alış/Satış faturası giriş (otomatik yevmiye motoru besler)
# ---------------------------------------------------------------------------
class FaturaForm(forms.Form):
    tip = forms.ModelChoiceField(
        label="Fatura Tipi", queryset=FaturaTipi.objects.none(), empty_label="— tip seç —")
    cari = forms.ModelChoiceField(
        label="Cari", queryset=Cari.objects.none(), empty_label="— cari seç —")
    tarih = forms.DateField(
        label="Fatura tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    fatura_no = forms.CharField(
        label="Fatura No", max_length=50, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    para_birimi = forms.ChoiceField(
        label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), required=False,
        empty_label="— depo seç —")

    def __init__(self, *args, yon=None, **kwargs):
        super().__init__(*args, **kwargs)
        tipler = FaturaTipi.objects.filter(silindi=False)
        if yon:
            tipler = tipler.filter(yon=yon)
        self.fields["tip"].queryset = tipler.order_by("sira", "ad")
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        depolar = Depo.objects.filter(silindi=False).order_by("kod")
        self.fields["depo"].queryset = depolar
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        # Yeni faturada ANA DEPO ön-seçili; düzenlemede faturanın deposu (initial) korunur.
        if not self.is_bound and "depo" not in self.initial:
            vd = depolar.filter(ad="ANA DEPO").first() or depolar.first()
            if vd:
                self.fields["depo"].initial = vd.pk


class FaturaSatirForm(forms.Form):
    """Fatura kalemi — Teklif/Sipariş kalem formuyla aynı şekil, aynı sebeple birim fiyat
    4 ondalık basamak (bkz. TeklifSiparisKalemForm): fatura kalemleri artık Satınalma
    zincirinde bir Sipariş/İrsaliye'den 4 basamaklı fiyatla devralınabiliyor — form yalnız
    2 basamak destekleseydi (eski hâli), TASLAK faturayı Düzenle'den tip atayıp kaydederken
    (fatura_onayla'dan önce zorunlu adım) fiyat sessizce 2 basamağa yuvarlanıp kalıcı veri
    kaybına yol açardı (`prepare_value` initial'ı basamak sayısına göre biçimlendirir)."""
    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.none(), required=False, empty_label="— stok seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stok"].queryset = (
            Stok.objects.filter(silindi=False).select_related("kategori", "kdv").order_by("kod"))
        self.fields["stok"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["stok"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        stok = cd.get("stok")
        miktar = cd.get("miktar")
        fiyat = cd.get("birim_fiyat")
        if not stok and miktar is None and fiyat is None:
            return cd                              # boş satır — atlanır
        if not stok:
            raise forms.ValidationError("Stok seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        if fiyat is None or fiyat < 0:
            raise forms.ValidationError("Birim fiyat girin.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class KasaHareketForm(forms.Form):
    """Kasa hareketi: karşı taraf (tipe göre Cari / BankaHesap / hedef Kasa) +
    tutar + tarih + açıklama. Kasa ve tip URL'den gelir; fiş otomatik üretilir."""
    karsi = forms.ModelChoiceField(
        label="Karşı taraf", queryset=Cari.objects.none(), empty_label="— seç —")
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, kasa=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.kasa_hareket import HAREKET
        tur = HAREKET.get(tip, {}).get("karsi", "cari")
        f = self.fields["karsi"]
        if tur == "banka":
            f.queryset = (BankaHesap.objects.filter(silindi=False)
                          .select_related("banka").order_by("banka__ad", "ad"))
            f.label_from_instance = lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})"
            f.label, f.empty_label = "Banka Hesabı", "— banka hesabı seç —"
        elif tur == "kasa":
            qs = Kasa.objects.filter(silindi=False)
            if kasa is not None:
                qs = qs.exclude(pk=kasa.pk)
            f.queryset = qs.order_by("ad")
            f.label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"
            f.label, f.empty_label = "Hedef Kasa", "— hedef kasa seç —"
        else:
            f.queryset = Cari.objects.filter(silindi=False).order_by("unvan")
            f.label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
            f.label, f.empty_label = "Cari (karşı taraf)", "— cari seç —"
        f.widget.attrs["class"] = "akilli-sec"


class BankaHareketForm(forms.Form):
    """Banka hesabı hareketi: karşı taraf (tipe göre Cari / hedef BankaHesap / Kasa)
    + tutar + tarih + açıklama. Banka hesabı ve tip URL'den; fiş otomatik üretilir."""
    karsi = forms.ModelChoiceField(
        label="Karşı taraf", queryset=Cari.objects.none(), empty_label="— seç —")
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, banka_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.banka_hareket import HAREKET
        tur = HAREKET.get(tip, {}).get("karsi", "cari")
        f = self.fields["karsi"]
        if tur == "banka":
            qs = BankaHesap.objects.filter(silindi=False).select_related("banka")
            if banka_hesap is not None:
                qs = qs.exclude(pk=banka_hesap.pk)
            f.queryset = qs.order_by("banka__ad", "ad")
            f.label_from_instance = lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})"
            f.label, f.empty_label = "Hedef Banka Hesabı", "— hedef hesap seç —"
        elif tur == "kasa":
            f.queryset = Kasa.objects.filter(silindi=False).order_by("ad")
            f.label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"
            f.label, f.empty_label = "Kasa", "— kasa seç —"
        else:
            f.queryset = Cari.objects.filter(silindi=False).order_by("unvan")
            f.label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
            f.label, f.empty_label = "Cari (karşı taraf)", "— cari seç —"
        f.widget.attrs["class"] = "akilli-sec"


class KrediKartiHareketForm(forms.Form):
    """Kredi kartı hareketi: karşı taraf (Harcama/İade → Cari VEYA Gider; Ödeme → Banka VEYA
    Kasa) + tutar + tarih + açıklama. Karşı alanlar tipe göre __init__'te eklenir; tam olarak
    biri seçilmeli (clean). Kart ve tip URL'den; fiş otomatik üretilir."""
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, kart=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        from core.services.kredi_karti_hareket import HAREKET
        turler = HAREKET.get(tip, {}).get("karsi", ())
        if "cari" in turler:
            self.fields["cari"] = forms.ModelChoiceField(
                label="Cari", required=False, empty_label="— cari seç —",
                queryset=Cari.objects.filter(silindi=False).order_by("unvan"),
                widget=forms.Select(attrs={"class": "akilli-sec"}))
            self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
            self.fields["gider"] = forms.ModelChoiceField(
                label="Gider Hesabı", required=False, empty_label="— gider hesabı seç —",
                queryset=yaprak_hesaplar(),
                widget=forms.Select(attrs={"class": "akilli-sec"}))
            self.fields["gider"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        if "banka" in turler:
            self.fields["banka_hesap"] = forms.ModelChoiceField(
                label="Banka Hesabı", required=False, empty_label="— banka hesabı seç —",
                queryset=(BankaHesap.objects.filter(silindi=False)
                          .select_related("banka").order_by("banka__ad", "ad")),
                widget=forms.Select(attrs={"class": "akilli-sec"}))
            self.fields["banka_hesap"].label_from_instance = (
                lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})")
            self.fields["kasa"] = forms.ModelChoiceField(
                label="Kasa", required=False, empty_label="— kasa seç —",
                queryset=Kasa.objects.filter(silindi=False).order_by("ad"),
                widget=forms.Select(attrs={"class": "akilli-sec"}))
            self.fields["kasa"].label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"
        if tip == "harcama":
            self.fields["taksit_adedi"] = forms.IntegerField(
                label="Taksit Sayısı", min_value=1, max_value=60, initial=1, required=False)
            self.fields["ilk_vade"] = forms.DateField(
                label="İlk Taksit Tarihi", required=False,
                widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def clean(self):
        cd = super().clean()
        secili = [cd[k] for k in ("cari", "gider", "banka_hesap", "kasa") if cd.get(k)]
        if len(secili) != 1:
            raise forms.ValidationError("Tam olarak bir karşı taraf seçin.")
        cd["karsi"] = secili[0]
        adet = cd.get("taksit_adedi") or 1
        if int(adet) > 1 and not cd.get("ilk_vade"):
            self.add_error("ilk_vade", "Taksitli harcamada ilk taksit tarihi zorunlu.")
        return cd


class KrediHareketForm(forms.Form):
    """Kredi hareketi (Dilim 1: Kullandırım): nakit hesabı Banka VEYA Kasa (tam biri) + tutar +
    tarih + açıklama. Kredi ve tip URL'den; fiş otomatik üretilir."""
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))
    banka_hesap = forms.ModelChoiceField(
        label="Banka Hesabı", required=False, empty_label="— banka hesabı seç —",
        queryset=(BankaHesap.objects.filter(silindi=False)
                  .select_related("banka").order_by("banka__ad", "ad")),
        widget=forms.Select(attrs={"class": "akilli-sec"}))
    kasa = forms.ModelChoiceField(
        label="Kasa", required=False, empty_label="— kasa seç —",
        queryset=Kasa.objects.filter(silindi=False).order_by("ad"),
        widget=forms.Select(attrs={"class": "akilli-sec"}))

    def __init__(self, *args, tip=None, kredi=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banka_hesap"].label_from_instance = (
            lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})")
        self.fields["kasa"].label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"

    def clean(self):
        cd = super().clean()
        secili = [cd[k] for k in ("banka_hesap", "kasa") if cd.get(k)]
        if len(secili) != 1:
            raise forms.ValidationError(
                "Nakit hesabı olarak Banka hesabı VEYA Kasa (yalnız biri) seçin.")
        cd["karsi"] = secili[0]
        return cd


class KrediTaksitForm(forms.Form):
    """Ödeme planı satırı: vade + anapara + faiz (üçü de ELLE)."""
    vade = forms.DateField(
        label="Vade", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    anapara = TRDecimalField(label="Anapara", basamak=2, required=False)
    faiz = TRDecimalField(label="Faiz", basamak=2, required=False)

    def dolu_mu(self):
        cd = getattr(self, "cleaned_data", {})
        return bool(cd.get("vade") or cd.get("anapara") or cd.get("faiz"))


class KrediTaksitOdemeForm(forms.Form):
    """Taksit ödeme başlığı: nakit hesabı (Banka VEYA Kasa) + faiz gider hesabı + tarih.
    Taksit seçimi şablonda checkbox'larla; faiz>0 ise faiz hesabı gerekir (serviste zorlanır)."""
    banka_hesap = forms.ModelChoiceField(
        label="Banka Hesabı", required=False, empty_label="— banka hesabı seç —",
        queryset=(BankaHesap.objects.filter(silindi=False)
                  .select_related("banka").order_by("banka__ad", "ad")),
        widget=forms.Select(attrs={"class": "akilli-sec"}))
    kasa = forms.ModelChoiceField(
        label="Kasa", required=False, empty_label="— kasa seç —",
        queryset=Kasa.objects.filter(silindi=False).order_by("ad"),
        widget=forms.Select(attrs={"class": "akilli-sec"}))
    faiz_hesap = forms.ModelChoiceField(
        label="Faiz Gider Hesabı", required=False, empty_label="— gider hesabı seç —",
        queryset=HesapPlani.objects.none(),
        widget=forms.Select(attrs={"class": "akilli-sec"}))
    tarih = forms.DateField(
        label="Ödeme Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        self.fields["faiz_hesap"].queryset = yaprak_hesaplar()
        self.fields["banka_hesap"].label_from_instance = (
            lambda o: f"{o.banka.ad} · {o.ad} ({o.para_birimi})")
        self.fields["kasa"].label_from_instance = lambda o: f"{o.ad} ({o.para_birimi})"
        self.fields["faiz_hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"

    def clean(self):
        cd = super().clean()
        secili = [cd[k] for k in ("banka_hesap", "kasa") if cd.get(k)]
        if len(secili) != 1:
            raise forms.ValidationError(
                "Nakit hesabı olarak Banka hesabı VEYA Kasa (yalnız biri) seçin.")
        cd["karsi"] = secili[0]
        return cd


class TeklifSiparisForm(forms.Form):
    """Teklif/Sipariş başlığı: cari + tarih + geçerlilik-teslim tarihi + PB + açıklama.
    belge_tur/yon URL'den (view sabit); belge no OTOMATİK (müteselsil) — form alanı değil."""
    cari = forms.ModelChoiceField(
        label="Cari", queryset=Cari.objects.none(), empty_label="— cari seç —")
    tarih = forms.DateField(
        label="Belge tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    gecerlilik_teslim_tarihi = forms.DateField(
        label="Tarih", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    para_birimi = forms.ChoiceField(
        label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    aciklama = forms.CharField(
        label="Açıklama", max_length=500, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, belge_tur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        from core.models import TeklifSiparis
        if belge_tur == TeklifSiparis.BelgeTur.SIPARIS:
            self.fields["gecerlilik_teslim_tarihi"].label = "Teslim Tarihi"
        else:
            self.fields["gecerlilik_teslim_tarihi"].label = "Geçerlilik Tarihi"
        # İrsaliye gerçek stok hareketi yazar — hangi depoya girdiği zorunlu.
        if belge_tur == TeklifSiparis.BelgeTur.IRSALIYE:
            self.fields["depo"] = forms.ModelChoiceField(
                label="Depo", queryset=Depo.objects.filter(silindi=False).order_by("kod"),
                empty_label="— depo seç —")
            self.fields["depo"].widget.attrs["class"] = "akilli-sec"
            # Tedarikçinin KENDİ (kağıt) irsaliye numarası — bizim otomatik belge_no'muzdan
            # ayrı, serbest metin, opsiyonel (Fatura.fatura_no ile aynı desen).
            self.fields["irsaliye_no"] = forms.CharField(
                label="İrsaliye No", max_length=50, required=False,
                widget=forms.TextInput(attrs={"autocomplete": "off"}))


class TeklifSiparisKalemForm(forms.Form):
    """Teklif/Sipariş kalemi: stok × miktar × birim fiyat (Fatura kalem formuyla aynı şekil,
    yalnız birim fiyat 4 ondalık basamak — Satınalma tarafının isteği)."""
    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.none(), required=False, empty_label="— stok seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stok"].queryset = (
            Stok.objects.filter(silindi=False).select_related("kategori", "kdv").order_by("kod"))
        self.fields["stok"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["stok"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        stok = cd.get("stok")
        miktar = cd.get("miktar")
        fiyat = cd.get("birim_fiyat")
        if not stok and miktar is None and fiyat is None:
            return cd                              # boş satır — atlanır
        if not stok:
            raise forms.ValidationError("Stok seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        if fiyat is None or fiyat < 0:
            raise forms.ValidationError("Birim fiyat girin.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


# ---------------------------------------------------------------------------
# STOKLAR Faz B — Depo + Stok hareketi
# ---------------------------------------------------------------------------
class DepoForm(forms.Form):
    kod = forms.CharField(label="Kod", max_length=20,
                          widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ad = forms.CharField(label="Ad", max_length=100,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))


class StokHareketForm(forms.Form):
    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    tur = forms.ChoiceField(label="Tür", choices=StokHareket.Tur.choices,
                            initial=StokHareket.Tur.GIRIS)
    miktar = TRDecimalField(label="Miktar", basamak=3)
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.depo import aktif_depolar
        self.fields["depo"].queryset = aktif_depolar()
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
