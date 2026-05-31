"""Kurlar (TCMB) testleri — XML parse, KAYDIRMALI tarih, yayın olmayan gün atlama,
fiş kur_usd otomatik doldurma bozulmadı, ekran yetkisi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Kur
from core.services.tcmb import kurlari_guncelle, parse_tcmb_xml
from core.services.yevmiye import SatirGirdi, fis_olustur, kur_usd_bul

D = datetime.date

ORNEK_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Tarih_Date Tarih="02.01.2024" Date="01/02/2024">
  <Currency CrossOrder="0" Kod="USD" CurrencyCode="USD">
    <Unit>1</Unit><Isim>ABD DOLARI</Isim>
    <ForexBuying>30.1234</ForexBuying><ForexSelling>30.2500</ForexSelling>
    <BanknoteBuying>30.1000</BanknoteBuying><BanknoteSelling>30.3000</BanknoteSelling>
  </Currency>
  <Currency CrossOrder="1" Kod="AUD" CurrencyCode="AUD">
    <Unit>1</Unit>
    <ForexBuying>20.0000</ForexBuying><ForexSelling>20.1000</ForexSelling>
    <BanknoteBuying>19.9000</BanknoteBuying><BanknoteSelling>20.2000</BanknoteSelling>
  </Currency>
  <Currency CrossOrder="9" Kod="EUR" CurrencyCode="EUR">
    <Unit>1</Unit>
    <ForexBuying>33.3300</ForexBuying><ForexSelling>33.5000</ForexSelling>
    <BanknoteBuying>33.3000</BanknoteBuying><BanknoteSelling>33.6000</BanknoteSelling>
  </Currency>
  <Currency CrossOrder="10" Kod="GBP" CurrencyCode="GBP">
    <Unit>1</Unit>
    <ForexBuying>38.8800</ForexBuying><ForexSelling>39.0000</ForexSelling>
    <BanknoteBuying>38.8000</BanknoteBuying><BanknoteSelling>39.1000</BanknoteSelling>
  </Currency>
</Tarih_Date>"""


class TcmbParseTest(TestCase):
    def test_parse_yalniz_usd_eur_gbp(self):
        r = parse_tcmb_xml(ORNEK_XML)
        self.assertEqual(set(r), {"USD", "EUR", "GBP"})   # AUD alınmaz
        self.assertEqual(r["USD"]["alis"], Decimal("30.1234"))
        self.assertEqual(r["USD"]["satis"], Decimal("30.2500"))
        self.assertEqual(r["USD"]["efektif_alis"], Decimal("30.1000"))
        self.assertEqual(r["USD"]["efektif_satis"], Decimal("30.3000"))
        self.assertEqual(r["EUR"]["alis"], Decimal("33.3300"))
        self.assertEqual(r["GBP"]["alis"], Decimal("38.8800"))


def _blok(taban):
    return {"alis": taban, "satis": taban + Decimal("0.05"),
            "efektif_alis": taban - Decimal("0.01"),
            "efektif_satis": taban + Decimal("0.06")}


def _kur(alis):
    a = Decimal(alis)
    return {"USD": _blok(a), "EUR": _blok(a + Decimal("3")), "GBP": _blok(a + Decimal("8"))}


# İş günü yayınları (Pzt-Cum); 13 Cmt + 14 Paz yayın yok
YAYIN = {
    D(2024, 1, 8): _kur("30.10"),   # Pazartesi
    D(2024, 1, 9): _kur("30.20"),   # Salı
    D(2024, 1, 10): _kur("30.30"),  # Çarşamba
    D(2024, 1, 11): _kur("30.40"),  # Perşembe
    D(2024, 1, 12): _kur("30.50"),  # Cuma
}


def _sahte_cekici(gun):
    return YAYIN.get(gun)   # yayın yoksa None


class TarihKaymasiTest(TestCase):
    def test_kaydirma_ve_atlama(self):
        ozet = kurlari_guncelle(D(2024, 1, 8), D(2024, 1, 14), cekici=_sahte_cekici)
        self.assertEqual(ozet["yayin"], 5)     # Pzt-Cum
        self.assertEqual(ozet["atlanan"], 2)   # Cmt+Paz yayın yok
        self.assertEqual(ozet["yazilan"], 5)
        # D yayını D+1'e: Pazartesi(8) kuru Salı(9)'ya; Mon gününe satır yazılmaz
        self.assertFalse(Kur.objects.filter(tarih=D(2024, 1, 8)).exists())
        self.assertEqual(Kur.objects.get(tarih=D(2024, 1, 9)).usd_alis, Decimal("30.10"))
        # Cuma(12) kuru Cumartesi(13)'ye
        self.assertEqual(Kur.objects.get(tarih=D(2024, 1, 13)).usd_alis, Decimal("30.50"))
        # 4 kur tipi + EUR/GBP de yazıldı (Salı satırında Pazartesi yayını)
        s = Kur.objects.get(tarih=D(2024, 1, 9))
        self.assertEqual(s.usd_satis, Decimal("30.15"))
        self.assertEqual(s.usd_efektif_alis, Decimal("30.09"))
        self.assertEqual(s.eur_alis, Decimal("33.10"))
        self.assertEqual(s.gbp_alis, Decimal("38.10"))

    def test_lookup_ornekleri(self):
        kurlari_guncelle(D(2024, 1, 8), D(2024, 1, 14), cekici=_sahte_cekici)
        # "Normal Salı → Çarşamba": Çarşamba fişi Salı'nın kurunu kullanır
        self.assertEqual(kur_usd_bul(D(2024, 1, 10)), Decimal("30.20"))
        # Cuma kuru Cmt+Paz+(sonraki)Pzt için geçerli
        self.assertEqual(kur_usd_bul(D(2024, 1, 13)), Decimal("30.50"))  # Cmt
        self.assertEqual(kur_usd_bul(D(2024, 1, 14)), Decimal("30.50"))  # Paz
        self.assertEqual(kur_usd_bul(D(2024, 1, 15)), Decimal("30.50"))  # sonraki Pzt

    def test_idempotent(self):
        kurlari_guncelle(D(2024, 1, 8), D(2024, 1, 12), cekici=_sahte_cekici)
        kurlari_guncelle(D(2024, 1, 8), D(2024, 1, 12), cekici=_sahte_cekici)  # tekrar
        self.assertEqual(Kur.objects.count(), 5)  # çift kayıt olmaz


class KurUsdOtomatikTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        cls.u = User.objects.create_superuser("yon", password="x")

    def test_fis_kur_usd_kaydirmali_doldurulur(self):
        kurlari_guncelle(D(2024, 1, 8), D(2024, 1, 14), cekici=_sahte_cekici)
        fis = fis_olustur(   # Çarşamba tarihli -> Salı'nın MB Alış'ı (30.20)
            tarih=D(2024, 1, 10), kullanici=self.u,
            satirlar=[SatirGirdi("100", "B", "1.000,00"),
                      SatirGirdi("600", "A", "1.000,00")],
        )
        self.assertEqual(fis.kur_usd, Decimal("30.20"))


class KurlarEkranYetkiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="kurlar")
        cls.kisitli = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=cls.kisitli, ekran_kod="mizan")

    def test_yetkisiz_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:kurlar")).status_code, 403)

    def test_yetkili_200_ve_tablar(self):
        Kur.objects.create(tarih=D(2024, 1, 9), usd_alis=Decimal("30.10"))
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:kurlar"))
        self.assertEqual(r.status_code, 200)
        for t in ["Kurlar", "USD", "EUR", "GBP", "MB Alış", "MB Efektif Satış"]:
            self.assertContains(r, t)
