"""DÖNEM SONU KUR DEĞERLEME (isteğe bağlı, kullanıcı tetikler).

Seçilen tarihte açık döviz bakiyelerini (kur farkı motorunun havuzları: döviz banka/kasa, döviz
cari, döviz çek-senet) TCMB DÖVİZ ALIŞ kuruyla değerler; değer farkını TEK fişe yazar:
  fark = döviz bakiyesi × kur − hesabın TL bakiyesi
  fark > 0 → hesap BORÇ, 646 KAMBİYO KÂRLARI ALACAK;  fark < 0 → hesap ALACAK, 656 KAMBİYO ZARARLARI BORÇ
Değerleme satırları ``islem_tutari = 0`` taşır: motorda yalnız TL değerini (ortalama maliyeti)
değiştirir, döviz bakiyesini değil. ``onizle`` hiçbir şey yazmaz (dry-run); ``ters_kayit`` değerleme
fişinin tüm satırlarını ters yöne çevirip (varsayılan ertesi gün) bakiyeyi eski haline getirir.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import Cari, HesapPlani, KurDegerleme, Kur, YevmiyeFisi, YevmiyeSatir
from core.sayi import yuvarla
from core.services import kur_farki as kf
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

SIFIR = Decimal("0.00")


class KurDegerlemeHatasi(ValueError):
    """Kur değerleme kural ihlali (Türkçe mesaj)."""


def _kurlar(tarih):
    k = Kur.objects.filter(tarih=tarih, silindi=False).first()
    return k


def onizle(tarih):
    """Dry-run: {'satirlar': [...], 'kar', 'zarar', 'eksik_kurlar': [pb...]} — DB'ye yazmaz."""
    kayit = _kurlar(tarih)
    satirlar, eksik = [], set()
    kar = zarar = SIFIR
    adlar = dict(HesapPlani.objects.values_list("hesap_kodu", "hesap_adi"))
    for hesap_kodu, pb in kf.tum_havuzlar():
        sat = list(YevmiyeSatir.objects.filter(
            hesap_id=hesap_kodu, islem_pb=pb, silindi=False, fis__silindi=False,
            fis__tarih__lte=tarih))
        q = sum(((1 if s.borc > 0 else -1) * s.islem_tutari for s in sat), Decimal("0"))
        v = sum((s.borc - s.alacak for s in sat), SIFIR)
        if q == 0 and v == 0:
            continue
        kur = kayit.deger(pb, Cari.KurTipi.MB_ALIS) if kayit else None
        if not kur:
            eksik.add(pb)
            continue
        yeni = yuvarla(q * kur, 2)
        fark = yeni - v
        satirlar.append({"hesap_kodu": hesap_kodu, "hesap_adi": adlar.get(hesap_kodu, ""), "pb": pb,
                         "doviz": q, "tl": v, "ort_kur": (v / q if q else None), "kur": kur,
                         "yeni_tl": yeni, "fark": fark})
        if fark > 0:
            kar += fark
        elif fark < 0:
            zarar += -fark
    return {"tarih": tarih, "satirlar": satirlar, "kar": kar, "zarar": zarar,
            "eksik_kurlar": sorted(eksik), "kur_kaydi_var": kayit is not None}


@transaction.atomic
def uygula(tarih, *, kullanici=None) -> KurDegerleme:
    """Değerleme fişini oluşturur (aynı tarihte geri alınmamış değerleme varsa reddeder)."""
    if KurDegerleme.objects.filter(tarih=tarih, ters_fis__isnull=True, silindi=False).exists():
        raise KurDegerlemeHatasi(f"{tarih:%d.%m.%Y} için ters kaydı alınmamış bir değerleme zaten var.")
    o = onizle(tarih)
    if o["eksik_kurlar"]:
        raise KurDegerlemeHatasi(f"{tarih:%d.%m.%Y} için {', '.join(o['eksik_kurlar'])} kuru yok; "
                                 f"Kurlar ekranından çekin.")
    satirlar = []
    for r in o["satirlar"]:
        if r["fark"] == 0:
            continue
        satirlar.append(SatirGirdi(
            hesap_kodu=r["hesap_kodu"], taraf="B" if r["fark"] > 0 else "A", islem_tutari=Decimal("0"),
            islem_pb=r["pb"], islem_kuru=r["kur"], tl_override=abs(r["fark"]),
            aciklama=f"KUR DEĞERLEME {r['doviz']:f} {r['pb']} @ {r['kur']:f}"))
    if not satirlar:
        raise KurDegerlemeHatasi("Değerlenecek fark yok (açık döviz bakiyesi ya da kur farkı bulunmuyor).")
    if o["kar"] > 0:
        satirlar.append(SatirGirdi(hesap_kodu=kf.KAR_HESABI, taraf="A", islem_tutari=o["kar"],
                                   aciklama="KUR DEĞERLEME KÂRI"))
    if o["zarar"] > 0:
        satirlar.append(SatirGirdi(hesap_kodu=kf.ZARAR_HESABI, taraf="B", islem_tutari=o["zarar"],
                                   aciklama="KUR DEĞERLEME ZARARI"))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=f"DÖNEM SONU KUR DEĞERLEME {tarih:%d.%m.%Y}",
                          kaynak=YevmiyeFisi.Kaynak.KUR_DEGERLEME, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KurDegerlemeHatasi(str(e))
    return KurDegerleme.objects.create(
        tarih=tarih, fis=fis, toplam_kar=o["kar"], toplam_zarar=o["zarar"],
        kurlar={f"{r['hesap_kodu']}|{r['pb']}": str(r["kur"]) for r in o["satirlar"]},
        created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def ters_kayit(deg: KurDegerleme, *, tarih=None, kullanici=None) -> KurDegerleme:
    """Değerleme fişinin ters yönlü kaydı (varsayılan değerleme tarihinden 1 gün sonra)."""
    deg = KurDegerleme.objects.select_for_update().get(pk=deg.pk)
    if deg.ters_fis_id:
        raise KurDegerlemeHatasi("Bu değerlemenin ters kaydı zaten alınmış.")
    tarih = tarih or deg.tarih + datetime.timedelta(days=1)
    if tarih <= deg.tarih:
        raise KurDegerlemeHatasi("Ters kayıt tarihi değerleme tarihinden sonra olmalı.")
    satirlar = []
    for s in deg.fis.satirlar.filter(silindi=False, ana_satir__isnull=True).order_by("id"):
        tl = s.borc or s.alacak
        satirlar.append(SatirGirdi(
            hesap_kodu=s.hesap_id, taraf="A" if s.borc else "B", islem_tutari=s.islem_tutari,
            islem_pb=s.islem_pb, islem_kuru=s.islem_kuru, tl_override=tl,
            aciklama=buyuk_harf_tr("ters kayıt — " + (s.aciklama or ""))[:500]))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar,
                          aciklama=f"KUR DEĞERLEME TERS KAYDI ({deg.tarih:%d.%m.%Y} DEĞERLEMESİ)",
                          kaynak=YevmiyeFisi.Kaynak.KUR_DEGERLEME, kullanici=kullanici)
    except YevmiyeHatasi as e:
        raise KurDegerlemeHatasi(str(e))
    deg.ters_fis = fis
    deg.updated_by = kullanici
    deg.save(update_fields=["ters_fis", "updated_by", "updated_at"])
    return deg
