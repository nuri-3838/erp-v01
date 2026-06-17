"""FİNANS modülü servis katmanı — Kasa/Banka/Çek-Senet/Kredi/Kredi Kartı TANIMLARI.

Her tanım bir YAPRAK muhasebe hesabına bağlanır; bakiye SAKLANMAZ, o hesabın
yevmiyesinden hesaplanır (cari/ekstre mantığı). İşlem motoru (tahsilat/ödeme) yok.
Ad TR büyük harfe çevrilir + silinmemişler arasında benzersiz.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Banka, Cari, CekSenet, HesapPlani, Kasa, Kredi, KrediKarti
from core.sayi import SayiHatasi, parse_tr


class FinansHatasi(ValueError):
    """Finans tanım kural ihlali (Türkçe mesaj)."""


def _yaprak_hesap_coz(hesap_kodu):
    """Verilen kodu yaprak (alt) muhasebe hesabına çözer; üst/eksik/yok ise hata."""
    from core.services.hesap_plani import yaprak_mi
    kod = (hesap_kodu or "").strip()
    if not kod:
        raise FinansHatasi("Muhasebe hesabı seçilmeli.")
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise FinansHatasi(f"Hesap bulunamadı: {kod}")
    if not yaprak_mi(h):
        raise FinansHatasi(f"{kod} bir üst hesap; yalnızca yaprak (alt) hesap seçilebilir.")
    return h


def _ad_dogrula(model, ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise FinansHatasi("Ad boş olamaz.")
    qs = model.objects.filter(silindi=False, ad=ad)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise FinansHatasi(f"Bu ad zaten kayıtlı: {ad}")
    return ad


# --- Kasa -------------------------------------------------------------------
def aktif_kasalar():
    return Kasa.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kasa_olustur(*, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    ad = _ad_dogrula(Kasa, ad)
    return Kasa.objects.create(
        ad=ad, para_birimi=(para_birimi or "TRY"),
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kasa_guncelle(k: Kasa, *, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(Kasa, ad, haric_pk=k.pk)
    k.para_birimi = (para_birimi or "TRY")
    k.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    k.updated_by = kullanici
    k.save(update_fields=["ad", "para_birimi", "muhasebe", "updated_by", "updated_at"])
    return k


def kasa_sil(k: Kasa, kullanici=None) -> Kasa:
    return _soft_sil(k, kullanici)


def _soft_sil(obj, kullanici=None):
    if obj.silindi:
        return obj
    obj.silindi = True
    obj.silindi_at = timezone.now()
    obj.updated_by = kullanici
    obj.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return obj


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise FinansHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise FinansHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if not pozitif and d < 0:
        raise FinansHatasi(f"{etiket} negatif olamaz.")
    return d


def _gun(deger, etiket):
    if deger in (None, ""):
        return None
    try:
        g = int(deger)
    except (TypeError, ValueError):
        raise FinansHatasi(f"{etiket} 1–31 arası bir gün olmalı.")
    if not 1 <= g <= 31:
        raise FinansHatasi(f"{etiket} 1–31 arası olmalı.")
    return g


def _cari_coz(cari_id):
    if cari_id in (None, ""):
        return None
    c = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if c is None:
        raise FinansHatasi("Cari bulunamadı.")
    return c


# --- Banka ------------------------------------------------------------------
def aktif_bankalar():
    return Banka.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def banka_olustur(*, ad, banka_adi="", sube="", hesap_no="", iban="", para_birimi="TRY",
                  muhasebe_kodu, kullanici=None) -> Banka:
    return Banka.objects.create(
        ad=_ad_dogrula(Banka, ad), banka_adi=buyuk_harf_tr((banka_adi or "").strip()),
        sube=buyuk_harf_tr((sube or "").strip()), hesap_no=(hesap_no or "").strip(),
        iban=(iban or "").strip().upper().replace(" ", ""), para_birimi=(para_birimi or "TRY"),
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu), created_by=kullanici, updated_by=kullanici)


def banka_guncelle(b: Banka, *, ad, banka_adi="", sube="", hesap_no="", iban="",
                   para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Banka:
    if b.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    b.ad = _ad_dogrula(Banka, ad, haric_pk=b.pk)
    b.banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    b.sube = buyuk_harf_tr((sube or "").strip())
    b.hesap_no = (hesap_no or "").strip()
    b.iban = (iban or "").strip().upper().replace(" ", "")
    b.para_birimi = (para_birimi or "TRY")
    b.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    b.updated_by = kullanici
    b.save(update_fields=["ad", "banka_adi", "sube", "hesap_no", "iban", "para_birimi",
                          "muhasebe", "updated_by", "updated_at"])
    return b


def banka_sil(b: Banka, kullanici=None) -> Banka:
    return _soft_sil(b, kullanici)


# --- Kredi Kartı ------------------------------------------------------------
def aktif_kredi_kartlari():
    return KrediKarti.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kredi_karti_olustur(*, ad, banka_adi="", kart_son4="", limit=0, kesim_gunu=None,
                        son_odeme_gunu=None, para_birimi="TRY", muhasebe_kodu,
                        kullanici=None) -> KrediKarti:
    return KrediKarti.objects.create(
        ad=_ad_dogrula(KrediKarti, ad), banka_adi=buyuk_harf_tr((banka_adi or "").strip()),
        kart_son4=(kart_son4 or "").strip(), limit=_sayi(limit, "Limit"),
        kesim_gunu=_gun(kesim_gunu, "Kesim günü"),
        son_odeme_gunu=_gun(son_odeme_gunu, "Son ödeme günü"),
        para_birimi=(para_birimi or "TRY"), muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kredi_karti_guncelle(k: KrediKarti, *, ad, banka_adi="", kart_son4="", limit=0,
                         kesim_gunu=None, son_odeme_gunu=None, para_birimi="TRY",
                         muhasebe_kodu, kullanici=None) -> KrediKarti:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(KrediKarti, ad, haric_pk=k.pk)
    k.banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    k.kart_son4 = (kart_son4 or "").strip()
    k.limit = _sayi(limit, "Limit")
    k.kesim_gunu = _gun(kesim_gunu, "Kesim günü")
    k.son_odeme_gunu = _gun(son_odeme_gunu, "Son ödeme günü")
    k.para_birimi = (para_birimi or "TRY")
    k.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    k.updated_by = kullanici
    k.save(update_fields=["ad", "banka_adi", "kart_son4", "limit", "kesim_gunu",
                          "son_odeme_gunu", "para_birimi", "muhasebe", "updated_by", "updated_at"])
    return k


def kredi_karti_sil(k: KrediKarti, kullanici=None) -> KrediKarti:
    return _soft_sil(k, kullanici)


# --- Kredi ------------------------------------------------------------------
def aktif_krediler():
    return Kredi.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kredi_olustur(*, ad, banka_adi="", anapara=0, faiz_orani=0, para_birimi="TRY",
                  muhasebe_kodu, kullanici=None) -> Kredi:
    return Kredi.objects.create(
        ad=_ad_dogrula(Kredi, ad), banka_adi=buyuk_harf_tr((banka_adi or "").strip()),
        anapara=_sayi(anapara, "Anapara"), faiz_orani=_sayi(faiz_orani, "Faiz oranı"),
        para_birimi=(para_birimi or "TRY"), muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kredi_guncelle(k: Kredi, *, ad, banka_adi="", anapara=0, faiz_orani=0, para_birimi="TRY",
                   muhasebe_kodu, kullanici=None) -> Kredi:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(Kredi, ad, haric_pk=k.pk)
    k.banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    k.anapara = _sayi(anapara, "Anapara")
    k.faiz_orani = _sayi(faiz_orani, "Faiz oranı")
    k.para_birimi = (para_birimi or "TRY")
    k.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    k.updated_by = kullanici
    k.save(update_fields=["ad", "banka_adi", "anapara", "faiz_orani", "para_birimi",
                          "muhasebe", "updated_by", "updated_at"])
    return k


def kredi_sil(k: Kredi, kullanici=None) -> Kredi:
    return _soft_sil(k, kullanici)


# --- Çek / Senet ------------------------------------------------------------
def aktif_cek_senetler():
    return (CekSenet.objects.filter(silindi=False)
            .select_related("muhasebe", "cari").order_by("vade", "-id"))


def _cek_alanlar(tip, yon, durum):
    if tip not in CekSenet.Tip.values:
        raise FinansHatasi("Tip Çek veya Senet olmalı.")
    if yon not in CekSenet.Yon.values:
        raise FinansHatasi("Yön Alınan veya Verilen olmalı.")
    if durum and durum not in CekSenet.Durum.values:
        raise FinansHatasi("Geçersiz durum.")
    return tip, yon, (durum or CekSenet.Durum.PORTFOYDE)


def cek_senet_olustur(*, tip, yon, tutar, vade, para_birimi="TRY", kesideci="", belge_no="",
                      durum="", muhasebe_kodu, cari_id=None, kullanici=None) -> CekSenet:
    tip, yon, durum = _cek_alanlar(tip, yon, durum)
    return CekSenet.objects.create(
        tip=tip, yon=yon, tutar=_sayi(tutar, "Tutar", pozitif=True), vade=vade,
        para_birimi=(para_birimi or "TRY"), kesideci=buyuk_harf_tr((kesideci or "").strip()),
        belge_no=(belge_no or "").strip(), durum=durum,
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu), cari=_cari_coz(cari_id),
        created_by=kullanici, updated_by=kullanici)


def cek_senet_guncelle(cs: CekSenet, *, tip, yon, tutar, vade, para_birimi="TRY", kesideci="",
                       belge_no="", durum="", muhasebe_kodu, cari_id=None,
                       kullanici=None) -> CekSenet:
    if cs.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    tip, yon, durum = _cek_alanlar(tip, yon, durum)
    cs.tip, cs.yon, cs.durum = tip, yon, durum
    cs.tutar = _sayi(tutar, "Tutar", pozitif=True)
    cs.vade = vade
    cs.para_birimi = (para_birimi or "TRY")
    cs.kesideci = buyuk_harf_tr((kesideci or "").strip())
    cs.belge_no = (belge_no or "").strip()
    cs.muhasebe = _yaprak_hesap_coz(muhasebe_kodu)
    cs.cari = _cari_coz(cari_id)
    cs.updated_by = kullanici
    cs.save(update_fields=["tip", "yon", "tutar", "vade", "para_birimi", "kesideci",
                           "belge_no", "durum", "muhasebe", "cari", "updated_by", "updated_at"])
    return cs


def cek_senet_sil(cs: CekSenet, kullanici=None) -> CekSenet:
    return _soft_sil(cs, kullanici)
