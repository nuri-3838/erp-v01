"""Aday Müşteri (CRM) servis katmanı — Cari'den kasıtlı olarak AYRI ve HAFİF: aday kaydı
açılırken muhasebe hesabı AÇILMAZ (bkz. cari_servis.muhasebe_hesabi_ac — bu, gerçek
müşteri/tedarikçi için doğru ama henüz hiçbir şey satmadığımız bir adayda hesap planını
kirletir). Yapısı bilinçli olarak Cari'ye çok yakın (kimlik/iletişim + kategori + para
birimi + iskonto). "Cariye Dönüştür" akışı (eşleşme bulma + yeni cari açma/mevcut cariye
bağlama) core.services.aday_donustur'da — aday kaydı silinmez, ``cari`` +
``cariye_donusum_tarihi`` ile iz kalır (TeklifSiparis.kaynak_teklif ile aynı invariant).

UPPER alanlar (unvan/ilgili kişi) TR büyük harfe çevrilir. Silme: soft-delete.
"""
from __future__ import annotations

import os

from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from core import gorsel
from core.dogrulama import web_normalize
from core.metin import buyuk_harf_tr
from core.models import (
    AdayAktivite, AdayAktiviteEk, AdayAsamaTanim, AdayMusteri, AdayMusteriKategori,
    AdayPotansiyelTanim, AdayTipTanim, AdayYetkili, KapanisNedeni, Sehir, Ulke,
)
from core.sayi import SayiHatasi, parse_tr

AKTIVITE_IZINLI_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
AKTIVITE_MAKS_BOYUT = 10 * 1024 * 1024   # 10 MB


class AdayHatasi(ValueError):
    """Aday müşteri kural ihlali (Türkçe mesaj)."""


def aktif_aday_musteriler():
    """Silinmemiş TÜM adaylar (Cariye dönüşmüş olsa da dahil — liste ekranının Tümü sekmesi
    dönüşmüşleri de gösterir, bkz. core/views.py::aday_musteriler). Cariye dönüşmüşleri
    Takibim/Sıcak/Temas yok sekmelerinden düşürmek görüntüleme view'ının kendi sekme
    kuralının işi (_aday_tab_q); "aktif aday" burada yalnız 'silinmemiş' anlamına gelir."""
    return (AdayMusteri.objects.filter(silindi=False)
            .select_related("ulke", "sehir", "kategori", "cari", "tip", "potansiyel", "asama"))


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
        raise AdayHatasi("Kaynak bulunamadı.")
    return k


def _tip(tip_id):
    t = AdayTipTanim.objects.filter(pk=tip_id, silindi=False).first()
    if t is None:
        raise AdayHatasi("Tip bulunamadı.")
    return t


def _potansiyel(potansiyel_id):
    if not potansiyel_id:
        return None
    p = AdayPotansiyelTanim.objects.filter(pk=potansiyel_id, silindi=False).first()
    if p is None:
        raise AdayHatasi("Potansiyel bulunamadı.")
    return p


def _asama(asama_id):
    a = AdayAsamaTanim.objects.filter(pk=asama_id, silindi=False).first()
    if a is None:
        raise AdayHatasi("Aşama bulunamadı.")
    return a


def _para_dogrula(deger, etiket):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise AdayHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise AdayHatasi(f"{etiket} negatif olamaz.")
    return d


def _alanlar(*, unvan, ilgili_kisi="", telefon="", telefon_2="", eposta="", eposta_2="",
            telefon_whatsapp=False, telefon_2_whatsapp=False, web="", adres="",
            ulke_id=None, sehir_id=None, kategori_id=None, para_birimi="TRY",
            iskonto_yuzdesi=0, tip_id=None, potansiyel_id=None, asama_id=None,
            kapanis_nedeni="", sonraki_adim="", sonraki_adim_tarihi=None,
            eposta_gecersiz=False, eposta_2_gecersiz=False):
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise AdayHatasi("Unvan boş olamaz.")
    if para_birimi not in dict(AdayMusteri.PARA_CHOICES):
        raise AdayHatasi("Geçersiz para birimi.")
    tip = _tip(tip_id)
    potansiyel = _potansiyel(potansiyel_id)
    asama = _asama(asama_id)
    if asama.rol == AdayAsamaTanim.Rol.KAPALI:
        if not kapanis_nedeni or kapanis_nedeni not in KapanisNedeni.values:
            raise AdayHatasi("Aşama Kapalı iken kapanış nedeni zorunlu.")
    else:
        kapanis_nedeni = ""
    sonraki_adim = (sonraki_adim or "").strip()
    if sonraki_adim_tarihi and not sonraki_adim:
        raise AdayHatasi("Sonraki adım tarihi girildiyse ne yapılacağı da yazılmalı.")
    eposta = (eposta or "").strip().lower()
    eposta_2 = (eposta_2 or "").strip().lower()
    if not eposta:
        eposta_gecersiz = False
    if not eposta_2:
        eposta_2_gecersiz = False
    web_norm = web_normalize(web)
    if web_norm is None:
        raise AdayHatasi(f"Geçersiz web adresi: {(web or '').strip()}")
    return dict(
        unvan=unvan,
        ilgili_kisi=buyuk_harf_tr((ilgili_kisi or "").strip()),
        telefon=(telefon or "").strip(), telefon_whatsapp=bool(telefon_whatsapp),
        telefon_2=(telefon_2 or "").strip(), telefon_2_whatsapp=bool(telefon_2_whatsapp),
        eposta=eposta, eposta_2=eposta_2,
        eposta_gecersiz=bool(eposta_gecersiz), eposta_2_gecersiz=bool(eposta_2_gecersiz),
        web=web_norm,
        ulke=_ulke(ulke_id), sehir=_sehir(sehir_id), kategori=_kategori(kategori_id),
        adres=buyuk_harf_tr((adres or "").strip()),
        para_birimi=para_birimi,
        iskonto_yuzdesi=_para_dogrula(iskonto_yuzdesi, "İskonto"),
        tip=tip, potansiyel=potansiyel, asama=asama, kapanis_nedeni=kapanis_nedeni,
        sonraki_adim=sonraki_adim, sonraki_adim_tarihi=sonraki_adim_tarihi,
    )


def aday_musteri_olustur(*, kullanici=None, **kw) -> AdayMusteri:
    veri = _alanlar(**kw)
    return AdayMusteri.objects.create(
        created_by=kullanici, updated_by=kullanici, **veri)


def aday_musteri_guncelle(aday: AdayMusteri, *, kullanici=None, **kw) -> AdayMusteri:
    if aday.silindi:
        raise AdayHatasi("Silinmiş aday düzenlenemez.")
    eski_eposta, eski_eposta_2 = aday.eposta, aday.eposta_2
    veri = _alanlar(**kw)
    # E-posta adresi fiilen değiştiyse eski "geçersiz" işareti yeni adrese taşınmaz —
    # kullanıcı aynı anda hem adresi değiştirip hem işaretlese bile (spec'in kuralı budur).
    if veri["eposta"] != eski_eposta:
        veri["eposta_gecersiz"] = False
    if veri["eposta_2"] != eski_eposta_2:
        veri["eposta_2_gecersiz"] = False
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


# --- Yetkili kişiler (CariYetkili ile birebir aynı desen) --------------------
def aktif_aday_yetkilileri(aday):
    return aday.yetkililer.filter(silindi=False).order_by("ad_soyad")


def aday_yetkili_ekle(aday, *, ad_soyad, unvan="", telefon="", eposta="", notlar="",
                      whatsapp=False, kullanici=None) -> AdayYetkili:
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise AdayHatasi("Ad soyad boş olamaz.")
    return AdayYetkili.objects.create(
        aday=aday, ad_soyad=ad_soyad, unvan=buyuk_harf_tr((unvan or "").strip()),
        telefon=(telefon or "").strip(), eposta=(eposta or "").strip().lower(),
        notlar=(notlar or "").strip(), whatsapp=bool(whatsapp),
        created_by=kullanici, updated_by=kullanici)


def aday_yetkili_guncelle(yetkili: AdayYetkili, *, ad_soyad, unvan="", telefon="",
                          eposta="", notlar="", whatsapp=False, kullanici=None) -> AdayYetkili:
    if yetkili.silindi:
        raise AdayHatasi("Silinmiş yetkili düzenlenemez.")
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise AdayHatasi("Ad soyad boş olamaz.")
    yetkili.ad_soyad = ad_soyad
    yetkili.unvan = buyuk_harf_tr((unvan or "").strip())
    yetkili.telefon = (telefon or "").strip()
    yetkili.eposta = (eposta or "").strip().lower()
    yetkili.notlar = (notlar or "").strip()
    yetkili.whatsapp = bool(whatsapp)
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["ad_soyad", "unvan", "telefon", "eposta", "notlar", "whatsapp",
                                "updated_by", "updated_at"])
    return yetkili


def aday_yetkili_sil(yetkili: AdayYetkili, kullanici=None) -> AdayYetkili:
    if yetkili.silindi:
        return yetkili
    yetkili.silindi = True
    yetkili.silindi_at = timezone.now()
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return yetkili


# --- Aktiviteler (görüşme/temas kayıtları) -----------------------------------
def aktif_aday_aktiviteleri(aday):
    return (aday.aktiviteler.filter(silindi=False)
            .select_related("created_by")
            .prefetch_related(Prefetch(
                "ekler", queryset=AdayAktiviteEk.objects.filter(silindi=False)))
            .order_by("-tarih", "-id"))


def _sonraki_adim_dogrula(sonraki_adim, sonraki_adim_tarihi):
    sonraki_adim = (sonraki_adim or "").strip()
    if sonraki_adim_tarihi and not sonraki_adim:
        raise AdayHatasi("Sonraki adım tarihi girildiyse ne yapılacağı da yazılmalı.")
    return sonraki_adim


def aday_aktivite_ekle(aday, *, tarih, tur, aciklama, kullanici=None,
                       sonraki_adim_guncelle=False, sonraki_adim="",
                       sonraki_adim_tarihi=None) -> AdayAktivite:
    """``sonraki_adim_guncelle=True`` iken (form akışı) aktivite + adayın sonraki_adim/
    sonraki_adim_tarihi'si TEK transaction'da yazılır — biri hata verirse ikisi de
    kaydedilmez. Boş bırakılırsa (kullanıcı temizlemişse) adayda da boşalır."""
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise AdayHatasi("Açıklama boş olamaz.")
    if tur not in AdayAktivite.Tur.values:
        raise AdayHatasi("Geçersiz aktivite türü.")
    sonraki_adim = _sonraki_adim_dogrula(sonraki_adim, sonraki_adim_tarihi)
    with transaction.atomic():
        aktivite = AdayAktivite.objects.create(
            aday=aday, tarih=tarih, tur=tur, aciklama=aciklama,
            created_by=kullanici, updated_by=kullanici)
        if sonraki_adim_guncelle:
            aday.sonraki_adim = sonraki_adim
            aday.sonraki_adim_tarihi = sonraki_adim_tarihi
            aday.updated_by = kullanici
            aday.save(update_fields=["sonraki_adim", "sonraki_adim_tarihi",
                                     "updated_by", "updated_at"])
    return aktivite


def aday_aktivite_guncelle(aktivite: AdayAktivite, *, tarih, tur, aciklama, kullanici=None,
                           sonraki_adim_guncelle=False, sonraki_adim="",
                           sonraki_adim_tarihi=None) -> AdayAktivite:
    if aktivite.silindi:
        raise AdayHatasi("Silinmiş aktivite düzenlenemez.")
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise AdayHatasi("Açıklama boş olamaz.")
    if tur not in AdayAktivite.Tur.values:
        raise AdayHatasi("Geçersiz aktivite türü.")
    sonraki_adim = _sonraki_adim_dogrula(sonraki_adim, sonraki_adim_tarihi)
    with transaction.atomic():
        aktivite.tarih = tarih
        aktivite.tur = tur
        aktivite.aciklama = aciklama
        aktivite.updated_by = kullanici
        aktivite.save(update_fields=["tarih", "tur", "aciklama", "updated_by", "updated_at"])
        if sonraki_adim_guncelle:
            aday = aktivite.aday
            aday.sonraki_adim = sonraki_adim
            aday.sonraki_adim_tarihi = sonraki_adim_tarihi
            aday.updated_by = kullanici
            aday.save(update_fields=["sonraki_adim", "sonraki_adim_tarihi",
                                     "updated_by", "updated_at"])
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
