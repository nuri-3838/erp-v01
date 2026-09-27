"""Aday Tip/Potansiyel/Aşama tanım tabloları (CRM) servis katmanı — üç model de aynı ortak
şekle sahip (bkz. core.models._AdayTanimTaban: ad/sıra/aktif/renk/sistem_kodu); burada ortak
kurallar TEK yerde, model-özel alanlar (kategori eşlemesi/sıcak/rol) kendi ekle/güncelle
fonksiyonlarında.

Kurallar (spec):
- ad TR büyük harfe çevrilir, (silindi=False) kapsamında benzersiz.
- sistem_kodu dolu kayıt: silinemez, kodu hiç değişmez (form/servis onu hiç almaz).
- Herhangi bir AdayMusteri'de kullanılan kayıt silinemez -> "Pasif yap" önerilir.
- aktif=False: formlarda seçilemez, var olan adaylarda görünmeye devam eder.
- Aşama'da ek kural: tam olarak bir aktif BASLANGIC, en az bir aktif KAPALI olmalı.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import AdayAsamaTanim


class AdayTanimHatasi(ValueError):
    """Aday tip/potansiyel/aşama tanım kural ihlali (Türkçe mesaj)."""


def tum_tanimlar(Model):
    return Model.objects.filter(silindi=False).order_by("sira", "ad")


def aktif_tanimlar(Model):
    return Model.objects.filter(silindi=False, aktif=True).order_by("sira", "ad")


def _ad_dogrula(Model, ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise AdayTanimHatasi("Ad boş olamaz.")
    qs = Model.objects.filter(silindi=False, ad=ad)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise AdayTanimHatasi(f"Bu ad zaten kayıtlı: {ad}")
    return ad


def tanim_sil(tanim, *, kullanici=None):
    """Soft delete — sistem kaydı veya kullanılan kayıt silinemez ('Pasif yap' önerilir)."""
    if tanim.silindi:
        return tanim
    if tanim.sistem_kodu:
        raise AdayTanimHatasi(
            "Bu sistem kaydı silinemez; gerekiyorsa 'Pasif yap' seçeneğini kullanın.")
    if tanim.aday_musteriler.filter(silindi=False).exists():
        raise AdayTanimHatasi(
            "Bu kayıt en az bir aday müşteride kullanılıyor; silinemez. Gerekiyorsa "
            "'Pasif yap' seçeneğini kullanın.")
    tanim.silindi = True
    tanim.silindi_at = timezone.now()
    tanim.updated_by = kullanici
    tanim.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return tanim


def tanim_pasif_yap(tanim, *, aktif, kullanici=None):
    tanim.aktif = aktif
    tanim.updated_by = kullanici
    tanim.save(update_fields=["aktif", "updated_by", "updated_at"])
    return tanim


# --- Tip -----------------------------------------------------------------------------
def tip_olustur(*, ad, sira=0, aktif=True, renk="GRI", cari_kategori_yurtici=None,
                cari_kategori_yurtdisi=None, cariye_donusturulebilir=True, kullanici=None):
    from core.models import AdayTipTanim
    ad = _ad_dogrula(AdayTipTanim, ad)
    return AdayTipTanim.objects.create(
        ad=ad, sira=sira, aktif=aktif, renk=renk,
        cari_kategori_yurtici=cari_kategori_yurtici,
        cari_kategori_yurtdisi=cari_kategori_yurtdisi,
        cariye_donusturulebilir=cariye_donusturulebilir,
        created_by=kullanici, updated_by=kullanici)


def tip_guncelle(tanim, *, ad, sira, aktif, renk, cari_kategori_yurtici,
                 cari_kategori_yurtdisi, cariye_donusturulebilir, kullanici=None):
    from core.models import AdayTipTanim
    if tanim.silindi:
        raise AdayTanimHatasi("Silinmiş kayıt düzenlenemez.")
    tanim.ad = _ad_dogrula(AdayTipTanim, ad, haric_pk=tanim.pk)
    tanim.sira, tanim.aktif, tanim.renk = sira, aktif, renk
    tanim.cari_kategori_yurtici = cari_kategori_yurtici
    tanim.cari_kategori_yurtdisi = cari_kategori_yurtdisi
    tanim.cariye_donusturulebilir = cariye_donusturulebilir
    tanim.updated_by = kullanici
    tanim.save(update_fields=[
        "ad", "sira", "aktif", "renk", "cari_kategori_yurtici", "cari_kategori_yurtdisi",
        "cariye_donusturulebilir", "updated_by", "updated_at"])
    return tanim


# --- Potansiyel ------------------------------------------------------------------------
def potansiyel_olustur(*, ad, sira=0, aktif=True, renk="GRI", sicak=False, kullanici=None):
    from core.models import AdayPotansiyelTanim
    ad = _ad_dogrula(AdayPotansiyelTanim, ad)
    return AdayPotansiyelTanim.objects.create(
        ad=ad, sira=sira, aktif=aktif, renk=renk, sicak=sicak,
        created_by=kullanici, updated_by=kullanici)


def potansiyel_guncelle(tanim, *, ad, sira, aktif, renk, sicak, kullanici=None):
    from core.models import AdayPotansiyelTanim
    if tanim.silindi:
        raise AdayTanimHatasi("Silinmiş kayıt düzenlenemez.")
    tanim.ad = _ad_dogrula(AdayPotansiyelTanim, ad, haric_pk=tanim.pk)
    tanim.sira, tanim.aktif, tanim.renk, tanim.sicak = sira, aktif, renk, sicak
    tanim.updated_by = kullanici
    tanim.save(update_fields=["ad", "sira", "aktif", "renk", "sicak",
                              "updated_by", "updated_at"])
    return tanim


# --- Aşama (BASLANGIC/KAPALI invariant'ı dahil) -----------------------------------------
def _asama_gecerlilik_kontrol(*, haric_pk, rol, aktif):
    """Bu rol/aktif ile kaydedilirse (veya haric_pk pasif yapılır/silinirse) spec kuralı hâlâ
    sağlanıyor mu: tam olarak 1 aktif BASLANGIC, en az 1 aktif KAPALI, en fazla 1 aktif CARI
    (CARI hiç yoksa sorun değil — o zaman cariye dönüşümünde aşama değişmez)."""
    digerleri = AdayAsamaTanim.objects.filter(silindi=False, aktif=True)
    if haric_pk is not None:
        digerleri = digerleri.exclude(pk=haric_pk)
    baslangic = digerleri.filter(rol=AdayAsamaTanim.Rol.BASLANGIC).count()
    kapali = digerleri.filter(rol=AdayAsamaTanim.Rol.KAPALI).count()
    cari = digerleri.filter(rol=AdayAsamaTanim.Rol.CARI).count()
    if aktif and rol == AdayAsamaTanim.Rol.BASLANGIC:
        baslangic += 1
    if aktif and rol == AdayAsamaTanim.Rol.KAPALI:
        kapali += 1
    if aktif and rol == AdayAsamaTanim.Rol.CARI:
        cari += 1
    if baslangic != 1:
        raise AdayTanimHatasi("Tam olarak bir aktif 'Başlangıç' rolündeki aşama olmalı.")
    if kapali < 1:
        raise AdayTanimHatasi("En az bir aktif 'Kapalı' rolündeki aşama olmalı.")
    if cari > 1:
        raise AdayTanimHatasi("En fazla bir aktif 'Cari olunca' rolündeki aşama olabilir.")


def asama_olustur(*, ad, sira=0, aktif=True, renk="GRI", rol=AdayAsamaTanim.Rol.ARA,
                  kullanici=None):
    ad = _ad_dogrula(AdayAsamaTanim, ad)
    _asama_gecerlilik_kontrol(haric_pk=None, rol=rol, aktif=aktif)
    return AdayAsamaTanim.objects.create(
        ad=ad, sira=sira, aktif=aktif, renk=renk, rol=rol,
        created_by=kullanici, updated_by=kullanici)


def asama_guncelle(tanim, *, ad, sira, aktif, renk, rol, kullanici=None):
    if tanim.silindi:
        raise AdayTanimHatasi("Silinmiş kayıt düzenlenemez.")
    ad = _ad_dogrula(AdayAsamaTanim, ad, haric_pk=tanim.pk)
    _asama_gecerlilik_kontrol(haric_pk=tanim.pk, rol=rol, aktif=aktif)
    tanim.ad, tanim.sira, tanim.aktif, tanim.renk, tanim.rol = ad, sira, aktif, renk, rol
    tanim.updated_by = kullanici
    tanim.save(update_fields=["ad", "sira", "aktif", "renk", "rol",
                              "updated_by", "updated_at"])
    return tanim


def asama_sil(tanim, *, kullanici=None):
    if tanim.silindi:
        return tanim
    _asama_gecerlilik_kontrol(haric_pk=tanim.pk, rol=tanim.rol, aktif=False)
    return tanim_sil(tanim, kullanici=kullanici)


def asama_pasif_yap(tanim, *, aktif, kullanici=None):
    if not aktif:
        _asama_gecerlilik_kontrol(haric_pk=tanim.pk, rol=tanim.rol, aktif=False)
    return tanim_pasif_yap(tanim, aktif=aktif, kullanici=kullanici)
