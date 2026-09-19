"""core.tarih — Türkiye günü yardımcısı (TIME_ZONE=UTC olduğundan timezone.localdate() TR
00:00-03:00 arası bir önceki günü verir)."""
from datetime import date, datetime, timezone as dt_timezone
from unittest import mock

from django.test import SimpleTestCase

from core.tarih import tr_bugun

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
