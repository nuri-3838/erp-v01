"""Döviz Alış / Satış hareket türü: TL ↔ döviz banka/kasa, çapraz kurlu tek fiş; satışta ortalama kur +
otomatik 646/656; kambiyo vergisi/masraf satırı; ekran; Sil."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Kur, YevmiyeFisi
from core.services import banka_hareket as bh
from core.services import doviz_islem as di
from core.services import kur_farki as kf
from core.services.finans import banka_hesap_olustur, banka_olustur, kasa_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class DovizIslemTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 1, 5), D(2026, 2, 5), D(2026, 3, 5)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        _hesap("100.01", "KASA TL")
        _hesap("100.02", "KASA USD")
        _hesap("102.01", "TL BANKA")
        _hesap("102.02", "USD BANKA")
        _hesap("770.11", "KAMBİYO GİDER VERGİSİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="halk")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02")
        cls.ktl = kasa_olustur(ad="kasa tl", para_birimi="TRY", muhasebe_kodu="100.01")
        cls.kusd = kasa_olustur(ad="kasa usd", para_birimi="USD", muhasebe_kodu="100.02")
        cls.su = User.objects.create_superuser("dv", password="x")

    def _satirlar(self, fis):
        return {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak, s.islem_pb, s.islem_tutari)
                for s in fis.satirlar.filter(silindi=False)}

    def test_alis_tl_banka_alacak_doviz_banka_borc(self):
        f = di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="1000", kur="43,60",
                                   tarih=D(2026, 1, 5), kullanici=self.su)
        s = self._satirlar(f)
        self.assertEqual(s[("102.02", "B")], (Dc("43600.00"), "USD", Dc("1000.00")))
        self.assertEqual(s[("102.01", "A")], (Dc("43600.00"), "TRY", Dc("43600.00")))
        self.assertEqual((f.kaynak, f.banka_hesap_id), ("BANKA", self.tl.pk))
        self.assertIn("DÖVİZ ALIŞ 1.000,00 USD", f.aciklama)

    def test_satis_ortalama_kurla_alacak_ve_kur_farki(self):
        di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="1000", kur="40", tarih=D(2026, 1, 5))
        di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="1000", kur="50", tarih=D(2026, 2, 5))
        f = di.doviz_islem_olustur(kaynak=self.usd, hedef=self.tl, doviz_tutari="500", kur="48",
                                   tarih=D(2026, 3, 5), kullanici=self.su)
        s = self._satirlar(f)
        self.assertEqual(s[("102.02", "A")][0], Dc("22500.00"))      # ortalama 45 × 500
        self.assertEqual(s[("102.01", "B")][0], Dc("24000.00"))      # satış kuru 48 × 500
        self.assertEqual(s[("646", "A")][0], Dc("1500.00"))          # kâr
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))
        self.assertEqual((f.kaynak, f.banka_hesap_id), ("BANKA", self.usd.pk))

    def test_tum_doviz_satilinca_tl_bakiye_sifir(self):
        di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="1000", kur="40", tarih=D(2026, 1, 5))
        di.doviz_islem_olustur(kaynak=self.usd, hedef=self.tl, doviz_tutari="1000", kur="43", tarih=D(2026, 2, 5))
        self.assertEqual(kf.havuz_bakiyesi("102.02", "USD"), (Dc("0.00"), Dc("0.00")))

    def test_kambiyo_vergisi_masraf_satiri_alista_tl_hesaptan(self):
        f = di.doviz_islem_olustur(
            kaynak=self.tl, hedef=self.usd, doviz_tutari="3480", kur="48.33278", tarih=D(2026, 1, 5),
            masraflar=[{"hesap_kodu": "770.11", "tutar": "336,40"}])
        s = self._satirlar(f)
        self.assertEqual(s[("102.02", "B")][0], Dc("168198.07"))
        self.assertEqual(s[("770.11", "B")][0], Dc("336.40"))
        self.assertEqual(s[("102.01", "A")][0], Dc("168534.47"))        # TL + masraf
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))

    def test_masraf_satista_tahsilattan_duser(self):
        di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="100", kur="40", tarih=D(2026, 1, 5))
        f = di.doviz_islem_olustur(kaynak=self.usd, hedef=self.tl, doviz_tutari="100", kur="40",
                                   tarih=D(2026, 2, 5), masraflar=[{"hesap_kodu": "770.11", "tutar": "10"}])
        s = self._satirlar(f)
        self.assertEqual(s[("102.01", "B")][0], Dc("3990.00"))
        self.assertEqual(s[("770.11", "B")][0], Dc("10.00"))

    def test_kasa_kasa_ve_banka_kasa(self):
        f = di.doviz_islem_olustur(kaynak=self.ktl, hedef=self.kusd, doviz_tutari="10", kur="40", tarih=D(2026, 1, 5))
        self.assertEqual((f.kaynak, f.kasa_id), ("KASA", self.ktl.pk))
        f2 = di.doviz_islem_olustur(kaynak=self.tl, hedef=self.kusd, doviz_tutari="10", kur="40", tarih=D(2026, 1, 5))
        self.assertEqual((f2.kaynak, f2.banka_hesap_id), ("BANKA", self.tl.pk))

    def test_gecersiz_kombinasyonlar_reddedilir(self):
        with self.assertRaises(di.DovizIslemHatasi):
            di.doviz_islem_olustur(kaynak=self.tl, hedef=self.ktl, doviz_tutari="1", kur="1", tarih=D(2026, 1, 5))
        with self.assertRaises(di.DovizIslemHatasi):
            di.doviz_islem_olustur(kaynak=self.usd, hedef=self.kusd, doviz_tutari="1", kur="1", tarih=D(2026, 1, 5))
        with self.assertRaises(di.DovizIslemHatasi):
            di.doviz_islem_olustur(kaynak=self.tl, hedef=self.tl, doviz_tutari="1", kur="1", tarih=D(2026, 1, 5))
        with self.assertRaises(di.DovizIslemHatasi):
            di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="0", kur="1", tarih=D(2026, 1, 5))

    def test_kur_bos_ise_tcmb_alis(self):
        f = di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="10", tarih=D(2026, 1, 5))
        self.assertEqual(self._satirlar(f)[("102.02", "B")][0], Dc("400.00"))
        with self.assertRaises(di.DovizIslemHatasi):
            di.doviz_islem_olustur(kaynak=self.tl, hedef=self.usd, doviz_tutari="10", tarih=D(2026, 1, 6))

    def test_ekran_ve_sil(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="banka")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:doviz_islem_ekle"), {"kaynak": f"banka:{self.tl.pk}"})
        self.assertContains(r, "Döviz Alış / Satış")
        veri = {"kaynak": f"banka:{self.tl.pk}", "hedef": f"banka:{self.usd.pk}", "doviz_tutari": "100,00",
                "kur": "40,0000", "tarih": "2026-01-05", "aciklama": "", "masraf_hesap_1": "770.11",
                "masraf_tutar_1": "5,00", "masraf_hesap_2": "", "masraf_tutar_2": ""}
        r = self.client.post(reverse("core:doviz_islem_ekle"), veri)
        fis = YevmiyeFisi.objects.filter(kaynak="BANKA").latest("pk")
        self.assertRedirects(r, reverse("core:banka_hesap_detay", args=[self.tl.pk]))
        # TL hesap detayında düğme + Sil çalışır
        d = self.client.get(reverse("core:banka_hesap_detay", args=[self.tl.pk]))
        self.assertContains(d, "Döviz Alış / Satış")
        pk = fis.pk
        self.client.post(reverse("core:banka_hareket_sil", args=[self.tl.pk, pk]))
        self.assertFalse(YevmiyeFisi.objects.filter(pk=pk).exists())
        self.assertEqual(kf.havuz_bakiyesi("102.02", "USD"), (Dc("0"), Dc("0")))
        # yön hatası ekranda gösterilir
        veri["hedef"] = f"kasa:{self.ktl.pk}"
        r = self.client.post(reverse("core:doviz_islem_ekle"), veri)
        self.assertContains(r, "TL, diğeri döviz")
