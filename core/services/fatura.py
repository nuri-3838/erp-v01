"""Fatura (FATURALAR) servis katmanı — Alış/Satış faturasından OTOMATİK yevmiye.

Fatura kaydedildiğinde dengeli bir yevmiye fişi üretilir (mevcut fis_olustur ile)
ve faturaya bağlanır. Muhasebe haritası:
  - Mal/gelir hesabı  = stok kategorisi × fatura tipi (KategoriHesap).
  - KDV hesabı         = stoğun KDV oranının BORÇ (alış 191) / ALACAK (satış 391) hesabı.
  - Karşı taraf        = carinin muhasebe hesabı (320.../120... yaprak).
ALIŞ:  Borç mal + Borç KDV  / Alacak cari.
GİDER (FaturaTipi.gider, yalnız ALIŞ): kalem STOK değil doğrudan bir GİDER HESABI (yaprak
  7xx/63x...; bkz. hesap_plani.gider_hesaplari) + satırda seçilen KDV oranı; kategori haritası,
  depo ve stok hareketi YOKTUR. Borç gider hesabı + Borç 191 KDV / Alacak cari.
SATIŞ: Alacak gelir + Alacak KDV / Borç cari.

İlk dilim: TL (kur=1). Tutarlar satırlardan; her şey atomik (eksik harita -> hiç kayıt yok).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (Cari, Depo, DuranVarlik, Fatura, FaturaSatir, FaturaTipi, HesapPlani,
                         KategoriHesap, KdvOrani, Kur, Stok, StokHareket, StokMaliyetKatmani,
                         StokMaliyetTuketimi, TeklifSiparis, TevkifatOrani, YatirimProjesi,
                         YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.cari import vade_hesapla
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services import donemsel_gider
from core.services import duran_hesap
from core.services import duran_varlik as dv_servis
from core.services.hesap_plani import (duran_varlik_hesaplari, gider_hesaplari,
                                       hesap_kodu_258_mi, hesap_kodu_duran_varlik_mi)
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_guncelle,
                                   fis_iptal, fis_olustur)

SIFIR = Decimal("0.00")

# Demirbaş (duran varlık) satışı: defter değeri üzerinden kâr 679'a, zarar 770.04'e; birikmiş amortisman 257'ye borç.
DEMIRBAS_KAR_HESABI = "679"
DEMIRBAS_ZARAR_HESABI = "770.04"
BIRIKMIS_AMORTISMAN_HESABI = "257"


class FaturaHatasi(ValueError):
    """Fatura kural ihlali (Türkçe mesaj)."""


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise FaturaHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise FaturaHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if not pozitif and d < 0:
        raise FaturaHatasi(f"{etiket} negatif olamaz.")
    return d


def aktif_faturalar():
    return (Fatura.objects.filter(silindi=False)
            .select_related("tip", "cari", "fis").order_by("-tarih", "-id"))


def _kur_coz(pb, tarih, cari=None):
    """Fatura para biriminin fiş tarihindeki TCMB kuru — carinin kur_tipi tercihine göre
    (bkz. Kur.deger); cari verilmezse MB Alış. TRY -> 1. Döviz için o tarihin KUR kaydı ve
    ilgili PB/kur tipi alanı dolu olmalı (carry-forward yok)."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    kur_tipi = cari.kur_tipi if cari else Cari.KurTipi.MB_ALIS
    deger = k.deger(pb, kur_tipi) if k else None
    if not deger:
        raise FaturaHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi çekmeden "
            f"döviz faturası kesilemez.")
    return deger


def _gider_ve_duran_varlik_hesaplari():
    """GİDER faturası kalem hesabı seçiminde izin verilen TAM küme: gider hesapları
    (7xx/65x/66x/68x) + duran varlık hesapları (253/254/255/258/260) — bkz.
    core.services.hesap_plani.gider_hesaplari / duran_varlik_hesaplari."""
    return gider_hesaplari() | duran_varlik_hesaplari() | duran_hesap.grup_hesaplari(duran_hesap.KART_AILELERI)


# Ortak adına (şahsi) alış: KDV'nin (191'e borçlanan TAM tutar) karşılığı bu hesaba
# alacak yazılır — şirket KDV'yi normal indirir ama ortak faturanın KDV DAHİL tamamını
# öder, aradaki KDV "FAZLA" (şirket için fazladan gelir) olarak burada izlenir.
FAZLA_KDV_HESAP_KODU = "602.01"


class MukerrerFaturaHatasi(FaturaHatasi):
    """Aynı cari + aynı fatura no zaten kayıtlı; ``fatura`` mevcut kaydı taşır."""

    def __init__(self, fatura):
        self.fatura = fatura
        super().__init__(
            f"Bu fatura zaten kayıtlı: {fatura.tarih:%d.%m.%Y}, "
            f"{fatura.genel_toplam} {fatura.para_birimi}")


def irsaliyeden_mi(fatura) -> bool:
    """Fatura bir (silinmemiş) İRSALİYE'ye bağlı mı? Öyleyse stok girişini İRSALİYE zaten
    yazmıştır — fatura yalnız muhasebe fişi üretir, stok hareketi YAZMAZ (çifte giriş olur).
    Hem ilk onayda hem ONAYLI faturanın düzenlenmesinde geçerlidir."""
    return fatura.kaynak_siparisler.filter(
        belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, silindi=False).exists()


def irsaliye_miktar_farklari(fatura) -> list:
    """İrsaliyeye bağlı faturada stok başına irsaliye miktarı ile fatura miktarı farklıysa
    [(stok, irsaliye_miktar, fatura_miktar)] döner (boşsa fark yok ya da irsaliyeli değil).
    Stok irsaliyeden girdiği için fatura miktarı stoğu ETKİLEMEZ — yalnız bilgi/uyarıdır."""
    from collections import defaultdict
    irsaliye = fatura.kaynak_siparisler.filter(
        belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, silindi=False).first()
    if irsaliye is None:
        return []
    ir, fa, stoklar = defaultdict(Decimal), defaultdict(Decimal), {}
    for k in irsaliye.kalemler.filter(silindi=False).select_related("stok"):
        ir[k.stok_id] += k.miktar
        stoklar[k.stok_id] = k.stok
    for s in fatura.satirlar.filter(silindi=False, stok__isnull=False).select_related("stok"):
        fa[s.stok_id] += s.miktar
        stoklar[s.stok_id] = s.stok
    return [(stoklar[i], ir[i], fa[i]) for i in stoklar
            if abs(ir[i] - fa[i]) > Decimal("0.0005")]


def _fatura_no_anahtar(fatura_no):
    """Büyük/küçük harf ve boşluk farkını yok sayan karşılaştırma anahtarı."""
    return buyuk_harf_tr("".join((fatura_no or "").split()))


def _mukerrer_alis_kontrol(*, cari, fatura_no, yon, haric_pk=None):
    """ALIŞ faturasında aynı cari + aynı fatura no (normalize) zaten varsa
    MukerrerFaturaHatasi. Silinmiş faturalar sayılmaz; ``haric_pk`` düzenlenen faturanın
    kendisini dışarıda tutar. Boş fatura no kontrol edilmez."""
    anahtar = _fatura_no_anahtar(fatura_no)
    if yon != FaturaTipi.Yon.ALIS or not anahtar:
        return
    adaylar = Fatura.objects.filter(cari=cari, yon=FaturaTipi.Yon.ALIS, silindi=False)
    if haric_pk:
        adaylar = adaylar.exclude(pk=haric_pk)
    for f in adaylar.exclude(fatura_no=""):
        if _fatura_no_anahtar(f.fatura_no) == anahtar:
            raise MukerrerFaturaHatasi(f)


# Serbest meslek makbuzu gibi GV stopajlı faturada kesilen stopaj bu hesaba alacak yazılır
# (360 Ödenecek Vergi ve Fonlar altında, bkz. FaturaTipi.stopajli).
GV_STOPAJ_HESAP_KODU = "360.20"


def _gv_stopaj_hesabi():
    hesap = HesapPlani.objects.filter(hesap_kodu=GV_STOPAJ_HESAP_KODU, silindi=False).first()
    if hesap is None:
        raise FaturaHatasi(
            f"{GV_STOPAJ_HESAP_KODU} (Ödenecek GV Stopajı) hesabı tanımlı değil; önce hesap "
            f"planında açılmalı.")
    return hesap


def _stopaj_orani_coz(tip, oran):
    """Stopajlı tipte oran ZORUNLU (0–100); diğer tiplerde yok sayılır (None)."""
    if tip is None or not getattr(tip, "stopajli", False):
        return None
    if oran is None or oran == "":
        raise FaturaHatasi("GV stopaj oranını girin.")
    oran = _sayi(oran, "GV stopaj oranı")
    if oran > 100:
        raise FaturaHatasi("GV stopaj oranı 100'den büyük olamaz.")
    return oran


def _fazla_kdv_hesabi():
    hesap = HesapPlani.objects.filter(hesap_kodu=FAZLA_KDV_HESAP_KODU, silindi=False).first()
    if hesap is None:
        raise FaturaHatasi(
            f"{FAZLA_KDV_HESAP_KODU} (FAZLA KDV) hesabı tanımlı değil; önce hesap "
            f"planında açılmalı.")
    return hesap


def _ortak_hesabi_coz(sahsi_ortak_id):
    """'Ortak adına (şahsi) alış' için seçilen hesabı çözer — bkz.
    core.services.hesap_plani.ortak_hesaplari (131 ailesi, yaprak)."""
    from core.services.hesap_plani import ortak_hesaplari
    hesap = ortak_hesaplari().filter(pk=sahsi_ortak_id).first() if sahsi_ortak_id else None
    if hesap is None:
        raise FaturaHatasi(
            "Ortak adına şahsi alış için geçerli bir ortak hesabı seçin (131 ailesi).")
    return hesap


def _tevkifat_coz(g, i, *, stok_varsayilani=None):
    """``g["tevkifat_yok"]`` True ise (kullanıcı seçicide "Yok"u AÇIKÇA seçti) stok
    kartında tanımlı olsa bile None döner. ``g["tevkifat_id"]`` doluysa o oran kullanılır.
    İkisi de yoksa (boş seçim) ``stok_varsayilani`` döner — stoklu kalemde stok kartının
    GÜNCEL tevkifatı, gider kaleminde zaten None (varsayılan yok)."""
    if g.get("tevkifat_yok"):
        return None
    tevkifat_id = g.get("tevkifat_id")
    if not tevkifat_id:
        return stok_varsayilani
    tevkifat = TevkifatOrani.objects.filter(pk=tevkifat_id, silindi=False).first()
    if tevkifat is None:
        raise FaturaHatasi(f"Satır {i}: tevkifat oranı bulunamadı.")
    return tevkifat


def _satis_hesap_kumesi():
    """SATIŞ faturasındaki "Hesap satırı" için izin verilen TAM küme: gider hesapları (7xx/65x/66x/68x) +
    258 Yapılmakta Olan Yatırımlar ailesi (alış iade faturaları: gideri/maliyeti azaltır). Diğer duran varlık
    hesapları (253/254/255/260) BURADA yoktur — duran varlık satışı "Demirbaş satırı" ile yapılır."""
    return (gider_hesaplari() | duran_varlik_hesaplari().filter(
        Q(hesap_kodu="258") | Q(hesap_kodu__startswith="258.")))


def _hesap_satiri_coz(g, i, hesap_kumesi, *, kullanici=None, tarih=None):
    """Gider/duran varlık/satış hesap satırı: (hesap, kdv, tevkifat, proje).

    * 258 ailesi: proje ZORUNLU, aktifleşmiş proje seçilemez; satır PROJENİN kendi hesabına (258.0X.000N) yazılır.
    * 253/254/255/260: GRUP (ör. 253.01) seçilirse bu kalem için YENİ KART + sıradaki 000N hesabı otomatik açılır (kalem = kart;
      kart adı ``varlik_adi``, boşsa grup adı); kartın hesabı (253.01.0001) seçilirse kalem O KARTA eklenir (yeni kart açılmaz).
    * Gider hesabında proje yok."""
    hesap = (hesap_kumesi.filter(pk=g.get("hesap_id")).first() if g.get("hesap_id") else None)
    if hesap is None:
        raise FaturaHatasi(
            f"Satır {i}: geçerli bir gider veya duran varlık hesabı seçin "
            f"(yaprak hesap olmalı).")
    kdv = None
    if g.get("kdv_id"):
        kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
        if kdv is None:
            raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
    tevkifat = _tevkifat_coz(g, i)
    proje = None
    if hesap_kodu_duran_varlik_mi(hesap.hesap_kodu):
        proje_id = g.get("yatirim_projesi_id")
        if proje_id:
            proje = YatirimProjesi.objects.filter(pk=proje_id, silindi=False).first()
            if proje is None:
                raise FaturaHatasi(f"Satır {i}: yatırım projesi bulunamadı.")
            if proje.durum != YatirimProjesi.Durum.DEVAM:
                durum_ad = "kapanmış" if proje.durum == YatirimProjesi.Durum.KAPANDI else "aktifleşmiş"
                raise FaturaHatasi(
                    f"Satır {i}: {proje.kod} projesi {durum_ad}; yeni kalem eklenemez.")
        elif hesap_kodu_258_mi(hesap.hesap_kodu):
            raise FaturaHatasi(
                f"Satır {i}: {hesap.hesap_kodu} hesabı için yatırım projesi seçimi "
                f"zorunludur.")
        if proje is not None and hesap_kodu_258_mi(hesap.hesap_kodu) and proje.hesap_id:
            hesap = proje.hesap                         # satır projenin hesabına yazılır
        elif duran_hesap.grup_mu(hesap.hesap_kodu) and not hesap_kodu_258_mi(hesap.hesap_kodu):
            ad = (g.get("varlik_adi") or "").strip() or hesap.hesap_adi
            try:
                hesap = dv_servis.kart_ac(hesap.hesap_kodu, ad, tarih=tarih or timezone.localdate(),
                                          kullanici=kullanici).hesap
            except dv_servis.DuranVarlikHatasi as e:
                raise FaturaHatasi(f"Satır {i}: {e}")
        elif duran_hesap.varlik_hesabi_mi(hesap.hesap_kodu) and not hesap_kodu_258_mi(hesap.hesap_kodu):
            kart = dv_servis.hesaptaki_kart(hesap.hesap_kodu)
            if kart is not None and kart.durum == DuranVarlik.Durum.SATILDI:
                raise FaturaHatasi(f"Satır {i}: {kart.demirbas_kodu} kartı satılmış; ek maliyet yazılamaz.")
    return hesap, kdv, tevkifat, proje


def _donemsel_satir_coz(g, i, *, kullanici=None, fatura_no=""):
    """Dönemsel gider satırı (alış gider faturası): 180.xx.000N hesabı (faturaya özel; düzenlemede mevcut hesap korunur) + gider hesabı + dönem.
    (None, hesap180, kdv, tevkifat, None, None) döner; hesap180._donem = gider/dönem bilgisi (bkz. _satirlari_yaz)."""
    gider_h = gider_hesaplari().filter(pk=g.get("hesap_id")).first() if g.get("hesap_id") else None
    if gider_h is None:
        raise FaturaHatasi(f"Satır {i}: dönemsel gider için gider hesabı (7xx/65x/66x/68x yaprak) seçin.")
    baslangic, bitis = g.get("donem_baslangic"), g.get("donem_bitis")
    try:
        donemsel_gider.ay_sonlari(baslangic, bitis)
    except donemsel_gider.DonemselGiderHatasi as e:
        raise FaturaHatasi(f"Satır {i}: {e}")
    aciklama = (g.get("donem_aciklama") or "").strip()[:200]
    h180 = None
    if g.get("donem_hesap_id"):
        h180 = HesapPlani.objects.filter(pk=g["donem_hesap_id"], silindi=False).first()
        if h180 is None or not donemsel_gider.HESAP_180.match(h180.hesap_kodu):
            raise FaturaHatasi(f"Satır {i}: dönemsel gider 180 hesabı geçersiz.")
    else:
        try:
            h180 = donemsel_gider.hesap_ac(g.get("donem_grup"), fatura_no, aciklama, kullanici=kullanici)
        except donemsel_gider.DonemselGiderHatasi as e:
            raise FaturaHatasi(f"Satır {i}: {e}")
    kdv = None
    if g.get("kdv_id"):
        kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
        if kdv is None:
            raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
    h180._donem = {"baslangic": baslangic, "bitis": bitis, "gider": gider_h, "aciklama": aciklama}
    return None, h180, kdv, _tevkifat_coz(g, i), None, None


def _donemsel_senkronla(fatura, kullanici):
    try:
        donemsel_gider.senkronla(fatura, kullanici)
    except donemsel_gider.DonemselGiderHatasi as e:
        raise FaturaHatasi(str(e))


def _demirbas_coz(g, i, *, fatura_pk=None):
    """Demirbaş satırı: satılabilir (AKTİF, silinmemiş; bu fatura zaten satmışsa SATILDI da olur) kartı çözer."""
    dv = DuranVarlik.objects.filter(pk=g.get("demirbas_id"), silindi=False).first() if g.get("demirbas_id") else None
    if dv is None:
        raise FaturaHatasi(f"Satır {i}: satılacak demirbaş (duran varlık kartı) seçin.")
    kendi = (dv.durum == DuranVarlik.Durum.SATILDI and fatura_pk is not None and dv.satis_faturasi_id == fatura_pk)
    if dv.durum != DuranVarlik.Durum.AKTIF and not kendi:
        raise FaturaHatasi(
            f"Satır {i}: {dv.demirbas_kodu} kartı satılamaz (durum: {dv.get_durum_display()}); "
            f"yalnız aktif, satılmamış kartlar satılabilir.")
    if dv.birikmis_amortisman > dv.maliyet:
        raise FaturaHatasi(f"Satır {i}: {dv.demirbas_kodu} kartının birikmiş amortismanı maliyetinden büyük.")
    return dv


def _satir_coz(g, i, gider, *, sahsi_ortak=None, satis=False, fatura_pk=None, kullanici=None, tarih=None, fatura_no=""):
    """Girdi satırını çözer -> (stok, hesap, kdv, tevkifat, proje, demirbas).

    Satır türü (``g["tur"]``: STOK / HESAP / DEMIRBAS; verilmezse alanlardan türetilir):
      * GİDER faturası (``gider``): kalem GİDER/DURAN VARLIK HESABI + KDV + opsiyonel tevkifat (eski davranış).
      * Diğer tiplerde STOK (KDV stoktan; tevkifat stoktan, `tevkifat_yok`/`tevkifat_id` ile ezilebilir).
      * SATIŞ yönünde (``satis``) ek olarak HESAP satırı (alış iade faturası: gider/258 hesabı ALACAK) ve
        DEMİRBAŞ satırı (aktif duran varlık kartı satışı).
    Karışıklık reddedilir (UI'a güvenilmez). ``sahsi_ortak`` doluysa (Ortak adına şahsi alış) satırın hesabı
    SUBMIT EDİLEN DEĞERDEN BAĞIMSIZ olarak doğrudan bu hesaba sabitlenir (131 ailesi)."""
    tur = (g.get("tur") or "").strip().upper()
    if not tur:
        tur = "DEMIRBAS" if g.get("demirbas_id") else ("HESAP" if g.get("hesap_id") else "STOK")
    if tur not in ("STOK", "HESAP", "DEMIRBAS"):
        raise FaturaHatasi(f"Satır {i}: geçersiz satır türü.")
    if gider:
        if g.get("stok_id") or g.get("demirbas_id"):
            raise FaturaHatasi(
                f"Satır {i}: gider faturasında stok kullanılamaz; gider hesabı seçin.")
        if g.get("donemsel"):
            if sahsi_ortak is not None:
                raise FaturaHatasi(f"Satır {i}: ortak adına (şahsi) alışta dönemsel gider kullanılamaz.")
            return _donemsel_satir_coz(g, i, kullanici=kullanici, fatura_no=fatura_no)
        if sahsi_ortak is not None:
            kdv = None
            if g.get("kdv_id"):
                kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
                if kdv is None:
                    raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
            tevkifat = _tevkifat_coz(g, i)
            return None, sahsi_ortak, kdv, tevkifat, None, None
        hesap, kdv, tevkifat, proje = _hesap_satiri_coz(g, i, _gider_ve_duran_varlik_hesaplari(),
                                                        kullanici=kullanici, tarih=tarih)
        return None, hesap, kdv, tevkifat, proje, None
    if tur == "HESAP":
        if not satis:
            raise FaturaHatasi(
                f"Satır {i}: gider hesabı yalnız gider faturası tipinde (ya da satış faturasında hesap "
                f"satırı olarak) kullanılabilir.")
        if g.get("stok_id") or g.get("demirbas_id"):
            raise FaturaHatasi(f"Satır {i}: hesap satırında stok/demirbaş seçilemez.")
        hesap, kdv, tevkifat, proje = _hesap_satiri_coz(g, i, _satis_hesap_kumesi(), kullanici=kullanici, tarih=tarih)
        return None, hesap, kdv, tevkifat, proje, None
    if tur == "DEMIRBAS":
        if not satis:
            raise FaturaHatasi(f"Satır {i}: demirbaş satışı yalnız satış faturasında yapılabilir.")
        if g.get("stok_id") or g.get("hesap_id"):
            raise FaturaHatasi(f"Satır {i}: demirbaş satırında stok/hesap seçilemez.")
        dv = _demirbas_coz(g, i, fatura_pk=fatura_pk)
        kdv = None
        if g.get("kdv_id"):
            kdv = KdvOrani.objects.filter(pk=g["kdv_id"], silindi=False).first()
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: KDV oranı bulunamadı.")
        return None, dv.hesap, kdv, _tevkifat_coz(g, i), None, dv
    if g.get("hesap_id") or g.get("demirbas_id"):
        raise FaturaHatasi(
            f"Satır {i}: stok satırında hesap/demirbaş seçilemez.")
    stok = (Stok.objects.filter(pk=g.get("stok_id"), silindi=False)
            .select_related("kategori", "kdv", "tevkifat").first())
    if stok is None:
        raise FaturaHatasi(f"Satır {i}: stok bulunamadı.")
    tevkifat = _tevkifat_coz(g, i, stok_varsayilani=stok.tevkifat)
    return stok, None, stok.kdv, tevkifat, None, None


def _demirbas_satirlari(dv, satis_tl):
    """Demirbaş satışının TL yevmiye satırları: 25x hesabı kart maliyetiyle ALACAK, birikmiş amortisman (257)
    varsa BORÇ, defter değeri ile satış bedeli (KDV hariç) farkı: kâr → 679 ALACAK, zarar → 770.04 BORÇ.
    (satırlar, borç_tl, alacak_tl) döner; cari/KDV satırları çağıranda."""
    maliyet = yuvarla(dv.maliyet, 2)
    amort = yuvarla(dv.birikmis_amortisman or SIFIR, 2)
    fark = yuvarla(satis_tl, 2) - (maliyet - amort)
    satirlar, borc, alacak = [], SIFIR, SIFIR

    def _tl(kod, taraf, tutar, ack):
        if not HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).exists():
            raise FaturaHatasi(f"{kod} hesabı hesap planında tanımlı değil; demirbaş satışı için önce açılmalı.")
        satirlar.append(SatirGirdi(hesap_kodu=kod, taraf=taraf, islem_tutari=tutar, islem_pb="TRY",
                                   islem_kuru=Decimal("1"), aciklama=ack))
    _tl(dv.hesap_id, "A", maliyet, f"{dv.demirbas_kodu} MALİYET")
    alacak += maliyet
    if amort > 0:
        _tl(BIRIKMIS_AMORTISMAN_HESABI, "B", amort, f"{dv.demirbas_kodu} BİRİKMİŞ AMORTİSMAN")
        borc += amort
    if fark > 0:
        _tl(DEMIRBAS_KAR_HESABI, "A", fark, f"{dv.demirbas_kodu} SATIŞ KÂRI")
        alacak += fark
    elif fark < 0:
        _tl(DEMIRBAS_ZARAR_HESABI, "B", -fark, f"{dv.demirbas_kodu} SATIŞ ZARARI")
        borc += -fark
    return satirlar, borc, alacak


def _demirbas_satildi_yaz(fatura, kullanici=None):
    """ONAYLI satış faturasındaki demirbaş satırlarının kartlarını SATILDI yapar (tarih + fatura bağı)."""
    for s in fatura.satirlar.filter(silindi=False, demirbas__isnull=False).select_related("demirbas"):
        dv = s.demirbas
        dv.durum, dv.satis_tarihi, dv.satis_faturasi = DuranVarlik.Durum.SATILDI, fatura.tarih, fatura
        dv.updated_by = kullanici
        dv.save(update_fields=["durum", "satis_tarihi", "satis_faturasi", "updated_by", "updated_at"])


def _demirbas_geri_al(fatura, kullanici=None):
    """Bu faturayla satılmış kartları eski durumuna (AKTİF) döndürür; fatura silinince/düzenlenince çağrılır."""
    for dv in DuranVarlik.objects.filter(satis_faturasi=fatura, durum=DuranVarlik.Durum.SATILDI):
        dv.durum, dv.satis_tarihi, dv.satis_faturasi = DuranVarlik.Durum.AKTIF, None, None
        dv.updated_by = kullanici
        dv.save(update_fields=["durum", "satis_tarihi", "satis_faturasi", "updated_by", "updated_at"])


def _hazirla(*, tip_id, cari_id, tarih, satirlar, para_birimi, kur_override=None,
             sahsi_ortak_id=None, gv_stopaj_orani=None, fatura_pk=None, kullanici=None, fatura_no=""):
    """Ortak hazırlık (oluştur+güncelle): doğrula, kur çöz, yevmiye satırlarını ve
    FaturaSatir verisini kur. (tip, cari, pb, kur, yevmiye_satirlari, hazir) döner.
    ``kur_override`` doluysa (kullanıcı elle girdi/değiştirdi) carinin kur_tipi'ne göre
    otomatik hesaplama YERİNE doğrudan kullanılır. ``sahsi_ortak_id`` doluysa (Ortak adına
    şahsi alış) her satırın hesabı ortak hesabına sabitlenir; mal satırı KDV DAHİL (gross)
    tutarla o hesaba borçlanır ve KDV'nin aynı tutarı FAZLA_KDV_HESAP_KODU'na alacak
    yazılır (bkz. core.services.fatura modül docstring'i ve _satir_coz)."""
    tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first()
    if tip is None:
        raise FaturaHatasi("Fatura tipi bulunamadı.")
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FaturaHatasi("Cari bulunamadı.")
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")

    # Carinin muhasebe (yaprak) hesabı
    cari_hesap = HesapPlani.objects.filter(
        hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if cari_hesap is None:
        raise FaturaHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")

    alis = (tip.yon == FaturaTipi.Yon.ALIS)
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise FaturaHatasi("Geçersiz para birimi.")
    kur = Decimal("1") if pb == "TRY" else (kur_override or _kur_coz(pb, tarih, cari=cari))

    if sahsi_ortak_id and not (tip.gider and alis):
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_ortak_id else None
    sahsi = sahsi_ortak is not None
    stopaj_orani = _stopaj_orani_coz(tip, gv_stopaj_orani)
    matrah_toplam = SIFIR

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}          # hesap_kodu -> KDV tutarı (PB) [alışta tam, satışta net]
    tevkifat_hesap_toplam = {}     # hesap_kodu -> tevkifat tutarı (PB) [yalnız ALIŞ -> 360]
    fazla_kdv_toplam = SIFIR       # yalnız şahsi alış: 602.01'e alacak
    borc_tl = SIFIR               # cari HARİÇ borç satırlarının TL toplamı
    alacak_tl = SIFIR             # cari HARİÇ alacak satırlarının TL toplamı
    cari_pb = SIFIR               # carinin PB tutarı = mal + (KDV − tevkifat) − GV stopajı
    hazir = []                     # FaturaSatir için (stok, hesap, miktar, fiyat, kdv, tevkifat)

    def _ekle(taraf, tutar_pb):
        nonlocal borc_tl, alacak_tl
        tl = yuvarla(tutar_pb * kur, 2)
        if taraf == "B":
            borc_tl += tl
        else:
            alacak_tl += tl

    if tip.gider and not alis:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")

    demirbas_idler = set()
    for i, g in enumerate(satirlar, start=1):
        stok, hesap, kdv, tevkifat, proje, dv = _satir_coz(
            g, i, tip.gider, sahsi_ortak=sahsi_ortak, satis=not alis, fatura_pk=fatura_pk,
            kullanici=kullanici, tarih=tarih, fatura_no=fatura_no)
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")
        if dv is not None:
            if dv.pk in demirbas_idler:
                raise FaturaHatasi(f"Satır {i}: {dv.demirbas_kodu} aynı faturada iki kez satılamaz.")
            demirbas_idler.add(dv.pk)
            miktar = Decimal("1")                  # kart tek birimdir; birim fiyat = satış bedeli

        # Mal/gider hesabı: gider faturasında satırın kendi gider hesabı; diğer tiplerde
        # kategori × fatura tipi haritası
        if hesap is not None:
            mal_kodu, mal_ad, etiket = hesap.hesap_kodu, hesap.hesap_adi, hesap.hesap_kodu
        else:
            kh = KategoriHesap.objects.filter(
                kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
            if kh is None:
                raise FaturaHatasi(
                    f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                    f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")
            mal_kodu, mal_ad, etiket = kh.hesap.hesap_kodu, stok.ad, stok.kod

        satir_tutar = yuvarla(miktar * birim, 2)
        oran = kdv.oran if kdv else SIFIR
        satir_kdv = yuvarla(satir_tutar * oran / Decimal("100"), 2)

        # Tevkifat (varsa): KDV'nin pay/payda kadarı
        tev = SIFIR
        if tevkifat and tevkifat.payda and satir_kdv > 0:
            tev = yuvarla(satir_kdv * Decimal(tevkifat.pay) / Decimal(tevkifat.payda), 2)
        kdv_net = satir_kdv - tev      # cariye yansıyan KDV

        # Mal/gider/gelir satırı (alış: Borç, satış: Alacak). Şahsi alışta KDV DAHİL
        # (gross) tutarla ortak hesabına borçlanır — ortak faturanın tamamını öder.
        if dv is not None:
            # Demirbaş satışı: 25x maliyet alacak, 257 borç, kâr 679 / zarar 770.04 (TL; cari TL'si denge satırı).
            dv_satirlar, dv_b, dv_a = _demirbas_satirlari(dv, yuvarla(satir_tutar * kur, 2))
            yevmiye_satirlari.extend(dv_satirlar)
            borc_tl += dv_b
            alacak_tl += dv_a
        else:
            mal_taraf = "B" if alis else "A"
            mal_tutar = (satir_tutar + satir_kdv) if sahsi else satir_tutar
            _ekle(mal_taraf, mal_tutar)
            yevmiye_satirlari.append(SatirGirdi(
                hesap_kodu=mal_kodu, taraf=mal_taraf,
                islem_tutari=mal_tutar, islem_pb=pb, islem_kuru=kur, aciklama=mal_ad))

        # KDV hesabı — ALIŞ: 191 TAM KDV (borç); SATIŞ: 391 NET KDV (alacak)
        kdv_post = satir_kdv if alis else kdv_net
        if kdv_post > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {etiket} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + kdv_post)
            if sahsi:
                fazla_kdv_toplam += kdv_post

        # Tevkifat — yalnız ALIŞ'ta 360'a (Ödenecek) alacak yazılır
        if alis and tev > 0:
            tev_hesap = tevkifat.hesap
            if tev_hesap is None:
                raise FaturaHatasi(
                    f"Satır {i}: {tevkifat.kod} tevkifatının muhasebe hesabı tanımlı "
                    f"değil (AYARLAR > Tevkifat Oranları).")
            tevkifat_hesap_toplam[tev_hesap.hesap_kodu] = (
                tevkifat_hesap_toplam.get(tev_hesap.hesap_kodu, SIFIR) + tev)

        cari_pb += satir_tutar + kdv_net
        matrah_toplam += satir_tutar
        hazir.append((stok, hesap, miktar, birim, kdv, tevkifat, proje, dv))

    # KDV satırları (alış: Borç, satış: Alacak)
    for hkod, tutar in kdv_hesap_toplam.items():
        kdv_taraf = "B" if alis else "A"
        _ekle(kdv_taraf, tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf=kdv_taraf,
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV"))

    # Tevkifat satırları (ALIŞ -> 360 Alacak)
    for hkod, tutar in tevkifat_hesap_toplam.items():
        _ekle("A", tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf="A",
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV TEVKİFATI"))

    # Fazla KDV (yalnız şahsi alış) — 191'e borçlanan KDV kadar 602.01'e alacak.
    if fazla_kdv_toplam > 0:
        fazla_kdv_hesap = _fazla_kdv_hesabi()
        _ekle("A", fazla_kdv_toplam)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=fazla_kdv_hesap.hesap_kodu, taraf="A",
            islem_tutari=fazla_kdv_toplam, islem_pb=pb, islem_kuru=kur,
            aciklama="FAZLA KDV (ortak adına şahsi alış)"))

    # GV stopajı (yalnız stopajlı tip — serbest meslek makbuzu): brüt ücret × oran/100,
    # 360.xx'e alacak; cariden düşülür (vergi dairesine yatar, satıcıya ödenmez).
    stopaj = yuvarla(matrah_toplam * stopaj_orani / Decimal("100"), 2) if stopaj_orani else SIFIR
    if stopaj > 0:
        stopaj_hesap = _gv_stopaj_hesabi()
        _ekle("A", stopaj)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=stopaj_hesap.hesap_kodu, taraf="A",
            islem_tutari=stopaj, islem_pb=pb, islem_kuru=kur, aciklama="GV STOPAJI"))
        cari_pb -= stopaj

    # Karşı taraf (cari): alış -> Alacak, satış -> Borç. TL'si DENGE için diğer
    # satırların TL'sinden türetilir (tl_override) -> döviz kuruş farkı oluşmaz.
    cari_taraf = "A" if alis else "B"
    cari_tl = (borc_tl - alacak_tl) if cari_taraf == "A" else (alacak_tl - borc_tl)
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
        islem_tutari=cari_pb, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan,
        tl_override=cari_tl))

    return tip, cari, pb, kur, yevmiye_satirlari, hazir


def _hazirla_taslak(*, cari_id, satirlar, para_birimi, gider=False, sahsi_ortak_id=None, satis=False,
                    fatura_pk=None, kullanici=None, tarih=None, fatura_no=""):
    """Taslak oluştur/güncelle ortak hazırlığı: cari + satırları doğrular — TİP'e ihtiyaç
    DUYMAZ (muhasebe haritası + yevmiye satırları onaylamaya ertelenir, bkz. fatura_onayla).
    KDV stoktan, tevkifat ise satırda elle seçilmemişse stoktan (seçilmişse formdan) bu anda
    (taslak anında) çekilip FaturaSatir'e SNAPSHOT yazılır. Gider faturasında (gider=True)
    kalemler stok değil gider/duran-varlık hesabıdır (+ satırda seçilen KDV + opsiyonel
    tevkifat, bkz. _satir_coz). ``sahsi_ortak_id`` doluysa (Ortak adına şahsi alış) her
    satırın hesabı o ortak hesabına sabitlenir. (cari, pb, hazir) döner — hazir =
    [(stok, hesap, miktar, fiyat, kdv, tevkifat, proje), ...]."""
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FaturaHatasi("Cari bulunamadı.")
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise FaturaHatasi("Geçersiz para birimi.")

    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_ortak_id else None
    hazir = []
    demirbas_idler = set()
    for i, g in enumerate(satirlar, start=1):
        stok, hesap, kdv, tevkifat, proje, dv = _satir_coz(
            g, i, gider, sahsi_ortak=sahsi_ortak, satis=satis, fatura_pk=fatura_pk,
            kullanici=kullanici, tarih=tarih, fatura_no=fatura_no)
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")
        if dv is not None:
            if dv.pk in demirbas_idler:
                raise FaturaHatasi(f"Satır {i}: {dv.demirbas_kodu} aynı faturada iki kez satılamaz.")
            demirbas_idler.add(dv.pk)
            miktar = Decimal("1")
        hazir.append((stok, hesap, miktar, birim, kdv, tevkifat, proje, dv))
    return cari, pb, hazir


def _muhasebe_satirlari(fatura, tip, cari, pb, kur):
    """fatura_onayla için: yevmiye satırlarını RAM'deki girdiden değil, faturaya zaten
    YAZILMIŞ FaturaSatir satırlarının KDV/tevkifat SNAPSHOT'ından kurar — taslak ile onay
    arasında stoğun KDV oranı değişse bile kullanıcının gördüğü taslak tutarlar korunur."""
    alis = (tip.yon == FaturaTipi.Yon.ALIS)
    cari_hesap = HesapPlani.objects.filter(
        hesap_kodu=cari.muhasebe_kodu, silindi=False).first() if cari.muhasebe_kodu else None
    if cari_hesap is None:
        raise FaturaHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")

    satirlar = list(fatura.satirlar.filter(silindi=False)
                    .select_related("stok__kategori", "hesap", "kdv", "tevkifat"))
    if not satirlar:
        raise FaturaHatasi("Faturada en az bir satır olmalı.")

    sahsi = bool(fatura.sahsi_alis and fatura.sahsi_ortak_id)
    stopaj_orani = fatura.gv_stopaj_orani if getattr(tip, "stopajli", False) else None
    if getattr(tip, "stopajli", False) and not stopaj_orani:
        raise FaturaHatasi("GV stopaj oranını girin.")
    matrah_toplam = SIFIR
    if sahsi and not (tip.gider and alis):
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}
    tevkifat_hesap_toplam = {}
    fazla_kdv_toplam = SIFIR       # yalnız şahsi alış: 602.01'e alacak
    borc_tl = SIFIR
    alacak_tl = SIFIR
    cari_pb = SIFIR

    def _ekle(taraf, tutar_pb):
        nonlocal borc_tl, alacak_tl
        tl = yuvarla(tutar_pb * kur, 2)
        if taraf == "B":
            borc_tl += tl
        else:
            alacak_tl += tl

    if tip.gider and not alis:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")

    for i, satir in enumerate(satirlar, start=1):
        dv = satir.demirbas if satir.demirbas_id else None
        if tip.gider:
            if satir.hesap_id is None or dv is not None:
                raise FaturaHatasi(
                    f"Satır {i}: kalem türü fatura tipiyle uyuşmuyor "
                    f"(gider faturasında gider hesabı olmalı).")
        elif satir.hesap_id is not None and alis:
            raise FaturaHatasi(
                f"Satır {i}: kalem türü fatura tipiyle uyuşmuyor "
                f"(hesap/demirbaş satırı yalnız satış faturasında olabilir).")
        if satir.hesap_id:
            mal_kodu, mal_ad, etiket = satir.hesap.hesap_kodu, satir.hesap.hesap_adi, satir.hesap.hesap_kodu
        else:
            stok = satir.stok
            kh = KategoriHesap.objects.filter(
                kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
            if kh is None:
                raise FaturaHatasi(
                    f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                    f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")
            mal_kodu, mal_ad, etiket = kh.hesap.hesap_kodu, stok.ad, stok.kod

        satir_tutar = satir.tutar
        kdv = satir.kdv
        satir_kdv = satir.kdv_tutari
        tevkifat = satir.tevkifat
        tev = satir.tevkifat_tutari
        kdv_net = satir_kdv - tev

        if dv is not None:
            # Demirbaş satışı: 25x maliyet alacak, 257 borç, kâr 679 / zarar 770.04 (TL; cari TL'si denge satırı).
            dv_satirlar, dv_b, dv_a = _demirbas_satirlari(dv, yuvarla(satir_tutar * kur, 2))
            yevmiye_satirlari.extend(dv_satirlar)
            borc_tl += dv_b
            alacak_tl += dv_a
        else:
            mal_taraf = "B" if alis else "A"
            mal_tutar = (satir_tutar + satir_kdv) if sahsi else satir_tutar
            _ekle(mal_taraf, mal_tutar)
            yevmiye_satirlari.append(SatirGirdi(
                hesap_kodu=mal_kodu, taraf=mal_taraf,
                islem_tutari=mal_tutar, islem_pb=pb, islem_kuru=kur, aciklama=mal_ad))

        kdv_post = satir_kdv if alis else kdv_net
        if kdv_post > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {etiket} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{kdv.oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + kdv_post)
            if sahsi:
                fazla_kdv_toplam += kdv_post

        if alis and tev > 0:
            tev_hesap = tevkifat.hesap
            if tev_hesap is None:
                raise FaturaHatasi(
                    f"Satır {i}: {tevkifat.kod} tevkifatının muhasebe hesabı tanımlı "
                    f"değil (AYARLAR > Tevkifat Oranları).")
            tevkifat_hesap_toplam[tev_hesap.hesap_kodu] = (
                tevkifat_hesap_toplam.get(tev_hesap.hesap_kodu, SIFIR) + tev)

        cari_pb += satir_tutar + kdv_net
        matrah_toplam += satir_tutar

    for hkod, tutar in kdv_hesap_toplam.items():
        kdv_taraf = "B" if alis else "A"
        _ekle(kdv_taraf, tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf=kdv_taraf,
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV"))

    if fazla_kdv_toplam > 0:
        fazla_kdv_hesap = _fazla_kdv_hesabi()
        _ekle("A", fazla_kdv_toplam)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=fazla_kdv_hesap.hesap_kodu, taraf="A",
            islem_tutari=fazla_kdv_toplam, islem_pb=pb, islem_kuru=kur,
            aciklama="FAZLA KDV (ortak adına şahsi alış)"))

    for hkod, tutar in tevkifat_hesap_toplam.items():
        _ekle("A", tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=hkod, taraf="A",
            islem_tutari=tutar, islem_pb=pb, islem_kuru=kur, aciklama="KDV TEVKİFATI"))

    # GV stopajı (yalnız stopajlı tip — serbest meslek makbuzu): brüt ücret × oran/100,
    # 360.xx'e alacak; cariden düşülür (vergi dairesine yatar, satıcıya ödenmez).
    stopaj = yuvarla(matrah_toplam * stopaj_orani / Decimal("100"), 2) if stopaj_orani else SIFIR
    if stopaj > 0:
        stopaj_hesap = _gv_stopaj_hesabi()
        _ekle("A", stopaj)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=stopaj_hesap.hesap_kodu, taraf="A",
            islem_tutari=stopaj, islem_pb=pb, islem_kuru=kur, aciklama="GV STOPAJI"))
        cari_pb -= stopaj

    cari_taraf = "A" if alis else "B"
    cari_tl = (borc_tl - alacak_tl) if cari_taraf == "A" else (alacak_tl - borc_tl)
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
        islem_tutari=cari_pb, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan,
        tl_override=cari_tl))

    return yevmiye_satirlari


def _aciklama(tip, cari, fatura_no):
    return buyuk_harf_tr(f"{tip.ad} - {cari.unvan}" + (f" - {fatura_no}" if fatura_no else ""))


def _kartlari_yenile(fatura, kullanici):
    """Faturanın (silinmiş kalemler dahil) kart hesaplarındaki kart maliyetlerini hesap bakiyesine eşitler."""
    for kod in set(fatura.satirlar.values_list("hesap_id", flat=True)):
        if kod and duran_hesap.varlik_hesabi_mi(kod) and not duran_hesap.proje_hesabi_mi(kod):
            dv_servis.kart_maliyetini_yenile(kod, kullanici=kullanici)


def _satirlari_yaz(fatura, hazir, kullanici):
    for stok, hesap, miktar, birim, kdv, tevkifat, proje, demirbas in hazir:
        donem = getattr(hesap, "_donem", None) or {}       # dönemsel gider: gider hesabı + dönem (bkz. _donemsel_satir_coz)
        satir = FaturaSatir.objects.create(
            fatura=fatura, stok=stok, hesap=hesap, miktar=miktar, birim_fiyat=birim, kdv=kdv,
            tevkifat=tevkifat, yatirim_projesi=proje, demirbas=demirbas,
            donem_baslangic=donem.get("baslangic"), donem_bitis=donem.get("bitis"), donem_gider=donem.get("gider"),
            donem_aciklama=donem.get("aciklama", ""),
            created_by=kullanici, updated_by=kullanici)
        dv_servis.satiri_karta_bagla(satir, kullanici=kullanici)   # kalem = kart / mevcut karta ekle
    _kartlari_yenile(fatura, kullanici)


def _taslak_kur_coz(kur, para_birimi):
    """Taslakta kullanıcının girdiği kur (yalnız döviz faturada anlamlı): saklanır, onayda aynen
    kullanılır. Boş/TRY -> None (onayda sistem atar)."""
    if kur in (None, "") or para_birimi == "TRY":
        return None
    kur = Decimal(str(kur)) if not isinstance(kur, Decimal) else kur
    if kur <= 0:
        raise FaturaHatasi("Kur sıfırdan büyük olmalı.")
    return kur


def _depo_coz(depo_id):
    """depo_id boşsa None (hareket üretilmez); doluysa aktif depoyu çözer."""
    if depo_id in (None, ""):
        return None
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise FaturaHatasi("Depo bulunamadı.")
    return depo


def _hareketleri_yaz(fatura, depo, *, kur, kullanici):
    """Fatura kalemleri için stok hareketi: ALIŞ→giriş, SATIŞ→çıkış. Miktar fatura
    biriminden üretim birimine çevrilir (çevirici). Çıkışta eldeki yetmezse engellenir.
    ALIŞ yönünde ayrıca bir FIFO maliyet katmanı açılır: birim_maliyet_try = birim_fiyat
    (fatura para biriminde) × kur (TL karşılığı) × cevirici (1 üretim birimi kaç fatura
    birimi ediyorsa, üretim birimi o kadar daha pahalıdır — bkz. Stok.cevirici)."""
    alis = (fatura.tip.yon == FaturaTipi.Yon.ALIS)
    tur = StokHareket.Tur.GIRIS if alis else StokHareket.Tur.CIKIS
    for satir in fatura.satirlar.filter(silindi=False, stok__isnull=False).select_related("stok"):
        cevirici = satir.stok.cevirici or Decimal("1")
        uretim_miktar = yuvarla(satir.miktar / cevirici, 3)
        if uretim_miktar <= 0:
            raise FaturaHatasi(
                f"{satir.stok.kod}: çevirici ({cevirici}) ile dönüştürülen miktar "
                f"sıfır oluyor; miktarı veya çeviriciyi düzeltin.")
        # Ağırlıklı ortalama maliyet: giriş tutarı = fatura satır tutarı (TL, KDV/tevkifat hariç).
        # Alış iadesi (tedarikçiye iade) çıkışı da ortalamayla DEĞİL iade faturasının tutarıyla değerlenir.
        alis_iade = (not alis) and fatura.tip.maliyet_fisi == FaturaTipi.MaliyetFisi.ALIS_IADE
        giris_tl = satir.tutar_tl if (alis or alis_iade) else None
        giris_usd = None
        if giris_tl is not None:
            usd_kuru = fatura.fis.kur_usd if fatura.fis_id else None
            giris_usd = yuvarla(giris_tl / usd_kuru, 2) if usd_kuru else None
        # Satış iadesi tipi: giriş fatura (satış) fiyatıyla DEĞİL o anki ortalama maliyetle değerlenir.
        iade = alis and fatura.tip.maliyet_fisi == FaturaTipi.MaliyetFisi.SATIS_IADE
        if iade:
            giris_tl = giris_usd = None
        try:
            hareket_ekle(
                stok_id=satir.stok_id, depo_id=depo.pk, tarih=fatura.tarih, tur=tur,
                miktar=uretim_miktar,
                aciklama=_aciklama(fatura.tip, fatura.cari, fatura.fatura_no),
                kaynak=StokHareket.Kaynak.FATURA, fatura_satir=satir, kullanici=kullanici,
                giris_tutar_try=giris_tl, giris_tutar_usd=giris_usd,
                maliyet_fatura_satir=satir if ((alis and not iade) or alis_iade) else None,
                giris_ortalama=iade)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))
    _maliyet_fisi_senkronla(fatura, kullanici)


def _maliyet_fisi_senkronla(fatura, kullanici=None):
    """Satış (çıkış) / satış iadesi (giriş) maliyet fişi — tip.maliyet_fisi doluysa."""
    if not fatura.tip.maliyet_fisi:
        return
    from core.services import stok_fis
    try:
        stok_fis.satis_senkronla(fatura, kullanici=kullanici)
    except stok_fis.MaliyetHatasi as e:
        raise FaturaHatasi(str(e))


def _irsaliye_girislerini_fiyatla(fatura):
    """İrsaliyeli faturada maliyeti FATURA belirler (bkz. core.services.stok_ortalama)."""
    from core.services import stok_ortalama
    try:
        stok_ortalama.irsaliye_girislerini_fiyatla(fatura)
    except stok_ortalama.MaliyetHatasi as e:
        raise FaturaHatasi(str(e))


def _irsaliye_girislerini_fiyatsiz_yap(fatura):
    from core.models import Stok
    from core.services import stok_ortalama
    for stok in Stok.objects.filter(pk__in=stok_ortalama.irsaliye_girislerini_sifirla(fatura)):
        try:
            stok_ortalama.yeniden_hesapla(stok)
        except stok_ortalama.MaliyetHatasi as e:
            raise FaturaHatasi(str(e))


def _hareketleri_iptal(fatura, *, kullanici):
    """Faturaya bağlı silinmemiş stok hareketlerini tek tek geri alır (hareket_sil
    üzerinden — ham toplu .update() DEĞİL, çünkü bir girişin maliyeti üretimde/satışta
    zaten tüketilmişse hareket_sil bunu tespit edip engelliyor; bkz. İrsaliye iptalindeki
    aynı desen, core/services/teklif_siparis.py)."""
    for hareket in StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False):
        try:
            hareket_sil(hareket, kullanici=kullanici)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))


def _fatura_hareket_ciftleri(fatura):
    """Faturanın aktif stok hareketlerinin (stok_id, depo_id) kümesi."""
    return set(StokHareket.objects.filter(
        fatura_satir__fatura=fatura, silindi=False).values_list("stok_id", "depo_id"))


def _negatif_eldeki_dogrula(ciftler):
    """Verilen (stok_id, depo_id) çiftlerinde eldeki negatife düştüyse hata. Bir alış
    faturası, malı satıldıktan sonra aşağı düzenlenince eldekinin eksiye düşmesini engeller
    (giriş `hareket_ekle`'de kontrol edilmez; bu güncelleme-sonrası backstop o açığı kapatır)."""
    for stok_id, depo_id in ciftler:
        if eldeki_miktar(stok_id, depo_id) < 0:
            raise FaturaHatasi(
                "Bu güncelleme bir stok+depoda eldeki miktarı negatife düşürüyor; "
                "önce o stoğun bağlı çıkış/satış hareketlerini düzeltin.")


@transaction.atomic
def fatura_taslak_olustur(*, cari_id, tarih, satirlar, tip_id=None, yon=None, fatura_no="",
                          para_birimi="TRY", depo_id=None, aciklama="", vade_tarihi=None,
                          sahsi_alis=False, sahsi_ortak_id=None, gv_stopaj_orani=None,
                          kullanici=None, kur=None) -> Fatura:
    """Faturayı TASLAK olarak oluşturur — fiş/stok hareketi ÜRETMEZ (bkz. fatura_onayla).
    tip_id verilirse yön ondan türetilir; verilmezse `yon` zorunludur (İrsaliye'den otomatik
    açılan, tipi henüz bilinmeyen taslaklar için). ``sahsi_alis``/``sahsi_ortak_id``: Ortak
    adına şahsi alış — yalnız alış-gider faturasında; kalemlerin hesabı ortak hesabına
    sabitlenir (bkz. _hazirla_taslak/_satir_coz)."""
    tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first() if tip_id else None
    gider = bool(tip and tip.gider)
    if sahsi_alis and not sahsi_ortak_id:
        raise FaturaHatasi("Ortak adına şahsi alış için bir ortak hesabı seçin.")
    if sahsi_alis and not gider:
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_alis else None
    satis = bool(tip and tip.yon == FaturaTipi.Yon.SATIS) or (tip is None and yon == FaturaTipi.Yon.SATIS)
    cari, pb, hazir = _hazirla_taslak(
        cari_id=cari_id, satirlar=satirlar, para_birimi=para_birimi, gider=gider, satis=satis,
        sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None), kullanici=kullanici, tarih=tarih, fatura_no=fatura_no)
    if tip is not None:
        cozulen_yon = tip.yon
    elif yon in FaturaTipi.Yon.values:
        cozulen_yon = yon
    else:
        raise FaturaHatasi("Fatura yönü belirlenemedi.")
    if gider and cozulen_yon != FaturaTipi.Yon.ALIS:
        raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")
    if sahsi_ortak is not None and cozulen_yon != FaturaTipi.Yon.ALIS:
        raise FaturaHatasi("Ortak adına şahsi alış yalnız alış yönünde olabilir.")
    depo = None if gider else _depo_coz(depo_id)        # gider faturasında depo/stok hareketi yok
    fatura_no = (fatura_no or "").strip()
    _mukerrer_alis_kontrol(cari=cari, fatura_no=fatura_no, yon=cozulen_yon)
    # Sunucu tarafı yedek: vade boş + carinin ödeme koşulu tanımlıysa otomatik hesapla
    # (ön yüz JS'i zaten doldurur; bu yalnız JS çalışmadıysa/atlandıysa devreye girer).
    if vade_tarihi is None:
        vade_tarihi = vade_hesapla(cari, tarih)
    fatura = Fatura.objects.create(
        tip=tip, yon=cozulen_yon, durum=Fatura.Durum.TASLAK, cari=cari, tarih=tarih,
        fatura_no=fatura_no, para_birimi=pb, kur=1, fis=None, depo=depo,
        aciklama=(aciklama or "").strip(), vade_tarihi=vade_tarihi,
        sahsi_alis=bool(sahsi_ortak), sahsi_ortak=sahsi_ortak,
        gv_stopaj_orani=_stopaj_orani_coz(tip, gv_stopaj_orani),
        taslak_kur=_taslak_kur_coz(kur, pb),
        created_by=kullanici, updated_by=kullanici)
    _satirlari_yaz(fatura, hazir, kullanici)
    return fatura


@transaction.atomic
def fatura_onayla(fatura: Fatura, kullanici=None, kur_override=None) -> Fatura:
    """TASLAK → ONAYLI: muhasebe haritasını (tip artık zorunlu) çözer, dengeli yevmiye
    fişini üretir ve — bu fatura bir İRSALİYE'den doğmadıysa (o zaten stokladı, çifte
    sayım olmasın) — depo verilmişse stok hareketlerini yazar. İdempotent (zaten onaylıysa
    sessiz). ``kur_override`` doluysa (kullanıcı Fatura formunda elle girdi/değiştirdi)
    carinin kur_tipi'ne göre otomatik hesaplama YERİNE doğrudan kullanılır."""
    if fatura.silindi:
        raise FaturaHatasi("İptal edilmiş fatura onaylanamaz.")
    if fatura.durum == Fatura.Durum.ONAYLI:
        return fatura
    kur_override = kur_override or fatura.taslak_kur          # taslakta elle girilen kur korunur
    if fatura.tip_id is None:
        raise FaturaHatasi("Fatura tipi seçilmeden onaylanamaz.")
    if fatura.tip.yon != fatura.yon:
        raise FaturaHatasi("Fatura tipi, faturanın yönüyle uyuşmuyor.")
    tip, cari = fatura.tip, fatura.cari
    kur = (Decimal("1") if fatura.para_birimi == "TRY"
          else (kur_override or _kur_coz(fatura.para_birimi, fatura.tarih, cari=cari)))
    yevmiye_satirlari = _muhasebe_satirlari(fatura, tip, cari, fatura.para_birimi, kur)
    try:
        fis = fis_olustur(tarih=fatura.tarih, satirlar=yevmiye_satirlari,
                          aciklama=_aciklama(tip, cari, fatura.fatura_no), kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.FATURA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    fatura.fis, fatura.kur, fatura.durum = fis, kur, Fatura.Durum.ONAYLI
    fatura.updated_by = kullanici
    fatura.save(update_fields=["fis", "kur", "durum", "updated_by", "updated_at"])
    _demirbas_satildi_yaz(fatura, kullanici)
    _kartlari_yenile(fatura, kullanici)
    _donemsel_senkronla(fatura, kullanici)
    if irsaliyeden_mi(fatura):
        _irsaliye_girislerini_fiyatla(fatura)
    elif fatura.depo_id:
        _hareketleri_yaz(fatura, fatura.depo, kur=kur, kullanici=kullanici)
    return fatura


@transaction.atomic
def fatura_olustur(*, tip_id, cari_id, tarih, satirlar, fatura_no="",
                   para_birimi="TRY", depo_id=None, aciklama="", vade_tarihi=None,
                   sahsi_alis=False, sahsi_ortak_id=None, gv_stopaj_orani=None,
                   kullanici=None, kur=None) -> Fatura:
    """Kolaylık sarmalayıcısı: taslak oluşturur ve tip zaten bilindiği için HEMEN onaylar —
    tek atomik blok, onaylama başarısız olursa (eksik harita/kur/vb.) taslak da geri alınır
    (eskisi gibi tam atomik: ya hepsi ya hiçbiri). Tip'in önceden bilinmediği tek durum —
    İrsaliye'den otomatik açılan taslak — bunun yerine doğrudan fatura_taslak_olustur kullanır.
    ``kur`` doluysa (Fatura formunda elle girildi/değiştirildi) otomatik hesaplama YERİNE
    doğrudan kullanılır."""
    fatura = fatura_taslak_olustur(
        cari_id=cari_id, tarih=tarih, satirlar=satirlar, tip_id=tip_id,
        fatura_no=fatura_no, para_birimi=para_birimi, depo_id=depo_id,
        aciklama=aciklama, vade_tarihi=vade_tarihi, sahsi_alis=sahsi_alis,
        sahsi_ortak_id=sahsi_ortak_id, gv_stopaj_orani=gv_stopaj_orani, kullanici=kullanici)
    return fatura_onayla(fatura, kullanici=kullanici, kur_override=kur)


@transaction.atomic
def fatura_guncelle(fatura: Fatura, *, tip_id=None, cari_id, tarih, satirlar,
                    fatura_no="", para_birimi="TRY", depo_id=None, aciklama="",
                    vade_tarihi=None, sahsi_alis=False, sahsi_ortak_id=None,
                    gv_stopaj_orani=None, kullanici=None, kur=None) -> Fatura:
    """Faturayı günceller. TASLAK ise hafif düzenleme (fiş/hareket yok — tip dahil her şey
    serbestçe değişebilir). ONAYLI ise bugünkü mevcut davranış AYNEN (bağlı fiş+stok
    hareketleri de reverse+rewrite edilir); yalnız koşul `fis_id`'den `durum`'a çevrilir.
    ``vade_tarihi`` burada OTOMATİK HESAPLANMAZ (bkz. fatura_taslak_olustur) — boş gelirse
    boş kalır; mevcut faturalar düzenlenirken beklenmedik bir vade yazılmasın diye kasıtlı.
    ``sahsi_alis``/``sahsi_ortak_id``: Ortak adına şahsi alış — sonradan kaldırılırsa (False/
    None gelirse) fiş normal alış-gider fişine döner (bkz. _hazirla/_muhasebe_satirlari)."""
    from django.utils import timezone
    if fatura.silindi:
        raise FaturaHatasi("Silinmiş fatura düzenlenemez.")
    if sahsi_alis and not sahsi_ortak_id:
        raise FaturaHatasi("Ortak adına şahsi alış için bir ortak hesabı seçin.")
    sahsi_ortak = _ortak_hesabi_coz(sahsi_ortak_id) if sahsi_alis else None

    if fatura.durum == Fatura.Durum.TASLAK:
        tip = FaturaTipi.objects.filter(pk=tip_id, silindi=False).first() if tip_id else None
        gider = bool(tip and tip.gider)
        if sahsi_ortak is not None and not gider:
            raise FaturaHatasi("Ortak adına şahsi alış yalnız alış-gider faturasında kullanılabilir.")
        cozulen_yon = tip.yon if tip is not None else fatura.yon
        cari, pb, hazir = _hazirla_taslak(
            cari_id=cari_id, satirlar=satirlar, para_birimi=para_birimi, gider=gider,
            satis=(cozulen_yon == FaturaTipi.Yon.SATIS), fatura_pk=fatura.pk,
            sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None), kullanici=kullanici, tarih=tarih, fatura_no=fatura_no)
        if gider and cozulen_yon != FaturaTipi.Yon.ALIS:
            raise FaturaHatasi("Gider faturası yalnız alış yönünde olabilir.")
        depo = None if gider else _depo_coz(depo_id)
        fatura_no = (fatura_no or "").strip()
        _mukerrer_alis_kontrol(cari=cari, fatura_no=fatura_no, yon=cozulen_yon,
                               haric_pk=fatura.pk)
        fatura.satirlar.filter(silindi=False).update(
            silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        fatura.tip, fatura.yon, fatura.cari, fatura.tarih = tip, cozulen_yon, cari, tarih
        fatura.fatura_no, fatura.para_birimi, fatura.depo = fatura_no, pb, depo
        fatura.aciklama = (aciklama or "").strip()
        fatura.vade_tarihi = vade_tarihi
        fatura.sahsi_alis, fatura.sahsi_ortak = bool(sahsi_ortak), sahsi_ortak
        fatura.gv_stopaj_orani = _stopaj_orani_coz(tip, gv_stopaj_orani)
        fatura.taslak_kur = _taslak_kur_coz(kur, pb)
        fatura.updated_by = kullanici
        fatura.save(update_fields=["tip", "yon", "cari", "tarih", "fatura_no", "para_birimi",
                                   "depo", "aciklama", "vade_tarihi", "sahsi_alis", "sahsi_ortak",
                                   "gv_stopaj_orani", "taslak_kur", "updated_by", "updated_at"])
        _satirlari_yaz(fatura, hazir, kullanici)
        return fatura

    # ONAYLI — bugünkü mevcut mantık aynen.
    if fatura.fis_id is None or fatura.fis.silindi:
        raise FaturaHatasi("Faturanın aktif yevmiye fişi yok; düzenlenemez.")
    tip, cari, pb, kur, yevmiye_satirlari, hazir = _hazirla(
        tip_id=tip_id, cari_id=cari_id, tarih=tarih, satirlar=satirlar,
        para_birimi=para_birimi, kur_override=kur,
        sahsi_ortak_id=(sahsi_ortak.pk if sahsi_ortak else None),
        gv_stopaj_orani=gv_stopaj_orani, fatura_pk=fatura.pk, kullanici=kullanici, fatura_no=fatura_no)
    depo = None if tip.gider else _depo_coz(depo_id)    # gider faturasında depo/stok hareketi yok
    fatura_no = (fatura_no or "").strip()
    _mukerrer_alis_kontrol(cari=cari, fatura_no=fatura_no, yon=tip.yon, haric_pk=fatura.pk)
    try:
        fis_guncelle(fatura.fis, tarih=tarih, satirlar=yevmiye_satirlari,
                     aciklama=_aciklama(tip, cari, fatura_no), kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    # Etkilenen (stok, depo) çiftleri — eski + yeni; güncelleme sonrası NEGATİF eldeki backstop'u.
    etkilenen = _fatura_hareket_ciftleri(fatura)
    # Eski stok hareketleri + satırları geri al (yeni çıkış kontrolü doğru eldekiyi görsün)
    _demirbas_geri_al(fatura, kullanici)             # önceki demirbaş satışları (yeniden işaretlenecek)
    _hareketleri_iptal(fatura, kullanici=kullanici)
    fatura.satirlar.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    fatura.tip, fatura.yon, fatura.cari, fatura.tarih = tip, tip.yon, cari, tarih
    fatura.fatura_no, fatura.para_birimi, fatura.kur, fatura.depo = fatura_no, pb, kur, depo
    fatura.aciklama = (aciklama or "").strip()
    fatura.vade_tarihi = vade_tarihi
    fatura.sahsi_alis, fatura.sahsi_ortak = bool(sahsi_ortak), sahsi_ortak
    fatura.gv_stopaj_orani = _stopaj_orani_coz(tip, gv_stopaj_orani)
    fatura.updated_by = kullanici
    fatura.save(update_fields=["tip", "yon", "cari", "tarih", "fatura_no", "para_birimi",
                               "kur", "depo", "aciklama", "vade_tarihi", "sahsi_alis",
                               "sahsi_ortak", "gv_stopaj_orani", "updated_by", "updated_at"])
    _satirlari_yaz(fatura, hazir, kullanici)
    _demirbas_satildi_yaz(fatura, kullanici)
    _donemsel_senkronla(fatura, kullanici)
    if irsaliyeden_mi(fatura):
        _irsaliye_girislerini_fiyatla(fatura)
    elif depo is not None:
        _hareketleri_yaz(fatura, depo, kur=kur, kullanici=kullanici)
    _negatif_eldeki_dogrula(etkilenen | _fatura_hareket_ciftleri(fatura))
    return fatura


@transaction.atomic
def fatura_sil(fatura: Fatura, kullanici=None) -> None:
    """Faturayı KALICI olarak siler — bağlı yevmiye fişi ve stok hareketleri (+ varsa FIFO
    maliyet katmanları) dahil hiçbir iz kalmaz (CLAUDE.md'nin bu ekrana özel BİLİNÇLİ
    istisnası — kullanıcı isteği, bkz. proje belleği). Girdiği stok başka bir hareketle
    (satış/üretim) zaten tüketilmişse reddedilir (hareket_sil'in mevcut güvenlik kontrolü).
    Bu fatura bir İrsaliye'den doğduysa (irsaliye.fatura), o bağlantı temizlenir —
    İrsaliye SİLİNMEZ, "Faturaya Dönüştü" rozetini kaybedip yeniden düzenlenebilir hale
    gelir (bkz. teklif_siparis.teklif_siparis_onayi_geri_al)."""
    _demirbas_geri_al(fatura, kullanici)             # satılan demirbaş kartları eski durumuna döner
    kart_hesaplari = set(fatura.satirlar.values_list("hesap_id", flat=True))
    hareketler = list(StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False))
    maliyet_fis_idler = {h.fis_id for h in hareketler if h.fis_id}   # satış maliyet fişi
    for h in hareketler:
        try:
            hareket_sil(h, kullanici=kullanici)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))
    if fatura.fis_id and not fatura.fis.silindi:
        fis_iptal(fatura.fis, kullanici=kullanici)
    if irsaliyeden_mi(fatura):
        _irsaliye_girislerini_fiyatsiz_yap(fatura)    # maliyeti fatura belirlemişti; geri al
    TeklifSiparis.objects.filter(fatura=fatura).update(fatura=None)
    katman_ids = list(StokMaliyetKatmani.objects.filter(
        stok_hareket__in=hareketler).values_list("id", flat=True))
    StokMaliyetTuketimi.objects.filter(katman_id__in=katman_ids).delete()
    StokMaliyetKatmani.objects.filter(id__in=katman_ids).delete()
    StokHareket.objects.filter(id__in=[h.pk for h in hareketler]).delete()
    YevmiyeFisi.objects.filter(pk__in=maliyet_fis_idler,
                               kaynak=YevmiyeFisi.Kaynak.STOK_SATIS).delete()
    donemsel_gider.fatura_temizle(fatura)             # aylık dağıtım fişleri + plan silinir (180 hesabı geçmiş için kalır)
    fis_id = fatura.fis_id
    fis_yil = fatura.fis.yil if fis_id else None
    fatura.delete()                                    # FaturaSatir CASCADE
    if fis_id:
        from core.services.yevmiye import fis_no_sayacini_koru
        fis_no_sayacini_koru(fis_yil)                  # silinen fiş numarası bir daha verilmez
        YevmiyeFisi.objects.filter(pk=fis_id).delete()  # YevmiyeSatir CASCADE (fis PROTECT
                                                         # olduğu için fatura'dan SONRA silinir)
    for kod in kart_hesaplari:                           # kart maliyeti hesaba eşitlenir; boş kalan kart silinir (hesap KALIR)
        if kod and duran_hesap.varlik_hesabi_mi(kod) and not duran_hesap.proje_hesabi_mi(kod):
            dv_servis.kart_maliyetini_yenile(kod, kullanici=kullanici)
