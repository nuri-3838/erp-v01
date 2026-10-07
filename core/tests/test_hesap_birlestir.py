"""679→649 / 689→659 birleştirme: demirbaş kârı 649, KDV mahsubu fark yönleri 649/659, pasif hesabın formlarda çıkmaması,
taşıma servisinin doğruluğu ve idempotentliği."""
import datetime
from decimal import Decimal as Dc

from django.contrib.auth.models import User
from django.test import TestCase

from core.forms import KdvMahsupForm
from core.models import HesapPlani, Kur, KdvMahsup, YevmiyeFisi, YevmiyeSatir
from core.services import hesap_birlestir as hb
from core.services import kdv_mahsup as km
from core.services.cari_kesinti import kesinti_hesap_kumesi
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.services.raporlar import _devir

D = datetime.date


def _h(kod, ad, kalem="E", grup="GELIR_TABLOSU"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem, parasal=(grup == "BILANCO"))


def _bak(k):
    return _devir(k, D(2100, 1, 1))[0]


class BirlestirTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 8, 25), usd_alis=Dc("30"))
        _h("100", "KASA", "A", "BILANCO")
        _h("649", "DİĞER OLAĞAN GELİR", "E")
        _h("659", "DİĞER OLAĞAN GİDER", "F")
        _h("679", "DİĞER OLAĞANDIŞI GELİR", "H")
        _h("689", "DİĞER OLAĞANDIŞI GİDER", "I")
        cls.su = User.objects.create_superuser("su", password="x")

    def _fis(self, borc, alacak, tutar, aciklama):
        return fis_olustur(tarih=D(2026, 8, 25), aciklama=aciklama, kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu=borc, taraf="B", islem_tutari=tutar), SatirGirdi(hesap_kodu=alacak, taraf="A", islem_tutari=tutar)])

    def test_tasima_ve_idempotent(self):
        self._fis("100", "679", "500.00", "kar")
        self._fis("689", "100", "30.25", "zarar")
        self._fis("100", "649", "10.00", "eski 649")
        fis_n, satir_n = YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count()
        onc = {k: _bak(k) for k in ("649", "659", "679", "689")}
        tarihler = list(YevmiyeSatir.objects.order_by("pk").values_list("fis__tarih", "borc", "alacak", "aciklama", "fis__kaynak", "fis_id"))
        hb.birlestir(uygula=False, cikti=lambda *_: None)                               # önizleme: yazmaz
        self.assertEqual(_bak("679"), onc["679"])
        r = hb.birlestir(uygula=True, cikti=lambda *_: None)
        self.assertTrue(r["uygulandi"])
        self.assertEqual((_bak("679"), _bak("689")), (Dc("0.00"), Dc("0.00")))
        self.assertEqual(_bak("649"), onc["649"] + onc["679"])
        self.assertEqual(_bak("659"), onc["659"] + onc["689"])
        self.assertEqual((YevmiyeFisi.objects.count(), YevmiyeSatir.objects.count()), (fis_n, satir_n))
        self.assertEqual(tarihler, list(YevmiyeSatir.objects.order_by("pk").values_list("fis__tarih", "borc", "alacak", "aciklama", "fis__kaynak", "fis_id")))
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="679").aktif)
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="689").aktif)
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="679", silindi=False).exists())     # silinmedi
        r2 = hb.birlestir(uygula=True, cikti=lambda *_: None)                           # ikinci çalıştırma: yapılacak iş yok
        self.assertEqual(r2["tasinan"], {})
        self.assertEqual((_bak("649"), _bak("659")), (onc["649"] + onc["679"], onc["659"] + onc["689"]))

    def test_kdv_mahsup_fark_hesabi_tasinir(self):
        kdv = KdvMahsup(yil=2026, ay=8, fark_hesap=HesapPlani.objects.get(hesap_kodu="689"), created_by=self.su, updated_by=self.su)
        kdv.donem_sonu = D(2026, 8, 31)
        for alan in ("beyan_devreden", "beyan_odenecek", "erp_191", "erp_391", "erp_190", "erp_devreden", "fark"):
            if hasattr(kdv, alan):
                setattr(kdv, alan, Dc("0"))
        kdv.save()
        hb.birlestir(uygula=True, cikti=lambda *_: None)
        kdv.refresh_from_db()
        self.assertEqual(kdv.fark_hesap_id, "659")

    def test_birlestirme_sonrasi_secenekler(self):
        for k in ("190", "191", "391", "360.30", "770.11"):
            if not HesapPlani.objects.filter(hesap_kodu=k).exists():
                _h(k, k, "C")
        self.assertEqual(km.FARK_HESAPLARI, ("659", "770.11", "649"))
        self.assertEqual(km.VARSAYILAN_FARK, "659")
        self.assertEqual([h.hesap_kodu for h in km.fark_hesap_secenekleri()], ["659", "770.11", "649"])
        self.assertEqual(KdvMahsupForm().fields["fark_hesap"].initial, "659")
        hb.birlestir(uygula=True, cikti=lambda *_: None)
        self.assertNotIn("679", [h.hesap_kodu for h in kesinti_hesap_kumesi()])                # pasif hesap formlarda yok
        self.assertNotIn("689", [h.hesap_kodu for h in kesinti_hesap_kumesi()])
        self.assertNotIn("689", [h[0] for h in KdvMahsupForm().fields["fark_hesap"].choices])
