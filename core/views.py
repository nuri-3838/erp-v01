"""Fiş giriş/liste/düzenleme/görüntüleme, rapor, kullanıcı yönetimi ve ekran yetkisi görünümleri."""
import calendar
import datetime
import json
import os
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Replace
from django.forms import formset_factory
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponsePermanentRedirect, JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from core.forms import (
    AdayAktiviteForm, AdayAsamaTanimForm, AdayCariyeMevcutCariForm, AdayCariyeYeniCariForm,
    AktiflestirmeBaslikForm, AktiflestirmeSatirForm, TEVKIFAT_YOK,
    AdayMusteriForm, AdayMusteriKategoriForm, AdayPotansiyelTanimForm, AdayTipTanimForm,
    AdayYetkiliForm,
    BilancoTarihForm, BirimForm, CariAktiviteForm, CariBankaForm, CariForm, CariKategoriForm,
    CariSevkAdresiForm,
    BankaForm, KdvMahsupForm, PersonelBordroForm, PersonelBordroSatirForm, YatirimProjesiKapatForm, BankaHareketDuzenleForm, BankaHareketForm, CariKesintiForm, CariVirmanForm, DovizIslemForm, KrediKartiHareketDuzenleForm, BankaHesapForm, BankaIslemForm, BordroBaslikForm, CariCiroForm, CariYetkiliForm, CekHesapAyariForm, CekKalemForm, CekNakitForm, DepoForm, DuranVarlikDuzenleForm, DuranVarlikForm, FaturaForm, FaturaSatirForm, FasonKesimForm, FasonSatirForm, FirmaBankaForm, FirmaBilgisiForm, IslemTarihForm,
    FaturaTipiForm, FisForm,
    KasaForm, KasaHareketForm, KategoriForm, KdvOraniForm, KrediForm, KrediKartiForm,
    KrediKartiHareketForm, KrediHareketForm, KrediTaksitForm, KrediTaksitOdemeForm,
    TeklifSiparisForm, TeklifSiparisKalemForm, SatisBelgeBaslikForm, SatisTeklifKalemForm,
    TeklifRedForm,
    SatisProformaBaslikForm, SatisProformaKalemForm,
    TanimSecenegiForm,
    KullaniciDuzenleForm, KullaniciEkleForm,
    BankaHesapHareketForm, BankaHesapSatirForm, DepoTransferForm, MizanFiltreForm, SarfCikisForm, SatirForm, SehirForm, StokForm, StokHareketForm, TevkifatOraniForm,
    UlkeForm, YemekSayimForm, YemekTakibiFiltreForm,
    IsIstasyonuForm, OperasyonBaslikForm, OperasyonGirdiSatirForm, OperasyonYanCiktiSatirForm, IhtiyacHesaplaSatirForm,
    UrunAgaciForm,
    UretimEmriBaslikForm, UretimEmriKalemSatirForm, SiparisUretimEmriSatirForm,
    OperasyonKaydiForm, OperasyonKaydiGirdiDuzeltForm, PersonelForm, PersonelIzinForm,
    PersonelBelgeForm, PersonelFotoForm, PersonelUcretForm, ResmiTatilForm,
    GirisForm, MesaiHesapOlusturForm, MesaiSifreForm, MesaiIzinliAgForm, MesaiDuzeltForm,
)
from core.models import (
    AdayAktivite, AdayAktiviteEk, AdayAsamaTanim, AdayMusteri, AdayMusteriKategori,
    AdayPotansiyelTanim, AdayTipTanim, AdayYetkili,
    Birim, Cari, CariAktivite, CariAktiviteEk, FaturaEk, CariBanka, CariKategori, CariSevkAdresi,
    CariYetkili, Depo, EkranYetki, Fatura, FaturaSatir, FasonKesim, FasonKesimKaydi,
    Banka, BankaHesap, CekBordrosu, CekSenet, DuranVarlik, FaturaTipi, FirmaBanka, HesapPlani, Kasa, Kategori, KdvOrani, Kredi, KrediKarti,
    KrediTaksit, Kur, KurDegerleme, Sehir, Stok, TanimSecenegi, TeklifSiparis, TevkifatOrani, Ulke, YatirimProjesi,
    YemekSayimi,
    YevmiyeFisi, YevmiyeSatir, KdvMahsup, PersonelBordro, IsIstasyonu, Operasyon, UretimEmri, UretimEmriKalemi, OperasyonKaydi,
    Personel, PersonelBelge, PersonelIzin, PersonelUcret, ResmiTatil,
    MesaiKaydi, MesaiIzinliAg,
)
from core.moduller import MODULLER
from core.metin import buyuk_harf_tr
from core.templatetags.core_extras import wa_link
from core import gorsel
from core.sayi import SayiHatasi, format_tr, parse_tr
from core.services.raporlar import (
    bilanco, bilanco_usd, ekstre as ekstre_servis,
    ekstre_devirli as ekstre_devirli_servis, gelir_tablosu, gelir_tablosu_usd,
    mali_yil_araligi, mizan, mizan_usd,
)
from core.services.yevmiye import (
    SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur, kur_usd_birebir,
)
from core.services.tcmb import TcmbHatasi, kurlari_guncelle
from core.services import duran_hesap as duran_hesap_servis
from core.services import hesap_plani as hp
from core.services import yedek as yedek_servis
from core.services import birim as birim_servis
from core.services import kategori as kategori_servis
from core.services import fatura_tipi as fatura_tipi_servis
from core.services import lokasyon as lokasyon_servis
from core.services import cari_kategori as cari_kategori_servis
from core.services import cari as cari_servis
from core.services import cari_kesinti as cari_kesinti_servis
from core.services import cari_virman as cari_virman_servis
from core.services import tanim as tanim_servis
from core.services import stok as stok_servis
from core.services import fatura as fatura_servis
from core.services import fatura_ek as fatura_ek_servis
from core.services import teklif_siparis as teklif_siparis_servis
from core.services import depo as depo_servis
from core.services import hareket as hareket_servis
from core.services import finans as finans_servis
from core.services import fis_sil as fis_sil_servis
from core.services import kasa_hareket as kasa_hareket_servis
from core.services import banka_hareket as banka_hareket_servis
from core.services import bordro as bordro_servis
from core.services import finans_ozet as finans_ozet_servis
from core.services import kdv_mahsup as kdv_servis
from core.services import kk_donem as kk_donem_servis
from core.services import doviz_islem as doviz_islem_servis
from core.services import kur_degerleme as kur_degerleme_servis
from core.services import donemsel_gider as donemsel_servis
from core.services import kur_farki as kur_farki_servis
from core.services import kredi_karti_hareket as kredi_karti_hareket_servis
from core.services import kredi_hareket as kredi_hareket_servis
from core.services import cek as cek_servis
from core.services import firma as firma_servis
from core.services import fason as fason_servis
from core.services import uretim as uretim_servis
from core.services import stok_maliyet
from core.services import yemek_takibi as yemek_takibi_servis
from core.services import aday as aday_servis
from core.services import aday_donustur as aday_donustur_servis
from core.services import aday_kategori as aday_kategori_servis
from core.services import aday_tanim as aday_tanim_servis
from core.services import personel as personel_servis
from core.services import personel_izin as izin_servis
from core.services import personel_belge as belge_servis
from core.services import personel_devam as devam_servis
from core.services import personel_ucret as ucret_servis
from core.services import personel_dokum as dokum_servis
from core.services import resmi_tatil as tatil_servis
from core.services import mesai as mesai_servis
from core.services import mesai_ag as mesai_ag_servis
from core.services import mesai_hesap as mesai_hesap_servis
from core.services import yatirim_projesi as yp_servis
from core.services import duran_varlik as dv_servis
from core.ip import istemci_ip
from core.tarih import ay_araligi, kidem_metni, tr_bugun
from core.yetki import (
    ekran_gerekli, ekran_gerekli_hepsi, ekran_gerekli_herhangi, ekran_gorebilir,
    kullanici_telefon, mesai_kullanicisi_mi, yonetici_gerekli, yonetici_mi,
)

SatirFormSet = formset_factory(SatirForm, extra=0, min_num=2, validate_min=True)


class GirisView(auth_views.LoginView):
    """Standart Django LoginView + "Beni hatırla" (bkz. core.forms.GirisForm). İşaretlenirse
    oturum 30 gün sürer; işaretlenmezse mevcut varsayılan (SESSION_COOKIE_AGE) hiç DEĞİŞMEZ —
    yalnız opt-in bir uzatma, başka hiçbir kullanıcı için davranış değişmez."""

    form_class = GirisForm

    def form_valid(self, form):
        yanit = super().form_valid(form)
        if form.cleaned_data.get("beni_hatirla"):
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        return yanit


@login_required
def pano(request):
    """Giriş sonrası açılan PANO (dashboard). Şimdilik karşılama;
    ileride özet/grafik eklenecek (yol haritası). Mesai kullanıcısı (self-servis, EkranYetki'siz)
    doğrudan kendi /mesai/ sayfasına yönlendirilir — LOGIN_REDIRECT_URL hep buraya düştüğü için."""
    if mesai_kullanicisi_mi(request.user):
        return redirect("core:mesaim")
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
                yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None,
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
    taban = YevmiyeFisi.objects.filter(tarih__gte=b, tarih__lte=s, silindi=False)
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
        taban = YevmiyeFisi.objects.filter(pk__in=eslesen, silindi=False)

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
    if fis.kaynak in (YevmiyeFisi.Kaynak.STOK_SARF, YevmiyeFisi.Kaynak.URETIM,
                      YevmiyeFisi.Kaynak.STOK_SATIS):
        messages.info(request, "Bu fiş stok maliyetinden otomatik üretilir; elle düzenlenemez "
                               "(tutar ortalama maliyetten gelir).")
        return redirect("core:fis_detay", pk=fis.pk)
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
    if fis.kaynak == YevmiyeFisi.Kaynak.CARI_KESINTI:
        messages.info(request, "Bu fiş bir cari kesinti/masraf hareketinden oluştu; cari ekstresinden düzenlenir.")
        return (redirect("core:cari_ekstresi", pk=fis.cari_id) if fis.cari_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.KDV_MAHSUP:
        messages.info(request, "Bu fiş bir KDV dönem mahsubundan oluştu; KDV Dönem Mahsubu ekranından düzenlenir.")
        return redirect("core:kdv_mahsup_listesi")
    if fis.kaynak == YevmiyeFisi.Kaynak.BORDRO:
        messages.info(request, "Bu fiş bir personel bordrosundan oluştu; bordro ekranından düzenlenir.")
        return (redirect("core:bordro_detay", pk=fis.personel_bordro_id) if fis.personel_bordro_id
                else redirect("core:fis_detay", pk=fis.pk))
    if fis.kaynak == YevmiyeFisi.Kaynak.CARI_VIRMAN:
        messages.info(request, "Bu fiş bir cari virman hareketinden oluştu; cari ekstresinden düzenlenir.")
        return (redirect("core:cari_ekstresi", pk=fis.cari_id) if fis.cari_id
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
        for s in fis.satirlar.filter(silindi=False, ana_satir__isnull=True).select_related("hesap"):
            borc_taraf = bool(s.borc and s.borc > 0)
            ilk.append({
                "hesap": s.hesap_id, "islem_pb": s.islem_pb,
                "borc": s.islem_tutari if borc_taraf else None,
                "alacak": None if borc_taraf else s.islem_tutari,
                "islem_kuru": s.islem_kuru, "aciklama": s.aciklama,
                "yatirim_projesi": s.yatirim_projesi_id,
            })
        formset = SatirFormSet(initial=ilk)
    return render(request, "core/fis_duzenle.html",
                  {"fform": fform, "formset": formset, "fis": fis})


def _fis_sil_akisi(request, fis, *, sil, geri, baslik):
    """KALICI silme akışı: GET → onay sayfası (fiş no, tarih, tutar, açıklama + varsa engel nedeni);
    POST → siler (yalnız süper kullanıcı) ve ``geri`` adresine döner."""
    ozet = fis_sil_servis.fis_ozeti(fis)
    if request.method == "POST":
        try:
            sil()
            messages.success(request, f"Silindi: {ozet['no']} · {ozet['tarih']:%d.%m.%Y} · "
                                      f"{format_tr(ozet['tutar'])} TL.")
        except ValueError as e:
            messages.error(request, str(e))
        return redirect(geri)
    yetkisiz = not request.user.is_superuser
    engel = None if yetkisiz else fis_sil_servis.onizle(sil)
    return render(request, "core/fis_sil_onay.html", {
        "baslik": baslik, "ozet": ozet, "engel": engel, "yetkisiz": yetkisiz, "geri": geri,
        "eylem": request.path})


@ekran_gerekli("fis_listesi")
def fis_sil_gorunum(request, pk):
    fis = get_object_or_404(YevmiyeFisi, pk=pk)
    if fis.kaynak == YevmiyeFisi.Kaynak.FATURA and not fis.silindi:
        fat = fis.faturalar.filter(silindi=False).first()
        if fat:
            messages.info(request, "Bu fiş bir faturadan oluştu; iptal için faturayı iptal edin.")
            return redirect("core:fatura_detay", pk=fat.pk)
    if fis.kaynak == YevmiyeFisi.Kaynak.YATIRIM and not fis.silindi:
        proje = fis.yatirim_projesi_aktiflestirmeleri.first()
        if proje:
            messages.info(request, "Bu fiş bir yatırım projesi aktifleştirmesinden oluştu; "
                                   "iptal için proje detayındaki 'Aktifleştirmeyi Geri Al'ı kullanın.")
            return redirect("core:yatirim_projesi_detay", pk=proje.pk)
    if fis.kaynak == YevmiyeFisi.Kaynak.KASA and fis.kasa_id:
        messages.info(request, "Bu fiş bir kasa hareketinden oluştu; silmek için kasa detayını kullanın.")
        return redirect("core:kasa_detay", pk=fis.kasa_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.BANKA and fis.banka_hesap_id:
        messages.info(request, "Bu fiş bir banka hareketinden oluştu; silmek için banka hesabı detayını kullanın.")
        return redirect("core:banka_hesap_detay", pk=fis.banka_hesap_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.CEK_SENET and fis.cek_bordrosu_id:
        messages.info(request, "Bu fiş bir çek/senet bordrosundan oluştu; silmek için bordro detayını kullanın.")
        return redirect("core:cek_bordro_detay", pk=fis.cek_bordrosu_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI_KARTI and fis.kredi_karti_id:
        messages.info(request, "Bu fiş bir kredi kartı hareketinden oluştu; silmek için kredi kartı detayını kullanın.")
        return redirect("core:kredi_karti_detay", pk=fis.kredi_karti_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.CARI_KESINTI and fis.cari_id:
        messages.info(request, "Bu fiş bir cari kesinti/masraf hareketinden oluştu; silmek için cari ekstresini kullanın.")
        return redirect("core:cari_ekstresi", pk=fis.cari_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.KDV_MAHSUP:
        messages.info(request, "Bu fiş bir KDV dönem mahsubundan oluştu; silmek için KDV Dönem Mahsubu ekranını kullanın.")
        return redirect("core:kdv_mahsup_listesi")
    if fis.kaynak == YevmiyeFisi.Kaynak.BORDRO and fis.personel_bordro_id:
        messages.info(request, "Bu fiş bir personel bordrosundan oluştu; silmek için bordro ekranını kullanın.")
        return redirect("core:bordro_detay", pk=fis.personel_bordro_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.CARI_VIRMAN and fis.cari_id:
        messages.info(request, "Bu fiş bir cari virman hareketinden oluştu; silmek için cari ekstresini kullanın.")
        return redirect("core:cari_ekstresi", pk=fis.cari_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.KREDI and fis.kredi_id:
        messages.info(request, "Bu fiş bir kredi hareketinden oluştu; silmek için kredi detayını kullanın.")
        return redirect("core:kredi_detay", pk=fis.kredi_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.STOK_SARF and not fis.silindi:
        hareket = fis.stok_sarf_hareketleri.filter(silindi=False).first()
        if hareket:
            messages.info(request, "Bu fiş bir stok sarf çıkışından oluştu; iptal için "
                                   "stok detayındaki hareketi silin.")
            return redirect("core:stok_detay", pk=hareket.stok_id)
    if fis.kaynak == YevmiyeFisi.Kaynak.URETIM and not fis.silindi:
        messages.info(request, "Bu fiş bir üretim kaydının maliyet aktarımıdır; elle silinemez "
                               "(üretim kaydı ve stok hareketleriyle birlikte yönetilir).")
        return redirect("core:fis_detay", pk=fis.pk)
    if fis.kaynak == YevmiyeFisi.Kaynak.STOK_SATIS and not fis.silindi:
        hareket = fis.stok_sarf_hareketleri.filter(silindi=False).first()
        fatura = hareket.fatura_satir.fatura if hareket and hareket.fatura_satir_id else None
        messages.info(request, "Bu fiş bir satış faturasının stok maliyetidir; silmek için "
                               "faturayı düzenleyin/silin.")
        return redirect("core:fatura_detay", pk=fatura.pk) if fatura else redirect(
            "core:fis_detay", pk=fis.pk)
    if fis.kaynak != YevmiyeFisi.Kaynak.MANUEL:
        messages.error(request, "Bu fiş buradan silinemez; kaynak ekranından yönetilir.")
        return redirect("core:fis_detay", pk=fis.pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Fiş {fis.yil}/{fis.fis_no}", geri=reverse("core:fis_listesi"),
        sil=lambda: fis_sil_servis.fis_sil(fis, kullanici=request.user,
                                           izinli_kaynaklar={YevmiyeFisi.Kaynak.MANUEL}))


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
    """Fiş/Fatura/İrsaliye/Kasa/Banka/Kredi Kartı/Çek ekranı önizleme için: tarihe
    (+ opsiyonel ?pb=) göre TCMB kuru. ?cari_id verilirse o carinin kur_tipi tercihine
    göre (bkz. Kur.deger) doğru alan okunur; verilmezse ?pb'siz eski davranış (USD,
    kur_usd_birebir) VEYA ?pb'li MB Alış korunur — mevcut çağıranlar (manuel Yevmiye
    Fişi ekranı dahil) hiç değişmeden çalışır. TRY için hep '1' döner; carry-forward YOK
    (tam o tarih için kayıt yoksa kur=null — bkz. core.services.fatura._kur_coz ile aynı
    kural)."""
    ham = request.GET.get("tarih")
    pb = (request.GET.get("pb") or "USD").upper()
    cari_id = request.GET.get("cari_id")
    kur = None
    if ham:
        try:
            t = datetime.date.fromisoformat(ham)
        except ValueError:
            t = None
        if t is not None:
            if pb == "TRY":
                kur = "1"
            elif cari_id:
                kur_tipi = (Cari.objects.filter(pk=cari_id, silindi=False)
                           .values_list("kur_tipi", flat=True).first()) or Cari.KurTipi.MB_ALIS
                kayit = Kur.objects.filter(tarih=t, silindi=False).first()
                deger = kayit.deger(pb, kur_tipi) if kayit else None
                if deger:
                    kur = str(deger)
            elif pb == "USD":
                k = kur_usd_birebir(t)
                if k is not None:
                    kur = str(k)
            elif pb in ("EUR", "GBP"):
                alan = {"EUR": "eur_alis", "GBP": "gbp_alis"}[pb]
                kayit = Kur.objects.filter(tarih=t, silindi=False).first()
                deger = getattr(kayit, alan, None) if kayit else None
                if deger:
                    kur = str(deger)
    return JsonResponse({"kur": kur})


@login_required
def cari_vade_api(request, pk):
    """Fatura ekranı önizleme: carinin ödeme koşulu tanımlıysa ?tarih=YYYY-MM-DD'ye göre
    hesaplanan vade tarihini döner (bkz. core.services.cari.vade_hesapla). Koşul yoksa veya
    tarih geçersizse ``vade_tarihi: null`` döner — kullanıcı vadeyi elle girer."""
    cari = Cari.objects.filter(pk=pk, silindi=False).first()
    vade = None
    ham = request.GET.get("tarih")
    if cari is not None and ham:
        try:
            t = datetime.date.fromisoformat(ham)
        except ValueError:
            t = None
        if t is not None:
            vade = cari_servis.vade_hesapla(cari, t)
    return JsonResponse({"vade_tarihi": vade.isoformat() if vade else None})


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
    # Üst (ara/ana) hesap kodları KODDAN türetilir (ayrı ust_hesap FK yok). Bu küme TÜM
    # kayıtlardan (filtre UYGULANMADAN) çıkarılır — filtrelenmiş görünümde bile "üst/yaprak"
    # ve "silinebilir" durumu gerçek veriyle tutarlı kalsın diye.
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
            "grup": h.rapor_grubu, "grup_ad": h.get_rapor_grubu_display(),
            "aktif": h.aktif,
        })

    ara = (request.GET.get("ara") or "").strip()
    grup = (request.GET.get("grup") or "").strip()
    durum = request.GET.get("durum") or "hepsi"
    if ara:
        ara_lower = ara.lower()
        agac = [h for h in agac
                if ara_lower in h["kod"].lower() or ara_lower in h["ad"].lower()]
    if grup in HesapPlani.RaporGrubu.values:
        agac = [h for h in agac if h["grup"] == grup]
    if durum == "aktif":
        agac = [h for h in agac if h["aktif"]]
    elif durum == "pasif":
        agac = [h for h in agac if not h["aktif"]]

    ust_kodu = request.GET.get("ust")
    ust_hesap = (HesapPlani.objects.filter(hesap_kodu=ust_kodu, silindi=False).first()
                 if ust_kodu else None)
    onerilen = hp.alt_kod_oner(ust_hesap) if ust_hesap else ""
    onerilen_silinmis = bool(onerilen) and hp.onerilen_kod_silinmis_mi(onerilen)
    duzenle_kod = request.GET.get("duzenle")
    duzenlenecek = (HesapPlani.objects.filter(hesap_kodu=duzenle_kod, silindi=False).first()
                    if duzenle_kod else None)
    return render(request, "core/hesap_plani.html", {
        "agac": agac, "ust": ust_hesap, "onerilen_kod": onerilen,
        "onerilen_silinmis": onerilen_silinmis,
        "duzenlenecek": duzenlenecek,
        "rapor_gruplari": HesapPlani.RaporGrubu.choices,
        "rapor_kalemleri": RAPOR_KALEMLERI,
        "ara": ara, "grup": grup, "durum": durum,
        "filtre_aktif": bool(ara or grup or durum != "hepsi"),
    })


def _parasal_coz(deger):
    return {"e": True, "h": False}.get(deger)


@ekran_gerekli("hesap_plani")
def hesap_ekle(request):
    if request.method == "POST":
        alanlar = dict(
            kod=request.POST.get("kod", ""),
            ad=request.POST.get("ad", ""),
            ust_kodu=(request.POST.get("ust_kodu") or "").strip() or None,
            rapor_grubu=(request.POST.get("rapor_grubu") or "").strip() or None,
            rapor_kalemi=(request.POST.get("rapor_kalemi") or "").strip(),
            parasal=_parasal_coz(request.POST.get("parasal")),
            kullanici=request.user,
        )
        try:
            h = hp.hesap_olustur(mevcut_hareketleri_tasi=request.POST.get("tasi") == "1", **alanlar)
            ek = (f" — {h.tasinan_satir_sayisi} satır {h.hesap_kodu} hesabına taşındı"
                  if getattr(h, "tasinan_satir_sayisi", 0) else "")
            messages.success(request, f"Hesap eklendi: {h.hesap_kodu} — {h.hesap_adi}{ek}")
        except hp.HareketliUstHesapHatasi as e:
            # Hareketli yaprağa alt hesap: uyar ve aynı taşıma işlemini sun (onay sayfası).
            return render(request, "core/hesap_tasima_onay.html", {
                "ust": e.ust, "satir_sayisi": e.satir_sayisi, "alanlar": alanlar,
                "alt_kod": alanlar["kod"], "alt_ad": buyuk_harf_tr((alanlar["ad"] or "").strip()),
                "ilk_satirlar": YevmiyeSatir.objects.filter(hesap_id=e.ust.hesap_kodu, silindi=False)
                .select_related("fis").order_by("fis__tarih", "fis__fis_no")[:8]})
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
        "son_yedek": yedek_servis.son_yedek(),      # yalnız VERİTABANI yedeği (evrak arşivi değil)
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
    kategori_id = request.GET.get("kategori") or ""
    qs = stok_servis.aktif_stoklar()
    if ara:
        buyuk = buyuk_harf_tr(ara)
        qs = qs.filter(
            Q(kod__icontains=ara) | Q(ad__contains=buyuk)
            | Q(kategori__ad__contains=buyuk) | Q(kategori__ust__ad__contains=buyuk))
    if kategori_id:
        qs = qs.filter(kategori_id=kategori_id)
    # Filtre seçenekleri yalnız en az bir stokta fiilen kullanılan ALT kategorilerden
    # oluşur (bkz. aday müşteri listesindeki aynı desen) — tüm kategori master verisini
    # değil, ekrandaki gerçek veriyi yansıtır.
    kategoriler = Kategori.objects.filter(
        silindi=False, pk__in=stok_servis.aktif_stoklar().exclude(kategori=None).values("kategori_id")
    ).select_related("ust").order_by("ust__ad", "ad")
    stok_listesi = list(qs)
    eldeki = hareket_servis.toplu_eldeki(s.pk for s in stok_listesi)
    for s in stok_listesi:
        s.eldeki = eldeki.get(s.pk, hareket_servis.SIFIR)
    return render(request, "core/stok_listesi.html", {
        "stoklar": stok_listesi, "ara": ara, "secili_kategori": kategori_id,
        "kategoriler": kategoriler,
    })


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
                    tedarikci_adi=cd.get("tedarikci_adi"),
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
                    tedarikci_adi=cd.get("tedarikci_adi"),
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
            "tedarikci_adi": stok.tedarikci_adi,
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
    """Stok kartı detay sayfası (read-only): mevcut miktar (toplam + depo dağılımı + kritik
    seviye uyarısı) ve fiyatlar üstte, sonra stok hareketleri, temel bilgiler ve (satış
    ürünüyse) teklif/teknik özellikler, en altta kayıt bilgisi."""
    stok = get_object_or_404(
        Stok.objects.select_related(
            "kategori", "kategori__ust", "uretim_birimi", "fatura_birimi", "kdv", "tevkifat",
            "tedarikci", "created_by", "updated_by"),
        pk=pk, silindi=False)
    eldeki = hareket_servis.eldeki_miktar(stok)
    hareketler = hareket_servis.stok_hareketleri(stok)
    return render(request, "core/stok_detay.html", {
        "stok": stok, "eldeki": eldeki,
        "kritik_alti": stok.kritik_stok > 0 and eldeki < stok.kritik_stok,
        # Yalnız stoğu OLAN depolar — net 0'a inmiş depo "stok nerede" sorusunda gürültüdür
        # (geçmişi hareket defterinde zaten görünür).
        "depo_bakiye": [(d, m) for d, m in hareket_servis.depo_bazinda_eldeki(stok) if m != 0],
        "hareketler": hareketler[:100], "hareket_sayisi": hareketler.count(),
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
def stok_depo_transferi(request, pk):
    """Aynı stoğu depolar arasında taşır (maliyeti değiştirmez, fiş üretmez)."""
    from core.services import depo_transfer
    stok = get_object_or_404(Stok.objects.select_related("uretim_birimi"), pk=pk, silindi=False)
    if request.method == "POST":
        form = DepoTransferForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                depo_transfer.depo_transferi_yap(
                    stok_id=stok.pk, kaynak_depo_id=cd["kaynak_depo"].pk,
                    hedef_depo_id=cd["hedef_depo"].pk, tarih=cd["tarih"], miktar=cd["miktar"],
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, "Depo transferi yapıldı.")
                return redirect("core:stok_detay", pk=stok.pk)
            except hareket_servis.HareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = DepoTransferForm()
    return render(request, "core/stok_transfer_form.html", {"form": form, "stok": stok})


@ekran_gerekli("stoklar")
def stok_hareket_sil(request, pk):
    from core.models import StokHareket
    hareket = get_object_or_404(StokHareket, pk=pk, silindi=False)
    stok_pk = hareket.stok_id
    if request.method == "POST":
        try:
            if hareket.kaynak == StokHareket.Kaynak.TRANSFER and hareket.transfer_grubu:
                from core.services import depo_transfer
                depo_transfer.depo_transferi_sil(hareket.transfer_grubu, kullanici=request.user)
                messages.success(request, "Depo transferi silindi (iki hareket).")
                return redirect("core:stok_detay", pk=stok_pk)
            hareket_servis.hareket_sil(hareket, kullanici=request.user)
            messages.success(request, "Stok hareketi silindi.")
        except hareket_servis.HareketHatasi as e:
            messages.error(request, str(e))
    return redirect("core:stok_detay", pk=stok_pk)


@ekran_gerekli("stoklar")
def stok_degerleme_raporu(request):
    """Kart bazında miktar/ortalama maliyet/değer + 150-153 mizan karşılaştırması (salt okunur)."""
    from core.services import stok_ortalama
    return render(request, "core/stok_degerleme.html",
                  {"rapor": stok_ortalama.degerleme_raporu()})


@ekran_gerekli("stoklar")
def stok_sarf_ekle(request, pk):
    """Stoktan hesaba/yatırım projesine SARF çıkışı: miktar + muhasebe fişi bir arada
    (bkz. core.services.hareket.sarf_cikis_ekle). Bugünkü fişsiz '+ Hareket > Çıkış'
    seçeneği DEĞİŞMEDİ — bu ayrı, fiş üreten bir ekrandır."""
    stok = get_object_or_404(Stok.objects.select_related("uretim_birimi", "kategori"),
                             pk=pk, silindi=False)
    if request.method == "POST":
        form = SarfCikisForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                hareket_servis.sarf_cikis_ekle(
                    stok_id=stok.pk, depo_id=cd["depo"].pk, tarih=cd["tarih"],
                    miktar=cd["miktar"], karsi_hesap_id=cd["karsi_hesap"].pk,
                    yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None,
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, "Sarf çıkışı ve muhasebe fişi oluşturuldu.")
                return redirect("core:stok_detay", pk=stok.pk)
            except hareket_servis.HareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = SarfCikisForm()
    kodlar_258 = {str(h.pk): hp.hesap_kodu_258_mi(h.hesap_kodu)
                 for h in form.fields["karsi_hesap"].queryset}
    return render(request, "core/stok_sarf_form.html",
                  {"form": form, "stok": stok, "kodlar_258": kodlar_258})


@ekran_gerekli("stoklar")
def stok_sarf_onizleme_api(request, pk):
    """Sarf ekranı için AJAX önizleme: {depo, miktar} -> birim maliyet + toplam tutar."""
    stok = get_object_or_404(Stok, pk=pk, silindi=False)
    depo_id = request.GET.get("depo")
    miktar = request.GET.get("miktar")
    if not depo_id or not miktar:
        return JsonResponse({"hata": None, "tutar_try": None})
    try:
        sonuc = hareket_servis.sarf_onizleme(stok_id=stok.pk, depo_id=depo_id, miktar=miktar)
    except hareket_servis.HareketHatasi as e:
        return JsonResponse({"hata": str(e), "tutar_try": None})
    return JsonResponse({
        "hata": None,
        "tutar_try": format_tr(sonuc["tutar_try"], 2) if sonuc["tutar_try"] is not None else None,
        "birim_maliyet_try": (format_tr(sonuc["birim_maliyet_try"], 4)
                              if sonuc["birim_maliyet_try"] is not None else None),
        "tam_mi": sonuc["tam_mi"],
    })


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
            "ad": tip.ad, "yon": tip.yon, "sira": tip.sira, "gider": tip.gider,
            "stopajli": tip.stopajli})
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
        telefon=cd["telefon"], telefon_whatsapp=cd["telefon_whatsapp"],
        telefon_2=cd["telefon_2"], telefon_2_whatsapp=cd["telefon_2_whatsapp"],
        eposta=cd["eposta"],
        web=cd["web"], ilgili_kisi=cd["ilgili_kisi"], kep_adresi=cd["kep_adresi"],
        ulke_id=g(cd["ulke"]), sehir_id=g(cd["sehir"]), adres=cd["adres"],
        para_birimi=cd["para_birimi"], kur_tipi=cd["kur_tipi"], kur_degerleme=cd.get("kur_degerleme"),
        kur_farki_hedefi=cd.get("kur_farki_hedefi"),
        kredi_limiti=cd["kredi_limiti"],
        iskonto_yuzdesi=cd["iskonto_yuzdesi"],
        odeme_kosulu=cd["odeme_kosulu"] or None, odeme_gunu=cd["odeme_gunu"],
        notlar=cd["notlar"])


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
    ).select_related("ust").order_by("ust__kod", "kod")
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
                for uyari in getattr(c, "telefon_uyarilari", []):
                    messages.error(request, uyari)
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
    kategori_kilitli = cari_servis.cari_hareketli_mi(cari)
    if request.method == "POST":
        form = CariForm(request.POST)
        if kategori_kilitli:
            form.fields["kategori"].widget.attrs["disabled"] = True
        if form.is_valid():
            try:
                kw = _cari_form_kw(form.cleaned_data)
                if kategori_kilitli:
                    # Kilitli alan: tarayıcı disabled select'i hiç göndermez; servis
                    # katmanı da aynı kuralı zorluyor ama burada hiç denemiyoruz.
                    kw["kategori_id"] = cari.kategori_id
                guncellenen = cari_servis.cari_guncelle(
                    cari, **kw, kullanici=request.user)
                for uyari in getattr(guncellenen, "telefon_uyarilari", []):
                    messages.error(request, uyari)
                messages.success(request, "Cari güncellendi.")
                return redirect("core:cari_detay", pk=cari.pk)
            except cari_servis.CariHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariForm(initial={
            "unvan": cari.unvan, "kisa_ad": cari.kisa_ad, "kategori": cari.kategori_id,
            "vergi_dairesi": cari.vergi_dairesi, "vkn_tckn": cari.vkn_tckn,
            "tax_id": cari.tax_id, "telefon": cari.telefon,
            "telefon_whatsapp": cari.telefon_whatsapp,
            "telefon_2": cari.telefon_2, "telefon_2_whatsapp": cari.telefon_2_whatsapp,
            "eposta": cari.eposta, "web": cari.web, "ilgili_kisi": cari.ilgili_kisi,
            "kep_adresi": cari.kep_adresi,
            "ulke": cari.ulke_id, "sehir": cari.sehir_id, "adres": cari.adres,
            "para_birimi": cari.para_birimi, "kur_tipi": cari.kur_tipi,
            "kur_degerleme": cari.kur_degerleme, "kur_farki_hedefi": cari.kur_farki_hedefi,
            "kredi_limiti": cari.kredi_limiti,
            "iskonto_yuzdesi": cari.iskonto_yuzdesi,
            "odeme_kosulu": cari.odeme_kosulu or "", "odeme_gunu": cari.odeme_gunu,
            "notlar": cari.notlar})
        if kategori_kilitli:
            form.fields["kategori"].widget.attrs["disabled"] = True
    return render(request, "core/cari_form.html",
                  {"form": form, "baslik": "Cari Düzenle", "duzenlenen": cari,
                   "kategori_kilitli": kategori_kilitli})


def _cari_kur_farki_projesi(cari):
    """Döviz hareketi olan carinin kur farkı yatırım maliyetine gidiyorsa o proje (bkz.
    core.services.kur_farki.yatirim_hedefi), değilse None."""
    if not cari.muhasebe_kodu:
        return None
    doviz = cari.para_birimi != "TRY" or YevmiyeSatir.objects.filter(
        hesap_id=cari.muhasebe_kodu, silindi=False, fis__silindi=False).exclude(islem_pb="TRY").exists()
    return kur_farki_servis.yatirim_hedefi(cari.muhasebe_kodu) if doviz else None


@ekran_gerekli("cariler")
def cari_detay(request, pk):
    cari = get_object_or_404(
        Cari.objects.select_related("kategori", "kategori__ust", "ulke", "sehir",
                                    "created_by", "updated_by"),
        pk=pk, silindi=False)
    return render(request, "core/cari_detay.html", {
        "cari": cari,
        "kur_farki_proje": _cari_kur_farki_projesi(cari),
        "bankalar": cari_servis.aktif_bankalar(cari),
        "yetkililer": cari_servis.aktif_yetkililer(cari),
        "sevk_adresleri": cari_servis.aktif_sevk_adresleri(cari),
        "aktiviteler": cari_servis.aktif_aktiviteler(cari),
        # Cariye Dönüştür — bu carinin kaynağı olan aday(lar) (spec madde 4: birden çok
        # aday bağlıysa hepsi listelenir; (B) mevcut cariye bağlama birden fazlasına izin verir).
        "kaynak_adaylar": cari.kaynak_adaylar.filter(silindi=False).order_by("unvan")})


@ekran_gerekli("cariler")
def cari_kesinti_ekle(request, pk):
    """Cari kartından Kesinti / Masraf hareketi (manuel fiş yerine)."""
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    try:
        yon = cari_kesinti_servis.yon_coz(cari)
    except cari_kesinti_servis.CariKesintiHatasi as e:
        messages.error(request, str(e))
        return redirect("core:cari_detay", pk=cari.pk)
    ortak = not (cari.muhasebe_kodu or "").startswith("500.")
    if request.method == "POST":
        form = CariKesintiForm(request.POST, ortak_secenegi=ortak, doviz_secenegi=cari.para_birimi != "TRY")
        if form.is_valid():
            cd = form.cleaned_data
            try:
                fis = cari_kesinti_servis.kesinti_olustur(
                    cari=cari, tarih=cd["tarih"], tutar=cd["tutar"],
                    gider_kodu=cd["gider"].hesap_kodu if cd.get("gider") else None,
                    karsi_cari=cd.get("karsi_cari"), para_birimi=cd.get("para_birimi") or "TRY", kur=cd.get("kur"),
                    sayilan_pb=cd.get("sayilan_pb"),
                    aciklama=cd["aciklama"], kullanici=request.user,
                    yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None)
                messages.success(request, f"Kesinti / masraf kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:cari_ekstresi", pk=cari.pk)
            except cari_kesinti_servis.CariKesintiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariKesintiForm(ortak_secenegi=ortak, doviz_secenegi=cari.para_birimi != "TRY")
    return render(request, "core/cari_kesinti_form.html", {
        "cari": cari, "form": form, "yon": yon, "duzenle": False,
        "havuzlar": cari_kesinti_servis.havuz_bakiyeleri(cari) if cari.para_birimi != "TRY" else []})


@ekran_gerekli("cariler")
def cari_kesinti_duzenle(request, pk, fis_pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    try:
        bilgi = cari_kesinti_servis.duzenleme_bilgisi(fis, cari)
    except cari_kesinti_servis.CariKesintiHatasi as e:
        messages.error(request, str(e))
        return redirect("core:cari_ekstresi", pk=cari.pk)
    ortak = not (cari.muhasebe_kodu or "").startswith("500.")
    if request.method == "POST":
        form = CariKesintiForm(request.POST, ortak_secenegi=ortak, doviz_secenegi=cari.para_birimi != "TRY")
        if form.is_valid():
            cd = form.cleaned_data
            try:
                cari_kesinti_servis.kesinti_guncelle(
                    fis=fis, cari=cari, tarih=cd["tarih"], tutar=cd["tutar"],
                    gider_kodu=cd["gider"].hesap_kodu if cd.get("gider") else None,
                    karsi_cari=cd.get("karsi_cari"), para_birimi=cd.get("para_birimi") or "TRY", kur=cd.get("kur"),
                    sayilan_pb=cd.get("sayilan_pb"),
                    aciklama=cd["aciklama"], kullanici=request.user,
                    yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None)
                messages.success(request, f"Kesinti / masraf güncellendi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:cari_ekstresi", pk=cari.pk)
            except cari_kesinti_servis.CariKesintiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariKesintiForm(initial={
            "tarih": fis.tarih, "tutar": bilgi["tutar"],
            **({"gider": bilgi["gider"].hesap_kodu} if bilgi["gider"] is not None else {}),
            "karsi_cari": bilgi["karsi_cari"].pk if bilgi["karsi_cari"] else None,
            "yatirim_projesi": bilgi["yatirim_projesi_id"], "aciklama": fis.aciklama,
            "para_birimi": bilgi["para_birimi"], "kur": bilgi["kur"] if bilgi["para_birimi"] != "TRY" else None},
            ortak_secenegi=ortak, doviz_secenegi=cari.para_birimi != "TRY")
    return render(request, "core/cari_kesinti_form.html", {
        "cari": cari, "form": form, "yon": bilgi["yon"], "duzenle": True, "fis": fis,
        "havuzlar": cari_kesinti_servis.havuz_bakiyeleri(cari) if cari.para_birimi != "TRY" else []})


@ekran_gerekli("cariler")
def cari_virman_ekle(request, pk):
    """Cariler arası virman (bu cari ↔ karşı cari; manuel mahsup fişi yerine)."""
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    if request.method == "POST":
        form = CariVirmanForm(request.POST, cari=cari)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                fis = cari_virman_servis.virman_olustur(
                    cari=cari, karsi_cari=cd.get("karsi_cari"), tarih=cd["tarih"], tutar=cd["tutar"], yon=cd["yon"],
                    aciklama=cd["aciklama"], sayilan_pb=cd.get("sayilan_pb"), kullanici=request.user,
                    karsi_hesap_kodu=cd["karsi_hesap"].hesap_kodu if cd.get("karsi_hesap") else None,
                    yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None,
                    kur=cd.get("kur"))
                messages.success(request, f"Virman kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:cari_ekstresi", pk=cari.pk)
            except cari_virman_servis.CariVirmanHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariVirmanForm(cari=cari)
    return render(request, "core/cari_virman_form.html", {"cari": cari, "form": form, "duzenle": False})


@ekran_gerekli("cariler")
def cari_virman_duzenle(request, pk, fis_pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    try:
        bilgi = cari_virman_servis.duzenleme_bilgisi(fis, cari)
    except cari_virman_servis.CariVirmanHatasi as e:
        messages.error(request, str(e))
        return redirect("core:cari_ekstresi", pk=cari.pk)
    if request.method == "POST":
        form = CariVirmanForm(request.POST, cari=cari)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                cari_virman_servis.virman_guncelle(
                    fis=fis, cari=cari, karsi_cari=cd.get("karsi_cari"), tarih=cd["tarih"], tutar=cd["tutar"], yon=cd["yon"],
                    aciklama=cd["aciklama"], sayilan_pb=cd.get("sayilan_pb"), kullanici=request.user,
                    karsi_hesap_kodu=cd["karsi_hesap"].hesap_kodu if cd.get("karsi_hesap") else None,
                    yatirim_projesi_id=cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None,
                    kur=cd.get("kur"))
                messages.success(request, f"Virman güncellendi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:cari_ekstresi", pk=cari.pk)
            except cari_virman_servis.CariVirmanHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CariVirmanForm(cari=cari, initial={
            "tarih": fis.tarih, "tutar": bilgi["tutar"], "kur": bilgi["kur"], "yon": bilgi["yon"],
            "karsi_cari": bilgi["karsi_cari"].pk if bilgi["karsi_cari"] else None,
            "karsi_hesap": bilgi["karsi_hesap"].hesap_kodu if bilgi["karsi_hesap"] else None,
            "yatirim_projesi": bilgi["yatirim_projesi_id"],
            "sayilan_pb": bilgi["sayilan_pb"], "aciklama": fis.aciklama})
    return render(request, "core/cari_virman_form.html", {"cari": cari, "form": form, "duzenle": True, "fis": fis})


@ekran_gerekli("cariler")
def cari_virman_sil(request, pk, fis_pk):
    """Virman hareketini KALICI siler (fiş dahil; onay sayfası; yalnız süper kullanıcı)."""
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Cari virman · {cari.unvan}", geri=reverse("core:cari_ekstresi", args=[cari.pk]),
        sil=lambda: cari_virman_servis.virman_sil(fis=fis, cari=cari, kullanici=request.user))


@ekran_gerekli("cariler")
def cari_kesinti_sil(request, pk, fis_pk):
    """Kesinti / masraf hareketini KALICI siler (onay sayfası; yalnız süper kullanıcı)."""
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Kesinti / masraf · {cari.unvan}", geri=reverse("core:cari_ekstresi", args=[cari.pk]),
        sil=lambda: cari_kesinti_servis.kesinti_sil(fis=fis, cari=cari, kullanici=request.user))


@ekran_gerekli("cariler")
def cari_ekstresi(request, pk):
    cari = get_object_or_404(Cari, pk=pk, silindi=False)
    form, b, s = _tarih_araligi(request)
    eks = ekstre_devirli_servis(cari.muhasebe_kodu, b, s) if cari.muhasebe_kodu else None
    kesinti_fis_pks = set(YevmiyeFisi.objects.filter(
        kaynak=YevmiyeFisi.Kaynak.CARI_KESINTI, cari=cari, silindi=False).values_list("pk", flat=True))
    return render(request, "core/cari_ekstresi.html",
                  {"cari": cari, "form": form, "ekstre": eks, "kesinti_fis_pks": kesinti_fis_pks,
                   "virman_fis_pks": cari_virman_servis.cari_virman_fisleri(cari)})


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
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user,
                    kur_override=form.cleaned_data.get("kur"), sayilan_pb=form.cleaned_data.get("sayilan_pb"))
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
def kasa_hareket_sil(request, pk, fis_pk):
    """Kasa hareketini (kaynak=KASA fiş) KALICI siler — kasa detayından (ham fiş ekranı kilitli)."""
    kasa = get_object_or_404(Kasa, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Kasa hareketi · {kasa.ad}", geri=reverse("core:kasa_detay", args=[kasa.pk]),
        sil=lambda: kasa_hareket_servis.hareket_sil(fis=fis, kasa=kasa, kullanici=request.user))


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

    banka_duzenle_pks = set()
    if hesap.para_birimi == "TRY":       # düzenlenebilir: TL hesaptan cariye ödeme (cari satırı BORÇ)
        banka_duzenle_pks = set(YevmiyeSatir.objects.filter(
            fis_id__in=banka_fis_pks, silindi=False, ana_satir__isnull=True, borc__gt=0,
            hesap_id__in=Cari.objects.filter(silindi=False).exclude(muhasebe_kodu="").values("muhasebe_kodu"),
        ).values_list("fis_id", flat=True))
    gorunen = {s.fis_pk for s in satirlar} & banka_fis_pks      # Hesaba Ödeme / Hesaptan Giriş (tek ya da bölünmüş): her para biriminde Düzenle
    for f in YevmiyeFisi.objects.filter(pk__in=gorunen - banka_duzenle_pks):
        if banka_hareket_servis.hesap_hareketi_uygun_mu(f, hesap):
            banka_duzenle_pks.add(f.pk)

    return render(request, "core/banka_hesap_detay.html",
                  {"hesap": hesap, "form": form, "ekstre": ekstre,
                   "satirlar": satirlar, "aciklama": aciklama,
                   "banka_fis_pks": banka_fis_pks, "banka_duzenle_pks": banka_duzenle_pks})


BankaHesapSatirFormSet = formset_factory(BankaHesapSatirForm, extra=0, min_num=1, validate_min=True)


def _banka_hesap_hareket_form(request, hesap, tip):
    """Hesaba Ödeme / Hesaptan Giriş: karşı taraf hesap planından bir ya da birden çok muavin hesap."""
    tan = banka_hareket_servis.HAREKET[tip]
    if request.method == "POST":
        form = BankaHesapHareketForm(request.POST)
        formset = BankaHesapSatirFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            satirlar = [
                {"hesap_kodu": f.cleaned_data["hesap"].hesap_kodu,
                 "tutar": f.cleaned_data.get("tutar"),
                 "aciklama": f.cleaned_data.get("aciklama", ""),
                 "yatirim_projesi_id": (f.cleaned_data["yatirim_projesi"].pk
                                        if f.cleaned_data.get("yatirim_projesi") else None)}
                for f in formset if f.cleaned_data.get("hesap")]
            try:
                fis = banka_hareket_servis.hareket_olustur(
                    banka_hesap=hesap, tip=tip, karsi=None, satirlar=satirlar,
                    tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user,
                    kur_override=form.cleaned_data.get("kur"))
                messages.success(request, f"{tan['ad']} kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:banka_hesap_detay", pk=hesap.pk)
            except banka_hareket_servis.BankaHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaHesapHareketForm()
        formset = BankaHesapSatirFormSet()
    return render(request, "core/banka_hesap_hareket_form.html",
                  {"hesap": hesap, "form": form, "formset": formset, "tip": tip, "tan": tan})


def _banka_hareket_form(request, hesap, tip):
    """Tipe göre banka hesabı hareketi formu (GET) / kaydı (POST) → otomatik dengeli fiş."""
    tan = banka_hareket_servis.HAREKET[tip]
    if tan["karsi"] == "hesap":
        return _banka_hesap_hareket_form(request, hesap, tip)
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
                    aciklama=form.cleaned_data["aciklama"], kullanici=request.user,
                    kur_override=form.cleaned_data.get("kur"), sayilan_pb=form.cleaned_data.get("sayilan_pb"),
                    sayilan_doviz=form.cleaned_data.get("sayilan_doviz"))
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
    """Banka hesabı hareketi — 7 tip de tipe göre form + otomatik dengeli fiş (kaynak=BANKA)."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    if tip not in banka_hareket_servis.HAREKET:
        messages.error(request, "Geçersiz hareket tipi.")
        return redirect("core:banka_hesap_detay", pk=hesap.pk)
    return _banka_hareket_form(request, hesap, tip)


@ekran_gerekli("kur_degerleme")
def kur_degerleme(request):
    """Dönem sonu kur değerleme: tarih seç → dry-run önizleme → değerleme fişi; geçmiş değerlemeler
    için tek tuşla ters kayıt."""
    ham = request.GET.get("tarih") or request.POST.get("tarih")
    try:
        tarih = datetime.date.fromisoformat(ham) if ham else timezone.localdate()
    except ValueError:
        tarih = timezone.localdate()
    if request.method == "POST":
        eylem = request.POST.get("eylem")
        try:
            if eylem == "uygula":
                d = kur_degerleme_servis.uygula(tarih, kullanici=request.user)
                messages.success(request, f"Değerleme fişi oluşturuldu: {d.fis.yil}/{d.fis.fis_no}.")
            elif eylem == "ters":
                d = get_object_or_404(KurDegerleme, pk=request.POST.get("degerleme"), silindi=False)
                ters_ham = request.POST.get("ters_tarih")
                ters_tarih = datetime.date.fromisoformat(ters_ham) if ters_ham else None
                d = kur_degerleme_servis.ters_kayit(d, tarih=ters_tarih, kullanici=request.user)
                messages.success(request, f"Ters kayıt oluşturuldu: {d.ters_fis.yil}/{d.ters_fis.fis_no}.")
        except (kur_degerleme_servis.KurDegerlemeHatasi, ValueError) as e:
            messages.error(request, str(e))
        return redirect(f"{reverse('core:kur_degerleme')}?tarih={tarih.isoformat()}")
    return render(request, "core/kur_degerleme.html", {
        "tarih": tarih, "onizleme": kur_degerleme_servis.onizle(tarih),
        "degerlemeler": KurDegerleme.objects.filter(silindi=False).select_related("fis", "ters_fis")})


@ekran_gerekli("donemsel_dagitim")
def donemsel_dagitim(request):
    """Dönemsel gider (180) aylık dağıtım fişlerini "bu tarihe kadar" üretir (idempotent; gelecek aylar için fiş üretmez)."""
    ham = request.GET.get("tarih") or request.POST.get("tarih")
    try:
        tarih = datetime.date.fromisoformat(ham) if ham else timezone.localdate()
    except ValueError:
        tarih = timezone.localdate()
    if request.method == "POST":
        try:
            n = donemsel_servis.uret(tarih, kullanici=request.user)
            messages.success(request, f"{n} dönemsel dağıtım fişi oluşturuldu." if n else "Üretilecek dönemsel dağıtım yok (hepsi güncel).")
        except donemsel_servis.DonemselGiderHatasi as e:
            messages.error(request, str(e))
        return redirect(f"{reverse('core:donemsel_dagitim')}?tarih={tarih.isoformat()}")
    from core.models import DonemselDagitim
    bekleyen = (DonemselDagitim.objects.filter(silindi=False, fis__isnull=True, ay_sonu__lte=tarih, fatura__silindi=False,
                                               fatura__durum=Fatura.Durum.ONAYLI)
                .select_related("fatura", "hesap", "gider_hesap").order_by("ay_sonu", "fatura_id", "hesap_id"))
    uretilen = (DonemselDagitim.objects.filter(silindi=False, fis__isnull=False).select_related("fatura", "hesap", "fis")
                .order_by("-ay_sonu", "-id")[:60])
    return render(request, "core/donemsel_dagitim.html", {"tarih": tarih, "bekleyen": bekleyen, "uretilen": uretilen})


@ekran_gerekli("banka")
def doviz_islem_ekle(request):
    """Döviz Alış / Satış (TL ↔ döviz banka/kasa; ortalama kurla maliyet + otomatik kur farkı)."""
    def coz(deger):
        tur, _, pk = (deger or "").partition(":")
        model = BankaHesap if tur == "banka" else Kasa
        return get_object_or_404(model, pk=int(pk), silindi=False)

    if request.method == "POST":
        form = DovizIslemForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            masraflar = [{"hesap_kodu": cd[f"masraf_hesap_{i}"].hesap_kodu, "tutar": cd.get(f"masraf_tutar_{i}")}
                         for i in ("1", "2") if cd.get(f"masraf_hesap_{i}")]
            try:
                kaynak, hedef = coz(cd["kaynak"]), coz(cd["hedef"])
                fis = doviz_islem_servis.doviz_islem_olustur(
                    kaynak=kaynak, hedef=hedef, doviz_tutari=cd["doviz_tutari"], tarih=cd["tarih"],
                    kur=cd.get("kur"), aciklama=cd["aciklama"], masraflar=masraflar,
                    kullanici=request.user)
                messages.success(request, f"Döviz işlemi kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                sahip = fis.banka_hesap_id
                return redirect("core:banka_hesap_detay", pk=sahip) if sahip else redirect(
                    "core:kasa_detay", pk=fis.kasa_id)
            except doviz_islem_servis.DovizIslemHatasi as e:
                form.add_error(None, str(e))
    else:
        form = DovizIslemForm(initial={k: request.GET.get(k) for k in ("kaynak", "hedef") if request.GET.get(k)})
    return render(request, "core/doviz_islem_form.html", {"form": form, "pb_haritasi": form.pb})


def _banka_hesap_hareketi_duzenle(request, hesap, fis, bilgi):
    """Hesaba Ödeme / Hesaptan Giriş düzenle (tarih, tutar, karşı hesap satırları [bölme], proje, açıklama) — yeni kayıt formuyla aynı alanlar."""
    tip = bilgi["tip"]
    tan = banka_hareket_servis.HAREKET[tip]
    if request.method == "POST":
        form = BankaHesapHareketForm(request.POST)
        formset = BankaHesapSatirFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            satirlar = [
                {"hesap_kodu": f.cleaned_data["hesap"].hesap_kodu,
                 "tutar": f.cleaned_data.get("tutar"),
                 "aciklama": f.cleaned_data.get("aciklama", ""),
                 "yatirim_projesi_id": (f.cleaned_data["yatirim_projesi"].pk
                                        if f.cleaned_data.get("yatirim_projesi") else None)}
                for f in formset if f.cleaned_data.get("hesap")]
            try:
                banka_hareket_servis.hesap_hareketi_guncelle(
                    fis=fis, banka_hesap=hesap, tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                    aciklama=form.cleaned_data["aciklama"], satirlar=satirlar, kur_override=form.cleaned_data.get("kur"),
                    kullanici=request.user)
                messages.success(request, f"{tan['ad']} güncellendi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:banka_hesap_detay", pk=hesap.pk)
            except banka_hareket_servis.BankaHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        form = BankaHesapHareketForm(initial={
            "tutar": bilgi["tutar"], "tarih": fis.tarih, "aciklama": fis.aciklama,
            "kur": bilgi["kur"] if hesap.para_birimi != "TRY" else None})
        formset = BankaHesapSatirFormSet(initial=[
            {"hesap": s.hesap_id, "tutar": s.islem_tutari, "yatirim_projesi": s.yatirim_projesi_id, "aciklama": s.aciklama}
            for s in bilgi["satirlar"]])
    return render(request, "core/banka_hesap_hareket_form.html",
                  {"hesap": hesap, "form": form, "formset": formset, "tip": tip, "tan": tan, "duzenle": True, "fis": fis})


@ekran_gerekli("banka")
def banka_hareket_duzenle(request, pk, fis_pk):
    """Banka hareketini düzenle: Hesaba Ödeme / Hesaptan Giriş (hesap bölme dahil) ya da TL hesaptan cariye ödeme (açıklama + döviz karşılığı)."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    try:
        return _banka_hesap_hareketi_duzenle(request, hesap, fis, banka_hareket_servis.hesap_hareketi_bilgisi(fis, hesap))
    except banka_hareket_servis.BankaHareketHatasi:
        pass                                                                            # başka yapı: cariye ödeme düzenleme yolu
    try:
        bilgi = banka_hareket_servis.duzenleme_bilgisi(fis, hesap)
    except banka_hareket_servis.BankaHareketHatasi as e:
        messages.error(request, str(e))
        return redirect("core:banka_hesap_detay", pk=hesap.pk)
    if request.method == "POST":
        form = BankaHareketDuzenleForm(request.POST)
        if form.is_valid():
            try:
                banka_hareket_servis.hareket_guncelle(
                    fis=fis, banka_hesap=hesap, aciklama=form.cleaned_data["aciklama"],
                    sayilan_pb=form.cleaned_data.get("sayilan_pb"), sayilan_doviz=form.cleaned_data.get("sayilan_doviz"),
                    kullanici=request.user)
                messages.success(request, f"Hareket güncellendi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:banka_hesap_detay", pk=hesap.pk)
            except banka_hareket_servis.BankaHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        s = bilgi["cari_satiri"]
        doviz = s.islem_pb != "TRY"
        form = BankaHareketDuzenleForm(initial={
            "aciklama": fis.aciklama, "sayilan_pb": s.islem_pb if doviz else "",
            "sayilan_doviz": s.islem_tutari if doviz else None})
    return render(request, "core/banka_hareket_duzenle.html", {"hesap": hesap, "fis": fis, "form": form, "bilgi": bilgi})


@ekran_gerekli("banka")
def banka_hareket_sil(request, pk, fis_pk):
    """Banka hareketini (kaynak=BANKA fiş) KALICI siler — hesap detayından (ham fiş ekranı kilitli)."""
    hesap = get_object_or_404(BankaHesap, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Banka hareketi · {hesap.ad}",
        geri=reverse("core:banka_hesap_detay", args=[hesap.pk]),
        sil=lambda: banka_hareket_servis.hareket_sil(fis=fis, banka_hesap=hesap, kullanici=request.user))


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
    (TeklifSiparis.BelgeTur.PROFORMA, TeklifSiparis.Yon.SATIS): "satis_proformalari",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "satis_siparisleri",
}
_TS_EKLE = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS): "satinalma_teklif_ekle",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS): "satinalma_siparis_ekle",
    (TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS): "satinalma_irsaliye_ekle",
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS): "satis_teklif_ekle",
    (TeklifSiparis.BelgeTur.PROFORMA, TeklifSiparis.Yon.SATIS): "satis_proforma_ekle",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "satis_siparis_ekle",
}
_TS_EMOJI = {
    "satinalma_teklifleri": "📥", "satinalma_siparisleri": "🛒",
    "satinalma_irsaliyeleri": "🚚", "satis_teklifleri": "📤", "satis_proformalari": "🧾",
    "satis_siparisleri": "📦",
}
TeklifSiparisKalemFormSet = formset_factory(
    TeklifSiparisKalemForm, extra=0, min_num=1, validate_min=True)
# Satış Teklifi: satır sayısı GET'te satış ürünü kataloğunun boyutuna sabitlenir (bkz.
# satis_teklif_ekle) — min_num burada 0 (formset başlangıçta zaten dolu; "hiç ürün yok"
# durumu ayrıca view'de kontrol edilir).
SatisTeklifKalemFormSet = formset_factory(SatisTeklifKalemForm, extra=0)
# Satış Proforması: aynı desen (bkz. satis_proforma_ekle) — TEK FARK, kalem formunda
# gerçek Miktar alanı var (SatisProformaKalemForm).
SatisProformaKalemFormSet = formset_factory(SatisProformaKalemForm, extra=0)


_TS_SAYFA_BOYUTLARI = (25, 50, 100, 200)

# Durum sekmeleri/filtresi — Satış Teklifi'nin Gönderildi/Kabul/Red/Süresi Doldu/İptal
# akışı yalnız kendi listesinde görünür; diğer 6 belge_tur/yön kombinasyonu hâlâ yalnız
# Taslak/Onaylı gösterir (bkz. TeklifSiparis.Durum docstring'i — _ts_liste'nin sat_teklif
# bayrağına göre ikisinden birini seçer).
_TS_DURUM_STANDART = (TeklifSiparis.Durum.TASLAK, TeklifSiparis.Durum.ONAYLI)
_TS_DURUM_SATIS_TEKLIFI = (
    TeklifSiparis.Durum.TASLAK, TeklifSiparis.Durum.GONDERILDI, TeklifSiparis.Durum.KABUL,
    TeklifSiparis.Durum.RED, TeklifSiparis.Durum.SURESI_DOLDU, TeklifSiparis.Durum.IPTAL)


def _ts_tarih_coz(deger):
    try:
        return datetime.date.fromisoformat((deger or "").strip())
    except ValueError:
        return None


def _ts_liste(request, belge_tur, yon, baslik, emoji):
    sat_teklif = (belge_tur == TeklifSiparis.BelgeTur.TEKLIF
                 and yon == TeklifSiparis.Yon.SATIS)
    durum_secenekleri = _TS_DURUM_SATIS_TEKLIFI if sat_teklif else _TS_DURUM_STANDART
    durum_etiketleri = dict(TeklifSiparis.Durum.choices)
    ara = (request.GET.get("ara") or "").strip()
    durum = request.GET.get("durum") or ""
    if durum not in durum_secenekleri:
        durum = ""
    ulke_id = (request.GET.get("ulke") or "").strip() if sat_teklif else ""
    tarih_bas = _ts_tarih_coz(request.GET.get("bas"))
    tarih_bit = _ts_tarih_coz(request.GET.get("bit"))
    try:
        boyut = int(request.GET.get("boyut", 50))
    except ValueError:
        boyut = 50
    if boyut not in _TS_SAYFA_BOYUTLARI:
        boyut = 50
    # "donustu" rozeti yalnız TEKLİF/PROFORMA/SİPARİŞ için anlamlı (bir sonraki belgeye
    # kaynak_teklif/kaynak_proforma/kaynak_siparis self-FK'sıyla dönüşür). ALIŞ+TEKLİF hedefi
    # Sipariş, SATIŞ+TEKLİF hedefi Proforma (bkz. TeklifSiparis docstring'i). İRSALİYE→Fatura
    # dönüşümü zaten şablonda ayrı (k.fatura_id — "🧾 Faturaya Dönüştü") gösteriliyor, burada
    # tekrar hesaplanmaz.
    donustu_etiket = None
    if belge_tur == TeklifSiparis.BelgeTur.SIPARIS:
        donusen_var = TeklifSiparis.objects.filter(kaynak_siparis=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 İrsaliyeye Dönüştü"
    elif belge_tur == TeklifSiparis.BelgeTur.PROFORMA:
        donusen_var = TeklifSiparis.objects.filter(kaynak_proforma=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 Siparişe Dönüştü"
    elif belge_tur == TeklifSiparis.BelgeTur.TEKLIF and yon == TeklifSiparis.Yon.SATIS:
        donusen_var = TeklifSiparis.objects.filter(kaynak_teklif=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 Proformaya Dönüştü"
    elif belge_tur == TeklifSiparis.BelgeTur.TEKLIF:
        donusen_var = TeklifSiparis.objects.filter(kaynak_teklif=OuterRef("pk"), silindi=False)
        donustu_etiket = "🔁 Siparişe Dönüştü"
    else:
        donusen_var = TeklifSiparis.objects.filter(pk=-1)   # her zaman boş — geçerli pk asla negatif değil
    temel = teklif_siparis_servis.aktif_teklif_siparisler(belge_tur, yon)
    if ara:
        buyuk = buyuk_harf_tr(ara)
        temel = temel.filter(
            Q(cari__unvan__contains=buyuk) | Q(cari__kod__icontains=ara)
            | Q(aday_musteri__unvan__contains=buyuk) | Q(belge_no__icontains=ara))
    # Ülke filtre seçenekleri yalnız bu listede fiilen kullanılan ülkelerden oluşur (bkz.
    # core.views.cariler'deki aynı desen) — yalnız Satış Teklifi'nde anlamlı.
    ulkeler = None
    if sat_teklif:
        ulkeler = Ulke.objects.filter(
            Q(pk__in=temel.exclude(cari__ulke=None).values_list("cari__ulke_id", flat=True))
            | Q(pk__in=temel.exclude(aday_musteri__ulke=None)
                .values_list("aday_musteri__ulke_id", flat=True))
        ).order_by("ad")
        if ulke_id.isdigit():
            temel = temel.filter(
                Q(cari__ulke_id=ulke_id) | Q(aday_musteri__ulke_id=ulke_id))
        else:
            ulke_id = ""
    if tarih_bas:
        temel = temel.filter(tarih__gte=tarih_bas)
    if tarih_bit:
        temel = temel.filter(tarih__lte=tarih_bit)
    # Durum sayaçları (rozet/sekme) arama+tarih süzgecine göre, durum filtresinden ÖNCE —
    # her sekmenin kaç kayıt getireceğini kullanıcı durumu değiştirmeden görsün. ÖNEMLİ:
    # bu, kalem_sayisi/donustu JOIN'leri EKLENMEDEN ÖNСЕ, ham `temel` üzerinden hesaplanır —
    # aksi halde Count("pk") her teklifi kendi kalem satırı sayısı kadar tekrar sayar
    # (ör. 4 teklif × ~11-14 kalem ≈ 55 gibi yanlış, şişirilmiş bir sayı çıkar).
    sayimlar = {d: 0 for d in durum_secenekleri}
    for satir in temel.values("durum").annotate(n=Count("pk")):
        if satir["durum"] in sayimlar:
            sayimlar[satir["durum"]] = satir["n"]
    durum_sekmeleri = [{"kod": kod, "ad": durum_etiketleri[kod], "n": sayimlar.get(kod, 0)}
                       for kod in durum_secenekleri]
    # annotate(Count(...)) model Meta.ordering'i (-tarih, -id) SESSİZCE sıfırlıyor (Django,
    # GROUP BY gerektiren bir annotate'ten sonra örtük varsayılan sıralamayı korumuyor) —
    # bu yüzden en yeni üstte kalsın diye burada AÇIKÇA tekrar belirtiliyor.
    kayitlar = (temel.filter(durum=durum) if durum else temel).annotate(
        donustu=Exists(donusen_var),
        kalem_sayisi=Count("kalemler", filter=Q(kalemler__silindi=False)),
    ).order_by("-tarih", "-id").prefetch_related("kalemler__kdv", "kalemler__tevkifat")
    sayfa = Paginator(kayitlar, boyut).get_page(request.GET.get("sayfa"))
    # sayfa linkleri: sayfa DIŞINDAKİ her şeyi (durum dahil) korur — yalnız sayfa değişir.
    sabit_qs = request.GET.copy()
    sabit_qs.pop("sayfa", None)
    # sekme linkleri: durum'u KENDİ href'i belirler (?durum=KOD) — sabit_qs'te de durum
    # olursa aynı isim iki kez eklenip son değer (eski durum) kazanır; bu yüzden ayrı.
    sekme_qs = sabit_qs.copy()
    sekme_qs.pop("durum", None)
    # Satış Teklifi'nde henüz fatura/sipariş olmadığı için "ödenecek" (tevkifat düşülmüş)
    # kavramı yok — liste bu ekranda sade tutuluyor (Cari/Tarih/Belge No/Durum/Ülke +
    # tıklanabilir satır); tutar yalnız detay sayfasında/PDF'te gösterilir.
    return render(request, "core/teklif_siparis_listesi.html", {
        "kayitlar": sayfa, "baslik": baslik, "emoji": emoji, "ara": ara,
        "durum": durum, "bas": request.GET.get("bas", ""), "bit": request.GET.get("bit", ""),
        "boyut": boyut, "sayfa_boyutlari": _TS_SAYFA_BOYUTLARI,
        "toplam": sum(sayimlar.values()), "durum_sekmeleri": durum_sekmeleri,
        "sabit_qs": sabit_qs.urlencode(), "sekme_qs": sekme_qs.urlencode(),
        "donustu_etiket": donustu_etiket, "sat_teklif": sat_teklif,
        "ulkeler": ulkeler, "ulke_id": ulke_id,
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


def _stok_meta(yon=None):
    """Kalem satırı JS'i için stok başına KDV oranı + tevkifat oranı + üretim/fatura
    birim çevirisi + alış fiyatı (varsa Birim Fiyat'a otomatik öneri için) + (yalnız ALIŞ
    yönünde) Tedarikçi Ürün Adı — stok seçilince alan altında ayrıca gösterilir (satış
    tarafında hiç dönmez, bkz. forms.py'deki akıllı-seç etiketiyle aynı ayrım)."""
    return {str(s.pk): {
        "kdv": float(s.kdv.oran) if s.kdv_id else 0,
        "tevkifat": (float(s.tevkifat.pay) / float(s.tevkifat.payda))
                    if (s.tevkifat_id and s.tevkifat.payda) else 0,
        "cevirici": float(s.cevirici),
        "uretim": s.uretim_birimi.kisa_ad,
        "fatura": s.fatura_birimi.kisa_ad,
        "alisFiyati": float(s.alis_fiyati) if s.alis_fiyati is not None else None,
        "alisFiyatiPb": s.alis_fiyati_pb,
        "tedarikciAdi": (s.tedarikci_adi or None) if yon == "ALIS" else None,
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


def _aday_meta():
    """Aday müşteri başına para birimi + varsayılan iskonto — _cari_meta ile aynı desen,
    aday seçilince JS otomatik uygular. Cariye zaten dönüşmüş adaylar dahil değil (bkz.
    SatisBelgeBaslikForm.aday_musteri queryset'i)."""
    return {str(a.pk): {"pb": a.para_birimi, "iskonto": float(a.iskonto_yuzdesi)}
            for a in AdayMusteri.objects.filter(silindi=False, cari__isnull=True)}


def _banka_meta():
    """FİNANS > Banka'daki açık hesap başına para birimi — Satış Proforması'nda para birimi
    seçilince JS ile o PB'deki hesaplar filtrelenir (bkz. satis_proforma_ekle.html,
    SatisProformaBaslikForm)."""
    return {str(b.pk): {"pb": b.para_birimi}
            for b in BankaHesap.objects.filter(silindi=False, banka__silindi=False)}


def _banka_hesap_pdf_goster(banka_hesap, firma):
    """BankaHesap (FİNANS > Banka altındaki gerçek hesap) kaydını Proforma PDF/detay
    şablonunun beklediği düz alanlara çevirir — banka_adi/sube üst kurumdan (Banka) gelir;
    BankaHesap'ta ayrı bir 'hesap sahibi' alanı yok, bu yüzden firma unvanı kullanılır."""
    return {
        "banka_adi": banka_hesap.banka.ad, "sube": banka_hesap.banka.sube,
        "hesap_adi": banka_hesap.ad, "hesap_sahibi": firma.unvan if firma else "",
        "iban": banka_hesap.iban, "para_birimi": banka_hesap.para_birimi,
        "swift_kod": banka_hesap.banka.swift_kod,
    }


def _ts_ekle(request, belge_tur, yon, baslik, emoji):
    ekran = _TS_EKRAN[(belge_tur, yon)]
    if request.method == "POST":
        bform = TeklifSiparisForm(request.POST, belge_tur=belge_tur)
        formset = TeklifSiparisKalemFormSet(request.POST, form_kwargs={"yon": yon})
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "uretim_miktar": f.cleaned_data.get("uretim_miktar")}
                for f in formset if f.dolu_mu()
            ]
            try:
                ts = teklif_siparis_servis.teklif_siparis_olustur(
                    belge_tur=belge_tur, yon=yon, cari_id=bform.cleaned_data["cari"].pk,
                    tarih=bform.cleaned_data["tarih"],
                    gecerlilik_teslim_tarihi=bform.cleaned_data.get("gecerlilik_teslim_tarihi"),
                    para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                    kur=bform.cleaned_data.get("kur"),
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
        formset = TeklifSiparisKalemFormSet(form_kwargs={"yon": yon})
    return render(request, "core/teklif_siparis_ekle.html",
                  {"bform": bform, "formset": formset, "baslik": baslik, "emoji": emoji,
                   "stok_meta": _stok_meta(yon), "iptal_url": reverse("core:" + ekran)})


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
    zaman 1 birim fiyatı iletilir); karşı taraf (Cari VEYA Aday Müşteri) seçilince PB/iskonto
    JS ile otomatik uygulanır (bkz. satis_teklif_ekle.html)."""
    urunler, stok_meta = _satis_teklif_stok_meta()
    if request.method == "POST":
        bform = SatisBelgeBaslikForm(request.POST)
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
                cd = bform.cleaned_data
                try:
                    ts = teklif_siparis_servis.teklif_siparis_olustur(
                        belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                        cari_id=(cd["cari"].pk if cd.get("cari") else None),
                        aday_musteri_id=(cd["aday_musteri"].pk if cd.get("aday_musteri") else None),
                        tarih=cd["tarih"],
                        gecerlilik_teslim_tarihi=cd.get("gecerlilik_teslim_tarihi"),
                        para_birimi=cd.get("para_birimi", "TRY"),
                        **_secenek_kwargs(cd),
                        taslak_olarak_kaydet=cd.get("taslak_olarak_kaydet", False),
                        satirlar=satirlar, kullanici=request.user)
                    durum_mesaji = ("taslak olarak" if cd.get("taslak_olarak_kaydet")
                                   else "gönderildi olarak")
                    messages.success(
                        request, f"Satış Teklifi {durum_mesaji} kaydedildi: {ts.belge_no}")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisBelgeBaslikForm()
        formset = SatisTeklifKalemFormSet(initial=[
            {"stok": s.pk, "dahil": True, "iskonto_yuzdesi": Decimal("0"),
             "birim_fiyat": next(
                 (f.fiyat for f in s.fiyatlar.all() if f.para_birimi == "TRY"), None)}
            for s in urunler])
    return render(request, "core/satis_teklif_ekle.html", {
        "bform": bform, "formset": formset, "satirlar": list(zip(urunler, formset)),
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "aday_meta": _aday_meta(),
        "tip_kodlari": _tip_kodlari(), "iptal_url": reverse("core:satis_teklifleri")})


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
        bform = SatisBelgeBaslikForm(request.POST)
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
                cd = bform.cleaned_data
                try:
                    teklif_siparis_servis.teklif_siparis_guncelle(
                        ts, cari_id=(cd["cari"].pk if cd.get("cari") else None),
                        aday_musteri_id=(cd["aday_musteri"].pk if cd.get("aday_musteri") else None),
                        tarih=cd["tarih"],
                        gecerlilik_teslim_tarihi=cd.get("gecerlilik_teslim_tarihi"),
                        para_birimi=cd.get("para_birimi", "TRY"),
                        aciklama=ts.aciklama,
                        **_secenek_kwargs(cd),
                        satirlar=satirlar, kullanici=request.user)
                    messages.success(request, "Satış Teklifi güncellendi.")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisBelgeBaslikForm(initial={
            "karsi_taraf_tip": "aday" if ts.aday_musteri_id else "cari",
            "cari": ts.cari_id, "aday_musteri": ts.aday_musteri_id, "tarih": ts.tarih,
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
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "aday_meta": _aday_meta(),
        "tip_kodlari": _tip_kodlari(),
        "duzenleme": True,
        "iptal_url": reverse("core:teklif_siparis_detay", args=[ts.pk])})


@ekran_gerekli("satis_proformalari")
def satis_proformalari(request):
    return _ts_liste(request, TeklifSiparis.BelgeTur.PROFORMA, TeklifSiparis.Yon.SATIS,
                     "Satış Proformaları", "🧾")


@ekran_gerekli("satis_proformalari")
def satis_proforma_ekle(request):
    """Satış Proforması — bağımsız ekran (paylaşımlı ``_ts_ekle``'yi ÇAĞIRMAZ, ``satis_teklif_
    ekle`` ile aynı iskelet). Sayfa açılırken TÜM satış ürünleri önceden gelir ama HİÇBİRİ
    dahil değildir (Teklif'in aksine — müşteri hangi üründen kaç adet istediğini belirtmiştir,
    kullanıcı yalnız o satırları işaretleyip gerçek miktarı girer). Genellikle bir Teklif'ten
    "Proformaya Çevir" ile açılır ama bağımsız da oluşturulabilir."""
    urunler, stok_meta = _satis_teklif_stok_meta()
    if request.method == "POST":
        bform = SatisProformaBaslikForm(request.POST)
        formset = SatisProformaKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "iskonto_yuzdesi": f.cleaned_data["iskonto_yuzdesi"]}
                for f in formset if f.dahil_mi()
            ]
            if not satirlar:
                bform.add_error(None, "En az bir ürün proformaya dahil edilmelidir.")
            else:
                cd = bform.cleaned_data
                try:
                    ts = teklif_siparis_servis.teklif_siparis_olustur(
                        belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS,
                        cari_id=(cd["cari"].pk if cd.get("cari") else None),
                        aday_musteri_id=(cd["aday_musteri"].pk if cd.get("aday_musteri") else None),
                        tarih=cd["tarih"],
                        gecerlilik_teslim_tarihi=cd.get("gecerlilik_teslim_tarihi"),
                        para_birimi=cd.get("para_birimi", "TRY"),
                        banka_hesabi_id=(cd["banka_hesabi"].pk if cd.get("banka_hesabi") else None),
                        **_secenek_kwargs(cd),
                        satirlar=satirlar, kullanici=request.user)
                    messages.success(request, f"Satış Proforması kaydedildi: {ts.belge_no}")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisProformaBaslikForm()
        formset = SatisProformaKalemFormSet(initial=[
            {"stok": s.pk, "dahil": False, "iskonto_yuzdesi": Decimal("0"),
             "birim_fiyat": next(
                 (f.fiyat for f in s.fiyatlar.all() if f.para_birimi == "TRY"), None)}
            for s in urunler])
    return render(request, "core/satis_proforma_ekle.html", {
        "bform": bform, "formset": formset, "satirlar": list(zip(urunler, formset)),
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "aday_meta": _aday_meta(),
        "banka_meta": _banka_meta(),
        "iptal_url": reverse("core:satis_proformalari")})


@ekran_gerekli("satis_proformalari")
def satis_proforma_duzenle(request, pk):
    """Satış Proforması düzenle — ``satis_proforma_ekle`` ile simetrik (paylaşımlı
    ``teklif_siparis_duzenle``'a hiç dokunmaz)."""
    ts = get_object_or_404(
        TeklifSiparis, pk=pk, silindi=False,
        belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS)
    urunler, stok_meta = _satis_teklif_stok_meta()
    if request.method == "POST":
        bform = SatisProformaBaslikForm(request.POST)
        formset = SatisProformaKalemFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "iskonto_yuzdesi": f.cleaned_data["iskonto_yuzdesi"]}
                for f in formset if f.dahil_mi()
            ]
            if not satirlar:
                bform.add_error(None, "En az bir ürün proformaya dahil edilmelidir.")
            else:
                cd = bform.cleaned_data
                try:
                    teklif_siparis_servis.teklif_siparis_guncelle(
                        ts, cari_id=(cd["cari"].pk if cd.get("cari") else None),
                        aday_musteri_id=(cd["aday_musteri"].pk if cd.get("aday_musteri") else None),
                        tarih=cd["tarih"],
                        gecerlilik_teslim_tarihi=cd.get("gecerlilik_teslim_tarihi"),
                        para_birimi=cd.get("para_birimi", "TRY"),
                        aciklama=ts.aciklama,
                        banka_hesabi_id=(cd["banka_hesabi"].pk if cd.get("banka_hesabi") else None),
                        **_secenek_kwargs(cd),
                        satirlar=satirlar, kullanici=request.user)
                    messages.success(request, "Satış Proforması güncellendi.")
                    return redirect("core:teklif_siparis_detay", pk=ts.pk)
                except teklif_siparis_servis.TeklifSiparisHatasi as e:
                    bform.add_error(None, str(e))
    else:
        bform = SatisProformaBaslikForm(initial={
            "karsi_taraf_tip": "aday" if ts.aday_musteri_id else "cari",
            "cari": ts.cari_id, "aday_musteri": ts.aday_musteri_id, "tarih": ts.tarih,
            "gecerlilik_teslim_tarihi": ts.gecerlilik_teslim_tarihi,
            "para_birimi": ts.para_birimi,
            "yukleme_sekli": ts.yukleme_sekli_id, "odeme_kosulu": ts.odeme_kosulu_id,
            "yukleme_tipi": ts.yukleme_tipi_id, "teslim_suresi": ts.teslim_suresi_id,
            "navlun_tutari": ts.navlun_tutari, "banka_hesabi": ts.banka_hesabi_id})
        mevcut = {k.stok_id: k for k in ts.kalemler.filter(silindi=False)}
        formset = SatisProformaKalemFormSet(initial=[
            {"stok": s.pk, "dahil": s.pk in mevcut,
             "miktar": mevcut[s.pk].miktar if s.pk in mevcut else None,
             "iskonto_yuzdesi": (mevcut[s.pk].iskonto_yuzdesi if s.pk in mevcut
                                 else Decimal("0")),
             "birim_fiyat": (mevcut[s.pk].birim_fiyat if s.pk in mevcut else next(
                 (f.fiyat for f in s.fiyatlar.all() if f.para_birimi == ts.para_birimi), None))}
            for s in urunler])
    return render(request, "core/satis_proforma_ekle.html", {
        "bform": bform, "formset": formset, "satirlar": list(zip(urunler, formset)),
        "stok_meta": stok_meta, "cari_meta": _cari_meta(), "aday_meta": _aday_meta(),
        "banka_meta": _banka_meta(),
        "duzenleme": True,
        "iptal_url": reverse("core:teklif_siparis_detay", args=[ts.pk])})


@ekran_gerekli("satis_siparisleri")
def satis_siparis_ekle(request):
    return _ts_ekle(request, TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS,
                    "Yeni Satış Siparişi", "📦")


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
def teklif_siparis_detay(request, pk):
    # silindi filtrelenmez: iptal edilmiş belge de görüntülenebilir (uyarı banner'ıyla).
    ts = get_object_or_404(
        TeklifSiparis.objects.select_related(
            "cari", "aday_musteri", "kaynak_teklif", "kaynak_proforma", "kaynak_siparis",
            "depo", "banka_hesabi", "banka_hesabi__banka"),
        pk=pk)
    kalemler = ts.kalemler.filter(silindi=False).select_related("stok", "kdv", "tevkifat")
    ekran = _TS_EKRAN[(ts.belge_tur, ts.yon)]
    emoji = _TS_EMOJI[ekran]
    # kaynak_teklif'in "donustugu belge" ALIŞ'ta Sipariş, SATIŞ'ta Proforma'dır (bkz.
    # TeklifSiparis.kaynak_teklif docstring'i) — iki ayrı context değişkenine ayrılır.
    donusen_siparis = None
    donusen_proforma = None
    if ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF and ts.yon == TeklifSiparis.Yon.ALIS:
        donusen_siparis = ts.donusen_belgeler.filter(silindi=False).first()
    elif ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF and ts.yon == TeklifSiparis.Yon.SATIS:
        donusen_proforma = ts.donusen_belgeler.filter(silindi=False).first()
    elif ts.belge_tur == TeklifSiparis.BelgeTur.PROFORMA:
        donusen_siparis = ts.donusen_siparisler.filter(silindi=False).first()
    donusen_irsaliye = (ts.donusen_irsaliyeler.filter(silindi=False).first()
                       if ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS else None)
    uretim_emri = None
    uretim_emri_acilabilir = False
    if ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS and ts.yon == TeklifSiparis.Yon.SATIS:
        uretim_emri = ts.uretim_emirleri.filter(silindi=False).first()
        if not uretim_emri and ts.durum == TeklifSiparis.Durum.ONAYLI:
            uygun, _ = uretim_servis.siparis_uretilebilir_kalemleri(ts)
            uretim_emri_acilabilir = bool(uygun)
    # SATIŞ'taki manuel dönüşüm zincirinde (Teklif->Proforma->Sipariş) kaynak belge artık
    # düzenlenemez/iptal edilemez (bkz. core.services.teklif_siparis._donusum_hedefi_manuel).
    # ALIŞ'taki otomatik zincir kasıtlı olarak kapsam dışı (o yüzden donusen_siparis burada
    # tek başına yeterli değil — yalnız PROFORMA'dan doğan sipariş sayılır). Bir Üretim
    # Emrine bağlanmış sipariş de aynı gerekçeyle kilitlenir: arkada gerçek operasyon kaydı
    # zinciri varken taslağa dönmemeli/silinmemeli.
    donusum_kilitli = bool(
        donusen_proforma or (ts.belge_tur == TeklifSiparis.BelgeTur.PROFORMA and donusen_siparis)
        or uretim_emri)
    return render(request, "core/teklif_siparis_detay.html",
                  {"ts": ts, "kalemler": kalemler, "emoji": emoji,
                   "liste_url": "core:" + ekran, "donusen_siparis": donusen_siparis,
                   "donusen_proforma": donusen_proforma,
                   "donusen_irsaliye": donusen_irsaliye, "donusen_fatura": ts.fatura,
                   "uretim_emri": uretim_emri, "uretim_emri_acilabilir": uretim_emri_acilabilir,
                   "donusum_kilitli": donusum_kilitli})


@ekran_gerekli("satis_teklifleri")
def teklif_proformaya_cevir(request, pk):
    """Satış Teklifi → Proforma (elle, tek tık). Alış teklifleri buraya hiç gelmez (onaylanınca
    otomatik siparişe dönüşürler, bkz. teklif_siparis_onayla) — URL'e doğrudan erişimde de
    servis katmanı zaten reddeder."""
    teklif = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            proforma = teklif_siparis_servis.teklifi_proformaya_cevir(
                teklif, tarih=timezone.localdate(), kullanici=request.user)
            messages.success(
                request, f"Proforma oluşturuldu (teklif {teklif.pk} kaynaklı).")
            return redirect("core:teklif_siparis_detay", pk=proforma.pk)
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=teklif.pk)


@ekran_gerekli("satis_proformalari")
def proforma_siparise_cevir(request, pk):
    """Proforma → Sipariş (elle, tek tık). Aday müşteriye ait proformada servis katmanı
    açık bir hata mesajıyla reddeder (önce Cariye Dönüştür gerekir, bkz.
    proformayi_siparise_cevir)."""
    proforma = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            siparis = teklif_siparis_servis.proformayi_siparise_cevir(
                proforma, tarih=timezone.localdate(), kullanici=request.user)
            messages.success(
                request, f"Sipariş oluşturuldu (proforma {proforma.pk} kaynaklı).")
            return redirect("core:teklif_siparis_detay", pk=siparis.pk)
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=proforma.pk)


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
        formset = FaturaSatirFormSet(request.POST, form_kwargs={"yon": yon})
        if fform.is_valid() and formset.is_valid():
            satirlar = _fatura_satir_girdileri(formset)
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
                _fatura_hatasi_ekle(fform, e)
    else:
        fform = FaturaForm(yon=yon, initial={
            "cari": siparis.cari_id, "tarih": timezone.localdate(),
            "para_birimi": siparis.para_birimi})
        ilk = [{"stok": k.stok_id, "miktar": k.miktar, "birim_fiyat": k.birim_fiyat}
               for k in siparis.kalemler.filter(silindi=False).select_related("stok")]
        formset = FaturaSatirFormSet(initial=ilk)
    stok_kdv, stok_tevkifat, stok_tedarikci = _stok_kdv_tevkifat(yon)
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat, "stok_tedarikci": stok_tedarikci,
                   **_fatura_gider_baglami(fform),
                   "baslik": f"Fatura Oluştur (Sipariş {siparis.pk} kaynaklı)",
                   "iptal_url": reverse("core:teklif_siparis_detay", args=[siparis.pk])})


@ekran_gerekli_herhangi("satis_siparisleri", "uretim_emirleri")
def siparis_uretim_emrine_cevir(request, pk):
    """SATIŞ Sipariş (ONAYLI) → Üretim Emri. Faturaya Çevir ile aynı UX ağırlığı: tek tık
    DEĞİL, önce siparişin üretime uygun kalemlerini ön-doldurulmuş bir formda göster,
    kullanıcı miktarları gözden geçirsin/istemediği satırı çıkarsın, sonra TEK (çoklu
    kalemli) emir açılır (bkz. core.services.uretim.siparisten_uretim_emri_olustur)."""
    siparis = get_object_or_404(
        TeklifSiparis, pk=pk, silindi=False, belge_tur=TeklifSiparis.BelgeTur.SIPARIS)
    if siparis.yon != TeklifSiparis.Yon.SATIS:
        messages.error(request, "Yalnız satış siparişinden üretim emri açılabilir.")
        return redirect("core:teklif_siparis_detay", pk=siparis.pk)
    mevcut = siparis.uretim_emirleri.filter(silindi=False).first()
    if mevcut:
        messages.info(request, "Bu siparişten zaten bir üretim emri açılmış.")
        return redirect("core:uretim_emri_detay", pk=mevcut.pk)
    if siparis.durum != TeklifSiparis.Durum.ONAYLI:
        messages.error(request, "Yalnız onaylı sipariş için üretim emri açılabilir.")
        return redirect("core:teklif_siparis_detay", pk=siparis.pk)

    uygun, uygun_degil = uretim_servis.siparis_uretilebilir_kalemleri(siparis)
    if not uygun:
        messages.error(
            request, "Bu siparişte üretime uygun (üretim ürünü + tanımlı operasyonu olan) "
                     "hiçbir kalem yok; üretim emri açılamıyor.")
        return redirect("core:teklif_siparis_detay", pk=siparis.pk)

    if request.method == "POST":
        bform = UretimEmriBaslikForm(request.POST)
        formset = SiparisUretimEmriSatirFormSet(request.POST)
        if bform.is_valid() and formset.is_valid():
            secimler = [
                {"kalem_id": f.cleaned_data["kalem_id"],
                 "hedef_miktar": f.cleaned_data["hedef_miktar"]}
                for f in formset if f.dolu_mu()
            ]
            try:
                emir = uretim_servis.siparisten_uretim_emri_olustur(
                    siparis=siparis, depo_id=bform.cleaned_data["depo"].pk,
                    tarih=bform.cleaned_data["tarih"], kalem_secimleri=secimler,
                    aciklama=bform.cleaned_data.get("aciklama", ""), kullanici=request.user)
                messages.success(
                    request, f"Üretim emri açıldı: {emir.no} — sipariş {siparis.pk} kaynaklı.")
                return redirect("core:uretim_emri_detay", pk=emir.pk)
            except uretim_servis.UretimHatasi as e:
                bform.add_error(None, str(e))
    else:
        bform = UretimEmriBaslikForm(initial={"tarih": timezone.localdate()})
        formset = SiparisUretimEmriSatirFormSet(initial=[
            {"kalem_id": k.pk, "hedef_miktar": k.miktar} for k in uygun])
    return render(request, "core/siparis_uretim_emri_ekle.html", {
        "bform": bform, "formset": formset, "siparis": siparis,
        "satirlar": zip(uygun, formset), "uygun_degil": uygun_degil,
        "iptal_url": reverse("core:teklif_siparis_detay", args=[siparis.pk])})


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
def teklif_siparis_duzenle(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    # Satış Teklifi/Proforması artık bağımsız ekranlarla düzenlenir (iskonto/teslim şekli/
    # ödeme koşulu/aday müşteri gibi bu eski paylaşımlı formun bilmediği alanları var —
    # buradan geçilirse sessizce sıfırlanırlardı). Eski URL'e doğrudan gelen istek de
    # güvenle yönlendirilir.
    if ts.yon == TeklifSiparis.Yon.SATIS and ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF:
        return redirect("core:satis_teklif_duzenle", pk=ts.pk)
    if ts.yon == TeklifSiparis.Yon.SATIS and ts.belge_tur == TeklifSiparis.BelgeTur.PROFORMA:
        return redirect("core:satis_proforma_duzenle", pk=ts.pk)
    ekran = _TS_EKRAN[(ts.belge_tur, ts.yon)]
    emoji = _TS_EMOJI[ekran]
    if request.method == "POST":
        bform = TeklifSiparisForm(request.POST, belge_tur=ts.belge_tur)
        formset = TeklifSiparisKalemFormSet(request.POST, form_kwargs={"yon": ts.yon})
        if bform.is_valid() and formset.is_valid():
            satirlar = [
                {"stok_id": f.cleaned_data["stok"].pk, "miktar": f.cleaned_data["miktar"],
                 "birim_fiyat": f.cleaned_data["birim_fiyat"],
                 "uretim_miktar": f.cleaned_data.get("uretim_miktar")}
                for f in formset if f.dolu_mu()
            ]
            try:
                teklif_siparis_servis.teklif_siparis_guncelle(
                    ts, cari_id=bform.cleaned_data["cari"].pk,
                    tarih=bform.cleaned_data["tarih"],
                    gecerlilik_teslim_tarihi=bform.cleaned_data.get("gecerlilik_teslim_tarihi"),
                    para_birimi=bform.cleaned_data.get("para_birimi", "TRY"),
                    kur=bform.cleaned_data.get("kur"),
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
            "para_birimi": ts.para_birimi, "kur": ts.kur, "aciklama": ts.aciklama,
            "depo": ts.depo_id, "irsaliye_no": ts.irsaliye_no})
        ilk = [{"stok": k.stok_id, "miktar": k.miktar, "birim_fiyat": k.birim_fiyat,
               "uretim_miktar": k.uretim_miktar}
               for k in ts.kalemler.filter(silindi=False).select_related("stok")]
        formset = TeklifSiparisKalemFormSet(initial=ilk, form_kwargs={"yon": ts.yon})
    return render(request, "core/teklif_siparis_ekle.html",
                  {"bform": bform, "formset": formset,
                   "baslik": f"{ts.get_belge_tur_display()} Düzenle", "emoji": emoji,
                   "stok_meta": _stok_meta(ts.yon),
                   "iptal_url": reverse("core:teklif_siparis_detay", args=[ts.pk])})


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
def teklif_siparis_iptal_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        if ts.belge_tur == TeklifSiparis.BelgeTur.IRSALIYE:
            # İrsaliye artık soft-iptal DEĞİL, kalıcı silme (bkz. teklif_siparis_servis.
            # irsaliye_sil) — başarılıysa belge yok olur, detay sayfasına dönülemez.
            ekran = _TS_EKRAN[(ts.belge_tur, ts.yon)]
            try:
                teklif_siparis_servis.irsaliye_sil(ts, kullanici=request.user)
            except teklif_siparis_servis.TeklifSiparisHatasi as e:
                messages.error(request, str(e))
                return redirect("core:teklif_siparis_detay", pk=ts.pk)
            messages.success(request, "İrsaliye kalıcı olarak silindi.")
            return redirect("core:" + ekran)
        try:
            teklif_siparis_servis.teklif_siparis_iptal(ts, kullanici=request.user)
            messages.success(request, f"{ts.get_belge_tur_display()} iptal edildi.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


@ekran_gerekli_herhangi("satinalma_teklifleri", "satinalma_siparisleri",
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
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
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
def teklif_siparis_onayi_geri_al(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_siparis_onayi_geri_al(ts, kullanici=request.user)
            messages.success(request, f"{ts.get_belge_tur_display()} onayı geri alındı.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


# --- Satış Teklifi: Gönderildi/Kabul/Red/İptal (bkz. core.services.teklif_siparis, yalnız
# belge_tur=TEKLIF, yon=SATIS) ---
@ekran_gerekli("satis_teklifleri")
def teklif_gonder_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_gonder(ts, kullanici=request.user)
            messages.success(request, "Teklif gönderildi.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


@ekran_gerekli("satis_teklifleri")
def teklif_kabul_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_kabul_et(ts, kullanici=request.user)
            messages.success(request, "Teklif kabul edildi olarak işaretlendi.")
        except teklif_siparis_servis.TeklifSiparisHatasi as e:
            messages.error(request, str(e))
    return redirect("core:teklif_siparis_detay", pk=ts.pk)


@ekran_gerekli("satis_teklifleri")
def teklif_red_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        form = TeklifRedForm(request.POST)
        if form.is_valid():
            try:
                teklif_siparis_servis.teklif_reddet(
                    ts, red_nedeni=form.cleaned_data["red_nedeni"], kullanici=request.user)
                messages.success(request, "Teklif reddedildi olarak işaretlendi.")
                return redirect("core:teklif_siparis_detay", pk=ts.pk)
            except teklif_siparis_servis.TeklifSiparisHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TeklifRedForm()
    return render(request, "core/teklif_red_form.html", {"form": form, "ts": ts})


@ekran_gerekli("satis_teklifleri")
def teklif_iptal_gorunum(request, pk):
    ts = get_object_or_404(TeklifSiparis, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            teklif_siparis_servis.teklif_iptal_et(ts, kullanici=request.user)
            messages.success(request, "Teklif iptal edildi.")
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


def _teslim_sekli_notu(ts, dil, yukleme_sekli_ad, yukleme_tipi_ad, sehir_ad, ulke_ad, E):
    """Teklif PDF'indeki ürün listesi üstü turuncu teslim şekli notu — Incoterm'e
    (ts.yukleme_sekli.kod) göre dinamik. FOB/EXW'de SATICI'nın (bizim) yükleme limanı —
    bkz. TanimSecenegi.ad, ör. "FOB İZMİR" — kullanılır, sonuna sabit ülke eklenir (bu
    firma yalnız Türkiye'den sevkiyat yapıyor). CFR/CIF/DAP'ta navlun/sigorta ALICI'ya
    kadar dahildir; varış konumu olarak ts.taraf (Cari/Aday Müşteri) şehir/ülkesi kullanılır
    (bkz. satis_teklif_pdf_baglam). Tanınmayan/boş kod (admin yeni bir satır eklemiş ama
    henüz kod atamamışsa) güvenli varsayılana düşer: eski statik "navlun dahil" cümlesi."""
    if not ts.yukleme_sekli_id:
        return ""
    kod = ts.yukleme_sekli.kod
    en = dil == "en"
    if kod in ("FOB", "EXW"):
        ulke = "Turkey" if en else "Türkiye"
        if yukleme_tipi_ad:
            if en:
                return f"Prices are {yukleme_sekli_ad}, {ulke}, based on a full {yukleme_tipi_ad}."
            return f"Fiyatlar {yukleme_sekli_ad}, {ulke}, {yukleme_tipi_ad} tam konteyner bazındadır."
        if en:
            return f"Prices are {yukleme_sekli_ad}, {ulke}."
        return f"Fiyatlar {yukleme_sekli_ad}, {ulke} bazındadır."
    if kod in ("CFR", "CIF"):
        varis = f"{sehir_ad}, {ulke_ad}" if sehir_ad and ulke_ad else (sehir_ad or ulke_ad)
        if not varis:
            return ""
        if en:
            cumle = f"Sea freight to {varis} is included."
            if kod == "CIF":
                cumle += " Insurance is included."
        else:
            cumle = f"Fiyatlara {varis} varış limanına kadar deniz navlunu dahildir."
            if kod == "CIF":
                cumle += " Sigorta dahildir."
        return cumle
    if kod == "DAP":
        varis = f"{sehir_ad}, {ulke_ad}" if sehir_ad and ulke_ad else (sehir_ad or ulke_ad)
        if not varis:
            return ""
        if en:
            return f"Freight to {varis} is included."
        return f"Fiyatlara {varis} adresine kadar nakliye dahildir."
    if kod == "NAKLIYE_DAHIL":
        return "Domestic freight is included." if en else "Fiyatlara yurt içi nakliye dahildir."
    if kod == "NAKLIYE_HARIC":
        return ""
    # Tanınmayan/boş kod — güvenli varsayılan: eski statik cümle (yükleme tipi varsa).
    if yukleme_tipi_ad:
        return f"{E['navlun_on']} {yukleme_tipi_ad}{E['navlun_son']}"
    return ""


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
    # "fiyatlara KDV dahil değildir" ifadesi yurtdışı alıcıyı yanıltır). ts.taraf: Cari VEYA
    # Aday Müşteri (CRM lead) — ortak alan adları (ulke/sehir/unvan) sayesinde tek erişimle
    # ikisini de kapsar (bkz. TeklifSiparis.taraf).
    yurt_ici = not ts.taraf.ulke_id or ts.taraf.ulke.kod == "TR"
    # navlun_var: yalnız KALEM FİYATI gösterimini kontrol eder (nakliye_dahil_fiyat mı,
    # net_birim_fiyat + "(navlun hariç)" mi) — bu, teslim şekli notundan (aşağıda,
    # yukleme_sekli'ye göre) bağımsız bir hesaplama yöntemi bayrağıdır, DEĞİŞMEDİ.
    navlun_var = ts.navlun_tutari is not None and bool(ts.yukleme_tipi_id)
    yukleme_sekli_ad = ts.yukleme_sekli.ad_dil(dil) if ts.yukleme_sekli_id else ""
    yukleme_tipi_ad = ts.yukleme_tipi.ad_dil(dil) if ts.yukleme_tipi_id else ""
    ulke_ad = ts.taraf.ulke.ad_dil(dil) if ts.taraf.ulke_id else ""
    sehir_ad = ts.taraf.sehir.ad_dil(dil) if ts.taraf.sehir_id else ""
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
        "yukleme_sekli_ad": yukleme_sekli_ad,
        "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil(dil) if ts.odeme_kosulu_id else "",
        "yukleme_tipi_ad": yukleme_tipi_ad,
        "teslim_suresi_ad": ts.teslim_suresi.ad_dil(dil) if ts.teslim_suresi_id else "",
        "ulke_ad": ulke_ad,
        "sehir_ad": sehir_ad,
        "navlun_var": navlun_var,
        "teslim_notu": _teslim_sekli_notu(
            ts, dil, yukleme_sekli_ad, yukleme_tipi_ad, sehir_ad, ulke_ad, E),
        "notlar": notlar,
        "firma": firma_servis.firma_bilgisi_getir(),
        "hazirlayan": kullanici.get_full_name() or kullanici.get_username(),
        "hazirlayan_eposta": kullanici.email,
        "hazirlayan_telefon": kullanici_telefon(kullanici),
        "yurt_ici": yurt_ici,
    }


# Satış Proforması PDF'i etiketleri — Teklif'in fiyat listesi/katalog PDF'inden farklı,
# tablo + toplam ağırlıklı bir "proforma fatura" tasarımı (bkz. satis_proforma_pdf.html).
_PDF_ETIKET_PROFORMA = {
    "tr": {
        "baslik": "PROFORMA FATURA", "alici": "Alıcı", "satici": "Satıcı",
        "unvan": "Unvan", "ilgili_kisi": "İlgili Kişi", "ulke": "Ülke", "adres": "Adres",
        "telefon": "Telefon", "eposta": "E-posta", "vergi_dairesi": "Vergi Dairesi",
        "vergi_no": "Vergi No", "proforma_no": "Proforma No", "tarih": "Tarih",
        "gecerlilik": "Geçerlilik Tarihi", "para_birimi": "Para Birimi",
        "yukleme_sekli": "Teslim / Yükleme Şekli", "odeme_kosulu": "Ödeme Koşulu",
        "yukleme_tipi": "Yükleme Tipi", "teslim_suresi": "Teslim Süresi", "navlun": "Navlun",
        "urun": "Ürün", "miktar": "Miktar", "fiyat": "Fiyat", "tutar": "Tutar", "kdv": "KDV",
        "agirlik": "Ağırlık (kg)", "cbm": "CBM (m³)", "toplam": "TOPLAM",
        "navlun_dahil_toplam": "TOPLAM (Navlun Dahil)",
        "ara_toplam": "Ara Toplam", "kdv_toplam": "KDV Toplam",
        "genel_toplam": "GENEL TOPLAM", "banka_bilgileri": "Banka Bilgileri",
        "banka": "Banka", "sube": "Banka Şubesi",
        "hesap_sahibi": "Hesap Sahibi", "swift_kod": "Swift Kodu",
        "hazirlayan": "Hazırlayan", "notlar": "Notlar",
        "not_gecerlilik_tarihli": "Bu proforma {tarih} tarihine kadar geçerlidir.",
        "sayfa": "Sayfa", "altbilgi": "SEMTA Alüminyum Merdiven İmalatı · Proforma Fatura",
    },
    "en": {
        "baslik": "PROFORMA INVOICE", "alici": "To", "satici": "From",
        "unvan": "Company", "ilgili_kisi": "Contact Person", "ulke": "Country",
        "adres": "Address", "telefon": "Phone", "eposta": "E-mail",
        "vergi_dairesi": "Tax Office", "vergi_no": "Tax No", "proforma_no": "Proforma No",
        "tarih": "Date", "gecerlilik": "Valid Until", "para_birimi": "Currency",
        "yukleme_sekli": "Delivery Term", "odeme_kosulu": "Payment Term",
        "yukleme_tipi": "Transport Mode", "teslim_suresi": "Lead Time", "navlun": "Freight",
        "urun": "Item", "miktar": "Qty", "fiyat": "Price", "tutar": "Amount", "kdv": "VAT",
        "agirlik": "Weight (kg)", "cbm": "CBM (m³)", "toplam": "TOTAL",
        "navlun_dahil_toplam": "TOTAL (incl. Freight)",
        "ara_toplam": "Subtotal", "kdv_toplam": "VAT Total",
        "genel_toplam": "GRAND TOTAL", "banka_bilgileri": "Bank Details",
        "banka": "Bank", "sube": "Bank Branch",
        "hesap_sahibi": "Account Holder", "swift_kod": "SWIFT Code",
        "hazirlayan": "Prepared by", "notlar": "Notes",
        "not_gecerlilik_tarihli": "This proforma invoice is valid until {tarih}.",
        "sayfa": "Page", "altbilgi": "SEMTA Aluminium Ladder Manufacturing · Proforma Invoice",
    },
}


def satis_proforma_pdf_baglam(ts, kalemler, dil, kullanici):
    """Satış Proforması PDF şablonuna (satis_proforma_pdf.html) eklenecek bağlam — hem
    teklif_siparis_pdf view'ından hem testlerden çağrılır (satis_teklif_pdf_baglam ile aynı
    desen).

    Yurt içi/dışı: KDV yalnız yurt içi alıcıya anlamlı (ihracat KDV'den istisnadır) —
    ts.kdv_toplam/ts.genel_toplam MODEL property'leri kalemin kendi kdv FK'sına göre
    (stoğun kendi KDV oranı, alıcının ülkesinden BAĞIMSIZ) hesaplanır; ihracat proformasında
    bu yüzden buradaki kdv_toplam/genel_toplam context değişkenleri ayrıca hesaplanır ve
    yurt dışı alıcıda KDV'yi SIFIRLAR (şablon da ts.kdv_toplam değil BUNLARI kullanır)."""
    from decimal import Decimal

    E = _PDF_ETIKET_PROFORMA[dil]
    yurt_ici = not ts.taraf.ulke_id or ts.taraf.ulke.kod == "TR"
    toplam_miktar = Decimal("0")
    toplam_agirlik = Decimal("0")
    toplam_cbm = Decimal("0")
    for k in kalemler:
        k.urun_ad = k.stok.ad_dil(dil)
        k.agirlik_toplam = k.miktar * k.stok.agirlik if k.stok.agirlik is not None else None
        k.cbm_toplam = k.miktar * k.stok.cbm if k.stok.cbm is not None else None
        toplam_miktar += k.miktar
        if k.agirlik_toplam is not None:
            toplam_agirlik += k.agirlik_toplam
        if k.cbm_toplam is not None:
            toplam_cbm += k.cbm_toplam
    kdv_toplam = ts.kdv_toplam if yurt_ici else Decimal("0")
    genel_toplam = ts.ara_toplam + kdv_toplam
    # Navlun kalem fiyatlarına DAĞITILMAZ (bkz. Satış Teklifi'ndeki navlun_payi/nakliye_dahil_
    # fiyat — proformada BİLİNÇLİ olarak kullanılmaz); yalnız kalemler tablosunun TOPLAM
    # satırının hemen altında düz bir ek satır olarak gösterilir.
    toplam_navlun_dahil = (
        ts.ara_toplam + ts.navlun_tutari if ts.navlun_tutari is not None else None)
    firma = firma_servis.firma_bilgisi_getir()
    bankalar = ([_banka_hesap_pdf_goster(ts.banka_hesabi, firma)] if ts.banka_hesabi_id else [])
    notlar = []
    if ts.gecerlilik_teslim_tarihi:
        notlar.append(E["not_gecerlilik_tarihli"].format(
            tarih=ts.gecerlilik_teslim_tarihi.strftime("%d.%m.%Y")))
    return {
        "dil": dil, "E": E,
        "yukleme_sekli_ad": ts.yukleme_sekli.ad_dil(dil) if ts.yukleme_sekli_id else "",
        "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil(dil) if ts.odeme_kosulu_id else "",
        "yukleme_tipi_ad": ts.yukleme_tipi.ad_dil(dil) if ts.yukleme_tipi_id else "",
        "teslim_suresi_ad": ts.teslim_suresi.ad_dil(dil) if ts.teslim_suresi_id else "",
        "ulke_ad": ts.taraf.ulke.ad_dil(dil) if ts.taraf.ulke_id else "",
        "sehir_ad": ts.taraf.sehir.ad_dil(dil) if ts.taraf.sehir_id else "",
        "yurt_ici": yurt_ici,
        "kdv_toplam": kdv_toplam,
        "genel_toplam": genel_toplam,
        "toplam_miktar": toplam_miktar,
        "toplam_agirlik": toplam_agirlik if toplam_agirlik else None,
        "toplam_cbm": toplam_cbm if toplam_cbm else None,
        "toplam_navlun_dahil": toplam_navlun_dahil,
        "notlar": notlar,
        "firma": firma,
        "bankalar": bankalar,
        "hazirlayan": kullanici.get_full_name() or kullanici.get_username(),
        "hazirlayan_eposta": kullanici.email,
        "hazirlayan_telefon": kullanici_telefon(kullanici),
    }


# Satış Siparişi PDF'i etiketleri — Satış Proforması ile aynı desen; başlık "SİPARİŞ ONAYI",
# geçerlilik yerine teslim tarihi, genel toplam yerine (tevkifat düşülmüş) ödenecek gösterir.
_PDF_ETIKET_SIPARIS = {
    "tr": {
        "baslik": "SİPARİŞ ONAYI", "alici": "Alıcı", "satici": "Satıcı",
        "unvan": "Unvan", "ilgili_kisi": "İlgili Kişi", "ulke": "Ülke", "adres": "Adres",
        "telefon": "Telefon", "eposta": "E-posta", "vergi_dairesi": "Vergi Dairesi",
        "vergi_no": "Vergi No", "siparis_no": "Sipariş No", "tarih": "Tarih",
        "teslim_tarihi": "Teslim Tarihi", "para_birimi": "Para Birimi",
        "yukleme_sekli": "Teslim / Yükleme Şekli", "odeme_kosulu": "Ödeme Koşulu",
        "yukleme_tipi": "Yükleme Tipi", "teslim_suresi": "Teslim Süresi", "navlun": "Navlun",
        "urun": "Ürün", "miktar": "Miktar", "fiyat": "Fiyat", "tutar": "Tutar", "kdv": "KDV",
        "agirlik": "Ağırlık (kg)", "cbm": "CBM (m³)", "toplam": "TOPLAM",
        "navlun_dahil_toplam": "TOPLAM (Navlun Dahil)",
        "ara_toplam": "Ara Toplam", "kdv_toplam": "KDV Toplam", "tevkifat_toplam": "Tevkifat (−)",
        "odenecek": "ÖDENECEK", "banka_bilgileri": "Banka Bilgileri",
        "banka": "Banka", "sube": "Banka Şubesi",
        "hesap_sahibi": "Hesap Sahibi", "swift_kod": "Swift Kodu",
        "hazirlayan": "Hazırlayan", "notlar": "Notlar",
        "not_teslim_tarihli": "Tahmini teslim tarihi: {tarih}.",
        "sayfa": "Sayfa", "altbilgi": "SEMTA Alüminyum Merdiven İmalatı · Sipariş Onayı",
    },
    "en": {
        "baslik": "ORDER CONFIRMATION", "alici": "To", "satici": "From",
        "unvan": "Company", "ilgili_kisi": "Contact Person", "ulke": "Country",
        "adres": "Address", "telefon": "Phone", "eposta": "E-mail",
        "vergi_dairesi": "Tax Office", "vergi_no": "Tax No", "siparis_no": "Order No",
        "tarih": "Date", "teslim_tarihi": "Delivery Date", "para_birimi": "Currency",
        "yukleme_sekli": "Delivery Term", "odeme_kosulu": "Payment Term",
        "yukleme_tipi": "Transport Mode", "teslim_suresi": "Lead Time", "navlun": "Freight",
        "urun": "Item", "miktar": "Qty", "fiyat": "Price", "tutar": "Amount", "kdv": "VAT",
        "agirlik": "Weight (kg)", "cbm": "CBM (m³)", "toplam": "TOTAL",
        "navlun_dahil_toplam": "TOTAL (incl. Freight)",
        "ara_toplam": "Subtotal", "kdv_toplam": "VAT Total", "tevkifat_toplam": "Withholding (−)",
        "odenecek": "TOTAL DUE", "banka_bilgileri": "Bank Details",
        "banka": "Bank", "sube": "Bank Branch",
        "hesap_sahibi": "Account Holder", "swift_kod": "SWIFT Code",
        "hazirlayan": "Prepared by", "notlar": "Notes",
        "not_teslim_tarihli": "Estimated delivery date: {tarih}.",
        "sayfa": "Page", "altbilgi": "SEMTA Aluminium Ladder Manufacturing · Order Confirmation",
    },
}


def satis_siparis_pdf_baglam(ts, kalemler, dil, kullanici):
    """Satış Siparişi PDF şablonuna (satis_siparis_pdf.html) eklenecek bağlam — Satış
    Proforması ile aynı desen (satis_proforma_pdf_baglam), iki farkla: (1) sipariş GERÇEK bir
    ticari taahhüt olduğu için nihai tutar tevkifatı da düşen 'Ödenecek'tir (Proforma'daki
    'Genel Toplam' KDV dahil ama tevkifatsızdır); (2) banka hesabı Sipariş'in KENDİSİNDE
    seçilmez (yalnız Proforma ekranında bir alan var) — kaynak proformadan varsa devralınır."""
    from decimal import Decimal

    E = _PDF_ETIKET_SIPARIS[dil]
    yurt_ici = not ts.taraf.ulke_id or ts.taraf.ulke.kod == "TR"
    toplam_miktar = Decimal("0")
    toplam_agirlik = Decimal("0")
    toplam_cbm = Decimal("0")
    for k in kalemler:
        k.urun_ad = k.stok.ad_dil(dil)
        k.agirlik_toplam = k.miktar * k.stok.agirlik if k.stok.agirlik is not None else None
        k.cbm_toplam = k.miktar * k.stok.cbm if k.stok.cbm is not None else None
        toplam_miktar += k.miktar
        if k.agirlik_toplam is not None:
            toplam_agirlik += k.agirlik_toplam
        if k.cbm_toplam is not None:
            toplam_cbm += k.cbm_toplam
    kdv_toplam = ts.kdv_toplam if yurt_ici else Decimal("0")
    tevkifat_toplam = ts.tevkifat_toplam
    odenecek = ts.ara_toplam + kdv_toplam - tevkifat_toplam
    toplam_navlun_dahil = (
        ts.ara_toplam + ts.navlun_tutari if ts.navlun_tutari is not None else None)
    firma = firma_servis.firma_bilgisi_getir()
    banka_hesabi = ts.banka_hesabi or (ts.kaynak_proforma.banka_hesabi if ts.kaynak_proforma_id
                                       else None)
    bankalar = [_banka_hesap_pdf_goster(banka_hesabi, firma)] if banka_hesabi else []
    notlar = []
    if ts.gecerlilik_teslim_tarihi:
        notlar.append(E["not_teslim_tarihli"].format(
            tarih=ts.gecerlilik_teslim_tarihi.strftime("%d.%m.%Y")))
    return {
        "dil": dil, "E": E,
        "yukleme_sekli_ad": ts.yukleme_sekli.ad_dil(dil) if ts.yukleme_sekli_id else "",
        "odeme_kosulu_ad": ts.odeme_kosulu.ad_dil(dil) if ts.odeme_kosulu_id else "",
        "yukleme_tipi_ad": ts.yukleme_tipi.ad_dil(dil) if ts.yukleme_tipi_id else "",
        "teslim_suresi_ad": ts.teslim_suresi.ad_dil(dil) if ts.teslim_suresi_id else "",
        "ulke_ad": ts.taraf.ulke.ad_dil(dil) if ts.taraf.ulke_id else "",
        "sehir_ad": ts.taraf.sehir.ad_dil(dil) if ts.taraf.sehir_id else "",
        "yurt_ici": yurt_ici,
        "kdv_toplam": kdv_toplam,
        "tevkifat_toplam": tevkifat_toplam,
        "odenecek": odenecek,
        "toplam_miktar": toplam_miktar,
        "toplam_agirlik": toplam_agirlik if toplam_agirlik else None,
        "toplam_cbm": toplam_cbm if toplam_cbm else None,
        "toplam_navlun_dahil": toplam_navlun_dahil,
        "notlar": notlar,
        "firma": firma,
        "bankalar": bankalar,
        "hazirlayan": kullanici.get_full_name() or kullanici.get_username(),
        "hazirlayan_eposta": kullanici.email,
        "hazirlayan_telefon": kullanici_telefon(kullanici),
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
                        "satinalma_irsaliyeleri", "satis_teklifleri",
                        "satis_proformalari", "satis_siparisleri")
def teklif_siparis_pdf(request, pk):
    """Belgenin PDF'i (WeasyPrint, A4) — çek bordrosu PDF'iyle aynı desen."""
    import base64

    from django.contrib.staticfiles import finders
    from weasyprint import HTML

    ts = get_object_or_404(
        TeklifSiparis.objects.select_related(
            "cari", "cari__ulke", "cari__sehir",
            "aday_musteri", "aday_musteri__ulke", "aday_musteri__sehir",
            "banka_hesabi", "banka_hesabi__banka"), pk=pk)
    kalemler = list(ts.kalemler.filter(silindi=False).select_related("stok", "kdv", "tevkifat"))
    for k in kalemler:
        k.gorsel_b64 = None
        if k.stok.satis_urunu and k.stok.gorsel:
            k.gorsel_b64 = _pdf_gorsel_b64(k.stok.gorsel)
    teknik_kalemler = [k for k in kalemler if k.stok.satis_urunu]
    sat_teklif = (ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF
                 and ts.yon == TeklifSiparis.Yon.SATIS)
    sat_proforma = ts.belge_tur == TeklifSiparis.BelgeTur.PROFORMA
    sat_siparis = (ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS
                  and ts.yon == TeklifSiparis.Yon.SATIS)
    ctx = {"ts": ts, "kalemler": kalemler, "teknik_kalemler": teknik_kalemler,
           "sat_teklif": sat_teklif}
    logo_yol = finders.find("core/img/semta-logo.png")
    if logo_yol:
        with open(logo_yol, "rb") as f:
            ctx["logo_b64"] = base64.b64encode(f.read()).decode("ascii")
    sablon = "core/teklif_siparis_pdf.html"
    dosya_adi = _pdf_dosya_adi(ts.belge_no or ts.pk, ts.taraf.unvan)
    if sat_teklif:
        dil = "en" if request.GET.get("dil") == "en" else "tr"
        ctx.update(satis_teklif_pdf_baglam(ts, kalemler, dil, request.user))
        sablon = "core/satis_teklif_pdf.html"
    elif sat_proforma:
        dil = "en" if request.GET.get("dil") == "en" else "tr"
        ctx.update(satis_proforma_pdf_baglam(ts, kalemler, dil, request.user))
        sablon = "core/satis_proforma_pdf.html"
    elif sat_siparis:
        dil = "en" if request.GET.get("dil") == "en" else "tr"
        ctx.update(satis_siparis_pdf_baglam(ts, kalemler, dil, request.user))
        sablon = "core/satis_siparis_pdf.html"
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
                        aciklama=form.cleaned_data["aciklama"], kullanici=request.user,
                        kur_override=form.cleaned_data.get("kur"), sayilan_pb=form.cleaned_data.get("sayilan_pb"),
                        sayilan_doviz=form.cleaned_data.get("sayilan_doviz"), ilk_donem=form.cleaned_data.get("ilk_donem") or 0,
                        yatirim_projesi_id=(form.cleaned_data["yatirim_projesi"].pk
                                            if form.cleaned_data.get("yatirim_projesi") else None))
                else:
                    fis = kredi_karti_hareket_servis.hareket_olustur(
                        kart=kart, tip=tip, karsi=form.cleaned_data["karsi"],
                        tutar=form.cleaned_data["tutar"], tarih=form.cleaned_data["tarih"],
                        aciklama=form.cleaned_data["aciklama"], kullanici=request.user,
                        kur_override=form.cleaned_data.get("kur"),
                        yatirim_projesi_id=(form.cleaned_data["yatirim_projesi"].pk
                                            if form.cleaned_data.get("yatirim_projesi") else None))
                messages.success(request, tan["ad"] + f" kaydedildi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:kredi_karti_detay", pk=kart.pk)
            except kredi_karti_hareket_servis.KrediKartiHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        ilk = {k: request.GET[k] for k in ("gider", "tarih", "aciklama") if request.GET.get(k)}      # dönem ekstresi kısayolları (faiz/masraf)
        form = KrediKartiHareketForm(tip=tip, kart=kart, initial=ilk)
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
def kredi_karti_hareket_duzenle(request, pk, fis_pk):
    """Kredi kartı hareketini düzenle: açıklama, gider hesabı, yatırım projesi (fiş otomatik güncellenir)."""
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    try:
        bilgi = kredi_karti_hareket_servis.duzenleme_bilgisi(fis, kart)
    except kredi_karti_hareket_servis.KrediKartiHareketHatasi as e:
        messages.error(request, str(e))
        return redirect("core:kredi_karti_detay", pk=kart.pk)
    duzenlenebilir = bilgi["gider_duzenlenebilir"]
    taksit_duzenlenebilir = kart.para_birimi == "TRY" and bool(bilgi["kart_satiri"].alacak)      # harcama: taksit planı düzenlenebilir
    if request.method == "POST":
        form = KrediKartiHareketDuzenleForm(request.POST, gider_duzenlenebilir=duzenlenebilir,
                                            doviz_alanlari=bilgi["doviz_duzenlenebilir"], taksit_alanlari=taksit_duzenlenebilir)
        if form.is_valid():
            try:
                kredi_karti_hareket_servis.hareket_guncelle(
                    fis=fis, kart=kart, aciklama=form.cleaned_data["aciklama"],
                    gider=form.cleaned_data.get("gider") if duzenlenebilir else None,
                    yatirim_projesi_id=(form.cleaned_data["yatirim_projesi"].pk
                                        if form.cleaned_data.get("yatirim_projesi") else None),
                    sayilan_pb=form.cleaned_data.get("sayilan_pb"), sayilan_doviz=form.cleaned_data.get("sayilan_doviz"),
                    taksit_adedi=(form.cleaned_data.get("taksit_adedi") or 1) if taksit_duzenlenebilir else None,
                    ilk_donem=form.cleaned_data.get("ilk_donem") or 0 if taksit_duzenlenebilir else None,
                    kullanici=request.user)
                messages.success(request, f"Hareket güncellendi: fiş {fis.yil}/{fis.fis_no}.")
                return redirect("core:kredi_karti_detay", pk=kart.pk)
            except kredi_karti_hareket_servis.KrediKartiHareketHatasi as e:
                form.add_error(None, str(e))
    else:
        karsi = bilgi["karsi_satiri"]
        ilk = {"aciklama": fis.aciklama, "yatirim_projesi": karsi.yatirim_projesi_id}
        if duzenlenebilir:
            ilk["gider"] = karsi.hesap_id
        if bilgi["doviz_duzenlenebilir"] and karsi.islem_pb != "TRY":
            ilk["sayilan_pb"], ilk["sayilan_doviz"] = karsi.islem_pb, karsi.islem_tutari
        if taksit_duzenlenebilir:
            plan = kk_donem_servis.aktif_plan(fis)
            ilk["taksit_adedi"] = plan.taksit_adedi if plan else 1
            ilk["ilk_donem"] = kk_donem_servis.mevcut_kaydirma(kart, fis, plan)
        form = KrediKartiHareketDuzenleForm(initial=ilk, gider_duzenlenebilir=duzenlenebilir,
                                            doviz_alanlari=bilgi["doviz_duzenlenebilir"], taksit_alanlari=taksit_duzenlenebilir)
    return render(request, "core/kredi_karti_hareket_duzenle.html", {
        "kart": kart, "fis": fis, "form": form, "bilgi": bilgi, "tutar": bilgi["kart_satiri"].islem_tutari,
        "plan": kk_donem_servis.aktif_plan(fis) if taksit_duzenlenebilir else None})


@ekran_gerekli("kredi_karti")
def kredi_karti_hareket_sil(request, pk, fis_pk):
    """Kredi kartı hareketini (fiş + taksit planı) KALICI siler."""
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Kredi kartı hareketi · {kart.ad}",
        geri=reverse("core:kredi_karti_detay", args=[kart.pk]),
        sil=lambda: kredi_karti_hareket_servis.hareket_sil(fis=fis, kart=kart, kullanici=request.user))


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
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user, kurus_farki=cd["kurus_farki"])
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
                    muhasebe_kodu=cd["muhasebe"].hesap_kodu, kullanici=request.user, kurus_farki=cd["kurus_farki"])
                messages.success(request, "Kredi kartı güncellendi.")
                return redirect("core:kredi_kartlari")
            except finans_servis.FinansHatasi as e:
                form.add_error(None, str(e))
    else:
        form = KrediKartiForm(initial={
            "ad": kart.ad, "banka": kart.banka_id, "kart_son4": kart.kart_son4,
            "limit": kart.limit, "kesim_gunu": kart.kesim_gunu,
            "son_odeme_gunu": kart.son_odeme_gunu, "kurus_farki": kart.kurus_farki, "para_birimi": kart.para_birimi,
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
def kredi_hareket_sil(request, pk, fis_pk):
    """Kredi hareketini KALICI siler (ödeme fişiyse taksitler BEKLİYOR'a döner)."""
    kredi = get_object_or_404(Kredi, pk=pk, silindi=False)
    fis = get_object_or_404(YevmiyeFisi, pk=fis_pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Kredi hareketi · {kredi.ad}",
        geri=reverse("core:kredi_detay", args=[kredi.pk]),
        sil=lambda: kredi_hareket_servis.hareket_sil(fis=fis, kredi=kredi, kullanici=request.user))


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
                    kur_override=bform.cleaned_data.get("kur"),
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
                if "kur" in form.fields:
                    kwargs["kur_override"] = form.cleaned_data.get("kur")
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


@never_cache
@ekran_gerekli("cek_senet")
def cek_gorsel(request, pk, yuz):
    """Çek/senet ön ya da arka yüz görseli (özel depoda) — yalnız bu yetkili görünümle sunulur,
    /media/ üzerinden DEĞİL."""
    if yuz not in ("on", "arka"):
        raise Http404
    cek = get_object_or_404(CekSenet, pk=pk, silindi=False)
    alan = cek.on_yuz if yuz == "on" else cek.arka_yuz
    if not alan:
        raise Http404
    return _ozel_dosya_yanit(alan, f"cek-{cek.pk}-{yuz}")


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
    """Bordroyu KALICI siler (fiş + satırlar + bordro; giriş bordrosunda evraklar da). GET onay sayfası."""
    bordro = get_object_or_404(CekBordrosu, pk=pk, silindi=False)
    geri = reverse("core:cek_bordro_detay", args=[bordro.pk])
    ctx = _bordro_baglam(bordro)
    fisler = list(bordro.fisler.order_by("yil", "fis_no"))
    ozet = {"no": f"{bordro.get_tur_display()} #{bordro.pk}", "tarih": bordro.tarih, "tutar": ctx["toplam"],
            "aciklama": bordro.aciklama,
            "fis_nolari": ", ".join(f"{f.yil}/{f.fis_no}" for f in fisler),
            "evrak_sayisi": len(ctx["cekler"]), "giris_mi": ctx["giris_mi"], "satirlar": []}

    def sil():
        return cek_servis.bordro_sil(bordro, kullanici=request.user)
    if request.method == "POST":
        try:
            sil()
            messages.success(request, f"Bordro silindi: {ozet['no']} (fiş {ozet['fis_nolari'] or '-'}).")
            return redirect("core:cek_senetler")
        except cek_servis.CekHatasi as e:
            messages.error(request, str(e))
            return redirect(geri)
    yetkisiz = not request.user.is_superuser
    engel = None if yetkisiz else fis_sil_servis.onizle(sil)
    return render(request, "core/fis_sil_onay.html", {
        "baslik": f"Bordro · {ozet['no']}", "ozet": ozet, "engel": engel, "yetkisiz": yetkisiz,
        "geri": geri, "eylem": request.path, "bordro_mu": True})


@ekran_gerekli("cek_senet")
def cek_hesap_ayari(request):
    """Çek/Senet muhasebe hesap eşlemesi (durum × çek/senet matrisi)."""
    ayar = cek_servis.hesap_ayari()
    if request.method == "POST":
        form = CekHesapAyariForm(request.POST)
        if form.is_valid():
            try:
                kodlar = {a: (form.cleaned_data[a].hesap_kodu if form.cleaned_data[a] else "")
                          for a in (*cek_servis.AYAR_ALANLARI, "doviz_cari_ara")}
                cek_servis.hesap_ayari_kaydet(kodlar, kullanici=request.user)
                messages.success(request, "Çek/Senet muhasebe hesapları kaydedildi.")
                return redirect("core:cek_senetler")
            except cek_servis.CekHatasi as e:
                form.add_error(None, str(e))
    else:
        form = CekHesapAyariForm(initial={
            a: (getattr(ayar, a).hesap_kodu if getattr(ayar, a) else None)
            for a in (*cek_servis.AYAR_ALANLARI, "doviz_cari_ara")})
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
            y = cari_servis.yetkili_ekle(cari, **form.cleaned_data, kullanici=request.user)
            for uyari in getattr(y, "telefon_uyarilari", []):
                messages.error(request, uyari)
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
            g = cari_servis.yetkili_guncelle(yetkili, **form.cleaned_data, kullanici=request.user)
            for uyari in getattr(g, "telefon_uyarilari", []):
                messages.error(request, uyari)
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


@never_cache
@ekran_gerekli("cariler")
def cari_ek_indir(request, pk):
    """Cari aktivite ekini (özel depoda) yetkili görünümden sunar — /media/ üzerinden DEĞİL."""
    ek = get_object_or_404(
        CariAktiviteEk, pk=pk, silindi=False, aktivite__silindi=False,
        aktivite__cari__silindi=False)
    return _ozel_dosya_yanit(ek.dosya, ek.orijinal_ad)


# --- CRM: Aday Kaynakları (model/servis adı tarihsel nedenle "kategori" kalır — kullanıcıya
# görünen HER YER "Kaynak"tır, bkz. core/models.py::AdayMusteriKategori docstring'i) --------
@ekran_gerekli("aday_kategoriler")
def aday_kategoriler(request):
    alt_qs = AdayMusteriKategori.objects.filter(silindi=False).select_related("ust").order_by("kod")
    koklar = (AdayMusteriKategori.objects.filter(silindi=False, ust__isnull=True)
              .order_by("kod")
              .prefetch_related(Prefetch("alt_kategoriler", queryset=alt_qs)))
    return render(request, "core/aday_kategori_listesi.html", {"koklar": koklar})


@ekran_gerekli("aday_kategoriler")
def aday_kategori_ekle(request):
    ham_ust = (request.POST.get("ust") if request.method == "POST"
               else request.GET.get("ust"))
    ust = (AdayMusteriKategori.objects.filter(pk=ham_ust, silindi=False, ust__isnull=True).first()
           if ham_ust else None)
    if request.method == "POST":
        form = AdayMusteriKategoriForm(request.POST)
        if form.is_valid():
            try:
                k = aday_kategori_servis.aday_kategori_olustur(
                    ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    ust_id=ust.pk if ust else None, kullanici=request.user)
                messages.success(request, f"Kaynak eklendi: {k.ad}")
                return redirect("core:aday_kategoriler")
            except aday_kategori_servis.AdayKategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayMusteriKategoriForm()
    baslik = (f"{ust.ad} → Yeni Alt Kaynak" if ust else "Yeni Üst Kaynak")
    return render(request, "core/aday_kategori_form.html",
                  {"form": form, "baslik": baslik, "ekle": True, "ust": ust})


@ekran_gerekli("aday_kategoriler")
def aday_kategori_duzenle(request, pk):
    kat = get_object_or_404(AdayMusteriKategori, pk=pk, silindi=False)
    if request.method == "POST":
        form = AdayMusteriKategoriForm(request.POST)
        if form.is_valid():
            try:
                aday_kategori_servis.aday_kategori_guncelle(
                    kat, ad=form.cleaned_data["ad"], kod=form.cleaned_data["kod"],
                    kullanici=request.user)
                messages.success(request, "Kaynak güncellendi.")
                return redirect("core:aday_kategoriler")
            except aday_kategori_servis.AdayKategoriHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayMusteriKategoriForm(initial={"ad": kat.ad, "kod": kat.kod})
    return render(request, "core/aday_kategori_form.html",
                  {"form": form, "baslik": "Kaynak Düzenle", "ekle": False,
                   "ust": kat.ust, "duzenlenen": kat})


@ekran_gerekli("aday_kategoriler")
def aday_kategori_sil(request, pk):
    kat = get_object_or_404(AdayMusteriKategori, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            aday_kategori_servis.aday_kategori_sil(kat, kullanici=request.user)
            messages.success(request, f"Kaynak silindi: {kat.ad}")
        except aday_kategori_servis.AdayKategoriHatasi as e:
            messages.error(request, str(e))
    return redirect("core:aday_kategoriler")


def aday_kategoriler_eski_url(request):
    """Eski /crm/kategoriler/... adresleri — kalıcı yönlendirme (301), querystring korunur."""
    hedef = "/crm/kaynaklar/"
    if request.GET:
        hedef += "?" + request.GET.urlencode()
    return HttpResponsePermanentRedirect(hedef)


def aday_kategori_ekle_eski_url(request):
    hedef = "/crm/kaynaklar/ekle/"
    if request.GET:
        hedef += "?" + request.GET.urlencode()
    return HttpResponsePermanentRedirect(hedef)


def aday_kategori_duzenle_eski_url(request, pk):
    return HttpResponsePermanentRedirect(f"/crm/kaynaklar/{pk}/duzenle/")


def aday_kategori_sil_eski_url(request, pk):
    return HttpResponsePermanentRedirect(f"/crm/kaynaklar/{pk}/sil/")


# --- CRM: Tipler / Potansiyeller / Aşamalar (tanım tabloları) -------------------------
def _tanim_kullanim_sayilari(Model):
    """{pk: kullanım sayısı} — silinmemiş aday müşteri sayısı (liste ekranının 'kullanım
    sayısı' sütunu + sil/pasif-yap düğmelerinin görünürlüğü için)."""
    from django.db.models import Count, Q as _Q
    return dict(Model.objects.filter(silindi=False).annotate(
        n=Count("aday_musteriler", filter=_Q(aday_musteriler__silindi=False))
    ).values_list("pk", "n"))


def _kullanim_sayisi_isle(kayitlar, Model):
    """Her tanım nesnesine ``.kullanim_sayisi`` ekler (template'te ayrı bir dict-lookup
    filtresi gerekmesin diye)."""
    kullanim = _tanim_kullanim_sayilari(Model)
    for k in kayitlar:
        k.kullanim_sayisi = kullanim.get(k.pk, 0)
    return kayitlar


@ekran_gerekli("aday_tipleri")
def aday_tipleri(request):
    kayitlar = _kullanim_sayisi_isle(list(aday_tanim_servis.tum_tanimlar(AdayTipTanim)
                                          .select_related("cari_kategori_yurtici",
                                                          "cari_kategori_yurtdisi")),
                                     AdayTipTanim)
    return render(request, "core/aday_tip_listesi.html", {"kayitlar": kayitlar})


@ekran_gerekli("aday_tipleri")
def aday_tipi_ekle(request):
    if request.method == "POST":
        form = AdayTipTanimForm(request.POST)
        if form.is_valid():
            try:
                t = aday_tanim_servis.tip_olustur(
                    ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    cari_kategori_yurtici=form.cleaned_data["cari_kategori_yurtici"],
                    cari_kategori_yurtdisi=form.cleaned_data["cari_kategori_yurtdisi"],
                    cariye_donusturulebilir=form.cleaned_data["cariye_donusturulebilir"],
                    kullanici=request.user)
                messages.success(request, f"Tip eklendi: {t.ad}")
                return redirect("core:aday_tipleri")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayTipTanimForm(initial={"aktif": True, "cariye_donusturulebilir": True})
    return render(request, "core/aday_tip_form.html",
                  {"form": form, "baslik": "Yeni Tip", "ekle": True})


@ekran_gerekli("aday_tipleri")
def aday_tipi_duzenle(request, pk):
    tanim = get_object_or_404(AdayTipTanim, pk=pk, silindi=False)
    if request.method == "POST":
        form = AdayTipTanimForm(request.POST)
        if form.is_valid():
            try:
                aday_tanim_servis.tip_guncelle(
                    tanim, ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    cari_kategori_yurtici=form.cleaned_data["cari_kategori_yurtici"],
                    cari_kategori_yurtdisi=form.cleaned_data["cari_kategori_yurtdisi"],
                    cariye_donusturulebilir=form.cleaned_data["cariye_donusturulebilir"],
                    kullanici=request.user)
                messages.success(request, "Tip güncellendi.")
                return redirect("core:aday_tipleri")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayTipTanimForm(initial={
            "ad": tanim.ad, "sira": tanim.sira, "aktif": tanim.aktif, "renk": tanim.renk,
            "cari_kategori_yurtici": tanim.cari_kategori_yurtici_id,
            "cari_kategori_yurtdisi": tanim.cari_kategori_yurtdisi_id,
            "cariye_donusturulebilir": tanim.cariye_donusturulebilir})
    return render(request, "core/aday_tip_form.html",
                  {"form": form, "baslik": "Tip Düzenle", "ekle": False, "duzenlenen": tanim})


@ekran_gerekli("aday_tipleri")
def aday_tipi_sil(request, pk):
    tanim = get_object_or_404(AdayTipTanim, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            aday_tanim_servis.tanim_sil(tanim, kullanici=request.user)
            messages.success(request, f"Tip silindi: {tanim.ad}")
        except aday_tanim_servis.AdayTanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:aday_tipleri")


@ekran_gerekli("aday_tipleri")
def aday_tipi_pasif_yap(request, pk):
    tanim = get_object_or_404(AdayTipTanim, pk=pk, silindi=False)
    if request.method == "POST":
        aday_tanim_servis.tanim_pasif_yap(tanim, aktif=not tanim.aktif, kullanici=request.user)
        messages.success(request, f"{'Aktif' if tanim.aktif else 'Pasif'} yapıldı: {tanim.ad}")
    return redirect("core:aday_tipleri")


@ekran_gerekli("aday_potansiyelleri")
def aday_potansiyelleri(request):
    kayitlar = _kullanim_sayisi_isle(
        list(aday_tanim_servis.tum_tanimlar(AdayPotansiyelTanim)), AdayPotansiyelTanim)
    return render(request, "core/aday_potansiyel_listesi.html", {"kayitlar": kayitlar})


@ekran_gerekli("aday_potansiyelleri")
def aday_potansiyeli_ekle(request):
    if request.method == "POST":
        form = AdayPotansiyelTanimForm(request.POST)
        if form.is_valid():
            try:
                t = aday_tanim_servis.potansiyel_olustur(
                    ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    sicak=form.cleaned_data["sicak"], kullanici=request.user)
                messages.success(request, f"Potansiyel eklendi: {t.ad}")
                return redirect("core:aday_potansiyelleri")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayPotansiyelTanimForm(initial={"aktif": True})
    return render(request, "core/aday_potansiyel_form.html",
                  {"form": form, "baslik": "Yeni Potansiyel", "ekle": True})


@ekran_gerekli("aday_potansiyelleri")
def aday_potansiyeli_duzenle(request, pk):
    tanim = get_object_or_404(AdayPotansiyelTanim, pk=pk, silindi=False)
    if request.method == "POST":
        form = AdayPotansiyelTanimForm(request.POST)
        if form.is_valid():
            try:
                aday_tanim_servis.potansiyel_guncelle(
                    tanim, ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    sicak=form.cleaned_data["sicak"], kullanici=request.user)
                messages.success(request, "Potansiyel güncellendi.")
                return redirect("core:aday_potansiyelleri")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayPotansiyelTanimForm(initial={
            "ad": tanim.ad, "sira": tanim.sira, "aktif": tanim.aktif, "renk": tanim.renk,
            "sicak": tanim.sicak})
    return render(request, "core/aday_potansiyel_form.html",
                  {"form": form, "baslik": "Potansiyel Düzenle", "ekle": False,
                   "duzenlenen": tanim})


@ekran_gerekli("aday_potansiyelleri")
def aday_potansiyeli_sil(request, pk):
    tanim = get_object_or_404(AdayPotansiyelTanim, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            aday_tanim_servis.tanim_sil(tanim, kullanici=request.user)
            messages.success(request, f"Potansiyel silindi: {tanim.ad}")
        except aday_tanim_servis.AdayTanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:aday_potansiyelleri")


@ekran_gerekli("aday_potansiyelleri")
def aday_potansiyeli_pasif_yap(request, pk):
    tanim = get_object_or_404(AdayPotansiyelTanim, pk=pk, silindi=False)
    if request.method == "POST":
        aday_tanim_servis.tanim_pasif_yap(tanim, aktif=not tanim.aktif, kullanici=request.user)
        messages.success(request, f"{'Aktif' if tanim.aktif else 'Pasif'} yapıldı: {tanim.ad}")
    return redirect("core:aday_potansiyelleri")


@ekran_gerekli("aday_asamalari")
def aday_asamalari(request):
    kayitlar = _kullanim_sayisi_isle(
        list(aday_tanim_servis.tum_tanimlar(AdayAsamaTanim)), AdayAsamaTanim)
    return render(request, "core/aday_asama_listesi.html", {"kayitlar": kayitlar})


@ekran_gerekli("aday_asamalari")
def aday_asamasi_ekle(request):
    if request.method == "POST":
        form = AdayAsamaTanimForm(request.POST)
        if form.is_valid():
            try:
                t = aday_tanim_servis.asama_olustur(
                    ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    rol=form.cleaned_data["rol"], kullanici=request.user)
                messages.success(request, f"Aşama eklendi: {t.ad}")
                return redirect("core:aday_asamalari")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayAsamaTanimForm(initial={"aktif": True, "rol": AdayAsamaTanim.Rol.ARA})
    return render(request, "core/aday_asama_form.html",
                  {"form": form, "baslik": "Yeni Aşama", "ekle": True})


@ekran_gerekli("aday_asamalari")
def aday_asamasi_duzenle(request, pk):
    tanim = get_object_or_404(AdayAsamaTanim, pk=pk, silindi=False)
    if request.method == "POST":
        form = AdayAsamaTanimForm(request.POST)
        if form.is_valid():
            try:
                aday_tanim_servis.asama_guncelle(
                    tanim, ad=form.cleaned_data["ad"], sira=form.cleaned_data["sira"],
                    aktif=form.cleaned_data["aktif"], renk=form.cleaned_data["renk"],
                    rol=form.cleaned_data["rol"], kullanici=request.user)
                messages.success(request, "Aşama güncellendi.")
                return redirect("core:aday_asamalari")
            except aday_tanim_servis.AdayTanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayAsamaTanimForm(initial={
            "ad": tanim.ad, "sira": tanim.sira, "aktif": tanim.aktif, "renk": tanim.renk,
            "rol": tanim.rol})
    return render(request, "core/aday_asama_form.html",
                  {"form": form, "baslik": "Aşama Düzenle", "ekle": False, "duzenlenen": tanim})


@ekran_gerekli("aday_asamalari")
def aday_asamasi_sil(request, pk):
    tanim = get_object_or_404(AdayAsamaTanim, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            aday_tanim_servis.asama_sil(tanim, kullanici=request.user)
            messages.success(request, f"Aşama silindi: {tanim.ad}")
        except aday_tanim_servis.AdayTanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:aday_asamalari")


@ekran_gerekli("aday_asamalari")
def aday_asamasi_pasif_yap(request, pk):
    tanim = get_object_or_404(AdayAsamaTanim, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            aday_tanim_servis.asama_pasif_yap(
                tanim, aktif=not tanim.aktif, kullanici=request.user)
            messages.success(request, f"{'Aktif' if tanim.aktif else 'Pasif'} yapıldı: {tanim.ad}")
        except aday_tanim_servis.AdayTanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:aday_asamalari")


# --- CRM: Aday Müşteriler ----------------------------------------------------
_ADAY_SAYFA_BOYUTLARI = (25, 50, 100, 200)
_ADAY_GORUNUMLER = ("takip", "sicak", "temas_yok", "kapali", "tumu")
_ADAY_TAKIP_ETIKET = {"gecmis": "Gecikmiş", "bugun": "Bugün", "hafta": "Bu hafta (7 gün)",
                      "planli": "Tarihi planlı (hepsi)", "yok": "Tarihi yok"}
_ADAY_EPOSTA_ETIKET = {"gecerli": "Geçerli adresi var", "gecersiz": "Hepsi geçersiz",
                       "yok": "Adresi yok"}
_ADAY_SON_AKT_ETIKET = {"30": "Son 30 gün", "90": "31-90 gün", "eski": "90 günden eski",
                        "yok": "Hiç aktivite yok"}
_ADAY_CARI_ETIKET = {"var": "Cari oldu", "yok": "Aday (cari değil)"}
_ADAY_SIRALAMA_SECENEKLERI = (
    ("unvan", "Unvan (A→Z)"), ("-unvan", "Unvan (Z→A)"),
    ("sonraki", "Sonraki adım (yakın→uzak)"), ("-sonraki", "Sonraki adım (uzak→yakın)"),
    ("son_akt", "Son aktivite (eski→yeni)"), ("-son_akt", "Son aktivite (yeni→eski)"),
    ("potansiyel", "Potansiyel (yüksek→düşük)"),
)
_ADAY_SIRALAMA_HARITASI = {
    "unvan": (F("unvan").asc(),),
    "-unvan": (F("unvan").desc(),),
    "sonraki": (F("sonraki_adim_tarihi").asc(nulls_last=True),),
    "-sonraki": (F("sonraki_adim_tarihi").desc(nulls_last=True),),
    "son_akt": (F("son_aktivite_tarihi").asc(nulls_last=True),),
    "-son_akt": (F("son_aktivite_tarihi").desc(nulls_last=True),),
    # Potansiyel'in kendi sira alanı (tanım tablosu) TEK kaynak — Yüksek=1/Orta=2/Düşük=3
    # tohum verisiyle "yüksek→düşük" ile aynı yönde (bkz. migration 0132).
    "potansiyel": (F("potansiyel__sira").asc(nulls_last=True), F("unvan").asc()),
}


def _tanim_coz(Model, deger):
    """GET parametresindeki tip/potansiyel/asama değerini tanım kaydına çözer — yeni linkler
    tanım pk'sı, eski linkler sistem_kodu (metin, ör. ?tip=ARACI) gönderir; ikisi de kabul
    edilir (spec: eski filtre parametreleri kırılmasın). Pasif tanımlar da bulunur (filtreleme
    var-olan veriyi süzer, yeni değer seçmez)."""
    if not deger:
        return None
    qs = Model.objects.filter(silindi=False)
    if deger.isdigit():
        bulunan = qs.filter(pk=deger).first()
        if bulunan:
            return bulunan
    return qs.filter(sistem_kodu=deger).first()


def _aday_tab_q(gorunum, bugun):
    """Sekme kuralı — hem sekme sayaçlarında (Count filter=) hem asıl listede (filter())
    AYNI Q nesnesinden kullanılır (tek kaynak, iki temsil arasında sürüklenme riski yok).
    Cariye dönüşmüş adaylar Takibim/Sıcak/Temas yok'tan düşer (artık aktif bir "lead"
    değiller); Kapalı ve Tümü'nde görünmeye devam ederler (spec: Cariye Dönüştür yeniden
    yazılması, madde 4). Sıcak/Temas yok/Kapalı artık AdayPotansiyelTanim.sicak ve
    AdayAsamaTanim.rol'den okunur (bkz. CRM > Tipler/Potansiyeller/Aşamalar — spec: 'Kategori'
    → 'Kaynak' + tanım ekranları). CARI rollü aşama KAPALI gibi davranmaz (Kapalı sekmesine
    girmez) ama Takibim/Sıcak/Temas yok'a da girmez — elle bu aşamaya alınmış, henüz
    dönüşmemiş bir aday da (cari__isnull=True) düşsün (spec: hızlı işlemler/otomatik aşama)."""
    if gorunum == "takip":
        return (Q(sonraki_adim_tarihi__isnull=False)
                & Q(sonraki_adim_tarihi__lte=bugun + datetime.timedelta(days=7))
                & ~Q(asama__rol__in=(AdayAsamaTanim.Rol.KAPALI, AdayAsamaTanim.Rol.CARI))
                & Q(cari__isnull=True))
    if gorunum == "sicak":
        return (Q(potansiyel__sicak=True)
                & ~Q(asama__rol__in=(AdayAsamaTanim.Rol.KAPALI, AdayAsamaTanim.Rol.CARI))
                & Q(cari__isnull=True))
    if gorunum == "temas_yok":
        return Q(asama__rol=AdayAsamaTanim.Rol.BASLANGIC) & Q(cari__isnull=True)
    if gorunum == "kapali":
        return Q(asama__rol=AdayAsamaTanim.Rol.KAPALI)
    return Q()  # tumu


def _aday_tab_sira(gorunum, takip_secim):
    if gorunum == "takip":
        return (F("sonraki_adim_tarihi").asc(nulls_last=True),)
    if gorunum == "sicak":
        return (F("son_aktivite_tarihi").desc(nulls_last=True),)
    if gorunum in {"temas_yok", "kapali"}:
        return (F("unvan").asc(),)
    # tumu: mevcut (eski) davranış — Takip filtresi seçiliyken sonraki adıma göre.
    if takip_secim in {"gecmis", "bugun", "hafta", "planli", "yok"}:
        return (F("sonraki_adim_tarihi").asc(nulls_last=True), "-created_at")
    return ("-created_at",)


def _aday_qs_with(request, **degisiklikler):
    """Mevcut querystring'i korur; her kwarg None ise o parametreyi kaldırır, değilse
    değerini değiştirir. sayfa her zaman kaldırılır (parametre değişince 1. sayfaya dön)."""
    qs = request.GET.copy()
    qs.pop("sayfa", None)
    for anahtar, deger in degisiklikler.items():
        if deger is None:
            qs.pop(anahtar, None)
        else:
            qs[anahtar] = deger
    kodlu = qs.urlencode()
    return "?" + kodlu if kodlu else "?"


def _aday_siralama_baglantisi(request, sirala_simdi, anahtar, tersi_var=True):
    if sirala_simdi == anahtar:
        sonraki, ok = (f"-{anahtar}" if tersi_var else anahtar), "▲"
    elif tersi_var and sirala_simdi == f"-{anahtar}":
        sonraki, ok = anahtar, "▼"
    else:
        sonraki, ok = anahtar, ""
    return {"url": _aday_qs_with(request, sirala=sonraki), "ok": ok}


def _whatsapp_secenekleri(aday):
    """Liste satırındaki 🟢 WhatsApp hızlı butonu için tıklanabilir seçenek listesi —
    yalnız WhatsApp işaretli VE '+' ile başlayan numaralar (mevcut wa_link filtresi
    yeniden kullanılır, spec). Kartın telefon/telefon_2'si + whatsapp_yetkilileri
    (bkz. aday_musteriler'deki Prefetch) taranır; boşsa buton hiç gösterilmez."""
    secenekler = []
    if aday.telefon_whatsapp:
        link = wa_link(aday.telefon)
        if link:
            secenekler.append({"etiket": f"Kart: {aday.telefon}", "url": link})
    if aday.telefon_2_whatsapp:
        link = wa_link(aday.telefon_2)
        if link:
            etiket = "Kart (2)" if secenekler else "Kart"
            secenekler.append({"etiket": f"{etiket}: {aday.telefon_2}", "url": link})
    for y in getattr(aday, "whatsapp_yetkilileri", []):
        link = wa_link(y.telefon)
        if link:
            secenekler.append({"etiket": f"{y.ad_soyad}: {y.telefon}", "url": link})
    return secenekler


def _aday_form_kw(cd):
    """AdayMusteriForm cleaned_data -> aday servis kwargs (FK'ler -> *_id)."""
    g = lambda x: x.pk if x else None
    return dict(
        unvan=cd["unvan"], ilgili_kisi=cd["ilgili_kisi"], telefon=cd["telefon"],
        telefon_whatsapp=cd["telefon_whatsapp"],
        telefon_2=cd["telefon_2"], telefon_2_whatsapp=cd["telefon_2_whatsapp"],
        eposta=cd["eposta"], eposta_2=cd["eposta_2"],
        eposta_gecersiz=cd["eposta_gecersiz"], eposta_2_gecersiz=cd["eposta_2_gecersiz"],
        web=cd["web"],
        ulke_id=g(cd["ulke"]), sehir_id=g(cd["sehir"]), adres=cd["adres"],
        kategori_id=g(cd["kategori"]), para_birimi=cd["para_birimi"],
        iskonto_yuzdesi=cd["iskonto_yuzdesi"],
        tip_id=g(cd["tip"]), potansiyel_id=g(cd["potansiyel"]), asama_id=g(cd["asama"]),
        kapanis_nedeni=cd["kapanis_nedeni"],
        sonraki_adim=cd["sonraki_adim"], sonraki_adim_tarihi=cd["sonraki_adim_tarihi"],
        farkli_firma_onay=cd["farkli_firma_onay"])


def _aday_duzenlenebilir_kontrol(request, aday):
    """Cariye dönüşmüş aday salt okunur — düzenle/sil/yetkili/aktivite işlemleri view
    seviyesinde engellenir (spec: butonlar zaten gizli, bu son çare/doğrudan URL koruması).
    Engelliyse redirect döner (view onu döndürüp çıkar), aksi halde None."""
    if aday.cari_id:
        messages.error(request, "Bu aday cariye dönüştürülmüş; kaydı salt okunur.")
        return redirect("core:aday_musteri_detay", pk=aday.pk)
    return None


@ekran_gerekli("aday_musteriler")
def aday_musteriler(request):
    ara = (request.GET.get("ara") or "").strip()
    # "kaynak" yeni kanonik parametre; eski "kategori" linkleri de çalışır (spec).
    kaynak_id = request.GET.get("kaynak") or request.GET.get("kategori") or ""
    sehir_id = request.GET.get("sehir") or ""
    ulke_id = request.GET.get("ulke") or ""
    tip_secim = request.GET.get("tip") or ""
    potansiyel_secim = request.GET.get("potansiyel") or ""
    asama_secim = request.GET.get("asama") or ""
    takip_secim = request.GET.get("takip") or ""
    eposta_secim = request.GET.get("eposta") or ""
    son_akt_secim = request.GET.get("son_akt") or ""
    cari_secim = request.GET.get("cari") or ""
    sirala = request.GET.get("sirala") or ""
    try:
        boyut = int(request.GET.get("boyut", 50))
    except ValueError:
        boyut = 50
    if boyut not in _ADAY_SAYFA_BOYUTLARI:
        boyut = 50
    bugun = tr_bugun()
    kayitlar = aday_servis.aktif_aday_musteriler()
    if ara:
        buyuk = buyuk_harf_tr(ara)
        # Telefon artık uluslararası INTERNATIONAL biçiminde boşluklu saklanabilir (bkz.
        # core.dogrulama.telefon_normalize) — kullanıcı genelde boşluksuz arar, o yüzden hem
        # arama terimi hem alan boşluksuzlaştırılıp öyle karşılaştırılır (Replace() ek sorgu
        # eklemez, aynı SELECT'e sütun ekler — assertNumQueries etkilenmez).
        ara_duz = ara.replace(" ", "")
        # Yetkili/aktivite eşleşmesi Exists ile — JOIN + distinct YOK (satır çoğalmaz, sorgu
        # sayısı artmaz: aynı SQL ifadesine gömülü korele alt sorgu, bkz. assertNumQueries
        # testi). aciklama TR büyük harfe ÇEVRİLMEDEN saklanır (serbest metin — unvan/adres
        # gibi kimlik alanı değil), o yüzden icontains (mevcut telefon/eposta/web deseniyle
        # aynı) — DB Türkçe collation'a güvenir (bkz. CLAUDE.md); contains+buyuk_harf_tr
        # yalnız YAZARKEN zaten büyük harfe çevrilen alanlarda (unvan/ilgili_kisi/adres) işe
        # yarar.
        yetkili_eslesme = AdayYetkili.objects.filter(
            aday_id=OuterRef("pk"), silindi=False
        ).annotate(_telefon_duz=Replace("telefon", Value(" "), Value(""))).filter(
            Q(ad_soyad__contains=buyuk) | Q(_telefon_duz__icontains=ara_duz)
            | Q(eposta__icontains=ara))
        aktivite_eslesme = AdayAktivite.objects.filter(
            aday_id=OuterRef("pk"), silindi=False, aciklama__icontains=ara)
        kayitlar = kayitlar.annotate(
            _telefon_duz=Replace("telefon", Value(" "), Value("")),
            _telefon_2_duz=Replace("telefon_2", Value(" "), Value(""))
        ).filter(
            Q(unvan__contains=buyuk) | Q(ilgili_kisi__contains=buyuk)
            | Q(_telefon_duz__icontains=ara_duz) | Q(_telefon_2_duz__icontains=ara_duz)
            | Q(eposta__icontains=ara) | Q(eposta_2__icontains=ara)
            | Q(web__icontains=ara) | Q(adres__contains=buyuk)
            | Q(Exists(yetkili_eslesme)) | Q(Exists(aktivite_eslesme)))
    if kaynak_id:
        # Hiyerarşik filtre: üst kaynak seçilince alt kaynaklardaki adaylar da gelir
        # (2 seviye sınırı sayesinde tek OR yeterli — bkz. AdayMusteriKategori).
        kayitlar = kayitlar.filter(
            Q(kategori_id=kaynak_id) | Q(kategori__ust_id=kaynak_id))
    if sehir_id:
        kayitlar = kayitlar.filter(sehir_id=sehir_id)
    if ulke_id:
        kayitlar = kayitlar.filter(ulke_id=ulke_id)
    tip_tanim = _tanim_coz(AdayTipTanim, tip_secim)
    if tip_tanim:
        kayitlar = kayitlar.filter(tip_id=tip_tanim.pk)
    potansiyel_tanim = None
    if potansiyel_secim == "BOS":
        kayitlar = kayitlar.filter(potansiyel__isnull=True)
    else:
        potansiyel_tanim = _tanim_coz(AdayPotansiyelTanim, potansiyel_secim)
        if potansiyel_tanim:
            kayitlar = kayitlar.filter(potansiyel_id=potansiyel_tanim.pk)
    asama_tanim = _tanim_coz(AdayAsamaTanim, asama_secim)
    if asama_tanim:
        kayitlar = kayitlar.filter(asama_id=asama_tanim.pk)
    if takip_secim == "gecmis":
        kayitlar = kayitlar.filter(sonraki_adim_tarihi__lt=bugun)
    elif takip_secim == "bugun":
        kayitlar = kayitlar.filter(sonraki_adim_tarihi=bugun)
    elif takip_secim == "hafta":
        kayitlar = kayitlar.filter(sonraki_adim_tarihi__gte=bugun,
                                   sonraki_adim_tarihi__lte=bugun + datetime.timedelta(days=7))
    elif takip_secim == "planli":
        kayitlar = kayitlar.filter(sonraki_adim_tarihi__isnull=False)
    elif takip_secim == "yok":
        kayitlar = kayitlar.filter(sonraki_adim_tarihi__isnull=True)
    _eposta_dolu_1, _eposta_dolu_2 = ~Q(eposta=""), ~Q(eposta_2="")
    _eposta_gecerli_1 = _eposta_dolu_1 & Q(eposta_gecersiz=False)
    _eposta_gecerli_2 = _eposta_dolu_2 & Q(eposta_2_gecersiz=False)
    if eposta_secim == "gecerli":
        kayitlar = kayitlar.filter(_eposta_gecerli_1 | _eposta_gecerli_2)
    elif eposta_secim == "gecersiz":
        kayitlar = (kayitlar.filter(_eposta_dolu_1 | _eposta_dolu_2)
                    .exclude(_eposta_gecerli_1 | _eposta_gecerli_2))
    elif eposta_secim == "yok":
        kayitlar = kayitlar.filter(eposta="", eposta_2="")
    if cari_secim == "var":
        kayitlar = kayitlar.filter(cari__isnull=False)
    elif cari_secim == "yok":
        kayitlar = kayitlar.filter(cari__isnull=True)

    # Son aktivite (tarih + tür) — korele Subquery (OuterRef), JOIN+GROUP BY YOK: filtre/
    # sıralama/sayım sıradan bir alan gibi çalışır (bkz. ix_aday_aktivite_aday_tarih index'i).
    _son_aktivite_sq = AdayAktivite.objects.filter(
        aday=OuterRef("pk"), silindi=False).order_by("-tarih", "-id")
    kayitlar = kayitlar.annotate(
        son_aktivite_tarihi=Subquery(_son_aktivite_sq.values("tarih")[:1]),
        son_aktivite_turu=Subquery(_son_aktivite_sq.values("tur")[:1]))
    # Liste satırındaki 🟢 WhatsApp hızlı butonu + kanal ikonu: kartın kendi alanları
    # (telefon_whatsapp/telefon_2_whatsapp) YETERLİ değilse yetkililerin WhatsApp işaretli
    # +'lı numaraları da gerekir (buton etiketi için ad_soyad+telefon) — Prefetch ile TEK
    # ek sorgu (satır sayısı kadar çoğalmaz), bkz. assertNumQueries testi.
    kayitlar = kayitlar.prefetch_related(Prefetch(
        "yetkililer",
        queryset=AdayYetkili.objects.filter(silindi=False, whatsapp=True, telefon__startswith="+"),
        to_attr="whatsapp_yetkilileri"))
    _son_akt_30_sinir = bugun - datetime.timedelta(days=29)
    _son_akt_90_sinir = bugun - datetime.timedelta(days=89)
    if son_akt_secim == "30":
        kayitlar = kayitlar.filter(son_aktivite_tarihi__gte=_son_akt_30_sinir)
    elif son_akt_secim == "90":
        kayitlar = kayitlar.filter(son_aktivite_tarihi__gte=_son_akt_90_sinir,
                                   son_aktivite_tarihi__lt=_son_akt_30_sinir)
    elif son_akt_secim == "eski":
        kayitlar = kayitlar.filter(son_aktivite_tarihi__lt=_son_akt_90_sinir)
    elif son_akt_secim == "yok":
        kayitlar = kayitlar.filter(son_aktivite_tarihi__isnull=True)

    # Sekme sayıları: arama + yukarıdaki TÜM filtreler uygulanmış, yalnız sekme kuralı hariç
    # (tek sorgu — Count(filter=) ile 5 koşullu sayım aynı anda).
    filtreli = kayitlar
    sekme_sayilari = filtreli.aggregate(**{
        g: Count("pk", filter=_aday_tab_q(g, bugun)) for g in _ADAY_GORUNUMLER})
    gorunum = request.GET.get("gorunum") or ""
    if gorunum not in _ADAY_GORUNUMLER:
        gorunum = "takip" if sekme_sayilari["takip"] > 0 else "tumu"
    kayitlar = filtreli.filter(_aday_tab_q(gorunum, bugun))
    siralama_ifadeleri = (_ADAY_SIRALAMA_HARITASI[sirala] if sirala in _ADAY_SIRALAMA_HARITASI
                          else _aday_tab_sira(gorunum, takip_secim))
    kayitlar = kayitlar.order_by(*siralama_ifadeleri, "pk")

    gecikmis_sayisi = aday_servis.aktif_aday_musteriler().filter(
        sonraki_adim_tarihi__lt=bugun, cari__isnull=True).count()
    # Filtre seçenekleri yalnız en az bir adayda fiilen kullanılanlardan oluşur (bkz.
    # cariler view'ındaki aynı desen — tüm kategori/lokasyon master verisini değil,
    # sayfadaki gerçek veriyi yansıtır).
    tumu = aday_servis.aktif_aday_musteriler()
    kaynaklar = list(AdayMusteriKategori.objects.filter(
        silindi=False, pk__in=tumu.exclude(kategori=None).values("kategori_id")
    ).order_by("kod"))
    # Tip/Potansiyel/Aşama: kullanılan/kullanılmayan ayrımı yok — sabit tanım tablosu, tümü
    # (aktif de pasif de) filtre seçeneği olarak gösterilir (mevcut veri süzülüyor olabilir).
    tip_secenekleri = [(str(t.pk), t.ad) for t in
                       AdayTipTanim.objects.filter(silindi=False).order_by("sira", "ad")]
    potansiyel_secenekleri = [(str(t.pk), t.ad) for t in
                              AdayPotansiyelTanim.objects.filter(silindi=False)
                              .order_by("sira", "ad")]
    asama_secenekleri = [(str(t.pk), t.ad) for t in
                         AdayAsamaTanim.objects.filter(silindi=False).order_by("sira", "ad")]
    # Şehir seçenekleri Ülke seçilmeden anlamsız (hangi ülkeninkiler gösterilecek?) — Ülke
    # seçilene kadar boş/kilitli, seçilince yalnız o ülkenin (fiilen kullanılan) şehirleri.
    sehirler = list(Sehir.objects.filter(
        silindi=False, ulke_id=ulke_id,
        pk__in=tumu.exclude(sehir=None).values("sehir_id")
    ).order_by("ad")) if ulke_id else []
    ulkeler = list(Ulke.objects.filter(
        silindi=False, pk__in=tumu.exclude(ulke=None).values("ulke_id")
    ).order_by("ad"))

    # --- Aktif filtre çipleri (arama kutusunun altında; ✕ yalnız o parametreyi kaldırır) ---
    cipler = []
    if ara:
        cipler.append({"etiket": f'Arama: "{ara}"', "url": _aday_qs_with(request, ara=None)})
    if kaynak_id:
        # Kaynaklar listesi yalnız FİİLEN kullanılan kayıtlardan oluşur — hiyerarşik filtre
        # yüzünden bir ÜST kaynak yalnızca alt kaynaklar üzerinden "kullanımda" olabilir,
        # bu yüzden çip etiketi ayrı (kullanım şartsız) bir sorguyla çözülür.
        kat = AdayMusteriKategori.objects.filter(pk=kaynak_id, silindi=False).first()
        cipler.append({"etiket": f"Kaynak: {kat.ad if kat else kaynak_id}",
                       "url": _aday_qs_with(request, kaynak=None, kategori=None)})
    if ulke_id:
        u = next((x for x in ulkeler if str(x.pk) == ulke_id), None)
        cipler.append({"etiket": f"Ülke: {u.ad if u else ulke_id}",
                       "url": _aday_qs_with(request, ulke=None)})
    if sehir_id:
        s = next((x for x in sehirler if str(x.pk) == sehir_id), None)
        cipler.append({"etiket": f"Şehir: {s.ad if s else sehir_id}",
                       "url": _aday_qs_with(request, sehir=None)})
    if tip_tanim:
        cipler.append({"etiket": f"Tip: {tip_tanim.ad}",
                       "url": _aday_qs_with(request, tip=None)})
    if potansiyel_secim == "BOS":
        cipler.append({"etiket": "Potansiyel: Belirlenmedi",
                       "url": _aday_qs_with(request, potansiyel=None)})
    elif potansiyel_tanim:
        cipler.append({"etiket": f"Potansiyel: {potansiyel_tanim.ad}",
                       "url": _aday_qs_with(request, potansiyel=None)})
    if asama_tanim:
        cipler.append({"etiket": f"Aşama: {asama_tanim.ad}",
                       "url": _aday_qs_with(request, asama=None)})
    if takip_secim in _ADAY_TAKIP_ETIKET:
        cipler.append({"etiket": f"Takip: {_ADAY_TAKIP_ETIKET[takip_secim]}",
                       "url": _aday_qs_with(request, takip=None)})
    if eposta_secim in _ADAY_EPOSTA_ETIKET:
        cipler.append({"etiket": f"E-posta: {_ADAY_EPOSTA_ETIKET[eposta_secim]}",
                       "url": _aday_qs_with(request, eposta=None)})
    if son_akt_secim in _ADAY_SON_AKT_ETIKET:
        cipler.append({"etiket": f"Son aktivite: {_ADAY_SON_AKT_ETIKET[son_akt_secim]}",
                       "url": _aday_qs_with(request, son_akt=None)})
    if cari_secim in _ADAY_CARI_ETIKET:
        cipler.append({"etiket": f"Cari durumu: {_ADAY_CARI_ETIKET[cari_secim]}",
                       "url": _aday_qs_with(request, cari=None)})
    aktif_filtre_sayisi = sum(1 for x in (
        kaynak_id, ulke_id, sehir_id, tip_secim, potansiyel_secim, asama_secim,
        takip_secim, eposta_secim, son_akt_secim, cari_secim) if x)

    # sirala TAŞINMAZ: sekmeye geçince o sekmenin kendi varsayılan sıralaması uygulanır
    # (kullanıcı önceki sekmede elle bir sıralama seçmiş olsa bile).
    sekmeler = [{"kod": g, "sayi": sekme_sayilari[g],
                "url": _aday_qs_with(request, gorunum=g, sirala=None),
                "aktif": g == gorunum} for g in _ADAY_GORUNUMLER]
    siralama_baglar = {
        "unvan": _aday_siralama_baglantisi(request, sirala, "unvan"),
        "sonraki": _aday_siralama_baglantisi(request, sirala, "sonraki"),
        "son_akt": _aday_siralama_baglantisi(request, sirala, "son_akt"),
        "potansiyel": _aday_siralama_baglantisi(request, sirala, "potansiyel", tersi_var=False),
    }

    sayfa = Paginator(kayitlar, boyut).get_page(request.GET.get("sayfa"))
    for a in sayfa:
        a.whatsapp_secenekleri = _whatsapp_secenekleri(a)
    sabit_qs = request.GET.copy()
    sabit_qs.pop("sayfa", None)
    return render(request, "core/aday_musteri_listesi.html", {
        "kayitlar": sayfa, "ara": ara, "secili_kaynak": kaynak_id,
        "secili_sehir": sehir_id, "secili_ulke": ulke_id, "boyut": boyut,
        "sayfa_boyutlari": _ADAY_SAYFA_BOYUTLARI, "kaynaklar": kaynaklar,
        "sehirler": sehirler, "ulkeler": ulkeler,
        "tip_secenekleri": tip_secenekleri,
        "secili_tip": str(tip_tanim.pk) if tip_tanim else "",
        "potansiyel_secenekleri": potansiyel_secenekleri,
        "secili_potansiyel": ("BOS" if potansiyel_secim == "BOS"
                              else (str(potansiyel_tanim.pk) if potansiyel_tanim else "")),
        "asama_secenekleri": asama_secenekleri,
        "secili_asama": str(asama_tanim.pk) if asama_tanim else "",
        "secili_takip": takip_secim, "secili_eposta": eposta_secim,
        "secili_son_akt": son_akt_secim, "secili_cari": cari_secim, "secili_sirala": sirala,
        "siralama_secenekleri": _ADAY_SIRALAMA_SECENEKLERI, "siralama_baglar": siralama_baglar,
        "gecikmis_sayisi": gecikmis_sayisi,
        "gecikmis_url": _aday_qs_with(request, gorunum="takip", takip="gecmis", sirala=None),
        "sekmeler": sekmeler, "gorunum": gorunum,
        "tumu_url": _aday_qs_with(request, gorunum="tumu", sirala=None),
        "cipler": cipler, "aktif_filtre_sayisi": aktif_filtre_sayisi,
        "sabit_qs": sabit_qs.urlencode(),
        "liste_url": request.get_full_path(),
        "bugun_iso": bugun.isoformat(),
        "aktivite_tur_secenekleri": AdayAktivite.Tur.choices})


def _aday_form_varsayilanlar():
    """Yeni aday formunun tip/aşama varsayılanı — eski sabit AdayTip.ADAY/AdayAsama.YENI
    yerine tanım tablosundaki sistem_kodu ile bulunur (bulunamazsa aktif ilk kayıt)."""
    tip0 = (AdayTipTanim.objects.filter(silindi=False, aktif=True, sistem_kodu="ADAY").first()
            or AdayTipTanim.objects.filter(silindi=False, aktif=True).order_by("sira").first())
    asama0 = (AdayAsamaTanim.objects.filter(
                silindi=False, aktif=True, rol=AdayAsamaTanim.Rol.BASLANGIC).first())
    return {"para_birimi": "TRY", "tip": tip0, "asama": asama0}


@ekran_gerekli("aday_musteriler")
def aday_musteri_ekle(request):
    eslesmeler = None
    if request.method == "POST":
        form = AdayMusteriForm(request.POST)
        if form.is_valid():
            try:
                aday = aday_servis.aday_musteri_olustur(
                    **_aday_form_kw(form.cleaned_data), kullanici=request.user)
            except aday_servis.MukerrerKayitBulunduHatasi as e:
                eslesmeler = e.eslesmeler
            except aday_servis.AdayHatasi as e:
                form.add_error(None, str(e))
            else:
                for uyari in getattr(aday, "telefon_uyarilari", []):
                    messages.error(request, uyari)
                messages.success(request, f"Aday müşteri eklendi: {aday.unvan}")
                return redirect("core:aday_musteri_detay", pk=aday.pk)
    else:
        form = AdayMusteriForm(initial=_aday_form_varsayilanlar())
    return render(request, "core/aday_musteri_form.html",
                  {"form": form, "baslik": "Yeni Aday Müşteri", "eslesmeler": eslesmeler})


@ekran_gerekli("aday_musteriler")
def aday_musteri_duzenle(request, pk):
    aday = get_object_or_404(AdayMusteri, pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aday)
    if engel:
        return engel
    eslesmeler = None
    if request.method == "POST":
        form = AdayMusteriForm(request.POST, duzenlenen_aday=aday)
        if form.is_valid():
            try:
                guncellenen = aday_servis.aday_musteri_guncelle(
                    aday, **_aday_form_kw(form.cleaned_data), kullanici=request.user)
            except aday_servis.MukerrerKayitBulunduHatasi as e:
                eslesmeler = e.eslesmeler
            except aday_servis.AdayHatasi as e:
                form.add_error(None, str(e))
            else:
                for uyari in getattr(guncellenen, "telefon_uyarilari", []):
                    messages.error(request, uyari)
                messages.success(request, "Aday müşteri güncellendi.")
                return redirect("core:aday_musteri_detay", pk=aday.pk)
    else:
        form = AdayMusteriForm(duzenlenen_aday=aday, initial={
            "unvan": aday.unvan, "ilgili_kisi": aday.ilgili_kisi, "telefon": aday.telefon,
            "telefon_whatsapp": aday.telefon_whatsapp,
            "telefon_2": aday.telefon_2, "telefon_2_whatsapp": aday.telefon_2_whatsapp,
            "eposta": aday.eposta, "eposta_2": aday.eposta_2,
            "eposta_gecersiz": aday.eposta_gecersiz, "eposta_2_gecersiz": aday.eposta_2_gecersiz,
            "web": aday.web,
            "ulke": aday.ulke_id, "sehir": aday.sehir_id, "adres": aday.adres,
            "kategori": aday.kategori_id, "para_birimi": aday.para_birimi,
            "iskonto_yuzdesi": aday.iskonto_yuzdesi,
            "tip": aday.tip_id, "potansiyel": aday.potansiyel_id, "asama": aday.asama_id,
            "kapanis_nedeni": aday.kapanis_nedeni,
            "sonraki_adim": aday.sonraki_adim, "sonraki_adim_tarihi": aday.sonraki_adim_tarihi})
    return render(request, "core/aday_musteri_form.html", {
        "form": form, "baslik": "Aday Müşteri Düzenle", "duzenlenen": aday,
        "eslesmeler": eslesmeler})


@ekran_gerekli("aday_musteriler")
def aday_musteri_detay(request, pk):
    aday = get_object_or_404(
        AdayMusteri.objects.select_related(
            "ulke", "sehir", "kategori", "cari", "tip", "potansiyel", "asama"),
        pk=pk, silindi=False)
    return render(request, "core/aday_musteri_detay.html", {
        "aday": aday, "aktiviteler": aday_servis.aktif_aday_aktiviteleri(aday),
        "yetkililer": aday_servis.aktif_aday_yetkilileri(aday)})


@ekran_gerekli("aday_musteriler")
def aday_musteri_sil(request, pk):
    aday = get_object_or_404(AdayMusteri, pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aday)
    if engel:
        return engel
    if request.method == "POST":
        aday_servis.aday_musteri_sil(aday, kullanici=request.user)
        messages.success(request, f"Aday müşteri silindi: {aday.unvan}")
    return redirect("core:aday_musteriler")


@ekran_gerekli("aday_musteriler")
def aday_cariye_donustur(request, pk):
    aday = get_object_or_404(AdayMusteri, pk=pk, silindi=False)
    if aday.cari_id:
        messages.info(request, "Bu aday zaten bir cariye dönüştürülmüş.")
        return redirect("core:cari_detay", pk=aday.cari_id)

    engel = aday_donustur_servis.donusturme_engeli_var_mi(aday)
    if engel:
        messages.error(request, engel)
        return render(request, "core/aday_cariye_donustur.html",
                      {"aday": aday, "engel": engel})

    mod = request.POST.get("mod") or request.GET.get("mod") or "A"
    if request.method == "POST" and mod == "A":
        form_a = AdayCariyeYeniCariForm(request.POST)
        form_b = AdayCariyeMevcutCariForm()
        if form_a.is_valid():
            try:
                cari = aday_donustur_servis.yeni_cari_ac(
                    aday, kullanici=request.user, **_cari_form_kw(form_a.cleaned_data))
                messages.success(request, f"Cariye dönüştürüldü: {cari.kod} — {cari.unvan}")
                return redirect("core:cari_detay", pk=cari.pk)
            except (aday_donustur_servis.AdayDonusturHatasi, cari_servis.CariHatasi) as e:
                form_a.add_error(None, str(e))
    elif request.method == "POST" and mod == "B":
        form_a = AdayCariyeYeniCariForm(initial=aday_donustur_servis.yeni_cari_baslangic_degerleri(aday))
        form_b = AdayCariyeMevcutCariForm(request.POST)
        if form_b.is_valid():
            try:
                cari = aday_donustur_servis.mevcut_cariye_bagla(
                    aday, form_b.cleaned_data["cari"], kullanici=request.user)
                messages.success(request, f"Cariye bağlandı: {cari.kod} — {cari.unvan}")
                return redirect("core:cari_detay", pk=cari.pk)
            except aday_donustur_servis.AdayDonusturHatasi as e:
                form_b.add_error(None, str(e))
    else:
        form_a = AdayCariyeYeniCariForm(
            initial=aday_donustur_servis.yeni_cari_baslangic_degerleri(aday))
        form_b = AdayCariyeMevcutCariForm()

    onizleme = None
    onizleme_cari_id = request.GET.get("mevcut_cari") or ""
    if onizleme_cari_id:
        secili_cari = Cari.objects.filter(pk=onizleme_cari_id, silindi=False).first()
        if secili_cari:
            alanlar = aday_donustur_servis.doldurulacak_alanlar(aday, secili_cari)
            etiketler = [v for k, v in {
                "telefon": "Telefon", "telefon_2": "Telefon 2", "eposta": "E-posta",
                "web": "Web", "adres": "Adres", "ilgili_kisi": "İlgili Kişi"}.items()
                if k in alanlar]
            onizleme = {"cari": secili_cari, "etiketler": etiketler}
            mod = "B"
            form_b.fields["cari"].initial = secili_cari.pk

    eslesmeler = aday_donustur_servis.eslesen_cariler(aday)
    return render(request, "core/aday_cariye_donustur.html", {
        "aday": aday, "form_a": form_a, "form_b": form_b, "mod": mod,
        "eslesmeler": eslesmeler, "onizleme": onizleme})


# --- Aday yetkilileri (CariYetkili ile birebir aynı desen) --------------------
@ekran_gerekli("aday_musteriler")
def aday_yetkili_ekle(request, aday_pk):
    aday = get_object_or_404(AdayMusteri, pk=aday_pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aday)
    if engel:
        return engel
    if request.method == "POST":
        form = AdayYetkiliForm(request.POST)
        if form.is_valid():
            y = aday_servis.aday_yetkili_ekle(aday, **form.cleaned_data, kullanici=request.user)
            for uyari in getattr(y, "telefon_uyarilari", []):
                messages.error(request, uyari)
            messages.success(request, "Yetkili kişi eklendi.")
            return redirect("core:aday_musteri_detay", pk=aday.pk)
    else:
        form = AdayYetkiliForm()
    return render(request, "core/aday_yetkili_form.html",
                  {"form": form, "baslik": "Yeni Yetkili Kişi", "aday": aday})


@ekran_gerekli("aday_musteriler")
def aday_yetkili_duzenle(request, pk):
    yetkili = get_object_or_404(AdayYetkili.objects.select_related("aday"), pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, yetkili.aday)
    if engel:
        return engel
    if request.method == "POST":
        form = AdayYetkiliForm(request.POST)
        if form.is_valid():
            g = aday_servis.aday_yetkili_guncelle(
                yetkili, **form.cleaned_data, kullanici=request.user)
            for uyari in getattr(g, "telefon_uyarilari", []):
                messages.error(request, uyari)
            messages.success(request, "Yetkili kişi güncellendi.")
            return redirect("core:aday_musteri_detay", pk=yetkili.aday_id)
    else:
        form = AdayYetkiliForm(initial={
            "ad_soyad": yetkili.ad_soyad, "unvan": yetkili.unvan, "telefon": yetkili.telefon,
            "whatsapp": yetkili.whatsapp, "eposta": yetkili.eposta, "notlar": yetkili.notlar})
    return render(request, "core/aday_yetkili_form.html",
                  {"form": form, "baslik": "Yetkili Kişi Düzenle", "aday": yetkili.aday})


@ekran_gerekli("aday_musteriler")
def aday_yetkili_sil(request, pk):
    yetkili = get_object_or_404(AdayYetkili.objects.select_related("aday"), pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, yetkili.aday)
    if engel:
        return engel
    if request.method == "POST":
        aday_servis.aday_yetkili_sil(yetkili, kullanici=request.user)
        messages.success(request, "Yetkili kişi silindi.")
    return redirect("core:aday_musteri_detay", pk=yetkili.aday_id)


def _aday_aktivite_ekleri_kaydet(request, aktivite, dosyalar):
    for f in dosyalar:
        try:
            aday_servis.aday_aktivite_ek_ekle(aktivite, dosya=f, kullanici=request.user)
        except aday_servis.AdayHatasi as e:
            messages.warning(request, str(e))


@ekran_gerekli("aday_musteriler")
def aday_aktivite_ekle(request, aday_pk):
    aday = get_object_or_404(AdayMusteri, pk=aday_pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aday)
    if engel:
        return engel
    if request.method == "POST":
        form = AdayAktiviteForm(request.POST)
        if form.is_valid():
            try:
                aktivite = aday_servis.aday_aktivite_ekle(
                    aday, **form.cleaned_data, sonraki_adim_guncelle=True, kullanici=request.user)
                _aday_aktivite_ekleri_kaydet(request, aktivite, request.FILES.getlist("dosyalar"))
                messages.success(request, f"Aktivite eklendi — {aday.unvan}.")
                # Liste satırındaki hızlı "+ Aktivite" penceresi buraya ?next=<liste url>#aday-
                # <id> ile POST eder — geçerliyse (açık yönlendirme koruması) oraya dönülür,
                # aksi halde (tam sayfa formu) her zamanki gibi aday detayına.
                sonraki = request.POST.get("next") or request.GET.get("next")
                if sonraki and url_has_allowed_host_and_scheme(
                        sonraki, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    return redirect(sonraki)
                return redirect("core:aday_musteri_detay", pk=aday.pk)
            except aday_servis.AdayHatasi as e:
                form.add_error(None, str(e))
    else:
        # Form adayın MEVCUT sonraki adımıyla dolu açılır (spec) — aktivitenin kendisi
        # yeni olduğu için tarih/tür/açıklama boş kalır.
        form = AdayAktiviteForm(initial={
            "sonraki_adim": aday.sonraki_adim, "sonraki_adim_tarihi": aday.sonraki_adim_tarihi})
    return render(request, "core/aday_aktivite_form.html",
                  {"form": form, "baslik": "Yeni Aktivite", "aday": aday})


@ekran_gerekli("aday_musteriler")
def aday_aktivite_duzenle(request, pk):
    aktivite = get_object_or_404(AdayAktivite.objects.select_related("aday"), pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aktivite.aday)
    if engel:
        return engel
    if request.method == "POST":
        form = AdayAktiviteForm(request.POST)
        if form.is_valid():
            try:
                aday_servis.aday_aktivite_guncelle(
                    aktivite, **form.cleaned_data, sonraki_adim_guncelle=True,
                    kullanici=request.user)
                _aday_aktivite_ekleri_kaydet(request, aktivite, request.FILES.getlist("dosyalar"))
                messages.success(request, "Aktivite güncellendi.")
                return redirect("core:aday_musteri_detay", pk=aktivite.aday_id)
            except aday_servis.AdayHatasi as e:
                form.add_error(None, str(e))
    else:
        form = AdayAktiviteForm(initial={
            "tarih": aktivite.tarih, "tur": aktivite.tur, "aciklama": aktivite.aciklama,
            "sonraki_adim": aktivite.aday.sonraki_adim,
            "sonraki_adim_tarihi": aktivite.aday.sonraki_adim_tarihi})
    return render(request, "core/aday_aktivite_form.html", {
        "form": form, "baslik": "Aktivite Düzenle", "aday": aktivite.aday,
        "aktivite": aktivite, "ekler": aktivite.ekler.filter(silindi=False)})


@ekran_gerekli("aday_musteriler")
def aday_aktivite_sil(request, pk):
    aktivite = get_object_or_404(AdayAktivite.objects.select_related("aday"), pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, aktivite.aday)
    if engel:
        return engel
    if request.method == "POST":
        aday_servis.aday_aktivite_sil(aktivite, kullanici=request.user)
        messages.success(request, "Aktivite silindi.")
    return redirect("core:aday_musteri_detay", pk=aktivite.aday_id)


@ekran_gerekli("aday_musteriler")
def aday_aktivite_ek_sil(request, pk):
    ek = get_object_or_404(
        AdayAktiviteEk.objects.select_related("aktivite__aday"), pk=pk, silindi=False)
    engel = _aday_duzenlenebilir_kontrol(request, ek.aktivite.aday)
    if engel:
        return engel
    if request.method == "POST":
        aday_servis.aday_aktivite_ek_sil(ek, kullanici=request.user)
        messages.success(request, "Dosya silindi.")
    return redirect("core:aday_aktivite_duzenle", pk=ek.aktivite_id)


@never_cache
@ekran_gerekli("aday_musteriler")
def aday_ek_indir(request, pk):
    """Aday aktivite ekini (özel depoda) yetkili görünümden sunar — /media/ üzerinden DEĞİL."""
    ek = get_object_or_404(
        AdayAktiviteEk, pk=pk, silindi=False, aktivite__silindi=False,
        aktivite__aday__silindi=False)
    return _ozel_dosya_yanit(ek.dosya, ek.orijinal_ad)


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
                    sira=cd["sira"], varsayilan=cd["varsayilan"], kullanici=request.user)
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
                    sira=cd["sira"], varsayilan=cd["varsayilan"], kullanici=request.user)
                messages.success(request, f"{baslik} güncellendi.")
                return redirect("core:secenek_listesi", slug=slug)
            except tanim_servis.TanimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = TanimSecenegiForm(kategori=kategori, initial={
            "ad": s.ad, "kod": s.kod, "ad_en": s.ad_en, "sira": s.sira,
            "varsayilan": s.varsayilan})
    return render(request, "core/tanim_secenek_form.html",
                  _secenek_ctx(slug, form=form, form_baslik=f"{baslik} Düzenle", duzenlenen=s))


@yonetici_gerekli
def secenek_varsayilan_ayarla(request, slug, pk):
    """Tanım listesi ekranındaki satır içi 'Varsayılan' checkbox'ı — tam form açmadan tek
    alanı değiştirir (kural 2). POST'ta checkbox işaretliyse True, yoksa False gelir."""
    kategori, baslik, _ = _secenek_kategori(slug)
    s = get_object_or_404(TanimSecenegi, pk=pk, silindi=False, kategori=kategori)
    if request.method == "POST":
        varsayilan = request.POST.get("varsayilan") == "1"
        try:
            tanim_servis.secenek_varsayilan_ayarla(s, varsayilan, kullanici=request.user)
            messages.success(
                request,
                f"{s.ad} varsayılan yapıldı." if varsayilan else f"{s.ad} varsayılan olmaktan çıkarıldı.")
        except tanim_servis.TanimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:secenek_listesi", slug=slug)


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


# === ÜRETİM — İş İstasyonu + Operasyon (rota) modeli (FASON'dan bağımsız) ===
OperasyonGirdiSatirFormSet = formset_factory(OperasyonGirdiSatirForm, extra=0)
OperasyonYanCiktiFormSet = formset_factory(OperasyonYanCiktiSatirForm, extra=0)


def _yan_ciktilar_post(post):
    """(formset, gonderildi): yan çıktı bölümü forma hiç gönderilmediyse (eski/özel istemci) boş-geçerli formset + gonderildi=False
    (düzenlemede mevcut yan çıktılar KORUNUR)."""
    if "yan-TOTAL_FORMS" in post:
        return OperasyonYanCiktiFormSet(post, prefix="yan"), True
    return OperasyonYanCiktiFormSet({"yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0"}, prefix="yan"), False
IhtiyacHesaplaSatirFormSet = formset_factory(IhtiyacHesaplaSatirForm, extra=0)
OperasyonKaydiGirdiDuzeltFormSet = formset_factory(OperasyonKaydiGirdiDuzeltForm, extra=0)
UretimEmriKalemSatirFormSet = formset_factory(
    UretimEmriKalemSatirForm, extra=0, min_num=1, validate_min=True)
SiparisUretimEmriSatirFormSet = formset_factory(SiparisUretimEmriSatirForm, extra=0)


# --- İş İstasyonları ---
@ekran_gerekli("is_istasyonlari")
def is_istasyonlari(request):
    return render(request, "core/is_istasyonu_listesi.html",
                  {"istasyonlar": uretim_servis.aktif_istasyonlar()})


@ekran_gerekli("is_istasyonlari")
def is_istasyonu_ekle(request):
    if request.method == "POST":
        form = IsIstasyonuForm(request.POST)
        if form.is_valid():
            try:
                i = uretim_servis.istasyon_olustur(**form.cleaned_data, kullanici=request.user)
                messages.success(request, f"İş istasyonu eklendi: {i.kod} — {i.ad}")
                return redirect("core:is_istasyonlari")
            except uretim_servis.UretimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = IsIstasyonuForm()
    return render(request, "core/is_istasyonu_form.html",
                  {"form": form, "baslik": "Yeni İş İstasyonu"})


@ekran_gerekli("is_istasyonlari")
def is_istasyonu_duzenle(request, pk):
    istasyon = get_object_or_404(IsIstasyonu, pk=pk, silindi=False)
    if request.method == "POST":
        form = IsIstasyonuForm(request.POST)
        if form.is_valid():
            try:
                uretim_servis.istasyon_guncelle(
                    istasyon, **form.cleaned_data, kullanici=request.user)
                messages.success(request, "İş istasyonu güncellendi.")
                return redirect("core:is_istasyonlari")
            except uretim_servis.UretimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = IsIstasyonuForm(initial={"kod": istasyon.kod, "ad": istasyon.ad})
    return render(request, "core/is_istasyonu_form.html",
                  {"form": form, "baslik": "İş İstasyonu Düzenle", "duzenlenen": istasyon})


@ekran_gerekli("is_istasyonlari")
def is_istasyonu_sil(request, pk):
    istasyon = get_object_or_404(IsIstasyonu, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            uretim_servis.istasyon_sil(istasyon, kullanici=request.user)
            messages.success(request, f"İş istasyonu silindi: {istasyon.kod}")
        except uretim_servis.UretimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:is_istasyonlari")


# --- Operasyon Tanımları ---
@ekran_gerekli("operasyon_tanimlari")
def operasyon_tanimlari(request):
    return render(request, "core/operasyon_tanimlari.html",
                  {"operasyonlar": uretim_servis.aktif_operasyonlar()})


def _form_hatalari(bform, formset, yan_formset) -> list:
    """Sayfa başı hata özeti: başlık formu + girdi satırları + yan çıktı satırları hataları (alan altındaki hatalarla birlikte gösterilir)."""
    out = []
    if bform is not None:
        out += [str(e) for e in bform.non_field_errors()]
        for ad, hatalar in bform.errors.items():
            if ad != "__all__":
                out += [f"{bform.fields[ad].label}: {e}" for e in hatalar]
    out += [str(e) for e in formset.non_form_errors()]
    for i, f in enumerate(formset, start=1):
        out += [f"Girdi {i}: {e}" for e in f.non_field_errors()]
        out += [f"Girdi {i} — {f.fields[ad].label}: {e}" for ad, hatalar in f.errors.items() if ad != "__all__" for e in hatalar]
    for i, f in enumerate(yan_formset, start=1):
        out += [f"Yan çıktı {i}: {e}" for e in f.non_field_errors()]
        out += [f"Yan çıktı {i} — {f.fields[ad].label}: {e}" for ad, hatalar in f.errors.items() if ad != "__all__" for e in hatalar]
    return out


def _operasyon_form_baglam(**ek):
    return {"boy_stok_idler": uretim_servis.boy_stok_idleri(), "stok_bilgi": uretim_servis.stok_bilgi_haritasi(), **ek}


@ekran_gerekli("operasyon_tanimlari")
def operasyon_ekle(request):
    """Yeni operasyon. ``?kopya=<operasyon pk>``: kaynağın istasyonu, çıktı miktarı, girdileri ve yan çıktılarıyla DOLU, ÇIKTI BOŞ açılır
    (kayıt yine bu görünümün normal oluşturma akışından geçer)."""
    kaynak = Operasyon.objects.filter(pk=request.GET.get("kopya") or 0, silindi=False).select_related("cikti").first()
    baslik = f"Operasyon Kopyala — kaynak: {kaynak.cikti.kod}" if kaynak else "Yeni Operasyon"
    hatalar = []
    if request.method == "POST":
        bform = OperasyonBaslikForm(request.POST)
        formset = OperasyonGirdiSatirFormSet(request.POST, prefix="satir")
        yan_formset, _yan_var = _yan_ciktilar_post(request.POST)
        if bform.is_valid() and formset.is_valid() and yan_formset.is_valid():
            satirlar = [(f.cleaned_data["girdi"], f.cleaned_data["miktar"])
                       for f in formset if f.dolu_mu()]
            yanlar = [(f.cleaned_data["stok"], f.cleaned_data["miktar"], f.cleaned_data["boy_mm"])
                      for f in yan_formset if f.dolu_mu()]
            cd = bform.cleaned_data
            try:
                uretim_servis.operasyon_olustur(
                    istasyon_id=cd["istasyon"].pk, cikti_id=cd["cikti"].pk,
                    cikti_miktar=cd["cikti_miktar"], satirlar=satirlar,
                    kullanici=request.user, boy_mm=cd.get("boy_mm"), yan_ciktilar=yanlar)
                messages.success(request, "Operasyon tanımı kaydedildi.")
                return redirect("core:operasyon_tanimlari")
            except uretim_servis.UretimHatasi as e:
                bform.add_error(None, str(e))
        hatalar = _form_hatalari(bform, formset, yan_formset)
    elif kaynak is not None:
        bform = OperasyonBaslikForm(initial={"istasyon": kaynak.istasyon_id, "cikti_miktar": kaynak.cikti_miktar})
        formset = OperasyonGirdiSatirFormSet(initial=[
            {"girdi": s.girdi_id, "miktar": s.miktar} for s in uretim_servis.operasyon_girdileri(kaynak)], prefix="satir")
        yan_formset = OperasyonYanCiktiFormSet(initial=[
            {"stok": y.stok_id, "miktar": y.miktar, "boy_mm": y.boy_mm}
            for y in uretim_servis.operasyon_yan_ciktilari(kaynak)], prefix="yan")
    else:
        bform = OperasyonBaslikForm()
        formset = OperasyonGirdiSatirFormSet(prefix="satir")
        yan_formset = OperasyonYanCiktiFormSet(prefix="yan")
    return render(request, "core/operasyon_form.html", _operasyon_form_baglam(
        bform=bform, formset=formset, yan_formset=yan_formset, baslik=baslik, hatalar=hatalar, kopya_kaynak=kaynak))


@ekran_gerekli("operasyon_tanimlari")
def operasyon_duzenle(request, pk):
    operasyon = get_object_or_404(Operasyon, pk=pk, silindi=False)
    hatalar = []
    if request.method == "POST":
        formset = OperasyonGirdiSatirFormSet(request.POST, prefix="satir")
        yan_formset, yan_var = _yan_ciktilar_post(request.POST)
        istasyon_id = request.POST.get("istasyon")
        cikti_miktar = request.POST.get("cikti_miktar")
        if formset.is_valid() and yan_formset.is_valid():
            satirlar = [(f.cleaned_data["girdi"], f.cleaned_data["miktar"])
                       for f in formset if f.dolu_mu()]
            yanlar = [(f.cleaned_data["stok"], f.cleaned_data["miktar"], f.cleaned_data["boy_mm"])
                      for f in yan_formset if f.dolu_mu()]
            try:
                uretim_servis.operasyon_guncelle(
                    operasyon, istasyon_id=istasyon_id, cikti_miktar=cikti_miktar,
                    satirlar=satirlar, kullanici=request.user,
                    boy_mm=request.POST.get("boy_mm", "") if yan_var or "boy_mm" in request.POST else uretim_servis.KORU,
                    yan_ciktilar=yanlar if yan_var else None)
                messages.success(request, "Operasyon tanımı güncellendi.")
                return redirect("core:operasyon_tanimlari")
            except uretim_servis.UretimHatasi as e:
                hatalar = [str(e)]
        else:
            hatalar = _form_hatalari(None, formset, yan_formset)
    else:
        formset = OperasyonGirdiSatirFormSet(initial=[
            {"girdi": s.girdi_id, "miktar": s.miktar}
            for s in uretim_servis.operasyon_girdileri(operasyon)], prefix="satir")
        yan_formset = OperasyonYanCiktiFormSet(initial=[
            {"stok": y.stok_id, "miktar": y.miktar, "boy_mm": y.boy_mm}
            for y in uretim_servis.operasyon_yan_ciktilari(operasyon)], prefix="yan")
    istasyonlar = uretim_servis.aktif_istasyonlar()
    return render(request, "core/operasyon_form.html", _operasyon_form_baglam(
        formset=formset, yan_formset=yan_formset, operasyon=operasyon, istasyonlar=istasyonlar,
        baslik="Operasyon Düzenle", hatalar=hatalar))


@ekran_gerekli("operasyon_tanimlari")
def operasyon_sil(request, pk):
    operasyon = get_object_or_404(Operasyon, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            uretim_servis.operasyon_sil(operasyon, kullanici=request.user)
            messages.success(request, "Operasyon tanımı silindi.")
        except uretim_servis.UretimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:operasyon_tanimlari")


# --- İhtiyaç Hesapla (salt-okunur, hiçbir kayıt açmaz) ---
@ekran_gerekli("ihtiyac_hesapla")
def ihtiyac_hesapla(request):
    sonuc = None
    if request.method == "POST":
        formset = IhtiyacHesaplaSatirFormSet(request.POST, prefix="satir")
        if formset.is_valid():
            kalemler = [(f.cleaned_data["hedef"], f.cleaned_data["miktar"])
                       for f in formset if f.dolu_mu()]
            if not kalemler:
                messages.error(request, "En az bir hedef ürün satırı girin.")
            else:
                try:
                    sonuc = uretim_servis.ihtiyac_hesapla(kalemler)
                except uretim_servis.UretimHatasi as e:
                    messages.error(request, str(e))
    else:
        formset = IhtiyacHesaplaSatirFormSet(prefix="satir")
    return render(request, "core/ihtiyac_hesapla.html", {"formset": formset, "sonuc": sonuc})


# --- Ürün Ağacı (salt-okunur GET görünümü; ağaç ihtiyac_hesapla'dan gelir, kayıt açmaz) ---
URUN_AGACI_ACIK_SEVIYE = 2      # kök + bir alt seviye açık başlar; derindekiler kapalı


@ekran_gerekli("urun_agaci")
def urun_agaci(request):
    agac = None
    if request.GET.get("urun"):
        form = UrunAgaciForm(request.GET)
        if form.is_valid():
            try:
                sonuc = uretim_servis.ihtiyac_hesapla(
                    [(form.cleaned_data["urun"], form.cleaned_data["miktar"])])
                agac = sonuc["agac"][0]
            except uretim_servis.UretimHatasi as e:
                messages.error(request, str(e))
    else:
        form = UrunAgaciForm()
    return render(request, "core/urun_agaci.html", {
        "form": form, "agac": agac, "acik_seviye": URUN_AGACI_ACIK_SEVIYE,
        "kokler": None if agac else uretim_servis.kok_operasyonlar()})


# --- Üretim Emirleri (üst-düzey tetikleyici) ---
@ekran_gerekli("uretim_emirleri")
def uretim_emirleri(request):
    ara = (request.GET.get("ara") or "").strip()
    qs = (UretimEmri.objects.filter(silindi=False)
         .select_related("depo")
         .prefetch_related(Prefetch(
             "kalemler",
             queryset=UretimEmriKalemi.objects.filter(silindi=False).select_related("hedef_urun")))
         .order_by("-yil", "-sira"))
    if ara:
        buyuk = buyuk_harf_tr(ara)
        qs = qs.filter(
            Q(no__icontains=ara) | Q(kalemler__hedef_urun__kod__icontains=ara)
            | Q(kalemler__hedef_urun__ad__contains=buyuk) | Q(depo__kod__icontains=ara)
            | Q(depo__ad__contains=buyuk)).distinct()
    emirler = []
    for e in qs:
        kalemler = list(e.kalemler.all())
        if kalemler:
            ilk = kalemler[0].hedef_urun
            kalem_ozet = f"{ilk.kod} {ilk.ad}"
            if len(kalemler) > 1:
                kalem_ozet += f" +{len(kalemler) - 1} kalem daha"
        else:
            kalem_ozet = "—"
        emirler.append({"e": e, "kalem_ozet": kalem_ozet,
                        "ilerleme": uretim_servis.uretim_emri_ilerleme(e)})
    return render(request, "core/uretim_emirleri.html", {"emirler": emirler, "ara": ara})


@ekran_gerekli("uretim_emirleri")
def uretim_emri_ekle(request):
    if request.method == "POST":
        bform = UretimEmriBaslikForm(request.POST)
        formset = UretimEmriKalemSatirFormSet(request.POST, prefix="satir")
        if bform.is_valid() and formset.is_valid():
            kalemler = [
                {"hedef_urun_id": f.cleaned_data["hedef_urun"].pk,
                 "hedef_miktar": f.cleaned_data["hedef_miktar"]}
                for f in formset if f.dolu_mu()
            ]
            cd = bform.cleaned_data
            try:
                emir = uretim_servis.uretim_emri_olustur(
                    kalemler=kalemler, depo_id=cd["depo"].pk, tarih=cd["tarih"],
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(
                    request, f"Üretim emri açıldı: {emir.no} — zincirdeki tüm istasyonlarda "
                             f"taslak operasyon kaydı oluşturuldu.")
                return redirect("core:uretim_emri_detay", pk=emir.pk)
            except uretim_servis.UretimHatasi as e:
                bform.add_error(None, str(e))
    else:
        bform = UretimEmriBaslikForm()
        formset = UretimEmriKalemSatirFormSet(prefix="satir")
    return render(request, "core/uretim_emri_form.html", {"bform": bform, "formset": formset})


@ekran_gerekli("uretim_emirleri")
def uretim_emri_detay(request, pk):
    emir = get_object_or_404(
        UretimEmri.objects.select_related("depo", "kaynak_siparis", "kaynak_siparis__cari"),
        pk=pk, silindi=False)
    kalemler = emir.kalemler.filter(silindi=False).select_related("hedef_urun")
    kayitlar = (emir.operasyon_kayitlari.filter(silindi=False)
               .select_related("operasyon__istasyon", "operasyon__cikti")
               .order_by("operasyon__istasyon__kod", "pk"))
    return render(request, "core/uretim_emri_detay.html", {
        "emir": emir, "kalemler": kalemler, "kayitlar": kayitlar,
        "ilerleme": uretim_servis.uretim_emri_ilerleme(emir)})


@ekran_gerekli("uretim_emirleri")
def uretim_emri_sil_gorunum(request, pk):
    emir = get_object_or_404(UretimEmri, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            uretim_servis.uretim_emri_sil(emir, kullanici=request.user)
        except uretim_servis.UretimHatasi as e:
            messages.error(request, str(e))
            return redirect("core:uretim_emri_detay", pk=emir.pk)
        messages.success(request, "Üretim emri ve bağlı taslak operasyon kayıtları kalıcı olarak silindi.")
        return redirect("core:uretim_emirleri")
    return redirect("core:uretim_emri_detay", pk=emir.pk)


# --- Operasyon Kayıtları ---
@ekran_gerekli("operasyon_kayitlari")
def operasyon_kayitlari(request):
    kayitlar = (OperasyonKaydi.objects.filter(silindi=False)
               .select_related("operasyon__istasyon", "operasyon__cikti", "depo")
               .order_by("-yil", "-sira"))
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    return render(request, "core/operasyon_kayitlari.html", {"kayitlar": sayfa})


@ekran_gerekli("operasyon_kayitlari")
def operasyon_kaydi_ekle(request):
    if request.method == "POST":
        form = OperasyonKaydiForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                kayit = uretim_servis.operasyon_kaydi_olustur(
                    operasyon_id=cd["operasyon"].pk, depo_id=cd["depo"].pk, tarih=cd["tarih"],
                    hedef_cikti_miktari=cd["hedef_cikti_miktari"],
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, f"Operasyon kaydı oluşturuldu: {kayit.no}")
                return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)
            except uretim_servis.UretimHatasi as e:
                form.add_error(None, str(e))
    else:
        form = OperasyonKaydiForm()
    return render(request, "core/operasyon_kaydi_form.html", {"form": form})


@ekran_gerekli("operasyon_kayitlari")
def operasyon_kaydi_detay(request, pk):
    kayit = get_object_or_404(OperasyonKaydi, pk=pk, silindi=False)
    satirlar = list(uretim_servis.kaydi_girdi_satirlari(kayit))
    if request.method == "POST" and kayit.durum == OperasyonKaydi.Durum.TASLAK:
        formset = OperasyonKaydiGirdiDuzeltFormSet(request.POST, prefix="gs")
        if formset.is_valid():
            satir_map = {s.pk: s for s in satirlar}
            hata = None
            for f in formset:
                satir = satir_map.get(f.cleaned_data["satir_id"])
                if satir is None:
                    continue
                try:
                    uretim_servis.operasyon_kaydi_girdi_guncelle(
                        satir, gerceklesen_miktar=f.cleaned_data["gerceklesen_miktar"],
                        kullanici=request.user)
                except uretim_servis.UretimHatasi as e:
                    hata = str(e)
                    break
            if hata:
                messages.error(request, hata)
            else:
                messages.success(request, "Miktarlar güncellendi.")
                return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)
    else:
        formset = OperasyonKaydiGirdiDuzeltFormSet(initial=[
            {"satir_id": s.pk, "gerceklesen_miktar": s.gerceklesen_miktar}
            for s in satirlar], prefix="gs")
    maliyetler = [None] * len(satirlar)
    cikti_katmani = None
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
        from core.models import StokHareket
        for i, satir in enumerate(satirlar):
            hareket = satir.stok_hareketleri.filter(
                silindi=False, kaynak=StokHareket.Kaynak.URETIM,
                tur=StokHareket.Tur.CIKIS).first()
            if hareket is not None:
                maliyetler[i] = stok_maliyet.hareket_maliyet_durumu(hareket)
        cikti_hareketleri = list(StokHareket.objects.filter(
            operasyon_kaydi=kayit, tur=StokHareket.Tur.GIRIS, silindi=False).select_related("stok").order_by("pk"))
        cikti_katmani = next((h for h in cikti_hareketleri if h.stok_id == kayit.operasyon.cikti_id), None)
        saklanan = {c.stok_id: c for c in kayit.ciktilar.filter(silindi=False)}          # onay anında saklanan boy / pay oranı
        ciktilar = [{"hareket": h, "ana": h.stok_id == kayit.operasyon.cikti_id,
                     "boy_mm": saklanan[h.stok_id].boy_mm if h.stok_id in saklanan else None,
                     "pay": saklanan[h.stok_id].pay_orani * 100 if h.stok_id in saklanan else None}
                    for h in cikti_hareketleri]
    else:
        ciktilar = []
    yan_tanimlar = list(uretim_servis.operasyon_yan_ciktilari(kayit.operasyon))
    return render(request, "core/operasyon_kaydi_detay.html",
                  {"kayit": kayit, "satirlar": list(zip(satirlar, formset, maliyetler)),
                   "formset": formset, "cikti_katmani": cikti_katmani, "ciktilar": ciktilar, "yan_tanimlar": yan_tanimlar,
                   "yonetici": yonetici_mi(request.user)})


@ekran_gerekli("operasyon_kayitlari")
def operasyon_kaydi_onayla(request, pk):
    kayit = get_object_or_404(OperasyonKaydi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            uretim_servis.operasyon_kaydi_onayla(kayit, kullanici=request.user)
            messages.success(request, f"{kayit.no} onaylandı; stok hareketleri oluşturuldu.")
        except uretim_servis.UretimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)


@yonetici_gerekli
def operasyon_kaydi_geri_al_sil(request, pk):
    """YALNIZ yönetici: ONAYLI kaydın tüm stok hareketlerini (girdi çıkışları + ana/yan çıktı girişleri) ve maliyet aktarım fişini geri alıp
    kaydı siler. Çıktı başka yerde tüketildiyse servisin hata mesajı gösterilir, hiçbir şey değişmez."""
    kayit = get_object_or_404(OperasyonKaydi, pk=pk, silindi=False)
    if request.method != "POST":
        return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)
    if kayit.durum != OperasyonKaydi.Durum.ONAYLI:
        messages.error(request, "Yalnız onaylı kayıt geri alınıp silinebilir (taslak için 'İptal Et').")
        return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)
    try:
        uretim_servis.operasyon_kaydi_sil(kayit, kullanici=request.user, onayli_geri_al=True)
    except uretim_servis.UretimHatasi as e:
        messages.error(request, str(e))
        return redirect("core:operasyon_kaydi_detay", pk=kayit.pk)
    messages.success(request, f"{kayit.no}: stok hareketleri ve maliyet fişi geri alındı, kayıt silindi.")
    return redirect("core:operasyon_kayitlari")


@ekran_gerekli("operasyon_kayitlari")
def operasyon_kaydi_sil(request, pk):
    kayit = get_object_or_404(OperasyonKaydi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            uretim_servis.operasyon_kaydi_sil(kayit, kullanici=request.user)
            messages.success(request, "Operasyon kaydı iptal edildi.")
        except uretim_servis.UretimHatasi as e:
            messages.error(request, str(e))
    return redirect("core:operasyon_kayitlari")


# === FATURALAR — Alış/Satış faturası (otomatik yevmiye) ===
FaturaSatirFormSet = formset_factory(FaturaSatirForm, extra=0, min_num=1, validate_min=True)


def _fatura_yon_kod(yon):
    return "alis_faturalari" if yon == FaturaTipi.Yon.ALIS else "satis_faturalari"


def _fatura_liste_url(yon):
    return "core:" + _fatura_yon_kod(yon)


def _fatura_ekle_url(yon):
    return ("core:alis_fatura_ekle" if yon == FaturaTipi.Yon.ALIS
            else "core:satis_fatura_ekle")


def _stok_kdv_tevkifat(yon=None):
    _stoklar = list(Stok.objects.filter(silindi=False).select_related("kdv", "tevkifat"))
    stok_kdv = {str(s.pk): float(s.kdv.oran) if s.kdv_id else 0 for s in _stoklar}
    # stok_id -> tevkifat_id: satır formundaki tevkifat seçicisini stok seçilince ön-doldurmak
    # için (JS); kullanıcı sonra değiştirebilir — oranın kendisi artık `tevkifat_oran`'dan
    # (bkz. _fatura_gider_baglami) seçili tevkifat_id'ye göre okunuyor, stok_id'ye göre değil.
    stok_tevkifat = {str(s.pk): s.tevkifat_id for s in _stoklar if s.tevkifat_id}
    # Yalnız ALIŞ yönünde: stok seçilince alan altında gösterilecek Tedarikçi Ürün Adı
    # (bkz. _stok_meta'daki aynı ayrım, fatura_ekle.html'nin tedarikci-etiket JS'i).
    stok_tedarikci = ({str(s.pk): s.tedarikci_adi for s in _stoklar if s.tedarikci_adi}
                       if yon == "ALIS" else {})
    return stok_kdv, stok_tevkifat, stok_tedarikci


def _tevkifat_initial(satir):
    """Düzenleme formunun tevkifat seçicisi için kayıtlı 3 durumdan hangisini
    göstereceğini çözer. FaturaSatir yalnız `tevkifat_id`'yi (nullable FK) saklar; "boş
    bırakıldı" (stok varsayılanını kullan) ile "açıkça Yok seçildi" ayrımı KAYDEDİLMEZ
    ama şuradan türetilebilir: stoklu bir kalemde satır.tevkifat_id boşken stok kartının
    KENDİ tevkifatı doluysa, bu ancak kullanıcının o anda tevkifatı AÇIKÇA temizlemiş
    olmasıyla açıklanır (boş bırakılsaydı satır.tevkifat_id de stoktan dolardı) — bkz.
    core.services.fatura._tevkifat_coz / core.forms.TEVKIFAT_YOK."""
    if satir.tevkifat_id:
        return str(satir.tevkifat_id)
    if satir.stok_id and satir.stok.tevkifat_id:
        return TEVKIFAT_YOK
    return ""


def _fatura_satir_girdileri(formset):
    """Fatura kalem formlarından servis girdisi: stok kalemi ya da (gider faturasında) gider
    hesabı + satırda seçilen KDV oranı. Tip × kalem türü tutarlılığı serviste doğrulanır.
    Tevkifat seçicisi üç değerli (bkz. core.forms.TEVKIFAT_YOK): "" (boş) -> stoklu kalemde
    stok kartının güncel tevkifatı kullanılır (`tevkifat_id`/`tevkifat_yok` ikisi de boş/
    False); TEVKIFAT_YOK -> `tevkifat_yok=True` (stok kartında tanımlı olsa bile
    uygulanmaz); <pk> -> `tevkifat_id` o orana işaret eder."""
    girdiler = []
    for f in formset:
        if not f.dolu_mu():
            continue
        cd = f.cleaned_data
        tev_ham = (cd.get("tevkifat") or "").strip()
        tevkifat_id = None
        tevkifat_yok = False
        if tev_ham == TEVKIFAT_YOK:
            tevkifat_yok = True
        elif tev_ham:
            tevkifat_id = int(tev_ham)
        girdiler.append({
            "stok_id": cd["stok"].pk if cd.get("stok") else None,
            "hesap_id": cd["hesap"].pk if cd.get("hesap") else None,
            "demirbas_id": cd["demirbas"].pk if cd.get("demirbas") else None,
            "kdv_id": cd["kdv"].pk if cd.get("kdv") else None,
            "tevkifat_id": tevkifat_id, "tevkifat_yok": tevkifat_yok,
            "yatirim_projesi_id": cd["yatirim_projesi"].pk if cd.get("yatirim_projesi") else None,
            "varlik_adi": (cd.get("varlik_adi") or "").strip(),
            "donemsel": bool(cd.get("donemsel")),
            "donem_grup": cd["donem_grup"].hesap_kodu if cd.get("donem_grup") else None,
            "donem_hesap_id": (cd.get("donem_hesap") or "").strip() or None,
            "donem_baslangic": cd.get("donem_baslangic"), "donem_bitis": cd.get("donem_bitis"),
            "donem_aciklama": (cd.get("donem_aciklama") or "").strip(),
            "miktar": cd["miktar"], "birim_fiyat": cd["birim_fiyat"]})
    return girdiler


def _fatura_gider_baglami(fform):
    """fatura_ekle.html JS'i için: hangi fatura tipleri GİDER faturası (depo gizlenir, kalem
    = gider hesabı) + KDV oran haritası (gider kaleminin KDV önizlemesi) + hangi gider hesabı
    seçenekleri DURAN VARLIK hesabı (proje alanı yalnız bunlarda gösterilir)."""
    return {"tip_gider": {str(t.pk): bool(t.gider) for t in fform.fields["tip"].queryset},
            "tip_stopajli": {str(t.pk): bool(t.stopajli) for t in fform.fields["tip"].queryset},
            "duran_varlik_hesap": {str(h.pk): True for h in (
                hp.duran_varlik_hesaplari() | duran_hesap_servis.grup_hesaplari(duran_hesap_servis.KART_AILELERI))},
            "demirbas_bilgi": {str(d.pk): {"maliyet": float(d.maliyet), "amortisman": float(d.birikmis_amortisman)}
                               for d in DuranVarlik.objects.filter(silindi=False)},
            "kdv_oran": {str(k.pk): float(k.oran)
                         for k in KdvOrani.objects.filter(silindi=False)},
            "tevkifat_oran": {str(t.pk): float(t.pay) / float(t.payda)
                              for t in TevkifatOrani.objects.filter(silindi=False) if t.payda}}


def _fatura_listesi(request, yon, baslik):
    # tip__yon DEĞİL — İrsaliye'den otomatik açılan taslağın tipi henüz boş olabilir
    # (INNER JOIN tip=None satırları dışlar); Fatura'nın kendi yon alanı bunun için var.
    ara = (request.GET.get("ara") or "").strip()
    durum = request.GET.get("durum") or "hepsi"
    tip_id = (request.GET.get("tip") or "").strip()
    bas = (request.GET.get("bas") or "").strip()
    bit = (request.GET.get("bit") or "").strip()

    faturalar = (fatura_servis.aktif_faturalar().filter(yon=yon)
                 .prefetch_related("satirlar__kdv"))
    if durum in (Fatura.Durum.TASLAK, Fatura.Durum.ONAYLI):
        faturalar = faturalar.filter(durum=durum)
    if tip_id.isdigit():
        faturalar = faturalar.filter(tip_id=tip_id)
    if bas:
        try:
            faturalar = faturalar.filter(tarih__gte=datetime.date.fromisoformat(bas))
        except ValueError:
            bas = ""
    if bit:
        try:
            faturalar = faturalar.filter(tarih__lte=datetime.date.fromisoformat(bit))
        except ValueError:
            bit = ""
    if ara:
        faturalar = faturalar.filter(
            Q(cari__unvan__contains=buyuk_harf_tr(ara)) | Q(fatura_no__icontains=ara))

    sayfa = Paginator(faturalar, 50).get_page(request.GET.get("sayfa"))

    params = {}
    if ara:
        params["ara"] = ara
    if durum != "hepsi":
        params["durum"] = durum
    if tip_id:
        params["tip"] = tip_id
    if bas:
        params["bas"] = bas
    if bit:
        params["bit"] = bit
    sorgu = urlencode(params) + "&" if params else ""

    return render(request, "core/fatura_listesi.html", {
        "faturalar": sayfa, "baslik": baslik, "ekle_url": _fatura_ekle_url(yon),
        "liste_url": _fatura_liste_url(yon),
        "ara": ara, "durum": durum, "secili_tip": tip_id, "bas": bas, "bit": bit,
        "tipler": FaturaTipi.objects.filter(yon=yon, silindi=False).order_by("sira", "ad"),
        "sorgu": sorgu,
        "filtre_aktif": bool(ara or durum != "hepsi" or tip_id or bas or bit)})


@ekran_gerekli("alis_faturalari")
def alis_faturalari(request):
    return _fatura_listesi(request, FaturaTipi.Yon.ALIS, "Alış Faturaları")


@ekran_gerekli("satis_faturalari")
def satis_faturalari(request):
    return _fatura_listesi(request, FaturaTipi.Yon.SATIS, "Satış Faturaları")


def _fatura_hatasi_ekle(fform, hata):
    """Servis hatasını forma ekler; mükerrer faturada mevcut kayda LİNK verir."""
    if isinstance(hata, fatura_servis.MukerrerFaturaHatasi):
        f = hata.fatura
        fform.add_error(None, format_html(
            '{} — <a href="{}">faturayı aç</a>', str(hata),
            reverse("core:fatura_detay", args=[f.pk])))
    else:
        fform.add_error(None, str(hata))


def _fatura_ekle(request, yon, baslik):
    if request.method == "POST":
        fform = FaturaForm(request.POST, yon=yon)
        formset = FaturaSatirFormSet(request.POST, form_kwargs={"yon": yon})
        if fform.is_valid() and formset.is_valid():
            satirlar = _fatura_satir_girdileri(formset)
            try:
                # fatura_olustur = taslak + hemen onay (tip formda zaten seçili) — günlük
                # fatura girişi tek tıkla kalır, tam atomik (eskisi gibi ya hepsi ya hiçbiri).
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
                mesaj = f"Fatura kaydedildi; fiş {fatura.fis.yil}/{fatura.fis.fis_no} oluştu."
                if fform.cleaned_data.get("depo") is None and not fform.cleaned_data["tip"].gider:
                    mesaj += " (Depo seçilmedi; stok hareketi oluşmadı.)"
                messages.success(request, mesaj)
                return redirect("core:fatura_detay", pk=fatura.pk)
            except fatura_servis.FaturaHatasi as e:
                _fatura_hatasi_ekle(fform, e)
    else:
        fform = FaturaForm(yon=yon)
        formset = FaturaSatirFormSet(form_kwargs={"yon": yon})
    stok_kdv, stok_tevkifat, stok_tedarikci = _stok_kdv_tevkifat(yon)
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat, "stok_tedarikci": stok_tedarikci,
                   **_fatura_gider_baglami(fform),
                   "baslik": baslik, "yon": yon, "iptal_url": reverse(_fatura_liste_url(yon))})


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
        formset = FaturaSatirDuzenleFormSet(request.POST, form_kwargs={"yon": yon, "fatura_pk": fatura.pk})
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
                if fatura.durum == Fatura.Durum.TASLAK:
                    mesaj = "Taslak fatura güncellendi."
                else:
                    mesaj = f"Fatura güncellendi; fiş {fatura.fis.yil}/{fatura.fis.fis_no} yenilendi."
                    if fform.cleaned_data.get("depo") is None and not fform.cleaned_data["tip"].gider:
                        mesaj += " (Depo seçilmedi; stok hareketi oluşmadı.)"
                messages.success(request, mesaj)
                return redirect("core:fatura_detay", pk=fatura.pk)
            except fatura_servis.FaturaHatasi as e:
                _fatura_hatasi_ekle(fform, e)
    else:
        fform = FaturaForm(yon=yon, initial={
            "tip": fatura.tip_id, "cari": fatura.cari_id, "tarih": fatura.tarih,
            "fatura_no": fatura.fatura_no, "para_birimi": fatura.para_birimi,
            # TASLAK'ta fatura.kur hep "1" yer tutucusudur (henüz gerçek hesaplanmadı) —
            # forma taşınırsa yanıltıcı olur; JS zaten taze bir önizleme dolduracak.
            "kur": (fatura.kur if fatura.durum == Fatura.Durum.ONAYLI else fatura.taslak_kur),
            "depo": fatura.depo_id, "aciklama": fatura.aciklama,
            "vade_tarihi": fatura.vade_tarihi,
            "sahsi_alis": fatura.sahsi_alis, "sahsi_ortak": fatura.sahsi_ortak_id,
            "gv_stopaj_orani": fatura.gv_stopaj_orani if fatura.gv_stopaj_orani is not None
            else Decimal("20")})
        ilk = [{"stok": s.stok_id,
                "hesap": s.donem_gider_id if s.donem_baslangic else (None if s.demirbas_id else s.hesap_id), "kdv": s.kdv_id,
                "donemsel": bool(s.donem_baslangic), "donem_hesap": s.hesap_id if s.donem_baslangic else "",
                "donem_baslangic": s.donem_baslangic, "donem_bitis": s.donem_bitis, "donem_aciklama": s.donem_aciklama,
                "tur": s.satir_tipi, "demirbas": s.demirbas_id,
                "yatirim_projesi": s.yatirim_projesi_id,
                "tevkifat": _tevkifat_initial(s), "miktar": s.miktar, "birim_fiyat": s.birim_fiyat}
               for s in fatura.satirlar.filter(silindi=False)
               .select_related("stok__tevkifat", "hesap")]
        formset = FaturaSatirDuzenleFormSet(initial=ilk, form_kwargs={"yon": yon, "fatura_pk": fatura.pk})
    stok_kdv, stok_tevkifat, stok_tedarikci = _stok_kdv_tevkifat(yon)
    return render(request, "core/fatura_ekle.html",
                  {"fform": fform, "formset": formset, "stok_kdv": stok_kdv,
                   "stok_tevkifat": stok_tevkifat, "stok_tedarikci": stok_tedarikci,
                   **_fatura_gider_baglami(fform),
                   "baslik": "Fatura Düzenle", "yon": yon,
                   "iptal_url": reverse("core:fatura_detay", args=[fatura.pk])})


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_detay(request, pk):
    fatura = get_object_or_404(
        Fatura.objects.select_related("tip", "cari", "fis"), pk=pk)
    satirlar = fatura.satirlar.filter(silindi=False).select_related(
        "stok", "hesap", "kdv", "yatirim_projesi", "demirbas").prefetch_related(
        Prefetch("duran_varliklar", queryset=DuranVarlik.objects.filter(silindi=False)))
    kart_acilabilir_hesap = set(
        hp.duran_varlik_karti_hesaplari().values_list("pk", flat=True))
    return render(request, "core/fatura_detay.html",
                  {"fatura": fatura, "satirlar": satirlar,
                   "donemsel_tablolar": donemsel_servis.fatura_tablolari(fatura),
                   "irsaliye_farklari": fatura_servis.irsaliye_miktar_farklari(fatura),
                   "liste_url": _fatura_liste_url(fatura.yon),
                   "ekler": fatura_ek_servis.ek_listele(fatura),
                   "kart_acilabilir_hesap": kart_acilabilir_hesap})


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_ek_ekle(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    if request.method == "POST":
        dosyalar = request.FILES.getlist("dosyalar")
        if not dosyalar:
            messages.error(request, "Dosya seçilmedi.")
        eklenen = 0
        for d in dosyalar:
            try:
                fatura_ek_servis.ek_ekle(fatura, dosya=d, kullanici=request.user)
                eklenen += 1
            except fatura_ek_servis.FaturaEkHatasi as e:
                messages.error(request, str(e))
        if eklenen:
            messages.success(request, f"{eklenen} dosya eklendi.")
    return redirect("core:fatura_detay", pk=fatura.pk)


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_ek_sil(request, pk):
    ek = get_object_or_404(FaturaEk, pk=pk, silindi=False, fatura__silindi=False)
    if request.method == "POST":
        fatura_ek_servis.ek_sil(ek, kullanici=request.user)
        messages.success(request, "Dosya silindi.")
    return redirect("core:fatura_detay", pk=ek.fatura_id)


@never_cache
@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_ek_indir(request, pk):
    """Fatura ekini (özel depoda) yetkili görünümden sunar — /media/ üzerinden DEĞİL."""
    ek = get_object_or_404(FaturaEk, pk=pk, silindi=False, fatura__silindi=False)
    return _ozel_dosya_yanit(ek.dosya, ek.orijinal_ad)


# === DURAN VARLIK — Yatırım Projeleri (FAZ 1) ===
def _proje_satir_qs(proje):
    return (proje.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
            .select_related("fatura", "fatura__cari", "hesap")
            .order_by("fatura__tarih", "fatura_id"))


_YP_KOLONLAR = (("kod", "Kod", False), ("ad", "Ad", False), ("hesap", "Hesap", False), ("durum", "Durum", False),
                ("ilk", "İlk hareket", False), ("son", "Son hareket", False), ("hareket", "Hareket", True), ("toplam", "Toplam (KDV hariç)", True))


@ekran_gerekli("yatirim_projeleri")
def yatirim_projeleri(request):
    from urllib.parse import urlencode
    g = request.GET

    def _tarih(anahtar):
        try:
            return datetime.date.fromisoformat(g.get(anahtar) or "")
        except ValueError:
            return None

    sonuc = yp_servis.proje_listesi(
        arama=g.get("q", ""), grup=g.get("grup", ""), baslangic=_tarih("bas"), bitis=_tarih("bit"),
        durum=g.get("durum", "DEVAM"), sirala=g.get("sirala", "son"), yon=g.get("yon", "azalan"), bugun=timezone.localdate())
    temel = {k: g.get(k) for k in ("q", "grup", "bas", "bit") if g.get(k)}

    def url(**ek):
        d = {**temel, "durum": sonuc["durum"], "sirala": sonuc["sirala"], "yon": sonuc["yon"], **ek}
        return "?" + urlencode(d)

    sekmeler = [{"kod": k, "ad": ad, "sayi": sonuc["sayilar"][k], "aktif": sonuc["durum"] == k, "url": url(durum=k)}
                for k, ad in yp_servis.DURUM_SEKMELERI]
    kolonlar = []
    for anahtar, etiket, sag in _YP_KOLONLAR:
        aktif = sonuc["sirala"] == anahtar
        yeni = ("artan" if sonuc["yon"] == "azalan" else "azalan") if aktif else ("artan" if anahtar in ("kod", "ad", "hesap", "durum") else "azalan")
        kolonlar.append({"etiket": etiket, "sag": sag, "aktif": aktif, "ok": ("▼" if sonuc["yon"] == "azalan" else "▲") if aktif else "",
                         "url": url(sirala=anahtar, yon=yeni)})
    return render(request, "core/yatirim_projeleri.html", {
        **sonuc, "sekmeler": sekmeler, "kolonlar": kolonlar, "gruplar": duran_hesap_servis.grup_hesaplari(("258",)),
        "q": g.get("q", ""), "grup": g.get("grup", ""), "bas": g.get("bas", ""), "bit": g.get("bit", ""),
        "filtre_var": bool(temel), "temizle_url": "?durum=" + sonuc["durum"], "bugun_yil": timezone.localdate().year})


@ekran_gerekli("yatirim_projeleri")
def yatirim_projesi_ekle(request):
    if request.method == "POST":
        try:
            grup = (request.POST.get("grup") or "").strip()
            if not grup and duran_hesap_servis.grup_hesaplari(("258",)).exists():
                raise yp_servis.YatirimProjesiHatasi("Proje grubunu seçin (258.01 … 258.04).")
            p = yp_servis.proje_olustur(
                ad=request.POST.get("ad", ""), aciklama=request.POST.get("aciklama", ""),
                grup_kodu=grup or None, kullanici=request.user)
            messages.success(request, f"Proje eklendi: {p.kod} — {p.ad}")
        except yp_servis.YatirimProjesiHatasi as e:
            messages.error(request, str(e))
    return redirect("core:yatirim_projeleri")


@ekran_gerekli("yatirim_projeleri")
def yatirim_projesi_detay(request, pk):
    from core.services import stok_maliyet
    proje = get_object_or_404(YatirimProjesi, pk=pk, silindi=False)
    parca = yp_servis.proje_parcalari(proje)
    satirlar = list(_proje_satir_qs(proje))
    sarf_hareketleri = [
        {"hareket": h, "tutar_try": stok_maliyet.hareket_maliyet_durumu(h)["tutar_try"]}
        for h in yp_servis.proje_sarf_hareketleri(proje)
    ]
    diger = list(yp_servis.proje_diger_hareketler(proje))
    ekstre = yp_servis.proje_yevmiye_kalemleri(proje)
    yevmiye_satirlari = []
    if ekstre:
        kaynaklar = {f.pk: f.get_kaynak_display() for f in YevmiyeFisi.objects.filter(pk__in={e.fis_pk for e in ekstre.satirlar})}
        yevmiye_satirlari = [{"tarih": e.tarih, "fis_pk": e.fis_pk, "fis_yil": e.fis_yil, "fis_no": e.fis_no, "kaynak": kaynaklar.get(e.fis_pk, ""),
                              "satir_aciklama": e.satir_aciklama, "fis_aciklama": e.fis_aciklama, "borc": e.borc, "alacak": e.alacak,
                              "yur_bakiye": e.yur_bakiye} for e in ekstre.satirlar]
    duran_varliklar_qs = DuranVarlik.objects.filter(
        yatirim_projesi=proje, silindi=False).select_related("hesap")
    bakiye = yp_servis.proje_bakiye_258(proje) if proje.durum == YatirimProjesi.Durum.DEVAM else None
    sekme = request.GET.get("sekme") if request.GET.get("sekme") in ("faturalar", "diger", "yevmiye") else "faturalar"
    sekmeler = [
        {"kod": "faturalar", "ad": "Faturalar", "sayi": len(satirlar)},
        {"kod": "diger", "ad": "Diğer Hareketler", "sayi": len(diger)},
        {"kod": "yevmiye", "ad": "Yevmiye Kalemleri", "sayi": len(ekstre.satirlar) if ekstre else 0},
    ]
    for t in sekmeler:
        t["aktif"] = t["kod"] == sekme
    return render(request, "core/yatirim_projesi_detay.html",
                  {"proje": proje, "satirlar": satirlar, "fatura_toplam": parca["fatura"],
                   "bakiye_258": bakiye, "kapatilabilir": bakiye is not None and bakiye == Decimal("0.00"),
                   "sarf_hareketleri": sarf_hareketleri, "sarf_toplam": parca["sarf"],
                   "diger_hareketler": diger, "diger_toplam": parca["diger"], "ekstre": ekstre,
                   "yevmiye_satirlari": yevmiye_satirlari,
                   "sekme": sekme, "sekmeler": sekmeler,
                   "toplam": parca["toplam"],
                   "yonetici": yonetici_mi(request.user), "duran_varliklar": duran_varliklar_qs})


AktiflestirmeSatirFormSet = formset_factory(
    AktiflestirmeSatirForm, extra=0, min_num=1, validate_min=True)


@ekran_gerekli("yatirim_projeleri")
def yatirim_projesi_aktiflestir(request, pk):
    proje = get_object_or_404(YatirimProjesi, pk=pk, silindi=False)
    if proje.durum != YatirimProjesi.Durum.DEVAM:
        messages.info(request, "Bu proje kapatılmış; aktifleştirmek için önce yeniden açın."
                      if proje.durum == YatirimProjesi.Durum.KAPANDI else "Bu proje zaten aktifleştirilmiş.")
        return redirect("core:yatirim_projesi_detay", pk=proje.pk)
    toplam = yp_servis.proje_toplami(proje)

    if request.method == "POST":
        baslik = AktiflestirmeBaslikForm(request.POST)
        formset = AktiflestirmeSatirFormSet(request.POST)
        if baslik.is_valid() and formset.is_valid():
            satirlar = [
                ({"grup_kodu": f.cleaned_data["hesap"].pk} if duran_hesap_servis.grup_mu(f.cleaned_data["hesap"].pk)
                 else {"hesap_id": f.cleaned_data["hesap"].pk})
                | {"varlik_adi": f.cleaned_data["varlik_adi"], "tutar": f.cleaned_data["tutar"]}
                for f in formset if f.cleaned_data and f.dolu_mu()
            ]
            try:
                yp_servis.proje_aktiflestir(
                    proje, tarih=baslik.cleaned_data["tarih"], satirlar=satirlar,
                    kullanici=request.user)
                messages.success(request, f"{proje.kod} aktifleştirildi.")
                return redirect("core:yatirim_projesi_detay", pk=proje.pk)
            except yp_servis.YatirimProjesiHatasi as e:
                baslik.add_error(None, str(e))
    else:
        baslik = AktiflestirmeBaslikForm(initial={"tarih": timezone.localdate()})
        formset = AktiflestirmeSatirFormSet()
    return render(request, "core/yatirim_projesi_aktiflestir.html", {
        "proje": proje, "toplam": toplam, "baslik": baslik, "formset": formset})


@ekran_gerekli("yatirim_projeleri")
def yatirim_projesi_kapat(request, pk):
    """Projeyi kapat (satıldı / diğer): yalnız 258 bakiyesi 0,00 ise; fiş üretmez, durum 'Kapandı' olur."""
    proje = get_object_or_404(YatirimProjesi, pk=pk, silindi=False)
    if proje.durum != YatirimProjesi.Durum.DEVAM:
        messages.info(request, "Yalnız 'Devam Ediyor' durumundaki proje kapatılabilir.")
        return redirect("core:yatirim_projesi_detay", pk=proje.pk)
    bakiye = yp_servis.proje_bakiye_258(proje)
    if request.method == "POST":
        form = YatirimProjesiKapatForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                yp_servis.proje_kapat(proje, tarih=cd["kapanis_tarihi"], neden=cd["kapanis_nedeni"],
                                      aciklama=cd["kapanis_aciklama"], kullanici=request.user)
                messages.success(request, f"{proje.kod} kapatıldı.")
                return redirect("core:yatirim_projesi_detay", pk=proje.pk)
            except yp_servis.YatirimProjesiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = YatirimProjesiKapatForm()
    return render(request, "core/yatirim_projesi_kapat.html", {
        "proje": proje, "form": form, "bakiye": bakiye, "kapatilabilir": bakiye == Decimal("0.00")})


@ekran_gerekli("yatirim_projeleri")
def yatirim_projesi_yeniden_ac(request, pk):
    """Kapanmış projeyi geri açar (yalnız süper kullanıcı; POST)."""
    proje = get_object_or_404(YatirimProjesi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            yp_servis.proje_yeniden_ac(proje, kullanici=request.user)
            messages.success(request, f"{proje.kod} yeniden açıldı.")
        except yp_servis.YatirimProjesiHatasi as e:
            messages.error(request, str(e))
    return redirect("core:yatirim_projesi_detay", pk=proje.pk)


@yonetici_gerekli
def yatirim_projesi_geri_al(request, pk):
    proje = get_object_or_404(YatirimProjesi, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            yp_servis.proje_geri_al(proje, kullanici=request.user)
            messages.success(request, f"{proje.kod} aktifleştirmesi geri alındı.")
        except yp_servis.YatirimProjesiHatasi as e:
            messages.error(request, str(e))
    return redirect("core:yatirim_projesi_detay", pk=proje.pk)


# === DURAN VARLIK — Duran Varlık Kartları (FAZ 2) ===
@ekran_gerekli("duran_varliklar")
def duran_varliklar(request):
    hesap_id = request.GET.get("hesap") or None
    durum = request.GET.get("durum") or None
    qs = dv_servis.varliklar(hesap_id=hesap_id, durum=durum)
    return render(request, "core/duran_varliklar.html", {
        "varliklar": qs,
        "hesap_ozet": dv_servis.hesap_bazli_toplam(qs if durum == DuranVarlik.Durum.BOLUNDU else qs.exclude(durum=DuranVarlik.Durum.BOLUNDU)),
        "hesaplar": hp.duran_varlik_karti_hesaplari(),
        "durum_secenekleri": DuranVarlik.Durum.choices,
        "hesap_id": hesap_id, "durum": durum,
        "kontrol_raporu": dv_servis.kontrol_raporu(),
    })


@ekran_gerekli("duran_varliklar")
def duran_varlik_ekle(request):
    satir_id = request.GET.get("satir") or request.POST.get("satir")
    satir = (FaturaSatir.objects.filter(pk=satir_id, silindi=False, fatura__silindi=False)
             .select_related("fatura", "hesap").first()) if satir_id else None

    if satir and duran_hesap_servis.varlik_hesabi_mi(satir.hesap_id):
        # Kalem zaten bir kartın hesabında (kalem = kart ya da mevcut karta eklenmiş): yeni kart açılmaz.
        # Hesap kart hesabı BİÇİMİNDE ama henüz kartı yoksa (kartsız yaprak) kart bu hesaba bağlanarak açılır.
        kart = dv_servis.hesaptaki_kart(satir.hesap_id)
        if kart is not None:
            messages.info(request, "Bu kalem zaten bir duran varlık kartının hesabında; yeni kart açılmaz.")
            return redirect("core:duran_varlik_detay", pk=kart.pk)

    adaylar = FaturaSatir.objects.none()
    otomatik_maliyet = None
    if satir:
        adaylar = (FaturaSatir.objects.filter(
                fatura_id=satir.fatura_id, hesap_id=satir.hesap_id,
                silindi=False, fatura__silindi=False)
            .exclude(duran_varliklar__silindi=False)
            .select_related("fatura").order_by("id"))
        secili_adaylar = list(adaylar) or [satir]
        otomatik_maliyet = sum((s.tutar for s in secili_adaylar), Decimal("0.00"))

    if request.method == "POST":
        form = DuranVarlikForm(request.POST, satir_adaylari=adaylar)
        if satir:
            form.fields["maliyet"].required = False   # satır(lar) varsa maliyet otomatik
            del form.fields["karsi_hesap"]            # fatura kalemli kartta karşı hesap/fiş yok
        if form.is_valid():
            try:
                cd = form.cleaned_data
                dv = dv_servis.duran_varlik_olustur(
                    ad=cd["ad"], hesap_id=cd["hesap"].pk,
                    grup_kodu=cd["hesap"].pk if duran_hesap_servis.grup_mu(cd["hesap"].pk) else None,
                    aktiflestirme_tarihi=cd["aktiflestirme_tarihi"], maliyet=cd.get("maliyet"),
                    marka_model=cd["marka_model"], seri_no=cd["seri_no"], notlar=cd["notlar"],
                    fatura_satirlari=list(cd["fatura_satir_ids"]), kullanici=request.user,
                    karsi_hesap_kodu=cd["karsi_hesap"].hesap_kodu if cd.get("karsi_hesap") else None)
                messages.success(request, f"Duran varlık kartı eklendi: {dv.demirbas_kodu} — {dv.ad}")
                return redirect("core:duran_varlik_detay", pk=dv.pk)
            except dv_servis.DuranVarlikHatasi as e:
                form.add_error(None, str(e))
    else:
        initial = {}
        if satir:
            initial = {"hesap": satir.hesap_id, "maliyet": otomatik_maliyet,
                       "aktiflestirme_tarihi": satir.fatura.tarih,
                       "fatura_satir_ids": [s.pk for s in (list(adaylar) or [satir])]}
        form = DuranVarlikForm(initial=initial, satir_adaylari=adaylar)
        if satir:
            form.fields["maliyet"].required = False
            del form.fields["karsi_hesap"]
    return render(request, "core/duran_varlik_form.html",
                  {"form": form, "satir": satir, "otomatik_maliyet": otomatik_maliyet})


@ekran_gerekli("duran_varliklar")
def duran_varlik_detay(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    satirlar = (varlik.fatura_satirlari.filter(silindi=False, fatura__silindi=False)
                .select_related("fatura", "fatura__cari").order_by("fatura__tarih", "fatura_id"))
    baglanti_toplami = dv_servis.baglanti_toplami(varlik)
    return render(request, "core/duran_varlik_detay.html", {
        "varlik": varlik, "satirlar": satirlar,
        "baglanabilir": dv_servis.baglanabilir_satirlar(varlik),
        "baglanti_toplami": baglanti_toplami,
        "toplam_farkli": satirlar.exists() and baglanti_toplami != varlik.maliyet,
        "silinebilir": dv_servis.silinebilir_mi(varlik),
        "bolunebilir": dv_servis.bolunebilir_mi(varlik),
        "yeni_kartlar": list(dv_servis.yeni_kartlar(varlik)) if varlik.durum == DuranVarlik.Durum.BOLUNDU else [],
        "geri_neden": dv_servis.bolme_geri_alinabilir_mi(varlik) if varlik.durum == DuranVarlik.Durum.BOLUNDU else "",
        "kaynak_satirlari": dv_servis.kaynak_satirlari(varlik) if varlik.bolunen_kart_id else [],
    })


@ekran_gerekli("duran_varliklar")
def duran_varlik_duzenle(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    maliyet_otomatik = varlik.fatura_satirlari.exists()
    karsi_uygun = varlik.kaynak == DuranVarlik.Kaynak.ACILIS and not maliyet_otomatik
    if request.method == "POST":
        form = DuranVarlikDuzenleForm(request.POST)
        if not karsi_uygun:
            del form.fields["karsi_hesap"]
        if maliyet_otomatik:
            form.fields["maliyet"].required = False
        if form.is_valid():
            try:
                cd = form.cleaned_data
                dv_servis.duran_varlik_guncelle(
                    varlik, ad=cd["ad"], maliyet=cd.get("maliyet"), marka_model=cd["marka_model"],
                    seri_no=cd["seri_no"], notlar=cd["notlar"],
                    birikmis_amortisman=cd.get("birikmis_amortisman"), kullanici=request.user,
                    karsi_hesap_guncelle=karsi_uygun,
                    karsi_hesap_kodu=cd["karsi_hesap"].hesap_kodu if cd.get("karsi_hesap") else None)
                messages.success(request, f"{varlik.demirbas_kodu} güncellendi.")
                return redirect("core:duran_varlik_detay", pk=pk)
            except dv_servis.DuranVarlikHatasi as e:
                form.add_error(None, str(e))
    else:
        form = DuranVarlikDuzenleForm(initial={
            "ad": varlik.ad, "maliyet": varlik.maliyet, "marka_model": varlik.marka_model,
            "birikmis_amortisman": varlik.birikmis_amortisman, "karsi_hesap": varlik.karsi_hesap_id,
            "seri_no": varlik.seri_no, "notlar": varlik.notlar})
        if not karsi_uygun:
            del form.fields["karsi_hesap"]
        if maliyet_otomatik:
            form.fields["maliyet"].required = False
    return render(request, "core/duran_varlik_duzenle.html",
                  {"form": form, "varlik": varlik, "maliyet_otomatik": maliyet_otomatik})


@ekran_gerekli("duran_varliklar")
def duran_varlik_sil(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            dv_servis.varlik_sil(varlik, kullanici=request.user)
            messages.success(request, f"{varlik.demirbas_kodu} silindi.")
            return redirect("core:duran_varliklar")
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    return redirect("core:duran_varlik_detay", pk=pk)


@ekran_gerekli("duran_varliklar")
def duran_varlik_durum_degistir(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if request.method == "POST":
        yeni = (DuranVarlik.Durum.PASIF if varlik.durum == DuranVarlik.Durum.AKTIF
                else DuranVarlik.Durum.AKTIF)
        try:
            dv_servis.durum_degistir(varlik, durum=yeni, kullanici=request.user)
            messages.success(request, f"{varlik.demirbas_kodu} {varlik.get_durum_display().lower()} yapıldı.")
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    return redirect("core:duran_varlik_detay", pk=pk)


@ekran_gerekli("duran_varliklar")
def duran_varlik_kalem_bagla(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            dv_servis.satir_bagla(varlik, request.POST.get("satir_id"), kullanici=request.user)
            messages.success(request, "Fatura kalemi karta bağlandı.")
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    return redirect("core:duran_varlik_detay", pk=pk)


@ekran_gerekli("duran_varliklar")
def duran_varlik_kalem_cikar(request, pk, satir_id):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            dv_servis.satir_cikar(varlik, satir_id, kullanici=request.user)
            messages.success(request, "Fatura kalemi karttan çıkarıldı.")
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    return redirect("core:duran_varlik_detay", pk=pk)


@ekran_gerekli("duran_varliklar")
def duran_varlik_bol(request, pk):
    """Kartı yeni kartlara böl (kısmi satış için): yeni kart sayısı + her kartın adı/maliyeti (varsayılan eşit; kuruş son karta). Muhasebe fişi yazılmaz."""
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if not dv_servis.bolunebilir_mi(varlik):
        messages.error(request, "Yalnız aktif, satılmamış ve bölünmemiş kart bölünebilir.")
        return redirect("core:duran_varlik_detay", pk=pk)
    try:
        adet = min(max(int(request.POST.get("adet") or request.GET.get("adet") or 2), 2), 50)
    except ValueError:
        adet = 2
    satirlar = None
    if request.method == "POST" and request.POST.get("islem") == "kaydet":
        satirlar = [{"ad": request.POST.get(f"ad_{i}", ""), "maliyet": request.POST.get(f"maliyet_{i}", "")} for i in range(1, adet + 1)]
        try:
            yeniler = dv_servis.bol(varlik, satirlar, kullanici=request.user)
            messages.success(request, f"{varlik.demirbas_kodu} bölündü: " + ", ".join(y.demirbas_kodu for y in yeniler) + ". Muhasebe fişi yazılmadı; hesap bakiyesi değişmedi.")
            return redirect("core:duran_varlik_detay", pk=pk)
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    if satirlar is None:
        satirlar = [{"ad": o["ad"], "maliyet": format_tr(o["maliyet"])} for o in dv_servis.bolme_onerisi(varlik, adet)]
    for i, s in enumerate(satirlar, 1):
        s["no"] = i
    return render(request, "core/duran_varlik_bol.html", {"varlik": varlik, "adet": adet, "satirlar": satirlar})


@ekran_gerekli("duran_varliklar")
def duran_varlik_bolmeyi_geri_al(request, pk):
    varlik = get_object_or_404(DuranVarlik, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            dv_servis.bolmeyi_geri_al(varlik, kullanici=request.user)
            messages.success(request, f"{varlik.demirbas_kodu} bölmesi geri alındı; kart yeniden aktif.")
        except dv_servis.DuranVarlikHatasi as e:
            messages.error(request, str(e))
    return redirect("core:duran_varlik_detay", pk=pk)


@ekran_gerekli_herhangi("alis_faturalari", "satis_faturalari")
def fatura_sil_gorunum(request, pk):
    fatura = get_object_or_404(Fatura, pk=pk, silindi=False)
    yon = fatura.yon
    if request.method == "POST":
        try:
            fatura_servis.fatura_sil(fatura, kullanici=request.user)
        except fatura_servis.FaturaHatasi as e:
            messages.error(request, str(e))
            return redirect("core:fatura_detay", pk=fatura.pk)
        messages.success(request, "Fatura ve bağlı yevmiye kaydı kalıcı olarak silindi.")
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


# === İNSAN KAYNAKLARI > Yemek Takibi ====================================================
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


# === İNSAN KAYNAKLARI ===
_PERSONEL_ALANLARI = (
    "ad", "soyad", "tc_kimlik_no", "dogum_tarihi", "kan_grubu", "telefon", "eposta", "adres",
    "acil_durum_kisi", "acil_durum_telefon", "departman", "gorev", "ise_giris_tarihi",
    "isten_cikis_tarihi", "cikis_nedeni", "notlar")


def _personel_form_kw(cd):
    kw = {k: cd.get(k) for k in _PERSONEL_ALANLARI}
    # Yalnız İzinler yetkisi olan formda bulunur; boş bırakılırsa mevcut değer korunur.
    if cd.get("izin_onceki_kullanilan") is not None:
        kw["izin_onceki_kullanilan"] = cd["izin_onceki_kullanilan"]
    return kw


def _personel_form_context(form, baslik, **ek):
    return {"form": form, "baslik": baslik, "departmanlar": personel_servis.departmanlar(),
            "gorevler": personel_servis.gorevler(), **ek}


@ekran_gerekli("personel")
def personeller(request):
    ara = (request.GET.get("ara") or "").strip()
    durum = request.GET.get("durum") or "aktif"
    if durum not in personel_servis.DURUMLAR:
        durum = "aktif"
    departman = (request.GET.get("departman") or "").strip()
    bugun = tr_bugun()
    liste = list(personel_servis.personel_listele(
        ara=ara, durum=durum, departman=departman, bugun=bugun))
    ucret_gorebilir = ekran_gorebilir(request.user, "personel_ucret")
    if ucret_gorebilir:
        ucretler_map = ucret_servis.gecerli_ucretler(liste, tarih=bugun)
        for p in liste:
            p.guncel_ucret = ucretler_map.get(p.pk)
    return render(request, "core/personel_listesi.html", {
        "personeller": liste, "ara": ara, "durum": durum, "secili_departman": departman,
        "departmanlar": personel_servis.departmanlar(), "bugun": bugun,
        "ucret_gorebilir": ucret_gorebilir})


@never_cache
@ekran_gerekli("personel")
def personel_ekle(request):
    izin_alani = ekran_gorebilir(request.user, "personel_izinleri")
    if request.method == "POST":
        form = PersonelForm(request.POST, izin_alani=izin_alani)
        if form.is_valid():
            try:
                p = personel_servis.personel_olustur(
                    **_personel_form_kw(form.cleaned_data), kullanici=request.user)
                messages.success(request, f"Personel kartı eklendi: {p.ad_soyad}")
                return redirect("core:personel_detay", pk=p.pk)
            except personel_servis.PersonelHatasi as e:
                form.add_error(None, str(e))
    else:
        form = PersonelForm(izin_alani=izin_alani)
    return render(request, "core/personel_form.html",
                  _personel_form_context(form, "Yeni Personel", izin_alani=izin_alani))


@never_cache
@ekran_gerekli("personel")
def personel_duzenle(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    izin_alani = ekran_gorebilir(request.user, "personel_izinleri")
    if request.method == "POST":
        form = PersonelForm(request.POST, izin_alani=izin_alani)
        if form.is_valid():
            try:
                personel_servis.personel_guncelle(
                    p, **_personel_form_kw(form.cleaned_data), kullanici=request.user)
                messages.success(request, "Personel kartı güncellendi.")
                return redirect("core:personel_detay", pk=p.pk)
            except personel_servis.PersonelHatasi as e:
                form.add_error(None, str(e))
    else:
        baslangic = {k: getattr(p, k) for k in _PERSONEL_ALANLARI}
        baslangic["izin_onceki_kullanilan"] = p.izin_onceki_kullanilan
        form = PersonelForm(initial=baslangic, izin_alani=izin_alani)
    ek = {"duzenlenen": p, "izin_alani": izin_alani}
    if izin_alani:
        ek["hak_edilen_bugun"] = izin_servis.bakiye(p).hak_edilen
    return render(request, "core/personel_form.html",
                  _personel_form_context(form, "Personel Düzenle", **ek))


@never_cache
@ekran_gerekli("personel")
def personel_detay(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    bugun = tr_bugun()
    bitis = p.isten_cikis_tarihi if (p.isten_cikis_tarihi and p.isten_cikis_tarihi < bugun) else bugun
    ctx = {"p": p, "calisiyor": personel_servis.aktif_mi(p, bugun),
           "kidem": kidem_metni(p.ise_giris_tarihi, bitis)}
    if ekran_gorebilir(request.user, "personel_ucret"):
        ctx["ucret_gorebilir"] = True
        ctx["guncel_ucret"] = ucret_servis.gecerli_ucret(p, bugun)
        ctx["ucret_gecmisi"] = list(ucret_servis.ucretler(p))
    if ekran_gorebilir(request.user, "personel_izinleri"):
        ctx["izin_gorebilir"] = True
        ctx["bakiye"] = izin_servis.bakiye(p, bugun=bugun)
        ctx["son_izinler"] = list(
            PersonelIzin.objects.filter(personel=p, silindi=False)
            .order_by("-baslangic", "-id")[:10])
    if ekran_gorebilir(request.user, "personel_belgeleri"):
        ctx["belge_gorebilir"] = True
        ctx["belgeler"] = belge_servis.belge_durumlari(p, bugun=bugun)
    if ekran_gorebilir(request.user, "personel_devam"):
        ctx["devam_gorebilir"] = True
        ozet = devam_servis.aylik_ozet(bugun.year, bugun.month, bugun=bugun, personel_ids=[p.pk])
        ctx["devam_ozet"] = ozet[0] if ozet else None
        ctx["devam_ay"] = bugun.replace(day=1)
        ctx["devam_ay_param"] = f"{bugun.year:04d}-{bugun.month:02d}"
    if yonetici_mi(request.user):
        ctx["mesai_hesap_gorebilir"] = True
    return render(request, "core/personel_detay.html", ctx)


@ekran_gerekli("personel")
def personel_sil(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            personel_servis.personel_sil(p, kullanici=request.user)
        except personel_servis.PersonelHatasi as e:
            messages.error(request, str(e))
            return redirect("core:personel_detay", pk=p.pk)
        messages.success(request, f"Personel kartı silindi: {p.ad_soyad}")
        return redirect("core:personeller")
    return redirect("core:personel_detay", pk=p.pk)


# --- Ücretler --- (yalnız KAYIT; bordro/brüt/SGK/vergi hesabı yok — dönüş her zaman personel kartı)
@ekran_gerekli("personel_ucret")
def ucret_ekle(request):
    pid = request.GET.get("personel")
    donus_pk = int(pid) if pid and pid.isdigit() else None
    if request.method == "POST":
        form = PersonelUcretForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                u = ucret_servis.ucret_ekle(
                    cd["personel"], gecerlilik_baslangic=cd["gecerlilik_baslangic"],
                    tip=cd["tip"], net_tutar=cd.get("net_tutar"),
                    aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, f"Ücret kaydedildi: {u.personel.ad_soyad}.")
                return redirect("core:personel_detay", pk=u.personel_id)
            except ucret_servis.PersonelUcretHatasi as e:
                form.add_error(None, str(e))
    else:
        form = PersonelUcretForm(initial={"personel": donus_pk} if donus_pk else None)
    return render(request, "core/ucret_form.html", {
        "form": form, "baslik": "Yeni Ücret Kaydı", "donus_pk": donus_pk})


@ekran_gerekli("personel_ucret")
def ucret_duzenle(request, pk):
    ucret = get_object_or_404(
        PersonelUcret.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        form = PersonelUcretForm(request.POST, personel_sabit=True)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                ucret_servis.ucret_guncelle(
                    ucret, gecerlilik_baslangic=cd["gecerlilik_baslangic"], tip=cd["tip"],
                    net_tutar=cd.get("net_tutar"), aciklama=cd.get("aciklama", ""),
                    kullanici=request.user)
                messages.success(request, "Ücret kaydı güncellendi.")
                return redirect("core:personel_detay", pk=ucret.personel_id)
            except ucret_servis.PersonelUcretHatasi as e:
                form.add_error(None, str(e))
    else:
        form = PersonelUcretForm(personel_sabit=True, initial={
            "gecerlilik_baslangic": ucret.gecerlilik_baslangic, "tip": ucret.tip,
            "net_tutar": ucret.net_tutar, "aciklama": ucret.aciklama})
    return render(request, "core/ucret_form.html", {
        "form": form, "baslik": "Ücret Kaydı Düzenle", "duzenlenen": ucret})


@ekran_gerekli("personel_ucret")
def ucret_sil(request, pk):
    ucret = get_object_or_404(
        PersonelUcret.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        ucret_servis.ucret_sil(ucret, kullanici=request.user)
        messages.success(request, f"Ücret kaydı silindi: {ucret.personel.ad_soyad}")
    return redirect("core:personel_detay", pk=ucret.personel_id)


# --- İzinler ---
def _tarih_param(request, ad):
    ham = (request.GET.get(ad) or "").strip()
    try:
        return datetime.date.fromisoformat(ham) if ham else None
    except ValueError:
        return None


@ekran_gerekli("personel_izinleri")
def izinler(request):
    bugun = tr_bugun()
    personel_id = request.GET.get("personel") or ""
    tur = request.GET.get("tur") or ""
    if tur not in PersonelIzin.Tur.values:
        tur = ""
    bas, bit = _tarih_param(request, "bas"), _tarih_param(request, "bit")
    izinde = request.GET.get("izinde") == "1"
    kayitlar = izin_servis.izin_listele(
        personel_id=personel_id if personel_id.isdigit() else None, tur=tur,
        baslangic=bas, bitis=bit, izinde_bugun=bugun if izinde else None)
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    sabit_qs = request.GET.copy()
    sabit_qs.pop("sayfa", None)
    return render(request, "core/izin_listesi.html", {
        "kayitlar": sayfa, "personeller": personel_servis.aktif_personeller(),
        "turler": PersonelIzin.Tur.choices, "secili_personel": personel_id, "secili_tur": tur,
        "bas": bas, "bit": bit, "izinde": izinde, "bugun": bugun,
        "personel_link": ekran_gorebilir(request.user, "personel"),
        "sabit_qs": sabit_qs.urlencode()})


@ekran_gerekli("personel_izinleri")
def izin_bakiyeleri(request):
    bugun = tr_bugun()
    ayrilan_dahil = request.GET.get("ayrilan") == "1"
    personeller = (personel_servis.aktif_personeller() if ayrilan_dahil
                   else personel_servis.calisan_personeller(bugun))
    personeller = list(personeller)
    bakiyeler = izin_servis.bakiyeler(personeller, bugun=bugun)
    satirlar = [{"p": p, "b": bakiyeler[p.pk],
                 "ayrildi": bool(p.isten_cikis_tarihi and p.isten_cikis_tarihi < bugun)}
                for p in personeller]
    return render(request, "core/izin_bakiyeleri.html", {
        "satirlar": satirlar, "ayrilan_dahil": ayrilan_dahil, "bugun": bugun})


def _tatil_tarihleri_json():
    """İzin formundaki JS gün-önerisinin resmî tatilleri de düşebilmesi için (bkz.
    personel_izin.pazarsiz_gun aynı listeyi sunucu tarafında kullanır)."""
    return json.dumps([t.isoformat() for t in tatil_servis.aktif_tatiller()
                       .values_list("tarih", flat=True)])


@ekran_gerekli("personel_izinleri")
def izin_ekle(request):
    if request.method == "POST":
        form = PersonelIzinForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                izin = izin_servis.izin_ekle(
                    cd["personel"], tur=cd["tur"], baslangic=cd["baslangic"], bitis=cd["bitis"],
                    gun=cd.get("gun"), aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(
                    request, f"İzin kaydedildi: {izin.personel.ad_soyad} — {izin.gun} gün.")
                return redirect(_izin_donus_url(request, izin.personel_id))
            except izin_servis.PersonelIzinHatasi as e:
                form.add_error(None, str(e))
    else:
        baslangic = {}
        pid = request.GET.get("personel")
        if pid and pid.isdigit():
            baslangic["personel"] = int(pid)
        form = PersonelIzinForm(initial=baslangic)
    return render(request, "core/izin_form.html", {
        "form": form, "baslik": "Yeni İzin", "sonraki": request.GET.get("sonraki", ""),
        "tatil_tarihleri_json": _tatil_tarihleri_json()})


@ekran_gerekli("personel_izinleri")
def izin_duzenle(request, pk):
    izin = get_object_or_404(
        PersonelIzin.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        form = PersonelIzinForm(request.POST, personel_sabit=True)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                izin_servis.izin_guncelle(
                    izin, tur=cd["tur"], baslangic=cd["baslangic"], bitis=cd["bitis"],
                    gun=cd.get("gun"), aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, "İzin güncellendi.")
                return redirect(_izin_donus_url(request, izin.personel_id))
            except izin_servis.PersonelIzinHatasi as e:
                form.add_error(None, str(e))
    else:
        form = PersonelIzinForm(personel_sabit=True, initial={
            "tur": izin.tur, "baslangic": izin.baslangic, "bitis": izin.bitis,
            "gun": izin.gun, "aciklama": izin.aciklama})
    return render(request, "core/izin_form.html", {
        "form": form, "baslik": "İzin Düzenle", "duzenlenen": izin,
        "sonraki": request.GET.get("sonraki", ""), "tatil_tarihleri_json": _tatil_tarihleri_json()})


@ekran_gerekli("personel_izinleri")
def izin_sil(request, pk):
    izin = get_object_or_404(
        PersonelIzin.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        izin_servis.izin_sil(izin, kullanici=request.user)
        messages.success(request, f"İzin silindi: {izin.personel.ad_soyad}")
        return redirect(_izin_donus_url(request, izin.personel_id))
    return redirect("core:izinler")


def _izin_donus_url(request, personel_id):
    """İzin ekle/düzenle/sil sonrası dönüş: personel kartından gelindiyse (sonraki=personel) ve
    kullanıcı personel ekranını görebiliyorsa karta, aksi halde izin listesine."""
    if (request.POST.get("sonraki") or request.GET.get("sonraki")) == "personel" \
            and ekran_gorebilir(request.user, "personel"):
        return reverse("core:personel_detay", args=[personel_id])
    return reverse("core:izinler")


# --- Özlük Belgeleri + fotoğraf (dosyalar ÖZEL depoda; yalnız bu görünümlerle sunulur) ---
_ICERIK_TURU = {".pdf": "application/pdf", ".webp": "image/webp", ".xml": "application/xml"}
# XML tarayıcıda açılmaz (içindeki XSLT/betik riski): her zaman indirme olarak sunulur.
_INDIRME_ZORUNLU = {".xml"}


def _ozel_dosya_yanit(alan, indirme_adi):
    """Özel depodaki dosyayı yetkili görünümden akıtır: içerik türü DEPOLANAN uzantıdan
    (beyaz liste; istemci değeri değil), PDF/resim tarayıcıda açılır, önbelleğe alınmaz."""
    uz = os.path.splitext(alan.name or "")[1].lower()
    tur = _ICERIK_TURU.get(uz)
    if not tur:
        raise Http404
    try:
        dosya = alan.open("rb")
    except (OSError, SuspiciousFileOperation, ValueError):
        raise Http404
    ad = os.path.splitext(os.path.basename(indirme_adi or "dosya"))[0] or "dosya"
    yanit = FileResponse(dosya, content_type=tur, filename=ad + uz,
                         as_attachment=uz in _INDIRME_ZORUNLU)
    yanit["Cache-Control"] = "private, no-store"
    yanit["X-Content-Type-Options"] = "nosniff"
    return yanit


@ekran_gerekli("personel_belgeleri")
def belgeler(request):
    bugun = tr_bugun()
    personel_id = request.GET.get("personel") or ""
    tur = request.GET.get("tur") or ""
    if tur not in PersonelBelge.Tur.values:
        tur = ""
    kayitlar = belge_servis.belge_listele(
        personel_id=personel_id if personel_id.isdigit() else None, tur=tur)
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    durumlar = belge_servis.durum_haritasi(list(belge_servis.aktif_belgeler()), bugun=bugun)
    satirlar = [{"b": b, "durum": durumlar.get(b.pk, ("SURESIZ", None))} for b in sayfa]
    sabit_qs = request.GET.copy()
    sabit_qs.pop("sayfa", None)
    return render(request, "core/belge_listesi.html", {
        "kayitlar": sayfa, "satirlar": satirlar, "personeller": personel_servis.aktif_personeller(),
        "turler": PersonelBelge.Tur.choices, "secili_personel": personel_id, "secili_tur": tur,
        "personel_link": ekran_gorebilir(request.user, "personel"),
        "sabit_qs": sabit_qs.urlencode()})


@ekran_gerekli("personel_belgeleri")
def belge_uyarilari(request):
    try:
        gun = int(request.GET.get("gun", 30))
    except ValueError:
        gun = 30
    if gun not in (15, 30, 60, 90):
        gun = 30
    return render(request, "core/belge_uyarilari.html", {
        "uyarilar": belge_servis.suresi_dolacaklar(gun=gun), "gun": gun,
        "gunler": (15, 30, 60, 90),
        "personel_link": ekran_gorebilir(request.user, "personel")})


def _belge_donus_url(request, personel_id):
    if (request.POST.get("sonraki") or request.GET.get("sonraki")) == "personel" \
            and ekran_gorebilir(request.user, "personel"):
        return reverse("core:personel_detay", args=[personel_id])
    return reverse("core:belgeler")


@ekran_gerekli("personel_belgeleri")
def belge_ekle(request):
    if request.method == "POST":
        form = PersonelBelgeForm(request.POST, request.FILES)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                belge = belge_servis.belge_ekle(
                    cd["personel"], tur=cd["tur"], dosya=cd["dosya"],
                    aciklama=cd.get("aciklama", ""), bitis_tarihi=cd.get("bitis_tarihi"),
                    kullanici=request.user)
                messages.success(
                    request, f"Belge kaydedildi: {belge.personel.ad_soyad} — {belge.get_tur_display()}")
                return redirect(_belge_donus_url(request, belge.personel_id))
            except belge_servis.PersonelBelgeHatasi as e:
                form.add_error(None, str(e))
    else:
        baslangic = {}
        pid = request.GET.get("personel")
        if pid and pid.isdigit():
            baslangic["personel"] = int(pid)
        form = PersonelBelgeForm(initial=baslangic)
    return render(request, "core/belge_form.html", {
        "form": form, "baslik": "Yeni Belge", "sonraki": request.GET.get("sonraki", "")})


@ekran_gerekli("personel_belgeleri")
def belge_duzenle(request, pk):
    belge = get_object_or_404(
        PersonelBelge.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        form = PersonelBelgeForm(request.POST, personel_sabit=True, dosya_alani=False)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                belge_servis.belge_guncelle(
                    belge, tur=cd["tur"], aciklama=cd.get("aciklama", ""),
                    bitis_tarihi=cd.get("bitis_tarihi"), kullanici=request.user)
                messages.success(request, "Belge güncellendi.")
                return redirect(_belge_donus_url(request, belge.personel_id))
            except belge_servis.PersonelBelgeHatasi as e:
                form.add_error(None, str(e))
    else:
        form = PersonelBelgeForm(personel_sabit=True, dosya_alani=False, initial={
            "tur": belge.tur, "aciklama": belge.aciklama, "bitis_tarihi": belge.bitis_tarihi})
    return render(request, "core/belge_form.html", {
        "form": form, "baslik": "Belge Düzenle", "duzenlenen": belge,
        "sonraki": request.GET.get("sonraki", "")})


@ekran_gerekli("personel_belgeleri")
def belge_sil(request, pk):
    belge = get_object_or_404(
        PersonelBelge.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    if request.method == "POST":
        belge_servis.belge_sil(belge, kullanici=request.user)
        messages.success(request, "Belge silindi.")
        return redirect(_belge_donus_url(request, belge.personel_id))
    return redirect("core:belgeler")


@never_cache
@ekran_gerekli("personel_belgeleri")
def belge_indir(request, pk):
    belge = get_object_or_404(
        PersonelBelge.objects.select_related("personel"), pk=pk, silindi=False,
        personel__silindi=False)
    return _ozel_dosya_yanit(belge.dosya, belge.orijinal_ad)


@never_cache
@ekran_gerekli("personel")
def personel_foto(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if not p.foto:
        raise Http404
    return _ozel_dosya_yanit(p.foto, "foto")


@ekran_gerekli("personel")
def personel_foto_yukle(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        form = PersonelFotoForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                belge_servis.foto_ayarla(p, form.cleaned_data["dosya"], kullanici=request.user)
                messages.success(request, "Fotoğraf güncellendi.")
            except belge_servis.PersonelBelgeHatasi as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Fotoğraf seçilmedi.")
    return redirect("core:personel_detay", pk=p.pk)


@ekran_gerekli("personel")
def personel_foto_kaldir(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        belge_servis.foto_kaldir(p, kullanici=request.user)
        messages.success(request, "Fotoğraf kaldırıldı.")
    return redirect("core:personel_detay", pk=p.pk)


# --- Devam / Yoklama ---
def _yoklama_tarihi(veri, varsayilan):
    """`tarih=YYYY-MM-DD` (GET ya da POST); geçersiz / makul aralık dışı → varsayılan."""
    try:
        t = datetime.datetime.strptime((veri.get("tarih") or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return varsayilan
    return t if 2000 <= t.year <= 2100 else varsayilan


def _yoklama_ay(veri, varsayilan):
    """`ay=YYYY-MM` → (yıl, ay); geçersiz → varsayılan tarihin yılı/ayı."""
    try:
        d = datetime.datetime.strptime((veri.get("ay") or "").strip(), "%Y-%m").date()
    except ValueError:
        return varsayilan.year, varsayilan.month
    if not 2000 <= d.year <= 2100:
        return varsayilan.year, varsayilan.month
    return d.year, d.month


@ekran_gerekli("personel_devam")
def yoklama(request):
    bugun = tr_bugun()
    if request.method == "POST":
        tarih = _yoklama_tarihi(request.POST, bugun)
        girdi = {}
        # Yalnız formda GÖSTERİLEN (kilitsiz) satırlar işlenir; alan adları personel pk'sına bağlıdır
        # (sıra kayması yok). Gönderilmeyen personele dokunulmaz.
        for s in devam_servis.gun_satirlari(tarih):
            pid = s.personel.pk
            if not s.kilitli and f"durum_{pid}" in request.POST:
                girdi[pid] = (request.POST.get(f"durum_{pid}", ""), request.POST.get(f"not_{pid}", ""))
        try:
            kaydedilen, temizlenen, _ = devam_servis.gunluk_kaydet(
                tarih, girdi, kullanici=request.user)
        except devam_servis.PersonelDevamHatasi as e:
            messages.error(request, str(e))
        else:
            if kaydedilen or temizlenen:
                messages.success(
                    request, f"{tarih:%d.%m.%Y} yoklaması kaydedildi "
                             f"({kaydedilen} kayıt eklendi/güncellendi, {temizlenen} temizlendi).")
            else:
                messages.success(request, "Değişiklik yok; yoklama zaten güncel.")
        return redirect(f"{reverse('core:yoklama')}?tarih={tarih.isoformat()}")

    tarih = _yoklama_tarihi(request.GET, bugun)
    satirlar = devam_servis.gun_satirlari(tarih)
    mesai_bilgisi = {}
    for m in MesaiKaydi.objects.filter(
            is_tarihi=tarih, silindi=False, personel_id__in=[s.personel.pk for s in satirlar]
    ).order_by("giris_zamani"):
        mesai_bilgisi.setdefault(m.personel_id, []).append(m)
    return render(request, "core/yoklama.html", {
        "tarih": tarih, "bugun": bugun, "satirlar": satirlar,
        "ozet": devam_servis.gun_ozeti(satirlar),
        "onceki": tarih - datetime.timedelta(days=1),
        "sonraki": tarih + datetime.timedelta(days=1) if tarih < bugun else None,
        "gelecek": tarih > bugun, "pazar": tarih.weekday() == 6,
        "tatil": tatil_servis.gunun_tatili(tarih),
        "mesai_bilgisi": mesai_bilgisi,
        "durumlar": [("GELDI", "Geldi", "Geldi"), ("YARIM_GUN", "Yarım", "Yarım Gün"),
                     ("GELMEDI", "Gelmedi", "Gelmedi")],
        "personel_link": ekran_gorebilir(request.user, "personel")})


@ekran_gerekli("personel_devam")
def yoklama_aylik(request):
    bugun = tr_bugun()
    yil, ay = _yoklama_ay(request.GET, bugun)
    ilk, son = ay_araligi(yil, ay)
    satirlar = devam_servis.aylik_ozet(yil, ay, bugun=bugun)
    onceki = ilk - datetime.timedelta(days=1)
    sonraki = son + datetime.timedelta(days=1)
    return render(request, "core/yoklama_aylik.html", {
        "ay": ilk, "ay_param": f"{yil:04d}-{ay:02d}",
        "onceki_ay": f"{onceki.year:04d}-{onceki.month:02d}",
        "sonraki_ay": f"{sonraki.year:04d}-{sonraki.month:02d}" if ilk < bugun.replace(day=1) else None,
        "satirlar": satirlar, "toplam": devam_servis.aylik_toplam(satirlar), "bugun": bugun,
        "personel_link": ekran_gorebilir(request.user, "personel"),
        "dokum_gorebilir": ekran_gorebilir(request.user, "personel_ucret")})


@ekran_gerekli_hepsi("personel_devam", "personel_ucret")
def yoklama_aylik_dokum(request):
    bugun = tr_bugun()
    yil, ay = _yoklama_ay(request.GET, bugun)
    xlsx = dokum_servis.dokum_xlsx(yil, ay, bugun=bugun, kullanici=request.user)
    resp = HttpResponse(
        xlsx, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="puantaj_{yil:04d}-{ay:02d}.xlsx"'
    resp["Cache-Control"] = "private, no-store"
    return resp


# --- Resmî Tatiller ---
@ekran_gerekli("resmi_tatil")
def resmi_tatiller(request):
    bugun = tr_bugun()
    try:
        yil = int(request.GET.get("yil") or bugun.year)
    except ValueError:
        yil = bugun.year
    if not 2000 <= yil <= 2100:
        yil = bugun.year
    return render(request, "core/resmi_tatil_listesi.html", {
        "tatiller": list(tatil_servis.yil_tatilleri(yil)), "yil": yil,
        "onceki_yil": yil - 1, "sonraki_yil": yil + 1,
        "dini_bayram_eksik": not tatil_servis.dini_bayram_var_mi(yil)})


@ekran_gerekli("resmi_tatil")
def resmi_tatil_ekle(request):
    if request.method == "POST":
        form = ResmiTatilForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                t = tatil_servis.tatil_ekle(
                    tarih=cd["tarih"], ad=cd["ad"], kullanici=request.user)
                messages.success(request, f"Tatil eklendi: {t.ad} ({t.tarih:%d.%m.%Y}).")
                return redirect(f"{reverse('core:resmi_tatiller')}?yil={t.tarih.year}")
            except tatil_servis.ResmiTatilHatasi as e:
                form.add_error(None, str(e))
    else:
        form = ResmiTatilForm()
    return render(request, "core/resmi_tatil_form.html", {"form": form, "baslik": "Yeni Tatil"})


@ekran_gerekli("resmi_tatil")
def resmi_tatil_duzenle(request, pk):
    tatil = get_object_or_404(ResmiTatil, pk=pk, silindi=False)
    if request.method == "POST":
        form = ResmiTatilForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                tatil_servis.tatil_guncelle(
                    tatil, tarih=cd["tarih"], ad=cd["ad"], kullanici=request.user)
                messages.success(request, "Tatil güncellendi.")
                return redirect(f"{reverse('core:resmi_tatiller')}?yil={cd['tarih'].year}")
            except tatil_servis.ResmiTatilHatasi as e:
                form.add_error(None, str(e))
    else:
        form = ResmiTatilForm(initial={"tarih": tatil.tarih, "ad": tatil.ad})
    return render(request, "core/resmi_tatil_form.html", {
        "form": form, "baslik": "Tatil Düzenle", "duzenlenen": tatil})


@ekran_gerekli("resmi_tatil")
def resmi_tatil_sil(request, pk):
    tatil = get_object_or_404(ResmiTatil, pk=pk, silindi=False)
    if request.method == "POST":
        yil = tatil.tarih.year
        tatil_servis.tatil_sil(tatil, kullanici=request.user)
        messages.success(request, f"Tatil silindi: {tatil.ad}")
        return redirect(f"{reverse('core:resmi_tatiller')}?yil={yil}")
    return redirect("core:resmi_tatiller")


# === Mesai (personelin kendi telefonundan başlat/bitir) ====================================
_MESAI_TR = ZoneInfo("Europe/Istanbul")


# --- Mesai Hesabı (Personel kartından, yalnız yönetici) ---
@yonetici_gerekli
def mesai_hesap_olustur(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        form = MesaiHesapOlusturForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                mesai_hesap_servis.hesap_olustur(
                    p, kullanici_adi=cd["kullanici_adi"], sifre=cd["sifre"], yapan=request.user)
                messages.success(request, f"Mesai hesabı oluşturuldu: {cd['kullanici_adi']}")
                return redirect("core:personel_detay", pk=p.pk)
            except mesai_hesap_servis.MesaiHesapHatasi as e:
                form.add_error(None, str(e))
    else:
        form = MesaiHesapOlusturForm()
    return render(request, "core/mesai_hesap_form.html", {
        "form": form, "baslik": "Mesai Hesabı Oluştur", "personel": p})


@yonetici_gerekli
def mesai_sifre_sifirla(request, pk):
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        form = MesaiSifreForm(request.POST)
        if form.is_valid():
            try:
                mesai_hesap_servis.sifre_sifirla(
                    p, sifre=form.cleaned_data["sifre"], yapan=request.user)
                messages.success(request, "Mesai hesabının şifresi sıfırlandı.")
                return redirect("core:personel_detay", pk=p.pk)
            except mesai_hesap_servis.MesaiHesapHatasi as e:
                form.add_error(None, str(e))
    else:
        form = MesaiSifreForm()
    return render(request, "core/mesai_hesap_form.html", {
        "form": form, "baslik": "Mesai Hesabı Şifresini Sıfırla", "personel": p})


@yonetici_gerekli
def mesai_hesap_durum(request, pk):
    """Hesabı kapatır/açar — mevcut duruma göre toggle, POST only."""
    p = get_object_or_404(Personel, pk=pk, silindi=False)
    if request.method == "POST":
        try:
            if p.kullanici_id and p.kullanici.is_active:
                mesai_hesap_servis.hesap_kapat(p, yapan=request.user)
                messages.success(request, "Mesai hesabı kapatıldı.")
            else:
                mesai_hesap_servis.hesap_ac(p, yapan=request.user)
                messages.success(request, "Mesai hesabı yeniden açıldı.")
        except mesai_hesap_servis.MesaiHesapHatasi as e:
            messages.error(request, str(e))
    return redirect("core:personel_detay", pk=p.pk)


# --- Mesaim (personelin kendisi, mobil öncelikli) ---
@login_required
@never_cache
def mesaim(request):
    p = Personel.objects.filter(kullanici=request.user, silindi=False).first()
    if p is None:
        raise PermissionDenied("Bu hesaba bağlı bir personel kartı yok.")
    calisiyor = personel_servis.aktif_mi(p)
    if request.method == "POST":
        if not calisiyor:
            messages.error(request, "Artık çalışmıyorsunuz; mesai başlatılamaz.")
        else:
            ip = istemci_ip(request)
            try:
                if request.POST.get("eylem") == "baslat":
                    mesai_servis.baslat(p, ip=ip, kullanici=request.user)
                    messages.success(request, "Mesai başlatıldı.")
                elif request.POST.get("eylem") == "bitir":
                    mesai_servis.bitir(p, ip=ip, kullanici=request.user)
                    messages.success(request, "Mesai bitirildi.")
            except mesai_servis.MesaiHatasi as e:
                messages.error(request, str(e))
        return redirect("core:mesaim")
    return render(request, "core/mesaim.html", {
        "p": p, "calisiyor": calisiyor, "acik_kayit": mesai_servis.acik_kayit(p),
        "kayitlar": list(mesai_servis.son_kayitlar(p, gun=7))})


# --- Mesai Kayıtları (yönetici ekranı) ---
def _mesai_tarih_araligi(request):
    bugun = tr_bugun()
    try:
        bas = datetime.date.fromisoformat(request.GET.get("bas") or "")
    except ValueError:
        bas = bugun - datetime.timedelta(days=30)
    try:
        bit = datetime.date.fromisoformat(request.GET.get("bit") or "")
    except ValueError:
        bit = bugun
    return bas, bit


@ekran_gerekli("mesai_kayitlari")
def mesai_kayitlari(request):
    bas, bit = _mesai_tarih_araligi(request)
    personel_id = (request.GET.get("personel") or "").strip()
    kayitlar = mesai_servis.kayitlari_listele(
        baslangic=bas, bitis=bit, personel_id=personel_id if personel_id.isdigit() else None)
    sayfa = Paginator(kayitlar, 50).get_page(request.GET.get("sayfa"))
    sabit_qs = request.GET.copy()
    sabit_qs.pop("sayfa", None)
    return render(request, "core/mesai_kayitlari.html", {
        "sayfa": sayfa, "bas": bas, "bit": bit, "secili_personel": personel_id,
        "personeller": personel_servis.aktif_personeller(),
        "cikisi_eksik": list(mesai_servis.cikisi_eksik_kayitlar()),
        "yoklama_uyusmazlik": list(
            mesai_servis.yoklama_uyusmazliklari(baslangic=bas, bitis=bit)),
        "izinli_giris": list(
            mesai_servis.izinli_gunde_giris_uyarilari(baslangic=bas, bitis=bit)),
        "sabit_qs": sabit_qs.urlencode()})


@ekran_gerekli("mesai_kayitlari")
def mesai_kaydi_ekle(request):
    if request.method == "POST":
        form = MesaiDuzeltForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                mesai_servis.manuel_ekle(
                    cd["personel"], giris_zamani=cd["giris_zamani"],
                    cikis_zamani=cd.get("cikis_zamani"), duzeltme_notu=cd["duzeltme_notu"],
                    kullanici=request.user)
                messages.success(request, "Mesai kaydı eklendi.")
                return redirect("core:mesai_kayitlari")
            except mesai_servis.MesaiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = MesaiDuzeltForm()
    return render(request, "core/mesai_kaydi_form.html", {
        "form": form, "baslik": "Mesai Kaydı Ekle"})


@ekran_gerekli("mesai_kayitlari")
def mesai_kaydi_duzenle(request, pk):
    kayit = get_object_or_404(
        MesaiKaydi.objects.select_related("personel"), pk=pk, silindi=False)
    if request.method == "POST":
        form = MesaiDuzeltForm(request.POST, personel_sabit=True)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                mesai_servis.duzelt(
                    kayit, giris_zamani=cd["giris_zamani"], cikis_zamani=cd.get("cikis_zamani"),
                    duzeltme_notu=cd["duzeltme_notu"], kullanici=request.user)
                messages.success(request, "Mesai kaydı güncellendi.")
                return redirect("core:mesai_kayitlari")
            except mesai_servis.MesaiHatasi as e:
                form.add_error(None, str(e))
    else:
        form = MesaiDuzeltForm(personel_sabit=True, initial={
            "giris_zamani": kayit.giris_zamani.astimezone(_MESAI_TR),
            "cikis_zamani": kayit.cikis_zamani.astimezone(_MESAI_TR) if kayit.cikis_zamani else None,
            "duzeltme_notu": kayit.duzeltme_notu})
    return render(request, "core/mesai_kaydi_form.html", {
        "form": form, "baslik": "Mesai Kaydı Düzenle", "duzenlenen": kayit})


@ekran_gerekli("mesai_kayitlari")
def mesai_kaydi_sil(request, pk):
    kayit = get_object_or_404(MesaiKaydi, pk=pk, silindi=False)
    if request.method == "POST":
        mesai_servis.sil(kayit, kullanici=request.user)
        messages.success(request, "Mesai kaydı silindi.")
    return redirect("core:mesai_kayitlari")


# --- Mesai Ayarları (Ayarlar > yalnız yönetici) ---
@yonetici_gerekli
def mesai_ayarlari(request):
    return render(request, "core/mesai_ayarlari.html", {
        "aglar": list(mesai_ag_servis.aktif_aglar())})


@yonetici_gerekli
def mesai_ag_ekle(request):
    if request.method == "POST":
        form = MesaiIzinliAgForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            try:
                mesai_ag_servis.ag_ekle(
                    cidr=cd["cidr"], aciklama=cd.get("aciklama", ""), kullanici=request.user)
                messages.success(request, f"İzinli ağ eklendi: {cd['cidr']}")
            except mesai_ag_servis.MesaiAgHatasi as e:
                messages.error(request, str(e))
    return redirect("core:mesai_ayarlari")


@yonetici_gerekli
def mesai_ag_sil(request, pk):
    ag = get_object_or_404(MesaiIzinliAg, pk=pk, silindi=False)
    if request.method == "POST":
        mesai_ag_servis.ag_sil(ag, kullanici=request.user)
        messages.success(request, f"İzinli ağ silindi: {ag.cidr}")
    return redirect("core:mesai_ayarlari")



# === İNSAN KAYNAKLARI — Aylık Bordro Tahakkuku ===
def _bordro_form_baglam(request, bordro=None):
    """Bordro ekle/düzenle: başlık formu + satır formset'i (POST ya da düzenlemede mevcut kayıttan)."""
    if request.method == "POST":
        return PersonelBordroForm(request.POST, request.FILES), PersonelBordroSatirFormSet(request.POST)
    if bordro is None:
        bugun = timezone.localdate()
        ay_sonu = (bugun.replace(day=1) - datetime.timedelta(days=1))          # varsayılan: geçen ay sonu
        return (PersonelBordroForm(initial={"yil": ay_sonu.year, "ay": ay_sonu.month, "tahakkuk_tarihi": ay_sonu}),
                PersonelBordroSatirFormSet())
    fis = bordro_servis.bordro_fisi(bordro)
    baslik = PersonelBordroForm(initial={"yil": bordro.yil, "ay": bordro.ay, "tahakkuk_tarihi": bordro.tahakkuk_tarihi,
                                         "aciklama": bordro.aciklama})
    ilk = [{"cari": s.cari_id, "ad_soyad": s.ad_soyad, "gider_hesap": s.gider_hesap_id,
            **{a: getattr(s, a) for a in bordro_servis.TUTAR_ALANLARI}}
           for s in bordro_servis.satirlar(bordro)]
    return baslik, PersonelBordroSatirFormSet(initial=ilk)


def _bordro_kaydet(request, bordro=None):
    form, formset = _bordro_form_baglam(request, bordro)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        satirlar = [f.girdi() for f in formset if f.dolu_mu()]
        cd = form.cleaned_data
        try:
            kw = dict(yil=cd["yil"], ay=cd["ay"], tahakkuk_tarihi=cd["tahakkuk_tarihi"], satirlar=satirlar,
                      aciklama=cd["aciklama"], dosya=cd.get("dosya") or None, kullanici=request.user)
            if bordro is None:
                bordro = bordro_servis.bordro_olustur(**kw)
                messages.success(request, f"Bordro kaydedildi: {AYLAR_TR[bordro.ay]} {bordro.yil}.")
            else:
                bordro_servis.bordro_guncelle(bordro, dosyayi_kaldir=cd.get("dosyayi_kaldir"), **kw)
                messages.success(request, "Bordro güncellendi; fiş yeniden yazıldı.")
            return None, None, redirect("core:bordro_detay", pk=bordro.pk)
        except bordro_servis.BordroHatasi as e:
            form.add_error(None, str(e))
    return form, formset, None


PersonelBordroSatirFormSet = formset_factory(PersonelBordroSatirForm, extra=0, min_num=1, validate_min=True)
AYLAR_TR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


@ekran_gerekli("bordro")
def bordro_listesi(request):
    liste = []
    for b in PersonelBordro.objects.filter(silindi=False):
        liste.append({"bordro": b, "t": bordro_servis.ozet(b), "fis": bordro_servis.bordro_fisi(b), "ay_adi": AYLAR_TR[b.ay]})
    return render(request, "core/bordro_listesi.html", {"liste": liste})


@ekran_gerekli("bordro")
def bordro_ekle(request):
    form, formset, yanit = _bordro_kaydet(request)
    if yanit:
        return yanit
    return render(request, "core/bordro_form.html", {"form": form, "formset": formset, "duzenle": False})


@ekran_gerekli("bordro")
def bordro_duzenle(request, pk):
    bordro = get_object_or_404(PersonelBordro, pk=pk, silindi=False)
    form, formset, yanit = _bordro_kaydet(request, bordro)
    if yanit:
        return yanit
    return render(request, "core/bordro_form.html", {"form": form, "formset": formset, "duzenle": True, "bordro": bordro})


@ekran_gerekli("bordro")
def bordro_detay(request, pk):
    bordro = get_object_or_404(PersonelBordro, pk=pk, silindi=False)
    return render(request, "core/bordro_detay.html", {
        "bordro": bordro, "satirlar": bordro_servis.satirlar(bordro), "t": bordro_servis.ozet(bordro),
        "fis": bordro_servis.bordro_fisi(bordro), "ay_adi": AYLAR_TR[bordro.ay]})


@ekran_gerekli("bordro")
def bordro_sil(request, pk):
    bordro = get_object_or_404(PersonelBordro, pk=pk, silindi=False)
    fis = bordro_servis.bordro_fisi(bordro)
    if fis is None:
        messages.error(request, "Bordronun fişi bulunamadı.")
        return redirect("core:bordro_detay", pk=bordro.pk)
    return _fis_sil_akisi(
        request, fis, baslik=f"Bordro · {AYLAR_TR[bordro.ay]} {bordro.yil}", geri=reverse("core:bordro_listesi"),
        sil=lambda: bordro_servis.bordro_sil(bordro, kullanici=request.user))


@never_cache
@ekran_gerekli("bordro")
def bordro_dosya(request, pk):
    """Bordro PDF'ini (özel depoda) yetkili görünümden sunar — /media/ üzerinden DEĞİL."""
    bordro = get_object_or_404(PersonelBordro, pk=pk, silindi=False)
    if not bordro.dosya:
        raise Http404
    return _ozel_dosya_yanit(bordro.dosya, bordro.orijinal_ad)



@ekran_gerekli_herhangi("kasa", "banka", "kredi_karti", "kredi", "cek_senet")
def finans_ozeti(request):
    """FİNANS özeti (dashboard): kasa/banka bakiyeleri, kredi kartı + kredi borcu, çek/senet durumu ve yaklaşan vadeler (yalnız okur)."""
    o = finans_ozet_servis.ozet(timezone.localdate())
    return render(request, "core/finans_ozet.html", {"o": o})



# === KREDİ KARTI — Dönem Ekstresi / Nakit Akışı / Taksit Önerileri ===
@ekran_gerekli("kredi_karti")
def kredi_karti_donem_ekstresi(request, pk):
    """Kart başına dönem ekstresi: kesim tarihi seç → devir + taksitler + tek çekimler − ödemeler = dönem borcu, son ödeme tarihi; banka ekstresiyle
    karşılaştırma (yalnız görüntü — kayıt yazmaz)."""
    kart = get_object_or_404(KrediKarti, pk=pk, silindi=False)
    bugun = timezone.localdate()
    ctx = {"kart": kart, "bugun": bugun, "hata": ""}
    if kart.para_birimi != "TRY" or not kk_donem_servis.gunler_tanimli(kart):
        ctx["hata"] = ("Dönem ekstresi yalnız TL kartlar için hesaplanır." if kart.para_birimi != "TRY"
                       else "Kartta hesap kesim günü ve son ödeme günü tanımlı olmalı — Kart Düzenle'den girin.")
        return render(request, "core/kredi_karti_donem_ekstresi.html", ctx)
    secenekler = kk_donem_servis.kesim_secenekleri(kart, bugun)
    ham = request.GET.get("kesim")
    try:
        kesim = datetime.date.fromisoformat(ham) if ham else kk_donem_servis.son_kesilmis_kesim(kart, bugun)
    except ValueError:
        kesim = kk_donem_servis.son_kesilmis_kesim(kart, bugun)
    kesim = kk_donem_servis.donem_kesimi(kart, kesim)
    ekstre = kk_donem_servis.donem_ekstresi(kart, kesim, bugun)
    banka_tutar, fark = None, None
    ham_banka = (request.GET.get("banka_tutar") or "").strip()
    if ham_banka:
        try:
            banka_tutar = parse_tr(ham_banka)
            fark = banka_tutar - ekstre["donem_borcu"]
        except SayiHatasi:
            ctx["hata"] = "Banka ekstresi tutarı geçerli bir sayı değil (örn. 12.345,67)."
    from core.services.hesap_plani import yaprak_hesaplar
    kodlar = {h.hesap_kodu for h in yaprak_hesaplar().filter(hesap_kodu__in=["770.03"]) | yaprak_hesaplar().filter(hesap_kodu__startswith="780")}
    faiz = sorted(k for k in kodlar if k.startswith("780"))
    ctx.update({"ekstre": ekstre, "secenekler": secenekler, "kesim": kesim, "banka_tutar": banka_tutar, "fark": fark, "ham_banka": ham_banka,
                "faiz_kodu": faiz[0] if faiz else "", "masraf_kodu": "770.03" if "770.03" in kodlar else ""})
    return render(request, "core/kredi_karti_donem_ekstresi.html", ctx)


@ekran_gerekli("kredi_karti")
def kredi_karti_nakit_akisi(request):
    """Tüm TL kartların gelecek son ödeme tarihleri ve tutarları (ay bazında özet + ayrıntı)."""
    return render(request, "core/kredi_karti_nakit_akisi.html", {"o": kk_donem_servis.nakit_akisi(timezone.localdate())})


@ekran_gerekli("kredi_karti")
def kredi_karti_taksit_onerileri(request):
    """Mevcut harcamalardan açıklamasında 'N TAKSİT' geçen ve planı olmayanlar için taksit planı önerisi; seçilenler onayla uygulanır
    (yalnız plan kaydeder; fiş/muhasebe değişmez)."""
    if request.method == "POST":
        secilen = request.POST.getlist("fis")
        try:
            n = kk_donem_servis.oneri_uygula(secilen, kullanici=request.user) if secilen else 0
            messages.success(request, f"{n} harcama için taksit planı oluşturuldu." if n else "Seçim yapılmadı.")
        except kk_donem_servis.KkDonemHatasi as e:
            messages.error(request, str(e))
        return redirect("core:kredi_karti_taksit_onerileri")
    return render(request, "core/kredi_karti_taksit_onerileri.html", {"oneriler": kk_donem_servis.oneriler()})



# === MUHASEBE — KDV Dönem Mahsubu (191 / 391 / 190 / 360.30) ===
def _kdv_baglam(request, m=None):
    """Mahsup formu: (form, önizleme). POST ``islem=onizle`` → hesapla ve göster; ``islem=kaydet`` → yaz."""
    if request.method == "POST":
        return KdvMahsupForm(request.POST, request.FILES, donem_sabit=m is not None, initial=({"yil": m.yil, "ay": m.ay} if m is not None else None))
    if m is None:
        bugun = timezone.localdate()
        onceki = bugun.replace(day=1) - datetime.timedelta(days=1)
        return KdvMahsupForm(initial={"yil": onceki.year, "ay": onceki.month, "fark_hesap": "659"})
    return KdvMahsupForm(donem_sabit=True, initial={
        "yil": m.yil, "ay": m.ay, "beyan_devreden": m.beyan_devreden, "beyan_odenecek": m.beyan_odenecek, "fark_hesap": m.fark_hesap_id, "aciklama": m.aciklama})


def _kdv_kaydet_ekran(request, m=None):
    form = _kdv_baglam(request, m)
    onizleme, yanit = None, None
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        yil, ay = (m.yil, m.ay) if m is not None else (cd["yil"], cd["ay"])
        fis = kdv_servis.fis_of(m) if m is not None else None
        try:
            onizleme = kdv_servis.hesapla(yil=yil, ay=ay, beyan_devreden=cd.get("beyan_devreden"), beyan_odenecek=cd.get("beyan_odenecek"),
                                          fark_hesap_kodu=cd["fark_hesap"], haric_fis=fis)
            if request.POST.get("islem") == "kaydet":
                kw = dict(beyan_devreden=cd.get("beyan_devreden"), beyan_odenecek=cd.get("beyan_odenecek"), fark_hesap_kodu=cd["fark_hesap"],
                          aciklama=cd["aciklama"], dosya=cd.get("dosya") or None, kullanici=request.user)
                if m is None:
                    m = kdv_servis.olustur(yil=yil, ay=ay, **kw)
                    messages.success(request, f"{kdv_servis.donem_adi(m.yil, m.ay)} KDV mahsubu kaydedildi: fiş {kdv_servis.fis_of(m).yil}/{kdv_servis.fis_of(m).fis_no}.")
                else:
                    kdv_servis.guncelle(m, dosyayi_kaldir=cd.get("dosyayi_kaldir"), **kw)
                    messages.success(request, f"{kdv_servis.donem_adi(m.yil, m.ay)} KDV mahsubu güncellendi; fiş aynı numarayla yeniden yazıldı.")
                yanit = redirect("core:kdv_mahsup_listesi")
        except kdv_servis.KdvMahsupHatasi as e:
            form.add_error(None, str(e))
            if onizleme is None:
                onizleme = None
    return form, onizleme, yanit


@ekran_gerekli("kdv_mahsup")
def kdv_mahsup_listesi(request):
    satirlar = []
    for m in kdv_servis.aktifler():
        sonraki = kdv_servis.sonraki_mahsup(m)
        satirlar.append({"m": m, "fis": kdv_servis.fis_of(m), "degisiklik": kdv_servis.degisiklik_var(m), "sonraki": sonraki,
                         "donem": kdv_servis.donem_adi(m.yil, m.ay)})
    return render(request, "core/kdv_mahsup_listesi.html", {"satirlar": satirlar})


@ekran_gerekli("kdv_mahsup")
def kdv_mahsup_ekle(request):
    form, onizleme, yanit = _kdv_kaydet_ekran(request)
    if yanit:
        return yanit
    return render(request, "core/kdv_mahsup_form.html", {"form": form, "o": onizleme, "duzenle": False})


@ekran_gerekli("kdv_mahsup")
def kdv_mahsup_duzenle(request, pk):
    m = get_object_or_404(KdvMahsup, pk=pk, silindi=False)
    sonraki = kdv_servis.sonraki_mahsup(m)
    if sonraki is not None:
        messages.error(request, f"{kdv_servis.donem_adi(sonraki.yil, sonraki.ay)} dönemine ait mahsup var; bu mahsup düzenlenemez "
                                f"(190 açılışı sonraki dönemi etkiler). Önce {kdv_servis.donem_adi(sonraki.yil, sonraki.ay)} mahsubunu silin.")
        return redirect("core:kdv_mahsup_listesi")
    form, onizleme, yanit = _kdv_kaydet_ekran(request, m)
    if yanit:
        return yanit
    return render(request, "core/kdv_mahsup_form.html", {"form": form, "o": onizleme, "duzenle": True, "m": m})


@ekran_gerekli("kdv_mahsup")
def kdv_mahsup_sil(request, pk):
    m = get_object_or_404(KdvMahsup, pk=pk, silindi=False)
    fis = kdv_servis.fis_of(m)
    sonraki = kdv_servis.sonraki_mahsup(m)
    if sonraki is not None or fis is None:
        messages.error(request, (f"{kdv_servis.donem_adi(sonraki.yil, sonraki.ay)} dönemine ait mahsup var; önce onu silin (en yeni dönemden geriye doğru)."
                                 if sonraki is not None else "Mahsubun fişi bulunamadı."))
        return redirect("core:kdv_mahsup_listesi")
    return _fis_sil_akisi(
        request, fis, baslik=f"KDV dönem mahsubu · {kdv_servis.donem_adi(m.yil, m.ay)}", geri=reverse("core:kdv_mahsup_listesi"),
        sil=lambda: kdv_servis.sil(m, kullanici=request.user))


@never_cache
@ekran_gerekli("kdv_mahsup")
def kdv_mahsup_dosya(request, pk):
    """Beyanname PDF'ini (özel depoda) yetkili görünümden sunar — /media/ üzerinden DEĞİL."""
    m = get_object_or_404(KdvMahsup, pk=pk, silindi=False)
    if not m.dosya:
        raise Http404
    return _ozel_dosya_yanit(m.dosya, m.orijinal_ad)
