"""Kredi kartı harcamasında yatırım projesi: 258 gider hesabında proje zorunlu, fişin 258 satırına yazılır;
hareket düzenleme (açıklama / gider hesabı / proje) fişi günceller; kk_proje_duzelt komutu."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from core.management.commands import kk_proje_duzelt as kpd
from core.models import Cari, EkranYetki, Kur, YatirimProjesi, YevmiyeFisi, YevmiyeSatir
from core.services import kredi_karti_hareket as kk
from core.services.finans import kredi_karti_olustur
from core.services.yatirim_projesi import proje_olustur, proje_toplami
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


class KkYatirimProjesiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 4, 3), usd_alis=Dc("40"))
        _hesap("309.02", "ZİRAAT KART", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("770.01", "GİDER", kalem="C", grup="GELIR_TABLOSU")
        _hesap("770.02", "GİDER 2", kalem="C", grup="GELIR_TABLOSU")
        _hesap("320.01", "SATICI")
        cls.su = User.objects.create_superuser("kkp", password="x")
        cls.kart = kredi_karti_olustur(ad="ziraat", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)
        cls.proje = proje_olustur(ad="araç", kullanici=cls.su)
        cls.proje2 = proje_olustur(ad="makine", kullanici=cls.su)
        cls.h258 = __import__("core.models", fromlist=["HesapPlani"]).HesapPlani.objects.get(hesap_kodu="258")
        cls.h770 = __import__("core.models", fromlist=["HesapPlani"]).HesapPlani.objects.get(hesap_kodu="770.01")
        cls.h7702 = __import__("core.models", fromlist=["HesapPlani"]).HesapPlani.objects.get(hesap_kodu="770.02")

    def _harcama(self, gider, proje_id=None, tutar="7000"):
        return kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=gider, tutar=tutar, tarih=D(2026, 4, 3),
                                  aciklama="expertiz", kullanici=self.su, yatirim_projesi_id=proje_id)

    def test_258_projeli_harcama_fisin_258_satirina_proje_yazilir(self):
        f = self._harcama(self.h258, self.proje.pk)
        s = f.satirlar.get(hesap_id="258")
        self.assertEqual((s.borc, s.yatirim_projesi_id), (Dc("7000.00"), self.proje.pk))
        self.assertEqual(proje_toplami(self.proje), Dc("7000.00"))

    def test_258_projesiz_reddedilir_ve_fis_olusmaz(self):
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
            self._harcama(self.h258)
        self.assertIn("258 hesabı için yatırım projesi", str(e.exception))
        self.assertEqual(YevmiyeFisi.objects.count(), n)

    def test_258_disi_hesapta_proje_secilemez_projesiz_normal(self):
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            self._harcama(self.h770, self.proje.pk)
        f = self._harcama(self.h770)
        self.assertIsNone(f.satirlar.get(hesap_id="770.01").yatirim_projesi_id)

    def test_aktiflesmis_proje_reddedilir(self):
        YatirimProjesi.objects.filter(pk=self.proje.pk).update(durum="AKTIFLESTI")
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            self._harcama(self.h258, self.proje.pk)

    def test_taksitli_projeli_harcama(self):
        f = kk.harcama_olustur(kart=self.kart, karsi=self.h258, tutar="600", tarih=D(2026, 4, 3), taksit_adedi=2,
                               ilk_vade=D(2026, 5, 3), yatirim_projesi_id=self.proje.pk, kullanici=self.su)
        self.assertEqual(f.satirlar.get(hesap_id="258").yatirim_projesi_id, self.proje.pk)

    def test_duzenle_aciklama_gider_ve_proje_fisi_gunceller(self):
        f = self._harcama(self.h770)
        kk.hareket_guncelle(fis=f, kart=self.kart, aciklama="yeni açıklama", gider=self.h258,
                            yatirim_projesi_id=self.proje.pk, kullanici=self.su)
        f.refresh_from_db()
        self.assertEqual(f.aciklama, "YENİ AÇIKLAMA")
        s = {x.hesap_id: x for x in f.satirlar.filter(silindi=False)}
        self.assertEqual(set(s), {"309.02", "258"})
        self.assertEqual((s["258"].borc, s["258"].yatirim_projesi_id), (Dc("7000.00"), self.proje.pk))
        self.assertEqual((s["309.02"].alacak, f.tarih), (Dc("7000.00"), D(2026, 4, 3)))     # tutar/tarih aynı
        self.assertEqual(sum(x.borc for x in s.values()), sum(x.alacak for x in s.values()))
        # proje değiştir, sonra 258'den çıkar (proje otomatik boşalır)
        kk.hareket_guncelle(fis=f, kart=self.kart, yatirim_projesi_id=self.proje2.pk, kullanici=self.su)
        self.assertEqual(f.satirlar.get(silindi=False, hesap_id="258").yatirim_projesi_id, self.proje2.pk)
        kk.hareket_guncelle(fis=f, kart=self.kart, gider=self.h7702, kullanici=self.su)
        self.assertEqual(f.satirlar.get(silindi=False, hesap_id="770.02").yatirim_projesi_id, None)

    def test_duzenle_258de_proje_zorunlu_ve_hata_fisi_degistirmez(self):
        f = self._harcama(self.h770)
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            kk.hareket_guncelle(fis=f, kart=self.kart, gider=self.h258, kullanici=self.su)
        self.assertEqual({x.hesap_id for x in f.satirlar.filter(silindi=False)}, {"309.02", "770.01"})

    def test_duzenle_cari_karsi_hesap_degistirilemez(self):
        cari = Cari.objects.create(kod="C1", unvan="SATICI", muhasebe_kodu="320.01")
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=cari, tutar="100", tarih=D(2026, 4, 3),
                               kullanici=self.su)
        self.assertFalse(kk.duzenleme_bilgisi(f, self.kart)["gider_duzenlenebilir"])
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            kk.hareket_guncelle(fis=f, kart=self.kart, gider=self.h770, kullanici=self.su)
        kk.hareket_guncelle(fis=f, kart=self.kart, aciklama="not", kullanici=self.su)      # açıklama serbest
        f.refresh_from_db()
        self.assertEqual(f.aciklama, "NOT")

    def test_ekranlar_ekle_ve_duzenle(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="kredi_karti")
        self.client.force_login(self.su)
        url = reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"])
        self.assertContains(self.client.get(url), "Yatırım projesi")
        veri = {"gider": "258", "tutar": "7.000,00", "tarih": "2026-04-03", "taksit_adedi": "1"}
        r = self.client.post(url, veri)
        self.assertContains(r, "258 hesabı için yatırım projesi seçilmelidir")             # form düzeyinde zorunlu
        veri["yatirim_projesi"] = self.proje.pk
        r = self.client.post(url, veri)
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        fis = YevmiyeFisi.objects.filter(kredi_karti=self.kart).get()
        self.assertEqual(fis.satirlar.get(hesap_id="258").yatirim_projesi_id, self.proje.pk)
        # düzenle
        d_url = reverse("core:kredi_karti_hareket_duzenle", args=[self.kart.pk, fis.pk])
        self.assertContains(self.client.get(d_url), "Kart Hareketini Düzenle")
        self.assertContains(self.client.get(reverse("core:kredi_karti_detay", args=[self.kart.pk]),
                                            {"baslangic": "2026-01-01", "bitis": "2026-12-31"}), d_url)
        r = self.client.post(d_url, {"aciklama": "düzeltildi", "gider": "258", "yatirim_projesi": self.proje2.pk})
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        fis.refresh_from_db()
        self.assertEqual(fis.aciklama, "DÜZELTİLDİ")
        self.assertEqual(fis.satirlar.get(silindi=False, hesap_id="258").yatirim_projesi_id, self.proje2.pk)
        # proje olmadan 258 → ekranda hata, fiş değişmez
        r = self.client.post(d_url, {"aciklama": "x", "gider": "258"})
        self.assertContains(r, "258 hesabı için yatırım projesi seçilmelidir")


class KkProjeDuzeltKomutTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 4, 3), usd_alis=Dc("40"))
        _hesap("309.02", "ZİRAAT KART", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        cls.su = User.objects.create_superuser("kkc", password="x")
        cls.kart = kredi_karti_olustur(ad="ziraat", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su)
        # YP-0008 ROLÜNDEKİ proje: 8. açılan proje (kod YP-0008). pk'si doğaldır — komutun sabit pk=8 varsayımı mock ile bu pk'ye çevrilir.
        for i in range(8):
            p = proje_olustur(ad=f"p{i}", kullanici=cls.su)
        cls.p8 = p

    def _eski_veri(self):
        def ham(no, ack, kaynak, satirlar, kart=None):
            f = YevmiyeFisi.objects.create(yil=2026, fis_no=no, tarih=D(2026, 4, 3), aciklama=ack,
                                           kaynak=kaynak, kredi_karti=kart)
            for hesap, b, a, proje in satirlar:
                YevmiyeSatir.objects.create(fis=f, hesap_id=hesap, borc=b, alacak=a, islem_pb="TRY",
                                            islem_tutari=b or a, islem_kuru=1, yatirim_projesi_id=proje)
            return f
        self.kk = ham(574, "EXPERTİZ - GEZEN ADAM (MEGANE)", "KREDI_KARTI",
                      [("309.02", 0, Dc("7000"), None), ("258", Dc("7000"), 0, None)], kart=self.kart)
        self.mahsup = ham(634, "EXPERTİZ - GEZEN ADAM (MEGANE) - YP-0008 PROJE BAĞLAMA (KK FİŞ 2026/574)", "MANUEL",
                          [("258", Dc("7000"), 0, self.p8.pk), ("258", 0, Dc("7000"), None)])
        self.diger = ham(575, "BAŞKA KK GİDERİ", "KREDI_KARTI",
                         [("309.02", 0, Dc("100"), None), ("258", Dc("100"), 0, None)], kart=self.kart)
        # komutun canlı sabitleri (fiş id 645/705, proje id 8) bu testin gerçek pk'lerine çevrilir; fiş no/açıklama sabitleri aynen kalır
        for patcher in (mock.patch.object(kpd, "KK_FIS", (self.kk.pk,) + tuple(kpd.KK_FIS[1:])),
                        mock.patch.object(kpd, "MAHSUP_FIS", (self.mahsup.pk,) + tuple(kpd.MAHSUP_FIS[1:])),
                        mock.patch.object(kpd, "PROJE_PK", self.p8.pk)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _komut(self, *ek):
        out = StringIO()
        call_command("kk_proje_duzelt", *ek, stdout=out)
        return out.getvalue()

    def test_dry_run_yazmaz_uygula_projeyi_yazar_mahsubu_siler_digerlerini_raporlar(self):
        from core.models import SilmeKaydi
        self._eski_veri()
        once = proje_toplami(self.p8)
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=self.mahsup.pk).exists())
        self.assertIsNone(YevmiyeSatir.objects.get(fis_id=self.kk.pk, hesap_id="258").yatirim_projesi_id)
        self.assertIn("BAŞKA KK GİDERİ", c)                                    # diğer projesiz KK 258 raporlandı
        c = self._komut("--uygula")
        self.assertIn("UYGULANDI", c)
        self.assertEqual(YevmiyeSatir.objects.get(fis_id=self.kk.pk, hesap_id="258").yatirim_projesi_id, self.p8.pk)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=self.mahsup.pk).exists())
        self.assertIsNone(YevmiyeSatir.objects.get(fis_id=self.diger.pk, hesap_id="258").yatirim_projesi_id)   # değiştirilmedi
        self.assertEqual(proje_toplami(self.p8), once)                          # proje toplamı aynı
        self.assertIn("DENGEDE", c)
        self.assertEqual(SilmeKaydi.objects.get().veri["temizlik"], "kk_proje_duzelt")

    def test_uymayan_veri_dokunulmaz(self):
        self._eski_veri()
        YevmiyeFisi.objects.filter(pk=self.kk.pk).update(aciklama="BAŞKA")
        c = self._komut("--uygula")
        self.assertIn("DOKUNULMAZ", c)
        self.assertTrue(YevmiyeFisi.objects.filter(pk=self.mahsup.pk).exists())            # mahsup de silinmez
