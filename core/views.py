"""Fiş giriş/liste/düzenleme/görüntüleme, rapor, kullanıcı yönetimi ve ekran yetkisi görünümleri."""
import calendar
import datetime
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q, Sum
from django.forms import formset_factory
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from core.forms import (
    BilancoTarihForm, BirimForm, CariAktiviteForm, CariBankaForm, CariForm, CariKategoriForm,
    CariSevkAdresiForm,
    BankaForm, BankaHareketForm, BankaHesapForm, BankaIslemForm, BordroBaslikForm, CariCiroForm, CariYetkiliForm, CekHesapAyariForm, CekKalemForm, CekNakitForm, DepoForm, FaturaForm, FaturaSatirForm, FasonKesimForm, FasonSatirForm, FirmaBankaForm, FirmaBilgisiForm, IslemTarihForm,
    FaturaTipiForm, FisForm,
    KasaForm, KasaHareketForm, KategoriForm, KdvOraniForm, KrediForm, KrediKartiForm,
    KrediKartiHareketForm, KrediHareketForm, KrediTaksitForm, KrediTaksitOdemeForm,
    TeklifSiparisForm, TeklifSiparisKalemForm, SatisTeklifBaslikForm, SatisTeklifKalemForm,
    TanimSecenegiForm,
    KullaniciDuzenleForm, KullaniciEkleForm,
    MizanFiltreForm, SatirForm, SehirForm, StokForm, StokHareketForm, TevkifatOraniForm,
    UlkeForm, YemekSayimForm, YemekTakibiFiltreForm,
)
from core.models import (
    Birim, Cari, CariAktivite, CariAktiviteEk, CariBanka, CariKategori, CariSevkAdresi,
    CariYetkili, Depo, EkranYetki, Fatura, FasonKesim, FasonKesimKaydi,
    Banka, BankaHesap, CekBordrosu, CekSenet, FaturaTipi, HesapPlani, Kasa, Kategori, KdvOrani, Kredi, KrediKarti,
    KrediTaksit, Kur, Sehir, Stok, TanimSecenegi, TeklifSiparis, TevkifatOrani, Ulke, YemekSayimi,
    YevmiyeFisi, YevmiyeSatir,
)
from core.moduller import MODULLER
from core.metin import buyuk_harf_tr
from core import gorsel
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
from core.services import teklif_siparis as teklif_siparis_servis
from core.services import depo as depo_servis
from core.services import hareket as hareket_servis
from core.services import finans as finans_servis
from core.services import kasa_hareket as kasa_hareket_servis
from core.services import banka_hareket as banka_hareket_servis
from core.services import kredi_karti_hareket as kredi_karti_hareket_servis
from core.services import kredi_hareket as kredi_hareket_servis
from core.services import cek as cek_servis
from core.services import firma as firma_servis
from core.services import fason as fason_servis
from core.services import yemek_takibi as yemek_takibi_servis
from core.yetki import (
    ekran_gerekli, ekran_gerekli_herhangi, ekran_gorebilir, kullanici_telefon, yonetici_gerekli,
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


@ekran_gerekli("fis_listesi")
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
    if fis.kaynak == YevmiyeFisi.Kaynak.KASA:
        messages.info(request, "Bu fiş bir kasa hareketinden oluştu; düzenlenemez. "
                               "Gerekirse hareketi iptal edip yeniden girin.")
        return (redirect("core:kasa_detay", pk=fis.kasa_id) if fis.kasa_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.BANKA:
        messages.info(request, "Bu fiş bir banka hareketinden oluştu; düzenlenemez. "
                               "Gerekirse hareketi iptal edip yeniden girin.")
        return (redirect("core:banka_hesap_detay", pk=fis.banka_hesap_id) if fis.banka_hesap_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.CEK_SENET:
        messages.info(request, "Bu fiş bir çek/senet bordrosundan oluştu; düzenlenemez. "
                               "Gerekirse bordroyu geri alıp yeniden girin.")
        return (redirect("core:cek_bordro_detay", pk=fis.cek_bordrosu_id) if fis.cek_bordrosu_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI_KARTI:
        messages.info(request, "Bu fiş bir kredi kartı hareketinden oluştu; düzenlenemez. "
                               "Gerekirse hareketi iptal edip yeniden girin.")
        return (redirect("core:kredi_karti_detay", pk=fis.kredi_karti_id) if fis.kredi_karti_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI:
        messages.info(request, "Bu fiş bir kredi hareketinden oluştu; düzenlenemez. "
                               "Gerekirse hareketi iptal edip yeniden girin.")
        return (redirect("core:kredi_detay", pk=fis.kredi_id) if fis.kredi_id
                else redirect("core:fis_detay", pk=fis.pk))

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
    if fis.kaynak == YevmiyeFisi.Kaynak.KASA and not fis.silindi and fis.kasa_id:
        messages.info(request, "Bu fiş bir kasa hareketinden oluştu; iptal için kasa detayını kullanın.")
        return redirect("core:kasa_detay", pk=fis.kasa_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.BANKA and not fis.silindi and fis.banka_hesap_id:
        messages.info(request, "Bu fiş bir banka hareketinden oluştu; iptal için banka hesabı detayını kullanın.")
        return redirect("core:banka_hesap_detay", pk=fis.banka_hesap_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.CEK_SENET and not fis.silindi and fis.cek_bordrosu_id:
        messages.info(request, "Bu fiş bir çek/senet bordrosundan oluştu; iptal için bordro detayını kullanın.")
        return redirect("core:cek_bordro_detay", pk=fis.cek_bordrosu_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI_KARTI and not fis.silindi and fis.kredi_karti_id:
        messages.info(request, "Bu fiş bir kredi kartı hareketinden oluştu; iptal için kredi kartı detayını kullanın.")
        return redirect("core:kredi_karti_detay", pk=fis.kredi_karti_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI and not fis.silindi and fis.kredi_id:
        messages.info(request, "Bu fiş bir kredi hareketinden oluştu; iptal için kredi detayını kullanın.")
        return redirect("core:kredi_detay", pk=fis.kredi_id)
    if request.method == "POST":
        if fis.silindi:
            messages.success(request, f"Fiş zaten iptal: {fis.yil}/{fis.fis_no}")
        else:
            fis_iptal(fis, kullanici=request.user)
            messages.success(request, f"Fiş iptal edildi: {fis.yil}/{fis.fis_no}")
    return redirect("core:fis_detay", pk=fis.pk)


@ekran_gerekli("fis_listesi")
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
        kayitlar = EkranYetki.objects.filter(kullanici=secili)
        aktif_kodlar = set(
            kayitlar.filter(silindi=False).values_list("ekran_kod", flat=True))
        simdi = timezone.now()
        # Artık seçilmeyenler: SOFT-DELETE (fiziksel silme yok — CLAUDE.md audit kuralı).
        kaldirilanlar = aktif_kodlar - secilenler
        if kaldirilanlar:
            kayitlar.filter(silindi=False, ekran_kod__in=kaldirilanlar).update(
                silindi=True, silindi_at=simdi, updated_by=request.user, updated_at=simdi)
        # Yeni seçilenler: daha önce soft-delete edilmişse CANLANDIR, hiç yoksa oluştur.
        yeni_secilenler = secilenler - aktif_kodlar
        if yeni_secilenler:
            canlanacaklar = set(
                kayitlar.filter(silindi=True, ekran_kod__in=yeni_secilenler)
                .values_list("ekran_kod", flat=True))
            if canlanacaklar:
                kayitlar.filter(ekran_kod__in=canlanacaklar).update(
                    silindi=False, silindi_at=None, updated_by=request.user,
                    updated_at=simdi)
            olusturulacaklar = yeni_secilenler - canlanacaklar
            EkranYetki.objects.bulk_create([
                EkranYetki(kullanici=secili, ekran_kod=k,
                          created_by=request.user, updated_by=request.user)
                for k in sorted(olusturulacaklar)
            ])
        ad = secili.get_full_name() or secili.username
        messages.success(request, f"{ad} için ekran yetkileri güncellendi.")
        return redirect(f"{reverse('core:kullanici_yetkileri')}?kullanici={secili.pk}")

    mevcut = set()
    if secili:
        mevcut = set(
            EkranYetki.objects.filter(kullanici=secili, silindi=False)
            .values_list("ekran_kod", flat=True)
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
    ara = (request.GET.get("ara") or "").strip()
    qs = stok_servis.aktif_stoklar()
    if ara:
        buyuk = buyuk_harf_tr(ara)
        qs = qs.filter(
            Q(kod__icontains=ara) | Q(ad__contains=buyuk)
            | Q(kategori__ad__contains=buyuk) | Q(kategori__ust__ad__contains=buyuk))
    return render(request, "core/stok_listesi.html", {"stoklar": qs, "ara": ara})


@ekran_gerekli("stoklar")
def stok_ekle(request):
    if request.method == "POST":
        form = StokForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                gorsel_dosya = (gorsel.kucult_webp(cd["gorsel"], max_kenar=1600, kalite=80,
                                                   ad="stok") if cd.get("gorsel") else None)
                s = stok_servis.stok_olustur(
                    ad=cd["ad"], kategori_id=cd["kategori"].pk,
                    uretim_birimi_id=cd["uretim_birimi"].pk,
                    fatura_birimi_id=cd["fatura_birimi"].pk,
                    cevirici=cd["cevirici"],
                    kdv_id=cd["kdv"].pk if cd.get("kdv") else None,
                    tevkifat_id=cd["tevkifat"].pk if cd.get("tevkifat") else None,
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci_id=cd["tedarikci"].pk if cd.get("tedarikci") else None,
                    alis_fiyati=cd.get("alis_fiyati"),
                    alis_fiyati_pb=cd.get("alis_fiyati_pb"),
                    satinalma_urunu=cd.get("satinalma_urunu"),
                    uretim_urunu=cd.get("uretim_urunu"),
                    satis_urunu=cd.get("satis_urunu"),
                    model_kodu=cd.get("model_kodu"), ad_en=cd.get("ad_en"),
                    hs_kodu=cd.get("hs_kodu"), materyal=cd.get("materyal"),
                    materyal_en=cd.get("materyal_en"),
                    basamak_sayisi=cd.get("basamak_sayisi"),
                    yukseklik=cd.get("yukseklik"), acik_derinlik=cd.get("acik_derinlik"),
                    taban_genisligi=cd.get("taban_genisligi"),
                    kapali_boy=cd.get("kapali_boy"), agirlik=cd.get("agirlik"),
                    azami_yuk=cd.get("azami_yuk"), cbm=cd.get("cbm"),
                    yukleme_20dc=cd.get("yukleme_20dc"),
                    yukleme_40hq=cd.get("yukleme_40hq"),
                    yukleme_tir=cd.get("yukleme_tir"),
                    gorsel=gorsel_dosya,
                    fiyat_try=cd.get("fiyat_try"), fiyat_usd=cd.get("fiyat_usd"),
                    fiyat_eur=cd.get("fiyat_eur"), fiyat_gbp=cd.get("fiyat_gbp"),
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
        form = StokForm(request.POST, request.FILES, duzenle=True)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                gorsel_dosya = (gorsel.kucult_webp(cd["gorsel"], max_kenar=1600, kalite=80,
                                                   ad="stok") if cd.get("gorsel") else None)
                stok_servis.stok_guncelle(
                    stok, ad=cd["ad"],
                    uretim_birimi_id=cd["uretim_birimi"].pk,
                    fatura_birimi_id=cd["fatura_birimi"].pk,
                    cevirici=cd["cevirici"],
                    kdv_id=cd["kdv"].pk if cd.get("kdv") else None,
                    tevkifat_id=cd["tevkifat"].pk if cd.get("tevkifat") else None,
                    kritik_stok=cd.get("kritik_stok"),
                    tedarikci_id=cd["tedarikci"].pk if cd.get("tedarikci") else None,
                    alis_fiyati=cd.get("alis_fiyati"),
                    alis_fiyati_pb=cd.get("alis_fiyati_pb"),
                    satinalma_urunu=cd.get("satinalma_urunu"),
                    uretim_urunu=cd.get("uretim_urunu"),
                    satis_urunu=cd.get("satis_urunu"),
                    model_kodu=cd.get("model_kodu"), ad_en=cd.get("ad_en"),
                    hs_kodu=cd.get("hs_kodu"), materyal=cd.get("materyal"),
                    materyal_en=cd.get("materyal_en"),
                    basamak_sayisi=cd.get("basamak_sayisi"),
                    yukseklik=cd.get("yukseklik"), acik_derinlik=cd.get("acik_derinlik"),
                    taban_genisligi=cd.get("taban_genisligi"),
                    kapali_boy=cd.get("kapali_boy"), agirlik=cd.get("agirlik"),
                    azami_yuk=cd.get("azami_yuk"), cbm=cd.get("cbm"),
                    yukleme_20dc=cd.get("yukleme_20dc"),
                    yukleme_40hq=cd.get("yukleme_40hq"),
                    yukleme_tir=cd.get("yukleme_tir"),
                    gorsel=gorsel_dosya,
                    fiyat_try=cd.get("fiyat_try"), fiyat_usd=cd.get("fiyat_usd"),
                    fiyat_eur=cd.get("fiyat_eur"), fiyat_gbp=cd.get("fiyat_gbp"),
                    kullanici=request.user)
                messages.success(request, "Stok güncellendi.")
                return redirect("core:stoklar")
            except stok_servis.StokHatasi as e:
                form.add_error(None, str(e))
    else:
        fiyatlar = {f.para_birimi: f.fiyat for f in stok.fiyatlar.filter(silindi=False)}
        form = StokForm(duzenle=True, initial={
            "ad": stok.ad, "uretim_birimi": stok.uretim_birimi_id,
            "fatura_birimi": stok.fatura_birimi_id, "cevirici": stok.cevirici,
            "kdv": stok.kdv_id, "tevkifat": stok.tevkifat_id,
            "kritik_stok": stok.kritik_stok, "tedarikci": stok.tedarikci_id,
            "alis_fiyati": stok.alis_fiyati, "alis_fiyati_pb": stok.alis_fiyati_pb,
            "satinalma_urunu": stok.satinalma_urunu, "uretim_urunu": stok.uretim_urunu,
            "satis_urunu": stok.satis_urunu, "model_kodu": stok.model_kodu,
            "ad_en": stok.ad_en, "hs_kodu": stok.hs_kodu,
            "materyal": stok.materyal, "materyal_en": stok.materyal_en,
            "basamak_sayisi": stok.basamak_sayisi,
            "yukseklik": stok.yukseklik, "acik_derinlik": stok.acik_derinlik,
            "taban_genisligi": stok.taban_genisligi, "kapali_boy": stok.kapali_boy,
            "agirlik": stok.agirlik, "azami_yuk": stok.azami_yuk, "cbm": stok.cbm,
            "yukleme_20dc": stok.yukleme_20dc, "yukleme_40hq": stok.yukleme_40hq,
            "yukleme_tir": stok.yukleme_tir,
            "fiyat_try": fiyatlar.get("TRY"), "fiyat_usd": fiyatlar.get("USD"),
            "fiyat_eur": fiyatlar.get("EUR"), "fiyat_gbp": fiyatlar.get("GBP")})
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
def stok_kopyala(request, pk):
    stok = get_object_or_404(Stok, pk=pk, silindi=False)
    if request.method == "POST":
        yeni = stok_servis.stok_kopyala(stok, kullanici=request.user)
        messages.success(request, f"Stok kopyalandı: {yeni.kod} — {yeni.ad}")
        return redirect("core:stok_detay", pk=yeni.pk)
    return redirect("core:stok_detay", pk=stok.pk)


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
        "fiyatlar": stok.fiyatlar.filter(silindi=False).order_by("para_birimi"),
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
    alt_qs = CariKategori.objects.filter(silindi=False).select_related("ust").order_by("kod")
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
        web=cd["web"], ilgili_kisi=cd["ilgili_kisi"], kep_adresi=cd["kep_adresi"],
        ulke_id=g(cd["ulke"]), sehir_id=g(cd["sehir"]), adres=cd["adres"],
        para_birimi=cd["para_birimi"], kredi_limiti=cd["kredi_limiti"],
        iskonto_yuzdesi=cd["iskonto_yuzdesi"], notlar=cd["notlar"])


@ekran_gerekli("cariler")
def cariler(request):
    ara = (request.GET.get("ara") or "").strip()
    kategori_id = request.GET.get("kategori") or ""
    sehir_id = request.GET.get("sehir") or ""
    ulke_id = request.GET.get("ulke") or ""
    qs = cari_servis.aktif_cariler()
    if ara:
        qs = qs.filter(
            Q(unvan__contains=buyuk_harf_tr(ara)) | Q(kod__contains=ara)
            | Q(vkn_tckn__contains=ara) | Q(tax_id__contains=ara))
    if kategori_id:
        qs = qs.filter(kategori_id=kategori_id)
    if sehir_id:
        qs = qs.filter(sehir_id=sehir_id)
    if ulke_id:
        qs = qs.filter(ulke_id=ulke_id)

    # Filtre seçenekleri yalnız en az bir cari'de fiilen kullanılanlardan oluşur
    # (tüm hesap planı/lokasyon master verisini değil, sayfadaki gerçek veriyi yansıtır).
    tumu = cari_servis.aktif_cariler()
    kategoriler = CariKategori.objects.filter(
        silindi=False, pk__in=tumu.exclude(kategori=None).values("kategori_id")
    ).order_by("kod")
    sehirler = Sehir.objects.filter(
        silindi=False, pk__in=tumu.exclude(sehir=None).values("sehir_id")
    ).order_by("ad")
    ulkeler = Ulke.objects.filter(
        silindi=False, pk__in=tumu.exclude(ulke=None).values("ulke_id")
    ).order_by("ad")

    return render(request, "core/cari_listesi.html", {
        "cariler": qs, "ara": ara,
        "kategoriler": kategoriler, "sehirler": sehirler, "ulkeler": ulkeler,
        "secili_kategori": kategori_id, "secili_sehir": sehir_id, "secili_ulke": ulke_id,
    })


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
        turkiye = Ulke.objects.filter(silindi=False, kod="TR").first()
        form = CariForm(initial={"ulke": turkiye})
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
            "eposta": cari.eposta, "web": cari.web, "ilgili_kisi": cari.ilgili_kisi,
            "kep_adresi": cari.kep_adresi,
            "ulke": cari.ulke_id, "sehir": cari.sehir_id, "adres": cari.adres,
            "para_birimi": cari.para_birimi, "kredi_limiti": cari.kredi_limiti,
            "iskonto_yuzdesi": cari.iskonto_yuzdesi, "notlar": cari.notlar})
    return render(request, "core/cari_form.html",
                  {"form": form, "baslik": "Cari Düzenle", "duzenlenen": cari})


@ekran_gerekli("cariler")
def cari_detay(request, pk):
    cari = get_object_or_404(
        Cari.objects.select_related("kategori", "kategori__ust", "ulke", "sehir",
                                    "created_by", "updated_by"),
        pk=pk, silindi=False)
    return render(request, "core/cari_detay.html", {
        "cari": cari,
        "bankalar": cari_servis.aktif_bankalar(cari),
        "yetkililer": cari_servis.aktif_yetkililer(cari),
        "sevk_adresleri": cari_servis.aktif_sevk_adresleri(cari),
        "aktiviteler": cari_servis.aktif_aktiviteler(cari)})


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
        form = KasaForm(request.POST, mevcut_hesap=kasa.muhasebe.hesap_kodu)
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
                                 "muhasebe": kasa.muhasebe.hesap_kodu},
                        mevcut_hesap=kasa.muhasebe.hesap_kodu)
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


# Kasa hareket tipleri (Slice 2'de tipe göre otomatik dengeli fiş üretilecek).
def _son_bir_ay(bugun):
    """bugün − 1 takvim ayı (ayın günü taşarsa o ayın son gününe kırpılır)."""
    ay = bugun.month - 1 or 12
    yil = bugun.year - (1 if bugun.month == 1 else 0)
    gun = min(bugun.day, calendar.monthrange(yil, ay)[1])
    return datetime.date(yil, ay, gun)


@ekran_gerekli("kasa")
def kasa_detay(request, pk):
    """Kasa detayı: lacivert başlık + 5 hareket aksiyonu + kasa ekstresi
    (bağlı muhasebe hesabının devirli ekstresi). Tarih + açıklama filtreli;
    varsayılan dönem SON 1 AY. Açıklama filtresi yürüyen bakiyeyi bozmaz:
    ekstre tüm dönem için hesaplanır, satırlar yalnız gösterimde süzülür."""
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)

    bugun = timezone.localdate()
    form = MizanFiltreForm(request.GET) if request.GET else None
    if form and form.is_valid():
        b, s = form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    else:
        b, s = _son_bir_ay(bugun), bugun
        form = MizanFiltreForm(initial={"baslangic": b, "bitis": s})

    ekstre = (ekstre_devirli_servis(kasa.muhasebe.hesap_kodu, b, s)
              if kasa.muhasebe_id else None)

    aciklama = (request.GET.get("aciklama") or "").strip()
    if ekstre and aciklama:
        ara = buyuk_harf_tr(aciklama)
        satirlar = [r for r in ekstre.satirlar
                    if ara in buyuk_harf_tr(r.fis_aciklama or "")
                    or ara in buyuk_harf_tr(r.satir_aciklama or "")]
    else:
        satirlar = ekstre.satirlar if ekstre else []
    satirlar = list(reversed(satirlar))   # ekstre yeni tarihten eskiye (yürüyen bakiye değişmez)

    # Bu kasanın hareketi olan (kaynak=KASA) fişler -> ekstrede İptal aksiyonu için.
    kasa_fis_pks = set(YevmiyeFisi.objects.filter(
        kasa=kasa, kaynak=YevmiyeFisi.Kaynak.KASA, silindi=False
    ).values_list("pk", flat=True))

    return render(request, "core/kasa_detay.html",
                  {"kasa": kasa, "form": form, "ekstre": ekstre,
                   "satirlar": satirlar, "aciklama": aciklama,
                   "kasa_fis_pks": kasa_fis_pks})


def _kasa_hareket_form(request, kasa, tip):
    """Tipe göre kasa hareketi formu (GET) / kaydı (POST) → otomatik dengeli fiş."""
    tan = kasa_hareket_servis.HAREKET[tip]
    if tan["karsi"] == "kasa" and not Kasa.objects.filter(
            silindi=False).exclude(pk=kasa.pk).exists():
        messages.error(request, "Virman için en az iki kasa tanımlı olmalı.")
        return redirect("core:kasa_detay", pk=kasa.pk)
    if tan["karsi"] == "banka" and not BankaHesap.objects.filter(silindi=False).exists():
        messages.error(request, "Önce FİNANS > Banka'dan bir banka hesabı tanımlayın.")
        return redirect("core:kasa_detay", pk=kasa.pk)
    if request.method == "POST":
        form = KasaHareketForm(request.POST, tip=tip, kasa=kasa)
        if form.is_valid():
            try:
                fis = kasa_hareket_servis.hareket_olustur(
                    kasa=kasa, tip=tip, karsi=form.cleaned_data["karsi"],
                    tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user)
                messages.success(request, f"{tan['ad']} kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:kasa_detay", pk=kasa.pk)
            except kasa_hareket_servis.KasaHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KasaHareketForm(tip=tip, kasa=kasa)
    return render(request, "core/kasa_hareket_form.html",
                  {"kasa": kasa, "form": form, "tip": tip, "tan": tan})


@ekran_gerekli("kasa")
def kasa_hareket_ekle(request, pk, tip):
    """Kasa hareketi girişi — 5 tip de tipe göre form + otomatik dengeli fiş (kaynak=KASA)."""
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)
    if tip not in kasa_hareket_servis.HAREKET:
        messages.error(request, "Geçersiz hareket tipi.")
        return redirect("core:kasa_detay", pk=kasa.pk)
    return _kasa_hareket_form(request, kasa, tip)


@ekran_gerekli("kasa")
def kasa_hareket_iptal(request, pk, fis_pk):
    """Kasa hareketi (kaynak=KASA fiş) iptali — kasa detayından (ham fiş ekranı kilitli)."""
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    if request.method == "POST":
        try:
            kasa_hareket_servis.hareket_iptal(fis=fis, kasa=kasa, kullanici=request.user)
            messages.success(request, f"Hareket iptal edildi: fiş {fis.yil}/{fis.fis_no}.")
        except kasa_hareket_servis.KasaHareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kasa_detay", pk=kasa.pk)


@ekran_gerekli("banka")
def banka_hesap_detay(request, pk):
    """Banka hesabı detayı: lacivert başlık + 5 hareket aksiyonu + hesap ekstresi
    (bağlı muhasebe hesabının devirli ekstresi). Tarih + açıklama filtreli;
    varsayılan dönem SON 1 AY. Kasa detayıyla aynı desen, ekstre yeni→eski."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)

    bugun = timezone.localdate()
    form = MizanFiltreForm(request.GET) if request.GET else None
    if form and form.is_valid():
        b, s = form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    else:
        b, s = _son_bir_ay(bugun), bugun
        form = MizanFiltreForm(initial={"baslangic": b, "bitis": s})

    ekstre = (ekstre_devirli_servis(hesap.muhasebe.hesap_kodu, b, s)
              if hesap.muhasebe_id else None)

    aciklama = (request.GET.get("aciklama") or "").strip()
    if ekstre and aciklama:
        ara = buyuk_harf_tr(aciklama)
        satirlar = [r for r in ekstre.satirlar
                    if ara in buyuk_harf_tr(r.fis_aciklama or "")
                    or ara in buyuk_harf_tr(r.satir_aciklama or "")]
    else:
        satirlar = ekstre.satirlar if ekstre else []
    satirlar = list(reversed(satirlar))   # ekstre yeni tarihten eskiye (yürüyen bakiye değişmez)

    banka_fis_pks = set(YevmiyeFisi.objects.filter(
        banka_hesap=hesap, kaynak=YevmiyeFisi.Kaynak.BANKA, silindi=False
    ).values_list("pk", flat=True))

    return render(request, "core/banka_hesap_detay.html",
                  {"hesap": hesap, "form": form, "ekstre": ekstre,
                   "satirlar": satirlar, "aciklama": aciklama,
                   "banka_fis_pks": banka_fis_pks})


def _banka_hareket_form(request, hesap, tip):
    """Tipe göre banka hesabı hareketi formu (GET) / kaydı (POST) → otomatik dengeli fiş."""
    tan = banka_hareket_servis.HAREKET[tip]
    if tan["karsi"] == "banka" and not BankaHesap.objects.filter(
            silindi=False).exclude(pk=hesap.pk).exists():
        messages.error(request, "Virman için en az iki banka hesabı tanımlı olmalı.")
        return redirect("core:banka_hesap_detay", pk=hesap.pk)
    if tan["karsi"] == "kasa" and not Kasa.objects.filter(silindi=False).exists():
        messages.error(request, "Önce FİNANS > Kasa'dan bir kasa tanımlayın.")
        return redirect("core:banka_hesap_detay", pk=hesap.pk)
    if request.method == "POST":
        form = BankaHareketForm(request.POST, tip=tip, banka_hesap=hesap)
        if form.is_valid():
            try:
                fis = banka_hareket_servis.hareket_olustur(
                    banka_hesap=hesap, tip=tip, karsi=form.cleaned_data["karsi"],
                    tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user)
                messages.success(request, f"{tan['ad']} kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:banka_hesap_detay", pk=hesap.pk)
            except banka_hareket_servis.BankaHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaHareketForm(tip=tip, banka_hesap=hesap)
    return render(request, "core/banka_hareket_form.html",
                  {"hesap": hesap, "form": form, "tip": tip, "tan": tan})


@ekran_gerekli("banka")
def banka_hareket_ekle(request, pk, tip):
    """Banka hesabı hareketi — 5 tip de tipe göre form + otomatik dengeli fiş (kaynak=BANKA)."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    if tip not in banka_hareket_servis.HAREKET:
        messages.error(request, "Geçersiz hareket tipi.")
        return redirect("core:banka_hesap_detay", pk=hesap.pk)
    return _banka_hareket_form(request, hesap, tip)


@ekran_gerekli("banka")
def banka_hareket_iptal(request, pk, fis_pk):
    """Banka hareketi (kaynak=BANKA fiş) iptali — hesap detayından (ham fiş ekranı kilitli)."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    if request.method == "POST":
        try:
            banka_hareket_servis.hareket_iptal(fis=fis, banka_hesap=hesap, kullanici=request.user)
            messages.success(request, f"Hareket iptal edildi: fiş {fis.yil}/{fis.fis_no}.")
        except banka_hareket_servis.BankaHareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:banka_hesap_detay", pk=hesap.pk)


# === FİNANS — Banka (kurum) + bağlı hesaplar (master-detail) ===
@ekran_gerekli("banka")
def bankalar(request):
    return render(request, "core/banka_listesi.html",
                  {"bankalar": finans_servis.aktif_bankalar()})


@ekran_gerekli("banka")
def banka_detay(request, pk):
    banka = get_object_or_404(Banka, pk=pk, silindi=False)
    return render(request, "core/banka_detay.html",
                  {"banka": banka, "hesaplar": finans_servis.banka_hesaplari(banka)})


@ekran_gerekli("banka")
def banka_kurum_ekle(request):
    if request.method == "POST":
        form = BankaForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                logo = gorsel.kucult_webp(cd["logo"], ad="banka") if cd.get("logo") else None
                banka = finans_servis.banka_olustur(
                    ad=cd["ad"], kisa_ad=cd["kisa_ad"], sube=cd["sube"],
                    swift_kod=cd["swift_kod"], musteri_no=cd["musteri_no"],
                    adres=cd["adres"], logo=logo, kullanici=request.user)
                messages.success(request, "Banka eklendi. Şimdi hesap ekleyebilirsiniz.")
                return redirect("core:banka_detay", pk=banka.pk)
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaForm()
    return render(request, "core/banka_form.html",
                  {"form": form, "banka": None, "baslik": "Yeni Banka",
                   "iptal_url": reverse("core:bankalar")})


@ekran_gerekli("banka")
def banka_kurum_duzenle(request, pk):
    banka = get_object_or_404(Banka, pk=pk, silindi=False)
    if request.method == "POST":
        form = BankaForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                logo = gorsel.kucult_webp(cd["logo"], ad="banka") if cd.get("logo") else None
                finans_servis.banka_guncelle(
                    banka, ad=cd["ad"], kisa_ad=cd["kisa_ad"], sube=cd["sube"],
                    swift_kod=cd["swift_kod"], musteri_no=cd["musteri_no"],
                    adres=cd["adres"], logo=logo, kullanici=request.user)
                messages.success(request, "Banka güncellendi.")
                return redirect("core:banka_detay", pk=banka.pk)
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaForm(initial={
            "ad": banka.ad, "kisa_ad": banka.kisa_ad, "sube": banka.sube,
            "swift_kod": banka.swift_kod, "musteri_no": banka.musteri_no,
            "adres": banka.adres})
    return render(request, "core/banka_form.html",
                  {"form": form, "banka": banka, "baslik": "Banka Düzenle",
                   "iptal_url": reverse("core:banka_detay", args=[banka.pk])})


@ekran_gerekli("banka")
def banka_kurum_sil(request, pk):
    banka = get_object_or_404(Banka, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            finans_servis.banka_sil(banka, kullanici=request.user)
            messages.success(request, "Banka silindi.")
        except finans_servis.FinansHatasi as e:
            messages.error(request, str(e))
            return redirect("core:banka_detay", pk=banka.pk)
    return redirect("core:bankalar")


@ekran_gerekli("banka")
def banka_hesap_ekle(request, banka_pk):
    banka = get_object_or_404(Banka, pk=banka_pk, silindi=False)
    if request.method == "POST":
        form = BankaHesapForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.banka_hesap_olustur(
                    banka=banka, ad=cd["ad"], hesap_no=cd["hesap_no"], iban=cd["iban"],
                    para_birimi=cd["para_birimi"], muhasebe_kodu=cd["muhasebe"].hesap_kodu,
                    kullanici=request.user)
                messages.success(request, "Hesap eklendi.")
                return redirect("core:banka_detay", pk=banka.pk)
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaHesapForm()
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Yeni Hesap — " + banka.ad, "emoji": "🏦",
                   "iptal_url": reverse("core:banka_detay", args=[banka.pk])})


@ekran_gerekli("banka")
def banka_hesap_duzenle(request, pk):
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    if request.method == "POST":
        form = BankaHesapForm(request.POST, mevcut_hesap=hesap.muhasebe.hesap_kodu)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.banka_hesap_guncelle(
                    hesap, ad=cd["ad"], hesap_no=cd["hesap_no"], iban=cd["iban"],
                    para_birimi=cd["para_birimi"], muhasebe_kodu=cd["muhasebe"].hesap_kodu,
                    kullanici=request.user)
                messages.success(request, "Hesap güncellendi.")
                return redirect("core:banka_detay", pk=hesap.banka_id)
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaHesapForm(initial={
            "ad": hesap.ad, "hesap_no": hesap.hesap_no, "iban": hesap.iban,
            "para_birimi": hesap.para_birimi, "muhasebe": hesap.muhasebe.hesap_kodu},
            mevcut_hesap=hesap.muhasebe.hesap_kodu)
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Hesap Düzenle", "emoji": "🏦",
                   "iptal_url": reverse("core:banka_detay", args=[hesap.banka_id])})


@ekran_gerekli("banka")
def banka_hesap_sil(request, pk):
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    banka_pk = hesap.banka_id
    if request.method == "POST":
        try:
            finans_servis.banka_hesap_sil(hesap, kullanici=request.user)
            messages.success(request, "Hesap silindi.")
        except finans_servis.FinansHatasi as e:
            messages.error(request, str(e))
    return redirect("core:banka_detay", pk=banka_pk)


# === TEKLİF & SİPARİŞ — Satınalma/Satış Teklifi ve Siparişi (yevmiye/stok üretmez) ===
_TS_EKRAN = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS): "satinalma_teklifleri",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS): "satinalma_siparisleri",
    (TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS): "satinalma_irsaliyeleri",
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS): "satis_teklifleri",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "satis_siparisleri",
}
_TS_EKLE = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS): "satinalma_teklif_ekle",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS): "satinalma_siparis_ekle",
    (TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS): "satinalma_irsaliye_ekle",
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS): "satis_teklif_ekle",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "satis_siparis_ekle",
}
_TS_EMOJI = {
    "satinalma_teklifleri": "📥", "satinalma_siparisleri": "🛒",
    "satinalma_irsaliyeleri": "🚚", "satis_teklifleri": "📤", "satis_siparisleri": "📦",
}
TeklifSiparisKalemFormSet = formset_factory(
    TeklifSiparisKalemForm, extra=0, min_num=1, validate_min=True)
# Satış Teklifi: satır sayısı GET'te satış ürünü kataloğunun boyutuna sabitlenir (bkz.
# satis_teklif_ekle) — min_num burada 0 (formset başlangıçta zaten dolu; "hiç ürün yok"
# durumu ayrıca view'de kontrol edilir).
SatisTeklifKalemFormSet = formset_factory(SatisTeklifKalemForm, extra=0)


def _ts_liste(request, belge_tur, yon, baslik, emoji):
    ara = (request.GET.get("ara") or "").strip()
    # "donustu" rozeti yalnız TEKLİF/SİPARİŞ için anlamlı (bir sonraki belgeye kaynak_teklif/
    # kaynak_siparis self-FK'sıyla dönüşür). İRSALİYE→Fatura dönüşümü zaten şablonda ayrı
    # (k.fatura_id — "🧾 Faturaya Dönüştü") gösteriliyor, burada tekrar hesaplanmaz.
    donustu_etiket = None
    if belge_tur == TeklifSiparis.BelgeTur.SIPARIS:
        donusen_var = TeklifSiparis.objects.filter(kaynak_siparis=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 İrsaliyeye Dönüştü"
    elif belge_tur == TeklifSiparis.BelgeTur.TEKLIF:
        donusen_var = TeklifSiparis.objects.filter(kaynak_teklif=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 Siparişe Dönüştü"
    else:
        donusen_var = TeklifSiparis.objects.filter(pk=-1)   # her zaman boş — geçerli pk asla negatif değil
    kayitlar = (teklif_siparis_servis.aktif_teklif_siparisler(belge_tur, yon)
                .annotate(donustu=Exists(donusen_var))
                .annotate(kalem_sayisi=Count("kalemler", filter=Q(kalemler__silindi=False)))
                .prefetch_related("kalemler__kdv", "kalemler__tevkifat"))
    if ara:
        buyuk = buyuk_harf_tr(ara)
        kayitlar = kayitlar.filter(
            Q(cari__unvan__contains=buyuk) | Q(cari__kod__icontains=ara)
            | Q(belge_no__icontains=ara))
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    return render(request, "core/teklif_siparis_listesi.html",
                  {"kayitlar": sayfa, "baslik": baslik, "emoji": emoji, "ara": ara,
                   "donustu_etiket": donustu_etiket,
                   "ekle_url": "core:" + _TS_EKLE[(belge_tur, yon)]})


@ekran_gerekli("satinalma_teklifleri")
def satinalma_teklifleri(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS,
                     "Satınalma Teklifleri", "📥")


@ekran_gerekli("satinalma_siparisleri")
def satinalma_siparisleri(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS,
                     "Satınalma Siparişleri", "🛒")


@ekran_gerekli("satinalma_irsaliyeleri")
def satinalma_irsaliyeleri(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS,
                     "Satınalma İrsaliyeleri", "🚚")


@ekran_gerekli("satis_teklifleri")
def satis_teklifleri(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS,
                     "Satış Teklifleri", "📤")


@ekran_gerekli("satis_siparisleri")
def satis_siparisleri(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS,
                     "Satış Siparişleri", "📦")


def _stok_meta():
    """Kalem satırı JS'i için stok başına KDV oranı + tevkifat oranı + üretim/fatura
    birim çevirisi + alış fiyatı (varsa Birim Fiyat'a otomatik öneri için)."""
    return {str(s.pk): {
        "kdv": float(s.kdv.oran) if s.kdv_id else 0,
        "tevkifat": (float(s.tevkifat.pay) / float(s.tevkifat.payda))
                    if (s.tevkifat_id and s.tevkifat.payda) else 0,
        "cevirici": float(s.cevirici),
        "uretim": s.uretim_birimi.kisa_ad,
        "fatura": s.fatura_birimi.kisa_ad,
        "alisFiyati": float(s.alis_fiyati) if s.alis_fiyati is not None else None,
        "alisFiyatiPb": s.alis_fiyati_pb,
    } for s in Stok.objects.filter(silindi=False)
      .select_related("kdv", "tevkifat", "uretim_birimi", "fatura_birimi")}


def _satis_teklif_stok_meta():
    """Satış Teklifi ekranı için: satis_urunu=True her stoğun görsel/model kodu/KDV oranı
    + PB başına aktif satış fiyatı (yoksa None -> JS'te 'fiyat tanımlı değil' uyarısı).
    (ürünler, meta) döner — ürünler formset'in başlangıç satırlarını, meta JS'in fiyat/
    görsel verisini besler."""
    urunler = list(stok_servis.satis_urunleri_fiyatlariyla())
    meta = {}
    for s in urunler:
        fiyatlar = {f.para_birimi: float(f.fiyat) for f in s.fiyatlar.all()}
        meta[str(s.pk)] = {
            "kod": s.kod, "ad": s.ad, "modelKodu": s.model_kodu,
            "gorselUrl": s.gorsel.url if s.gorsel else None,
            "kdv": float(s.kdv.oran) if s.kdv_id else 0,
            "fiyatlar": {pb: fiyatlar.get(pb) for pb in ("TRY", "USD", "EUR", "GBP")},
            # Yükleme tipi kodu -> bu ürünün o tipe sığan adedi (navlun dağıtımı için).
            "yukleme": {kod: getattr(s, alan) or None
                        for kod, alan in TanimSecenegi.YUKLEME_ALANI.items()},
        }
    return urunler, meta


def _tip_kodlari():
    """Yükleme Tipi seçeneği pk -> kod (JS, seçilen tipin Stok adet alanını bulmak için)."""
    return {str(s.pk): s.kod for s in TanimSecenegi.objects.filter(
        silindi=False, kategori=TanimSecenegi.Kategori.YUKLEME_TIPI)}


def _secenek_kwargs(cd):
    return {
        "yukleme_sekli_id": cd["yukleme_sekli"].pk if cd.get("yukleme_sekli") else None,
        "odeme_kosulu_id": cd["odeme_kosulu"].pk if cd.get("odeme_kosulu") else None,
        "yukleme_tipi_id": cd["yukleme_tipi"].pk if cd.get("yukleme_tipi") else None,
        "teslim_suresi_id": cd["teslim_suresi"].pk if cd.get("teslim_suresi") else None,
        "navlun_tutari": cd.get("navlun_tutari"),
    }


def _cari_meta():
    """Cari başına para birimi + varsayılan iskonto — cari seçilince JS otomatik uygular
    (elle değiştirilebilir), bkz. satis_teklif_ekle.html."""
    return {str(c.pk): {"pb": c.para_birimi, "iskonto": float(c.iskonto_yuzdesi)}
            for c in Cari.objects.filter(silindi=False)}


def _ts_ekle(request, belge_tur, yon, baslik, emoji):
    ekran = _TS_EKRAN[(belge_tur, yon)]
    if request.method == "POST":
        bform = TeklifSiparisForm(request.POST, belge_tur=belge_tur)
        formset = TeklifSiparisKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"]}
                for f in formset if f.dolu_mu()
            ]
            try:
                ts = teklif_siparis_servis.teklif_siparis_olustur(
                    belge_tur=belge_tur, yon=yon, cari_id=bform.cleaned_data["cari"].pk,
                    tarih=bform.cleaned_data["tarih"],
                    gecerlilik_teslim_tarihi=bform.cleaned_data.get("gecerlilik_teslim_tarihi"),
                    para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                    aciklama=bform.cleaned_data.get("aciklama", ""),
                    depo_id=(bform.cleaned_data["depo"].pk
                             if bform.cleaned_data.get("depo") else None),
                    irsaliye_no=bform.cleaned_data.get("irsaliye_no", ""),
                    satirlar=satirlar, kullanici=request.user)
                messages.success(request, f"{ts.get_belge_tur_display()} kaydedildi.")
                return redirect("core:teklif_siparis_detay", pk=ts.pk)
            except teklif_siparis_servis.TeklifSiparisHatasi as e:
                bform.add_error(None, str(e))
    else:
        bform = TeklifSiparisForm(belge_tur=belge_tur)
        formset = TeklifSiparisKalemFormSet()
    return render(request, "core/teklif_siparis_ekle.html",
                  {"bform": bform, "formset": formset, "baslik": baslik, "emoji": emoji,
                   "stok_meta": _stok_meta(), "iptal_url": reverse("core:" + ekran)})


@ekran_gerekli("satinalma_teklifleri")
def satinalma_teklif_ekle(request):
    return _ts_ekle(request, TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS,
                    "Yeni Satınalma Teklifi", "📥")


@ekran_gerekli("satinalma_siparisleri")
def satinalma_siparis_ekle(request):
    return _ts_ekle(request, TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS,
                    "Yeni Satınalma Siparişi", "🛒")


@ekran_gerekli("satinalma_irsaliyeleri")
def satinalma_irsaliye_ekle(request):
    return _ts_ekle(request, TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS,
                    "Yeni Satınalma İrsaliyesi", "🚚")


@ekran_gerekli("satis_teklifleri")
def satis_teklif_ekle(request):
    """Satış Teklifi — bağımsız ekran (paylaşımlı ``_ts_ekle``'yi ÇAĞIRMAZ). Sayfa açılırken
    TÜM satış ürünleri (satis_urunu=True) formsete önceden dolu gelir; miktar YOK (her
    zaman 1 birim fiyatı iletilir); cari seçilince PB/iskonto JS ile otomatik uygulanır
    (bkz. satis_teklif_ekle.html)."""
    urunler, stok_meta = _satis_teklif_stok_meta()
    if request.method == "POST":
        bform = SatisTeklifBaslikForm(request.POST)
        formset = SatisTeklifKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": Decimal("1"),
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "iskonto_yuzdesi": f.cleaned_data["iskonto_yuzdesi"]}
                for f in formset if f.dahil_mi()
            ]
            if not satirlar:
                bform.add_error(None, "En az bir ürün teklife dahil edilmelidir.")
            else:
                try:
                    ts = teklif_siparis_servis.teklif_siparis_olustur(
                        belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                        cari_id=bform.cleaned_data["cari"].pk, tarih=bform.cleaned_data["tarih"],
                        gecerlilik_teslim_tarihi=bform.cleaned_data.get(
                            "gecerlilik_teslim_tarihi"),
                        para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                        **_secenek_kwargs(bform.cleaned_data),
                        satirlar=satirlar, kullanici=request.user)
                    messages.success(request, f"Satış Teklifi kaydedildi: {ts.belge_no}")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisTeklifBaslikForm()
        formset = SatisTeklifKalemFormSet(initial=[
            {"stok": s.pk, "dahil": True, "iskonto_yuzdesi": Decimal("0"),
             "birim_fiyat": next(
                 (f.fiyat for f in s.fiyatlar.all() if f.para_birimi == "TRY"), None)}
            for s in urunler])
    return render(request, "core/satis_teklif_ekle.html", {
        "bform": bform, "formset": formset, "satirlar": list(zip(urunler, formset)),
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "tip_kodlari": _tip_kodlari(),
        "iptal_url": reverse("core:satis_teklifleri")})


@ekran_gerekli("satis_teklifleri")
def satis_teklif_duzenle(request, pk):
    """Satış Teklifi düzenle — ``satis_teklif_ekle`` ile simetrik (paylaşımlı
    ``teklif_siparis_duzenle``'a hiç dokunmaz). Yalnız SATIŞ+TEKLİF belgeleri kabul eder
    (başka bir kombinasyonun pk'sı 404 verir — URL'den doğrudan erişim de güvenli)."""
    ts = get_object_or_404(
        TeklifSiparis, pk=pk, silindi=False,
        belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS)
    urunler, stok_meta = _satis_teklif_stok_meta()
    if request.method == "POST":
        bform = SatisTeklifBaslikForm(request.POST)
        formset = SatisTeklifKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": Decimal("1"),
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "iskonto_yuzdesi": f.cleaned_data["iskonto_yuzdesi"]}
                for f in formset if f.dahil_mi()
            ]
            if not satirlar:
                bform.add_error(None, "En az bir ürün teklife dahil edilmelidir.")
            else:
                try:
                    teklif_siparis_servis.teklif_siparis_guncelle(
                        ts, cari_id=bform.cleaned_data["cari"].pk,
                        tarih=bform.cleaned_data["tarih"],
                        gecerlilik_teslim_tarihi=bform.cleaned_data.get(
                            "gecerlilik_teslim_tarihi"),
                        para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                        aciklama=ts.aciklama,
                        **_secenek_kwargs(bform.cleaned_data),
                        satirlar=satirlar, kullanici=request.user)
                    messages.success(request, "Satış Teklifi güncellendi.")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisTeklifBaslikForm(initial={
            "cari": ts.cari_id, "tarih": ts.tarih,
            "gecerlilik_teslim_tarihi": ts.gecerlilik_teslim_tarihi,
            "para_birimi": ts.para_birimi,
            "yukleme_sekli": ts.yukleme_sekli_id, "odeme_kosulu": ts.odeme_kosulu_id,
            "yukleme_tipi": ts.yukleme_tipi_id, "teslim_suresi": ts.teslim_suresi_id,
            "navlun_tutari": ts.navlun_tutari})
        mevcut = {k.stok_id: k for k in ts.kalemler.filter(silindi=False)}
        formset = SatisTeklifKalemFormSet(initial=[
            {"stok": s.pk, "dahil": s.pk in mevcut,
             "iskonto_yuzdesi": (mevcut[s.pk].iskonto_yuzdesi if s.pk in mevcut
                                 else Decimal("0")),
             "birim_fiyat": (mevcut[s.pk].birim_fiyat if s.pk in mevcut else next(
                 (f.fiyat for f in s.fiyatlar.all() if f.para_birimi == ts.para_birimi), None))}
            for s in urunler])
    return render(request, "core/satis_teklif_ekle.html", {
        "bform": bform, "formset": formset, "satirlar": list(zip(urunler, formset)),
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "tip_kodlari": _tip_kodlari(),
        "duzenleme": True,
        "iptal_url": reverse("core:teklif_siparis_detay", args=[ts.pk])})


@ekran_gerekli("satis_siparisleri")
def satis_siparis_ekle(request):
    return _ts_ekle(request, TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS,
                    "Yeni Satış Siparişi", "📦")


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_detay(request, pk):
    # silindi filtrelenmez: iptal edilmiş belge de görüntülenebilir (uyarı banner'ıyla).
    ts = get_object_or_404(
        TeklifSiparis.objects.select_related("cari", "kaynak_teklif", "kaynak_siparis", "depo"),
        pk=pk)
    kalemler = ts.kalemler.filter(silindi=False).select_related("stok", "kdv", "tevkifat")
    ekran = _TS_EKRAN[(ts.belge_tur, ts.yon)]
    emoji = _TS_EMOJI[ekran]
    donusen_siparis = (ts.donusen_siparisler.filter(silindi=False).first()
                       if ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF else None)
    donusen_irsaliye = (ts.donusen_irsaliyeler.filter(silindi=False).first()
                       if ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS else None)
    return render(request, "core/teklif_siparis_detay.html",
                  {"ts": ts, "kalemler": kalemler, "emoji": emoji,
                   "liste_url": "core:" + ekran, "donusen_siparis": donusen_siparis,
                   "donusen_irsaliye": donusen_irsaliye, "donusen_fatura": ts.fatura})


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparise_cevir(request, pk):
    teklif = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if teklif.yon == TeklifSiparis.Yon.ALIS:
        messages.error(
            request, "Alış teklifleri artık onaylanınca otomatik siparişe dönüşür; "
                     "elle çevrilemez.")
        return redirect("core:teklif_siparis_detay", pk=teklif.pk)
    if request.method == "POST":
        try:
            siparis = teklif_siparis_servis.teklifi_siparise_cevir(
                teklif, tarih=timezone.localdate(), kullanici=request.user)
            messages.success(
                request, f"{siparis.get_belge_tur_display()} oluşturuldu (teklif {teklif.pk} kaynaklı).")
            return redirect("core:teklif_siparis_detay", pk=siparis.pk)
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=teklif.pk)


@ekran_gerekli_herhangi("satinalma_siparisleri", "satis_siparisleri")
def siparis_faturaya_cevir(request, pk):
    """Siparişi faturaya çevir: Fatura ekle formunu (core/fatura_ekle.html — Fatura'nın KENDİ
    şablonu, aynen yeniden kullanılır) sipariş verileriyle ön-doldurur. Fatura tipi + depo
    BİLEREK ön-doldurulmaz — bunlar muhasebe hesap haritası/stok deposu belirleyen fatura-
    özel kararlardır; kullanıcı burada seçer/onaylar (tam otomatik DEĞİL, tek tık + gözden
    geçirme). Fatura oluşunca sipariş.fatura set edilir (tek seferlik — TeklifSiparis
    tarafında; Fatura modülü TeklifSiparis'i hiç bilmez)."""
    siparis = get_object_or_404(
        TeklifSiparis, pk=pk, silindi=False, belge_tur=TeklifSiparis.BelgeTur.SIPARIS)
    if siparis.yon == TeklifSiparis.Yon.ALIS:
        messages.error(
            request, "Alış siparişleri artık onaylanınca otomatik irsaliyeye, oradan "
                     "faturaya dönüşür; doğrudan çevrilemez.")
        return redirect("core:teklif_siparis_detay", pk=siparis.pk)
    if siparis.fatura_id:
        messages.info(request, "Bu sipariş zaten faturalandırılmış.")
        return redirect("core:fatura_detay", pk=siparis.fatura_id)
    if siparis.durum != TeklifSiparis.Durum.ONAYLI:
        messages.error(request, "Yalnız onaylı sipariş faturaya çevrilebilir.")
        return redirect("core:teklif_siparis_detay", pk=siparis.pk)
    yon = FaturaTipi.Yon.SATIS
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
                    depo_id=(fform.cleaned_data["depo"].pk
                             if fform.cleaned_data.get("depo") else None),
                    satirlar=satirlar, kullanici=request.user)
                siparis.fatura = fatura
                siparis.updated_by = request.user
                siparis.save(update_fields=["fatura", "updated_by", "updated_at"])
                messages.success(
                    request, f"Fatura oluşturuldu (sipariş {siparis.pk} kaynaklı); "
                             f"fiş {fatura.fis.yil}/{fatura.fis.fis_no} oluştu.")
                return redirect("core:fatura_detay", pk=fatura.pk)
            except fatura_servis.FaturaHatasi as e:
                fform.add_error(None, str(e))
    else:
        fform = FaturaForm(yon=yon, initial={
            "cari": siparis.cari_id, "tarih": timezone.localdate(),
            "para_birimi": siparis.para_birimi})
        ilk = [{"stok": k.stok_id, "miktar": k.miktar, "birim_fiyat": k.birim_fiyat}
               for k in siparis.kalemler.filter(silindi=False).select_related("stok")]
        formset = FaturaSatirFormSet(initial=ilk)
    stok_kdv, stok_tevkifat = _stok_kdv_tevkifat()
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat,
                   "baslik": f"Fatura Oluştur (Sipariş {siparis.pk} kaynaklı)",
                   "iptal_url": reverse("core:teklif_siparis_detay", args=[siparis.pk])})


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_duzenle(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    # Satış Teklifi artık bağımsız bir ekranla düzenlenir (iskonto/teslim şekli/ödeme
    # koşulu gibi bu eski paylaşımlı formun bilmediği alanları var — buradan geçilirse
    # sessizce sıfırlanırlardı). Eski URL'e doğrudan gelen istek de güvenle yönlendirilir.
    if (ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF
            and ts.yon == TeklifSiparis.Yon.SATIS):
        return redirect("core:satis_teklif_duzenle", pk=ts.pk)
    ekran = _TS_EKRAN[(ts.belge_tur, ts.yon)]
    emoji = _TS_EMOJI[ekran]
    if request.method == "POST":
        bform = TeklifSiparisForm(request.POST, belge_tur=ts.belge_tur)
        formset = TeklifSiparisKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"]}
                for f in formset if f.dolu_mu()
            ]
            try:
                teklif_siparis_servis.teklif_siparis_guncelle(
                    ts, cari_id=bform.cleaned_data["cari"].pk,
                    tarih=bform.cleaned_data["tarih"],
                    gecerlilik_teslim_tarihi=bform.cleaned_data.get("gecerlilik_teslim_tarihi"),
                    para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                    aciklama=bform.cleaned_data.get("aciklama", ""),
                    depo_id=(bform.cleaned_data["depo"].pk
                             if bform.cleaned_data.get("depo") else None),
                    irsaliye_no=bform.cleaned_data.get("irsaliye_no", ""),
                    satirlar=satirlar, kullanici=request.user)
                messages.success(request, f"{ts.get_belge_tur_display()} güncellendi.")
                return redirect("core:teklif_siparis_detay", pk=ts.pk)
            except teklif_siparis_servis.TeklifSiparisHatasi as e:
                bform.add_error(None, str(e))
    else:
        bform = TeklifSiparisForm(belge_tur=ts.belge_tur, initial={
            "cari": ts.cari_id, "tarih": ts.tarih,
            "gecerlilik_teslim_tarihi": ts.gecerlilik_teslim_tarihi,
            "para_birimi": ts.para_birimi, "aciklama": ts.aciklama, "depo": ts.depo_id,
            "irsaliye_no": ts.irsaliye_no})
        ilk = [{"stok": k.stok_id, "miktar": k.miktar, "birim_fiyat": k.birim_fiyat}
               for k in ts.kalemler.filter(silindi=False).select_related("stok")]
        formset = TeklifSiparisKalemFormSet(initial=ilk)
    return render(request, "core/teklif_siparis_ekle.html",
                  {"bform": bform, "formset": formset,
                   "baslik": f"{ts.get_belge_tur_display()} Düzenle", "emoji": emoji,
                   "stok_meta": _stok_meta(),
                   "iptal_url": reverse("core:teklif_siparis_detay", args=[ts.pk])})


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_iptal_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_siparis_iptal(ts, kullanici=request.user)
            messages.success(request, f"{ts.get_belge_tur_display()} iptal edildi.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_onayla(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_siparis_onayla(ts, kullanici=request.user)
            messages.success(request, f"{ts.get_belge_tur_display()} onaylandı.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_onayi_geri_al(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_siparis_onayi_geri_al(ts, kullanici=request.user)
            messages.success(request, f"{ts.get_belge_tur_display()} onayı geri alındı.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


def _pdf_gorsel_b64(gorsel, arkaplan=(255, 255, 255)):
    """Ürün görselini PDF'e gömülecek şekilde hazırlar: WeasyPrint'in şeffaf WebP'yi
    SİYAH dolgu ile çizdiği görüldü (alfa kanalı doğru compositelenmiyor) — bu yüzden
    burada Pillow ile düz bir zemin üzerine biz composite edip PNG (alfasız) olarak
    gömüyoruz; renderer'ın WebP/alfa davranışına hiç bağımlı kalınmıyor."""
    import base64
    import io

    from PIL import Image

    try:
        with gorsel.open("rb") as f:
            img = Image.open(f)
            img.load()
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                img = img.convert("RGBA")
                duz = Image.new("RGB", img.size, arkaplan)
                duz.paste(img, mask=img.split()[-1])
                img = duz
            else:
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii")
    except (OSError, ValueError):
        return None


def _basamak_goster(stok):
    """Basamak sayısı gösterimi: Çift Çıkışlı (model_kodu 'C' + iki eş rakam, ör. 'C66')
    ürünlerde toplam yerine 'X+X' (ör. '6+6') — model_kodu daha güvenilir kaynak, ayrı taraf
    sayısını basamak_sayisi'nden (12) türetmeye çalışmak yerine doğrudan koddan okunur."""
    if stok.basamak_sayisi is None:
        return None
    kod = (stok.model_kodu or "").upper()
    if (len(kod) == 3 and kod[0] == "C" and kod[1].isdigit() and kod[2].isdigit()
            and kod[1] == kod[2]):
        return f"{kod[1]}+{kod[2]}"
    return str(stok.basamak_sayisi)


def satis_teklif_pdf_baglam(ts, kalemler, dil, kullanici):
    """Satış Teklifi PDF şablonuna (satis_teklif_pdf.html) eklenecek bağlam — hem
    teklif_siparis_pdf view'ından hem testlerden çağrılır (context inşası tek yerde, iki
    kopya sürüklenmesin)."""
    E = _PDF_ETIKET[dil]
    for k in kalemler:
        k.urun_ad = k.stok.ad_dil(dil)
        k.basamak_goster = _basamak_goster(k.stok)
        k.materyal_goster = k.stok.materyal_dil(dil)
    # Yurt içi/dışı: KDV notu yalnız yurt içi alıcıya anlamlı (ihracatta KDV istisnası var —
    # "fiyatlara KDV dahil değildir" ifadesi yurtdışı alıcıyı yanıltır).
    yurt_ici = not ts.cari.ulke_id or ts.cari.ulke.kod == "TR"
    navlun_var = ts.navlun_tutari is not None and bool(ts.yukleme_tipi_id)
    notlar = [E["not_birim_fiyat"]]
    if yurt_ici:
        notlar.append(E["not_kdv"])
    if navlun_var:
        notlar.append(E["not_navlun"])
    notlar.append(E["not_agirlik_tolerans"])
    notlar.append(E["not_yukleme_tahmini"])
    notlar.append(E["not_cbm"])
    if ts.gecerlilik_teslim_tarihi:
        notlar.append(E["not_gecerlilik_tarihli"].format(
            tarih=ts.gecerlilik_teslim_tarihi.strftime("%d.%m.%Y")))
    else:
        notlar.append(E["not_gecerlilik_varsayilan"])
    return {
        "dil": dil, "E": E,
        "yukleme_sekli_ad": ts.yukleme_sekli.ad_dil(dil) if ts.yukleme_sekli_id else "",
        "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil(dil) if ts.odeme_kosulu_id else "",
        "yukleme_tipi_ad": ts.yukleme_tipi.ad_dil(dil) if ts.yukleme_tipi_id else "",
        "teslim_suresi_ad": ts.teslim_suresi.ad_dil(dil) if ts.teslim_suresi_id else "",
        "ulke_ad": ts.cari.ulke.ad_dil(dil) if ts.cari.ulke_id else "",
        "sehir_ad": ts.cari.sehir.ad_dil(dil) if ts.cari.sehir_id else "",
        "navlun_var": navlun_var,
        "notlar": notlar,
        "firma": firma_servis.firma_bilgisi_getir(),
        "hazirlayan": kullanici.get_full_name() or kullanici.get_username(),
        "hazirlayan_eposta": kullanici.email,
        "hazirlayan_telefon": kullanici_telefon(kullanici),
        "yurt_ici": yurt_ici,
    }


_DOSYA_GECERSIZ = str.maketrans("", "", '\\/:*?"<>|')


def _pdf_dosya_adi(*parcalar) -> str:
    """PDF indirme dosya adı: parçaları '-' ile birleştirir, dosya sisteminde geçersiz
    karakterleri temizler (ör. cari unvanı '/' veya ':' içerebilir)."""
    temiz = [str(p).translate(_DOSYA_GECERSIZ).strip() for p in parcalar if p]
    return "-".join(temiz) + ".pdf"


def _pdf_content_disposition(dosya_adi: str, ek="inline") -> str:
    """Content-Disposition değeri: ASCII yedek + RFC 6266 UTF-8 (Türkçe karakterli
    dosya adı — ör. cari unvanındaki İ/Ş/Ğ — ham haliyle latin-1 header'a sığmaz)."""
    from urllib.parse import quote
    ascii_ad = dosya_adi.encode("ascii", "ignore").decode("ascii").strip(" -") or "belge.pdf"
    return f'{ek}; filename="{ascii_ad}"; filename*=UTF-8\'\'{quote(dosya_adi)}'


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri", "satis_siparisleri")
def teklif_siparis_pdf(request, pk):
    """Belgenin PDF'i (WeasyPrint, A4) — çek bordrosu PDF'iyle aynı desen."""
    import base64

    from django.contrib.staticfiles import finders
    from weasyprint import HTML

    ts = get_object_or_404(
        TeklifSiparis.objects.select_related("cari", "cari__ulke", "cari__sehir"), pk=pk)
    kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv", "tevkifat"))
    for k in kalemler:
        k.gorsel_b64 = None
        if k.stok.satis_urunu and k.stok.gorsel:
            k.gorsel_b64 = _pdf_gorsel_b64(k.stok.gorsel)
    teknik_kalemler = [k for k in kalemler if k.stok.satis_urunu]
    sat_teklif = (ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF
                 and ts.yon == TeklifSiparis.Yon.SATIS)
    ctx = {"ts": ts, "kalemler": kalemler, "teknik_kalemler": teknik_kalemler,
           "sat_teklif": sat_teklif}
    logo_yol = finders.find("core/img/semta-logo.png")
    if logo_yol:
        with open(logo_yol, "rb") as f:
            ctx["logo_b64"] = base64.b64encode(f.read()).decode("ascii")
    sablon = "core/teklif_siparis_pdf.html"
    dosya_adi = _pdf_dosya_adi(ts.belge_no or ts.pk, ts.cari.unvan)
    if sat_teklif:
        dil = "en" if request.GET.get("dil") == "en" else "tr"
        ctx.update(satis_teklif_pdf_baglam(ts, kalemler, dil, request.user))
        sablon = "core/satis_teklif_pdf.html"
    html = render_to_string(sablon, ctx)
    pdf = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = _pdf_content_disposition(dosya_adi)
    return resp


# Satış Teklifi PDF'i etiketleri — kullanıcı çıktıyı TR ya da EN seçer (?dil=).
_PDF_ETIKET = {
    "tr": {
        "baslik": "Teklif Detayı", "alt_baslik": "Fiyat Teklifi / QUOTATION",
        "alici": "Alıcı", "satici": "Satıcı", "unvan": "Unvan", "ilgili_kisi": "İlgili Kişi",
        "ulke": "Ülke", "adres": "Adres", "telefon": "Telefon", "web": "Web Sitesi",
        "eposta": "E-posta", "hazirlayan": "Hazırlayan",
        "teklif_no": "Teklif No", "tarih": "Tarih",
        "para_birimi": "Para Birimi", "yukleme_sekli": "Teslim / Yükleme Şekli",
        "odeme_kosulu": "Ödeme Koşulu", "yukleme_tipi": "Yükleme Tipi",
        "teslim_suresi": "Teslim Süresi", "navlun": "Navlun",
        "gorsel": "Görsel", "urun": "Ürün", "ozellik": "Teknik Özellikler",
        "fiyat": "Fiyat", "navlun_haric": "(navlun hariç)",
        "navlun_on": "Fiyatlara", "navlun_son": " navlunu dahildir.",
        "agirlik": "Ağırlık (kg)", "cbm": "CBM (m³)",
        "yukleme_adedi": "Yükleme Adedi (adet)", "tir": "TIR",
        "basamak": "Basamak", "yukseklik": "Platform Yüksekliği", "acik_derinlik": "Açık derinlik",
        "taban": "Taban genişliği", "kapali": "Kapalı boy", "azami_yuk": "Azami yük",
        "materyal": "Materyal", "hs_kodu": "H/S Kodu",
        "adet": "adet", "notlar": "Notlar",
        "not_birim_fiyat": ("Fiyatlar birim (1 adet) fiyatıdır; miktar ve toplam tutar "
                            "proforma faturada belirtilir."),
        "not_kdv": "Fiyatlara KDV dahil değildir.",
        "not_navlun": ("Navlun, seçilen yükleme tipine sığan adede bölünerek ürün başına "
                       "dağıtılmıştır."),
        "not_agirlik_tolerans": "Ürün ve ambalaj ağırlıklarında ±%5 tolerans olabilir.",
        "not_yukleme_tahmini": ("20'DC/40'HQ/TIR yükleme adetleri tahminidir; ambalaj ve "
                                "istifleme düzenine göre değişebilir."),
        "not_cbm": "CBM, ambalajlı ürün başına hacmi ifade eder.",
        "not_gecerlilik_varsayilan": "Teklif, geçerlilik tarihine kadar bağlayıcıdır.",
        "not_gecerlilik_tarihli": "Fiyatlar {tarih} tarihine kadar geçerlidir.",
        "sayfa": "Sayfa", "altbilgi": "SEMTA Alüminyum Merdiven İmalatı · Satış Teklifi",
    },
    "en": {
        "baslik": "Quotation Details", "alt_baslik": "Sales Quotation",
        "alici": "To", "satici": "From", "unvan": "Company", "ilgili_kisi": "Contact Person",
        "ulke": "Country", "adres": "Address", "telefon": "Phone", "web": "Website",
        "eposta": "E-mail", "hazirlayan": "Prepared by",
        "teklif_no": "Quotation No", "tarih": "Date",
        "para_birimi": "Currency", "yukleme_sekli": "Delivery Term",
        "odeme_kosulu": "Payment Term", "yukleme_tipi": "Transport Mode",
        "teslim_suresi": "Lead Time", "navlun": "Freight",
        "gorsel": "Picture", "urun": "Item", "ozellik": "Specifications",
        "fiyat": "Unit Price", "navlun_haric": "(excl. freight)",
        "navlun_on": "Prices include freight for", "navlun_son": ".",
        "agirlik": "Weight (kg)", "cbm": "CBM (m³)",
        "yukleme_adedi": "Loading Qty (pcs)", "tir": "Truck",
        "basamak": "Steps", "yukseklik": "Platform Height", "acik_derinlik": "Open depth",
        "taban": "Base width", "kapali": "Folded length", "azami_yuk": "Max load",
        "materyal": "Material", "hs_kodu": "HS Code",
        "adet": "pcs", "notlar": "Notes",
        "not_birim_fiyat": ("Prices are per unit (1 pc); quantities and total amount are "
                            "stated on the proforma invoice."),
        "not_kdv": "Prices exclude VAT.",
        "not_navlun": ("Freight is allocated per unit by dividing it by the quantity that "
                       "fits the selected loading type."),
        "not_agirlik_tolerans": "Product and package weights may vary by ±5%.",
        "not_yukleme_tahmini": ("20'DC/40'HQ/Truck loading quantities are estimated and may "
                                "vary depending on packaging and stacking."),
        "not_cbm": "CBM refers to the volume per packaged unit.",
        "not_gecerlilik_varsayilan": "This quotation is binding until the validity date.",
        "not_gecerlilik_tarihli": "Prices are valid until {tarih}.",
        "sayfa": "Page", "altbilgi": "SEMTA Aluminium Ladder Manufacturing · Quotation",
    },
}


# === FİNANS — Kredi Kartı ===
@ekran_gerekli("kredi_karti")
def kredi_kartlari(request):
    return render(request, "core/kredi_karti_listesi.html",
                  {"kartlar": finans_servis.aktif_kredi_kartlari()})


@ekran_gerekli("kredi_karti")
def kredi_karti_detay(request, pk):
    """Kredi kartı detayı (Dilim 1): lacivert başlık + kart özeti + hareket buton İSKELETİ
    (yer tutucu, motor Dilim 2'de) + kart ekstresi (bağlı muhasebe hesabının devirli ekstresi).
    Kredi kartı bir YÜKÜMLÜLÜK: bakiye alacak yönlü → güncel borç = negatif kapanış bakiyesi.
    Tarih + açıklama filtreli; varsayılan dönem SON 1 AY."""
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)

    bugun = timezone.localdate()
    form = MizanFiltreForm(request.GET) if request.GET else None
    if form and form.is_valid():
        b, s = form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    else:
        b, s = _son_bir_ay(bugun), bugun
        form = MizanFiltreForm(initial={"baslangic": b, "bitis": s})

    ekstre = (ekstre_devirli_servis(kart.muhasebe.hesap_kodu, b, s)
              if kart.muhasebe_id else None)

    aciklama = (request.GET.get("aciklama") or "").strip()
    if ekstre and aciklama:
        ara = buyuk_harf_tr(aciklama)
        satirlar = [r for r in ekstre.satirlar
                    if ara in buyuk_harf_tr(r.fis_aciklama or "")
                    or ara in buyuk_harf_tr(r.satir_aciklama or "")]
    else:
        satirlar = ekstre.satirlar if ekstre else []
    satirlar = list(reversed(satirlar))   # yeni tarihten eskiye (yürüyen bakiye değişmez)

    # Güncel borç = kapanış ALACAK bakiyesi (yükümlülük); TRY kartta TL kapanıştan, DÖVİZ
    # kartta döviz kapanıştan (kur farkı TL bakiyeyi saptırır; limit de kart PB'sinde).
    borc = Decimal("0")
    if ekstre:
        if kart.para_birimi == "TRY":
            net = ekstre.kapanis_bakiye
        else:
            d = ekstre.dvz_toplamlar.get(kart.para_birimi, {}).get("bakiye", Decimal("0"))
            net = (ekstre.acilis_dvz or {}).get(kart.para_birimi, Decimal("0")) + d
        if net < 0:
            borc = -net
    kullanilabilir = (kart.limit or Decimal("0")) - borc

    kk_fis_pks = set(YevmiyeFisi.objects.filter(
        kredi_karti=kart, kaynak=YevmiyeFisi.Kaynak.KREDI_KARTI, silindi=False
    ).values_list("pk", flat=True))
    taksitler = kredi_karti_hareket_servis.kart_taksit_takvimi(kart)
    return render(request, "core/kredi_karti_detay.html",
                  {"kart": kart, "form": form, "ekstre": ekstre, "satirlar": satirlar,
                   "aciklama": aciklama, "borc": borc, "kullanilabilir": kullanilabilir,
                   "kk_fis_pks": kk_fis_pks, "taksitler": taksitler})


def _kredi_karti_hareket_form(request, kart, tip):
    tan = kredi_karti_hareket_servis.HAREKET[tip]
    if request.method == "POST":
        form = KrediKartiHareketForm(request.POST, tip=tip, kart=kart)
        if form.is_valid():
            try:
                if tip == "harcama":
                    fis = kredi_karti_hareket_servis.harcama_olustur(
                        kart=kart, karsi=form.cleaned_data["karsi"],
                        tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                        taksit_adedi=form.cleaned_data.get("taksit_adedi") or 1,
                        ilk_vade=form.cleaned_data.get("ilk_vade"),
                        aciklama=form.cleaned_data["aciklama"], kullanici=request.user)
                else:
                    fis = kredi_karti_hareket_servis.hareket_olustur(
                        kart=kart, tip=tip, karsi=form.cleaned_data["karsi"],
                        tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                        aciklama=form.cleaned_data["aciklama"], kullanici=request.user)
                messages.success(request, tan["ad"] + f" kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:kredi_karti_detay", pk=kart.pk)
            except kredi_karti_hareket_servis.KrediKartiHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediKartiHareketForm(tip=tip, kart=kart)
    return render(request, "core/kredi_karti_hareket_form.html",
                  {"kart": kart, "form": form, "tip": tip, "tan": tan})


@ekran_gerekli("kredi_karti")
def kredi_karti_hareket_ekle(request, pk, tip):
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    if tip not in kredi_karti_hareket_servis.HAREKET:
        messages.error(request, "Geçersiz hareket tipi.")
        return redirect("core:kredi_karti_detay", pk=kart.pk)
    return _kredi_karti_hareket_form(request, kart, tip)


@ekran_gerekli("kredi_karti")
def kredi_karti_hareket_iptal(request, pk, fis_pk):
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    if request.method == "POST":
        try:
            kredi_karti_hareket_servis.hareket_iptal(fis=fis, kart=kart, kullanici=request.user)
            messages.success(request, "Kart hareketi iptal edildi.")
        except kredi_karti_hareket_servis.KrediKartiHareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kredi_karti_detay", pk=kart.pk)


@ekran_gerekli("kredi_karti")
def kredi_karti_ekle(request):
    if request.method == "POST":
        form = KrediKartiForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_karti_olustur(
                    ad=cd["ad"], banka=cd["banka"], kart_son4=cd["kart_son4"],
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
        form = KrediKartiForm(request.POST, mevcut_hesap=kart.muhasebe.hesap_kodu)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_karti_guncelle(
                    kart, ad=cd["ad"], banka=cd["banka"], kart_son4=cd["kart_son4"],
                    limit=cd["limit"], kesim_gunu=cd["kesim_gunu"],
                    son_odeme_gunu=cd["son_odeme_gunu"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi kartı güncellendi.")
                return redirect("core:kredi_kartlari")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediKartiForm(initial={
            "ad": kart.ad, "banka": kart.banka_id, "kart_son4": kart.kart_son4,
            "limit": kart.limit, "kesim_gunu": kart.kesim_gunu,
            "son_odeme_gunu": kart.son_odeme_gunu, "para_birimi": kart.para_birimi,
            "muhasebe": kart.muhasebe.hesap_kodu}, mevcut_hesap=kart.muhasebe.hesap_kodu)
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Kredi Kartı Düzenle", "emoji": "💳",
                   "iptal_url": reverse("core:kredi_kartlari")})


@ekran_gerekli("kredi_karti")
def kredi_karti_sil(request, pk):
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            finans_servis.kredi_karti_sil(kart, kullanici=request.user)
            messages.success(request, "Kredi kartı silindi.")
        except finans_servis.FinansHatasi as e:
            messages.error(request, str(e))
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
                    ad=cd["ad"], banka=cd["banka"], anapara=cd["anapara"],
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
        form = KrediForm(request.POST, mevcut_hesap=kredi.muhasebe.hesap_kodu)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                finans_servis.kredi_guncelle(
                    kredi, ad=cd["ad"], banka=cd["banka"], anapara=cd["anapara"],
                    faiz_orani=cd["faiz_orani"], para_birimi=cd["para_birimi"],
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user)
                messages.success(request, "Kredi güncellendi.")
                return redirect("core:krediler")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediForm(initial={
            "ad": kredi.ad, "banka": kredi.banka_id, "anapara": kredi.anapara,
            "faiz_orani": kredi.faiz_orani, "para_birimi": kredi.para_birimi,
            "muhasebe": kredi.muhasebe.hesap_kodu}, mevcut_hesap=kredi.muhasebe.hesap_kodu)
    return render(request, "core/finans_form.html",
                  {"form": form, "baslik": "Kredi Düzenle", "emoji": "🏛️",
                   "iptal_url": reverse("core:krediler")})


@ekran_gerekli("kredi")
def kredi_detay(request, pk):
    """Kredi detayı (Dilim 1): başlık + kredi özeti + Kullandırım + kredi ekstresi. Kredi
    YÜKÜMLÜLÜK: kalan borç = negatif kapanış bakiyesi. Tarih + açıklama filtreli; SON 1 AY."""
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    bugun = timezone.localdate()
    form = MizanFiltreForm(request.GET) if request.GET else None
    if form and form.is_valid():
        b, s = form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    else:
        b, s = _son_bir_ay(bugun), bugun
        form = MizanFiltreForm(initial={"baslangic": b, "bitis": s})
    ekstre = (ekstre_devirli_servis(kredi.muhasebe.hesap_kodu, b, s)
              if kredi.muhasebe_id else None)
    aciklama = (request.GET.get("aciklama") or "").strip()
    if ekstre and aciklama:
        ara = buyuk_harf_tr(aciklama)
        satirlar = [r for r in ekstre.satirlar
                    if ara in buyuk_harf_tr(r.fis_aciklama or "")
                    or ara in buyuk_harf_tr(r.satir_aciklama or "")]
    else:
        satirlar = ekstre.satirlar if ekstre else []
    satirlar = list(reversed(satirlar))
    # Kalan borç: TRY kredide TL kapanıştan; DÖVİZ kredide döviz kapanıştan — kur farkı
    # TL bakiyeyi saptırır, kalan borç kredinin KENDİ para biriminde anlamlıdır.
    kalan = Decimal("0")
    if ekstre:
        if kredi.para_birimi == "TRY":
            net = ekstre.kapanis_bakiye
        else:
            d = ekstre.dvz_toplamlar.get(kredi.para_birimi, {}).get("bakiye", Decimal("0"))
            net = (ekstre.acilis_dvz or {}).get(kredi.para_birimi, Decimal("0")) + d
        if net < 0:
            kalan = -net
    kredi_fis_pks = set(YevmiyeFisi.objects.filter(
        kredi=kredi, kaynak=YevmiyeFisi.Kaynak.KREDI, silindi=False
    ).values_list("pk", flat=True))
    tozet = kredi_hareket_servis.taksit_ozet(kredi)
    return render(request, "core/kredi_detay.html",
                  {"kredi": kredi, "form": form, "ekstre": ekstre, "satirlar": satirlar,
                   "aciklama": aciklama, "kalan": kalan, "kredi_fis_pks": kredi_fis_pks,
                   "taksitler": tozet["taksitler"], "bekleyen": tozet["bekleyen"],
                   "odenen": tozet["odenen"], "bekleyen_sayi": tozet["bekleyen_sayi"],
                   "odeme_form": KrediTaksitOdemeForm()})


def _kredi_hareket_form(request, kredi, tip):
    tan = kredi_hareket_servis.HAREKET[tip]
    if request.method == "POST":
        form = KrediHareketForm(request.POST, tip=tip, kredi=kredi)
        if form.is_valid():
            try:
                fis = kredi_hareket_servis.hareket_olustur(
                    kredi=kredi, tip=tip, karsi=form.cleaned_data["karsi"],
                    tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user)
                messages.success(request, tan["ad"] + f" kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:kredi_detay", pk=kredi.pk)
            except kredi_hareket_servis.KrediHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediHareketForm(tip=tip, kredi=kredi)
    return render(request, "core/kredi_hareket_form.html",
                  {"kredi": kredi, "form": form, "tip": tip, "tan": tan})


@ekran_gerekli("kredi")
def kredi_hareket_ekle(request, pk, tip):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if tip not in kredi_hareket_servis.HAREKET:
        messages.error(request, "Geçersiz hareket tipi.")
        return redirect("core:kredi_detay", pk=kredi.pk)
    return _kredi_hareket_form(request, kredi, tip)


@ekran_gerekli("kredi")
def kredi_hareket_iptal(request, pk, fis_pk):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    if request.method == "POST":
        try:
            kredi_hareket_servis.hareket_iptal(fis=fis, kredi=kredi, kullanici=request.user)
            messages.success(request, "Kredi hareketi iptal edildi.")
        except kredi_hareket_servis.KrediHareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kredi_detay", pk=kredi.pk)


KrediTaksitFormSet = formset_factory(KrediTaksitForm, extra=0, min_num=1, validate_min=True)


@ekran_gerekli("kredi")
def kredi_taksit_ekle(request, pk):
    """Ödeme planına ELLE taksit(ler) ekle (formset: vade + anapara + faiz)."""
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if request.method == "POST":
        formset = KrediTaksitFormSet(request.POST)
        if formset.is_valid():
            satirlar = [f.cleaned_data for f in formset if f.dolu_mu()]
            try:
                olusan = kredi_hareket_servis.taksit_plani_ekle(
                    kredi=kredi, satirlar=satirlar, kullanici=request.user)
                messages.success(request, f"{len(olusan)} taksit eklendi.")
                return redirect("core:kredi_detay", pk=kredi.pk)
            except kredi_hareket_servis.KrediHareketHatasi as e:
                formset.forms[0].add_error(None, str(e))
    else:
        formset = KrediTaksitFormSet(initial=[{}])
    return render(request, "core/kredi_taksit_ekle.html", {"kredi": kredi, "formset": formset})


@ekran_gerekli("kredi")
def kredi_taksit_sil(request, pk, taksit_pk):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    taksit = get_object_or_404(KrediTaksit, pk=taksit_pk, kredi=kredi, silindi=False)
    if request.method == "POST":
        try:
            kredi_hareket_servis.taksit_sil(taksit, kullanici=request.user)
            messages.success(request, "Taksit silindi.")
        except kredi_hareket_servis.KrediHareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:kredi_detay", pk=kredi.pk)


@ekran_gerekli("kredi")
def kredi_taksit_ode(request, pk):
    """Seçilen bekleyen taksitleri tek geri ödeme fişiyle öder."""
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if request.method == "POST":
        form = KrediTaksitOdemeForm(request.POST)
        taksit_ids = request.POST.getlist("taksit_ids")
        if form.is_valid():
            try:
                fis = kredi_hareket_servis.taksitleri_ode(
                    kredi=kredi, taksit_ids=taksit_ids, karsi=form.cleaned_data["karsi"],
                    faiz_hesap=form.cleaned_data.get("faiz_hesap"),
                    tarih=form.cleaned_data["tarih"], aciklama=form.cleaned_data["aciklama"],
                    kullanici=request.user)
                messages.success(request,
                                 f"{len(taksit_ids)} taksit ödendi: fiş {fis.yil}/{fis.fis_no}.")
            except kredi_hareket_servis.KrediHareketHatasi as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Nakit hesabı olarak Banka hesabı VEYA Kasa (yalnız biri) seçin.")
    return redirect("core:kredi_detay", pk=kredi.pk)


@ekran_gerekli("kredi")
def kredi_sil(request, pk):
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            finans_servis.kredi_sil(kredi, kullanici=request.user)
            messages.success(request, "Kredi silindi.")
        except finans_servis.FinansHatasi as e:
            messages.error(request, str(e))
    return redirect("core:krediler")


# === FİNANS — Çek / Senet (bordro mantığı; yeniden inşa) ===
CekKalemFormSet = formset_factory(CekKalemForm, extra=0, min_num=1, validate_min=True)


@ekran_gerekli("cek_senet")
def cek_senetler(request):
    """Çek/Senet ana sayfa: Muhasebe Hesap Kodları butonu + giriş/çıkış işlem
    butonları + Bordrolar/Çek-Senetler tabları (listeler)."""
    return render(request, "core/cek_senetler.html", {
        "bordrolar": cek_servis.aktif_bordrolar(),
        "cekler": cek_servis.aktif_cek_senetler(),
    })


def _bordro_giris_view(request, *, servis_fn, cari_label, baslik, emoji, yardim, basari_ek):
    """Giriş/çıkış bordrosu giriş ekranı ortak gövdesi (çok satır + görsel + tek fiş)."""
    if request.method == "POST":
        bform = BordroBaslikForm(request.POST, cari_label=cari_label)
        formset = CekKalemFormSet(request.POST, request.FILES)
        if bform.is_valid() and formset.is_valid():
            satirlar = []
            for f in formset:
                if not f.dolu_mu():
                    continue
                cd = f.cleaned_data
                satirlar.append({
                    "tip": cd["tip"], "tutar": cd["tutar"], "vade": cd["vade"],
                    "belge_no": cd["belge_no"], "kesideci": cd["kesideci"],
                    "on_yuz": (gorsel.kucult_webp(cd["on_yuz"], max_kenar=1600, kalite=80, ad="cek_on")
                               if cd.get("on_yuz") else None),
                    "arka_yuz": (gorsel.kucult_webp(cd["arka_yuz"], max_kenar=1600, kalite=80, ad="cek_arka")
                                 if cd.get("arka_yuz") else None),
                })
            try:
                bordro = servis_fn(
                    cari_id=bform.cleaned_data["cari"].pk,
                    tarih=bform.cleaned_data["tarih"],
                    para_birimi=bform.cleaned_data["para_birimi"],
                    satirlar=satirlar, kullanici=request.user)
                messages.success(request, f"Bordro kaydedildi; {bordro.cek_senetler.count()} "
                                          f"evrak {basari_ek}, fiş oluşturuldu.")
                return redirect("core:cek_bordro_detay", pk=bordro.pk)
            except cek_servis.CekHatasi as e:
                bform.add_error(None, str(e))
    else:
        bform = BordroBaslikForm(cari_label=cari_label)
        formset = CekKalemFormSet()
    return render(request, "core/cek_bordro_form.html",
                  {"bform": bform, "formset": formset, "baslik": baslik, "emoji": emoji, "yardim": yardim})


@ekran_gerekli("cek_senet")
def cek_cari_giris(request):
    """Cari Giriş: alınan çek/senetler portföye girer → N evrak (PORTFÖYDE) + tek fiş."""
    return _bordro_giris_view(
        request, servis_fn=cek_servis.cari_giris_bordrosu_olustur,
        cari_label="Cari (çeki/senedi veren)", baslik="Cari Giriş Bordrosu", emoji="🤝",
        yardim="Alınan çek/senetler portföye girer; bordro için tek birleşik yevmiye fişi oluşur "
               "(Portföydeki çek/senet borç / cari alacak). Hesaplar Muhasebe Hesap Kodları'ndan gelir.",
        basari_ek="portföye girdi")


@ekran_gerekli("cek_senet")
def cek_firma_cikis(request):
    """Firma Çek-Senet: kendi çek/senedimizi cariye veririz → N evrak (VERİLDİ) + tek fiş."""
    return _bordro_giris_view(
        request, servis_fn=cek_servis.firma_cikis_bordrosu_olustur,
        cari_label="Cari (çek/senedi verdiğimiz)", baslik="Firma Çek-Senet Bordrosu", emoji="🏭",
        yardim="Kendi çek/senedimiz cariye verilir; bordro için tek birleşik yevmiye fişi oluşur "
               "(cari borç / Verilen çek/senet alacak). Hesaplar Muhasebe Hesap Kodları'ndan gelir.",
        basari_ek="verildi")


def _islem_secim_view(request, *, form_cls, hedef_alani, servis_fn, baslik, emoji, yardim, buton,
                      basari, durum=CekSenet.Durum.PORTFOYDE, yon=CekSenet.Yon.ALINAN):
    """Portföy-seçim işlem bordrosu ortak gövdesi (Ciro / Banka Tahsil / Teminat / İade / Cari İade).
    hedef_alani=None → formda hedef seçici yok (İade işlemleri; hedef zaten mevcut duruma bağlı).
    durum → seçim listesindeki evrakın aranan durumu (İade'de TAHSILDE/TEMINATTA)."""
    if request.method == "POST":
        form = form_cls(request.POST)
        cek_ids = request.POST.getlist("cek_ids")
        if form.is_valid():
            try:
                kwargs = {"tarih": form.cleaned_data["tarih"], "cek_ids": cek_ids,
                         "kullanici": request.user}
                if hedef_alani:
                    kwargs["hedef_id"] = form.cleaned_data[hedef_alani].pk
                bordro = servis_fn(**kwargs)
                messages.success(request, f"Bordro kaydedildi; {len(cek_ids)} evrak {basari}, "
                                          f"fiş oluşturuldu.")
                return redirect("core:cek_bordro_detay", pk=bordro.pk)
            except cek_servis.CekHatasi as e:
                form.add_error(None, str(e))
    else:
        form = form_cls()
    return render(request, "core/cek_islem_secim.html",
                  {"form": form, "portfoy": cek_servis.portfoydeki_cekler(yon=yon, durum=durum),
                   "baslik": baslik, "emoji": emoji, "yardim": yardim, "buton": buton,
                   "secili_ids": request.POST.getlist("cek_ids")})


@ekran_gerekli("cek_senet")
def cek_cari_ciro(request):
    """Cari Ciro: portföydeki alınan çek/senetleri listeden seçip bir cariye ciro."""
    return _islem_secim_view(
        request, form_cls=CariCiroForm, hedef_alani="cari",
        servis_fn=cek_servis.cari_ciro_bordrosu_olustur, baslik="Cari Ciro", emoji="🔁",
        yardim="Portföydeki alınan çek/senetler seçtiğin cariye ciro edilir "
               "(ciro carisi borç / Portföydeki çek-senet alacak). Seçilenler aynı para biriminde olmalı.",
        buton="Ciro Et", basari="ciro edildi")


@ekran_gerekli("cek_senet")
def cek_banka_tahsil(request):
    """Banka Tahsil: portföydeki çek/senetleri seçip bankaya tahsile ver (→ Bankada Tahsilde)."""
    return _islem_secim_view(
        request, form_cls=BankaIslemForm, hedef_alani="banka_hesap",
        servis_fn=cek_servis.banka_tahsil_bordrosu_olustur, baslik="Banka Tahsil", emoji="🏦",
        yardim="Portföydeki çek/senetler seçtiğin bankaya tahsile verilir; Bankada Tahsildeki "
               "hesabına geçer (Tahsildeki çek-senet borç / Portföydeki çek-senet alacak). "
               "Para vade gelince ayrı işlenir; yanlışsa geri alınabilir.",
        buton="Tahsile Ver", basari="tahsile verildi")


@ekran_gerekli("cek_senet")
def cek_banka_teminat(request):
    """Banka Teminat: portföydeki çek/senetleri seçip bankaya teminat ver (→ Bankada Teminatta)."""
    return _islem_secim_view(
        request, form_cls=BankaIslemForm, hedef_alani="banka_hesap",
        servis_fn=cek_servis.banka_teminat_bordrosu_olustur, baslik="Banka Teminat", emoji="🔒",
        yardim="Portföydeki çek/senetler seçtiğin bankaya teminat olarak verilir; Bankada "
               "Teminattaki hesabına geçer (Teminattaki çek-senet borç / Portföydeki çek-senet alacak). "
               "Banka iade ederse geri alınabilir.",
        buton="Teminata Ver", basari="teminata verildi")


@ekran_gerekli("cek_senet")
def cek_banka_tahsil_iade(request):
    """Banka Tahsil İade: Bankada Tahsildeki çek/senetler portföye iade edilir (banka tahsil
    edemedi / işlemden vazgeçildi)."""
    return _islem_secim_view(
        request, form_cls=IslemTarihForm, hedef_alani=None,
        servis_fn=cek_servis.banka_tahsil_iade_bordrosu_olustur, baslik="Banka Tahsil İade", emoji="↩️",
        yardim="Bankada Tahsildeki çek/senetler portföye iade edilir (Portföydeki çek-senet borç / "
               "Tahsildeki çek-senet alacak). Hangi banka olduğu önemli değil; hedef seçilmez.",
        buton="Portföye İade Al", basari="portföye iade alındı",
        durum=CekSenet.Durum.TAHSILDE)


@ekran_gerekli("cek_senet")
def cek_banka_teminat_iade(request):
    """Banka Teminat İade: Bankada Teminattaki çek/senetler portföye iade edilir (teminat çözüldü)."""
    return _islem_secim_view(
        request, form_cls=IslemTarihForm, hedef_alani=None,
        servis_fn=cek_servis.banka_teminat_iade_bordrosu_olustur, baslik="Banka Teminat İade", emoji="↩️",
        yardim="Bankada Teminattaki çek/senetler portföye iade edilir (Portföydeki çek-senet borç / "
               "Teminattaki çek-senet alacak). Teminat çözüldüğünde kullanılır.",
        buton="Portföye İade Al", basari="portföye iade alındı",
        durum=CekSenet.Durum.TEMINATTA)


@ekran_gerekli("cek_senet")
def cek_cari_iade(request):
    """Cari İade: portföydeki alınan çek/senetler kendi carisine (evrakı veren) iade edilir."""
    return _islem_secim_view(
        request, form_cls=IslemTarihForm, hedef_alani=None,
        servis_fn=cek_servis.cari_iade_bordrosu_olustur, baslik="Cari İade", emoji="↩️",
        yardim="Portföydeki alınan çek/senetler kendi carisine (evrakı veren) iade edilir "
               "(cari borç / Portföydeki çek-senet alacak). Evrak portföyden çıkar (İade Edildi).",
        buton="Cariye İade Et", basari="cariye iade edildi")


@ekran_gerekli("cek_senet")
def cek_karsiliksiz(request):
    """Karşılıksız: Portföyde / Bankada Tahsilde / Teminattaki alınan çek/senetler
    karşılıksız çıkar; borç kendi carisine geri yüklenir (durum → Karşılıksız)."""
    return _islem_secim_view(
        request, form_cls=IslemTarihForm, hedef_alani=None,
        servis_fn=cek_servis.karsiliksiz_bordrosu_olustur, baslik="Karşılıksız", emoji="⛔",
        yardim="Portföyde, Bankada Tahsilde veya Teminattaki alınan çek/senetler karşılıksız "
               "çıktığında seçilir; borç kendi carisine geri yüklenir (cari borç / kaynak "
               "çek-senet alacak). Nakit hareketi yoktur; durum Karşılıksız (terminal) olur.",
        buton="Karşılıksız İşle", basari="karşılıksız işlendi",
        durum=(CekSenet.Durum.PORTFOYDE, CekSenet.Durum.TAHSILDE, CekSenet.Durum.TEMINATTA))


@ekran_gerekli("cek_senet")
def cek_firma_karsiliksiz(request):
    """Firma Çek Karşılıksız: Verildi durumundaki kendi (verilen) çek/senetlerimiz
    karşılıksız çıkar; verilen çek-senet borç / cari alacak (borcumuz geri doğar)."""
    return _islem_secim_view(
        request, form_cls=IslemTarihForm, hedef_alani=None,
        servis_fn=cek_servis.firma_karsiliksiz_bordrosu_olustur,
        baslik="Firma Çek Karşılıksız", emoji="🚫",
        yardim="Verildi durumundaki kendi (verilen) çek/senetlerimiz karşılıksız çıktığında "
               "seçilir; verilen çek-senet borç / cari alacak (borcumuz geri doğar). Nakit "
               "hareketi yoktur; durum Karşılıksız (terminal) olur.",
        buton="Karşılıksız İşle", basari="karşılıksız işlendi",
        yon=CekSenet.Yon.VERILEN, durum=CekSenet.Durum.VERILDI)


def _nakit_islem_view(request, *, servis_fn, yon, durum, baslik, emoji, yardim, buton,
                      basari, liste_baslik, bos_metin):
    """Nakit gerçekleşme ekranı ortak gövdesi (Tahsil / Firma Çek Ödeme): evrak seçimi +
    nakit hesabı (Banka VEYA Kasa) + tarih → tek birleşik fiş."""
    if request.method == "POST":
        form = CekNakitForm(request.POST)
        cek_ids = request.POST.getlist("cek_ids")
        if form.is_valid():
            try:
                bh = form.cleaned_data.get("banka_hesap")
                ks = form.cleaned_data.get("kasa")
                bordro = servis_fn(
                    tarih=form.cleaned_data["tarih"], cek_ids=cek_ids,
                    banka_hesap_id=bh.pk if bh else None,
                    kasa_id=ks.pk if ks else None, kullanici=request.user)
                messages.success(request, f"Bordro kaydedildi; {len(cek_ids)} evrak {basari}, "
                                          f"fiş oluşturuldu.")
                return redirect("core:cek_bordro_detay", pk=bordro.pk)
            except cek_servis.CekHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CekNakitForm()
    return render(request, "core/cek_islem_secim.html", {
        "form": form, "baslik": baslik, "emoji": emoji, "yardim": yardim, "buton": buton,
        "durum_goster": True, "liste_baslik": liste_baslik, "bos_metin": bos_metin,
        "portfoy": cek_servis.portfoydeki_cekler(yon=yon, durum=durum),
        "secili_ids": request.POST.getlist("cek_ids"),
    })


@ekran_gerekli("cek_senet")
def cek_tahsil(request):
    """Tahsil Gerçekleşme: Bankada Tahsildeki veya Portföydeki alınan çek/senetler nakde
    döner; para seçilen Banka hesabı VEYA Kasa'ya girer (evrak durumu → Tahsil Edildi)."""
    return _nakit_islem_view(
        request, servis_fn=cek_servis.tahsil_bordrosu_olustur,
        yon=CekSenet.Yon.ALINAN, durum=(CekSenet.Durum.TAHSILDE, CekSenet.Durum.PORTFOYDE),
        baslik="Tahsil Gerçekleşme", emoji="💰",
        yardim="Bankada Tahsildeki veya Portföydeki alınan çek/senetler seçilir; para seçtiğin "
               "Banka hesabı ya da Kasa'ya girer (nakit hesabı borç / kaynak çek-senet alacak). "
               "Evrak durumu Tahsil Edildi olur; yanlışsa bordrodan geri alınabilir.",
        buton="Tahsil Et", basari="tahsil edildi",
        liste_baslik="Tahsil Edilebilir Çek / Senetler (Bankada Tahsilde + Portföyde)",
        bos_metin="Tahsil edilebilir alınan çek/senet yok (Bankada Tahsilde veya Portföyde "
                  "evrak gerekli).")


@ekran_gerekli("cek_senet")
def cek_odeme(request):
    """Firma Çek Ödeme: Verildi durumundaki verilen çek/senetler ödenir; para seçilen
    Banka hesabı VEYA Kasa'dan çıkar (evrak durumu → Ödendi)."""
    return _nakit_islem_view(
        request, servis_fn=cek_servis.odeme_bordrosu_olustur,
        yon=CekSenet.Yon.VERILEN, durum=CekSenet.Durum.VERILDI,
        baslik="Firma Çek Ödeme", emoji="💸",
        yardim="Verildi durumundaki kendi çek/senetlerimiz seçilir; para seçtiğin Banka hesabı "
               "ya da Kasa'dan çıkar (Verilen çek-senet borç / nakit hesabı alacak). Evrak "
               "durumu Ödendi olur; yanlışsa bordrodan geri alınabilir.",
        buton="Öde", basari="ödendi",
        liste_baslik="Ödenecek Firma Çek / Senetleri (Verildi)",
        bos_metin="Ödenecek verilen çek/senet yok. Önce Firma Çek-Senet ile evrak verin.")


def _bordro_baglam(bordro):
    """Bordro + çek/senetleri + fişleri + toplam + ORTALAMA VADE bağlamı.
    Evrak: giriş/çıkış → oluşturduğu; işlem bordrosu → seçtiği (evrak_qs)."""
    cekler = list(bordro.evrak_qs().order_by("vade", "id"))
    toplam = sum((c.tutar for c in cekler), Decimal("0"))
    ort_vade, vade_gun = cek_servis.ortalama_vade(
        [(c.tutar, c.vade) for c in cekler], bordro.tarih)
    return {
        "bordro": bordro, "cekler": cekler,
        "fisler": bordro.fisler.filter(silindi=False).order_by("yil", "fis_no"),
        "toplam": toplam, "para_birimi": (cekler[0].para_birimi if cekler else "TRY"),
        "ort_vade": ort_vade, "vade_gun": vade_gun,
        "giris_mi": bordro.tur in CekBordrosu.GIRIS_TURLERI,
    }


@ekran_gerekli("cek_senet")
def cek_bordro_detay(request, pk):
    """Bordro detayı: başlık + çek/senetler + toplam/ortalama vade + bağlı yevmiye fişi."""
    bordro = get_object_or_404(CekBordrosu, pk=pk, silindi=False)
    return render(request, "core/cek_bordro_detay.html", _bordro_baglam(bordro))


@ekran_gerekli("cek_senet")
def cek_bordro_pdf(request, pk):
    """Bordronun PDF'i (WeasyPrint, A4) — tarayıcıda açılır, oradan yazdırılır/kaydedilir."""
    import base64

    from django.contrib.staticfiles import finders
    from weasyprint import HTML

    bordro = get_object_or_404(CekBordrosu, pk=pk, silindi=False)
    ctx = _bordro_baglam(bordro)
    logo_yol = finders.find("core/img/semta-logo.png")
    if logo_yol:
        with open(logo_yol, "rb") as f:
            ctx["logo_b64"] = base64.b64encode(f.read()).decode("ascii")
    html = render_to_string("core/cek_bordro_pdf.html", ctx)
    pdf = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="cek-senet-bordro-{bordro.pk}.pdf"'
    return resp


@ekran_gerekli("cek_senet")
def cek_bordro_sil(request, pk):
    bordro = get_object_or_404(CekBordrosu, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            cek_servis.bordro_sil(bordro, kullanici=request.user)
            evrak_ek = ("evraklar silindi" if bordro.tur in CekBordrosu.GIRIS_TURLERI
                        else "evraklar önceki durumuna döndü")
            messages.success(request, f"Bordro geri alındı (fiş iptal, {evrak_ek}).")
            return redirect("core:cek_senetler")
        except cek_servis.CekHatasi as e:
            messages.error(request, str(e))
    return redirect("core:cek_bordro_detay", pk=bordro.pk)


@ekran_gerekli("cek_senet")
def cek_hesap_ayari(request):
    """Çek/Senet muhasebe hesap eşlemesi (durum × çek/senet matrisi)."""
    ayar = cek_servis.hesap_ayari()
    if request.method == "POST":
        form = CekHesapAyariForm(request.POST)
        if form.is_valid():
            try:
                kodlar = {a: (form.cleaned_data[a].hesap_kodu if form.cleaned_data[a] else "")
                          for a in cek_servis.AYAR_ALANLARI}
                cek_servis.hesap_ayari_kaydet(kodlar, kullanici=request.user)
                messages.success(request, "Çek/Senet muhasebe hesapları kaydedildi.")
                return redirect("core:cek_senetler")
            except cek_servis.CekHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CekHesapAyariForm(initial={
            a: (getattr(ayar, a).hesap_kodu if getattr(ayar, a) else None)
            for a in cek_servis.AYAR_ALANLARI})
    return render(request, "core/cek_hesap_ayari.html", {"form": form})


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


# --- Cari sevk (teslimat) adresleri ------------------------------------------
def _sevk_form_kw(cd):
    g = lambda x: x.pk if x else None
    return dict(ad=cd["ad"], ulke_id=g(cd["ulke"]), sehir_id=g(cd["sehir"]),
                adres=cd["adres"], varsayilan=cd["varsayilan"])


@ekran_gerekli("cariler")
def sevk_adresi_ekle(request, cari_pk):
    cari = get_object_or_404(Cari, pk=cari_pk, silindi=False)
    if request.method == "POST":
        form = CariSevkAdresiForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.sevk_adresi_ekle(cari, **_sevk_form_kw(form.cleaned_data),
                                             kullanici=request.user)
                messages.success(request, "Sevk adresi eklendi.")
                return redirect("core:cari_detay", pk=cari.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariSevkAdresiForm()
    return render(request, "core/cari_sevk_adresi_form.html",
                  {"form": form, "baslik": "Yeni Sevk Adresi", "cari": cari})


@ekran_gerekli("cariler")
def sevk_adresi_duzenle(request, pk):
    sevk = get_object_or_404(CariSevkAdresi, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariSevkAdresiForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.sevk_adresi_guncelle(sevk, **_sevk_form_kw(form.cleaned_data),
                                                 kullanici=request.user)
                messages.success(request, "Sevk adresi güncellendi.")
                return redirect("core:cari_detay", pk=sevk.cari_id)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariSevkAdresiForm(initial={
            "ad": sevk.ad, "ulke": sevk.ulke_id, "sehir": sevk.sehir_id,
            "adres": sevk.adres, "varsayilan": sevk.varsayilan})
    return render(request, "core/cari_sevk_adresi_form.html",
                  {"form": form, "baslik": "Sevk Adresi Düzenle", "cari": sevk.cari})


@ekran_gerekli("cariler")
def sevk_adresi_sil(request, pk):
    sevk = get_object_or_404(CariSevkAdresi, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.sevk_adresi_sil(sevk, kullanici=request.user)
        messages.success(request, "Sevk adresi silindi.")
    return redirect("core:cari_detay", pk=sevk.cari_id)


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


# --- Cari aktiviteler (görüşme/temas kayıtları) -----------------------------
def _aktivite_ekleri_kaydet(request, aktivite, dosyalar):
    for f in dosyalar:
        try:
            cari_servis.aktivite_ek_ekle(aktivite, dosya=f, kullanici=request.user)
        except cari_servis.CariHatasi as e:
            messages.warning(request, str(e))


@ekran_gerekli("cariler")
def aktivite_ekle(request, cari_pk):
    cari = get_object_or_404(Cari, pk=cari_pk, silindi=False)
    if request.method == "POST":
        form = CariAktiviteForm(request.POST)
        if form.is_valid():
            try:
                aktivite = cari_servis.aktivite_ekle(
                    cari, **form.cleaned_data, kullanici=request.user)
                _aktivite_ekleri_kaydet(request, aktivite, request.FILES.getlist("dosyalar"))
                messages.success(request, "Aktivite eklendi.")
                return redirect("core:cari_detay", pk=cari.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariAktiviteForm()
    return render(request, "core/cari_aktivite_form.html",
                  {"form": form, "baslik": "Yeni Aktivite", "cari": cari})


@ekran_gerekli("cariler")
def aktivite_duzenle(request, pk):
    aktivite = get_object_or_404(CariAktivite, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariAktiviteForm(request.POST)
        if form.is_valid():
            try:
                cari_servis.aktivite_guncelle(
                    aktivite, **form.cleaned_data, kullanici=request.user)
                _aktivite_ekleri_kaydet(request, aktivite, request.FILES.getlist("dosyalar"))
                messages.success(request, "Aktivite güncellendi.")
                return redirect("core:cari_detay", pk=aktivite.cari_id)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariAktiviteForm(initial={
            "tarih": aktivite.tarih, "tur": aktivite.tur, "aciklama": aktivite.aciklama})
    return render(request, "core/cari_aktivite_form.html", {
        "form": form, "baslik": "Aktivite Düzenle", "cari": aktivite.cari,
        "aktivite": aktivite, "ekler": aktivite.ekler.filter(silindi=False)})


@ekran_gerekli("cariler")
def aktivite_sil(request, pk):
    aktivite = get_object_or_404(CariAktivite, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.aktivite_sil(aktivite, kullanici=request.user)
        messages.success(request, "Aktivite silindi.")
    return redirect("core:cari_detay", pk=aktivite.cari_id)


@ekran_gerekli("cariler")
def aktivite_ek_sil(request, pk):
    ek = get_object_or_404(CariAktiviteEk, pk=pk, silindi=False)
    if request.method == "POST":
        cari_servis.aktivite_ek_sil(ek, kullanici=request.user)
        messages.success(request, "Dosya silindi.")
    return redirect("core:aktivite_duzenle", pk=ek.aktivite_id)


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


# Tanım seçenekleri (Yükleme Şekli / Ödeme Koşulu / Yükleme Tipi) — tek model, kategoriye göre
# parametreli ortak view'lar (kdv_orani_* kalıbı). slug -> (kategori, başlık, emoji).
_SECENEK_KATEGORI = {
    "yukleme-sekli": (TanimSecenegi.Kategori.YUKLEME_SEKLI, "Yükleme Şekli", "🚢"),
    "odeme-kosulu": (TanimSecenegi.Kategori.ODEME_KOSULU, "Ödeme Koşulları", "💳"),
    "yukleme-tipi": (TanimSecenegi.Kategori.YUKLEME_TIPI, "Yükleme Tipi", "🚚"),
    "teslim-suresi": (TanimSecenegi.Kategori.TESLIM_SURESI, "Teslim Süresi", "🕒"),
}


def _secenek_kategori(slug):
    if slug not in _SECENEK_KATEGORI:
        raise Http404
    return _SECENEK_KATEGORI[slug]


def _secenek_ctx(slug, **ek):
    kategori, baslik, emoji = _secenek_kategori(slug)
    return {"slug": slug, "kategori": kategori, "baslik": baslik, "emoji": emoji,
            "kod_var": kategori == TanimSecenegi.Kategori.YUKLEME_TIPI, **ek}


@yonetici_gerekli
def secenek_listesi(request, slug):
    kategori, _, _ = _secenek_kategori(slug)
    return render(request, "core/tanim_secenek_listesi.html",
                  _secenek_ctx(slug, secenekler=tanim_servis.aktif_secenekler(kategori)))


@yonetici_gerekli
def secenek_ekle(request, slug):
    kategori, baslik, _ = _secenek_kategori(slug)
    if request.method == "POST":
        form = TanimSecenegiForm(request.POST, kategori=kategori)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.secenek_olustur(
                    kategori, ad=cd["ad"], kod=cd.get("kod", ""), ad_en=cd.get("ad_en", ""),
                    sira=cd["sira"], kullanici=request.user)
                messages.success(request, f"{baslik} eklendi.")
                return redirect("core:secenek_listesi", slug=slug)
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TanimSecenegiForm(kategori=kategori)
    return render(request, "core/tanim_secenek_form.html",
                  _secenek_ctx(slug, form=form, form_baslik=f"Yeni {baslik}"))


@yonetici_gerekli
def secenek_duzenle(request, slug, pk):
    kategori, baslik, _ = _secenek_kategori(slug)
    s = get_object_or_404(TanimSecenegi, pk=pk, silindi=False, kategori=kategori)
    if request.method == "POST":
        form = TanimSecenegiForm(request.POST, kategori=kategori)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                tanim_servis.secenek_guncelle(
                    s, ad=cd["ad"], kod=cd.get("kod", ""), ad_en=cd.get("ad_en", ""),
                    sira=cd["sira"], kullanici=request.user)
                messages.success(request, f"{baslik} güncellendi.")
                return redirect("core:secenek_listesi", slug=slug)
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TanimSecenegiForm(kategori=kategori, initial={
            "ad": s.ad, "kod": s.kod, "ad_en": s.ad_en, "sira": s.sira})
    return render(request, "core/tanim_secenek_form.html",
                  _secenek_ctx(slug, form=form, form_baslik=f"{baslik} Düzenle", duzenlenen=s))


@yonetici_gerekli
def secenek_sil(request, slug, pk):
    kategori, baslik, _ = _secenek_kategori(slug)
    s = get_object_or_404(TanimSecenegi, pk=pk, silindi=False, kategori=kategori)
    if request.method == "POST":
        try:
            tanim_servis.secenek_sil(s, kullanici=request.user)
            messages.success(request, f"{baslik} silindi.")
        except tanim_servis.TanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:secenek_listesi", slug=slug)


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


# === AYARLAR — Firma Bilgileri (tekil kayıt + serbest sayıda banka hesabı) ===
FirmaBankaFormSet = formset_factory(FirmaBankaForm, extra=0)


@yonetici_gerekli
def firma_bilgileri(request):
    firma = firma_servis.firma_bilgisi_getir()
    if request.method == "POST":
        form = FirmaBilgisiForm(request.POST, request.FILES)
        formset = FirmaBankaFormSet(request.POST, prefix="banka")
        if form.is_valid() and formset.is_valid():
            cd = form.cleaned_data
            logo_dosya = (gorsel.kucult_webp(cd["logo"], max_kenar=800, kalite=85, ad="firma")
                         if cd.get("logo") else None)
            bankalar = [
                {"banka_adi": f.cleaned_data.get("banka_adi", ""),
                 "sube": f.cleaned_data.get("sube", ""),
                 "hesap_sahibi": f.cleaned_data.get("hesap_sahibi", ""),
                 "iban": f.cleaned_data.get("iban", ""),
                 "para_birimi": f.cleaned_data.get("para_birimi") or "TRY"}
                for f in formset if f.dolu_mu()
            ]
            try:
                firma_servis.firma_bilgisi_kaydet(
                    unvan=cd["unvan"], vergi_dairesi=cd["vergi_dairesi"], vergi_no=cd["vergi_no"],
                    adres=cd["adres"], telefon=cd["telefon"], eposta=cd["eposta"], web=cd["web"],
                    logo=logo_dosya, bankalar=bankalar, kullanici=request.user)
                messages.success(request, "Firma bilgileri kaydedildi.")
                return redirect("core:firma_bilgileri")
            except firma_servis.FirmaHatasi as e:
                form.add_error(None, str(e))
    else:
        form = FirmaBilgisiForm(initial={
            "unvan": firma.unvan, "vergi_dairesi": firma.vergi_dairesi, "vergi_no": firma.vergi_no,
            "adres": firma.adres, "telefon": firma.telefon, "eposta": firma.eposta,
            "web": firma.web})
        ilk = [{"banka_adi": b.banka_adi, "sube": b.sube, "hesap_sahibi": b.hesap_sahibi,
                "iban": b.iban, "para_birimi": b.para_birimi}
               for b in firma.bankalar.filter(silindi=False)]
        formset = FirmaBankaFormSet(initial=ilk, prefix="banka")
    return render(request, "core/firma_bilgileri.html",
                  {"form": form, "formset": formset, "firma": firma})


# === FASON — Kesim Tanımları (yönetici) + Kesim Listesi Hesapla (+ PDF) ===
@yonetici_gerekli
def fason_kesim_tanimlari(request):
    return render(request, "core/fason_kesim_listesi.html",
                  {"kesimler": fason_servis.aktif_kesimler()})


@yonetici_gerekli
def fason_kesim_ekle(request):
    if request.method == "POST":
        form = FasonKesimForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                fason_servis.kesim_olustur(
                    urun_id=cd["urun"].pk, kesilmis_parca_id=cd["kesilmis_parca"].pk,
                    adet=cd["adet"], sira=cd["sira"], kullanici=request.user)
                messages.success(request, "Kesim tanımı eklendi.")
                return redirect("core:fason_kesim_tanimlari")
            except fason_servis.FasonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = FasonKesimForm()
    return render(request, "core/fason_kesim_form.html",
                  {"form": form, "baslik": "Yeni Kesim Tanımı"})


@yonetici_gerekli
def fason_kesim_duzenle(request, pk):
    k = get_object_or_404(FasonKesim, pk=pk, silindi=False)
    if request.method == "POST":
        form = FasonKesimForm(request.POST)
        if form.is_valid():
            try:
                cd = form.cleaned_data
                fason_servis.kesim_guncelle(
                    k, urun_id=cd["urun"].pk, kesilmis_parca_id=cd["kesilmis_parca"].pk,
                    adet=cd["adet"], sira=cd["sira"], kullanici=request.user)
                messages.success(request, "Kesim tanımı güncellendi.")
                return redirect("core:fason_kesim_tanimlari")
            except fason_servis.FasonHatasi as e:
                form.add_error(None, str(e))
    else:
        form = FasonKesimForm(initial={
            "urun": k.urun_id, "kesilmis_parca": k.kesilmis_parca_id,
            "adet": k.adet, "sira": k.sira})
    return render(request, "core/fason_kesim_form.html",
                  {"form": form, "baslik": "Kesim Tanımı Düzenle", "duzenlenen": k})


@yonetici_gerekli
def fason_kesim_sil(request, pk):
    k = get_object_or_404(FasonKesim, pk=pk, silindi=False)
    if request.method == "POST":
        fason_servis.kesim_sil(k, kullanici=request.user)
        messages.success(request, "Kesim tanımı silindi.")
    return redirect("core:fason_kesim_tanimlari")


FasonSatirFormSet = formset_factory(FasonSatirForm, extra=0)


def _fason_pdf_yanit(*, kalemler, sonuc, kullanici, no=None):
    import base64

    from django.contrib.staticfiles import finders
    from weasyprint import HTML
    logo_b64 = None
    logo_yol = finders.find("core/img/semta-logo.png")
    if logo_yol:
        with open(logo_yol, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("ascii")
    html = render_to_string("core/fason_pdf.html", {
        "kalemler": kalemler, "sonuc": sonuc, "logo_b64": logo_b64, "no": no,
        "hazirlayan": kullanici.get_full_name() or kullanici.username,
        "tarih": timezone.localdate()})
    pdf = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    dosya_adi = f"fason-kesim-listesi-{no}.pdf" if no else "fason-kesim-listesi.pdf"
    resp["Content-Disposition"] = f'inline; filename="{dosya_adi}"'
    return resp


@ekran_gerekli("fason_hesapla")
def fason_hesapla(request):
    sonuc = None
    if request.method == "POST":
        formset = FasonSatirFormSet(request.POST, prefix="satir")
        if formset.is_valid():
            kalemler = [(f.cleaned_data["urun"], f.cleaned_data["miktar"])
                       for f in formset if f.dolu_mu()]
            if not kalemler:
                messages.error(request, "En az bir ürün satırı girin.")
            else:
                sonuc = fason_servis.fason_listesi_hesapla(kalemler)
                if request.POST.get("eylem") == "pdf":
                    kayit = fason_servis.fason_kaydi_olustur(
                        kalemler=kalemler, kullanici=request.user)
                    return _fason_pdf_yanit(kalemler=kalemler, sonuc=sonuc,
                                            kullanici=request.user, no=kayit.no)
    else:
        formset = FasonSatirFormSet(prefix="satir")
    return render(request, "core/fason_hesapla.html", {
        "formset": formset, "sonuc": sonuc, "yonetici": yonetici_mi(request.user),
        "kayitlar_yetkili": ekran_gorebilir(request.user, "fason_kayitlari")})


@ekran_gerekli("fason_kayitlari")
def fason_kayitlari(request):
    kayitlar = (FasonKesimKaydi.objects.filter(silindi=False)
                .select_related("created_by").order_by("-yil", "-sira"))
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    return render(request, "core/fason_kayitlari.html", {"kayitlar": sayfa})


@ekran_gerekli("fason_kayitlari")
def fason_kaydi_detay(request, pk):
    kayit = get_object_or_404(FasonKesimKaydi, pk=pk, silindi=False)
    kalemler = fason_servis.kayit_kalemleri(kayit)
    sonuc = fason_servis.kayit_sonucu(kayit)
    return render(request, "core/fason_kaydi_detay.html",
                  {"kayit": kayit, "kalemler": kalemler, "sonuc": sonuc})


@ekran_gerekli("fason_kayitlari")
def fason_kaydi_pdf(request, pk):
    kayit = get_object_or_404(FasonKesimKaydi, pk=pk, silindi=False)
    kalemler = fason_servis.kayit_kalemleri(kayit)
    sonuc = fason_servis.kayit_sonucu(kayit)
    return _fason_pdf_yanit(kalemler=kalemler, sonuc=sonuc,
                            kullanici=kayit.created_by or request.user, no=kayit.no)


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
    # tip__yon DEĞİL — İrsaliye'den otomatik açılan taslağın tipi henüz boş olabilir
    # (INNER JOIN tip=None satırları dışlar); Fatura'nın kendi yon alanı bunun için var.
    faturalar = (fatura_servis.aktif_faturalar().filter(yon=yon)
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
                # fatura_olustur = taslak + hemen onay (tip formda zaten seçili) — günlük
                # fatura girişi tek tıkla kalır, tam atomik (eskisi gibi ya hepsi ya hiçbiri).
                fatura = fatura_servis.fatura_olustur(
                    tip_id=fform.cleaned_data["tip"].pk,
                    cari_id=fform.cleaned_data["cari"].pk,
                    tarih=fform.cleaned_data["tarih"],
                    fatura_no=fform.cleaned_data.get("fatura_no", ""),
                    para_birimi=fform.cleaned_data.get("para_birimi", "TRY"),
                    depo_id=(fform.cleaned_data["depo"].pk
                             if fform.cleaned_data.get("depo") else None),
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
    yon = fatura.yon
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
                if fatura.durum == Fatura.Durum.TASLAK:
                    mesaj = "Taslak fatura güncellendi."
                else:
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
                   "liste_url": _fatura_liste_url(fatura.yon)})


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_iptal_gorunum(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    yon = fatura.yon
    if request.method == "POST":
        fatura_servis.fatura_iptal(fatura, kullanici=request.user)
        messages.success(request, "Fatura ve bağlı fiş iptal edildi.")
    return redirect(_fatura_liste_url(yon))


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_onayla(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            fatura_servis.fatura_onayla(fatura, kullanici=request.user)
            messages.success(
                request, f"Fatura onaylandı; fiş {fatura.fis.yil}/{fatura.fis.fis_no} oluştu.")
        except fatura_servis.FaturaHatasi as e:
            messages.error(request, str(e))
    return redirect("core:fatura_detay", pk=fatura.pk)


# === DİĞER > Yemek Takibi ====================================================
def _yemek_takibi_bu_ay():
    bugun = timezone.localdate()
    return bugun.replace(day=1), bugun


def _yemek_takibi_liste_url(cari_id=None):
    url = reverse("core:yemek_takibi")
    return f"{url}?cari={cari_id}" if cari_id else url


def _yemek_takibi_filtrele(request):
    """GET parametrelerinden (cari, tarih aralığı) çözer + aktif kayıtları/özeti getirir.
    Liste ekranı ve PDF dökümü aynı filtre mantığını kullanır."""
    form = YemekTakibiFiltreForm(request.GET or None)
    if form.is_valid():
        cari = form.cleaned_data["cari"]
        baslangic, bitis = form.cleaned_data["baslangic"], form.cleaned_data["bitis"]
    else:
        # Tarih aralığı eksik/geçersizse (örn. kayıt eklendikten sonra yalnız ?cari= ile
        # dönülünce) varsayılan bu-ay aralığına düş, ama GET'te bir cari geldiyse onu
        # yok SAYMA — form'u tutarlı biçimde (cari + hesaplanan aralık) yeniden kur.
        baslangic, bitis = _yemek_takibi_bu_ay()
        cari_id = request.GET.get("cari")
        cari = Cari.objects.filter(pk=cari_id, silindi=False).first() if cari_id else None
        form = YemekTakibiFiltreForm(initial={
            "cari": cari.pk if cari else None, "baslangic": baslangic, "bitis": bitis})
    kayitlar = list(yemek_takibi_servis.aktif_kayitlar(
        cari=cari, baslangic=baslangic, bitis=bitis))
    gun, kisi, tutar = yemek_takibi_servis.aylik_ozet(kayitlar)
    return form, cari, baslangic, bitis, kayitlar, gun, kisi, tutar


@ekran_gerekli("yemek_takibi")
def yemek_takibi(request):
    form, cari, baslangic, bitis, kayitlar, gun, kisi, tutar = _yemek_takibi_filtrele(request)
    return render(request, "core/yemek_takibi_listesi.html", {
        "form": form, "kayitlar": kayitlar, "cari": cari,
        "baslangic": baslangic, "bitis": bitis,
        "gun": gun, "kisi": kisi, "tutar": tutar,
        "ekle_url": f"{reverse('core:yemek_sayimi_ekle')}{'?cari=' + str(cari.pk) if cari else ''}",
        "pdf_url": f"{reverse('core:yemek_takibi_pdf')}?{request.GET.urlencode()}",
    })


@ekran_gerekli("yemek_takibi")
def yemek_takibi_pdf(request):
    """Filtrelenen dökümün PDF'i (WeasyPrint, A4) — çek bordrosu PDF'iyle aynı desen."""
    import base64

    from django.contrib.staticfiles import finders
    from weasyprint import HTML

    _form, cari, baslangic, bitis, kayitlar, gun, kisi, tutar = _yemek_takibi_filtrele(request)
    ctx = {"cari": cari, "baslangic": baslangic, "bitis": bitis,
           "kayitlar": kayitlar, "gun": gun, "kisi": kisi, "tutar": tutar}
    logo_yol = finders.find("core/img/semta-logo.png")
    if logo_yol:
        with open(logo_yol, "rb") as f:
            ctx["logo_b64"] = base64.b64encode(f.read()).decode("ascii")
    html = render_to_string("core/yemek_takibi_pdf.html", ctx)
    pdf = HTML(string=html).write_pdf()
    resp = HttpResponse(pdf, content_type="application/pdf")
    dosya_adi = f"yemek-takibi-{baslangic:%Y-%m-%d}-{bitis:%Y-%m-%d}.pdf"
    resp["Content-Disposition"] = f'inline; filename="{dosya_adi}"'
    return resp


@ekran_gerekli("yemek_takibi")
def yemek_sayimi_ekle(request):
    if request.method == "POST":
        form = YemekSayimForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                yemek_takibi_servis.kayit_ekle(
                    cari=cd["cari"], tarih=cd["tarih"], kisi_sayisi=cd["kisi_sayisi"],
                    birim_fiyat=cd["birim_fiyat"], notlar=cd["notlar"], kullanici=request.user)
                messages.success(request, "Kayıt eklendi.")
                return redirect(_yemek_takibi_liste_url(cd["cari"].pk))
            except yemek_takibi_servis.YemekTakibiHatasi as e:
                form.add_error(None, str(e))
    else:
        initial = {}
        cari_id = request.GET.get("cari")
        if cari_id:
            cari_obj = Cari.objects.filter(pk=cari_id, silindi=False).first()
            if cari_obj:
                initial["cari"] = cari_obj.pk
                son_fiyat = yemek_takibi_servis.son_birim_fiyat(cari_obj)
                if son_fiyat is not None:
                    initial["birim_fiyat"] = son_fiyat
        form = YemekSayimForm(initial=initial)
    return render(request, "core/yemek_sayimi_form.html",
                  {"form": form, "baslik": "Yeni Kayıt"})


@ekran_gerekli("yemek_takibi")
def yemek_sayimi_duzenle(request, pk):
    kayit = get_object_or_404(YemekSayimi, pk=pk, silindi=False)
    if request.method == "POST":
        form = YemekSayimForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                yemek_takibi_servis.kayit_guncelle(
                    kayit, tarih=cd["tarih"], kisi_sayisi=cd["kisi_sayisi"],
                    birim_fiyat=cd["birim_fiyat"], notlar=cd["notlar"], kullanici=request.user)
                messages.success(request, "Kayıt güncellendi.")
                return redirect(_yemek_takibi_liste_url(kayit.cari_id))
            except yemek_takibi_servis.YemekTakibiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = YemekSayimForm(initial={
            "cari": kayit.cari_id, "tarih": kayit.tarih, "kisi_sayisi": kayit.kisi_sayisi,
            "birim_fiyat": kayit.birim_fiyat, "notlar": kayit.notlar})
    return render(request, "core/yemek_sayimi_form.html",
                  {"form": form, "baslik": "Kayıt Düzenle", "duzenlenen": kayit})


@ekran_gerekli("yemek_takibi")
def yemek_sayimi_sil(request, pk):
    kayit = get_object_or_404(YemekSayimi, pk=pk, silindi=False)
    cari_id = kayit.cari_id
    if request.method == "POST":
        yemek_takibi_servis.kayit_sil(kayit, kullanici=request.user)
        messages.success(request, "Kayıt silindi.")
    return redirect(_yemek_takibi_liste_url(cari_id))
