"""İş İstasyonu: fason bayrağı + varsayılan fasoncu + sıra (üretim sadeleştirme adım 1). Sabit "10" istasyon kodu kaldırıldı — fason davranışı
(Üretildi yasağı, Fason dönüş aç düğmesi, kesim listesi) yalnız ``IsIstasyonu.fason`` bayrağına bakar; liste ekranı Sıra/Fason/Operasyon sayısı/
Açık emir sütunlarını gösterir; 0205 veri migration'ı kod 10'u fason + fasoncu 320-30-0040 yapar ve kod sırasıyla sıra verir."""
import importlib
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, Depo, EkranYetki, IsIstasyonu, Operasyon, OperasyonKaydi
from core.services import fason as fason_servis
from core.services import uretim
from core.services.uretim import UretimHatasi, aktif_istasyonlar, istasyon_guncelle, istasyon_listesi, istasyon_olustur
from core.tests import test_istasyon_emri_uret as tiu
from core.tests import test_uretim as tu

D = Decimal


class IstasyonServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fasoncu = Cari.objects.create(kod="FSN-1", unvan="FASONCU TEST")

    def test_olustur_fason_fasoncu_sira(self):
        i = istasyon_olustur(kod="10", ad="boru lazer", fason=True, fasoncu_id=self.fasoncu.pk, sira=5)
        self.assertEqual((i.kod, i.ad, i.fason, i.fasoncu_id, i.sira), ("10", "BORU LAZER", True, self.fasoncu.pk, 5))
        j = istasyon_olustur(kod="70", ad="montaj")
        self.assertEqual((j.fason, j.fasoncu_id, j.sira), (False, None, 0))

    def test_fasoncu_yalniz_fason_istasyonunda(self):
        with self.assertRaisesMessage(UretimHatasi, "yalnız fason istasyonunda"):
            istasyon_olustur(kod="70", ad="montaj", fason=False, fasoncu_id=self.fasoncu.pk)
        with self.assertRaisesMessage(UretimHatasi, "Fasoncu (cari) bulunamadı"):
            istasyon_olustur(kod="10", ad="lazer", fason=True, fasoncu_id=99999)
        with self.assertRaisesMessage(UretimHatasi, "negatif"):
            istasyon_olustur(kod="10", ad="lazer", sira=-1)

    def test_guncelle_verilmeyen_alanlar_korunur(self):
        i = istasyon_olustur(kod="10", ad="lazer", fason=True, fasoncu_id=self.fasoncu.pk, sira=30)
        istasyon_guncelle(i, kod="10", ad="boru lazer")                                   # eski çağıran: yalnız kod + ad
        i.refresh_from_db()
        self.assertEqual((i.ad, i.fason, i.fasoncu_id, i.sira), ("BORU LAZER", True, self.fasoncu.pk, 30))
        istasyon_guncelle(i, kod="10", ad="boru lazer", fason=False, fasoncu_id=None, sira=1)
        i.refresh_from_db()
        self.assertEqual((i.fason, i.fasoncu_id, i.sira), (False, None, 1))

    def test_siralama_sira_sonra_kod(self):
        istasyon_olustur(kod="70", ad="montaj", sira=10)
        istasyon_olustur(kod="10", ad="lazer", sira=20)
        istasyon_olustur(kod="05", ad="depo", sira=20)
        self.assertEqual([i.kod for i in aktif_istasyonlar()], ["70", "05", "10"])
        self.assertEqual([i.kod for i in IsIstasyonu.objects.all()], ["70", "05", "10"])   # Meta.ordering

    def test_liste_sayilari(self):
        lazer = istasyon_olustur(kod="10", ad="lazer", fason=True)
        birim, kat = tu._birim(), tu._kategori()
        c1 = tu._stok(kat, birim, kod="LS-C1", ad="c1")
        c2 = tu._stok(kat, birim, kod="LS-C2", ad="c2")
        g = tu._stok(kat, birim, kod="LS-G", ad="g", satinalma=True)
        uretim.operasyon_olustur(istasyon_id=lazer.pk, cikti_id=c1.pk, cikti_miktar=D("1"), satirlar=[(g, D("1"))])
        op2 = uretim.operasyon_olustur(istasyon_id=lazer.pk, cikti_id=c2.pk, cikti_miktar=D("1"), satirlar=[(g, D("1"))])
        uretim.operasyon_sil(op2)
        satir = istasyon_listesi()[0]
        self.assertEqual((satir["istasyon"].pk, satir["operasyon_sayisi"], satir["acik_emir"]), (lazer.pk, 1, 0))


class FasonBayragiDavranisTest(tiu.UretTaban):
    """Fason davranışı KOD'a değil BAYRAĞA bağlı: kod "10" ama fason=False → normal istasyon; kod "KPN" ama fason=True → fason istasyonu."""

    def test_kod_10_ama_fason_degil_normal_uretilir(self):
        self.kur("3")
        self.ie.istasyon.kod = "10"
        self.ie.istasyon.save()
        d = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertContains(d, f'action="{self.url}"')                                                 # Üretildi var
        self.assertNotContains(d, reverse("core:fason_donus_ekle") + f"?istasyon_emri={self.ie.pk}")  # Fason dönüş aç yok
        kayit, _ = uretim.istasyon_emri_uret(self.ie, miktar=D("1"))
        self.assertEqual(kayit.durum, OperasyonKaydi.Durum.ONAYLI)

    def test_fason_bayrakli_istasyonda_uretildi_yok(self):
        self.kur("3")
        self.ie.istasyon.fason = True
        self.ie.istasyon.save()
        for url in (reverse("core:uretim_emri_detay", args=[self.emir.pk]), reverse("core:istasyon_emirleri")):
            d = self.client.get(url)
            self.assertNotContains(d, f'action="{self.url}"')
            self.assertContains(d, reverse("core:fason_donus_ekle") + f"?istasyon_emri={self.ie.pk}")
        with self.assertRaisesMessage(UretimHatasi, "fason dönüş"):
            uretim.istasyon_emri_uret(self.ie)
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.ie).count(), 0)

    def test_fason_donus_formunda_varsayilan_fasoncu_secili(self):
        self.kur("3")
        fasoncu = Cari.objects.create(kod="FSN-2", unvan="FASONCU VARSAYILAN")
        Depo.objects.create(kod="FSD", ad="FASON DEPO", fason_cari=fasoncu)
        self.ie.istasyon.fason = True
        self.ie.istasyon.fasoncu = fasoncu
        self.ie.istasyon.save()
        r = self.client.get(reverse("core:fason_donus_ekle") + f"?istasyon_emri={self.ie.pk}")
        self.assertContains(r, f'<option value="{fasoncu.pk}" selected>')
        r2 = self.client.get(reverse("core:fason_donus_ekle"))
        self.assertNotContains(r2, f'<option value="{fasoncu.pk}" selected>')

    def test_kesim_listesi_fason_bayragina_bakar(self):
        self.kur("3")
        sonuc = fason_servis.fason_listesi_operasyondan([(self.stok, 2)])
        self.assertEqual(sonuc["ozet"], [])                                                            # fason değil → listede yok
        self.ie.istasyon.fason = True
        self.ie.istasyon.save()
        sonuc = fason_servis.fason_listesi_operasyondan([(self.stok, 2)])
        self.assertEqual([(s["kesilmis_parca"].pk, s["toplam_adet"]) for s in sonuc["ozet"]], [(self.stok.pk, D("2"))])

    def test_istasyon_listesinde_acik_emir_sayisi(self):
        self.kur("3")
        ist = self.ie.istasyon
        EkranYetki.objects.create(kullanici=self.yon, ekran_kod="is_istasyonlari")
        r = self.client.get(reverse("core:is_istasyonlari"))
        satir = next(s for s in istasyon_listesi() if s["istasyon"].pk == ist.pk)
        self.assertEqual((satir["operasyon_sayisi"], satir["acik_emir"]), (1, 1))
        self.assertContains(r, reverse("core:istasyon_emirleri") + f"?istasyon={ist.pk}")
        self.uret()                                                                                    # emir bitti → açık emir 0
        satir = next(s for s in istasyon_listesi() if s["istasyon"].pk == ist.pk)
        self.assertEqual(satir["acik_emir"], 0)

    def test_acik_emirli_fason_istasyonunun_bayragi_kaldirilamaz(self):
        self.kur("3")
        ist = self.ie.istasyon
        ist.fason = True
        ist.save()
        with self.assertRaisesMessage(UretimHatasi, "açık istasyon emirleri var"):
            istasyon_guncelle(ist, kod=ist.kod, ad=ist.ad, fason=False)
        istasyon_guncelle(ist, kod=ist.kod, ad=ist.ad, fason=True, sira=7)                             # bayrak kalıyorsa düzenleme serbest
        self.assertEqual(IsIstasyonu.objects.get(pk=ist.pk).sira, 7)


class IstasyonEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("istyon", password="x")
        cls.fasoncu = Cari.objects.create(kod="FSN-3", unvan="FASONCU EKRAN")

    def setUp(self):
        self.client.force_login(self.yon)

    def test_liste_sutunlari(self):
        istasyon_olustur(kod="10", ad="BORU LAZER", fason=True, fasoncu_id=self.fasoncu.pk, sira=10)
        istasyon_olustur(kod="70", ad="MONTAJ", sira=20)
        r = self.client.get(reverse("core:is_istasyonlari"))
        for s in ("<th class=\"sag\">Sıra</th>", "<th>Fason</th>", "Operasyon sayısı", "Açık emir", "FASONCU EKRAN", ">Fason<"):
            self.assertContains(r, s)
        govde = r.content.decode()
        self.assertLess(govde.index("BORU LAZER"), govde.index("MONTAJ"))                             # sıra 10 önce

    def test_form_ekle_ve_duzenle(self):
        r = self.client.post(reverse("core:is_istasyonu_ekle"), {"kod": "10", "ad": "boru lazer", "sira": "10", "fason": "on", "fasoncu": self.fasoncu.pk},
                             follow=True)
        self.assertContains(r, "İş istasyonu eklendi")
        i = IsIstasyonu.objects.get(kod="10")
        self.assertEqual((i.fason, i.fasoncu_id, i.sira), (True, self.fasoncu.pk, 10))
        r = self.client.get(reverse("core:is_istasyonu_duzenle", args=[i.pk]))
        self.assertContains(r, 'name="fason"')
        self.assertContains(r, "checked")
        self.assertContains(r, f'<option value="{self.fasoncu.pk}" selected>')
        r = self.client.post(reverse("core:is_istasyonu_duzenle", args=[i.pk]), {"kod": "10", "ad": "boru lazer", "sira": "15"}, follow=True)
        i.refresh_from_db()
        self.assertEqual((i.fason, i.fasoncu_id, i.sira), (False, None, 15))                           # kutu işaretsiz → fason kalkar
        r = self.client.post(reverse("core:is_istasyonu_ekle"), {"kod": "70", "ad": "montaj", "sira": "0", "fasoncu": self.fasoncu.pk}, follow=True)
        self.assertContains(r, "yalnız fason istasyonunda")
        self.assertFalse(IsIstasyonu.objects.filter(kod="70").exists())


class VeriMigrationTest(TestCase):
    """0205: kod 10 → fason + fasoncu 320-30-0040 (varsa), diğerleri değişmez; kod sırasıyla sıra 10, 20, 30…"""

    def test_ileri(self):
        mod = importlib.import_module("core.migrations.0205_is_istasyonu_fason_veri")
        gurbuz = Cari.objects.create(kod="320-30-0040", unvan="FASON GÜRBÜZ LAZER")
        a = IsIstasyonu.objects.create(kod="70", ad="MONTAJ")
        b = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        c = IsIstasyonu.objects.create(kod="30", ad="BÜKÜM")
        s = IsIstasyonu.objects.create(kod="05", ad="SİLİNMİŞ", silindi=True)
        mod.ileri(apps, None)
        a.refresh_from_db(); b.refresh_from_db(); c.refresh_from_db(); s.refresh_from_db()
        self.assertEqual((b.fason, b.fasoncu_id, b.sira), (True, gurbuz.pk, 10))
        self.assertEqual((c.fason, c.fasoncu_id, c.sira), (False, None, 20))
        self.assertEqual((a.fason, a.fasoncu_id, a.sira), (False, None, 30))
        self.assertEqual((s.fason, s.sira), (False, 0))                                                 # silinmişe dokunulmaz
        mod.geri(apps, None)
        b.refresh_from_db()
        self.assertEqual((b.fason, b.fasoncu_id, b.sira), (False, None, 0))

    def test_fasoncu_yoksa_bos_kalir(self):
        mod = importlib.import_module("core.migrations.0205_is_istasyonu_fason_veri")
        b = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        mod.ileri(apps, None)
        b.refresh_from_db()
        self.assertEqual((b.fason, b.fasoncu_id), (True, None))
