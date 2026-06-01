"""Hesap planı yönetimi (Aşama 1) — alt hesap açma, miras, yaprak/fiş engeli,
silme kuralları, ekran/yetki. Mevcut raporlar etkilenmez (ayrı testlerde)."""
import datetime

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, HesapPlani
from core.services.hesap_plani import (
    HesapHatasi, alt_kod_oner, hesap_adi_guncelle, hesap_olustur, hesap_sil,
    yaprak_hesaplar,
)
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

D = datetime.date


def _fis(u, hesaplar=("100", "600")):
    return fis_olustur(
        tarih=D(2026, 3, 10), kullanici=u,
        satirlar=[SatirGirdi(hesaplar[0], "B", "1.000,00"),
                  SatirGirdi(hesaplar[1], "A", "1.000,00")],
    )


class HesapOlusturTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.u = User.objects.create_superuser("yon", password="x")

    def test_alt_hesap_ve_miras(self):
        ana = HesapPlani.objects.get(hesap_kodu="320")
        alt = hesap_olustur(kod="320.10", ad="tedarikçi a", ust_kodu="320", kullanici=self.u)
        self.assertEqual(alt.ust_hesap_id, "320")
        self.assertEqual(alt.hesap_adi, "TEDARİKÇİ A")          # TR büyük
        self.assertEqual(alt.rapor_grubu, ana.rapor_grubu)      # miras
        self.assertEqual(alt.rapor_kalemi, ana.rapor_kalemi)
        self.assertEqual(alt.parasal, ana.parasal)

    def test_ucuncu_seviye(self):
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=self.u)
        h3 = hesap_olustur(kod="320.10.0001", ad="b", ust_kodu="320.10", kullanici=self.u)
        self.assertEqual(h3.ust_hesap_id, "320.10")

    def test_dorduncu_seviye_engellenir(self):
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=self.u)
        hesap_olustur(kod="320.10.0001", ad="b", ust_kodu="320.10", kullanici=self.u)
        with self.assertRaises(HesapHatasi):
            hesap_olustur(kod="320.10.0001.1", ad="c", ust_kodu="320.10.0001", kullanici=self.u)

    def test_alt_kod_oneki_yanlis(self):
        with self.assertRaises(HesapHatasi):
            hesap_olustur(kod="321.10", ad="a", ust_kodu="320", kullanici=self.u)

    def test_ana_hesap_noktali_engellenir(self):
        with self.assertRaises(HesapHatasi):
            hesap_olustur(kod="999.10", ad="a", rapor_grubu="BILANCO", kullanici=self.u)

    def test_ana_hesap_rapor_grubu_zorunlu(self):
        with self.assertRaises(HesapHatasi):
            hesap_olustur(kod="999", ad="a", kullanici=self.u)

    def test_tekrarli_kod_engellenir(self):
        with self.assertRaises(HesapHatasi):
            hesap_olustur(kod="320", ad="a", rapor_grubu="BILANCO", kullanici=self.u)

    # --- rapor_kalemi doğrulaması (Aşama 2) — kod 9xx: grup açıkça verildiğinden
    #     TDHP anlamı önemsiz; yalnız grup ↔ kalem uyumu sınanır.
    def test_gecerli_kalemle_acilir(self):
        b = hesap_olustur(kod="910", ad="bilanco hesabi", rapor_grubu="BILANCO",
                          rapor_kalemi="DV", kullanici=self.u)
        g = hesap_olustur(kod="920", ad="gelir hesabi", rapor_grubu="GELIR_TABLOSU",
                          rapor_kalemi="A", kullanici=self.u)
        self.assertEqual((b.rapor_kalemi, g.rapor_kalemi), ("DV", "A"))

    def test_gelir_ozet_bos_ve_maliyet_bos_gecerli(self):
        g = hesap_olustur(kod="920", ad="ozet gelir", rapor_grubu="GELIR_TABLOSU",
                          rapor_kalemi="", kullanici=self.u)       # 690 gibi özet hesap
        m = hesap_olustur(kod="930", ad="maliyet", rapor_grubu="MALIYET",
                          rapor_kalemi="", kullanici=self.u)
        self.assertEqual((g.rapor_kalemi, m.rapor_kalemi), ("", ""))

    def test_bilanco_bos_kalem_reddedilir(self):
        with self.assertRaises(HesapHatasi) as cm:
            hesap_olustur(kod="910", ad="x", rapor_grubu="BILANCO",
                          rapor_kalemi="", kullanici=self.u)
        self.assertIn("Bilanço", str(cm.exception))
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="910").exists())

    def test_uyumsuz_kalem_reddedilir(self):
        with self.assertRaises(HesapHatasi):                       # bilançoya gelir kalemi
            hesap_olustur(kod="910", ad="x", rapor_grubu="BILANCO",
                          rapor_kalemi="A", kullanici=self.u)
        with self.assertRaises(HesapHatasi):                       # gelire bilanço kalemi
            hesap_olustur(kod="920", ad="x", rapor_grubu="GELIR_TABLOSU",
                          rapor_kalemi="DV", kullanici=self.u)
        with self.assertRaises(HesapHatasi):                       # gelirde A-J dışı
            hesap_olustur(kod="921", ad="x", rapor_grubu="GELIR_TABLOSU",
                          rapor_kalemi="Z", kullanici=self.u)
        with self.assertRaises(HesapHatasi):                       # maliyete dolu kalem
            hesap_olustur(kod="930", ad="x", rapor_grubu="MALIYET",
                          rapor_kalemi="A", kullanici=self.u)

    def test_mevcut_hesaplarin_hepsi_gecerli(self):
        from core.services.hesap_plani import _kalem_dogrula
        for h in HesapPlani.objects.all():
            try:
                _kalem_dogrula(h.rapor_grubu, h.rapor_kalemi)      # raise ETMEMELİ
            except HesapHatasi:
                self.fail(f"Mevcut hesap geçersiz sayıldı: {h.hesap_kodu} "
                          f"{h.rapor_grubu}/{h.rapor_kalemi!r}")

    def test_alt_hesap_miras_kalem_gecerli(self):
        ana = HesapPlani.objects.get(hesap_kodu="100")            # BILANCO / DV
        alt = hesap_olustur(kod="100.01", ad="merkez kasa", ust_kodu="100", kullanici=self.u)
        self.assertEqual(alt.rapor_kalemi, ana.rapor_kalemi)      # miras -> uyumlu

    def test_kod_oneri(self):
        ana = HesapPlani.objects.get(hesap_kodu="320")
        self.assertEqual(alt_kod_oner(ana), "320.10")
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=self.u)
        self.assertEqual(alt_kod_oner(ana), "320.20")

    def test_ad_guncelle(self):
        h = hesap_adi_guncelle(kod="320", yeni_ad="satıcılar yeni", kullanici=self.u)
        self.assertEqual(h.hesap_adi, "SATICILAR YENİ")


class YapragaFisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.u = User.objects.create_superuser("yon", password="x")

    def test_ust_hesaba_fis_engellenir(self):
        hesap_olustur(kod="100.01", ad="kasa tl", ust_kodu="100", kullanici=self.u)
        with self.assertRaises(YevmiyeHatasi):
            _fis(self.u, hesaplar=("100", "600"))   # 100 artık üst hesap

    def test_yaprak_hesaba_fis_olur(self):
        hesap_olustur(kod="100.01", ad="kasa tl", ust_kodu="100", kullanici=self.u)
        f = _fis(self.u, hesaplar=("100.01", "600"))
        self.assertEqual(f.satirlar.count(), 2)

    def test_yaprak_listesi_ust_haric(self):
        hesap_olustur(kod="100.01", ad="x", ust_kodu="100", kullanici=self.u)
        kodlar = set(yaprak_hesaplar().values_list("hesap_kodu", flat=True))
        self.assertIn("100.01", kodlar)
        self.assertNotIn("100", kodlar)
        self.assertIn("600", kodlar)


class HesapSilmeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.u = User.objects.create_superuser("yon", password="x")

    def test_yevmiyeli_silinemez(self):
        _fis(self.u)
        with self.assertRaises(HesapHatasi):
            hesap_sil(kod="100", kullanici=self.u)
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="100").silindi)

    def test_alt_hesapli_silinemez(self):
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=self.u)
        with self.assertRaises(HesapHatasi):
            hesap_sil(kod="320", kullanici=self.u)

    def test_bos_hesap_silinir(self):
        hesap_olustur(kod="320.10", ad="a", ust_kodu="320", kullanici=self.u)
        hesap_sil(kod="320.10", kullanici=self.u)
        self.assertTrue(HesapPlani.objects.get(hesap_kodu="320.10").silindi)
        # silinince 320 tekrar yaprak olur
        self.assertIn("320", set(yaprak_hesaplar().values_list("hesap_kodu", flat=True)))


class HesapPlaniEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="hesap_plani")
        cls.kisitli = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=cls.kisitli, ekran_kod="mizan")

    def test_yetkisiz_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:hesap_plani")).status_code, 403)

    def test_yetkili_200_agac(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:hesap_plani"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Hesap Planı")
        self.assertContains(r, "320")

    def test_ekleme_view(self):
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:hesap_ekle"),
                             {"ust_kodu": "320", "kod": "320.10", "ad": "alt a"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="320.10").exists())

    def test_silme_view_yevmiyeli_engellenir(self):
        su = User.objects.create_superuser("su", password="x")
        _fis(su)
        self.client.force_login(self.yetkili)
        self.client.post(reverse("core:hesap_sil", args=["100"]))
        self.assertFalse(HesapPlani.objects.get(hesap_kodu="100").silindi)
