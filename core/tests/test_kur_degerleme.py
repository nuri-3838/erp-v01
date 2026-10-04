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


class HaftaSonuKuruTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 6, 26), usd_alis=Dc("45"))          # Cuma
        Kur.objects.create(tarih=D(2026, 1, 5), usd_alis=Dc("40"))
        _hesap("102.02.0001", "USD BANKA")
        _hesap("131.01", "ORTAK")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="b")
        banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("102.02.0001", "B", "1000", "40"), _g("131.01", "A", "1000", "40")])

    def test_hafta_sonu_onceki_is_gununun_kurunu_kullanir(self):
        o = kd.onizle(D(2026, 6, 28))                                   # Pazar
        r = o["satirlar"][0]
        self.assertEqual((r["kur"], r["kur_tarihi"]), (Dc("45"), D(2026, 6, 26)))
        self.assertEqual(o["kur_tarihleri"], {"USD": D(2026, 6, 26)})
        self.assertEqual(o["eksik_kurlar"], [])

    def test_uygula_hafta_sonu_tarihinde_fis_kesilir(self):
        d = kd.uygula(D(2026, 6, 28))
        self.assertEqual(d.fis.tarih, D(2026, 6, 28))
        self.assertEqual(d.fis.kur_usd, Dc("45"))                       # USD raporlama kuru da önceki iş günü
        self.assertEqual(kf.havuz_bakiyesi("102.02.0001", "USD"), (Dc("1000.00"), Dc("45000.00")))
        self.assertIn("26.06.2026", list(d.kurlar.values())[0])
        kd.ters_kayit(d, tarih=D(2026, 6, 29))
        self.assertEqual(kf.havuz_bakiyesi("102.02.0001", "USD"), (Dc("1000.00"), Dc("40000.00")))

    def test_yedi_gunden_eski_kur_kullanilmaz_uyari_kalir(self):
        o = kd.onizle(D(2026, 7, 4))                                    # kur 8 gün önce
        self.assertEqual(o["eksik_kurlar"], ["USD"])
        self.assertEqual(o["satirlar"], [])
        with self.assertRaises(kd.KurDegerlemeHatasi) as e:
            kd.uygula(D(2026, 7, 4))
        self.assertIn("önceki 7 günde", str(e.exception))
        self.assertEqual(kd.onizle(D(2026, 7, 3))["eksik_kurlar"], [])  # tam 7 gün geri: kullanılır


class AvansKuraliTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import Cari
        Kur.objects.create(tarih=D(2026, 1, 5), usd_alis=Dc("40"))
        Kur.objects.create(tarih=D(2026, 9, 30), usd_alis=Dc("45"))
        for kod, ad in (("320.01", "SATICI A"), ("320.02", "SATICI B"), ("320.03", "SATICI C"),
                        ("120.01", "MÜŞTERİ A"), ("120.02", "MÜŞTERİ B")):
            _hesap(kod, ad)
        _hesap("131.01", "ORTAK")
        _hesap("102.02.0001", "USD BANKA")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="b")
        banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        cls.c = {}
        for kod, hesap in (("A", "320.01"), ("B", "320.02"), ("C", "320.03"), ("M1", "120.01"), ("M2", "120.02")):
            cls.c[kod] = Cari.objects.create(kod=kod, unvan=f"CARİ {kod}", para_birimi="USD", muhasebe_kodu=hesap)
        # A: verilen avans (320 BORÇ) | B: normal borç (320 ALACAK) | C: avans ama HER_ZAMAN denenecek
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("320.01", "B", "100", "40"), _g("131.01", "A", "100", "40")])
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("131.01", "B", "100", "40"), _g("320.02", "A", "100", "40")])
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("320.03", "B", "50", "40"), _g("131.01", "A", "50", "40")])
        # M1: alınan avans (120 ALACAK) | M2: normal alacak (120 BORÇ)
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("131.01", "B", "70", "40"), _g("120.01", "A", "70", "40")])
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("120.02", "B", "70", "40"), _g("131.01", "A", "70", "40")])
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[_g("102.02.0001", "B", "10", "40"), _g("131.01", "A", "10", "40")])

    def _ozet(self):
        o = kd.onizle(D(2026, 9, 30))
        return ({r["hesap_kodu"] for r in o["satirlar"]}, {r["hesap_kodu"]: r["neden"] for r in o["degerlenmeyen"]})

    def test_otomatik_avans_yonu_kurali(self):
        degerlenen, degerlenmeyen = self._ozet()
        self.assertEqual(set(degerlenmeyen), {"320.01", "320.03", "120.01"})       # verilen/alınan avanslar
        self.assertIn("Verilen avans", degerlenmeyen["320.01"])
        self.assertIn("Alınan avans", degerlenmeyen["120.01"])
        self.assertEqual(degerlenen, {"320.02", "120.02", "102.02.0001"})          # normal borç/alacak + banka

    def test_cari_her_zaman_degerle_avansi_degerlemeye_alir(self):
        from core.models import Cari
        Cari.objects.filter(pk=self.c["C"].pk).update(kur_degerleme="HER_ZAMAN")
        degerlenen, degerlenmeyen = self._ozet()
        self.assertIn("320.03", degerlenen)
        self.assertNotIn("320.03", degerlenmeyen)
        self.assertIn("320.01", degerlenmeyen)                                     # diğer avans hâlâ değil

    def test_cari_hic_degerleme_normal_bakiyeyi_de_disarida_birakir(self):
        from core.models import Cari
        Cari.objects.filter(pk=self.c["B"].pk).update(kur_degerleme="HIC")
        degerlenen, degerlenmeyen = self._ozet()
        self.assertNotIn("320.02", degerlenen)
        self.assertEqual(degerlenmeyen["320.02"], "Cari ayarı: hiç değerleme")

    def test_uygula_yalniz_degerlenenleri_yazar(self):
        d = kd.uygula(D(2026, 9, 30))
        hesaplar = {s.hesap_id for s in d.fis.satirlar.all()}
        self.assertTrue({"320.02", "120.02", "102.02.0001"} <= hesaplar)
        self.assertFalse({"320.01", "320.03", "120.01"} & hesaplar)

    def test_ekranda_degerlenmeyen_bolumu_ve_kur_tarihi(self):
        from django.urls import reverse
        from core.models import EkranYetki
        su = User.objects.create_superuser("av", password="x")
        EkranYetki.objects.create(kullanici=su, ekran_kod="kur_degerleme")
        self.client.force_login(su)
        r = self.client.get(reverse("core:kur_degerleme"), {"tarih": "2026-09-30"})
        self.assertContains(r, "Değerlenmeyen (avans)")
        self.assertContains(r, "Verilen avans")
        self.assertContains(r, "30.09.2026")                                       # kur tarihi sütunu

    def test_cari_formu_ve_kartta_kur_degerleme(self):
        from django.urls import reverse
        from core.services import cari as cari_servis
        su = User.objects.create_superuser("cf", password="x")
        self.client.force_login(su)
        c = self.c["A"]
        self.assertContains(self.client.get(reverse("core:cari_duzenle", args=[c.pk])), "Her zaman değerle")
        cari_servis.cari_guncelle(c, unvan=c.unvan, para_birimi="USD", kur_degerleme="HER_ZAMAN",
                                  kredi_limiti=0, iskonto_yuzdesi=0)
        c.refresh_from_db()
        self.assertEqual(c.kur_degerleme, "HER_ZAMAN")
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[c.pk])), "Her zaman değerle")
