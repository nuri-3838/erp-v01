"""DÖNEM SONU KUR DEĞERLEME (isteğe bağlı, kullanıcı tetikler).

Seçilen tarihte açık döviz bakiyelerini (kur farkı motorunun havuzları: döviz banka/kasa, döviz
cari, döviz çek-senet) TCMB DÖVİZ ALIŞ kuruyla değerler; değer farkını TEK fişe yazar:
  fark = döviz bakiyesi × kur − hesabın TL bakiyesi
  fark > 0 → hesap BORÇ, 646 KAMBİYO KÂRLARI ALACAK;  fark < 0 → hesap ALACAK, 656 KAMBİYO ZARARLARI BORÇ
KUR: seçilen tarihte TCMB kuru yoksa (tatil/hafta sonu) en yakın ÖNCEKİ iş gününün kuru kullanılır (en
çok 7 gün geri; kur tarihi önizlemede gösterilir); 7 günde de yoksa uyarı verilir.
AVANS: 320/321 havuzunda döviz bakiyesi BORÇ (verilen avans) ya da 120/121 havuzunda ALACAK (alınan
avans) ise değerlenmez (önizlemede "Değerlenmeyen" bölümü); cari kartındaki "Kur değerlemesi" ayarı
(Otomatik / Her zaman değerle / Hiç değerleme) bu kuralı ezer.
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


GERI_GUN = 7


def kur_bul(pb, tarih):
    """(kur, kur tarihi): ``tarih``te (yoksa en çok 7 gün önceye kadar en yakın önceki günde) TCMB döviz
    ALIŞ kuru; bulunamazsa (None, None)."""
    for k in (Kur.objects.filter(silindi=False, tarih__lte=tarih,
                                 tarih__gte=tarih - datetime.timedelta(days=GERI_GUN))
              .order_by("-tarih")):
        deger = k.deger(pb, Cari.KurTipi.MB_ALIS)
        if deger:
            return deger, k.tarih
    return None, None


def _usd_kuru(tarih):
    """Fişin USD raporlama kuru: tatil/hafta sonunda önceki iş gününün USD alış kuru."""
    return kur_bul("USD", tarih)[0]


def _avans_mi(hesap_kodu, q):
    """320/321'de borç bakiye (verilen avans) ya da 120/121'de alacak bakiye (alınan avans)."""
    if hesap_kodu.startswith(("320", "321")):
        return q > 0
    if hesap_kodu.startswith(("120", "121")):
        return q < 0
    return False


def onizle(tarih):
    """Dry-run: {'satirlar': [...değerlenecek], 'degerlenmeyen': [...avans/cari ayarı], 'kar', 'zarar',
    'eksik_kurlar', 'kur_tarihleri': {pb: tarih}} — DB'ye yazmaz."""
    satirlar, degerlenmeyen, eksik, kur_tarihleri = [], [], set(), {}
    kar = zarar = SIFIR
    adlar = dict(HesapPlani.objects.values_list("hesap_kodu", "hesap_adi"))
    cariler = {c.muhasebe_kodu: c for c in Cari.objects.filter(silindi=False).exclude(muhasebe_kodu="")}
    kur_onbellek = {}
    for hesap_kodu, pb in kf.tum_havuzlar():
        sat = list(YevmiyeSatir.objects.filter(
            hesap_id=hesap_kodu, islem_pb=pb, silindi=False, fis__silindi=False,
            fis__tarih__lte=tarih))
        q = sum(((1 if s.borc > 0 else -1) * s.islem_tutari for s in sat), Decimal("0"))
        v = sum((s.borc - s.alacak for s in sat), SIFIR)
        if q == 0 and v == 0:
            continue
        cari = cariler.get(hesap_kodu)
        satir = {"hesap_kodu": hesap_kodu, "hesap_adi": adlar.get(hesap_kodu, ""), "pb": pb, "doviz": q, "tl": v,
                 "ort_kur": (v / q if q else None), "cari": cari}
        kural = cari.kur_degerleme if cari else Cari.DegerlemeKurali.OTOMATIK
        if kural == Cari.DegerlemeKurali.HIC:
            satir["neden"] = "Cari ayarı: hiç değerleme"
            degerlenmeyen.append(satir)
            continue
        if kural == Cari.DegerlemeKurali.OTOMATIK and _avans_mi(hesap_kodu, q):
            satir["neden"] = "Verilen avans (döviz bakiyesi borç)" if hesap_kodu.startswith(("320", "321"))                 else "Alınan avans (döviz bakiyesi alacak)"
            degerlenmeyen.append(satir)
            continue
        if pb not in kur_onbellek:
            kur_onbellek[pb] = kur_bul(pb, tarih)
        kur, kur_tarihi = kur_onbellek[pb]
        if not kur:
            eksik.add(pb)
            continue
        kur_tarihleri[pb] = kur_tarihi
        yeni = yuvarla(q * kur, 2)
        fark = yeni - v
        proje = kf.yatirim_hedefi(hesap_kodu)          # kur farkı motoruyla AYNI hedef mantığı
        satir.update(kur=kur, kur_tarihi=kur_tarihi, yeni_tl=yeni, fark=fark, proje=proje,
                     hedef=f"258 / {proje.kod}" if proje else "646/656")
        satirlar.append(satir)
        if fark > 0:
            kar += fark
        elif fark < 0:
            zarar += -fark
    return {"tarih": tarih, "satirlar": satirlar, "degerlenmeyen": degerlenmeyen, "kar": kar, "zarar": zarar,
            "eksik_kurlar": sorted(eksik), "kur_tarihleri": kur_tarihleri,
            "kur_kaydi_var": bool(kur_tarihleri) or not eksik}


@transaction.atomic
def uygula(tarih, *, kullanici=None) -> KurDegerleme:
    """Değerleme fişini oluşturur (aynı tarihte geri alınmamış değerleme varsa reddeder)."""
    if KurDegerleme.objects.filter(tarih=tarih, ters_fis__isnull=True, silindi=False).exists():
        raise KurDegerlemeHatasi(f"{tarih:%d.%m.%Y} için ters kaydı alınmamış bir değerleme zaten var.")
    o = onizle(tarih)
    if o["eksik_kurlar"]:
        raise KurDegerlemeHatasi(f"{tarih:%d.%m.%Y} ve önceki {GERI_GUN} günde {', '.join(o['eksik_kurlar'])} "
                                 f"kuru yok; Kurlar ekranından çekin.")
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
    # Karşı satırlar: yatırım carilerinin farkı 258 + proje (proje bazında kâr/zarar ayrı), kalanı 646/656.
    kar646 = zarar656 = SIFIR
    yatirim = {}
    for r in o["satirlar"]:
        if r["fark"] == 0:
            continue
        if r["proje"] is not None:
            k = yatirim.setdefault(r["proje"].pk, {"kod": r["proje"].kod, "kar": SIFIR, "zarar": SIFIR})
            if r["fark"] > 0:
                k["kar"] += r["fark"]
            else:
                k["zarar"] += -r["fark"]
        elif r["fark"] > 0:
            kar646 += r["fark"]
        else:
            zarar656 += -r["fark"]
    if kar646 > 0:
        satirlar.append(SatirGirdi(hesap_kodu=kf.KAR_HESABI, taraf="A", islem_tutari=kar646,
                                   aciklama="KUR DEĞERLEME KÂRI"))
    if zarar656 > 0:
        satirlar.append(SatirGirdi(hesap_kodu=kf.ZARAR_HESABI, taraf="B", islem_tutari=zarar656,
                                   aciklama="KUR DEĞERLEME ZARARI"))
    for pk, k in sorted(yatirim.items()):
        if k["kar"] > 0:     # kâr: maliyet azalır → 258 alacak
            satirlar.append(SatirGirdi(hesap_kodu=kf.YATIRIM_HESABI, taraf="A", islem_tutari=k["kar"],
                                       aciklama=f"KUR DEĞERLEME KÂRI ({k['kod']})", yatirim_projesi_id=pk))
        if k["zarar"] > 0:   # zarar: maliyet artar → 258 borç
            satirlar.append(SatirGirdi(hesap_kodu=kf.YATIRIM_HESABI, taraf="B", islem_tutari=k["zarar"],
                                       aciklama=f"KUR DEĞERLEME ZARARI ({k['kod']})", yatirim_projesi_id=pk))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar, aciklama=f"DÖNEM SONU KUR DEĞERLEME {tarih:%d.%m.%Y}",
                          kaynak=YevmiyeFisi.Kaynak.KUR_DEGERLEME, kullanici=kullanici,
                          kur_usd=_usd_kuru(tarih))
    except YevmiyeHatasi as e:
        raise KurDegerlemeHatasi(str(e))
    return KurDegerleme.objects.create(
        tarih=tarih, fis=fis, toplam_kar=o["kar"], toplam_zarar=o["zarar"],
        kurlar={f"{r['hesap_kodu']}|{r['pb']}": f"{r['kur']} ({r['kur_tarihi']:%d.%m.%Y})" for r in o["satirlar"]},
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
            yatirim_projesi_id=s.yatirim_projesi_id,       # ters kayıt AYNI hesaba (258 + aynı proje)
            aciklama=buyuk_harf_tr("ters kayıt — " + (s.aciklama or ""))[:500]))
    try:
        fis = fis_olustur(tarih=tarih, satirlar=satirlar,
                          aciklama=f"KUR DEĞERLEME TERS KAYDI ({deg.tarih:%d.%m.%Y} DEĞERLEMESİ)",
                          kaynak=YevmiyeFisi.Kaynak.KUR_DEGERLEME, kullanici=kullanici,
                          kur_usd=_usd_kuru(tarih))
    except YevmiyeHatasi as e:
        raise KurDegerlemeHatasi(f"{e} (258'e yazılmış bir değerlemede proje aktifleştirilmişse ters kayıt "
                                 f"manuel fişle yapılmalıdır.)")
    deg.ters_fis = fis
    deg.updated_by = kullanici
    deg.save(update_fields=["ters_fis", "updated_by", "updated_at"])
    return deg
