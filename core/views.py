"""Fiş giriş/liste/düzenleme/görüntüleme, rapor, kullanıcı yönetimi ve ekran yetkisi görünümleri."""
import datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.forms import formset_factory
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import (
    FisForm, KullaniciDuzenleForm, KullaniciEkleForm, MizanFiltreForm, SatirForm,
)
from core.models import EkranYetki, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.moduller import MODULLER
from core.metin import buyuk_harf_tr
from core.sayi import SayiHatasi, parse_tr
from core.services.raporlar import (
    bilanco, bilanco_usd, ekstre as ekstre_servis, gelir_tablosu, gelir_tablosu_usd,
    mali_yil_araligi, mizan, mizan_usd,
)
from core.services.yevmiye import (
    SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_iptal, fis_olustur, kur_usd_birebir,
)
from core.services.tcmb import TcmbHatasi, kurlari_guncelle
from core.services import hesap_plani as hp
from core.services import yedek as yedek_servis
from core.yetki import (
    ekran_gerekli, ekran_gerekli_herhangi, ekran_gorebilir, yonetici_gerekli,
    yonetici_mi,
)

SatirFormSet = formset_factory(SatirForm, extra=0, min_num=2, validate_min=True)


@login_required
def pano(request):
    """Giriş sonrası açılan PANO (dashboard). Şimdilik karşılama;
    ileride özet/grafik eklenecek (yol haritası)."""
    return render(request, "core/pano.html")


def _satir_girdileri(formset):
    """Geçerli formset'ten dolu satırları SatirGirdi listesine çevirir."""
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
    return satirlar


@ekran_gerekli_herhangi("fis_listesi", "fis_ekle")
def fis_ekle(request):
    if request.method == "POST":
        fform = FisForm(request.POST)
        formset = SatirFormSet(request.POST)
        if fform.is_valid() and formset.is_valid():
            try:
                fis = fis_olustur(
                    tarih=fform.cleaned_data["tarih"],
                    aciklama=fform.cleaned_data.get("aciklama", ""),
                    satirlar=_satir_girdileri(formset),
                    kullanici=request.user,
                )
                messages.success(request, f"Fiş kaydedildi: {fis.yil}/{fis.fis_no}")
                return redirect("core:fis_detay", pk=fis.pk)
            except YevmiyeHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FisForm()
        formset = SatirFormSet()
    return render(request, "core/fis_ekle.html", {"fform": fform, "formset": formset})


@ekran_gerekli("fis_listesi")
def fis_listesi(request):
    form, b, s = _tarih_araligi(request)
    # Yalnız "ara" gelince (tarih GET'te yok) tarih formu varsayılanı göstersin
    if not (request.GET.get("baslangic") and request.GET.get("bitis")):
        form = MizanFiltreForm(initial={"baslangic": b, "bitis": s})

    usd = request.GET.get("gorunum") == "usd"
    ara = (request.GET.get("ara") or "").strip()
    taban = YevmiyeFisi.objects.filter(tarih__gte=b, tarih__lte=s)
    if ara:
        kosul = (
            Q(aciklama__contains=buyuk_harf_tr(ara))
            | Q(satirlar__hesap__hesap_kodu__contains=ara)
            | Q(satirlar__hesap__hesap_adi__contains=buyuk_harf_tr(ara))
        )
        if ara.isdigit():
            kosul |= Q(fis_no=int(ara))
        try:
            tutar = parse_tr(ara)
        except SayiHatasi:
            pass
        else:
            kosul |= Q(satirlar__borc=tutar) | Q(satirlar__alacak=tutar)
        # Aramayı ALT SORGU ile uygula: toplam annotate'i join çakışmasından korunur
        eslesen = taban.filter(kosul).values("pk").distinct()
        taban = YevmiyeFisi.objects.filter(pk__in=eslesen)

    fisler = (
        taban.annotate(t_borc=Sum("satirlar__borc"), t_alacak=Sum("satirlar__alacak"))
        .order_by("yil", "fis_no")
    )
    sayfa = Paginator(fisler, 50).get_page(request.GET.get("sayfa"))
    if usd:
        for f in sayfa:
            if f.kur_usd and f.t_borc is not None:
                f.usd_borc = f.t_borc / f.kur_usd
                f.usd_alacak = f.t_alacak / f.kur_usd
            else:
                f.usd_borc = f.usd_alacak = None

    params = {"baslangic": b.isoformat(), "bitis": s.isoformat()}
    if ara:
        params["ara"] = ara
    if usd:
        params["gorunum"] = "usd"
    sorgu = urlencode(params) + "&"
    return render(request, "core/fis_listesi.html",
                  {"form": form, "fisler": sayfa, "ara": ara, "sorgu": sorgu,
                   "usd": usd, "bas": b.isoformat(), "bit": s.isoformat()})


@ekran_gerekli("fis_listesi")
def fis_duzenle(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    if fis.silindi:
        return redirect("core:fis_detay", pk=fis.pk)

    if request.method == "POST":
        fform = FisForm(request.POST)
        formset = SatirFormSet(request.POST)
        if fform.is_valid() and formset.is_valid():
            try:
                fis_guncelle(
                    fis,
                    tarih=fform.cleaned_data["tarih"],
                    aciklama=fform.cleaned_data.get("aciklama", ""),
                    satirlar=_satir_girdileri(formset),
                    kullanici=request.user,
                )
                messages.success(request, f"Fiş güncellendi: {fis.yil}/{fis.fis_no}")
                return redirect("core:fis_detay", pk=fis.pk)
            except YevmiyeHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FisForm(initial={
            "tarih": fis.tarih, "aciklama": fis.aciklama,
        })
        ilk = [
            {"hesap": s.hesap_id, "islem_pb": s.islem_pb,
             "borc": s.borc or None, "alacak": s.alacak or None,
             "islem_kuru": s.islem_kuru, "aciklama": s.aciklama}
            for s in fis.satirlar.filter(silindi=False).select_related("hesap")
        ]
        formset = SatirFormSet(initial=ilk)
    return render(request, "core/fis_duzenle.html",
                  {"fform": fform, "formset": formset, "fis": fis})


@ekran_gerekli("fis_listesi")
def fis_iptal_gorunum(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    if request.method == "POST":
        if fis.silindi:
            messages.success(request, f"Fiş zaten iptal: {fis.yil}/{fis.fis_no}")
        else:
            fis_iptal(fis, kullanici=request.user)
            messages.success(request, f"Fiş iptal edildi: {fis.yil}/{fis.fis_no}")
    return redirect("core:fis_detay", pk=fis.pk)


@ekran_gerekli_herhangi("fis_ekle", "fis_listesi")
def fis_detay(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    satirlar = fis.satirlar.select_related("hesap").all()
    toplam_borc = sum((s.borc for s in satirlar), Decimal("0.00"))
    toplam_alacak = sum((s.alacak for s in satirlar), Decimal("0.00"))
    return render(
        request, "core/fis_detay.html",
        {"fis": fis, "satirlar": satirlar,
         "toplam_borc": toplam_borc, "toplam_alacak": toplam_alacak,
         "fis_listesi_yetkili": ekran_gorebilir(request.user, "fis_listesi")},
    )


def _tarih_araligi(request):
    form = MizanFiltreForm(request.GET or None)
    if form.is_valid():
        return form, form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    baslangic, bitis = mali_yil_araligi()
    if not request.GET:
        form = MizanFiltreForm(initial={"baslangic": baslangic, "bitis": bitis})
    return form, baslangic, bitis


@login_required
def kur_usd_api(request):
    """Fiş ekranı USD önizleme için: fiş tarihine göre USD kuru (TCMB MB Alış)."""
    ham = request.GET.get("tarih")
    kur = None
    if ham:
        try:
            t = datetime.date.fromisoformat(ham)
        except ValueError:
            t = None
        if t is not None:
            k = kur_usd_birebir(t)
            if k is not None:
                kur = str(k)
    return JsonResponse({"kur": kur})


@ekran_gerekli("kurlar")
def kurlar(request):
    pb = (request.GET.get("pb") or "USD").upper()
    if pb not in ("USD", "EUR", "GBP"):
        pb = "USD"
    bugun = timezone.localdate()
    # Çekme formu (POST) — varsayılan son 7 gün
    form = MizanFiltreForm(
        request.POST or None,
        initial={"baslangic": bugun - datetime.timedelta(days=7), "bitis": bugun},
    )
    if request.method == "POST" and form.is_valid():
        try:
            ozet = kurlari_guncelle(
                form.cleaned_data["baslangic"], form.cleaned_data["bitis"],
                kullanici=request.user,
            )
        except TcmbHatasi as e:
            form.add_error(None, str(e))
        else:
            messages.success(
                request,
                f"TCMB çekildi: {ozet['yayin']} gün yayın bulundu, "
                f"{ozet['yazilan']} kur satırı yazıldı, "
                f"{ozet['atlanan']} gün yayın yok (hafta sonu/tatil — önceki iş günü kuru yazıldı).",
            )
            return redirect(f"{reverse('core:kurlar')}?pb={pb}")

    # Liste filtresi (GET) — varsayılan son 30 gün
    def _tarih(ad, varsayilan):
        ham = request.GET.get(ad)
        if ham:
            try:
                return datetime.date.fromisoformat(ham)
            except ValueError:
                pass
        return varsayilan
    liste_bit = _tarih("lbit", bugun)
    liste_bas = _tarih("lbas", bugun - datetime.timedelta(days=30))
    kayitlar = (
        Kur.objects.filter(silindi=False, tarih__gte=liste_bas, tarih__lte=liste_bit)
        .order_by("-tarih")
    )
    return render(request, "core/kurlar.html", {
        "form": form, "pb": pb, "kayitlar": kayitlar,
        "liste_bas": liste_bas, "liste_bit": liste_bit,
    })


RAPOR_KALEMLERI = [
    ("", "— (maliyet 7/A · ya da özet hesap)"),
    ("DV", "Bilanço · Dönen Varlıklar"),
    ("DDV", "Bilanço · Duran Varlıklar"),
    ("KVYK", "Bilanço · Kısa Vadeli Yabancı Kaynaklar"),
    ("UVYK", "Bilanço · Uzun Vadeli Yabancı Kaynaklar"),
    ("OZK", "Bilanço · Özkaynaklar"),
    ("A", "Gelir · A. Brüt Satışlar"),
    ("B", "Gelir · B. Satış İndirimleri"),
    ("C", "Gelir · C. Satışların Maliyeti"),
    ("D", "Gelir · D. Faaliyet Giderleri"),
    ("E", "Gelir · E. Diğer Olağan Gelir/Kâr"),
    ("F", "Gelir · F. Diğer Olağan Gider/Zarar"),
    ("G", "Gelir · G. Finansman Giderleri"),
    ("H", "Gelir · H. Olağandışı Gelir/Kâr"),
    ("I", "Gelir · I. Olağandışı Gider/Zarar"),
    ("J", "Gelir · J. Dönem Kârı Vergi Karşılığı"),
]


@ekran_gerekli("hesap_plani")
def hesap_plani(request):
    ust_kodlari = set(
        HesapPlani.objects.filter(silindi=False, ust_hesap__isnull=False)
        .values_list("ust_hesap_id", flat=True)
    )
    yevmiyeli = set(YevmiyeSatir.objects.values_list("hesap_id", flat=True).distinct())
    agac = []
    for h in HesapPlani.objects.filter(silindi=False).order_by("hesap_kodu"):
        ust = h.hesap_kodu in ust_kodlari
        agac.append({
            "kod": h.hesap_kodu, "ad": h.hesap_adi,
            "seviye": h.hesap_kodu.count("."),
            "yaprak": not ust,
            "silinebilir": (not ust) and (h.hesap_kodu not in yevmiyeli),
        })
    ust_kodu = request.GET.get("ust")
    ust_hesap = (HesapPlani.objects.filter(hesap_kodu=ust_kodu, silindi=False).first()
                 if ust_kodu else None)
    onerilen = hp.alt_kod_oner(ust_hesap) if ust_hesap else ""
    duzenle_kod = request.GET.get("duzenle")
    duzenlenecek = (HesapPlani.objects.filter(hesap_kodu=duzenle_kod, silindi=False).first()
                    if duzenle_kod else None)
    return render(request, "core/hesap_plani.html", {
        "agac": agac, "ust": ust_hesap, "onerilen_kod": onerilen,
        "duzenlenecek": duzenlenecek,
        "rapor_gruplari": HesapPlani.RaporGrubu.choices,
        "rapor_kalemleri": RAPOR_KALEMLERI,
    })


def _parasal_coz(deger):
    return {"e": True, "h": False}.get(deger)


@ekran_gerekli("hesap_plani")
def hesap_ekle(request):
    if request.method == "POST":
        try:
            h = hp.hesap_olustur(
                kod=request.POST.get("kod", ""),
                ad=request.POST.get("ad", ""),
                ust_kodu=(request.POST.get("ust_kodu") or "").strip() or None,
                rapor_grubu=(request.POST.get("rapor_grubu") or "").strip() or None,
                rapor_kalemi=(request.POST.get("rapor_kalemi") or "").strip(),
                parasal=_parasal_coz(request.POST.get("parasal")),
                kullanici=request.user,
            )
            messages.success(request, f"Hesap eklendi: {h.hesap_kodu} — {h.hesap_adi}")
        except hp.HesapHatasi as e:
            messages.error(request, str(e))
    return redirect("core:hesap_plani")


@ekran_gerekli("hesap_plani")
def hesap_ad_guncelle(request, kod):
    if request.method == "POST":
        try:
            h = hp.hesap_adi_guncelle(kod=kod, yeni_ad=request.POST.get("ad", ""),
                                      kullanici=request.user)
            messages.success(request, f"Hesap adı güncellendi: {h.hesap_kodu} — {h.hesap_adi}")
        except hp.HesapHatasi as e:
            messages.error(request, str(e))
    return redirect("core:hesap_plani")


@ekran_gerekli("hesap_plani")
def hesap_sil(request, kod):
    if request.method == "POST":
        try:
            h = hp.hesap_sil(kod=kod, kullanici=request.user)
            messages.success(request, f"Hesap silindi (pasifleştirildi): {h.hesap_kodu}")
        except hp.HesapHatasi as e:
            messages.error(request, str(e))
    return redirect("core:hesap_plani")


@ekran_gerekli("mizan")
def mizan_gorunum(request):
    form, b, s = _tarih_araligi(request)
    detay = request.GET.get("gorunum") == "detay"
    return render(request, "core/mizan.html", {
        "form": form, "mizan": mizan(b, s, detay=detay), "detay": detay,
    })


@ekran_gerekli("mizan")
def hesap_ekstresi(request, hesap_kodu):
    get_object_or_404(HesapPlani, hesap_kodu=hesap_kodu)
    form, b, s = _tarih_araligi(request)
    eks = ekstre_servis(hesap_kodu, b, s)
    return render(request, "core/hesap_ekstresi.html", {"form": form, "ekstre": eks})


@ekran_gerekli("bilanco")
def bilanco_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/bilanco.html", {"form": form, "bilanco": bilanco(b, s)})


@ekran_gerekli("gelir_tablosu")
def gelir_tablosu_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/gelir_tablosu.html",
                  {"form": form, "gt": gelir_tablosu(b, s)})


@ekran_gerekli("mizan_usd")
def mizan_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/mizan_usd.html",
                  {"form": form, "mizan": mizan_usd(b, s)})


@ekran_gerekli("gelir_tablosu_usd")
def gelir_tablosu_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/gelir_tablosu_usd.html",
                  {"form": form, "gt": gelir_tablosu_usd(b, s)})


@ekran_gerekli("bilanco_usd")
def bilanco_usd_gorunum(request):
    form, b, s = _tarih_araligi(request)
    return render(request, "core/bilanco_usd.html",
                  {"form": form, "bilanco": bilanco_usd(b, s)})


# --- Ayarlar modülü (yalnızca yönetici) ------------------------------------
@yonetici_gerekli
def kullanici_listesi(request):
    kullanicilar = User.objects.select_related("profil").order_by("username")
    return render(request, "core/kullanici_listesi.html",
                  {"kullanicilar": kullanicilar})


@yonetici_gerekli
def kullanici_ekle(request):
    if request.method == "POST":
        form = KullaniciEkleForm(request.POST)
        if form.is_valid():
            u = form.kaydet()
            messages.success(
                request, f"Kullanıcı eklendi: {u.get_full_name()} ({u.username})"
            )
            return redirect("core:kullanici_listesi")
    else:
        form = KullaniciEkleForm()
    return render(request, "core/kullanici_form.html",
                  {"form": form, "baslik": "Yeni Kullanıcı"})


@yonetici_gerekli
def kullanici_duzenle(request, pk):
    kullanici = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = KullaniciDuzenleForm(request.POST, kullanici=kullanici)
        if form.is_valid():
            form.kaydet()
            messages.success(request, "Kullanıcı güncellendi.")
            return redirect("core:kullanici_listesi")
    else:
        form = KullaniciDuzenleForm(kullanici=kullanici)
    return render(request, "core/kullanici_form.html",
                  {"form": form, "baslik": "Kullanıcı Düzenle", "duzenlenen": kullanici})


@yonetici_gerekli
def kullanici_yetkileri(request):
    kullanicilar = User.objects.order_by("username")
    pk = request.POST.get("kullanici") or request.GET.get("kullanici")
    secili = get_object_or_404(User, pk=pk) if pk else None

    if request.method == "POST" and secili:
        gecerli = {e.kod for m in MODULLER if not m.yonetici_modulu for e in m.ekranlar}
        secilenler = set(request.POST.getlist("ekranlar")) & gecerli
        EkranYetki.objects.filter(kullanici=secili).delete()
        EkranYetki.objects.bulk_create(
            [EkranYetki(kullanici=secili, ekran_kod=k) for k in sorted(secilenler)]
        )
        ad = secili.get_full_name() or secili.username
        messages.success(request, f"{ad} için ekran yetkileri güncellendi.")
        return redirect(f"{reverse('core:kullanici_yetkileri')}?kullanici={secili.pk}")

    mevcut = set()
    if secili:
        mevcut = set(
            EkranYetki.objects.filter(kullanici=secili).values_list("ekran_kod", flat=True)
        )
    return render(request, "core/kullanici_yetkileri.html", {
        "kullanicilar": kullanicilar,
        "secili": secili,
        "mevcut": mevcut,
        "yetki_modulleri": [m for m in MODULLER if not m.yonetici_modulu],
        "secili_yonetici": yonetici_mi(secili) if secili else False,
    })


@yonetici_gerekli
def yedek_yonetim(request):
    """AYARLAR > Yedek: liste + Şimdi Yedek Al (Aşama 1 motorunu tetikler)."""
    if request.method == "POST":
        basari, mesaj = yedek_servis.yedek_al_arkaplan()   # bekletmez (arka plan)
        (messages.success if basari else messages.error)(request, mesaj)
        return redirect("core:yedek")
    yedekler = yedek_servis.yedekleri_listele()
    return render(request, "core/yedek.html", {
        "yedekler": yedekler,
        "son_yedek": yedekler[0] if yedekler else None,
    })


@yonetici_gerekli
def yedek_indir(request, ad):
    """Bir yedek dosyasını tarayıcıdan indir (offsite kopya). Geçersiz ad => 404."""
    yol = yedek_servis.yedek_yolu(ad)
    if yol is None:
        raise Http404("Yedek bulunamadı.")
    return FileResponse(open(yol, "rb"), as_attachment=True, filename=yol.name)
