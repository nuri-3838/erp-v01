"""Aday Müşteri (CRM) servis katmanı — Cari'den kasıtlı olarak AYRI ve HAFİF: aday kaydı
açılırken muhasebe hesabı AÇILMAZ (bkz. cari_servis.muhasebe_hesabi_ac — bu, gerçek
müşteri/tedarikçi için doğru ama henüz hiçbir şey satmadığımız bir adayda hesap planını
kirletir). Yapısı bilinçli olarak Cari'ye çok yakın (kimlik/iletişim + kategori + para
birimi + iskonto). ``aday_cariye_donustur`` gerçek bir Cari açar; aday kaydı silinmez,
``donusen_cari`` ile iz kalır (TeklifSiparis.kaynak_teklif ile aynı invariant).

UPPER alanlar (unvan/ilgili kişi) TR büyük harfe çevrilir. Silme: soft-delete.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import AdayAktivite, AdayMusteri, AdayMusteriKategori, Cari, Sehir, Ulke
from core.sayi import SayiHatasi, parse_tr
from core.services import cari as cari_servis


class AdayHatasi(ValueError):
    """Aday müşteri kural ihlali (Türkçe mesaj)."""


def aktif_aday_musteriler():
    return (AdayMusteri.objects.filter(silindi=False)
            .select_related("ulke", "sehir", "kategori", "donusen_cari"))


def _ulke(ulke_id):
    if not ulke_id:
        return None
    u = Ulke.objects.filter(pk=ulke_id, silindi=False).first()
    if u is None:
        raise AdayHatasi("Ülke bulunamadı.")
    return u


def _sehir(sehir_id):
    if not sehir_id:
        return None
    s = Sehir.objects.filter(pk=sehir_id, silindi=False).first()
    if s is None:
        raise AdayHatasi("Şehir bulunamadı.")
    return s


def _kategori(kategori_id):
    if not kategori_id:
        return None
    k = AdayMusteriKategori.objects.filter(pk=kategori_id, silindi=False).first()
    if k is None:
        raise AdayHatasi("Kategori bulunamadı.")
    return k


def _para_dogrula(deger, etiket):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise AdayHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise AdayHatasi(f"{etiket} negatif olamaz.")
    return d


def _alanlar(*, unvan, ilgili_kisi="", telefon="", eposta="", ulke_id=None, sehir_id=None,
            kategori_id=None, para_birimi="TRY", iskonto_yuzdesi=0):
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise AdayHatasi("Unvan boş olamaz.")
    if para_birimi not in dict(AdayMusteri.PARA_CHOICES):
        raise AdayHatasi("Geçersiz para birimi.")
    return dict(
        unvan=unvan,
        ilgili_kisi=buyuk_harf_tr((ilgili_kisi or "").strip()),
        telefon=(telefon or "").strip(), eposta=(eposta or "").strip().lower(),
        ulke=_ulke(ulke_id), sehir=_sehir(sehir_id), kategori=_kategori(kategori_id),
        para_birimi=para_birimi,
        iskonto_yuzdesi=_para_dogrula(iskonto_yuzdesi, "İskonto"),
    )


def aday_musteri_olustur(*, kullanici=None, **kw) -> AdayMusteri:
    veri = _alanlar(**kw)
    return AdayMusteri.objects.create(
        created_by=kullanici, updated_by=kullanici, **veri)


def aday_musteri_guncelle(aday: AdayMusteri, *, kullanici=None, **kw) -> AdayMusteri:
    if aday.silindi:
        raise AdayHatasi("Silinmiş aday düzenlenemez.")
    veri = _alanlar(**kw)
    for alan, deger in veri.items():
        setattr(aday, alan, deger)
    aday.updated_by = kullanici
    aday.save()
    return aday


def aday_musteri_sil(aday: AdayMusteri, kullanici=None) -> AdayMusteri:
    if aday.silindi:
        return aday
    aday.silindi = True
    aday.silindi_at = timezone.now()
    aday.updated_by = kullanici
    aday.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return aday


@transaction.atomic
def aday_cariye_donustur(aday: AdayMusteri, *, kategori_id=None, kullanici=None) -> Cari:
    """Adayı gerçek bir Cari'ye dönüştürür (muhasebe hesabı bu noktada açılır) — tek
    seferlik, zaten dönüştürülmüş bir aday tekrar dönüştürülemez. ``kategori_id`` burada
    Cari'nin KENDİ kategorisidir (CariKategori) — adayın kendi AdayMusteriKategori'siyle
    karışmaz, ayrı ağaçlardır."""
    if aday.silindi:
        raise AdayHatasi("Silinmiş aday dönüştürülemez.")
    if aday.donusen_cari_id:
        raise AdayHatasi("Bu aday zaten bir cariye dönüştürülmüş.")
    cari = cari_servis.cari_olustur(
        unvan=aday.unvan, kategori_id=kategori_id, kullanici=kullanici,
        ilgili_kisi=aday.ilgili_kisi, telefon=aday.telefon, eposta=aday.eposta,
        ulke_id=aday.ulke_id, sehir_id=aday.sehir_id, para_birimi=aday.para_birimi,
        iskonto_yuzdesi=aday.iskonto_yuzdesi)
    aday.donusen_cari = cari
    aday.updated_by = kullanici
    aday.save(update_fields=["donusen_cari", "updated_by", "updated_at"])
    return cari


# --- Aktiviteler (görüşme/temas kayıtları) -----------------------------------
def aktif_aday_aktiviteleri(aday):
    return (aday.aktiviteler.filter(silindi=False)
            .select_related("created_by").order_by("-tarih", "-id"))


def aday_aktivite_ekle(aday, *, tarih, tur, aciklama, kullanici=None) -> AdayAktivite:
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise AdayHatasi("Açıklama boş olamaz.")
    if tur not in AdayAktivite.Tur.values:
        raise AdayHatasi("Geçersiz aktivite türü.")
    return AdayAktivite.objects.create(
        aday=aday, tarih=tarih, tur=tur, aciklama=aciklama,
        created_by=kullanici, updated_by=kullanici)


def aday_aktivite_guncelle(aktivite: AdayAktivite, *, tarih, tur, aciklama,
                           kullanici=None) -> AdayAktivite:
    if aktivite.silindi:
        raise AdayHatasi("Silinmiş aktivite düzenlenemez.")
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise AdayHatasi("Açıklama boş olamaz.")
    if tur not in AdayAktivite.Tur.values:
        raise AdayHatasi("Geçersiz aktivite türü.")
    aktivite.tarih = tarih
    aktivite.tur = tur
    aktivite.aciklama = aciklama
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["tarih", "tur", "aciklama", "updated_by", "updated_at"])
    return aktivite


def aday_aktivite_sil(aktivite: AdayAktivite, kullanici=None) -> AdayAktivite:
    if aktivite.silindi:
        return aktivite
    aktivite.silindi = True
    aktivite.silindi_at = timezone.now()
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return aktivite
