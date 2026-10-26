"""ÜS DETAY EKRANI (2026-10-14 yeniden tasarım): yalnız GÖRÜNÜM verisi — üst bant, akış şeridi, Kayıtlar sekmesi. Hesaplar mevcut servislerin ürettiği verilerden
(kalemler, istasyon emri grupları, eksikler) Python'da türetilir; ek sorgu tek tek sayılıdır:
  * ``kayit_sayilari``: tek aggregate (eski: 3 ayrı sayım),
  * ``ilerleme``: istasyon emri gruplarından (eski ``uretim_emri_ilerleme``: 3 sorgu),
  * ``eksik_istasyonlari``: yalnız gerçek eksik varsa 1 sorgu (eksik stoğu girdi olarak kullanan operasyonlar),
  * ``son_kayitlar``: 3 sorgu (kayıt + çıktılar + ekip), kayıt sayısından BAĞIMSIZ."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from core.models import IsOturumuUyesi, IstasyonEmri, OperasyonGirdi, OperasyonKaydi, StokHareket, UretimEmri

SIFIR = Decimal("0")
SEKMELER = ("urunler", "istasyonlar", "malzeme", "kayitlar")
KAYIT_ADET = 50
GERCEK_EKSIK = ("SATINALMA", "STOKLU")                       # liste rozetiyle aynı: "Eksik malzeme: N"

RENK_ETIKET = {"tamam": "tamamlandı", "devam": "devam ediyor", "bekliyor": "başlamadı", "eksik": "malzeme eksik", "fason": "fason", "iptal": "iptal"}


def css_yuzde(d) -> str:
    """CSS genişliği için NOKTALI metin (şablon yerelleştirmesi virgül basıp CSS'i bozar), 0–100 arası, 1 hane."""
    d = Decimal(d or 0)
    return format(min(max(d, SIFIR), Decimal("100")), ".1f")


def sekme_sec(istek_sekme, hata_var=False) -> str:
    """URL ``?sekme=`` değeri; geçersiz/boşsa varsayılan 'urunler' (üretim hatası varsa 'istasyonlar')."""
    if istek_sekme in SEKMELER:
        return istek_sekme
    return "istasyonlar" if hata_var else "urunler"


def kayit_sayilari(emir: UretimEmri) -> dict:
    a = emir.operasyon_kayitlari.filter(silindi=False).aggregate(
        toplam=Count("pk"), onayli=Count("pk", filter=Q(durum=OperasyonKaydi.Durum.ONAYLI)),
        bagli=Count("pk", filter=Q(durum=OperasyonKaydi.Durum.ONAYLI) | Q(istasyon_emri__isnull=False)))
    return {"toplam": a["toplam"], "onayli": a["onayli"], "taslak": a["toplam"] - a["onayli"], "bagli": a["bagli"]}


def silinebilir(emir: UretimEmri, ieler: list, kayit_sayisi: dict) -> bool:
    """``uretim.uretim_emri_silinebilir`` ile AYNI karar, yüklenmiş veriden: açık ve başlamamış (istasyon emri BASLADI/BITTI/tamamlanan>0, onaylı ya da emre bağlı kayıt,
    ya da kayıt hareketi yok). Önceki koşullar yetiyorsa son (hareket) sorgusu atılmaz."""
    if emir.durum != UretimEmri.Durum.ACIK:
        return False
    if any(ie.durum in (IstasyonEmri.Durum.BASLADI, IstasyonEmri.Durum.BITTI) or ie.tamamlanan > 0 for ie in ieler):
        return False
    if kayit_sayisi["bagli"] > 0:
        return False
    return not StokHareket.objects.filter(operasyon_kaydi__uretim_emri=emir).exists()


def ilerleme(gruplar: list, kayit_sayisi: dict) -> dict:
    """``uretim.uretim_emri_ilerleme`` ile AYNI sonuç: iptal olmayan istasyon emri sayısı ve biten; emri yoksa (eski ÜS) operasyon kayıtlarından."""
    ieler = [s["ie"] for g in gruplar for s in g["satirlar"] if s["ie"].durum != IstasyonEmri.Durum.IPTAL]
    if ieler:
        return {"toplam": len(ieler), "onayli": sum(1 for ie in ieler if ie.durum == IstasyonEmri.Durum.BITTI)}
    return {"toplam": kayit_sayisi["toplam"], "onayli": kayit_sayisi["onayli"]}


def is_yuzdesi(gruplar: list):
    """İş %: iptal olmayan istasyon emirlerinde Σ min(tamamlanan, planlanan) ÷ Σ planlanan × 100 (çalıştırma ağırlıklı; biten emir tam). Emir yoksa None.
    ``uretim.uretim_emirleri_ozet`` ["is_yuzde"] ile AYNI sonuç (testle kilitli)."""
    ieler = [s["ie"] for g in gruplar for s in g["satirlar"] if s["ie"].durum != IstasyonEmri.Durum.IPTAL]
    plan = sum((ie.planlanan for ie in ieler), SIFIR)
    if not plan:
        return None
    yapilan = sum((ie.planlanan if ie.durum == IstasyonEmri.Durum.BITTI else min(ie.tamamlanan, ie.planlanan) for ie in ieler), SIFIR)
    return min(yapilan * 100 / plan, Decimal("100"))


def genel_yuzde(kalemler: list, tamamlandi: bool) -> Decimal:
    """``uretim.uretim_emri_yuzde`` ile AYNI: Σ min(hedef, ayrılan+sevk) ÷ Σ hedef × 100; tamamlanmış stok ÜS'si %100."""
    if tamamlandi:
        return Decimal("100")
    toplam = hazir = SIFIR
    for s in kalemler:
        hedef = s["k"].hedef_miktar
        toplam += hedef
        hazir += min(hedef, s["ayrilan"] + s["sevk"])
    return (hazir * 100 / toplam) if toplam else SIFIR


def bant(emir: UretimEmri, bugun: datetime.date, eksikler: list) -> dict:
    """Üst bant: müşteri/kaynak, termin + kalan gün, rozet sayıları (liste ekranıyla aynı sayım)."""
    from core.services import uretim as uretim_servis
    sip = emir.kaynak_siparis
    termin = uretim_servis.etkin_termin(emir)                       # ÜS'nin kendi terminı; boşsa (eski ÜS) kaynak siparişin teslim tarihi
    acik = emir.durum == UretimEmri.Durum.ACIK
    kalan = (termin - bugun).days if termin else None
    return {
        "sip": sip, "musteri": sip.cari.unvan if sip is not None and sip.cari_id else "", "termin": termin,
        "kalan_gun": kalan if acik else None, "termin_gecmis": bool(acik and kalan is not None and kalan < 0),
        "kalan_metin": ("" if kalan is None else f"{-kalan} gün geçti" if kalan < 0 else "bugün" if kalan == 0 else f"{kalan} gün kaldı"),
        "eksik": sum(1 for x in eksikler if x["tur"] in GERCEK_EKSIK),
        "fason_bekleyen": sum(1 for x in eksikler if x["tur"] == "FASON_SIPARIS"),
        "satinalma_bekleyen": sum(1 for x in eksikler if x["tur"] == "SATINALMA_SIPARIS"),
        "fason_emri_gerekli": sum(1 for x in eksikler if x["tur"] == "FASON_EMRI_GEREKLI"),
    }


def eksik_istasyonlari(gruplar: list, eksikler: list, operasyonlar=None) -> dict:
    """{istasyon pk: [eksik stok kodları]} — o istasyonun AÇIK (bitmemiş) emirlerinin operasyonu, gerçek eksik stoğu GİRDİ olarak kullanıyorsa. Eksik yoksa sorgusuz."""
    eksik_stok = {x["stok"].pk: x["stok"].kod for x in eksikler if x["tur"] in GERCEK_EKSIK and x.get("stok") is not None}
    if not eksik_stok:
        return {}
    acik = [s["ie"] for g in gruplar for s in g["satirlar"] if s["ie"].durum in (IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI)]
    if not acik:
        return {}
    kullanan = {}
    if operasyonlar is not None:                                  # yüklenmiş operasyonlar (ek sorgu yok)
        for ie in acik:
            op = operasyonlar[ie.operasyon_id]
            for g in op.girdiler.all():
                if not g.silindi and g.girdi_id in eksik_stok:
                    kullanan.setdefault(op.pk, set()).add(g.girdi_id)
    else:
        for op_id, girdi_id in (OperasyonGirdi.objects.filter(silindi=False, operasyon_id__in={ie.operasyon_id for ie in acik}, girdi_id__in=list(eksik_stok))
                                .values_list("operasyon_id", "girdi_id")):
            kullanan.setdefault(op_id, set()).add(girdi_id)
    sonuc = {}
    for ie in acik:
        for stok_id in kullanan.get(ie.operasyon_id, ()):
            kodlar = sonuc.setdefault(ie.istasyon_id, [])
            if eksik_stok[stok_id] not in kodlar:
                kodlar.append(eksik_stok[stok_id])
    return sonuc


def akis_seridi(gruplar: list, eksik_ist: dict) -> list:
    """İstasyonlar üretim sırasıyla (gruplar zaten sıralı) kutular. Renk önceliği: tamamlandı > malzeme eksik > fason > devam > başlamadı."""
    kutular = []
    for g in gruplar:
        ieler = [s["ie"] for s in g["satirlar"] if s["ie"].durum != IstasyonEmri.Durum.IPTAL]
        plan = sum((ie.planlanan for ie in ieler), SIFIR)
        yapilan = sum((ie.planlanan if ie.durum == IstasyonEmri.Durum.BITTI else min(ie.tamamlanan, ie.planlanan) for ie in ieler), SIFIR)
        yuzde = min(yapilan * 100 / plan, Decimal("100")) if plan else SIFIR
        bitti = sum(1 for ie in ieler if ie.durum == IstasyonEmri.Durum.BITTI)
        baslamis = any(ie.durum in (IstasyonEmri.Durum.BASLADI, IstasyonEmri.Durum.BITTI) or ie.tamamlanan > 0 for ie in ieler)
        eksikler = eksik_ist.get(g["istasyon"].pk, [])
        if not ieler:
            renk = "iptal"
        elif bitti == len(ieler):
            renk = "tamam"
        elif eksikler:
            renk = "eksik"
        elif g["fason"]:
            renk = "fason"
        elif baslamis:
            renk = "devam"
        else:
            renk = "bekliyor"
        ipucu = f"{g['istasyon'].kod} {g['istasyon'].ad}: {RENK_ETIKET[renk]}" + (f" — eksik: {', '.join(eksikler)}" if eksikler else "")
        kutular.append({"istasyon": g["istasyon"], "grup_id": f"ie-grup-{g['istasyon'].pk}", "bitti": bitti, "toplam": len(ieler), "yuzde": yuzde,
                        "yuzde_css": css_yuzde(yuzde), "renk": renk, "etiket": RENK_ETIKET[renk], "eksikler": eksikler, "fason": g["fason"], "ipucu": ipucu})
    return kutular


def son_kayitlar(emir: UretimEmri, adet: int = KAYIT_ADET) -> list:
    """Bu ÜS'nin son ``adet`` operasyon kaydı (geri alınanlar hariç): tarih-saat, personel/ekip, istasyon, çıktı, sağlam/fire. 3 sorgu (kayıt, çıktılar, ekip)."""
    uyeler = Prefetch("is_oturumu__uyeler", queryset=IsOturumuUyesi.objects.filter(silindi=False).select_related("personel").order_by("-baslatan", "id"))
    qs = (OperasyonKaydi.objects.filter(uretim_emri=emir, silindi=False)
          .select_related("operasyon__istasyon", "operasyon__cikti", "personel", "istasyon_emri", "is_oturumu", "cikti_varyant__stok", "cikti_varyant__markalama",
                          "cikti_varyant__kilavuz", "cikti_varyant__afis", "cikti_varyant__yan_etiket")
          .prefetch_related("ciktilar", uyeler).order_by("-yil", "-sira")[:adet])
    from core.services import planlama_varyant
    satirlar = []
    for k in qs:
        ana = next((c for c in k.ciktilar.all() if c.ana_mi and not c.silindi), None)
        saglam = ana.miktar if ana is not None else k.hedef_cikti_miktari
        beklenen = ana.beklenen_miktar if ana is not None and ana.beklenen_miktar is not None else k.hedef_cikti_miktari
        fire = max(beklenen - saglam, SIFIR)
        try:
            oturum = k.is_oturumu
        except ObjectDoesNotExist:
            oturum = None
        if oturum is not None:
            ekip = list(dict.fromkeys(u.personel.ad_soyad for u in oturum.uyeler.all() if not u.silindi))       # ayrılıp tekrar katılan bir kez
            if oturum.miktar is not None:
                saglam = oturum.miktar
            fire = oturum.fire or SIFIR
        else:
            ekip = [k.personel.ad_soyad] if k.personel_id else []
        satirlar.append({"k": k, "zaman": timezone.localtime(k.created_at), "personel": ", ".join(ekip) or "—", "ekip": len(ekip) > 1,
                         "istasyon": k.operasyon.istasyon, "cikti": k.operasyon.cikti,
                         "varyant": planlama_varyant.etiket(k.cikti_varyant) if k.cikti_varyant_id else "",
                         "saglam": saglam, "fire": fire, "taslak": k.durum != OperasyonKaydi.Durum.ONAYLI, "ie_no": k.istasyon_emri.no if k.istasyon_emri_id else ""})
    return satirlar
