"""VARYANTLI KARTI VARYANTSIZ YAPMA (tek seferlik düzeltme; bkz. core/management/commands/varyant_kapat.py).

Yanlışlıkla varyantlı (boyut: MARKALAMA) açılmış kartlar (ör. CS ayak SOL 151-10-0064…0086) için: karttaki TEK varyanta bağlı tüm satırlar (stok hareketi — silinmişler
dahil —, belge kalemleri, ayırma, üretim kaydı satırları… alanlar modellerden bulunur) varyantsıza (NULL) çevrilir; kartın ``varyantli`` / ``varyant_boyutlari`` /
``varsayilan_varyant`` ayarı kapanır, varyant satırı kapatılır (soft-delete, iz kalır); kart yeniden hesaplanır. Kartı girdi/çıktı olarak kullanan operasyon tanımlarında
SABİT varyant kuralı varsa (varyantsız kartta geçersiz) TASI'ye çevrilir — TASI varsayılan kural varyantsız kartta zararsızdır, dokunulmaz.

Tek atomik işlem; ``uygula=False`` ise GERİ ALINIR (kuru çalıştırma). Doğrulama (biri tutmazsa MaliyetHatasi gibi VaryantKapatHatasi, hiçbir şey kaydedilmez):
hareket sayısı/tarih/miktar/tutar/maliyet durumu aynı, kart miktar-değer-ortalama aynı, depo bazında eldeki aynı, mizan aynı, tüm kartların stok değeri aynı,
varyantlı hareket kalmadı, hiçbir çıkışın maliyeti değişmedi, kartları kullanan bitmiş ürünlerin ihtiyaç planı (kart bazlı VE varyantlı planlama) aynı.
Ön koşullar: kart varyantlı, tek aktif varyant satırı, hareketlerde tek varyant — aksi halde DURUR (iki varyanta ayrılmış kart elle birleştirilmeli: ``varyant_birlestir``)."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import Depo, Operasyon, OperasyonGirdi, Stok, StokHareket, StokVaryant, YevmiyeSatir
from core.services import stok_ortalama
from core.services import varyant as varyant_servis

SIFIR = Decimal("0")
VARSAYILAN_KODLAR = ("151-10-0064", "151-10-0066", "151-10-0068", "151-10-0070", "151-10-0072", "151-10-0074",      # kesilmiş CS ayak SOL
                     "151-10-0076", "151-10-0078", "151-10-0080", "151-10-0082", "151-10-0084", "151-10-0086")      # boyalı CS ayak SOL
PLAN_MIKTARI = Decimal("10")


class VaryantKapatHatasi(ValueError):
    """Kural ihlali / doğrulama başarısızlığı (Türkçe mesaj); işlem geri alınır."""


def varyant_fk_alanlari():
    """[(model, alan adı)] — StokVaryant'a FK veren tüm alanlar (kartın varsayılan varyantı hariç; o ayrıca yönetilir)."""
    sonuc = []
    for m in apps.get_app_config("core").get_models():
        for f in m._meta.get_fields():
            if f.is_relation and f.many_to_one and f.related_model is StokVaryant and not (m is Stok and f.name == "varsayilan_varyant"):
                sonuc.append((m, f.name))
    return sonuc


def kapat(kodlar=VARSAYILAN_KODLAR, *, uygula=False, kullanici=None) -> dict:
    """Tek transaction: ön koşullar → varyantsıza çevirme → maliyet → doğrulama → rapor; ``uygula=False`` ise GERİ ALINIR."""
    with transaction.atomic():
        rapor = _isle(list(kodlar), kullanici)
        if not uygula:
            transaction.set_rollback(True)
    rapor["uygulandi"] = uygula
    if uygula and rapor["kartlar"]:
        rapor["log"] = _log(rapor)
    return rapor


# ------------------------------------------------------------------------------------------------------------ anlık görüntü

def _mizan():
    return {r["hesap__hesap_kodu"]: (r["b"] or SIFIR, r["a"] or SIFIR)
            for r in YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).values("hesap__hesap_kodu").annotate(b=Sum("borc"), a=Sum("alacak"))}


def _plan_ozeti(stoklar) -> dict:
    """Kartları (zincirde) kullanan bitmiş ürünlerin ihtiyaç planı — kart bazlı VE varyantlı planlama yolu; satırlar KOD bazında toplanır (düğüm
    ayrıntısı varyant sayısına bağlı olduğundan karşılaştırma kod toplamlarıyla yapılır)."""
    from core.services import planlama_varyant, urun_agaci
    from core.services.uretim import ihtiyac_hesapla
    graf = urun_agaci.graf_yukle()
    pks = {s.pk for s in stoklar}
    kokler = [k for k in graf.kokler() if pks & set(graf.birim_tuketim(k))]

    def ozet(sonuc):
        t = defaultdict(lambda: [SIFIR, SIFIR, SIFIR, SIFIR])
        for o in sonuc["ozet"]:
            kod = str(getattr(o["stok"], "kod", o["stok"])).split(" [")[0]          # varyantlı düğüm adı "KOD [VARYANT]": etiket atılır, kod bazında toplanır
            for i, alan in enumerate(("ihtiyac", "uretilecek_miktar", "toplam_miktar", "calistirma_sayisi")):
                t[kod][i] += o.get(alan) or SIFIR
        return {k: tuple(v) for k, v in t.items()}

    sonuc = {}
    for kok in sorted(kokler, key=lambda s: s.kod):
        try:
            klasik = ozet(ihtiyac_hesapla([(kok, PLAN_MIKTARI)], graf=graf))
        except Exception as e:                                               # noqa: BLE001 — hata metni de karşılaştırılır (önce/sonra aynı olmalı)
            klasik = f"HATA: {e}"
        try:
            cz = planlama_varyant.Cozucu(None)
            varyantli = ozet(ihtiyac_hesapla([(cz.kalem_dugumu(kok, None), PLAN_MIKTARI)], cozucu=cz))
        except Exception as e:                                               # noqa: BLE001
            varyantli = f"HATA: {e}"
        sonuc[kok.kod] = {"klasik": klasik, "varyantli": varyantli}
    return sonuc


def _anlik(stoklar) -> dict:
    kartlar = {}
    for s in stoklar:
        s = Stok.objects.get(pk=s.pk)
        hareketler = list(StokHareket.objects.filter(stok=s, silindi=False))
        eldeki = defaultdict(lambda: SIFIR)
        for h in hareketler:
            eldeki[h.depo_id] += h.miktar if h.tur == StokHareket.Tur.GIRIS else -h.miktar
        kartlar[s.kod] = {
            "ad": s.ad, "varyantli": s.varyantli, "boyutlar": list(s.varyant_boyutlari or []), "varsayilan": s.varsayilan_varyant_id,
            "miktar": s.maliyet_miktar, "deger": s.maliyet_deger_try, "ort": s.ort_maliyet_try,
            "aktif": len(hareketler), "silinmis": StokHareket.objects.filter(stok=s, silindi=True).count(),
            "varyantli_hareket": sum(1 for h in hareketler if h.varyant_id), "eldeki": dict(eldeki),
            "hareketler": {h.pk: (h.tur, h.miktar, h.tarih, h.depo_id, h.tutar_try, h.maliyet_durumu) for h in hareketler}}
    return {"kartlar": kartlar, "mizan": _mizan(), "tum_deger": Stok.objects.aggregate(d=Sum("maliyet_deger_try"))["d"] or SIFIR,
            "plan": _plan_ozeti(stoklar)}


# ------------------------------------------------------------------------------------------------------------ işlem

def _isle(kodlar, kullanici) -> dict:
    stoklar = {s.kod: s for s in Stok.objects.filter(kod__in=kodlar, silindi=False)}
    eksik = [k for k in kodlar if k not in stoklar]
    if eksik:
        raise VaryantKapatHatasi(f"Stok bulunamadı: {', '.join(eksik)}")
    rapor = {"kodlar": list(kodlar), "kartlar": [], "zaten_varyantsiz": [k for k in kodlar if not stoklar[k].varyantli], "once": None, "sonra": None,
             "degisen_satirlar": {}, "degisen_hareket_idleri": [], "operasyonlar": [], "operasyon_duzeltme": [], "kontrol": [], "degisen_cikis": 0}
    hedefler = [stoklar[k] for k in kodlar if stoklar[k].varyantli]
    if not hedefler:
        return rapor
    alanlar = varyant_fk_alanlari()
    svler = {}
    for s in hedefler:                                                   # ön koşullar (hiçbir şey yazmadan)
        aktif = list(StokVaryant.objects.filter(stok=s, silindi=False))
        if len(aktif) != 1:
            raise VaryantKapatHatasi(f"{s.kod}: tek aktif varyant satırı olmalı (bulunan {len(aktif)}); iki varyantlı kart önce varyant_birlestir ile birleştirilmeli.")
        kullanilan = set(StokHareket.objects.filter(stok=s).exclude(varyant__isnull=True).values_list("varyant_id", flat=True))
        if kullanilan - {aktif[0].pk}:
            raise VaryantKapatHatasi(f"{s.kod}: hareketlerde birden çok varyant kullanılıyor ({sorted(kullanilan)}); önce birleştirin.")
        svler[s.pk] = list(StokVaryant.objects.filter(stok=s))          # silinmişler dahil (bağlı eski satırlar da çözülür)
    once = _anlik(hedefler)
    rapor["once"] = once
    # --- 1) varyant bağlarını kaldır (NULL) ---
    for s in hedefler:
        for model, alan in alanlar:
            ids = list(model._base_manager.filter(**{f"{alan}__in": svler[s.pk]}).values_list("pk", flat=True))
            if not ids:
                continue
            try:
                with transaction.atomic():
                    model._base_manager.filter(pk__in=ids).update(**{alan: None})
            except IntegrityError as e:
                raise VaryantKapatHatasi(f"{s.kod} {model.__name__}.{alan}: varyantsıza çevirirken benzersizlik çakışması — {e}")
            anahtar = f"{model.__name__}.{alan}"
            rapor["degisen_satirlar"][anahtar] = rapor["degisen_satirlar"].get(anahtar, 0) + len(ids)
            if model is StokHareket:
                rapor["degisen_hareket_idleri"] += [(s.kod, pk) for pk in ids]
    # --- 2) kart ayarı + varyant satırları ---
    for s in hedefler:
        Stok.objects.filter(pk=s.pk).update(varyantli=False, varyant_boyutlari=[], varsayilan_varyant=None, updated_by=kullanici, updated_at=timezone.now())
        s = Stok.objects.get(pk=s.pk)
        try:
            varyant_servis.stok_varyantlarini_esitle(s, kullanici)
        except varyant_servis.VaryantHatasi as e:
            raise VaryantKapatHatasi(str(e))
        StokVaryant.objects.filter(stok=s).update(maliyet_miktar=SIFIR, maliyet_deger_try=SIFIR, maliyet_deger_usd=SIFIR, ort_maliyet_try=None, ort_maliyet_usd=None)
        rapor["kartlar"].append(s.kod)
    # --- 3) operasyon tanımları: kartı kullanan tanımlar listelenir; SABİT kural (varyantsız kartta geçersiz) TASI'ye alınır ---
    TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT
    pks = [s.pk for s in hedefler]
    kullanan = set(Operasyon.objects.filter(silindi=False, cikti_id__in=pks).values_list("pk", flat=True))
    kullanan |= set(OperasyonGirdi.objects.filter(silindi=False, girdi_id__in=pks, operasyon__silindi=False).values_list("operasyon_id", flat=True))
    rapor["operasyonlar"] = sorted(kullanan)
    for g in OperasyonGirdi.objects.filter(girdi_id__in=pks, silindi=False).select_related("girdi"):
        if g.varyant_kurali != TASI or g.sabit_varyant_id:
            OperasyonGirdi.objects.filter(pk=g.pk).update(varyant_kurali=TASI, sabit_varyant=None)
            rapor["operasyon_duzeltme"].append({"operasyon": g.operasyon_id, "alan": "girdi", "stok": g.girdi.kod, "eski": g.varyant_kurali})
    for op in Operasyon.objects.filter(silindi=False, cikti_id__in=pks).select_related("cikti"):
        if op.cikti_varyant_kurali != TASI or op.cikti_sabit_varyant_id:
            Operasyon.objects.filter(pk=op.pk).update(cikti_varyant_kurali=TASI, cikti_sabit_varyant=None)
            rapor["operasyon_duzeltme"].append({"operasyon": op.pk, "alan": "cikti", "stok": op.cikti.kod, "eski": op.cikti_varyant_kurali})
    # --- 4) maliyet baştan (çıkış tutarı değişmemeli) ---
    for s in hedefler:
        degisen = stok_ortalama.yeniden_hesapla(Stok.objects.get(pk=s.pk), fis_guncelle=False)
        rapor["degisen_cikis"] += len(degisen)
    sonra = _anlik(hedefler)
    rapor["sonra"] = sonra
    _dogrula(rapor, once, sonra, hedefler)
    return rapor


def _dogrula(rapor, once, sonra, hedefler):
    kontrol = rapor["kontrol"]

    def kontrol_et(ad, tamam, ayrinti=""):
        kontrol.append((ad, bool(tamam), ayrinti))
        if not tamam:
            raise VaryantKapatHatasi(f"Doğrulama başarısız — {ad}: {ayrinti}")

    kontrol_et("kartlar varyantsız (varyantli kapalı, boyut yok, varsayılan varyant yok)",
               all(not k["varyantli"] and not k["boyutlar"] and k["varsayilan"] is None for k in sonra["kartlar"].values()))
    kontrol_et("varyantlı hareket kalmadı", all(k["varyantli_hareket"] == 0 for k in sonra["kartlar"].values()),
               str({kod: k["varyantli_hareket"] for kod, k in sonra["kartlar"].items() if k["varyantli_hareket"]}))
    kontrol_et("aktif varyant satırı kalmadı", not StokVaryant.objects.filter(stok__in=hedefler, silindi=False).exists())
    for kod, o in once["kartlar"].items():
        s = sonra["kartlar"][kod]
        kontrol_et(f"{kod} miktar/değer/ortalama aynı", (o["miktar"], o["deger"], o["ort"]) == (s["miktar"], s["deger"], s["ort"]),
                   f"{(o['miktar'], o['deger'], o['ort'])} → {(s['miktar'], s['deger'], s['ort'])}")
        kontrol_et(f"{kod} hareketler (sayı/tarih/miktar/tutar/durum) aynı", o["hareketler"] == s["hareketler"], f"{o['aktif']} → {s['aktif']} hareket")
        kontrol_et(f"{kod} depo bazında eldeki aynı", o["eldeki"] == s["eldeki"])
        kontrol_et(f"{kod} silinmiş hareket sayısı aynı", o["silinmis"] == s["silinmis"])
    kontrol_et("mizan (hesap bazında borç/alacak) aynı", once["mizan"] == sonra["mizan"])
    kontrol_et("tüm kartların stok değeri toplamı aynı", once["tum_deger"] == sonra["tum_deger"], f"{once['tum_deger']} → {sonra['tum_deger']}")
    kontrol_et("hiçbir çıkışın maliyeti değişmedi", rapor["degisen_cikis"] == 0, f"{rapor['degisen_cikis']} çıkış")
    kontrol_et("ihtiyaç planı aynı (kart bazlı + varyantlı planlama; bitmiş ürün × kod toplamları)", once["plan"] == sonra["plan"],
               "plan farklı" if once["plan"] != sonra["plan"] else "")


def _log(rapor) -> str:
    import json
    klasor = Path(getattr(settings, "BASE_DIR", ".")) / "logs"
    klasor.mkdir(exist_ok=True)
    yol = klasor / f"varyant_kapat_{timezone.now():%Y%m%d_%H%M%S}.json"
    veri = {"kartlar": rapor["kartlar"], "degisen_satirlar": rapor["degisen_satirlar"], "degisen_hareket_idleri": rapor["degisen_hareket_idleri"],
            "operasyonlar": rapor["operasyonlar"], "operasyon_duzeltme": rapor["operasyon_duzeltme"]}
    yol.write_text(json.dumps(veri, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return str(yol)


def rapor_metni(r: dict) -> str:
    def sayi(d):
        return "-" if d is None else format(Decimal(d).normalize(), "f")

    s = [f"VARYANT KAPAT — {'UYGULANDI' if r.get('uygulandi') else 'KURU ÇALIŞTIRMA (geri alındı)'}", ""]
    if r["zaten_varyantsiz"]:
        s.append(f"Zaten varyantsız (atlandı): {', '.join(r['zaten_varyantsiz'])}")
    if not r["kartlar"]:
        s.append("Yapılacak iş yok.")
        return "\n".join(s)
    s.append(f"Varyantsız yapılan kart: {len(r['kartlar'])}")
    s.append("")
    s.append("DEĞİŞEN SATIRLAR (tablo.alan: adet)")
    for ad, n in sorted(r["degisen_satirlar"].items()):
        s.append(f"  {ad}: {n}")
    hareket_aktif = sum(r["once"]["kartlar"][k]["varyantli_hareket"] for k in r["kartlar"])
    s.append(f"  → varyantı kaldırılan hareket: {len(r['degisen_hareket_idleri'])} (aktif {hareket_aktif}, silinmiş {len(r['degisen_hareket_idleri']) - hareket_aktif})")
    s += ["", "KART BAZINDA ÖNCE → SONRA (miktar | değer TL | ortalama TL | varyantlı hareket)"]
    for kod in r["kartlar"]:
        o, n = r["once"]["kartlar"][kod], r["sonra"]["kartlar"][kod]
        s.append(f"  {kod} {o['ad'][:30]:<30} varyantli {o['varyantli']}→{n['varyantli']} | {sayi(o['miktar'])}→{sayi(n['miktar'])} | {o['deger']}→{n['deger']} | "
                 f"{sayi(o['ort'])}→{sayi(n['ort'])} | varyantlı hareket {o['varyantli_hareket']}→{n['varyantli_hareket']} (hareket {o['aktif']}+{o['silinmis']} silinmiş)")
    s.append(f"  Tüm kartların stok değeri toplamı: {r['once']['tum_deger']} → {r['sonra']['tum_deger']}")
    s.append(f"  Mizan: {'AYNI' if r['once']['mizan'] == r['sonra']['mizan'] else 'DEĞİŞTİ'} ({len(r['sonra']['mizan'])} hesap)")
    s += ["", f"OPERASYON TANIMLARI (kartları girdi/çıktı olarak kullanan, aktif): {len(r['operasyonlar'])} → {', '.join(str(x) for x in r['operasyonlar'])}",
          f"  SABİT kural → TASI düzeltilen: {len(r['operasyon_duzeltme'])}"]
    for d in r["operasyon_duzeltme"]:
        s.append(f"    op {d['operasyon']} {d['alan']} {d['stok']}: {d['eski']} → TASI")
    s += ["", "İHTİYAÇ PLANI (bitmiş ürün × 10 adet; kart bazlı + varyantlı planlama): " + ("AYNI" if r["once"]["plan"] == r["sonra"]["plan"] else "FARKLI"),
          "  Bitmiş ürünler: " + ", ".join(sorted(r["once"]["plan"])),
          "", f"Maliyet yeniden hesabında değişen çıkış: {r['degisen_cikis']} (0 olmalı)", "", "DOĞRULAMALAR:"]
    for ad, tamam, ayr in r["kontrol"]:
        s.append(f"  [{'OK' if tamam else 'HATA'}] {ad}" + (f" — {ayr}" if ayr and not tamam else ""))
    if r.get("log"):
        s += ["", f"Denetim dökümü: {r['log']}"]
    return "\n".join(s)
