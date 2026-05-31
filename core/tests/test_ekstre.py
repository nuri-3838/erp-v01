"""Hesap ekstresi testleri — doğru satırlar, yürüyen bakiye, iptal hariç,
tarih aralığı + mizan tutarlılığı, yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki
from core.services.raporlar import ekstre as ekstre_servis, mizan
from core.services.yevmiye import SatirGirdi, fis_iptal, fis_olustur

D = datetime.date


def _try(hesap, taraf, tutar):
    return SatirGirdi(hesap_kodu=hesap, taraf=taraf, islem_tutari=Decimal(tutar),
                      islem_pb="TRY", islem_kuru=Decimal("1"))


class EkstreServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")

    def test_dogru_hesabın_satirlarini_gosterir(self):
        fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _try("100", "B", "1000"), _try("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 11), satirlar=[
            _try("320", "B", "500"), _try("102", "A", "500")])
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(len(eks.satirlar), 1)
        self.assertEqual(eks.satirlar[0].borc, Decimal("1000.00"))
        self.assertEqual(eks.satirlar[0].alacak, Decimal("0.00"))
        self.assertEqual(eks.hesap_kodu, "100")
        self.assertIn("KASA", eks.hesap_adi)

    def test_yurüyen_bakiye_dogru(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "1000"), _try("600", "A", "1000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try("320", "B", "300"), _try("100", "A", "300")])
        fis_olustur(tarih=D(2026, 3, 3), satirlar=[
            _try("100", "B", "500"), _try("320", "A", "500")])
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(len(eks.satirlar), 3)
        self.assertEqual(eks.satirlar[0].yur_bakiye, Decimal("1000.00"))
        self.assertEqual(eks.satirlar[1].yur_bakiye, Decimal("700.00"))
        self.assertEqual(eks.satirlar[2].yur_bakiye, Decimal("1200.00"))
        self.assertEqual(eks.bakiye, Decimal("1200.00"))
        self.assertEqual(eks.toplam_borc, Decimal("1500.00"))
        self.assertEqual(eks.toplam_alacak, Decimal("300.00"))

    def test_alacak_bakiye_negatif_net(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("600", "A", "800"), _try("100", "B", "800")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try("600", "B", "200"), _try("100", "A", "200")])
        eks = ekstre_servis("600", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(eks.satirlar[0].yur_bakiye, Decimal("-800.00"))
        self.assertEqual(eks.satirlar[1].yur_bakiye, Decimal("-600.00"))
        self.assertEqual(eks.bakiye, Decimal("-600.00"))

    def test_iptal_fis_satirlari_ekstrede_yok(self):
        f = fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "500"), _try("600", "A", "500")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try("100", "B", "200"), _try("600", "A", "200")])
        fis_iptal(f)
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(len(eks.satirlar), 1)
        self.assertEqual(eks.satirlar[0].borc, Decimal("200.00"))

    def test_tarih_araligi_filtreler(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "500"), _try("600", "A", "500")])
        fis_olustur(tarih=D(2026, 8, 1), satirlar=[
            _try("100", "B", "300"), _try("600", "A", "300")])
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 3, 31))
        self.assertEqual(len(eks.satirlar), 1)
        self.assertEqual(eks.satirlar[0].tarih, D(2026, 3, 1))

    def test_ekstre_toplam_mizan_bakiyesiyle_tutuyor(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "2000"), _try("600", "A", "2000")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try("320", "B", "600"), _try("100", "A", "600")])
        b, s = D(2026, 1, 1), D(2026, 12, 31)
        m = mizan(b, s)
        harita = {r.hesap_kodu: r for r in m.satirlar}
        eks = ekstre_servis("100", b, s)
        s100 = harita["100"]
        self.assertEqual(eks.toplam_borc, s100.borc)
        self.assertEqual(eks.toplam_alacak, s100.alacak)
        self.assertEqual(eks.bakiye, s100.borc - s100.alacak)

    def test_hareketsiz_hesap_bos_satir_listesi(self):
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(eks.satirlar, [])
        self.assertEqual(eks.toplam_borc, Decimal("0.00"))
        self.assertEqual(eks.bakiye, Decimal("0.00"))
        self.assertIn("KASA", eks.hesap_adi)

    def test_son_satir_bakiye_ekstre_bakiyesiyle_esit(self):
        for i in range(1, 5):
            fis_olustur(tarih=D(2026, i, 15), satirlar=[
                _try("100", "B", str(i * 100)), _try("600", "A", str(i * 100))])
        eks = ekstre_servis("100", D(2026, 1, 1), D(2026, 12, 31))
        self.assertEqual(eks.satirlar[-1].yur_bakiye, eks.bakiye)


class EkstreViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        cls.yon = User.objects.create_superuser("yon", password="parola1234")
        cls.mizan_user = User.objects.create_user("miz", password="x")
        EkranYetki.objects.create(kullanici=cls.mizan_user, ekran_kod="mizan")
        cls.bos = User.objects.create_user("bos", password="x")

    def setUp(self):
        self.client.force_login(self.yon)

    def _url(self, kod="100", b="2026-01-01", s="2026-12-31"):
        return reverse("core:hesap_ekstresi", args=[kod]) + f"?baslangic={b}&bitis={s}"

    def test_view_200_ve_hesap_adi(self):
        fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _try("100", "B", "500"), _try("600", "A", "500")])
        r = self.client.get(self._url("100"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "100")
        self.assertContains(r, "KASA")
        self.assertContains(r, "500,00")

    def test_view_tarih_araligi_mizan_ile_tutarli(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "500"), _try("600", "A", "500")])
        fis_olustur(tarih=D(2026, 8, 1), satirlar=[
            _try("100", "B", "300"), _try("600", "A", "300")])
        r = self.client.get(self._url("100", "2026-01-01", "2026-03-31"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "01.03.2026")
        self.assertNotContains(r, "01.08.2026")

    def test_view_mizan_linki_ekstre_aciyor(self):
        fis_olustur(tarih=D(2026, 5, 1), satirlar=[
            _try("100", "B", "750"), _try("600", "A", "750")])
        r = self.client.get(
            reverse("core:mizan") + "?baslangic=2026-01-01&bitis=2026-12-31"
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'href="/ekstre/100/?baslangic=2026-01-01&bitis=2026-12-31"')

    def test_olmayan_hesap_404(self):
        r = self.client.get(self._url("999"))
        self.assertEqual(r.status_code, 404)

    def test_mizan_yetkili_erisebilir(self):
        self.client.force_login(self.mizan_user)
        r = self.client.get(self._url("100"))
        self.assertEqual(r.status_code, 200)

    def test_yetkisiz_kullanici_403(self):
        self.client.force_login(self.bos)
        r = self.client.get(self._url("100"))
        self.assertEqual(r.status_code, 403)

    def test_yurüyen_bakiye_html_de_gorünür(self):
        fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try("100", "B", "1200"), _try("600", "A", "1200")])
        fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try("320", "B", "200"), _try("100", "A", "200")])
        r = self.client.get(self._url("100"))
        self.assertContains(r, "1.200,00 B")
        self.assertContains(r, "1.000,00 B")
