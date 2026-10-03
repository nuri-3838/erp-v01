"""Stok hareketi (STOKLAR Faz B) servis katmanı — miktar defteri.

Eldeki miktar SAKLANMAZ; hareketlerden hesaplanır (Σgiriş − Σçıkış). Muhasebeden
bağımsız (TL tarafını fatura işler). Çıkış, o stok+depo eldeki miktarından fazla olamaz.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from core.metin import buyuk_harf_tr
from core.models import Depo, Stok, StokHareket
from core.sayi import SayiHatasi, parse_tr
from core.services import stok_maliyet

SIFIR = Decimal("0.000")


class HareketHatasi(ValueError):
    """Stok hareketi kural ihlali (Türkçe mesaj)."""


def eldeki_miktar(stok, depo=None) -> Decimal:
    """Stok (opsiyonel depo) için eldeki miktar = Σgiriş − Σçıkış (silinmemiş)."""
    qs = StokHareket.objects.filter(stok=stok, silindi=False)
    if depo is not None:
        qs = qs.filter(depo=depo)
    g = qs.filter(tur=StokHareket.Tur.GIRIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
    c = qs.filter(tur=StokHareket.Tur.CIKIS).aggregate(t=Sum("miktar"))["t"] or SIFIR
    return g - c


def toplu_eldeki(stok_idler) -> dict:
    """{stok_id: eldeki} — verilen stokların TÜM depolardaki toplam eldeki miktarı
    (``eldeki_miktar(stok)`` ile aynı anlam), stok başına ayrı sorgu yerine TEK gruplu
    sorgu. Hareketi olmayan stok sözlükte yoktur (çağıran SIFIR varsayar)."""
    gruplu = (StokHareket.objects.filter(stok_id__in=list(stok_idler), silindi=False)
              .values("stok_id", "tur").annotate(t=Sum("miktar")))
    eldeki = {}
    for g in gruplu:
        fark = g["t"] if g["tur"] == StokHareket.Tur.GIRIS else -g["t"]
        eldeki[g["stok_id"]] = eldeki.get(g["stok_id"], SIFIR) + fark
    return eldeki


def depo_bazinda_eldeki(stok):
    """[(depo, miktar)] — stoğun hareket gördüğü depolar bazında eldeki (≠0 dahil hepsi).
    Depo başına ayrı sorgu yerine TEK gruplu sorgu (depo × tür toplamı), giriş-çıkış farkı
    Python'da hesaplanır."""
    gruplu = (StokHareket.objects.filter(stok=stok, silindi=False)
              .values("depo_id", "tur").annotate(t=Sum("miktar")))
    eldeki = {}
    for g in gruplu:
        fark = g["t"] if g["tur"] == StokHareket.Tur.GIRIS else -g["t"]
        eldeki[g["depo_id"]] = eldeki.get(g["depo_id"], SIFIR) + fark
    depolar = Depo.objects.filter(pk__in=eldeki.keys(), silindi=False).order_by("kod")
    return [(d, eldeki[d.pk]) for d in depolar]


def stok_hareketleri(stok):
    return (StokHareket.objects.filter(stok=stok, silindi=False)
            .select_related("depo", "fis", "karsi_hesap", "yatirim_projesi")
            .order_by("-tarih", "-id"))


@transaction.atomic
def hareket_ekle(*, stok_id, depo_id, tarih, tur, miktar, aciklama="",
                 kaynak=StokHareket.Kaynak.MANUEL, fatura_satir=None,
                 teklif_siparis_kalem=None, operasyon_kaydi_girdi=None, kullanici=None,
                 birim_maliyet_try=None, kaynak_pb="", kaynak_birim_fiyat=None,
                 kaynak_kur=None, tahmini=False) -> StokHareket:
    """Miktar hareketi yazar. ``birim_maliyet_try`` yalnız GİRİŞ'te ve biliniyorsa
    (ALIŞ irsaliyesi/faturası) bir FIFO maliyet katmanı açar — bkz. core.services.
    stok_maliyet. ÇIKIŞ'ta katman tüketimi HER ZAMAN otomatik çalışır (parametre
    gerekmez); dönüş tipi değişmez, maliyeti öğrenmek isteyen dönen nesnenin
    ``.maliyet_katmani`` / ``.maliyet_tuketimleri`` ilişkisini okur."""
    stok = Stok.objects.filter(pk=stok_id, silindi=False).first()
    if stok is None:
        raise HareketHatasi("Stok bulunamadı.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise HareketHatasi("Depo bulunamadı.")
    if tur not in StokHareket.Tur.values:
        raise HareketHatasi("Hareket türü Giriş veya Çıkış olmalı.")
    try:
        m = parse_tr(miktar)
    except SayiHatasi:
        raise HareketHatasi("Miktar geçerli bir sayı olmalı.")
    if m <= 0:
        raise HareketHatasi("Miktar sıfırdan büyük olmalı.")
    if tur == StokHareket.Tur.CIKIS:
        mevcut = eldeki_miktar(stok, depo)
        if m > mevcut:
            raise HareketHatasi(
                f"Yetersiz stok: {depo.kod} deposunda {stok.kod} için eldeki {mevcut}, "
                f"çıkış {m} olamaz.")
    hareket = StokHareket.objects.create(
        stok=stok, depo=depo, tarih=tarih, tur=tur, miktar=m,
        aciklama=buyuk_harf_tr((aciklama or "").strip()), kaynak=kaynak,
        fatura_satir=fatura_satir, teklif_siparis_kalem=teklif_siparis_kalem,
        operasyon_kaydi_girdi=operasyon_kaydi_girdi,
        created_by=kullanici, updated_by=kullanici)
    if tur == StokHareket.Tur.GIRIS:
        if birim_maliyet_try is not None:
            stok_maliyet.katman_olustur(
                hareket, birim_maliyet_try, kaynak_pb=kaynak_pb,
                kaynak_birim_fiyat=kaynak_birim_fiyat, kaynak_kur=kaynak_kur, tahmini=tahmini)
    else:
        stok_maliyet.fifo_tuket(hareket)
    return hareket


@transaction.atomic
def sarf_cikis_ekle(*, stok_id, depo_id, tarih, miktar, karsi_hesap_id,
                    yatirim_projesi_id=None, aciklama="", kullanici=None) -> StokHareket:
    """Stoktan hesaba/yatırım projesine SARF çıkışı: miktar hareketi + FIFO maliyet
    tüketimi + muhasebe fişi (karşı hesap BORÇ, stoğun muhasebe hesabı ALACAK) BİR
    ARADA oluşturur. Tutar = FIFO ile hesaplanan maliyet (bkz. core.services.stok_maliyet);
    katmanlar ihtiyacı tam karşılamazsa (maliyet bilinmiyor) hiçbir şey kaydedilmez
    (atomik) ve HareketHatasi yükselir. 258 karşı hesabı seçilirse yatırım projesi
    ZORUNLUDUR (bkz. hesap_plani.hesap_kodu_258_mi)."""
    from core.models import HesapPlani, YatirimProjesi, YevmiyeFisi
    from core.services import kategori as kategori_servis
    from core.services.hesap_plani import hesap_kodu_258_mi, yaprak_mi
    from core.services.kategori import KategoriHatasi
    from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

    stok = Stok.objects.filter(pk=stok_id, silindi=False).select_related("kategori").first()
    if stok is None:
        raise HareketHatasi("Stok bulunamadı.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise HareketHatasi("Depo bulunamadı.")
    karsi_hesap = HesapPlani.objects.filter(pk=karsi_hesap_id, aktif=True, silindi=False).first()
    if karsi_hesap is None or not yaprak_mi(karsi_hesap):
        raise HareketHatasi("Karşı hesap bulunamadı ya da yaprak (fişe kesilebilir) değil.")
    yatirim_projesi = None
    if hesap_kodu_258_mi(karsi_hesap.hesap_kodu):
        if not yatirim_projesi_id:
            raise HareketHatasi("258 karşı hesabı için yatırım projesi seçilmelidir.")
        yatirim_projesi = YatirimProjesi.objects.filter(
            pk=yatirim_projesi_id, silindi=False, durum=YatirimProjesi.Durum.DEVAM).first()
        if yatirim_projesi is None:
            raise HareketHatasi("Yatırım projesi bulunamadı ya da 'Devam Ediyor' durumunda değil.")
    elif yatirim_projesi_id:
        raise HareketHatasi("Yatırım projesi yalnız 258 karşı hesabı seçilince kullanılabilir.")

    try:
        m = parse_tr(miktar)
    except SayiHatasi:
        raise HareketHatasi("Miktar geçerli bir sayı olmalı.")
    if m <= 0:
        raise HareketHatasi("Miktar sıfırdan büyük olmalı.")
    mevcut = eldeki_miktar(stok, depo)
    if m > mevcut:
        raise HareketHatasi(
            f"Yetersiz stok: {depo.kod} deposunda {stok.kod} için eldeki {mevcut}, "
            f"çıkış {m} olamaz.")

    try:
        stok_hesabi = kategori_servis.stok_muhasebe_hesabi(stok)
    except KategoriHatasi as e:
        raise HareketHatasi(str(e))

    hareket = StokHareket.objects.create(
        stok=stok, depo=depo, tarih=tarih, tur=StokHareket.Tur.CIKIS, miktar=m,
        aciklama=buyuk_harf_tr((aciklama or "").strip()), kaynak=StokHareket.Kaynak.SARF,
        karsi_hesap=karsi_hesap, yatirim_projesi=yatirim_projesi,
        created_by=kullanici, updated_by=kullanici)
    stok_maliyet.fifo_tuket(hareket)
    durum = stok_maliyet.hareket_maliyet_durumu(hareket)
    tutar = durum["tutar_try"] or Decimal("0.00")
    if tutar <= 0:
        raise HareketHatasi(
            "Bu çıkış için maliyet hesaplanamadı (eldeki miktara karşılık gelen maliyet "
            "katmanı yok); sarf fişi kesilemedi.")
    aciklama_fis = f"{stok.kod} — {stok.ad} sarf çıkışı"
    if yatirim_projesi:
        aciklama_fis += f" ({yatirim_projesi.kod})"
    try:
        fis = fis_olustur(
            tarih=tarih,
            satirlar=[
                SatirGirdi(karsi_hesap.hesap_kodu, "B", tutar),
                SatirGirdi(stok_hesabi.hesap_kodu, "A", tutar),
            ],
            aciklama=aciklama_fis, kaynak=YevmiyeFisi.Kaynak.STOK_SARF, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise HareketHatasi(str(e))
    hareket.fis = fis
    hareket.save(update_fields=["fis", "updated_at"])
    return hareket


def sarf_onizleme(*, stok_id, depo_id, miktar) -> dict:
    """Sarf formu için birim maliyet/toplam tutar ÖNİZLEMESİ — hiçbir şey yazmaz.
    Mevcut FIFO katmanlarına göre hesaplanır (gerçek çıkışta katman sırası/kalan miktar
    değişmemişse aynı sonucu verir; ekran uyarısı 'tahmini' bayrağıyla bunu belirtir)."""
    from core.models import StokMaliyetKatmani

    stok = Stok.objects.filter(pk=stok_id, silindi=False).first()
    if stok is None:
        raise HareketHatasi("Stok bulunamadı.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise HareketHatasi("Depo bulunamadı.")
    try:
        m = parse_tr(miktar)
    except SayiHatasi:
        raise HareketHatasi("Miktar geçerli bir sayı olmalı.")
    if m <= 0:
        raise HareketHatasi("Miktar sıfırdan büyük olmalı.")
    kalan_ihtiyac = m
    tutar = Decimal("0.00")
    karsilanan = SIFIR
    for katman in (StokMaliyetKatmani.objects
                  .filter(stok=stok, depo=depo, kalan_miktar__gt=0, silindi=False)
                  .order_by("tarih", "id")):
        if kalan_ihtiyac <= 0:
            break
        dusulecek = min(katman.kalan_miktar, kalan_ihtiyac)
        tutar += dusulecek * katman.birim_maliyet_try
        karsilanan += dusulecek
        kalan_ihtiyac -= dusulecek
    tam_mi = karsilanan >= m
    birim_maliyet = (tutar / karsilanan) if karsilanan > 0 else None
    return {"tutar_try": tutar.quantize(Decimal("0.01")) if karsilanan > 0 else None,
           "birim_maliyet_try": birim_maliyet, "karsilanan_miktar": karsilanan, "tam_mi": tam_mi}


@transaction.atomic
def hareket_sil(hareket: StokHareket, kullanici=None) -> StokHareket:
    """Soft-delete. Giriş silinince eldeki azalır; negatife düşürmemeli. Girişin
    maliyeti başka hareketlere (FIFO ile) aktarılmışsa silinemez. Çıkış silinince
    tükettiği maliyet katman(lar)ı geri yüklenir; sarf çıkışıysa bağlı muhasebe fişi
    de iptal edilir (bkz. core.services.yevmiye.fis_iptal)."""
    from django.utils import timezone
    if hareket.silindi:
        return hareket
    if hareket.tur == StokHareket.Tur.GIRIS:
        # Bu girişi geri alınca eldeki negatif olur mu?
        if eldeki_miktar(hareket.stok, hareket.depo) - hareket.miktar < 0:
            raise HareketHatasi(
                "Bu giriş silinemez: depodaki eldeki miktar negatife düşer (önce çıkışları düzeltin).")
        katman = getattr(hareket, "maliyet_katmani", None)
        if katman is not None and not katman.silindi and katman.kalan_miktar != katman.giris_miktar:
            raise HareketHatasi(
                "Bu girişin maliyeti başka hareketlere (üretim/satış) aktarılmış; silinemez.")
    else:
        stok_maliyet.tuketimi_geri_al(hareket)
        if hareket.fis_id and not hareket.fis.silindi:
            from core.services.yevmiye import fis_iptal
            fis_iptal(hareket.fis, kullanici=kullanici)
    hareket.silindi = True
    hareket.silindi_at = timezone.now()
    hareket.updated_by = kullanici
    hareket.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    if hareket.tur == StokHareket.Tur.GIRIS:
        katman = getattr(hareket, "maliyet_katmani", None)
        if katman is not None and not katman.silindi:
            katman.silindi = True
            katman.silindi_at = timezone.now()
            katman.save(update_fields=["silindi", "silindi_at", "updated_at"])
    return hareket
