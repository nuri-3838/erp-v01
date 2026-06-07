"""Fiş giriş/liste/düzenleme/görüntüleme, rapor, kullanıcı yönetimi ve ekran yetkisi görünümleri."""
import datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.forms import formset_factory
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.forms import (
    BirimForm, CariBankaForm, CariForm, CariKategoriForm, CariYetkiliForm,
    FaturaTipiForm, FisForm, KategoriForm, KullaniciDuzenleForm, KullaniciEkleForm,
    MizanFiltreForm, SatirForm, SehirForm, StokForm, UlkeForm,
)
from core.models import (
    Birim, Cari, CariBanka, CariKategori, CariYetkili, EkranYetki, FaturaTipi,
    HesapPlani, Kategori, Kur, Sehir, Stok, Ulke, YevmiyeFisi, YevmiyeSatir,
)
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
from core.services import birim as birim_servis
from core.services import kategori as kategori_servis
from core.services import fatura_tipi as fatura_tipi_servis
from core.services import lokasyon as lokasyon_servis
from core.services import cari_kategori as cari_kategori_servis
from core.services import cari as cari_servis
from core.services import stok as stok_servis
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


# --- STOKLAR modülü --------------------------------------------------------
@ekran_gerekli("stoklar")
def stoklar(request):
    return render(request, "core/stok_listesi.html",
                  {"stoklar": stok_servis.aktif_stoklar()})


@ekran_gerekli("stoklar")
def stok_ekle(request):
    if request.method == "POST":
        form = StokForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                s = stok_servis.stok_olustur(
                    ad=cd["ad"], kategori_id=cd["kategori"].pk,
                    uretim_birimi_id=cd["uretim_birimi"].pk,
                    fatura_birimi_id=cd["fatura_birimi"].pk,
                    cevirici=cd["cevirici"], kdv_orani=cd["kdv_orani"],
                    tevkifat_orani=cd.get("tevkifat_orani"),
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci=cd.get("tedarikci", ""),
                    kullanici=request.user)
                messages.success(request, f"Stok eklendi: {s.kod} — {s.ad}")
                return redirect("core:stoklar")
            except stok_servis.StokHatasi as e:
                form.add_error(None, str(e))
    else:
        form = StokForm()
    return render(request, "core/stok_form.html", {"form": form, "baslik": "Yeni Stok"})


@ekran_gerekli("stoklar")
def stok_duzenle(request, pk):
    stok = get_object_or_404(
        Stok.objects.select_related("kategori", "kategori__ust"), pk=pk, silindi=False)
    if request.method == "POST":
        form = StokForm(request.POST, duzenle=True)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                stok_servis.stok_guncelle(
                    stok, ad=cd["ad"],
                    uretim_birimi_id=cd["uretim_birimi"].pk,
                    fatura_birimi_id=cd["fatura_birimi"].pk,
                    cevirici=cd["cevirici"], kdv_orani=cd["kdv_orani"],
                    tevkifat_orani=cd.get("tevkifat_orani"),
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci=cd.get("tedarikci", ""),
                    kullanici=request.user)
                messages.success(request, "Stok güncellendi.")
                return redirect("core:stoklar")
            except stok_servis.StokHatasi as e:
                form.add_error(None, str(e))
    else:
        form = StokForm(duzenle=True, initial={
            "ad": stok.ad, "uretim_birimi": stok.uretim_birimi_id,
            "fatura_birimi": stok.fatura_birimi_id, "cevirici": stok.cevirici,
            "kdv_orani": stok.kdv_orani, "tevkifat_orani": stok.tevkifat_orani,
            "kritik_stok": stok.kritik_stok, "tedarikci": stok.tedarikci})
    return render(request, "core/stok_form.html",
                  {"form": form, "baslik": "Stok Düzenle", "duzenlenen": stok})


@ekran_gerekli("stoklar")
def stok_sil(request, pk):
    stok = get_object_or_404(Stok, pk=pk, silindi=False)
    if request.method == "POST":
        stok_servis.stok_sil(stok, kullanici=request.user)
        messages.success(request, f"Stok silindi: {stok.kod}")
    return redirect("core:stoklar")


@ekran_gerekli("stoklar")
def stok_kod_api(request):
    """Yeni stok ekranı için: seçilen ALT kategoriye göre sıradaki otomatik kodu döndürür."""
    kod = None
    ham = request.GET.get("kategori")
    if ham:
        kategori = Kategori.objects.filter(
            pk=ham, silindi=False, ust__isnull=False).select_related("ust").first()
        if kategori is not None:
            kod = stok_servis.sonraki_stok_kodu(kategori)
    return JsonResponse({"kod": kod})


@ekran_gerekli("stoklar")
def stok_detay(request, pk):
    """Stok kartı detay sayfası (master-detail, read-only). Temel bilgiler + kategoriden
    gelen muhasebe hesabı haritası + audit. Stok hareketleri/bakiye Faz B'de gelecek."""
    stok = get_object_or_404(
        Stok.objects.select_related(
            "kategori", "kategori__ust", "uretim_birimi", "fatura_birimi",
            "created_by", "updated_by"),
        pk=pk, silindi=False)
    harita = kategori_servis.kategori_hesaplari(stok.kategori)
    baglar = sorted(harita.values(),
                    key=lambda kh: (kh.fatura_tipi.sira, kh.fatura_tipi.ad))
    return render(request, "core/stok_detay.html", {"stok": stok, "baglar": baglar})


@ekran_gerekli("kategoriler")
def kategoriler(request):
    es = Count("hesap_baglari", filter=Q(hesap_baglari__silindi=False))
    alt_qs = Kategori.objects.filter(silindi=False).order_by("kod").annotate(es=es)
    koklar = (Kategori.objects.filter(silindi=False, ust__isnull=True)
              .order_by("kod").annotate(es=es)
              .prefetch_related(Prefetch("alt_kategoriler", queryset=alt_qs)))
    return render(request, "core/kategori_listesi.html", {"koklar": koklar})


def _harita_gruplari(aktif_ft, secili_map):
    """Şablon için fatura tiplerini SATIŞ/ALIŞ gruplar; her birine seçili hesap kodunu ekler."""
    satis, alis = [], []
    for ft in aktif_ft:
        satir = {"ft": ft, "secili": secili_map.get(ft.pk, "")}
        (satis if ft.yon == FaturaTipi.Yon.SATIS else alis).append(satir)
    return satis, alis


@ekran_gerekli("kategoriler")
def kategori_ekle(request):
    # Üst, GİRİŞ NOKTASIYLA belirlenir: ?ust=<kök pk> → ALT (o üstün altına, harita var);
    # yoksa → KÖK kategori (harita yok). POST'ta üst gizli alandan gelir.
    ham_ust = request.POST.get("ust") if request.method == "POST" else request.GET.get("ust")
    ust = (Kategori.objects.filter(pk=ham_ust, silindi=False, ust__isnull=True).first()
           if ham_ust else None)
    alt_mod = ust is not None
    aktif_ft = list(fatura_tipi_servis.aktif_fatura_tipleri()) if alt_mod else []
    secili_map = {}
    if request.method == "POST":
        form = KategoriForm(request.POST)
        if alt_mod:
            secili_map = {ft.pk: (request.POST.get(f"hesap_{ft.pk}") or "").strip()
                          for ft in aktif_ft}
        if form.is_valid():
            try:
                k = kategori_servis.kategori_olustur(
                    ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    ust_id=ust.pk if ust else None, kullanici=request.user)
                if alt_mod:
                    kategori_servis.kategori_hesaplari_kaydet(
                        k, eslesmeler=secili_map, kullanici=request.user)
                messages.success(request, f"Kategori eklendi: {k.ad}")
                return redirect("core:kategoriler")
            except kategori_servis.KategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KategoriForm()
    satis, alis = _harita_gruplari(aktif_ft, secili_map)
    baslik = (f"{ust.ad} → Yeni Alt Kategori" if alt_mod else "Yeni Üst Kategori")
    return render(request, "core/kategori_form.html", {
        "form": form, "baslik": baslik, "ekle": True, "ust": ust, "kat_alt": alt_mod,
        "harita_satis": satis, "harita_alis": alis, "yaprak": hp.yaprak_hesaplar(),
    })


@ekran_gerekli("kategoriler")
def kategori_duzenle(request, pk):
    kat = get_object_or_404(Kategori, pk=pk, silindi=False)
    kat_alt = kat.ust_id is not None
    aktif_ft = list(fatura_tipi_servis.aktif_fatura_tipleri()) if kat_alt else []
    if request.method == "POST":
        form = KategoriForm(request.POST)
        secili_map = {ft.pk: (request.POST.get(f"hesap_{ft.pk}") or "").strip()
                      for ft in aktif_ft}
        if form.is_valid():
            try:
                kategori_servis.kategori_guncelle(
                    kat, ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    kullanici=request.user)
                if kat_alt:
                    kategori_servis.kategori_hesaplari_kaydet(
                        kat, eslesmeler=secili_map, kullanici=request.user)
                messages.success(request, "Kategori güncellendi.")
                return redirect("core:kategoriler")
            except kategori_servis.KategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KategoriForm(initial={"ad": kat.ad, "kod": kat.kod})
        mevcut = kategori_servis.kategori_hesaplari(kat)
        secili_map = {ft_id: kh.hesap_id for ft_id, kh in mevcut.items()}
    satis, alis = _harita_gruplari(aktif_ft, secili_map)
    return render(request, "core/kategori_form.html", {
        "form": form, "baslik": "Kategori Düzenle", "duzenlenen": kat,
        "ekle": False, "ust": kat.ust, "kat_alt": kat_alt,
        "harita_satis": satis, "harita_alis": alis, "yaprak": hp.yaprak_hesaplar(),
    })


@ekran_gerekli("kategoriler")
def kategori_sil(request, pk):
    kat = get_object_or_404(Kategori, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            kategori_servis.kategori_sil(kat, kullanici=request.user)
            messages.success(request, f"Kategori silindi: {kat.ad}")
        except kategori_servis.KategoriHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kategoriler")


@ekran_gerekli("birimler")
def birimler(request):
    return render(request, "core/birim_listesi.html",
                  {"birimler": birim_servis.aktif_birimler()})


@ekran_gerekli("birimler")
def birim_ekle(request):
    if request.method == "POST":
        form = BirimForm(request.POST)
        if form.is_valid():
            try:
                b = birim_servis.birim_olustur(**form.cleaned_data, kullanici=request.user)
                messages.success(request, f"Birim eklendi: {b.ad} ({b.kisa_ad})")
                return redirect("core:birimler")
            except birim_servis.BirimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BirimForm()
    return render(request, "core/birim_form.html", {"form": form, "baslik": "Yeni Birim"})


@ekran_gerekli("birimler")
def birim_duzenle(request, pk):
    birim = get_object_or_404(Birim, pk=pk, silindi=False)
    if request.method == "POST":
        form = BirimForm(request.POST)
        if form.is_valid():
            try:
                birim_servis.birim_guncelle(birim, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Birim güncellendi.")
                return redirect("core:birimler")
            except birim_servis.BirimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BirimForm(initial={"ad": birim.ad, "kisa_ad": birim.kisa_ad,
                                  "ondalik": birim.ondalik})
    return render(request, "core/birim_form.html",
                  {"form": form, "baslik": "Birim Düzenle", "duzenlenen": birim})


@ekran_gerekli("birimler")
def birim_sil(request, pk):
    birim = get_object_or_404(Birim, pk=pk, silindi=False)
    if request.method == "POST":
        birim_servis.birim_sil(birim, kullanici=request.user)
        messages.success(request, f"Birim silindi: {birim.ad}")
    return redirect("core:birimler")


@ekran_gerekli("fatura_tipleri")
def fatura_tipleri(request):
    tipler = fatura_tipi_servis.aktif_fatura_tipleri()
    return render(request, "core/fatura_tipi_listesi.html", {
        "satis": [t for t in tipler if t.yon == FaturaTipi.Yon.SATIS],
        "alis": [t for t in tipler if t.yon == FaturaTipi.Yon.ALIS],
    })


@ekran_gerekli("fatura_tipleri")
def fatura_tipi_ekle(request):
    if request.method == "POST":
        form = FaturaTipiForm(request.POST)
        if form.is_valid():
            try:
                t = fatura_tipi_servis.fatura_tipi_olustur(
                    **form.cleaned_data, kullanici=request.user)
                messages.success(request, f"Fatura tipi eklendi: {t.ad}")
                return redirect("core:fatura_tipleri")
            except fatura_tipi_servis.FaturaTipiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = FaturaTipiForm()
    return render(request, "core/fatura_tipi_form.html",
                  {"form": form, "baslik": "Yeni Fatura Tipi"})


@ekran_gerekli("fatura_tipleri")
def fatura_tipi_duzenle(request, pk):
    tip = get_object_or_404(FaturaTipi, pk=pk, silindi=False)
    if request.method == "POST":
        form = FaturaTipiForm(request.POST)
        if form.is_valid():
            try:
                fatura_tipi_servis.fatura_tipi_guncelle(
                    tip, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Fatura tipi güncellendi.")
                return redirect("core:fatura_tipleri")
            except fatura_tipi_servis.FaturaTipiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = FaturaTipiForm(initial={
            "ad": tip.ad, "yon": tip.yon, "sira": tip.sira})
    return render(request, "core/fatura_tipi_form.html",
                  {"form": form, "baslik": "Fatura Tipi Düzenle", "duzenlenen": tip})


@ekran_gerekli("fatura_tipleri")
def fatura_tipi_sil(request, pk):
    tip = get_object_or_404(FaturaTipi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            fatura_tipi_servis.fatura_tipi_sil(tip, kullanici=request.user)
            messages.success(request, f"Fatura tipi silindi: {tip.ad}")
        except fatura_tipi_servis.FaturaTipiHatasi as e:
            messages.error(request, str(e))
    return redirect("core:fatura_tipleri")


# --- CARİLER modülü — Ülke / Şehir -----------------------------------------
@ekran_gerekli("lokasyonlar")
def lokasyonlar(request):
    from django.db.models import Count, Q
    ulkeler = (lokasyon_servis.aktif_ulkeler()
               .annotate(sehir_sayisi=Count("sehirler",
                                            filter=Q(sehirler__silindi=False))))
    return render(request, "core/lokasyon_listesi.html", {
        "ulkeler": ulkeler, "sehirler": lokasyon_servis.aktif_sehirler()})


@ekran_gerekli("lokasyonlar")
def ulke_ekle(request):
    if request.method == "POST":
        form = UlkeForm(request.POST)
        if form.is_valid():
            try:
                lokasyon_servis.ulke_olustur(**form.cleaned_data, kullanici=request.user)
                messages.success(request, "Ülke eklendi.")
                return redirect("core:lokasyonlar")
            except lokasyon_servis.LokasyonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = UlkeForm()
    return render(request, "core/ulke_form.html", {"form": form, "baslik": "Yeni Ülke"})


@ekran_gerekli("lokasyonlar")
def ulke_duzenle(request, pk):
    ulke = get_object_or_404(Ulke, pk=pk, silindi=False)
    if request.method == "POST":
        form = UlkeForm(request.POST)
        if form.is_valid():
            try:
                lokasyon_servis.ulke_guncelle(ulke, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Ülke güncellendi.")
                return redirect("core:lokasyonlar")
            except lokasyon_servis.LokasyonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = UlkeForm(initial={"kod": ulke.kod, "ad": ulke.ad, "ad_en": ulke.ad_en})
    return render(request, "core/ulke_form.html",
                  {"form": form, "baslik": "Ülke Düzenle", "duzenlenen": ulke})


@ekran_gerekli("lokasyonlar")
def ulke_sil(request, pk):
    ulke = get_object_or_404(Ulke, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            lokasyon_servis.ulke_sil(ulke, kullanici=request.user)
            messages.success(request, f"Ülke silindi: {ulke.ad}")
        except lokasyon_servis.LokasyonHatasi as e:
            messages.error(request, str(e))
    return redirect("core:lokasyonlar")


@ekran_gerekli("lokasyonlar")
def sehir_ekle(request):
    if request.method == "POST":
        form = SehirForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                lokasyon_servis.sehir_olustur(
                    ulke_id=cd["ulke"].pk, ad=cd["ad"], kod=cd.get("kod", ""),
                    ad_en=cd.get("ad_en", ""), kullanici=request.user)
                messages.success(request, "Şehir eklendi.")
                return redirect("core:lokasyonlar")
            except lokasyon_servis.LokasyonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = SehirForm()
    return render(request, "core/sehir_form.html", {"form": form, "baslik": "Yeni Şehir"})


@ekran_gerekli("lokasyonlar")
def sehir_duzenle(request, pk):
    sehir = get_object_or_404(Sehir, pk=pk, silindi=False)
    if request.method == "POST":
        form = SehirForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                lokasyon_servis.sehir_guncelle(
                    sehir, ulke_id=cd["ulke"].pk, ad=cd["ad"], kod=cd.get("kod", ""),
                    ad_en=cd.get("ad_en", ""), kullanici=request.user)
                messages.success(request, "Şehir güncellendi.")
                return redirect("core:lokasyonlar")
            except lokasyon_servis.LokasyonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = SehirForm(initial={"ulke": sehir.ulke_id, "ad": sehir.ad,
                                  "kod": sehir.kod, "ad_en": sehir.ad_en})
    return render(request, "core/sehir_form.html",
                  {"form": form, "baslik": "Şehir Düzenle", "duzenlenen": sehir})


@ekran_gerekli("lokasyonlar")
def sehir_sil(request, pk):
    sehir = get_object_or_404(Sehir, pk=pk, silindi=False)
    if request.method == "POST":
        lokasyon_servis.sehir_sil(sehir, kullanici=request.user)
        messages.success(request, f"Şehir silindi: {sehir.ad}")
    return redirect("core:lokasyonlar")


# --- CARİLER modülü — Cari Kategorileri ------------------------------------
@ekran_gerekli("cari_kategoriler")
def cari_kategoriler(request):
    alt_qs = CariKategori.objects.filter(silindi=False).order_by("kod")
    koklar = (CariKategori.objects.filter(silindi=False, ust__isnull=True)
              .order_by("kod")
              .prefetch_related(Prefetch("alt_kategoriler", queryset=alt_qs)))
    return render(request, "core/cari_kategori_listesi.html", {"koklar": koklar})


@ekran_gerekli("cari_kategoriler")
def cari_kategori_ekle(request):
    ham_ust = (request.POST.get("ust") if request.method == "POST"
               else request.GET.get("ust"))
    ust = (CariKategori.objects.filter(pk=ham_ust, silindi=False, ust__isnull=True).first()
           if ham_ust else None)
    if request.method == "POST":
        form = CariKategoriForm(request.POST)
        if form.is_valid():
            try:
                k = cari_kategori_servis.cari_kategori_olustur(
                    ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    ust_id=ust.pk if ust else None, kullanici=request.user)
                messages.success(request, f"Cari kategori eklendi: {k.ad}")
                return redirect("core:cari_kategoriler")
            except cari_kategori_servis.CariKategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariKategoriForm()
    baslik = (f"{ust.ad} → Yeni Alt Kategori" if ust else "Yeni Üst Kategori")
    return render(request, "core/cari_kategori_form.html",
                  {"form": form, "baslik": baslik, "ekle": True, "ust": ust})


@ekran_gerekli("cari_kategoriler")
def cari_kategori_duzenle(request, pk):
    kat = get_object_or_404(CariKategori, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariKategoriForm(request.POST)
        if form.is_valid():
            try:
                cari_kategori_servis.cari_kategori_guncelle(
                    kat, ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    kullanici=request.user)
                messages.success(request, "Cari kategori güncellendi.")
                return redirect("core:cari_kategoriler")
            except cari_kategori_servis.CariKategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariKategoriForm(initial={"ad": kat.ad, "kod": kat.kod})
    return render(request, "core/cari_kategori_form.html",
                  {"form": form, "baslik": "Cari Kategori Düzenle", "ekle": False,
                   "ust": kat.ust, "duzenlenen": kat})


@ekran_gerekli("cari_kategoriler")
def cari_kategori_sil(request, pk):
    kat = get_object_or_404(CariKategori, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            cari_kategori_servis.cari_kategori_sil(kat, kullanici=request.user)
            messages.success(request, f"Cari kategori silindi: {kat.ad}")
        except cari_kategori_servis.CariKategoriHatasi as e:
            messages.error(request, str(e))
    return redirect("core:cari_kategoriler")


# --- CARİLER modülü — Cari kartı --------------------------------------------
def _cari_form_kw(cd):
    """CariForm cleaned_data -> cari servis kwargs (FK'ler -> *_id)."""
    g = lambda x: x.pk if x else None
    return dict(
        unvan=cd["unvan"], kategori_id=g(cd["kategori"]), kisa_ad=cd["kisa_ad"],
        vergi_dairesi=cd["vergi_dairesi"], vkn_tckn=cd["vkn_tckn"], tax_id=cd["tax_id"],
        telefon=cd["telefon"], telefon_2=cd["telefon_2"], eposta=cd["eposta"],
        web=cd["web"], kep_adresi=cd["kep_adresi"],
        ulke_id=g(cd["ulke"]), sehir_id=g(cd["sehir"]), adres=cd["adres"],
        posta_kodu=cd["posta_kodu"], sevk_farkli=cd["sevk_farkli"],
        sevk_ulke_id=g(cd["sevk_ulke"]), sevk_sehir_id=g(cd["sevk_sehir"]),
        sevk_adres=cd["sevk_adres"], sevk_posta_kodu=cd["sevk_posta_kodu"],
        para_birimi=cd["para_birimi"], kredi_limiti=cd["kredi_limiti"],
        iskonto_yuzdesi=cd["iskonto_yuzdesi"], notlar=cd["notlar"])


@ekran_gerekli("cariler")
def cariler(request):
    ara = (request.GET.get("ara") or "").strip()
    qs = cari_servis.aktif_cariler()
    if ara:
        qs = qs.filter(
            Q(unvan__contains=buyuk_harf_tr(ara)) | Q(kod__contains=ara)
            | Q(vkn_tckn__contains=ara) | Q(tax_id__contains=ara))
    return render(request, "core/cari_listesi.html", {"cariler": qs, "ara": ara})


@ekran_gerekli("cariler")
def cari_ekle(request):
    if request.method == "POST":
        form = CariForm(request.POST)
        if form.is_valid():
            try:
                c = cari_servis.cari_olustur(**_cari_form_kw(form.cleaned_data),
                                             kullanici=request.user)
                messages.success(request, f"Cari eklendi: {c.kod} — {c.unvan}")
                return redirect("core:cari_detay", pk=c.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariForm()
    return render(request, "core/cari_form.html", {"form": form, "baslik": "Yeni Cari"})


@ekran_gerekli("cariler")
def cari_duzenle(request, pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.cari_guncelle(cari, **_cari_form_kw(form.cleaned_data),
                                          kullanici=request.user)
                messages.success(request, "Cari güncellendi.")
                return redirect("core:cari_detay", pk=cari.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariForm(initial={
            "unvan": cari.unvan, "kisa_ad": cari.kisa_ad, "kategori": cari.kategori_id,
            "vergi_dairesi": cari.vergi_dairesi, "vkn_tckn": cari.vkn_tckn,
            "tax_id": cari.tax_id, "telefon": cari.telefon, "telefon_2": cari.telefon_2,
            "eposta": cari.eposta, "web": cari.web, "kep_adresi": cari.kep_adresi,
            "ulke": cari.ulke_id, "sehir": cari.sehir_id, "adres": cari.adres,
            "posta_kodu": cari.posta_kodu, "sevk_farkli": cari.sevk_farkli,
            "sevk_ulke": cari.sevk_ulke_id, "sevk_sehir": cari.sevk_sehir_id,
            "sevk_adres": cari.sevk_adres, "sevk_posta_kodu": cari.sevk_posta_kodu,
            "para_birimi": cari.para_birimi, "kredi_limiti": cari.kredi_limiti,
            "iskonto_yuzdesi": cari.iskonto_yuzdesi, "notlar": cari.notlar})
    return render(request, "core/cari_form.html",
                  {"form": form, "baslik": "Cari Düzenle", "duzenlenen": cari})


@ekran_gerekli("cariler")
def cari_detay(request, pk):
    cari = get_object_or_404(
        Cari.objects.select_related("kategori", "ulke", "sehir", "sevk_ulke",
                                    "sevk_sehir", "created_by", "updated_by"),
        pk=pk, silindi=False)
    return render(request, "core/cari_detay.html", {
        "cari": cari,
        "bankalar": cari_servis.aktif_bankalar(cari),
        "yetkililer": cari_servis.aktif_yetkililer(cari)})


@ekran_gerekli("cariler")
def cari_sil(request, pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.cari_sil(cari, kullanici=request.user)
        messages.success(request, f"Cari silindi: {cari.kod}")
    return redirect("core:cariler")


@ekran_gerekli("cariler")
def cari_kod_api(request):
    """Yeni cari ekranı için: seçilen kategoriye göre sıradaki otomatik kodu döndürür."""
    ham = request.GET.get("kategori")
    kategori = (CariKategori.objects.filter(pk=ham, silindi=False).select_related("ust").first()
                if ham else None)
    return JsonResponse({"kod": cari_servis.sonraki_cari_kodu(kategori)})


# --- Cari banka hesapları ---------------------------------------------------
@ekran_gerekli("cariler")
def banka_ekle(request, cari_pk):
    cari = get_object_or_404(Cari, pk=cari_pk, silindi=False)
    if request.method == "POST":
        form = CariBankaForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.banka_ekle(cari, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Banka hesabı eklendi.")
                return redirect("core:cari_detay", pk=cari.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariBankaForm()
    return render(request, "core/cari_banka_form.html",
                  {"form": form, "baslik": "Yeni Banka Hesabı", "cari": cari})


@ekran_gerekli("cariler")
def banka_duzenle(request, pk):
    banka = get_object_or_404(CariBanka, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariBankaForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.banka_guncelle(banka, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Banka hesabı güncellendi.")
                return redirect("core:cari_detay", pk=banka.cari_id)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariBankaForm(initial={
            "banka_adi": banka.banka_adi, "hesap_sahibi": banka.hesap_sahibi,
            "iban": banka.iban, "swift": banka.swift, "para_birimi": banka.para_birimi,
            "aciklama": banka.aciklama, "varsayilan": banka.varsayilan})
    return render(request, "core/cari_banka_form.html",
                  {"form": form, "baslik": "Banka Hesabı Düzenle", "cari": banka.cari})


@ekran_gerekli("cariler")
def banka_sil(request, pk):
    banka = get_object_or_404(CariBanka, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.banka_sil(banka, kullanici=request.user)
        messages.success(request, "Banka hesabı silindi.")
    return redirect("core:cari_detay", pk=banka.cari_id)


# --- Cari yetkili kişiler ---------------------------------------------------
@ekran_gerekli("cariler")
def yetkili_ekle(request, cari_pk):
    cari = get_object_or_404(Cari, pk=cari_pk, silindi=False)
    if request.method == "POST":
        form = CariYetkiliForm(request.POST)
        if form.is_valid():
            cari_servis.yetkili_ekle(cari, **form.cleaned_data, kullanici=request.user)
            messages.success(request, "Yetkili kişi eklendi.")
            return redirect("core:cari_detay", pk=cari.pk)
    else:
        form = CariYetkiliForm()
    return render(request, "core/cari_yetkili_form.html",
                  {"form": form, "baslik": "Yeni Yetkili Kişi", "cari": cari})


@ekran_gerekli("cariler")
def yetkili_duzenle(request, pk):
    yetkili = get_object_or_404(CariYetkili, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariYetkiliForm(request.POST)
        if form.is_valid():
            cari_servis.yetkili_guncelle(yetkili, **form.cleaned_data, kullanici=request.user)
            messages.success(request, "Yetkili kişi güncellendi.")
            return redirect("core:cari_detay", pk=yetkili.cari_id)
    else:
        form = CariYetkiliForm(initial={
            "ad_soyad": yetkili.ad_soyad, "unvan": yetkili.unvan,
            "telefon": yetkili.telefon, "eposta": yetkili.eposta, "notlar": yetkili.notlar})
    return render(request, "core/cari_yetkili_form.html",
                  {"form": form, "baslik": "Yetkili Kişi Düzenle", "cari": yetkili.cari})


@ekran_gerekli("cariler")
def yetkili_sil(request, pk):
    yetkili = get_object_or_404(CariYetkili, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.yetkili_sil(yetkili, kullanici=request.user)
        messages.success(request, "Yetkili kişi silindi.")
    return redirect("core:cari_detay", pk=yetkili.cari_id)
