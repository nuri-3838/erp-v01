"""İstasyon Emirleri ekranı (üretim sadeleştirme adım 7): istasyon sekmeleri (açık emir sayısıyla) + Tümü; süzgeç ÜS no + durum (varsayılan açık);
satır başına tek işlem (Üretildi / Fason dönüş aç / Tamamlandı); seçili istasyonda "Bu istasyondaki açık emirlerin tümünü üret" (tüm açık ÜS'ler,
tek atomik işlem; ÜS sırası)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import IstasyonEmri, OperasyonKaydi, StokHareket
from core.services import uretim
from core.services.hareket import eldeki_miktar
from core.services.uretim import TopluUretimHatasi, UretimHatasi
from core.tests.test_uretim_toplu_uret import GUN, TopluTaban

D = Decimal


class IstasyonSekmeEkranTest(TopluTaban):
    def setUp(self):
        super().setUp()
        self.url = reverse("core:istasyon_emirleri")

    def test_sekmeler_sayilar_ve_tumu(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        e1, e2 = self.emir_ac("4"), self.emir_ac("2")
        r = self.client.get(self.url)
        self.assertEqual([(s["ad"], s["sayi"], s["aktif"]) for s in r.context["sekmeler"]],
                         [("Tümü", 4, True), ("LAZER LAZER İSTASYONU", 2, False), ("MONTAJ MONTAJ İSTASYONU", 2, False)])
        self.assertContains(r, "<th>İş İstasyonu</th>")                                                  # Tümü'de istasyon sütunu
        self.assertContains(r, 'name="us"')
        self.assertNotContains(r, 'name="ara"')
        self.assertNotContains(r, "tümünü üret")                                                        # Tümü sekmesinde toplu düğme yok
        r = self.client.get(self.url, {"istasyon": self.lazer.pk})
        self.assertEqual([ie.istasyon_id for ie in r.context["sayfa"]], [self.lazer.pk, self.lazer.pk])
        self.assertNotContains(r, "<th>İş İstasyonu</th>")
        self.assertContains(r, "▶▶ Bu istasyondaki açık emirlerin tümünü üret")
        self.assertContains(r, f'action="{reverse("core:istasyon_toplu_uret", args=[self.lazer.pk])}"')
        self.assertContains(r, 'class="ie-sekme aktif"')
        uretim.uretim_emri_toplu_uret(e1, istasyon=self.lazer, tarih=GUN)                               # 1 LAZER emri bitti
        r = self.client.get(self.url, {"istasyon": self.lazer.pk})
        self.assertEqual([(s["ad"], s["sayi"]) for s in r.context["sekmeler"]][1], ("LAZER LAZER İSTASYONU", 1))
        self.assertEqual(len(r.context["sayfa"]), 1)                                                     # varsayılan: açık
        r = self.client.get(self.url, {"istasyon": self.lazer.pk, "durum": "BITTI"})
        self.assertEqual(len(r.context["sayfa"]), 1)
        self.assertContains(r, "Tamamlandı")
        self.assertNotContains(r, "tümünü üret")                                                        # yalnız açık süzgecinde

    def test_us_no_suzgeci_ve_tek_islem(self):
        self.gir(self.profil, "100")
        e1, e2 = self.emir_ac("4"), self.emir_ac("2")
        r = self.client.get(self.url, {"us": e2.no})
        self.assertEqual({ie.uretim_emri_id for ie in r.context["sayfa"]}, {e2.pk})
        r = self.client.get(self.url, {"ara": e1.no})                                                    # eski bağlantılar
        self.assertEqual({ie.uretim_emri_id for ie in r.context["sayfa"]}, {e1.pk})
        r = self.client.get(self.url, {"istasyon": self.lazer.pk})
        ie = e1.istasyon_emirleri.get(istasyon=self.lazer)
        self.assertContains(r, f'?istasyon_emri={ie.pk}" title="Bu emrin operasyon kayıtları">{ie.no}</a>')   # emir no → kayıtlar
        self.assertNotContains(r, ">Kayıtlar</a>")
        self.assertContains(r, ">Üretildi<", count=2)
        self.lazer.fason = True
        self.lazer.save()
        r = self.client.get(self.url, {"istasyon": self.lazer.pk})
        self.assertContains(r, ">Fason dönüş aç</a>", count=2)
        self.assertNotContains(r, ">Üretildi<")
        self.assertEqual(len(self.client.get(self.url, {"istasyon": self.lazer.pk + 999}).context["sayfa"]), 0)   # bilinmeyen istasyon → boş
        self.assertNotContains(r, "tümünü üret")                                                        # fason istasyonunda toplu düğme yok


class IstasyonTopluUretTest(TopluTaban):
    def test_servis_tum_us_lerde_us_sirasiyla(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        e1, e2 = self.emir_ac("4"), self.emir_ac("2")
        yapilan = uretim.istasyon_toplu_uret(self.lazer, tarih=GUN, kullanici=self.yon)
        self.assertEqual([(ie.uretim_emri.no, ie.istasyon.kod) for ie, _ in yapilan], [(e1.no, "LAZER"), (e2.no, "LAZER")])
        self.assertEqual([ie.durum for ie in IstasyonEmri.objects.filter(istasyon=self.lazer).order_by("pk")], ["BITTI", "BITTI"])
        self.assertEqual([ie.durum for ie in IstasyonEmri.objects.filter(istasyon=self.montaj)], ["BEKLIYOR", "BEKLIYOR"])
        self.assertEqual((eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis)), (D("97"), D("6")))
        with self.assertRaisesMessage(UretimHatasi, "üretilecek açık istasyon emri yok"):
            uretim.istasyon_toplu_uret(self.lazer, tarih=GUN)

    def test_atomik_ikinci_us_de_hata(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "4")                                                                          # MONTAJ: e1 için 4 vida var, e2 için yok
        e1, e2 = self.emir_ac("4"), self.emir_ac("2")
        uretim.istasyon_toplu_uret(self.lazer, tarih=GUN)
        once = (OperasyonKaydi.objects.count(), StokHareket.objects.count())
        with self.assertRaises(TopluUretimHatasi) as c:
            uretim.istasyon_toplu_uret(self.montaj, tarih=GUN)
        self.assertIn(f"{e2.no} · ", str(c.exception))                                                   # ÜS no da mesajda
        self.assertIn("Bu tarihte yetersiz: TU-VIDA", str(c.exception))
        self.assertIn("1 emir üretilmişti; hepsi geri alındı", str(c.exception))
        self.assertEqual((OperasyonKaydi.objects.count(), StokHareket.objects.count()), once)
        self.assertEqual(eldeki_miktar(self.mamul), D("0"))
        self.assertEqual([ie.durum for ie in IstasyonEmri.objects.filter(istasyon=self.montaj).order_by("pk")], ["BEKLIYOR", "BEKLIYOR"])

    def test_fason_reddedilir(self):
        self.gir(self.profil, "100")
        self.emir_ac("4")
        self.lazer.fason = True
        self.lazer.save()
        with self.assertRaisesMessage(UretimHatasi, "fason dönüş"):
            uretim.istasyon_toplu_uret(self.lazer, tarih=GUN)
        self.assertEqual(uretim.istasyon_toplu_uretilecek_emirler(self.lazer), [])

    def test_ekran_basari_ve_hata(self):
        self.gir(self.profil, "100")
        e1, e2 = self.emir_ac("4"), self.emir_ac("2")
        url = reverse("core:istasyon_toplu_uret", args=[self.lazer.pk])
        r = self.client.post(url, {"tarih": "2026-03-10"}, follow=True)
        self.assertEqual(r.request["PATH_INFO"], reverse("core:istasyon_emirleri"))
        self.assertContains(r, "LAZER LAZER İSTASYONU: 2 istasyon emri üretildi")
        self.assertContains(r, f"{e1.no}/")
        r = self.client.post(reverse("core:istasyon_toplu_uret", args=[self.montaj.pk]), {"tarih": "2026-03-10"}, follow=True)   # vida yok
        self.assertContains(r, "Toplu üretim yapılmadı")
        self.assertContains(r, "Bu tarihte yetersiz: TU-VIDA")
        self.assertContains(r, 'class="ie-hata"')
        self.assertEqual(self.client.get(url).status_code, 302)
        r = self.client.post(url, {"tarih": "yanlış"}, follow=True)
        self.assertContains(r, "Tarih geçerli değil")
        bos = User.objects.create_user("istbos", password="x")
        self.client.force_login(bos)
        self.assertEqual(self.client.post(url, {"tarih": "2026-03-10"}).status_code, 403)
