"""Dönem sonu kur değerleme: dry-run önizleme (yazmaz), değerleme fişi (kâr 646 / zarar 656), döviz
bakiyesi değişmez, sonraki çıkışlar değerlenmiş ortalamayla, tek tuşla ters kayıt, ekran."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Kur, KurDegerleme, YevmiyeFisi, YevmiyeSatir
from core.services import kur_degerleme as kd
from core.services import kur_farki as kf
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _g(hesap, taraf, dvz, kur, pb="USD"):
    return SatirGirdi(hesap_kodu=hesap, taraf=taraf, islem_tutari=dvz, islem_pb=pb, islem_kuru=kur)


class KurDegerlemeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 1, 5), D(2026, 6, 30), D(2026, 7, 1), D(2026, 7, 5), D(2026, 12, 31)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        Kur.objects.filter(tarih=D(2026, 6, 30)).update(usd_alis=Dc("45"))
        Kur.objects.filter(tarih=D(2026, 12, 31)).update(usd_alis=Dc("38"))
        _hesap("102.02.0001", "USD BANKA")
        _hesap("131.01", "ORTAK")
        _hesap("320.01", "SATICI")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="b")
        banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        cls.su = User.objects.create_superuser("kd", password="x")

    def setUp(self):
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("102.02.0001", "B", "1000", "40"), _g("131.01", "A", "1000", "40")])

    def test_onizleme_yazmaz(self):
        n = YevmiyeFisi.objects.count()
        o = kd.onizle(D(2026, 6, 30))
        self.assertEqual(YevmiyeFisi.objects.count(), n)
        self.assertEqual(len(o["satirlar"]), 1)
        r = o["satirlar"][0]
        self.assertEqual((r["hesap_kodu"], r["doviz"], r["tl"], r["kur"], r["yeni_tl"], r["fark"]),
                         ("102.02.0001", Dc("1000.00"), Dc("40000.00"), Dc("45"), Dc("45000.00"), Dc("5000.00")))
        self.assertEqual((o["kar"], o["zarar"]), (Dc("5000.00"), Dc("0.00")))

    def test_kar_degerlemesi_fisi_ve_doviz_bakiyesi_degismez(self):
        d = kd.uygula(D(2026, 6, 30), kullanici=self.su)
        satirlar = {s.hesap_id: (s.borc, s.alacak, s.islem_tutari) for s in d.fis.satirlar.all()}
        self.assertEqual(satirlar["102.02.0001"], (Dc("5000.00"), Dc("0.00"), Dc("0.00")))
        self.assertEqual(satirlar["646"], (Dc("0.00"), Dc("5000.00"), Dc("5000.00")))
        self.assertEqual(d.fis.kaynak, "KUR_DEGERLEME")
        self.assertEqual(kf.havuz_bakiyesi("102.02.0001", "USD"), (Dc("1000.00"), Dc("45000.00")))
        self.assertEqual(sum(s.borc for s in d.fis.satirlar.all()), sum(s.alacak for s in d.fis.satirlar.all()))

    def test_zarar_degerlemesi_cari_alacak_bakiyesi(self):
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("131.01", "B", "500", "40"), _g("320.01", "A", "500", "40")])
        d = kd.uygula(D(2026, 6, 30), kullanici=self.su)        # cari 500 USD borçlu → 45 kurunda 22.500 (artış 2.500)
        satirlar = {(s.hesap_id): (s.borc, s.alacak) for s in d.fis.satirlar.all()}
        self.assertEqual(satirlar["320.01"], (Dc("0.00"), Dc("2500.00")))
        self.assertEqual(satirlar["656"][0], Dc("2500.00"))
        self.assertEqual(kf.havuz_bakiyesi("320.01", "USD"), (Dc("-500.00"), Dc("-22500.00")))

    def test_ters_kayit_bakiyeyi_eski_haline_getirir(self):
        d = kd.uygula(D(2026, 6, 30))
        d = kd.ters_kayit(d, tarih=D(2026, 7, 1), kullanici=self.su)
        self.assertEqual(d.ters_fis.tarih, D(2026, 7, 1))
        self.assertEqual(kf.havuz_bakiyesi("102.02.0001", "USD"), (Dc("1000.00"), Dc("40000.00")))
        with self.assertRaises(kd.KurDegerlemeHatasi):
            kd.ters_kayit(d)                                    # ikinci kez olmaz
        with self.assertRaises(kd.KurDegerlemeHatasi):          # 30.06 bakiyesi zaten değerli (ters 01.07'de)
            kd.uygula(D(2026, 6, 30))

    def test_ayni_tarihte_geri_alinmamis_degerleme_reddedilir(self):
        kd.uygula(D(2026, 6, 30))
        with self.assertRaises(kd.KurDegerlemeHatasi):
            kd.uygula(D(2026, 6, 30))

    def test_kur_yoksa_ve_fark_yoksa_reddedilir(self):
        with self.assertRaises(kd.KurDegerlemeHatasi):
            kd.uygula(D(2026, 6, 29))                           # o gün kur yok
        Kur.objects.create(tarih=D(2026, 2, 1), usd_alis=Dc("40"))
        with self.assertRaises(kd.KurDegerlemeHatasi):
            kd.uygula(D(2026, 2, 1))                            # kur = ortalama → fark 0

    def test_deger_sonrasi_cikis_degerlenmis_ortalamayla(self):
        kd.uygula(D(2026, 6, 30))                               # ortalama 45'e çıktı
        f = fis_olustur(tarih=D(2026, 7, 5), satirlar=[_g("131.01", "B", "1000", "46"), _g("102.02.0001", "A", "1000", "46")])
        banka = f.satirlar.get(hesap_id="102.02.0001")
        self.assertEqual(banka.alacak, Dc("45000.00"))
        self.assertEqual(kf.havuz_bakiyesi("102.02.0001", "USD"), (Dc("0.00"), Dc("0.00")))

    def test_ekran_onizleme_uygula_ve_ters(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="kur_degerleme")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:kur_degerleme"), {"tarih": "2026-06-30"})
        self.assertContains(r, "5.000,00")
        self.assertContains(r, "Değerleme fişini oluştur")
        self.client.post(reverse("core:kur_degerleme"), {"eylem": "uygula", "tarih": "2026-06-30"})
        deg = KurDegerleme.objects.get()
        r = self.client.get(reverse("core:kur_degerleme"), {"tarih": "2026-06-30"})
        self.assertContains(r, "Ters kayıt")
        self.client.post(reverse("core:kur_degerleme"),
                         {"eylem": "ters", "degerleme": deg.pk, "tarih": "2026-06-30", "ters_tarih": "2026-07-01"})
        deg.refresh_from_db()
        self.assertIsNotNone(deg.ters_fis_id)
