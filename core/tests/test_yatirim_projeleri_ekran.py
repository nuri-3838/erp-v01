"""Yatırım Projeleri liste (durum sekmeleri, filtre, özet, sıralama, hareket sayısı) ve detay (Faturalar / Diğer Hareketler / Yevmiye Kalemleri)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, EkranYetki, FaturaTipi, HesapPlani, KdvOrani, Kur, YatirimProjesi
from core.services import cari_kesinti as ck
from core.services import fatura as fs
from core.services import hesap_plani as hp
from core.services import raporlar
from core.services import yatirim_projesi as yp

D = datetime.date
Dc = Decimal


def _hesap(kod, ad, grup="BILANCO", kalem="DDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem, parasal=(grup == "BILANCO"))


class YPBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 3, 10), D(2026, 4, 10), D(2026, 5, 10), D(2026, 6, 10)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        for kod, ad in (("320.30.0024", "ÖMER ORHAN"), ("320.10.0001", "TEDARİKÇİ"), ("253", "TESİS MAKİNE")):
            _hesap(kod, ad)
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        hp.hesap_olustur(kod="258.01", ad="MAKİNE YATIRIMLARI", ust_kodu="258")
        hp.hesap_olustur(kod="258.04", ad="ARSA YATIRIMLARI", ust_kodu="258")
        cls.kdv0 = KdvOrani.objects.create(aciklama="%00", oran=Dc("0"))
        cls.gider = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.omer = Cari.objects.create(kod="S1", unvan="ÖMER ORHAN", muhasebe_kodu="320.30.0024")
        cls.ted = Cari.objects.create(kod="T1", unvan="TEDARİKÇİ", muhasebe_kodu="320.10.0001")
        cls.su = User.objects.create_superuser("ypsu", password="x")
        cls.sade = User.objects.create_user("ypsade", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="yatirim_projeleri")

    def fatura(self, proje, tutar, tarih=D(2026, 3, 10), no="F"):
        return fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.ted.pk, tarih=tarih, fatura_no=no,
                                 satirlar=[{"hesap_id": proje.hesap_id, "miktar": "1", "birim_fiyat": tutar, "kdv_id": self.kdv0.pk,
                                            "yatirim_projesi_id": proje.pk}])

    def kesinti(self, proje, tutar, tarih=D(2026, 4, 10)):
        return ck.kesinti_olustur(cari=self.omer, tarih=tarih, tutar=tutar, gider_kodu=proje.hesap_id, yatirim_projesi_id=proje.pk,
                                  aciklama="kesinti", kullanici=self.su)

    def ekle(self, ad, grup, **kw):
        return yp.proje_olustur(ad=ad, grup_kodu=grup, kullanici=self.su)


class ListeTest(YPBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a = yp.proje_olustur(ad="fabrika hattı", aciklama="cnc hattı", grup_kodu="258.01", kullanici=cls.su)       # DEVAM, hareketli
        cls.b = yp.proje_olustur(ad="arsa alımı", grup_kodu="258.04", kullanici=cls.su)                                 # DEVAM, hareketsiz
        cls.c = yp.proje_olustur(ad="tesis", grup_kodu="258.01", kullanici=cls.su)                                      # AKTIFLEŞTİ
        cls.d = yp.proje_olustur(ad="satılan arsa", grup_kodu="258.04", kullanici=cls.su)                               # KAPANDI

    def setUp(self):
        self.fatura(self.a, "10000", D(2026, 3, 10), "A1")
        self.kesinti(self.a, "2500", D(2026, 4, 10))
        self.fatura(self.c, "5000", D(2026, 3, 10), "C1")
        yp.proje_aktiflestir(self.c, tarih=D(2026, 5, 10), kullanici=self.su,
                             satirlar=[{"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk, "varlik_adi": "makine", "tutar": Dc("5000")}])
        self.fatura(self.d, "1000", D(2026, 3, 10), "D1")
        self.kesinti(self.d, "1000", D(2026, 4, 10))                                                                    # bakiye 0 → kapat
        yp.proje_kapat(self.d, tarih=D(2026, 4, 10), neden="SATILDI", kullanici=self.su)
        self.client.force_login(self.sade)

    def liste(self, **q):
        return yp.proje_listesi(bugun=D(2026, 6, 10), **q)

    def test_varsayilan_yalniz_devam_edenler(self):
        r = self.client.get(reverse("core:yatirim_projeleri"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual({k["proje"].pk for k in r.context["kayitlar"]}, {self.a.pk, self.b.pk})
        self.assertContains(r, "HATTI")
        self.assertNotContains(r, "SATILAN ARSA")

    def test_durum_sayilari_ve_sekmeler(self):
        s = self.liste()["sayilar"]
        self.assertEqual(s, {"DEVAM": 2, "AKTIFLESTI": 1, "KAPANDI": 1, "TUMU": 4})
        r = self.client.get(reverse("core:yatirim_projeleri"))
        self.assertEqual([(t["kod"], t["sayi"]) for t in r.context["sekmeler"]],
                         [("DEVAM", 2), ("AKTIFLESTI", 1), ("KAPANDI", 1), ("TUMU", 4)])
        self.assertEqual(len(self.client.get(reverse("core:yatirim_projeleri"), {"durum": "TUMU"}).context["kayitlar"]), 4)
        self.assertEqual([k["proje"].pk for k in self.client.get(reverse("core:yatirim_projeleri"), {"durum": "KAPANDI"}).context["kayitlar"]],
                         [self.d.pk])

    def test_arama_kod_ad_hesap_aciklama(self):
        def idler(**q):
            return {k["proje"].pk for k in self.liste(durum="TUMU", **q)["kayitlar"]}
        self.assertEqual(idler(arama="fabrika"), {self.a.pk})                    # küçük harf + i/İ duyarsız
        self.assertEqual(idler(arama=self.b.kod), {self.b.pk})
        self.assertEqual(idler(arama=self.b.hesap_id), {self.b.pk})
        self.assertEqual(idler(arama="CNC HATTI"), {self.a.pk})                  # açıklama
        self.assertEqual(idler(arama="yok böyle"), set())

    def test_hesap_grubu_filtresi(self):
        ids = lambda g: {k["proje"].pk for k in self.liste(durum="TUMU", grup=g)["kayitlar"]}
        self.assertEqual(ids("258.01"), {self.a.pk, self.c.pk})
        self.assertEqual(ids("258.04"), {self.b.pk, self.d.pk})

    def test_tarih_araligi_son_hareket(self):
        # a: son hareket 10.04; c: 10.03; d: 10.04; b: hareket yok
        ids = lambda **q: {k["proje"].pk for k in self.liste(durum="TUMU", **q)["kayitlar"]}
        self.assertEqual(ids(baslangic=D(2026, 4, 1)), {self.a.pk, self.d.pk})
        self.assertEqual(ids(bitis=D(2026, 3, 31)), {self.c.pk})
        self.assertEqual(ids(baslangic=D(2026, 3, 1), bitis=D(2026, 4, 30)), {self.a.pk, self.c.pk, self.d.pk})

    def test_hareket_sayisi_kesinti_ve_virmani_icerir(self):
        k = {x["proje"].pk: x for x in self.liste(durum="TUMU")["kayitlar"]}
        self.assertEqual(k[self.a.pk]["hareket"], 2)                              # 1 fatura kalemi + 1 kesinti
        self.assertEqual((k[self.a.pk]["fatura_adet"], k[self.a.pk]["diger_adet"]), (1, 1))
        self.assertEqual(k[self.b.pk]["hareket"], 0)
        self.assertEqual((k[self.a.pk]["ilk"], k[self.a.pk]["son"]), (D(2026, 3, 10), D(2026, 4, 10)))
        self.assertEqual(k[self.a.pk]["toplam"], Dc("7500.00"))                  # 10.000 − 2.500 kesinti

    def test_ozet_karti_mizan_258_ile_uyumlu_ve_uyari(self):
        o = self.liste()
        self.assertEqual(o["devam_toplam"], Dc("7500.00"))
        self.assertEqual(o["mizan_258"], raporlar._devir("258", D(2100, 1, 1))[0])
        self.assertEqual(o["mizan_258"], Dc("7500.00"))                           # yalnız a bakiyeli (c aktifleşti, d kapandı = 0)
        self.assertFalse(o["uyari"])
        self.assertEqual(o["bu_yil_aktiflesen"], Dc("5000.00"))
        # projeye bağlanmamış 258 satırı → uyarı
        from core.services.yevmiye import SatirGirdi, fis_olustur
        fis_olustur(tarih=D(2026, 5, 10), aciklama="BAĞSIZ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu=self.b.hesap_id, taraf="B", islem_tutari="100"), SatirGirdi(hesap_kodu="320.10.0001", taraf="A", islem_tutari="100")])
        o2 = self.liste()
        self.assertTrue(o2["uyari"])
        self.assertEqual(o2["fark"], Dc("-100.00"))
        self.assertContains(self.client.get(reverse("core:yatirim_projeleri")), "258 mizan bakiyesinden")

    def test_siralama_varsayilan_son_hareket_azalan_ve_kolon(self):
        sira = [k["proje"].pk for k in self.liste(durum="TUMU")["kayitlar"]]
        self.assertEqual(sira[-1], self.b.pk)                                     # hareketsiz sonda
        self.assertEqual(set(sira[:2]), {self.a.pk, self.d.pk})                   # 10.04
        t = [k["toplam"] for k in self.liste(durum="TUMU", sirala="toplam", yon="azalan")["kayitlar"]]
        self.assertEqual(t, sorted(t, reverse=True))
        kod = [k["proje"].kod for k in self.liste(durum="TUMU", sirala="kod", yon="artan")["kayitlar"]]
        self.assertEqual(kod, sorted(kod))

    def test_sonuc_kolonu_aktiflesmis_ve_kapanmis(self):
        r = self.client.get(reverse("core:yatirim_projeleri"), {"durum": "TUMU"})
        self.assertContains(r, "10.05.2026")                                      # aktifleştirme tarihi
        self.assertContains(r, "DV-0001")                                         # kart kodu (link)
        self.assertContains(r, "Satıldı")                                         # kapanış nedeni
        self.assertContains(r, reverse("core:yatirim_projesi_aktiflestir", args=[self.a.pk]))   # devam eden: kısayollar
        self.assertNotContains(r, reverse("core:yatirim_projesi_kapat", args=[self.c.pk]))

    def test_bos_sonuc_mesaji_ve_url_parametreleri(self):
        r = self.client.get(reverse("core:yatirim_projeleri"), {"q": "olmayan", "durum": "TUMU"})
        self.assertContains(r, "Bu filtreyle eşleşen proje yok.")
        self.assertContains(r, "q=olmayan")                                       # sekme/sıralama linkleri filtreyi taşır


class DetayTest(YPBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.p = yp.proje_olustur(ad="fabrika hattı", grup_kodu="258.01", kullanici=cls.su)

    def setUp(self):
        self.fatura(self.p, "10000", D(2026, 3, 10), "A1")
        self.fatura(self.p, "4000", D(2026, 3, 10), "A2")
        self.kesinti(self.p, "2500", D(2026, 4, 10))
        self.client.force_login(self.sade)

    def test_diger_hareketler_toplami_genel_toplamla_eslesir(self):
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[self.p.pk]), {"sekme": "diger"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["diger_hareketler"]), 1)
        self.assertEqual(r.context["diger_toplam"], Dc("-2500.00"))
        self.assertContains(r, "Diğer Hareketler Toplamı")
        self.assertEqual(r.context["fatura_toplam"] + r.context["diger_toplam"] + r.context["sarf_toplam"], r.context["toplam"])
        self.assertEqual(r.context["toplam"], yp.proje_toplami(self.p))
        self.assertEqual(r.context["toplam"], Dc("11500.00"))

    def test_yevmiye_kalemleri_258_ekstresiyle_birebir(self):
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[self.p.pk]), {"sekme": "yevmiye"})
        e = raporlar.ekstre(self.p.hesap_id, D(1900, 1, 1), D(2999, 12, 31))
        satirlar = r.context["yevmiye_satirlari"]
        self.assertEqual(len(satirlar), len(e.satirlar))
        self.assertEqual(len(satirlar), 3)                                        # 2 fatura + 1 kesinti
        self.assertEqual((sum(x["borc"] for x in satirlar), sum(x["alacak"] for x in satirlar)), (e.toplam_borc, e.toplam_alacak))
        self.assertEqual(satirlar[-1]["yur_bakiye"], e.bakiye)
        self.assertEqual(e.bakiye, raporlar._devir(self.p.hesap_id, D(2100, 1, 1))[0])
        self.assertEqual({x["kaynak"] for x in satirlar}, {"Fatura (otomatik)", "Cari Kesinti / Masraf (otomatik)"})
        self.assertContains(r, "Yevmiye Kalemleri")

    def test_aktiflesmis_projede_bakiye_sifira_iner_ve_aktiflestirme_fisi_gorunur(self):
        yp.proje_aktiflestir(self.p, tarih=D(2026, 5, 10), kullanici=self.su,
                             satirlar=[{"hesap_id": HesapPlani.objects.get(hesap_kodu="253").pk, "varlik_adi": "makine", "tutar": Dc("11500")}])
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[self.p.pk]), {"sekme": "yevmiye"})
        satirlar = r.context["yevmiye_satirlari"]
        self.assertEqual(len(satirlar), 4)                                        # + aktifleştirme fişi
        self.assertEqual(satirlar[-1]["yur_bakiye"], Dc("0.00"))
        self.assertEqual(satirlar[-1]["fis_pk"], self.p.__class__.objects.get(pk=self.p.pk).aktiflestirme_fisi_id)
        self.assertEqual(r.context["ekstre"].bakiye, Dc("0.00"))

    def test_varsayilan_sekme_faturalar_ve_sekme_sayilari(self):
        r = self.client.get(reverse("core:yatirim_projesi_detay", args=[self.p.pk]))
        self.assertEqual(r.context["sekme"], "faturalar")
        self.assertEqual([(t["kod"], t["sayi"]) for t in r.context["sekmeler"]], [("faturalar", 2), ("diger", 1), ("yevmiye", 3)])
        self.assertEqual(r.context["fatura_toplam"], Dc("14000.00"))
