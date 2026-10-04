"""Dönemsel gider (180): alış gider faturası satırı → faturaya özel 180.xx.000N hesabı; aylık dağıtım (son ay yuvarlama, döviz TL hesabı),
vadesi gelen aylar için idempotent üretim (gelecek ay yok), fatura düzenlenince yeniden hesap / silinince silme, ekran + komut."""
import calendar
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, DonemselDagitim, EkranYetki, FaturaSatir, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import donemsel_gider as dg
from core.services import fatura as fs
from core.services import hesap_plani as hp
from core.services import raporlar
from core.tests.test_banka_hesap_hareketi import _hesap
from core.tests.test_satis_hesap_demirbas import SatisHesapDemirbasTestBase

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class PlanTest(TestCase):
    def test_12_ay_esit_ve_son_ay_yuvarlama_farki(self):
        aylar = dg.ay_sonlari(D(2026, 7, 1), D(2027, 6, 30))
        self.assertEqual((len(aylar), aylar[0], aylar[-1]), (12, D(2026, 7, 31), D(2027, 6, 30)))
        p = dg.plan(Dc("53000"), Dc("1"), aylar)
        self.assertEqual([x[1] for x in p[:11]], [Dc("4416.67")] * 11)
        self.assertEqual(p[11][1], Dc("4416.63"))                                  # 53.000 − 11 × 4.416,67
        self.assertEqual(sum(x[1] for x in p), Dc("53000.00"))

    def test_doviz_tl_aylik_doviz_x_kur_son_ay_toplamdan(self):
        p = dg.plan(Dc("53000"), Dc("54.123456"), dg.ay_sonlari(D(2026, 7, 1), D(2027, 6, 30)))
        self.assertEqual(p[0][2], (Dc("4416.67") * Dc("54.123456")).quantize(Dc("0.01")))
        self.assertEqual(sum(x[2] for x in p), (Dc("53000") * Dc("54.123456")).quantize(Dc("0.01")))   # TL toplamı tam

    def test_tam_ay_zorunlu(self):
        for b, e in ((D(2026, 7, 2), D(2027, 6, 30)), (D(2026, 7, 1), D(2027, 6, 29)), (D(2026, 8, 1), D(2026, 7, 31))):
            with self.assertRaises(dg.DonemselGiderHatasi):
                dg.ay_sonlari(b, e)
        self.assertEqual(len(dg.ay_sonlari(D(2026, 2, 1), D(2026, 2, 28))), 1)


class DonemselFaturaBase(SatisHesapDemirbasTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("180", "GELECEK AYLARA AİT GİDERLER")
        hp.hesap_olustur(kod="180.01", ad="KİRA", ust_kodu="180")
        _hesap("320.30.0001", "ARGEMA", kalem="KVYK")
        _hesap("646", "KUR FARKI KÂRI", kalem="D", grup="GELIR_TABLOSU")
        _hesap("656", "KUR FARKI ZARARI", kalem="D", grup="GELIR_TABLOSU")
        cls.argema = Cari.objects.create(kod="320-30-0001", unvan="ARGEMA", muhasebe_kodu="320.30.0001", para_birimi="EUR")
        y, m = 2026, 7
        for _ in range(13):                                           # 2026-07-01 + her ay sonu (fiş USD kuru için)
            for g in (D(y, m, 1), D(y, m, calendar.monthrange(y, m)[1])):
                Kur.objects.get_or_create(tarih=g, defaults=dict(usd_alis=Dc("40"), eur_alis=Dc("54")))
            m += 1
            if m == 13:
                y, m = y + 1, 1

    def _donemsel(self, tutar="12000", kdv=None, cari=None, no="GR-2026-00005", b=D(2026, 7, 1), e=D(2027, 6, 30),
                  tarih=D(2026, 7, 1), hesap_id="180.01", gider="770.01", ack="KİRA"):
        satir = {"hesap_id": gider, "donemsel": True, "donem_grup": hesap_id, "donem_baslangic": b, "donem_bitis": e,
                 "donem_aciklama": ack, "miktar": "1", "birim_fiyat": tutar, "kdv_id": (kdv or self.kdv0).pk}
        return fs.fatura_olustur(tip_id=self.gider.pk, cari_id=(cari or self.satici).pk, tarih=tarih, fatura_no=no,
                                 satirlar=[satir], para_birimi=("EUR" if cari is self.argema else "TRY"), kullanici=self.su)


class DonemselFaturaTest(DonemselFaturaBase):
    def test_satir_faturaya_ozel_180_hesabina_yazilir_kdv_191(self):
        f = self._donemsel("12000", kdv=self.kdv20)
        h = HesapPlani.objects.get(hesap_kodu="180.01.0001")
        self.assertEqual(h.hesap_adi, "GR-2026-00005 KİRA")
        s = f.satirlar.get()
        self.assertEqual((s.hesap_id, s.donem_gider_id, s.donem_baslangic, s.donem_bitis), ("180.01.0001", "770.01", D(2026, 7, 1), D(2027, 6, 30)))
        satirlar = {(x.hesap_id, "B" if x.borc else "A"): x.borc or x.alacak for x in f.fis.satirlar.all()}
        self.assertEqual(satirlar[("180.01.0001", "B")], Dc("12000.00"))
        self.assertEqual(satirlar[("191.20", "B")], Dc("2400.00"))                       # KDV normal 191
        self.assertEqual(satirlar[("320.01", "A")], Dc("14400.00"))
        self.assertEqual(DonemselDagitim.objects.filter(fatura=f).count(), 12)
        self.assertFalse(DonemselDagitim.objects.filter(fis__isnull=False).exists())      # fiş henüz yok

    def test_uretim_idempotent_gelecek_ay_yok(self):
        f = self._donemsel("12000")
        self.assertEqual(dg.uret(D(2026, 9, 30)), 3)                                       # Temmuz, Ağustos, Eylül
        self.assertEqual(dg.uret(D(2026, 9, 30)), 0)                                       # idempotent
        self.assertEqual(dg.uret(D(2026, 10, 15)), 0)                                      # 31.10 henüz gelmedi
        fisler = YevmiyeFisi.objects.filter(kaynak="DONEMSEL").order_by("tarih")
        self.assertEqual([x.tarih for x in fisler], [D(2026, 7, 31), D(2026, 8, 31), D(2026, 9, 30)])
        s = {(x.hesap_id, "B" if x.borc else "A"): x.borc or x.alacak for x in fisler[0].satirlar.all()}
        self.assertEqual(s, {("770.01", "B"): Dc("1000.00"), ("180.01.0001", "A"): Dc("1000.00")})
        self.assertEqual(_bak("180.01.0001"), Dc("9000.00"))
        self.assertEqual(_bak("770.01"), Dc("3000.00"))
        self.assertEqual(dg.uret(D(2027, 6, 30)), 9)
        self.assertEqual(_bak("180.01.0001"), Dc("0"))                                     # dönem sonunda tam 0

    def test_doviz_faturada_aylik_doviz_x_kur_ve_180_sifirlanir(self):
        f = self._donemsel("53000", cari=self.argema)
        self.assertEqual((f.para_birimi, f.kur), ("EUR", Dc("54.000000")))
        r = DonemselDagitim.objects.filter(fatura=f).order_by("sira")
        self.assertEqual((r[0].doviz, r[0].tl, r[11].doviz), (Dc("4416.67"), Dc("238500.18"), Dc("4416.63")))
        self.assertEqual(dg.uret(D(2027, 6, 30)), 12)
        s180 = YevmiyeSatir.objects.filter(hesap_id="180.01.0001", silindi=False, fis__silindi=False, alacak__gt=0).first()
        self.assertEqual((s180.islem_pb, s180.islem_tutari, s180.islem_kuru), ("EUR", Dc("4416.67"), Dc("54.000000")))
        self.assertEqual(_bak("180.01.0001"), Dc("0"))                                     # TL 0
        eur = sum((x.islem_tutari if x.borc else -x.islem_tutari) for x in
                  YevmiyeSatir.objects.filter(hesap_id="180.01.0001", islem_pb="EUR", silindi=False, fis__silindi=False))
        self.assertEqual(eur, Dc("0"))                                                      # EUR de 0
        self.assertEqual(_bak("770.01"), Dc("2862000.00"))                                  # 53.000 × 54

    def test_180_kur_farki_havuzu_ve_kur_degerleme_kapsaminda_degil(self):
        from core.services import kur_degerleme as kd
        from core.services import kur_farki as kf
        HesapPlani.objects.filter(hesap_kodu__startswith="180").update(parasal=False)                  # canlıdaki gibi parasal olmayan hesap
        f = self._donemsel("53000", cari=self.argema)
        dg.uret(D(2026, 9, 30))
        self.assertNotIn("180.01.0001", {h for h, pb in kf.tum_havuzlar()})                           # kur farkı havuzu yok
        fis = YevmiyeFisi.objects.filter(kaynak="DONEMSEL").first()
        self.assertEqual(fis.satirlar.count(), 2)                                                      # kur farkı satırı üretilmedi
        self.assertFalse(YevmiyeSatir.objects.filter(fis__kaynak="DONEMSEL", ana_satir__isnull=False).exists())
        o = kd.onizle(D(2026, 9, 30))
        self.assertNotIn("180.01.0001", {r["hesap_kodu"] for r in o["satirlar"] + o["degerlenmeyen"]})   # değerleme kapsamı dışı
        for kur_degisti in (Dc("60"),):                                                                # kur değişse de 180 dokunulmaz
            Kur.objects.filter(tarih=D(2026, 9, 30)).update(eur_alis=kur_degisti)
            self.assertNotIn("180.01.0001", {r["hesap_kodu"] for r in kd.onizle(D(2026, 9, 30))["satirlar"]})
        self.assertEqual(f.satirlar.get().hesap_id, "180.01.0001")

    def test_fatura_silinince_fisler_ve_plan_silinir_hesap_kalir(self):
        f = self._donemsel("12000")
        dg.uret(D(2026, 9, 30))
        fis_pks = list(YevmiyeFisi.objects.filter(kaynak="DONEMSEL").values_list("pk", flat=True))
        self.assertEqual(len(fis_pks), 3)
        fs.fatura_sil(f, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk__in=fis_pks).exists())
        self.assertFalse(DonemselDagitim.objects.exists())
        self.assertEqual(_bak("770.01"), Dc("0"))
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="180.01.0001", silindi=False).exists())      # hesap KALIR

    def test_fatura_duzenlenince_plan_yeniden_hesaplanir_uretilen_aylar_yeniden_uretilir(self):
        f = self._donemsel("12000")
        dg.uret(D(2026, 8, 31))                                                            # Temmuz + Ağustos
        s = f.satirlar.get()
        fs.fatura_guncelle(f, tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=f.tarih, fatura_no="GR-2026-00005",
                           satirlar=[{"hesap_id": "770.01", "donemsel": True, "donem_hesap_id": s.hesap_id,
                                      "donem_baslangic": D(2026, 7, 1), "donem_bitis": D(2027, 6, 30), "donem_aciklama": "KİRA",
                                      "miktar": "1", "birim_fiyat": "24000", "kdv_id": self.kdv0.pk}], kullanici=self.su)
        self.assertEqual(HesapPlani.objects.filter(hesap_kodu__startswith="180.01.").count(), 1)          # yeni hesap açılmadı
        f.refresh_from_db()
        r = DonemselDagitim.objects.filter(fatura=f).order_by("sira")
        self.assertEqual((r.count(), r[0].doviz), (12, Dc("2000.00")))
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="DONEMSEL", silindi=False).count(), 2)          # önceden üretilen 2 ay yeniden
        self.assertEqual(_bak("770.01"), Dc("4000.00"))
        self.assertEqual(_bak("180.01.0001"), Dc("20000.00"))

    def test_dusen_satirin_dagitimi_silinir(self):
        f = self._donemsel("12000")
        dg.uret(D(2026, 8, 31))
        fs.fatura_guncelle(f, tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=f.tarih, fatura_no="GR-2026-00005",
                           satirlar=[{"hesap_id": "770.01", "miktar": "1", "birim_fiyat": "12000", "kdv_id": self.kdv0.pk}],
                           kullanici=self.su)
        self.assertFalse(DonemselDagitim.objects.exists())
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="DONEMSEL").exists())
        self.assertEqual(_bak("770.01"), Dc("12000.00"))

    def test_gecersiz_girdiler(self):
        with self.assertRaises(fs.FaturaHatasi):
            self._donemsel(b=D(2026, 7, 5))                                                # tam ay değil
        with self.assertRaises(fs.FaturaHatasi):
            self._donemsel(hesap_id="180.99")                                              # grup yok
        with self.assertRaises(fs.FaturaHatasi):
            self._donemsel(gider="253")                                                    # gider hesabı değil
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu__startswith="180.01.").exists())            # hata → hesap da açılmadı
        self.assertFalse(FaturaSatir.objects.exists())


class DonemselEkranKomutTest(DonemselFaturaBase):
    def test_komut_ve_ekran(self):
        self._donemsel("12000")
        out = StringIO()
        call_command("donemsel_dagitim_olustur", "--tarih", "2026-08-31", stdout=out)
        self.assertIn("2 dönemsel dağıtım fişi", out.getvalue())
        out = StringIO()
        call_command("donemsel_dagitim_olustur", "--tarih", "2026-08-31", stdout=out)
        self.assertIn("0 dönemsel", out.getvalue())
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="donemsel_dagitim")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:donemsel_dagitim"), {"tarih": "2026-10-31"})
        self.assertContains(r, "Dönemsel dağıtımları oluştur (2 bekliyor)")                 # Eylül + Ekim bekliyor
        r = self.client.post(reverse("core:donemsel_dagitim"), {"tarih": "2026-10-31"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="DONEMSEL").count(), 4)

    def test_fatura_detayinda_ay_ay_tablo(self):
        f = self._donemsel("12000")
        dg.uret(D(2026, 7, 31))
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="alis_faturalari")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(r, "Dönemsel Dağıtım")
        self.assertContains(r, "07.2026")
        self.assertContains(r, "henüz üretilmedi")
