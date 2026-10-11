"""E-4: satış teklif/proforma/sipariş satırında varyant (4 soru; boş = YOK), dönüşümlerde taşıma, PDF bağlamında yalnız standart dışı
seçimler (TR/EN; tamamı standartsa boş → PDF birebir), "+ yeni varyant" uç noktası, özel varyantlı siparişten ÜS açma kilidi (E-6'ya kadar)."""
import json
from datetime import date
from decimal import Decimal

from django.template.loader import render_to_string
from django.urls import reverse

from core import views
from core.models import Stok, TeklifSiparis, UretimEmri, Varyant
from core.services import planlama_varyant as pv
from core.services import teklif_siparis as ts_servis
from core.services import uretim
from core.services import varyant as vs
from core.tests import test_satis_proforma as tsp
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal
DORT = ["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"]


def _secim(semta, mop=None, afis=""):
    m = mop or semta
    return f"v:MARKALAMA={m.pk}|KILAVUZ={semta.pk}|AFIS={afis}|YAN_ETIKET={semta.pk}"


class SatisVaryantTaban(kt.KapanisTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Stok.objects.filter(pk=cls.stok.pk).update(varyantli=True, varyant_boyutlari=DORT)
        cls.stok.refresh_from_db()
        vs.standart_varyant(cls.stok)
        cls.semta, cls.mop = Varyant.objects.get(kod="SEMTA"), Varyant.objects.get(kod="MR. MOP")

    def siparis_v(self, varyant=None, miktar="3", onayla=True, belge_tur="SIPARIS"):
        s = {"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": "50"}
        if varyant is not None:
            s["varyant"] = varyant
        ts = ts_servis.teklif_siparis_olustur(belge_tur=belge_tur, yon="SATIS", cari_id=self.musteri.pk, tarih=kt.GUN, satirlar=[s], kullanici=self.yon)
        return ts_servis.teklif_siparis_onayla(ts, kullanici=self.yon) if onayla else ts


class SatisVaryantServisTest(SatisVaryantTaban):
    def test_kalem_varyanti_cozulur_bos_boyut_yok_olur_metin_tr_en(self):
        sip = self.siparis_v(_secim(self.semta, self.mop, afis=""))
        sv = sip.kalemler.get(silindi=False).varyant
        self.assertEqual((sv.markalama_id, sv.kilavuz_id, sv.afis.kod, sv.yan_etiket_id, sv.standart), (self.mop.pk, self.semta.pk, "YOK", self.semta.pk, False))
        self.assertEqual(vs.standart_disi_metin(sv, "tr"), "Markalama: MR. MOP · Afiş: yok")
        self.assertEqual(vs.standart_disi_metin(sv, "en"), "Branding: MR. MOP · Sticker: none")
        std = self.siparis_v(_secim(self.semta, afis=str(self.semta.pk))).kalemler.get(silindi=False).varyant
        self.assertEqual((std.standart, vs.standart_disi_metin(std, "en")), (True, ""))
        self.assertIsNone(self.siparis_v().kalemler.get(silindi=False).varyant_id)                      # varyant verilmedi → standart (boş)

    def test_teklif_proforma_siparis_donusumleri_varyanti_tasir(self):
        teklif = self.siparis_v(_secim(self.semta, self.mop), miktar="1", onayla=False, belge_tur="TEKLIF")
        sv = teklif.kalemler.get(silindi=False).varyant
        ts_servis.teklif_gonder(teklif, kullanici=self.yon)
        ts_servis.teklif_kabul_et(teklif, kullanici=self.yon)
        proforma = ts_servis.teklifi_proformaya_cevir(teklif, tarih=kt.GUN, kullanici=self.yon)
        self.assertEqual(proforma.kalemler.get(silindi=False).varyant_id, sv.pk)
        ts_servis.teklif_siparis_onayla(proforma, kullanici=self.yon)
        siparis = ts_servis.proformayi_siparise_cevir(proforma, tarih=kt.GUN, kullanici=self.yon)
        self.assertEqual(siparis.kalemler.get(silindi=False).varyant_id, sv.pk)

    def test_ozel_varyantli_siparisten_us_acilamaz_standart_acilir(self):
        ozel = self.siparis_v(_secim(self.semta, self.mop))
        self.assertTrue(uretim.siparis_ozel_varyantli(ozel))
        with self.assertRaisesMessage(uretim.UretimHatasi, "Bu siparişte özel varyant var; varyantlı üretim henüz devrede değil."):
            self.emir_ac(ozel)
        self.assertFalse(UretimEmri.objects.filter(kaynak_siparis=ozel).exists())
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ozel.pk]))
        self.assertContains(r, "özel varyant, henüz devrede değil")
        self.assertContains(r, "🔀 Markalama: MR. MOP · Afiş: yok")
        self.assertNotContains(r, reverse("core:siparis_uretim_emrine_cevir", args=[ozel.pk]))
        r = self.client.get(reverse("core:siparis_uretim_emrine_cevir", args=[ozel.pk]), follow=True)
        self.assertTrue(any("özel varyant" in str(m) for m in r.context["messages"]))
        std = self.siparis_v(_secim(self.semta, afis=str(self.semta.pk)))
        self.assertFalse(uretim.siparis_ozel_varyantli(std))
        emir = self.emir_ac(std)                                                                        # standart: ÜS akışı aynen
        self.assertEqual(emir.kaynak_siparis_id, std.pk)
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[std.pk]))
        self.assertNotContains(r, "🔀")

    def test_pdf_baglami_yalniz_standart_disi_ve_tamami_standartsa_bos(self):
        sip = ts_servis.teklif_siparis_olustur(belge_tur="SIPARIS", yon="SATIS", cari_id=self.musteri.pk, tarih=kt.GUN, kullanici=self.yon, satirlar=[
            {"stok_id": self.stok.pk, "miktar": "2", "birim_fiyat": "50", "varyant": _secim(self.semta, self.mop)},
            {"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "50"}])
        kalemler = list(sip.kalemler.filter(silindi=False).order_by("id"))
        ctx = views.satis_siparis_pdf_baglam(sip, kalemler, "en", self.yon)
        self.assertEqual([k.varyant_metni for k in kalemler], ["Branding: MR. MOP · Sticker: none", ""])
        self.assertEqual(ctx["E"]["varyant"], "Variant")
        html = render_to_string("core/satis_siparis_pdf.html", {"ts": sip, "kalemler": kalemler, **ctx})
        self.assertEqual(html.count("Variant: Branding: MR. MOP · Sticker: none"), 1)
        ctx_tr = views.satis_siparis_pdf_baglam(sip, kalemler, "tr", self.yon)
        html_tr = render_to_string("core/satis_siparis_pdf.html", {"ts": sip, "kalemler": kalemler, **ctx_tr})
        self.assertIn("Varyant: Markalama: MR. MOP · Afiş: yok", html_tr)
        std = self.siparis_v()
        k2 = list(std.kalemler.filter(silindi=False))
        ctx2 = views.satis_siparis_pdf_baglam(std, k2, "en", self.yon)
        html2 = render_to_string("core/satis_siparis_pdf.html", {"ts": std, "kalemler": k2, **ctx2})
        self.assertNotIn('class="varyant-not"', html2)                                                  # tamamı standart: PDF'te satır notu yok

    def test_hizli_varyant_ekle_uc_noktasi(self):
        r = self.client.post(reverse("core:varyant_hizli_ekle"), {"kod": "nova"})
        self.assertEqual(r.status_code, 200)
        j = json.loads(r.content)
        self.assertEqual((j["kod"], Varyant.objects.get(pk=j["pk"]).aktif), ("NOVA", True))
        self.assertEqual(self.client.post(reverse("core:varyant_hizli_ekle"), {"kod": "NOVA"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("core:varyant_hizli_ekle")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("core:varyant_hizli_ekle"), {"kod": "x"}).status_code, 302)


class ProformaEkraniVaryantTest(tsp.SatisProformaTest):
    """Proforma ekranı: satırda varyant bölümü + gizli alan; kayıt ve düzenlemede ön-dolum. (Üst sınıfın testleri de aynen koşar.)"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Stok.objects.filter(pk=cls.a21.pk).update(varyantli=True, varyant_boyutlari=DORT)
        cls.a21.refresh_from_db()
        vs.standart_varyant(cls.a21)
        cls.semta, cls.mop = Varyant.objects.get(kod="SEMTA"), Varyant.objects.get(kod="MR. MOP")

    def test_ekran_varyant_alani_ve_kayit_duzenleme(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_proforma_ekle"))
        self.assertContains(r, 'id="varyant-meta"')
        self.assertContains(r, 'name="form-0-varyant"')
        self.assertEqual(r.content.decode().count('class="varyant-alan"'), 2)                            # her ürün kartında (JS varyantsızda gizler)
        secim = _secim(self.semta, self.mop)
        r = self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde(**{"form-0-varyant": secim}))
        self.assertEqual(r.status_code, 302)
        p = self._son_proforma()
        sv = p.kalemler.get(silindi=False).varyant
        self.assertEqual((sv.stok_id, sv.markalama_id, sv.afis.kod, sv.standart), (self.a21.pk, self.mop.pk, "YOK", False))
        r = self.client.get(reverse("core:satis_proforma_duzenle", args=[p.pk]))
        self.assertContains(r, f'name="form-0-varyant" value="{vs.secim_degeri(sv)}"')
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[p.pk]))
        self.assertContains(r, "🔀 Markalama: MR. MOP · Afiş: yok")
        r = self.client.post(reverse("core:satis_proforma_ekle"), self._post_govde(**{                 # varyantsız c22'ye varyant → ret
            "form-1-dahil": "on", "form-1-miktar": "5", "form-1-varyant": "v:AFIS=1"}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "varyantlı bir kart değil")


class PlanlamaAcikSiparisTest(SatisVaryantTaban):
    """E-6, anahtar AÇIK: özel varyantlı siparişten ÜS açılır (kilit kalkar), ÜS/kalem/istasyon emri/kayıt varyant taşır, mamul o varyanta
    girer; sevk (depolu fatura, satırda aynı varyant) o varyantın ayırmasını düşürür (``sevk_dusen`` "stok:varyant"), fatura silinince geri gelir."""

    def setUp(self):
        super().setUp()
        pv.anahtar_ayarla(True, self.yon)

    def fatura_kes_v(self, sip, sv, miktar="3"):
        r = self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28", "para_birimi": "TRY", "depo": self.depo.pk,
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": miktar, "form-0-birim_fiyat": "50", "form-0-varyant": vs.secim_degeri(sv)}, follow=True)
        sip.refresh_from_db()
        return r, [str(m) for m in r.context["messages"]]

    def test_ozel_siparis_us_acilir_uretilir_sevk_varyant_bazinda(self):
        from core.services import stok_ayirma as sa
        from core.services import fatura as fatura_servis
        from core.services.hareket import eldeki_miktar
        ozel = self.siparis_v(_secim(self.semta, self.mop))
        sv = ozel.kalemler.get(silindi=False).varyant
        self.assertTrue(uretim.siparis_ozel_varyantli(ozel))
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ozel.pk]))
        self.assertNotContains(r, "henüz devrede değil")
        self.assertContains(r, reverse("core:siparis_uretim_emrine_cevir", args=[ozel.pk]))
        emir = self.emir_ac(ozel)                                                                       # kilit yok
        self.assertTrue(emir.varyantli_plan)
        self.assertEqual(emir.kalemler.get().varyant_id, sv.pk)
        ie = emir.istasyon_emirleri.get()
        self.assertEqual((ie.cikti_varyant_id, ie.ihtiyac), (sv.pk, {f"{self.stok.pk}:{sv.pk}": "3"}))
        self.uret(emir)
        kayit = emir.operasyon_kayitlari.get(silindi=False)
        self.assertEqual((kayit.cikti_varyant_id, kayit.ciktilar.get(ana_mi=True).varyant_id), (sv.pk, sv.pk))
        self.assertEqual((eldeki_miktar(self.stok, varyant=sv), sa.ayrilan_miktar(emir, self.stok, varyant=sv)), (D("3"), D("3")))
        self.assertEqual(uretim.uretim_emri_yuzde(emir), D("100"))
        r, mesajlar = self.fatura_kes_v(ozel, sv)                                                       # sevk: satırda aynı varyant
        self.assertIsNotNone(ozel.fatura_id)
        self.assertFalse(any("kapanmadı" in m or "bitmemiş" in m for m in mesajlar), mesajlar)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen), (UretimEmri.Durum.KAPALI, {f"{self.stok.pk}:{sv.pk}": "3"}))
        self.assertEqual((eldeki_miktar(self.stok, varyant=sv), sa.ayrilan_miktar(emir, self.stok)), (D("0"), D("0")))
        fatura_servis.fatura_sil(ozel.fatura)                                                           # geri alma: ayırma aynı varyanta döner
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen), (UretimEmri.Durum.ACIK, {}))
        self.assertEqual(sa.ayrilan_miktar(emir, self.stok, varyant=sv), D("3"))
        pv.anahtar_ayarla(False, self.yon)                                                              # anahtar kapanınca mevcut ÜS varyantlı kalır
        emir.refresh_from_db()
        self.assertTrue(emir.varyantli_plan)
        self.assertEqual(uretim.uretim_emri_eksikleri(emir), [])
