"""FATURALAR › Fatura DETAY ekranı verisi (2026-10-15, 3/3) — SALT-OKUNUR; muhasebe mantığına (fiş, stok, cari) dokunmaz, hiçbir şey yazmaz.

Bölümler: başlık kartı (tip rozeti, ödeme durumu), kalemler + toplam kutusu (KDV kırılımı, döviz faturada TL karşılığı), ödemeler (``fatura_odeme`` — listedeki FIFO ile aynı fonksiyon),
muhasebe (fiş satırları, kur farkı satırları, satış maliyet fişi, bağlı kayıtlar).

Tanımlar (liste ekranıyla aynı):
- Matrah = satır tutarları toplamı; KDV = satır KDV toplamı; Genel toplam = Matrah + KDV (KDV dahil brüt); Ödenecek = Genel toplam − tevkifat − GV stopajı (carinin hesabına yazılan tutar).
- "Yalnız KDV" tipinde matrah fişe yazılmaz: Matrah "—", Genel toplam yalnız KDV, Ödenecek = KDV − tevkifat.
- Satır toplamı = satır matrahı + satır KDV'si (tevkifat ayrı sütunda gösterilir, satır toplamından düşülmez; düşüm toplam kutusunda).
- TL karşılığı = tutar × fatura kuru (kuruş yuvarlaması); fiş TL'si (kur farkı/yuvarlama) ile birkaç kuruş fark edebilir → muhasebe kartında fiş değeri ayrıca görünür."""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from django.db.models import Prefetch

from core.models import Fatura, StokHareket, TeklifSiparis, YevmiyeFisi
from core.sayi import yuvarla
from core.services import fatura_liste, fatura_odeme

SIFIR = Decimal("0")


def _kur(f):
    """TL karşılığı için kur: ONAYLI → fatura kuru; TASLAK → elle girilen kur (yoksa TRY için 1, döviz için belirsiz=None)."""
    if f.durum == Fatura.Durum.ONAYLI:
        return f.kur
    if f.taslak_kur:
        return f.taslak_kur
    return Decimal("1") if f.para_birimi == "TRY" else None


def _tl(tutar, kur):
    return None if (tutar is None or kur is None) else yuvarla(tutar * kur, 2)


def kalem_satirlari(fatura, satirlar, yalniz_kdv) -> list:
    """Kalem tablosu satırları (fatura para biriminde). ``satirlar``: görünümde zaten çekilmiş FaturaSatir listesi."""
    cikti = []
    for s in satirlar:
        matrah, kdv = s.tutar, s.kdv_tutari
        birim = "—"
        if s.stok_id and s.stok.fatura_birimi_id:
            birim = s.stok.fatura_birimi.kisa_ad
        cikti.append({
            "satir": s, "tur": s.satir_tipi_ad, "birim": birim,
            "kdv_oran": (s.kdv.oran if s.kdv_id else None), "kdv": kdv,
            "tevkifat_kod": (s.tevkifat.kod if s.tevkifat_id else None), "tevkifat": s.tevkifat_tutari,
            "matrah": None if yalniz_kdv else matrah,
            "toplam": kdv if yalniz_kdv else matrah + kdv})
    return cikti


def toplam_kutusu(fatura, kalemler, kur) -> dict:
    """Sağdaki toplam kutusu: matrah, oranlara göre KDV kırılımı, tevkifat, stopaj, genel toplam, ödenecek — her biri için TL karşılığı (döviz faturada)."""
    yalniz_kdv = bool(fatura.tip_id and fatura.tip.yalniz_kdv)
    matrah = sum((k["satir"].tutar for k in kalemler), SIFIR)
    kirilim = OrderedDict()
    for k in sorted(kalemler, key=lambda x: (x["kdv_oran"] is None, x["kdv_oran"] or SIFIR)):
        anahtar = k["kdv_oran"] if k["kdv_oran"] is not None else SIFIR
        kirilim[anahtar] = kirilim.get(anahtar, SIFIR) + k["kdv"]
    kdv_toplam = sum(kirilim.values(), SIFIR)
    tevkifat = sum((k["tevkifat"] for k in kalemler), SIFIR)
    stopaj = fatura.gv_stopaj_tutari
    genel = kdv_toplam if yalniz_kdv else matrah + kdv_toplam
    odenecek = fatura.odenecek
    doviz = fatura.para_birimi != "TRY"
    satirlar = []
    if not yalniz_kdv:
        satirlar.append({"etiket": "Matrah", "tutar": matrah})
    else:
        satirlar.append({"etiket": "Matrah", "tutar": None})
    for oran, tutar in kirilim.items():
        satirlar.append({"etiket": f"KDV %{oran.normalize():f}", "tutar": tutar, "alt": True})
    if len(kirilim) > 1:
        satirlar.append({"etiket": "Toplam KDV", "tutar": kdv_toplam})
    if tevkifat:
        satirlar.append({"etiket": "KDV tevkifatı (−)", "tutar": tevkifat, "eksi": True})
    if fatura.gv_stopaj_orani and stopaj:
        satirlar.append({"etiket": f"GV stopajı (−) %{fatura.gv_stopaj_orani.normalize():f}", "tutar": stopaj, "eksi": True})
    satirlar.append({"etiket": "Genel toplam (KDV dahil)" if not yalniz_kdv else "Genel toplam (yalnız KDV)", "tutar": genel, "kalin": True})
    satirlar.append({"etiket": "Ödenecek (cari tutarı)", "tutar": odenecek, "kalin": True, "vurgu": True})
    for r in satirlar:
        r["tl"] = _tl(r["tutar"], kur) if doviz else None
    return {"satirlar": satirlar, "doviz": doviz, "pb": fatura.para_birimi, "kur": kur, "yalniz_kdv": yalniz_kdv, "tl_belirsiz": doviz and kur is None,
            "genel": genel, "odenecek": odenecek, "matrah": matrah, "kdv_toplam": kdv_toplam}


def _fis_satirlari(fis):
    return list(fis.satirlar.filter(silindi=False).select_related("hesap", "yatirim_projesi").order_by("pk"))


def _fis_ozeti(fis, satirlar=None) -> dict:
    satirlar = _fis_satirlari(fis) if satirlar is None else satirlar
    ana = [s for s in satirlar if s.ana_satir_id is None]
    kur_farki = [s for s in satirlar if s.ana_satir_id is not None]
    return {"fis": fis, "satirlar": ana, "kur_farki": kur_farki,
            "borc": sum((s.borc for s in ana), SIFIR), "alacak": sum((s.alacak for s in ana), SIFIR),
            "kur_farki_borc": sum((s.borc for s in kur_farki), SIFIR), "kur_farki_alacak": sum((s.alacak for s in kur_farki), SIFIR)}


def muhasebe(fatura, kalemler, kaynak_irsaliyeler) -> dict | None:
    """Muhasebe kartı verisi: fatura fişi (kur farkı satırları ayrı), maliyet fişi(leri) (satışta), bağlı kayıtlar. Fişsiz (taslak) faturada None."""
    if not fatura.fis_id or fatura.fis.silindi:
        return None
    ana = _fis_ozeti(fatura.fis)
    maliyet = []
    fis_idleri = (StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False, fis__isnull=False).exclude(fis_id=fatura.fis_id)
                  .values_list("fis_id", flat=True).distinct())
    for f in YevmiyeFisi.objects.filter(pk__in=list(fis_idleri), silindi=False).order_by("pk"):
        o = _fis_ozeti(f)
        o["baslik"] = "Satış maliyeti fişi" if f.kaynak == YevmiyeFisi.Kaynak.STOK_SATIS else f.get_kaynak_display()
        maliyet.append(o)
    projeler, kartlar = OrderedDict(), OrderedDict()
    for k in kalemler:
        s = k["satir"]
        if s.yatirim_projesi_id:
            projeler[s.yatirim_projesi_id] = s.yatirim_projesi
        if s.demirbas_id:
            kartlar[s.demirbas_id] = s.demirbas
        for dv in s.duran_varliklar.all():
            kartlar[dv.pk] = dv
    doviz_var = any(s.islem_pb != "TRY" for s in ana["satirlar"])
    return {**ana, "maliyet_fisleri": maliyet, "projeler": list(projeler.values()), "kartlar": list(kartlar.values()), "doviz_var": doviz_var,
            "irsaliyeler": list(kaynak_irsaliyeler)}


def baglam(fatura, satirlar, *, kaynak_irsaliyeler, kaynak_siparisler, bugun=None) -> dict:
    """Detay ekranının ek bağlamı (görünüm bunu mevcut bağlama ekler)."""
    tip_etiket, tip_css = fatura_liste.tip_rozeti(fatura.tip, fatura.yon)
    yalniz_kdv = bool(fatura.tip_id and fatura.tip.yalniz_kdv)
    kur = _kur(fatura)
    kalemler = kalem_satirlari(fatura, satirlar, yalniz_kdv)
    odeme = fatura_odeme.fatura_odemeleri(fatura, bugun)
    siparisler = [x for x in kaynak_siparisler if x.belge_tur != TeklifSiparis.BelgeTur.IRSALIYE]       # irsaliye muhasebe kartındaki "Bağlı kayıtlar"da; teklif/sipariş başlıkta
    kutu = toplam_kutusu(fatura, kalemler, kur)
    muh = muhasebe(fatura, kalemler, kaynak_irsaliyeler)
    if muh is not None and kutu["doviz"] and kur is not None:
        # Döviz faturada fişteki cari satırı TL'si, kur farkı satırları yüzünden "tutar × kur"dan farklı olabilir (havuz ortalama kuru): fark açıkça gösterilir.
        cari_satir = next((x for x in muh["satirlar"] if x.hesap_id == fatura.cari.muhasebe_kodu), None)
        if cari_satir is not None:
            fis_tl = cari_satir.alacak or cari_satir.borc
            hesap_tl = _tl(kutu["odenecek"], kur)
            if fis_tl != hesap_tl:
                kutu["fis_cari_tl"], kutu["fis_cari_fark"] = fis_tl, fis_tl - hesap_tl
    return {"tip_etiket": tip_etiket, "tip_css": tip_css, "yalniz_kdv": yalniz_kdv, "kur": kur, "siparisler": siparisler,
            "kalemler": kalemler, "kutu": kutu, "odeme": odeme, "muh": muh}
