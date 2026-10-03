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
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import stok_ortalama

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
                 tahmini=False, giris_tutar_try=None, giris_tutar_usd=None,
                 maliyet_fatura_satir=None, operasyon_kaydi=None, giris_ortalama=False,
                 transfer_grubu=None) -> StokHareket:
    """Miktar hareketi yazar. Hareketli ağırlıklı ortalama maliyet (bkz. core.services.
    stok_ortalama): ``giris_tutar_try`` (+``giris_tutar_usd``) GİRİŞ'in fatura tutarıdır;
    verilmezse giriş fiyatsız/GEÇİCİ sayılır (``giris_ortalama=True``: satış iadesi — o anki
    ortalamayla değerlenir). Çıkışta ``giris_tutar_try`` yalnız alış iadesi faturasında verilir (çıkış
    iade faturası tutarıyla değerlenir, ortalamayla değil). ``tahmini``: tutar kısmi veriden türedi (GEÇİCİ). ``operasyon_kaydi``
    üretim girdi çıkışı/çıktı girişi maliyet aktarımı için; ``transfer_grubu`` depo transferi
    bacakları için (maliyeti DEĞİŞTİRMEZ). Her yazımdan sonra kart yeniden hesaplanır, dönen
    hareket güncel ``tutar_try``/``maliyet_durumu`` ile gelir."""
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
        giris_tutar_try=giris_tutar_try, giris_tutar_usd=giris_tutar_usd,
        maliyet_fatura_satir=maliyet_fatura_satir,
        giris_tahmini=bool(tahmini) and tur == StokHareket.Tur.GIRIS,
        giris_ortalama=bool(giris_ortalama) and tur == StokHareket.Tur.GIRIS,
        operasyon_kaydi=operasyon_kaydi, transfer_grubu=transfer_grubu,
        created_by=kullanici, updated_by=kullanici)
    _ortalamayi_hesapla(stok)
    hareket.refresh_from_db()
    return hareket


def _ortalamayi_hesapla(stok, **kw):
    try:
        return stok_ortalama.yeniden_hesapla(stok, **kw)
    except stok_ortalama.MaliyetHatasi as e:
        raise HareketHatasi(str(e))


@transaction.atomic
def sarf_cikis_ekle(*, stok_id, depo_id, tarih, miktar, karsi_hesap_id,
                    yatirim_projesi_id=None, aciklama="", kullanici=None) -> StokHareket:
    """Stoktan hesaba/yatırım projesine SARF çıkışı: miktar hareketi + FIFO maliyet
    tüketimi + muhasebe fişi (karşı hesap BORÇ, stoğun muhasebe hesabı ALACAK) BİR
    ARADA oluşturur. Tutar = o anki hareketli ağırlıklı ortalama maliyet (bkz. core.services.
    stok_ortalama); maliyet bilinmiyorsa (fiyatlı giriş yok) hiçbir şey kaydedilmez (atomik)
    ve HareketHatasi yükselir. 258 karşı hesabı seçilirse yatırım projesi
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
    _ortalamayi_hesapla(stok)                        # çıkış, o anki ağırlıklı ortalamayla değerlenir
    hareket.refresh_from_db()
    tutar = hareket.tutar_try or Decimal("0.00")
    if tutar <= 0:
        raise HareketHatasi(
            "Bu çıkış için maliyet hesaplanamadı (stokta fatura tutarıyla fiyatlanmış giriş "
            "yok); sarf fişi kesilemedi.")
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
    Kartın güncel hareketli ağırlıklı ortalama maliyetine göre hesaplanır (gerçek çıkışta
    çıkış tarihindeki ortalama kullanılır; ekran uyarısı 'tahmini' bayrağıyla bunu belirtir)."""
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
    # Ağırlıklı ortalama: kartın GÜNCEL ortalama maliyetiyle (tarih bazlı değil, önizleme).
    q, v = stok.maliyet_miktar, stok.maliyet_deger_try
    if q <= 0 or stok.ort_maliyet_try is None:
        return {"tutar_try": None, "birim_maliyet_try": None, "karsilanan_miktar": SIFIR,
                "tam_mi": False}
    karsilanan = min(m, q)
    tutar = v if m >= q else yuvarla(m * v / q, 2)
    return {"tutar_try": tutar, "birim_maliyet_try": stok.ort_maliyet_try,
            "karsilanan_miktar": karsilanan, "tam_mi": karsilanan >= m}


@transaction.atomic
def hareket_sil(hareket: StokHareket, kullanici=None, *, _transfer_icinden=False) -> StokHareket:
    """Soft-delete. Giriş silinince eldeki azalır; negatife düşürmemeli. Silinen hareketin
    kartı yeniden hesaplanır (sonraki çıkışların maliyeti ve sarf/üretim/satış fişleri
    güncellenir); bağlı otomatik muhasebe fişi iptal edilir (bkz. core.services.yevmiye.
    fis_iptal). Depo transferinin tek bacağı silinemez (bkz. core.services.depo_transfer)."""
    from django.utils import timezone
    if hareket.silindi:
        return hareket
    if hareket.kaynak == StokHareket.Kaynak.TRANSFER and not _transfer_icinden:
        raise HareketHatasi("Depo transferinin tek bacağı silinemez; Depo Transferi'ni silin.")
    if hareket.tur == StokHareket.Tur.GIRIS:
        # Bu girişi geri alınca eldeki negatif olur mu?
        if eldeki_miktar(hareket.stok, hareket.depo) - hareket.miktar < 0:
            raise HareketHatasi(
                "Bu giriş silinemez: depodaki eldeki miktar negatife düşer (önce çıkışları düzeltin).")
    if hareket.fis_id and not hareket.fis.silindi:
        from core.services.yevmiye import fis_iptal
        fis_iptal(hareket.fis, kullanici=kullanici)
    hareket.silindi = True
    hareket.silindi_at = timezone.now()
    hareket.updated_by = kullanici
    hareket.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    _ortalamayi_hesapla(hareket.stok)               # silinen hareket ortalamadan çıkar
    return hareket
