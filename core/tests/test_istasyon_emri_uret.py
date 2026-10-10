"""İstasyon emrinden TEK TIKLA ÜRETİM ("Üretildi": kayıt aç + mevcut onay servisiyle onayla, tek işlem) ve satış siparişinde üretim siparişi işareti."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import IstasyonEmri, OperasyonKaydi, StokHareket, UretimEmri
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import eldeki_miktar
from core.services.uretim import UretimHatasi, istasyon_emri_acik_kalan
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class UretTaban(kt.KapanisTaban):
    def kur(self, miktar="3"):
        self.sip = self.siparis(miktar)
        self.emir = self.emir_ac(self.sip)
        self.ie = self.emir.istasyon_emirleri.get()
        self.url = reverse("core:istasyon_emri_uret", args=[self.ie.pk])
        return self.emir

    def uret(self, miktar="", tarih="2026-06-29", **ek):
        return self.client.post(self.url, {"miktar": miktar, "tarih": tarih, **ek}, follow=True)

    def yenile(self):
        self.ie.refresh_from_db()
        return self.ie


class UretildiTest(UretTaban):
    def test_tam_miktar_kayit_acar_ve_onaylar(self):
        self.kur("3")
        r = self.uret()
        kayit = OperasyonKaydi.objects.get(istasyon_emri=self.ie)
        self.assertEqual((kayit.durum, kayit.hedef_cikti_miktari, kayit.tarih), (OperasyonKaydi.Durum.ONAYLI, D("3"), date(2026, 6, 29)))
        self.assertContains(r, "üretildi")
        self.assertContains(r, "Tamamlandı")
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum), (D("3"), IstasyonEmri.Durum.BITTI))
        self.assertEqual((eldeki_miktar(self.stok), eldeki_miktar(self.ham)), (D("3"), D("97")))      # ham düştü, mamul girdi (mevcut onay servisi)
        self.assertEqual(sa.ayrilan_miktar(self.emir, self.stok), D("3"))                              # üretilen parça ÜS'ye ayrıldı
        self.assertTrue(StokHareket.objects.filter(operasyon_kaydi=kayit, tarih=date(2026, 6, 29)).exists())
        self.assertNotContains(r, f'action="{self.url}"')                                              # tamamlandıktan sonra form yok

    def test_kismi_uretim_ve_virgullu_miktar(self):
        self.kur("3")
        self.uret(miktar="1")
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum, istasyon_emri_acik_kalan(ie)), (D("1"), IstasyonEmri.Durum.BASLADI, D("2")))
        r = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertContains(r, 'value="2"')                                                            # kutu varsayılanı = açık kalan
        self.uret(miktar="1,5")                                                                        # TR virgül
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, istasyon_emri_acik_kalan(ie)), (D("2.5"), D("0.5")))
        self.uret(miktar="0,5")
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum), (D("3"), IstasyonEmri.Durum.BITTI))
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie, durum=OperasyonKaydi.Durum.ONAYLI).count(), 3)

    def test_fazla_uretim_serbest_stoga_uyari_verir(self):
        self.kur("3")
        r = self.uret(miktar="5")
        self.assertContains(r, "kalanını aşıyor")
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum), (D("5"), IstasyonEmri.Durum.BITTI))
        self.assertEqual((eldeki_miktar(self.stok), sa.ayrilan_miktar(self.emir, self.stok)), (D("5"), D("3")))   # fazlası serbest stok (ÜS'ye yalnız 3)

    def test_stok_yetersiz_hicbir_sey_kalmaz_hata_satirda(self):
        self.kur("150")                                                                                # ham 100 var, 150 gerekir
        n_kayit, n_hareket = OperasyonKaydi.objects.count(), StokHareket.objects.count()
        r = self.uret(miktar="150")
        self.assertEqual((OperasyonKaydi.objects.count(), StokHareket.objects.count()), (n_kayit, n_hareket))   # kayıt da oluşmadı
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum), (D("0"), IstasyonEmri.Durum.BEKLIYOR))
        self.assertEqual(eldeki_miktar(self.ham), D("100"))
        self.assertContains(r, "Yetersiz stok")
        self.assertContains(r, "Sf-HAM")
        self.assertContains(r, "eksik: 50")                                                            # hangi stok, ne kadar eksik
        self.assertContains(r, 'class="ie-hata"')                                                      # satırda görünür
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])), "ie-hata\">")   # tek seferlik (oturumdan düştü)
        self.uret(miktar="90")                                                                         # sonra yeterli miktar üretilebilir
        self.assertEqual(self.yenile().tamamlanan, D("90"))

    def test_fason_emrinde_uretildi_yok_ve_servis_reddeder(self):
        self.kur("3")
        self.ie.istasyon.kod = "10"
        self.ie.istasyon.save()
        d = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertNotContains(d, f'action="{self.url}"')
        self.assertContains(d, f"?istasyon_emri={self.ie.pk}")                                         # Fason dönüş aç / Kayıtlar bağlantısı
        with self.assertRaisesMessage(UretimHatasi, "fason dönüş"):
            uretim.istasyon_emri_uret(self.ie)
        r = self.uret()
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie).count(), 0)
        self.assertContains(r, "fason dönüş")

    def test_geri_alinca_acik_kalan_geri_artar(self):
        self.kur("3")
        self.uret(miktar="2")
        kayit = OperasyonKaydi.objects.get(istasyon_emri=self.ie)
        self.assertEqual(istasyon_emri_acik_kalan(self.yenile()), D("1"))
        uretim.operasyon_kaydi_sil(kayit, onayli_geri_al=True)                                         # mevcut geri alma
        ie = self.yenile()
        self.assertEqual((ie.tamamlanan, ie.durum, istasyon_emri_acik_kalan(ie)), (D("0"), IstasyonEmri.Durum.BEKLIYOR, D("3")))
        self.assertEqual((eldeki_miktar(self.ham), eldeki_miktar(self.stok)), (D("100"), D("0")))
        r = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertContains(r, f'action="{self.url}"')                                                 # tekrar üretilebilir

    def test_istasyon_emirleri_listesinde_form_ve_geri_donus(self):
        self.kur("3")
        liste = reverse("core:istasyon_emirleri")
        r = self.client.get(liste)
        self.assertContains(r, f'action="{self.url}"')
        self.assertContains(r, 'name="miktar"')
        self.assertContains(r, 'name="tarih"')
        self.assertContains(r, ">Üretildi<")
        r = self.client.post(self.url, {"miktar": "3", "tarih": "2026-06-29", "geri": liste}, follow=True)
        self.assertEqual(r.request["PATH_INFO"], liste)                                                # listeye döner
        self.assertContains(r, "üretildi")
        r2 = self.client.post(self.url, {"miktar": "1", "tarih": "x", "geri": "https://kotu.example/"}, follow=True)   # güvensiz geri yok sayılır
        self.assertNotEqual(r2.request["PATH_INFO"], "/")
        self.assertContains(r2, "Tarih geçerli değil")


class SiparisUsIsaretiTest(UretTaban):
    def test_rozet_pasif_dugme_silinince_geri_gelir(self):
        emir = self.kur("3")
        acma = reverse("core:siparis_uretim_emrine_cevir", args=[self.sip.pk])
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        self.assertContains(d, "Üretim siparişi:")
        self.assertContains(d, emir.no)
        self.assertContains(d, "Açık")
        self.assertContains(d, "%0")
        self.assertContains(d, f"Zaten açık: {emir.no}")
        self.assertNotContains(d, acma)                                                                # buton pasif (bağlantı yok)
        self.uret()
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk])), "%100")       # üretim ilerledikçe yüzde artar

    def test_us_silinince_buton_geri_gelir(self):
        emir = self.kur("3")
        acma = reverse("core:siparis_uretim_emrine_cevir", args=[self.sip.pk])
        self.assertNotContains(self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk])), acma)
        uretim.uretim_emri_sil(emir)                                                                   # başlamamış ÜS silinince
        d2 = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        self.assertContains(d2, acma)
        self.assertNotContains(d2, "Zaten açık")
        self.assertNotContains(d2, "Üretim siparişi:")

    def test_iptal_soluk_aktif_one_cikar_ve_buton_geri(self):
        eski = self.kur("3")
        uretim.uretim_emri_iptal(eski)
        acma = reverse("core:siparis_uretim_emrine_cevir", args=[self.sip.pk])
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        self.assertContains(d, 'class="us-rozet soluk"')
        self.assertContains(d, acma)                                                                    # iptal ÜS varken yeniden açılabilir
        yeni = self.emir_ac(self.sip)
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        govde = d.content.decode()
        self.assertIn('class="us-rozet ana"', govde)
        self.assertLess(govde.index(yeni.no), govde.index(eski.no))                                     # aktif önce
        self.assertContains(d, f"Zaten açık: {yeni.no}")
        self.assertNotContains(d, acma)

    def test_satis_siparisleri_listesinde_rozet(self):
        emir = self.kur("3")
        liste = self.client.get(reverse("core:satis_siparisleri"))
        self.assertContains(liste, f"🏭 {emir.no}")
        self.assertContains(liste, reverse("core:uretim_emri_detay", args=[emir.pk]))
        uretim.uretim_emri_iptal(emir)
        self.assertNotContains(self.client.get(reverse("core:satis_siparisleri")), f"🏭 {emir.no}")      # iptal ÜS listede rozet vermez
        diger = self.siparis("2")                                                                       # ÜS'siz sipariş
        self.assertNotContains(self.client.get(reverse("core:satis_siparisleri")), "🏭 ÜS-")
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists() and diger.pk != self.sip.pk)
