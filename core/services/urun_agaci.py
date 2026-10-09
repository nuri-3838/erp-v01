"""ÜRETİM > Ürün Ağacı görünümleri (Ürün · Nerede kullanılıyor · Karşılaştır) — SALT-OKUNUR hesap katmanı.

Ayrı ürün ağacı tablosu YOK (bkz. e3e9053): her şey mevcut Operasyon zincirinden, ``uretim.ihtiyac_hesapla`` motoruyla türetilir.
Tüm operasyon/girdi/yan çıktı/stok-maliyet bilgisi ``graf_yukle()`` ile TEK seferde bellekte toplanır (sabit sorgu sayısı); sonraki
bütün hesaplar bellekte yürür. Maliyet/karşılaştırma/nerede-kullanılıyor için tüketim KESİRLİ (tam boy yuvarlaması YOK) ve yan çıktılı
kesimde girdi, ana çıktının boy payı (``uretim.ana_cikti_payi``) oranında düşülür — hepsi aynı ``birim_tuketim`` fonksiyonundan geçer, bu
yüzden Ürün, Nerede kullanılıyor ve Karşılaştır ekranlarındaki rakamlar birbiriyle birebir aynıdır."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Prefetch, Sum

from core.models import Depo, Operasyon, OperasyonGirdi, OperasyonYanCikti, StokHareket
from core.services.uretim import ihtiyac_hesapla

SIFIR = Decimal("0")
BIR = Decimal("1")
KARSILASTIR_EN_FAZLA = 8

SERI_ONEKI = {"A": "152-10-", "C": "152-22-"}
SERI_AD = {"A": "A tipi", "C": "Çift çıkış", "DIGER": "Diğer"}
SERI_SIRA = ("A", "C", "DIGER")


def seri_of(kod: str) -> str:
    for seri, onek in SERI_ONEKI.items():
        if (kod or "").startswith(onek):
            return seri
    return "DIGER"


class Graf:
    """Aktif operasyon zincirinin bellek kopyası (``ihtiyac_hesapla(graf=...)`` bunu okur)."""

    def __init__(self, operasyonlar):
        self.operasyonlar = operasyonlar
        self.op_of = {op.cikti_id: op for op in operasyonlar}                 # stok pk -> operasyon
        self.girdiler = {op.pk: list(op.girdiler.all()) for op in operasyonlar}
        self.yanlar = {op.pk: list(op.yan_ciktilar.all()) for op in operasyonlar}
        self.stoklar = {}                                                      # stok pk -> Stok (zincirdeki her stok)
        self.kullanan = {}                                                     # stok pk -> [girdi olarak kullanan operasyon]
        self.yan_ureten = {}                                                   # stok pk -> [yan çıktı olarak üreten operasyon]
        for op in operasyonlar:
            self.stoklar[op.cikti_id] = op.cikti
            for g in self.girdiler[op.pk]:
                self.stoklar.setdefault(g.girdi_id, g.girdi)
                self.kullanan.setdefault(g.girdi_id, []).append(op)
            for y in self.yanlar[op.pk]:
                self.stoklar.setdefault(y.stok_id, y.stok)
                self.yan_ureten.setdefault(y.stok_id, []).append(op)
        self._tuketim = {}

    def kokler(self):
        """Zincirin en üstü (bitmiş ürünler): çıktısı hiçbir operasyonun girdisi olmayanlar, kod sırasıyla."""
        return sorted((op.cikti for op in self.operasyonlar if op.cikti_id not in self.kullanan), key=lambda s: s.kod)

    def birim_tuketim(self, stok) -> dict:
        """1 birim ``stok`` için KESİRLİ tüketim: {stok_pk: {"stok", "ihtiyac", "yaprak"}} — zincirde geçilen her stok (kök hariç)."""
        if stok.pk not in self._tuketim:
            sonuc = ihtiyac_hesapla([(stok, BIR)], boy_yuvarla=False, pay_dus=True, graf=self)
            self._tuketim[stok.pk] = {o["stok"].pk: {"stok": o["stok"], "ihtiyac": o["ihtiyac"], "yaprak": o["yaprak"]}
                                      for o in sonuc["ozet"]}
        return self._tuketim[stok.pk]

    def yapraklar(self, stok) -> dict:
        """1 birim ``stok`` için yaprak (operasyonsuz hazır stok) tüketimi: {stok_pk: (stok, miktar)}."""
        return {pk: (o["stok"], o["ihtiyac"]) for pk, o in self.birim_tuketim(stok).items() if o["yaprak"]}


def graf_yukle() -> Graf:
    """3 sorgu: operasyonlar (+istasyon, çıktı stoku), girdiler (+stok/kategori/birim/ortalama maliyet), yan çıktılar."""
    ops = list(
        Operasyon.objects.filter(silindi=False)
        .select_related("istasyon", "cikti__kategori__ust", "cikti__uretim_birimi")
        .prefetch_related(
            Prefetch("girdiler", queryset=OperasyonGirdi.objects.filter(silindi=False)
                     .select_related("girdi__kategori__ust", "girdi__uretim_birimi").order_by("sira", "pk")),
            Prefetch("yan_ciktilar", queryset=OperasyonYanCikti.objects.filter(silindi=False)
                     .select_related("stok__kategori__ust", "stok__uretim_birimi").order_by("sira", "pk")))
        .order_by("cikti__kod"))
    return Graf(ops)


# --- ortak yardımcılar ------------------------------------------------------------------------------------------------------

def kategori_etiketi(stok) -> str:
    k = stok.kategori
    return f"{k.ust.ad} › {k.ad}" if k.ust_id else k.ad


def _grupla(satirlar, anahtar_stok):
    """satirlar → [{"kategori": etiket, "satirlar": [...]}], kategori adına, sonra stok koduna göre sıralı."""
    gruplar = {}
    for s in satirlar:
        gruplar.setdefault(kategori_etiketi(anahtar_stok(s)), []).append(s)
    return [{"kategori": k, "satirlar": sorted(v, key=lambda s: anahtar_stok(s).kod)} for k, v in sorted(gruplar.items())]


def urun_secenekleri(graf: Graf):
    """Ürün seçici için gruplu seçenekler: [(grup adı, [(pk, "kod  ad"), ...]), ...] — Bitmiş ürünler (A / Çift çıkış / Diğer), Ara parçalar."""
    kokler = graf.kokler()
    kok_idler = {s.pk for s in kokler}
    gruplar = []
    for seri in SERI_SIRA:
        uyeler = [(s.pk, f"{s.kod}  {s.ad}") for s in kokler if seri_of(s.kod) == seri]
        if uyeler:
            gruplar.append((f"Bitmiş ürünler – {SERI_AD[seri]}", uyeler))
    ara = sorted((op.cikti for op in graf.operasyonlar if op.cikti_id not in kok_idler), key=lambda s: s.kod)
    if ara:
        gruplar.append(("Ara parçalar", [(s.pk, f"{s.kod}  {s.ad}") for s in ara]))
    return gruplar


def eldeki_haritasi(stok_idler, depo=None) -> dict:
    """{stok_id: eldeki} — TEK gruplu sorgu; ``depo`` verilirse yalnız o depo, yoksa tüm depolar."""
    qs = StokHareket.objects.filter(stok_id__in=list(stok_idler), silindi=False)
    if depo is not None:
        qs = qs.filter(depo=depo)
    sonuc = {}
    for r in qs.values("stok_id", "tur").annotate(t=Sum("miktar")):
        fark = r["t"] if r["tur"] == StokHareket.Tur.GIRIS else -r["t"]
        sonuc[r["stok_id"]] = sonuc.get(r["stok_id"], SIFIR) + fark
    return sonuc


def depolar():
    return list(Depo.objects.filter(silindi=False).order_by("kod"))


# --- 1. ÜRÜN görünümü: Malzeme ve Stok · Maliyet ---------------------------------------------------------------------------

def urun_malzeme(graf: Graf, urun, miktar, depo=None) -> dict:
    """Ağacın yaprakları toplanmış düz liste: Gerekli (TAM BOY hesabıyla — ``ihtiyac_hesapla`` varsayılan sonucu) | Eldeki | Eksik."""
    sonuc = ihtiyac_hesapla([(urun, miktar)], graf=graf)
    yapraklar = [o for o in sonuc["ozet"] if o["yaprak"]]
    eldeki = eldeki_haritasi([o["stok"].pk for o in yapraklar], depo)
    satirlar = []
    for o in yapraklar:
        stok, gerekli = o["stok"], o["ihtiyac"]
        mevcut = eldeki.get(stok.pk, SIFIR)
        satirlar.append({"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "gerekli": gerekli,
                         "eldeki": mevcut, "eksik": max(SIFIR, gerekli - mevcut)})
    eksik_sayi = sum(1 for s in satirlar if s["eksik"] > 0)
    return {"gruplar": _grupla(satirlar, lambda s: s["stok"]), "yeterli": len(satirlar) - eksik_sayi, "eksik": eksik_sayi,
            "satir_sayisi": len(satirlar)}


def _maliyet_satiri(stok, miktar):
    ort_try, ort_usd = stok.ort_maliyet_try, stok.ort_maliyet_usd
    yok = ort_try is None
    return {"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "tuketim": miktar,
            "ort_try": ort_try, "ort_usd": ort_usd, "maliyet_yok": yok,
            "tutar_try": None if yok else miktar * ort_try,
            "tutar_usd": None if (yok or ort_usd is None) else miktar * ort_usd}


def _toplam(satirlar, alan):
    return sum((s[alan] for s in satirlar if s[alan] is not None), SIFIR)


def urun_maliyet(graf: Graf, urun, miktar) -> dict:
    """1 ADET için KESİRLİ malzeme maliyeti (TL ve USD) + seçilen miktar toplamı. Birim maliyet = hareketli ağırlıklı ortalama
    (Stok.ort_maliyet_*, stok_ortalama servisinin önbelleği). Ortalaması olmayan kalem 'maliyet yok' sayılır, toplama girmez."""
    satirlar = [_maliyet_satiri(stok, m) for stok, m in graf.yapraklar(urun).values()]
    toplam_try, toplam_usd = _toplam(satirlar, "tutar_try"), _toplam(satirlar, "tutar_usd")
    for s in satirlar:
        s["pay"] = (s["tutar_try"] / toplam_try * 100) if (s["tutar_try"] is not None and toplam_try) else None
    gruplar = _grupla(satirlar, lambda s: s["stok"])
    for g in gruplar:
        g["toplam_try"], g["toplam_usd"] = _toplam(g["satirlar"], "tutar_try"), _toplam(g["satirlar"], "tutar_usd")
        g["pay"] = (g["toplam_try"] / toplam_try * 100) if toplam_try else None
    return {"gruplar": gruplar, "toplam_try": toplam_try, "toplam_usd": toplam_usd,
            "miktar_toplam_try": toplam_try * miktar, "miktar_toplam_usd": toplam_usd * miktar, "miktar": miktar,
            "maliyetsiz": sum(1 for s in satirlar if s["maliyet_yok"]), "satir_sayisi": len(satirlar)}
