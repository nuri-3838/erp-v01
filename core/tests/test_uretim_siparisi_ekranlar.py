"""Üretim siparişi EKRANLARI (plan adım 9 — docs/uretim-siparisi-plan.md): ÜS liste/detay, istasyon emirleri listesi, emirden kayıt açma,
iptal / sil / revize ekranları, sipariş revize ekranı, operasyon kayıtları süzgeci, sipariş detayı rozeti ve yetki. Gerçek sipariş + ÜS fixture'ıyla."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, IstasyonEmri, OperasyonKaydi, UretimEmri
from core.services import uretim
from core.services.uretim import istasyon_emri_kayit_ac, operasyon_kaydi_onayla
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class EkranTaban(kt.KapanisTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from django.contrib.auth import get_user_model
        cls.sadece_us = get_user_model().objects.create_user("ekrus", password="x")
        EkranYetki.objects.create(kullanici=cls.sadece_us, ekran_kod="uretim_emirleri")

    def kur(self, miktar="3"):
        self.sip = self.siparis(miktar)
        self.emir = self.emir_ac(self.sip)
        self.ie = self.emir.istasyon_emirleri.get()
        return self.emir


class EkranTest(EkranTaban):
    def test_liste_ve_detay_gosterir(self):
        emir = self.kur()
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, "Üretim Siparişleri")
        self.assertContains(r, emir.no)
        r = self.client.get(reverse("core:uretim_emirleri") + "?durum=KAPALI")
        self.assertNotContains(r, emir.no)
        d = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        for metin in (emir.no, self.ie.no, "Ayrılan Stoklar", "Revizyon Geçmişi", "Kayıt aç", "Fason dönüş aç", "Açılış", "İptal Et", "Revize", "Sil"):
            self.assertContains(d, metin)
        self.assertContains(d, reverse("core:siparis_revize", args=[self.sip.pk]))            # siparişli ÜS: revize sipariş kalemlerinden
        self.assertContains(d, f"?istasyon_emri={self.ie.pk}")
        s = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        self.assertContains(s, "Üretim Siparişine Dönüştü")
        self.assertContains(s, reverse("core:siparis_revize", args=[self.sip.pk]))

    def test_istasyon_emirleri_listesi_ve_suzgecler(self):
        emir = self.kur()
        url = reverse("core:istasyon_emirleri")
        self.assertContains(self.client.get(url), self.ie.no)                                  # varsayılan: bekleyen / başlayan
        self.assertNotContains(self.client.get(url + "?durum=BITTI"), self.ie.no)
        self.assertContains(self.client.get(url + f"?istasyon={self.ie.istasyon_id}"), self.ie.no)
        self.assertNotContains(self.client.get(url + f"?istasyon={self.ie.istasyon_id + 999}"), self.ie.no)
        self.assertContains(self.client.get(url + f"?uretim_emri={emir.pk}&ara={self.ie.no}"), self.ie.no)
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.ie))
        self.assertNotContains(self.client.get(url), self.ie.no)                               # bitti → açık işlerde yok
        self.assertContains(self.client.get(url + "?durum=BITTI"), self.ie.no)

    def test_emirden_kayit_ac_ve_kayit_listesi(self):
        emir = self.kur()
        r = self.client.post(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk]), {}, follow=True)
        kayit = OperasyonKaydi.objects.get(istasyon_emri=self.ie)
        self.assertEqual((kayit.durum, kayit.uretim_emri_id, kayit.hedef_cikti_miktari), (OperasyonKaydi.Durum.TASLAK, emir.pk, D("3")))
        self.assertContains(r, kayit.no)
        self.assertContains(r, self.ie.no)                                                     # kayıt detayında istasyon emri görünür
        liste = self.client.get(reverse("core:operasyon_kayitlari") + f"?istasyon_emri={self.ie.pk}")
        self.assertContains(liste, kayit.no)
        self.assertContains(liste, self.ie.no)
        self.assertNotContains(self.client.get(reverse("core:operasyon_kayitlari") + "?emir=bagimsiz"), kayit.no)
        self.assertContains(self.client.get(reverse("core:operasyon_kayitlari") + f"?uretim_emri={emir.pk}"), kayit.no)
        # kalan yokken (açık taslak kalanı kapattı) hedefsiz ikinci açılış reddedilir, hedefli fazla açılış uyarı verir
        r2 = self.client.post(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk]), {}, follow=True)
        self.assertContains(r2, "kalan miktar yok")
        r3 = self.client.post(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk]), {"hedef": "2"}, follow=True)
        self.assertContains(r3, "kalanını aşıyor")
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie).count(), 2)
        self.assertEqual(self.client.get(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk])).status_code, 302)   # GET kayıt açmaz

    def test_iptal_ekrani_sip_detayi_rozeti_ve_yeniden_acma(self):
        emir = self.kur()
        r = self.client.post(reverse("core:uretim_emri_iptal", args=[emir.pk]), follow=True)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.IPTAL)
        self.assertContains(r, "iptal edildi")
        d = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(d, "İptal")
        self.assertNotContains(d, reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk]))   # iptal ÜS'nin emirleri kayıt açtırmaz
        self.assertNotContains(d, reverse("core:uretim_emri_iptal", args=[emir.pk]))
        s = self.client.get(reverse("core:teklif_siparis_detay", args=[self.sip.pk]))
        self.assertNotContains(s, "Üretim Siparişine Dönüştü")                                 # IPTAL ÜS rozeti/kilidi yok
        self.assertContains(s, "Üretim Siparişi Aç")                                           # yeniden açılabilir
        self.assertContains(self.client.post(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk]), follow=True), "açık üretim siparişinin")

    def test_sil_dugmesi_yalniz_baslamamis_ve_basladiktan_sonra_sil_reddedilir(self):
        emir = self.kur()
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertContains(self.client.get(reverse("core:uretim_emirleri")), reverse("core:uretim_emri_sil", args=[emir.pk]))
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.ie))
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertNotContains(self.client.get(reverse("core:uretim_emirleri")), reverse("core:uretim_emri_sil", args=[emir.pk]))
        r = self.client.post(reverse("core:uretim_emri_sil", args=[emir.pk]), follow=True)
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk, silindi=False).exists())          # başlamış ÜS silinmez
        self.assertContains(r, "iptal")

    def test_siparis_revize_ekrani(self):
        emir = self.kur("3")
        url = reverse("core:siparis_revize", args=[self.sip.pk])
        g = self.client.get(url)
        self.assertEqual(g.status_code, 200)
        self.assertContains(g, "Revize Et")
        kalem = self.sip.kalemler.get(silindi=False)
        veri = {"tarih": "2026-06-29", "aciklama": "adet artışı",
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
                "form-0-stok": self.stok.pk, "form-0-miktar": "5", "form-0-birim_fiyat": "50"}
        r = self.client.post(url, veri, follow=True)
        emir.refresh_from_db()
        self.assertEqual(emir.revizyon_no, 1)
        self.assertEqual(emir.kalemler.get(silindi=False).hedef_miktar, D("5"))
        self.assertContains(r, "revize edildi")
        self.assertContains(r, "Revizyon Geçmişi")
        self.assertContains(r, "3 → 5")                                                         # revize geçmişinde kalem farkı
        self.assertEqual(self.ie.__class__.objects.get(pk=self.ie.pk).planlanan, D("5"))
        # sevk edilmişin altına / geçersiz giriş: hata ekranda kalır, hiçbir şey değişmez
        veri["form-0-miktar"] = "0"
        r2 = self.client.post(url, veri)
        self.assertEqual(r2.status_code, 200)
        emir.refresh_from_db()
        self.assertEqual(emir.revizyon_no, 1)
        # ÜS iptal edilince revize ekranı kapanır
        uretim.uretim_emri_iptal(emir)
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_manuel_us_revize_ekrani_ve_siparisli_yonlendirme(self):
        siparisli = self.kur()
        self.assertRedirects(self.client.get(reverse("core:uretim_emri_revize", args=[siparisli.pk])), reverse("core:siparis_revize", args=[self.sip.pk]))
        manuel = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=kt.GUN)
        url = reverse("core:uretim_emri_revize", args=[manuel.pk])
        g = self.client.get(url)
        self.assertContains(g, f"{manuel.no} Revize")
        r = self.client.post(url, {"tarih": "2026-06-29", "aciklama": "", "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "1",
                                   "satir-MIN_NUM_FORMS": "1", "satir-MAX_NUM_FORMS": "1000",
                                   "satir-0-hedef_urun": self.stok.pk, "satir-0-hedef_miktar": "4"}, follow=True)
        manuel.refresh_from_db()
        self.assertEqual((manuel.revizyon_no, manuel.kalemler.get(silindi=False).hedef_miktar), (1, D("4")))
        self.assertContains(r, "revize edildi")

    def test_yetki(self):
        emir = self.kur()
        self.client.force_login(self.bos)
        for ad, args in (("uretim_emirleri", []), ("istasyon_emirleri", []), ("uretim_emri_detay", [emir.pk]), ("uretim_emri_revize", [emir.pk])):
            self.assertEqual(self.client.get(reverse("core:" + ad, args=args)).status_code, 403, ad)
        for ad, args in (("uretim_emri_iptal", [emir.pk]), ("istasyon_emri_kayit_ac", [self.ie.pk])):
            self.assertEqual(self.client.post(reverse("core:" + ad, args=args)).status_code, 403, ad)
        self.assertEqual(self.client.get(reverse("core:siparis_revize", args=[self.sip.pk])).status_code, 403)
        self.client.force_login(self.sadece_us)                                                # ÜS ekranı yetkisi İstasyon Emirleri listesini açmaz
        self.assertEqual(self.client.get(reverse("core:uretim_emirleri")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:istasyon_emirleri")).status_code, 403)
        self.assertEqual(self.client.post(reverse("core:istasyon_emri_kayit_ac", args=[self.ie.pk])).status_code, 302)   # ÜS detayından kayıt açılabilir
