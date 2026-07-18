"""Teklif & Sipariş — iskelet: modül/ekranların menüde görünmesi + erişim/yetki."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

EKRANLAR = ("satinalma_teklifleri", "satinalma_siparisleri",
            "satis_teklifleri", "satis_siparisleri")


class TeklifSiparisIskeletTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsyon", password="x")
        cls.bos = User.objects.create_user("tsbos", password="x")

    def test_modul_ve_ekranlar_moduller_de(self):
        from core.moduller import MODULLER
        kodlar = {m.kod for m in MODULLER}
        self.assertIn("SATINALMA", kodlar)
        self.assertIn("SATIS", kodlar)
        sa = next(m for m in MODULLER if m.kod == "SATINALMA")
        st = next(m for m in MODULLER if m.kod == "SATIS")
        self.assertEqual([e.kod for e in sa.ekranlar],
                         ["satinalma_teklifleri", "satinalma_siparisleri"])
        self.assertEqual([e.kod for e in st.ekranlar],
                         ["satis_teklifleri", "satis_siparisleri"])
        # operasyonel modül (yönetici-only değil)
        self.assertFalse(sa.yonetici_modulu)
        self.assertFalse(st.yonetici_modulu)

    def test_ekranlar_200_ve_iskelet(self):
        self.client.force_login(self.yon)
        for ad in EKRANLAR:
            r = self.client.get(reverse("core:" + ad))
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Yapım aşamasında")

    def test_menude_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "Satınalma")
        for ad in EKRANLAR:
            self.assertContains(r, reverse("core:" + ad))

    def test_yetkisiz_403(self):
        self.client.force_login(self.bos)
        for ad in EKRANLAR:
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 403)
