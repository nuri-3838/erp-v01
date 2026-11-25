"""CARİ VİRMAN — bir carinin bakiyesini başka bir cariye (özellikle ortak 500.10.0001'e) her iki yönde aktarır; manuel mahsup fişi yerine.

Cari kartından: tarih, tutar (TL), karşı cari (tüm cariler), yön:
  "alacak": Bu cari ALACAK / karşı cari BORÇ      "borc": Bu cari BORÇ / karşı cari ALACAK
Karşı taraf cari YERİNE HESAP da olabilir (gider 7xx/65x/66x/68x, gelir 64x/67x, 258 + proje; Kesinti ile aynı küme): örn. araç kiralama
depozito iadesi 500.10.0001 NURİ ÖZER BORÇ 4.500 / 770.10 SEYAHAT GİDERİ ALACAK 4.500.
Fiş otomatik ve dengeli (kaynak=CARI_VIRMAN; fiş→cari ve fiş→karşı cari bağı); iki carinin ekstresinde de görünür; Düzenle ve (yalnız süper
kullanıcı) kalıcı Sil vardır ("iptal" durumu yok). Döviz carilerde TL tutar ödeme günü TCMB alış kuruyla cari para birimine çevrilir (TL aynı;
bkz. core.services.doviz_cari; ``sayilan_pb='TRY'`` = çevirme, TL havuzu).
Örnek (Kaygun'dan elden iade): 320.30.0038 KAYGUN ALACAK 75.500 / 500.10.0001 NURİ ÖZER BORÇ 75.500.

KUR FARKI VİRMANI (2026-11-25, BAREN PROJE 2026/2463 vakası): karşı hesap 646/656 iken ``kur_farki_pb`` (USD/EUR/GBP) seçilirse cari satırı o
DÖVİZ havuzuna bağlanır ama döviz tutarı 0'dır (yalnız TL etkisi — dönem sonu kur değerleme satırlarıyla aynı yapı; bkz. kur_farki._girdi):
havuzun döviz bakiyesi değişmez, TL karşılığı kapanır; TRY havuzu etkilenmez. Fiş TL'si aynıdır (cari ALACAK/BORÇ tutar TL, 646/656 karşı satır).
Varsayılan havuz: carinin hesabında döviz bakiyesi 0 ama TL farkı kalmış TEK döviz havuzu (``varsayilan_kur_farki_pb``).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, YevmiyeFisi
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import doviz_cari
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur

YONLER = (("alacak", "Bu cari ALACAK / karşı cari BORÇ"), ("borc", "Bu cari BORÇ / karşı cari ALACAK"))


class CariVirmanHatasi(ValueError):
    """Cari virman kural ihlali (Türkçe mesaj)."""


def kur_farki_hesabi_mi(kod) -> bool:
    """646 KAMBİYO KÂRLARI / 656 KAMBİYO ZARARLARI (ya da alt hesapları)."""
    from core.services.kur_farki import KAR_HESABI, ZARAR_HESABI
    kod = kod or ""
    return any(kod == k or kod.startswith(k + ".") for k in (KAR_HESABI, ZARAR_HESABI))


def kur_farki_havuzlari(cari) -> list:
    """Carinin muhasebe hesabındaki DÖVİZ havuzları: [{pb, doviz (q, borç +), tl (v, borç +)}] — kur farkı virmanının bağlanabileceği havuzlar."""
    from core.models import YevmiyeSatir
    if not cari or not cari.muhasebe_kodu:
        return []
    h = {}
    for s in YevmiyeSatir.objects.filter(hesap_id=cari.muhasebe_kodu, silindi=False, fis__silindi=False).exclude(islem_pb="TRY"):
        t = h.setdefault(s.islem_pb, [Decimal("0"), Decimal("0")])
        t[0] += (1 if s.borc > 0 else -1) * s.islem_tutari
        t[1] += s.borc - s.alacak
    return [{"pb": pb, "doviz": q, "tl": v} for pb, (q, v) in sorted(h.items())]


def varsayilan_kur_farki_pb(cari):
    """Döviz bakiyesi 0 ama TL farkı kalmış TEK döviz havuzunun para birimi (yoksa ya da birden çoksa None)."""
    adaylar = [h["pb"] for h in kur_farki_havuzlari(cari) if h["doviz"] == 0 and h["tl"] != 0]
    return adaylar[0] if len(adaylar) == 1 else None


def _kur_farki_kuru(cari, pb, tarih):
    """Döviz tutarı 0 olan satırın kaydı için kur (TL'yi ETKİLEMEZ, yalnız bilgi): tarihin (yoksa 7 gün öncesine kadar) TCMB alış kuru,
    o da yoksa havuzun son satırının kuru, o da yoksa 1."""
    from core.models import YevmiyeSatir
    from core.services.kur_degerleme import kur_bul
    kur = kur_bul(pb, tarih)[0]
    if kur:
        return yuvarla(kur, 6)
    son = (YevmiyeSatir.objects.filter(hesap_id=cari.muhasebe_kodu, islem_pb=pb, silindi=False, fis__silindi=False, islem_tutari__gt=0)
           .order_by("-fis__tarih", "-id").values_list("islem_kuru", flat=True).first())
    return son or Decimal("1")


def kur_farki_satiri(cari, tl_tutar, tarih, taraf, pb, aciklama=""):
    """Kur farkı virmanının cari satırı: ``pb`` döviz havuzuna bağlı, döviz tutarı 0, TL = ``tl_tutar``."""
    pb = (pb or "").strip().upper()
    if pb not in doviz_cari.DOVIZLER:
        raise CariVirmanHatasi("Kur farkı havuzu bir döviz olmalı (USD/EUR/GBP).")
    if pb not in {h["pb"] for h in kur_farki_havuzlari(cari)}:
        raise CariVirmanHatasi(f"{cari.unvan} carisinin hesabında {pb} havuzu yok; kur farkı bu havuza bağlanamaz.")
    return SatirGirdi(hesap_kodu=cari.muhasebe_kodu, taraf=taraf, islem_tutari=Decimal("0"), islem_pb=pb,
                      islem_kuru=_kur_farki_kuru(cari, pb, tarih), tl_override=tl_tutar, aciklama=aciklama)


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariVirmanHatasi("Tutar geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d <= 0:
        raise CariVirmanHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _kur(deger):
    """İsteğe bağlı elle kur: boş → None; dolu → 6 basamak, sıfırdan büyük."""
    if deger in (None, ""):
        return None
    try:
        d = parse_tr(deger)
    except SayiHatasi:
        raise CariVirmanHatasi("Kur geçerli bir sayı olmalı.")
    d = yuvarla(d, 6)
    if d <= 0:
        raise CariVirmanHatasi("Kur sıfırdan büyük olmalı.")
    return d


def _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama, karsi_hesap_kodu=None, yatirim_projesi_id=None, kur=None, mevcut_idler=(),
             kur_farki_pb=None):
    from core.services.cari import pasif_kontrol
    for c in (cari, karsi_cari):
        if c is not None and c.pk not in mevcut_idler:
            pasif_kontrol(c, CariVirmanHatasi, ne="Yeni virman")
    if bool(karsi_cari) == bool(karsi_hesap_kodu):
        raise CariVirmanHatasi("Karşı tarafı cari VEYA hesap olarak seçin (yalnız biri).")
    if karsi_cari is not None and karsi_cari.pk == cari.pk:
        raise CariVirmanHatasi("Karşı cari, virman yapılan cariden farklı bir cari olmalı.")
    if yon not in ("alacak", "borc"):
        raise CariVirmanHatasi("Yön seçin (bu cari ALACAK / BORÇ).")
    for c in (cari, karsi_cari):
        if c is not None and (not c.muhasebe_kodu or not HesapPlani.objects.filter(
                hesap_kodu=c.muhasebe_kodu, silindi=False, aktif=True).exists()):
            raise CariVirmanHatasi(f"{c.unvan} carisinin muhasebe hesabı yok ya da pasif.")
    tut = _tutar(tutar)
    kur_d = _kur(kur)
    taraf_cari = "A" if yon == "alacak" else "B"
    taraf_karsi = "B" if yon == "alacak" else "A"
    kur_farki_pb = (kur_farki_pb or "").strip().upper()
    if kur_farki_pb:
        if karsi_cari is not None or not kur_farki_hesabi_mi(karsi_hesap_kodu):
            raise CariVirmanHatasi("Kur farkı havuzu yalnız karşı hesap 646 KAMBİYO KÂRLARI / 656 KAMBİYO ZARARLARI iken seçilebilir.")
        if kur_d is not None:
            raise CariVirmanHatasi("Kur farkı virmanında elle kur girilmez; tutar TL'dir (döviz bakiyesi değişmez).")
    try:
        pb = doviz_cari.hedef_pb(cari, sayilan_pb)
        if kur_d is not None and pb == "TRY":
            kur_d = None                                  # TL cari / 'TL (çevirme)': elle kur yok sayılır, tutar TL
        if kur_d is not None:
            # ELLE KUR: tutar DÖVİZ cinsindendir (cari para biriminde); TL = tutar × kur; iki tarafta aynı döviz/TL (kur farkı doğmaz).
            tut_tl = yuvarla(tut * kur_d, 2)
            satirlar = [doviz_cari.elle_kur_satiri(cari, tut, kur_d, taraf_cari, pb, aciklama=cari.unvan)]
        elif kur_farki_pb:
            # KUR FARKI: cari satırı döviz havuzuna bağlı, döviz tutarı 0 → havuzun yalnız TL karşılığı değişir (TRY havuzu etkilenmez).
            tut_tl = tut
            satirlar = [kur_farki_satiri(cari, tut, tarih, taraf_cari, kur_farki_pb, aciklama=f"{cari.unvan} · {kur_farki_pb} KUR FARKI")]
        else:
            tut_tl = tut
            satirlar = [doviz_cari.cari_satiri(cari, tut, tarih, taraf_cari, sayilan_pb, aciklama=cari.unvan)]
        if karsi_cari is not None:
            if kur_d is not None:
                pb2 = doviz_cari.hedef_pb(karsi_cari, sayilan_pb)
                if pb2 == pb:
                    satirlar.append(doviz_cari.elle_kur_satiri(karsi_cari, tut, kur_d, taraf_karsi, pb, aciklama=karsi_cari.unvan))
                elif pb2 == "TRY":
                    satirlar.append(doviz_cari.cari_satiri(karsi_cari, tut_tl, tarih, taraf_karsi, sayilan_pb, aciklama=karsi_cari.unvan))
                else:
                    raise CariVirmanHatasi(f"Elle kurda iki cari aynı para biriminde olmalı ({pb} ↔ {pb2}); karşı cariyi TL ya da aynı dövizde seçin.")
            else:
                satirlar.append(doviz_cari.cari_satiri(karsi_cari, tut, tarih, taraf_karsi, sayilan_pb, aciklama=karsi_cari.unvan))
            hedef_ad = karsi_cari.unvan
        else:
            from core.services import cari_kesinti
            try:
                hesap = cari_kesinti.gider_hesabi_coz(karsi_hesap_kodu)
                proje_id = cari_kesinti.proje_coz(hesap, yatirim_projesi_id)
            except cari_kesinti.CariKesintiHatasi as e:
                raise CariVirmanHatasi(str(e))
            satirlar.append(SatirGirdi(hesap_kodu=hesap.hesap_kodu, taraf=taraf_karsi, islem_tutari=tut_tl,
                                       aciklama=hesap.hesap_adi, yatirim_projesi_id=proje_id))
            hedef_ad = hesap.hesap_adi
    except doviz_cari.DovizCariHatasi as e:
        raise CariVirmanHatasi(str(e))
    ack = buyuk_harf_tr((aciklama or "").strip()) or buyuk_harf_tr(f"CARİ VİRMAN - {cari.unvan} / {hedef_ad}")
    return satirlar, ack[:500]


@transaction.atomic
def virman_olustur(*, cari, karsi_cari=None, tarih, tutar, yon, aciklama="", sayilan_pb=None, kullanici=None,
                   karsi_hesap_kodu=None, yatirim_projesi_id=None, kur=None, kur_farki_pb=None) -> YevmiyeFisi:
    """``kur`` (opsiyonel, elle): dolu ve cari döviz iken ``tutar`` DÖVİZ cinsindendir, TL = tutar × kur; boşsa tutar TL, tarihin TCMB kuru.
    ``kur_farki_pb`` (yalnız karşı hesap 646/656): cari satırı o döviz havuzuna döviz tutarı 0 ile bağlanır (bkz. modül notu)."""
    satirlar, ack = _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama, karsi_hesap_kodu, yatirim_projesi_id, kur,
                             kur_farki_pb=kur_farki_pb)
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=ack, kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariVirmanHatasi(str(e))
    fis.cari, fis.karsi_cari = cari, karsi_cari
    fis.save(update_fields=["cari", "karsi_cari", "updated_at"])
    return fis


def _fis_kontrol(fis, cari):
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_VIRMAN or fis.silindi or cari.pk not in (fis.cari_id, fis.karsi_cari_id):
        raise CariVirmanHatasi("Bu fiş bu carinin düzenlenebilir bir virman hareketi değil.")


def duzenleme_bilgisi(fis, cari):
    """{'karsi_cari' | 'karsi_hesap' (+ 'yatirim_projesi_id'), 'yon', 'tutar' (TL), 'sayilan_pb'} — ``cari`` ekranının bakış açısıyla."""
    _fis_kontrol(fis, cari)
    karsi = (fis.karsi_cari if cari.pk == fis.cari_id else fis.cari)
    s = list(fis.satirlar.filter(silindi=False, ana_satir__isnull=True, hesap_id=cari.muhasebe_kodu))
    if len(s) != 1:
        raise CariVirmanHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
    s = s[0]
    tl = s.ham_tl if s.ham_tl is not None else (s.borc or s.alacak)       # kur farkı motoru satır TL'sini değiştirmiş olabilir
    out = {"karsi_cari": karsi, "karsi_hesap": None, "yatirim_projesi_id": None, "yon": "alacak" if s.alacak else "borc",
           "tutar": tl, "sayilan_pb": (s.islem_pb if cari.para_birimi != "TRY" else ""), "kur": None, "kur_farki_pb": ""}
    if s.islem_pb != "TRY" and s.islem_tutari == 0:                       # KUR FARKI virmanı: döviz havuzuna döviz 0 / yalnız TL bağlı satır
        out["kur_farki_pb"], out["sayilan_pb"], out["tutar"] = s.islem_pb, "", (s.borc or s.alacak)
    elif s.islem_pb != "TRY" and s.islem_kuru:                                # elle girilmiş kur mu? (tarihin TCMB alış kurundan farklıysa)
        try:
            tcmb = doviz_cari.alis_kuru(s.islem_pb, fis.tarih)
        except doviz_cari.DovizCariHatasi:
            tcmb = None
        if tcmb is None or yuvarla(s.islem_kuru, 6) != yuvarla(tcmb, 6):
            out["kur"], out["tutar"] = s.islem_kuru, s.islem_tutari          # elle kurda tutar DÖVİZ cinsindendir
    if karsi is None:                                                       # karşı taraf HESAP
        diger = [x for x in fis.satirlar.filter(silindi=False, ana_satir__isnull=True) if x.pk != s.pk]
        if len(diger) != 1:
            raise CariVirmanHatasi("Hareketin satır yapısı düzenlemeye uygun değil.")
        out["karsi_hesap"] = diger[0].hesap
        out["yatirim_projesi_id"] = diger[0].yatirim_projesi_id
    return out


@transaction.atomic
def virman_guncelle(*, fis, cari, karsi_cari=None, tarih, tutar, yon, aciklama="", sayilan_pb=None, kullanici=None,
                    karsi_hesap_kodu=None, yatirim_projesi_id=None, kur=None, kur_farki_pb=None) -> YevmiyeFisi:
    _fis_kontrol(fis, cari)
    satirlar, ack = _hazirla(cari, karsi_cari, tarih, tutar, yon, sayilan_pb, aciklama, karsi_hesap_kodu, yatirim_projesi_id, kur,
                             mevcut_idler=(fis.cari_id, fis.karsi_cari_id), kur_farki_pb=kur_farki_pb)
    try:
        fis_guncelle(fis, tarih=tarih, satirlar=satirlar, aciklama=ack, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CariVirmanHatasi(str(e))
    fis.cari, fis.karsi_cari = cari, karsi_cari
    fis.save(update_fields=["cari", "karsi_cari", "updated_at"])
    return fis


@transaction.atomic
def virman_sil(*, fis, cari, kullanici=None):
    """Virman hareketini KALICI siler (fiş dahil; yalnız süper kullanıcı; denetim kaydı yazılır)."""
    if fis.kaynak != YevmiyeFisi.Kaynak.CARI_VIRMAN or cari.pk not in (fis.cari_id, fis.karsi_cari_id):
        raise CariVirmanHatasi("Bu fiş bu carinin virman hareketi değil.")
    try:
        return fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.CARI_VIRMAN})
    except SilmeHatasi as e:
        raise CariVirmanHatasi(str(e))


def cari_virman_fisleri(cari):
    """Cari ekstresinde Düzenle/Sil gösterilecek virman fişlerinin pk'leri (carinin kendisi ya da karşı carisi olduğu)."""
    from django.db.models import Q
    return set(YevmiyeFisi.objects.filter(Q(cari=cari) | Q(karsi_cari=cari), kaynak=YevmiyeFisi.Kaynak.CARI_VIRMAN,
                                          silindi=False).values_list("pk", flat=True))
