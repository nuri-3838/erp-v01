"""TEKLİF & PROFORMA & SİPARİŞ & İRSALİYE servis katmanı — Satınalma/Satış teklifi,
(yalnız Satış) proforması, siparişi ve (yalnız Satınalma/Alış) irsaliyesi.

TEKLİF/PROFORMA/SİPARİŞ TİCARİ belge: yevmiye fişi ÜRETMEZ, stok hareketi YARATMAZ.
İRSALİYE ise BİLİNÇLİ istisna — onaylanınca GERÇEK stok girişi yazar (mal fatura
beklenmeden depoya girmiş sayılır). belge_tur × yon — ekranları tek modelden besler.
ALIŞ yönünde onay, otomasyon zincirini de tetikler: Teklif onaylanınca Taslak Sipariş,
Sipariş onaylanınca Taslak İrsaliye + stok girişi, İrsaliye onaylanınca Taslak Alış
Faturası — hepsi arka planda sessizce (bkz. teklif_siparis_onayla). SATIŞ yönünde
otomasyon yok, dönüşümler manuel ve üç aşamalı: Teklif → Proforma (teklifi_proformaya_
cevir) → Sipariş (proformayi_siparise_cevir, aday müşteride ENGELLENİR) →
core.services.fatura üzerinden Faturaya Çevir ekranı."""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max

from core.models import (AdayMusteri, Cari, Depo, KdvOrani, Stok, StokHareket, TanimSecenegi,
                         TeklifSiparis, TeklifSiparisKalem)
from core.sayi import SayiHatasi, parse_tr
from core.services.hareket import HareketHatasi, hareket_ekle, hareket_sil


class TeklifSiparisHatasi(ValueError):
    """Teklif/Sipariş kural ihlali (Türkçe mesaj)."""


# Belge no öneki, belge_tur × yon'a göre (SAT-2026-0001 gibi). (IRSALIYE, SATIS) bilinçli
# olarak YOK — otomasyon zinciri yalnız ALIŞ yönünde çalışır, bu kombinasyon hiç üretilmez.
# PROFORMA da yalnız SATIŞ'ta var (bkz. TeklifSiparis docstring'i, Teklif→Proforma→Sipariş).
_BELGE_ONEK = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.ALIS): "SAT",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.ALIS): "SAS",
    (TeklifSiparis.BelgeTur.IRSALIYE, TeklifSiparis.Yon.ALIS): "SAI",
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS): "SST",
    (TeklifSiparis.BelgeTur.PROFORMA, TeklifSiparis.Yon.SATIS): "SSP",
    (TeklifSiparis.BelgeTur.SIPARIS, TeklifSiparis.Yon.SATIS): "SSS",
}

# Aday müşteriye (CRM lead, henüz Cari değil) yalnız bu iki ekrandan belge açılabilir —
# ikisi de SATIŞ zincirinin henüz muhasebe/stok'a dokunmayan ilk iki aşaması. SIPARIS'e
# (ne satış ne alış) aday müşteri hiçbir zaman giremez (bkz. proformayi_siparise_cevir).
_ADAY_MUSTERI_IZINLI = {
    (TeklifSiparis.BelgeTur.TEKLIF, TeklifSiparis.Yon.SATIS),
    (TeklifSiparis.BelgeTur.PROFORMA, TeklifSiparis.Yon.SATIS),
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
            .select_related("cari", "aday_musteri"))


def _yuzde(deger, etiket):
    """0-100 arası ondalık doğrular; boş/None -> 0 (iskonto opsiyonel, varsayılan yok)."""
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise TeklifSiparisHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0 or d > 100:
        raise TeklifSiparisHatasi(f"{etiket} 0 ile 100 arasında olmalı.")
    return d


def _hazirla(*, cari_id=None, aday_musteri_id=None, satirlar):
    """Ortak hazırlık (oluştur/güncelle): cari VEYA aday müşteri (karşılıklı dışlayıcı —
    ikisi birden ya da hiçbiri verilemez) + satırları doğrular. (cari, aday_musteri, hazir)
    döner — hazir = [(stok, miktar, birim_fiyat, iskonto_yuzdesi, kdv, tevkifat), ...]."""
    if cari_id and aday_musteri_id:
        raise TeklifSiparisHatasi("Cari ve aday müşteri aynı anda seçilemez.")
    cari = aday = None
    if aday_musteri_id:
        aday = AdayMusteri.objects.filter(pk=aday_musteri_id, silindi=False).first()
        if aday is None:
            raise TeklifSiparisHatasi("Aday müşteri bulunamadı.")
    elif cari_id:
        cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
        if cari is None:
            raise TeklifSiparisHatasi("Cari bulunamadı.")
    else:
        raise TeklifSiparisHatasi("Cari veya aday müşteri seçilmeli.")
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
        iskonto_yuzdesi = _yuzde(s.get("iskonto_yuzdesi"), "İskonto %")
        hazir.append((stok, miktar, birim_fiyat, iskonto_yuzdesi, stok.kdv, stok.tevkifat))
    return cari, aday, hazir


def _pb_dogrula(para_birimi):
    pb = (para_birimi or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise TeklifSiparisHatasi("Geçersiz para birimi.")
    return pb


def _secenek_coz(pk, kategori):
    """Satış Teklifi'nin Tanım Listesi FK'ları: boşsa None; doluysa aktif + doğru kategoride
    bir TanimSecenegi olmalı (yanlış listeden pk gönderilemez)."""
    if not pk:
        return None
    s = TanimSecenegi.objects.filter(pk=pk, silindi=False, kategori=kategori).first()
    if s is None:
        raise TeklifSiparisHatasi(
            f"{TanimSecenegi.Kategori(kategori).label} seçeneği bulunamadı.")
    return s


def _navlun_coz(navlun_tutari):
    if navlun_tutari in (None, ""):
        return None
    return _sayi(navlun_tutari, "Navlun tutarı")


def _teklif_secenekleri(yukleme_sekli_id, odeme_kosulu_id, yukleme_tipi_id, navlun_tutari,
                        teslim_suresi_id=None):
    K = TanimSecenegi.Kategori
    return {
        "yukleme_sekli": _secenek_coz(yukleme_sekli_id, K.YUKLEME_SEKLI),
        "odeme_kosulu": _secenek_coz(odeme_kosulu_id, K.ODEME_KOSULU),
        "yukleme_tipi": _secenek_coz(yukleme_tipi_id, K.YUKLEME_TIPI),
        "teslim_suresi": _secenek_coz(teslim_suresi_id, K.TESLIM_SURESI),
        "navlun_tutari": _navlun_coz(navlun_tutari),
    }


def _depo_coz_irsaliye(belge_tur, depo_id):
    """İRSALİYE'de depo ZORUNLU (gerçek stok hareketi için); diğer belge türlerinde hep None."""
    if belge_tur != TeklifSiparis.BelgeTur.IRSALIYE:
        return None
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first() if depo_id else None
    if depo is None:
        raise TeklifSiparisHatasi("İrsaliye için depo seçilmeli.")
    return depo


def _kalemleri_yaz(ts, hazir, kullanici):
    for stok, miktar, birim_fiyat, iskonto_yuzdesi, kdv, tevkifat in hazir:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=ts, stok=stok, miktar=miktar, birim_fiyat=birim_fiyat,
            iskonto_yuzdesi=iskonto_yuzdesi, kdv=kdv,
            tevkifat=tevkifat, created_by=kullanici, updated_by=kullanici)


def _sonraki_sira(belge_tur, yon, yil):
    """Belge türü × yön × yıl içinde sıradaki sıra (iptaller dahil; numara yeniden kullanılmaz)."""
    m = (TeklifSiparis.objects.filter(belge_tur=belge_tur, yon=yon, yil=yil)
         .aggregate(m=Max("sira"))["m"])
    return (m or 0) + 1


def _belge_olustur(*, belge_tur, yon, cari=None, aday_musteri=None, tarih,
                   gecerlilik_teslim_tarihi, para_birimi,
                   aciklama, kaynak_teklif=None, kaynak_proforma=None, kaynak_siparis=None,
                   depo=None,
                   irsaliye_no="", yukleme_sekli=None, odeme_kosulu=None, yukleme_tipi=None,
                   teslim_suresi=None, navlun_tutari=None, kullanici=None) -> TeklifSiparis:
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
                    belge_tur=belge_tur, yon=yon, cari=cari, aday_musteri=aday_musteri,
                    tarih=tarih,
                    gecerlilik_teslim_tarihi=gecerlilik_teslim_tarihi,
                    belge_no=f"{onek}-{yil}-{sira:04d}", yil=yil, sira=sira,
                    para_birimi=para_birimi, aciklama=(aciklama or "").strip(),
                    kaynak_teklif=kaynak_teklif, kaynak_proforma=kaynak_proforma,
                    kaynak_siparis=kaynak_siparis, depo=depo,
                    irsaliye_no=(irsaliye_no or "").strip(),
                    yukleme_sekli=yukleme_sekli, odeme_kosulu=odeme_kosulu,
                    yukleme_tipi=yukleme_tipi, teslim_suresi=teslim_suresi,
                    navlun_tutari=navlun_tutari,
                    created_by=kullanici, updated_by=kullanici)
        except IntegrityError as e:
            if "uq_teklif_siparis_tur_yon_yil_sira" not in str(e):
                raise
            continue
    raise TeklifSiparisHatasi("Belge numarası üretilemedi; tekrar deneyin.")


def _aday_musteri_izin_kontrol(belge_tur, yon, aday_musteri_id):
    """Aday müşteriye yalnız SATIŞ+TEKLİF/PROFORMA ekranlarından belge açılabilir — CRM lead
    henüz gerçek Cari değil, Satınalma zincirlerinin ve Sipariş'in hiçbiri (muhasebe hesabı,
    stok hareketi, fatura) onunla çalışamaz (bkz. _ADAY_MUSTERI_IZINLI)."""
    if aday_musteri_id and (belge_tur, yon) not in _ADAY_MUSTERI_IZINLI:
        raise TeklifSiparisHatasi(
            "Aday müşteriye yalnızca satış teklifi veya proforması oluşturulabilir.")


@transaction.atomic
def teklif_siparis_olustur(*, belge_tur, yon, cari_id=None, aday_musteri_id=None, tarih,
                           satirlar, gecerlilik_teslim_tarihi=None, para_birimi="TRY",
                           aciklama="", depo_id=None, irsaliye_no="",
                           yukleme_sekli_id=None, odeme_kosulu_id=None, yukleme_tipi_id=None,
                           teslim_suresi_id=None,
                           navlun_tutari=None, kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş/İrsaliye başlığı + kalemlerini oluşturur. Yevmiye ÜRETMEZ; İRSALİYE
    stok hareketi de ÜRETMEZ (o yalnız onaylanınca — bkz. teklif_siparis_onayla). Durum
    TASLAK başlar; belge_no otomatik (müteselsil) üretilir."""
    if belge_tur not in TeklifSiparis.BelgeTur.values:
        raise TeklifSiparisHatasi("Geçersiz belge türü.")
    if yon not in TeklifSiparis.Yon.values:
        raise TeklifSiparisHatasi("Geçersiz yön.")
    _aday_musteri_izin_kontrol(belge_tur, yon, aday_musteri_id)
    cari, aday, hazir = _hazirla(cari_id=cari_id, aday_musteri_id=aday_musteri_id,
                                 satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    depo = _depo_coz_irsaliye(belge_tur, depo_id)
    secenekler = _teklif_secenekleri(yukleme_sekli_id, odeme_kosulu_id, yukleme_tipi_id,
                                     navlun_tutari, teslim_suresi_id)
    ts = _belge_olustur(belge_tur=belge_tur, yon=yon, cari=cari, aday_musteri=aday, tarih=tarih,
                        gecerlilik_teslim_tarihi=gecerlilik_teslim_tarihi,
                        irsaliye_no=irsaliye_no, **secenekler,
                        para_birimi=pb, aciklama=aciklama, depo=depo, kullanici=kullanici)
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


@transaction.atomic
def teklif_siparis_guncelle(ts: TeklifSiparis, *, cari_id=None, aday_musteri_id=None, tarih,
                            satirlar, gecerlilik_teslim_tarihi=None, para_birimi="TRY",
                            aciklama="", depo_id=None, irsaliye_no="",
                            yukleme_sekli_id=None, odeme_kosulu_id=None, yukleme_tipi_id=None,
                            teslim_suresi_id=None,
                            navlun_tutari=None, kullanici=None) -> TeklifSiparis:
    """Teklif/Sipariş/İrsaliye başlığı + kalemlerini günceller (belge_tur/yon/belge_no SABİT —
    hangi ekrana ait olduğunu ve numarasını belirler, değişmez). Onaylı belge düzenlenemez
    (önce onayı geri alın). Eski kalemler soft-delete edilir, yenileri yazılır."""
    from django.utils import timezone
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge düzenlenemez.")
    if ts.durum == TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Onaylı belge düzenlenemez; önce onayı geri alın.")
    _aday_musteri_izin_kontrol(ts.belge_tur, ts.yon, aday_musteri_id)
    cari, aday, hazir = _hazirla(cari_id=cari_id, aday_musteri_id=aday_musteri_id,
                                 satirlar=satirlar)
    pb = _pb_dogrula(para_birimi)
    depo = _depo_coz_irsaliye(ts.belge_tur, depo_id)
    ts.kalemler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    ts.cari, ts.aday_musteri, ts.tarih = cari, aday, tarih
    ts.gecerlilik_teslim_tarihi = gecerlilik_teslim_tarihi
    ts.para_birimi = pb
    ts.aciklama = (aciklama or "").strip()
    ts.depo = depo
    ts.irsaliye_no = (irsaliye_no or "").strip()
    for alan, deger in _teklif_secenekleri(yukleme_sekli_id, odeme_kosulu_id, yukleme_tipi_id,
                                           navlun_tutari, teslim_suresi_id).items():
        setattr(ts, alan, deger)
    ts.updated_by = kullanici
    ts.save(update_fields=["cari", "aday_musteri", "tarih", "gecerlilik_teslim_tarihi",
                           "para_birimi", "aciklama", "depo", "irsaliye_no", "yukleme_sekli",
                           "odeme_kosulu", "yukleme_tipi", "teslim_suresi", "navlun_tutari",
                           "updated_by", "updated_at"])
    _kalemleri_yaz(ts, hazir, kullanici)
    return ts


@transaction.atomic
def teklif_siparis_onayla(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """TASLAK → ONAYLI. İptal edilmiş belge onaylanamaz; zaten onaylıysa sessiz (idempotent).

    ALIŞ yönünde onay, otomasyon ZİNCİRİNİ de tetikler (kullanıcı isteği: Teklif onaylanınca
    Taslak Sipariş, Sipariş onaylanınca Taslak İrsaliye + gerçek stok girişi, İrsaliye
    onaylanınca Taslak Alış Faturası — hepsi ARKA PLANDA SESSİZCE, yönlendirme yok). SATIŞ
    yönünde hiçbir yan etkisi yok (yalnız durum flip). Tek atomik blok: zincirde bir yerde
    hata olursa (örn. aktif depo yok) durum flip'i de geri alınır, belge TASLAK kalır."""
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge onaylanamaz.")
    if ts.durum == TeklifSiparis.Durum.ONAYLI:
        return ts
    ts.durum = TeklifSiparis.Durum.ONAYLI
    ts.updated_by = kullanici
    ts.save(update_fields=["durum", "updated_by", "updated_at"])

    if ts.yon == TeklifSiparis.Yon.ALIS:
        if ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF:
            if not ts.donusen_belgeler.filter(silindi=False).exists():
                teklifi_siparise_cevir(ts, tarih=ts.tarih, kullanici=kullanici)
        elif ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS:
            if not ts.donusen_irsaliyeler.filter(silindi=False).exists():
                # FaturaForm ile aynı desen: önce ANA DEPO'yu dene, yoksa ilk aktif depo.
                aktif_depolar = Depo.objects.filter(silindi=False)
                depo = (aktif_depolar.filter(ad="ANA DEPO").first()
                       or aktif_depolar.order_by("kod").first())
                if depo is None:
                    raise TeklifSiparisHatasi(
                        "Aktif depo yok; sipariş irsaliyeye otomatik çevrilemedi. Önce "
                        "Stoklar > Depolar'dan bir depo tanımlayın.")
                siparisi_irsaliyeye_cevir(ts, tarih=ts.tarih, depo_id=depo.pk, kullanici=kullanici)
        elif ts.belge_tur == TeklifSiparis.BelgeTur.IRSALIYE:
            _irsaliye_stok_hareketi_yaz(ts, kullanici)
            if not ts.fatura_id:
                irsaliyeyi_faturaya_cevir(ts, kullanici=kullanici)
    return ts


def teklif_siparis_onayi_geri_al(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """ONAYLI → TASLAK. Zaten siparişe/irsaliyeye/faturaya dönüştürülmüş belgenin onayı geri
    alınamaz (zincirin bütünlüğü bozulur). İptal edilmişse hata; zaten taslaksa sessiz (idempotent)."""
    if ts.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş belge için onay geri alınamaz.")
    if ts.durum == TeklifSiparis.Durum.TASLAK:
        return ts
    if (ts.belge_tur == TeklifSiparis.BelgeTur.TEKLIF
            and ts.donusen_belgeler.filter(silindi=False).exists()):
        hedef = "siparişe" if ts.yon == TeklifSiparis.Yon.ALIS else "proformaya"
        raise TeklifSiparisHatasi(f"Bu teklif {hedef} dönüştürülmüş; onayı geri alınamaz.")
    if (ts.belge_tur == TeklifSiparis.BelgeTur.PROFORMA
            and ts.donusen_siparisler.filter(silindi=False).exists()):
        raise TeklifSiparisHatasi("Bu proforma siparişe dönüştürülmüş; onayı geri alınamaz.")
    if (ts.belge_tur == TeklifSiparis.BelgeTur.SIPARIS
            and ts.donusen_irsaliyeler.filter(silindi=False).exists()):
        raise TeklifSiparisHatasi("Bu sipariş irsaliyeye dönüştürülmüş; onayı geri alınamaz.")
    if ts.fatura_id:
        raise TeklifSiparisHatasi("Bu belge faturaya dönüştürülmüş; onayı geri alınamaz.")
    ts.durum = TeklifSiparis.Durum.TASLAK
    ts.updated_by = kullanici
    ts.save(update_fields=["durum", "updated_by", "updated_at"])
    return ts


@transaction.atomic
def teklifi_siparise_cevir(teklif: TeklifSiparis, *, tarih, kullanici=None) -> TeklifSiparis:
    """Teklifi siparişe DOĞRUDAN çevirir — yalnız ALIŞ zincirinde kullanılır (Satınalma
    Teklifi onaylanınca otomatik, bkz. teklif_siparis_onayla). SATIŞ yönünde artık bu
    doğrudan yol YOK — zincir Teklif → Proforma → Sipariş (bkz. teklifi_proformaya_cevir /
    proformayi_siparise_cevir); satış için buraya gelinmesi kapsam hatasıdır. Aynı cari/
    yön/para birimi, kalemler (stok/miktar/fiyat/KDV snapshot) kopyalanır. Yalnız aktif +
    ONAYLI TEKLİF + henüz dönüştürülmemiş teklif çevrilebilir (tek seferlik)."""
    if teklif.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş teklif siparişe çevrilemez.")
    if teklif.belge_tur != TeklifSiparis.BelgeTur.TEKLIF:
        raise TeklifSiparisHatasi("Yalnız teklif siparişe çevrilebilir.")
    if teklif.yon != TeklifSiparis.Yon.ALIS:
        raise TeklifSiparisHatasi(
            "Satış teklifi doğrudan siparişe çevrilemez; önce proformaya çevrilmeli.")
    if teklif.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı teklif siparişe çevrilebilir.")
    if teklif.donusen_belgeler.filter(silindi=False).exists():
        raise TeklifSiparisHatasi("Bu teklif zaten bir siparişe dönüştürülmüş.")
    kalemler = list(teklif.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("Teklifte kalem yok; sipariş oluşturulamaz.")
    siparis = _belge_olustur(belge_tur=TeklifSiparis.BelgeTur.SIPARIS, yon=teklif.yon,
                             cari=teklif.cari, tarih=tarih, gecerlilik_teslim_tarihi=None,
                             para_birimi=teklif.para_birimi, aciklama=teklif.aciklama,
                             yukleme_sekli=teklif.yukleme_sekli, odeme_kosulu=teklif.odeme_kosulu,
                             yukleme_tipi=teklif.yukleme_tipi, navlun_tutari=teklif.navlun_tutari,
                             kaynak_teklif=teklif, kullanici=kullanici)
    for k in kalemler:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=siparis, stok=k.stok, miktar=k.miktar, birim_fiyat=k.birim_fiyat,
            iskonto_yuzdesi=k.iskonto_yuzdesi, kdv=k.kdv, tevkifat=k.tevkifat,
            created_by=kullanici, updated_by=kullanici)
    return siparis


@transaction.atomic
def teklifi_proformaya_cevir(teklif: TeklifSiparis, *, tarih, kullanici=None) -> TeklifSiparis:
    """Satış teklifini proformaya çevirir: aynı cari/aday müşteri/para birimi, kalemler
    (stok/miktar/fiyat/iskonto/KDV snapshot) kopyalanır — miktarlar burada teklifteki gibi
    kalır, müşterinin istediği GERÇEK adede Düzenle ekranından güncellenir. Yalnız aktif +
    ONAYLI + SATIŞ yönünde TEKLİF + henüz dönüştürülmemiş teklif çevrilebilir (tek seferlik).
    Aday müşteride de çalışır — proforma aşaması henüz muhasebe/stok'a dokunmaz (bkz.
    _ADAY_MUSTERI_IZINLI); Cariye dönüşüm zorunluluğu bir sonraki adımda, Siparişe
    çevrilirken devreye girer (bkz. proformayi_siparise_cevir)."""
    if teklif.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş teklif proformaya çevrilemez.")
    if teklif.belge_tur != TeklifSiparis.BelgeTur.TEKLIF or teklif.yon != TeklifSiparis.Yon.SATIS:
        raise TeklifSiparisHatasi("Yalnız satış teklifi proformaya çevrilebilir.")
    if teklif.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı teklif proformaya çevrilebilir.")
    if teklif.donusen_belgeler.filter(silindi=False).exists():
        raise TeklifSiparisHatasi("Bu teklif zaten bir proformaya dönüştürülmüş.")
    kalemler = list(teklif.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("Teklifte kalem yok; proforma oluşturulamaz.")
    proforma = _belge_olustur(belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=teklif.yon,
                              cari=teklif.cari, aday_musteri=teklif.aday_musteri, tarih=tarih,
                              gecerlilik_teslim_tarihi=None, para_birimi=teklif.para_birimi,
                              aciklama=teklif.aciklama,
                              yukleme_sekli=teklif.yukleme_sekli, odeme_kosulu=teklif.odeme_kosulu,
                              yukleme_tipi=teklif.yukleme_tipi, navlun_tutari=teklif.navlun_tutari,
                              kaynak_teklif=teklif, kullanici=kullanici)
    for k in kalemler:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=proforma, stok=k.stok, miktar=k.miktar, birim_fiyat=k.birim_fiyat,
            iskonto_yuzdesi=k.iskonto_yuzdesi, kdv=k.kdv, tevkifat=k.tevkifat,
            created_by=kullanici, updated_by=kullanici)
    return proforma


@transaction.atomic
def proformayi_siparise_cevir(proforma: TeklifSiparis, *, tarih, kullanici=None) -> TeklifSiparis:
    """Proformayı siparişe çevirir: kalemler (gerçek miktarlarıyla) kopyalanır. Aday
    müşteriye ait proforma, aday HENÜZ Cariye dönüştürülmediyse ÇEVRİLEMEZ — sipariş artık
    gerçek zincire (fatura/muhasebe) giden bir belge. Aday bu arada Cariye dönüştürülmüşse
    (bkz. core.services.aday.aday_cariye_donustur) proformanın KENDİ cari alanı geriye
    dönük güncellenmez — bu yüzden burada aday_musteri.donusen_cari'ye her seferinde taze
    bakılır, dönüşüm o anda tamamlanmışsa sipariş o gerçek cariyle açılır. Yalnız aktif +
    ONAYLI PROFORMA + henüz dönüştürülmemiş proforma çevrilebilir (tek seferlik)."""
    if proforma.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş proforma siparişe çevrilemez.")
    if proforma.belge_tur != TeklifSiparis.BelgeTur.PROFORMA:
        raise TeklifSiparisHatasi("Yalnız proforma siparişe çevrilebilir.")
    if proforma.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı proforma siparişe çevrilebilir.")
    if proforma.donusen_siparisler.filter(silindi=False).exists():
        raise TeklifSiparisHatasi("Bu proforma zaten bir siparişe dönüştürülmüş.")
    cari = proforma.cari
    if cari is None and proforma.aday_musteri_id:
        # proforma.aday_musteri Django'nun ilişki önbelleğinde oluşturulduğu andaki (henüz
        # dönüştürülmemiş) haliyle takılı kalabilir — taze bir sorguyla okunur.
        cari = (AdayMusteri.objects.filter(pk=proforma.aday_musteri_id)
                .select_related("donusen_cari").first().donusen_cari)
    if cari is None:
        raise TeklifSiparisHatasi(
            "Bu proforma bir aday müşteriye ait; siparişe çevirmeden önce aday müşteriyi "
            "cariye dönüştürün.")
    kalemler = list(proforma.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("Proformada kalem yok; sipariş oluşturulamaz.")
    siparis = _belge_olustur(belge_tur=TeklifSiparis.BelgeTur.SIPARIS, yon=proforma.yon,
                             cari=cari, tarih=tarih, gecerlilik_teslim_tarihi=None,
                             para_birimi=proforma.para_birimi, aciklama=proforma.aciklama,
                             yukleme_sekli=proforma.yukleme_sekli,
                             odeme_kosulu=proforma.odeme_kosulu,
                             yukleme_tipi=proforma.yukleme_tipi,
                             navlun_tutari=proforma.navlun_tutari,
                             kaynak_proforma=proforma, kullanici=kullanici)
    for k in kalemler:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=siparis, stok=k.stok, miktar=k.miktar, birim_fiyat=k.birim_fiyat,
            iskonto_yuzdesi=k.iskonto_yuzdesi, kdv=k.kdv, tevkifat=k.tevkifat,
            created_by=kullanici, updated_by=kullanici)
    return siparis


@transaction.atomic
def siparisi_irsaliyeye_cevir(siparis: TeklifSiparis, *, tarih, depo_id,
                              kullanici=None) -> TeklifSiparis:
    """Siparişi irsaliyeye çevirir: aynı cari/yön/para birimi, kalemler (stok/miktar/fiyat/
    KDV/tevkifat snapshot) kopyalanır. Yalnız aktif + ONAYLI SİPARİŞ + henüz dönüştürülmemiş
    sipariş çevrilebilir (tek seferlik). Gerçek stok hareketi burada DEĞİL — irsaliyenin
    KENDİSİ onaylanınca yazılır (bkz. _irsaliye_stok_hareketi_yaz)."""
    if siparis.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş sipariş irsaliyeye çevrilemez.")
    if siparis.belge_tur != TeklifSiparis.BelgeTur.SIPARIS:
        raise TeklifSiparisHatasi("Yalnız sipariş irsaliyeye çevrilebilir.")
    if siparis.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı sipariş irsaliyeye çevrilebilir.")
    if siparis.donusen_irsaliyeler.filter(silindi=False).exists():
        raise TeklifSiparisHatasi("Bu sipariş zaten bir irsaliyeye dönüştürülmüş.")
    kalemler = list(siparis.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("Siparişte kalem yok; irsaliye oluşturulamaz.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise TeklifSiparisHatasi("Depo bulunamadı.")
    irsaliye = _belge_olustur(belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, yon=siparis.yon,
                              cari=siparis.cari, tarih=tarih, gecerlilik_teslim_tarihi=None,
                              para_birimi=siparis.para_birimi, aciklama=siparis.aciklama,
                              yukleme_sekli=siparis.yukleme_sekli, odeme_kosulu=siparis.odeme_kosulu,
                              yukleme_tipi=siparis.yukleme_tipi, navlun_tutari=siparis.navlun_tutari,
                              kaynak_siparis=siparis, depo=depo, kullanici=kullanici)
    for k in kalemler:
        TeklifSiparisKalem.objects.create(
            teklif_siparis=irsaliye, stok=k.stok, miktar=k.miktar, birim_fiyat=k.birim_fiyat,
            iskonto_yuzdesi=k.iskonto_yuzdesi, kdv=k.kdv, tevkifat=k.tevkifat,
            created_by=kullanici, updated_by=kullanici)
    return irsaliye


def _irsaliye_stok_hareketi_yaz(irsaliye: TeklifSiparis, kullanici):
    """İrsaliye onaylanınca: kalemleri için GERÇEK giriş stok hareketi (mal depoya girmiş
    sayılır — fatura beklenmez, bkz. CLAUDE.md'nin bu zincire özel bilinçli istisnası).
    Miktar üretim birimine çevrilir (fatura._hareketleri_yaz ile aynı desen)."""
    from core.sayi import yuvarla
    for k in irsaliye.kalemler.filter(silindi=False).select_related("stok"):
        cevirici = k.stok.cevirici or Decimal("1")
        uretim_miktar = yuvarla(k.miktar / cevirici, 3)
        if uretim_miktar <= 0:
            raise TeklifSiparisHatasi(
                f"{k.stok.kod}: çevirici ({cevirici}) ile dönüştürülen miktar sıfır oluyor; "
                f"miktarı veya çeviriciyi düzeltin.")
        try:
            hareket_ekle(
                stok_id=k.stok_id, depo_id=irsaliye.depo_id, tarih=irsaliye.tarih,
                tur=StokHareket.Tur.GIRIS, miktar=uretim_miktar,
                aciklama=f"{irsaliye.belge_no} irsaliyesi — {irsaliye.cari.unvan}",
                kaynak=StokHareket.Kaynak.IRSALIYE, teklif_siparis_kalem=k, kullanici=kullanici)
        except HareketHatasi as e:
            raise TeklifSiparisHatasi(str(e))


@transaction.atomic
def irsaliyeyi_faturaya_cevir(irsaliye: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """İrsaliye onaylanınca: TASLAK Alış Faturası açar (tip BİLİNMEZ — kullanıcı Faturalar
    ekranından bulup tip seçip onaylar). KDV/tevkifat stoktan taze çekilir (siparis_faturaya_
    cevir'in bugün yaptığı gibi — yalnız stok_id/miktar/birim_fiyat geçirilir)."""
    from core.services import fatura as fatura_servis

    if irsaliye.silindi:
        raise TeklifSiparisHatasi("İptal edilmiş irsaliye faturaya çevrilemez.")
    if irsaliye.belge_tur != TeklifSiparis.BelgeTur.IRSALIYE:
        raise TeklifSiparisHatasi("Yalnız irsaliye faturaya çevrilebilir.")
    if irsaliye.durum != TeklifSiparis.Durum.ONAYLI:
        raise TeklifSiparisHatasi("Yalnız onaylı irsaliye faturaya çevrilebilir.")
    if irsaliye.fatura_id:
        raise TeklifSiparisHatasi("Bu irsaliye zaten bir faturaya dönüştürülmüş.")
    kalemler = list(irsaliye.kalemler.filter(silindi=False))
    if not kalemler:
        raise TeklifSiparisHatasi("İrsaliyede kalem yok; fatura oluşturulamaz.")
    satirlar = [{"stok_id": k.stok_id, "miktar": k.miktar, "birim_fiyat": k.birim_fiyat}
                for k in kalemler]
    fatura = fatura_servis.fatura_taslak_olustur(
        cari_id=irsaliye.cari_id, tarih=irsaliye.tarih, satirlar=satirlar,
        tip_id=None, yon=irsaliye.yon, para_birimi=irsaliye.para_birimi,
        depo_id=irsaliye.depo_id, kullanici=kullanici)
    irsaliye.fatura = fatura
    irsaliye.updated_by = kullanici
    irsaliye.save(update_fields=["fatura", "updated_by", "updated_at"])
    return irsaliye


def _irsaliye_hareketleri_iptal(irsaliye: TeklifSiparis, kullanici):
    """İrsaliye iptal edilince onun onaylanınca yazdığı GERÇEK stok girişini de geri alır
    (hareket_sil'in kendi negatif-eldeki koruması aynen geçerli — bir stok+depoda eldeki
    miktarı negatife düşürüyorsa TeklifSiparisHatasi ile iptal engellenir)."""
    hareketler = list(StokHareket.objects.filter(
        teklif_siparis_kalem__teklif_siparis=irsaliye, silindi=False))
    for h in hareketler:
        try:
            hareket_sil(h, kullanici=kullanici)
        except HareketHatasi as e:
            raise TeklifSiparisHatasi(str(e))


@transaction.atomic
def teklif_siparis_iptal(ts: TeklifSiparis, kullanici=None) -> TeklifSiparis:
    """Belgeyi soft-delete eder (kalemler kalır; geçmiş görüntüleme için). İRSALİYE ise,
    onaylanınca yazdığı GERÇEK stok girişi de geri alınır (bir stok+depoda eldeki miktarı
    negatife düşürüyorsa iptal engellenir — bkz. _irsaliye_hareketleri_iptal)."""
    from django.utils import timezone
    if ts.silindi:
        return ts
    if ts.belge_tur == TeklifSiparis.BelgeTur.IRSALIYE:
        _irsaliye_hareketleri_iptal(ts, kullanici)
    ts.silindi = True
    ts.silindi_at = timezone.now()
    ts.updated_by = kullanici
    ts.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ts
