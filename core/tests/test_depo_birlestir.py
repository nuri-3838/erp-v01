"""``depo_birlestir``: 151/152 depoları 150 SEMTA DEPO'da birleşir — transfer çiftleri KALICI silinir, kalan bağlı kayıtlar (silinmişler dahil) 150'ye taşınır, kontroller
önce/sonra birebir, depo kartları KALICI silinir; kontrol tutmazsa hepsi geri alınır; --dry-run hiçbir şey yazmaz."""
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import Depo, OperasyonKaydi, StokHareket
from core.services.depo_transfer import depo_transferi_yap
from core.services.hareket import eldeki_miktar, hareket_ekle, hareket_sil
from core.services.stok_ortalama import degerleme_raporu
from core.services.uretim import operasyon_kaydi_olustur
from core.tests import test_depo_sadelestir as ts

D = Decimal


class DepoBirlestirTest(ts.DepoTaban):
    def kur(self):
        self.girisler()                                                                                   # ham: 150=10, 151=20; mamul: 151=5, 999=7
        depo_transferi_yap(stok_id=self.ham.pk, kaynak_depo_id=self.d151.pk, hedef_depo_id=self.d150.pk, tarih=date(2026, 1, 9), miktar=D("4"))   # 151→150 çifti
        depo_transferi_yap(stok_id=self.stok.pk, kaynak_depo_id=self.d151.pk, hedef_depo_id=self.d150.pk, tarih=date(2026, 1, 9), miktar=D("5"))
        gec = hareket_ekle(stok_id=self.ham.pk, depo_id=self.d151.pk, tarih=date(2026, 1, 4), tur=StokHareket.Tur.GIRIS, miktar=D("3"), giris_tutar_try=D("30"))
        hareket_sil(gec)                                                                                  # SİLİNMİŞ 151 hareketi
        self.kayit = operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.d151.pk, tarih=date(2026, 1, 10), hedef_cikti_miktari=D("1"))

    def calistir(self, *args):
        cikti = StringIO()
        call_command("depo_birlestir", *args, stdout=cikti)
        return cikti.getvalue()

    def anlik(self):
        return ({s.pk: eldeki_miktar(s) for s in (self.ham, self.stok)},
                [(k["kod"], k["stok_degeri"], k["mizan"], k["fark"]) for k in degerleme_raporu()["karsilastirma"]])

    def test_dry_run_geri_alir_gercek_koşu_birlestirir(self):
        self.kur()
        once = self.anlik()
        n_hareket = StokHareket.objects.count()
        cikti = self.calistir("--dry-run")
        self.assertIn("DRY-RUN", cikti)
        self.assertIn("4) Kontroller", cikti)
        self.assertEqual((StokHareket.objects.count(), Depo.objects.filter(kod__in=["151", "152"]).count()), (n_hareket, 2))   # dry-run: hiçbir şey yazılmadı
        self.assertEqual(OperasyonKaydi.objects.get(pk=self.kayit.pk).depo_id, self.d151.pk)

        cikti = self.calistir()
        self.assertIn("TAMAM", cikti)
        self.assertIn("2 transfer çifti (4 bacak) KALICI silindi", cikti)
        self.assertEqual(Depo.objects.filter(kod__in=["151", "152"]).count(), 0)                         # kalıcı silindi (soft değil)
        self.assertFalse(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.TRANSFER, tarih=date(2026, 1, 9)).exists())   # transfer bacakları fiziksel olarak yok
        self.assertFalse(StokHareket.objects.filter(depo_id=self.d151.pk).exists())
        self.assertEqual(OperasyonKaydi.objects.get(pk=self.kayit.pk).depo_id, self.d150.pk)
        self.assertEqual(StokHareket.objects.filter(depo=self.d150, silindi=True).count(), 1)             # silinmiş hareket de 150'ye taşındı
        self.assertEqual(eldeki_miktar(self.ham, self.d150.pk), D("30"))                                  # 10 + 20
        self.assertEqual(eldeki_miktar(self.stok, self.d150.pk), D("5"))
        self.assertEqual(eldeki_miktar(self.stok, self.d999.pk), D("7"))                                  # fason deposu aynen
        self.assertEqual(self.anlik(), once)
        self.assertIn("Zaten tamam", self.calistir())

    def test_kontrol_basarisizsa_tamami_geri_alinir(self):
        self.kur()
        n_hareket = StokHareket.objects.count()
        with mock.patch("core.services.stok_ortalama.yeniden_hesapla", return_value=[object()]):
            with self.assertRaisesMessage(CommandError, "maliyeti değişti"):
                self.calistir()
        self.assertEqual((StokHareket.objects.count(), Depo.objects.filter(kod__in=["151", "152"]).count()), (n_hareket, 2))   # transfer silmesi dahil geri alındı
        self.assertTrue(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.TRANSFER).exists())
        self.assertEqual(OperasyonKaydi.objects.get(pk=self.kayit.pk).depo_id, self.d151.pk)

    def test_tam_cift_olmayan_transfer_reddedilir(self):
        self.kur()
        StokHareket.objects.filter(kaynak=StokHareket.Kaynak.TRANSFER, depo=self.d150).first().delete()      # tek bacak kaldı
        with self.assertRaisesMessage(CommandError, "tam çift değil"):
            self.calistir()
        self.assertEqual(Depo.objects.filter(kod__in=["151", "152"]).count(), 2)
