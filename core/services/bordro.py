"""AYLIK BORDRO TAHAKKUĞU — mali müşavirden gelen maaş bordrosu (PDF) ERP'de kendi ekranından işlenir; manuel fiş yok.

Bordro = bir dönem (ay/yıl) + tahakkuk tarihi + (ops.) PDF eki + personel satırları. Her satır: personel carisi (335.10.000N; ad soyadla yeni
personel carisi de açılabilir; NET = 0 satırda cari yerine "ad soyad (cari yok)" yazılabilir), gider hesabı (varsayılan 730.06; satırda değiştirilebilir), brüt, SGK işçi, işsizlik işçi, gelir vergisi,
damga vergisi, net, SGK işveren (teşvik sonrası net), işsizlik işveren. Kontrol: brüt − SGK işçi − işsizlik işçi − GV − DV = net.

TEK fiş (kaynak=BORDRO; fiş→bordro bağı), satır başına:
  gider hesabı            BORÇ   brüt + SGK işveren + işsizlik işveren
  335 personel carisi     ALACAK net   (net = 0 / carisiz satırda YAZILMAZ)
 ve toplamlar:
  360.20                  ALACAK gelir vergisi + damga vergisi
  361                     ALACAK SGK işçi + işsizlik işçi + SGK işveren + işsizlik işveren
Sonraki maaş ödemeleri personel carisine (Cari Ödeme) yapılıp tahakkuku kapatır. Düzenle: fiş yeniden yazılır; Sil: fiş KALICI silinir
(yalnız süper kullanıcı, denetim kaydı) ve bordro kaydı kapanır — "iptal" durumu yoktur.
"""
from __future__ import annotations

import datetime
import os
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Cari, CariKategori, HesapPlani, PersonelBordro, PersonelBordroSatir, YevmiyeFisi
from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla
from core.services import cari as cari_servis
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur

VARSAYILAN_GIDER = "730.06"
GV_DV_HESABI = "360.20"
SGK_HESABI = "361"
MAKS_PDF = 10 * 1024 * 1024
AYLAR = ["", "OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]
TUTAR_ALANLARI = ("brut", "sgk_isci", "issizlik_isci", "gelir_vergisi", "damga_vergisi", "net", "sgk_isveren", "issizlik_isveren")


class BordroHatasi(ValueError):
    """Bordro kural ihlali (Türkçe mesaj)."""


def personel_carileri():
    """Bordroda seçilebilen personel carileri (muhasebe hesabı 335.x)."""
    return Cari.objects.filter(silindi=False, muhasebe_kodu__startswith="335.").order_by("unvan")


def _tutar(deger, ad, sira):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise BordroHatasi(f"Satır {sira}: {ad} geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d < 0:
        raise BordroHatasi(f"Satır {sira}: {ad} negatif olamaz.")
    return d


def _gider_hesabi(kod, sira):
    from core.services.hesap_plani import yaprak_mi
    h = HesapPlani.objects.filter(hesap_kodu=(kod or "").strip(), silindi=False, aktif=True).first()
    if h is None or not yaprak_mi(h):
        raise BordroHatasi(f"Satır {sira}: gider hesabı bulunamadı ya da yaprak (fişe kesilebilir) değil.")
    if not (h.hesap_kodu.startswith("7") or h.hesap_kodu.startswith("63")):
        raise BordroHatasi(f"Satır {sira}: gider hesabı bir gider/maliyet hesabı (7xx, 63x) olmalı.")
    return h


def yeni_personel_cari(ad, kullanici=None) -> Cari:
    """Ad soyadla personel carisi açar (335.10 PERSONELE BORÇLAR; hesap sıradaki 335.10.000N)."""
    unvan = buyuk_harf_tr((ad or "").strip())
    if len(unvan.split()) < 2:
        raise BordroHatasi(f"Yeni personel için ad ve soyad girin: '{ad}'.")
    kategori = CariKategori.objects.filter(kod="10", ust__kod="335", silindi=False).first()
    if kategori is None:
        raise BordroHatasi("Cari kategorisi 335-10 PERSONELE BORÇLAR bulunamadı; personel carisi açılamaz.")
    if personel_carileri().filter(unvan=unvan).exists():
        raise BordroHatasi(f"'{unvan}' adlı personel carisi zaten var; listeden seçin.")
    try:
        return cari_servis.cari_olustur(unvan=unvan, kategori_id=kategori.pk, para_birimi="TRY", kullanici=kullanici)
    except cari_servis.CariHatasi as e:
        raise BordroHatasi(f"Personel carisi açılamadı: {e}")


def _satirlari_coz(satirlar, kullanici):
    """Girdi satırlarını doğrular; ([çözülmüş dict], yeni açılan cari sayısı). Yeni personel carileri burada açılır
    (çağıran transaction içinde olmalı)."""
    girdiler = [s for s in (satirlar or []) if s]
    if not girdiler:
        raise BordroHatasi("En az bir personel satırı gerekli.")
    cozulen, goruldu = [], set()
    for i, s in enumerate(girdiler, 1):
        cari, yeni_ad = s.get("cari"), (s.get("yeni_ad") or "").strip()
        ad_soyad = buyuk_harf_tr((s.get("ad_soyad") or "").strip())
        t = {a: _tutar(s.get(a), a, i) for a in TUTAR_ALANLARI}
        if t["net"] > 0:                                   # net > 0: personel carisi ZORUNLU (seç YA DA yeni aç)
            if ad_soyad:
                raise BordroHatasi(f"Satır {i}: net ödenen sıfırdan büyükse personel carisi gerekir; 'ad soyad (cari yok)' yalnız net 0 satırda kullanılır.")
            if bool(cari) == bool(yeni_ad):
                raise BordroHatasi(f"Satır {i}: personel carisini seçin YA DA yeni personelin ad soyadını yazın (yalnız biri).")
        else:                                              # net = 0: cari yok olabilir; yeni cari AÇILMAZ
            if yeni_ad:
                raise BordroHatasi(f"Satır {i}: net ödenen 0 olan satır için yeni personel carisi açılmaz; ad soyadı 'ad soyad (cari yok)' alanına yazın.")
            if cari is not None and ad_soyad:
                raise BordroHatasi(f"Satır {i}: personel carisi seçildiyse 'ad soyad (cari yok)' yazılmaz.")
            if cari is None and not ad_soyad:
                raise BordroHatasi(f"Satır {i}: personel carisini seçin ya da 'ad soyad (cari yok)' yazın.")
        if cari is not None:
            if cari.silindi or not (cari.muhasebe_kodu or "").startswith("335."):
                raise BordroHatasi(f"Satır {i}: {cari.unvan} bir personel (335) carisi değil.")
        if t["brut"] <= 0:
            raise BordroHatasi(f"Satır {i}: brüt kazanç sıfırdan büyük olmalı.")
        hesapla = t["brut"] - t["sgk_isci"] - t["issizlik_isci"] - t["gelir_vergisi"] - t["damga_vergisi"]
        if hesapla != t["net"]:
            ad = cari.unvan if cari else (yeni_ad or ad_soyad)
            raise BordroHatasi(
                f"Satır {i} ({ad}): brüt − SGK işçi − işsizlik işçi − gelir vergisi − damga vergisi = {format_tr(hesapla)}; "
                f"girilen net {format_tr(t['net'])} — tutmuyor.")
        gider = _gider_hesabi(s.get("gider_hesap_kodu") or VARSAYILAN_GIDER, i)
        anahtar = cari.pk if cari else (yeni_ad or ad_soyad).upper()
        if anahtar in goruldu:
            raise BordroHatasi(f"Satır {i}: aynı personel bordroda ikinci kez var.")
        goruldu.add(anahtar)
        cozulen.append({"cari": cari, "yeni_ad": yeni_ad, "ad_soyad": ad_soyad, "gider": gider, **t})
    for ad, kod in (("gelir vergisi/damga vergisi", GV_DV_HESABI), ("SGK kesintileri", SGK_HESABI)):
        h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).first()
        from core.services.hesap_plani import yaprak_mi
        if h is None or not yaprak_mi(h):
            raise BordroHatasi(f"{kod} ({ad}) hesabı hesap planında yaprak ve aktif olmalı.")
    for c in cozulen:
        if c["cari"] is None and c["yeni_ad"]:
            c["cari"] = yeni_personel_cari(c["yeni_ad"], kullanici)
    return cozulen


def _fis_satirlari(cozulen):
    out = []
    gv_dv = sgk = Decimal("0.00")
    for c in cozulen:
        ad = c["cari"].unvan if c["cari"] else c["ad_soyad"]
        out.append(SatirGirdi(hesap_kodu=c["gider"].hesap_kodu, taraf="B", aciklama=ad,
                              islem_tutari=c["brut"] + c["sgk_isveren"] + c["issizlik_isveren"]))
        if c["net"] > 0:                                   # net 0 (carisiz) satırda personel carisine ALACAK yazılmaz
            out.append(SatirGirdi(hesap_kodu=c["cari"].muhasebe_kodu, taraf="A", aciklama=ad, islem_tutari=c["net"]))
        gv_dv += c["gelir_vergisi"] + c["damga_vergisi"]
        sgk += c["sgk_isci"] + c["issizlik_isci"] + c["sgk_isveren"] + c["issizlik_isveren"]
    if gv_dv > 0:
        out.append(SatirGirdi(hesap_kodu=GV_DV_HESABI, taraf="A", aciklama="GELİR VERGİSİ + DAMGA VERGİSİ", islem_tutari=gv_dv))
    if sgk > 0:
        out.append(SatirGirdi(hesap_kodu=SGK_HESABI, taraf="A", aciklama="SGK + İŞSİZLİK (İŞÇİ + İŞVEREN)", islem_tutari=sgk))
    return out


def _donem(yil, ay):
    try:
        yil, ay = int(yil), int(ay)
    except (TypeError, ValueError):
        raise BordroHatasi("Dönem (yıl/ay) geçerli olmalı.")
    if not (1 <= ay <= 12) or not (2000 <= yil <= 2100):
        raise BordroHatasi("Dönem (yıl/ay) geçerli olmalı.")
    return yil, ay


def _ack(yil, ay, aciklama):
    ek = buyuk_harf_tr((aciklama or "").strip())
    return (f"{AYLAR[ay]} {yil} PERSONEL BORDRO TAHAKKUKU" + (f" - {ek}" if ek else ""))[:500]


def _pdf_kontrol(dosya):
    if dosya is None:
        return None
    ad = os.path.basename(getattr(dosya, "name", "") or "bordro.pdf")
    if os.path.splitext(ad)[1].lower() != ".pdf":
        raise BordroHatasi("Bordro eki PDF olmalı.")
    if dosya.size > MAKS_PDF:
        raise BordroHatasi("Dosya çok büyük (en fazla 10 MB).")
    bas = dosya.read(1024)
    dosya.seek(0)
    if b"%PDF-" not in bas:
        raise BordroHatasi("Geçersiz PDF dosyası.")
    return ad[:255]


def _kaydet_satirlar(bordro, cozulen, kullanici):
    for c in cozulen:
        PersonelBordroSatir.objects.create(
            bordro=bordro, cari=c["cari"], ad_soyad=c["ad_soyad"] if c["cari"] is None else "", gider_hesap=c["gider"],
            created_by=kullanici, updated_by=kullanici,
            **{a: c[a] for a in TUTAR_ALANLARI})


@transaction.atomic
def bordro_olustur(*, yil, ay, tahakkuk_tarihi, satirlar, aciklama="", dosya=None, kullanici=None) -> PersonelBordro:
    yil, ay = _donem(yil, ay)
    if not isinstance(tahakkuk_tarihi, datetime.date):
        raise BordroHatasi("Tahakkuk tarihi gerekli.")
    ad = _pdf_kontrol(dosya)
    cozulen = _satirlari_coz(satirlar, kullanici)
    bordro = PersonelBordro.objects.create(
        yil=yil, ay=ay, tahakkuk_tarihi=tahakkuk_tarihi, aciklama=(aciklama or "").strip()[:200],
        created_by=kullanici, updated_by=kullanici)
    if dosya is not None:
        bordro.dosya = dosya
        bordro.orijinal_ad = ad
        bordro.save(update_fields=["dosya", "orijinal_ad", "updated_at"])
    _kaydet_satirlar(bordro, cozulen, kullanici)
    try:
        fis = fis_olustur(tarih=tahakkuk_tarihi, satirlar=_fis_satirlari(cozulen), aciklama=_ack(yil, ay, aciklama),
                          kaynak=YevmiyeFisi.Kaynak.BORDRO, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise BordroHatasi(str(e))
    fis.personel_bordro = bordro
    fis.save(update_fields=["personel_bordro", "updated_at"])
    return bordro


def bordro_fisi(bordro):
    return bordro.fisler.filter(silindi=False).first()


@transaction.atomic
def bordro_guncelle(bordro, *, yil, ay, tahakkuk_tarihi, satirlar, aciklama="", dosya=None, dosyayi_kaldir=False,
                    kullanici=None) -> PersonelBordro:
    """Bordroyu DÜZENLER: fiş silinmeden aynı numarayla yeniden yazılır (tarih yılı fişin yılıyla aynı kalmalı); eski satırlar
    düzenleme geçmişi olarak pasifleşir. Hatada hiçbir şey değişmez."""
    if bordro.silindi:
        raise BordroHatasi("Silinmiş bordro düzenlenemez.")
    fis = bordro_fisi(bordro)
    if fis is None:
        raise BordroHatasi("Bordronun fişi bulunamadı.")
    yil, ay = _donem(yil, ay)
    if not isinstance(tahakkuk_tarihi, datetime.date):
        raise BordroHatasi("Tahakkuk tarihi gerekli.")
    ad = _pdf_kontrol(dosya)
    cozulen = _satirlari_coz(satirlar, kullanici)
    try:
        fis_guncelle(fis, tarih=tahakkuk_tarihi, satirlar=_fis_satirlari(cozulen), aciklama=_ack(yil, ay, aciklama),
                     kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise BordroHatasi(str(e))
    bordro.satirlar.filter(silindi=False).update(silindi=True, silindi_at=timezone.now())
    _kaydet_satirlar(bordro, cozulen, kullanici)
    bordro.yil, bordro.ay, bordro.tahakkuk_tarihi = yil, ay, tahakkuk_tarihi
    bordro.aciklama = (aciklama or "").strip()[:200]
    bordro.updated_by = kullanici
    alanlar = ["yil", "ay", "tahakkuk_tarihi", "aciklama", "updated_by", "updated_at"]
    if dosya is not None:
        bordro.dosya, bordro.orijinal_ad = dosya, ad
        alanlar += ["dosya", "orijinal_ad"]
    elif dosyayi_kaldir and bordro.dosya:
        bordro.dosya, bordro.orijinal_ad = "", ""
        alanlar += ["dosya", "orijinal_ad"]
    bordro.save(update_fields=alanlar)
    return bordro


def satirlar(bordro):
    return list(bordro.satirlar.filter(silindi=False).select_related("cari", "gider_hesap").order_by("id"))


def ozet(bordro):
    """Toplamlar: kişi, brüt, net, GV+DV, SGK(işçi+işveren), işveren maliyeti, toplam maliyet."""
    t = {a: Decimal("0.00") for a in TUTAR_ALANLARI}
    rows = satirlar(bordro)
    for s in rows:
        for a in TUTAR_ALANLARI:
            t[a] += getattr(s, a)
    t["kisi"] = len(rows)
    t["gv_dv"] = t["gelir_vergisi"] + t["damga_vergisi"]
    t["sgk_toplam"] = t["sgk_isci"] + t["issizlik_isci"] + t["sgk_isveren"] + t["issizlik_isveren"]
    t["maliyet"] = t["brut"] + t["sgk_isveren"] + t["issizlik_isveren"]
    return t


@transaction.atomic
def bordro_sil(bordro, *, kullanici):
    """Bordroyu siler: fiş KALICI silinir (yalnız süper kullanıcı; denetim kaydı bordro özetiyle yazılır), bordro ve satırları kapanır."""
    if bordro.silindi:
        raise BordroHatasi("Bordro zaten silinmiş.")
    fis = bordro_fisi(bordro)
    ek = {"bordro": {"pk": bordro.pk, "donem": f"{bordro.ay:02d}.{bordro.yil}", "tahakkuk_tarihi": str(bordro.tahakkuk_tarihi),
                     "satirlar": [{"cari": s.personel_adi, "gider": s.gider_hesap_id, **{a: str(getattr(s, a)) for a in TUTAR_ALANLARI}}
                                  for s in satirlar(bordro)]}}
    try:
        if fis is not None:
            fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.BORDRO}, ek_veri=ek)
        else:
            from core.services.fis_sil import yetki_kontrol
            yetki_kontrol(kullanici)
    except SilmeHatasi as e:
        raise BordroHatasi(str(e))
    simdi = timezone.now()
    bordro.satirlar.filter(silindi=False).update(silindi=True, silindi_at=simdi)
    bordro.silindi, bordro.silindi_at, bordro.updated_by = True, simdi, kullanici
    bordro.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return bordro
