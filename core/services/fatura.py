"""Fatura (FATURALAR) servis katmanı — Alış/Satış faturasından OTOMATİK yevmiye.

Fatura kaydedildiğinde dengeli bir yevmiye fişi üretilir (mevcut fis_olustur ile)
ve faturaya bağlanır. Muhasebe haritası:
  - Mal/gelir hesabı  = stok kategorisi × fatura tipi (KategoriHesap).
  - KDV hesabı         = stoğun KDV oranının BORÇ (alış 191) / ALACAK (satış 391) hesabı.
  - Karşı taraf        = carinin muhasebe hesabı (320.../120... yaprak).
ALIŞ:  Borç mal + Borç KDV  / Alacak cari.
SATIŞ: Alacak gelir + Alacak KDV / Borç cari.

İlk dilim: TL (kur=1). Tutarlar satırlardan; her şey atomik (eksik harita -> hiç kayıt yok).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (Cari, Depo, Fatura, FaturaSatir, FaturaTipi, HesapPlani,
                         KategoriHesap, Kur, Stok, StokHareket, YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.hareket import HareketHatasi, hareket_ekle
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_guncelle,
                                   fis_iptal, fis_olustur)

SIFIR = Decimal("0.00")


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


def _kur_coz(pb, tarih):
    """Fatura para biriminin fiş tarihindeki TCMB alış kuru. TRY -> 1.
    Döviz için o tarihin KUR kaydı ve ilgili PB alanı dolu olmalı (carry-forward yok)."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise FaturaHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi çekmeden "
            f"döviz faturası kesilemez.")
    return deger


def _hazirla(*, tip_id, cari_id, tarih, satirlar, para_birimi):
    """Ortak hazırlık (oluştur+güncelle): doğrula, kur çöz, yevmiye satırlarını ve
    FaturaSatir verisini kur. (tip, cari, pb, kur, yevmiye_satirlari, hazir) döner."""
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
    kur = _kur_coz(pb, tarih)

    yevmiye_satirlari = []
    kdv_hesap_toplam = {}          # hesap_kodu -> KDV tutarı (PB) [alışta tam, satışta net]
    tevkifat_hesap_toplam = {}     # hesap_kodu -> tevkifat tutarı (PB) [yalnız ALIŞ -> 360]
    borc_tl = SIFIR               # cari HARİÇ borç satırlarının TL toplamı
    alacak_tl = SIFIR             # cari HARİÇ alacak satırlarının TL toplamı
    cari_pb = SIFIR               # carinin PB tutarı = mal + (KDV − tevkifat)
    hazir = []                     # FaturaSatir için (stok, miktar, fiyat, kdv, tevkifat)

    def _ekle(taraf, tutar_pb):
        nonlocal borc_tl, alacak_tl
        tl = yuvarla(tutar_pb * kur, 2)
        if taraf == "B":
            borc_tl += tl
        else:
            alacak_tl += tl

    for i, g in enumerate(satirlar, start=1):
        stok = Stok.objects.filter(pk=g.get("stok_id"), silindi=False).first()
        if stok is None:
            raise FaturaHatasi(f"Satır {i}: stok bulunamadı.")
        miktar = _sayi(g.get("miktar"), f"Satır {i} miktar", pozitif=True)
        birim = _sayi(g.get("birim_fiyat"), f"Satır {i} birim fiyat")

        # Mal/gelir hesabı: kategori × fatura tipi
        kh = KategoriHesap.objects.filter(
            kategori=stok.kategori, fatura_tipi=tip, silindi=False).first()
        if kh is None:
            raise FaturaHatasi(
                f"Satır {i}: {stok.kod} kategorisinin '{tip.ad}' için muhasebe hesabı "
                f"tanımlı değil (STOKLAR > Kategoriler'den bağlayın).")

        satir_tutar = yuvarla(miktar * birim, 2)
        kdv = stok.kdv
        oran = kdv.oran if kdv else SIFIR
        satir_kdv = yuvarla(satir_tutar * oran / Decimal("100"), 2)

        # Tevkifat (varsa): KDV'nin pay/payda kadarı
        tevkifat = stok.tevkifat
        tev = SIFIR
        if tevkifat and tevkifat.payda and satir_kdv > 0:
            tev = yuvarla(satir_kdv * Decimal(tevkifat.pay) / Decimal(tevkifat.payda), 2)
        kdv_net = satir_kdv - tev      # cariye yansıyan KDV

        # Mal/gelir satırı (alış: Borç, satış: Alacak)
        mal_taraf = "B" if alis else "A"
        _ekle(mal_taraf, satir_tutar)
        yevmiye_satirlari.append(SatirGirdi(
            hesap_kodu=kh.hesap.hesap_kodu, taraf=mal_taraf,
            islem_tutari=satir_tutar, islem_pb=pb, islem_kuru=kur, aciklama=stok.ad))

        # KDV hesabı — ALIŞ: 191 TAM KDV (borç); SATIŞ: 391 NET KDV (alacak)
        kdv_post = satir_kdv if alis else kdv_net
        if kdv_post > 0:
            if kdv is None:
                raise FaturaHatasi(f"Satır {i}: {stok.kod} için KDV oranı tanımlı değil.")
            kdv_hesap = kdv.hesap_borc if alis else kdv.hesap_alacak
            if kdv_hesap is None:
                yer = "borç (İndirilecek)" if alis else "alacak (Hesaplanan)"
                raise FaturaHatasi(
                    f"Satır {i}: %{oran} KDV oranının {yer} hesabı tanımlı değil "
                    f"(AYARLAR > KDV Oranları).")
            kdv_hesap_toplam[kdv_hesap.hesap_kodu] = (
                kdv_hesap_toplam.get(kdv_hesap.hesap_kodu, SIFIR) + kdv_post)

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
        hazir.append((stok, miktar, birim, kdv, tevkifat))

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

    # Karşı taraf (cari): alış -> Alacak, satış -> Borç. TL'si DENGE için diğer
    # satırların TL'sinden türetilir (tl_override) -> döviz kuruş farkı oluşmaz.
    cari_taraf = "A" if alis else "B"
    cari_tl = (borc_tl - alacak_tl) if cari_taraf == "A" else (alacak_tl - borc_tl)
    yevmiye_satirlari.append(SatirGirdi(
        hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
        islem_tutari=cari_pb, islem_pb=pb, islem_kuru=kur, aciklama=cari.unvan,
        tl_override=cari_tl))

    return tip, cari, pb, kur, yevmiye_satirlari, hazir


def _aciklama(tip, cari, fatura_no):
    return buyuk_harf_tr(f"{tip.ad} - {cari.unvan}" + (f" - {fatura_no}" if fatura_no else ""))


def _satirlari_yaz(fatura, hazir, kullanici):
    for stok, miktar, birim, kdv, tevkifat in hazir:
        FaturaSatir.objects.create(
            fatura=fatura, stok=stok, miktar=miktar, birim_fiyat=birim, kdv=kdv,
            tevkifat=tevkifat, created_by=kullanici, updated_by=kullanici)


def _depo_coz(depo_id):
    """depo_id boşsa None (hareket üretilmez); doluysa aktif depoyu çözer."""
    if depo_id in (None, ""):
        return None
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise FaturaHatasi("Depo bulunamadı.")
    return depo


def _hareketleri_yaz(fatura, depo, *, kullanici):
    """Fatura kalemleri için stok hareketi: ALIŞ→giriş, SATIŞ→çıkış. Miktar fatura
    biriminden üretim birimine çevrilir (çevirici). Çıkışta eldeki yetmezse engellenir."""
    alis = (fatura.tip.yon == FaturaTipi.Yon.ALIS)
    tur = StokHareket.Tur.GIRIS if alis else StokHareket.Tur.CIKIS
    for satir in fatura.satirlar.filter(silindi=False).select_related("stok"):
        cevirici = satir.stok.cevirici or Decimal("1")
        uretim_miktar = yuvarla(satir.miktar / cevirici, 3)
        if uretim_miktar <= 0:
            continue
        try:
            hareket_ekle(
                stok_id=satir.stok_id, depo_id=depo.pk, tarih=fatura.tarih, tur=tur,
                miktar=uretim_miktar,
                aciklama=_aciklama(fatura.tip, fatura.cari, fatura.fatura_no),
                kaynak=StokHareket.Kaynak.FATURA, fatura_satir=satir, kullanici=kullanici)
        except HareketHatasi as e:
            raise FaturaHatasi(str(e))


def _hareketleri_iptal(fatura, *, kullanici):
    """Faturaya bağlı silinmemiş stok hareketlerini soft-delete eder."""
    from django.utils import timezone
    StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)


@transaction.atomic
def fatura_olustur(*, tip_id, cari_id, tarih, satirlar, fatura_no="",
                   para_birimi="TRY", depo_id=None, kullanici=None) -> Fatura:
    """Faturayı + otomatik dengeli yevmiye fişini + (depo verildiyse) stok hareketlerini
    atomik oluşturur. Para birimi TRY değilse kur fiş tarihinin TCMB kurundan çözülür.
    Eksik harita / kur yok / dengesizlik / yetersiz stok -> FaturaHatasi (hiçbir şey kaydedilmez)."""
    tip, cari, pb, kur, yevmiye_satirlari, hazir = _hazirla(
        tip_id=tip_id, cari_id=cari_id, tarih=tarih, satirlar=satirlar,
        para_birimi=para_birimi)
    depo = _depo_coz(depo_id)
    fatura_no = (fatura_no or "").strip()
    try:
        fis = fis_olustur(tarih=tarih, satirlar=yevmiye_satirlari,
                          aciklama=_aciklama(tip, cari, fatura_no), kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.FATURA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    fatura = Fatura.objects.create(
        tip=tip, cari=cari, tarih=tarih, fatura_no=fatura_no, para_birimi=pb,
        kur=kur, fis=fis, depo=depo, created_by=kullanici, updated_by=kullanici)
    _satirlari_yaz(fatura, hazir, kullanici)
    if depo is not None:
        _hareketleri_yaz(fatura, depo, kullanici=kullanici)
    return fatura


@transaction.atomic
def fatura_guncelle(fatura: Fatura, *, tip_id, cari_id, tarih, satirlar,
                    fatura_no="", para_birimi="TRY", depo_id=None, kullanici=None) -> Fatura:
    """Faturayı + bağlı yevmiye fişini + stok hareketlerini günceller (fiş no/yıl korunur).
    Eski FaturaSatır, fiş satırları ve stok hareketleri soft-delete edilir, yenileri yazılır."""
    from django.utils import timezone
    if fatura.silindi:
        raise FaturaHatasi("Silinmiş fatura düzenlenemez.")
    if fatura.fis_id is None or fatura.fis.silindi:
        raise FaturaHatasi("Faturanın aktif yevmiye fişi yok; düzenlenemez.")
    tip, cari, pb, kur, yevmiye_satirlari, hazir = _hazirla(
        tip_id=tip_id, cari_id=cari_id, tarih=tarih, satirlar=satirlar,
        para_birimi=para_birimi)
    depo = _depo_coz(depo_id)
    fatura_no = (fatura_no or "").strip()
    try:
        fis_guncelle(fatura.fis, tarih=tarih, satirlar=yevmiye_satirlari,
                     aciklama=_aciklama(tip, cari, fatura_no), kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise FaturaHatasi(str(e))
    # Eski stok hareketleri + satırları geri al (yeni çıkış kontrolü doğru eldekiyi görsün)
    _hareketleri_iptal(fatura, kullanici=kullanici)
    fatura.satirlar.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    fatura.tip, fatura.cari, fatura.tarih = tip, cari, tarih
    fatura.fatura_no, fatura.para_birimi, fatura.kur, fatura.depo = fatura_no, pb, kur, depo
    fatura.updated_by = kullanici
    fatura.save(update_fields=["tip", "cari", "tarih", "fatura_no", "para_birimi",
                               "kur", "depo", "updated_by", "updated_at"])
    _satirlari_yaz(fatura, hazir, kullanici)
    if depo is not None:
        _hareketleri_yaz(fatura, depo, kullanici=kullanici)
    return fatura


@transaction.atomic
def fatura_iptal(fatura: Fatura, kullanici=None) -> Fatura:
    """Faturayı soft-delete eder; bağlı yevmiye fişini ve stok hareketlerini de iptal eder."""
    from django.utils import timezone
    if fatura.silindi:
        return fatura
    if fatura.fis_id and not fatura.fis.silindi:
        fis_iptal(fatura.fis, kullanici=kullanici)
    _hareketleri_iptal(fatura, kullanici=kullanici)
    fatura.silindi = True
    fatura.silindi_at = timezone.now()
    fatura.updated_by = kullanici
    fatura.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return fatura
