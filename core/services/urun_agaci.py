"""ÜRETİM > Ürün Ağacı görünümleri (Ürün · Nerede kullanılıyor · Karşılaştır) — SALT-OKUNUR hesap katmanı.

Ayrı ürün ağacı tablosu YOK (bkz. e3e9053): her şey mevcut Operasyon zincirinden, ``uretim.ihtiyac_hesapla`` motoruyla türetilir.
Tüm operasyon/girdi/yan çıktı/stok-maliyet bilgisi ``graf_yukle()`` ile TEK seferde bellekte toplanır (sabit sorgu sayısı); sonraki
bütün hesaplar bellekte yürür. Maliyet/karşılaştırma/nerede-kullanılıyor için tüketim KESİRLİ (tam boy yuvarlaması YOK) ve yan çıktılı
kesimde girdi, ana çıktının boy payı (``uretim.ana_cikti_payi``) oranında düşülür — hepsi aynı ``birim_tuketim`` fonksiyonundan geçer, bu
yüzden Ürün, Nerede kullanılıyor ve Karşılaştır ekranlarındaki rakamlar birbiriyle birebir aynıdır.
Maliyet görünümlerinde malzemenin yanına İŞLEM / FASON BEDELİ gelir (``fason_bedeli``): ağaçtaki her üretilen çıktı için birim ihtiyaç adedi ×
fasoncunun seçilen tarihte geçerli ``FasonFiyat``ı — malzeme hesabına dokunmaz, ayrı grup + ayrı toplam, genel toplam ikisinin toplamıdır."""
from __future__ import annotations

import datetime
from collections import deque
from decimal import Decimal

from django.db.models import Prefetch, Sum
from django.utils import timezone

from core.models import Cari, Depo, FasonFiyat, Kur, Operasyon, OperasyonCikti, OperasyonGirdi, Stok, StokHareket
from core.sayi import format_tr, yuvarla
from core.services.stok_ortalama import fiyatsiz_girisli_stoklar
from core.services.uretim import ihtiyac_hesapla, tanim_ciktilari

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

    def __init__(self, operasyonlar, bugun=None):
        self.operasyonlar = operasyonlar
        self.bugun = bugun
        self._kurlar = None
        self._fiyatsiz = None
        self.ciktilar = {op.pk: tanim_ciktilari(op) for op in operasyonlar}   # operasyon pk -> çıktı satırları (sıra 0 referans)
        self.op_of = {c.stok_id: op for op in operasyonlar for c in self.ciktilar[op.pk] if c.surucu}   # stok pk -> üreten operasyon
        self.girdiler = {op.pk: list(op.girdiler.all()) for op in operasyonlar}
        self.yanlar = {op.pk: [c for c in self.ciktilar[op.pk] if c.sira != 0] for op in operasyonlar}   # ek çıktılar (ÜRET: yan)
        self.stoklar = {}                                                      # stok pk -> Stok (zincirdeki her stok)
        self.kullanan = {}                                                     # stok pk -> [girdi olarak kullanan operasyon]
        self.yan_ureten = {}                                                   # stok pk -> [yan çıktı (sürücü olmayan) olarak üreten operasyon]
        for op in operasyonlar:
            self.stoklar[op.cikti_id] = op.cikti
            for g in self.girdiler[op.pk]:
                self.stoklar.setdefault(g.girdi_id, g.girdi)
                self.kullanan.setdefault(g.girdi_id, []).append(op)
            for c in self.ciktilar[op.pk]:
                self.stoklar.setdefault(c.stok_id, c.stok)
                if not c.surucu:
                    self.yan_ureten.setdefault(c.stok_id, []).append(op)
        self._tuketim = {}

    @property
    def kurlar(self):
        """Alış fiyatı çevrimi için TCMB kurları (``kur_haritasi``; ilk kullanımda 1 sorgu)."""
        if self._kurlar is None:
            self._kurlar = kur_haritasi(self.bugun)
        return self._kurlar

    @property
    def fiyatsiz_girisli(self):
        """Zincirdeki kartlardan fiyatsız (fatura tutarı yazılmamış) girişi olanların id kümesi (``stok_ortalama.fiyatsiz_girisli_stoklar``;
        ilk kullanımda 1 sorgu) — ``birim_maliyet`` sıfır ortalamayı bununla ayıklar."""
        if self._fiyatsiz is None:
            self._fiyatsiz = fiyatsiz_girisli_stoklar(self.stoklar)
        return self._fiyatsiz

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


def graf_yukle(bugun=None) -> Graf:
    """3 sorgu: operasyonlar (+istasyon, çıktı stoku), girdiler (+stok/kategori/birim/ortalama maliyet), çıktı satırları (OperasyonCikti)."""
    ops = list(
        Operasyon.objects.filter(silindi=False)
        .select_related("istasyon", "cikti__kategori__ust", "cikti__uretim_birimi")
        .prefetch_related(
            Prefetch("girdiler", queryset=OperasyonGirdi.objects.filter(silindi=False)
                     .select_related("girdi__kategori__ust", "girdi__uretim_birimi").order_by("sira", "pk")),
            Prefetch("ciktilar", queryset=OperasyonCikti.objects.filter(silindi=False)
                     .select_related("stok__kategori__ust", "stok__uretim_birimi").order_by("sira", "pk")))
        .order_by("cikti__kod"))
    return Graf(ops, bugun)


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
    ara = sorted((graf.stoklar[pk] for pk in graf.op_of if pk not in kok_idler), key=lambda s: s.kod)     # PARÇALA'nın her çıktısı dahil
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


KUR_GERI_GUN = 7
KAYNAK_AD = {"ORTALAMA": "Ortalama", "KART": "Alış fiyatı (kart)", "YOK": "Yok"}


def kur_haritasi(bugun=None) -> dict:
    """{"USD": (kur, tarih), "EUR": ..., "GBP": ...}: ``bugun``e (yoksa en çok 7 gün önceye kadar en yakın güne) göre EN GÜNCEL TCMB döviz
    ALIŞ kuru (``kur_degerleme.kur_bul`` ile aynı kaydırmalı tarih mantığı; TEK sorgu). Kuru bulunamayan para birimi sözlükte yoktur."""
    bugun = bugun or timezone.localdate()
    haritasi = {}
    for k in (Kur.objects.filter(silindi=False, tarih__lte=bugun, tarih__gte=bugun - datetime.timedelta(days=KUR_GERI_GUN))
              .order_by("-tarih")):
        for pb in ("USD", "EUR", "GBP"):
            deger = k.deger(pb, Cari.KurTipi.MB_ALIS)
            if deger and pb not in haritasi:
                haritasi[pb] = (deger, k.tarih)
    return haritasi


ORTALAMA_NOTU = "stokta maliyetsiz giriş var"


def birim_maliyet(stok, kurlar, fiyatsiz_girisli=frozenset()) -> dict:
    """Bir stoğun BİRİM maliyeti — Ürün/Maliyet, Karşılaştır ve Excel'in TEK kaynağı. Öncelik:
      a) hareketli ağırlıklı ortalama (Stok.ort_maliyet_try/usd — stok_ortalama servisinin önbelleği; TL ve USD ayrı) → "ORTALAMA".
         Ortalama yalnız fiyatlanmış girişlerden gelmişse geçerlidir: ortalama 0 ve kartta fiyatsız giriş varsa (irsaliye fatura bekliyor /
         manuel açılış — ``fiyatsiz_girisli``: fiyatsız girişi olan kart id kümesi, ``Graf.fiyatsiz_girisli``) ortalama SAYILMAZ, b)'ye
         düşülür ve ``ortalama_notu`` = "stokta maliyetsiz giriş var". Ortalama > 0 (fiyatlı + fiyatsız karışık) mevcut ortalamadır.
      b) stok kartındaki alış fiyatı (+ para birimi), en güncel TCMB kuruyla çevrilir → "KART":
           TRY: TL = fiyat, USD = fiyat / USD · USD: USD = fiyat, TL = fiyat × USD · EUR/GBP: TL = fiyat × kur, USD = TL / USD
         (gereken kur bulunamazsa yanlış rakam üretmek yerine "YOK" + uyarı)
      c) ikisi de yoksa → "YOK".
    Döner: {"kaynak", "try", "usd", "uyari", "kurlar": kullanılan para birimleri, "ortalama_notu"}. Birim tutarlar 6 ondalık, ROUND_HALF_UP.
    Yalnız GÖSTERİM kuralıdır: stok değerleme / mizan / ortalama motoru buna bakmaz."""
    ort = stok.ort_maliyet_try
    notu = ""
    if ort is not None:
        if ort > 0 or stok.pk not in fiyatsiz_girisli:
            return {"kaynak": "ORTALAMA", "try": ort, "usd": stok.ort_maliyet_usd, "uyari": "", "kurlar": (), "ortalama_notu": ""}
        notu = ORTALAMA_NOTU
    fiyat = stok.alis_fiyati
    if fiyat:
        pb = stok.alis_fiyati_pb or "TRY"
        tl, usd, gerekli, eksik = doviz_cevir(fiyat, pb, kurlar)
        if eksik:
            return {"kaynak": "YOK", "try": None, "usd": None, "kurlar": (), "ortalama_notu": notu,
                    "uyari": f"{stok.kod}: alış fiyatı ({pb}) çevrilemedi — {', '.join(eksik)} için TCMB kuru bulunamadı"}
        return {"kaynak": "KART", "try": tl, "usd": usd, "uyari": "", "kurlar": gerekli, "ortalama_notu": notu}
    return {"kaynak": "YOK", "try": None, "usd": None, "uyari": "", "kurlar": (), "ortalama_notu": notu}


def doviz_cevir(fiyat, pb, kurlar):
    """``pb`` para birimindeki birim fiyatın TL ve USD karşılığı — kart alış fiyatı (``birim_maliyet``) ve fason fiyatının ORTAK kuralı:
      TRY: TL = fiyat, USD = fiyat / USD · USD: USD = fiyat, TL = fiyat × USD · EUR/GBP: TL = fiyat × kur, USD = TL / USD (6 ondalık, ROUND_HALF_UP).
    Döner: (tl, usd, gereken para birimleri, eksik kurlar) — gereken kur yoksa tl/usd None (yanlış rakam üretmek yerine)."""
    pb = pb or "TRY"
    gerekli = ("USD",) if pb in ("TRY", "USD") else (pb, "USD")
    eksik = [x for x in gerekli if x not in kurlar]
    if eksik:
        return None, None, gerekli, eksik
    usd_kur = kurlar["USD"][0]
    if pb == "TRY":
        return fiyat, yuvarla(fiyat / usd_kur, 6), gerekli, []
    if pb == "USD":
        return yuvarla(fiyat * usd_kur, 6), fiyat, gerekli, []
    tl = yuvarla(fiyat * kurlar[pb][0], 6)
    return tl, yuvarla(tl / usd_kur, 6), gerekli, []


def kur_notu(kurlar, kullanilan, baslik="Alış fiyatı çevrimi") -> str:
    """Çevrimde kullanılan kurların küçük notu: "Alış fiyatı çevrimi: TCMB 09.10.2026, USD 41,2345" (fason için ``baslik`` değişir)."""
    pbler = [pb for pb in ("USD", "EUR", "GBP") if pb in kullanilan and pb in kurlar]
    if not pbler:
        return ""
    tarihler = {kurlar[pb][1] for pb in pbler}
    if len(tarihler) == 1:
        return f"{baslik}: TCMB {next(iter(tarihler)):%d.%m.%Y}, " + ", ".join(f"{pb} {format_tr(kurlar[pb][0], 4)}" for pb in pbler)
    return f"{baslik}: " + ", ".join(f"{pb} {format_tr(kurlar[pb][0], 4)} (TCMB {kurlar[pb][1]:%d.%m.%Y})" for pb in pbler)


def _maliyet_satiri(stok, miktar, kurlar, fiyatsiz_girisli=frozenset()):
    b = birim_maliyet(stok, kurlar, fiyatsiz_girisli)
    yok = b["try"] is None
    return {"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "tuketim": miktar,
            "ort_try": b["try"], "ort_usd": b["usd"], "kaynak": b["kaynak"], "uyari": b["uyari"], "kurlar": b["kurlar"],
            "ortalama_notu": b["ortalama_notu"], "maliyet_yok": yok,
            "tutar_try": None if yok else miktar * b["try"],
            "tutar_usd": None if (yok or b["usd"] is None) else miktar * b["usd"]}


def _toplam(satirlar, alan):
    return sum((s[alan] for s in satirlar if s[alan] is not None), SIFIR)


def urun_maliyet(graf: Graf, urun, miktar, *, tarih=None, fasoncu_id=None) -> dict:
    """1 ADET için KESİRLİ malzeme maliyeti (TL ve USD ayrı) + seçilen miktar toplamları (kalem, kategori ara toplamı ve malzeme toplamı
    için de). Birim maliyet: ``birim_maliyet`` (ortalama > kart alış fiyatı > yok). Maliyeti olmayan kalem toplama girmez.
    Malzemenin yanında ikinci grup: ``fason`` (``fason_bedeli`` — üretilen çıktı başına birim ihtiyaç × fason fiyatı; ``tarih``/``fasoncu_id``
    seçimi) ve ``genel_*`` = malzeme + işlem/fason. Malzeme anahtarları (``toplam_try``, ``gruplar``, ``pay``…) fason eklenince DEĞİŞMEZ."""
    kurlar = graf.kurlar
    satirlar = [_maliyet_satiri(stok, m, kurlar, graf.fiyatsiz_girisli) for stok, m in graf.yapraklar(urun).values()]
    toplam_try, toplam_usd = _toplam(satirlar, "tutar_try"), _toplam(satirlar, "tutar_usd")
    for s in satirlar:
        s["pay"] = (s["tutar_try"] / toplam_try * 100) if (s["tutar_try"] is not None and toplam_try) else None
        s["tutar_try_n"] = None if s["tutar_try"] is None else s["tutar_try"] * miktar
        s["tutar_usd_n"] = None if s["tutar_usd"] is None else s["tutar_usd"] * miktar
    gruplar = _grupla(satirlar, lambda s: s["stok"])
    for g in gruplar:
        g["toplam_try"], g["toplam_usd"] = _toplam(g["satirlar"], "tutar_try"), _toplam(g["satirlar"], "tutar_usd")
        g["toplam_try_n"], g["toplam_usd_n"] = g["toplam_try"] * miktar, g["toplam_usd"] * miktar
        g["pay"] = (g["toplam_try"] / toplam_try * 100) if toplam_try else None
    kart = [s for s in satirlar if s["kaynak"] == "KART"]
    kart_try = _toplam(kart, "tutar_try")
    kullanilan = {pb for s in kart for pb in s["kurlar"]}
    fb = fason_bedeli(graf, [urun], tarih=tarih, fasoncu_id=fasoncu_id)
    fason = {**fb["urunler"][urun.pk], "tarih": fb["tarih"], "fasoncular": fb["fasoncular"], "fasoncu": fb["fasoncu"],
             "kur_notu": fb["kur_notu"], "kur_uyarilari": fb["kur_uyarilari"]}
    for s in fason["satirlar"]:
        s["tutar_try_n"] = None if s["tutar_try"] is None else s["tutar_try"] * miktar
        s["tutar_usd_n"] = None if s["tutar_usd"] is None else s["tutar_usd"] * miktar
    fason["toplam_try_n"], fason["toplam_usd_n"] = fason["toplam_try"] * miktar, fason["toplam_usd"] * miktar
    genel_try, genel_usd = toplam_try + fason["toplam_try"], toplam_usd + fason["toplam_usd"]
    return {"gruplar": gruplar, "toplam_try": toplam_try, "toplam_usd": toplam_usd,
            "miktar_toplam_try": toplam_try * miktar, "miktar_toplam_usd": toplam_usd * miktar, "miktar": miktar,
            "maliyetsiz": sum(1 for s in satirlar if s["maliyet_yok"]), "satir_sayisi": len(satirlar),
            "ortalama_sayi": sum(1 for s in satirlar if s["kaynak"] == "ORTALAMA"), "kart_sayi": len(kart),
            "kart_notlu": sum(1 for s in satirlar if s["ortalama_notu"]),            # ortalama 0 + fiyatsız giriş → ortalama kullanılmadı
            "kart_pay": (kart_try / toplam_try * 100) if toplam_try else None,
            "usd_eksik": sum(1 for s in satirlar if not s["maliyet_yok"] and s["tutar_usd"] is None),
            "kur_notu": kur_notu(kurlar, kullanilan),
            "kur_uyarilari": sorted({s["uyari"] for s in satirlar if s["uyari"]}),
            "fason": fason, "genel_try": genel_try, "genel_usd": genel_usd,
            "genel_try_n": genel_try * miktar, "genel_usd_n": genel_usd * miktar}


# --- İŞLEM / FASON BEDELİ (Ürün/Maliyet, Karşılaştır, Excel'in ortak kaynağı) ------------------------------------------------
# Ağaçtaki her ÜRETİLEN çıktı (operasyonu olan stok — ÜRET'in ana çıktısı, PARÇALA'nın ihtiyaç duyulan HER çıktısı; ürünün kendisi dahil)
# için: birim ihtiyaç adedi (kesirli, ``birim_tuketim`` ile aynı rakam) × fasoncunun ``tarih``te geçerli fason fiyatı. Fiyat seçimi
# ``fason.gecerli_fiyat`` kuralıyla (başlangıcı tarihi geçmeyen en son aktif satır) ama TEK sorguda; döviz fiyat TCMB alış kuruyla TL'ye
# çevrilir (``kur_haritasi`` — ``kur_degerleme.kur_bul`` ile aynı 7 gün geriye bakan tarih kuralı). Fiyatı olmayan üretilen parça listelenmez,
# sayısı verilir (iç üretim parçaları için normal). Malzeme hesabına hiç dokunmaz.

def uretilen_ciktilar(graf: Graf, urun) -> list:
    """1 adet ``urun`` için ağaçtaki üretilen çıktılar: [(stok, birim ihtiyaç adedi)] — ürünün kendisi (operasyonu varsa, 1 adet) + zincirde
    geçilen operasyonlu her stok (PARÇALA'da ihtiyaç duyulan her çıktı kendi satırıyla)."""
    satirlar = [(urun, BIR)] if urun.pk in graf.op_of else []
    satirlar += [(o["stok"], o["ihtiyac"]) for o in graf.birim_tuketim(urun).values() if not o["yaprak"]]
    return satirlar


def fason_fiyat_haritasi(stok_idler, tarih) -> dict:
    """{cari pk: {stok pk: FasonFiyat}} — ``tarih``te geçerli fason fiyatı (başlangıcı tarihi GEÇMEYEN en son aktif satır; ``fason.gecerli_fiyat``
    ile aynı kural) fasoncu başına, TEK sorguda. Silinmiş cari/fiyat ve pasif fiyat dışarıda."""
    harita = {}
    if not stok_idler:
        return harita
    qs = (FasonFiyat.objects.filter(silindi=False, aktif=True, cari__silindi=False, stok_id__in=list(stok_idler), gecerlilik_baslangic__lte=tarih)
          .select_related("cari").order_by("cari_id", "stok_id", "-gecerlilik_baslangic", "-pk"))
    for f in qs:
        harita.setdefault(f.cari_id, {}).setdefault(f.stok_id, f)       # ilk gelen = en son başlangıçlı
    return harita


def fasoncu_sec(harita, fasoncu_id=None):
    """(seçenekler, seçili fasoncu): fiyatı olan fasoncular — ağaçtaki en çok parçayı fiyatlayan önce (eşitlikte unvan); ``fasoncu_id``
    listedeyse o, değilse ilk seçenek (tek fasoncu varsa o; hiç yoksa None). Seçenek: {"cari", "sayi"}."""
    secenekler = sorted(({"cari": next(iter(f.values())).cari, "sayi": len(f)} for f in harita.values()),
                        key=lambda s: (-s["sayi"], s["cari"].unvan, s["cari"].pk))
    secili = next((s["cari"] for s in secenekler if s["cari"].pk == fasoncu_id), secenekler[0]["cari"] if secenekler else None)
    return secenekler, secili


def _fason_satiri(stok, ihtiyac, fiyat, kurlar):
    tl, usd, gerekli, eksik = doviz_cevir(fiyat.birim_fiyat, fiyat.para_birimi, kurlar)
    uyari = f"{stok.kod}: fason fiyatı ({fiyat.para_birimi}) çevrilemedi — {', '.join(eksik)} için TCMB kuru bulunamadı" if eksik else ""
    return {"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "tuketim": ihtiyac,
            "fiyat": fiyat.birim_fiyat, "pb": fiyat.para_birimi, "fasoncu_kodu": fiyat.fasoncu_kodu, "gecerlilik": fiyat.gecerlilik_baslangic,
            "birim_try": tl, "birim_usd": usd, "maliyet_yok": tl is None, "uyari": uyari, "kurlar": () if eksik else gerekli,
            "tutar_try": None if tl is None else ihtiyac * tl, "tutar_usd": None if usd is None else ihtiyac * usd}


def fason_bedeli(graf: Graf, urunler, *, tarih=None, fasoncu_id=None) -> dict:
    """Ürün(ler) için işlem/fason bedeli (1 adet ürün başına). ``tarih`` yoksa grafın günü (bugün); ``fasoncu_id`` yoksa ``fasoncu_sec`` varsayılanı.
    Döner: {"tarih", "fasoncular" (seçenekler), "fasoncu", "urunler": {ürün pk: {"satirlar" (fiyatı olan çıktılar), "toplam_try", "toplam_usd",
    "uretilen" (üretilen çıktı sayısı), "fiyatli", "fiyatsiz" (fason fiyatı olmayan üretilen parça), "kursuz", "usd_eksik"}}, "kur_notu",
    "kur_uyarilari"}. Kuru bulunamayan döviz fiyat listelenir ama toplama girmez (uyarı)."""
    tarih = tarih or graf.bugun or timezone.localdate()
    uretilen = {u.pk: uretilen_ciktilar(graf, u) for u in urunler}
    harita = fason_fiyat_haritasi({s.pk for sat in uretilen.values() for s, _ in sat}, tarih)
    secenekler, fasoncu = fasoncu_sec(harita, fasoncu_id)
    fiyatlar = harita.get(fasoncu.pk, {}) if fasoncu else {}
    kurlar = {}
    if fiyatlar:
        kurlar = graf.kurlar if tarih == (graf.bugun or timezone.localdate()) else kur_haritasi(tarih)
    sonuc = {}
    for u in urunler:
        satirlar = [_fason_satiri(s, m, fiyatlar[s.pk], kurlar) for s, m in uretilen[u.pk] if s.pk in fiyatlar]
        sonuc[u.pk] = {"satirlar": satirlar, "toplam_try": _toplam(satirlar, "tutar_try"), "toplam_usd": _toplam(satirlar, "tutar_usd"),
                       "uretilen": len(uretilen[u.pk]), "fiyatli": len(satirlar), "fiyatsiz": len(uretilen[u.pk]) - len(satirlar),
                       "kursuz": sum(1 for s in satirlar if s["maliyet_yok"]),
                       "usd_eksik": sum(1 for s in satirlar if not s["maliyet_yok"] and s["tutar_usd"] is None)}
    tum = [s for v in sonuc.values() for s in v["satirlar"]]
    return {"tarih": tarih, "fasoncular": secenekler, "fasoncu": fasoncu, "urunler": sonuc,
            "kur_notu": kur_notu(kurlar, {pb for s in tum for pb in s["kurlar"]}, "Fason fiyatı çevrimi"),
            "kur_uyarilari": sorted({s["uyari"] for s in tum if s["uyari"]})}


# --- 2. NEREDE KULLANILIYOR ---------------------------------------------------------------------------------------------------

def kullanim_secenekleri(graf: Graf):
    """Zincirdeki her stok (hammadde + ara parça + bitmiş ürün), gruplu: Hammadde/hazır stok · Ara parçalar."""
    kok_idler = {s.pk for s in graf.kokler()}
    hazir = sorted((s for pk, s in graf.stoklar.items() if pk not in graf.op_of), key=lambda s: s.kod)
    ara = sorted((s for pk, s in graf.stoklar.items() if pk in graf.op_of and pk not in kok_idler), key=lambda s: s.kod)
    gruplar = []
    if hazir:
        gruplar.append(("Hammadde / hazır stok", [(s.pk, f"{s.kod}  {s.ad}") for s in hazir]))
    if ara:
        gruplar.append(("Ara parçalar", [(s.pk, f"{s.kod}  {s.ad}") for s in ara]))
    diger = (Stok.objects.filter(silindi=False).exclude(pk__in=list(graf.stoklar)).order_by("kod").values_list("pk", "kod", "ad"))
    diger = [(pk, f"{kod}  {ad}") for pk, kod, ad in diger]
    if diger:
        gruplar.append(("Hiçbir zincirde olmayan stoklar", diger))
    return gruplar


def stok_bul(graf: Graf, pk):
    """Zincirdeki stok (bellekten) ya da zincir dışı aktif stok (1 sorgu); yoksa None."""
    if pk in graf.stoklar:
        return graf.stoklar[pk]
    return Stok.objects.filter(pk=pk, silindi=False).select_related("uretim_birimi").first()


def _yol_bul(graf: Graf, stok, kok):
    """``stok``tan bitmiş ürüne (kok) giden EN KISA yol: [stok, ..., kok] (girdi → çıktı kenarları; BFS). Yoksa None."""
    onceki = {stok.pk: None}
    kuyruk = deque([stok.pk])
    while kuyruk:
        pk = kuyruk.popleft()
        if pk == kok.pk:
            yol = []
            while pk is not None:
                yol.append(graf.stoklar[pk])
                pk = onceki[pk]
            return list(reversed(yol))
        for op in graf.kullanan.get(pk, []):
            for c in graf.ciktilar.get(op.pk, []):                 # ÜRET: ana çıktı; PARÇALA: her (sürücü) çıktı
                if c.surucu and c.stok_id not in onceki:
                    onceki[c.stok_id] = pk
                    kuyruk.append(c.stok_id)
    return None


def nerede_kullaniliyor(graf: Graf, stok) -> dict:
    """Bu stoğu kullanan BİTMİŞ ürünler: 1 adet ürün başına KESİRLİ tüketim + yol (ara parça zinciri) + yan çıktı notu; seriye göre gruplu."""
    satirlar = []
    for kok in graf.kokler():
        if kok.pk == stok.pk:
            continue
        o = graf.birim_tuketim(kok).get(stok.pk)
        if o is None:
            continue
        yol = _yol_bul(graf, stok, kok)
        zincir_idler = set(graf.birim_tuketim(kok))
        yan = [op.cikti for op in graf.yan_ureten.get(stok.pk, []) if op.cikti_id in zincir_idler or op.cikti_id == kok.pk]
        satirlar.append({"urun": kok, "seri": seri_of(kok.kod), "tuketim": o["ihtiyac"], "yol": yol or [stok, kok],
                         "yan_cikti_ureten": yan})
    gruplar = [{"seri": s, "ad": SERI_AD[s], "satirlar": [r for r in satirlar if r["seri"] == s]} for s in SERI_SIRA]
    return {"stok": stok, "gruplar": [g for g in gruplar if g["satirlar"]], "sayi": len(satirlar),
            "bitmis_urun": stok.pk in {k.pk for k in graf.kokler()},
            "yan_cikti_ureten": [op.cikti for op in graf.yan_ureten.get(stok.pk, [])],
            "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad}


# --- 3. KARŞILAŞTIR ---------------------------------------------------------------------------------------------------------

def karsilastir_urunleri(graf: Graf, seri: str = "", urun_idler=()):
    """(ürünler, uyarı): ``seri`` A/C ise o serinin bitmiş ürünleri; yoksa elle seçilen (en çok 8) operasyonlu stoklar."""
    kokler = graf.kokler()
    uyari = ""
    if seri in SERI_ONEKI:
        return [s for s in kokler if seri_of(s.kod) == seri], uyari
    secili, gorulen = [], set()
    for pk in urun_idler:
        stok = graf.stoklar.get(pk)
        if stok is not None and stok.pk in graf.op_of and stok.pk not in gorulen:
            secili.append(stok)
            gorulen.add(stok.pk)
    if len(secili) > KARSILASTIR_EN_FAZLA:
        uyari = f"En fazla {KARSILASTIR_EN_FAZLA} ürün karşılaştırılabilir; ilk {KARSILASTIR_EN_FAZLA} ürün gösteriliyor."
        secili = secili[:KARSILASTIR_EN_FAZLA]
    return secili, uyari


def karsilastir(graf: Graf, urunler, mod="miktar", pb="TL", *, tarih=None, fasoncu_id=None) -> dict:
    """Matris: satırlar yaprak malzemeler (kategoriye göre gruplu), sütunlar ürünler; hücre = 1 adet başına KESİRLİ tüketim
    (``mod='maliyet'``: seçilen para biriminde (``pb`` TL/USD) tutar; birim maliyet ``birim_maliyet`` — Ürün/Maliyet ile aynı). Kullanılmayan
    hücre None. Maliyet modunda ürün başına toplam (``toplam`` = malzeme) + o para biriminde maliyeti olmayan kalem sayısı; ayrıca
    ``fason`` (işlem/fason bedeli satırları, ``fason_bedeli`` ile — aynı tarih/fasoncu seçimi) ve toplamda ``fason``/``genel``/``fiyatsiz``."""
    maliyet = mod == "maliyet"
    pb = "USD" if pb == "USD" else "TL"
    anahtar = "usd" if pb == "USD" else "try"
    kurlar = graf.kurlar if maliyet else {}
    yaprak_of = [graf.yapraklar(u) for u in urunler]
    stoklar = {}
    for y in yaprak_of:
        for pk, (stok, _) in y.items():
            stoklar[pk] = stok
    birim = {pk: birim_maliyet(st, kurlar, graf.fiyatsiz_girisli) for pk, st in stoklar.items()} if maliyet else {}
    satirlar = []
    for pk, stok in stoklar.items():
        hucreler = []
        for y in yaprak_of:
            if pk not in y:
                hucreler.append(None)
                continue
            miktar = y[pk][1]
            if maliyet:
                b = birim[pk]
                tutar = None if b[anahtar] is None else miktar * b[anahtar]
                hucreler.append({"miktar": miktar, "deger": tutar, "maliyet_yok": tutar is None, "kaynak": b["kaynak"]})
            else:
                hucreler.append({"miktar": miktar, "deger": miktar, "maliyet_yok": False, "kaynak": ""})
        satirlar.append({"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "hucreler": hucreler,
                         "kaynak": birim[pk]["kaynak"] if maliyet else "", "ortalama_notu": birim[pk]["ortalama_notu"] if maliyet else ""})
    toplamlar = None
    kart_kalem, kur_n, fason = 0, "", None
    if maliyet:
        toplamlar = []
        for i in range(len(urunler)):
            hs = [s["hucreler"][i] for s in satirlar if s["hucreler"][i] is not None]
            toplamlar.append({"toplam": sum((h["deger"] for h in hs if h["deger"] is not None), SIFIR),
                              "maliyetsiz": sum(1 for h in hs if h["maliyet_yok"])})
        kart = [birim[pk] for pk in stoklar if birim[pk]["kaynak"] == "KART"]
        kart_kalem = len(kart)
        kur_n = kur_notu(kurlar, {x for b in kart for x in b["kurlar"]})
        # işlem / fason bedeli: fiyatı olan üretilen çıktılar × ürünler (hücre = birim ihtiyaç × fason fiyatı, seçilen para biriminde)
        fb = fason_bedeli(graf, urunler, tarih=tarih, fasoncu_id=fasoncu_id)
        tutar_anahtar = "tutar_usd" if pb == "USD" else "tutar_try"
        fason_stoklar, hucre = {}, {}
        for i, u in enumerate(urunler):
            for s in fb["urunler"][u.pk]["satirlar"]:
                fason_stoklar[s["stok"].pk] = s["stok"]
                hucre[(s["stok"].pk, i)] = s
        fason_satirlar = []
        for spk, stok in sorted(fason_stoklar.items(), key=lambda kv: kv[1].kod):
            hucreler = []
            for i in range(len(urunler)):
                s = hucre.get((spk, i))
                if s is None:
                    hucreler.append(None)
                    continue
                hucreler.append({"miktar": s["tuketim"], "deger": s[tutar_anahtar], "maliyet_yok": s[tutar_anahtar] is None, "pb": s["pb"],
                                 "fiyat": s["fiyat"], "fasoncu_kodu": s["fasoncu_kodu"]})
            fason_satirlar.append({"stok": stok, "birim": stok.uretim_birimi.kisa_ad or stok.uretim_birimi.ad, "hucreler": hucreler})
        for i, u in enumerate(urunler):
            f = fb["urunler"][u.pk]
            toplamlar[i]["fason"] = f["toplam_usd"] if pb == "USD" else f["toplam_try"]
            toplamlar[i]["genel"] = toplamlar[i]["toplam"] + toplamlar[i]["fason"]
            toplamlar[i]["fiyatsiz"] = f["fiyatsiz"]
            toplamlar[i]["fason_eksik"] = f["kursuz"] + (f["usd_eksik"] if pb == "USD" else 0)
        fason = {"satirlar": fason_satirlar, "satir_sayisi": len(fason_satirlar), "tarih": fb["tarih"], "fasoncular": fb["fasoncular"],
                 "fasoncu": fb["fasoncu"], "kur_notu": fb["kur_notu"], "kur_uyarilari": fb["kur_uyarilari"]}
    return {"urunler": urunler, "gruplar": _grupla(satirlar, lambda s: s["stok"]), "mod": "maliyet" if maliyet else "miktar",
            "pb": pb, "satir_sayisi": len(satirlar), "toplamlar": toplamlar, "kart_kalem": kart_kalem, "kur_notu": kur_n,
            "kur_uyarilari": sorted({b["uyari"] for b in birim.values() if b["uyari"]}), "fason": fason}
