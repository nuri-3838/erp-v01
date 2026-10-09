"""FASON — fasoncunun faturası: dönüş belgesine bağlama, beklenen tutar ↔ fatura tutarı farkı (uyarı, sessiz düzeltme yok), "faturaya göre güncelle"
(çıktı maliyeti + ortalama yeniden hesap), faturası gelmemiş bedelin 'tahmini' olması ve değerleme raporunda stok değeri − 151 mizanı farkının bu tutara
eşitliği. Fason hizmet faturası = ALIŞ faturası; hizmet kartının kategorisi 151 yarı mamul alt hesabına eşli (fatura 151'e borç yazar)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Fatura, FaturaTipi, HesapPlani, Stok, StokHareket
from core.services import fason_donus as fd
from core.services import stok_ortalama as so
from core.services.fason import FasonHatasi
from core.services.fatura import fatura_olustur, fatura_onayla, fatura_sil, fatura_taslak_olustur
from core.tests.test_fason_donus import TARIH, FasonBase

D = Decimal


class FaturaBase(FasonBase):
    """Fason bedeli (ana 3×10 + yan 1×6 = 36 TL) onaylı dönüşte."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        HesapPlani.objects.create(hesap_kodu="320.10.0001", hesap_adi="SALİM FASON", rapor_grubu="BILANCO", rapor_kalemi="KVYK",
                                  parasal=True, aktif=True)
        HesapPlani.objects.create(hesap_kodu="320.10.0002", hesap_adi="DİĞER FASON", rapor_grubu="BILANCO", rapor_kalemi="KVYK",
                                  parasal=True, aktif=True)
        cls.hizmet = Stok.objects.create(kod="FASON-HIZMET", ad="FASON KESİM HİZMETİ", kategori=cls.kat_ana, uretim_birimi=cls.adet,
                                         fatura_birimi=cls.adet, uretim_urunu=False, satinalma_urunu=True)
        cls.tip = FaturaTipi.objects.get(ad="ALIŞ ÜT")
        cls.no = [0]

    def donus(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar(ana="10", yan="6")
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, irsaliye_no="IRS-1", satirlar=[(self.op.pk, "3")], onayla=True)

    def fatura(self, tutar, onayli=True, cari=None):
        self.no[0] += 1
        fn = fatura_olustur if onayli else fatura_taslak_olustur
        return fn(tip_id=self.tip.pk, cari_id=(cari or self.salim).pk, tarih=TARIH, fatura_no=f"FS-{self.no[0]}",
                  satirlar=[{"stok_id": self.hizmet.pk, "miktar": 1, "birim_fiyat": tutar}])

    def cikti_giris(self, kayit):
        return {h.stok.kod: h for h in StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}

    def satir151(self):
        return next(r for r in so.degerleme_raporu()["karsilastirma"] if r["kod"] == "151")

    def kontrol_sifirdan_hesap_esit(self, stok):
        hs = so.kart_hareketleri(stok)
        for h, s in zip(hs, so.hesapla(hs)):
            self.assertEqual((h.maliyet_durumu, h.tutar_try, h.sonrasi_deger_try), (s.durum, s.tutar_try, s.sonrasi_deger_try))


class FaturaBekleyenTest(FaturaBase):
    def test_faturasiz_bedel_tahmini_ve_degerleme_farki_bu_tutara_esit(self):
        d = self.donus()
        h = self.cikti_giris(d.kayitlar.get())
        self.assertTrue(h["AYAK66"].giris_tahmini and h["AYAK55"].giris_tahmini)
        r = self.satir151()
        self.assertEqual(r["fason_bekleyen"], D("36.00"))
        self.assertEqual(r["ham_fark"], D("36.00"))                      # stok değeri (136) − 151 mizanı (100: yalnız malzeme fişi)
        self.assertEqual(r["fark"], D("0.00"))                           # raporun 'kesin' farkı: bekleyen fason geçici olarak ayrılır
        self.assertEqual(r["toplam_deger"], D("136.00"))
        self.assertEqual(r["mizan"], D("100.00"))

    def test_fatura_baglaninca_fark_sifir(self):
        d = self.donus()
        f = self.fatura("36")                                            # 151.10'a borç 36 (hizmet kartı, depo yok → stok hareketi yok)
        self.assertFalse(StokHareket.objects.filter(fatura_satir__fatura=f).exists())
        fd.faturaya_bagla(d, f)
        h = self.cikti_giris(d.kayitlar.get())
        self.assertFalse(h["AYAK66"].giris_tahmini or h["AYAK55"].giris_tahmini)
        r = self.satir151()
        self.assertEqual((r["fason_bekleyen"], r["ham_fark"], r["fark"]), (D("0.00"), D("0.00"), D("0.00")))
        self.assertEqual(r["mizan"], D("136.00"))                        # 100 malzeme fişi + 36 fatura
        kar = fd.fatura_karsilastirma(f)
        self.assertFalse(kar["fark_var"])
        self.assertEqual((kar["beklenen_try"], kar["fatura_try"]), (D("36.00"), D("36.00")))

    def test_taslak_fatura_baglaninca_tahmini_kalir_onayla_cikar(self):
        d = self.donus()
        f = self.fatura("36", onayli=False)
        fd.faturaya_bagla(d, f)
        self.assertTrue(self.cikti_giris(d.kayitlar.get())["AYAK66"].giris_tahmini)
        self.assertEqual(self.satir151()["fason_bekleyen"], D("36.00"))
        fatura_onayla(f)                                                  # fatura onayı → bağlı dönüşler senkronlanır
        self.assertFalse(self.cikti_giris(d.kayitlar.get())["AYAK66"].giris_tahmini)
        self.assertEqual(self.satir151()["ham_fark"], D("0.00"))

    def test_fatura_silinince_bag_kopar_ve_tahmini_geri_gelir(self):
        d = self.donus()
        f = self.fatura("36")
        fd.faturaya_bagla(d, f)
        fatura_sil(f)
        d.refresh_from_db()
        self.assertIsNone(d.fatura_id)
        self.assertTrue(self.cikti_giris(d.kayitlar.get())["AYAK66"].giris_tahmini)
        r = self.satir151()
        self.assertEqual((r["fason_bekleyen"], r["ham_fark"]), (D("36.00"), D("36.00")))

    def test_faturadan_ayir(self):
        d = self.donus()
        fd.faturaya_bagla(d, self.fatura("36"))
        fd.faturadan_kopar(d)
        d.refresh_from_db()
        self.assertIsNone(d.fatura_id)
        self.assertTrue(self.cikti_giris(d.kayitlar.get())["AYAK55"].giris_tahmini)

    def test_baglama_kurallari(self):
        d = self.donus()
        with self.assertRaises(FasonHatasi):
            fd.faturaya_bagla(d, self.fatura("36", cari=self.diger))        # başka cari
        taslak = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        with self.assertRaises(FasonHatasi):
            fd.faturaya_bagla(taslak, self.fatura("36"))                     # onaysız dönüş
        satis = Fatura.objects.create(cari=self.salim, tarih=TARIH, yon="SATIS", durum="TASLAK")
        with self.assertRaises(FasonHatasi):
            fd.faturaya_bagla(d, satis)


class FaturaFarkTest(FaturaBase):
    def test_fark_uyarisi_sessiz_duzeltme_yok(self):
        d = self.donus()
        f = self.fatura("40")
        fd.faturaya_bagla(d, f)
        kar = fd.fatura_karsilastirma(f)
        self.assertTrue(kar["fark_var"])
        self.assertEqual(kar["fark"], D("4.00"))
        h = self.cikti_giris(d.kayitlar.get())
        self.assertEqual(h["AYAK66"].giris_tutar_try + h["AYAK55"].giris_tutar_try, D("136.00"))     # maliyet otomatik DEĞİŞMEZ
        self.assertEqual(self.satir151()["ham_fark"], D("-4.00"))                                    # mizan 140, stok 136

    def test_faturaya_gore_guncelle_maliyet_ve_ortalama(self):
        d = self.donus()
        f = self.fatura("40")
        fd.faturaya_bagla(d, f)
        sonuc = fd.faturaya_gore_guncelle(f)
        self.assertTrue(sonuc["guncellendi"])
        self.assertFalse(sonuc["fark_var"])
        k = d.kayitlar.get()
        satir = {c.stok.kod: c for c in k.ciktilar.select_related("stok")}
        self.assertEqual(satir["AYAK66"].fason_tutar + satir["AYAK55"].fason_tutar, D("40.00"))      # fatura tutarına BİREBİR
        self.assertEqual(satir["AYAK66"].fason_tutar_usd + satir["AYAK55"].fason_tutar_usd, D("1.00"))  # 40 TL / 40
        h = self.cikti_giris(k)
        self.assertEqual(h["AYAK66"].giris_tutar_try + h["AYAK55"].giris_tutar_try, D("140.00"))
        self.assertEqual(self.satir151()["ham_fark"], D("0.00"))
        for st in (self.ana, self.yan):                                                              # ortalama yeniden hesaplandı
            st.refresh_from_db()
            self.assertEqual(st.maliyet_deger_try, h[st.kod].giris_tutar_try)
            self.assertEqual(st.ort_maliyet_try, so._ort(st.maliyet_deger_try, st.maliyet_miktar))
            self.kontrol_sifirdan_hesap_esit(st)
        k.refresh_from_db()
        self.assertEqual(sum((s.borc for s in k.fis.satirlar.filter(silindi=False)), D("0")), D("100.00"))          # üretim fişi hâlâ yalnız malzeme

    def test_guncelle_idempotent_ve_fark_yoksa_degismez(self):
        d = self.donus()
        f = self.fatura("36")
        fd.faturaya_bagla(d, f)
        self.assertFalse(fd.faturaya_gore_guncelle(f)["guncellendi"])
        f2 = self.fatura("40")
        fd.faturaya_bagla(d, f2)
        fd.faturaya_gore_guncelle(f2)
        self.assertFalse(fd.faturaya_gore_guncelle(f2)["guncellendi"])

    def test_guncelle_kurallari(self):
        self.donus()
        taslak = self.fatura("40", onayli=False)
        with self.assertRaises(FasonHatasi):
            fd.faturaya_gore_guncelle(taslak)                                # taslak fatura
        bagsiz = self.fatura("40")
        with self.assertRaises(FasonHatasi):
            fd.faturaya_gore_guncelle(bagsiz)                                # bağlı dönüş yok

    def test_bir_fatura_birden_cok_donus(self):
        d1 = self.donus()
        self.fason_profil(10, 1000)
        d2 = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        f = self.fatura("80")
        fd.faturaya_bagla(d1, f)
        fd.faturaya_bagla(d2, f)
        kar = fd.fatura_karsilastirma(f)
        self.assertEqual((len(kar["donusler"]), kar["beklenen_try"], kar["fark"]), (2, D("72.00"), D("8.00")))
        fd.faturaya_gore_guncelle(f)
        self.assertEqual(fd.fatura_karsilastirma(f)["beklenen_try"], D("80.00"))
        self.assertFalse(fd.fatura_karsilastirma(f)["fark_var"])


class FaturaEkranTest(FaturaBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("ff4_y", password="x"))

    def test_donus_detayindan_bagla_ve_fatura_detayinda_uyari_ve_guncelle(self):
        d = self.donus()
        f = self.fatura("40")
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "Faturaya bağla")
        self.assertContains(det, "tahmini")
        self.client.post(reverse("core:fason_donus_fatura_bagla", args=[d.pk]), {"fatura": f.pk})
        d.refresh_from_db()
        self.assertEqual(d.fatura_id, f.pk)
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "beklenen fason bedelinden farklı")
        fdet = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(fdet, "Fason dönüşler")
        self.assertContains(fdet, "Maliyeti faturaya göre güncelle")
        self.assertContains(fdet, d.no)
        self.client.post(reverse("core:fason_fatura_guncelle", args=[f.pk]))
        fdet = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(fdet, "Fatura tutarı beklenen fason bedeliyle uyumlu")
        self.assertNotContains(fdet, "Maliyeti faturaya göre güncelle")

    def test_baglanti_ayirma_ekrani(self):
        d = self.donus()
        fd.faturaya_bagla(d, self.fatura("36"))
        self.client.post(reverse("core:fason_donus_fatura_kopar", args=[d.pk]))
        d.refresh_from_db()
        self.assertIsNone(d.fatura_id)

    def test_fason_olmayan_faturada_bolum_yok(self):
        f = self.fatura("36")
        self.assertNotContains(self.client.get(reverse("core:fatura_detay", args=[f.pk])), "Fason dönüşler")

    def test_degerleme_ekraninda_bekleyen_fason_aciklamasi(self):
        d = self.donus()
        r = self.client.get(reverse("core:stok_degerleme_raporu"))
        self.assertContains(r, "faturası bekleyen fason bedeli")
        self.assertContains(r, "36,00 TL")
        fd.faturaya_bagla(d, self.fatura("36"))
        self.assertNotContains(self.client.get(reverse("core:stok_degerleme_raporu")), "faturası bekleyen fason bedeli")
