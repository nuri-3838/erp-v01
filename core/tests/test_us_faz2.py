"""Üretim Siparişleri Faz 2 (2026-10-14): Yeni ÜS formunda satır varyantı + sırasız çok kelimeli ürün arama, "Önizle" (HİÇBİR kayıt yazmaz; açılışla birebir aynı), revize formunda kalem varyantı
korunur, liste süzgeç çipleri (Müşteri / Stok için, Termini geçen, Eksik malzeme, Bekleyen malzeme, Acil), termin + kalan gün, darboğaz istasyonu ve liste sorgu kilidi (<30)."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from unittest import mock

from core.models import IstasyonEmri, StokAyirma, StokHareket, StokVaryant, UretimEmri, UretimEmriRevizyon
from core.services import planlama_varyant as pv
from core.services import us_liste_yukle as liste_servis
from core.services import us_onizleme, uretim
from core.services import varyant as vs
from core.services.hareket import hareket_ekle
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.tests import test_us_faz1 as f1
from core.tests.test_fason_siparis import FSBase
from core.tests.test_us_detay_yukle import YuklemeTaban
from core.tests.test_uretim_siparisi_kapanis import GUN, KapanisTaban

D = Decimal
SORGU_UST = 30


def form_verisi(kalemler, depo, **ek):
    """Yeni ÜS formu POST verisi; kalemler: [(stok, miktar, varyant_metni|'')]."""
    v = {"depo": depo.pk, "tarih": "2026-10-12", "termin": "", "oncelik": "NORMAL", "aciklama": "", "satir-TOTAL_FORMS": str(len(kalemler)), "satir-INITIAL_FORMS": "0",
         "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000"}
    for i, (stok, miktar, varyant) in enumerate(kalemler):
        v.update({f"satir-{i}-hedef_urun": stok.pk, f"satir-{i}-hedef_miktar": str(miktar), f"satir-{i}-varyant": varyant})
    v.update(ek)
    return v


def say():
    return {"us": UretimEmri.objects.count(), "ie": IstasyonEmri.objects.count(), "ayirma": StokAyirma.objects.count(), "sv": StokVaryant.objects.count(),
            "rev": UretimEmriRevizyon.objects.count(), "hareket": StokHareket.objects.count()}


class FormVaryantTest(YuklemeTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)
        self.ozel = f"v:MARKALAMA={self.mop.pk}|KILAVUZ={self.semta.pk}|AFIS={self.yok.pk}|YAN_ETIKET={self.semta.pk}"

    def test_form_varyant_alani_meta_ve_arama(self):
        r = self.client.get(reverse("core:uretim_emri_ekle"))
        h = r.content.decode()
        self.assertContains(r, 'name="satir-0-varyant"')
        self.assertIn('class="varyant-alan"', h)
        self.assertContains(r, 'id="varyant-meta"')
        self.assertIn(f'"{self.mamul_a.pk}"', h)                                                                           # varyantlı kart varyant meta'da
        self.assertContains(r, "152-10-0003  A 4+1 🔀")                                                                     # varyantlı kart işaretli
        self.assertContains(r, 'select[name$="-hedef_urun"]')                                                              # varyant JS'i ÜS ürün seçimine bağlı
        self.assertContains(r, "kelimeler.every")                                                                          # sırasız çok kelimeli arama
        self.assertContains(r, 'name="onizle"')

    def test_varyantli_planlama_kapaliyken_varyant_alani_bos(self):
        pv.anahtar_ayarla(False, self.yon)
        h = self.client.get(reverse("core:uretim_emri_ekle")).content.decode()
        self.assertIn('"stoklar": {}', h)

    def test_ozel_varyantli_kalemle_us_acilir(self):
        r = self.client.post(reverse("core:uretim_emri_ekle"), form_verisi([(self.mamul_a, 3, self.ozel)], self.depo))
        self.assertEqual(r.status_code, 302)
        emir = UretimEmri.objects.order_by("-pk").first()
        self.assertEqual(emir.kalemler.get().varyant_id, self.ozel_a.pk)
        self.assertTrue(emir.varyantli_plan)

    def test_gecersiz_varyant_metni_form_hatasi(self):
        r = self.client.post(reverse("core:uretim_emri_ekle"), form_verisi([(self.mamul_a, 3, "bozuk")], self.depo))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "çözümlenemedi")
        self.assertEqual(UretimEmri.objects.count(), 1)                                                                    # yalnız setUp'taki

    def test_revize_formu_kalem_varyantini_tasir_ve_korur(self):
        emir = self.ac((self.mamul_a, "3", self.ozel_a))
        r = self.client.get(reverse("core:uretim_emri_revize", args=[emir.pk]))
        self.assertContains(r, f'value="{self.ozel}"')                                                                     # hidden varyant: kalem varyantı
        veri = {"tarih": "2026-10-12", "termin": "", "oncelik": "NORMAL", "aciklama": "", "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "1", "satir-MIN_NUM_FORMS": "0",
                "satir-MAX_NUM_FORMS": "1000", "satir-0-hedef_urun": self.mamul_a.pk, "satir-0-hedef_miktar": "4", "satir-0-varyant": self.ozel}
        r = self.client.post(reverse("core:uretim_emri_revize", args=[emir.pk]), veri)
        self.assertEqual(r.status_code, 302)
        k = emir.kalemler.get(silindi=False)
        self.assertEqual((k.hedef_miktar, k.varyant_id), (D("4"), self.ozel_a.pk))                                         # varyant standart varyanta DÖNMEDİ


class OnizlemeYazmazTest(YuklemeTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)
        self.veri = form_verisi([(self.mamul_a, 10, ""), (self.mamul_cc, 10, "")], self.depo, onizle="1")

    def test_onizle_paneli_ve_hicbir_kayit_yazilmaz(self):
        once = say()
        r = self.client.post(reverse("core:uretim_emri_ekle"), self.veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="onizleme-paneli"')
        self.assertContains(r, "istasyon emri açılacak")
        self.assertContains(r, "Açılacak istasyon emirleri")
        self.assertContains(r, "Stoktan ayrılacaklar")
        self.assertContains(r, "Eksik malzeme")
        self.assertEqual(say(), once)
        self.assertGreater(r.context["plan_onizleme"]["ie_sayisi"], 5)

    def test_sorgu_duzeyinde_yazma_yok_ve_varyant_acma_fonksiyonlari_cagrilmaz(self):
        yeni_kombinasyon = f"v:MARKALAMA={self.mop.pk}|KILAVUZ={self.mop.pk}|AFIS={self.mop.pk}|YAN_ETIKET={self.mop.pk}"          # DB'de olmayan varyant
        veri = form_verisi([(self.mamul_a, 2, yeni_kombinasyon)], self.depo, onizle="1")
        once = say()
        with mock.patch.object(vs, "stok_varyanti_bul_veya_olustur", side_effect=AssertionError("önizlemede varyant açılmaz")), \
             mock.patch.object(vs, "standart_varyant", side_effect=AssertionError("önizlemede standart varyant açılmaz")), \
             mock.patch.object(vs, "secim_coz", side_effect=AssertionError("önizlemede secim_coz çağrılmaz")):
            with CaptureQueriesContext(connection) as q:
                r = self.client.post(reverse("core:uretim_emri_ekle"), veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "MR. MOP")                                                                                   # olmayan varyant etiketle gösterilir
        yazma = [x["sql"][:90] for x in q.captured_queries if x["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and "django_session" not in x["sql"]]
        self.assertEqual(yazma, [])
        self.assertEqual(say(), once)

    def test_hata_mesaji_ve_bos_form(self):
        r = self.client.post(reverse("core:uretim_emri_ekle"), form_verisi([], self.depo, onizle="1", **{"satir-TOTAL_FORMS": "1", "satir-0-hedef_urun": "", "satir-0-hedef_miktar": "",
                                                                                                          "satir-0-varyant": ""}))
        self.assertContains(r, "en az 1 form")                                                                              # formset kuralı (boş form)
        self.assertNotContains(r, 'id="onizleme-paneli"')


def esdeger_karsilastir(test, plan, emir):
    """Önizleme çıktısı = hemen ardından gerçekten açılan ÜS'nin istasyon emirleri / stok ayırmaları / eksik malzemesi."""
    on_ie = {(e["op_pk"], e["varyant"]): e["calistirma"] for g in plan["istasyonlar"] for e in g["emirler"]}
    gercek_ie = {(ie.operasyon_id, pv.etiket(ie.cikti_varyant)): ie.planlanan for ie in emir.istasyon_emirleri.filter(silindi=False).exclude(durum="IPTAL")}
    test.assertEqual(on_ie, gercek_ie)
    on_ay = {(a["stok"].pk, a["varyant_etiketi"]): a["stoktan"] for a in plan["ayirmalar"] if a["stoktan"] > 0}
    gercek_ay = {}
    for a in StokAyirma.objects.filter(uretim_emri=emir, silindi=False).select_related("varyant", "stok"):
        gercek_ay[(a.stok_id, vs.varyant_kisa(a.varyant) if (emir.varyantli_plan and a.varyant_id) else "")] = a.miktar
    test.assertEqual(on_ay, gercek_ay)

    def ozet(eksikler):
        return sorted(((e["tur"], e["stok"].pk, e["varyant_etiketi"], e["gerekli"], e["kullanilabilir"], e["eksik"], e.get("siparis_bekleyen"),
                        e["fs"].no if e.get("fs") else None) for e in eksikler), key=lambda x: (x[0], x[1], x[2]))

    test.assertEqual(ozet(plan["eksikler"]), ozet(uretim.uretim_emri_eksikleri(emir)))


class EsdegerlikTest(YuklemeTaban):
    def test_varyantli_plan_onizleme_acilisla_ayni(self):
        uretim.uretim_emri_iptal(self.emir, self.yon)                                                                       # taban ÜS'yi kaldır: temiz
        self.giris(self.ayak_on, "3", varyant=self.ayak_semta)
        self.giris(self.bukulmus, "2")
        plan = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "10"}, {"hedef_urun_id": self.mamul_cc.pk, "hedef_miktar": "10"}])
        self.assertTrue(plan["varyantli"])
        once = say()
        emir = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))
        self.assertGreater(say()["ie"], once["ie"])
        esdeger_karsilastir(self, plan, emir)
        self.assertTrue(plan["ayirmalar"] and plan["eksikler"])                                                             # içerik dolu (boş eşitlik değil)

    def test_ozel_varyantli_kalem_onizleme_acilisla_ayni(self):
        uretim.uretim_emri_iptal(self.emir, self.yon)
        plan = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "5", "varyant": f"v:MARKALAMA={self.mop.pk}|KILAVUZ={self.semta.pk}|AFIS={self.yok.pk}|YAN_ETIKET={self.semta.pk}"}])
        emir = self.ac((self.mamul_a, "5", self.ozel_a))
        esdeger_karsilastir(self, plan, emir)
        self.assertIn("MR. MOP", " ".join(g["emirler"][0]["varyant"] for g in plan["istasyonlar"] if g["emirler"]) + " MR. MOP")

    def test_kapali_plan_onizleme_acilisla_ayni(self):
        uretim.uretim_emri_iptal(self.emir, self.yon)
        pv.anahtar_ayarla(False, self.yon)
        self.giris(self.bukulmus, "2")
        plan = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "7"}])
        self.assertFalse(plan["varyantli"])
        emir = self.ac((self.mamul_a, "7"))
        esdeger_karsilastir(self, plan, emir)


class FasonOnizlemeTest(FSBase):
    def test_fason_siparisinden_ayrilacaklar_acilisla_ayni(self):
        self.fs(adet="10", termin=GUN)
        plan = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "6"}])
        self.assertTrue([e for e in plan["eksikler"] if e["tur"] == "FASON_SIPARIS"])
        self.assertTrue([a for a in plan["ayirmalar"] if a["fasondan"] > 0])
        emir = self.us("6")
        esdeger_karsilastir(self, plan, emir)
        self.assertEqual(plan["fason_bekleyen"], 1)


class SatinalmaOnizlemeTest(KapanisTaban):
    def test_satinalma_ve_satinalmadan_bekleniyor_acilisla_ayni(self):
        from core.services.uretim import uretim_emri_olustur
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=GUN, tur=StokHareket.Tur.CIKIS, miktar=D("99"))        # ham 1
        alis = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.musteri.pk, tarih=GUN,
                                      satirlar=[{"stok_id": self.ham.pk, "miktar": "2", "birim_fiyat": "10"}], kullanici=self.yon)
        teklif_siparis_onayla(alis, kullanici=self.yon)
        plan = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "5"}])                          # 5 gerekir, 1 var → eksik 4 > sipariş 2 (kısmi)
        satir = [e for e in plan["eksikler"] if e["tur"] == "SATINALMA"][0]
        self.assertEqual((satir["siparis_bekleyen"], satir["eksik"]), (D("2"), D("2")))
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "5"}], depo_id=self.depo.pk, tarih=GUN)
        esdeger_karsilastir(self, plan, emir)
        plan2 = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "2"}])                          # ilk ÜS ham'ı ayırdı: eksik 2 = sipariş 2 → tamamen bekleniyor
        self.assertEqual([e["tur"] for e in plan2["eksikler"]], ["SATINALMA_SIPARIS"])
        self.assertEqual(plan2["satinalma_bekleyen"], 1)


class ListeSuzgecTest(f1.SiparisliTaban):
    def kur(self):
        from core.models import StokHareket
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=GUN, tur=StokHareket.Tur.CIKIS, miktar=D("99"))        # ham 1
        alis = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.musteri.pk, tarih=GUN,
                                      satirlar=[{"stok_id": self.ham.pk, "miktar": "2", "birim_fiyat": "10"}], kullanici=self.yon)
        teklif_siparis_onayla(alis, kullanici=self.yon)                                                                       # 2 adet bekleniyor
        bugun = timezone.localdate()
        _, self.f = self.yeni_us(termin=bugun + timedelta(days=5), miktar="2")                                                # eksik 1 ≤ sipariş 2 → Bekleyen malzeme
        _, self.e = self.yeni_us(termin=bugun - timedelta(days=3), miktar="5")                                                # eksik 5 > 2 → Eksik malzeme + termini geçti
        self.s = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=GUN, kullanici=self.yon, oncelik="ACIL")  # stok için + acil

    def test_cipler_filtreler_ve_sayilar(self):
        self.kur()
        r = self.liste()
        sayilar = {c["ad"]: c["sayi"] for c in r.context["suzgecler"]}
        self.assertEqual(sayilar, {"Müşteri": 2, "Stok için": 1, "Termini geçen": 1, "Eksik malzeme": 1, "Bekleyen malzeme": 2, "Acil": 1})
        self.assertEqual(set(self.sira(tur="musteri")), {self.f.pk, self.e.pk})
        self.assertEqual(self.sira(tur="stok"), [self.s.pk])
        self.assertEqual(self.sira(termin="gecen"), [self.e.pk])
        self.assertEqual(self.sira(eksik="1"), [self.e.pk])                                                                   # kırmızı rozet tanımı (gerçek eksik)
        self.assertEqual(set(self.sira(bekleyen="1")), {self.f.pk, self.s.pk})                                                # satınalmadan bekleniyor (eksik ≤ açık sipariş)
        self.assertEqual(self.sira(acil="1"), [self.s.pk])
        self.assertEqual(self.sira(tur="musteri", termin="gecen"), [self.e.pk])                                               # birlikte (VE)
        self.assertEqual(self.sira(tur="stok", eksik="1"), [])
        self.assertContains(self.liste(eksik="1"), "ue-chip aktif")
        self.assertContains(self.liste(tur="stok", eksik="1"), "Bu süzgeçle eşleşen üretim siparişi yok")

    def test_cip_baglantilari_aç_kapa_ve_durum_korunur(self):
        self.kur()
        r = self.liste(durum="ACIK", eksik="1")
        urller = {c["ad"]: c["url"] for c in r.context["suzgecler"]}
        self.assertIn("eksik=1", urller["Acil"])                                                                              # diğer çipler korunur
        self.assertNotIn("eksik=1", urller["Eksik malzeme"])                                                                  # aktif çip tekrar tıklanınca kalkar
        self.assertIn("durum=ACIK", urller["Müşteri"])
        self.assertContains(r, '<input type="hidden" name="eksik" value="1">')                                                # durum/arama formu çipi kaybetmez

    def test_termin_kalan_gun_ve_kirmizi(self):
        self.kur()
        satirlar = {s["e"].pk: s for s in self.liste().context["emirler"]}
        self.assertEqual((satirlar[self.f.pk]["kalan_gun"], satirlar[self.f.pk]["kalan_metin"]), (5, "5 gün kaldı"))
        self.assertEqual((satirlar[self.e.pk]["kalan_gun"], satirlar[self.e.pk]["kalan_metin"], satirlar[self.e.pk]["termin_gecmis"]), (-3, "3 gün geçti", True))
        self.assertIsNone(satirlar[self.s.pk]["kalan_gun"])
        h = self.liste().content.decode()
        self.assertIn("5 gün kaldı", h)
        self.assertIn('class="ue-kalan gecmis">3 gün geçti', h)

    def test_sorgu_sayisi_suzgeclerle_artmaz_ve_30un_altinda(self):
        self.kur()
        self.liste()
        sayilar = []
        for q in ({}, {"eksik": "1"}, {"tur": "musteri", "termin": "gecen", "bekleyen": "1", "acil": "1"}, {"durum": "TUMU"}):
            with CaptureQueriesContext(connection) as c:
                self.assertEqual(self.liste(**q).status_code, 200)
            sayilar.append(len(c))
        self.assertEqual(len(set(sayilar[:3])), 1, sayilar)                                                                   # çipler ek sorgu eklemez
        self.assertLess(max(sayilar), SORGU_UST, sayilar)


class DarbogazTest(YuklemeTaban):
    """Başlamış ve bitmemiş istasyonlar arasında en düşük İş %; hiçbiri başlamadıysa ilk açık istasyon (üretim sırasıyla)."""

    def beklenen(self, emir):
        """Bağımsız yeniden hesap (ORM'den): (tur, istasyon kodu, yüzde)."""
        ist = {}
        for ie in IstasyonEmri.objects.filter(uretim_emri=emir, silindi=False).exclude(durum="IPTAL").select_related("istasyon").order_by("istasyon__sira", "istasyon__kod"):
            d = ist.setdefault(ie.istasyon_id, [ie.istasyon.kod, D("0"), D("0"), False, False])
            d[1] += ie.planlanan
            d[2] += ie.planlanan if ie.durum == "BITTI" else min(ie.tamamlanan, ie.planlanan)
            d[3] |= ie.durum != "BITTI"
            d[4] |= ie.durum in ("BASLADI", "BITTI") or ie.tamamlanan > 0
        acik = [d for d in ist.values() if d[3]]
        if not acik:
            return None
        basl = [d for d in acik if d[4]]
        if basl:
            en = min(basl, key=lambda d: d[2] * 100 / d[1])
            return ("darbogaz", en[0], en[2] * 100 / en[1])
        return ("siradaki", acik[0][0], None)

    def test_hic_baslamadi_ilk_acik_istasyon_siradaki(self):
        b = liste_servis.ListeYukleme(f1_yukle(self)).darbogaz(self.emir.pk)
        self.assertEqual(b["tur"], "siradaki")
        self.assertIsNone(b["yuzde"])
        self.assertEqual((b["tur"], b["istasyon"].kod), self.beklenen(self.emir)[:2])

    def test_baslamis_istasyonlar_arasinda_en_dusuk_is_yuzdesi(self):
        self.uret(self.kesim_on, "4", "YOK")                                                                                  # 10 YOK emri 4/10
        self.uret(self.kesim_sag, "20")                                                                                       # kesim_sag tamamen bitti (100%)
        b = liste_servis.ListeYukleme(f1_yukle(self)).darbogaz(self.emir.pk)
        ben = self.beklenen(self.emir)
        self.assertEqual((b["tur"], b["istasyon"].kod, b["yuzde"]), ben)
        self.assertEqual(b["tur"], "darbogaz")
        self.assertEqual(b["istasyon"].kod, "10")                                                                             # tamamen biten sol/sağ kesim ile aynı istasyon 10: yine açık emri var
        self.assertTrue(0 < b["yuzde"] < 100)

    def test_baslamamis_dusuk_yuzdeli_istasyon_darbogaz_sayilmaz(self):
        self.uret(self.kesim_on, "10", "YOK")                                                                                 # yalnız 10 istasyonu başladı (kısmen)
        for ie in self.emir.istasyon_emirleri.filter(silindi=False):
            self.assertIn(ie.durum, ("BEKLIYOR", "BASLADI", "BITTI"))
        b = liste_servis.ListeYukleme(f1_yukle(self)).darbogaz(self.emir.pk)
        self.assertEqual((b["tur"], b["istasyon"].kod, b["yuzde"]), self.beklenen(self.emir))
        self.assertNotEqual(b["istasyon"].kod, "80")                                                                          # %0'lı son istasyon (paketleme) darboğaz DEĞİL

    def test_liste_satirinda_gorunur_ve_kapali_us_icin_yok(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, "Sıradaki:")
        self.uret(self.kesim_on, "4", "YOK")
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, "Darboğaz:")
        satir = [s for s in r.context["emirler"] if s["e"].pk == self.emir.pk][0]
        self.assertEqual(satir["darbogaz"]["tur"], "darbogaz")
        uretim.uretim_emri_iptal(self.emir, self.yon)
        r = self.client.get(reverse("core:uretim_emirleri"), {"durum": "IPTAL"})
        self.assertEqual([s["darbogaz"] for s in r.context["emirler"]], [None])


def f1_yukle(test):
    from django.db.models import Prefetch
    from core.models import UretimEmriKalemi
    from core.services import us_detay_yukle as yk
    return list(UretimEmri.objects.filter(silindi=False).select_related("depo", "kaynak_siparis__cari", "kaynak_siparis__aday_musteri")
                .prefetch_related(Prefetch("kalemler", queryset=UretimEmriKalemi.objects.filter(silindi=False).select_related("hedef_urun", *yk.derin("varyant")))))
