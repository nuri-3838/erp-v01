"""kur_cek management command testleri — eksik gün telafisi (tarih aralığı mantığı).

Ağa çıkılmaz: kurlari_guncelle mock'lanır; komutun hangi aralığı çağırdığı doğrulanır.
"bugün" TR takvim günüdür (komutla aynı tr_bugun kullanılır).
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from core.management.commands.kur_cek import tr_bugun
from core.models import Kur
from core.services.tcmb import AZAMI_GUN


def _calistir(*args):
    """Komutu mock'lu çalıştırır; kurlari_guncelle'e geçen (baslangic, bitis) döner."""
    cagrilar = []

    def sahte(bas, bit, **kw):
        cagrilar.append((bas, bit))
        return {"yayin": 0, "yazilan": 0, "atlanan": 0}

    with patch("core.management.commands.kur_cek.kurlari_guncelle", side_effect=sahte):
        call_command("kur_cek", *args)
    return cagrilar


class KurCekKomutTest(TestCase):
    def test_bos_tablo_son_7_gun(self):
        cagrilar = _calistir()
        b = tr_bugun()
        self.assertEqual(cagrilar, [(b - datetime.timedelta(days=7), b)])

    def test_bos_tablo_gun_parametresi(self):
        cagrilar = _calistir("--gun", "3")
        b = tr_bugun()
        self.assertEqual(cagrilar, [(b - datetime.timedelta(days=3), b)])

    def test_son_tarihten_telafi(self):
        b = tr_bugun()
        Kur.objects.create(tarih=b - datetime.timedelta(days=3), usd_alis=Decimal("30"))
        cagrilar = _calistir()
        self.assertEqual(cagrilar, [(b - datetime.timedelta(days=3), b)])

    def test_buyuk_bosluk_caplanir(self):
        b = tr_bugun()
        Kur.objects.create(tarih=b - datetime.timedelta(days=400), usd_alis=Decimal("30"))
        cagrilar = _calistir()
        self.assertEqual(cagrilar, [(b - datetime.timedelta(days=AZAMI_GUN - 1), b)])

    def test_son_tarih_gelecekteyse_bugun(self):
        b = tr_bugun()
        Kur.objects.create(tarih=b + datetime.timedelta(days=5), usd_alis=Decimal("30"))
        cagrilar = _calistir()
        self.assertEqual(cagrilar, [(b, b)])
