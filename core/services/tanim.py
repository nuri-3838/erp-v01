"""Tanım listeleri (AYARLAR) servis katmanı — KDV oranları + Tevkifat oranları.

Otomatik yevmiyede KDV/tevkifat muhasebe hesaplarını besler. Açıklama/kod TR büyük harf;
oran/pay/payda doğrulanır; muhasebe hesabı opsiyonel (sonra da bağlanabilir).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, KdvOrani, TanimSecenegi, TeklifSiparis, TevkifatOrani
from core.sayi import SayiHatasi, parse_tr


class TanimHatasi(ValueError):
    """Tanım listesi kural ihlali (Türkçe mesaj)."""


def _hesap_coz(hesap_kodu):
    kod = (hesap_kodu or "").strip()
    if not kod:
        return None
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise TanimHatasi(f"Hesap bulunamadı: {kod}")
    return h


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise TanimHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise TanimHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if not pozitif and d < 0:
        raise TanimHatasi(f"{etiket} negatif olamaz.")
    return d


# --- KDV oranları -----------------------------------------------------------
def aktif_kdv_oranlari():
    return (KdvOrani.objects.filter(silindi=False)
            .select_related("hesap_borc", "hesap_alacak").order_by("sira", "oran"))


def _kdv_oran_benzersiz(oran, *, haric_pk=None):
    """Aynı KDV oranı silinmemişler arasında ikinci kez tanımlanamaz."""
    qs = KdvOrani.objects.filter(silindi=False, oran=oran)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise TanimHatasi(f"Bu KDV oranı zaten kayıtlı: %{oran}")


def kdv_orani_olustur(*, aciklama, oran, sira=0, hesap_borc_kodu="", hesap_alacak_kodu="",
                      kullanici=None) -> KdvOrani:
    aciklama = buyuk_harf_tr((aciklama or "").strip())
    if not aciklama:
        raise TanimHatasi("Açıklama boş olamaz.")
    oran = _sayi(oran, "KDV oranı")
    _kdv_oran_benzersiz(oran)
    return KdvOrani.objects.create(
        aciklama=aciklama, oran=oran, sira=int(sira or 0),
        hesap_borc=_hesap_coz(hesap_borc_kodu), hesap_alacak=_hesap_coz(hesap_alacak_kodu),
        created_by=kullanici, updated_by=kullanici)


def kdv_orani_guncelle(k: KdvOrani, *, aciklama, oran, sira=0, hesap_borc_kodu="",
                       hesap_alacak_kodu="", kullanici=None) -> KdvOrani:
    if k.silindi:
        raise TanimHatasi("Silinmiş kayıt düzenlenemez.")
    aciklama = buyuk_harf_tr((aciklama or "").strip())
    if not aciklama:
        raise TanimHatasi("Açıklama boş olamaz.")
    oran = _sayi(oran, "KDV oranı")
    _kdv_oran_benzersiz(oran, haric_pk=k.pk)
    k.aciklama = aciklama
    k.oran = oran
    k.sira = int(sira or 0)
    k.hesap_borc = _hesap_coz(hesap_borc_kodu)
    k.hesap_alacak = _hesap_coz(hesap_alacak_kodu)
    k.updated_by = kullanici
    k.save(update_fields=["aciklama", "oran", "sira", "hesap_borc", "hesap_alacak",
                          "updated_by", "updated_at"])
    return k


def kdv_orani_sil(k: KdvOrani, kullanici=None) -> KdvOrani:
    if k.silindi:
        return k
    if k.stoklar.filter(silindi=False).exists():
        raise TanimHatasi(
            "Bu KDV oranı stoklarda kullanılıyor; önce ilgili stoklardan kaldırın.")
    k.silindi = True
    k.silindi_at = timezone.now()
    k.updated_by = kullanici
    k.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return k


# --- Tevkifat oranları ------------------------------------------------------
def aktif_tevkifat_oranlari():
    return (TevkifatOrani.objects.filter(silindi=False)
            .select_related("hesap").order_by("kod"))


def _kod_dogrula(kod, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    if not kod:
        raise TanimHatasi("Kod boş olamaz.")
    cak = TevkifatOrani.objects.filter(silindi=False, kod=kod)
    if haric_pk is not None:
        cak = cak.exclude(pk=haric_pk)
    if cak.exists():
        raise TanimHatasi(f"Bu kod zaten kayıtlı: {kod}")
    return kod


def tevkifat_orani_olustur(*, kod, pay, payda, aciklama="", hesap_kodu="",
                           kullanici=None) -> TevkifatOrani:
    kod = _kod_dogrula(kod)
    return TevkifatOrani.objects.create(
        kod=kod, pay=int(_sayi(pay, "Pay")), payda=int(_sayi(payda, "Payda", pozitif=True)),
        aciklama=buyuk_harf_tr((aciklama or "").strip()), hesap=_hesap_coz(hesap_kodu),
        created_by=kullanici, updated_by=kullanici)


def tevkifat_orani_guncelle(t: TevkifatOrani, *, kod, pay, payda, aciklama="",
                            hesap_kodu="", kullanici=None) -> TevkifatOrani:
    if t.silindi:
        raise TanimHatasi("Silinmiş kayıt düzenlenemez.")
    t.kod = _kod_dogrula(kod, haric_pk=t.pk)
    t.pay = int(_sayi(pay, "Pay"))
    t.payda = int(_sayi(payda, "Payda", pozitif=True))
    t.aciklama = buyuk_harf_tr((aciklama or "").strip())
    t.hesap = _hesap_coz(hesap_kodu)
    t.updated_by = kullanici
    t.save(update_fields=["kod", "pay", "payda", "aciklama", "hesap",
                          "updated_by", "updated_at"])
    return t


def tevkifat_orani_sil(t: TevkifatOrani, kullanici=None) -> TevkifatOrani:
    if t.silindi:
        return t
    if t.stoklar.filter(silindi=False).exists():
        raise TanimHatasi(
            "Bu tevkifat oranı stoklarda kullanılıyor; önce ilgili stoklardan kaldırın.")
    t.silindi = True
    t.silindi_at = timezone.now()
    t.updated_by = kullanici
    t.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return t


# --- Tanım seçenekleri (Yükleme Şekli / Ödeme Koşulu / Yükleme Tipi) ---------------
def _kategori_dogrula(kategori):
    if kategori not in TanimSecenegi.Kategori.values:
        raise TanimHatasi("Geçersiz tanım kategorisi.")
    return kategori


def aktif_secenekler(kategori):
    return (TanimSecenegi.objects.filter(silindi=False, kategori=_kategori_dogrula(kategori))
            .order_by("sira", "ad"))


def _secenek_dogrula(kategori, ad, kod, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    kod = buyuk_harf_tr((kod or "").strip())
    if not ad:
        raise TanimHatasi("Ad boş olamaz.")
    if kategori == TanimSecenegi.Kategori.YUKLEME_TIPI and not kod:
        raise TanimHatasi("Yükleme tipinde kod zorunlu (örn. 20DC, 40HQ, TIR).")
    aktif = TanimSecenegi.objects.filter(silindi=False, kategori=kategori)
    if haric_pk is not None:
        aktif = aktif.exclude(pk=haric_pk)
    if aktif.filter(ad=ad).exists():
        raise TanimHatasi(f"Bu ad zaten kayıtlı: {ad}")
    if kod and aktif.filter(kod=kod).exists():
        raise TanimHatasi(f"Bu kod zaten kayıtlı: {kod}")
    return ad, kod


def secenek_olustur(kategori, *, ad, kod="", ad_en="", sira=0, kullanici=None) -> TanimSecenegi:
    kategori = _kategori_dogrula(kategori)
    ad, kod = _secenek_dogrula(kategori, ad, kod)
    return TanimSecenegi.objects.create(
        kategori=kategori, ad=ad, kod=kod, ad_en=(ad_en or "").strip(), sira=int(sira or 0),
        created_by=kullanici, updated_by=kullanici)


def secenek_guncelle(s: TanimSecenegi, *, ad, kod="", ad_en="", sira=0,
                     kullanici=None) -> TanimSecenegi:
    if s.silindi:
        raise TanimHatasi("Silinmiş kayıt düzenlenemez.")
    s.ad, s.kod = _secenek_dogrula(s.kategori, ad, kod, haric_pk=s.pk)
    s.ad_en = (ad_en or "").strip()          # İngilizce: TR büyük harfe ÇEVRİLMEZ
    s.sira = int(sira or 0)
    s.updated_by = kullanici
    s.save(update_fields=["ad", "kod", "ad_en", "sira", "updated_by", "updated_at"])
    return s


def secenek_sil(s: TanimSecenegi, kullanici=None) -> TanimSecenegi:
    if s.silindi:
        return s
    from django.db.models import Q
    kullanimda = TeklifSiparis.objects.filter(silindi=False).filter(
        Q(yukleme_sekli=s) | Q(odeme_kosulu=s) | Q(yukleme_tipi=s)).exists()
    if kullanimda:
        raise TanimHatasi("Bu seçenek tekliflerde kullanılıyor; silinemez.")
    s.silindi = True
    s.silindi_at = timezone.now()
    s.updated_by = kullanici
    s.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return s
