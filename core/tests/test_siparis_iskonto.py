"""Satış siparişinde satır iskontosu: proforma→sipariş liste fiyatı + iskonto taşır (net = liste × (1 − %)); sipariş detayında Liste/İskonto/Net/Tutar
sütunları tutarlı; düzenleme ve revize formları iskontoyu taşır (önceden sessizce 0'lanıyordu); sipariş→satış faturası NET fiyatla açılır (tutar
değişmez); alış/irsaliye formlarında iskonto gizli alan olarak korunur."""
from decimal import Decimal

from django.urls import reverse

from core import views
from core.models import Fatura, TeklifSiparis
from core.services import teklif_siparis as ts_servis
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class SiparisIskontoTest(kt.KapanisTaban):
    def proforma_sip(self, miktar="3", fiyat="50", iskonto="20"):
        pf = ts_servis.teklif_siparis_olustur(belge_tur="PROFORMA", yon="SATIS", cari_id=self.musteri.pk, tarih=kt.GUN, kullanici=self.yon,
                                              satirlar=[{"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": fiyat, "iskonto_yuzdesi": iskonto}])
        ts_servis.teklif_siparis_onayla(pf, kullanici=self.yon)
        return pf, ts_servis.proformayi_siparise_cevir(pf, tarih=kt.GUN, kullanici=self.yon)

    def duzenle_govde(self, sip, **over):
        g = {"cari": self.musteri.pk, "tarih": "2026-06-28", "para_birimi": "TRY", "aciklama": "",
             "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
             "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "50", "form-0-iskonto_yuzdesi": "20"}
        g.update(over)
        return g

    def test_proformadan_siparise_iskonto_tasinir_ve_tutar_ayni(self):
        pf, sip = self.proforma_sip()
        k = sip.kalemler.get(silindi=False)
        self.assertEqual((k.birim_fiyat, k.iskonto_yuzdesi, k.net_birim_fiyat_tam, k.tutar), (D("50"), D("20"), D("40"), D("120")))
        self.assertEqual(sip.ara_toplam, pf.ara_toplam)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertContains(r, "Liste Fiyatı")
        self.assertContains(r, "<th class=\"sag\">İskonto</th><th class=\"sag\">Net Fiyat</th>")
        self.assertContains(r, "%20,00")
        self.assertContains(r, "40,0000")
        self.assertContains(r, "120,00")
        kalemler = list(sip.kalemler.filter(silindi=False))
        views.satis_siparis_pdf_baglam(sip, kalemler, "tr", self.yon)                     # PDF: net fiyat × miktar = tutar (proforma PDF ile aynı)
        self.assertEqual((kalemler[0].net_birim_fiyat, kalemler[0].tutar), (D("40"), D("120")))

    def test_duzenleme_formu_iskontoyu_tasir(self):
        _pf, sip = self.proforma_sip()
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[sip.pk]))
        self.assertContains(r, "İskonto %")
        self.assertContains(r, 'name="form-0-iskonto_yuzdesi"')
        self.assertContains(r, 'value="20,00"')
        r = self.client.post(reverse("core:teklif_siparis_duzenle", args=[sip.pk]), self.duzenle_govde(sip, **{"form-0-miktar": "4"}))
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["bform"].errors)
        k = sip.kalemler.get(silindi=False)
        self.assertEqual((k.miktar, k.birim_fiyat, k.iskonto_yuzdesi, k.tutar), (D("4"), D("50"), D("20"), D("160")))
        r = self.client.post(reverse("core:teklif_siparis_duzenle", args=[sip.pk]), self.duzenle_govde(sip, **{"form-0-iskonto_yuzdesi": "101"}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "İskonto 0 ile 100 arasında olmalı")

    def test_revize_formu_iskontoyu_korur(self):
        _pf, sip = self.proforma_sip()
        ts_servis.teklif_siparis_onayla(sip, kullanici=self.yon)
        emir = self.emir_ac(sip)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:siparis_revize", args=[sip.pk]))
        self.assertContains(r, "İskonto %")
        self.assertContains(r, 'value="20,00"')
        r = self.client.post(reverse("core:siparis_revize", args=[sip.pk]), {
            "tarih": "2026-06-28", "aciklama": "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "5", "form-0-birim_fiyat": "50", "form-0-iskonto_yuzdesi": "20"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["bform"].errors)
        k = sip.kalemler.get(silindi=False)
        self.assertEqual((k.miktar, k.iskonto_yuzdesi, k.tutar), (D("5"), D("20"), D("200")))
        emir.refresh_from_db()
        self.assertEqual(emir.kalemler.get(silindi=False).hedef_miktar, D("5"))

    def test_siparisten_faturaya_net_fiyat_gecer_tutar_degismez(self):
        _pf, sip = self.proforma_sip()
        ts_servis.teklif_siparis_onayla(sip, kullanici=self.yon)
        emir = self.emir_ac(sip)
        self.uret(emir)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertContains(r, 'value="40,0000"')                                        # NET fiyat ön-dolu (liste 50 değil)
        self.assertNotContains(r, 'value="50,0000"')
        r = self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28", "para_birimi": "TRY", "faturasiz": "on", "depo": self.depo.pk,
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "3", "form-0-birim_fiyat": "40"}, follow=True)
        sip.refresh_from_db()
        self.assertIsNotNone(sip.fatura_id)
        f = Fatura.objects.get(pk=sip.fatura_id)
        satir = f.satirlar.get(silindi=False)
        self.assertEqual((satir.birim_fiyat, satir.miktar * satir.birim_fiyat), (D("40"), D("120")))
        self.assertEqual(satir.miktar * satir.birim_fiyat, sip.ara_toplam)

    def test_alis_siparisinde_iskonto_gizli_korunur(self):
        sip = ts_servis.teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.tedarikci.pk, tarih=kt.GUN, kullanici=self.yon,
                                               satirlar=[{"stok_id": self.ham.pk, "miktar": "2", "birim_fiyat": "10"}])
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[sip.pk]))
        self.assertNotContains(r, "İskonto %")
        self.assertContains(r, 'type="hidden" name="form-0-iskonto_yuzdesi"')
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertNotContains(r, "Liste Fiyatı")
        self.assertContains(r, "Birim Fiyat")
