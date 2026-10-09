"""Operasyon kaydı çıktı satırları (OperasyonKaydiCikti): onay anındaki boy/oran SAKLANIR; sonradan operasyon tanımı değişse de yeniden paylaştırma ESKİ
orana göre yapılır; eski (satırsız) kayıt = ana çıktı %100. Onaylı kaydı geri alıp silme düğmesi: yalnız yönetici (403), servis çağrısı, hata mesajı."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, StokHareket
from core.services import stok_ortalama
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import operasyon_guncelle, operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur
from core.tests.test_uretim_yan_cikti import YanCiktiBase

D = Decimal


class KayitAniSaklamaTest(YanCiktiBase):
    def _onayli(self):
        h = self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        return h, k

    def _maliyeti_degistir(self, h, tl, usd):
        StokHareket.objects.filter(pk=h.pk).update(giris_tutar_try=D(tl), giris_tutar_usd=D(usd))
        stok_ortalama.yeniden_hesapla(self.profil)

    def test_cikti_satirlari_saklanir(self):
        _, k = self._onayli()
        satirlar = {c.stok.kod: c for c in k.ciktilar.all()}
        self.assertEqual(set(satirlar), {"AYAK66", "AYAK55"})
        self.assertEqual((satirlar["AYAK66"].ana_mi, satirlar["AYAK66"].miktar, satirlar["AYAK66"].boy_mm), (True, D("3"), D("1292.60")))
        self.assertEqual((satirlar["AYAK55"].ana_mi, satirlar["AYAK55"].miktar, satirlar["AYAK55"].boy_mm), (False, D("1"), D("1063.53")))
        self.assertAlmostEqual(float(satirlar["AYAK55"].pay_orani), 0.2152, places=3)
        self.assertEqual(float(satirlar["AYAK66"].pay_orani + satirlar["AYAK55"].pay_orani), 1.0)

    def test_tanimdaki_boy_ve_yan_cikti_degisince_eski_oranla_yeniden_paylastirilir(self):
        h, k = self._onayli()
        ilk = {x.stok.kod: x.tutar_try for x in self.girisler(k).values()}
        # tanım: ana boy 5.000 mm, yan çıktı boyu 100 mm (yeni tanımla yan pay ≈ %2 olurdu)
        operasyon_guncelle(self.op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))],
                           boy_mm=D("5000"), yan_ciktilar=[(self.yan, D("1"), D("100"))])
        self._maliyeti_degistir(h, "20000", "500")
        g = self.girisler(k)
        for x in g.values():
            x.refresh_from_db()
        self.assertEqual(g["AYAK55"].tutar_try + g["AYAK66"].tutar_try, D("2000.00"))
        self.assertAlmostEqual(float(g["AYAK55"].tutar_try / D("2000")), 0.2152, places=3)       # eski oran, %2 değil
        self.assertAlmostEqual(float(ilk["AYAK55"] / D("1000")), 0.2152, places=3)

    def test_tanimdan_yan_cikti_silinse_de_kayit_eski_oranla_paylasir(self):
        h, k = self._onayli()
        operasyon_guncelle(self.op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))],
                           boy_mm="", yan_ciktilar=[])
        self._maliyeti_degistir(h, "20000", "500")
        g = self.girisler(k)
        for x in g.values():
            x.refresh_from_db()
        self.assertEqual(g["AYAK55"].tutar_try + g["AYAK66"].tutar_try, D("2000.00"))
        self.assertAlmostEqual(float(g["AYAK55"].tutar_try / D("2000")), 0.2152, places=3)
        k.refresh_from_db()
        fis = {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak) for s in k.fis.satirlar.filter(silindi=False)}
        self.assertEqual(fis[("151.20", "B")], g["AYAK55"].tutar_try)

    def test_eski_kayit_cikti_satiri_yoksa_ana_cikti_yuzde_yuz(self):
        ana = self._stok("ESKI", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("2"), satirlar=[(self.profil, D("1"))])
        h = self.profil_gir(10, 1000, 25)
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("4"))
        operasyon_kaydi_onayla(k)
        k.ciktilar.all().delete()                                                    # canlıdaki eski onaylı kayıtlar: çıktı satırı yok
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("2"), satirlar=[(self.profil, D("1"))], boy_mm=D("500"),
                           yan_ciktilar=[(self.yan, D("1"), D("100"))])               # tanım sonradan yan çıktı kazandı
        self._maliyeti_degistir(h, "2000", "50")
        g = self.girisler(k)
        self.assertEqual(list(g), ["ESKI"])
        g["ESKI"].refresh_from_db()
        self.assertEqual((g["ESKI"].tutar_try, g["ESKI"].tutar_usd), (D("400.00"), D("10.00")))      # 2 profil × (2000/10)

    def test_yan_cikti_olmayan_yeni_kayit_tek_satir_yuzde_yuz(self):
        ana = self._stok("TEK2", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("2"), satirlar=[(self.profil, D("1"))])
        self.profil_gir(10, 1000, 25)
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("4"))
        operasyon_kaydi_onayla(k)
        c = k.ciktilar.get()
        self.assertEqual((c.ana_mi, c.pay_orani, c.miktar), (True, D("1"), D("4")))


class GeriAlSilEkranTest(YanCiktiBase):
    def setUp(self):
        self.yonetici = User.objects.create_superuser("gyon", password="x")
        self.sade = User.objects.create_user("gsade", password="x")
        EkranYetki.objects.create(kullanici=self.sade, ekran_kod="operasyon_kayitlari")
        self.profil_gir(10, 10000, 250)
        self.k = self.kayit("3")
        operasyon_kaydi_onayla(self.k)
        self.url = reverse("core:operasyon_kaydi_geri_al_sil", args=[self.k.pk])
        self.detay = reverse("core:operasyon_kaydi_detay", args=[self.k.pk])

    def test_yonetici_dugmeyi_gorur_onay_metniyle_sade_kullanici_gormez(self):
        self.client.force_login(self.yonetici)
        r = self.client.get(self.detay)
        self.assertContains(r, "Geri al ve sil")
        self.assertContains(r, "Bu kaydın tüm stok hareketleri ve maliyet fişi geri alınıp kayıt kalıcı silinecek")
        self.client.force_login(self.sade)
        self.assertNotContains(self.client.get(self.detay), "Geri al ve sil")

    def test_yonetici_olmayana_403_ve_hicbir_sey_degismez(self):
        self.client.force_login(self.sade)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.k.refresh_from_db()
        self.assertFalse(self.k.silindi)
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi=self.k, silindi=False).count(), 3)

    def test_yonetici_geri_alir_ve_siler(self):
        self.client.force_login(self.yonetici)
        r = self.client.post(self.url)
        self.assertRedirects(r, reverse("core:operasyon_kayitlari"), fetch_redirect_response=False)
        self.k.refresh_from_db()
        self.assertTrue(self.k.silindi)
        self.assertEqual((eldeki_miktar(self.ana, self.depo), eldeki_miktar(self.yan, self.depo), eldeki_miktar(self.profil, self.depo)),
                         (D("0"), D("0"), D("10")))

    def test_cikti_baska_yerde_tuketildiyse_hata_mesaji_ve_degisiklik_yok(self):
        hareket_ekle(stok_id=self.yan.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 10), tur=StokHareket.Tur.CIKIS, miktar=D("1"))
        self.client.force_login(self.yonetici)
        r = self.client.post(self.url, follow=True)
        self.assertContains(r, "negatife düşer")                                      # servis hata mesajı ekranda
        self.k.refresh_from_db()
        self.assertFalse(self.k.silindi)
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi=self.k, silindi=False).count(), 3)

    def test_get_ve_taslak_kayit_degistirmez(self):
        self.client.force_login(self.yonetici)
        self.client.get(self.url)
        self.k.refresh_from_db()
        self.assertFalse(self.k.silindi)
        taslak = self.kayit("3")
        r = self.client.post(reverse("core:operasyon_kaydi_geri_al_sil", args=[taslak.pk]), follow=True)
        self.assertContains(r, "Yalnız onaylı kayıt")
        taslak.refresh_from_db()
        self.assertFalse(taslak.silindi)
