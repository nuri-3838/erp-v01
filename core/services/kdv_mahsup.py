"""KDV DÖNEM MAHSUBU (191 / 391 / 190 / 360.30) — KDV1 beyannamesi verilince AY SONU tarihli TEK otomatik mahsup fişi.

Kendi ekranından yönetilir (liste + yeni + düzenle + sil); manuel fiş girilmez. Sistem, dönem sonuna kadarki (mahsup edilmemiş = defter bakiyesi) tutarları ALT HESAP bazında hesaplar:
  191.xx İNDİRİLECEK KDV (borç bakiyesi)   391.xx HESAPLANAN KDV (alacak bakiyesi)   190 DEVREDEN KDV (önceki dönemden devreden borç bakiyesi)
  ERP devreden = 190 açılış + 191 − 391        Beyan = devreden KDV − ödenecek KDV        Fark = ERP devreden − Beyan
Fiş (kaynak=KDV_MAHSUP):  391.xx BORÇ (bakiyeleri kadar) · 191.xx ALACAK (bakiyeleri kadar) · 190 ALACAK (önceki devreden kapanır) · 190 BORÇ (beyan devreden) ·
  360.30 ALACAK (ödenecek KDV) · fark hesabı BORÇ (ERP fazla) / ALACAK (ERP eksik). Borç = alacak zorunlu. 360.10.xx (KDV2 tevkifat) bu fişe GİRMEZ.
Tarih sınırı yoktur: ilk mahsupta başlangıçtan (ör. 01.01.2026 açılışları) dönem sonuna kadar TÜM 191/391 dahil edilir.
Kurallar: aynı dönem için ikinci mahsup yok; mevcut bir mahsuptan SONRAKİ dönem mahsubu varsa (190 açılışı değişeceği için) düzenle/sil ve ÖNCEKİ dönem ekleme engellenir;
mahsuptan sonra dönem içine 191/391 kaydı girilirse listede "mahsup sonrası değişiklik var" uyarısı çıkar. Düzenle fişi aynı numarayla yeniden yazar; Sil fişle birlikte KALICI siler.
"""
from __future__ import annotations

import calendar
import datetime
import os
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import HesapPlani, KdvMahsup, YevmiyeFisi, YevmiyeSatir
from core.sayi import SayiHatasi, format_tr, parse_tr, yuvarla
from core.services.fis_sil import SilmeHatasi, fis_sil
from core.services.hesap_plani import yaprak_mi
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_guncelle, fis_olustur

SIFIR = Decimal("0.00")
DEVREDEN = "190"
ODENECEK = "360.30"
FARK_HESAPLARI = ("689", "770.11", "659")
VARSAYILAN_FARK = "689"
MAKS_PDF = 10 * 1024 * 1024
AYLAR = ["", "OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM", "ARALIK"]


class KdvMahsupHatasi(ValueError):
    """KDV dönem mahsubu kural ihlali (Türkçe mesaj)."""


def ay_sonu(yil, ay):
    return datetime.date(yil, ay, calendar.monthrange(yil, ay)[1])


def donem_adi(yil, ay):
    return f"{ay:02d}/{yil}"


def _tutar(deger, ad):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise KdvMahsupHatasi(f"{ad} geçerli bir sayı olmalı.")
    d = yuvarla(d, 2)
    if d < 0:
        raise KdvMahsupHatasi(f"{ad} negatif olamaz.")
    return d


def fark_hesap_secenekleri():
    """Fark hesabı olarak seçilebilen (aktif + yaprak) hesaplar: 689, 770.11, 659."""
    out = []
    for kod in FARK_HESAPLARI:
        h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).first()
        if h is not None and yaprak_mi(h):
            out.append(h)
    return out


def _bakiyeler(kok, tarih, haric_fis=None):
    """``kok`` hesabı ve alt hesaplarının ``tarih`` (dâhil) sonuna kadarki borç−alacak bakiyeleri: {hesap_kodu: Decimal} (sıfır olanlar yok)."""
    qs = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, fis__tarih__lte=tarih)
          .filter(Q(hesap_id=kok) | Q(hesap__hesap_kodu__startswith=kok + ".")))
    if haric_fis is not None:
        qs = qs.exclude(fis_id=haric_fis.pk)
    out = {}
    for s in qs.only("hesap", "borc", "alacak"):
        out[s.hesap_id] = out.get(s.hesap_id, SIFIR) + s.borc - s.alacak
    return {k: v for k, v in sorted(out.items()) if v != 0}


def _ad(kod):
    h = HesapPlani.objects.filter(hesap_kodu=kod).first()
    return h.hesap_adi if h else ""


def hesapla(*, yil, ay, beyan_devreden, beyan_odenecek, fark_hesap_kodu=VARSAYILAN_FARK, haric_fis=None):
    """Mahsup önizlemesi/fiş planı: alt hesap bazında 191/391/190 bakiyeleri, ERP devreden, fark ve fiş satırları. Hiçbir şey yazmaz."""
    try:
        yil, ay = int(yil), int(ay)
    except (TypeError, ValueError):
        raise KdvMahsupHatasi("Dönem (yıl/ay) geçerli olmalı.")
    if not (1 <= ay <= 12) or not (2000 <= yil <= 2100):
        raise KdvMahsupHatasi("Dönem (yıl/ay) geçerli olmalı.")
    dev, ode = _tutar(beyan_devreden, "Devreden KDV"), _tutar(beyan_odenecek, "Ödenecek KDV")
    if dev > 0 and ode > 0:
        raise KdvMahsupHatasi("Beyannamede devreden KDV varsa ödenecek KDV 0 olmalı (ikisi birden olamaz).")
    sonu = ay_sonu(yil, ay)
    b191, b391, b190 = _bakiyeler("191", sonu, haric_fis), _bakiyeler("391", sonu, haric_fis), _bakiyeler("190", sonu, haric_fis)
    t191 = sum(b191.values(), SIFIR)
    t391 = -sum(b391.values(), SIFIR)                                   # 391 alacak bakiyesi
    a190 = sum(b190.values(), SIFIR)                                    # önceki dönemden devreden (borç bakiyesi)
    erp = a190 + t191 - t391
    beyan = dev - ode
    fark = erp - beyan
    satirlar = []

    def ekle(kod, taraf, tutar, ack):
        if tutar != 0:
            satirlar.append({"hesap": kod, "ad": _ad(kod), "taraf": taraf, "tutar": abs(tutar), "aciklama": ack})
    for kod, bak in b391.items():                                       # 391: alacak bakiyesi → BORÇ
        ekle(kod, "B" if bak < 0 else "A", -bak if bak < 0 else bak, "HESAPLANAN KDV KAPAMA")
    for kod, bak in b191.items():                                       # 191: borç bakiyesi → ALACAK
        ekle(kod, "A" if bak > 0 else "B", bak, "İNDİRİLECEK KDV KAPAMA")
    for kod, bak in b190.items():                                       # önceki devreden kapanır
        ekle(kod, "A" if bak > 0 else "B", bak, "ÖNCEKİ DÖNEM DEVREDEN KDV KAPAMA")
    if dev > 0:
        ekle(DEVREDEN, "B", dev, "SONRAKİ DÖNEME DEVREDEN KDV (BEYAN)")
    if ode > 0:
        ekle(ODENECEK, "A", ode, "ÖDENECEK KDV (BEYAN)")
    fark_kod = (fark_hesap_kodu or VARSAYILAN_FARK).strip()
    if fark != 0:
        if fark_kod not in FARK_HESAPLARI:
            raise KdvMahsupHatasi(f"Fark hesabı {', '.join(FARK_HESAPLARI)} olmalı.")
        ekle(fark_kod, "B" if fark > 0 else "A", fark, "KDV BEYAN FARKI (ERP − BEYAN)")
    borc = sum((s["tutar"] for s in satirlar if s["taraf"] == "B"), SIFIR)
    alacak = sum((s["tutar"] for s in satirlar if s["taraf"] == "A"), SIFIR)
    uyari = ""
    if fark != 0:
        uyari = (f"ERP'deki 190+191−391 devreden/ödenecek tutarı beyandan {format_tr(abs(fark))} TL "
                 f"{'FAZLA' if fark > 0 else 'EKSİK'} (fark {fark_kod} hesabına {'BORÇ' if fark > 0 else 'ALACAK'} yazılır).")
    return {"yil": yil, "ay": ay, "donem": donem_adi(yil, ay), "donem_sonu": sonu, "b191": b191, "b391": b391, "b190": b190,
            "t191": t191, "t391": t391, "a190": a190, "erp_devreden": erp, "beyan_devreden": dev, "beyan_odenecek": ode, "beyan": beyan,
            "fark": fark, "fark_hesap": fark_kod, "uyari": uyari, "satirlar": satirlar, "borc": borc, "alacak": alacak, "dengeli": borc == alacak,
            "bos": not satirlar,
            "l191": [{"kod": k, "ad": _ad(k), "tutar": v} for k, v in b191.items()],
            "l391": [{"kod": k, "ad": _ad(k), "tutar": -v} for k, v in b391.items()],        # alacak bakiyesi pozitif gösterilir
            "l190": [{"kod": k, "ad": _ad(k), "tutar": v} for k, v in b190.items()]}


def _fis_girdileri(h):
    return [SatirGirdi(hesap_kodu=s["hesap"], taraf=s["taraf"], islem_tutari=s["tutar"], aciklama=s["aciklama"]) for s in h["satirlar"]]


def _kontrol(h):
    if h["bos"]:
        raise KdvMahsupHatasi("Kapatılacak 190/191/391 bakiyesi ve beyan tutarı yok; mahsup fişi oluşmaz.")
    if not h["dengeli"]:
        raise KdvMahsupHatasi(f"Fiş dengeli değil (borç {format_tr(h['borc'])} ≠ alacak {format_tr(h['alacak'])}); kaydedilmedi.")
    for kod in {s["hesap"] for s in h["satirlar"]}:
        hp = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False, aktif=True).first()
        if hp is None or not yaprak_mi(hp):
            raise KdvMahsupHatasi(f"{kod} hesabı hesap planında aktif ve yaprak (fişe kesilebilir) olmalı.")


def _ack(yil, ay, aciklama):
    ek = (aciklama or "").strip().upper()
    return (f"{AYLAR[ay]} {yil} KDV DÖNEM MAHSUBU" + (f" - {ek}" if ek else ""))[:500]


def _pdf_kontrol(dosya):
    if dosya is None:
        return None
    ad = os.path.basename(getattr(dosya, "name", "") or "beyanname.pdf")
    if os.path.splitext(ad)[1].lower() != ".pdf":
        raise KdvMahsupHatasi("Beyanname eki PDF olmalı.")
    if dosya.size > MAKS_PDF:
        raise KdvMahsupHatasi("Dosya çok büyük (en fazla 10 MB).")
    bas = dosya.read(1024)
    dosya.seek(0)
    if b"%PDF-" not in bas:
        raise KdvMahsupHatasi("Geçersiz PDF dosyası.")
    return ad[:255]


def aktifler():
    return KdvMahsup.objects.filter(silindi=False).select_related("fark_hesap").order_by("-yil", "-ay")


def fis_of(m):
    return m.fisler.filter(silindi=False).first()


def sonraki_mahsup(m):
    """Bu mahsuptan SONRAKİ dönemin (aktif) mahsubu — varsa bu mahsup düzenlenemez/silinemez (sonraki dönemin 190 açılışı değişirdi)."""
    return (KdvMahsup.objects.filter(silindi=False).filter(Q(yil__gt=m.yil) | Q(yil=m.yil, ay__gt=m.ay)).order_by("yil", "ay").first())


def _kilit(m):
    s = sonraki_mahsup(m)
    if s is not None:
        raise KdvMahsupHatasi(f"{donem_adi(s.yil, s.ay)} dönemine ait mahsup var; bu mahsubun 190 açılışı sonraki dönemi etkiler. Önce {donem_adi(s.yil, s.ay)} "
                              f"mahsubunu silin (en yeni dönemden geriye doğru).")


def degisiklik_var(m):
    """Mahsuptan SONRA dönem içine (dönem sonu ve öncesi) 191/391 kaydı girildi/değişti mi? (kaydedilen 191/391 toplamlarıyla karşılaştırır.)"""
    fis = fis_of(m)
    sonu = ay_sonu(m.yil, m.ay)
    t191 = sum(_bakiyeler("191", sonu, fis).values(), SIFIR)
    t391 = -sum(_bakiyeler("391", sonu, fis).values(), SIFIR)
    return (t191, t391) != (m.erp_191, m.erp_391)


def _kaydet_alanlar(m, h, fark_hesap, dev, ode, aciklama):
    m.donem_sonu, m.beyan_devreden, m.beyan_odenecek = h["donem_sonu"], dev, ode
    m.fark_hesap, m.aciklama = fark_hesap, (aciklama or "").strip()[:200]
    m.erp_191, m.erp_391, m.erp_190, m.erp_devreden, m.fark = h["t191"], h["t391"], h["a190"], h["erp_devreden"], h["fark"]


@transaction.atomic
def olustur(*, yil, ay, beyan_devreden, beyan_odenecek, fark_hesap_kodu=VARSAYILAN_FARK, aciklama="", dosya=None, kullanici=None) -> KdvMahsup:
    h = hesapla(yil=yil, ay=ay, beyan_devreden=beyan_devreden, beyan_odenecek=beyan_odenecek, fark_hesap_kodu=fark_hesap_kodu)
    yil, ay = h["yil"], h["ay"]
    if KdvMahsup.objects.filter(silindi=False, yil=yil, ay=ay).exists():
        raise KdvMahsupHatasi(f"{donem_adi(yil, ay)} dönemi için mahsup zaten var; düzenleyin ya da silip yeniden oluşturun.")
    son = KdvMahsup.objects.filter(silindi=False).filter(Q(yil__gt=yil) | Q(yil=yil, ay__gt=ay)).order_by("yil", "ay").first()
    if son is not None:
        raise KdvMahsupHatasi(f"Daha sonraki dönem ({donem_adi(son.yil, son.ay)}) için mahsup var; önceki bir dönem eklenemez (190 açılışı değişirdi).")
    ad = _pdf_kontrol(dosya)
    _kontrol(h)
    fark_hesap = HesapPlani.objects.filter(hesap_kodu=(fark_hesap_kodu or VARSAYILAN_FARK)).first()
    if fark_hesap is None or fark_hesap.hesap_kodu not in FARK_HESAPLARI:
        raise KdvMahsupHatasi(f"Fark hesabı {', '.join(FARK_HESAPLARI)} olmalı.")
    m = KdvMahsup(yil=yil, ay=ay, created_by=kullanici, updated_by=kullanici, fark_hesap=fark_hesap)
    _kaydet_alanlar(m, h, fark_hesap, h["beyan_devreden"], h["beyan_odenecek"], aciklama)
    m.save()
    if dosya is not None:
        m.dosya, m.orijinal_ad = dosya, ad
        m.save(update_fields=["dosya", "orijinal_ad", "updated_at"])
    try:
        fis = fis_olustur(tarih=h["donem_sonu"], satirlar=_fis_girdileri(h), aciklama=_ack(yil, ay, aciklama), kaynak=YevmiyeFisi.Kaynak.KDV_MAHSUP,
                          kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KdvMahsupHatasi(str(e))
    fis.kdv_mahsup = m
    fis.save(update_fields=["kdv_mahsup", "updated_at"])
    return m


@transaction.atomic
def guncelle(m, *, beyan_devreden, beyan_odenecek, fark_hesap_kodu=VARSAYILAN_FARK, aciklama="", dosya=None, dosyayi_kaldir=False, kullanici=None):
    """Mahsubu düzenler: dönem sabit; fiş silinmeden AYNI numarayla yeniden yazılır (bakiyeler bu fişin kendi satırları HARİÇ hesaplanır)."""
    if m.silindi:
        raise KdvMahsupHatasi("Silinmiş mahsup düzenlenemez.")
    _kilit(m)
    fis = fis_of(m)
    if fis is None:
        raise KdvMahsupHatasi("Mahsubun fişi bulunamadı.")
    h = hesapla(yil=m.yil, ay=m.ay, beyan_devreden=beyan_devreden, beyan_odenecek=beyan_odenecek, fark_hesap_kodu=fark_hesap_kodu, haric_fis=fis)
    ad = _pdf_kontrol(dosya)
    _kontrol(h)
    fark_hesap = HesapPlani.objects.filter(hesap_kodu=(fark_hesap_kodu or VARSAYILAN_FARK)).first()
    if fark_hesap is None or fark_hesap.hesap_kodu not in FARK_HESAPLARI:
        raise KdvMahsupHatasi(f"Fark hesabı {', '.join(FARK_HESAPLARI)} olmalı.")
    try:
        fis_guncelle(fis, tarih=h["donem_sonu"], satirlar=_fis_girdileri(h), aciklama=_ack(m.yil, m.ay, aciklama), kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KdvMahsupHatasi(str(e))
    _kaydet_alanlar(m, h, fark_hesap, h["beyan_devreden"], h["beyan_odenecek"], aciklama)
    m.updated_by = kullanici
    alanlar = ["donem_sonu", "beyan_devreden", "beyan_odenecek", "fark_hesap", "aciklama", "erp_191", "erp_391", "erp_190", "erp_devreden", "fark",
               "updated_by", "updated_at"]
    if dosya is not None:
        m.dosya, m.orijinal_ad = dosya, ad
        alanlar += ["dosya", "orijinal_ad"]
    elif dosyayi_kaldir and m.dosya:
        m.dosya, m.orijinal_ad = "", ""
        alanlar += ["dosya", "orijinal_ad"]
    m.save(update_fields=alanlar)
    return m


@transaction.atomic
def sil(m, *, kullanici):
    """Mahsubu fişiyle birlikte KALICI siler (yalnız süper kullanıcı; denetim kaydı yazılır). Sonraki dönem mahsubu varsa engellenir."""
    if m.silindi:
        raise KdvMahsupHatasi("Mahsup zaten silinmiş.")
    _kilit(m)
    fis = fis_of(m)
    ek = {"kdv_mahsup": {"pk": m.pk, "donem": donem_adi(m.yil, m.ay), "beyan_devreden": str(m.beyan_devreden), "beyan_odenecek": str(m.beyan_odenecek),
                         "erp_191": str(m.erp_191), "erp_391": str(m.erp_391), "erp_190": str(m.erp_190), "fark": str(m.fark)}}
    try:
        if fis is not None:
            fis_sil(fis, kullanici=kullanici, izinli_kaynaklar={YevmiyeFisi.Kaynak.KDV_MAHSUP}, ek_veri=ek)
        else:
            from core.services.fis_sil import yetki_kontrol
            yetki_kontrol(kullanici)
    except SilmeHatasi as e:
        raise KdvMahsupHatasi(str(e))
    m.silindi, m.silindi_at, m.updated_by = True, timezone.now(), kullanici
    m.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return m
