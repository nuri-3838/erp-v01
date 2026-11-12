"""maliyet_bayrak_duzelt (2026-11-12): girdisi artık tamamen KESİN olduğu hâlde 'tahmini/GEÇİCİ' kalmış BAYAT üretim girişi bayrakları temizlenir; YALNIZ bayraklar değişir —
hareket tutarları, fişler, mizan, kart değerleri aynı kalır (doğrulanır, tutmazsa geri alınır); kuru çalıştırma iz bırakmaz; ikinci çalıştırma iş çıkarmaz."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import StokHareket, YevmiyeFisi, YevmiyeSatir
from core.services import maliyet_bayrak
from core.services import stok_ortalama as so
from core.services.hareket import hareket_ekle
from core.services.uretim import operasyon_kaydi_onayla
from core.tests.test_uretim_yan_cikti import YanCiktiBase

DEC = Decimal
G = StokHareket.Tur.GIRIS


class BayatBayrakTaban(YanCiktiBase):
    def kur_bayat(self):
        """Girdi fiyatsız giriş sonrası üretim → çıktılar tahmini; fiyat aynı tutarla gelir (motor yayar); sonra çıktı bayrakları ESKİ davranışta olduğu gibi bayata çevrilir."""
        self.profil_gir(10, 1000)
        self.fiyatsiz = hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 2, 1), tur=G, miktar=DEC("10"))
        self.k = self.kayit("3")
        operasyon_kaydi_onayla(self.k)
        StokHareket.objects.filter(pk=self.fiyatsiz.pk).update(giris_tutar_try=DEC("1000"), giris_tutar_usd=None)
        so.yeniden_hesapla(self.profil)
        StokHareket.objects.filter(operasyon_kaydi=self.k, tur=G).update(giris_tahmini=True, maliyet_durumu=so.GECICI)             # bayat işaret

    def calistir(self, *arg):
        cikti = StringIO()
        call_command("maliyet_bayrak_duzelt", *arg, stdout=cikti)
        return cikti.getvalue()

    def sabit(self):
        return sorted((h.pk, h.miktar, h.tutar_try, h.tutar_usd, h.giris_tutar_try, h.giris_tutar_usd, h.sonrasi_miktar, h.sonrasi_deger_try)
                      for h in StokHareket.objects.filter(silindi=False))

    def bayraklar(self):
        return sorted((h.pk, h.giris_tahmini, h.maliyet_durumu) for h in StokHareket.objects.filter(silindi=False))


class KuruCalistirmaTest(BayatBayrakTaban):
    def test_kuru_calistirma_bayat_bayraklari_raporlar_ama_yazmaz(self):
        self.kur_bayat()
        bayat = maliyet_bayrak.bayat_bayraklar()
        self.assertEqual(len(bayat), 2)                                                                    # ana + yan çıktı girişi
        once = self.bayraklar()
        cikti = self.calistir()
        self.assertIn("KURU ÇALIŞTIRMA", cikti)
        self.assertIn("Bayat bayrak: önce 2 → sonra 0", cikti)
        self.assertIn("DÜZELEN HAREKET: 2", cikti)
        self.assertIn("giris_tahmini tahmini → kesin: 2", cikti)
        self.assertIn("fiş yazımı (satır eklenen ya da updated_at değişen fiş): 0", cikti)
        self.assertNotIn("[HATA]", cikti)
        self.assertEqual(self.bayraklar(), once)                                                           # geri alındı
        self.assertEqual(len(maliyet_bayrak.bayat_bayraklar()), 2)


class UygulamaTest(BayatBayrakTaban):
    def test_uygula_yalniz_bayraklar_degisir_tutar_fis_mizan_ayni(self):
        self.kur_bayat()
        sabit_once, satir_once = self.sabit(), YevmiyeSatir.objects.count()
        fis = YevmiyeFisi.objects.get(pk=self.k.fis_id)
        guncel_once = fis.updated_at
        cikti = self.calistir("--uygula")
        self.assertIn("UYGULANDI", cikti)
        self.assertEqual(maliyet_bayrak.bayat_bayraklar(), [])
        for h in StokHareket.objects.filter(operasyon_kaydi=self.k, tur=G):
            self.assertEqual((h.giris_tahmini, h.maliyet_durumu), (False, so.KESIN))
        self.assertEqual(self.sabit(), sabit_once)                                                         # tutarlar/miktarlar aynı
        self.assertEqual(YevmiyeSatir.objects.count(), satir_once)                                         # fiş satırı yeniden yazılmadı
        fis.refresh_from_db()
        self.assertEqual(fis.updated_at, guncel_once)

    def test_ikinci_calistirma_is_cikarmaz(self):
        self.kur_bayat()
        self.calistir("--uygula")
        once = self.bayraklar()
        cikti = self.calistir("--uygula")
        self.assertIn("DÜZELEN HAREKET: 0", cikti)
        self.assertEqual(self.bayraklar(), once)

    def test_bayat_olmayan_veride_is_cikarmaz(self):
        self.profil_gir(10, 1000)
        operasyon_kaydi_onayla(self.kayit("3"))
        once = self.bayraklar()
        cikti = self.calistir("--uygula")
        self.assertIn("DÜZELEN HAREKET: 0", cikti)
        self.assertEqual(self.bayraklar(), once)

    def test_tutar_degisecekse_islem_durur_ve_hicbir_sey_yazilmaz(self):
        """Saklanan bir tutar önbelleği bozuksa (sıfırdan hesapla eşit değil) komut bayrak dışı değişikliği yazmaz: doğrulama başarısız → geri alınır."""
        self.kur_bayat()
        cikis = StokHareket.objects.get(operasyon_kaydi=self.k, tur=StokHareket.Tur.CIKIS)
        StokHareket.objects.filter(pk=cikis.pk).update(tutar_try=cikis.tutar_try + DEC("1"))
        once = (self.bayraklar(), self.sabit())
        with self.assertRaisesMessage(CommandError, "hiçbir hareketin miktar/tutar"):
            self.calistir("--uygula")
        self.assertEqual((self.bayraklar(), self.sabit()), once)
