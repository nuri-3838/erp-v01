"""Depo sadeleştirme (tek fabrika deposu "SEMTA DEPO"): ``depo_sadelestir`` komutu (--dry-run), pasif depo kuralı, formlarda pasif depoların çıkmaması ve
varsayılan depo; ayrıca ÜS ekran düzeltmeleri (eksik malzeme bölümü/rozeti, fason düğmesi yalnız istasyon 10, revizyon işlem zamanı)."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import UretimEmriBaslikForm
from core.models import Cari, Depo, Stok, StokHareket
from core.services import depo as depo_servis
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.stok_ortalama import degerleme_raporu
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class DepoTaban(kt.KapanisTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.d150 = Depo.objects.create(kod="150", ad="DEPO - HAMMADDE")
        cls.d151 = Depo.objects.create(kod="151", ad="DEPO - ÜRETİM")
        cls.d152 = Depo.objects.create(kod="152", ad="DEPO - MAMUL")
        fasoncu = Cari.objects.create(kod="FSN", unvan="FASONCU TEST")
        cls.d999 = Depo.objects.create(kod="999", ad="FASON-TEST", fason_cari=fasoncu)

    def girisler(self):
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.d150.pk, tarih=date(2026, 1, 3), tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("100"))
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.d151.pk, tarih=date(2026, 1, 3), tur=StokHareket.Tur.GIRIS, miktar=D("20"), giris_tutar_try=D("300"))
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.d151.pk, tarih=date(2026, 1, 3), tur=StokHareket.Tur.GIRIS, miktar=D("5"), giris_tutar_try=D("50"))
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.d999.pk, tarih=date(2026, 1, 3), tur=StokHareket.Tur.GIRIS, miktar=D("7"), giris_tutar_try=D("70"))


class DepoSadelestirTest(DepoTaban):
    def calistir(self, *args):
        cikti = StringIO()
        call_command("depo_sadelestir", *args, stdout=cikti)
        return cikti.getvalue()

    def test_dry_run_hicbir_sey_yazmaz_gercek_kosu_tasir_ve_pasif_yapar(self):
        self.girisler()
        once = {d.pk: eldeki_miktar(self.ham, d.pk) for d in (self.d150, self.d151, self.d152)}
        deger_once = [(k["kod"], k["stok_degeri"], k["mizan"], k["fark"]) for k in degerleme_raporu()["karsilastirma"]]
        sayim = StokHareket.objects.count()
        cikti = self.calistir("--dry-run")
        self.assertIn("DRY-RUN", cikti)
        self.assertIn("Sf-HAM", cikti)
        self.assertEqual(StokHareket.objects.count(), sayim)
        self.d150.refresh_from_db()
        self.assertEqual(self.d150.ad, "DEPO - HAMMADDE")
        self.assertTrue(Depo.objects.get(pk=self.d151.pk).aktif)

        cikti = self.calistir()
        self.assertIn("Tamam", cikti)
        for d in (self.d150, self.d151, self.d152):
            d.refresh_from_db()
        self.assertEqual((self.d150.ad, self.d150.aktif, self.d151.aktif, self.d152.aktif), ("SEMTA DEPO", True, False, False))
        self.assertEqual(eldeki_miktar(self.ham, self.d150.pk), once[self.d150.pk] + once[self.d151.pk])
        self.assertEqual((eldeki_miktar(self.ham, self.d151.pk), eldeki_miktar(self.stok, self.d151.pk)), (D("0"), D("0")))
        self.assertEqual(eldeki_miktar(self.stok, self.d150.pk), D("5"))
        self.assertEqual(eldeki_miktar(self.stok, self.d999.pk), D("7"))                          # fason deposu aynen
        self.assertTrue(Depo.objects.get(pk=self.d999.pk).aktif)
        self.assertEqual([(k["kod"], k["stok_degeri"], k["mizan"], k["fark"]) for k in degerleme_raporu()["karsilastirma"]], deger_once)   # değerleme/mizan aynı
        self.assertEqual(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.TRANSFER, tarih=timezone.localdate()).count(), 4)    # 2 stok × (çıkış+giriş)
        self.assertIn("Zaten tamam", self.calistir())                                              # tekrar: yapılacak bir şey yok

    def test_pasif_depo_secimde_cikmaz_varsayilan_semta_depo(self):
        self.girisler()
        self.calistir()
        idler = set(depo_servis.aktif_depolar().values_list("pk", flat=True))
        self.assertIn(self.d150.pk, idler)
        self.assertNotIn(self.d151.pk, idler)
        self.assertNotIn(self.d152.pk, idler)
        self.assertIn(self.d151.pk, set(depo_servis.tum_depolar().values_list("pk", flat=True)))   # yönetim listesinde görünür
        self.assertEqual(depo_servis.varsayilan_depo().pk, self.d150.pk)
        self.assertEqual(UretimEmriBaslikForm().fields["depo"].initial, self.d150.pk)
        self.assertNotIn(self.d151.pk, set(UretimEmriBaslikForm().fields["depo"].queryset.values_list("pk", flat=True)))
        self.client.force_login(self.yon)
        liste = self.client.get(reverse("core:depolar"))
        self.assertContains(liste, "Pasif")
        self.assertContains(liste, "DEPO - ÜRETİM")

    def test_eldeki_stoklu_depo_pasif_yapilamaz(self):
        self.girisler()
        with self.assertRaisesMessage(depo_servis.DepoHatasi, "eldeki stok var"):
            depo_servis.depo_guncelle(self.d151, kod="151", ad="DEPO - ÜRETİM", aktif=False)
        self.assertTrue(Depo.objects.get(pk=self.d151.pk).aktif)
        depo_servis.depo_guncelle(self.d152, kod="152", ad="DEPO - MAMUL", aktif=False)           # boş depo pasif olabilir
        self.assertFalse(Depo.objects.get(pk=self.d152.pk).aktif)


class UsEkranDuzeltmeTest(kt.KapanisTaban):
    def test_eksik_malzeme_bolumu_ve_liste_rozeti(self):
        sip = self.siparis("150")                                                                  # 150 mamul → 150 ham gerekir, eldeki 100
        emir = self.emir_ac(sip)
        d = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(d, "Eksik Malzeme (satınalma ihtiyacı)")
        self.assertContains(d, "Sf-HAM")
        self.assertContains(d, "50,000")                                                           # eksik = 150 − 100
        self.assertContains(self.client.get(reverse("core:uretim_emirleri")), "Eksik malzeme: 1")
        from core.services import uretim
        eksik = uretim.uretim_emri_eksikleri(emir)
        self.assertEqual([(e["stok"].kod, e["gerekli"], e["kullanilabilir"], e["eksik"]) for e in eksik], [("Sf-HAM", D("150"), D("100"), D("50"))])

    def test_eksik_yoksa_bolum_bos_mesaj_ve_rozet_yok(self):
        emir = self.emir_ac(self.siparis("3"))
        d = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(d, "Eksik malzeme yok")
        self.assertNotContains(self.client.get(reverse("core:uretim_emirleri")), "Eksik malzeme: ")

    def test_fason_donus_dugmesi_yalniz_istasyon_10(self):
        emir = self.emir_ac(self.siparis("3"))
        ie = emir.istasyon_emirleri.get()
        url = reverse("core:fason_donus_ekle") + f"?istasyon_emri={ie.pk}"
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), url)
        self.assertNotContains(self.client.get(reverse("core:istasyon_emirleri")), url)
        uret = reverse("core:istasyon_emri_uret", args=[ie.pk])
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), uret)               # fason değilde "Üretildi" var
        ie.istasyon.kod = "10"
        ie.istasyon.save()
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), url)
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), uret)            # fason emrinde "Üretildi" yok
        self.assertContains(self.client.get(reverse("core:istasyon_emirleri")), url)

    def test_revizyon_tarihi_islem_ani(self):
        emir = self.emir_ac(self.siparis("3"))                                                     # ÜS tarihi geçmiş (GUN); revizyon işlem anı
        rev = emir.revizyonlar.get()
        self.assertEqual((emir.tarih, rev.tarih), (kt.GUN, timezone.localdate()))
        d = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(d, "İşlem Zamanı")
        self.assertContains(d, timezone.localtime(rev.created_at).strftime("%d.%m.%Y %H:%M"))
