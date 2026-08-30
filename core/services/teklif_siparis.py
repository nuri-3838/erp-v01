"""TEKLİF & SİPARİŞ servis katmanı — Satınalma/Satış teklifi ve siparişi.

TİCARİ belge: yevmiye fişi ÜRETMEZ, stok hareketi YARATMAZ (muhasebe ve stok her
zaman faturayla girer). belge_tur (Teklif/Sipariş) × yon (Alış/Satış) — dört ekranı
tek modelden besler. Kapsam: liste + oluştur + düzenle + iptal + görüntüle +
teklif→sipariş/sipariş→fatura dönüşümü + durum akışı (Taslak→Onaylı) + otomatik
(müteselsil) belge no.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max

from core.models import Cari, KdvOrani, Stok, TeklifSiparis, TeklifSiparisKalem
from core.sayi import SayiHatasi, parse_tr


class TeklifSiparisHatasi(ValueError):
    """Teklif/Sipariş kural ihlali (Türkçe mesaj)."""


# Belge no öneki, belge_tur × yon'a göre (SAT-2026-0001 gibi).
_BELGE_ONEK = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS): "SAT",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS): "SAS",
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS): "SST",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "SSS",
}


def _sayi(deger, etiket, *, pozitif=False):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise TeklifSiparisHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if pozitif and d <= 0:
        raise TeklifSiparisHatasi(f"{etiket} sıfırdan büyük olmalı.")
    if d < 0:
        raise TeklifSiparisHatasi(f"{etiket} negatif olamaz.")
    return d


def aktif_teklif_siparisler(belge_tur, yon):
    return (TeklifSiparis.objects.filter(silindi=False, belge_tur=belge_tur, yon=yon)
            .select_related("cari"))


def _hazirla(*, cari_id, satirlar):
    """Ortak hazırlık (oluştur): cari + satırları doğrula. (cari, hazir) döner —
    hazir = [(stok, miktar, birim_fiyat, kdv, tevkifat), ...]."""
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise TeklifSiparisHatasi("Cari bulunamadı.")
    if not satirlar:
        raise TeklifSiparisHatasi("En az bir kalem olmalı.")
    hazir = []
    for s in satirlar:
        stok = (Stok.objects.filter(pk=s["stok_id"], silindi=False)
                .select_related("kdv", "tevkifat").first())
        if stok is None:
            raise TeklifSiparisHatasi("Stok bulunamadı.")
        miktar = _sayi(s.get("miktar"), "Miktar", pozitif=True)
        birim_fiyat = _sayi(s.get("birim_fiyat"), "Birim fiyat")
        hazir.append((stok, miktar, birim_fiyat, stok.kdv, stok.tevkifat))
    return cari, hazir


def _pb_dogrula(para_birimi):
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise TeklifSiparisHatasi("Geçersiz para birimi.")
    return pb


def _kalemleri_yaz(ts, hazir, kullanici):
    for stok, miktar, birim_fiyat, kdv, tevkifat in hazir:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=ts, stok=stok, miktar=miktar, birim_fiyat=birim_fiyat, kdv=kdv,
            tevkifat=tevkifat, created_by=kullanici, updated_by=kullanici)


def _sonraki_sira(belge_tur, yon, yil):
    """Belge türü × yön × yıl içinde sıradaki sıra (iptaller dahil; numara yeniden kullanılmaz)."""
    m = (TeklifSiparis.objects.filter(belge_tur=belge_tur, yon=yon, yil=yil)
         .aggregate(m=Max("sira"))["m"])
    return (m or 0) + 1


def _belge_olustur(*, belge_tur, yon, cari, tarih, gecerlilik_teslim_tarihi, para_birimi,
                   aciklama, kaynak_teklif=None, kullanici=None) -> TeklifSiparis:
    """Numaralı başlık oluşturur: belge_no = ÖNEK-yıl-sıra (müteselsil/boşluksuz — fiş no ile
    aynı invariant, kullanıcı giremez/değiştiremez). Numara çakışırsa (eşzamanlı oluşturma)
    savepoint geri alınır, bir sonraki sırayla yeniden denenir (fis_olustur ile aynı desen)."""
    yil = tarih.year
    onek = _BELGE_ONEK[(belge_tur, yon)]
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(belge_tur, yon, yil)
                return TeklifSiparis.objects.create(
                    belge_tur=belge_tur, yon=yon, cari=cari, tarih=tarih,
                    gecerlilik_teslim_tarihi=gecerlilik_teslim_tarihi,
                    belge_no=f"{onek}-{yil}-{sira:04d}", yil=yil, sira=sira,
                    para_birimi=para_birimi, aciklama=(aciklama or "").strip(),
                    kaynak_teklif=kaynak_teklif, created_by=kullanici, updated_by=kullanici)
        except IntegrityError as e:
            if "uq_teklif_siparis_tur_yon_yil_sira" not in str(e):
                raise
            continue
    raise TeklifSiparisHatasi("Belge numarası üretilemedi; tekrar deneyin.")


@transaction.atomic
def teklif_siparis_olustur(*, belge_tur, yon, cari_id, tarih, satirlar,
                           gecerlilik_teslim_tarihi=None, para_birimi="TRY",
                           aciklama="", kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş başlığı + kalemlerini oluşturur. Yevmiye/stok hareketi ÜRETMEZ.
    Durum TASLAK başlar; belge_no otomatik (müteselsil) üretilir."""
    if belge_tur not in TeklifSiparis.BelgeTur.values:
        raise TeklifSiparisHatasi("Geçersiz belge türü.")
    if yon not in TeklifSiparis.Yon.values:
        raise TeklifSiparisHatasi("Geçersiz yön.")
    cari, hazir = _hazirla(cari_id=cari_id, satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    ts = _belge_olustur(belge_tur=belge_tur, yon=yon, cari=cari, tarih=tarih,
                        gecerlilik_teslim_tarihi=gecerlilik_teslim_tarihi,
                        para_birimi=pb, aciklama=aciklama, kullanici=kullanici)
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


@transaction.atomic
def teklif_siparis_guncelle(ts: TeklifSiparis, *, cari_id, tarih, satirlar,
                            gecerlilik_teslim_tarihi=None, para_birimi="TRY",
                            aciklama="", kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş başlığı + kalemlerini günceller (belge_tur/yon/belge_no SABİT — hangi
    ekrana ait olduğunu ve numarasını belirler, değişmez). Onaylı belge düzenlenemez (önce
    onayı geri alın). Eski kalemler soft-delete edilir, yenileri yazılır."""
    from django.utils import timezone
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge düzenlenemez.")
    if ts.durum == TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Onaylı belge düzenlenemez; önce onayı geri alın.")
    cari, hazir = _hazirla(cari_id=cari_id, satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    ts.kalemler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    ts.cari, ts.tarih = cari, tarih
    ts.gecerlilik_teslim_tarihi = gecerlilik_teslim_tarihi
    ts.para_birimi = pb
    ts.aciklama = (aciklama or "").strip()
    ts.updated_by = kullanici
    ts.save(update_fields=["cari", "tarih", "gecerlilik_teslim_tarihi",
                           "para_birimi", "aciklama", "updated_by", "updated_at"])
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


def teklif_siparis_onayla(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """TASLAK → ONAYLI. Yalnız onaylı belge siparişe/faturaya dönüştürülebilir. İptal
    edilmiş belge onaylanamaz; zaten onaylıysa sessiz (idempotent)."""
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge onaylanamaz.")
    if ts.durum == TeklifSiparis.Durum.ONAYLI:
        return ts
    ts.durum = TeklifSiparis.Durum.ONAYLI
    ts.updated_by = kullanici
    ts.save(update_fields=["durum", "updated_by", "updated_at"])
    return ts


def teklif_siparis_onayi_geri_al(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """ONAYLI → TASLAK. Zaten siparişe/faturaya dönüştürülmüş belgenin onayı geri alınamaz
    (zincirin bütünlüğü bozulur). İptal edilmişse hata; zaten taslaksa sessiz (idempotent)."""
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge için onay geri alınamaz.")
    if ts.durum == TeklifSiparis.Durum.TASLAK:
        return ts
    if (ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF
            and ts.donusen_siparisler.filter(silindi=False).exists()):
        raise TeklifSiparisHatasi("Bu teklif siparişe dönüştürülmüş; onayı geri alınamaz.")
    if ts.fatura_id:
        raise TeklifSiparisHatasi("Bu belge faturaya dönüştürülmüş; onayı geri alınamaz.")
    ts.durum = TeklifSiparis.Durum.TASLAK
    ts.updated_by = kullanici
    ts.save(update_fields=["durum", "updated_by", "updated_at"])
    return ts


@transaction.atomic
def teklifi_siparise_cevir(teklif: TeklifSiparis, *, tarih, kullanici=None) -> TeklifSiparis:
    """Teklifi siparişe çevirir: aynı cari/yön/para birimi, kalemler (stok/miktar/fiyat/KDV
    snapshot) kopyalanır. Yalnız aktif + ONAYLI TEKLİF + henüz dönüştürülmemiş teklif
    çevrilebilir (tek seferlik — servis katmanında zorlanır, DB kısıtı değil)."""
    if teklif.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş teklif siparişe çevrilemez.")
    if teklif.belge_tur != TeklifSiparis.BelgeTur.TEKLIF:
        raise TeklifSiparisHatasi("Yalnız teklif siparişe çevrilebilir.")
    if teklif.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı teklif siparişe çevrilebilir.")
    if teklif.donusen_siparisler.filter(silindi=False).exists():
        raise TeklifSiparisHatasi("Bu teklif zaten bir siparişe dönüştürülmüş.")
    kalemler = list(teklif.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("Teklifte kalem yok; sipariş oluşturulamaz.")
    siparis = _belge_olustur(belge_tur=TeklifSiparis.BelgeTur.SIPARIS, yon=teklif.yon,
                             cari=teklif.cari, tarih=tarih, gecerlilik_teslim_tarihi=None,
                             para_birimi=teklif.para_birimi, aciklama=teklif.aciklama,
                             kaynak_teklif=teklif, kullanici=kullanici)
    for k in kalemler:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=siparis, stok=k.stok, miktar=k.miktar, birim_fiyat=k.birim_fiyat,
            kdv=k.kdv, tevkifat=k.tevkifat, created_by=kullanici, updated_by=kullanici)
    return siparis


def teklif_siparis_iptal(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """Belgeyi soft-delete eder (kalemler kalır; geçmiş görüntüleme için)."""
    from django.utils import timezone
    if ts.silindi:
        return ts
    ts.silindi = True
    ts.silindi_at = timezone.now()
    ts.updated_by = kullanici
    ts.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ts
