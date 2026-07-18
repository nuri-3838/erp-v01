"""ÇEK/SENET modülü (BORDRO mantığı) — servis katmanı.

Slice 1: muhasebe hesap eşlemesi (CekHesapAyari) oku/kaydet. Her durum için çek ve
senet ayrı yaprak hesaba bağlanır; bordro işlemleri yevmiye fişini bu eşlemeden,
evrak tipine (çek/senet) bakarak üretir. Bordro motoru sonraki dilimlerde.
"""
from __future__ import annotations

import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (BankaHesap, Cari, CekBordrosu, CekBordroSatir, CekHesapAyari,
                         CekSenet, HesapPlani, Kasa, Kur, YevmiyeFisi)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services.finans import FinansHatasi, _soft_sil, _yaprak_hesap_coz
from core.services.yevmiye import (SatirGirdi, YevmiyeHatasi, fis_iptal,
                                   fis_olustur)


class CekHatasi(ValueError):
    """Çek/senet kural ihlali (Türkçe mesaj)."""


# Ayar alanları (durum + tip). Form/DB/initial hep bu listeyi kullanır.
AYAR_ALANLARI = (
    "portfoy_cek", "portfoy_senet",
    "tahsilde_cek", "tahsilde_senet",
    "teminatta_cek", "teminatta_senet",
    "verilen_cek", "verilen_senet",
)


def hesap_ayari() -> CekHesapAyari:
    """Tekil çek/senet hesap ayarı (yoksa oluşturur)."""
    return CekHesapAyari.get()


@transaction.atomic
def hesap_ayari_kaydet(kodlar: dict, kullanici=None) -> CekHesapAyari:
    """kodlar: {alan: hesap_kodu | ""}. Dolu olanlar YAPRAK doğrulanıp atanır;
    boş olanlar None'a çekilir (o durum henüz tanımlanmadı)."""
    ayar = CekHesapAyari.get()
    for alan in AYAR_ALANLARI:
        kod = (kodlar.get(alan) or "").strip()
        if kod:
            try:
                hesap = _yaprak_hesap_coz(kod)
            except FinansHatasi as e:
                raise CekHatasi(str(e))
        else:
            hesap = None
        setattr(ayar, alan, hesap)
    ayar.updated_by = kullanici
    ayar.save()
    return ayar


# === Slice 2: Cari Giriş bordrosu (çok çek/senet → tek birleşik fiş) ===
# Durum alanı adları config matrisindeki ön ekler ("portfoy"/"tahsilde"/"teminatta"/"verilen").
_DURUM_AD = {"portfoy": "Portföydeki", "tahsilde": "Bankada Tahsildeki",
             "teminatta": "Bankada Teminattaki", "verilen": "Verilen"}


def _ayar_hesap(ayar, durum_oneki, tip):
    """Config matrisinden (durum × tip) muhasebe hesabını çöz; tanımsızsa net hata."""
    alan = f"{durum_oneki}_{'cek' if tip == CekSenet.Tip.CEK else 'senet'}"
    hesap = getattr(ayar, alan, None)
    if hesap is None:
        tipad = "çek" if tip == CekSenet.Tip.CEK else "senet"
        raise CekHatasi(f"{_DURUM_AD[durum_oneki]} {tipad} hesabı tanımlı değil; "
                        f"önce Muhasebe Hesap Kodları ekranından seçin.")
    return hesap


def _cari_coz(cari_id):
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first() if cari_id else None
    if cari is None:
        raise CekHatasi("Cari (karşı taraf) seçilmedi.")
    hesap = (HesapPlani.objects.filter(hesap_kodu=cari.muhasebe_kodu, silindi=False).first()
             if cari.muhasebe_kodu else None)
    if hesap is None:
        raise CekHatasi(f"{cari.unvan} carisinin muhasebe hesabı yok; önce hesap planında açın.")
    return cari, hesap


def _pb_coz(pb):
    pb = (pb or "TRY").strip().upper()
    if pb not in dict(Cari.PARA_CHOICES):
        raise CekHatasi("Geçersiz para birimi.")
    return pb


def _kur_coz(pb, tarih):
    if pb == "TRY":
        return Decimal("1")
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    alan = {"USD": "usd_alis", "EUR": "eur_alis", "GBP": "gbp_alis"}.get(pb)
    deger = getattr(k, alan) if (k and alan) else None
    if not deger:
        raise CekHatasi(f"{tarih:%d.%m.%Y} için {pb} kuru yok; Kurlar ekranından çekin.")
    return deger


def _tutar(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CekHatasi("Tutar geçerli bir sayı olmalı.")
    if d <= 0:
        raise CekHatasi("Tutar sıfırdan büyük olmalı.")
    return d


def _kalemleri_dogrula(satirlar):
    """Form satırlarını doğrula → temiz dict listesi (tip/tutar/vade/belge_no/keşideci)."""
    temiz = []
    for s in satirlar:
        tip = s.get("tip")
        if tip not in CekSenet.Tip.values:
            raise CekHatasi("Tip Çek veya Senet olmalı.")
        if not s.get("vade"):
            raise CekHatasi("Her satır için vade tarihi zorunlu.")
        temiz.append({
            "tip": tip, "tutar": _tutar(s.get("tutar")), "vade": s.get("vade"),
            "belge_no": (s.get("belge_no") or "").strip(),
            "kesideci": buyuk_harf_tr((s.get("kesideci") or "").strip()),
            "on_yuz": s.get("on_yuz"), "arka_yuz": s.get("arka_yuz"),
        })
    if not temiz:
        raise CekHatasi("En az bir çek/senet satırı girilmeli.")
    return temiz


# Giriş/çıkış bordrosu yönleri. cek_taraf = çek hesap satırının fiş tarafı (cari TERS).
#   giriş  (ALINAN): portföy çek/senet BORÇ / cari ALACAK   → durum PORTFOYDE
#   çıkış (VERİLEN): cari BORÇ / verilen çek/senet ALACAK    → durum VERILDI
GIRIS_TANIM = {
    "giris": {"tur": CekBordrosu.Tur.CARI_GIRIS, "yon": CekSenet.Yon.ALINAN,
              "durum": CekSenet.Durum.PORTFOYDE, "oneki": "portfoy", "cek_taraf": "B",
              "ack": "ÇEK/SENET GİRİŞ"},
    "cikis": {"tur": CekBordrosu.Tur.FIRMA_CIKIS, "yon": CekSenet.Yon.VERILEN,
              "durum": CekSenet.Durum.VERILDI, "oneki": "verilen", "cek_taraf": "A",
              "ack": "FİRMA ÇEK/SENET"},
}
# Bordro türü -> oluşturduğu çek/senetlerin giriş durumu (bordro_sil "işlem görmüş" kontrolü).
_GIRIS_DURUM = {t["tur"]: t["durum"] for t in GIRIS_TANIM.values()}


@transaction.atomic
def _bordro_olustur(tan, *, cari_id, tarih, para_birimi="TRY", satirlar,
                    aciklama="", kullanici=None) -> CekBordrosu:
    """Giriş/çıkış bordrosu ortak motoru: N adet CekSenet + TEK birleşik fiş.
    tan = GIRIS_TANIM['giris'|'cikis']. Hesaplar config matrisinden (evrak tipine göre)."""
    cari, cari_hesap = _cari_coz(cari_id)
    pb = _pb_coz(para_birimi)
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    temiz = _kalemleri_dogrula(satirlar)
    cek_top = sum((s["tutar"] for s in temiz if s["tip"] == CekSenet.Tip.CEK), Decimal("0"))
    senet_top = sum((s["tutar"] for s in temiz if s["tip"] == CekSenet.Tip.SENET), Decimal("0"))
    # Çek hesabı satırları (config'den; eksikse net hata — kayıt açılmadan önce).
    cek_satir = []
    if cek_top > 0:
        cek_satir.append((_ayar_hesap(ayar, tan["oneki"], CekSenet.Tip.CEK).hesap_kodu, cek_top))
    if senet_top > 0:
        cek_satir.append((_ayar_hesap(ayar, tan["oneki"], CekSenet.Tip.SENET).hesap_kodu, senet_top))
    toplam = cek_top + senet_top
    bordro = CekBordrosu.objects.create(
        tur=tan["tur"], tarih=tarih, cari=cari,
        aciklama=(aciklama.strip() or buyuk_harf_tr(f"{tan['ack']} - {cari.unvan}")),
        created_by=kullanici, updated_by=kullanici)
    for s in temiz:
        CekSenet.objects.create(
            tip=s["tip"], yon=tan["yon"], tutar=s["tutar"], para_birimi=pb,
            vade=s["vade"], belge_no=s["belge_no"], kesideci=s["kesideci"],
            durum=tan["durum"], cari=cari, giris_bordrosu=bordro,
            on_yuz=s.get("on_yuz"), arka_yuz=s.get("arka_yuz"),
            created_by=kullanici, updated_by=kullanici)
    cek_taraf = tan["cek_taraf"]
    cari_taraf = "A" if cek_taraf == "B" else "B"
    # Çek/senet satırları (per tip) + cari DENGE satırı: dövizde per-tip yuvarlama toplamı tek
    # cari satırından sapmasın diye cari TL'si tl_override ile çek satırları toplamına eşitlenir.
    girdiler, cek_tl = [], Decimal("0")
    for kod, tut in cek_satir:
        cek_tl += yuvarla(tut * kur, 2)
        girdiler.append(SatirGirdi(hesap_kodu=kod, taraf=cek_taraf,
                                   islem_tutari=tut, islem_pb=pb, islem_kuru=kur))
    girdiler.append(SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf=cari_taraf,
                               islem_tutari=toplam, islem_pb=pb, islem_kuru=kur, tl_override=cek_tl))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


def cari_giris_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Cari Giriş: alınan çek/senetler portföye girer (portföy çek/senet borç / cari alacak)."""
    return _bordro_olustur(GIRIS_TANIM["giris"], **kwargs)


def firma_cikis_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Firma Çek-Senet: kendi çek/senedimizi cariye veririz (cari borç / verilen çek-senet alacak)."""
    return _bordro_olustur(GIRIS_TANIM["cikis"], **kwargs)


# === İşlem bordroları (mevcut evrakı SEÇER): Cari Ciro/İade · Banka Tahsil/Teminat (+İade) ===
# İşlem bordrosunun çeke verdiği SONUÇ durum (geri-al güvenlik kontrolü için).
_ISLEM_SONUC = {
    CekBordrosu.Tur.CARI_CIRO: CekSenet.Durum.CIRO,
    CekBordrosu.Tur.BANKA_TAHSIL: CekSenet.Durum.TAHSILDE,
    CekBordrosu.Tur.BANKA_TEMINAT: CekSenet.Durum.TEMINATTA,
    CekBordrosu.Tur.BANKA_TAHSIL_IADE: CekSenet.Durum.PORTFOYDE,
    CekBordrosu.Tur.BANKA_TEMINAT_IADE: CekSenet.Durum.PORTFOYDE,
    CekBordrosu.Tur.CARI_IADE: CekSenet.Durum.IADE,
    CekBordrosu.Tur.TAHSIL: CekSenet.Durum.TAHSIL,
    CekBordrosu.Tur.ODEME: CekSenet.Durum.ODENDI,
    CekBordrosu.Tur.KARSILIKSIZ: CekSenet.Durum.KARSILIKSIZ,
}
# Banka reclass işlemleri — iki yönlü: ileri (tahsil/teminat: portföy→ara durum, banka hesabı
# zorunlu, belge bilgisi) ve İADE (ara durum→portföy, hedef seçilmez, mevcut duruma bakılır).
# kaynak = ALACAK (evrak çıkar), hedef = BORÇ (evrak girer).
_RECLASS = {
    "tahsil": {"tur": CekBordrosu.Tur.BANKA_TAHSIL, "kaynak_durum": CekSenet.Durum.PORTFOYDE,
               "hedef_durum": CekSenet.Durum.TAHSILDE, "kaynak_oneki": "portfoy",
               "hedef_oneki": "tahsilde", "banka_gerekli": True, "ack": "BANKA TAHSİL"},
    "teminat": {"tur": CekBordrosu.Tur.BANKA_TEMINAT, "kaynak_durum": CekSenet.Durum.PORTFOYDE,
                "hedef_durum": CekSenet.Durum.TEMINATTA, "kaynak_oneki": "portfoy",
                "hedef_oneki": "teminatta", "banka_gerekli": True, "ack": "BANKA TEMİNAT"},
    "tahsil_iade": {"tur": CekBordrosu.Tur.BANKA_TAHSIL_IADE, "kaynak_durum": CekSenet.Durum.TAHSILDE,
                    "hedef_durum": CekSenet.Durum.PORTFOYDE, "kaynak_oneki": "tahsilde",
                    "hedef_oneki": "portfoy", "banka_gerekli": False, "ack": "BANKA TAHSİL İADE"},
    "teminat_iade": {"tur": CekBordrosu.Tur.BANKA_TEMINAT_IADE, "kaynak_durum": CekSenet.Durum.TEMINATTA,
                     "hedef_durum": CekSenet.Durum.PORTFOYDE, "kaynak_oneki": "teminatta",
                     "hedef_oneki": "portfoy", "banka_gerekli": False, "ack": "BANKA TEMİNAT İADE"},
}


def _banka_hesap_coz(banka_hesap_id):
    bh = (BankaHesap.objects.filter(pk=banka_hesap_id, silindi=False)
          .select_related("banka").first() if banka_hesap_id else None)
    if bh is None:
        raise CekHatasi("Banka hesabı seçilmedi.")
    return bh


def _durum_kumesi(durum):
    """durum tek değer ya da iterable olabilir → izin verilen durum kümesi (str set)."""
    return {durum} if isinstance(durum, str) else set(durum)


def portfoydeki_cekler(yon=CekSenet.Yon.ALINAN, durum=CekSenet.Durum.PORTFOYDE):
    """Seçim ekranı için uygun (alınan) çek/senetler — vade sırasıyla. durum tek değer
    ya da iterable (ör. Tahsil ekranında TAHSILDE+PORTFOYDE)."""
    return (CekSenet.objects.filter(silindi=False, yon=yon,
                                    durum__in=_durum_kumesi(durum))
            .select_related("cari").order_by("para_birimi", "vade", "id"))


def _secim_coz(cek_ids, *, yon=CekSenet.Yon.ALINAN, durum=CekSenet.Durum.PORTFOYDE):
    """Form'dan gelen pk listesini doğrula → CekSenet listesi (hepsi uygun + tek PB).
    durum tek değer ya da iterable (birden çok durum kabul eden işlemler için)."""
    try:
        ids = [int(x) for x in cek_ids if str(x).strip()]
    except (TypeError, ValueError):
        raise CekHatasi("Geçersiz çek/senet seçimi.")
    if not ids:
        raise CekHatasi("En az bir çek/senet seçilmeli.")
    cekler = list(CekSenet.objects.filter(pk__in=set(ids), silindi=False))
    if len(cekler) != len(set(ids)):
        raise CekHatasi("Seçilen çek/senetlerden bazıları bulunamadı.")
    izin = _durum_kumesi(durum)
    ad = dict(CekSenet.Durum.choices)
    durum_ad = " / ".join(ad.get(d, d) for d in izin)
    yon_ad = dict(CekSenet.Yon.choices).get(yon, yon).lower()
    for c in cekler:
        if c.yon != yon or c.durum not in izin:
            raise CekHatasi(f"{c.belge_no or c.pk} bu işlem için uygun değil "
                            f"(yalnız \"{durum_ad}\" durumundaki {yon_ad} evrak seçilebilir).")
    if len({c.para_birimi for c in cekler}) > 1:
        raise CekHatasi("Seçilen çek/senetler aynı para biriminde olmalı (tek fiş).")
    return cekler


# === Cari terminal bordroları: Cari İade / Karşılıksız ===
# Her evrakın KENDİ carisi BORÇ (borç geri doğar) / kaynak çek-senet hesabı (durum × tip)
# ALACAK; nakit hareketi yok. Durum → tan["sonuc"] (terminal). onek: kabul edilen kaynak
# durumlar → config ön eki (Cari İade yalnız Portföy; Karşılıksız Portföy/Tahsilde/Teminatta).
_CARI_TERMINAL = {
    "iade": {"tur": CekBordrosu.Tur.CARI_IADE, "sonuc": CekSenet.Durum.IADE,
             "onek": {CekSenet.Durum.PORTFOYDE: "portfoy"}, "ack": "ÇEK/SENET CARİ İADE"},
    "karsiliksiz": {"tur": CekBordrosu.Tur.KARSILIKSIZ, "sonuc": CekSenet.Durum.KARSILIKSIZ,
                    "onek": {CekSenet.Durum.PORTFOYDE: "portfoy",
                             CekSenet.Durum.TAHSILDE: "tahsilde",
                             CekSenet.Durum.TEMINATTA: "teminatta"},
                    "ack": "ÇEK/SENET KARŞILIKSIZ"},
}


@transaction.atomic
def _cari_terminal_bordrosu(tan, *, tarih, cek_ids, aciklama="", kullanici=None) -> CekBordrosu:
    """Cari İade / Karşılıksız ortak motoru: seçilen alınan çek/senetlerin borcu KENDİ carisine
    geri yüklenir. Cari(ler) BORÇ / kaynak çek-senet hesabı (durum öneki × tip) ALACAK — nakit
    yok. Durum → tan["sonuc"] (terminal). Farklı cariler olabilir (her biri kendi hesabına);
    aynı para birimi zorunlu (tek fiş). Hedef seçilmez — evrakın kendi carisidir.
    Dövizde çok gruplu bordroda satır-bazlı kuruş yuvarlaması fişi bozmasın diye ALACAK son
    satırı tl_override ile BORÇ TL toplamına denklenir (kambiyo farkı v0.1 kapsam dışı)."""
    cekler = _secim_coz(cek_ids, durum=tuple(tan["onek"]))
    pb = cekler[0].para_birimi
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    # BORÇ: her cari kendi toplamına (TL satır bazında yuvarlanır → anchor).
    cari_toplam, cari_nesne = {}, {}
    for c in cekler:
        cari_toplam[c.cari_id] = cari_toplam.get(c.cari_id, Decimal("0")) + c.tutar
        cari_nesne[c.cari_id] = c.cari
    borc_satir, borc_tl = [], Decimal("0")
    for cari_id, tutar in cari_toplam.items():
        _, hesap = _cari_coz(cari_id)
        borc_satir.append((hesap.hesap_kodu, tutar))
        borc_tl += yuvarla(tutar * kur, 2)
    # ALACAK: (durum öneki, tip) grubu → config hesabı.
    grup = {}
    for c in cekler:
        anahtar = (tan["onek"][c.durum], c.tip)
        grup[anahtar] = grup.get(anahtar, Decimal("0")) + c.tutar
    alacak_satir = [(_ayar_hesap(ayar, oneki, tip).hesap_kodu, top)
                    for (oneki, tip), top in grup.items()]
    tek_cari = next(iter(cari_nesne.values())) if len(cari_nesne) == 1 else None
    bordro = CekBordrosu.objects.create(
        tur=tan["tur"], tarih=tarih, cari=tek_cari,
        aciklama=(aciklama.strip() or buyuk_harf_tr(
            f"{tan['ack']} - {tek_cari.unvan}" if tek_cari else tan["ack"])),
        created_by=kullanici, updated_by=kullanici)
    for c in cekler:
        CekBordroSatir.objects.create(bordro=bordro, cek_senet=c, onceki_durum=c.durum,
                                      created_by=kullanici, updated_by=kullanici)
        c.durum = tan["sonuc"]
        c.updated_by = kullanici
        c.save(update_fields=["durum", "updated_by", "updated_at"])
    girdiler = [SatirGirdi(hesap_kodu=kod, taraf="B", islem_tutari=tut, islem_pb=pb, islem_kuru=kur)
                for kod, tut in borc_satir]
    alacak_tl = Decimal("0")
    for i, (kod, top) in enumerate(alacak_satir):
        if i == len(alacak_satir) - 1:      # son satır: kalan farkı denkle (döviz yuvarlaması)
            girdiler.append(SatirGirdi(hesap_kodu=kod, taraf="A", islem_tutari=top,
                                       islem_pb=pb, islem_kuru=kur, tl_override=borc_tl - alacak_tl))
        else:
            alacak_tl += yuvarla(top * kur, 2)
            girdiler.append(SatirGirdi(hesap_kodu=kod, taraf="A", islem_tutari=top,
                                       islem_pb=pb, islem_kuru=kur))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


def cari_iade_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Cari İade: portföydeki alınan çek/senetler kendi carisine (evrakı veren) iade edilir
    (cari borç / Portföydeki çek-senet alacak). Durum PORTFOYDE → IADE (terminal)."""
    return _cari_terminal_bordrosu(_CARI_TERMINAL["iade"], **kwargs)


def karsiliksiz_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Karşılıksız: Portföyde / Bankada Tahsilde / Teminattaki alınan çek/senetler karşılıksız
    çıkar; borç KENDİ carisine geri yüklenir (cari borç / kaynak çek-senet alacak). Durum →
    KARSILIKSIZ (terminal)."""
    return _cari_terminal_bordrosu(_CARI_TERMINAL["karsiliksiz"], **kwargs)


@transaction.atomic
def cari_ciro_bordrosu_olustur(*, hedef_id, tarih, cek_ids, aciklama="", kullanici=None) -> CekBordrosu:
    """Portföydeki alınan çek/senetleri bir cariye CİRO: ciro carisi BORÇ / Portföydeki
    çek-senet ALACAK. Seçilen evrak PORTFÖYDE+ALINAN+aynı PB; durumları CIRO'ya geçer.
    hedef_id = ciro edilen cari pk."""
    cari, cari_hesap = _cari_coz(hedef_id)
    cekler = _secim_coz(cek_ids)
    pb = cekler[0].para_birimi
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    cek_top = sum((c.tutar for c in cekler if c.tip == CekSenet.Tip.CEK), Decimal("0"))
    senet_top = sum((c.tutar for c in cekler if c.tip == CekSenet.Tip.SENET), Decimal("0"))
    alacak_satir = []
    if cek_top > 0:
        alacak_satir.append((_ayar_hesap(ayar, "portfoy", CekSenet.Tip.CEK).hesap_kodu, cek_top))
    if senet_top > 0:
        alacak_satir.append((_ayar_hesap(ayar, "portfoy", CekSenet.Tip.SENET).hesap_kodu, senet_top))
    toplam = cek_top + senet_top
    bordro = CekBordrosu.objects.create(
        tur=CekBordrosu.Tur.CARI_CIRO, tarih=tarih, cari=cari,
        aciklama=(aciklama.strip() or buyuk_harf_tr(f"ÇEK/SENET CİRO - {cari.unvan}")),
        created_by=kullanici, updated_by=kullanici)
    for c in cekler:
        CekBordroSatir.objects.create(bordro=bordro, cek_senet=c, onceki_durum=c.durum,
                                      created_by=kullanici, updated_by=kullanici)
        c.durum = CekSenet.Durum.CIRO
        c.updated_by = kullanici
        c.save(update_fields=["durum", "updated_by", "updated_at"])
    # Alacak (per tip) + cari DENGE satırı (borç): dövizde yuvarlama farkını cari tl_override kapatır.
    alacak_girdiler, alacak_tl = [], Decimal("0")
    for kod, tut in alacak_satir:
        alacak_tl += yuvarla(tut * kur, 2)
        alacak_girdiler.append(SatirGirdi(hesap_kodu=kod, taraf="A",
                                          islem_tutari=tut, islem_pb=pb, islem_kuru=kur))
    girdiler = [SatirGirdi(hesap_kodu=cari_hesap.hesap_kodu, taraf="B",
                           islem_tutari=toplam, islem_pb=pb, islem_kuru=kur, tl_override=alacak_tl)]
    girdiler += alacak_girdiler
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


@transaction.atomic
def _reclass_bordrosu(tan, *, tarih, cek_ids, hedef_id=None, aciklama="", kullanici=None) -> CekBordrosu:
    """Banka Tahsil/Teminat (ileri) veya İade (geri): portföy ↔ ara durum RECLASS.
    Kaynak hesap ALACAK (evrak çıkar) / hedef hesap BORÇ (evrak girer), her tip için ayrı
    satır. Banka hesabı yalnız İLERİ yönde zorunlu (belge bilgisi; fişte nakit satırı yok —
    hesaplar hep config'den). hedef_id = banka hesabı pk (yalnız ileri yönde kullanılır)."""
    cekler = _secim_coz(cek_ids, durum=tan["kaynak_durum"])
    pb = cekler[0].para_birimi
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    cek_top = sum((c.tutar for c in cekler if c.tip == CekSenet.Tip.CEK), Decimal("0"))
    senet_top = sum((c.tutar for c in cekler if c.tip == CekSenet.Tip.SENET), Decimal("0"))
    girdiler = []
    for tip, top in ((CekSenet.Tip.CEK, cek_top), (CekSenet.Tip.SENET, senet_top)):
        if top > 0:
            hedef_h = _ayar_hesap(ayar, tan["hedef_oneki"], tip)
            kaynak_h = _ayar_hesap(ayar, tan["kaynak_oneki"], tip)
            girdiler.append(SatirGirdi(hesap_kodu=hedef_h.hesap_kodu, taraf="B",
                                       islem_tutari=top, islem_pb=pb, islem_kuru=kur))
            girdiler.append(SatirGirdi(hesap_kodu=kaynak_h.hesap_kodu, taraf="A",
                                       islem_tutari=top, islem_pb=pb, islem_kuru=kur))
    bh = _banka_hesap_coz(hedef_id) if tan["banka_gerekli"] else None
    ack_ek = f" - {bh.banka.ad} {bh.ad}" if bh else ""
    bordro = CekBordrosu.objects.create(
        tur=tan["tur"], tarih=tarih, banka_hesap=bh,
        aciklama=(aciklama.strip() or buyuk_harf_tr(f"{tan['ack']}{ack_ek}")),
        created_by=kullanici, updated_by=kullanici)
    for c in cekler:
        CekBordroSatir.objects.create(bordro=bordro, cek_senet=c, onceki_durum=c.durum,
                                      created_by=kullanici, updated_by=kullanici)
        c.durum = tan["hedef_durum"]
        c.updated_by = kullanici
        c.save(update_fields=["durum", "updated_by", "updated_at"])
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


def banka_tahsil_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Portföydeki çek/senetleri bankaya TAHSİLE ver (→ Bankada Tahsilde). hedef_id = banka hesabı."""
    return _reclass_bordrosu(_RECLASS["tahsil"], **kwargs)


def banka_teminat_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Portföydeki çek/senetleri bankaya TEMİNAT ver (→ Bankada Teminatta). hedef_id = banka hesabı."""
    return _reclass_bordrosu(_RECLASS["teminat"], **kwargs)


def banka_tahsil_iade_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Bankada Tahsildeki çek/senetler PORTFÖYE iade edilir (banka tahsil edemedi/vazgeçildi)."""
    return _reclass_bordrosu(_RECLASS["tahsil_iade"], **kwargs)


def banka_teminat_iade_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Bankada Teminattaki çek/senetler PORTFÖYE iade edilir (teminat çözüldü)."""
    return _reclass_bordrosu(_RECLASS["teminat_iade"], **kwargs)


# === Nakit gerçekleşme: Tahsil (para GİRER) / Firma Çek Ödeme (para ÇIKAR) ===
# nakit_taraf = nakit hesabının fiş tarafı; çek-senet satırları TERS taraf.
# onek: evrakın kaynak durumuna göre karşı çek/senet hesabının config ön eki
# (anahtar kümesi aynı zamanda işlemin kabul ettiği kaynak durumlar).
_NAKIT = {
    "tahsil": {"tur": CekBordrosu.Tur.TAHSIL, "yon": CekSenet.Yon.ALINAN,
               "sonuc": CekSenet.Durum.TAHSIL, "nakit_taraf": "B",
               "onek": {CekSenet.Durum.PORTFOYDE: "portfoy",
                        CekSenet.Durum.TAHSILDE: "tahsilde"},
               "ack": "ÇEK/SENET TAHSİL"},
    "odeme": {"tur": CekBordrosu.Tur.ODEME, "yon": CekSenet.Yon.VERILEN,
              "sonuc": CekSenet.Durum.ODENDI, "nakit_taraf": "A",
              "onek": {CekSenet.Durum.VERILDI: "verilen"},
              "ack": "FİRMA ÇEK/SENET ÖDEME"},
}


def _nakit_hedef_coz(banka_hesap_id, kasa_id):
    """Nakit ayağı hedefi: Banka hesabı VEYA Kasa (yalnız biri). Döner:
    (hesap_kodu, ad, banka_obj|None, kasa_obj|None)."""
    if bool(banka_hesap_id) == bool(kasa_id):
        raise CekHatasi("Nakit hesabı olarak Banka hesabı VEYA Kasa (yalnız biri) seçin.")
    if banka_hesap_id:
        bh = _banka_hesap_coz(banka_hesap_id)
        if bh.muhasebe_id is None:
            raise CekHatasi("Banka hesabının muhasebe hesabı tanımlı değil.")
        return bh.muhasebe.hesap_kodu, f"{bh.banka.ad} {bh.ad}", bh, None
    ks = Kasa.objects.filter(pk=kasa_id, silindi=False).first()
    if ks is None:
        raise CekHatasi("Kasa seçilmedi.")
    if ks.muhasebe_id is None:
        raise CekHatasi("Kasanın muhasebe hesabı tanımlı değil.")
    return ks.muhasebe.hesap_kodu, ks.ad, None, ks


@transaction.atomic
def _nakit_bordrosu(tan, *, tarih, cek_ids, banka_hesap_id=None, kasa_id=None,
                    aciklama="", kullanici=None) -> CekBordrosu:
    """Nakit gerçekleşme ortak motoru (tan = _NAKIT['tahsil'|'odeme']). Nakit hesabı
    tan["nakit_taraf"] tarafına toplam; karşı çek-senet hesapları ters tarafa, her evrakın
    KENDİ durumuna ve tipine göre ayrı satır. Durum → tan["sonuc"] (terminal). Karışık
    kaynak durum tek bordroda olabilir; aynı para birimi zorunlu (tek fiş)."""
    hedef_kod, hedef_ad, banka, kasa = _nakit_hedef_coz(banka_hesap_id, kasa_id)
    cekler = _secim_coz(cek_ids, yon=tan["yon"], durum=tuple(tan["onek"]))
    pb = cekler[0].para_birimi
    # Nakit hesabının PB'si evrak PB'siyle aynı olmalı (kasa/banka motorlarıyla aynı
    # invariant); çapraz kurlu işlem v0.1 kapsamı dışında.
    hedef_pb = banka.para_birimi if banka else kasa.para_birimi
    if hedef_pb != pb:
        raise CekHatasi(f"{hedef_ad} hesabının para birimi ({hedef_pb}) evrak para "
                        f"birimiyle ({pb}) aynı olmalı; çapraz kurlu işlem desteklenmiyor.")
    kur = _kur_coz(pb, tarih)
    ayar = CekHesapAyari.get()
    # Karşı satırlar: (durum öneki, tip) grubuna göre topla → config hesabı.
    grup = {}
    for c in cekler:
        anahtar = (tan["onek"][c.durum], c.tip)
        grup[anahtar] = grup.get(anahtar, Decimal("0")) + c.tutar
    toplam = sum(grup.values(), Decimal("0"))
    nakit_taraf = tan["nakit_taraf"]
    karsi_taraf = "A" if nakit_taraf == "B" else "B"
    girdiler, karsi_tl = [], Decimal("0")
    for (oneki, tip), top in grup.items():
        karsi_h = _ayar_hesap(ayar, oneki, tip)
        karsi_tl += yuvarla(top * kur, 2)
        girdiler.append(SatirGirdi(hesap_kodu=karsi_h.hesap_kodu, taraf=karsi_taraf,
                                   islem_tutari=top, islem_pb=pb, islem_kuru=kur))
    # Nakit DENGE satırı: TL'si karşı satırların yuvarlanmış TL toplamı (tl_override) —
    # dövizde çok gruplu bordroda kuruş yuvarlama farkı fişi dengesiz bırakmasın.
    girdiler.insert(0, SatirGirdi(hesap_kodu=hedef_kod, taraf=nakit_taraf, islem_tutari=toplam,
                                  islem_pb=pb, islem_kuru=kur, tl_override=karsi_tl))
    bordro = CekBordrosu.objects.create(
        tur=tan["tur"], tarih=tarih, banka_hesap=banka, kasa=kasa,
        aciklama=(aciklama.strip() or buyuk_harf_tr(f"{tan['ack']} - {hedef_ad}")),
        created_by=kullanici, updated_by=kullanici)
    for c in cekler:
        CekBordroSatir.objects.create(bordro=bordro, cek_senet=c, onceki_durum=c.durum,
                                      created_by=kullanici, updated_by=kullanici)
        c.durum = tan["sonuc"]
        c.updated_by = kullanici
        c.save(update_fields=["durum", "updated_by", "updated_at"])
    try:
        fis = fis_olustur(tarih=tarih, satirlar=girdiler, aciklama=bordro.aciklama,
                          kur_usd=None, kaynak=YevmiyeFisi.Kaynak.CEK_SENET, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise CekHatasi(str(e))
    fis.cek_bordrosu = bordro
    fis.save(update_fields=["cek_bordrosu", "updated_at"])
    return bordro


def tahsil_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Tahsil gerçekleşme: TAHSILDE/PORTFOYDE alınan çek/senetler nakde döner (para GİRİŞİ).
    Nakit hesabı (Banka/Kasa) BORÇ / kaynak çek-senet hesabı ALACAK; durum → TAHSIL."""
    return _nakit_bordrosu(_NAKIT["tahsil"], **kwargs)


def odeme_bordrosu_olustur(**kwargs) -> CekBordrosu:
    """Firma Çek Ödeme: VERİLDİ durumundaki verilen çek/senetler ödenir (para ÇIKIŞI).
    Verilen çek-senet hesabı BORÇ (borç kapanır) / nakit hesabı (Banka/Kasa) ALACAK;
    durum → ODENDI."""
    return _nakit_bordrosu(_NAKIT["odeme"], **kwargs)


@transaction.atomic
def bordro_sil(bordro: CekBordrosu, kullanici=None) -> CekBordrosu:
    """Bordroyu geri al: bağlı fiş(ler) iptal + bordro soft-delete.
    Giriş/çıkış bordrosu: oluşturduğu evrakı soft-delete (evrak işlem görmüşse engellenir).
    İşlem bordrosu: seçtiği evrakın durumunu ÖNCEKİ haline döndürür (sonradan işlem görmüşse engellenir)."""
    if bordro.silindi:
        return bordro
    if bordro.tur in CekBordrosu.GIRIS_TURLERI:
        cekler = bordro.cek_senetler.filter(silindi=False)
        beklenen = _GIRIS_DURUM.get(bordro.tur)
        if beklenen and cekler.exclude(durum=beklenen).exists():
            raise CekHatasi("Bu bordrodaki bazı çek/senetler işlem görmüş; önce o işlemleri geri alın.")
        for fis in bordro.fisler.filter(silindi=False):
            fis_iptal(fis, kullanici=kullanici)
        for cek in cekler:
            _soft_sil(cek, kullanici)
    else:
        satirlar = list(bordro.satirlar.filter(silindi=False).select_related("cek_senet"))
        sonuc = _ISLEM_SONUC.get(bordro.tur)
        if sonuc and any(s.cek_senet.durum != sonuc for s in satirlar):
            raise CekHatasi("Bu bordrodaki bazı çek/senetler sonradan işlem görmüş; "
                            "önce o işlemleri geri alın.")
        for fis in bordro.fisler.filter(silindi=False):
            fis_iptal(fis, kullanici=kullanici)
        for s in satirlar:
            c = s.cek_senet
            c.durum = s.onceki_durum
            c.updated_by = kullanici
            c.save(update_fields=["durum", "updated_by", "updated_at"])
            _soft_sil(s, kullanici)
    return _soft_sil(bordro, kullanici)


def aktif_bordrolar():
    return (CekBordrosu.objects.filter(silindi=False)
            .select_related("cari", "banka_hesap").order_by("-tarih", "-id"))


def aktif_cek_senetler():
    return (CekSenet.objects.filter(silindi=False)
            .select_related("cari", "giris_bordrosu").order_by("vade", "-id"))


def ortalama_vade(kalemler, baz_tarih):
    """Tutara göre AĞIRLIKLI ortalama vade (çek/senet bordrosu standardı).

    kalemler: [(tutar, vade), ...] (vade = date). baz_tarih = referans (genelde bordro
    işlem tarihi). Döner: (ortalama_vade_tarihi, gun). Boş/sıfır toplamda (None, None).
    gun = baz_tarih'ten ortalama vadeye gün sayısı (ROUND_HALF_UP)."""
    kalemler = [(Decimal(t), v) for t, v in kalemler if t and v]
    toplam = sum((t for t, v in kalemler), Decimal("0"))
    if toplam <= 0:
        return None, None
    agirlikli = sum((t * (v - baz_tarih).days for t, v in kalemler), Decimal("0")) / toplam
    gun = int(agirlikli.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return baz_tarih + datetime.timedelta(days=gun), gun
