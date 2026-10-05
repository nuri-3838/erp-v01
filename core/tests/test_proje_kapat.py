"""Yatırım projesi 'Kapat (satıldı/diğer)': yalnız 258 bakiyesi 0,00 ise; fiş üretmez, durum 'Kapandı'; kapanmış projeye yeni satır bağlanamaz;
'Yeniden aç' yalnız süper kullanıcı; 'Aktifleştir' akışı değişmez."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.forms import KrediKartiHareketForm
from core.models import EkranYetki, HesapPlani, Kur, YatirimProjesi, YevmiyeFisi
from core.services import cari_kesinti as ck
from core.services import hesap_plani as hp
from core.services import kredi_karti_hareket as kk
from core.services import yatirim_projesi as yp
from core.services.finans import kredi_karti_olustur
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class ProjeKapatBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 1, 19), D(2026, 1, 10), D(2026, 4, 3)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        _hesap("102.01.0001", "BANKA")
        _hesap("309.02", "KART", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        hp.hesap_olustur(kod="258.04", ad="ARSA YATIRIMLARI", ust_kodu="258")
        cls.su = User.objects.create_superuser("pks", password="x")
        cls.sade = User.objects.create_user("pkd", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="yatirim_projeleri")
        cls.proje = yp.proje_olustur(ad="mimarsinan arsa", grup_kodu="258.04", kullanici=cls.su)
        cls.kart = kredi_karti_olustur(ad="kart", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)

    def alis(self, tutar="100000", proje=None):
        return fis_olustur(tarih=D(2026, 1, 10), aciklama="ARSA ALIŞ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu="258", taraf="B", islem_tutari=tutar, yatirim_projesi_id=(proje or self.proje).pk),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari=tutar)])

    def satis(self, tutar="100000", proje=None):
        """Projenin 258 hesabından çıkış (satış/aktarım): 258 ALACAK."""
        return fis_olustur(tarih=D(2026, 1, 19), aciklama="ARSA SATIŞ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu="102.01.0001", taraf="B", islem_tutari=tutar),
            SatirGirdi(hesap_kodu="258", taraf="A", islem_tutari=tutar, yatirim_projesi_id=(proje or self.proje).pk)])


class ProjeKapatServisTest(ProjeKapatBase):
    def test_bakiye_sifir_kapanir_fis_uretmez(self):
        self.alis()
        self.satis()
        self.assertEqual(yp.proje_bakiye_258(self.proje), Dc("0.00"))
        n = YevmiyeFisi.objects.count()
        yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", aciklama="Bahçelievler 405 ada 1 parsel", kullanici=self.su)
        self.proje.refresh_from_db()
        self.assertEqual((self.proje.durum, self.proje.kapanis_tarihi, self.proje.kapanis_nedeni, self.proje.kapanis_aciklama),
                         ("KAPANDI", D(2026, 1, 19), "SATILDI", "Bahçelievler 405 ada 1 parsel"))
        self.assertEqual(YevmiyeFisi.objects.count(), n)                                   # fiş üretmedi
        self.assertFalse(self.proje.silindi)

    def test_bakiye_sifir_degilse_reddedilir_ve_bakiye_gosterilir(self):
        self.alis("100000")
        self.satis("40000")
        with self.assertRaises(yp.YatirimProjesiHatasi) as cm:
            yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", kullanici=self.su)
        self.assertIn("60.000,00", str(cm.exception))
        self.proje.refresh_from_db()
        self.assertEqual((self.proje.durum, self.proje.kapanis_tarihi), ("DEVAM", None))

    def test_gecersiz_girdiler(self):
        for kw in (dict(tarih=None, neden="SATILDI"), dict(tarih=D(2026, 1, 19), neden="BELKI"),
                   dict(tarih=D(2026, 1, 19), neden="DIGER", aciklama="  ")):
            with self.assertRaises(yp.YatirimProjesiHatasi):
                yp.proje_kapat(self.proje, kullanici=self.su, **kw)
        yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="DIGER", aciklama="başka hesaba aktarıldı", kullanici=self.su)
        with self.assertRaises(yp.YatirimProjesiHatasi):                                   # zaten kapalı
            yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", kullanici=self.su)

    def test_kapanmis_projeye_yeni_satir_baglanamaz(self):
        self.alis()
        self.satis()
        yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", kullanici=self.su)
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(YevmiyeHatasi):                                             # manuel fiş
            self.alis("10")
        h258 = HesapPlani.objects.get(hesap_kodu="258")
        with self.assertRaises(kk.KrediKartiHareketHatasi):                                # kredi kartı harcaması
            kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=h258, tutar="100", tarih=D(2026, 4, 3), kullanici=self.su,
                               yatirim_projesi_id=self.proje.pk)
        with self.assertRaises(ck.CariKesintiHatasi):                                      # kesinti / virman hesap tarafı
            ck.proje_coz(h258, self.proje.pk)
        self.assertEqual(YevmiyeFisi.objects.count(), n)
        # seçim listesinde görünmez
        f = KrediKartiHareketForm(tip="harcama", kart=self.kart)
        self.assertNotIn(self.proje, f.fields["yatirim_projesi"].queryset)

    def test_aktiflestir_akisi_kapali_projede_calismaz_acik_projede_degismez(self):
        self.alis()
        self.satis()
        yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", kullanici=self.su)
        with self.assertRaises(yp.YatirimProjesiHatasi):
            yp.proje_aktiflestir(self.proje, tarih=D(2026, 2, 1), satirlar=[], kullanici=self.su)

    def test_yeniden_ac_yalniz_super_kullanici(self):
        self.alis()
        self.satis()
        yp.proje_kapat(self.proje, tarih=D(2026, 1, 19), neden="SATILDI", aciklama="x", kullanici=self.su)
        with self.assertRaises(yp.YatirimProjesiHatasi):
            yp.proje_yeniden_ac(self.proje, kullanici=self.sade)
        self.proje.refresh_from_db()
        self.assertEqual(self.proje.durum, "KAPANDI")
        yp.proje_yeniden_ac(self.proje, kullanici=self.su)
        self.proje.refresh_from_db()
        self.assertEqual((self.proje.durum, self.proje.kapanis_tarihi, self.proje.kapanis_nedeni, self.proje.kapanis_aciklama),
                         ("DEVAM", None, "", ""))
        self.alis("5")                                                                     # tekrar satır bağlanabilir
        with self.assertRaises(yp.YatirimProjesiHatasi):                                   # açık proje yeniden açılamaz
            yp.proje_yeniden_ac(self.proje, kullanici=self.su)

    def test_iptal_durumu_yok(self):
        self.assertEqual({d.value for d in YatirimProjesi.Durum}, {"DEVAM", "AKTIFLESTI", "KAPANDI"})


class ProjeKapatEkranTest(ProjeKapatBase):
    def test_ekranlar_kapat_ve_yeniden_ac(self):
        self.alis("100000")
        self.satis("40000")
        self.client.force_login(self.su)
        kapat = reverse("core:yatirim_projesi_kapat", args=[self.proje.pk])
        r = self.client.get(kapat)                                                         # bakiye 0 değil: bakiye görünür, form yok
        self.assertContains(r, "60.000,00")
        self.assertContains(r, "kapatılamaz")
        self.assertNotContains(r, "Projeyi Kapat</button>")
        r = self.client.post(kapat, {"kapanis_tarihi": "2026-01-19", "kapanis_nedeni": "SATILDI", "kapanis_aciklama": ""})
        self.assertEqual(r.status_code, 200)                                               # servis reddeder
        self.assertContains(r, "kapatılamaz")
        self.satis("60000")                                                                # bakiye 0
        self.assertContains(self.client.get(reverse("core:yatirim_projesi_detay", args=[self.proje.pk])), "Kapat")
        r = self.client.get(kapat)
        self.assertContains(r, "Projeyi Kapat</button>")
        r = self.client.post(kapat, {"kapanis_tarihi": "2026-01-19", "kapanis_nedeni": "SATILDI",
                                     "kapanis_aciklama": "Bahçelievler 405 ada 1 parsel"})
        self.assertEqual(r.status_code, 302)
        self.proje.refresh_from_db()
        self.assertEqual(self.proje.durum, "KAPANDI")
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[self.proje.pk]))
        self.assertContains(r, "Kapandı")
        self.assertContains(r, "Bahçelievler 405 ada 1 parsel")
        self.assertContains(r, "Yeniden Aç")
        self.assertContains(self.client.get(reverse("core:yatirim_projeleri")), "rozet-kapali")
        # kapalı projede aktifleştir ve tekrar kapat yönlendirir
        self.assertEqual(self.client.get(reverse("core:yatirim_projesi_aktiflestir", args=[self.proje.pk])).status_code, 302)
        self.assertEqual(self.client.get(kapat).status_code, 302)
        # yeniden aç: ekran yetkili ama süper kullanıcı değil → reddedilir
        ac = reverse("core:yatirim_projesi_yeniden_ac", args=[self.proje.pk])
        self.client.force_login(self.sade)
        self.assertNotContains(self.client.get(reverse("core:yatirim_projesi_detay", args=[self.proje.pk])), "Yeniden Aç")
        self.client.post(ac)
        self.proje.refresh_from_db()
        self.assertEqual(self.proje.durum, "KAPANDI")
        self.client.force_login(self.su)
        self.assertEqual(self.client.post(ac).status_code, 302)
        self.proje.refresh_from_db()
        self.assertEqual(self.proje.durum, "DEVAM")
        self.assertEqual(self.client.get(ac).status_code, 302)                            # GET durumu değiştirmez
