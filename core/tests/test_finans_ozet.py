"""Finans özeti (dashboard): kasa/banka bakiyeleri (TL + döviz, güncel kurla TL), kredi kartı borç/limit/kullanılabilir/son ödeme, kredi kalan borç, çek/senet
özeti ve yaklaşan vadeler; menüde Finans başlığı özet sayfasına gider; yalnız okur."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, CekSenet, EkranYetki, Kur, YevmiyeFisi
from core.services import finans_ozet as fo
from core.services.finans import (banka_hesap_olustur, banka_olustur, kasa_olustur, kredi_karti_olustur, kredi_olustur)
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal
BUGUN = D(2026, 10, 6)


class FinansOzetBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 9, 1), D(2026, 10, 5)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40") if g.month == 9 else Dc("42"), eur_alis=Dc("46") if g.month == 9 else Dc("48"))
        _hesap("100.01", "KASA TL")
        _hesap("102.01.0001", "HALK TL")
        _hesap("102.02.0001", "HALK EUR")
        _hesap("309.01", "KART", kalem="KVYK")
        _hesap("300.01", "KREDİ", kalem="KVYK")
        _hesap("500", "SERMAYE", kalem="OZK")
        cls.su = User.objects.create_superuser("fo", password="x")
        b = banka_olustur(ad="halk", kisa_ad="HALK")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.eur = banka_hesap_olustur(banka=b, ad="eur", para_birimi="EUR", muhasebe_kodu="102.02.0001")
        cls.kasa = kasa_olustur(ad="merkez kasa", para_birimi="TRY", muhasebe_kodu="100.01", kullanici=cls.su)
        cls.kart = kredi_karti_olustur(ad="halk kart", banka=b, kart_son4="1234", limit=100000, kesim_gunu=20, son_odeme_gunu=28,
                                       para_birimi="TRY", muhasebe_kodu="309.01", kullanici=cls.su)
        cls.kredi = kredi_olustur(ad="isletme kredisi", banka=b, anapara=500000, faiz_orani=3, para_birimi="TRY", muhasebe_kodu="300.01",
                                  kullanici=cls.su)
        # sermaye ile: TL banka 80.000, kasa 5.000, EUR banka 1.000 EUR @46 = 46.000 TL; kart borcu 30.000; kredi borcu 200.000
        for tutar, hesap, taraf_pb in ((80000, "102.01.0001", None), (5000, "100.01", None)):
            fis_olustur(tarih=D(2026, 9, 1), aciklama="açılış", satirlar=[
                SatirGirdi(hesap_kodu=hesap, taraf="B", islem_tutari=str(tutar)),
                SatirGirdi(hesap_kodu="500", taraf="A", islem_tutari=str(tutar))])
        fis_olustur(tarih=D(2026, 9, 1), aciklama="eur", satirlar=[
            SatirGirdi(hesap_kodu="102.02.0001", taraf="B", islem_tutari="1000", islem_pb="EUR", islem_kuru="46"),
            SatirGirdi(hesap_kodu="500", taraf="A", islem_tutari="46000")])
        fis_olustur(tarih=D(2026, 9, 1), aciklama="kart", satirlar=[
            SatirGirdi(hesap_kodu="500", taraf="B", islem_tutari="30000"), SatirGirdi(hesap_kodu="309.01", taraf="A", islem_tutari="30000")])
        fis_olustur(tarih=D(2026, 9, 1), aciklama="kredi", satirlar=[
            SatirGirdi(hesap_kodu="500", taraf="B", islem_tutari="200000"), SatirGirdi(hesap_kodu="300.01", taraf="A", islem_tutari="200000")])
        cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A.Ş.", para_birimi="TRY")
        c = lambda **kw: CekSenet.objects.create(tip="CEK", para_birimi="TRY", **kw)
        c(yon="ALINAN", tutar=Dc("10000"), vade=D(2026, 10, 20), durum="PORTFOYDE", cari=cari, belge_no="A1")
        c(yon="ALINAN", tutar=Dc("5000"), vade=D(2026, 10, 1), durum="PORTFOYDE", belge_no="A2")             # vadesi geçmiş
        c(yon="ALINAN", tutar=Dc("7000"), vade=D(2026, 12, 1), durum="TAHSILDE", belge_no="A3")
        c(yon="ALINAN", tutar=Dc("999"), vade=D(2026, 10, 10), durum="TAHSIL", belge_no="A4")                 # kapanmış: sayılmaz
        c(yon="VERILEN", tutar=Dc("20000"), vade=D(2026, 10, 15), durum="VERILDI", cari=cari, belge_no="V1")
        c(yon="VERILEN", tutar=Dc("3000"), vade=D(2026, 9, 30), durum="VERILDI", belge_no="V2")               # vadesi geçmiş
        c(yon="VERILEN", tutar=Dc("1"), vade=D(2026, 10, 15), durum="ODENDI", belge_no="V3")                  # ödenmiş: sayılmaz


class FinansOzetServisTest(FinansOzetBase):
    def test_nakit_toplamlari_guncel_kurla(self):
        o = fo.ozet(BUGUN)
        eur = next(r for r in o["banka"] if r["pb"] == "EUR")
        self.assertEqual((eur["bakiye"], eur["defter"], eur["guncel"]), (Dc("1000.00"), Dc("46000.00"), Dc("48000.00")))   # 1.000 × 48 (son kur)
        tl = {t["pb"]: t for t in o["pb_toplam"]}
        self.assertEqual((tl["TRY"]["bakiye"], tl["TRY"]["adet"]), (Dc("85000.00"), 2))                                      # banka + kasa
        self.assertEqual(tl["EUR"]["guncel"], Dc("48000.00"))
        self.assertEqual(o["nakit_tl"], Dc("133000.00"))
        self.assertEqual(o["kur_tarihi"], D(2026, 10, 5))

    def test_kart_ve_kredi(self):
        o = fo.ozet(BUGUN)
        k = o["kartlar"][0]
        self.assertEqual((k["limit"], k["borc"], k["kullanilabilir"], k["doluluk"]), (Dc("100000.00"), Dc("30000.00"), Dc("70000.00"), 30))
        self.assertEqual(k["son_odeme"], D(2026, 10, 28))                                    # bugün 6'sı, son ödeme günü 28
        self.assertEqual(o["kart_borc_tl"], Dc("30000.00"))
        self.assertEqual((o["kart_limit_tl"], o["kart_kullanilabilir_tl"]), (Dc("100000.00"), Dc("70000.00")))
        self.assertEqual((k["kalan_gun"], k["ton"]), (22, "iyi"))                           # 28.10 − 06.10
        self.assertEqual(o["krediler"][0]["kalan"], Dc("200000.00"))
        self.assertEqual(o["kredi_tl"], Dc("200000.00"))

    def test_son_odeme_tarihi_gecmisse_sonraki_ay(self):
        self.assertEqual(fo._sonraki_tarih(5, D(2026, 10, 6)), D(2026, 11, 5))
        self.assertEqual(fo._sonraki_tarih(31, D(2026, 2, 3)), D(2026, 2, 28))                # kısa ay → ay sonu
        self.assertEqual(fo._sonraki_tarih(10, D(2026, 12, 20)), D(2027, 1, 10))
        self.assertIsNone(fo._sonraki_tarih(None, BUGUN))

    def test_cek_ozeti(self):
        o = fo.ozet(BUGUN)
        c = o["cek"]
        tutar = lambda g: [(x["pb"], x["tutar"], x["adet"]) for x in g]
        self.assertEqual(tutar(c["portfoy"]), [("TRY", Dc("15000.00"), 2)])
        self.assertEqual(tutar(c["tahsilde"]), [("TRY", Dc("7000.00"), 1)])
        self.assertEqual(tutar(c["alinan_vadesi_gecmis"]), [("TRY", Dc("5000.00"), 1)])
        self.assertEqual(tutar(c["alinan_yakin"]), [("TRY", Dc("10000.00"), 1)])               # 20.10 ≤ 30 gün
        self.assertEqual(tutar(c["verilen"]), [("TRY", Dc("23000.00"), 2)])
        self.assertEqual(tutar(c["verilen_vadesi_gecmis"]), [("TRY", Dc("3000.00"), 1)])
        self.assertEqual((o["cek_alinan_tl"], o["cek_verilen_tl"]), (Dc("22000.00"), Dc("23000.00")))
        self.assertEqual(o["cek_net_tl"], Dc("-1000.00"))
        al, ve = o["cek_ozet"]["alinan"], o["cek_ozet"]["verilen"]
        self.assertEqual((al["toplam"], al["adet"]), (Dc("22000.00"), 3))
        self.assertEqual([(p["ad"], p["tl"], p["ton"]) for p in al["parcalar"]],
                         [("Portföyde", Dc("15000.00"), "iyi"), ("Tahsilde", Dc("7000.00"), "iyi"),
                          ("30 gün içinde", Dc("10000.00"), "orta"), ("Vadesi geçmiş", Dc("5000.00"), "yuksek")])
        self.assertEqual([(p["ad"], p["tl"]) for p in ve["parcalar"]], [("30 gün içinde", Dc("20000.00")), ("Vadesi geçmiş", Dc("3000.00"))])
        self.assertIsNone(o["cek_ozet"]["karsiliksiz"])
        self.assertEqual([x.belge_no for x in o["yaklasan"]], ["V2", "A2", "V1", "A1"])        # vadeye göre; kapanmış çekler yok
        self.assertEqual(o["net_pozisyon"], Dc("133000") + Dc("22000") - Dc("30000") - Dc("200000") - Dc("23000"))

    def test_yalniz_okur_kayit_olusturmaz(self):
        n = YevmiyeFisi.objects.count()
        fo.ozet(BUGUN)
        self.assertEqual(YevmiyeFisi.objects.count(), n)

    def test_kur_yoksa_defter_degeriyle(self):
        Kur.objects.all().delete()
        o = fo.ozet(BUGUN)
        eur = next(r for r in o["banka"] if r["pb"] == "EUR")
        self.assertEqual(eur["guncel"], Dc("46000.00"))
        self.assertIsNone(o["kur_tarihi"])


class FinansOzetEkranTest(FinansOzetBase):
    def test_sayfa_icerigi(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:finans_ozet"))
        self.assertEqual(r.status_code, 200)
        for metin in ("Finans Özeti", "133.000,00", "HALK KART", "Kullanılabilir Kart Limiti", "Kullanılabilir limit", "70.000,00", "Vadesi Yaklaşan", "V1"):
            self.assertContains(r, metin)

    def test_menude_finans_basligi_ozete_gider_ve_modul_acik(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:finans_ozet"))
        self.assertContains(r, 'href="%s"' % reverse("core:finans_ozet"))
        # kasa sayfasında da menü Finans'ı özet bağlantısıyla gösterir
        self.assertContains(self.client.get(reverse("core:kasalar")), 'href="%s"' % reverse("core:finans_ozet"))

    def test_yetki_finans_ekranlarindan_biri_yeterli(self):
        u = User.objects.create_user("fd", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:finans_ozet")).status_code, 403)
        EkranYetki.objects.create(kullanici=u, ekran_kod="kredi_karti")
        self.assertEqual(self.client.get(reverse("core:finans_ozet")).status_code, 200)
        self.assertContains(self.client.get(reverse("core:kredi_kartlari")), reverse("core:finans_ozet"))
