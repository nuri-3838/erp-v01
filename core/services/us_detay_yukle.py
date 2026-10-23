"""ÜS DETAY — TOPLU YÜKLEYİCİ (2026-10-14): detay sayfası (GET) için istasyon emri satırları ve eksik malzeme planı, SORGU SAYISI emir sayısından BAĞIMSIZ.

Eski yol (``uretim.istasyon_emri_gruplari`` + ``uretim.uretim_emri_eksikleri``) her istasyon emri / planlama düğümü için ayrı sorgu atıyordu (44 emirli ÜS'de ~670
sorgu). Burada ÜS'nin ihtiyacı olan veri TEK SEFERDE yüklenir (istasyon emirleri, taslak kayıtlar, tüm aktif operasyonlar + girdi/çıktı satırları, varyantlar) ve
hesaplar bellekte yapılır. Eski fonksiyonlar DEĞİŞMEDİ (Revize, liste, planlama aynen kullanır); eşdeğerlik testle kilitli (``test_us_detay_yukle``).

GÖRÜNTÜLEME YAZMAZ: planlama çözücüsünün ``stok_varyanti_bul_veya_olustur``u (eksik varyantı AÇAR) burada hiç çağrılmaz — varyant yalnız BULUNUR; bulunamazsa kaydedilmemiş
(``pk`` < 0, hiçbir stok/ayırma kaydı olamaz) bir yer tutucu ile etiket metinle gösterilir. ``kard.varsayilan_varyant`` da yazılmaz."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Prefetch, Q

from core.models import (IstasyonEmri, Operasyon, OperasyonCikti, OperasyonGirdi, OperasyonKaydi, StokVaryant, Varyant)
from core.services import planlama_varyant as pv
from core.services import uretim
from core.services import urun_agaci as urun_agaci_servis
from core.services import varyant as vs

SIFIR = Decimal("0")
DERIN_SV = ("stok", "markalama", "kilavuz", "afis", "yan_etiket")
YOK, SABIT = vs.YOK_KODU, pv.SABIT


def derin(onek: str) -> tuple:
    """select_related yolları: ``onek`` bir StokVaryant ilişkisinin adı (örn. 'cikti_varyant') → etiket için gereken tüm yollar (ek sorgu yok)."""
    return tuple(f"{onek}__{a}" for a in DERIN_SV)


class TopluOnbellek:
    """``planlama_varyant.Onbellek`` yerine: SORGUSUZ ve YAZMAZ. Varyantlar önceden yüklenmiştir; bulunamayan kombinasyon yer tutucu olur."""

    def __init__(self, y: "Yukleme"):
        self.y = y
        self.sv = {}
        self.varsayilan = {}
        self.yok = y.yok_pk
        self.genel = y.genel

    def sv_bul(self, stok, secimler: dict, kullanici=None) -> StokVaryant:
        sec = {}
        for b in vs.stok_boyutlari(stok):
            v = secimler.get(b)
            pk = getattr(v, "pk", v)
            if pk is not None and not isinstance(v, Varyant) and pk not in self.y.varyantlar:
                raise vs.VaryantHatasi("Varyant bulunamadı.")
            sec[b] = pk if pk else self.yok               # boş boyut = YOK (eski ``stok_varyanti_bul_veya_olustur`` ile aynı normalizasyon)
        anahtar = vs.anahtar_uret(stok, sec)
        k = (stok.pk, anahtar)
        if k not in self.sv:
            sv = self.y.sv_anahtar.get(k)
            if sv is None:
                sv = self.y.yer_tutucu(stok, anahtar, sec)
            self.sv[k] = sv
        return self.sv[k]

    def varsayilan_secimler(self, stok) -> dict:
        if stok.pk not in self.varsayilan:
            sv = None
            if stok.varsayilan_varyant_id:
                sv = self.y.sv_id.get(stok.varsayilan_varyant_id) or stok.varsayilan_varyant
            if sv is not None:
                self.varsayilan[stok.pk] = pv._secimler(sv)
            else:
                self.varsayilan[stok.pk] = {b: self.genel.pk for b in vs.stok_boyutlari(stok)} if self.genel else {}
        return self.varsayilan[stok.pk]


class TopluCozucu(pv.Cozucu):
    """``planlama_varyant.Cozucu`` ile AYNI mantık; tanım/girdi/çıktı/varyant aramaları önceden yüklenmiş verilerden (sorgusuz), varyant oluşturma yok."""

    def __init__(self, y: "Yukleme"):
        self.kullanici = None
        self.y = y
        self.ob = TopluOnbellek(y)
        self._dugum = {}
        self._ureten = {}
        self._donustur = {}
        self._girdiler = {}
        self._ciktilar = {}
        self._kosum = {}

    def kalem_dugumu(self, stok, varyant=None):
        if isinstance(stok, pv.Dugum):
            return stok
        if stok.varyantli:
            if varyant is None:
                varyant = self.y.standart(stok, self.ob)
            elif not isinstance(varyant, StokVaryant):
                varyant = self.y.sv_id.get(varyant) or StokVaryant.objects.select_related(*DERIN_SV).get(pk=varyant)
            if varyant.stok_id != stok.pk:
                raise pv._hata(f"{stok.kod}: seçilen varyant bu karta ait değil.")
        else:
            varyant = None
        return self.dugum(stok, varyant)

    def _ureten_op(self, stok):
        return self.y.ureten.get(stok.pk)

    def _donustur_op(self, stok):
        return self.y.donustur.get(stok.pk)

    def _tanim_girdileri(self, op):
        return self.y.girdiler[op.pk]

    def _tanim_ciktilari(self, op):
        return self.y.ciktilar[op.pk]

    def girdi_dugumleri(self, op, cikti_varyant) -> list:
        """``pv.girdi_varyanti`` ile aynı karar (ureten_operasyon sorgusu yerine yüklenmiş harita)."""
        sonuc = []
        for g in self._tanim_girdileri(op):
            girdi = g.girdi
            if girdi.varyantli:
                if g.varyant_kurali == SABIT:
                    sv = pv.sabit_varyant(girdi, g.sabit_varyant_id, self.kullanici, self.ob)
                else:
                    sv = pv.tasi_varyant(girdi, cikti_varyant, self.kullanici, self.ob)
                if pv.hepsi_yok_mu(sv, self.ob) and self._ureten_op(girdi) is None:
                    continue
            else:
                sv = None
            sonuc.append(pv._Girdi(g, self.dugum(girdi, sv)))
        return sonuc


class Paylasim:
    """ÜS'ler ARASI ortak veri (detayda tek ÜS, listede tüm açık ÜS'ler için bir kez yüklenir): tüm aktif operasyonlar + girdi/çıktı satırları, varyantlar ve
    sorgusuz/yazmayan ``TopluCozucu``. ``ek_operasyon_idleri``: silinmiş olsa bile emirlerin başvurduğu operasyonlar."""

    def __init__(self, ek_operasyon_idleri=()):
        self._sahte = 0
        self.ops = {op.pk: op for op in
                    Operasyon.objects.filter(Q(silindi=False) | Q(pk__in=list(ek_operasyon_idleri))).select_related("istasyon", "cikti", "cikti_sabit_varyant")
                    .prefetch_related(
                        Prefetch("girdiler", queryset=OperasyonGirdi.objects.filter(silindi=False).select_related("girdi__varsayilan_varyant", "sabit_varyant")
                                 .order_by("sira", "pk")),
                        Prefetch("ciktilar", queryset=OperasyonCikti.objects.filter(silindi=False).select_related("stok").order_by("sira", "pk")))
                    .order_by("pk")}
        self.girdiler = {pk: [g for g in op.girdiler.all() if not g.silindi] for pk, op in self.ops.items()}
        self.ciktilar = {pk: uretim.tanim_ciktilari(op) for pk, op in self.ops.items()}
        self.ureten, self.donustur = {}, {}
        for pk, op in self.ops.items():
            if op.silindi:
                continue
            for c in self.ciktilar[pk]:
                if c.surucu:
                    self.ureten.setdefault(c.stok_id, op)
            if op.tur == Operasyon.Tur.DONUSTUR:
                self.donustur.setdefault(op.cikti_id, op)
        # varyantlar
        self.varyantlar = {v.pk: v for v in Varyant.objects.filter(silindi=False)}
        self.yok_pk = next((v.pk for v in self.varyantlar.values() if v.kod == YOK), None)
        self.genel = next((v for v in self.varyantlar.values() if v.varsayilan), None)
        self.sv_id, self.sv_anahtar = {}, {}
        for sv in StokVaryant.objects.filter(silindi=False).select_related(*DERIN_SV):
            self.sv_id[sv.pk] = sv
            self.sv_anahtar[(sv.stok_id, sv.anahtar)] = sv
        self.cozucu = TopluCozucu(self)

    # --- varyant yardımcıları (YAZMAZ) ---
    def yer_tutucu(self, stok, anahtar: str, sec: dict) -> StokVaryant:
        """Bulunamayan kombinasyon: kaydedilmemiş, ``pk`` < 0 StokVaryant (etiket metinle gösterilir; hiçbir stok/ayırma kaydı olamaz)."""
        self._sahte -= 1
        standart = bool(self.genel) and all(sec.get(b) == self.genel.pk for b in vs.stok_boyutlari(stok))
        sv = StokVaryant(pk=self._sahte, stok=stok, anahtar=anahtar, standart=standart)
        for b in vs.stok_boyutlari(stok):
            setattr(sv, pv._ALAN[b], self.varyantlar.get(sec.get(b)))
        return sv

    def standart(self, stok, ob):
        """Kartın standart varyantı (tüm boyutlar varsayılan değer) — BULUNUR, açılmaz; yoksa yer tutucu. Varsayılan Varyant tanımsızsa None."""
        if self.genel is None or not stok.varyantli:
            return None
        return ob.sv_bul(stok, {b: self.genel.pk for b in vs.stok_boyutlari(stok)})


class EmirVerisi:
    """Bir ÜS'nin ``uretim_emri_eksikleri`` için girdileri (``self.emir`` ve ``self.ieler`` gerekir; aynı veri, sorgusuz)."""

    def kalem_listesi(self) -> list:
        return [k for k in self.emir.kalemler.all() if not k.silindi]

    def emir_kalemleri(self) -> list:
        return [(k.hedef_urun, k.hedef_miktar, k.varyant if self.emir.varyantli_plan else None) for k in self.kalem_listesi()]

    def ie_aktif(self) -> list:
        return [ie for ie in self.ieler if ie.durum != IstasyonEmri.Durum.IPTAL]

    def haric_ciktilar(self) -> set:
        return {ie.operasyon.cikti_id for ie in self.ie_aktif()}


class Yukleme(EmirVerisi, Paylasim):
    """Bir ÜS'nin detay ekranı için tüm veri; ``gruplar`` ve ``eksikler`` eski fonksiyonlarla AYNI biçimde sonuç verir."""

    def __init__(self, emir, ayirmalar=None):
        self.emir = emir
        self.ayirmalar = ayirmalar
        # 1) istasyon emirleri (üretim sırasıyla; etiket için varyant derin yüklü) ve taslak kayıtlar
        self.ieler = list(emir.istasyon_emirleri.filter(silindi=False)
                          .select_related("istasyon__fasoncu", *derin("cikti_varyant"))
                          .order_by("istasyon__sira", "istasyon__kod", "-seviye", "pk"))
        self.taslak = {}
        for k in OperasyonKaydi.objects.filter(istasyon_emri__uretim_emri=emir, silindi=False, durum=OperasyonKaydi.Durum.TASLAK):
            self.taslak.setdefault(k.istasyon_emri_id, []).append(k)
        # 2) ortak veri (operasyonlar + varyantlar)
        Paylasim.__init__(self, {ie.operasyon_id for ie in self.ieler} | {k.operasyon_id for ks in self.taslak.values() for k in ks})
        for ie in self.ieler:
            ie.operasyon = self.ops[ie.operasyon_id]
        for ks in self.taslak.values():
            for k in ks:
                k.operasyon = self.ops[k.operasyon_id]

    # --- ÜS detayı istasyon emri grupları (uretim.istasyon_emri_gruplari ile aynı sonuç) ---
    def _acik_kalan(self, ie) -> Decimal:
        acik = SIFIR
        for k in self.taslak.get(ie.pk, ()):
            acik += uretim.kayit_calistirma(k)
        return ie.planlanan - ie.tamamlanan - acik

    def satir(self, ie, hatalar=None) -> dict:
        op = ie.operasyon
        tanim = self.ciktilar[op.pk]
        ref = tanim[0].miktar if tanim else op.cikti_miktar
        acilabilir = self.emir.durum == self.emir.Durum.ACIK and ie.durum != IstasyonEmri.Durum.IPTAL
        bitti = ie.durum == IstasyonEmri.Durum.BITTI
        return {"ie": ie, "ref": ref, "planlanan": ie.planlanan * ref, "tamamlanan": ie.tamamlanan * ref,
                "acik": max(self._acik_kalan(ie), SIFIR) * ref,
                "uretilebilir": acilabilir and not ie.istasyon.fason and not bitti, "hata": (hatalar or {}).get(str(ie.pk)),
                "fason_acilabilir": acilabilir and ie.istasyon.fason and not bitti,
                "varyant": vs.varyant_kisa(ie.cikti_varyant) if ie.cikti_varyant_id else "", "fazla_yasak": uretim.fazla_uretim_yasak(ie)}

    def gruplar(self, hatalar=None) -> list:
        gruplar, sira = {}, []
        for ie in self.ieler:
            g = gruplar.get(ie.istasyon_id)
            if g is None:
                g = gruplar[ie.istasyon_id] = {"istasyon": ie.istasyon, "satirlar": [], "toplam": 0, "bitti": 0, "iptal": 0, "acik_adet": 0, "fason": ie.istasyon.fason}
                sira.append(g)
            s = self.satir(ie, hatalar)
            g["satirlar"].append(s)
            g["toplam"] += 1
            g["bitti"] += ie.durum == IstasyonEmri.Durum.BITTI
            g["iptal"] += ie.durum == IstasyonEmri.Durum.IPTAL
            g["acik_adet"] += bool(s["uretilebilir"] and s["acik"] > 0)
        for g in sira:
            g["uretilebilir"] = g["acik_adet"] > 0 and not g["fason"]
            g["tamam"] = g["bitti"] + g["iptal"] == g["toplam"]
        return sira

    def eksikler(self) -> list:
        """Eksik malzeme. Kapalı (kart bazlı) planda ürün ağacı grafı TEK seferde yüklenir (3 sorgu; liste ekranıyla aynı yol); varyantlı planda ``TopluCozucu``."""
        return self.eksik_ve_tablo()[0]

    def eksik_ve_tablo(self) -> tuple:
        """(eksikler, tablo) — TEK planlama çağrısı (``uretim.uretim_emri_eksik_hesabi``); ikinci çağrıda önbellekten."""
        if getattr(self, "_eksik_tablo", None) is None:
            graf = None if self.emir.varyantli_plan else urun_agaci_servis.graf_yukle()
            self._eksik_tablo = uretim.uretim_emri_eksik_hesabi(self.emir, graf=graf, yukle=self)
        return self._eksik_tablo
