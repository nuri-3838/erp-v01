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
    BilancoTarihForm, BirimForm, CariBankaForm, CariForm, CariKategoriForm,
    BankaForm, CariYetkiliForm, CekSenetForm, DepoForm, FaturaForm, FaturaSatirForm,
    FaturaTipiForm, FisForm,
    KasaForm, KategoriForm, KdvOraniForm, KrediForm, KrediKartiForm,
    KullaniciDuzenleForm, KullaniciEkleForm,
    MizanFiltreForm, SatirForm, SehirForm, StokForm, StokHareketForm, TevkifatOraniForm,
    UlkeForm,
)
from core.models import (
    Birim, Cari, CariBanka, CariKategori, CariYetkili, Depo, EkranYetki, Fatura,
    Banka, CekSenet, FaturaTipi, HesapPlani, Kasa, Kategori, KdvOrani, Kredi, KrediKarti,
    Kur, Sehir, Stok, TevkifatOrani, Ulke,
    YevmiyeFisi, YevmiyeSatir,
)
from core.moduller import MODULLER
from core.metin import buyuk_harf_tr
from core.sayi import SayiHatasi, parse_tr
from core.services.raporlar import (
    bilanco, bilanco_usd, ekstre as ekstre_servis,
    ekstre_devirli as ekstre_devirli_servis, gelir_tablosu, gelir_tablosu_usd,
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
from core.services import tanim as tanim_servis
from core.services import stok as stok_servis
from core.services import fatura as fatura_servis
from core.services import depo as depo_servis
from core.services import hareket as hareket_servis
from core.services import finans as finans_servis
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
    if fis.kaynak == YevmiyeFisi.Kaynak.FATURA:
        fat = fis.faturalar.filter(silindi=False).first()
        messages.info(request, "Bu fiş bir faturadan oluştu; düzenlemek için faturayı düzenleyin.")
        return redirect("core:fatura_duzenle", pk=fat.pk) if fat else redirect("core:fis_detay", pk=fis.pk)

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
        # NOT: Borç/Alacak alanları İŞLEM TUTARINI (döviz) taşır; TL değil. Döviz
        # fişte TL = islem_tutari × kur olduğundan kutuya islem_tutari konur (çift
        # çevrim olmaz). TRY'de islem_tutari == TL zaten.
        ilk = []
        for s in fis.satirlar.filter(silindi=False).select_related("hesap"):
            borc_taraf = bool(s.borc and s.borc > 0)
            ilk.append({
                "hesap": s.hesap_id, "islem_pb": s.islem_pb,
                "borc": s.islem_tutari if borc_taraf else None,
                "alacak": None if borc_taraf else s.islem_tutari,
                "islem_kuru": s.islem_kuru, "aciklama": s.aciklama,
            })
        formset = SatirFormSet(initial=ilk)
    return render(request, "core/fis_duzenle.html",
                  {"fform": fform, "formset": formset, "fis": fis})


@ekran_gerekli("fis_listesi")
def fis_iptal_gorunum(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    if fis.kaynak == YevmiyeFisi.Kaynak.FATURA and not fis.silindi:
        fat = fis.faturalar.filter(silindi=False).first()
        if fat:
            messages.info(request, "Bu fiş bir faturadan oluştu; iptal için faturayı iptal edin.")
            return redirect("core:fatura_detay", pk=fat.pk)
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
    satirlar = fis.satirlar.filter(silindi=False).select_related("hesap")
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
    # Üst (ara/ana) hesap kodları KODDAN türetilir (ayrı ust_hesap FK yok).
    ust_kodlari = set()
    for k in (HesapPlani.objects.filter(silindi=False)
              .values_list("hesap_kodu", flat=True)):
        if "." in k:
            ust_kodlari.add(k.rsplit(".", 1)[0])
    yevmiyeli = set(YevmiyeSatir.objects.filter(silindi=False)
                    .values_list("hesap_id", flat=True).distinct())
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


def _bilanco_tarihi(request):
    """Bilanço TEK tarih (anlık durum). Varsayılan: bugün."""
    form = BilancoTarihForm(request.GET or None)
    if form.is_valid():
        return form, form.cleaned_data["tarih"]
    t = timezone.localdate()
    if not request.GET:
        form = BilancoTarihForm(initial={"tarih": t})
    return form, t


@ekran_gerekli("bilanco")
def bilanco_gorunum(request):
    form, t = _bilanco_tarihi(request)
    return render(request, "core/bilanco.html", {"form": form, "bilanco": bilanco(t)})


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
    form, t = _bilanco_tarihi(request)
    return render(request, "core/bilanco_usd.html",
                  {"form": form, "bilanco": bilanco_usd(t)})


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
                    cevirici=cd["cevirici"],
                    kdv_id=cd["kdv"].pk if cd.get("kdv") else None,
                    tevkifat_id=cd["tevkifat"].pk if cd.get("tevkifat") else None,
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci_id=cd["tedarikci"].pk if cd.get("tedarikci") else None,
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
                    cevirici=cd["cevirici"],
                    kdv_id=cd["kdv"].pk if cd.get("kdv") else None,
                    tevkifat_id=cd["tevkifat"].pk if cd.get("tevkifat") else None,
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci_id=cd["tedarikci"].pk if cd.get("tedarikci") else None,
                    kullanici=request.user)
                messages.success(request, "Stok güncellendi.")
                return redirect("core:stoklar")
            except stok_servis.StokHatasi as e:
                form.add_error(None, str(e))
    else:
        form = StokForm(duzenle=True, initial={
            "ad": stok.ad, "uretim_birimi": stok.uretim_birimi_id,
            "fatura_birimi": stok.fatura_birimi_id, "cevirici": stok.cevirici,
            "kdv": stok.kdv_id, "tevkifat": stok.tevkifat_id,
            "kritik_stok": stok.kritik_stok, "tedarikci": stok.tedarikci_id})
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
    return render(request, "core/stok_detay.html", {
        "stok": stok, "baglar": baglar,
        "eldeki": hareket_servis.eldeki_miktar(stok),
        "depo_bakiye": hareket_servis.depo_bazinda_eldeki(stok),
        "hareketler": hareket_servis.stok_hareketleri(stok)[:100],
    })


# === STOKLAR Faz B — Depolar (CRUD) + Stok hareketleri ===
@ekran_gerekli("depolar")
def depolar(request):
    return render(request, "core/depo_listesi.html",
                  {"depolar": depo_servis.aktif_depolar()})


@ekran_gerekli("depolar")
def depo_ekle(request):
    if request.method == "POST":
        form = DepoForm(request.POST)
        if form.is_valid():
            try:
                d = depo_servis.depo_olustur(**form.cleaned_data, kullanici=request.user)
                messages.success(request, f"Depo eklendi: {d.kod} — {d.ad}")
                return redirect("core:depolar")
            except depo_servis.DepoHatasi as e:
                form.add_error(None, str(e))
    else:
        form = DepoForm()
    return render(request, "core/depo_form.html", {"form": form, "baslik": "Yeni Depo"})


@ekran_gerekli("depolar")
def depo_duzenle(request, pk):
    depo = get_object_or_404(Depo, pk=pk, silindi=False)
    if request.method == "POST":
        form = DepoForm(request.POST)
        if form.is_valid():
            try:
                depo_servis.depo_guncelle(depo, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "Depo güncellendi.")
                return redirect("core:depolar")
            except depo_servis.DepoHatasi as e:
                form.add_error(None, str(e))
    else:
        form = DepoForm(initial={"kod": depo.kod, "ad": depo.ad})
    return render(request, "core/depo_form.html",
                  {"form": form, "baslik": "Depo Düzenle", "duzenlenen": depo})


@ekran_gerekli("depolar")
def depo_sil(request, pk):
    depo = get_object_or_404(Depo, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            depo_servis.depo_sil(depo, kullanici=request.user)
            messages.success(request, f"Depo silindi: {depo.kod}")
        except depo_servis.DepoHatasi as e:
            messages.error(request, str(e))
    return redirect("core:depolar")


@ekran_gerekli("stoklar")
def stok_hareket_ekle(request, pk):
    stok = get_object_or_404(Stok.objects.select_related("uretim_birimi"),
                             pk=pk, silindi=False)
    if request.method == "POST":
        form = StokHareketForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                hareket_servis.hareket_ekle(
                    stok_id=stok.pk, depo_id=cd["depo"].pk, tarih=cd["tarih"],
                    tur=cd["tur"], miktar=cd["miktar"],
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, "Stok hareketi eklendi.")
                return redirect("core:stok_detay", pk=stok.pk)
            except hareket_servis.HareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = StokHareketForm()
    return render(request, "core/stok_hareket_form.html", {"form": form, "stok": stok})


@ekran_gerekli("stoklar")
def stok_hareket_sil(request, pk):
    from core.models import StokHareket
    hareket = get_object_or_404(StokHareket, pk=pk, silindi=False)
    stok_pk = hareket.stok_id
    if request.method == "POST":
        try:
            hareket_servis.hareket_sil(hareket, kullanici=request.user)
            messages.success(request, "Stok hareketi silindi.")
        except hareket_servis.HareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:stok_detay", pk=stok_pk)


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
def cari_ekstresi(request, pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    form, b, s = _tarih_araligi(request)
    eks = ekstre_devirli_servis(cari.muhasebe_kodu, b, s) if cari.muhasebe_kodu else None
    return render(request, "core/cari_ekstresi.html",
                  {"cari": cari, "form": form, "ekstre": eks})


# === FİNANS — Kasa ===
@ekran_gerekli("kasa")
def kasalar(request):
    return render(request, "core/kasa_listesi.html",
                  {"kasalar": finans_servis.aktif_kasalar()})


@ekran_gerekli("kasa")
def kasa_ekle(request):
    if request.method == "POST":
        form = KasaForm(request.POST)
        if form.is_valid():
            try:
                finans_servis.kasa_olustur(
                    ad=form.cleaned_data["ad"],
                    para_birimi=form.cleaned_data["para_birimi"],
                    muhasebe_kodu=form.cleaned_data["muhasebe"].hesap_kodu,
                    kullanici=request.user)
                messages.success(request, "Kasa eklendi.")
                return redirect("core:kasalar")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KasaForm()
    return render(request, "core/kasa_form.html",
                  {"form": form, "baslik": "Yeni Kasa", "iptal_url": reverse("core:kasalar")})


@ekran_gerekli("kasa")
def kasa_duzenle(request, pk):
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)
    if request.method == "POST":
        form = KasaForm(request.POST)
        if form.is_valid():
            try:
                finans_servis.kasa_guncelle(
                    kasa, ad=form.cleaned_data["ad"],
                    para_birimi=form.cleaned_data["para_birimi"],
                    muhasebe_kodu=form.cleaned_data["muhasebe"].hesap_kodu,
                    kullanici=request.user)
                messages.success(request, "Kasa güncellendi.")
                return redirect("core:kasalar")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KasaForm(initial={"ad": kasa.ad, "para_birimi": kasa.para_birimi,
                                 "muhasebe": kasa.muhasebe.hesap_kodu})
    return render(request, "core/kasa_form.html",
                  {"form": form, "baslik": "Kasa Düzenle", "iptal_url": reverse("core:kasalar")})


@ekran_gerekli("kasa")
def kasa_sil(request, pk):
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            finans_servis.kasa_sil(kasa, kullanici=request.user)
            messages.success(request, "Kasa silindi.")
        except finans_servis.FinansHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kasalar")


# === FİNANS — Banka ===
@ekran_gerekli("banka")
def bankalar(request):
    return render(request, "core/banka_listesi.html",
                  {"bankalar": finans_servis.aktif_bankalar()})


@ekran_gerekli("banka")
def banka_hesap_ekle(request):
    if request.method == "POST":
        form = BankaForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.banka_olustur(
                    ad=cd["ad"], banka_adi=cd["banka_adi"], sube=cd["sube"],
                    hesap_no=cd["hesap_no"], iban=cd["iban"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Banka hesabı eklendi.")
                return redirect("core:bankalar")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaForm()
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Yeni Banka Hesabı", "emoji": "🏦",
                   "iptal_url": reverse("core:bankalar")})


@ekran_gerekli("banka")
def banka_hesap_duzenle(request, pk):
    banka = get_object_or_404(Banka, pk=pk, silindi=False)
    if request.method == "POST":
        form = BankaForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.banka_guncelle(
                    banka, ad=cd["ad"], banka_adi=cd["banka_adi"], sube=cd["sube"],
                    hesap_no=cd["hesap_no"], iban=cd["iban"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Banka hesabı güncellendi.")
                return redirect("core:bankalar")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaForm(initial={
            "ad": banka.ad, "banka_adi": banka.banka_adi, "sube": banka.sube,
            "hesap_no": banka.hesap_no, "iban": banka.iban, "para_birimi": banka.para_birimi,
            "muhasebe": banka.muhasebe.hesap_kodu})
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Banka Hesabı Düzenle", "emoji": "🏦",
                   "iptal_url": reverse("core:bankalar")})


@ekran_gerekli("banka")
def banka_hesap_sil(request, pk):
    banka = get_object_or_404(Banka, pk=pk, silindi=False)
    if request.method == "POST":
        finans_servis.banka_sil(banka, kullanici=request.user)
        messages.success(request, "Banka hesabı silindi.")
    return redirect("core:bankalar")


# === FİNANS — Kredi Kartı ===
@ekran_gerekli("kredi_karti")
def kredi_kartlari(request):
    return render(request, "core/kredi_karti_listesi.html",
                  {"kartlar": finans_servis.aktif_kredi_kartlari()})


@ekran_gerekli("kredi_karti")
def kredi_karti_ekle(request):
    if request.method == "POST":
        form = KrediKartiForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_karti_olustur(
                    ad=cd["ad"], banka_adi=cd["banka_adi"], kart_son4=cd["kart_son4"],
                    limit=cd["limit"], kesim_gunu=cd["kesim_gunu"],
                    son_odeme_gunu=cd["son_odeme_gunu"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi kartı eklendi.")
                return redirect("core:kredi_kartlari")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediKartiForm()
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Yeni Kredi Kartı", "emoji": "💳",
                   "iptal_url": reverse("core:kredi_kartlari")})


@ekran_gerekli("kredi_karti")
def kredi_karti_duzenle(request, pk):
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    if request.method == "POST":
        form = KrediKartiForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_karti_guncelle(
                    kart, ad=cd["ad"], banka_adi=cd["banka_adi"], kart_son4=cd["kart_son4"],
                    limit=cd["limit"], kesim_gunu=cd["kesim_gunu"],
                    son_odeme_gunu=cd["son_odeme_gunu"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi kartı güncellendi.")
                return redirect("core:kredi_kartlari")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediKartiForm(initial={
            "ad": kart.ad, "banka_adi": kart.banka_adi, "kart_son4": kart.kart_son4,
            "limit": kart.limit, "kesim_gunu": kart.kesim_gunu,
            "son_odeme_gunu": kart.son_odeme_gunu, "para_birimi": kart.para_birimi,
            "muhasebe": kart.muhasebe.hesap_kodu})
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Kredi Kartı Düzenle", "emoji": "💳",
                   "iptal_url": reverse("core:kredi_kartlari")})


@ekran_gerekli("kredi_karti")
def kredi_karti_sil(request, pk):
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    if request.method == "POST":
        finans_servis.kredi_karti_sil(kart, kullanici=request.user)
        messages.success(request, "Kredi kartı silindi.")
    return redirect("core:kredi_kartlari")


# === FİNANS — Kredi ===
@ekran_gerekli("kredi")
def krediler(request):
    return render(request, "core/kredi_listesi.html",
                  {"krediler": finans_servis.aktif_krediler()})


@ekran_gerekli("kredi")
def kredi_ekle(request):
    if request.method == "POST":
        form = KrediForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_olustur(
                    ad=cd["ad"], banka_adi=cd["banka_adi"], anapara=cd["anapara"],
                    faiz_orani=cd["faiz_orani"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi eklendi.")
                return redirect("core:krediler")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediForm()
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Yeni Kredi", "emoji": "🏛️",
                   "iptal_url": reverse("core:krediler")})


@ekran_gerekli("kredi")
def kredi_duzenle(request, pk):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if request.method == "POST":
        form = KrediForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_guncelle(
                    kredi, ad=cd["ad"], banka_adi=cd["banka_adi"], anapara=cd["anapara"],
                    faiz_orani=cd["faiz_orani"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi güncellendi.")
                return redirect("core:krediler")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediForm(initial={
            "ad": kredi.ad, "banka_adi": kredi.banka_adi, "anapara": kredi.anapara,
            "faiz_orani": kredi.faiz_orani, "para_birimi": kredi.para_birimi,
            "muhasebe": kredi.muhasebe.hesap_kodu})
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Kredi Düzenle", "emoji": "🏛️",
                   "iptal_url": reverse("core:krediler")})


@ekran_gerekli("kredi")
def kredi_sil(request, pk):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if request.method == "POST":
        finans_servis.kredi_sil(kredi, kullanici=request.user)
        messages.success(request, "Kredi silindi.")
    return redirect("core:krediler")


# === FİNANS — Çek / Senet ===
@ekran_gerekli("cek_senet")
def cek_senetler(request):
    return render(request, "core/cek_senet_listesi.html",
                  {"kayitlar": finans_servis.aktif_cek_senetler()})


@ekran_gerekli("cek_senet")
def cek_senet_ekle(request):
    if request.method == "POST":
        form = CekSenetForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.cek_senet_olustur(
                    tip=cd["tip"], yon=cd["yon"], tutar=cd["tutar"], vade=cd["vade"],
                    para_birimi=cd["para_birimi"], kesideci=cd["kesideci"],
                    belge_no=cd["belge_no"], durum=cd["durum"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu,
                    cari_id=(cd["cari"].pk if cd.get("cari") else None), kullanici=request.user)
                messages.success(request, "Çek/Senet eklendi.")
                return redirect("core:cek_senetler")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CekSenetForm()
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Yeni Çek / Senet", "emoji": "📜",
                   "iptal_url": reverse("core:cek_senetler")})


@ekran_gerekli("cek_senet")
def cek_senet_duzenle(request, pk):
    cs = get_object_or_404(CekSenet, pk=pk, silindi=False)
    if request.method == "POST":
        form = CekSenetForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.cek_senet_guncelle(
                    cs, tip=cd["tip"], yon=cd["yon"], tutar=cd["tutar"], vade=cd["vade"],
                    para_birimi=cd["para_birimi"], kesideci=cd["kesideci"],
                    belge_no=cd["belge_no"], durum=cd["durum"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu,
                    cari_id=(cd["cari"].pk if cd.get("cari") else None), kullanici=request.user)
                messages.success(request, "Çek/Senet güncellendi.")
                return redirect("core:cek_senetler")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CekSenetForm(initial={
            "tip": cs.tip, "yon": cs.yon, "tutar": cs.tutar, "vade": cs.vade,
            "para_birimi": cs.para_birimi, "kesideci": cs.kesideci, "belge_no": cs.belge_no,
            "durum": cs.durum, "cari": cs.cari_id, "muhasebe": cs.muhasebe.hesap_kodu})
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Çek / Senet Düzenle", "emoji": "📜",
                   "iptal_url": reverse("core:cek_senetler")})


@ekran_gerekli("cek_senet")
def cek_senet_sil(request, pk):
    cs = get_object_or_404(CekSenet, pk=pk, silindi=False)
    if request.method == "POST":
        finans_servis.cek_senet_sil(cs, kullanici=request.user)
        messages.success(request, "Çek/Senet silindi.")
    return redirect("core:cek_senetler")


@ekran_gerekli("cariler")
def cari_sil(request, pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            cari_servis.cari_sil(cari, kullanici=request.user)
            messages.success(request, f"Cari silindi: {cari.kod}")
        except cari_servis.CariHatasi as e:
            messages.error(request, str(e))
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


# --- AYARLAR > Tanım Listeleri (KDV / Tevkifat oranları) --------------------
def _hesap_kodu(cd, alan="hesap"):
    h = cd.get(alan)
    return h.hesap_kodu if h else ""


@yonetici_gerekli
def tanim_listeleri(request):
    return render(request, "core/tanim_listeleri.html")


@yonetici_gerekli
def kdv_oranlari(request):
    return render(request, "core/kdv_orani_listesi.html",
                  {"kdvler": tanim_servis.aktif_kdv_oranlari()})


@yonetici_gerekli
def kdv_orani_ekle(request):
    if request.method == "POST":
        form = KdvOraniForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.kdv_orani_olustur(
                    aciklama=cd["aciklama"], oran=cd["oran"], sira=cd["sira"],
                    hesap_borc_kodu=_hesap_kodu(cd, "hesap_borc"),
                    hesap_alacak_kodu=_hesap_kodu(cd, "hesap_alacak"),
                    kullanici=request.user)
                messages.success(request, "KDV oranı eklendi.")
                return redirect("core:kdv_oranlari")
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KdvOraniForm()
    return render(request, "core/kdv_orani_form.html",
                  {"form": form, "baslik": "Yeni KDV Oranı"})


@yonetici_gerekli
def kdv_orani_duzenle(request, pk):
    k = get_object_or_404(KdvOrani, pk=pk, silindi=False)
    if request.method == "POST":
        form = KdvOraniForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.kdv_orani_guncelle(
                    k, aciklama=cd["aciklama"], oran=cd["oran"], sira=cd["sira"],
                    hesap_borc_kodu=_hesap_kodu(cd, "hesap_borc"),
                    hesap_alacak_kodu=_hesap_kodu(cd, "hesap_alacak"),
                    kullanici=request.user)
                messages.success(request, "KDV oranı güncellendi.")
                return redirect("core:kdv_oranlari")
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KdvOraniForm(initial={"aciklama": k.aciklama, "oran": k.oran,
                                     "sira": k.sira, "hesap_borc": k.hesap_borc_id,
                                     "hesap_alacak": k.hesap_alacak_id})
    return render(request, "core/kdv_orani_form.html",
                  {"form": form, "baslik": "KDV Oranı Düzenle", "duzenlenen": k})


@yonetici_gerekli
def kdv_orani_sil(request, pk):
    k = get_object_or_404(KdvOrani, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            tanim_servis.kdv_orani_sil(k, kullanici=request.user)
            messages.success(request, "KDV oranı silindi.")
        except tanim_servis.TanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kdv_oranlari")


@yonetici_gerekli
def tevkifat_oranlari(request):
    return render(request, "core/tevkifat_orani_listesi.html",
                  {"tevkifatlar": tanim_servis.aktif_tevkifat_oranlari()})


@yonetici_gerekli
def tevkifat_orani_ekle(request):
    if request.method == "POST":
        form = TevkifatOraniForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.tevkifat_orani_olustur(
                    kod=cd["kod"], pay=cd["pay"], payda=cd["payda"],
                    aciklama=cd["aciklama"], hesap_kodu=_hesap_kodu(cd),
                    kullanici=request.user)
                messages.success(request, "Tevkifat oranı eklendi.")
                return redirect("core:tevkifat_oranlari")
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TevkifatOraniForm()
    return render(request, "core/tevkifat_orani_form.html",
                  {"form": form, "baslik": "Yeni Tevkifat Oranı"})


@yonetici_gerekli
def tevkifat_orani_duzenle(request, pk):
    t = get_object_or_404(TevkifatOrani, pk=pk, silindi=False)
    if request.method == "POST":
        form = TevkifatOraniForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.tevkifat_orani_guncelle(
                    t, kod=cd["kod"], pay=cd["pay"], payda=cd["payda"],
                    aciklama=cd["aciklama"], hesap_kodu=_hesap_kodu(cd),
                    kullanici=request.user)
                messages.success(request, "Tevkifat oranı güncellendi.")
                return redirect("core:tevkifat_oranlari")
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TevkifatOraniForm(initial={"kod": t.kod, "pay": t.pay, "payda": t.payda,
                                          "aciklama": t.aciklama, "hesap": t.hesap_id})
    return render(request, "core/tevkifat_orani_form.html",
                  {"form": form, "baslik": "Tevkifat Oranı Düzenle", "duzenlenen": t})


@yonetici_gerekli
def tevkifat_orani_sil(request, pk):
    t = get_object_or_404(TevkifatOrani, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            tanim_servis.tevkifat_orani_sil(t, kullanici=request.user)
            messages.success(request, "Tevkifat oranı silindi.")
        except tanim_servis.TanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:tevkifat_oranlari")


# === FATURALAR — Alış/Satış faturası (otomatik yevmiye) ===
FaturaSatirFormSet = formset_factory(FaturaSatirForm, extra=0, min_num=1, validate_min=True)


def _fatura_yon_kod(yon):
    return "alis_faturalari" if yon == FaturaTipi.Yon.ALIS else "satis_faturalari"


def _fatura_liste_url(yon):
    return "core:" + _fatura_yon_kod(yon)


def _fatura_ekle_url(yon):
    return ("core:alis_fatura_ekle" if yon == FaturaTipi.Yon.ALIS
            else "core:satis_fatura_ekle")


def _stok_kdv_tevkifat():
    _stoklar = list(Stok.objects.filter(silindi=False).select_related("kdv", "tevkifat"))
    stok_kdv = {str(s.pk): float(s.kdv.oran) if s.kdv_id else 0 for s in _stoklar}
    stok_tevkifat = {str(s.pk): (float(s.tevkifat.pay) / float(s.tevkifat.payda))
                     if (s.tevkifat_id and s.tevkifat.payda) else 0 for s in _stoklar}
    return stok_kdv, stok_tevkifat


def _fatura_listesi(request, yon, baslik):
    faturalar = (fatura_servis.aktif_faturalar().filter(tip__yon=yon)
                 .prefetch_related("satirlar__kdv"))
    sayfa = Paginator(faturalar, 50).get_page(request.GET.get("sayfa"))
    return render(request, "core/fatura_listesi.html",
                  {"faturalar": sayfa, "baslik": baslik,
                   "ekle_url": _fatura_ekle_url(yon)})


@ekran_gerekli("alis_faturalari")
def alis_faturalari(request):
    return _fatura_listesi(request, FaturaTipi.Yon.ALIS, "Alış Faturaları")


@ekran_gerekli("satis_faturalari")
def satis_faturalari(request):
    return _fatura_listesi(request, FaturaTipi.Yon.SATIS, "Satış Faturaları")


def _fatura_ekle(request, yon, baslik):
    if request.method == "POST":
        fform = FaturaForm(request.POST, yon=yon)
        formset = FaturaSatirFormSet(request.POST)
        if fform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk,
                 "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"]}
                for f in formset if f.dolu_mu()
            ]
            try:
                fatura = fatura_servis.fatura_olustur(
                    tip_id=fform.cleaned_data["tip"].pk,
                    cari_id=fform.cleaned_data["cari"].pk,
                    tarih=fform.cleaned_data["tarih"],
                    fatura_no=fform.cleaned_data.get("fatura_no", ""),
                    para_birimi=fform.cleaned_data.get("para_birimi", "TRY"),
                    depo_id=fform.cleaned_data["depo"].pk if fform.cleaned_data.get("depo") else None,
                    satirlar=satirlar,
                    kullanici=request.user,
                )
                mesaj = f"Fatura kaydedildi; fiş {fatura.fis.yil}/{fatura.fis.fis_no} oluştu."
                if fform.cleaned_data.get("depo") is None:
                    mesaj += " (Depo seçilmedi; stok hareketi oluşmadı.)"
                messages.success(request, mesaj)
                return redirect("core:fatura_detay", pk=fatura.pk)
            except fatura_servis.FaturaHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FaturaForm(yon=yon)
        formset = FaturaSatirFormSet()
    stok_kdv, stok_tevkifat = _stok_kdv_tevkifat()
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat, "baslik": baslik,
                   "iptal_url": reverse(_fatura_liste_url(yon))})


@ekran_gerekli("alis_faturalari")
def alis_fatura_ekle(request):
    return _fatura_ekle(request, FaturaTipi.Yon.ALIS, "Yeni Alış Faturası")


@ekran_gerekli("satis_faturalari")
def satis_fatura_ekle(request):
    return _fatura_ekle(request, FaturaTipi.Yon.SATIS, "Yeni Satış Faturası")


FaturaSatirDuzenleFormSet = formset_factory(FaturaSatirForm, extra=0, min_num=1, validate_min=True)


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_duzenle(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    yon = fatura.tip.yon
    if request.method == "POST":
        fform = FaturaForm(request.POST, yon=yon)
        formset = FaturaSatirDuzenleFormSet(request.POST)
        if fform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk,
                 "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"]}
                for f in formset if f.dolu_mu()
            ]
            try:
                fatura_servis.fatura_guncelle(
                    fatura,
                    tip_id=fform.cleaned_data["tip"].pk,
                    cari_id=fform.cleaned_data["cari"].pk,
                    tarih=fform.cleaned_data["tarih"],
                    fatura_no=fform.cleaned_data.get("fatura_no", ""),
                    para_birimi=fform.cleaned_data.get("para_birimi", "TRY"),
                    depo_id=fform.cleaned_data["depo"].pk if fform.cleaned_data.get("depo") else None,
                    satirlar=satirlar,
                    kullanici=request.user,
                )
                mesaj = f"Fatura güncellendi; fiş {fatura.fis.yil}/{fatura.fis.fis_no} yenilendi."
                if fform.cleaned_data.get("depo") is None:
                    mesaj += " (Depo seçilmedi; stok hareketi oluşmadı.)"
                messages.success(request, mesaj)
                return redirect("core:fatura_detay", pk=fatura.pk)
            except fatura_servis.FaturaHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FaturaForm(yon=yon, initial={
            "tip": fatura.tip_id, "cari": fatura.cari_id, "tarih": fatura.tarih,
            "fatura_no": fatura.fatura_no, "para_birimi": fatura.para_birimi,
            "depo": fatura.depo_id})
        ilk = [{"stok": s.stok_id, "miktar": s.miktar, "birim_fiyat": s.birim_fiyat}
               for s in fatura.satirlar.filter(silindi=False).select_related("stok")]
        formset = FaturaSatirDuzenleFormSet(initial=ilk)
    stok_kdv, stok_tevkifat = _stok_kdv_tevkifat()
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat,
                   "baslik": "Fatura Düzenle",
                   "iptal_url": reverse("core:fatura_detay", args=[fatura.pk])})


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_detay(request, pk):
    fatura = get_object_or_404(
        Fatura.objects.select_related("tip", "cari", "fis"), pk=pk)
    satirlar = fatura.satirlar.filter(silindi=False).select_related("stok", "kdv")
    return render(request, "core/fatura_detay.html",
                  {"fatura": fatura, "satirlar": satirlar,
                   "liste_url": _fatura_liste_url(fatura.tip.yon)})


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_iptal_gorunum(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    yon = fatura.tip.yon
    if request.method == "POST":
        fatura_servis.fatura_iptal(fatura, kullanici=request.user)
        messages.success(request, "Fatura ve bağlı fiş iptal edildi.")
    return redirect(_fatura_liste_url(yon))
