"""ÜS detayı: istasyon bazında katlanır gruplar + "Bu istasyonu üret" / "Tümünü üret" (üretim sadeleştirme adım 3). Toplu üretim istasyon sırasıyla
(``IsIstasyonu.sira``), tek tarihle, fason emirleri atlayarak, TEK ATOMİK işlem: ilk hatada hiçbir şey yazılmaz ve hata hangi emir/stokta durduğunu söyler
(adım 2 tarih bazlı kontrol). Operasyon Kayıtları bölümü sayaç + "Kayıtları gör" bağlantısı."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import FaturaTipi, HesapPlani, IstasyonEmri, KategoriHesap, OperasyonKaydi, StokHareket, UretimEmri
from core.services import uretim
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import TopluUretimHatasi, UretimHatasi, operasyon_olustur, uretim_emri_olustur, uretim_emri_toplu_uret
from core.tests.test_uretim import _birim, _depo, _istasyon, _kategori, _stok

D = Decimal
GUN = date(2026, 3, 10)


class TopluTaban(TestCase):
    """profil → (LAZER, sıra 10: 1 profil → 2 kesilmiş) → (MONTAJ, sıra 20: 1 kesilmiş + 1 vida → 1 mamul); ÜS 4 mamul."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("topluyon", password="x")
        cls.birim, cls.kat = _birim(), _kategori()
        hesap = HesapPlani.objects.create(hesap_kodu="150.10", hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
        KategoriHesap.objects.create(kategori=cls.kat, hesap=hesap, fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ TU", yon="ALIS"))
        cls.depo = _depo("TUDEPO")
        cls.montaj = _istasyon("MONTAJ")
        cls.lazer = _istasyon("LAZER")
        cls.lazer.sira, cls.montaj.sira = 10, 20
        cls.lazer.save()
        cls.montaj.save()
        cls.profil = _stok(cls.kat, cls.birim, kod="TU-PROFIL", ad="ham profil", satinalma=True)
        cls.vida = _stok(cls.kat, cls.birim, kod="TU-VIDA", ad="vida", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="TU-KESILMIS", ad="kesilmiş parça")
        cls.mamul = _stok(cls.kat, cls.birim, kod="TU-MAMUL", ad="mamul", satis=True)
        cls.kesim_op = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=D("2"), satirlar=[(cls.profil, D("1"))])
        cls.montaj_op = operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"),
                                          satirlar=[(cls.kesilmis, D("1")), (cls.vida, D("1"))])

    def setUp(self):
        self.client.force_login(self.yon)

    def gir(self, stok, miktar, tarih=date(2026, 1, 1)):
        hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih, tur=StokHareket.Tur.GIRIS, miktar=D(miktar), giris_tutar_try=D(miktar) * 10)

    def emir_ac(self, miktar="4"):
        return uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": miktar}], depo_id=self.depo.pk, tarih=GUN, kullanici=self.yon)

    def ie(self, emir, istasyon):
        return emir.istasyon_emirleri.get(istasyon=istasyon)

    def sayilar(self):
        return OperasyonKaydi.objects.count(), StokHareket.objects.count()


class TopluUretServisTest(TopluTaban):
    def test_tumunu_uret_istasyon_sirasiyla_tek_tarih(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        yapilan = uretim_emri_toplu_uret(emir, tarih=GUN, kullanici=self.yon)
        self.assertEqual([(ie.istasyon.kod, k.durum, k.tarih) for ie, k in yapilan], [("LAZER", "ONAYLI", GUN), ("MONTAJ", "ONAYLI", GUN)])
        self.assertEqual([ie.durum for ie in emir.istasyon_emirleri.order_by("istasyon__sira")], ["BITTI", "BITTI"])
        self.assertEqual((eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis), eldeki_miktar(self.mamul), eldeki_miktar(self.vida)),
                         (D("98"), D("0"), D("4"), D("96")))
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)                                               # stok için üretim %100 → tamamlandı

    def test_sira_istasyon_sira_alanina_gore(self):
        self.lazer.sira, self.montaj.sira = 20, 10                                                           # yanlış sıra: montaj önce → kesilmiş yok
        self.lazer.save()
        self.montaj.save()
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        with self.assertRaises(TopluUretimHatasi) as c:
            uretim_emri_toplu_uret(emir, tarih=GUN)
        self.assertIn("MONTAJ", str(c.exception))
        self.assertIn("Bu tarihte yetersiz: TU-KESILMIS", str(c.exception))
        self.assertEqual(c.exception.istasyon_emri, self.ie(emir, self.montaj))

    def test_atomik_ikinci_emirde_hata_hicbir_sey_yazilmaz(self):
        self.gir(self.profil, "100")                                                                          # vida yok → MONTAJ düşer
        emir = self.emir_ac()
        once = self.sayilar()
        with self.assertRaises(TopluUretimHatasi) as c:
            uretim_emri_toplu_uret(emir, tarih=GUN, kullanici=self.yon)
        e = c.exception
        self.assertEqual((e.istasyon_emri, e.tamamlanan), (self.ie(emir, self.montaj), 1))
        self.assertIn(f"{e.istasyon_emri.no} · MONTAJ", str(e))
        self.assertIn("Bu tarihte yetersiz: TU-VIDA (eldeki 0, gerekli 4)", str(e))
        self.assertIn("1 emir üretilmişti; hepsi geri alındı", str(e))
        self.assertEqual(self.sayilar(), once)                                                              # LAZER kaydı da yok
        self.assertEqual((eldeki_miktar(self.profil), eldeki_miktar(self.kesilmis)), (D("100"), D("0")))
        self.assertEqual([ie.durum for ie in emir.istasyon_emirleri.all()], ["BEKLIYOR", "BEKLIYOR"])
        self.assertEqual(IstasyonEmri.objects.get(pk=self.ie(emir, self.lazer).pk).tamamlanan, D("0"))

    def test_tek_istasyon(self):
        self.gir(self.profil, "100")
        emir = self.emir_ac()
        yapilan = uretim_emri_toplu_uret(emir, istasyon=self.lazer, tarih=GUN)
        self.assertEqual([ie.istasyon.kod for ie, _ in yapilan], ["LAZER"])
        self.assertEqual((self.ie(emir, self.lazer).durum, self.ie(emir, self.montaj).durum), ("BITTI", "BEKLIYOR"))
        self.assertEqual(eldeki_miktar(self.kesilmis), D("4"))
        with self.assertRaisesMessage(UretimHatasi, "Üretilecek açık istasyon emri yok"):
            uretim_emri_toplu_uret(emir, istasyon=self.lazer, tarih=GUN)                                     # LAZER bitti

    def test_fason_emri_atlanir(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        self.lazer.fason = True
        self.lazer.save()
        with self.assertRaises(TopluUretimHatasi) as c:                                                      # yalnız MONTAJ denenir, kesilmiş yok
            uretim_emri_toplu_uret(emir, tarih=GUN)
        self.assertIn("MONTAJ", str(c.exception))
        self.assertNotIn("LAZER", str(c.exception))
        self.assertEqual(self.ie(emir, self.lazer).durum, "BEKLIYOR")
        self.montaj.fason = True
        self.montaj.save()
        with self.assertRaisesMessage(UretimHatasi, "fason emirleri fason dönüş"):
            uretim_emri_toplu_uret(emir, tarih=GUN)

    def test_gecmis_tarih_stok_kontrolu(self):
        self.gir(self.profil, "100", tarih=date(2026, 3, 12))
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        with self.assertRaises(TopluUretimHatasi) as c:
            uretim_emri_toplu_uret(emir, tarih=date(2026, 3, 11))                                           # profil 12'sinde geldi
        self.assertIn("Bu tarihte yetersiz: TU-PROFIL (eldeki 0, gerekli 2) — TUDEPO, 11.03.2026", str(c.exception))
        self.assertEqual(len(uretim_emri_toplu_uret(emir, tarih=date(2026, 3, 12))), 2)

    def test_kapali_us_reddedilir(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        uretim.uretim_emri_iptal(emir)
        with self.assertRaisesMessage(UretimHatasi, "Yalnız açık"):
            uretim_emri_toplu_uret(emir, tarih=GUN)

    def test_gruplar(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        g = uretim.istasyon_emri_gruplari(emir)
        self.assertEqual([(x["istasyon"].kod, x["toplam"], x["bitti"], x["acik_adet"], x["uretilebilir"], x["tamam"]) for x in g],
                         [("LAZER", 1, 0, 1, True, False), ("MONTAJ", 1, 0, 1, True, False)])
        uretim_emri_toplu_uret(emir, istasyon=self.lazer, tarih=GUN)
        g = uretim.istasyon_emri_gruplari(emir)
        self.assertEqual([(x["istasyon"].kod, x["bitti"], x["uretilebilir"], x["tamam"]) for x in g], [("LAZER", 1, False, True), ("MONTAJ", 0, True, False)])


class TopluUretEkranTest(TopluTaban):
    def test_detay_gruplar_ve_dugmeler(self):
        self.gir(self.profil, "100")
        self.gir(self.vida, "100")
        emir = self.emir_ac()
        url = reverse("core:uretim_emri_toplu_uret", args=[emir.pk])
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, "▶▶ Tümünü üret")
        self.assertContains(r, "▶ Bu istasyonu üret", count=2)
        self.assertContains(r, f'action="{url}"', count=3)
        self.assertContains(r, '<details class="ie-grup" open>', count=2)
        self.assertContains(r, "LAZER İSTASYONU")
        self.assertContains(r, "Kayıtları gör")
        self.assertContains(r, f"?uretim_emri={emir.pk}")
        self.assertNotContains(r, "<th>İş İstasyonu</th>")                                                   # grup başlığında; sütun yok
        govde = r.content.decode()
        self.assertLess(govde.index("LAZER İSTASYONU"), govde.index("MONTAJ İSTASYONU"))                     # istasyon sırası
        r = self.client.post(url, {"istasyon": self.lazer.pk, "tarih": "2026-03-10"}, follow=True)
        self.assertContains(r, "1 istasyon emri üretildi (LAZER LAZER İSTASYONU)")
        self.assertContains(r, '<details class="ie-grup">', count=1)                                         # tamamlanan grup kapalı
        self.assertContains(r, '<details class="ie-grup" open>', count=1)
        self.assertContains(r, "▶ Bu istasyonu üret", count=1)
        self.assertContains(r, "1 onaylı")
        r = self.client.post(url, {"tarih": "2026-03-10"}, follow=True)
        self.assertContains(r, "1 istasyon emri üretildi (tüm istasyonlar)")
        self.assertNotContains(r, "▶▶ Tümünü üret")
        self.assertContains(r, "2 onaylı")
        self.assertContains(r, '<details class="ie-grup">', count=2)

    def test_hata_mesaji_ve_satirda_gosterim(self):
        self.gir(self.profil, "100")                                                                          # vida yok
        emir = self.emir_ac()
        url = reverse("core:uretim_emri_toplu_uret", args=[emir.pk])
        once = self.sayilar()
        r = self.client.post(url, {"tarih": "2026-03-10"}, follow=True)
        ie = self.ie(emir, self.montaj)
        self.assertContains(r, "Toplu üretim yapılmadı")
        self.assertContains(r, f"{ie.no} · MONTAJ")
        self.assertContains(r, "Bu tarihte yetersiz: TU-VIDA")
        self.assertContains(r, 'class="ie-hata"')
        self.assertEqual(self.sayilar(), once)
        r = self.client.post(url, {"tarih": "yanlış"}, follow=True)
        self.assertContains(r, "Tarih geçerli değil")
        self.assertEqual(self.client.get(url).status_code, 302)                                              # GET → detaya

    def test_yetkisiz_kullanici(self):
        self.gir(self.profil, "100")
        emir = self.emir_ac()
        bos = User.objects.create_user("toplubos", password="x")
        self.client.force_login(bos)
        self.assertEqual(self.client.post(reverse("core:uretim_emri_toplu_uret", args=[emir.pk]), {"tarih": "2026-03-10"}).status_code, 403)
