"""NUMUNE ÇIKIŞI (NC-yyyy-nnnn): müşteriye bedelsiz giden numunenin stoktan düşülmesi + gider fişi. Fatura/KDV YOKTUR.

Onay (atomik — hata olursa hiçbir şey kaydedilmez):
  1. Kullanılabilir stok kontrolü: eldeki − üretim siparişine AYRILAN (bkz. stok_ayirma.kullanilabilir) ve depodaki eldeki; yetersizse açık hata.
  2. Her satır için mevcut stok çıkışı (``hareket_ekle``, kaynak=NUMUNE) — maliyet satış/sarf ile aynı hareketli ağırlıklı ortalamadan gelir.
  3. TEK yevmiye fişi: numune gider hesabı (varsayılan 760.06) BORÇ / stoğun kategori haritasındaki stok hesabı (152.xx…) ALACAK, tutar = çıkış maliyeti.
Geri alma: fiş iptal edilir, stok hareketleri silinir (kart yeniden hesaplanır), belge TASLAK'a döner."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (Cari, Depo, HesapPlani, NumuneAyari, NumuneCikis, NumuneCikisSatir, Stok, StokHareket, Ulke,
                         YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr
from core.services import kategori as kategori_servis
from core.services import stok_ayirma
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services.kategori import KategoriHatasi
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_iptal, fis_olustur

VARSAYILAN_GIDER_HESABI = "760.06"
SIFIR = Decimal("0.00")


class NumuneHatasi(ValueError):
    """Numune çıkışı kural ihlali (Türkçe mesaj)."""


def gider_hesabi() -> HesapPlani:
    ayar = NumuneAyari.get()
    hesap = ayar.gider_hesap if ayar.gider_hesap_id else HesapPlani.objects.filter(hesap_kodu=VARSAYILAN_GIDER_HESABI, silindi=False).first()
    if hesap is None or hesap.silindi or not hesap.aktif:
        raise NumuneHatasi(f"Numune gider hesabı ({VARSAYILAN_GIDER_HESABI} NUMUNE GİDERLERİ) hesap planında yok/aktif değil; "
                           f"hesabı açın ya da Numune Ayarı'ndan başka bir hesap seçin.")
    return hesap


def ayar_kaydet(hesap, kullanici=None) -> NumuneAyari:
    ayar = NumuneAyari.get()
    ayar.gider_hesap = hesap
    ayar.updated_by = kullanici
    ayar.save(update_fields=["gider_hesap", "updated_by", "updated_at"])
    return ayar


def _sonraki_sira(yil) -> int:
    return (NumuneCikis.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"] or 0) + 1       # silinenler dahil: numara yeniden kullanılmaz


def _satirlari_coz(satirlar):
    """[{'stok_id':…, 'miktar':…}] -> [(Stok, Decimal)]; aynı stok birden çok satırsa ayrı satır kalır."""
    sonuc = []
    for i, g in enumerate(satirlar or [], start=1):
        stok = Stok.objects.filter(pk=g.get("stok_id"), silindi=False).first()
        if stok is None:
            raise NumuneHatasi(f"Satır {i}: stok bulunamadı.")
        try:
            m = parse_tr(g.get("miktar"))
        except SayiHatasi:
            raise NumuneHatasi(f"Satır {i}: miktar geçerli bir sayı olmalı.")
        if m <= 0:
            raise NumuneHatasi(f"Satır {i}: miktar sıfırdan büyük olmalı.")
        sonuc.append((stok, m))
    if not sonuc:
        raise NumuneHatasi("En az bir satır olmalı.")
    return sonuc


def _baslik_dogrula(*, depo_id, cari_id, alici_adi, ulke_id):
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise NumuneHatasi("Depo bulunamadı.")
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first() if cari_id else None
    if cari_id and cari is None:
        raise NumuneHatasi("Cari bulunamadı.")
    alici_adi = (alici_adi or "").strip()
    if cari is None and not alici_adi:
        raise NumuneHatasi("Alıcı için bir cari seçin ya da alıcı adı yazın.")
    ulke = Ulke.objects.filter(pk=ulke_id).first() if ulke_id else None
    return depo, cari, alici_adi, ulke


@transaction.atomic
def numune_olustur(*, tarih, depo_id, satirlar, cari_id=None, alici_adi="", ulke_id=None, belge_no_gumruk="", aciklama="",
                   kullanici=None) -> NumuneCikis:
    depo, cari, alici_adi, ulke = _baslik_dogrula(depo_id=depo_id, cari_id=cari_id, alici_adi=alici_adi, ulke_id=ulke_id)
    coz = _satirlari_coz(satirlar)
    yil = tarih.year
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(yil)
                nc = NumuneCikis.objects.create(
                    yil=yil, sira=sira, no=f"NC-{yil}-{sira:04d}", tarih=tarih, depo=depo, cari=cari, alici_adi=buyuk_harf_tr(alici_adi),
                    ulke=ulke, belge_no_gumruk=(belge_no_gumruk or "").strip(), aciklama=(aciklama or "").strip(),
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            nc = None
    if nc is None:
        raise NumuneHatasi("Belge numarası üretilemedi; tekrar deneyin.")
    for stok, m in coz:
        NumuneCikisSatir.objects.create(cikis=nc, stok=stok, miktar=m, created_by=kullanici, updated_by=kullanici)
    return nc


@transaction.atomic
def numune_guncelle(nc: NumuneCikis, *, tarih, depo_id, satirlar, cari_id=None, alici_adi="", ulke_id=None, belge_no_gumruk="",
                    aciklama="", kullanici=None) -> NumuneCikis:
    nc.refresh_from_db()                                                  # bayat nesneye güvenme: durum DB'den
    if nc.silindi:
        raise NumuneHatasi("Silinmiş belge düzenlenemez.")
    if nc.durum != NumuneCikis.Durum.TASLAK:
        raise NumuneHatasi("Onaylı numune çıkışı düzenlenemez; önce onayını geri alın.")
    depo, cari, alici_adi, ulke = _baslik_dogrula(depo_id=depo_id, cari_id=cari_id, alici_adi=alici_adi, ulke_id=ulke_id)
    coz = _satirlari_coz(satirlar)
    nc.tarih, nc.depo, nc.cari, nc.alici_adi, nc.ulke = tarih, depo, cari, buyuk_harf_tr(alici_adi), ulke
    nc.belge_no_gumruk, nc.aciklama, nc.updated_by = (belge_no_gumruk or "").strip(), (aciklama or "").strip(), kullanici
    nc.save()
    nc.satirlar.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    for stok, m in coz:
        NumuneCikisSatir.objects.create(cikis=nc, stok=stok, miktar=m, created_by=kullanici, updated_by=kullanici)
    return nc


@transaction.atomic
def numune_sil(nc: NumuneCikis, kullanici=None) -> None:
    nc.refresh_from_db()
    if nc.durum != NumuneCikis.Durum.TASLAK:
        raise NumuneHatasi("Onaylı numune çıkışı silinemez; önce onayını geri alın.")
    nc.silindi, nc.silindi_at, nc.updated_by = True, timezone.now(), kullanici
    nc.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])


def _aktif_satirlar(nc):
    return list(nc.satirlar.filter(silindi=False).select_related("stok", "stok__kategori").order_by("id"))


def uygunluk_kontrol(nc: NumuneCikis, satirlar=None) -> None:
    """Stok yetersizse açık hata (yazma yok). Satırlar stok bazında toplanır; ayrılan (üretim siparişi) stok kullanılmaz."""
    satirlar = satirlar if satirlar is not None else _aktif_satirlar(nc)
    toplam = defaultdict(lambda: SIFIR)
    stoklar = {}
    for s in satirlar:
        toplam[s.stok_id] += s.miktar
        stoklar[s.stok_id] = s.stok
    for sid, m in toplam.items():
        stok = stoklar[sid]
        depoda = eldeki_miktar(stok, nc.depo, tarih=nc.tarih)                   # TARİH BAZLI (belge tarihi sonu itibarıyla)
        if m > depoda:
            from core.services.hareket import yetersiz_mesaji
            raise NumuneHatasi(yetersiz_mesaji(stok, nc.depo, nc.tarih, depoda, m))
        kullanilabilir = stok_ayirma.kullanilabilir(stok)
        if m > kullanilabilir:
            raise NumuneHatasi(f"{stok.kod}: serbest (kullanılabilir) stok {kullanilabilir:f}; üretim siparişine ayrılan stok numuneye verilemez "
                               f"(istenen {m:f}).".replace(".000000", ""))


@transaction.atomic
def numune_onayla(nc: NumuneCikis, kullanici=None) -> NumuneCikis:
    nc = NumuneCikis.objects.select_for_update(of=("self",)).select_related("depo", "cari").get(pk=nc.pk)
    if nc.silindi:
        raise NumuneHatasi("Silinmiş belge onaylanamaz.")
    if nc.durum == NumuneCikis.Durum.ONAYLI:
        return nc
    satirlar = _aktif_satirlar(nc)
    if not satirlar:
        raise NumuneHatasi("En az bir satır olmalı.")
    hesap = gider_hesabi()
    uygunluk_kontrol(nc, satirlar)
    alici = nc.alici or "—"
    stok_toplam = defaultdict(lambda: SIFIR)
    for s in satirlar:
        try:
            stok_hesabi = kategori_servis.stok_muhasebe_hesabi(s.stok)
        except KategoriHatasi as e:
            raise NumuneHatasi(f"{s.stok.kod}: {e}")
        try:
            h = hareket_ekle(stok_id=s.stok_id, depo_id=nc.depo_id, tarih=nc.tarih, tur=StokHareket.Tur.CIKIS, miktar=s.miktar,
                             aciklama=f"NUMUNE ÇIKIŞI {nc.no} — {alici}", kaynak=StokHareket.Kaynak.NUMUNE, kullanici=kullanici)
        except HareketHatasi as e:
            raise NumuneHatasi(str(e))
        tutar = h.tutar_try or SIFIR
        if tutar <= 0:
            raise NumuneHatasi(f"{s.stok.kod}: maliyet hesaplanamadı (stokta fatura tutarıyla fiyatlanmış giriş yok); numune fişi kesilemedi.")
        s.hareket, s.maliyet_try = h, tutar
        s.save(update_fields=["hareket", "maliyet_try", "updated_at"])
        stok_toplam[stok_hesabi.hesap_kodu] += tutar
    toplam = sum(stok_toplam.values(), SIFIR)
    try:
        fis = fis_olustur(
            tarih=nc.tarih,
            satirlar=[SatirGirdi(hesap.hesap_kodu, "B", toplam)] + [SatirGirdi(kod, "A", t) for kod, t in stok_toplam.items()],
            aciklama=f"NUMUNE ÇIKIŞI {nc.no} — {alici}", kaynak=YevmiyeFisi.Kaynak.NUMUNE, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise NumuneHatasi(str(e))
    nc.fis, nc.durum, nc.updated_by = fis, NumuneCikis.Durum.ONAYLI, kullanici
    nc.save(update_fields=["fis", "durum", "updated_by", "updated_at"])
    # Çıkış maliyeti sonradan değişirse (geç gelen fatura / fiyat düzeltme) stok_ortalama.yeniden_hesapla zinciri numune_senkronla'yı çağırır.
    return nc


@transaction.atomic
def numune_senkronla(nc: NumuneCikis, *, kullanici=None):
    """Onaylı numunenin satır maliyetlerini ve gider fişini (760.06 B / stok hesabı A) hareketlerin GÜNCEL ağırlıklı ortalama tutarına
    eşitler — fiş YERİNDE güncellenir (yenisi açılmaz). Maliyet sonradan değişince (geç gelen fatura, fiyatsız girişe fiyat verilmesi)
    ``stok_ortalama._degisenleri_isle`` çağırır. Bir satırın maliyeti bilinmez (0) olduysa fiş onay anındaki tutarıyla bırakılır."""
    from core.services.yevmiye import fis_guncelle
    if nc.durum != NumuneCikis.Durum.ONAYLI or not nc.fis_id or nc.fis.silindi:
        return None
    stok_toplam, degisti = defaultdict(lambda: SIFIR), False
    for s in _aktif_satirlar(nc):
        if not s.hareket_id:
            continue
        tutar = s.hareket.tutar_try or SIFIR
        if tutar <= 0:
            return None
        if s.maliyet_try != tutar:
            s.maliyet_try = tutar
            s.save(update_fields=["maliyet_try", "updated_at"])
            degisti = True
        try:
            stok_toplam[kategori_servis.stok_muhasebe_hesabi(s.stok).hesap_kodu] += tutar
        except KategoriHatasi as e:
            raise NumuneHatasi(f"{s.stok.kod}: {e}")
    if not degisti:
        return nc.fis
    gider_satiri = nc.fis.satirlar.filter(silindi=False, borc__gt=0).select_related("hesap").first()   # onay anındaki gider hesabı korunur
    gider_kodu = gider_satiri.hesap.hesap_kodu if gider_satiri else gider_hesabi().hesap_kodu
    toplam = sum(stok_toplam.values(), SIFIR)
    try:
        fis_guncelle(nc.fis, tarih=nc.fis.tarih,
                     satirlar=[SatirGirdi(gider_kodu, "B", toplam)] + [SatirGirdi(kod, "A", t) for kod, t in stok_toplam.items()],
                     aciklama=nc.fis.aciklama, kur_usd=nc.fis.kur_usd, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise NumuneHatasi(str(e))
    return nc.fis


@transaction.atomic
def numune_geri_al(nc: NumuneCikis, kullanici=None) -> NumuneCikis:
    nc = NumuneCikis.objects.select_for_update(of=("self",)).select_related("fis").get(pk=nc.pk)
    if nc.durum != NumuneCikis.Durum.ONAYLI:
        return nc
    if nc.fis_id and not nc.fis.silindi:
        fis_iptal(nc.fis, kullanici=kullanici)
    for s in _aktif_satirlar(nc):
        if s.hareket_id:
            try:
                hareket_sil(s.hareket, kullanici=kullanici, _numune_icinden=True)
            except HareketHatasi as e:
                raise NumuneHatasi(str(e))
        s.hareket, s.maliyet_try = None, None
        s.save(update_fields=["hareket", "maliyet_try", "updated_at"])
    nc.fis, nc.durum, nc.updated_by = None, NumuneCikis.Durum.TASLAK, kullanici
    nc.save(update_fields=["fis", "durum", "updated_by", "updated_at"])
    return nc


def toplamlar(nc: NumuneCikis):
    """(adet, maliyet TL) — maliyet yalnız onaylıda."""
    satirlar = list(nc.satirlar.filter(silindi=False))
    adet = sum((s.miktar for s in satirlar), Decimal("0"))
    maliyet = sum((s.maliyet_try or SIFIR for s in satirlar), SIFIR) if nc.durum == NumuneCikis.Durum.ONAYLI else None
    return adet, maliyet
