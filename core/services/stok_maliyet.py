"""Stok FIFO maliyet katmanları — TL tarafı, StokHareket'in miktar defterinden AYRI.

Bu modül kendi başına çağrılmaz: core.services.hareket.hareket_ekle/hareket_sil
GİRİŞ/ÇIKIŞ hareketlerinde otomatik olarak burayı çağırır (bkz. o dosya). Hiçbir
çağıran taraf (Fatura/İrsaliye/Üretim) bu modülü doğrudan çağırmaz — merkezi ve
tutarlı kalması için tek giriş noktası hareket_ekle/hareket_sil'dir.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.models import StokMaliyetKatmani, StokMaliyetTuketimi
from core.sayi import yuvarla

SIFIR = Decimal("0.000")


def katman_olustur(stok_hareket, birim_maliyet_try, *, kaynak_pb="", kaynak_birim_fiyat=None,
                   kaynak_kur=None, tahmini=False) -> StokMaliyetKatmani:
    """GİRİŞ hareketi için katman yaratır. stok/depo/tarih HER ZAMAN stok_hareket'ten
    okunur (asla ayrıca parametre olarak verilmez — iki kopyanın sapması engellenir)."""
    return StokMaliyetKatmani.objects.create(
        stok_hareket=stok_hareket, stok=stok_hareket.stok, depo=stok_hareket.depo,
        tarih=stok_hareket.tarih, giris_miktar=stok_hareket.miktar,
        kalan_miktar=stok_hareket.miktar, birim_maliyet_try=birim_maliyet_try,
        kaynak_pb=kaynak_pb, kaynak_birim_fiyat=kaynak_birim_fiyat, kaynak_kur=kaynak_kur,
        tahmini=tahmini, created_by=stok_hareket.created_by, updated_by=stok_hareket.created_by)


def fifo_tuket(stok_hareket) -> None:
    """ÇIKIŞ hareketi için (stok, depo) katmanlarını tarih/id sırasıyla (eskiden yeniye)
    tüketir. Katmanlar yetmezse kalan kısım için hiç StokMaliyetTuketimi satırı açılmaz
    (o kısmın maliyeti bilinmiyor demektir — çağıran taraf hareket.maliyet_tuketimleri
    toplamını hareket.miktar ile karşılaştırıp eksikliği kendi anlar)."""
    kalan_ihtiyac = stok_hareket.miktar
    katmanlar = (StokMaliyetKatmani.objects
                .filter(stok=stok_hareket.stok, depo=stok_hareket.depo,
                        kalan_miktar__gt=0, silindi=False)
                .order_by("tarih", "id").select_for_update())
    for katman in katmanlar:
        if kalan_ihtiyac <= 0:
            break
        dusulecek = min(katman.kalan_miktar, kalan_ihtiyac)
        StokMaliyetTuketimi.objects.create(
            katman=katman, tuketen_hareket=stok_hareket, miktar=dusulecek,
            birim_maliyet_try=katman.birim_maliyet_try,
            tutar_try=yuvarla(dusulecek * katman.birim_maliyet_try, 2),
            created_by=stok_hareket.created_by, updated_by=stok_hareket.created_by)
        katman.kalan_miktar -= dusulecek
        katman.save(update_fields=["kalan_miktar", "updated_at"])
        kalan_ihtiyac -= dusulecek


def tuketimi_geri_al(stok_hareket) -> None:
    """hareket_sil'in ÇIKIŞ dalı için: bu hareketin tükettiği katman(lar)ın kalan_miktar'ını
    geri yükler, StokMaliyetTuketimi satırlarını soft-delete eder."""
    for tuketim in stok_hareket.maliyet_tuketimleri.filter(silindi=False).select_related("katman"):
        katman = tuketim.katman
        katman.kalan_miktar += tuketim.miktar
        katman.save(update_fields=["kalan_miktar", "updated_at"])
        tuketim.silindi = True
        tuketim.silindi_at = timezone.now()
        tuketim.save(update_fields=["silindi", "silindi_at", "updated_at"])


def hareket_maliyet_durumu(stok_hareket) -> dict:
    """ÇIKIŞ hareketi için: {'tutar_try', 'karsilanan_miktar', 'tam_mi', 'tahmini'}.
    Operasyon Kaydı onayı ve raporlama ekranları bunu kullanır."""
    tuketimler = list(stok_hareket.maliyet_tuketimleri.filter(silindi=False)
                      .select_related("katman"))
    karsilanan_miktar = sum((t.miktar for t in tuketimler), SIFIR)
    tutar_try = sum((t.tutar_try for t in tuketimler), Decimal("0.00")) if tuketimler else None
    tam_mi = karsilanan_miktar >= stok_hareket.miktar
    tahmini = (not tam_mi) or any(t.katman.tahmini for t in tuketimler)
    return {"tutar_try": tutar_try, "karsilanan_miktar": karsilanan_miktar,
           "tam_mi": tam_mi, "tahmini": tahmini}
