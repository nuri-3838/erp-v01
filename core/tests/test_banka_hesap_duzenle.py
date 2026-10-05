"""Banka Hesaba Ödeme / Hesaptan Giriş: DÜZENLE (tarih, tutar, hesap, proje, açıklama) + hesap BÖLME (N karşı satır; toplam = banka tutarı).
Fiş silinmeden aynı numarayla yeniden yazılır; tek satırlı kayıtlar aynen çalışır; cari/banka/kasa karşılıklı hareketler bu yolda düzenlenmez."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Kur, YevmiyeFisi
from core.services import banka_hareket as bh
from core.services import raporlar
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yatirim_projesi import proje_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class HesapDuzenleBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 9, 10), D(2026, 9, 20)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        _hesap("102.01.0001", "TL BANKA")
        _hesap("102.02.0001", "USD BANKA")
        _hesap("100.01", "KASA")
        _hesap("360", "ÖDENECEK VERGİ VE FONLAR", kalem="KVYK")
        _hesap("360.20", "GELİR VERGİSİ STOPAJI", kalem="KVYK")
        _hesap("770", "GENEL YÖNETİM GİDERLERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.11", "DAMGA VERGİSİ GİDERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.01", "AĞIRLAMA", kalem="C", grup="GELIR_TABLOSU")
        _hesap("780.01", "FİNANSMAN GİDERİ", kalem="C", grup="GELIR_TABLOSU")
        _hesap("649", "DİĞER GELİRLER", kalem="D", grup="GELIR_TABLOSU")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("320.01", "SATICI")
        from core.models import Cari
        cls.satici = Cari.objects.create(kod="S1", unvan="SATICI", muhasebe_kodu="320.01", para_birimi="TRY")
        cls.su = User.objects.create_superuser("hd", password="x")
        b = banka_olustur(ad="halk")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        cls.proje = proje_olustur(ad="hat", kullanici=cls.su)

    def odeme(self, satirlar=None, tutar="2.248,15", tarih=D(2026, 9, 10), hesap=None, tip="hesaba_odeme"):
        satirlar = satirlar or [{"hesap_kodu": "360.20", "tutar": None, "aciklama": "MUHTASAR"}]
        return bh.hareket_olustur(banka_hesap=hesap or self.tl, tip=tip, karsi=None, satirlar=satirlar, tutar=tutar, tarih=tarih,
                                  kullanici=self.su)

    def satirlar(self, fis):
        return [(s.hesap_id, "B" if s.borc else "A", s.borc or s.alacak) for s in fis.satirlar.filter(silindi=False, ana_satir__isnull=True).order_by("id")]


class HesapDuzenleServisTest(HesapDuzenleBase):
    def test_muhtasar_bolme_ornegi(self):
        f = self.odeme()                                                                   # tek satır 360.20 2.248,15
        no, sayac = (f.yil, f.fis_no, f.tarih), YevmiyeFisi.objects.filter(silindi=False).count()
        self.assertEqual(self.satirlar(f), [("102.01.0001", "A", Dc("2248.15")), ("360.20", "B", Dc("2248.15"))])
        bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="2.248,15", tarih=D(2026, 9, 10), aciklama="MUHTASAR + DAMGA", satirlar=[
            {"hesap_kodu": "360.20", "tutar": "1.308,45", "aciklama": "GELİR VERGİSİ STOPAJI"},
            {"hesap_kodu": "770.11", "tutar": "939,70", "aciklama": "BEYANNAME DAMGA VERGİSİ"}], kullanici=self.su)
        f.refresh_from_db()
        self.assertEqual(self.satirlar(f), [("102.01.0001", "A", Dc("2248.15")), ("360.20", "B", Dc("1308.45")), ("770.11", "B", Dc("939.70"))])
        self.assertEqual(((f.yil, f.fis_no, f.tarih), f.silindi, f.aciklama), (no, False, "MUHTASAR + DAMGA"))   # aynı fiş no + tarih
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), sayac)
        self.assertEqual(sum(x.borc for x in f.satirlar.filter(silindi=False)), sum(x.alacak for x in f.satirlar.filter(silindi=False)))
        self.assertEqual(raporlar._devir("102.01.0001", D(2100, 1, 1))[0], Dc("-2248.15"))

    def test_toplam_eslesmezse_reddedilir_fis_degismez(self):
        f = self.odeme()
        onceki = self.satirlar(f)
        with self.assertRaises(bh.BankaHareketHatasi) as cm:
            bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="2.248,15", tarih=D(2026, 9, 10), satirlar=[
                {"hesap_kodu": "360.20", "tutar": "1.308,45"}, {"hesap_kodu": "770.11", "tutar": "939,69"}], kullanici=self.su)
        self.assertIn("eşit olmalı", str(cm.exception))
        self.assertEqual(self.satirlar(f), onceki)

    def test_tarih_tutar_hesap_proje_aciklama_degisir(self):
        f = self.odeme()
        no = (f.yil, f.fis_no)
        bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="5000", tarih=D(2026, 9, 20), aciklama="arsa gideri", satirlar=[
            {"hesap_kodu": "258", "tutar": "3000", "yatirim_projesi_id": self.proje.pk, "aciklama": "TAPU"},
            {"hesap_kodu": "770.01", "tutar": "2000"}], kullanici=self.su)
        f.refresh_from_db()
        self.assertEqual(((f.yil, f.fis_no), f.tarih, f.aciklama), (no, D(2026, 9, 20), "ARSA GİDERİ"))
        s258 = f.satirlar.get(hesap_id="258", silindi=False)
        self.assertEqual((s258.borc, s258.yatirim_projesi_id), (Dc("3000.00"), self.proje.pk))
        self.assertEqual(f.satirlar.get(hesap_id="102.01.0001", silindi=False).alacak, Dc("5000.00"))
        with self.assertRaises(bh.BankaHareketHatasi):                                     # 258'de proje zorunlu
            bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="5000", tarih=D(2026, 9, 20), satirlar=[
                {"hesap_kodu": "258", "tutar": "5000"}], kullanici=self.su)

    def test_yil_degisimi_reddedilir(self):
        f = self.odeme()
        onceki = self.satirlar(f)
        Kur.objects.create(tarih=D(2027, 1, 5), usd_alis=Dc("40"))
        with self.assertRaises(bh.BankaHareketHatasi):
            bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="2.248,15", tarih=D(2027, 1, 5),
                                       satirlar=[{"hesap_kodu": "360.20"}], kullanici=self.su)
        self.assertEqual(self.satirlar(f), onceki)

    def test_hesaptan_giris_simetrik_bolme(self):
        f = self.odeme(tip="hesaptan_giris", satirlar=[{"hesap_kodu": "649"}], tutar="1000")
        self.assertEqual(self.satirlar(f), [("102.01.0001", "B", Dc("1000.00")), ("649", "A", Dc("1000.00"))])
        self.assertEqual(bh.hesap_hareketi_bilgisi(f, self.tl)["tip"], "hesaptan_giris")
        bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="1.500,00", tarih=D(2026, 9, 10), satirlar=[
            {"hesap_kodu": "649", "tutar": "1.000,00"}, {"hesap_kodu": "360.20", "tutar": "500,00"}], kullanici=self.su)
        self.assertEqual(self.satirlar(f), [("102.01.0001", "B", Dc("1500.00")), ("649", "A", Dc("1000.00")), ("360.20", "A", Dc("500.00"))])

    def test_tek_satirli_eski_kayit_aynen_calisir(self):
        f = self.odeme([{"hesap_kodu": "780.01"}], tutar="750")
        onceki = self.satirlar(f)
        bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.tl, tutar="750", tarih=D(2026, 9, 10), aciklama=f.aciklama,
                                   satirlar=[{"hesap_kodu": "780.01", "tutar": None}], kullanici=self.su)
        self.assertEqual(self.satirlar(f), onceki)

    def test_doviz_banka_hesabi_bolme_kur_korunur(self):
        f = self.odeme([{"hesap_kodu": "770.01"}], tutar="100", hesap=self.usd)
        b = bh.hesap_hareketi_bilgisi(f, self.usd)
        self.assertEqual((b["pb"], b["kur"], b["tutar"]), ("USD", Dc("40.000000"), Dc("100.00")))
        bh.hesap_hareketi_guncelle(fis=f, banka_hesap=self.usd, tutar="100", tarih=D(2026, 9, 10), kur_override=Dc("40"), satirlar=[
            {"hesap_kodu": "770.01", "tutar": "60"}, {"hesap_kodu": "780.01", "tutar": "40"}], kullanici=self.su)
        s = {x.hesap_id: x for x in f.satirlar.filter(silindi=False, ana_satir__isnull=True)}
        self.assertEqual((s["770.01"].borc, s["770.01"].islem_tutari, s["780.01"].borc, s["780.01"].islem_pb), (Dc("2400.00"), Dc("60.00"), Dc("1600.00"), "USD"))
        self.assertEqual(s["102.02.0001"].alacak, Dc("4000.00"))

    def test_uygun_olmayan_hareketler(self):
        cari = bh.hareket_olustur(banka_hesap=self.tl, tip="cari_odeme", karsi=self.satici, tutar="100", tarih=D(2026, 9, 10), kullanici=self.su)
        self.assertFalse(bh.hesap_hareketi_uygun_mu(cari, self.tl))
        virman = bh.hareket_olustur(banka_hesap=self.tl, tip="banka_virman", karsi=self._tl2(), tutar="100", tarih=D(2026, 9, 10), kullanici=self.su)
        self.assertFalse(bh.hesap_hareketi_uygun_mu(virman, self.tl))
        hesap = self.odeme()
        self.assertTrue(bh.hesap_hareketi_uygun_mu(hesap, self.tl))
        self.assertFalse(bh.hesap_hareketi_uygun_mu(hesap, self.usd))                       # başka banka hesabının hareketi değil
        with self.assertRaises(bh.BankaHareketHatasi):
            bh.hesap_hareketi_guncelle(fis=cari, banka_hesap=self.tl, tutar="100", tarih=D(2026, 9, 10), satirlar=[{"hesap_kodu": "770.01"}],
                                       kullanici=self.su)

    def _tl2(self):
        _hesap("102.01.0002", "TL 2")
        return banka_hesap_olustur(banka=self.tl.banka, ad="tl2", para_birimi="TRY", muhasebe_kodu="102.01.0002")


class HesapDuzenleEkranTest(HesapDuzenleBase):
    def setUp(self):
        self.client.force_login(self.su)

    def post(self, satirlar, tutar="2.248,15", tarih="2026-09-10", aciklama=""):
        v = {"tutar": tutar, "tarih": tarih, "kur": "", "aciklama": aciklama, "form-TOTAL_FORMS": str(len(satirlar)),
             "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, (hesap, tutar_s, ack) in enumerate(satirlar):
            v.update({f"form-{i}-hesap": hesap, f"form-{i}-tutar": tutar_s, f"form-{i}-yatirim_projesi": "", f"form-{i}-aciklama": ack})
        return v

    def test_yeni_kayit_ekraninda_bolme(self):
        url = reverse("core:banka_hareket_ekle", args=[self.tl.pk, "hesaba_odeme"])
        r = self.client.post(url, self.post([("360.20", "1.308,45", ""), ("770.11", "939,70", "damga")]))
        self.assertEqual(r.status_code, 302)
        f = YevmiyeFisi.objects.filter(kaynak="BANKA").latest("pk")
        self.assertEqual(self.satirlar(f), [("102.01.0001", "A", Dc("2248.15")), ("360.20", "B", Dc("1308.45")), ("770.11", "B", Dc("939.70"))])
        r = self.client.post(url, self.post([("360.20", "1.308,45", ""), ("770.11", "939,00", "")]))      # toplam tutmuyor
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")

    def test_duzenle_ekrani_listede_link_doldurma_ve_kayit(self):
        f = self.odeme()
        url = reverse("core:banka_hareket_duzenle", args=[self.tl.pk, f.pk])
        liste = self.client.get(reverse("core:banka_hesap_detay", args=[self.tl.pk]) + "?baslangic=2026-09-01&bitis=2026-09-30")
        self.assertContains(liste, url)                                                    # listede Düzenle linki
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Değişiklikleri Kaydet")
        self.assertContains(r, "2.248,15")                                                 # mevcut tutar dolu
        r = self.client.post(url, self.post([("360.20", "1.308,45", "GV"), ("770.11", "939,70", "DV")], aciklama="muhtasar"))
        self.assertEqual(r.status_code, 302)
        f.refresh_from_db()
        self.assertEqual(self.satirlar(f)[1:], [("360.20", "B", Dc("1308.45")), ("770.11", "B", Dc("939.70"))])
        r = self.client.get(url)                                                           # bölünmüş satırlar dolu gelir
        self.assertContains(r, "939,70")
        self.assertContains(r, "1.308,45")
        r = self.client.post(url, self.post([("360.20", "1.308,45", ""), ("770.11", "100,00", "")]))
        self.assertEqual(r.status_code, 200)                                               # hata → formda, fiş aynı
        self.assertEqual(self.satirlar(f)[1:], [("360.20", "B", Dc("1308.45")), ("770.11", "B", Dc("939.70"))])

    def test_cariye_odeme_duzenleme_yolu_etkilenmez(self):
        f = bh.hareket_olustur(banka_hesap=self.tl, tip="cari_odeme", karsi=self.satici, tutar="100", tarih=D(2026, 9, 10), kullanici=self.su)
        r = self.client.get(reverse("core:banka_hareket_duzenle", args=[self.tl.pk, f.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Banka Hareketini Düzenle")                                  # eski cari düzenleme ekranı
        v = self.client.get(reverse("core:banka_hareket_duzenle", args=[self.tl.pk, f.pk]))
        self.assertNotContains(v, "Karşı Hesap Satırları")
