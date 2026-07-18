"""FİNANS modülü servis katmanı — Kasa/Banka/Kredi/Kredi Kartı TANIMLARI.

Her tanım bir YAPRAK muhasebe hesabına bağlanır; bakiye SAKLANMAZ, o hesabın
yevmiyesinden hesaplanır (cari/ekstre mantığı). İşlem motoru (tahsilat/ödeme) yok.
Ad TR büyük harfe çevrilir + silinmemişler arasında benzersiz.
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (Banka, BankaHesap, Cari, HesapPlani, Kasa, Kredi,
                         KrediKarti)
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


def _pb(para_birimi):
    """Para birimini doğrula (servis katmanı zorlar; UI'a güvenilmez). Boş → TRY."""
    pb = (para_birimi or "TRY")
    if pb not in dict(Cari.PARA_CHOICES):
        raise FinansHatasi(f"Geçersiz para birimi: {pb}")
    return pb


# --- Kasa -------------------------------------------------------------------
def aktif_kasalar():
    return Kasa.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kasa_olustur(*, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    ad = _ad_dogrula(Kasa, ad)
    return Kasa.objects.create(
        ad=ad, para_birimi=_pb(para_birimi),
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def _hareket_kilidi(obj, *, yeni_pb, yeni_muhasebe, ad):
    """Aktif kaynak fişi (hareket) olan tanımda muhasebe hesabı / para birimi DEĞİŞTİRİLEMEZ:
    bakiye yevmiyeden hesaplandığı için hesap/PB kayarsa eski hareketler ekstreden kaybolur ve
    kalan borç yanlışlanır (invariant: bakiyeler hesaplanır, saklanmaz). Diğer alanlar serbest."""
    if not obj.fisler.filter(silindi=False).exists():
        return
    if yeni_muhasebe.pk != obj.muhasebe_id:
        raise FinansHatasi(f"{ad} aktif hareket fişleri var; muhasebe hesabı değiştirilemez "
                           f"(önce hareketleri iptal edin).")
    if yeni_pb != obj.para_birimi:
        raise FinansHatasi(f"{ad} aktif hareket fişleri var; para birimi değiştirilemez "
                           f"(önce hareketleri iptal edin).")


def kasa_guncelle(k: Kasa, *, ad, para_birimi="TRY", muhasebe_kodu, kullanici=None) -> Kasa:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(Kasa, ad, haric_pk=k.pk)
    pb, hesap = _pb(para_birimi), _yaprak_hesap_coz(muhasebe_kodu)
    _hareket_kilidi(k, yeni_pb=pb, yeni_muhasebe=hesap, ad="Kasanın")
    k.para_birimi = pb
    k.muhasebe = hesap
    k.updated_by = kullanici
    k.save(update_fields=["ad", "para_birimi", "muhasebe", "updated_by", "updated_at"])
    return k


def kasa_sil(k: Kasa, kullanici=None) -> Kasa:
    if k.fisler.filter(silindi=False).exists():
        raise FinansHatasi("Bu kasanın aktif hareket fişleri var; önce hareketleri iptal edin.")
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


# --- Banka (kurum) ----------------------------------------------------------
def aktif_bankalar():
    return Banka.objects.filter(silindi=False).order_by("ad")


# Banka adı anahtar kelimesi -> havuzdaki (banka_logo/_havuz/) logo dosyası.
# Havuz `tasi_banka_logo` komutuyla doldurulur; logosuz banka açılınca/atanırken
# isim eşleşirse otomatik kullanılır (Vakıf vb. "ileriye hazır").
LOGO_HAVUZ = {"ZİRAAT": "ziraat.webp", "HALK": "halk.webp",
              "VAKIF": "vakif.webp", "VAKİF": "vakif.webp"}


def _havuz_logo(ad):
    """Banka adına göre havuzdan logo öner; varsa ContentFile döner, yoksa None."""
    from pathlib import Path
    from django.conf import settings
    from django.core.files.base import ContentFile
    ust = buyuk_harf_tr(ad or "")
    for anahtar, dosya in LOGO_HAVUZ.items():
        if anahtar in ust:
            yol = Path(settings.MEDIA_ROOT) / "banka_logo" / "_havuz" / dosya
            if yol.exists():
                return ContentFile(yol.read_bytes(), name=dosya)
    return None


def banka_olustur(*, ad, kisa_ad="", sube="", swift_kod="", musteri_no="", adres="",
                  logo=None, kullanici=None) -> Banka:
    ad_d = _ad_dogrula(Banka, ad)
    if logo is None:                       # logo yüklenmediyse havuzdan isimle öner
        logo = _havuz_logo(ad_d)
    return Banka.objects.create(
        ad=ad_d, kisa_ad=buyuk_harf_tr((kisa_ad or "").strip()),
        sube=buyuk_harf_tr((sube or "").strip()), swift_kod=(swift_kod or "").strip().upper(),
        musteri_no=(musteri_no or "").strip(), adres=(adres or "").strip(),
        logo=logo, created_by=kullanici, updated_by=kullanici)


def banka_guncelle(b: Banka, *, ad, kisa_ad="", sube="", swift_kod="", musteri_no="", adres="",
                   logo=None, kullanici=None) -> Banka:
    if b.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    b.ad = _ad_dogrula(Banka, ad, haric_pk=b.pk)
    b.kisa_ad = buyuk_harf_tr((kisa_ad or "").strip())
    b.sube = buyuk_harf_tr((sube or "").strip())
    b.swift_kod = (swift_kod or "").strip().upper()
    b.musteri_no = (musteri_no or "").strip()
    b.adres = (adres or "").strip()
    b.updated_by = kullanici
    alanlar = ["ad", "kisa_ad", "sube", "swift_kod", "musteri_no", "adres",
               "updated_by", "updated_at"]
    if logo is not None:                   # yalnız yeni logo yüklendiyse değiştir
        b.logo = logo
        alanlar.append("logo")
    b.save(update_fields=alanlar)
    return b


def banka_sil(b: Banka, kullanici=None) -> Banka:
    if b.silindi:
        return b
    if b.hesaplar.filter(silindi=False).exists():
        raise FinansHatasi("Bu bankaya bağlı hesap var; önce hesapları silin.")
    return _soft_sil(b, kullanici)


# --- Banka Hesabı (bankaya bağlı) -------------------------------------------
def banka_hesaplari(banka):
    return banka.hesaplar.filter(silindi=False).select_related("muhasebe").order_by("ad")


def _banka_hesap_ad(banka, ad, *, haric_pk=None):
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise FinansHatasi("Hesap adı boş olamaz.")
    qs = BankaHesap.objects.filter(silindi=False, banka=banka, ad=ad)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise FinansHatasi(f"Bu bankada '{ad}' adlı hesap zaten var.")
    return ad


def banka_hesap_olustur(*, banka, ad, hesap_no="", iban="", para_birimi="TRY",
                        muhasebe_kodu, kullanici=None) -> BankaHesap:
    return BankaHesap.objects.create(
        banka=banka, ad=_banka_hesap_ad(banka, ad), hesap_no=(hesap_no or "").strip(),
        iban=(iban or "").strip().upper().replace(" ", ""), para_birimi=_pb(para_birimi),
        muhasebe=_yaprak_hesap_coz(muhasebe_kodu), created_by=kullanici, updated_by=kullanici)


def banka_hesap_guncelle(h: BankaHesap, *, ad, hesap_no="", iban="", para_birimi="TRY",
                         muhasebe_kodu, kullanici=None) -> BankaHesap:
    if h.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    h.ad = _banka_hesap_ad(h.banka, ad, haric_pk=h.pk)
    h.hesap_no = (hesap_no or "").strip()
    h.iban = (iban or "").strip().upper().replace(" ", "")
    pb, hesap = _pb(para_birimi), _yaprak_hesap_coz(muhasebe_kodu)
    _hareket_kilidi(h, yeni_pb=pb, yeni_muhasebe=hesap, ad="Banka hesabının")
    h.para_birimi = pb
    h.muhasebe = hesap
    h.updated_by = kullanici
    h.save(update_fields=["ad", "hesap_no", "iban", "para_birimi", "muhasebe",
                          "updated_by", "updated_at"])
    return h


def banka_hesap_sil(h: BankaHesap, kullanici=None) -> BankaHesap:
    if h.fisler.filter(silindi=False).exists():
        raise FinansHatasi("Bu hesabın aktif hareket fişleri var; önce hareketleri iptal edin.")
    return _soft_sil(h, kullanici)


# --- Kredi Kartı ------------------------------------------------------------
def aktif_kredi_kartlari():
    return KrediKarti.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kredi_karti_olustur(*, ad, banka=None, kart_son4="", limit=0, kesim_gunu=None,
                        son_odeme_gunu=None, para_birimi="TRY", muhasebe_kodu,
                        kullanici=None) -> KrediKarti:
    return KrediKarti.objects.create(
        ad=_ad_dogrula(KrediKarti, ad), banka=banka,
        kart_son4=(kart_son4 or "").strip(), limit=_sayi(limit, "Limit"),
        kesim_gunu=_gun(kesim_gunu, "Kesim günü"),
        son_odeme_gunu=_gun(son_odeme_gunu, "Son ödeme günü"),
        para_birimi=_pb(para_birimi), muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kredi_karti_guncelle(k: KrediKarti, *, ad, banka=None, kart_son4="", limit=0,
                         kesim_gunu=None, son_odeme_gunu=None, para_birimi="TRY",
                         muhasebe_kodu, kullanici=None) -> KrediKarti:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(KrediKarti, ad, haric_pk=k.pk)
    k.banka = banka
    k.kart_son4 = (kart_son4 or "").strip()
    k.limit = _sayi(limit, "Limit")
    k.kesim_gunu = _gun(kesim_gunu, "Kesim günü")
    k.son_odeme_gunu = _gun(son_odeme_gunu, "Son ödeme günü")
    pb, hesap = _pb(para_birimi), _yaprak_hesap_coz(muhasebe_kodu)
    _hareket_kilidi(k, yeni_pb=pb, yeni_muhasebe=hesap, ad="Kartın")
    k.para_birimi = pb
    k.muhasebe = hesap
    k.updated_by = kullanici
    k.save(update_fields=["ad", "banka", "kart_son4", "limit", "kesim_gunu",
                          "son_odeme_gunu", "para_birimi", "muhasebe", "updated_by", "updated_at"])
    return k


def kredi_karti_sil(k: KrediKarti, kullanici=None) -> KrediKarti:
    if k.fisler.filter(silindi=False).exists():
        raise FinansHatasi("Bu kartın aktif hareket fişleri var; önce hareketleri iptal edin.")
    return _soft_sil(k, kullanici)


# --- Kredi ------------------------------------------------------------------
def aktif_krediler():
    return Kredi.objects.filter(silindi=False).select_related("muhasebe").order_by("ad")


def kredi_olustur(*, ad, banka=None, anapara=0, faiz_orani=0, para_birimi="TRY",
                  muhasebe_kodu, kullanici=None) -> Kredi:
    return Kredi.objects.create(
        ad=_ad_dogrula(Kredi, ad), banka=banka,
        anapara=_sayi(anapara, "Anapara"), faiz_orani=_sayi(faiz_orani, "Faiz oranı"),
        para_birimi=_pb(para_birimi), muhasebe=_yaprak_hesap_coz(muhasebe_kodu),
        created_by=kullanici, updated_by=kullanici)


def kredi_guncelle(k: Kredi, *, ad, banka=None, anapara=0, faiz_orani=0, para_birimi="TRY",
                   muhasebe_kodu, kullanici=None) -> Kredi:
    if k.silindi:
        raise FinansHatasi("Silinmiş kayıt düzenlenemez.")
    k.ad = _ad_dogrula(Kredi, ad, haric_pk=k.pk)
    k.banka = banka
    k.anapara = _sayi(anapara, "Anapara")
    k.faiz_orani = _sayi(faiz_orani, "Faiz oranı")
    pb, hesap = _pb(para_birimi), _yaprak_hesap_coz(muhasebe_kodu)
    _hareket_kilidi(k, yeni_pb=pb, yeni_muhasebe=hesap, ad="Kredinin")
    k.para_birimi = pb
    k.muhasebe = hesap
    k.updated_by = kullanici
    k.save(update_fields=["ad", "banka", "anapara", "faiz_orani", "para_birimi",
                          "muhasebe", "updated_by", "updated_at"])
    return k


def kredi_sil(k: Kredi, kullanici=None) -> Kredi:
    if k.fisler.filter(silindi=False).exists():
        raise FinansHatasi("Bu kredinin aktif hareket fişleri var; önce hareketleri iptal edin.")
    return _soft_sil(k, kullanici)
