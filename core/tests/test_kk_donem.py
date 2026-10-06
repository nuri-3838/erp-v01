"""Kredi kartı taksit planı + dönem ekstresi + nakit akışı + toplu öneri: bölme/yuvarlama, kesim sınırı (kesim günü DÂHİL), son ödeme (hafta sonu/resmî tatil → iş günü),
dönem ekstresi = devir + taksit + tek çekim − ödeme, 309 defter uyumu, mevcut fişlerin değişmemesi, ekranlar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, Kur, KrediKartiTaksit, ResmiTatil, YevmiyeFisi, YevmiyeSatir
from core.services import kk_donem as kd
from core.services import kredi_karti_hareket as kk
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur, kredi_karti_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class KkDonemBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        g = D(2026, 1, 1)
        Kur.objects.bulk_create([Kur(tarih=g + datetime.timedelta(days=i), usd_alis=Dc("40")) for i in range(400)])
        _hesap("309.01", "HALK KART", kalem="KVYK")
        _hesap("309.02", "ZİRAAT KART", kalem="KVYK")
        _hesap("102.01.0001", "BANKA")
        _hesap("770", "GENEL GİDER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.01", "AĞIRLAMA", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.03", "BANKA MASRAFLARI", kalem="C", grup="GELIR_TABLOSU")
        _hesap("780", "FİNANSMAN", kalem="C", grup="GELIR_TABLOSU")
        _hesap("780.01", "KART FAİZİ", kalem="C", grup="GELIR_TABLOSU")
        cls.su = User.objects.create_superuser("kd", password="x")
        cls.halk = kredi_karti_olustur(ad="halk", kesim_gunu=26, son_odeme_gunu=2, limit=100000, para_birimi="TRY", muhasebe_kodu="309.01", kullanici=cls.su)
        cls.ziraat = kredi_karti_olustur(ad="ziraat", kesim_gunu=2, son_odeme_gunu=12, limit=50000, para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)
        cls.banka = banka_hesap_olustur(banka=banka_olustur(ad="b"), ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.gider = HesapPlani.objects.get(hesap_kodu="770.01")

    def harcama(self, kart, tutar, tarih, ack="HARCAMA", **kw):
        return kk.harcama_olustur(kart=kart, karsi=self.gider, tutar=tutar, tarih=tarih, aciklama=ack, kullanici=self.su, **kw)

    def odeme(self, kart, tutar, tarih):
        return kk.hareket_olustur(kart=kart, tip="odeme", karsi=self.banka, tutar=tutar, tarih=tarih, kullanici=self.su)

    def snapshot(self):
        return sorted((s.fis_id, s.hesap_id, str(s.borc), str(s.alacak)) for s in YevmiyeSatir.objects.filter(silindi=False))


class TakvimTest(KkDonemBase):
    def test_bolme_ve_yuvarlama(self):
        self.assertEqual(kd.bolme(Dc("27499.00"), 5), [Dc("5499.80")] * 5)
        self.assertEqual(kd.bolme(Dc("100.00"), 3), [Dc("33.33"), Dc("33.33"), Dc("33.34")])          # kuruş farkı SON taksitte
        t = kd.bolme(Dc("0.10"), 3)
        self.assertEqual((t, sum(t)), ([Dc("0.03"), Dc("0.03"), Dc("0.04")], Dc("0.10")))
        for tutar, adet in ((Dc("1000000.00"), 4), (Dc("81685.00"), 6), (Dc("168.00"), 3), (Dc("7.01"), 7)):
            self.assertEqual(sum(kd.bolme(tutar, adet)), tutar)

    def test_kesim_gunu_dahil_sinir(self):
        self.assertEqual(kd.donem_kesimi(self.halk, D(2026, 8, 25)), D(2026, 8, 26))
        self.assertEqual(kd.donem_kesimi(self.halk, D(2026, 8, 26)), D(2026, 8, 26))                    # kesim günü DÂHİL
        self.assertEqual(kd.donem_kesimi(self.halk, D(2026, 8, 27)), D(2026, 9, 26))                    # ertesi gün sonraki ekstre
        self.assertEqual(kd.donem_kesimi(self.ziraat, D(2026, 3, 2)), D(2026, 3, 2))
        self.assertEqual(kd.donem_kesimi(self.ziraat, D(2026, 3, 3)), D(2026, 4, 2))
        self.assertEqual(kd.donem_kesimi(self.halk, D(2026, 12, 27)), D(2027, 1, 26))                   # yıl dönümü
        self.halk.kesim_gunu = 31
        self.assertEqual(kd.donem_kesimi(self.halk, D(2026, 2, 10)), D(2026, 2, 28))                    # kısa ay → ay sonu

    def test_son_odeme_hafta_sonu_ve_resmi_tatil(self):
        self.assertEqual(kd.vade(self.halk, D(2026, 7, 26)), D(2026, 8, 3))        # 02.08 Pazar → 03.08
        self.assertEqual(kd.vade(self.ziraat, D(2026, 7, 2)), D(2026, 7, 13))      # 12.07 Pazar → 13.07 (canlı plan tarihi)
        self.assertEqual(kd.vade(self.ziraat, D(2026, 9, 2)), D(2026, 9, 14))      # 12.09 Cumartesi → 14.09 (canlı plan tarihi)
        self.assertEqual(kd.vade(self.halk, D(2026, 8, 26)), D(2026, 9, 2))        # hafta içi aynen
        ResmiTatil.objects.create(tarih=D(2026, 9, 2), ad="x")
        self.assertEqual(kd.vade(self.halk, D(2026, 8, 26)), D(2026, 9, 3))        # resmî tatil → sonraki iş günü

    def test_ilk_vade_onerisi_kaydirma(self):
        self.assertEqual(kd.ilk_vade_onerisi(self.halk, D(2026, 8, 14)), D(2026, 9, 2))
        self.assertEqual(kd.ilk_vade_onerisi(self.halk, D(2026, 8, 14), 1), D(2026, 10, 2))
        self.assertEqual(kd.ilk_vade_onerisi(self.halk, D(2026, 8, 31)), D(2026, 10, 2))           # kesimden sonraki gün → sonraki ekstre
        self.halk.kesim_gunu = None
        with self.assertRaises(kd.KkDonemHatasi):
            kd.ilk_vade_onerisi(self.halk, D(2026, 8, 14))


class PlanTest(KkDonemBase):
    def test_taksitli_harcama_plani_kesimden_kurulur_fis_degismez(self):
        f = self.harcama(self.halk, "27499", D(2026, 8, 14), "BOSCH TESTERE - 5 TAKSİT", taksit_adedi=5)
        plan = kd.aktif_plan(f)
        self.assertEqual((plan.taksit_adedi, plan.ilk_vade, plan.toplam_tutar), (5, D(2026, 9, 2), Dc("27499.00")))
        satirlar = kd.plan_satirlari(self.halk, plan)
        self.assertEqual([r["kesim"] for r in satirlar], [D(2026, 8, 26), D(2026, 9, 26), D(2026, 10, 26), D(2026, 11, 26), D(2026, 12, 26)])
        self.assertEqual([r["vade"] for r in satirlar], [D(2026, 9, 2), D(2026, 10, 2), D(2026, 11, 2), D(2026, 12, 2), D(2027, 1, 4)])
        self.assertEqual([r["tutar"] for r in satirlar], [Dc("5499.80")] * 5)
        # muhasebe tek satır tam tutar; 309 bakiyesi aynı
        self.assertEqual({(s.hesap_id, s.borc, s.alacak) for s in f.satirlar.filter(silindi=False)},
                         {("309.01", Dc("0.00"), Dc("27499.00")), ("770.01", Dc("27499.00"), Dc("0.00"))})
        self.assertEqual(_bak("309.01"), Dc("-27499.00"))

    def test_ilk_donem_kaydirma_ve_acik_ilk_vade(self):
        f = self.harcama(self.halk, "3000", D(2026, 8, 14), "X - 3 TAKSİT", taksit_adedi=3, ilk_donem=1)
        self.assertEqual(kd.aktif_plan(f).ilk_vade, D(2026, 10, 2))
        g = self.harcama(self.halk, "3000", D(2026, 8, 14), "Y - 3 TAKSİT", taksit_adedi=3, ilk_vade=D(2026, 9, 2))
        self.assertEqual(kd.aktif_plan(g).ilk_vade, D(2026, 9, 2))
        self.halk.kesim_gunu = self.halk.son_odeme_gunu = None
        self.halk.save()
        with self.assertRaises(kk.KrediKartiHareketHatasi):                                   # günler tanımsız + ilk tarih yok
            self.harcama(self.halk, "100", D(2026, 8, 14), taksit_adedi=3)

    def test_eski_planlar_en_yakin_ekstreye_eslenir(self):
        # canlıdaki elle girilmiş ilk_vade'ler: Halk 07-31 / 08-31 / 10-01, Ziraat 04-12 / 07-13 / 09-14
        for kart, vade_, kesim in ((self.halk, D(2026, 7, 31), D(2026, 7, 26)), (self.halk, D(2026, 8, 31), D(2026, 8, 26)),
                                   (self.halk, D(2026, 10, 1), D(2026, 9, 26)), (self.ziraat, D(2026, 4, 12), D(2026, 4, 2)),
                                   (self.ziraat, D(2026, 7, 13), D(2026, 7, 2)), (self.ziraat, D(2026, 9, 14), D(2026, 9, 2))):
            f = self.harcama(kart, "1000", D(2026, 3, 1))
            plan = KrediKartiTaksit.objects.create(kart=kart, fis=f, taksit_adedi=3, ilk_vade=vade_, toplam_tutar=Dc("1000.00"))
            self.assertEqual(kd.ilk_kesim_plan(kart, plan), kesim, (kart.ad, vade_))

    def test_duzenle_taksit_adedi_plan_kurar_degistirir_siler_fis_degismez(self):
        f = self.harcama(self.halk, "3000", D(2026, 8, 14), "AÇIKLAMA")
        once = self.snapshot()
        self.assertIsNone(kd.aktif_plan(f))
        kk.hareket_guncelle(fis=f, kart=self.halk, taksit_adedi=3, ilk_donem=0, kullanici=self.su)
        plan = kd.aktif_plan(f)
        self.assertEqual((plan.taksit_adedi, plan.ilk_vade), (3, D(2026, 9, 2)))
        kk.hareket_guncelle(fis=f, kart=self.halk, taksit_adedi=3, ilk_donem=0, kullanici=self.su)    # değişiklik yok → aynı plan
        self.assertEqual(KrediKartiTaksit.objects.filter(fis=f, silindi=False).count(), 1)
        kk.hareket_guncelle(fis=f, kart=self.halk, taksit_adedi=4, ilk_donem=1, kullanici=self.su)
        plan.refresh_from_db()
        self.assertEqual((plan.taksit_adedi, plan.ilk_vade), (4, D(2026, 10, 2)))
        self.assertEqual(kd.mevcut_kaydirma(self.halk, f, plan), 1)
        kk.hareket_guncelle(fis=f, kart=self.halk, taksit_adedi=1, kullanici=self.su)                  # 1 → plan kalkar
        self.assertIsNone(kd.aktif_plan(f))
        self.assertEqual(self.snapshot(), once)                                                          # fiş satırları hiç değişmedi

    def test_plan_yalniz_harcamada_ve_tl_kartta(self):
        o = self.odeme(self.halk, "100", D(2026, 8, 14))
        with self.assertRaises(kd.KkDonemHatasi):
            kd.plan_ayarla(fis=o, kart=self.halk, adet=3, kullanici=self.su)
        with self.assertRaises(kd.KkDonemHatasi):
            kd.plan_ayarla(fis=self.harcama(self.halk, "100", D(2026, 8, 14)), kart=self.halk, adet=99, kullanici=self.su)


class DonemEkstresiTest(KkDonemBase):
    def senaryo(self):
        self.tek1 = self.harcama(self.halk, "1000", D(2026, 8, 10), "TEK 1")
        self.taksitli = self.harcama(self.halk, "3000", D(2026, 8, 14), "TAKSİTLİ - 3 TAKSİT", taksit_adedi=3)
        self.tek2 = self.harcama(self.halk, "500", D(2026, 8, 30), "TEK 2")
        self.odeme_f = self.odeme(self.halk, "400", D(2026, 8, 28))

    def test_ilk_ekstre(self):
        self.senaryo()
        e = kd.donem_ekstresi(self.halk, D(2026, 8, 26), D(2026, 10, 6))
        self.assertEqual((e["devir"], e["t_cekim"], e["t_taksit"], e["t_odeme"]), (Dc("0.00"), Dc("1000.00"), Dc("1000.00"), Dc("0.00")))
        self.assertEqual(e["donem_borcu"], Dc("2000.00"))
        self.assertEqual(e["son_odeme"], D(2026, 9, 2))
        self.assertEqual((e["t_sonra_odeme"], e["kalan"]), (Dc("400.00"), Dc("1600.00")))             # 28.08 ödemesi kesimden sonra
        self.assertEqual([x["fis_pk"] for x in e["taksitler"]], [self.taksitli.pk])
        self.assertEqual([x["fis_pk"] for x in e["cekimler"]], [self.tek1.pk])

    def test_ikinci_ekstre_devir_taksit_tek_cekim_odeme(self):
        self.senaryo()
        e = kd.donem_ekstresi(self.halk, D(2026, 9, 26), D(2026, 10, 6))
        self.assertEqual(e["devir"], Dc("2000.00"))
        self.assertEqual((e["t_cekim"], e["t_taksit"], e["t_odeme"]), (Dc("500.00"), Dc("1000.00"), Dc("400.00")))
        self.assertEqual(e["donem_borcu"], Dc("2000.00") + Dc("500.00") + Dc("1000.00") - Dc("400.00"))   # devir + tek çekim + taksit − ödeme
        self.assertEqual([x["sira"] for x in e["taksitler"]], [2])

    def test_309_defter_uyumu(self):
        self.senaryo()
        for c in (D(2026, 8, 26), D(2026, 9, 26), D(2026, 10, 26), D(2026, 12, 26)):
            e = kd.donem_ekstresi(self.halk, c, D(2026, 10, 6))
            self.assertEqual(e["uyum"], e["defter_borcu"], c)
            self.assertEqual(e["defter_borcu"], -_bak("309.01"))                                       # 4.100 = 309 borç bakiyesi
        self.assertEqual(-_bak("309.01"), Dc("4100.00"))

    def test_kesim_gunundeki_harcama_o_ekstrede_ertesi_gun_sonrakinde(self):
        self.harcama(self.halk, "100", D(2026, 8, 26), "KESİM GÜNÜ")
        self.harcama(self.halk, "200", D(2026, 8, 27), "ERTESİ GÜN")
        e1 = kd.donem_ekstresi(self.halk, D(2026, 8, 26), D(2026, 10, 6))
        e2 = kd.donem_ekstresi(self.halk, D(2026, 9, 26), D(2026, 10, 6))
        self.assertEqual([x["tutar"] for x in e1["cekimler"]], [Dc("100.00")])
        self.assertEqual([x["tutar"] for x in e2["cekimler"]], [Dc("200.00")])

    def test_faiz_ucret_kalemi_tek_cekim_olarak_girer(self):
        self.senaryo()
        faiz = HesapPlani.objects.get(hesap_kodu="780.01")
        kk.hareket_olustur(kart=self.halk, tip="harcama", karsi=faiz, tutar="75", tarih=D(2026, 8, 26), aciklama="KART FAİZİ", kullanici=self.su)
        e = kd.donem_ekstresi(self.halk, D(2026, 8, 26), D(2026, 10, 6))
        self.assertEqual(e["donem_borcu"], Dc("2075.00"))

    def test_gun_tanimsiz_veya_doviz_kart_hata(self):
        self.halk.son_odeme_gunu = None
        with self.assertRaises(kd.KkDonemHatasi):
            kd.donem_ekstresi(self.halk, D(2026, 8, 26))


class NakitAkisiOnerTest(KkDonemBase):
    def test_nakit_akisi_kesilmis_acik_ve_gelecek(self):
        self.harcama(self.halk, "800", D(2026, 9, 15), "TEK")
        self.harcama(self.halk, "3000", D(2026, 9, 20), "TAKSİT - 3 TAKSİT", taksit_adedi=3)
        self.odeme(self.halk, "300", D(2026, 10, 1))
        o = kd.nakit_akisi(D(2026, 10, 6))
        rows = [(r["kart"].ad, r["kesim"], r["vade"], r["tutar"], r["tur"]) for r in o["satirlar"]]
        self.assertEqual(rows, [("HALK", D(2026, 9, 26), D(2026, 10, 2), Dc("1500.00"), "Kesilmiş ekstre"),        # 800 + 1000 − 300
                                ("HALK", D(2026, 10, 26), D(2026, 11, 2), Dc("1000.00"), "Açık dönem"),
                                ("HALK", D(2026, 11, 26), D(2026, 12, 2), Dc("1000.00"), "Taksit")])
        self.assertEqual([(a["yil"], a["ay"], a["toplam"]) for a in o["aylar"]],
                         [(2026, 10, Dc("1500.00")), (2026, 11, Dc("1000.00")), (2026, 12, Dc("1000.00"))])
        self.assertEqual(o["toplam"], Dc("3500.00"))

    def test_fazla_odeme_acik_donemden_dusulur(self):
        self.harcama(self.halk, "1000", D(2026, 9, 15), "TEK")
        self.harcama(self.halk, "700", D(2026, 10, 3), "TEK 2")                  # açık dönem
        self.odeme(self.halk, "1200", D(2026, 10, 4))
        o = kd.nakit_akisi(D(2026, 10, 6))
        self.assertEqual([(r["tur"], r["tutar"]) for r in o["satirlar"]], [("Açık dönem", Dc("500.00"))])    # 700 − 200 fazla ödeme

    def test_gunu_tanimsiz_kart_atlanir(self):
        self.ziraat.kesim_gunu = None
        self.ziraat.save()
        o = kd.nakit_akisi(D(2026, 10, 6))
        self.assertEqual([k.ad for k in o["atlanan"]], ["ZİRAAT"])

    def test_oneriler_ve_uygulama(self):
        f1 = self.harcama(self.halk, "4000", D(2026, 8, 14), "KOÇTAŞ - 4 TAKSİT")
        f2 = self.harcama(self.halk, "900", D(2026, 8, 15), "PEŞİN ALIŞVERİŞ")
        f3 = self.harcama(self.halk, "6000", D(2026, 8, 16), "VAR - 6 TAKSİT", taksit_adedi=6)
        once = self.snapshot()
        o = kd.oneriler()
        self.assertEqual([(x["fis"].pk, x["adet"], x["ilk_vade"], x["tutar"]) for x in o], [(f1.pk, 4, D(2026, 9, 2), Dc("4000.00"))])
        self.assertEqual(kd.oneri_uygula([f1.pk], kullanici=self.su), 1)
        self.assertEqual((kd.aktif_plan(f1).taksit_adedi, kd.aktif_plan(f1).ilk_vade), (4, D(2026, 9, 2)))
        self.assertIsNone(kd.aktif_plan(f2))
        self.assertEqual(kd.oneriler(), [])
        with self.assertRaises(kd.KkDonemHatasi):                                              # artık listede olmayan seçim
            kd.oneri_uygula([f1.pk], kullanici=self.su)
        self.assertEqual(self.snapshot(), once)                                                  # muhasebe hiç değişmedi

    def test_oneri_gunsuz_kartta_uygulanamaz(self):
        self.ziraat.kesim_gunu = self.ziraat.son_odeme_gunu = None
        self.ziraat.save()
        f = self.harcama(self.ziraat, "900", D(2026, 8, 15), "PEŞİN - 3 TAKSİT")
        o = kd.oneriler()
        self.assertEqual((o[0]["ilk_vade"], bool(o[0]["neden"])), (None, True))
        with self.assertRaises(kd.KkDonemHatasi):
            kd.oneri_uygula([f.pk], kullanici=self.su)


class EkranTest(KkDonemBase):
    def setUp(self):
        self.client.force_login(self.su)

    def test_donem_ekstresi_ekrani_ve_banka_karsilastirma(self):
        f = self.harcama(self.halk, "3000", D(2026, 8, 14), "TAKSİTLİ - 3 TAKSİT", taksit_adedi=3)
        url = reverse("core:kredi_karti_donem_ekstresi", args=[self.halk.pk])
        n = YevmiyeFisi.objects.count()
        r = self.client.get(url + "?kesim=2026-08-26")
        self.assertContains(r, "Dönem borcu")
        self.assertContains(r, "02.09.2026")
        self.assertContains(r, "Taksit 1/3")
        self.assertContains(r, "Faiz ekle (780.01)")
        r = self.client.get(url + "?kesim=2026-08-26&banka_tutar=1.050,00")
        self.assertContains(r, "fark")
        self.assertContains(r, "50,00")
        self.assertContains(self.client.get(url + "?kesim=2026-08-26&banka_tutar=1.000,00"), "fark yok")
        self.assertContains(self.client.get(url + "?kesim=2026-08-26&banka_tutar=abc"), "geçerli bir sayı değil")
        self.assertEqual(self.client.get(url).status_code, 200)                                  # varsayılan: son kesilmiş ekstre
        self.assertEqual(YevmiyeFisi.objects.count(), n)                                         # karşılaştırma hiçbir şey yazmaz
        self.halk.son_odeme_gunu = None
        self.halk.save()
        self.assertContains(self.client.get(url), "tanımlı olmalı")

    def test_faiz_kisayolu_formu_doldurur(self):
        r = self.client.get(reverse("core:kredi_karti_hareket_ekle", args=[self.halk.pk, "harcama"]) + "?gider=780.01&tarih=2026-08-26&aciklama=KART%20FAİZİ")
        self.assertContains(r, 'value="780.01" selected')
        self.assertContains(r, "2026-08-26")

    def test_nakit_akisi_ve_oneri_ekranlari(self):
        self.harcama(self.halk, "3000", D(2026, 9, 20), "T - 3 TAKSİT", taksit_adedi=3)
        self.assertContains(self.client.get(reverse("core:kredi_karti_nakit_akisi")), "Gelecek Son Ödemeler")
        f = self.harcama(self.halk, "4000", D(2026, 8, 14), "KOÇTAŞ - 4 TAKSİT")
        url = reverse("core:kredi_karti_taksit_onerileri")
        self.assertContains(self.client.get(url), "KOÇTAŞ")
        r = self.client.post(url, {"fis": [f.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(kd.aktif_plan(f).taksit_adedi, 4)
        self.assertContains(self.client.get(url), "yok")

    def test_harcama_formu_ve_duzenle_ekrani_taksit_alanlari(self):
        url = reverse("core:kredi_karti_hareket_ekle", args=[self.halk.pk, "harcama"])
        self.assertContains(self.client.get(url), "İlk taksit dönemi")
        r = self.client.post(url, {"gider": "770.01", "tutar": "3.000,00", "tarih": "2026-08-14", "aciklama": "DENEME - 3 TAKSİT",
                                   "taksit_adedi": "3", "ilk_donem": "1", "ilk_vade": "", "kur": ""})
        self.assertEqual(r.status_code, 302)
        f = YevmiyeFisi.objects.filter(kaynak="KREDI_KARTI").latest("pk")
        self.assertEqual(kd.aktif_plan(f).ilk_vade, D(2026, 10, 2))
        duz = reverse("core:kredi_karti_hareket_duzenle", args=[self.halk.pk, f.pk])
        r = self.client.get(duz)
        self.assertContains(r, "Taksit Sayısı")
        self.assertContains(r, "Mevcut plan: 3 taksit")
        once = self.snapshot()
        r = self.client.post(duz, {"aciklama": "DENEME - 3 TAKSİT", "gider": "770.01", "taksit_adedi": "5", "ilk_donem": "0", "yatirim_projesi": ""})
        self.assertEqual(r.status_code, 302)
        plan = kd.aktif_plan(f)
        self.assertEqual((plan.taksit_adedi, plan.ilk_vade), (5, D(2026, 9, 2)))
        self.assertEqual(self.snapshot(), once)

    def test_liste_ve_detay_baglantilari(self):
        r = self.client.get(reverse("core:kredi_kartlari"))
        self.assertContains(r, "Son ödeme")
        self.assertContains(r, reverse("core:kredi_karti_nakit_akisi"))
        self.assertContains(r, reverse("core:kredi_karti_taksit_onerileri"))
        self.assertContains(self.client.get(reverse("core:kredi_karti_detay", args=[self.halk.pk])), "Dönem Ekstresi")
