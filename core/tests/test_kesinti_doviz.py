"""Cari Kesinti / Masraf döviz carilerinde: para birimi seçimi (TRY/USD/EUR), kur (boşsa TCMB), cari satırı seçilen dövizle,
karşı taraf TL karşılığıyla; kur farkı motoru havuzu işler; açık bakiye (havuz) özeti; ortak ödedi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, EkranYetki, Kur, YevmiyeSatir
from core.services import cari_kesinti as ck
from core.services import hesap_plani as hp
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class KesintiDovizTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 9, 1), usd_alis=Dc("40"), eur_alis=Dc("44"))
        Kur.objects.create(tarih=D(2026, 9, 7), usd_alis=Dc("45"), eur_alis=Dc("49"))
        _hesap("320.10.0001", "FORMAL", kalem="KVYK")
        _hesap("320.01", "SATICI", kalem="KVYK")
        _hesap("770.01", "GİDER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.03", "DİĞER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("646", "KUR FARKI KÂRI", kalem="D", grup="GELIR_TABLOSU")
        _hesap("656", "KUR FARKI ZARARI", kalem="D", grup="GELIR_TABLOSU")
        _hesap("500", "SERMAYE", kalem="OZK")
        hp.hesap_olustur(kod="500.10", ad="ÖDENMİŞ SERMAYE", ust_kodu="500")
        hp.hesap_olustur(kod="500.10.0001", ad="NURİ ÖZER", ust_kodu="500.10")
        cls.formal = Cari.objects.create(kod="320-10-0001", unvan="FORMAL", muhasebe_kodu="320.10.0001", para_birimi="USD")
        cls.tl_cari = Cari.objects.create(kod="S1", unvan="SATICI TL", muhasebe_kodu="320.01", para_birimi="TRY")
        cls.nuri = Cari.objects.create(kod="500-10-0001", unvan="NURİ ÖZER", muhasebe_kodu="500.10.0001", para_birimi="TRY")
        cls.su = User.objects.create_superuser("kd", password="x")
        # USD havuzu: 1.000 USD borcumuz @40 (40.000 TL)
        fis_olustur(tarih=D(2026, 9, 1), aciklama="alış", satirlar=[
            SatirGirdi(hesap_kodu="770.01", taraf="B", islem_tutari="40000"),
            SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="1000", islem_pb="USD", islem_kuru="40")])

    def _satirlar(self, fis):
        return list(fis.satirlar.filter(silindi=False).order_by("id"))

    def test_try_secilirse_cari_satiri_tl_havuza(self):
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 7), tutar="509437,10", para_birimi="TRY", sayilan_pb="TRY",
                               karsi_cari=self.nuri, kullanici=self.su)
        s = {x.hesap_id: x for x in self._satirlar(f)}
        self.assertEqual((s["320.10.0001"].islem_pb, s["320.10.0001"].borc), ("TRY", Dc("509437.10")))
        self.assertEqual(s["500.10.0001"].alacak, Dc("509437.10"))
        self.assertFalse(f.satirlar.filter(ana_satir__isnull=False).exists())         # TL havuzda kur farkı yok

    def test_usd_secilirse_cari_usd_karsi_tl_ve_kur_farki(self):
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 7), tutar="400", para_birimi="USD", kur="45",
                               karsi_cari=self.nuri, kullanici=self.su)
        s = self._satirlar(f)
        cari = next(x for x in s if x.hesap_id == "320.10.0001" and x.ana_satir_id is None)
        ortak = next(x for x in s if x.hesap_id == "500.10.0001")
        self.assertEqual((cari.islem_pb, cari.islem_tutari, cari.islem_kuru, cari.borc), ("USD", Dc("400.00"), Dc("45.000000"), Dc("16000.00")))
        self.assertEqual(ortak.alacak, Dc("18000.00"))                                  # karşı taraf TL karşılığı 400 × 45
        zarar = [x for x in s if x.hesap_id == "656"]
        self.assertEqual((len(zarar), zarar[0].borc if zarar else None), (1, Dc("2000.00")))   # ort. kur 40 → 45: kur farkı zararı
        self.assertEqual(sum(x.borc for x in s), sum(x.alacak for x in s))                  # fiş dengeli
        usd = sum((x.islem_tutari if x.alacak else -x.islem_tutari) for x in YevmiyeSatir.objects.filter(
            hesap_id="320.10.0001", islem_pb="USD", silindi=False, fis__silindi=False))
        self.assertEqual(usd, Dc("600.00"))                                              # USD havuzu 600 USD kaldı

    def test_gider_modu_doviz_ve_kur_bos_ise_tcmb(self):
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 7), tutar="100", para_birimi="EUR", gider_kodu="770.03",
                               kullanici=self.su)
        s = self._satirlar(f)
        cari = next(x for x in s if x.hesap_id == "320.10.0001")
        self.assertEqual((cari.islem_pb, cari.islem_kuru), ("EUR", Dc("49.000000")))     # TCMB (cari kur tipi MB alış)
        self.assertEqual(next(x for x in s if x.hesap_id == "770.03").alacak, Dc("4900.00"))   # tedarikçi yönü: gider ALACAK

    def test_tl_carisinde_doviz_ve_kursuz_tarih_reddedilir(self):
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.tl_cari, tarih=D(2026, 9, 7), tutar="10", para_birimi="USD", kur="45", gider_kodu="770.03")
        with self.assertRaises(ck.CariKesintiHatasi):
            ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 9), tutar="10", para_birimi="USD", gider_kodu="770.03")   # kur yok
        self.assertEqual(YevmiyeSatir.objects.filter(fis__kaynak="CARI_KESINTI").count(), 0)

    def test_duzenle_para_birimi_ve_kur_geri_okunur(self):
        f = ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 7), tutar="400", para_birimi="USD", kur="45",
                               karsi_cari=self.nuri, kullanici=self.su)
        b = ck.duzenleme_bilgisi(f, self.formal)
        self.assertEqual((b["tutar"], b["para_birimi"], b["kur"], b["karsi_cari"]), (Dc("400.00"), "USD", Dc("45.000000"), self.nuri))
        ck.kesinti_guncelle(fis=f, cari=self.formal, tarih=D(2026, 9, 7), tutar="500", para_birimi="USD", kur="45",
                            karsi_cari=self.nuri, kullanici=self.su)
        b = ck.duzenleme_bilgisi(f, self.formal)
        self.assertEqual(b["tutar"], Dc("500.00"))
        ck.kesinti_guncelle(fis=f, cari=self.formal, tarih=D(2026, 9, 7), tutar="22500", para_birimi="TRY", sayilan_pb="TRY",
                            karsi_cari=self.nuri, kullanici=self.su)
        self.assertEqual(ck.duzenleme_bilgisi(f, self.formal)["para_birimi"], "TRY")

    def test_havuz_bakiyeleri(self):
        ck.kesinti_olustur(cari=self.formal, tarih=D(2026, 9, 7), tutar="1000", para_birimi="TRY", sayilan_pb="TRY", karsi_cari=self.nuri,
                           kullanici=self.su)
        h = {x["pb"]: x for x in ck.havuz_bakiyeleri(self.formal)}
        self.assertEqual((h["USD"]["doviz"], h["USD"]["taraf"]), (Dc("1000.00"), "alacak"))
        self.assertEqual((h["TRY"]["tl"], h["TRY"]["taraf"]), (Dc("1000.00"), "borç"))

    def test_ekran_doviz_alanlari_ve_havuz_gorunur_tl_carisinde_yok(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="cariler")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[self.formal.pk]))
        self.assertContains(r, "id_para_birimi")
        self.assertContains(r, "Açık bakiyeler")
        r = self.client.post(reverse("core:cari_kesinti_ekle", args=[self.formal.pk]), {
            "tarih": "2026-09-07", "tutar": "400,00", "para_birimi": "USD", "kur": "45,000000", "gider": "770.03",
            "karsi_cari": self.nuri.pk, "aciklama": ""})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.formal.pk]))
        r = self.client.get(reverse("core:cari_kesinti_ekle", args=[self.tl_cari.pk]))
        self.assertNotContains(r, "id_para_birimi")
