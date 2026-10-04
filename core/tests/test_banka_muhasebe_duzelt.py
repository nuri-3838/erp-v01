"""banka_hesap_muhasebe_duzelt: dry-run yazmaz; --uygula yeni muavini açıp banka hesabını bağlar;
paylaşan diğer hesaba dokunmaz; aktif hareket varsa hiçbir şey yapmaz."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import BankaHesap, HesapPlani
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.tests.test_banka_hesap_hareketi import _hesap


class MuhasebeDuzeltTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("102.02", "HALK BANKASI")
        _hesap("102.02.0002", "HALK BANKASI USD HESABI")
        _hesap("131.01", "KARŞI")
        b = banka_olustur(ad="halk")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0002")
        cls.eur = banka_hesap_olustur(banka=b, ad="eur", para_birimi="EUR", muhasebe_kodu="102.02.0002")

    def _komut(self, *ek):
        out = StringIO()
        call_command("banka_hesap_muhasebe_duzelt", "--hesap", str(self.eur.pk), "--yeni-kod", "102.02.0003",
                     "--yeni-ad", "HALK BANKASI EUR HESABI", "--ornek", "102.02.0002", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_baglar_usd_hesabina_dokunmaz(self):
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertIn("AÇILDI", c)
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="102.02.0003").exists())
        self.eur.refresh_from_db()
        self.assertEqual(self.eur.muhasebe_id, "102.02.0002")
        self._komut("--uygula")
        self.eur.refresh_from_db()
        self.usd.refresh_from_db()
        self.assertEqual((self.eur.muhasebe_id, self.usd.muhasebe_id), ("102.02.0003", "102.02.0002"))
        yeni = HesapPlani.objects.get(hesap_kodu="102.02.0003")
        ornek = HesapPlani.objects.get(hesap_kodu="102.02.0002")
        self.assertEqual((yeni.hesap_adi, yeni.rapor_grubu, yeni.rapor_kalemi, yeni.parasal, yeni.aktif),
                         ("HALK BANKASI EUR HESABI", ornek.rapor_grubu, ornek.rapor_kalemi, ornek.parasal, ornek.aktif))
        self.assertIn("zaten var", self._komut("--uygula"))          # tekrar: açma yok, bağ aynı

    def test_aktif_hareket_varsa_hicbir_sey_yapmaz(self):
        from decimal import Decimal
        import datetime
        from core.models import Kur
        from core.services import banka_hareket as bh
        Kur.objects.create(tarih=datetime.date(2026, 6, 1), usd_alis=Decimal("30"))
        bh.hareket_olustur(banka_hesap=self.usd, tip="hesaba_odeme", karsi=None, tutar="10",
                           tarih=datetime.date(2026, 6, 1), satirlar=[{"hesap_kodu": "131.01", "tutar": "10"}])
        # usd hesabında hareket var ama hedef eur hesabında yok -> eur taşınabilir; usd taşınmaya çalışılırsa reddedilir
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("banka_hesap_muhasebe_duzelt", "--hesap", str(self.usd.pk), "--yeni-kod", "102.02.0003",
                         "--yeni-ad", "X", "--ornek", "102.02.0002", "--uygula", stdout=out)
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="102.02.0003").exists())    # açılış geri alındı
        self.usd.refresh_from_db()
        self.assertEqual(self.usd.muhasebe_id, "102.02.0002")
