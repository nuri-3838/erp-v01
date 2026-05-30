"""Fiş giriş/görüntüleme ve rapor görünümleri."""
from decimal import Decimal

from django.contrib import messages
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render

from core.forms import FisForm, MizanFiltreForm, SatirForm
from core.models import YevmiyeFisi
from core.services.raporlar import (
    bilanco, bilanco_usd, gelir_tablosu, gelir_tablosu_usd,
    mali_yil_araligi, mizan, mizan_usd,
)
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

SatirFormSet = formset_factory(SatirForm, extra=0, min_num=2, validate_min=True)


def fis_ekle(request):
    if request.method == "POST":
        fform = FisForm(request.POST)
        formset = SatirFormSet(request.POST)
        if fform.is_valid() and formset.is_valid():
            satirlar = []
            for f in formset:
                if not f.temiz_mi():
                    continue
                cd = f.cleaned_data
                satirlar.append(
                    SatirGirdi(
                        hesap_kodu=cd["hesap"].hesap_kodu,
                        taraf=cd["taraf"],
                        islem_tutari=cd["islem_tutari"],
                        islem_pb=cd["islem_pb"],
                        islem_kuru=cd.get("islem_kuru") or Decimal("1"),
                        aciklama=cd.get("aciklama", ""),
                    )
                )
            try:
                fis = fis_olustur(
                    tarih=fform.cleaned_data["tarih"],
                    aciklama=fform.cleaned_data.get("aciklama", ""),
                    kur_usd=fform.cleaned_data.get("kur_usd"),
                    satirlar=satirlar,
                )
                messages.success(request, f"Fiş kaydedildi: {fis.yil}/{fis.fis_no}")
                return redirect("core:fis_detay", pk=fis.pk)
            except YevmiyeHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FisForm()
        formset = SatirFormSet()
    return render(request, "core/fis_ekle.html", {"fform": fform, "formset": formset})


def fis_detay(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    satirlar = fis.satirlar.select_related("hesap").all()
    toplam_borc = sum((s.borc for s in satirlar), Decimal("0.00"))
    toplam_alacak = sum((s.alacak for s in satirlar), Decimal("0.00"))
    return render(
        request, "core/fis_detay.html",
        {"fis": fis, "satirlar": satirlar,
         "toplam_borc": toplam_borc, "toplam_alacak": toplam_alacak},
    )


def _tarih_araligi(request):
    """GET'ten tarih aralığı; yoksa varsayılan mali yıl (form önceden doldurulur)."""
    form = MizanFiltreForm(request.GET or None)
    if form.is_valid():
        return form, form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    baslangic, bitis = mali_yil_araligi()
    if not request.GET:
        form = MizanFiltreForm(initial={"baslangic": baslangic, "bitis": bitis})
    return form, baslangic, bitis


def mizan_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/mizan.html", {"form": form, "mizan": mizan(b, s)})


def bilanco_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/bilanco.html", {"form": form, "bilanco": bilanco(b, s)})


def gelir_tablosu_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/gelir_tablosu.html",
                  {"form": form, "gt": gelir_tablosu(b, s)})


def mizan_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/mizan_usd.html",
                  {"form": form, "mizan": mizan_usd(b, s)})


def gelir_tablosu_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/gelir_tablosu_usd.html",
                  {"form": form, "gt": gelir_tablosu_usd(b, s)})


def bilanco_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/bilanco_usd.html",
                  {"form": form, "bilanco": bilanco_usd(b, s)})
