"""DONDURULMUŞ KOPYA — fatura formu yeniden tasarımından ÖNCEKİ (commit b2e6d01) ekle/düzenle POST akışı ve FaturaForm. YALNIZ test karşılaştırması içindir
(core/tests/test_fatura_form_fis_karsilastirma.py): aynı girdiyle eski akışın ürettiği fiş ile yeni akışın ürettiği fişin birebir aynı olduğunu kanıtlar. DEĞİŞTİRMEYİN."""
from decimal import Decimal

from django import forms
from django.utils import timezone

from core.forms import FaturaSatirForm, TRDecimalField, _secilebilir_cariler
from core.models import Cari, Depo, Fatura, FaturaTipi, HesapPlani
from core.services import fatura as fatura_servis
from core.views import FaturaSatirDuzenleFormSet, FaturaSatirFormSet, _fatura_satir_girdileri


class EskiFaturaForm(forms.Form):
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
        self.fields["cari"].queryset = _secilebilir_cariler(self)
        self.fields["cari"].label_from_instance = lambda o: f"{o.kod}  {o.unvan}"
        self.fields["cari"].widget.attrs["class"] = "akilli-sec"
        self.fields["kur"].widget.attrs.update({"class": "kur-girdi", "autocomplete": "off"})
        depolar = Depo.objects.filter(silindi=False, aktif=True).order_by("kod")
        self.fields["depo"].queryset = depolar
        self.fields["depo"].label_from_instance = lambda o: f"{o.kod}  {o.ad}"
        # Yeni faturada ANA DEPO ön-seçili; düzenlemede faturanın deposu (initial) korunur.
        if not self.is_bound and "depo" not in self.initial:
            from core.services.depo import varsayilan_depo
            vd = varsayilan_depo(depolar)
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


def eski_ekle_post(request, yon):
    """b2e6d01 ``_fatura_ekle`` POST dalı (birebir): (fatura | None, form, formset)."""
    fform = EskiFaturaForm(request.POST, yon=yon)
    formset = FaturaSatirFormSet(request.POST, form_kwargs={"yon": yon})
    fatura = None
    if fform.is_valid() and formset.is_valid():
        satirlar = _fatura_satir_girdileri(formset)
        try:
            fatura = fatura_servis.fatura_olustur(
                tip_id=fform.cleaned_data["tip"].pk,
                cari_id=fform.cleaned_data["cari"].pk,
                tarih=fform.cleaned_data["tarih"],
                fatura_no=fform.cleaned_data.get("fatura_no", ""),
                para_birimi=fform.cleaned_data.get("para_birimi", "TRY"),
                kur=fform.cleaned_data.get("kur"),
                depo_id=(fform.cleaned_data["depo"].pk
                         if fform.cleaned_data.get("depo") else None),
                aciklama=fform.cleaned_data.get("aciklama", ""),
                vade_tarihi=fform.cleaned_data.get("vade_tarihi"),
                sahsi_alis=fform.cleaned_data.get("sahsi_alis", False),
                gv_stopaj_orani=fform.cleaned_data.get("gv_stopaj_orani"),
                sahsi_ortak_id=(fform.cleaned_data["sahsi_ortak"].pk
                               if fform.cleaned_data.get("sahsi_ortak") else None),
                satirlar=satirlar,
                kullanici=request.user,
            )
        except fatura_servis.FaturaHatasi as e:
            fform.add_error(None, str(e))
    return fatura, fform, formset


def eski_duzenle_post(request, fatura):
    """b2e6d01 ``fatura_duzenle`` POST dalı (birebir): (fatura güncellendi mi, form, formset)."""
    yon = fatura.yon
    fform = EskiFaturaForm(request.POST, yon=yon)
    formset = FaturaSatirDuzenleFormSet(request.POST, form_kwargs={"yon": yon, "fatura_pk": fatura.pk})
    tamam = False
    if fform.is_valid() and formset.is_valid():
        satirlar = _fatura_satir_girdileri(formset)
        try:
            fatura_servis.fatura_guncelle(
                fatura,
                tip_id=fform.cleaned_data["tip"].pk,
                cari_id=fform.cleaned_data["cari"].pk,
                tarih=fform.cleaned_data["tarih"],
                fatura_no=fform.cleaned_data.get("fatura_no", ""),
                para_birimi=fform.cleaned_data.get("para_birimi", "TRY"),
                kur=fform.cleaned_data.get("kur"),
                depo_id=fform.cleaned_data["depo"].pk if fform.cleaned_data.get("depo") else None,
                aciklama=fform.cleaned_data.get("aciklama", ""),
                vade_tarihi=fform.cleaned_data.get("vade_tarihi"),
                sahsi_alis=fform.cleaned_data.get("sahsi_alis", False),
                gv_stopaj_orani=fform.cleaned_data.get("gv_stopaj_orani"),
                sahsi_ortak_id=(fform.cleaned_data["sahsi_ortak"].pk
                               if fform.cleaned_data.get("sahsi_ortak") else None),
                satirlar=satirlar,
                kullanici=request.user,
            )
            tamam = True
        except fatura_servis.FaturaHatasi as e:
            fform.add_error(None, str(e))
    return tamam, fform, formset
