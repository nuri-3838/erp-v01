"""Stok maliyetinin OTOMATİK MUHASEBE FİŞLERİ (ağırlıklı ortalama maliyet tutarıyla):

- Üretim kaydı onayı: girdilerin maliyeti çıktıya aktarılır — girdi stok hesapları (150-153) ALACAK,
  çıktı stok hesabı BORÇ. Hesaplar aynıysa fiş yoktur. YALNIZ MALZEME maliyeti (işçilik/GÜG
  yansıtması ay sonu manuel kalır — CLAUDE.md).
- Satış faturası (tip.maliyet_fisi=SATIS): satılan stoğun ortalama maliyeti 620 (mamul, 152) /
  621 (ticari mal, 153) / 623 (150-151: diğer satışların maliyeti) BORÇ, stok hesabı ALACAK.
- Satış iadesi (SATIS_IADE): aynı fişin tersi (stok borç, maliyet hesabı alacak).

Fişler ``senkronla`` fonksiyonlarıyla İDEMPOTENT yazılır (oluştur / güncelle / hiç tutar kalmadıysa
iptal); maliyet sonradan değişince (geç gelen fatura vb.) ``stok_ortalama.yeniden_hesapla`` bu
fonksiyonları çağırır. Elle düzenlenmez/iptal edilmez (ekranlar yönlendirir)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from core.models import FaturaTipi, HesapPlani, StokHareket, YevmiyeFisi
from core.services import kategori as kategori_servis
from core.services.kategori import KategoriHatasi
from core.services.stok_ortalama import MaliyetHatasi
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_iptal, fis_olustur

SIFIR = Decimal("0.00")
GIRIS, CIKIS = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
YOK, KESIN = StokHareket.MaliyetDurumu.YOK, StokHareket.MaliyetDurumu.KESIN


def stok_hesabi_kodu(stok) -> str:
    try:
        return kategori_servis.stok_muhasebe_hesabi(stok).hesap_kodu
    except KategoriHatasi as e:
        raise MaliyetHatasi(f"{stok.kod}: {e}")


def maliyet_hesabi_kodu(stok_hesap_kodu: str) -> str:
    """Satılan stoğun maliyet hesabı: mamul 620, ticari mal 621, ilk madde/yarı mamul 623."""
    kod = {"152": "620", "153": "621"}.get(stok_hesap_kodu[:3], "623")
    if not HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).exists():
        raise MaliyetHatasi(f"Satış maliyeti hesabı {kod} hesap planında yok/aktif değil.")
    return kod


def _satirlar(net: dict) -> list:
    return [SatirGirdi(kod, "B", tutar) if tutar > 0 else SatirGirdi(kod, "A", -tutar)
            for kod, tutar in net.items() if tutar != 0]


def _fis_senkronla(mevcut, satirlar, *, tarih, aciklama, kaynak, kullanici=None):
    """Mevcut fişi günceller / yoksa oluşturur / satır kalmadıysa iptal eder. Fişi (ya da None) döner."""
    if not satirlar:
        if mevcut is not None and not mevcut.silindi:
            fis_iptal(mevcut, kullanici=kullanici)
        return None
    try:
        if mevcut is not None and not mevcut.silindi:
            fis_guncelle(mevcut, tarih=tarih, satirlar=satirlar, aciklama=aciklama,
                         kur_usd=mevcut.kur_usd, kullanici=kullanici)
            return mevcut
        return fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=aciklama, kaynak=kaynak,
                           kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise MaliyetHatasi(str(e))


# === Üretim ============================================================================

def uretim_senkronla(kayit, *, kullanici=None):
    """Operasyon kaydının maliyet aktarımı: çıktı girişinin tutarını girdi çıkışlarının
    ağırlıklı ortalama maliyet toplamından türetir ve 15x→15x fişini yazar/günceller.
    Çıktı tutarı DEĞİŞTİYSE çıktı stokunu döner (çağıran onu da yeniden hesaplar)."""
    hs = list(StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False)
              .select_related("stok"))
    girdiler = [h for h in hs if h.tur == CIKIS]
    ciktilar = [h for h in hs if h.tur == GIRIS]
    if not ciktilar:
        return None
    cikti = ciktilar[0]
    bilinen = [g for g in girdiler if g.maliyet_durumu != YOK and (g.tutar_try or 0) > 0]
    toplam = sum((g.tutar_try for g in bilinen), SIFIR)
    toplam_usd = sum((g.tutar_usd or SIFIR for g in bilinen), SIFIR)
    tahmini = any(g.maliyet_durumu != KESIN for g in girdiler)
    yeni_try = toplam if toplam > 0 else None
    yeni_usd = toplam_usd if toplam > 0 else None
    degisti = ((cikti.giris_tutar_try, cikti.giris_tutar_usd, cikti.giris_tahmini)
               != (yeni_try, yeni_usd, tahmini))
    if degisti:
        cikti.giris_tutar_try, cikti.giris_tutar_usd, cikti.giris_tahmini = yeni_try, yeni_usd, tahmini
        cikti.save(update_fields=["giris_tutar_try", "giris_tutar_usd", "giris_tahmini",
                                  "updated_at"])
    net = defaultdict(lambda: SIFIR)
    if yeni_try:
        for g in bilinen:
            net[stok_hesabi_kodu(g.stok)] -= g.tutar_try
        net[stok_hesabi_kodu(cikti.stok)] += yeni_try
    fis = _fis_senkronla(kayit.fis, _satirlar(net), tarih=kayit.tarih,
                         aciklama=f"{kayit.no} maliyet aktarımı",
                         kaynak=YevmiyeFisi.Kaynak.URETIM, kullanici=kullanici)
    if (fis.pk if fis else None) != kayit.fis_id:
        kayit.fis = fis
        kayit.save(update_fields=["fis", "updated_at"])
    return cikti.stok if degisti else None


# === Satış / satış iadesi ==============================================================

def satis_senkronla(fatura, *, kullanici=None):
    """Satış faturasının (tip.maliyet_fisi) maliyet fişi — fatura hareketlerinin ağırlıklı
    ortalama tutarlarından. Maliyeti hâlâ bilinmeyen satır fişe girmez; sonradan bilinince
    ``yeniden_hesapla`` bu fonksiyonu tekrar çağırır."""
    mod = fatura.tip.maliyet_fisi if fatura.tip_id else ""
    if not mod:
        return None
    iade = mod == FaturaTipi.MaliyetFisi.SATIS_IADE
    yon = GIRIS if iade else CIKIS
    hs = list(StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False)
              .select_related("stok"))
    net = defaultdict(lambda: SIFIR)
    for h in hs:
        if h.tur != yon or h.maliyet_durumu == YOK or not h.tutar_try or h.tutar_try <= 0:
            continue
        stok_kod = stok_hesabi_kodu(h.stok)
        mal_kod = maliyet_hesabi_kodu(stok_kod)
        isaret = Decimal("-1") if iade else Decimal("1")
        net[mal_kod] += isaret * h.tutar_try          # satış: maliyet hesabı borç; iade: alacak
        net[stok_kod] -= isaret * h.tutar_try          # satış: stok alacak; iade: borç
    mevcut = next((h.fis for h in hs if h.fis_id and not h.fis.silindi), None)
    fis = _fis_senkronla(mevcut, _satirlar(net), tarih=fatura.tarih,
                         aciklama=f"{fatura.fatura_no or fatura.pk} {'satış iadesi' if iade else 'satış'} maliyeti",
                         kaynak=YevmiyeFisi.Kaynak.STOK_SATIS, kullanici=kullanici)
    if fis is not None:
        StokHareket.objects.filter(pk__in=[h.pk for h in hs if h.fis_id != fis.pk]).update(fis=fis)
    return fis
