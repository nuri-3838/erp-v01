"""Cari ekstresi (devirli) servis + view testleri. Carinin yaprak hesabının
hareketleri yevmiyeden; devir = aralık öncesi net, yürüyen bakiye devirden başlar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Kur
from core.services.fatura import fatura_olustur
from core.services.raporlar import ekstre_devirli
from core.tests.test_fatura import FaturaTestTemel

D = datetime.date


class CariEkstreTest(FaturaTestTemel):
    def test_devirli_ekstre_yurur(self):
        # Aralık ÖNCESİ (Şubat) alış 1000+%20 -> cari alacak 1200 (devir);
        # aralık İÇİ (Mart) alış 500+%20 -> alacak 600.
        Kur.objects.create(tarih=D(2026, 2, 1), usd_alis=Decimal("30"))
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 2, 1), satirlar=self._satir(miktar="10", fiyat="100"))
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="5", fiyat="100"))
        eks = ekstre_devirli("320.10.0001", D(2026, 3, 1), D(2026, 3, 31))
        self.assertEqual(eks.acilis, Decimal("-1200.00"))          # devir (alacak bakiye)
        self.assertEqual(len(eks.satirlar), 1)                     # yalnız Mart hareketi
        self.assertEqual(eks.satirlar[0].alacak, Decimal("600.00"))
        self.assertEqual(eks.satirlar[0].yur_bakiye, Decimal("-1800.00"))  # devir + dönem
        self.assertEqual(eks.kapanis_bakiye, Decimal("-1800.00"))

    def test_devirsiz_hesap_ekstresi_aynen(self):
        # ekstre() devirsiz (acilis=0) -> eski davranış korunur
        from core.services.raporlar import ekstre
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir(miktar="5", fiyat="100"))
        eks = ekstre("320.10.0001", D(2026, 3, 1), D(2026, 3, 31))
        self.assertEqual(eks.acilis, Decimal("0.00"))
        self.assertEqual(eks.satirlar[0].yur_bakiye, Decimal("-600.00"))

    def test_view_200(self):
        su = User.objects.create_superuser("eks", password="x")
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk,
                       tarih=D(2026, 3, 10), satirlar=self._satir())
        self.client.force_login(su)
        r = self.client.get(reverse("core:cari_ekstresi", args=[self.tedarikci.pk]),
                            {"baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TEDARİKÇİ A")
        self.assertContains(r, "Devir")
        self.assertContains(r, reverse("core:cari_detay", args=[self.tedarikci.pk]))
