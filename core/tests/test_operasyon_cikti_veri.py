"""Operasyon PARÇALA geçişi: OperasyonCikti satırları tanımın referans çıktısı (cikti/cikti_miktar/boy_mm kopyası) + yan çıktılarını birebir
yansıtır; olustur/guncelle/sil sonrası tutarlı. (Veri göçü 0195 canlıda bir kez koşar; eski yan çıktı tablosu 0197 ile kaldırıldı.)"""
from decimal import Decimal

from core.models import Operasyon, OperasyonCikti
from core.services.uretim import operasyon_ciktilari, operasyon_guncelle, operasyon_olustur, operasyon_sil
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal


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
