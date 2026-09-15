"""Aday Müşteri (CRM) servis katmanı — Cari'den kasıtlı olarak AYRI ve HAFİF: aday kaydı
açılırken muhasebe hesabı AÇILMAZ (bkz. cari_servis.muhasebe_hesabi_ac — bu, gerçek
müşteri/tedarikçi için doğru ama henüz hiçbir şey satmadığımız bir adayda hesap planını
kirletir). Yapısı bilinçli olarak Cari'ye çok yakın (kimlik/iletişim + kategori + para
birimi + iskonto). ``aday_cariye_donustur`` gerçek bir Cari açar; aday kaydı silinmez,
``donusen_cari`` ile iz kalır (TeklifSiparis.kaynak_teklif ile aynı invariant).

UPPER alanlar (unvan/ilgili kişi) TR büyük harfe çevrilir. Silme: soft-delete.
"""
from __future__ import annotations

import os

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core import gorsel
from core.metin import buyuk_harf_tr
from core.models import (
    AdayAktivite, AdayAktiviteEk, AdayMusteri, AdayMusteriKategori, Cari, CariAktivite,
    CariAktiviteEk, Sehir, Ulke,
)
from core.sayi import SayiHatasi, parse_tr
from core.services import cari as cari_servis

AKTIVITE_IZINLI_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
AKTIVITE_MAKS_BOYUT = 10 * 1024 * 1024   # 10 MB


class AdayHatasi(ValueError):
    """Aday müşteri kural ihlali (Türkçe mesaj)."""


def aktif_aday_musteriler():
    """Henüz Cariye dönüştürülmemiş adaylar — dönüştürülmüş bir aday artık 'aktif aday'
    sayılmaz (bkz. aday_cariye_donustur), listeden çıkar; kaydın kendisi silinmez, yalnız
    buradaki (liste ekranı) queryset'ten hariç tutulur — doğrudan pk ile erişim etkilenmez."""
    return (AdayMusteri.objects.filter(silindi=False, donusen_cari__isnull=True)
            .select_related("ulke", "sehir", "kategori"))


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


def _alanlar(*, unvan, ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
            ulke_id=None, sehir_id=None, kategori_id=None, para_birimi="TRY",
            iskonto_yuzdesi=0):
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise AdayHatasi("Unvan boş olamaz.")
    if para_birimi not in dict(AdayMusteri.PARA_CHOICES):
        raise AdayHatasi("Geçersiz para birimi.")
    return dict(
        unvan=unvan,
        ilgili_kisi=buyuk_harf_tr((ilgili_kisi or "").strip()),
        telefon=(telefon or "").strip(), telefon_2=(telefon_2 or "").strip(),
        eposta=(eposta or "").strip().lower(), eposta_2=(eposta_2 or "").strip().lower(),
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


def _aktiviteleri_cariye_kopyala(aday, cari, kullanici=None):
    """Adayın aktivitelerini (+ ekli dosyalarını) yeni Cari'ye KOPYALAR — CariAktivite ile
    AdayAktivite birebir aynı alan şekline sahip (tarih/tür/açıklama). Aday tarafındaki
    kayıtlar SİLİNMEZ/taşınmaz (iz kalır); Cari'de de aynı geçmiş görünsün diye kopyalanır."""
    from django.core.files.base import ContentFile

    aktiviteler = aday.aktiviteler.filter(silindi=False).prefetch_related(
        Prefetch("ekler", queryset=AdayAktiviteEk.objects.filter(silindi=False)))
    for aktivite in aktiviteler:
        yeni = CariAktivite.objects.create(
            cari=cari, tarih=aktivite.tarih, tur=aktivite.tur, aciklama=aktivite.aciklama,
            created_by=kullanici, updated_by=kullanici)
        for ek in aktivite.ekler.all():
            with ek.dosya.open("rb") as f:
                icerik = ContentFile(f.read(), name=ek.dosya.name.rsplit("/", 1)[-1])
            CariAktiviteEk.objects.create(
                aktivite=yeni, dosya=icerik, orijinal_ad=ek.orijinal_ad,
                created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def aday_cariye_donustur(aday: AdayMusteri, *, kategori_id=None, kullanici=None) -> Cari:
    """Adayı gerçek bir Cari'ye dönüştürür (muhasebe hesabı bu noktada açılır) — tek
    seferlik, zaten dönüştürülmüş bir aday tekrar dönüştürülemez. ``kategori_id`` burada
    Cari'nin KENDİ kategorisidir (CariKategori) — adayın kendi AdayMusteriKategori'siyle
    karışmaz, ayrı ağaçlardır. Aday üzerindeki aktiviteler (+ ekleri) yeni Cari'ye kopyalanır
    (bkz. _aktiviteleri_cariye_kopyala) — dönüşümle birlikte geçmiş görüşme kaydı kaybolmasın."""
    if aday.silindi:
        raise AdayHatasi("Silinmiş aday dönüştürülemez.")
    if aday.donusen_cari_id:
        raise AdayHatasi("Bu aday zaten bir cariye dönüştürülmüş.")
    cari = cari_servis.cari_olustur(
        unvan=aday.unvan, kategori_id=kategori_id, kullanici=kullanici,
        ilgili_kisi=aday.ilgili_kisi, telefon=aday.telefon, telefon_2=aday.telefon_2,
        eposta=aday.eposta,
        ulke_id=aday.ulke_id, sehir_id=aday.sehir_id, para_birimi=aday.para_birimi,
        iskonto_yuzdesi=aday.iskonto_yuzdesi)
    aday.donusen_cari = cari
    aday.updated_by = kullanici
    aday.save(update_fields=["donusen_cari", "updated_by", "updated_at"])
    _aktiviteleri_cariye_kopyala(aday, cari, kullanici=kullanici)
    return cari


# --- Aktiviteler (görüşme/temas kayıtları) -----------------------------------
def aktif_aday_aktiviteleri(aday):
    return (aday.aktiviteler.filter(silindi=False)
            .select_related("created_by")
            .prefetch_related(Prefetch(
                "ekler", queryset=AdayAktiviteEk.objects.filter(silindi=False)))
            .order_by("-tarih", "-id"))


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


def aday_aktivite_ek_ekle(aktivite: AdayAktivite, *, dosya, kullanici=None) -> AdayAktiviteEk:
    """Aktiviteye tek dosya ekler (çoklu yükleme view katmanında döngüyle bu fonksiyonu
    çağırır). Resim ise WebP'ye küçültülür (spec görsel invariant'ı: en uzun kenar ~1600px,
    ~%80 kalite); PDF olduğu gibi saklanır. CariAktiviteEk ile birebir aynı desen."""
    if aktivite.silindi:
        raise AdayHatasi("Silinmiş aktiviteye dosya eklenemez.")
    ad = dosya.name or "dosya"
    uzanti = os.path.splitext(ad)[1].lower()
    if uzanti not in AKTIVITE_IZINLI_UZANTI:
        raise AdayHatasi(f"Desteklenmeyen dosya türü: {ad} (yalnız resim veya PDF).")
    if dosya.size > AKTIVITE_MAKS_BOYUT:
        raise AdayHatasi(f"Dosya çok büyük (10 MB üzeri): {ad}")
    if uzanti == ".pdf":
        saklanan = dosya
    else:
        try:
            saklanan = gorsel.kucult_webp(dosya, max_kenar=1600, kalite=80, ad="aday_aktivite")
        except Exception:
            raise AdayHatasi(f"Geçersiz resim dosyası: {ad}")
    return AdayAktiviteEk.objects.create(
        aktivite=aktivite, dosya=saklanan, orijinal_ad=ad,
        created_by=kullanici, updated_by=kullanici)


def aday_aktivite_ek_sil(ek: AdayAktiviteEk, kullanici=None) -> AdayAktiviteEk:
    if ek.silindi:
        return ek
    ek.silindi = True
    ek.silindi_at = timezone.now()
    ek.updated_by = kullanici
    ek.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ek
