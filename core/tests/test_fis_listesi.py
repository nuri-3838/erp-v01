"""Fiş Listesi + düzenleme + iptal ekranı testleri.

Kapsam: liste doğru fişleri/toplamları gösterir; tarih aralığı filtreler; iptal fiş
listede İPTAL damgalı; düzenleme dengeyi korur ve eski satırları fiziksel siler;
audit (updated_by) korunur; iptal sonrası fiş mizana girmez; yetkisiz kullanıcı
ekranı göremez (403 + menüde yok).
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, YevmiyeFisi, YevmiyeSatir
from core.services.raporlar import mizan
from core.services.yevmiye import SatirGirdi, fis_olustur

D = datetime.date


def _try(hesap, taraf, tutar):
    return SatirGirdi(hesap_kodu=hesap, taraf=taraf, islem_tutari=tutar,
                      islem_pb="TRY", islem_kuru=Decimal("1"))


def _duzenle_payload(tutar="1.000,00", tarih="2026-03-10", aciklama="kasa"):
    """Düzenleme POST gövdesi (2 satır: 100 borç / 600 alacak)."""
    return {
        "tarih": tarih, "aciklama": aciklama, "kur_usd": "",
        "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "2",
        "form-MIN_NUM_FORMS": "2", "form-MAX_NUM_FORMS": "1000",
        "form-0-hesap": "100", "form-0-islem_pb": "TRY",
        "form-0-borc": tutar, "form-0-alacak": "",
        "form-0-islem_kuru": "1", "form-0-aciklama": "",
        "form-1-hesap": "600", "form-1-islem_pb": "TRY",
        "form-1-borc": "", "form-1-alacak": tutar,
        "form-1-islem_kuru": "1", "form-1-aciklama": "",
    }


class FisListesiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _Kur.objects.get_or_create(tarih=_dtk.date(2020, 1, 1), defaults={"usd_alis": _Dec("30")})
        cls.yon = User.objects.create_superuser("yon", password="parola1234")

    def setUp(self):
        self.client.force_login(self.yon)

    def _fis(self, tutar="1.000,00", tarih=D(2026, 3, 10), aciklama="ilk fiş"):
        return fis_olustur(tarih=tarih, aciklama=aciklama, satirlar=[
            _try("100", "B", tutar), _try("600", "A", tutar)], kullanici=self.yon)

    # --- Liste ---------------------------------------------------------------
    def test_liste_fisleri_ve_toplamlari_gosterir(self):
        f = self._fis()
        r = self.client.get(reverse("core:fis_listesi"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f"{f.yil}/{f.fis_no}")
        self.assertContains(r, "10.03.2026")
        self.assertContains(r, "İLK FİŞ")        # TR büyük harf
        self.assertContains(r, "1.000,00")        # borç/alacak toplamı, TR biçim
        self.assertContains(r, "Aktif")

    def test_tarih_araligi_filtreler(self):
        self._fis(tarih=D(2026, 3, 10), aciklama="mart fişi")
        self._fis(tarih=D(2026, 8, 10), aciklama="ağustos fişi")
        r = self.client.get(reverse("core:fis_listesi"),
                             {"baslangic": "2026-03-01", "bitis": "2026-03-31"})
        self.assertContains(r, "MART FİŞİ")
        self.assertNotContains(r, "AĞUSTOS FİŞİ")

    def test_iptal_fis_listede_damgali(self):
        f = self._fis()
        self.client.post(reverse("core:fis_iptal", args=[f.pk]))
        r = self.client.get(reverse("core:fis_listesi"))
        self.assertContains(r, "İPTAL")
        self.assertContains(r, "1.000,00")  # iptal fişin orijinal toplamı yine görünür

    # --- Düzenleme -----------------------------------------------------------
    def test_duzenle_formu_dolu_gelir(self):
        f = self._fis(aciklama="kasa tahsilatı")
        r = self.client.get(reverse("core:fis_duzenle", args=[f.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "KASA TAHSİLATI")
        self.assertContains(r, "1.000,00")

    def test_duzenle_gunceller_ve_eski_satir_silinir(self):
        f = self._fis(tutar="1.000,00")
        r = self.client.post(reverse("core:fis_duzenle", args=[f.pk]),
                             _duzenle_payload(tutar="2.500,00", aciklama="düzeltildi"))
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(f.aciklama, "DÜZELTİLDİ")
        self.assertEqual(f.satirlar.get(hesap_id="100").borc, Decimal("2500.00"))
        # Eski satırlar fiziksel silindi → fişe bağlı tam 2 satır var, denk
        self.assertEqual(YevmiyeSatir.objects.filter(fis=f).count(), 2)
        self.assertEqual(f.satirlar.get(hesap_id="600").alacak, Decimal("2500.00"))

    def test_duzenle_dengesiz_reddedilir_degismez(self):
        f = self._fis(tutar="1.000,00")
        data = _duzenle_payload(tutar="1.000,00")
        data["form-1-alacak"] = "900,00"  # borç 1000 ≠ alacak 900
        r = self.client.post(reverse("core:fis_duzenle", args=[f.pk]), data)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "dengesiz")
        f.refresh_from_db()
        self.assertEqual(f.satirlar.get(hesap_id="100").borc, Decimal("1000.00"))

    def test_duzenle_audit_updated_by(self):
        editor = User.objects.create_user("editor", password="parola1234", is_superuser=True)
        f = self._fis()
        self.client.force_login(editor)
        self.client.post(reverse("core:fis_duzenle", args=[f.pk]),
                         _duzenle_payload(tutar="1.500,00"))
        f.refresh_from_db()
        self.assertEqual(f.updated_by_id, editor.pk)

    def test_iptal_fis_duzenlenemez(self):
        f = self._fis()
        self.client.post(reverse("core:fis_iptal", args=[f.pk]))
        r = self.client.get(reverse("core:fis_duzenle", args=[f.pk]))
        self.assertEqual(r.status_code, 302)  # salt-okunur detaya yönlendirilir

    # --- İptal + raporlar ----------------------------------------------------
    def test_iptal_sonrasi_mizana_girmez(self):
        f = self._fis(tutar="1.000,00", tarih=D(2026, 3, 10))
        m1 = mizan(D(2026, 1, 1), D(2026, 12, 31))
        self.assertTrue(any(s.hesap_kodu == "100" for s in m1.satirlar))
        self.client.post(reverse("core:fis_iptal", args=[f.pk]))
        m2 = mizan(D(2026, 1, 1), D(2026, 12, 31))
        self.assertFalse(any(s.hesap_kodu == "100" for s in m2.satirlar))
        f.refresh_from_db()
        self.assertTrue(f.silindi)

    def test_iptal_audit(self):
        f = self._fis()
        self.client.post(reverse("core:fis_iptal", args=[f.pk]))
        f.refresh_from_db()
        self.assertEqual(f.updated_by_id, self.yon.pk)
        self.assertIsNotNone(f.silindi_at)

    # --- Arama + sayfalama -------------------------------------------------
    def test_arama_aciklama(self):
        self._fis(aciklama="alfa")
        self._fis(aciklama="beta")
        r = self.client.get(reverse("core:fis_listesi"),
                             {"ara": "alfa", "baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertContains(r, "ALFA")
        self.assertNotContains(r, "BETA")
        self.assertContains(r, "1.000,00")   # toplam tam (arama join'i bozmaz)

    def test_arama_tutar(self):
        self._fis(tutar="1.000,00", aciklama="alfa")
        self._fis(tutar="2.500,00", aciklama="beta")
        r = self.client.get(reverse("core:fis_listesi"),
                             {"ara": "2.500,00", "baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertContains(r, "BETA")
        self.assertNotContains(r, "ALFA")

    def test_arama_hesap_kodu(self):
        self._fis(aciklama="gama")   # 100 + 600 hesaplı
        r = self.client.get(reverse("core:fis_listesi"),
                             {"ara": "600", "baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertContains(r, "GAMA")

    def test_arama_fis_no(self):
        f = self._fis(aciklama="delta")
        r = self.client.get(reverse("core:fis_listesi"),
                             {"ara": str(f.fis_no), "baslangic": "2026-01-01", "bitis": "2026-12-31"})
        self.assertContains(r, "DELTA")

    def test_sayfalama(self):
        for _ in range(51):
            self._fis(aciklama="kayit")
        ortak = {"baslangic": "2026-01-01", "bitis": "2026-12-31"}
        r = self.client.get(reverse("core:fis_listesi"), ortak)
        self.assertEqual(len(r.context["fisler"]), 50)
        self.assertEqual(r.context["fisler"].paginator.count, 51)
        self.assertEqual(r.context["fisler"].paginator.num_pages, 2)
        r2 = self.client.get(reverse("core:fis_listesi"), {**ortak, "sayfa": "2"})
        self.assertEqual(len(r2.context["fisler"]), 1)


class FisListesiYetkiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _Kur.objects.get_or_create(tarih=_dtk.date(2020, 1, 1), defaults={"usd_alis": _Dec("30")})
        cls.kisitli = User.objects.create_user(
            "kis", password="x", first_name="AYŞE", last_name="DEMİR")
        EkranYetki.objects.create(kullanici=cls.kisitli, ekran_kod="mizan")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="fis_listesi")

    def test_yetkisiz_kullanici_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:fis_listesi")).status_code, 403)

    def test_yetkili_kullanici_200(self):
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(reverse("core:fis_listesi")).status_code, 200)

    def test_menude_yetkisize_gizli(self):
        self.client.force_login(self.kisitli)
        r = self.client.get(reverse("core:mizan"))
        self.assertNotContains(r, "Yevmiye Fişleri")

    def test_menude_yetkiliye_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:fis_listesi"))
        self.assertContains(r, "Yevmiye Fişleri")
