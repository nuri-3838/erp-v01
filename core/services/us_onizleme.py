"""ÜS ÖNİZLEME (2026-10-14, Üretim Siparişleri Faz 2): Yeni ÜS formunda "Önizle" — KAYDETMEDEN açılacak istasyon emirleri (istasyon bazında), stoktan/fasondan ayrılacaklar ve eksik malzeme
(satınalma / stoklu ara mamul / satınalmadan-fasondan bekleniyor).

YENİ HESAP YAZILMAZ: ``uretim_emri_olustur``un kullandığı net plan (``uretim._plani_hesapla`` → ``ihtiyac_hesapla``) ve ``_ayrilani_bol`` AYNEN çağrılır; yalnız sonuç yazmak yerine
gösterilir. HİÇBİR KAYIT YAZILMAZ: varyantlı planda ``us_detay_yukle.TopluCozucu`` (varyant AÇMAZ; olmayan kombinasyon kaydedilmemiş yer tutucu), fason dağıtımı salt okunur,
ÜS/ayırma/istasyon emri/rezerv oluşturulmaz. Eşdeğerlik testle kilitli: önizleme, hemen ardından gerçekten açılan ÜS'nin istasyon emirleri, ayırmaları ve eksikleriyle birebir aynı."""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.models import StokVaryant
from core.services import fason_siparis as fs_servis
from core.services import planlama_varyant, satinalma, urun_agaci
from core.services import uretim
from core.services import varyant as vs
from core.services.us_detay_yukle import Paylasim

SIFIR = Decimal("0")


def _coz_varyant(y: Paylasim, stok, ham):
    """Ekran girdisi ('v:BOYUT=pk|…', 'sv:pk', pk) → StokVaryant; BULUNUR, AÇILMAZ (yoksa kaydedilmemiş yer tutucu). Boş → None (kartın standart varyantı)."""
    if ham in (None, ""):
        return None
    if isinstance(ham, StokVaryant):
        return ham
    if not stok.varyantli:
        raise vs.VaryantHatasi(f"{stok.kod} varyantlı bir kart değil; varyant verilemez.")
    metin = str(ham)
    if metin.startswith("sv:"):
        metin = metin[3:]
    if metin.isdigit():
        sv = y.sv_id.get(int(metin))
        if sv is None or sv.stok_id != stok.pk:
            raise vs.VaryantHatasi(f"{stok.kod}: varyant bulunamadı.")
        return sv
    if not metin.startswith("v:"):
        raise vs.VaryantHatasi(f"{stok.kod}: varyant değeri çözümlenemedi.")
    secimler = {}
    for parca in metin[2:].split("|"):
        k, _, v = parca.partition("=")
        if k.strip():
            secimler[k.strip()] = int(v) if v.strip() else None
    bilinmeyen = set(secimler) - set(vs.stok_boyutlari(stok))
    if bilinmeyen:
        raise vs.VaryantHatasi(f"{stok.kod}: bilinmeyen varyant boyutu ({', '.join(sorted(bilinmeyen))}).")
    return y.cozucu.ob.sv_bul(stok, secimler)


def _fason_dagit(fason_pay: dict) -> list:
    """``fason_siparis.rezervleri_esitle`` ile AYNI dağıtım (termin → belge → sıra), YAZMADAN: [{"siparis", "stok", "varyant_etiketi", "miktar", "termin"}]."""
    satirlar_sonuc = []
    for (stok_pk, vpk), miktar in sorted(fason_pay.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
        kalan = max(SIFIR, Decimal(str(miktar)))
        satirlar = (fs_servis._satirlar_qs().filter(siparis__silindi=False, siparis__durum__in=fs_servis.ACIK_DURUMLAR, stok_id=stok_pk)
                    .select_related("siparis").order_by("siparis__termin", "siparis__pk", "sira", "pk"))
        if vpk is not None:
            satirlar = satirlar.filter(varyant_id=vpk)
        satirlar = sorted(satirlar, key=lambda s: (s.siparis.termin is None, s.siparis.termin or timezone.localdate(), s.siparis_id, s.sira, s.pk))
        for s in satirlar:
            x = min(kalan, fs_servis.satir_kalan(s))
            kalan -= x
            if x > 0:
                satirlar_sonuc.append({"siparis": s.siparis, "stok": s.stok, "varyant_etiketi": vs.varyant_kisa(s.varyant) if s.varyant_id else "", "miktar": x,
                                       "termin": s.siparis.termin})
            if kalan <= 0:
                break
    return satirlar_sonuc


def onizle(*, kalemler, kullanici=None) -> dict:
    """``kalemler``: [{"hedef_urun_id", "hedef_miktar", "varyant": 'v:…' (ham, isteğe bağlı)}] — Yeni ÜS (stok için) açılışının KAYDETMEDEN önizlemesi.
    Dönüş: {"varyantli", "kalemler", "istasyonlar", "ie_sayisi", "ayirmalar", "eksikler"} (aşağıda)."""
    varyantli = planlama_varyant.acik()
    y = Paylasim()
    satirlar = []
    for s in kalemler:
        d = dict(s)
        ham = d.pop("varyant", None)
        if varyantli:
            urun = uretim._cikti_coz(d["hedef_urun_id"])
            d["varyant_id"] = _coz_varyant(y, urun, ham) if urun.varyantli else None
        satirlar.append(d)
    coz = uretim._kalemleri_coz(satirlar, None, varyantli)
    stoktan = uretim.stok_icin_tavan(coz, varyantli)                                         # yeni ÜS = stok için: kök hedef kendi stoğundan karşılanmaz
    graf = None if varyantli else urun_agaci.graf_yukle()
    sonuc, _cz = uretim._plani_hesapla(coz, varyantli, kullanici, stoktan=stoktan, graf=graf, cozucu=y.cozucu if varyantli else None)
    stok_pay, fason_pay = uretim._ayrilani_bol(sonuc, varyantli)

    # 1) açılacak istasyon emirleri (uretim_emri_olustur: çalıştırması > 0 olan her plan satırı), istasyon bazında
    gruplar = {}
    for p in sonuc["plan"]:
        if p["calistirma"] <= 0:
            continue
        op, sv = planlama_varyant.kosum_coz(p["operasyon"])
        ref = uretim._referans_miktar(op)
        g = gruplar.setdefault(op.istasyon_id, {"istasyon": op.istasyon, "emirler": [], "fason": bool(op.istasyon.fason)})
        g["emirler"].append({"cikti": op.cikti, "varyant": vs.varyant_kisa(sv) if sv is not None else "", "calistirma": p["calistirma"], "adet": p["calistirma"] * ref,
                             "seviye": p["seviye"], "op_pk": op.pk, "varyant_pk": sv.pk if sv is not None else None})
    istasyonlar = sorted(gruplar.values(), key=lambda g: (g["istasyon"].sira, g["istasyon"].kod))
    for g in istasyonlar:
        g["emirler"].sort(key=lambda e: (-e["seviye"], e["cikti"].kod))
        g["emir_sayisi"] = len(g["emirler"])
        g["adet"] = sum((e["adet"] for e in g["emirler"]), SIFIR)

    # 2) stoktan / fasondan ayrılacaklar
    ozet = {o["stok"].pk: o for o in sonuc["ozet"]}
    ayirmalar = []
    for k, a in sonuc["ayrilan"].items():
        if a <= 0 or k not in ozet:
            continue
        st, sv = planlama_varyant.dugum_coz(ozet[k]["stok"])
        ayirmalar.append({"stok": st, "varyant_etiketi": vs.varyant_kisa(sv) if sv is not None else "", "toplam": a, "stoktan": stok_pay.get(k, SIFIR),
                          "fasondan": fason_pay.get(k if varyantli else (k, None), SIFIR)})
    ayirmalar.sort(key=lambda r: (r["stok"].kod, r["varyant_etiketi"]))

    # 3) eksik malzeme (uretim.uretim_emri_eksikleri ile aynı sınıflandırma; ÜS henüz yok → rezerv yok)
    eksikler = []
    for o in sonuc["ozet"]:
        if o["yaprak"] and o["eksik"] is not None and o["eksik"] > 0:
            st, sv = planlama_varyant.dugum_coz(o["stok"])
            eksikler.append({"stok": st, "varyant": sv, "varyant_etiketi": planlama_varyant.etiket(sv), "gerekli": o["ihtiyac"], "kullanilabilir": o["ayrilan"],
                             "eksik": o["eksik"], "stoklu": bool(st.stoklu_uretim), "tur": "STOKLU" if st.stoklu_uretim else "SATINALMA"})
    eksikler.sort(key=lambda x: (x["stok"].kod, x["varyant_etiketi"]))
    satinalma_idler = {e["stok"].pk for e in eksikler if e["tur"] == "SATINALMA"}
    if satinalma_idler:
        sat_satirlar = satinalma.acik_siparis_satirlari(satinalma_idler)
        sat_harita = satinalma.harita_kur(sat_satirlar)
        for e in eksikler:
            if e["tur"] != "SATINALMA":
                continue
            vpk = e["varyant"].pk if e["varyant"] is not None else None
            kalan = satinalma.kalan_icin(sat_harita, e["stok"].pk, vpk)
            if kalan <= 0:
                continue
            bekleyen = min(e["eksik"], kalan)
            e["siparis_bekleyen"] = bekleyen
            e["siparisler"] = sorted({(s["siparis"].belge_no, s["siparis"].pk, s["termin"]) for s in sat_satirlar
                                      if s["stok_id"] == e["stok"].pk and (vpk is None or s["varyant_id"] == vpk)})
            e["eksik"] = e["eksik"] - bekleyen
            if e["eksik"] <= 0:
                e["tur"] = "SATINALMA_SIPARIS"
    for r in _fason_dagit(fason_pay):                                                          # fasondan bekleniyor (ÜS açılınca bu miktar rezerve edilir)
        eksikler.append({"stok": r["stok"], "varyant": None, "varyant_etiketi": r["varyant_etiketi"], "gerekli": r["miktar"], "kullanilabilir": SIFIR, "eksik": SIFIR,
                         "stoklu": False, "tur": "FASON_SIPARIS", "fs": r["siparis"], "termin": r["termin"]})
    return {"varyantli": varyantli, "kalemler": [(u, m, vs.varyant_kisa(v) if v is not None else "") for u, m, v in coz], "istasyonlar": istasyonlar,
            "ie_sayisi": sum(g["emir_sayisi"] for g in istasyonlar), "ayirmalar": ayirmalar, "eksikler": eksikler,
            "eksik_sayisi": sum(1 for e in eksikler if e["tur"] in ("SATINALMA", "STOKLU")), "satinalma_bekleyen": sum(1 for e in eksikler if e["tur"] == "SATINALMA_SIPARIS"),
            "fason_bekleyen": sum(1 for e in eksikler if e["tur"] == "FASON_SIPARIS")}
