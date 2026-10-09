"""HİZMET (stoksuz) kategori: bu kategorideki kartlar hiçbir koşulda stok hareketi yazmaz (fatura kalemi atlanır, elle hareket/transfer/sarf/üretim reddedilir),
değerleme raporuna girmez; fatura muhasebe fişi kategori hesap haritasına göre normal oluşur (151.10 borç); karışık faturada stoklu kalem hareket üretir."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import HesapPlani, Kategori, KategoriHesap, Stok, StokHareket
from core.services import fason_donus as fd
from core.services import kategori as kategori_servis
from core.services.depo_transfer import depo_transferi_yap
from core.services.fatura import fatura_guncelle, fatura_olustur, fatura_taslak_olustur, fatura_onayla
from core.services.hareket import HareketHatasi, hareket_ekle, hizmet_mi
from core.services.kategori import KategoriHatasi
from core.services.uretim import operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur, UretimHatasi
from core.tests.test_fason_donus import TARIH
from core.tests.test_fason_fatura import FaturaBase

D = Decimal


class HizmetBase(FaturaBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ust = Kategori.objects.create(kod="HU", ad="HİZMETLER")
        cls.hz_kat = Kategori.objects.create(kod="HZ", ad="FASON HİZMET", ust=ust, hizmet_kategorisi=True)
        KategoriHesap.objects.create(kategori=cls.hz_kat, hesap=HesapPlani.objects.get(hesap_kodu="151.10"), fatura_tipi=cls.tip)
        cls.hz = Stok.objects.create(kod="151-15-0001", ad="FASON KESİM HİZMETİ", kategori=cls.hz_kat, uretim_birimi=cls.adet, fatura_birimi=cls.adet,
                                     uretim_urunu=False, satinalma_urunu=True)

    def fatura_x(self, satirlar, depo=True, onayli=True, **kw):
        self.no[0] += 1
        fn = fatura_olustur if onayli else fatura_taslak_olustur
        return fn(tip_id=self.tip.pk, cari_id=self.salim.pk, tarih=TARIH, fatura_no=f"HZ-{self.no[0]}", satirlar=satirlar,
                  depo_id=self.depo.pk if depo else None, **kw)

    def hareketler(self, fatura):
        return list(StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False).select_related("stok"))

    def fis_borc(self, fatura):
        return {s.hesap.hesap_kodu: s.borc for s in fatura.fis.satirlar.filter(silindi=False) if s.borc}


class FaturaTest(HizmetBase):
    def test_depo_secili_hizmet_faturasi_hareket_uretmez(self):
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")}])
        self.assertEqual(self.hareketler(f), [])
        self.assertIsNone(f.depo_id)                                    # yalnız hizmet kalemi → depo yok sayıldı
        self.assertEqual(self.fis_borc(f), {"151.10": D("36.00")})      # muhasebe: kategori haritasına göre 151.10 borç

    def test_karisik_faturada_stoklu_kalem_hareket_uretir(self):
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")},
                           {"stok_id": self.profil.pk, "miktar": 2, "birim_fiyat": D("50")}])
        h = self.hareketler(f)
        self.assertEqual([(x.stok.kod, x.miktar) for x in h], [("PROFIL", D("2"))])
        self.assertEqual(f.depo_id, self.depo.pk)
        self.assertEqual(self.fis_borc(f), {"151.10": D("36.00"), "150.10": D("100.00")})

    def test_taslak_onay_yolu(self):
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")}], onayli=False)
        self.assertIsNone(f.depo_id)
        fatura_onayla(f)
        self.assertEqual(self.hareketler(f), [])

    def test_onayli_fatura_yeniden_yazilinca_hizmet_hareket_uretmez(self):
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")},
                           {"stok_id": self.profil.pk, "miktar": 2, "birim_fiyat": D("50")}])
        f = fatura_guncelle(f, tip_id=self.tip.pk, cari_id=self.salim.pk, tarih=TARIH, fatura_no=f.fatura_no, depo_id=self.depo.pk,
                            satirlar=[{"stok_id": self.hz.pk, "miktar": 2, "birim_fiyat": D("36")},
                                      {"stok_id": self.profil.pk, "miktar": 3, "birim_fiyat": D("50")}])
        self.assertEqual([(x.stok.kod, x.miktar) for x in self.hareketler(f)], [("PROFIL", D("3"))])
        self.assertEqual(self.fis_borc(f)["151.10"], D("72.00"))
        f = fatura_guncelle(f, tip_id=self.tip.pk, cari_id=self.salim.pk, tarih=TARIH, fatura_no=f.fatura_no, depo_id=self.depo.pk,
                            satirlar=[{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")}])           # artık yalnız hizmet
        self.assertEqual(self.hareketler(f), [])
        self.assertIsNone(f.depo_id)

    def test_stoklu_fatura_etkilenmez(self):
        f = self.fatura_x([{"stok_id": self.profil.pk, "miktar": 2, "birim_fiyat": D("50")}])
        self.assertEqual(len(self.hareketler(f)), 1)
        self.assertEqual(f.depo_id, self.depo.pk)

    def test_hizmet_kalemi_irsaliye_farki_gurultusu_yok(self):
        from core.services.fatura import irsaliye_miktar_farklari
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")}])
        self.assertEqual(irsaliye_miktar_farklari(f), [])

    def test_fason_baglamada_degerleme_151_farki_sifir(self):
        d = self.donus()
        self.assertEqual(self.satir151()["ham_fark"], D("36.00"))
        f = self.fatura_x([{"stok_id": self.hz.pk, "miktar": 1, "birim_fiyat": D("36")}])
        fd.faturaya_bagla(d, f)
        r = self.satir151()
        self.assertEqual((r["ham_fark"], r["fark"], r["fason_bekleyen"]), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(r["mizan"], D("136.00"))


class HareketYollariTest(HizmetBase):
    def test_hareket_ekle_reddeder(self):
        self.assertTrue(hizmet_mi(Stok.objects.get(pk=self.hz.pk)))
        with self.assertRaises(HareketHatasi) as c:
            hareket_ekle(stok_id=self.hz.pk, depo_id=self.depo.pk, tarih=TARIH, tur=StokHareket.Tur.GIRIS, miktar=D("1"))
        self.assertIn("hizmet kartı", str(c.exception))
        self.assertFalse(StokHareket.objects.filter(stok=self.hz).exists())

    def test_transfer_reddeder(self):
        with self.assertRaises(HareketHatasi):
            depo_transferi_yap(stok_id=self.hz.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=TARIH, miktar=D("1"))
        self.assertFalse(StokHareket.objects.filter(stok=self.hz).exists())

    def test_uretim_girdisi_olarak_reddedilir(self):
        cikti = self._stok("HZ-CIKTI", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=cikti.pk, cikti_miktar=D("1"), satirlar=[(self.hz, D("1"))])
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("1"))
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("hizmet kartı", str(c.exception))

    def test_ekranlar(self):
        self.client.force_login(User.objects.create_superuser("hz_y", password="x"))
        for ad in ("stok_hareket_ekle", "stok_depo_transferi", "stok_sarf_ekle"):
            r = self.client.get(reverse(f"core:{ad}", args=[self.hz.pk]), follow=True)
            self.assertContains(r, "hizmet kartı")
            r = self.client.post(reverse(f"core:{ad}", args=[self.hz.pk]), {}, follow=True)
            self.assertContains(r, "hizmet kartı")
        self.assertFalse(StokHareket.objects.filter(stok=self.hz).exists())

    def test_normal_kart_etkilenmez(self):
        self.client.force_login(User.objects.create_superuser("hz_y2", password="x"))
        self.assertEqual(self.client.get(reverse("core:stok_hareket_ekle", args=[self.profil.pk])).status_code, 200)

    def test_stok_detay_ve_liste(self):
        self.client.force_login(User.objects.create_superuser("hz_y3", password="x"))
        det = self.client.get(reverse("core:stok_detay", args=[self.hz.pk]))
        self.assertContains(det, "Hizmet kartı — stok tutulmaz")
        self.assertNotContains(det, reverse("core:stok_hareket_ekle", args=[self.hz.pk]))
        self.assertNotContains(det, "Mevcut Miktar")
        det2 = self.client.get(reverse("core:stok_detay", args=[self.profil.pk]))
        self.assertContains(det2, reverse("core:stok_hareket_ekle", args=[self.profil.pk]))
        self.assertContains(det2, "Mevcut Miktar")
        self.assertNotContains(det2, "Hizmet kartı — stok tutulmaz")
        liste = self.client.get(reverse("core:stoklar"))
        self.assertContains(liste, "Hizmet kartı — stok tutulmaz")

    def test_degerleme_raporuna_girmez(self):
        from core.services import stok_ortalama as so
        k = Kategori.objects.create(kod="HZ2", ad="ESKİ HİZMET", ust=self.hz_kat.ust)
        s = Stok.objects.create(kod="151-15-0002", ad="ESKİ HİZMET KARTI", kategori=k, uretim_birimi=self.adet, fatura_birimi=self.adet, satinalma_urunu=True)
        hareket_ekle(stok_id=s.pk, depo_id=self.depo.pk, tarih=TARIH, tur=StokHareket.Tur.GIRIS, miktar=D("5"), giris_tutar_try=D("50"))
        self.assertIn(s.pk, [r["stok"].pk for r in so.degerleme_raporu()["satirlar"]])
        Kategori.objects.filter(pk=k.pk).update(hizmet_kategorisi=True)                # sonradan işaretlendi (servis engeller, model güncellemesi atlar)
        self.assertNotIn(s.pk, [r["stok"].pk for r in so.degerleme_raporu()["satirlar"]])


class KategoriAyariTest(HizmetBase):
    def hareketli_alt(self):
        """Üst kategori altında, aktif stok hareketi olan bir alt kategori."""
        k = Kategori.objects.create(kod="HK", ad="HAREKETLİ ALT", ust=self.hz_kat.ust)
        s = Stok.objects.create(kod="HK-1", ad="HAREKETLİ", kategori=k, uretim_birimi=self.adet, fatura_birimi=self.adet, satinalma_urunu=True)
        hareket_ekle(stok_id=s.pk, depo_id=self.depo.pk, tarih=TARIH, tur=StokHareket.Tur.GIRIS, miktar=D("1"), giris_tutar_try=D("10"))
        return k

    def test_yalniz_alt_kategoride(self):
        with self.assertRaises(KategoriHatasi):
            kategori_servis.kategori_olustur(ad="KÖK HİZMET", kod="KH", hizmet=True)
        k = kategori_servis.kategori_olustur(ad="ALT HİZMET", kod="AH", ust_id=self.hz_kat.ust_id, hizmet=True)
        self.assertTrue(k.hizmet_kategorisi)
        with self.assertRaises(KategoriHatasi):
            kategori_servis.kategori_guncelle(self.hz_kat.ust, ad="HİZMETLER", kod="HU", hizmet=True)

    def test_hareketli_kategori_isaretlenemez(self):
        k = self.hareketli_alt()
        with self.assertRaises(KategoriHatasi) as c:
            kategori_servis.kategori_guncelle(k, ad=k.ad, kod=k.kod, hizmet=True)
        self.assertIn("stok hareketi", str(c.exception))
        k.refresh_from_db()
        self.assertFalse(k.hizmet_kategorisi)

    def test_hareketsiz_kategori_acilip_kapanir_ve_hizmet_none_degistirmez(self):
        k = Kategori.objects.create(kod="HZ3", ad="BOŞ ALT", ust=self.hz_kat.ust)
        kategori_servis.kategori_guncelle(k, ad="BOŞ ALT", kod="HZ3", hizmet=True)
        k.refresh_from_db()
        self.assertTrue(k.hizmet_kategorisi)
        kategori_servis.kategori_guncelle(k, ad="BOŞ ALT 2", kod="HZ3")                    # hizmet verilmedi → değişmez
        k.refresh_from_db()
        self.assertTrue(k.hizmet_kategorisi)
        kategori_servis.kategori_guncelle(k, ad="BOŞ ALT 2", kod="HZ3", hizmet=False)
        k.refresh_from_db()
        self.assertFalse(k.hizmet_kategorisi)

    def test_kategori_ekrani(self):
        self.client.force_login(User.objects.create_superuser("hz_y4", password="x"))
        r = self.client.get(reverse("core:kategori_duzenle", args=[self.hz_kat.pk]))
        self.assertContains(r, "Hizmet (stok hareketi yapmaz)")
        self.assertTrue(r.context["form"].initial["hizmet_kategorisi"])
        r = self.client.get(reverse("core:kategori_duzenle", args=[self.hz_kat.ust_id]))             # üst kategoride kutu yok
        self.assertNotContains(r, "Hizmet (stok hareketi yapmaz)")
        k = Kategori.objects.create(kod="HZ4", ad="EKRAN ALT", ust=self.hz_kat.ust)
        r = self.client.post(reverse("core:kategori_duzenle", args=[k.pk]), {"ad": "EKRAN ALT", "kod": "HZ4", "hizmet_kategorisi": "on"})
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertTrue(k.hizmet_kategorisi)
        r = self.client.post(reverse("core:kategori_duzenle", args=[k.pk]), {"ad": "EKRAN ALT", "kod": "HZ4"})
        k.refresh_from_db()
        self.assertFalse(k.hizmet_kategorisi)
        h = self.hareketli_alt()
        r = self.client.post(reverse("core:kategori_duzenle", args=[h.pk]), {"ad": h.ad, "kod": h.kod, "hizmet_kategorisi": "on"})
        self.assertEqual(r.status_code, 200)                                                        # hareketli → hata, formda kalır
        self.assertContains(r, "aktif stok hareketi var")
