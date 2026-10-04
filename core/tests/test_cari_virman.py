"""Cariler arası virman: her iki yön, iki ekstrede görünür, düzenle (her iki cariden), kalıcı sil (yalnız süper kullanıcı), döviz cari,
ekranlar; Kesinti davranışı değişmez."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, EkranYetki, Kur, SilmeKaydi, YevmiyeFisi, YevmiyeSatir
from core.services import cari_kesinti as ck
from core.services import cari_virman as cv
from core.services import hesap_plani as hp
from core.services import raporlar
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class VirmanBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 7, 13), D(2026, 7, 14)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"), eur_alis=Dc("44"))
        _hesap("102.01.0001", "TL BANKA")
        _hesap("320.30.0038", "KAYGUN", kalem="KVYK")
        _hesap("320.10.0001", "FORMAL USD", kalem="KVYK")
        _hesap("120.01", "MÜŞTERİ")
        _hesap("500", "SERMAYE", kalem="OZK")
        hp.hesap_olustur(kod="500.10", ad="ÖDENMİŞ SERMAYE", ust_kodu="500")
        hp.hesap_olustur(kod="500.10.0001", ad="NURİ ÖZER", ust_kodu="500.10")
        cls.su = User.objects.create_superuser("cvs", password="x")
        cls.sade = User.objects.create_user("cvd", password="x")
        EkranYetki.objects.create(kullanici=cls.sade, ekran_kod="cariler")
        cls.kaygun = Cari.objects.create(kod="320-30-0038", unvan="KAYGUN", muhasebe_kodu="320.30.0038", para_birimi="TRY")
        cls.nuri = Cari.objects.create(kod="500-10-0001", unvan="NURİ ÖZER", muhasebe_kodu="500.10.0001", para_birimi="TRY")
        cls.formal = Cari.objects.create(kod="320-10-0001", unvan="FORMAL", muhasebe_kodu="320.10.0001", para_birimi="USD")
        cls.musteri = Cari.objects.create(kod="120-01", unvan="MÜŞTERİ", muhasebe_kodu="120.01", para_birimi="TRY")
        # Halkbank → Kaygun 75.500 TL (fiş 2026/488 benzeri): Kaygun BORÇ 75.500
        from core.services.yevmiye import SatirGirdi, fis_olustur
        fis_olustur(tarih=D(2026, 7, 13), aciklama="HALKBANK KAYGUN", satirlar=[
            SatirGirdi(hesap_kodu="320.30.0038", taraf="B", islem_tutari="75500"),
            SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="75500")])

    def _satirlar(self, fis):
        return {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak) for s in fis.satirlar.filter(silindi=False)}


class VirmanServisTest(VirmanBase):
    def test_kaygun_ornegi_kaygun_alacak_nuri_borc(self):
        f = cv.virman_olustur(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="75500", yon="alacak", kullanici=self.su)
        self.assertEqual(self._satirlar(f), {("320.30.0038", "A"): Dc("75500.00"), ("500.10.0001", "B"): Dc("75500.00")})
        self.assertEqual((f.kaynak, f.cari_id, f.karsi_cari_id), ("CARI_VIRMAN", self.kaygun.pk, self.nuri.pk))
        self.assertEqual(_bak("320.30.0038"), Dc("0"))                                       # Kaygun borcu kapandı
        self.assertEqual(_bak("500.10.0001"), Dc("75500.00"))
        for kod in ("320.30.0038", "500.10.0001"):                                           # iki ekstrede de görünür
            e = raporlar.ekstre_devirli(kod, D(2026, 1, 1), D(2026, 12, 31))
            self.assertTrue(any(s.fis_pk == f.pk for s in e.satirlar))

    def test_ters_yon_bu_cari_borc_karsi_alacak(self):
        f = cv.virman_olustur(cari=self.nuri, karsi_cari=self.musteri, tarih=D(2026, 7, 13), tutar="1.000,50", yon="borc", kullanici=self.su)
        self.assertEqual(self._satirlar(f), {("500.10.0001", "B"): Dc("1000.50"), ("120.01", "A"): Dc("1000.50")})

    def test_duzenle_her_iki_cariden_ve_bilgi(self):
        f = cv.virman_olustur(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="75500", yon="alacak", kullanici=self.su)
        b = cv.duzenleme_bilgisi(f, self.kaygun)
        self.assertEqual((b["karsi_cari"], b["yon"], b["tutar"]), (self.nuri, "alacak", Dc("75500.00")))
        b = cv.duzenleme_bilgisi(f, self.nuri)                                               # karşı carinin bakış açısı
        self.assertEqual((b["karsi_cari"], b["yon"]), (self.kaygun, "borc"))
        cv.virman_guncelle(fis=f, cari=self.nuri, karsi_cari=self.kaygun, tarih=D(2026, 7, 14), tutar="80000", yon="borc", kullanici=self.su)
        f.refresh_from_db()
        self.assertEqual(self._satirlar(f), {("500.10.0001", "B"): Dc("80000.00"), ("320.30.0038", "A"): Dc("80000.00")})
        self.assertEqual((f.tarih, f.cari_id, f.karsi_cari_id), (D(2026, 7, 14), self.nuri.pk, self.kaygun.pk))
        with self.assertRaises(cv.CariVirmanHatasi):                                          # ilgisiz cari
            cv.duzenleme_bilgisi(f, self.musteri)

    def test_sil_yalniz_superuser_fis_de_silinir_denetim_kaydi(self):
        f = cv.virman_olustur(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="75500", yon="alacak", kullanici=self.su)
        with self.assertRaises(cv.CariVirmanHatasi):
            cv.virman_sil(fis=f, cari=self.kaygun, kullanici=self.sade)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        cv.virman_sil(fis=f, cari=self.nuri, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=f.pk).exists())
        self.assertEqual(_bak("500.10.0001"), Dc("0"))
        self.assertEqual(SilmeKaydi.objects.count(), 1)
        self.assertFalse(YevmiyeFisi.objects.filter(silindi=True).exists())                   # "iptal" durumu yok

    def test_gecersiz_girdiler(self):
        for kw in (dict(karsi_cari=self.kaygun), dict(tutar="0"), dict(yon="x")):
            args = dict(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="10", yon="alacak")
            args.update(kw)
            with self.assertRaises(cv.CariVirmanHatasi):
                cv.virman_olustur(**args)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="CARI_VIRMAN").count(), 0)

    def test_doviz_cari_tl_tutar_dovize_cevrilir(self):
        f = cv.virman_olustur(cari=self.formal, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="8400", yon="borc", kullanici=self.su)
        s = f.satirlar.get(hesap_id="320.10.0001", ana_satir__isnull=True)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("USD", Dc("210.00"), Dc("40.000000")))     # 8.400 / 40
        self.assertEqual(f.satirlar.get(hesap_id="500.10.0001").alacak, Dc("8400.00"))
        f2 = cv.virman_olustur(cari=self.formal, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="1000", yon="borc",
                               sayilan_pb="TRY", kullanici=self.su)
        self.assertEqual(f2.satirlar.get(hesap_id="320.10.0001").islem_pb, "TRY")

    def test_kesinti_davranisi_degismedi(self):
        f = ck.kesinti_olustur(cari=self.kaygun, tarih=D(2026, 7, 13), tutar="10", karsi_cari=self.nuri, kullanici=self.su)
        self.assertEqual((f.kaynak, f.cari_id, f.karsi_cari_id), ("CARI_KESINTI", self.kaygun.pk, None))
        self.assertEqual(self._satirlar(f), {("320.30.0038", "B"): Dc("10.00"), ("500.10.0001", "A"): Dc("10.00")})


class VirmanEkranTest(VirmanBase):
    def test_ekran_olustur_ekstrede_duzenle_sil_ve_fis_kilidi(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:cari_detay", args=[self.kaygun.pk]))
        self.assertContains(r, "🔁 Virman")
        r = self.client.get(reverse("core:cari_virman_ekle", args=[self.kaygun.pk]))
        self.assertContains(r, "Bu cari ALACAK / karşı cari BORÇ")
        r = self.client.post(reverse("core:cari_virman_ekle", args=[self.kaygun.pk]), {
            "tarih": "2026-07-13", "tutar": "75.500,00", "yon": "alacak", "karsi_cari": self.nuri.pk, "aciklama": ""})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.kaygun.pk]))
        f = YevmiyeFisi.objects.get(kaynak="CARI_VIRMAN")
        for cari in (self.kaygun, self.nuri):                                                # her iki ekstrede Düzenle/Sil
            r = self.client.get(reverse("core:cari_ekstresi", args=[cari.pk]), {"baslangic": "2026-01-01", "bitis": "2026-12-31"})
            self.assertContains(r, reverse("core:cari_virman_duzenle", args=[cari.pk, f.pk]))
            self.assertContains(r, reverse("core:cari_virman_sil", args=[cari.pk, f.pk]))
        r = self.client.post(reverse("core:cari_virman_duzenle", args=[self.nuri.pk, f.pk]), {
            "tarih": "2026-07-13", "tutar": "70.000,00", "yon": "borc", "karsi_cari": self.kaygun.pk, "aciklama": "iade"})
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.nuri.pk]))
        self.assertEqual(_bak("500.10.0001"), Dc("70000.00"))
        r = self.client.get(reverse("core:fis_duzenle", args=[f.pk]))                          # ham fiş ekranı kilitli
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse("core:cari_virman_sil", args=[self.kaygun.pk, f.pk]))
        self.assertRedirects(r, reverse("core:cari_ekstresi", args=[self.kaygun.pk]))
        self.assertFalse(YevmiyeFisi.objects.filter(pk=f.pk).exists())

    def test_fis_detayinda_virman_aciklamasi(self):
        f = cv.virman_olustur(cari=self.kaygun, karsi_cari=self.nuri, tarih=D(2026, 7, 13), tutar="100", yon="alacak", kullanici=self.su)
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fis_detay", args=[f.pk]))
        self.assertContains(r, "cari virman hareketinden")
