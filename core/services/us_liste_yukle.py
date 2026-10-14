"""ÜS LİSTESİ — TOPLU YÜKLEYİCİ (2026-10-14): liste ekranındaki "eksik malzeme / satınalmadan / fasondan bekleniyor / fason emri gerekli" rozetleri için her açık ÜS'nin
``uretim_emri_eksikleri`` hesabı, SORGU SAYISI ÜS sayısından BAĞIMSIZ.

Eski yol her açık ÜS için ayrı hesap (varyantlı çözücü, kullanılabilir stok, fason/satınalma okumaları, sevk) yapıyordu: 3 açık ÜS'de ~720 sorgu. Burada ortak veri
(operasyonlar, varyantlar, tüm stokların eldeki/ayrılanı, açık fason ve satınalma siparişleri, rezervler, istasyon emirleri) TEK seferde yüklenir; her ÜS'nin hesabı
bellekte, ÜS'nin kendi ayırması/rezervi çıkarılarak yapılır. ``uretim_emri_eksikleri``nin eski yolu DEĞİŞMEDİ (Revize, detay, planlama aynen kullanır); eşdeğerlik testle
kilitli (``test_us_liste_yukle``). GÖRÜNTÜLEME YAZMAZ (``us_detay_yukle.TopluCozucu``: varyant açılmaz)."""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from core.models import Fatura, FaturaSatir, IstasyonEmri, UretimEmri
from core.services import fason_siparis as fs_servis
from core.services import satinalma, stok_ayirma
from core.services.hareket import toplu_eldeki, toplu_eldeki_varyant
from core.services.us_detay_yukle import EmirVerisi, Paylasim

SIFIR = Decimal("0")


def _pk(x):
    return getattr(x, "pk", x)


class ListeYukleme(Paylasim):
    """Açık ÜS'lerin ortak verisi. ``emirler``: ``kaynak_siparis`` (select_related) ve ``kalemler`` (prefetch, varyant derin) yüklü ÜS listesi."""

    def __init__(self, emirler):
        self.emirler = [e for e in emirler if e.durum == UretimEmri.Durum.ACIK and not e.silindi]
        idler = [e.pk for e in self.emirler]
        self.ies = {}
        for ie in IstasyonEmri.objects.filter(uretim_emri_id__in=idler, silindi=False).order_by("uretim_emri_id", "pk"):
            self.ies.setdefault(ie.uretim_emri_id, []).append(ie)
        super().__init__({ie.operasyon_id for liste in self.ies.values() for ie in liste})
        for liste in self.ies.values():
            for ie in liste:
                ie.operasyon = self.ops[ie.operasyon_id]
        self.ayirmalar = {}
        for a in stok_ayirma.acik_ayirmalar().filter(uretim_emri_id__in=idler):
            self.ayirmalar.setdefault(a.uretim_emri_id, []).append(a)
        self._stok_yukle()
        self._fason_yukle(idler)
        self._sevk_yukle()
        self._sat = None

    # --- stok: eldeki ve ayrılan (hesaplanan her ÜS için kendi ayırması çıkarılır) ---
    def _stok_yukle(self):
        evren = set()
        for op in self.ops.values():
            evren.add(op.cikti_id)
            evren.update(g.girdi_id for g in self.girdiler[op.pk])
            evren.update(c.stok_id for c in self.ciktilar[op.pk])
        for e in self.emirler:
            evren.update(k.hedef_urun_id for k in e.kalemler.all())
        self.evren = evren
        self.eldeki = toplu_eldeki(evren) if evren else {}
        self.eldeki_v = toplu_eldeki_varyant(evren) if evren else {}
        self.ay_stok, self.ay_var, self.ay_stok_us, self.ay_var_us = {}, {}, {}, {}
        if evren:
            for r in stok_ayirma.acik_ayirmalar().filter(stok_id__in=evren).values("stok_id", "varyant_id", "uretim_emri_id").annotate(t=Sum("miktar")):
                s, v, u, t = r["stok_id"], r["varyant_id"], r["uretim_emri_id"], r["t"]
                self.ay_stok[s] = self.ay_stok.get(s, SIFIR) + t
                self.ay_var[(s, v)] = self.ay_var.get((s, v), SIFIR) + t
                self.ay_stok_us[(s, u)] = self.ay_stok_us.get((s, u), SIFIR) + t
                self.ay_var_us[(s, v, u)] = self.ay_var_us.get((s, v, u), SIFIR) + t

    def _ayrilan(self, stok, haric_emir) -> Decimal:
        t = self.ay_stok.get(stok, SIFIR)
        return t - self.ay_stok_us.get((stok, _pk(haric_emir)), SIFIR) if haric_emir is not None else t

    def _ayrilan_v(self, stok, varyant, haric_emir) -> Decimal:
        t = self.ay_var.get((stok, varyant), SIFIR)
        return t - self.ay_var_us.get((stok, varyant, _pk(haric_emir)), SIFIR) if haric_emir is not None else t

    def kullanilabilir(self, idler, haric_emir=None) -> dict:
        """``stok_ayirma.kullanilabilir_haritasi`` ile aynı ({stok id: eldeki − ayrılan}), sorgusuz."""
        idler = list(idler)
        if not set(idler) <= self.evren:                                   # beklenmeyen stok: eski yol (güvenli)
            return stok_ayirma.kullanilabilir_haritasi(idler, haric_emir=haric_emir)
        return {pk: self.eldeki.get(pk, SIFIR) - self._ayrilan(pk, haric_emir) for pk in idler}

    def kullanilabilir_varyant(self, dugumler, haric_emir=None) -> dict:
        """``stok_ayirma.kullanilabilir_varyant_haritasi`` ile aynı ({(stok, varyant|None): kullanılabilir}), sorgusuz."""
        dugumler = list(dugumler)
        idler = {pk for pk, _ in dugumler}
        if not idler:
            return {}
        if not idler <= self.evren:
            return stok_ayirma.kullanilabilir_varyant_haritasi(dugumler, haric_emir=haric_emir)
        sonuc = {}
        for pk, vpk in dugumler:
            if vpk is None:
                sonuc[(pk, None)] = self.eldeki.get(pk, SIFIR) - self._ayrilan(pk, haric_emir)
            else:
                sonuc[(pk, vpk)] = self.eldeki_v.get((pk, vpk), SIFIR) - self._ayrilan_v(pk, vpk, haric_emir)
        return sonuc

    # --- fason siparişi: açık kalan (ÜS'nin kendi rezervi kalan sayılır) ve ÜS rezervleri ---
    def _fason_yukle(self, idler):
        self.fs_satirlar = list(fs_servis._satirlar_qs().filter(siparis__silindi=False, siparis__durum__in=fs_servis.ACIK_DURUMLAR))
        self.fs_rez, self.fs_rez_us = {}, {}
        if self.fs_satirlar:
            for r in (fs_servis.aktif_rezervler().filter(satir_id__in=[s.pk for s in self.fs_satirlar])
                      .values("satir_id", "uretim_emri_id").annotate(t=Sum("miktar"))):
                self.fs_rez[r["satir_id"]] = self.fs_rez.get(r["satir_id"], SIFIR) + r["t"]
                self.fs_rez_us[(r["satir_id"], r["uretim_emri_id"])] = r["t"]
        self.fs_emir_rezervleri = {}
        for r in (fs_servis.aktif_rezervler().filter(uretim_emri_id__in=idler)
                  .select_related("satir__siparis__cari", "satir__stok", "satir__varyant").order_by("satir__siparis__termin", "satir__siparis__pk", "satir__sira")):
            self.fs_emir_rezervleri.setdefault(r.uretim_emri_id, []).append(r)

    def fs_harita(self, haric_emir=None) -> dict:
        """``fason_siparis.acik_kalan_haritasi`` ile aynı, sorgusuz."""
        if not self.fs_satirlar:
            return {}
        harita = {}
        for s in self.fs_satirlar:
            rezerv = self.fs_rez.get(s.pk, SIFIR)
            if haric_emir is not None:
                rezerv -= self.fs_rez_us.get((s.pk, _pk(haric_emir)), SIFIR)
            k = max(SIFIR, s.adet - s.gelen - rezerv)
            if k > 0:
                anahtar = (s.stok_id, s.varyant_id)
                harita[anahtar] = harita.get(anahtar, SIFIR) + k
        return harita

    def fs_rezervleri(self, emir) -> list:
        """``fason_siparis.emir_rezervleri`` ile aynı, sorgusuz."""
        return list(self.fs_emir_rezervleri.get(emir.pk, []))

    # --- sevk (satış faturası satırları) ---
    def _sevk_yukle(self):
        fatura_idler = {e.kaynak_siparis.fatura_id for e in self.emirler if e.kaynak_siparis_id and e.kaynak_siparis.fatura_id}
        self.fatura_silindi, self.fatura_satirlari = {}, {}
        if fatura_idler:
            self.fatura_silindi = {f.pk: f.silindi for f in Fatura.objects.filter(pk__in=fatura_idler)}
            for sat in FaturaSatir.objects.filter(fatura_id__in=fatura_idler, silindi=False, stok__isnull=False):
                self.fatura_satirlari.setdefault(sat.fatura_id, []).append(sat)

    def sevk_haritasi(self, emir) -> dict:
        """``uretim.sevk_edilen_haritasi`` ile aynı, sorgusuz."""
        sip = emir.kaynak_siparis
        if sip is None or not sip.fatura_id or self.fatura_silindi.get(sip.fatura_id, True):
            return {}
        h = {}
        for sat in self.fatura_satirlari.get(sip.fatura_id, []):
            k = (sat.stok_id, sat.varyant_id) if emir.varyantli_plan else sat.stok_id
            h[k] = h.get(k, SIFIR) + sat.miktar
        return h

    # --- satınalma: açık alış siparişlerinden gelecek (ilk kullanımda TEK sorgu) ---
    def satinalma_satirlari(self, stok_idler) -> list:
        if self._sat is None:
            self._sat = satinalma.acik_siparis_satirlari(None, etiketli=False)
        idler = set(stok_idler)
        return [s for s in self._sat if s["stok_id"] in idler]

    def icin(self, emir) -> "UsAdaptoru":
        return UsAdaptoru(self, emir)


class UsAdaptoru(EmirVerisi):
    """``uretim_emri_eksikleri(yukle=)`` arabirimi (``us_detay_yukle.Yukleme`` ile uyumlu): bir ÜS için ortak ``ListeYukleme``den dilim."""

    def __init__(self, liste: ListeYukleme, emir):
        self.kaynak = liste
        self.emir = emir
        self.ieler = liste.ies.get(emir.pk, [])
        self.cozucu = liste.cozucu
        self.ayirmalar = liste.ayirmalar.get(emir.pk, [])
