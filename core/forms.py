"""Formlar.

Sayı alanları İSTİSNASIZ tek parser/formatter'dan geçer (core.sayi).
İsim/soyisim TR büyük harfe çevrilir; e-posta küçük; TC/telefon doğrulanır.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone

from core.dogrulama import tc_dogrula, telefon_dogrula, telefon_kanonik
from core.metin import buyuk_harf_tr
from core.models import (
    AdayAktivite, AdayAsamaTanim, AdayMusteri, AdayMusteriKategori, AdayPotansiyelTanim,
    AdayTipTanim, Banka,
    BankaHesap, Birim, Cari,
    CariAktivite, CariKategori, KapanisNedeni,
    CekSenet, Depo, DuranVarlik, FaturaSatir, FaturaTipi, FirmaBanka,
    HesapPlani, IsIstasyonu, Kasa, Kategori, KdvOrani, Operasyon, Personel, PersonelBelge,
    PersonelIzin, PersonelUcret, Profil, Sehir, Stok, StokHareket, TanimRenk, TanimSecenegi,
    TevkifatOrani, Ulke, YatirimProjesi, YevmiyeSatir,
)
from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla
from core.tarih import tr_bugun


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
            # basamak yalnız TİPİK gösterim hassasiyetidir — değerin kendisi ondan FAZLA
            # anlamlı ondalık taşıyorsa (örn. model alanı basamak'tan derin bir
            # decimal_places'e sahipse) round-trip'te (formu HİÇ değiştirmeden yeniden
            # kaydetmede) sessiz veri kaybı olmasın diye tam hassasiyetle gösterilir.
            if yuvarla(value, self.basamak) == value:
                return format_tr(value, self.basamak)
            tam_basamak = max(self.basamak, -value.normalize().as_tuple().exponent)
            return format_tr(value, tam_basamak)
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

    # queryset burada DEĞİL __init__ içinde atanır — class gövdesindeki bir default
    # değer yalnız modül ilk import edildiğinde BİR KEZ hesaplanır (_aktif_hesaplar ->
    # yaprak_hesaplar -> _ust_kod_kumesi, hesap kodlarını set() ile hemen materyalize
    # eder); o andan sonra hangi hesaplar "üst/ara" olduğu donar ve bir daha
    # güncellenmez. __init__'te atamak her form örneğinde (istek başına) taze sorgu
    # garanti eder — bkz. KrediKartiHareketForm'daki "gider" alanı, aynı desen.
    hesap = forms.ModelChoiceField(
        label="Hesap", queryset=HesapPlani.objects.none(), to_field_name="hesap_kodu",
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
    # Yalnız hesap 258 (Yapılmakta Olan Yatırımlar) ailesi seçilince anlamlı — fatura
    # dışı (örn. gümrükçü dekontu) tutarları projeye bağlamak için (bkz. clean()).
    yatirim_projesi = forms.ModelChoiceField(
        label="Yatırım projesi", queryset=YatirimProjesi.objects.none(), required=False,
        empty_label="— proje seç —")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hesap"].queryset = _aktif_hesaplar()
        self.fields["yatirim_projesi"].queryset = YatirimProjesi.objects.filter(
            silindi=False, durum=YatirimProjesi.Durum.DEVAM)
        self.fields["yatirim_projesi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"

    def clean(self):
        cd = super().clean()
        hesap = cd.get("hesap")
        borc = cd.get("borc")
        alacak = cd.get("alacak")
        proje = cd.get("yatirim_projesi")
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
        from core.services.hesap_plani import hesap_kodu_258_mi
        if proje and not hesap_kodu_258_mi(hesap.hesap_kodu):
            self.add_error("yatirim_projesi",
                           "Yatırım projesi yalnız 258 hesabı seçilince kullanılabilir.")
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
    gider = forms.BooleanField(
        label="Gider faturası (kalemler stok yerine gider hesabına yazılır; depo/stok hareketi yok — "
              "yalnız Alış)", required=False)
    stopajli = forms.BooleanField(
        label="GV stopajlı (serbest meslek makbuzu — faturada stopaj oranı girilir; "
              "yalnız gider faturası)", required=False)


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
    tedarikci_adi = forms.CharField(
        label="Tedarikçi Ürün Adı", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "tedarikçinin kullandığı isim — boşsa dahili ad kullanılır"}))
    alis_fiyati_pb = forms.ChoiceField(
        label="Alış Fiyatı Para Birimi", choices=Cari.PARA_CHOICES, required=False,
        initial="TRY")
    alis_fiyati = TRDecimalField(
        label="Alış Fiyatı", basamak=6, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "— girilmedi —"}))

    # Ürün grubu — birden çok seçilebilir, en az biri zorunlu (clean() + serviste).
    # Gruba göre formda hangi bölümlerin görüneceği JS ile ayarlanır (stok_form.html).
    satinalma_urunu = forms.BooleanField(label="Satınalma", required=False)
    uretim_urunu = forms.BooleanField(label="Üretim", required=False, initial=True)
    satis_urunu = forms.BooleanField(label="Satış", required=False)

    # Satış/teklif alanları — yalnız Satış işaretliyken formda görünür/anlamlıdır;
    # işaretli değilse serviste None'a (model_kodu için "") sabitlenir
    # (bkz. core/services/stok.py).
    model_kodu = forms.CharField(
        label="Model Kodu", max_length=30, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "örn. A21"}))
    ad_en = forms.CharField(
        label="Ürün Adı (İngilizce)", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "örn. Aluminium Platform Stepladder 2+1"}))
    hs_kodu = forms.CharField(
        label="H/S Kodu", max_length=20, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "örn. 7615.10"}))
    materyal = forms.CharField(
        label="Materyal (Türkçe)", max_length=100, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "örn. Alüminyum"}))
    materyal_en = forms.CharField(
        label="Materyal (İngilizce)", max_length=100, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "örn. Aluminium"}))
    basamak_sayisi = forms.IntegerField(
        label="Basamak Sayısı", required=False, min_value=0)
    yukseklik = TRDecimalField(label="Yükseklik (cm)", basamak=1, required=False)
    acik_derinlik = TRDecimalField(label="Açık Derinlik (cm)", basamak=1, required=False)
    taban_genisligi = TRDecimalField(
        label="Taban Genişliği (cm)", basamak=1, required=False)
    kapali_boy = TRDecimalField(label="Kapalı Boy (cm)", basamak=1, required=False)
    agirlik = TRDecimalField(label="Ağırlık (kg)", basamak=2, required=False)
    azami_yuk = TRDecimalField(label="Azami Yük (kg)", basamak=2, required=False)
    cbm = TRDecimalField(label="CBM (m³ / adet)", basamak=3, required=False)
    yukleme_20dc = forms.IntegerField(
        label="20' DC Yükleme Adedi", required=False, min_value=0)
    yukleme_40hq = forms.IntegerField(
        label="40' HQ Yükleme Adedi", required=False, min_value=0)
    yukleme_tir = forms.IntegerField(
        label="TIR Yükleme Adedi", required=False, min_value=0)
    gorsel = forms.ImageField(label="Ürün Görseli", required=False)

    # Satış fiyat listesi — PB başına sabit 4 alan (StokFiyat, Satış Teklifi ekranının
    # birim fiyatları buradan otomatik gelir). Boş bırakılan PB'nin fiyatı tanımsız kalır.
    fiyat_try = TRDecimalField(label="Satış Fiyatı (TRY)", basamak=4, required=False)
    fiyat_usd = TRDecimalField(label="Satış Fiyatı (USD)", basamak=4, required=False)
    fiyat_eur = TRDecimalField(label="Satış Fiyatı (EUR)", basamak=4, required=False)
    fiyat_gbp = TRDecimalField(label="Satış Fiyatı (GBP)", basamak=4, required=False)

    def clean(self):
        cd = super().clean()
        if not (cd.get("satinalma_urunu") or cd.get("uretim_urunu")
               or cd.get("satis_urunu")):
            raise forms.ValidationError(
                "En az bir grup (Satınalma/Üretim/Satış) seçilmelidir.")
        return cd

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
    """Cari kartı ekle/düzenle (CARİLER). Kod OTOMATİK (formda yok).
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
    telefon_whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    telefon_2 = forms.CharField(label="Telefon 2", max_length=20, required=False,
                                widget=forms.TextInput(attrs={**_K, "inputmode": "tel"}))
    telefon_2_whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs=_K))
    # Doğrulama/normalizasyon serviste (core.dogrulama.web_normalize) — UI'a güvenilmez
    # (spec invariant'ı); form yalnız ham metni alır, "www.x.com" gibi şemasız girişi
    # reddetmez (Django URLField burada reddedebilirdi).
    web = forms.CharField(label="Web", max_length=200, required=False,
                          widget=forms.TextInput(attrs=_K))
    ilgili_kisi = forms.CharField(label="Adı Soyadı", max_length=120, required=False,
                                  widget=forms.TextInput(attrs=_K))
    kep_adresi = forms.CharField(label="KEP", max_length=100, required=False,
                                 widget=forms.TextInput(attrs=_K))
    # Ana adres
    ulke = forms.ModelChoiceField(label="Ülke", queryset=Ulke.objects.none(),
                                  required=False, empty_label="— ülke seç —")
    sehir = forms.ModelChoiceField(label="Şehir", queryset=Sehir.objects.none(),
                                   required=False, empty_label="— şehir seç —")
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 7, **_K}))
    # Ticari
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    # Yalnız para_birimi != TRY iken gösterilir (JS) — bkz. Cari.kur_tipi. required=False:
    # TRY carilerde/eski POST'larda hiç gönderilmeyebilir, servis katmanı boşsa MB_ALIS'e düşer.
    kur_tipi = forms.ChoiceField(label="Kur Tipi", choices=Cari.KurTipi.choices,
                                 initial=Cari.KurTipi.MB_ALIS, required=False)
    kur_degerleme = forms.ChoiceField(label="Kur Değerlemesi", choices=Cari.DegerlemeKurali.choices,
                                      initial=Cari.DegerlemeKurali.OTOMATIK, required=False)
    kur_farki_hedefi = forms.ChoiceField(label="Kur Farkı Hedefi", choices=Cari.KurFarkiHedefi.choices,
                                         initial=Cari.KurFarkiHedefi.OTOMATIK, required=False)
    kredi_limiti = TRDecimalField(label="Kredi/Risk Limiti", basamak=2,
                                  initial=Decimal("0"), required=False)
    iskonto_yuzdesi = TRDecimalField(label="Varsayılan İskonto %", basamak=2,
                                     initial=Decimal("0"), required=False)
    # Boş: koşul yok, fatura vade tarihi elle girilir (varsayılan). odeme_gunu alanı
    # yalnız GÜN_SONRA/SONRAKİ_AY_GÜNÜ seçiliyken gösterilir (JS) — bkz. cari_form.html.
    odeme_kosulu = forms.ChoiceField(
        label="Ödeme Koşulu", required=False,
        choices=[("", "— koşul yok (vade elle girilir) —")] + list(Cari.OdemeKosulu.choices))
    odeme_gunu = forms.IntegerField(label="Gün", required=False, min_value=0, max_value=365,
                                    widget=forms.NumberInput(attrs=_K))
    notlar = forms.CharField(label="Notlar", required=False,
                             widget=forms.Textarea(attrs={"rows": 3, **_K}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari_kategori import aktif_cari_kategoriler
        from core.services.lokasyon import aktif_sehirler, aktif_ulkeler
        # Cari yalnız ALT kategoriye bağlanır; üst (ana) kategoriler seçilemez.
        self.fields["kategori"].queryset = aktif_cari_kategoriler().filter(ust__isnull=False)
        self.fields["kategori"].label_from_instance = lambda o: f"{o.kod_yolu}  {o.ad}"
        self.fields["ulke"].queryset = aktif_ulkeler()
        self.fields["sehir"].queryset = aktif_sehirler()
        self.fields["sehir"].label_from_instance = lambda o: f"{o.ad} ({o.ulke.kod})"
        for f in ("kategori", "ulke", "sehir"):
            self.fields[f].widget.attrs["class"] = "akilli-sec"


class AdayMusteriKategoriForm(forms.Form):
    """Aday müşteri kategorisi ekle/düzenle (CRM). Ad+Kod TR büyük harf + benzersizlik
    serviste. Üst kategori formda değil — ekleme giriş noktasıyla belirlenir (kök / +Alt)."""

    ad = forms.CharField(
        label="Ad", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    kod = forms.CharField(
        label="Kod", max_length=10, widget=forms.TextInput(attrs={"autocomplete": "off"}))


class _AdayTanimFormTaban(forms.Form):
    """Tip/Potansiyel/Aşama tanım formlarının ortak alanları — sistem_kodu formda YOK
    (sistem kaydının kodu hiç değişmez, bkz. core.services.aday_tanim); ad/sıra/renk/aktif
    her zaman düzenlenebilir."""

    ad = forms.CharField(
        label="Ad", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    sira = forms.IntegerField(label="Sıra", initial=0, min_value=0)
    aktif = forms.BooleanField(label="Aktif", required=False, initial=True)
    renk = forms.ChoiceField(label="Renk", choices=TanimRenk.choices, initial=TanimRenk.GRI)


class AdayTipTanimForm(_AdayTanimFormTaban):
    cari_kategori_yurtici = forms.ModelChoiceField(
        label="Yurtiçi Cari Kategorisi", queryset=CariKategori.objects.none(),
        required=False, empty_label="— seçin —")
    cari_kategori_yurtdisi = forms.ModelChoiceField(
        label="Yurtdışı Cari Kategorisi", queryset=CariKategori.objects.none(),
        required=False, empty_label="— seçin —")
    cariye_donusturulebilir = forms.BooleanField(
        label="Cariye dönüştürülebilir", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari_kategori import aktif_cari_kategoriler
        qs = aktif_cari_kategoriler()
        for f in ("cari_kategori_yurtici", "cari_kategori_yurtdisi"):
            self.fields[f].queryset = qs
            self.fields[f].label_from_instance = lambda o: f"{o.kod_yolu}  {o.ad}"
            self.fields[f].widget.attrs["class"] = "akilli-sec"


class AdayPotansiyelTanimForm(_AdayTanimFormTaban):
    sicak = forms.BooleanField(label="Sıcak sekmesine girer", required=False)


class AdayAsamaTanimForm(_AdayTanimFormTaban):
    rol = forms.ChoiceField(label="Rol", choices=AdayAsamaTanim.Rol.choices)


class AdayMusteriForm(forms.Form):
    """Aday müşteri (CRM) ekle/düzenle. TR büyük harf serviste. Kasıtlı olarak Cari'nin
    Kimlik/İletişim + Kategori + Para Birimi + İskonto yapısını yansıtır."""

    _K = {"autocomplete": "off"}
    unvan = forms.CharField(label="Unvan / Ad Soyad", max_length=200,
                            widget=forms.TextInput(attrs=_K))
    ilgili_kisi = forms.CharField(label="İlgili Kişi", max_length=120, required=False,
                                  widget=forms.TextInput(attrs=_K))
    telefon = forms.CharField(label="Telefon", max_length=20, required=False,
                              widget=forms.TextInput(attrs={**_K, "inputmode": "tel"}))
    telefon_whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    telefon_2 = forms.CharField(label="Telefon 2", max_length=20, required=False,
                                widget=forms.TextInput(attrs={**_K, "inputmode": "tel"}))
    telefon_2_whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs=_K))
    eposta_gecersiz = forms.BooleanField(label="Geçersiz (bounce)", required=False)
    eposta_2 = forms.EmailField(label="E-posta 2", required=False,
                                widget=forms.EmailInput(attrs=_K))
    eposta_2_gecersiz = forms.BooleanField(label="Geçersiz (bounce)", required=False)
    # Doğrulama/normalizasyon serviste (core.dogrulama.web_normalize) — bkz. CariForm.web.
    web = forms.CharField(label="Web", max_length=200, required=False,
                          widget=forms.TextInput(attrs=_K))
    ulke = forms.ModelChoiceField(label="Ülke", queryset=Ulke.objects.none(),
                                  required=False, empty_label="— ülke seç —")
    sehir = forms.ModelChoiceField(label="Şehir", queryset=Sehir.objects.none(),
                                   required=False, empty_label="— şehir seç —")
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 5, **_K}))
    # required=True (varsayılan) — seçenekler (optgroup'lu, yalnız yaprak kaynaklar
    # seçilebilir) __init__'te kurulur, bkz. _kaynak_secenekleri. queryset TÜM aktif
    # kaynakları (üst+alt) kapsar çünkü ModelChoiceField.clean() seçimi widget'ın
    # choices'ına değil queryset'e göre doğrular — yaprak kontrolü ayrıca clean()'de.
    kategori = forms.ModelChoiceField(label="Kaynak", queryset=AdayMusteriKategori.objects.none())
    farkli_firma_onay = forms.BooleanField(
        label="Farklı firma, yine de kaydet", required=False)
    tip = forms.ModelChoiceField(label="Tip", queryset=AdayTipTanim.objects.none())
    potansiyel = forms.ModelChoiceField(
        label="Potansiyel", queryset=AdayPotansiyelTanim.objects.none(),
        required=False, empty_label="— belirlenmedi —")
    asama = forms.ModelChoiceField(label="Aşama", queryset=AdayAsamaTanim.objects.none())
    kapanis_nedeni = forms.ChoiceField(
        label="Kapanış Nedeni",
        choices=[("", "— seçin —")] + list(KapanisNedeni.choices), required=False)
    sonraki_adim = forms.CharField(label="Sonraki Adım", max_length=200, required=False,
                                   widget=forms.TextInput(attrs=_K))
    sonraki_adim_tarihi = forms.DateField(
        label="Tarih", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=AdayMusteri.PARA_CHOICES,
                                    initial="TRY")
    iskonto_yuzdesi = TRDecimalField(label="Varsayılan İskonto %", basamak=2,
                                     initial=Decimal("0"), required=False)

    def __init__(self, *args, duzenlenen_aday=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.aday_kategori import aktif_aday_kategoriler
        from core.services.lokasyon import aktif_sehirler, aktif_ulkeler
        self.fields["ulke"].queryset = aktif_ulkeler()
        self.fields["sehir"].queryset = aktif_sehirler()
        self.fields["sehir"].label_from_instance = lambda o: f"{o.ad} ({o.ulke.kod})"
        kaynaklar = list(aktif_aday_kategoriler())
        self.fields["kategori"].queryset = AdayMusteriKategori.objects.filter(
            pk__in=[k.pk for k in kaynaklar])
        self.fields["kategori"].label_from_instance = lambda o: f"{o.kod_yolu}  {o.ad}"
        self.fields["kategori"].choices = self._kaynak_secenekleri(kaynaklar)
        # aktif=False tanımlar formda seçilemez — AMA düzenlenen adayın MEVCUT değeri
        # (artık pasif olsa bile) seçenek listesinden düşmesin (spec: "mevcut kayıtlarda
        # görünmeye devam eder").
        self.fields["tip"].queryset = self._tanim_secenekleri(
            AdayTipTanim, duzenlenen_aday and duzenlenen_aday.tip_id)
        self.fields["potansiyel"].queryset = self._tanim_secenekleri(
            AdayPotansiyelTanim, duzenlenen_aday and duzenlenen_aday.potansiyel_id)
        self.fields["asama"].queryset = self._tanim_secenekleri(
            AdayAsamaTanim, duzenlenen_aday and duzenlenen_aday.asama_id)
        # Kapanış Nedeni'nin JS'te gösterilip gizlenmesi asama <select> değerinin (pk)
        # KAPALI rolüne mi denk geldiğine bakar — template bu pk kümesini JS'e gömer.
        self.kapali_asama_pkleri = list(
            AdayAsamaTanim.objects.filter(silindi=False, rol=AdayAsamaTanim.Rol.KAPALI)
            .values_list("pk", flat=True))
        for f in ("ulke", "sehir", "kategori"):
            self.fields[f].widget.attrs["class"] = "akilli-sec"

    @staticmethod
    def _tanim_secenekleri(Model, mevcut_pk):
        qs = Model.objects.filter(silindi=False, aktif=True)
        if mevcut_pk:
            qs = Model.objects.filter(silindi=False, pk=mevcut_pk) | qs
        return qs.order_by("sira", "ad")

    @staticmethod
    def _kaynak_secenekleri(kaynaklar):
        """Hiyerarşik <select>: alt kaynağı olan üst başlıklar <optgroup> ETİKETİ olarak
        görünür (native HTML optgroup zaten seçilemez) — bkz. spec "Kaynak zorunlu, yalnız
        yaprak seçilebilir". Alt kaynağı OLMAYAN üstler (bugün 02 FUARLAR/03 TAVSİYE/
        04 ZİYARET gibi) kendileri birer yaprak olduğu için doğrudan seçilebilir <option>.
        En fazla 2 seviye olduğu için (bkz. aday_kategori_olustur) tek seviye gruplama yeter."""
        altlar = {}
        for k in kaynaklar:
            if k.ust_id:
                altlar.setdefault(k.ust_id, []).append(k)
        for grup in altlar.values():
            grup.sort(key=lambda k: k.kod)
        secenekler = [("", "— kaynak seç —")]
        for k in kaynaklar:
            if k.ust_id is not None:
                continue
            cocuklar = altlar.get(k.pk)
            if cocuklar:
                secenekler.append((f"{k.kod_yolu}  {k.ad}",
                                   [(c.pk, f"{c.kod_yolu}  {c.ad}") for c in cocuklar]))
            else:
                secenekler.append((k.pk, f"{k.kod_yolu}  {k.ad}"))
        return secenekler

    def clean(self):
        cleaned = super().clean()
        kategori = cleaned.get("kategori")
        if kategori is not None and kategori.alt_kategoriler.filter(silindi=False).exists():
            self.add_error("kategori", "Lütfen alt kaynak seçin.")
        asama = cleaned.get("asama")
        if asama and asama.rol == AdayAsamaTanim.Rol.KAPALI:
            if not cleaned.get("kapanis_nedeni"):
                self.add_error("kapanis_nedeni", "Aşama Kapalı iken kapanış nedeni zorunlu.")
        else:
            cleaned["kapanis_nedeni"] = ""
        if cleaned.get("sonraki_adim_tarihi") and not cleaned.get("sonraki_adim"):
            self.add_error("sonraki_adim",
                           "Sonraki adım tarihi girildiyse ne yapılacağı da yazılmalı.")
        return cleaned


class AdayAktiviteForm(forms.Form):
    """Aday aktivite (görüşme/temas) ekle/düzenle. Sonraki adım metni/tarihi de burada —
    kaydedilince adaya yazılır (bkz. core.services.aday.aday_aktivite_ekle/guncelle);
    Cari aktivite formunda bu alanlar YOK (spec kararı, dokunulmayacaklar listesi)."""

    tarih = forms.DateField(
        label="Tarih", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    tur = forms.ChoiceField(label="Tür", choices=AdayAktivite.Tur.choices)
    aciklama = forms.CharField(label="Açıklama", widget=forms.Textarea(attrs={"rows": 4}))
    sonraki_adim = forms.CharField(label="Sonraki Adım", max_length=200, required=False,
                                   widget=forms.TextInput(attrs={"autocomplete": "off"}))
    sonraki_adim_tarihi = forms.DateField(
        label="Sonraki Adım Tarihi", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("sonraki_adim_tarihi") and not cleaned.get("sonraki_adim"):
            self.add_error("sonraki_adim",
                           "Sonraki adım tarihi girildiyse ne yapılacağı da yazılmalı.")
        return cleaned


class AdayCariyeYeniCariForm(CariForm):
    """(A) Yeni cari aç — CariForm'un TÜMÜNÜ yeniden kullanır (spec: 'aynı form sınıfı/
    validasyon yeniden kullanılsın'); CariForm'un KENDİSİ değiştirilmez (dokunulmayacaklar
    listesi), yalnız bu ekrana özel EK kural miras alınarak eklenir: ülke Türkiye ise
    VKN/TCKN (10 veya 11 hane) + vergi dairesi zorunlu; yurtdışında (veya ülke boşsa) hiçbiri
    aranmaz — mevcut Cari'de format/checksum doğrulaması yok, yalnız hane sayısı kontrol
    edilir (spec: 'mevcut cari validasyonu varsa o' — yoktu)."""

    def clean(self):
        cleaned = super().clean()
        ulke = cleaned.get("ulke")
        turkiye_mi = bool(ulke) and ulke.kod == "TR"
        if turkiye_mi:
            vkn = (cleaned.get("vkn_tckn") or "").strip()
            if not vkn:
                self.add_error("vkn_tckn", "Türkiye'deki carilerde VKN/TCKN zorunlu.")
            elif not (vkn.isdigit() and len(vkn) in (10, 11)):
                self.add_error("vkn_tckn", "VKN/TCKN 10 (VKN) veya 11 (TCKN) haneli olmalı.")
            if not (cleaned.get("vergi_dairesi") or "").strip():
                self.add_error("vergi_dairesi", "Türkiye'deki carilerde vergi dairesi zorunlu.")
        return cleaned


class AdayCariyeMevcutCariForm(forms.Form):
    """(B) Mevcut cariye bağla — cari seçimi (unvan/kod ile akıllı-seç arama, mevcut deseni)."""

    cari = forms.ModelChoiceField(label="Cari", queryset=Cari.objects.none(),
                                  empty_label="— cari seç —")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari import aktif_cariler
        self.fields["cari"].queryset = aktif_cariler()
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"


class CariSevkAdresiForm(forms.Form):
    """Cari sevk (teslimat) adresi ekle/düzenle (çoklu). TR büyük harf serviste."""

    ad = forms.CharField(label="Adres Adı", max_length=100,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ulke = forms.ModelChoiceField(label="Ülke", queryset=Ulke.objects.none(),
                                  required=False, empty_label="— ülke seç —")
    sehir = forms.ModelChoiceField(label="Şehir", queryset=Sehir.objects.none(),
                                   required=False, empty_label="— şehir seç —")
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 5, "autocomplete": "off"}))
    varsayilan = forms.BooleanField(label="Varsayılan", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.lokasyon import aktif_sehirler, aktif_ulkeler
        self.fields["ulke"].queryset = aktif_ulkeler()
        self.fields["sehir"].queryset = aktif_sehirler()
        self.fields["sehir"].label_from_instance = lambda o: f"{o.ad} ({o.ulke.kod})"
        for f in ("ulke", "sehir"):
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
    whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    notlar = forms.CharField(label="Notlar", max_length=200, required=False,
                             widget=forms.TextInput(attrs={"autocomplete": "off"}))


class AdayYetkiliForm(forms.Form):
    """Aday yetkili kişi ekle/düzenle — CariYetkiliForm ile birebir aynı (bkz. dosya başı
    ilke: Cariye Dönüştür'ün alanları ileride birebir aktarabilmesi için)."""

    ad_soyad = forms.CharField(label="Ad Soyad", max_length=120,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    unvan = forms.CharField(label="Görev / Unvan", max_length=80, required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "off"}))
    telefon = forms.CharField(label="Telefon", max_length=20, required=False,
                              widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "tel"}))
    whatsapp = forms.BooleanField(label="Bu numara WhatsApp kullanıyor", required=False)
    eposta = forms.EmailField(label="E-posta", required=False,
                              widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    notlar = forms.CharField(label="Notlar", max_length=200, required=False,
                             widget=forms.TextInput(attrs={"autocomplete": "off"}))


class CariAktiviteForm(forms.Form):
    """Cari aktivite (görüşme/temas) ekle/düzenle. Dosya ekleri ayrı, çoklu işlenir (view'da)."""

    tarih = forms.DateField(
        label="Tarih", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    tur = forms.ChoiceField(label="Tür", choices=CariAktivite.Tur.choices)
    aciklama = forms.CharField(label="Açıklama", widget=forms.Textarea(attrs={"rows": 4}))


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


class TanimSecenegiForm(forms.Form):
    """Tanım seçeneği ekle/düzenle (AYARLAR > Tanım Listeleri: Yükleme Şekli / Ödeme Koşulu /
    Yükleme Tipi). ``kod`` yalnız Yükleme Tipi'nde gösterilir ve zorunludur (serviste)."""

    sira = forms.IntegerField(label="Sıra", min_value=0, initial=0,
                              widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}))
    kod = forms.CharField(label="Kod", max_length=30, required=False,
                          widget=forms.TextInput(attrs={"autocomplete": "off",
                                                        "placeholder": "örn. 20DC, 40HQ, TIR"}))
    ad = forms.CharField(label="Ad", max_length=200,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ad_en = forms.CharField(label="Ad (İngilizce — EN teklif PDF'i)", max_length=200,
                            required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "off",
                                                          "placeholder": "boşsa Türkçe ad kullanılır"}))
    varsayilan = forms.BooleanField(
        label="Varsayılan (yeni teklifte önceden seçili gelsin)", required=False)

    def __init__(self, *args, kategori=None, **kwargs):
        super().__init__(*args, **kwargs)
        if kategori != TanimSecenegi.Kategori.YUKLEME_TIPI:
            self.fields.pop("kod")


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


class FirmaBilgisiForm(forms.Form):
    """AYARLAR > Firma Bilgileri — TR büyük harf serviste (e-posta/web hariç, bkz. core/metin.py)."""
    unvan = forms.CharField(label="Unvan", max_length=200, required=False,
                            widget=forms.TextInput(attrs={"autocomplete": "off"}))
    vergi_dairesi = forms.CharField(label="Vergi Dairesi", max_length=100, required=False,
                                    widget=forms.TextInput(attrs={"autocomplete": "off"}))
    vergi_no = forms.CharField(label="Vergi No", max_length=20, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    telefon = forms.CharField(label="Telefon", max_length=30, required=False,
                              widget=forms.TextInput(attrs={"autocomplete": "off"}))
    eposta = forms.EmailField(label="E-posta", required=False)
    web = forms.CharField(label="Web Sitesi", max_length=200, required=False,
                          widget=forms.TextInput(attrs={"autocomplete": "off"}))
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 2}))
    logo = forms.ImageField(label="Logo", required=False)


class FirmaBankaForm(forms.Form):
    """Firma banka hesabı satırı — Firma Bilgileri ekranında satır ekle/çıkar (formset)."""
    banka_adi = forms.CharField(label="Banka", max_length=100, required=False,
                                widget=forms.TextInput(attrs={"autocomplete": "off"}))
    sube = forms.CharField(label="Şube", max_length=100, required=False,
                           widget=forms.TextInput(attrs={"autocomplete": "off"}))
    hesap_sahibi = forms.CharField(label="Hesap Sahibi", max_length=200, required=False,
                                   widget=forms.TextInput(attrs={"autocomplete": "off"}))
    iban = forms.CharField(label="IBAN", max_length=34, required=False,
                           widget=forms.TextInput(attrs={"autocomplete": "off"}))
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES,
                                    required=False, initial="TRY")

    def clean(self):
        cd = super().clean()
        dolu_mu = any((cd.get(a) or "").strip() for a in
                      ("banka_adi", "sube", "hesap_sahibi", "iban"))
        if not dolu_mu:
            return cd                          # tamamen boş satır — atlanır
        if not (cd.get("banka_adi") or "").strip():
            raise forms.ValidationError("Banka adı girin.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class FasonKesimForm(forms.Form):
    """FASON > Kesim Tanımları ekle/düzenle: 1 adet bitmiş ürün için hangi kesilmiş
    parçadan kaç adet gerektiği (o parçanın hangi ham profilden kesildiği kendi stok
    kartında — bkz. `Stok.kesildigi_profil` — burada seçilmez, salt-okunur gösterilir)."""
    urun = forms.ModelChoiceField(
        label="Ürün (Bitmiş)", queryset=Stok.objects.none(), empty_label="— ürün seç —")
    kesilmis_parca = forms.ModelChoiceField(
        label="Kesilmiş Parça", queryset=Stok.objects.none(), empty_label="— parça seç —")
    adet = forms.IntegerField(label="Adet (1 ürün için)", min_value=1, initial=1,
                              widget=forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}))
    sira = forms.IntegerField(label="Sıra", min_value=0, initial=0,
                              widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["urun"].queryset = (
            Stok.objects.filter(silindi=False, satis_urunu=True).order_by("kod"))
        self.fields["urun"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["urun"].widget.attrs["class"] = "akilli-sec"
        from core.services.fason import kesilmis_parca_secenekleri
        self.fields["kesilmis_parca"].queryset = kesilmis_parca_secenekleri()
        self.fields["kesilmis_parca"].label_from_instance = (
            lambda o: f"{o.kod}  {o.ad}  ←  {o.kesildigi_profil.kod} {o.kesildigi_profil.ad}")
        self.fields["kesilmis_parca"].widget.attrs["class"] = "akilli-sec"


class FasonSatirForm(forms.Form):
    """FASON > Kesim Listesi Hesapla: bir satır (ürün + miktar). Teklif/Sipariş
    kalemleriyle aynı 'boş satır atlanır' deseni (TeklifSiparisKalemForm)."""
    urun = forms.ModelChoiceField(
        label="Ürün", queryset=Stok.objects.none(), required=False, empty_label="— ürün seç —")
    miktar = forms.IntegerField(label="Miktar", min_value=1, required=False,
                                widget=forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["urun"].queryset = (
            Stok.objects.filter(silindi=False, satis_urunu=True).order_by("kod"))
        self.fields["urun"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["urun"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        urun = cd.get("urun")
        miktar = cd.get("miktar")
        if not urun and miktar is None:
            return cd                              # boş satır — atlanır
        if not urun:
            raise forms.ValidationError("Ürün seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


# === ÜRETİM modülü — İş İstasyonları + Operasyon Tanımları + İhtiyaç Hesapla
#     + Üretim Emirleri + Operasyon Kayıtları ===
class IsIstasyonuForm(forms.Form):
    """ÜRETİM > İş İstasyonları — DepoForm ile birebir aynı desen."""
    kod = forms.CharField(label="Kod", max_length=20,
                          widget=forms.TextInput(attrs={"autocomplete": "off"}))
    ad = forms.CharField(label="Ad", max_length=100,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))


class OperasyonBaslikForm(forms.Form):
    """ÜRETİM > Operasyon Tanımları başlığı: istasyon + çıktı (yalnız oluştururken seçilir —
    mevcut bir operasyonun çıktısı sonradan değiştirilemez, bkz. operasyon_guncelle) +
    çıktı miktarı (1 çalıştırmada üretilen adet) + açıklama. Çıktı adayları uretim_urunu=True
    kartlarla sınırlı — ara parçalar da (satis_urunu=False olsalar bile) geçerli çıktıdır."""
    istasyon = forms.ModelChoiceField(
        label="İş İstasyonu", queryset=IsIstasyonu.objects.none(), empty_label="— istasyon seç —")
    cikti = forms.ModelChoiceField(
        label="Çıktı", queryset=Stok.objects.none(), empty_label="— çıktı seç —")
    cikti_miktar = TRDecimalField(label="Çıktı Miktarı (1 çalıştırma için)", basamak=3,
                                  initial=Decimal("1"))
    ad = forms.CharField(label="Ad", max_length=150, required=False,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.uretim import aktif_istasyonlar
        self.fields["istasyon"].queryset = aktif_istasyonlar()
        self.fields["istasyon"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["istasyon"].widget.attrs["class"] = "akilli-sec"
        self.fields["cikti"].queryset = (
            Stok.objects.filter(silindi=False, uretim_urunu=True).order_by("kod"))
        self.fields["cikti"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["cikti"].widget.attrs["class"] = "akilli-sec"


class OperasyonGirdiSatirForm(forms.Form):
    """ÜRETİM > Operasyon Tanımları satırı: girdi + miktar (formset satırı, FASON'daki Kesim
    Listesi Hesapla ile aynı 'boş satır atlanır' deseni)."""
    girdi = forms.ModelChoiceField(
        label="Girdi", queryset=Stok.objects.none(), required=False, empty_label="— girdi seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["girdi"].queryset = Stok.objects.filter(silindi=False).order_by("kod")
        self.fields["girdi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["girdi"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        girdi = cd.get("girdi")
        miktar = cd.get("miktar")
        if not girdi and miktar is None:
            return cd                              # boş satır — atlanır
        if not girdi:
            raise forms.ValidationError("Girdi seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class IhtiyacHesaplaSatirForm(forms.Form):
    """ÜRETİM > İhtiyaç Hesapla satırı: hedef ürün + miktar (formset satırı, aynı 'boş satır
    atlanır' deseni). Hedef adayları en az bir aktif Operasyon'u olan kartlarla sınırlı —
    aksi hâlde hesaplanacak hiçbir zincir yoktur."""
    hedef = forms.ModelChoiceField(
        label="Hedef Ürün", queryset=Stok.objects.none(), required=False,
        empty_label="— ürün seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.uretim import operasyonlu_stok_idler
        self.fields["hedef"].queryset = (
            Stok.objects.filter(silindi=False, pk__in=operasyonlu_stok_idler()).order_by("kod"))
        self.fields["hedef"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["hedef"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        hedef = cd.get("hedef")
        miktar = cd.get("miktar")
        if not hedef and miktar is None:
            return cd                              # boş satır — atlanır
        if not hedef:
            raise forms.ValidationError("Hedef ürün seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class UrunAgaciForm(forms.Form):
    """ÜRETİM > Ürün Ağacı (salt-okunur, GET): ağacı gösterilecek ürün + isteğe bağlı miktar
    (boş = 1, yani "bir adet için ağaç"). Ürün adayları en az bir aktif Operasyon'u olan
    kartlarla sınırlı — İhtiyaç Hesapla ile aynı kural; aksi hâlde gösterilecek zincir yoktur."""
    urun = forms.ModelChoiceField(
        label="Ürün", queryset=Stok.objects.none(), empty_label="— ürün seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False, initial=Decimal("1"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.uretim import operasyonlu_stok_idler
        self.fields["urun"].queryset = (
            Stok.objects.filter(silindi=False, pk__in=operasyonlu_stok_idler()).order_by("kod"))
        self.fields["urun"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["urun"].widget.attrs["class"] = "akilli-sec"

    def clean_miktar(self):
        miktar = self.cleaned_data.get("miktar")
        if miktar is None:
            return Decimal("1")
        if miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        return miktar


class UretimEmriBaslikForm(forms.Form):
    """ÜRETİM > Üretim Emirleri başlık alanları — hem manuel (+ Yeni) hem sipariş-kaynaklı
    oluşturma ekranında ortak (depo/tarih emrin TÜMÜNE, tüm kalemlere uygulanır)."""
    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.depo import aktif_depolar
        depolar = aktif_depolar()
        self.fields["depo"].queryset = depolar
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["depo"].widget.attrs["class"] = "akilli-sec"
        # FaturaForm ile aynı desen: yeni emirde ANA DEPO ön-seçili.
        if not self.is_bound and "depo" not in self.initial:
            vd = depolar.filter(ad="ANA DEPO").first() or depolar.first()
            if vd:
                self.fields["depo"].initial = vd.pk


class UretimEmriKalemSatirForm(forms.Form):
    """Manuel (+ Yeni) Üretim Emri ekranı satırı — serbest ürün seçimi, diğer formset
    satırlarıyla birebir aynı 'boş satır atlanır' deseni."""
    hedef_urun = forms.ModelChoiceField(
        label="Hedef Ürün", queryset=Stok.objects.none(), required=False,
        empty_label="— ürün seç —")
    hedef_miktar = TRDecimalField(label="Hedef Miktar", basamak=3, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.uretim import operasyonlu_stok_idler
        self.fields["hedef_urun"].queryset = (
            Stok.objects.filter(silindi=False, pk__in=operasyonlu_stok_idler()).order_by("kod"))
        self.fields["hedef_urun"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["hedef_urun"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        urun = cd.get("hedef_urun")
        miktar = cd.get("hedef_miktar")
        if not urun and miktar is None:
            return cd                              # boş satır — atlanır
        if not urun:
            raise forms.ValidationError("Hedef ürün seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Hedef miktar sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class SiparisUretimEmriSatirForm(forms.Form):
    """Sipariş → Üretim Emri Aç onay ekranı satırı: sipariş kalemi SABİT (hidden kalem_id),
    yalnız hedef miktar düzenlenebilir — '✕' ile kaldırılan satır aynı 'boş satır atlanır'
    deseniyle (clean()) sessizce dışlanır, ürün burada SEÇİLEMEZ (yalnız siparişin kendi
    kalemleri arasından, view tarafında belirlenir)."""
    kalem_id = forms.IntegerField(widget=forms.HiddenInput, required=False)
    hedef_miktar = TRDecimalField(label="Hedef Miktar", basamak=3, required=False)

    def clean(self):
        cd = super().clean()
        kalem_id = cd.get("kalem_id")
        miktar = cd.get("hedef_miktar")
        if not kalem_id and miktar is None:
            return cd                              # boş/kaldırılmış satır — atlanır
        if not kalem_id:
            raise forms.ValidationError("Sipariş kalemi eksik.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Hedef miktar sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


class OperasyonKaydiForm(forms.Form):
    """ÜRETİM > Operasyon Kayıtları: bağımsız/serbest kayıt açma formu (bir istasyonun kendi
    inisiyatifiyle, herhangi bir Üretim Emri'ne bağlı olmadan açtığı kayıt) — operasyon +
    hedef çıktı miktarı + depo + tarih."""
    operasyon = forms.ModelChoiceField(
        label="Operasyon", queryset=Operasyon.objects.none(), empty_label="— operasyon seç —")
    hedef_cikti_miktari = TRDecimalField(label="Hedef Çıktı Miktarı", basamak=3)
    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.uretim import aktif_operasyonlar
        self.fields["operasyon"].queryset = aktif_operasyonlar()
        self.fields["operasyon"].label_from_instance = (
            lambda o: f"{o.istasyon.kod} — {o.cikti.kod} {o.cikti.ad}")
        self.fields["operasyon"].widget.attrs["class"] = "akilli-sec"
        from core.services.depo import aktif_depolar
        self.fields["depo"].queryset = aktif_depolar()
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["depo"].widget.attrs["class"] = "akilli-sec"


class OperasyonKaydiGirdiDuzeltForm(forms.Form):
    """ÜRETİM > Operasyon Kaydı detayı: TASLAK'ta satır bazında gerçekleşen miktarı düzeltme
    formseti (gizli satir_id + tek TR ondalık alan)."""
    satir_id = forms.IntegerField(widget=forms.HiddenInput)
    gerceklesen_miktar = TRDecimalField(label="Gerçekleşen", basamak=3)


class BordroBaslikForm(forms.Form):
    """Çek/senet bordrosu başlığı: cari + işlem tarihi + para birimi (giriş ve çıkış ortak)."""
    cari = forms.ModelChoiceField(label="Cari", queryset=Cari.objects.none(),
                                  empty_label="— cari seç —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    para_birimi = forms.ChoiceField(label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    # TRY'de anlamsız (JS ile gizlenir). Doluysa carinin kur_tipi tercihine göre otomatik
    # hesaplanan kur YERİNE bu kullanılır.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)

    def __init__(self, *args, cari_label="Cari", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].label = cari_label
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})


class CariCiroForm(forms.Form):
    """Cari Ciro başlığı: ciro edilen cari (yeni hamil) + işlem tarihi. (Evrak seçimi
    şablonda checkbox listesiyle; PB seçilen evraktan türer.)"""
    cari = forms.ModelChoiceField(label="Ciro Edilen Cari (yeni hamil)",
                                  queryset=Cari.objects.none(), empty_label="— cari seç —")
    tarih = forms.DateField(
        label="İşlem Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    # Seçilen evrakın PB'si TRY değilse gösterilir (JS); ciro edilen carinin kur_tipi
    # tercihine göre otomatik doldurulur. Doluysa otomatik hesaplama YERİNE kullanılır.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})


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
    # Yalnız ALIŞ ekranında gösterilir (bkz. fatura_ekle.html); SATIŞ'ta hiç render edilmez.
    vade_tarihi = forms.DateField(
        label="Vade Tarihi", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    para_birimi = forms.ChoiceField(
        label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    # TRY'de anlamsız (JS ile gizlenir). Doluysa carinin kur_tipi tercihine göre otomatik
    # hesaplanan kur YERİNE bu kullanılır — bkz. core.services.fatura._hazirla/fatura_onayla.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), required=False,
        empty_label="— depo seç —")
    aciklama = forms.CharField(
        label="Açıklama", max_length=300, required=False,
        widget=forms.Textarea(attrs={"rows": 2}))
    # Yalnız ALIŞ-GİDER faturasında anlamlı (bkz. fatura_ekle.html JS) — şirkete kesilmiş
    # ama ortağın şahsi harcaması olan fatura: kalem(ler) ortak hesabına (131 ailesi) KDV
    # DAHİL borçlanır, KDV'nin aynı tutarı "FAZLA KDV" hesabına alacak yazılır.
    sahsi_alis = forms.BooleanField(label="Ortak adına (şahsi) alış", required=False)
    # Yalnız GV stopajlı tipte (serbest meslek makbuzu) gösterilir/zorunludur — stopaj =
    # brüt ücret × oran/100 (bkz. core.services.fatura, FaturaTipi.stopajli).
    gv_stopaj_orani = TRDecimalField(
        label="GV Stopaj Oranı (%)", basamak=2, required=False, initial=Decimal("20"))
    sahsi_ortak = forms.ModelChoiceField(
        label="Ortak Hesabı", queryset=HesapPlani.objects.none(), required=False,
        empty_label="— ortak seç —")

    def __init__(self, *args, yon=None, **kwargs):
        super().__init__(*args, **kwargs)
        tipler = FaturaTipi.objects.filter(silindi=False)
        if yon:
            tipler = tipler.filter(yon=yon)
        self.fields["tip"].queryset = tipler.order_by("sira", "ad")
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
        depolar = Depo.objects.filter(silindi=False).order_by("kod")
        self.fields["depo"].queryset = depolar
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        # Yeni faturada ANA DEPO ön-seçili; düzenlemede faturanın deposu (initial) korunur.
        if not self.is_bound and "depo" not in self.initial:
            vd = depolar.filter(ad="ANA DEPO").first() or depolar.first()
            if vd:
                self.fields["depo"].initial = vd.pk
        from core.services.hesap_plani import ortak_hesaplari
        self.fields["sahsi_ortak"].queryset = ortak_hesaplari()
        self.fields["sahsi_ortak"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["sahsi_ortak"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        if cd.get("sahsi_alis") and not cd.get("sahsi_ortak"):
            self.add_error("sahsi_ortak", "Ortak adına şahsi alış için bir ortak hesabı seçin.")
        return cd


def _gruplu_secenekler_uygula(field, gruplar):
    """Bir ModelChoiceField'ın WIDGET'ına <optgroup> destekli seçenek listesi uygular.
    ``gruplar``: sıralı [(grup_etiketi, queryset), ...]. field.queryset grupların birleşimi
    olmalı (doğrulama ondan çalışır — bkz. ModelChoiceField.to_python); bu fonksiyon yalnız
    GÖRÜNÜMÜ (widget.choices) değiştirir, field.label_from_instance set edildikten SONRA
    çağrılmalıdır. bkz. FaturaSatirForm.hesap — "Gider Hesapları" / "Duran Varlık Hesapları"."""
    secenekler = [("", field.empty_label)]
    for etiket, qs in gruplar:
        alt = [(o.pk, field.label_from_instance(o)) for o in qs]
        if alt:
            secenekler.append((etiket, alt))
    field.widget.choices = secenekler


# Fatura kalemi tevkifat seçicisinde "açıkça tevkifat yok" seçeneğinin değeri — boş
# seçimden (stoklu kalemde stok kartının GÜNCEL tevkifatını kullan) kasıtlı olarak AYRI
# (bkz. FaturaSatirForm.tevkifat, core.services.fatura._satir_coz, core.views
# ._fatura_satir_girdileri).
TEVKIFAT_YOK = "YOK"


class FaturaSatirForm(forms.Form):
    """Fatura kalemi — Teklif/Sipariş kalem formuyla aynı şekil, aynı sebeple birim fiyat
    4 ondalık basamak (bkz. TeklifSiparisKalemForm): fatura kalemleri artık Satınalma
    zincirinde bir Sipariş/İrsaliye'den 4 basamaklı fiyatla devralınabiliyor — form yalnız
    2 basamak destekleseydi (eski hâli), TASLAK faturayı Düzenle'den tip atayıp kaydederken
    (fatura_onayla'dan önce zorunlu adım) fiyat sessizce 2 basamağa yuvarlanıp kalıcı veri
    kaybına yol açardı (`prepare_value` initial'ı basamak sayısına göre biçimlendirir)."""
    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.none(), required=False, empty_label="— stok seç —")
    # GİDER faturasında (FaturaTipi.gider) stok yerine kullanılır: yaprak gider hesabı
    # (önce 730.x/770.x) + satırda seçilen KDV oranı. Şablon JS'i fatura tipine göre
    # stok ↔ hesap alanlarını değiştirir; kesin doğrulama serviste (tip × kalem türü).
    hesap = forms.ModelChoiceField(
        label="Gider Hesabı", queryset=HesapPlani.objects.none(), required=False,
        empty_label="— gider hesabı seç —")
    kdv = forms.ModelChoiceField(
        label="KDV", queryset=KdvOrani.objects.none(), required=False, empty_label=None)
    # Yalnız duran varlık hesabı (253/254/255/258/260) seçiliyken anlamlı — 258'de ZORUNLU,
    # diğerlerinde opsiyonel (clean() doğrular); gider hesaplarında (7xx/65x/66x/68x) şablon
    # JS'i alanı gizler, serviste de yok sayılır.
    yatirim_projesi = forms.ModelChoiceField(
        label="Yatırım Projesi", queryset=YatirimProjesi.objects.none(), required=False,
        empty_label="— proje seç —")
    # 253/254/255/260 GRUBU (ör. 253.01) seçilince açılacak YENİ kartın adı (boşsa grup adı); kart hesabı seçilirse yok sayılır.
    varlik_adi = forms.CharField(
        label="Yeni kart adı", max_length=200, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Yeni kart adı (grup seçildiyse)", "autocomplete": "off"}))
    # Stoklu kalemde stok kartından (JS) otomatik ön-dolar ama DEĞİŞTİRİLEBİLİR; hesap
    # (gider/duran varlık) kaleminde elle seçilir. ÜÇ AYRI durum (bkz. TEVKIFAT_YOK altında
    # modül seviyesi sabiti + core.services.fatura._satir_coz): "" (boş) = stoklu kalemde
    # stok kartındaki GÜNCEL tevkifatı kullan (gider kaleminde anlamsız, tevkifatsız
    # sayılır); TEVKIFAT_YOK = açıkça "tevkifat uygulanmasın" (stok kartında tanımlı olsa
    # bile); <pk> = o tevkifatı uygula.
    tevkifat = forms.ChoiceField(label="Tevkifat", required=False, choices=[])
    # SATIŞ faturasında satır türü: STOK / HESAP (alış iade: gider·258 hesabı ALACAK) / DEMIRBAS (aktif duran
    # varlık kartı satışı). Gider faturasında ve alış yönünde kullanılmaz (JS gizler).
    tur = forms.ChoiceField(
        label="Satır türü", required=False, initial="STOK",
        choices=[("STOK", "Stok"), ("HESAP", "Hesap satırı"), ("DEMIRBAS", "Demirbaş satışı")])
    demirbas = forms.ModelChoiceField(
        label="Demirbaş", queryset=DuranVarlik.objects.none(), required=False,
        empty_label="— demirbaş seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)

    def __init__(self, *args, yon=None, fatura_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.yon = yon
        self.fields["tur"].widget.attrs["class"] = "tur-secim"
        # Yalnız AKTİF (satılmamış) kartlar; düzenlenen faturanın kendi sattığı kart da listede kalır.
        dq = Q(durum=DuranVarlik.Durum.AKTIF)
        if fatura_pk:
            dq |= Q(durum=DuranVarlik.Durum.SATILDI, satis_faturasi_id=fatura_pk)
        self.fields["demirbas"].queryset = DuranVarlik.objects.filter(dq, silindi=False).order_by("demirbas_kodu")
        self.fields["demirbas"].label_from_instance = (
            lambda o: f"{o.demirbas_kodu}  {o.ad} · maliyet {o.maliyet:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        self.fields["demirbas"].widget.attrs["class"] = "akilli-sec"
        self.fields["stok"].queryset = (
            Stok.objects.filter(silindi=False).select_related("kategori", "kdv").order_by("kod"))
        if yon == "ALIS":
            # Satınalma tarafında yalnız stok adı gösterilir (kod yok) — tedarikçi ürün
            # adı seçim sonrası alan altında ayrıca gösterilir (bkz. ilgili şablonun
            # tedarikci-etiket JS'i, _stok_meta/_stok_kdv_tevkifat'taki yon parametresi).
            self.fields["stok"].label_from_instance = lambda o: o.ad
        else:
            self.fields["stok"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["stok"].widget.attrs["class"] = "akilli-sec"

        from core.services.hesap_plani import (duran_varlik_hesaplari, gider_hesaplari,
                                               ortak_hesaplari)
        gider_qs = gider_hesaplari()
        duran_qs = duran_varlik_hesaplari()
        # ortak_qs: Ortak adına şahsi alışta JS, gizlenen bu alana ortak hesabını otomatik
        # yazar (bkz. fatura_ekle.html sahsiUygula) — kalemde elle seçilmez, ama form/DB
        # kısıtının "stok veya hesap dolu olmalı" şartını karşılaması için queryset'te olmalı.
        ortak_qs = ortak_hesaplari()
        from core.services import duran_hesap
        duran_grup_qs = duran_hesap.grup_hesaplari(duran_hesap.KART_AILELERI)
        self.fields["hesap"].queryset = (gider_qs | duran_qs | ortak_qs | duran_grup_qs).distinct()
        self.fields["hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["hesap"].widget.attrs["class"] = "akilli-sec"
        if yon == "SATIS":
            # Satış faturası hesap satırı (alış iadesi): yalnız gider hesapları + 258 ailesi.
            from core.services.fatura import _satis_hesap_kumesi
            satis_qs = _satis_hesap_kumesi()
            self.fields["hesap"].queryset = satis_qs
            self.fields["hesap"].label = "Hesap"
            self.fields["hesap"].empty_label = "— hesap seç —"
            _gruplu_secenekler_uygula(self.fields["hesap"], [
                ("Gider Hesapları", gider_qs),
                ("Yatırım (258)", duran_qs.filter(Q(hesap_kodu="258") | Q(hesap_kodu__startswith="258.")))])
        else:
            _gruplu_secenekler_uygula(self.fields["hesap"], [
                ("Gider Hesapları", gider_qs),
                ("Duran Varlık — YENİ KART AÇ (grup seç)", duran_grup_qs),
                ("Duran Varlık — MEVCUT KARTA EKLE / Yatırım (258: proje seç)", duran_qs),
                ("Ortak Hesapları (şahsi alış)", ortak_qs)])
        from core.services.yatirim_projesi import aktif_projeler
        self.fields["yatirim_projesi"].queryset = aktif_projeler()
        self.fields["yatirim_projesi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["yatirim_projesi"].widget.attrs["class"] = "akilli-sec"
        kdvler = KdvOrani.objects.filter(silindi=False).order_by("oran")
        self.fields["kdv"].queryset = kdvler
        self.fields["kdv"].label_from_instance = (
            lambda o: "KDV %" + str(int(o.oran) if o.oran == int(o.oran) else o.oran))
        varsayilan = kdvler.filter(oran=20).first() or kdvler.last()
        if varsayilan is not None:
            self.fields["kdv"].initial = varsayilan.pk       # gider kaleminde en sık: %20
        tevkifatlar = TevkifatOrani.objects.filter(silindi=False).order_by("pay")
        self.fields["tevkifat"].choices = (
            [("", "— stok kartından gelsin —"), (TEVKIFAT_YOK, "— Tevkifat yok —")]
            + [(str(t.pk), t.kod) for t in tevkifatlar])
        self.fields["tevkifat"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        from decimal import Decimal as _D
        from core.services.hesap_plani import hesap_kodu_258_mi, hesap_kodu_duran_varlik_mi
        cd = super().clean()
        stok = cd.get("stok")
        hesap = cd.get("hesap")
        dv = cd.get("demirbas")
        tur = (cd.get("tur") or "STOK") if self.yon == "SATIS" else "STOK"
        miktar = cd.get("miktar")
        fiyat = cd.get("birim_fiyat")
        if not stok and not hesap and not dv and miktar is None and fiyat is None:
            return cd                              # boş satır — atlanır
        if self.yon == "SATIS":
            if tur == "HESAP" and not hesap:
                raise forms.ValidationError("Hesap satırı için bir hesap seçin.")
            if tur == "DEMIRBAS" and not dv:
                raise forms.ValidationError("Demirbaş satırı için satılacak kartı seçin.")
            if tur == "STOK" and not stok:
                raise forms.ValidationError("Stok seçin (ya da satır türünü Hesap/Demirbaş yapın).")
            # Seçilen türün dışındaki alanlar yok sayılır (JS temizler; sunucu da temizler).
            if tur != "STOK":
                stok = cd["stok"] = None
            if tur != "HESAP":
                hesap = cd["hesap"] = None if tur != "DEMIRBAS" else None
            if tur != "DEMIRBAS":
                dv = cd["demirbas"] = None
        else:
            dv = cd["demirbas"] = None
        if stok and hesap:
            raise forms.ValidationError("Ya stok ya da gider hesabı seçin; ikisi birden olmaz.")
        if not stok and not hesap and not dv:
            raise forms.ValidationError("Stok seçin (gider faturasında gider hesabı seçin).")
        if dv is not None and miktar is None:
            miktar = cd["miktar"] = _D("1")         # demirbaş tek birimdir
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        if fiyat is None or fiyat < 0:
            raise forms.ValidationError("Birim fiyat girin." if dv is None else "Satış bedelini girin.")
        if stok:
            cd["kdv"] = None                       # stok kaleminde KDV stoktan gelir
            cd["yatirim_projesi"] = None
        elif dv is not None:
            cd["yatirim_projesi"] = None
        elif hesap and hesap_kodu_duran_varlik_mi(hesap.hesap_kodu):
            cd["varlik_adi"] = (cd.get("varlik_adi") or "").strip()
            if hesap_kodu_258_mi(hesap.hesap_kodu) and not cd.get("yatirim_projesi"):
                raise forms.ValidationError(
                    f"{hesap.hesap_kodu} hesabı için yatırım projesi seçimi zorunludur.")
            if cd.get("yatirim_projesi") and cd["yatirim_projesi"].durum == YatirimProjesi.Durum.AKTIFLESTI:
                raise forms.ValidationError(
                    f"{cd['yatirim_projesi'].kod} projesi aktifleşmiş; yeni kalem eklenemez.")
        else:
            cd["yatirim_projesi"] = None           # gider hesabında proje anlamsız — temizle
            cd["varlik_adi"] = ""
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


def _yeni_kart_hedefleri():
    """Yeni duran varlık kartının bağlanacağı hedef: GRUP hesapları (253.01 …; sistem sıradaki 000N hesabını açar) + (grup
    açılmamış eski planda) kartsız düz yaprak hesaplar. Mevcut kart hesapları (253.01.0001 …) ASLA seçilemez (hesap başına tek kart)."""
    from core.services import duran_hesap
    from core.services.hesap_plani import duran_varlik_karti_hesaplari
    duz = duran_varlik_karti_hesaplari().exclude(hesap_kodu__regex=r"^\d{3}\.\d{2}\.\d{4}$")
    return (duran_hesap.grup_hesaplari(duran_hesap.KART_AILELERI) | duz).distinct().order_by("hesap_kodu")


class DuranVarlikForm(forms.Form):
    """Duran varlık kartı ekle (DURAN VARLIK FAZ 2). Demirbaş kodu OTOMATİK; kaynak/
    durum serviste belirlenir (forma hiç konmaz — bkz. core.services.duran_varlik)."""

    _K = {"autocomplete": "off"}
    ad = forms.CharField(label="Ad", max_length=200, widget=forms.TextInput(attrs=_K))
    hesap = forms.ModelChoiceField(label="Grup (yeni hesap otomatik açılır)", queryset=HesapPlani.objects.none(),
                                   empty_label="— grup seç —")
    aktiflestirme_tarihi = forms.DateField(
        label="Aktifleştirme Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    maliyet = TRDecimalField(label="Maliyet (KDV Hariç, TRY)", basamak=2)
    marka_model = forms.CharField(label="Marka / Model", max_length=200, required=False,
                                  widget=forms.TextInput(attrs=_K))
    seri_no = forms.CharField(label="Seri No", max_length=100, required=False,
                              widget=forms.TextInput(attrs=_K))
    notlar = forms.CharField(label="Notlar", required=False,
                             widget=forms.Textarea(attrs={"rows": 3, **_K}))
    fatura_satir_ids = forms.ModelMultipleChoiceField(
        label="Fatura Kalemleri", queryset=FaturaSatir.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple)
    karsi_hesap = forms.ModelChoiceField(
        label="Karşı hesap (opsiyonel)", queryset=HesapPlani.objects.none(), required=False, to_field_name="hesap_kodu",
        empty_label="— yok (fiş oluşmaz) —")

    def __init__(self, *args, satir_adaylari=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hesap"].queryset = _yeni_kart_hedefleri()
        self.fields["hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["hesap"].widget.attrs["class"] = "akilli-sec"
        from core.services.duran_varlik import karsi_hesaplari
        self.fields["karsi_hesap"].queryset = karsi_hesaplari().order_by("hesap_kodu")
        self.fields["karsi_hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["karsi_hesap"].widget.attrs["class"] = "akilli-sec"
        self.fields["fatura_satir_ids"].queryset = (
            satir_adaylari if satir_adaylari is not None else FaturaSatir.objects.none())
        self.fields["fatura_satir_ids"].label_from_instance = (
            lambda s: f"{s.fatura.fatura_no or 'taslak'} — {s.tutar} TL")


class DuranVarlikDuzenleForm(forms.Form):
    """Duran varlık kartı düzenle — yalnız ad/marka-model/seri no/notlar/maliyet.
    Hesap ve kaynak SABİTTİR, bu formda hiç yer almaz (bkz. core.services.duran_varlik
    .duran_varlik_guncelle)."""

    _K = {"autocomplete": "off"}
    ad = forms.CharField(label="Ad", max_length=200, widget=forms.TextInput(attrs=_K))
    maliyet = TRDecimalField(label="Maliyet (KDV Hariç, TRY)", basamak=2)
    birikmis_amortisman = TRDecimalField(label="Birikmiş Amortisman (257, TRY)", basamak=2, required=False,
                                         initial=Decimal("0"))
    marka_model = forms.CharField(label="Marka / Model", max_length=200, required=False,
                                  widget=forms.TextInput(attrs=_K))
    seri_no = forms.CharField(label="Seri No", max_length=100, required=False,
                              widget=forms.TextInput(attrs=_K))
    notlar = forms.CharField(label="Notlar", required=False,
                             widget=forms.Textarea(attrs={"rows": 3, **_K}))
    karsi_hesap = forms.ModelChoiceField(
        label="Karşı hesap (opsiyonel)", queryset=HesapPlani.objects.none(), required=False, to_field_name="hesap_kodu",
        empty_label="— yok (fiş oluşmaz) —")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.duran_varlik import karsi_hesaplari
        self.fields["karsi_hesap"].queryset = karsi_hesaplari().order_by("hesap_kodu")
        self.fields["karsi_hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["karsi_hesap"].widget.attrs["class"] = "akilli-sec"


class AktiflestirmeBaslikForm(forms.Form):
    """Yatırım projesini aktifleştir (DURAN VARLIK FAZ 3) — tarih başlığı."""
    tarih = forms.DateField(
        label="Aktifleştirme Tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))


class AktiflestirmeSatirForm(forms.Form):
    """Aktifleştirme satırı: 258 toplamının bölüneceği hedef hesap + varlık adı + tutar."""

    _K = {"autocomplete": "off"}
    hesap = forms.ModelChoiceField(label="Hedef Grup", queryset=HesapPlani.objects.none(),
                                   empty_label="— grup seç —")
    varlik_adi = forms.CharField(label="Varlık Adı", max_length=200,
                                 widget=forms.TextInput(attrs=_K))
    tutar = TRDecimalField(label="Tutar", basamak=2)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hesap"].queryset = _yeni_kart_hedefleri()
        self.fields["hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["hesap"].widget.attrs["class"] = "akilli-sec"

    def dolu_mu(self) -> bool:
        cd = getattr(self, "cleaned_data", {}) or {}
        return bool(cd.get("hesap") or cd.get("varlik_adi") or cd.get("tutar"))


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
    # Kasa döviz ise gösterilir (JS); karşı taraf Cari'yse onun kur_tipi tercihine göre
    # otomatik doldurulur (bkz. core.services.kasa_hareket.hareket_olustur). Doluysa
    # otomatik hesaplama YERİNE doğrudan kullanılır.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, kasa=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.kasa_hareket import HAREKET
        tur = HAREKET.get(tip, {}).get("karsi", "cari")
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
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
    # Banka hesabı döviz ise gösterilir (JS); karşı taraf Cari'yse onun kur_tipi tercihine
    # göre otomatik doldurulur. Doluysa otomatik hesaplama YERİNE doğrudan kullanılır.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, banka_hesap=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.banka_hareket import HAREKET
        tur = HAREKET.get(tip, {}).get("karsi", "cari")
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
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


class BankaHesapHareketForm(forms.Form):
    """Hesaba Ödeme / Hesaptan Giriş başlığı: banka tutarı + tarih + (döviz hesapta) kur + açıklama.
    Karşı hesap satırları ``BankaHesapSatirFormSet``'te (bkz. core.services.banka_hareket)."""
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})


class BankaHesapSatirForm(forms.Form):
    """Tek karşı hesap satırı: hesap + tutar (tek satırda boşsa tamamı) + açıklama + (258'de zorunlu)
    yatırım projesi."""
    hesap = forms.ModelChoiceField(
        label="Karşı hesap", queryset=HesapPlani.objects.none(), to_field_name="hesap_kodu",
        empty_label="— hesap seç —", required=False)
    tutar = TRDecimalField(label="Tutar", basamak=2, required=False)
    yatirim_projesi = forms.ModelChoiceField(
        label="Yatırım projesi", queryset=YatirimProjesi.objects.none(), required=False,
        empty_label="— proje —")
    aciklama = forms.CharField(label="Satır açıklaması", required=False, max_length=200)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        from core.services.yatirim_projesi import aktif_projeler
        self.fields["hesap"].queryset = yaprak_hesaplar()
        self.fields["hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["hesap"].widget.attrs["class"] = "akilli-sec"
        self.fields["yatirim_projesi"].queryset = aktif_projeler().filter(
            durum=YatirimProjesi.Durum.DEVAM)
        self.fields["yatirim_projesi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"

    def clean(self):
        cd = super().clean()
        hesap, proje = cd.get("hesap"), cd.get("yatirim_projesi")
        if hesap is not None:
            from core.services.hesap_plani import hesap_kodu_258_mi
            if hesap_kodu_258_mi(hesap.hesap_kodu) and not proje:
                self.add_error("yatirim_projesi", "258 hesabı için yatırım projesi seçilmelidir.")
            if not hesap_kodu_258_mi(hesap.hesap_kodu) and proje:
                self.add_error("yatirim_projesi", "Yatırım projesi yalnız 258 hesabında seçilebilir.")
        elif cd.get("tutar") or proje:
            self.add_error("hesap", "Karşı hesap seçilmelidir.")
        return cd


def _yatirim_projesi_alani():
    from core.services.yatirim_projesi import aktif_projeler
    f = forms.ModelChoiceField(
        label="Yatırım projesi", required=False, empty_label="— proje (yalnız 258 için) —",
        queryset=aktif_projeler().filter(durum=YatirimProjesi.Durum.DEVAM))
    f.label_from_instance = lambda o: f"{o.kod}  {o.ad}"
    f.widget.attrs["class"] = "akilli-sec"
    return f


def _yatirim_projesi_denetle(form, gider, proje):
    """Gider hesabı 258 ailesindeyse proje zorunlu; değilse proje seçilemez."""
    from core.services.hesap_plani import hesap_kodu_258_mi
    if gider is not None and hesap_kodu_258_mi(gider.hesap_kodu):
        if not proje:
            form.add_error("yatirim_projesi", "258 hesabı için yatırım projesi seçilmelidir.")
    elif proje:
        form.add_error("yatirim_projesi", "Yatırım projesi yalnız 258 hesabında seçilebilir.")


class KrediKartiHareketDuzenleForm(forms.Form):
    """Kredi kartı hareketi düzenleme: açıklama + (karşı taraf gider hesabıysa) gider hesabı + yatırım
    projesi (258'de zorunlu). Tutar/tarih/kur değişmez."""
    aciklama = forms.CharField(label="Açıklama", max_length=200, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, gider_duzenlenebilir=False, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        self.gider_duzenlenebilir = gider_duzenlenebilir
        if gider_duzenlenebilir:
            self.fields["gider"] = forms.ModelChoiceField(
                label="Gider Hesabı", required=True, empty_label="— gider hesabı seç —",
                queryset=yaprak_hesaplar(), widget=forms.Select(attrs={"class": "akilli-sec"}))
            self.fields["gider"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["yatirim_projesi"] = _yatirim_projesi_alani()

    def clean(self):
        cd = super().clean()
        if self.gider_duzenlenebilir:
            _yatirim_projesi_denetle(self, cd.get("gider"), cd.get("yatirim_projesi"))
        elif cd.get("yatirim_projesi"):
            self.add_error("yatirim_projesi", "Bu hareketin karşı tarafı gider hesabı değil; proje seçilemez.")
        return cd


class KrediKartiHareketForm(forms.Form):
    """Kredi kartı hareketi: karşı taraf (Harcama/İade → Cari VEYA Gider; Ödeme → Banka VEYA
    Kasa) + tutar + tarih + açıklama. Karşı alanlar tipe göre __init__'te eklenir; tam olarak
    biri seçilmeli (clean). Kart ve tip URL'den; fiş otomatik üretilir."""
    tutar = TRDecimalField(label="Tutar", basamak=2)
    tarih = forms.DateField(
        label="Tarih",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    # Kart döviz ise gösterilir (JS); karşı taraf Cari'yse onun kur_tipi tercihine göre
    # otomatik doldurulur. Doluysa otomatik hesaplama YERİNE doğrudan kullanılır.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off",
                                      "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, tip=None, kart=None, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        from core.services.kredi_karti_hareket import HAREKET
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
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
            self.fields["yatirim_projesi"] = _yatirim_projesi_alani()
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
        if "yatirim_projesi" in self.fields:
            _yatirim_projesi_denetle(self, cd.get("gider"), cd.get("yatirim_projesi"))
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
    # TRY'de anlamsız (JS ile gizlenir). Doluysa carinin kur_tipi tercihine göre otomatik
    # hesaplanan kur YERİNE bu kullanılır — bkz. core.services.teklif_siparis.
    # _irsaliye_stok_hareketi_yaz. Yalnız İRSALİYE'de gerçek bir etkisi var (FIFO maliyeti);
    # diğer belge türlerinde salt önizleme/dönüşüm zincirinde taşınan bir değer.
    kur = TRDecimalField(label="Kur", basamak=6, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=500, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, belge_tur=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
        from core.models import TeklifSiparis
        if belge_tur == TeklifSiparis.BelgeTur.SIPARIS:
            self.fields["gecerlilik_teslim_tarihi"].label = "Teslim Tarihi"
        else:
            self.fields["gecerlilik_teslim_tarihi"].label = "Geçerlilik Tarihi"
        # İrsaliye gerçek stok hareketi yazar — hangi depoya girdiği zorunlu. Geçerlilik/
        # teslim tarihi kavramı İrsaliye'de anlamsız (mal zaten teslim edilmiş sayılır) —
        # alan hiç yok. İrsaliye No, kendi (kağıt) numarası olduğu için Tarih'e yakın
        # gösterilsin diye alan sırası da düzeltiliyor (bkz. order_fields altta).
        if belge_tur == TeklifSiparis.BelgeTur.IRSALIYE:
            del self.fields["gecerlilik_teslim_tarihi"]
            self.fields["depo"] = forms.ModelChoiceField(
                label="Depo", queryset=Depo.objects.filter(silindi=False).order_by("kod"),
                empty_label="— depo seç —")
            self.fields["depo"].widget.attrs["class"] = "akilli-sec"
            # Tedarikçinin KENDİ (kağıt) irsaliye numarası — bizim otomatik belge_no'muzdan
            # ayrı, serbest metin, opsiyonel (Fatura.fatura_no ile aynı desen).
            self.fields["irsaliye_no"] = forms.CharField(
                label="İrsaliye No", max_length=50, required=False,
                widget=forms.TextInput(attrs={"autocomplete": "off"}))
            self.order_fields(["cari", "tarih", "irsaliye_no", "depo", "para_birimi", "kur",
                               "aciklama"])


class TeklifSiparisKalemForm(forms.Form):
    """Teklif/Sipariş kalemi: stok × miktar × birim fiyat (Fatura kalem formuyla aynı şekil,
    yalnız birim fiyat 4 ondalık basamak — Satınalma tarafının isteği)."""
    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.none(), required=False, empty_label="— stok seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)
    # Fatura biriminden (miktar/cevirici) hesaplanan değer yalnız TEORİK bir yaklaşıklık —
    # gerçek dünyada tolerans farkı olabilir (bkz. TeklifSiparisKalem.uretim_miktar model
    # alanı). Girildiyse İrsaliye stok girişinde bunun YERİNE kullanılır.
    uretim_miktar = TRDecimalField(label="Üretim Miktarı", basamak=3, required=False)

    def __init__(self, *args, yon=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stok"].queryset = (
            Stok.objects.filter(silindi=False).select_related("kategori", "kdv").order_by("kod"))
        if yon == "ALIS":
            # Satınalma tarafında yalnız stok adı gösterilir (kod yok) — tedarikçi ürün
            # adı seçim sonrası alan altında ayrıca gösterilir (bkz. ilgili şablonun
            # tedarikci-etiket JS'i, _stok_meta/_stok_kdv_tevkifat'taki yon parametresi).
            self.fields["stok"].label_from_instance = lambda o: o.ad
        else:
            self.fields["stok"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["stok"].widget.attrs["class"] = "akilli-sec"
        self.fields["uretim_miktar"].widget.attrs.update(
            {"class": "uretim-miktar-yardimci", "inputmode": "decimal",
             "placeholder": "0", "autocomplete": "off"})

    def clean(self):
        cd = super().clean()
        stok = cd.get("stok")
        miktar = cd.get("miktar")
        fiyat = cd.get("birim_fiyat")
        uretim_miktar = cd.get("uretim_miktar")
        if not stok and miktar is None and fiyat is None and uretim_miktar is None:
            return cd                              # boş satır — atlanır
        if not stok:
            raise forms.ValidationError("Stok seçin.")
        if miktar is None or miktar <= 0:
            raise forms.ValidationError("Miktar sıfırdan büyük olmalı.")
        if fiyat is None or fiyat < 0:
            raise forms.ValidationError("Birim fiyat girin.")
        if uretim_miktar is not None and uretim_miktar <= 0:
            raise forms.ValidationError("Üretim miktarı girildiyse sıfırdan büyük olmalı.")
        cd["dolu"] = True
        return cd

    def dolu_mu(self) -> bool:
        return bool(getattr(self, "cleaned_data", {}).get("dolu"))


def _gecerlilik_varsayilan():
    return timezone.localdate() + timedelta(days=15)


def _secenek_varsayilan(kategori):
    """İlgili kategoride ``varsayilan=True`` işaretli seçeneğin pk'sını döner — yeni Satış
    Teklifi açılırken ilgili alan önceden seçili gelsin diye (bkz. AYARLAR > Tanım
    Listeleri'ndeki "Varsayılan" işaretleme). İşaretli satır yoksa None döner, seçim boş
    kalır (zararsız — kural 4)."""
    def _al():
        s = TanimSecenegi.objects.filter(
            kategori=kategori, varsayilan=True, silindi=False).first()
        return s.pk if s else None
    return _al


class SatisBelgeBaslikForm(forms.Form):
    """Satış Teklifi VEYA Satış Proforması başlığı (ikisi de bu formu kullanır — alanlar
    birebir aynı): karşı taraf (Cari VEYA Aday Müşteri — CRM lead, karşılıklı dışlayıcı) +
    tarih + geçerlilik (varsayılan +15 gün) + PB + Tanım Listeleri'nden seçilen yükleme
    şekli / ödeme koşulu / yükleme tipi + navlun. Karşı taraf seçilince PB/varsayılan
    iskonto JS ile otomatik doldurulur — bkz. satis_teklif_ekle.html / satis_proforma_ekle.html."""

    KARSI_TARAF_CHOICES = [("cari", "Cari"), ("aday", "Aday Müşteri")]

    karsi_taraf_tip = forms.ChoiceField(
        label="Kime", choices=KARSI_TARAF_CHOICES, initial="cari", required=False,
        widget=forms.RadioSelect)
    cari = forms.ModelChoiceField(
        label="Cari", queryset=Cari.objects.none(), required=False, empty_label="— cari seç —")
    aday_musteri = forms.ModelChoiceField(
        label="Aday Müşteri", queryset=AdayMusteri.objects.none(), required=False,
        empty_label="— aday müşteri seç —")
    tarih = forms.DateField(
        label="Belge tarihi",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    gecerlilik_teslim_tarihi = forms.DateField(
        label="Geçerlilik Tarihi", required=False, initial=_gecerlilik_varsayilan,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    para_birimi = forms.ChoiceField(
        label="Para Birimi", choices=Cari.PARA_CHOICES, initial="TRY")
    yukleme_sekli = forms.ModelChoiceField(
        label="Yükleme Şekli", queryset=TanimSecenegi.objects.none(), required=False,
        empty_label="— seçiniz —",
        initial=_secenek_varsayilan(TanimSecenegi.Kategori.YUKLEME_SEKLI))
    odeme_kosulu = forms.ModelChoiceField(
        label="Ödeme Koşulu", queryset=TanimSecenegi.objects.none(), required=False,
        empty_label="— seçiniz —",
        initial=_secenek_varsayilan(TanimSecenegi.Kategori.ODEME_KOSULU))
    yukleme_tipi = forms.ModelChoiceField(
        label="Yükleme Tipi", queryset=TanimSecenegi.objects.none(), required=False,
        empty_label="— seçiniz —",
        initial=_secenek_varsayilan(TanimSecenegi.Kategori.YUKLEME_TIPI))
    teslim_suresi = forms.ModelChoiceField(
        label="Teslim Süresi", queryset=TanimSecenegi.objects.none(), required=False,
        empty_label="— seçiniz —",
        initial=_secenek_varsayilan(TanimSecenegi.Kategori.TESLIM_SURESI))
    navlun_tutari = TRDecimalField(
        label="Navlun / FOB Masrafları", basamak=2, required=False,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off",
                                      "placeholder": "0,00"}))
    # Yalnız Satış Teklifi ekle ekranında render edilir — işaretlenmezse teklif doğrudan
    # "Gönderildi" durumunda kaydedilir (bkz. core.services.teklif_siparis.teklif_siparis_
    # olustur, taslak_olarak_kaydet kwarg'ı).
    taslak_olarak_kaydet = forms.BooleanField(
        label="Taslak olarak kaydet", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cari"].queryset = Cari.objects.filter(silindi=False).order_by("unvan")
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        # Cariye zaten dönüşmüş adaylar artık normal Cari akışıyla teklif alır — listeden
        # düşer (bkz. AdayMusteri.cari, core.services.aday_donustur).
        self.fields["aday_musteri"].queryset = (
            AdayMusteri.objects.filter(silindi=False, cari__isnull=True)
            .select_related("ulke").order_by("unvan"))
        # Aynı unvanlı/benzer adaylar farklı ülkelerden olabilir — akıllı-seç'te ayırt
        # edilsin diye ülke adı öne eklenir (ör. "DUBAİ AL BAWADI METALS").
        self.fields["aday_musteri"].label_from_instance = (
            lambda o: f"{o.ulke.ad} {o.unvan}" if o.ulke_id else o.unvan)
        self.fields["aday_musteri"].widget.attrs["class"] = "akilli-sec"
        K = TanimSecenegi.Kategori
        for alan, kategori in (("yukleme_sekli", K.YUKLEME_SEKLI),
                               ("odeme_kosulu", K.ODEME_KOSULU),
                               ("yukleme_tipi", K.YUKLEME_TIPI),
                               ("teslim_suresi", K.TESLIM_SURESI)):
            self.fields[alan].queryset = (
                TanimSecenegi.objects.filter(silindi=False, kategori=kategori)
                .order_by("sira", "ad"))
            self.fields[alan].label_from_instance = lambda o: o.ad
            self.fields[alan].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        if cd.get("karsi_taraf_tip") == "aday":
            if not cd.get("aday_musteri"):
                self.add_error("aday_musteri", "Aday müşteri seçin.")
            cd["cari"] = None
        else:
            if not cd.get("cari"):
                self.add_error("cari", "Cari seçin.")
            cd["aday_musteri"] = None
        return cd


class SatisProformaBaslikForm(SatisBelgeBaslikForm):
    """Satış Proforması başlığı — SatisBelgeBaslikForm'un tüm alanlarına ek olarak banka
    hesabı seçimi (yalnız Proforma'da anlamlı; PDF ve detay sayfasında gösterilir). FİNANS >
    Banka altındaki AÇIK (silindi=False) gerçek hesaplardan seçilir (core.models.BankaHesap) —
    AYARLAR > Firma Bilgileri'ndeki statik FirmaBanka DEĞİL. Queryset TÜM açık hesapları taşır —
    para birimine göre filtreleme JS ile yapılır (bkz. satis_proforma_ekle.html); sunucu tarafı
    eşleşme kontrolü servis katmanında (_banka_coz)."""

    banka_hesabi = forms.ModelChoiceField(
        label="Banka Hesabı", queryset=BankaHesap.objects.none(), required=False,
        empty_label="— banka seçilmedi —")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banka_hesabi"].queryset = (
            BankaHesap.objects.filter(silindi=False, banka__silindi=False)
            .select_related("banka").order_by("banka__ad", "ad"))
        self.fields["banka_hesabi"].label_from_instance = (
            lambda o: f"{o.banka.kisa_ad or o.banka.ad} · {o.ad} ({o.para_birimi})")
        self.fields["banka_hesabi"].widget.attrs["class"] = "akilli-sec"


class TeklifRedForm(forms.Form):
    """Satış Teklifi reddedilirken isteğe bağlı kısa açıklama — bkz. core.views.teklif_red_
    gorunum / core.services.teklif_siparis.teklif_reddet."""

    red_nedeni = forms.CharField(
        label="Red Nedeni (isteğe bağlı)", max_length=300, required=False,
        widget=forms.Textarea(attrs={"rows": 3, "autocomplete": "off"}))


class SatisTeklifKalemForm(forms.Form):
    """Satış Teklifi kalemi: stok GİZLİ alan (sayfa açılırken tüm satış ürünleriyle
    önceden dolu gelir, bkz. views.py::satis_teklif_ekle) — kullanıcı yalnız "Dahil" /
    İskonto % / Birim Fiyat'ı düzenler. MİKTAR YOK — teklifte her zaman 1 birim
    fiyatı iletilir (view sabit ``miktar=1`` gönderir)."""

    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.filter(silindi=False, satis_urunu=True),
        required=False, widget=forms.HiddenInput())
    dahil = forms.BooleanField(label="Dahil", required=False, initial=True)
    iskonto_yuzdesi = TRDecimalField(label="İskonto %", basamak=2, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)

    def clean(self):
        cd = super().clean()
        if not cd.get("dahil"):
            return cd
        if not cd.get("stok"):
            raise forms.ValidationError("Stok bulunamadı.")
        fiyat = cd.get("birim_fiyat")
        if fiyat is None or fiyat < 0:
            self.add_error("birim_fiyat", "Birim fiyat girin.")
        iskonto = cd.get("iskonto_yuzdesi")
        if iskonto is None:
            cd["iskonto_yuzdesi"] = Decimal("0")
        elif iskonto < 0 or iskonto > 100:
            self.add_error("iskonto_yuzdesi", "İskonto 0 ile 100 arasında olmalı.")
        return cd

    def dahil_mi(self) -> bool:
        cd = getattr(self, "cleaned_data", {})
        return bool(cd.get("dahil")) and bool(cd.get("stok"))


class SatisProformaKalemForm(forms.Form):
    """Satış Proforması kalemi: SatisTeklifKalemForm ile aynı desen (stok GİZLİ, sayfa
    açılırken tüm satış ürünleriyle önceden dolu gelir) — TEK FARK: burada MİKTAR gerçek
    ve elle girilir (Teklif'in aksine "hep 1" değil — müşterinin istediği gerçek adet)."""

    stok = forms.ModelChoiceField(
        label="Stok", queryset=Stok.objects.filter(silindi=False, satis_urunu=True),
        required=False, widget=forms.HiddenInput())
    dahil = forms.BooleanField(label="Dahil", required=False, initial=False)
    miktar = TRDecimalField(label="Miktar", basamak=3, required=False)
    iskonto_yuzdesi = TRDecimalField(label="İskonto %", basamak=2, required=False)
    birim_fiyat = TRDecimalField(label="Birim Fiyat", basamak=4, required=False)

    def clean(self):
        cd = super().clean()
        if not cd.get("dahil"):
            return cd
        if not cd.get("stok"):
            raise forms.ValidationError("Stok bulunamadı.")
        miktar = cd.get("miktar")
        if miktar is None or miktar <= 0:
            self.add_error("miktar", "Miktar sıfırdan büyük olmalı.")
        fiyat = cd.get("birim_fiyat")
        if fiyat is None or fiyat < 0:
            self.add_error("birim_fiyat", "Birim fiyat girin.")
        iskonto = cd.get("iskonto_yuzdesi")
        if iskonto is None:
            cd["iskonto_yuzdesi"] = Decimal("0")
        elif iskonto < 0 or iskonto > 100:
            self.add_error("iskonto_yuzdesi", "İskonto 0 ile 100 arasında olmalı.")
        return cd

    def dahil_mi(self) -> bool:
        cd = getattr(self, "cleaned_data", {})
        return bool(cd.get("dahil")) and bool(cd.get("stok"))


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


class DepoTransferForm(forms.Form):
    """Aynı stoğun bir depodan diğerine transferi — bkz. core.services.depo_transfer."""

    kaynak_depo = forms.ModelChoiceField(
        label="Kaynak Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    hedef_depo = forms.ModelChoiceField(
        label="Hedef Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3)
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(label="Açıklama", max_length=250, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.depo import aktif_depolar
        for ad in ("kaynak_depo", "hedef_depo"):
            self.fields[ad].queryset = aktif_depolar()
            self.fields[ad].label_from_instance = lambda o: f"{o.kod}  {o.ad}"

    def clean(self):
        cd = super().clean()
        if cd.get("kaynak_depo") and cd.get("kaynak_depo") == cd.get("hedef_depo"):
            raise forms.ValidationError("Kaynak ve hedef depo aynı olamaz.")
        return cd


class SarfCikisForm(forms.Form):
    """Stoktan hesaba/yatırım projesine SARF çıkışı — bkz. core.services.hareket.
    sarf_cikis_ekle. 258 karşı hesabında yatırım projesi zorunlu, başka hesapta gizli/
    boş kalmalı (clean() zorlar)."""

    depo = forms.ModelChoiceField(
        label="Depo", queryset=Depo.objects.none(), empty_label="— depo seç —")
    miktar = TRDecimalField(label="Miktar", basamak=3)
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    karsi_hesap = forms.ModelChoiceField(
        label="Karşı hesap", queryset=HesapPlani.objects.none(), empty_label="— hesap seç —")
    yatirim_projesi = forms.ModelChoiceField(
        label="Yatırım projesi", queryset=YatirimProjesi.objects.none(), required=False,
        empty_label="— proje seç —")
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.depo import aktif_depolar
        from core.services.hesap_plani import sarf_karsi_hesaplari
        from core.services.yatirim_projesi import aktif_projeler
        self.fields["depo"].queryset = aktif_depolar()
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["karsi_hesap"].queryset = sarf_karsi_hesaplari()
        self.fields["karsi_hesap"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["karsi_hesap"].widget.attrs["class"] = "akilli-sec"
        self.fields["yatirim_projesi"].queryset = aktif_projeler().filter(
            durum=YatirimProjesi.Durum.DEVAM)
        self.fields["yatirim_projesi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["yatirim_projesi"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        karsi = cd.get("karsi_hesap")
        proje = cd.get("yatirim_projesi")
        if karsi is not None:
            from core.services.hesap_plani import hesap_kodu_258_mi
            is_258 = hesap_kodu_258_mi(karsi.hesap_kodu)
            if is_258 and not proje:
                self.add_error("yatirim_projesi",
                               "258 karşı hesabı için yatırım projesi seçilmelidir.")
            if not is_258 and proje:
                self.add_error("yatirim_projesi",
                               "Yatırım projesi yalnız 258 karşı hesabı seçilince kullanılabilir.")
        return cd


class YemekTakibiFiltreForm(forms.Form):
    """Yemek Takibi liste ekranı üst filtresi: cari (opsiyonel) + tarih aralığı."""

    cari = forms.ModelChoiceField(label="Cari", queryset=Cari.objects.none(),
                                  required=False, empty_label="— tüm cariler —")
    baslangic = forms.DateField(
        label="Başlangıç", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    bitis = forms.DateField(
        label="Bitiş", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari import aktif_cariler
        self.fields["cari"].queryset = aktif_cariler()
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        b, s = cd.get("baslangic"), cd.get("bitis")
        if b and s and b > s:
            raise forms.ValidationError("Başlangıç, bitişten sonra olamaz.")
        return cd


class YemekSayimForm(forms.Form):
    """Yemek Takibi günlük kayıt ekle/düzenle. TR büyük harf gerekmiyor (sayısal kayıt)."""

    cari = forms.ModelChoiceField(label="Cari (Yemek Firması)", queryset=Cari.objects.none(),
                                  empty_label="— cari seç —")
    tarih = forms.DateField(
        label="Tarih", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    kisi_sayisi = forms.IntegerField(
        label="Kişi Sayısı", min_value=0,
        widget=forms.NumberInput(attrs={"autocomplete": "off", "inputmode": "numeric"}))
    birim_fiyat = TRDecimalField(label="Kişi Başı Ücret", basamak=2)
    notlar = forms.CharField(label="Notlar", max_length=200, required=False,
                             widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari import aktif_cariler
        self.fields["cari"].queryset = aktif_cariler()
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"


class PersonelForm(forms.Form):
    """İNSAN KAYNAKLARI > Personel Kartı ekle/düzenle. Büyük harf, telefon kanonikleştirme ve
    TC benzersizliği serviste (core.services.personel) zorlanır; burada yalnız kibar hata için
    TC algoritması + alan uzunlukları var."""

    ad = forms.CharField(label="Ad", max_length=100,
                         widget=forms.TextInput(attrs={"autocomplete": "off"}))
    soyad = forms.CharField(label="Soyad", max_length=100,
                            widget=forms.TextInput(attrs={"autocomplete": "off"}))
    tc_kimlik_no = forms.CharField(
        label="TC Kimlik No", max_length=11, required=False, validators=[tc_dogrula],
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "numeric"}))
    dogum_tarihi = forms.DateField(
        label="Doğum Tarihi", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    kan_grubu = forms.ChoiceField(
        label="Kan Grubu", required=False,
        choices=[("", "— seçin —")] + list(Personel.KAN_GRUPLARI))
    telefon = forms.CharField(
        label="Telefon", max_length=20, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "tel"}))
    eposta = forms.EmailField(
        label="E-posta", required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    adres = forms.CharField(label="Adres", required=False,
                            widget=forms.Textarea(attrs={"rows": 3}))
    acil_durum_kisi = forms.CharField(
        label="Acil Durumda Aranacak Kişi", max_length=120, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    acil_durum_telefon = forms.CharField(
        label="Acil Durum Telefonu", max_length=20, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "inputmode": "tel"}))
    departman = forms.CharField(
        label="Departman", max_length=80, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "list": "dl-departman"}))
    gorev = forms.CharField(
        label="Görev", max_length=80, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "list": "dl-gorev"}))
    ise_giris_tarihi = forms.DateField(
        label="İşe Giriş Tarihi", initial=tr_bugun,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    isten_cikis_tarihi = forms.DateField(
        label="İşten Çıkış Tarihi", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    cikis_nedeni = forms.CharField(
        label="Çıkış Nedeni", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    notlar = forms.CharField(label="Notlar", required=False,
                             widget=forms.Textarea(attrs={"rows": 3}))
    # Yalnız İzinler ekranına yetkisi olan kullanıcıya gösterilir (izin_alani=True).
    izin_onceki_kullanilan = TRDecimalField(
        label="Sisteme geçmeden önce kullanılan yıllık izin (gün)", basamak=1, required=False)

    def __init__(self, *args, izin_alani=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not izin_alani:
            del self.fields["izin_onceki_kullanilan"]


class PersonelIzinForm(forms.Form):
    """İNSAN KAYNAKLARI > İzin ekle/düzenle. Gün boş bırakılırsa sunucu Pazar günleri VE
    resmî tatiller hariç takvim gününü hesaplar. Düzenlemede personel değiştirilemez."""

    personel = forms.ModelChoiceField(
        label="Personel", queryset=Personel.objects.none(), empty_label="— personel seç —")
    tur = forms.ChoiceField(label="İzin Türü", choices=PersonelIzin.Tur.choices,
                            initial=PersonelIzin.Tur.YILLIK)
    baslangic = forms.DateField(
        label="Başlangıç", initial=tr_bugun,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    bitis = forms.DateField(
        label="Bitiş", initial=tr_bugun,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    gun = TRDecimalField(label="Gün Sayısı", basamak=1, required=False)
    aciklama = forms.CharField(label="Açıklama", max_length=300, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))

    def __init__(self, *args, personel_sabit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if personel_sabit:
            del self.fields["personel"]
        else:
            from core.services.personel import aktif_personeller
            bugun = tr_bugun()
            self.fields["personel"].queryset = aktif_personeller()
            self.fields["personel"].label_from_instance = lambda p: (
                p.ad_soyad + (" (ayrıldı)" if p.isten_cikis_tarihi
                              and p.isten_cikis_tarihi < bugun else ""))
            self.fields["personel"].widget.attrs["class"] = "akilli-sec"


class PersonelBelgeForm(forms.Form):
    """İNSAN KAYNAKLARI > Özlük Belgesi ekle/düzenle. Dosya yalnız EKLEMEDE seçilir (belge
    düzenlemede dosya değişmez; yenileme = aynı türden yeni kayıt). Dosya içerik doğrulaması
    serviste (core.services.personel_belge)."""

    personel = forms.ModelChoiceField(
        label="Personel", queryset=Personel.objects.none(), empty_label="— personel seç —")
    tur = forms.ChoiceField(label="Belge Türü", choices=PersonelBelge.Tur.choices)
    aciklama = forms.CharField(label="Açıklama", max_length=200, required=False,
                               widget=forms.TextInput(attrs={"autocomplete": "off"}))
    bitis_tarihi = forms.DateField(
        label="Geçerlilik Bitiş Tarihi", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    dosya = forms.FileField(
        label="Dosya (PDF veya resim, en fazla 10 MB)",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,application/pdf"}))

    def __init__(self, *args, personel_sabit=False, dosya_alani=True, **kwargs):
        super().__init__(*args, **kwargs)
        if not dosya_alani:
            del self.fields["dosya"]
        if personel_sabit:
            del self.fields["personel"]
        else:
            from core.services.personel import aktif_personeller
            bugun = tr_bugun()
            self.fields["personel"].queryset = aktif_personeller()
            self.fields["personel"].label_from_instance = lambda p: (
                p.ad_soyad + (" (ayrıldı)" if p.isten_cikis_tarihi
                              and p.isten_cikis_tarihi < bugun else ""))
            self.fields["personel"].widget.attrs["class"] = "akilli-sec"


class PersonelFotoForm(forms.Form):
    dosya = forms.FileField(
        label="Fotoğraf", widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))


class PersonelUcretForm(forms.Form):
    """İNSAN KAYNAKLARI > Personel Ücreti ekle/düzenle. tip=Asgari Ücret iken tutar alanı JS ile
    gizlenir; tip=Net Ücret iken zorunlu (serviste de zorlanır). Düzenlemede personel değişmez."""

    personel = forms.ModelChoiceField(
        label="Personel", queryset=Personel.objects.none(), empty_label="— personel seç —")
    gecerlilik_baslangic = forms.DateField(
        label="Geçerlilik Başlangıcı", initial=tr_bugun,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    tip = forms.ChoiceField(label="Tip", choices=PersonelUcret.Tip.choices,
                            initial=PersonelUcret.Tip.ASGARI, widget=forms.RadioSelect)
    net_tutar = TRDecimalField(label="Net Tutar (TL)", basamak=2, required=False)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "ör. 2027 zammı"}))

    def __init__(self, *args, personel_sabit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if personel_sabit:
            del self.fields["personel"]
        else:
            from core.services.personel import aktif_personeller
            bugun = tr_bugun()
            self.fields["personel"].queryset = aktif_personeller()
            self.fields["personel"].label_from_instance = lambda p: (
                p.ad_soyad + (" (ayrıldı)" if p.isten_cikis_tarihi
                              and p.isten_cikis_tarihi < bugun else ""))
            self.fields["personel"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        if cd.get("tip") == PersonelUcret.Tip.NET and cd.get("net_tutar") is None:
            self.add_error("net_tutar", "Net ücret tipinde tutar zorunlu.")
        return cd


class ResmiTatilForm(forms.Form):
    """İNSAN KAYNAKLARI > Resmî Tatil ekle/düzenle. Ad TR büyük harf + tarih benzersizliği
    serviste (core.services.resmi_tatil) zorlanır."""

    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    ad = forms.CharField(
        label="Ad", max_length=100,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "ör. Cumhuriyet Bayramı"}))


class GirisForm(AuthenticationForm):
    """Standart giriş formu + "Beni hatırla" (bkz. core.views.GirisView). İşaretlenmezse
    davranış hiç değişmez — yalnız işaretlenince oturum 30 gün sürer."""

    beni_hatirla = forms.BooleanField(label="Beni hatırla (30 gün)", required=False)


_TR = ZoneInfo("Europe/Istanbul")


class TRDateTimeField(forms.DateTimeField):
    """Girilen (naive görünümlü) saat HER ZAMAN TR yerel saati olarak yorumlanır. Django'nun
    kendi to_python'ı USE_TZ=True iken zaten (yanlış biçimde) UTC tzinfo iliştirdiği için
    is_naive() burada hep False döner — bu yüzden koşulsuz replace(tzinfo=_TR) yapılır."""

    widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")

    def to_python(self, value):
        dt = super().to_python(value)
        if dt is not None:
            dt = dt.replace(tzinfo=_TR)
        return dt


class MesaiHesapOlusturForm(forms.Form):
    """İNSAN KAYNAKLARI > Personel kartından mesai hesabı oluştur. Şifre gücü serviste
    (core.services.mesai_hesap) doğrulanır."""

    kullanici_adi = forms.CharField(
        label="Kullanıcı Adı", max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))
    sifre = forms.CharField(
        label="Şifre", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))


class MesaiSifreForm(forms.Form):
    """İNSAN KAYNAKLARI > Mesai hesabının şifresini sıfırla."""

    sifre = forms.CharField(
        label="Yeni Şifre", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))


class MesaiIzinliAgForm(forms.Form):
    """AYARLAR > Mesai Ayarları — izinli IP/CIDR ekle. Geçerlilik/benzersizlik serviste
    (core.services.mesai_ag) doğrulanır."""

    cidr = forms.CharField(
        label="IP / CIDR", max_length=43, widget=forms.TextInput(attrs={"autocomplete": "off"}))
    aciklama = forms.CharField(
        label="Açıklama", max_length=100, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off"}))


class MesaiDuzeltForm(forms.Form):
    """İNSAN KAYNAKLARI > Mesai kaydı ekle/düzenle (yönetici). Zamanlar TR yerel saatiyle
    girilir (bkz. TRDateTimeField). Düzeltme notu zorunlu — kaynak "Yönetici" işaretlenir."""

    personel = forms.ModelChoiceField(
        label="Personel", queryset=Personel.objects.none(), empty_label="— personel seç —")
    giris_zamani = TRDateTimeField(label="Giriş Zamanı")
    cikis_zamani = TRDateTimeField(label="Çıkış Zamanı", required=False)
    duzeltme_notu = forms.CharField(
        label="Düzeltme Notu", max_length=300,
        widget=forms.Textarea(attrs={"rows": 2, "autocomplete": "off"}))

    def __init__(self, *args, personel_sabit=False, **kwargs):
        super().__init__(*args, **kwargs)
        if personel_sabit:
            del self.fields["personel"]
        else:
            from core.services.personel import aktif_personeller
            self.fields["personel"].queryset = aktif_personeller()
            self.fields["personel"].widget.attrs["class"] = "akilli-sec"

    def clean(self):
        cd = super().clean()
        giris, cikis = cd.get("giris_zamani"), cd.get("cikis_zamani")
        if giris and cikis and cikis < giris:
            self.add_error("cikis_zamani", "Çıkış zamanı girişten önce olamaz.")
        return cd


class DovizIslemForm(forms.Form):
    """Döviz Alış / Satış: TL banka/kasa ↔ döviz banka/kasa (bkz. core.services.doviz_islem).
    Kaynak/hedef "banka:<id>" / "kasa:<id>" değeriyle seçilir; yön (alış/satış) para birimlerinden çıkar."""
    kaynak = forms.ChoiceField(label="Kaynak hesap")
    hedef = forms.ChoiceField(label="Hedef hesap")
    doviz_tutari = TRDecimalField(label="Döviz tutarı", basamak=2)
    kur = TRDecimalField(label="Kur (TL)", basamak=6, required=False)
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Boş bırakılırsa otomatik"}))
    masraf_hesap_1 = forms.ModelChoiceField(
        label="Kambiyo vergisi / masraf hesabı", queryset=HesapPlani.objects.none(),
        to_field_name="hesap_kodu", empty_label="— masraf yok —", required=False)
    masraf_tutar_1 = TRDecimalField(label="Tutar (TL)", basamak=2, required=False)
    masraf_hesap_2 = forms.ModelChoiceField(
        label="2. masraf hesabı", queryset=HesapPlani.objects.none(),
        to_field_name="hesap_kodu", empty_label="— masraf yok —", required=False)
    masraf_tutar_2 = TRDecimalField(label="Tutar (TL)", basamak=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.hesap_plani import yaprak_hesaplar
        secenekler = [("", "— hesap seç —")]
        self.pb = {}
        for h in BankaHesap.objects.filter(silindi=False).select_related("banka").order_by("banka__ad", "ad"):
            secenekler.append((f"banka:{h.pk}", f"Banka · {h.banka.ad} - {h.ad} ({h.para_birimi})"))
            self.pb[f"banka:{h.pk}"] = h.para_birimi
        for k in Kasa.objects.filter(silindi=False).order_by("ad"):
            secenekler.append((f"kasa:{k.pk}", f"Kasa · {k.ad} ({k.para_birimi})"))
            self.pb[f"kasa:{k.pk}"] = k.para_birimi
        for ad in ("kaynak", "hedef"):
            self.fields[ad].choices = secenekler
        for ad in ("masraf_hesap_1", "masraf_hesap_2"):
            self.fields[ad].queryset = yaprak_hesaplar()
            self.fields[ad].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
            self.fields[ad].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})

    def clean(self):
        cd = super().clean()
        if cd.get("kaynak") and cd.get("kaynak") == cd.get("hedef"):
            self.add_error("hedef", "Kaynak ve hedef aynı hesap olamaz.")
        for i in ("1", "2"):
            if cd.get(f"masraf_tutar_{i}") and not cd.get(f"masraf_hesap_{i}"):
                self.add_error(f"masraf_hesap_{i}", "Masraf tutarı için hesap seçilmelidir.")
        return cd


class CariKesintiForm(forms.Form):
    """Cari kartından Kesinti / Masraf hareketi: tarih, tutar, gider hesabı (varsayılan 770.03), açıklama."""
    tarih = forms.DateField(
        label="Tarih", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        initial=timezone.localdate)
    tutar = TRDecimalField(label="Tutar (TL)", basamak=2)
    gider = forms.ModelChoiceField(
        label="Hesap (gider · 258 yatırım · gelir 64x/67x)", queryset=HesapPlani.objects.none(),
        to_field_name="hesap_kodu", empty_label=None)
    yatirim_projesi = forms.ModelChoiceField(
        label="Yatırım projesi", queryset=YatirimProjesi.objects.none(), required=False,
        empty_label="— proje (yalnız 258 için) —")
    karsi_cari = forms.ModelChoiceField(
        label="Karşı cari — ortak ödediyse (opsiyonel)", queryset=Cari.objects.none(), required=False,
        empty_label="— yok (hesap seçilir) —")
    aciklama = forms.CharField(
        label="Açıklama", max_length=200, required=False,
        widget=forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Boş bırakılırsa otomatik"}))

    def __init__(self, *args, ortak_secenegi=True, **kwargs):
        super().__init__(*args, **kwargs)
        from core.services.cari_kesinti import VARSAYILAN_GIDER, kesinti_hesap_kumesi, ortak_cariler
        if ortak_secenegi:
            self.fields["karsi_cari"].queryset = ortak_cariler()
            self.fields["karsi_cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
            self.fields["karsi_cari"].widget.attrs["class"] = "akilli-sec"
        else:
            del self.fields["karsi_cari"]
        from core.services.yatirim_projesi import aktif_projeler
        self.fields["gider"].queryset = kesinti_hesap_kumesi().order_by("hesap_kodu")
        self.fields["gider"].label_from_instance = lambda o: f"{o.hesap_kodu}  {o.hesap_adi}"
        self.fields["gider"].widget.attrs["class"] = "akilli-sec"
        self.fields["yatirim_projesi"].queryset = aktif_projeler().filter(durum=YatirimProjesi.Durum.DEVAM)
        self.fields["yatirim_projesi"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        self.fields["yatirim_projesi"].widget.attrs["class"] = "akilli-sec"
        if not self.is_bound and "gider" not in (self.initial or {}):
            self.initial["gider"] = VARSAYILAN_GIDER

    def clean(self):
        from core.services.hesap_plani import hesap_kodu_258_mi
        cd = super().clean()
        gider, proje = cd.get("gider"), cd.get("yatirim_projesi")
        if cd.get("karsi_cari") is not None:          # ortak ödedi: gider/proje kullanılmaz
            cd["gider"], cd["yatirim_projesi"] = None, None
            self.errors.pop("gider", None)
            self.errors.pop("yatirim_projesi", None)
            return cd
        if gider is not None:
            if hesap_kodu_258_mi(gider.hesap_kodu) and not proje:
                self.add_error("yatirim_projesi", "258 hesabı için yatırım projesi seçilmelidir.")
            elif not hesap_kodu_258_mi(gider.hesap_kodu) and proje:
                self.add_error("yatirim_projesi", "Yatırım projesi yalnız 258 hesabında seçilebilir.")
        return cd
