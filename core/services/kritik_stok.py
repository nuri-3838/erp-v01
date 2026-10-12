"""KRİTİK STOK UYARISI (2026-10-12): kalan = kullanılabilir (mevcut − ayrılan) + gelecek − bekleyen tüketim; ``Stok.kritik_stok`` 0 ise kontrol yok;
varyantlı kartlarda kart toplamı.

* Gelecek: satın alınan malzemede AÇIK satınalma siparişleri (ALIŞ + SİPARİŞ + ONAYLI, faturalanmamış, ONAYLI irsaliyesi olmayan; miktar üretim
  biriminde: ``uretim_miktar`` ya da miktar / çevirici); üretilen parçada (152 mamul ve 151 ara mamul) açık istasyon emirlerinin kalan çıktısı
  (kalan çalıştırma × tanım çıktı miktarı, sürücü çıktılar).
* Bekleyen tüketim: açık istasyon emirlerinin henüz tüketmediği, AYRILMAMIŞ girdi ihtiyacı — ÜS başına: Σ(kalan çalıştırma × operasyon girdi
  miktarı) − ÜS'nin o stok için ayırması (ayrılan zaten "kullanılabilir"den düşüldüğünden bir daha sayılmaz), eksi olamaz.
* Öneri: fason parça → "Fason sipariş"; satın alınan → "Satınalma"; 152 ve üretilen/stoklu 151 → "Stok için ÜS aç" (miktar = max(parti miktarı,
  kritik − kalan), ÜS aç ekranı ön dolu)."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse

from django.db.models import Sum

from core.models import IstasyonEmri, OperasyonCikti, OperasyonGirdi, Stok, TeklifSiparis, TeklifSiparisKalem, UretimEmri
from core.services import stok_ayirma
from core.services import uretim

SIFIR = Decimal("0")


def acik_satinalma_haritasi(stok_idler) -> dict:
    """{stok pk: açık satınalma siparişlerindeki miktar (üretim birimi)}."""
    idler = list(stok_idler)
    if not idler:
        return {}
    # ALIŞ sipariş onayı otomatik TASLAK irsaliye açar; stok girişi irsaliye ONAYLANINCA yazılır → onaylı irsaliyesi olan sipariş artık "gelecek" değil.
    irsaliyeli = TeklifSiparis.objects.filter(silindi=False, belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, kaynak_siparis__isnull=False,
                                              durum=TeklifSiparis.Durum.ONAYLI).values("kaynak_siparis_id")
    qs = (TeklifSiparisKalem.objects.filter(silindi=False, stok_id__in=idler, teklif_siparis__silindi=False,
                                            teklif_siparis__belge_tur=TeklifSiparis.BelgeTur.SIPARIS, teklif_siparis__yon=TeklifSiparis.Yon.ALIS,
                                            teklif_siparis__durum=TeklifSiparis.Durum.ONAYLI, teklif_siparis__fatura__isnull=True)
          .exclude(teklif_siparis_id__in=irsaliyeli).select_related("stok"))
    sonuc = {}
    for k in qs:
        if k.uretim_miktar is not None:
            m = k.uretim_miktar
        else:
            m = k.miktar / k.stok.cevirici if k.stok.cevirici else k.miktar
        sonuc[k.stok_id] = sonuc.get(k.stok_id, SIFIR) + m
    return sonuc


def acik_istasyon_emirleri():
    return (IstasyonEmri.objects.filter(silindi=False, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                        durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
            .select_related("operasyon"))


def gelecek_ve_bekleyen(stok_idler) -> tuple:
    """({stok pk: açık istasyon emirlerinin kalan çıktısı}, {stok pk: bekleyen tüketim}) — tek geçişte, yalnız istenen stoklar."""
    idler = set(stok_idler)
    gelecek, tuketim = {}, {}                                   # tuketim: {(ÜS pk, stok pk): kalan girdi ihtiyacı}
    cikti_onbellek, girdi_onbellek = {}, {}
    for ie in acik_istasyon_emirleri():
        kalan = max(ie.planlanan - ie.tamamlanan, SIFIR)
        if kalan <= 0:
            continue
        if ie.operasyon_id not in cikti_onbellek:
            cikti_onbellek[ie.operasyon_id] = [(c.stok_id, c.miktar) for c in uretim.tanim_ciktilari(ie.operasyon) if c.surucu]
            girdi_onbellek[ie.operasyon_id] = list(OperasyonGirdi.objects.filter(operasyon_id=ie.operasyon_id, silindi=False)
                                                   .values_list("girdi_id", "miktar"))
        for stok_pk, miktar in cikti_onbellek[ie.operasyon_id]:
            if stok_pk in idler:
                gelecek[stok_pk] = gelecek.get(stok_pk, SIFIR) + kalan * miktar
        for stok_pk, miktar in girdi_onbellek[ie.operasyon_id]:
            if stok_pk in idler:
                k = (ie.uretim_emri_id, stok_pk)
                tuketim[k] = tuketim.get(k, SIFIR) + kalan * miktar
    bekleyen = {}
    if tuketim:
        ayrilan = {(r["uretim_emri_id"], r["stok_id"]): r["t"]
                   for r in stok_ayirma.acik_ayirmalar().filter(stok_id__in=idler, uretim_emri_id__in={k[0] for k in tuketim})
                   .values("uretim_emri_id", "stok_id").annotate(t=Sum("miktar"))}
        for (emir_pk, stok_pk), m in tuketim.items():
            b = max(m - ayrilan.get((emir_pk, stok_pk), SIFIR), SIFIR)
            if b > 0:
                bekleyen[stok_pk] = bekleyen.get(stok_pk, SIFIR) + b
    return gelecek, bekleyen


def _ureten_haritasi(stok_idler) -> dict:
    """{stok pk: istasyon fason mı} — aktif tanımın ürettiği stoklar (tek sorgu)."""
    return {r["stok_id"]: bool(r["operasyon__istasyon__fason"])
            for r in OperasyonCikti.objects.filter(silindi=False, surucu=True, operasyon__silindi=False, stok_id__in=list(stok_idler))
            .values("stok_id", "operasyon__istasyon__fason")}


def oneri_coz(stok, eksik, ureten) -> dict:
    """{"tur": FASON|SATINALMA|URETIM|None, "metin", "url", "miktar"}."""
    if stok.pk in ureten and ureten[stok.pk]:
        return {"tur": "FASON", "metin": "Fason sipariş", "url": reverse("core:fason_donusleri"), "miktar": eksik}
    if stok.satinalma_urunu:
        return {"tur": "SATINALMA", "metin": "Satınalma", "url": reverse("core:satinalma_siparisleri"), "miktar": eksik}
    if stok.pk in ureten:
        miktar = max(stok.parti_miktari or SIFIR, eksik)
        return {"tur": "URETIM", "metin": "Stok için ÜS aç", "miktar": miktar,
                "url": f"{reverse('core:uretim_emri_ekle')}?urun={stok.pk}&miktar={miktar.normalize():f}"}
    return {"tur": None, "metin": "—", "url": "", "miktar": eksik}


def hesapla(stoklar, *, kullanilabilir=None) -> dict:
    """{stok pk: satır} — yalnız kritik eşiği olan (kritik_stok > 0), hizmet olmayan kartlar. Satır: kullanilabilir, gelecek, bekleyen, kalan,
    kritik, eksik (= max(0, kritik − kalan)), kritik_mi (kalan < kritik), oneri. ``kullanilabilir`` {pk: Decimal} verilirse yeniden hesaplanmaz."""
    stoklar = [s for s in stoklar if s.kritik_stok and s.kritik_stok > 0 and not s.kategori.hizmet_kategorisi]
    if not stoklar:
        return {}
    idler = [s.pk for s in stoklar]
    if kullanilabilir is None:
        kullanilabilir = stok_ayirma.kullanilabilir_haritasi(idler)
    satinalma = acik_satinalma_haritasi(idler)
    gelecek_ie, bekleyen = gelecek_ve_bekleyen(idler)
    ureten = _ureten_haritasi(idler)
    sonuc = {}
    for s in stoklar:
        kul = kullanilabilir.get(s.pk, SIFIR)
        gelecek = satinalma.get(s.pk, SIFIR) + gelecek_ie.get(s.pk, SIFIR)
        bek = bekleyen.get(s.pk, SIFIR)
        kalan = kul + gelecek - bek
        eksik = max(SIFIR, s.kritik_stok - kalan)
        sonuc[s.pk] = {"stok": s, "kullanilabilir": kul, "gelecek": gelecek, "bekleyen": bek, "kalan": kalan, "kritik": s.kritik_stok,
                       "eksik": eksik, "kritik_mi": kalan < s.kritik_stok, "oneri": oneri_coz(s, eksik, ureten)}
    return sonuc


def kritik_kalemler() -> list:
    """Tüm aktif kartlarda kritik altı satırlar (pano kutusu / liste süzgeci)."""
    stoklar = list(Stok.objects.filter(silindi=False, kritik_stok__gt=0).select_related("kategori").order_by("kod"))
    return [r for r in hesapla(stoklar).values() if r["kritik_mi"]]
