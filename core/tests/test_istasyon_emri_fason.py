"""Fason dönüşü ↔ istasyon emri (plan adım 6 — docs/uretim-siparisi-plan.md): istasyon emrinden fason dönüş açma (varsayılan adet = açık kalan × referans),
kayıt emre bağlı doğar, onayda tamamlanan GELEN adetten artar (fire sonrası kalan açık kalır; ikinci dönüş kalanı kapatır), PARÇALA'da çıktı bazlı gelenden
çalıştırma, ayırma yalnız gelen kadar, geri al, doğrulamalar (yanlış operasyon / iptal edilmiş emir) ve ekran (GET ön doldurma + POST gizli alan)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import IstasyonEmri, OperasyonKaydi, UretimEmri
from core.services import fason_donus as fd
from core.services import stok_ayirma as sa
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar
from core.services.uretim import uretim_emri_iptal, uretim_emri_olustur
from core.tests.test_fason_donus import TARIH
from core.tests.test_fason_parcala import FasonParcalaBase

D = Decimal


class UretFasonTest(FasonParcalaBase):
    def setUp(self):
        self.fason_profil(10, 6400, 160)
        self.fiyatlar()
        self.emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.ana.pk, "hedef_miktar": D("6")}], depo_id=self.depo.pk, tarih=TARIH)
        self.ie = self.emir.istasyon_emirleri.get(operasyon=self.op)

    def durum(self):
        self.ie.refresh_from_db()
        return self.ie.durum, self.ie.tamamlanan

    def test_donus_emre_bagli_acilir_ve_fire_kalani_acik_birakir(self):
        self.assertEqual(self.ie.planlanan, D("2"))                                           # 6 adet / 3 adet-boy
        d = fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, gelen_ana=D("4"))
        k = d.kayitlar.get()
        self.assertEqual((k.uretim_emri_id, k.istasyon_emri_id, k.hedef_cikti_miktari, k.fason_cari_id), (self.emir.pk, self.ie.pk, D("6"), self.salim.pk))
        self.assertEqual(self.durum(), (IstasyonEmri.Durum.BASLADI, D("0")))                  # açık taslak
        fd.donus_onayla(d)
        self.assertEqual(self.durum()[0], IstasyonEmri.Durum.BASLADI)
        self.assertEqual(self.ie.tamamlanan.quantize(D("0.000001")), D("1.333333"))
        self.assertEqual(eldeki_miktar(self.ana, self.depo), D("4"))
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.ana), D("4"))                      # yalnız gelen adet ÜS'ye ayrıldı
        d2 = fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, gelen_ana=D("2"))   # kalan 0,6667 boy = 2 adet
        self.assertEqual(d2.kayitlar.get().hedef_cikti_miktari, D("3"))                       # tam boy: beklenen 1 boy = 3 adet; gelen 2 (fire 1)
        fd.donus_onayla(d2)
        self.assertEqual(self.durum(), (IstasyonEmri.Durum.BITTI, D("2")))
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.ana), D("6"))

    def test_geri_al_tamamlanan_ve_ayirmayi_geri_sarar(self):
        d = fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH)
        fd.donus_onayla(d)
        self.assertEqual((self.durum(), sa.ayrilan_miktar(self.emir, self.ana)), ((IstasyonEmri.Durum.BITTI, D("2")), D("6")))
        fd.donus_geri_al_sil(d)
        self.assertEqual((self.durum(), sa.ayrilan_miktar(self.emir, self.ana)), ((IstasyonEmri.Durum.BEKLIYOR, D("0")), D("0")))
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("10"))

    def test_taslak_donus_silinince_emir_bekliyor(self):
        d = fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH)
        fd.donus_sil(d)
        self.assertEqual(self.durum(), (IstasyonEmri.Durum.BEKLIYOR, D("0")))

    def test_dogrulamalar(self):
        with self.assertRaisesMessage(FasonHatasi, "operasyonu"):
            fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk + 999, "3", None, None, self.ie.pk)])
        fd.donus_onayla(fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH))
        with self.assertRaisesMessage(FasonHatasi, "kalan miktar yok"):
            fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH)
        uretim_emri_iptal(self.emir)
        with self.assertRaisesMessage(FasonHatasi, "açık değil"):
            fd.istasyon_emri_fason_donusu_ac(self.ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, adet="3")
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie, silindi=False).count(), 1)

    def test_ekran_on_doldurma_ve_gizli_alan(self):
        self.client.force_login(User.objects.create_superuser("fd_ie", password="x"))
        r = self.client.get(reverse("core:fason_donus_ekle"), {"istasyon_emri": self.ie.pk})
        ilk = r.context["formset"].initial[0]
        self.assertEqual((ilk["operasyon"], ilk["istasyon_emri"], ilk["adet"]), (self.op.pk, self.ie.pk, D("6.000")))
        self.assertContains(r, f'name="satir-0-istasyon_emri" value="{self.ie.pk}"')
        r = self.client.post(reverse("core:fason_donus_ekle"), {
            "cari": self.salim.pk, "depo": self.depo.pk, "tarih": "2026-10-09", "irsaliye_no": "", "aciklama": "",
            "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-operasyon": self.op.pk, "satir-0-adet": "6", "satir-0-gelen": "",
            "satir-0-istasyon_emri": self.ie.pk})
        self.assertEqual(r.status_code, 302)
        k = OperasyonKaydi.objects.get(istasyon_emri=self.ie)
        self.assertEqual((k.uretim_emri_id, k.fason_donus_id is not None), (self.emir.pk, True))


class ParcalaFasonTest(FasonParcalaBase):
    def test_parcala_gelenden_calistirma_ve_ayirma(self):
        self.parcala(tam_boy=False)
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.sag.pk, "hedef_miktar": D("24")}], depo_id=self.depo.pk, tarih=TARIH)
        ie = emir.istasyon_emirleri.get(operasyon=self.pop)
        self.assertEqual(ie.planlanan, D("2"))                                               # 24 / 12
        self.assertEqual(ie.ihtiyac, {str(self.sag.pk): "24"})                               # yalnız talep edilen çıktı net ihtiyaç
        d = fd.istasyon_emri_fason_donusu_ac(ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, adet=D("12"), gelen={self.sol.pk: D("6")})
        k = d.kayitlar.get()
        self.assertEqual((k.istasyon_emri_id, k.hedef_cikti_miktari), (ie.pk, D("12")))      # çalıştırma max(12/12, 6/12) = 1
        fd.donus_onayla(d)
        ie.refresh_from_db()
        self.assertEqual((ie.tamamlanan, ie.durum), (D("1"), IstasyonEmri.Durum.BASLADI))
        self.assertEqual(sa.ayrilan_miktar(emir, self.sag), D("12"))
        self.assertEqual(sa.ayrilan_miktar(emir, self.sol), D("0"))                          # talep edilmeyen kardeş serbest stok
        self.assertEqual(eldeki_miktar(self.sol, self.depo), D("6"))
        d2 = fd.istasyon_emri_fason_donusu_ac(ie, cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH)    # kalan 1 çalıştırma = 12 referans
        self.assertEqual(d2.kayitlar.get().hedef_cikti_miktari, D("12"))
