"""0190: kaldırılan Fason Kesim ekranlarının yetki satırları silinir, diğerleri kalır."""
import importlib

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase

from core.models import EkranYetki

m = importlib.import_module("core.migrations.0190_fason_kesim_yetki_temizle")


class YetkiTemizleTest(TestCase):
    def test_yalniz_olu_kodlar_silinir(self):
        u = User.objects.create_user("yt_u", password="x")
        for k in ("fason_kayitlari", "fason_kesim_tanimlari", "fason_hesapla", "fason_fiyatlari"):
            EkranYetki.objects.create(kullanici=u, ekran_kod=k)
        m.temizle(apps, None)
        self.assertEqual(sorted(EkranYetki.objects.values_list("ekran_kod", flat=True)), ["fason_fiyatlari", "fason_hesapla"])
