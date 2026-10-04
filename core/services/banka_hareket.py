"""BANKA hesabı hareket motoru — banka hareketinden OTOMATİK dengeli yevmiye fişi.

Kasa motorunun (`kasa_hareket.py`) banka hesabı karşılığı: her hareket = bir yevmiye
fişi (kaynak=BANKA, kaynak banka hesabına bağlı). Yeni bakiye/hareket modeli YOK;
bakiye/ekstre bağlı muhasebe hesabının (102.x) yevmiyesinden gelir. Fiş hem banka
hesabının hem karşı tarafın hesabını işlediği için ilgili tüm ekstrelerde görünür.

7 hareket tipi — banka hesabı perspektifi (yönler KİLİTLİ; kasa modülüyle TUTARLI,
aynı işlem her iki ekrandan da kaydedilebilir):
  cari_tahsilat  Banka B / Cari A         (giriş, karşı=Cari)
  cari_odeme     Banka A / Cari B          (çıkış, karşı=Cari)
  banka_virman   Kaynak Banka A / Hedef Banka B  (transfer, karşı=BankaHesap)
  banka_yatan    Banka B / Kasa A          (giriş, karşı=Kasa)  kasadan bankaya nakit
  banka_cekilen  Banka A / Kasa B          (çıkış, karşı=Kasa)  bankadan kasaya nakit
  hesaba_odeme   Banka A / Karşı hesap(lar) B   (çıkış, karşı=hesap planından muavin hesap;
                 çok satırlı: ör. kredi taksidi 300 anapara + 780 faiz, EFT + masraf)
  hesaptan_giris Banka B / Karşı hesap(lar) A   (giriş, karşı=hesap planından muavin hesap)
  Karşı satırların toplamı banka tutarına EŞİT olmalı; 258 karşı hesabında yatırım projesi
  zorunludur. Döviz hesapta işlem dövizi + kur her satırda saklanır (kuruş farkı son satırda).

İlk dilim: banka hesabının para birimi + TCMB kuru. Karşı taraf (banka/kasa) banka
hesabıyla AYNI para biriminde olmalı (çapraz kur sonraki dilim).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, Kur, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi,
                                   fis_olustur)


class BankaHareketHatasi(ValueError):
    """Banka hareketi kural ihlali (Türkçe mesaj)."""


# tip -> davranış. banka: banka hesabının tarafı (B=giriş/A=çıkış); karsi: karşı taraf
# tipi; yon: gösterim; ikon + ack (fiş açıklaması ön eki) + aciklama (form ipucu).
HAREKET = {
    "cari_tahsilat": {"ad": "Cari Tahsilat", "banka": "B", "karsi": "cari", "yon": "giris",
                      "ikon": "📥", "ack": "TAHSİLAT",
                      "aciklama": "Müşteriden bankaya — Banka borç / Cari alacak."},
    "cari_odeme": {"ad": "Cari Ödeme", "banka": "A", "karsi": "cari", "yon": "cikis",
                   "ikon": "📤", "ack": "ÖDEME",
                   "aciklama": "Bankadan tedarikçiye — Cari borç / Banka alacak."},
    "banka_virman": {"ad": "Banka Virman", "banka": "A", "karsi": "banka", "yon": "notr",
                     "ikon": "🔁", "ack": "VİRMAN",
                     "aciklama": "Bankadan bankaya — Hedef banka borç / Kaynak banka alacak."},
    "banka_yatan": {"ad": "Banka Yatan", "banka": "B", "karsi": "kasa", "yon": "giris",
                    "ikon": "🏦", "ack": "BANKAYA YATAN",
                    "aciklama": "Kasadan bankaya nakit — Banka borç / Kasa alacak."},
    "banka_cekilen": {"ad": "Banka Çekilen", "banka": "A", "karsi": "kasa", "yon": "cikis",
                      "ikon": "🏧", "ack": "BANKADAN ÇEKİLEN",
                      "aciklama": "Bankadan kasaya nakit — Kasa borç / Banka alacak."},
    "hesaba_odeme": {"ad": "Hesaba Ödeme", "banka": "A", "karsi": "hesap", "yon": "cikis",
                     "ikon": "💸", "ack": "HESABA ÖDEME",
                     "aciklama": "Bankadan muavin hesaba (kredi taksidi, masraf, ortak…) — "
                                 "Karşı hesap(lar) borç / Banka alacak."},
    "hesaptan_giris": {"ad": "Hesaptan Giriş", "banka": "B", "karsi": "hesap", "yon": "giris",
                       "ikon": "💰", "ack": "HESAPTAN GİRİŞ",
                       "aciklama": "Muavin hesaptan bankaya (kredi kullanımı, iade…) — "
                                   "Banka borç / Karşı hesap(lar) alacak."},
}


def _kur_coz(pb, tarih, cari=None):
    """Banka hesabının para biriminin fiş tarihindeki TCMB kuru — karşı taraf bir
    Cari'yse onun kur_tipi tercihine göre (bkz. Kur.deger), değilse MB Alış. TRY -> 1."""
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    kur_tipi = cari.kur_tipi if cari else Cari.KurTipi.MB_ALIS
    deger = k.deger(pb, kur_tipi) if k else None
    if not deger:
        raise BankaHareketHatasi(
            f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından bu tarihi "
            f"çekmeden döviz banka hesabı hareketi girilemez.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise BankaHareketHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise BankaHareketHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _cari_hesap(cari):
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise BankaHareketHatasi(
            f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açılmalı.")
    return hesap


def _karsi_coz(tip, banka_hesap, karsi):
    """Karşı taraf nesnesinden (Cari/BankaHesap/Kasa) muhasebe hesabı + ad döner;
    banka/kasa karşı tarafı banka hesabıyla aynı para biriminde olmalı."""
    tur = HAREKET[tip]["karsi"]
    if tur == "cari":
        return _cari_hesap(karsi).hesap_kodu, karsi.unvan
    if tur == "banka":
        if karsi.pk == banka_hesap.pk:
            raise BankaHareketHatasi("Virman aynı banka hesabına yapılamaz.")
        if karsi.para_birimi != banka_hesap.para_birimi:
            raise BankaHareketHatasi(
                f"Hedef hesap ({karsi.para_birimi}) ile kaynak hesap "
                f"({banka_hesap.para_birimi}) para birimi farklı; çapraz kurlu virman "
                f"sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, f"{karsi.banka.ad} - {karsi.ad}"
    if tur == "kasa":
        if karsi.para_birimi != banka_hesap.para_birimi:
            raise BankaHareketHatasi(
                f"Kasa ({karsi.para_birimi}) ile banka hesabı ({banka_hesap.para_birimi}) "
                f"para birimi farklı; çapraz kurlu hareket sonraki dilimde.")
        return karsi.muhasebe.hesap_kodu, karsi.ad
    raise BankaHareketHatasi("Geçersiz hareket tipi.")


def _hesap_satirlarini_coz(banka_hesap, satirlar, toplam):
    """Hesaba Ödeme/Hesaptan Giriş karşı satırlarını doğrular. ``satirlar`` = [{"hesap_kodu",
    "tutar" (tek satırda boş olabilir -> tamamı), "aciklama", "yatirim_projesi_id"}]. Döner:
    [(HesapPlani, tutar, aciklama, proje_id)]. Toplam banka tutarına eşit olmalı."""
    from core.services.hesap_plani import hesap_kodu_258_mi, yaprak_mi
    satirlar = [s for s in (satirlar or []) if s and s.get("hesap_kodu")]
    if not satirlar:
        raise BankaHareketHatasi("En az bir karşı hesap satırı gerekli.")
    cozulen = []
    for i, s in enumerate(satirlar, 1):
        hesap = HesapPlani.objects.filter(
            hesap_kodu=s["hesap_kodu"], aktif=True, silindi=False).first()
        if hesap is None or not yaprak_mi(hesap):
            raise BankaHareketHatasi(
                f"Satır {i}: karşı hesap bulunamadı ya da yaprak (fişe kesilebilir) değil.")
        if hesap.hesap_kodu == banka_hesap.muhasebe.hesap_kodu:
            raise BankaHareketHatasi(f"Satır {i}: karşı hesap banka hesabının kendisi olamaz.")
        ham = s.get("tutar")
        if ham in (None, "") and len(satirlar) == 1:
            t = toplam
        else:
            t = _tutar(ham)
        proje_id = s.get("yatirim_projesi_id") or None
        if hesap_kodu_258_mi(hesap.hesap_kodu) and not proje_id:
            raise BankaHareketHatasi(
                f"Satır {i}: 258 hesabı için yatırım projesi seçilmelidir.")
        if proje_id and not hesap_kodu_258_mi(hesap.hesap_kodu):
            raise BankaHareketHatasi(
                f"Satır {i}: yatırım projesi yalnız 258 hesabında seçilebilir.")
        cozulen.append((hesap, t, (s.get("aciklama") or "").strip(), proje_id))
    fark = toplam - sum((c[1] for c in cozulen), Decimal("0"))
    if fark != 0:
        raise BankaHareketHatasi(
            f"Karşı satırların toplamı ({sum((c[1] for c in cozulen), Decimal('0')):,.2f}) "
            f"banka tutarına ({toplam:,.2f}) eşit olmalı; fark {fark:,.2f}.")
    return cozulen


@transaction.atomic
def hareket_olustur(*, banka_hesap, tip, karsi, tutar, tarih, aciklama="", kullanici=None,
                    kur_override=None, satirlar=None, sayilan_pb=None, sayilan_doviz=None) -> YevmiyeFisi:
    """Bir banka hesabı hareketinden otomatik DENGELİ yevmiye fişi üretir
    (kaynak=BANKA, kaynak banka hesabı=`banka_hesap`). `karsi` tipe göre
    Cari / (hedef) BankaHesap / Kasa. İhlalde hiçbir şey kaydedilmez. ``kur_override``
    doluysa (kullanıcı formda elle girdi/değiştirdi) carinin kur_tipi'ne göre otomatik
    hesaplama YERİNE doğrudan kullanılır. Hesaba Ödeme/Hesaptan Giriş tiplerinde ``karsi`` yerine
    ``satirlar`` (bir ya da birden çok karşı hesap satırı) verilir; ``tutar`` banka tutarıdır."""
    if tip not in HAREKET:
        raise BankaHareketHatasi("Geçersiz hareket tipi.")
    if banka_hesap.muhasebe_id is None:
        raise BankaHareketHatasi("Banka hesabının muhasebe hesabı tanımlı değil.")
    tan = HAREKET[tip]
    tut = _tutar(tutar)
    hesap_satirlari = None
    if tan["karsi"] == "hesap":
        hesap_satirlari = _hesap_satirlarini_coz(banka_hesap, satirlar, tut)
        karsi_kod = None
        karsi_ad = (hesap_satirlari[0][0].hesap_adi if len(hesap_satirlari) == 1
                    else f"{len(hesap_satirlari)} KALEM")
    else:
        karsi_kod, karsi_ad = _karsi_coz(tip, banka_hesap, karsi)
    pb = banka_hesap.para_birimi
    if pb == "TRY":
        kur = Decimal("1")
    else:
        cari_karsi = karsi if tan["karsi"] == "cari" else None
        kur = kur_override or _kur_coz(pb, tarih, cari=cari_karsi)
    banka_taraf = tan["banka"]
    karsi_taraf = "A" if banka_taraf == "B" else "B"
    ack = (buyuk_harf_tr((aciklama or "").strip())
           or buyuk_harf_tr(f"BANKA {tan['ack']} - {karsi_ad}"))

    fis_satirlari = [SatirGirdi(hesap_kodu=banka_hesap.muhasebe.hesap_kodu, taraf=banka_taraf,
                                islem_tutari=tut, islem_pb=pb, islem_kuru=kur)]
    if hesap_satirlari is None:
        fis_satirlari.append(SatirGirdi(hesap_kodu=karsi_kod, taraf=karsi_taraf,
                                        islem_tutari=tut, islem_pb=pb, islem_kuru=kur))
    else:
        banka_tl = yuvarla(tut * kur, 2)
        verilen_tl = Decimal("0")
        for i, (hesap, t, satir_ack, proje_id) in enumerate(hesap_satirlari):
            son = i == len(hesap_satirlari) - 1
            tl = banka_tl - verilen_tl if son else yuvarla(t * kur, 2)     # kuruş farkı son satırda
            verilen_tl += tl
            fis_satirlari.append(SatirGirdi(
                hesap_kodu=hesap.hesap_kodu, taraf=karsi_taraf, islem_tutari=t, islem_pb=pb,
                islem_kuru=kur, aciklama=satir_ack, yatirim_projesi_id=proje_id,
                tl_override=(tl if pb != "TRY" else None)))
    if tip == "cari_odeme" and pb == "TRY" and hesap_satirlari is None:
        # Döviz carisine TL ödeme: cari satırı ödeme günü TCMB alış kuruyla dövize çevrilir (TL aynı; bkz. doviz_cari).
        from core.services import doviz_cari
        try:
            if doviz_cari.donusum_gerekli_mi(karsi, sayilan_pb) or sayilan_doviz not in (None, ""):
                fis_satirlari[1] = doviz_cari.cari_satiri(karsi, tut, tarih, karsi_taraf, sayilan_pb, doviz_tutar=sayilan_doviz)
        except doviz_cari.DovizCariHatasi as e:
            raise BankaHareketHatasi(str(e))
    elif sayilan_doviz not in (None, ""):
        raise BankaHareketHatasi("Sayılan döviz tutarı yalnız TL banka hesabından cariye yapılan ödemede girilebilir.")
    try:
        fis = fis_olustur(tarih=tarih, satirlar=fis_satirlari, aciklama=ack, kur_usd=None,
                          kaynak=YevmiyeFisi.Kaynak.BANKA, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise BankaHareketHatasi(str(e))
    fis.banka_hesap = banka_hesap
    fis.save(update_fields=["banka_hesap", "updated_at"])
    return fis


def hareket_sil(*, fis, banka_hesap, kullanici=None):
    """Banka hareketini (kaynak=BANKA) KALICI siler: fiş + satırlar (yalnız süper kullanıcı,
    denetim kaydı yazılır, bağlı kayıt varsa reddedilir). Fiş bu hesabın hareketi değilse reddeder."""
    if fis.kaynak != YevmiyeFisi.Kaynak.BANKA or fis.banka_hesap_id != banka_hesap.pk:
        raise BankaHareketHatasi("Bu fiş bu banka hesabının hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.BANKA})
    except SilmeHatasi as e:
        raise BankaHareketHatasi(str(e))


def duzenleme_bilgisi(fis, banka_hesap):
    """Düzenleme ekranı için: TL banka hesabından CARİYE yapılan ödeme (cari satırı BORÇ) hareketi.
    {'cari', 'banka_satiri', 'cari_satiri', 'tl'} — başka yapıdaki hareket düzenlenemez."""
    from core.models import Cari
    if fis.kaynak != YevmiyeFisi.Kaynak.BANKA or fis.banka_hesap_id != banka_hesap.pk or fis.silindi:
        raise BankaHareketHatasi("Bu fiş bu banka hesabının düzenlenebilir bir hareketi değil.")
    if banka_hesap.para_birimi != "TRY":
        raise BankaHareketHatasi("Yalnız TL banka hesabının hareketi bu ekranda düzenlenir.")
    ana = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True).select_related("hesap").order_by("id"))
    banka_s = [x for x in ana if x.hesap_id == banka_hesap.muhasebe_id]
    karsi_s = [x for x in ana if x.hesap_id != banka_hesap.muhasebe_id]
    if len(ana) != 2 or len(banka_s) != 1 or len(karsi_s) != 1:
        raise BankaHareketHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    cari = Cari.objects.filter(muhasebe_kodu=karsi_s[0].hesap_id, silindi=False).first()
    if cari is None or not karsi_s[0].borc or not banka_s[0].alacak or banka_s[0].islem_pb != "TRY":
        raise BankaHareketHatasi("Yalnız cariye yapılan ödeme (cari BORÇ / banka ALACAK) bu ekranda düzenlenir.")
    return {"cari": cari, "banka_satiri": banka_s[0], "cari_satiri": karsi_s[0], "tl": banka_s[0].alacak}


@transaction.atomic
def hareket_guncelle(*, fis, banka_hesap, aciklama=None, sayilan_pb=None, sayilan_doviz=None, kullanici=None):
    """Cariye ödeme hareketini DÜZENLER: açıklama + sayılan para birimi / sayılan döviz tutarı. Tutar (TL), tarih ve banka tarafı
    DEĞİŞMEZ. ``sayilan_doviz`` doluysa kur = TL / döviz tutarı; yalnız ``sayilan_pb`` doluysa ödeme günü TCMB alış kuru;
    ikisi de boşsa cari satırı olduğu gibi kalır. Fiş ``fis_guncelle`` ile yeniden yazılır (kur farkı motoru yeniden çalışır)."""
    from core.services import doviz_cari
    from core.services.yevmiye import fis_guncelle
    b = duzenleme_bilgisi(fis, banka_hesap)
    banka_s, cari_s = b["banka_satiri"], b["cari_satiri"]
    yeni_ack = fis.aciklama if aciklama is None else (buyuk_harf_tr((aciklama or "").strip()) or fis.aciklama)

    def _ayni(ln):
        return SatirGirdi(hesap_kodu=ln.hesap_id, taraf="B" if ln.borc else "A", islem_tutari=ln.islem_tutari,
                          islem_pb=ln.islem_pb, islem_kuru=ln.islem_kuru, aciklama=ln.aciklama,
                          yatirim_projesi_id=ln.yatirim_projesi_id)
    karsi = _ayni(cari_s)
    if (sayilan_pb or "").strip() or sayilan_doviz not in (None, ""):
        try:
            karsi = doviz_cari.cari_satiri(b["cari"], b["tl"], fis.tarih, "B", sayilan_pb, aciklama=cari_s.aciklama,
                                           yatirim_projesi_id=cari_s.yatirim_projesi_id, doviz_tutar=sayilan_doviz)
        except doviz_cari.DovizCariHatasi as e:
            raise BankaHareketHatasi(str(e))
    try:
        fis_guncelle(fis, tarih=fis.tarih, aciklama=yeni_ack, kur_usd=fis.kur_usd, kullanici=kullanici,
                     satirlar=[_ayni(banka_s), karsi])
    except YevmiyeHatasi as e:
        raise BankaHareketHatasi(str(e))
    return fis
