"""FASON > Kesim Listesi Hesapla artık Operasyon Tanımları'ndan (İstasyon 10 · Boru Lazer) hesaplanır: tam boy kuralı, yan çıktılar, fasoncu parça kodu ve birim fiyat."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Cari, FasonKesimKaydi, IsIstasyonu, Kategori, Operasyon, Stok
from core.services import fason as fs
from core.services.uretim import operasyon_guncelle, operasyon_olustur

D = Decimal


class ListeBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.boy = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        kat = Kategori.objects.create(kod="KL", ad="KESİM LİSTESİ TEST")
        cls.lazer = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        cls.montaj = IsIstasyonu.objects.create(kod="70", ad="MONTAJ")

        def stok(kod, ad, birim=None, **kw):
            return Stok.objects.create(kod=kod, ad=ad, kategori=kat, uretim_birimi=birim or cls.adet, fatura_birimi=birim or cls.adet,
                                       uretim_urunu=kw.pop("uretim", True), satinalma_urunu=kw.pop("satinalma", False), satis_urunu=kw.pop("satis", False))
        cls.profil = stok("150-10-0007", "7378 ÖN AYAK PROFİL", cls.boy, uretim=False, satinalma=True)
        cls.parca = stok("151-10-0021", "KESİLMİŞ C AYAK 6+6 SAĞ")
        cls.yan = stok("151-10-0019", "KESİLMİŞ C AYAK 5+5 SAĞ")
        cls.urun1 = stok("152-22-0001", "ÇİFT ÇIKIŞ 2+2", satis=True)
        cls.urun2 = stok("152-22-0002", "ÇİFT ÇIKIŞ 3+3", satis=True)
        # kesim (İstasyon 10): 1 boy → 3 ana + 1 yan; montaj (İstasyon 70): urun1 ← 2 parça, urun2 ← 1 parça
        cls.op_kesim = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.parca.pk, cikti_miktar=D("3"), satirlar=[(cls.profil, D("1"))],
                                         boy_mm=D("1292.60"), yan_ciktilar=[(cls.yan, D("1"), D("1063.53"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.urun1.pk, cikti_miktar=D("1"), satirlar=[(cls.parca, D("2"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.urun2.pk, cikti_miktar=D("1"), satirlar=[(cls.parca, D("1"))])
        cls.salim = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")

    def fiyatlar(self, tarih=date(2026, 1, 1), ana="10", yan="6"):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.parca.pk, birim_fiyat=D(ana), gecerlilik_baslangic=tarih, fasoncu_kodu="GZ-P-00041")
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.yan.pk, birim_fiyat=D(yan), gecerlilik_baslangic=tarih, fasoncu_kodu="GZ-P-00042")


class HesapTest(ListeBase):
    def test_operasyondan_tam_boy_ve_yan_cikti(self):
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)])                  # 10 parça → ceil(10/3) = 4 boy → 12 ana + 4 yan
        ana, yan = s["ozet"]
        self.assertEqual((ana["profil"].kod, ana["boy"], ana["kesilmis_parca"].kod, ana["toplam_adet"], ana["yan"]), ("150-10-0007", D("4"), "151-10-0021", D("12"), False))
        self.assertEqual((yan["kesilmis_parca"].kod, yan["toplam_adet"], yan["yan"], yan["boy"]), ("151-10-0019", D("4"), True, None))

    def test_ortak_parca_toplanmis_talep_uzerinden_tek_yuvarlama(self):
        s = fs.fason_listesi_operasyondan([(self.urun1, 1), (self.urun2, 1)])  # 2 + 1 = 3 parça → 1 boy (ayrı ayrı 2 boy olurdu)
        self.assertEqual(s["ozet"][0]["boy"], D("1"))
        self.assertEqual(s["ozet"][0]["toplam_adet"], D("3"))

    def test_yalniz_istasyon_10_operasyonlari(self):
        s = fs.fason_listesi_operasyondan([(self.urun1, 1)])
        self.assertEqual({r["kesilmis_parca"].kod for r in s["ozet"]}, {"151-10-0021", "151-10-0019"})   # montaj (70) listede YOK
        self.assertEqual(fs.fason_listesi_operasyondan([(self.parca, 3)])["ozet"][0]["boy"], D("1"))        # parçanın kendisi de istenebilir
        Operasyon.objects.filter(pk=self.op_kesim.pk).update(istasyon=self.montaj)
        self.assertEqual(fs.fason_listesi_operasyondan([(self.urun1, 1)])["ozet"], [])

    def test_bos_ve_sifir(self):
        self.assertEqual(fs.fason_listesi_operasyondan([])["ozet"], [])
        self.assertEqual(fs.fason_listesi_operasyondan([(self.urun1, 0)])["ozet"], [])

    def test_fasoncusuz_fiyat_alanlari_bos(self):
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)])
        self.assertTrue(all(r["birim_fiyat"] is None and r["fasoncu_kodu"] == "" and r["tutar"] is None for r in s["ozet"]))
        self.assertEqual((s["toplam_tutar"], s["fiyat_eksikleri"]), ({}, []))

    def test_fasoncu_kodu_birim_fiyat_ve_toplam(self):
        self.fiyatlar()
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)], cari=self.salim, tarih=date(2026, 10, 9))
        ana, yan = s["ozet"]
        self.assertEqual((ana["fasoncu_kodu"], ana["birim_fiyat"], ana["tutar"]), ("GZ-P-00041", D("10"), D("120")))
        self.assertEqual((yan["fasoncu_kodu"], yan["birim_fiyat"], yan["tutar"]), ("GZ-P-00042", D("6"), D("24")))
        self.assertEqual(s["toplam_tutar"], {"TRY": D("144")})

    def test_fiyat_tarihi_ve_eksik_fiyat(self):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.parca.pk, birim_fiyat=D("10"), gecerlilik_baslangic=date(2026, 6, 1))
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)], cari=self.salim, tarih=date(2026, 5, 1))      # henüz geçerli fiyat yok
        self.assertTrue(all(r["birim_fiyat"] is None for r in s["ozet"]))
        self.assertEqual({x.kod for x in s["fiyat_eksikleri"]}, {"151-10-0021", "151-10-0019"})
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)], cari=self.salim, tarih=date(2026, 7, 1))
        self.assertEqual([x.kod for x in s["fiyat_eksikleri"]], ["151-10-0019"])                            # yalnız yan çıktının fiyatı yok
        self.assertEqual(s["toplam_tutar"], {"TRY": D("120")})

    def test_doviz_fiyat_pb_ayri_toplanir(self):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.parca.pk, birim_fiyat=D("0.25"), para_birimi="USD", gecerlilik_baslangic=date(2026, 1, 1))
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.yan.pk, birim_fiyat=D("6"), gecerlilik_baslangic=date(2026, 1, 1))
        s = fs.fason_listesi_operasyondan([(self.urun1, 5)], cari=self.salim, tarih=date(2026, 10, 9))
        self.assertEqual(s["toplam_tutar"], {"USD": D("3.00"), "TRY": D("24")})


class KayitTest(ListeBase):
    def test_kayit_fasoncuyu_saklar_ve_sonuc_canli(self):
        self.fiyatlar()
        kayit = fs.fason_kaydi_olustur(kalemler=[(self.urun1, 5)], cari=self.salim)
        self.assertEqual(kayit.cari, self.salim)
        self.assertEqual(fs.kayit_sonucu(kayit)["ozet"][0]["toplam_adet"], D("12"))
        operasyon_guncelle(self.op_kesim, istasyon_id=self.lazer.pk, cikti_miktar=D("5"), satirlar=[(self.profil, D("1"))], boy_mm=D("1292.60"),
                           yan_ciktilar=[(self.yan, D("1"), D("1063.53"))])
        self.assertEqual(fs.kayit_sonucu(kayit)["ozet"][0]["toplam_adet"], D("10"))                        # 10 parça → 2 boy × 5 = 10; güncel tanım
        self.assertEqual(fs.kayit_sonucu(kayit)["cari"], self.salim)

    def test_kayit_fasoncusuz(self):
        kayit = fs.fason_kaydi_olustur(kalemler=[(self.urun1, 5)])
        self.assertIsNone(kayit.cari)
        self.assertIsNone(fs.kayit_sonucu(kayit)["cari"])


class EkranTest(ListeBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("kl_y", password="x"))

    def govde(self, **ek):
        v = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
             "satir-0-urun": self.urun1.pk, "satir-0-miktar": "5", "eylem": "hesapla"}
        v.update(ek)
        return v

    def test_hesapla_fasoncu_kodu_ve_fiyat_sutunlari(self):
        self.fiyatlar()
        r = self.client.post(reverse("core:fason_hesapla"), self.govde(cari=self.salim.pk))
        self.assertContains(r, "GZ-P-00041")
        self.assertContains(r, "Birim Fiyat")
        self.assertContains(r, "yan çıktı")
        self.assertContains(r, "144,00 TRY")
        self.assertNotContains(r, "151-10-0021</span>")                                  # parça adı gösterilir, kodu değil
        r = self.client.post(reverse("core:fason_hesapla"), self.govde())                # fasoncusuz: fiyat sütunu yok
        self.assertNotContains(r, "Birim Fiyat")
        self.assertContains(r, "KESİLMİŞ C AYAK 6+6 SAĞ")

    def test_fasoncu_secimi_yalniz_fiyati_olan_cariler(self):
        self.fiyatlar()
        Cari.objects.create(kod="320-10-0002", unvan="FİYATSIZ CARİ", muhasebe_kodu="320.10.0002")
        r = self.client.get(reverse("core:fason_hesapla"))
        self.assertEqual([c.pk for c in r.context["cariler"]], [self.salim.pk])

    def test_eksik_fiyat_uyarisi(self):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.parca.pk, birim_fiyat=D("10"), gecerlilik_baslangic=date(2026, 1, 1))
        r = self.client.post(reverse("core:fason_hesapla"), self.govde(cari=self.salim.pk))
        self.assertContains(r, "geçerli fason fiyatı olmayan parçalar")

    def test_pdf_ve_kayit_fasoncu_ile(self):
        self.fiyatlar()
        r = self.client.post(reverse("core:fason_hesapla"), self.govde(cari=self.salim.pk, eylem="pdf"))
        self.assertEqual(r["Content-Type"], "application/pdf")
        kayit = FasonKesimKaydi.objects.get()
        self.assertEqual(kayit.cari, self.salim)
        det = self.client.get(reverse("core:fason_kaydi_detay", args=[kayit.pk]))
        self.assertContains(det, "GZ-P-00041")
        self.assertContains(det, "SALİM FASON")
        pdf = self.client.get(reverse("core:fason_kaydi_pdf", args=[kayit.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertGreater(len(pdf.content), 500)

    def test_urun_secici_operasyonlu_bitmis_urunler(self):
        r = self.client.get(reverse("core:fason_hesapla"))
        self.assertContains(r, "152-22-0001")
        Stok.objects.create(kod="152-99-9999", ad="ZİNCİRSİZ ÜRÜN", kategori=self.parca.kategori, uretim_birimi=self.adet, fatura_birimi=self.adet,
                            satis_urunu=True)
        self.assertNotContains(self.client.get(reverse("core:fason_hesapla")), "152-99-9999")
