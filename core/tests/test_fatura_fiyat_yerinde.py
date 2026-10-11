"""Onaylı alış faturasında YALNIZ birim fiyat değişince stok girişi silinmeden yerinde güncellenir (kısmen tüketilmiş giriş de
düzeltilebilir) ve mevcut maliyet zinciri — girdi tüketimi → mamul girişi + 15x aktarım fişi → satış çıkışı + 620 fişi — kendiliğinden
yenilenir; fiş sayısı değişmez. Miktar/stok/depo/tarih değişince eski yol (sil + yeniden yaz; tüketilmişse ret) aynen kalır."""
from datetime import date
from decimal import Decimal

from core.models import FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur, StokHareket, YevmiyeFisi
from core.services import fatura as fs
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.tests import test_uretim_siparisi_kapanis as kt
from core.tests.test_teklif_siparis import _hesap

D = Decimal


class AlisFiyatYerindeTest(kt.KapanisTaban):
    """KapanisTaban zinciri (sipariş→ÜS→üretim→depolu satış faturası) + ham 151.10, mamul 152.10, satış tipinde 620 maliyet fişi."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for t in (date(2026, 1, 1), date(2026, 1, 2)):
            Kur.objects.create(tarih=t, usd_alis=D("40"))
        _hesap("151.10", "YARI MAMUL")
        _hesap("152.10", "MAMUL")
        _hesap("620", "SMM", grup="GELIR_TABLOSU", kalem="A")
        k_ham = Kategori.objects.create(ad="HAM151", kod="h151")
        k_mam = Kategori.objects.create(ad="MAMUL152", kod="m152")
        KategoriHesap.objects.create(kategori=k_ham, fatura_tipi=cls.alis_tip, hesap=HesapPlani.objects.get(hesap_kodu="151.10"))
        KategoriHesap.objects.create(kategori=k_mam, fatura_tipi=cls.alis_tip, hesap=HesapPlani.objects.get(hesap_kodu="152.10"))
        KategoriHesap.objects.create(kategori=k_mam, fatura_tipi=cls.satis_tip, hesap=HesapPlani.objects.get(hesap_kodu="600"))
        cls.ham.kategori = k_ham
        cls.ham.save()
        cls.stok.kategori = k_mam
        cls.stok.save()
        cls.satis_tip.maliyet_fisi = FaturaTipi.MaliyetFisi.SATIS
        cls.satis_tip.save()

    def setUp(self):
        self.client.force_login(self.yon)          # KapanisTaban'ın 100 adet @10 TL elle girişi YOK

    def alis(self):
        return fs.fatura_olustur(tip_id=self.alis_tip.pk, cari_id=self.tedarikci.pk, tarih=date(2026, 1, 2), fatura_no="A1",
                                 satirlar=[{"stok_id": self.ham.pk, "miktar": "10", "birim_fiyat": "1"}], depo_id=self.depo.pk, kullanici=self.yon)

    def guncelle(self, f, *, miktar="10", fiyat="2", tarih=None):
        return fs.fatura_guncelle(f, tip_id=self.alis_tip.pk, cari_id=self.tedarikci.pk, tarih=tarih or f.tarih, fatura_no="A1",
                                  satirlar=[{"stok_id": self.ham.pk, "miktar": miktar, "birim_fiyat": fiyat}], depo_id=self.depo.pk, kullanici=self.yon)

    def zincir(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        _r, mesajlar = self.fatura_kes(sip)
        assert sip.fatura_id, mesajlar
        return sip

    @staticmethod
    def fis_borc(fis, kod):
        return sum((s.borc for s in fis.satirlar.filter(silindi=False, hesap__hesap_kodu=kod)), D("0"))

    def giris(self):
        return StokHareket.objects.get(stok=self.ham, tur="GIRIS", kaynak="FATURA", silindi=False)

    def durum(self, sip):
        tuketim = StokHareket.objects.get(stok=self.ham, tur="CIKIS", silindi=False)
        cikti = StokHareket.objects.get(stok=self.stok, tur="GIRIS", kaynak="URETIM", silindi=False)
        satis = StokHareket.objects.get(fatura_satir__fatura=sip.fatura, tur="CIKIS", silindi=False)
        return dict(tuketim=tuketim.tutar_try, cikti=cikti.tutar_try, satis=satis.tutar_try,
                    aktarim_152=self.fis_borc(cikti.operasyon_kaydi.fis, "152.10"), smm_620=self.fis_borc(satis.fis, "620"))

    def esit(self, sip, tutar):
        self.assertEqual(self.durum(sip), dict(tuketim=tutar, cikti=tutar, satis=tutar, aktarim_152=tutar, smm_620=tutar))

    def test_a_tuketilmis_giriste_fiyat_duzeltme_kabul_zincir_yenilenir(self):
        f = self.alis()                                     # 10 adet @ 1 TL; 3'ü üretimde tüketildi + satıldı → eldeki 7 < 10
        sip = self.zincir()
        self.esit(sip, D("3.00"))
        giris_pk, fis_sayisi = self.giris().pk, YevmiyeFisi.objects.filter(silindi=False).count()
        self.guncelle(f, fiyat="2")                         # eskiden "negatife düşer" ile reddedilirdi
        g = self.giris()
        self.assertEqual((g.pk, g.giris_tutar_try, g.miktar, g.maliyet_durumu), (giris_pk, D("20.00"), D("10.000"), "KESIN"))   # yerinde
        self.assertEqual(g.fatura_satir.fatura_id, f.pk)
        self.assertFalse(g.fatura_satir.silindi)            # yeni satıra bağlandı
        self.assertEqual(g.maliyet_fatura_satir_id, g.fatura_satir_id)
        self.esit(sip, D("6.00"))                           # 3 × 2 TL zincir boyunca
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), fis_sayisi)            # fişler yerinde güncellendi
        f.refresh_from_db()
        self.assertEqual(self.fis_borc(f.fis, "151.10"), D("20.00"))                               # fatura fişi de yeni tutarda
        self.assertEqual(eldeki_miktar(self.ham), D("7"))

    def test_b_tuketilmis_giriste_miktar_degisikligi_hala_reddedilir(self):
        f = self.alis()
        sip = self.zincir()
        with self.assertRaisesMessage(fs.FaturaHatasi, "negatife düşer"):
            self.guncelle(f, miktar="8", fiyat="2")
        self.esit(sip, D("3.00"))                           # hiçbir şey değişmedi (atomik)
        self.assertEqual(self.giris().giris_tutar_try, D("10.00"))

    def test_b2_tarih_degisince_eski_yol(self):
        f = self.alis()
        self.zincir()
        with self.assertRaisesMessage(fs.FaturaHatasi, "negatife düşer"):
            self.guncelle(f, fiyat="2", tarih=date(2026, 1, 1))

    def test_c_tuketilmemis_giriste_fiyat_duzeltme_eskisi_gibi(self):
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS,
                     miktar=D("10"), giris_tutar_try=D("10"))           # elle 10 @ 1 TL
        f = self.alis()                                                  # fatura 10 @ 1 TL
        sip = self.zincir()
        self.esit(sip, D("3.00"))
        self.guncelle(f, fiyat="2")
        # ortalama (10×1 + 10×2) / 20 = 1,50 → 3 adet = 4,50 TL zincir boyunca
        self.esit(sip, D("4.50"))
        self.assertEqual(self.giris().giris_tutar_try, D("20.00"))

    def test_c2_tuketilmemis_giriste_miktar_degisikligi_sil_yeniden_yaz(self):
        f = self.alis()
        eski_pk = self.giris().pk
        self.guncelle(f, miktar="8", fiyat="2")
        g = self.giris()
        self.assertNotEqual(g.pk, eski_pk)
        self.assertEqual((g.miktar, g.giris_tutar_try), (D("8.000"), D("16.00")))
        self.assertTrue(StokHareket.objects.get(pk=eski_pk).silindi)
