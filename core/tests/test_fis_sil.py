"""Kalıcı silme (iptal yok): manuel fiş / kasa / banka / kredi kartı hareketi + çek bordrosu.
Silme çalışır, bağlı kayıt reddedilir, yetkisiz kullanıcı reddedilir, denetim kaydı oluşur,
mizan ve ekstre doğru kalır; onay ekranı fiş no/tarih/tutar/açıklamayı gösterir."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (EkranYetki, KrediKartiTaksit, SilmeKaydi, YatirimProjesi, YevmiyeFisi,
                         YevmiyeSatir)
from core.services import fis_sil as fs
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.raporlar import ekstre, mizan
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date


def _manuel(tutar="1000", ack="deneme fişi", tarih=D(2026, 3, 10), kaynak=YevmiyeFisi.Kaynak.MANUEL):
    return fis_olustur(
        tarih=tarih, aciklama=ack, kaynak=kaynak,
        satirlar=[SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari=tutar),
                  SatirGirdi(hesap_kodu="600.01", taraf="A", islem_tutari=tutar)])


class FisSilTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Kur
        for g in (D(2026, 3, 5), D(2026, 3, 10)):
            Kur.objects.create(tarih=g, usd_alis=Decimal("40"))
        _hesap("100.01", "KASA")
        _hesap("600.01", "SATIŞ", kalem="C", grup="GELIR_TABLOSU")
        cls.su = User.objects.create_superuser("sup", password="x")
        cls.sade = User.objects.create_user("sade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="fis_listesi")

    def test_manuel_fis_silinir_denetim_kaydi_yazilir(self):
        f = _manuel(ack="açıklama X")
        no, pk = f"{f.yil}/{f.fis_no}", f.pk
        fs.fis_sil(f, kullanici=self.su, izinli_kaynaklar={"MANUEL"})
        self.assertFalse(YevmiyeFisi.objects.filter(pk=pk).exists())
        self.assertFalse(YevmiyeSatir.objects.filter(fis_id=pk).exists())
        k = SilmeKaydi.objects.get(kayit_no=no)
        self.assertEqual((k.tur, k.silen_id, k.tarih, k.tutar, k.aciklama),
                         ("FIS", self.su.pk, D(2026, 3, 10), Decimal("1000.00"), "AÇIKLAMA X"))
        self.assertEqual(len(k.veri["satirlar"]), 2)
        self.assertEqual({s["hesap"] for s in k.veri["satirlar"]}, {"100.01", "600.01"})
        self.assertIsNotNone(k.created_at)

    def test_yetkisiz_kullanici_reddedilir(self):
        f = _manuel()
        with self.assertRaises(fs.SilmeHatasi):
            fs.fis_sil(f, kullanici=self.sade)
        with self.assertRaises(fs.SilmeHatasi):
            fs.fis_sil(f, kullanici=None)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertFalse(SilmeKaydi.objects.exists())

    def test_yetkisiz_view_silmez_ve_onay_ekrani_uyarir(self):
        f = _manuel()
        self.client.force_login(self.sade)
        r = self.client.get(reverse("core:fis_sil", args=[f.pk]))
        self.assertContains(r, "yalnız süper kullanıcı")
        self.client.post(reverse("core:fis_sil", args=[f.pk]))
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertFalse(SilmeKaydi.objects.exists())

    def test_onay_ekrani_fis_no_tarih_tutar_aciklama_gosterir(self):
        f = _manuel(tutar="1234.50", ack="onay ekranı")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fis_sil", args=[f.pk]))
        self.assertContains(r, f"{f.yil}/{f.fis_no}")
        self.assertContains(r, "10.03.2026")
        self.assertContains(r, "1.234,50")
        self.assertContains(r, "ONAY EKRANI")
        self.assertContains(r, "Kalıcı olarak sil")
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())     # GET silmez

    def test_baglı_kayit_varsa_reddedilir_ve_neden_gosterilir(self):
        f = _manuel()
        YatirimProjesi.objects.create(kod="YP-1", ad="hat", aktiflestirme_fisi=f)      # fişe bağlı proje
        with self.assertRaises(fs.SilmeHatasi) as e:
            fs.fis_sil(f, kullanici=self.su)
        self.assertIn("yatırım proje", str(e.exception).lower())
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertEqual(YevmiyeSatir.objects.filter(fis=f).count(), 2)
        self.assertFalse(SilmeKaydi.objects.exists())                       # denetim kaydı da geri alındı
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fis_sil", args=[f.pk]))
        self.assertContains(r, "Silinemez")
        self.assertNotContains(r, "Kalıcı olarak sil")

    def test_baska_kaynakli_fis_manuel_ekrandan_silinmez(self):
        f = _manuel(kaynak=YevmiyeFisi.Kaynak.STOK_SARF)
        with self.assertRaises(fs.SilmeHatasi):
            fs.fis_sil(f, kullanici=self.su, izinli_kaynaklar={"MANUEL"})
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())

    def test_silme_sonrasi_mizan_ve_ekstre_dogru(self):
        _manuel(tutar="500", ack="kalan", tarih=D(2026, 3, 5))
        f = _manuel(tutar="1000", ack="silinecek", tarih=D(2026, 3, 10))
        m1 = {s.hesap_kodu: s.borc_bakiye for s in mizan(D(2026, 1, 1), D(2026, 12, 31)).satirlar}
        self.assertEqual(m1["100"], Decimal("1500.00"))
        fs.fis_sil(f, kullanici=self.su)
        m2 = {s.hesap_kodu: s.borc_bakiye for s in mizan(D(2026, 1, 1), D(2026, 12, 31)).satirlar}
        self.assertEqual(m2["100"], Decimal("500.00"))
        e = ekstre("100.01", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual((len(e.satirlar), e.bakiye), (1, Decimal("500.00")))

    def test_view_post_siler_ve_listeye_doner(self):
        f = _manuel()
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fis_sil", args=[f.pk]))
        self.assertRedirects(r, reverse("core:fis_listesi"))
        self.assertFalse(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertEqual(SilmeKaydi.objects.count(), 1)

    def test_onizle_geri_alir(self):
        f = _manuel()
        pk = f.pk
        sonuc = fs.onizle(lambda: fs.fis_sil(f, kullanici=self.su))
        self.assertIsNone(sonuc)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=pk).exists())         # önizleme silmez
        self.assertFalse(SilmeKaydi.objects.exists())
        self.assertIn("süper kullanıcı", fs.onizle(lambda: fs.fis_sil(f, kullanici=self.sade)))


class HareketSilTest(TestCase):
    """Kasa/banka/kredi kartı hareketi silinir; bağlı taksit planı denetim kaydına yazılıp birlikte silinir."""

    @classmethod
    def setUpTestData(cls):
        from core.models import Kur
        Kur.objects.create(tarih=D(2026, 6, 28), usd_alis=Decimal("40"))
        _hesap("102.01.0001", "BANKA")
        _hesap("309.01", "KART", kalem="KVYK")
        _hesap("770.01", "GİDER", kalem="C", grup="GELIR_TABLOSU")
        cls.su = User.objects.create_superuser("sup2", password="x")
        b = banka_olustur(ad="b")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")

    def test_kredi_karti_taksitli_harcama_silinir(self):
        from core.services.finans import kredi_karti_olustur
        from core.services.kredi_karti_hareket import harcama_olustur, hareket_sil
        kart = kredi_karti_olustur(ad="kart", para_birimi="TRY", muhasebe_kodu="309.01", kullanici=self.su)
        gider = __import__("core.models", fromlist=["HesapPlani"]).HesapPlani.objects.get(hesap_kodu="770.01")
        f = harcama_olustur(kart=kart, karsi=gider, tutar=Decimal("600"), tarih=D(2026, 6, 28),
                            taksit_adedi=2, ilk_vade=D(2026, 7, 28), kullanici=self.su)
        no = f"{f.yil}/{f.fis_no}"
        hareket_sil(fis=f, kart=kart, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertFalse(KrediKartiTaksit.objects.exists())
        k = SilmeKaydi.objects.get(kayit_no=no)
        self.assertEqual(k.veri["kk_taksit_planlari"][0]["taksit_adedi"], 2)
        self.assertEqual(k.tutar, Decimal("600.00"))

    def test_banka_hareketi_yetkisiz_silinmez(self):
        from core.services import banka_hareket as bh
        from core.services.banka_hareket import BankaHareketHatasi
        sade = User.objects.create_user("sade2", password="x")
        _hesap("131.01", "KARŞI")
        fis = bh.hareket_olustur(banka_hesap=self.tl, tip="hesaba_odeme", karsi=None, tutar="100",
                                 tarih=D(2026, 6, 28), satirlar=[{"hesap_kodu": "131.01", "tutar": "100"}],
                                 kullanici=self.su)
        with self.assertRaises(BankaHareketHatasi):
            bh.hareket_sil(fis=fis, banka_hesap=self.tl, kullanici=sade)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
        bh.hareket_sil(fis=fis, banka_hesap=self.tl, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=fis.pk).exists())
