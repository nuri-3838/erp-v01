"""Fiş giriş formları.

Sayı alanları İSTİSNASIZ tek parser/formatter'dan geçer (core.sayi):
giriş "10,35" -> Decimal(10.35); gösterim Decimal -> "10,35" / "1.035,00".
Açıklama gibi metinler servis katmanında buyuk_harf_tr'den geçer.
"""
from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils import timezone

from core.models import HesapPlani, YevmiyeSatir
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
        # Decimal (initial) -> TR formatla; ham metin (hatadan sonra) -> olduğu gibi
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
    """Klasik yevmiye satırı: tutar BORÇ veya ALACAK sütununa yazılır; yazılan
    sütun tarafı belirler. Tutar = işlem tutarı (işlem PB cinsinden); TL serviste
    islem_tutari × islem_kuru'dan türetilir.
    """

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

        # Tamamen boş satır: atlanır (view temiz_mi ile eler).
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

        # Tarafı yazılan sütun belirler; tutar = işlem tutarı.
        if borc:
            cd["taraf"], cd["islem_tutari"] = "B", borc
        else:
            cd["taraf"], cd["islem_tutari"] = "A", alacak
        return cd

    def temiz_mi(self) -> bool:
        """Satırda geçerli/dolu veri var mı (boş satırları elemek için)."""
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
