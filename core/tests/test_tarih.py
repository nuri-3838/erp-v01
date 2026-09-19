"""core.tarih — Türkiye günü yardımcısı (TIME_ZONE=UTC olduğundan timezone.localdate() TR
00:00-03:00 arası bir önceki günü verir)."""
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import SimpleTestCase

from core.tarih import kidem_metni, tamamlanan_yil, tr_bugun, yil_donumu

UTC = dt_timezone.utc


class TrBugunTest(SimpleTestCase):
    def _bugun(self, an):
        with mock.patch("django.utils.timezone.now", return_value=an):
            return tr_bugun()

    def test_utc_gece_yarisi_oncesi_tr_ertesi_gundur(self):
        # 22:30 UTC = 01:30 TR (UTC+3) -> ertesi gün
        self.assertEqual(self._bugun(datetime(2026, 9, 19, 22, 30, tzinfo=UTC)), date(2026, 9, 20))

    def test_sinir_21_00_utc_tr_gece_yarisidir(self):
        self.assertEqual(self._bugun(datetime(2026, 9, 19, 21, 0, tzinfo=UTC)), date(2026, 9, 20))

    def test_sinirin_bir_dakika_oncesi_ayni_gun(self):
        self.assertEqual(self._bugun(datetime(2026, 9, 19, 20, 59, tzinfo=UTC)), date(2026, 9, 19))

    def test_gun_ortasi_ayni_gun(self):
        self.assertEqual(self._bugun(datetime(2026, 9, 19, 12, 0, tzinfo=UTC)), date(2026, 9, 19))

    def test_yil_sonu_sinirinda_ertesi_yila_gecer(self):
        self.assertEqual(self._bugun(datetime(2026, 12, 31, 22, 0, tzinfo=UTC)), date(2027, 1, 1))


class YilDonumuTest(SimpleTestCase):
    def test_normal_ve_artik_yil(self):
        self.assertEqual(yil_donumu(date(2020, 3, 15), 1), date(2021, 3, 15))
        self.assertEqual(yil_donumu(date(2020, 3, 15), 0), date(2020, 3, 15))
        self.assertEqual(yil_donumu(date(2024, 2, 29), 4), date(2028, 2, 29))

    def test_29_subat_artik_olmayan_yilda_28_subat(self):
        self.assertEqual(yil_donumu(date(2024, 2, 29), 1), date(2025, 2, 28))
        self.assertEqual(yil_donumu(date(2024, 2, 29), 3), date(2027, 2, 28))


class TamamlananYilTest(SimpleTestCase):
    def test_yil_donumu_gunu_dahil(self):
        self.assertEqual(tamamlanan_yil(date(2020, 3, 15), date(2021, 3, 14)), 0)
        self.assertEqual(tamamlanan_yil(date(2020, 3, 15), date(2021, 3, 15)), 1)
        self.assertEqual(tamamlanan_yil(date(2020, 3, 15), date(2026, 9, 19)), 6)

    def test_baslangictan_once_sifir(self):
        self.assertEqual(tamamlanan_yil(date(2026, 1, 1), date(2025, 12, 31)), 0)
        self.assertEqual(tamamlanan_yil(date(2026, 1, 1), date(2026, 1, 1)), 0)

    def test_29_subat_dogumlu_yas(self):
        dogum = date(2000, 2, 29)
        self.assertEqual(tamamlanan_yil(dogum, date(2025, 2, 27)), 24)
        self.assertEqual(tamamlanan_yil(dogum, date(2025, 2, 28)), 25)     # artık olmayan yılda 28 Şubat
        self.assertEqual(tamamlanan_yil(dogum, date(2028, 2, 29)), 28)


class KidemMetniTest(SimpleTestCase):
    def test_yil_ve_ay(self):
        self.assertEqual(kidem_metni(date(2020, 3, 15), date(2026, 9, 19)), "6 yıl 6 ay")
        self.assertEqual(kidem_metni(date(2025, 9, 19), date(2026, 9, 19)), "1 yıl")
        self.assertEqual(kidem_metni(date(2026, 1, 10), date(2026, 9, 19)), "8 ay")

    def test_bir_aydan_az(self):
        self.assertEqual(kidem_metni(date(2026, 9, 1), date(2026, 9, 19)), "1 aydan az")
        self.assertEqual(kidem_metni(date(2026, 1, 31), date(2026, 2, 28)), "1 aydan az")

    def test_gelecek_giris(self):
        self.assertEqual(kidem_metni(date(2027, 1, 1), date(2026, 9, 19)), "Henüz başlamadı")
