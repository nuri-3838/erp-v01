"""Operasyon PARÇALA geçişi adım 2: veri göçü (0195) ve çift yazım — OperasyonCikti satırları tanımın cikti/cikti_miktar/boy_mm + yan çıktılarını
birebir yansıtır; olustur/guncelle/sil sonrası tutarlı; göç idempotent ve geri alınabilir."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.test import TestCase

from core.models import Operasyon, OperasyonCikti
from core.services.uretim import operasyon_ciktilari, operasyon_guncelle, operasyon_olustur, operasyon_sil
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
m = importlib.import_module("core.migrations.0195_operasyon_cikti_veri")


def satirlar(op):
    return [(c.stok.kod, c.miktar, c.boy_mm, c.sira, c.surucu) for c in operasyon_ciktilari(op)]


class CiftYazimTest(TamBoyBase):
    def test_olustur_guncelle_sil(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        ana, yan, yan2 = self.stok("ANA"), self.stok("YAN"), self.stok("YAN2")
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))],
                               boy_mm=D("1292.60"), yan_ciktilar=[(yan, D("1"), D("1063.53"))])
        self.assertEqual(satirlar(op), [("ANA", D("3"), D("1292.60"), 0, True), ("YAN", D("1"), D("1063.53"), 10, False)])
        self.assertEqual((op.tur, op.pay_anahtari), (Operasyon.Tur.URET, Operasyon.PayAnahtari.BOY))
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("4"), satirlar=[(profil, D("1"))], boy_mm=D("1000"),
                           yan_ciktilar=[(yan2, D("2"), D("500")), (yan, D("1"), D("900"))])
        self.assertEqual(satirlar(op), [("ANA", D("4"), D("1000.00"), 0, True), ("YAN2", D("2"), D("500.00"), 10, False), ("YAN", D("1"), D("900.00"), 20, False)])
        self.assertEqual(OperasyonCikti.objects.filter(operasyon=op).count(), 5)                 # eskiler soft-delete (iz kalır)
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("4"), satirlar=[(profil, D("1"))], yan_ciktilar=[])
        self.assertEqual(satirlar(op), [("ANA", D("4"), D("1000.00"), 0, True)])
        operasyon_sil(op)
        self.assertEqual(satirlar(op), [])
        yeni = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("2"), satirlar=[(profil, D("1"))])   # sürücü kilidi serbest
        self.assertEqual(satirlar(yeni), [("ANA", D("2"), None, 0, True)])

    def test_yan_cikti_baska_tanimda_surucu_olabilir(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        ana, yan = self.stok("ANA"), self.stok("YAN")
        operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))],
                          boy_mm=D("100"), yan_ciktilar=[(yan, D("1"), D("50"))])
        op2 = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=yan.pk, cikti_miktar=D("5"), satirlar=[(profil, D("1"))])   # YAN'ın kendi tanımı
        self.assertEqual(satirlar(op2), [("YAN", D("5"), None, 0, True)])


class GocTest(TamBoyBase):
    def test_goc_idempotent_ve_geri_alinabilir(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        ana, yan, tek = self.stok("ANA"), self.stok("YAN"), self.stok("TEK")
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))],
                               boy_mm=D("1292.60"), yan_ciktilar=[(yan, D("1"), D("1063.53"))])
        op2 = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=tek.pk, cikti_miktar=D("18"), satirlar=[(profil, D("1"))])
        silinen = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self.stok("SIL").pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))])
        operasyon_sil(silinen)
        OperasyonCikti.objects.all().delete()                                                      # göç öncesi durum
        m.ileri(apps, None)
        self.assertEqual(satirlar(op), [("ANA", D("3"), D("1292.60"), 0, True), ("YAN", D("1"), D("1063.53"), 10, False)])
        self.assertEqual(satirlar(op2), [("TEK", D("18"), None, 0, True)])
        self.assertEqual(OperasyonCikti.objects.filter(operasyon=silinen).count(), 0)              # silinmiş tanım için satır yok
        m.ileri(apps, None)                                                                        # ikinci koşu: mükerrer yok
        self.assertEqual(OperasyonCikti.objects.count(), 3)
        m.geri(apps, None)
        self.assertEqual(OperasyonCikti.objects.count(), 0)
        op.refresh_from_db()
        self.assertEqual((op.cikti.kod, op.cikti_miktar, op.yan_ciktilar.filter(silindi=False).count()), ("ANA", D("3.000"), 1))   # tanım bozulmadı
