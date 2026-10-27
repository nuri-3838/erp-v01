"""Kredi kartı Harcama/İade: karşı gider hesabı SATIRLARI (bölünebilir; banka "Hesaba Ödeme" deseni).
Satırlar toplamı = kart tutarı; tek satır bugünkü gibi; düzenlemede 309 bakiyesi + taksit planı değişmez; 258'de proje satır bazında zorunlu."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, HesapPlani, Kur, YevmiyeFisi, YevmiyeSatir
from core.services import kk_donem
from core.services import kredi_karti_hareket as kk
from core.services.finans import kredi_karti_olustur
from core.services.yatirim_projesi import proje_olustur, proje_toplami
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def formset_verisi(*satirlar, ilk=0):
    """(hesap, tutar, proje, aciklama) satırlarından formset POST verisi."""
    veri = {"form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": str(ilk)}
    for i, s in enumerate(satirlar):
        hesap, tutar, proje, ack = (tuple(s) + ("", "", ""))[:4] if len(s) < 4 else s
        veri.update({f"form-{i}-hesap": hesap, f"form-{i}-tutar": tutar, f"form-{i}-yatirim_projesi": proje, f"form-{i}-aciklama": ack})
    return veri


class KartGiderSatirTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 8, 14), usd_alis=Dc("33.333333"))
        _hesap("309.02", "ZİRAAT KART", kalem="KVYK")
        _hesap("309.03", "USD KART", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        for kod in ("360.10.0210", "360.10.0510", "360.10.0710", "770.01"):
            _hesap(kod, "GİDER " + kod, kalem="C", grup="GELIR_TABLOSU")
        _hesap("320.01", "SATICI")
        cls.su = User.objects.create_superuser("kgs", password="x")
        cls.kart = kredi_karti_olustur(ad="ziraat", para_birimi="TRY", muhasebe_kodu="309.02", kullanici=cls.su,
                                       kesim_gunu=10, son_odeme_gunu=20)
        cls.usd_kart = kredi_karti_olustur(ad="dolar", para_birimi="USD", muhasebe_kodu="309.03", kullanici=cls.su)
        cls.proje = proje_olustur(ad="araç", kullanici=cls.su)
        cls.h = {k: HesapPlani.objects.get(hesap_kodu=k) for k in ("360.10.0210", "360.10.0510", "360.10.0710", "770.01", "258")}

    def satir(self, kod, tutar=None, proje=None, ack=""):
        return {"hesap_kodu": kod, "tutar": tutar, "aciklama": ack, "yatirim_projesi_id": proje}

    def kdv2(self, tutar="3000", kart=None, tarih=D(2026, 8, 14), **kw):
        """KDV2 ödemesi: 360.10.0210 / 0510 / 0710'a bölünmüş harcama."""
        kart = kart or self.kart
        t = Dc(tutar)
        a, b = (t * Dc("0.4")).quantize(Dc("0.01")), (t * Dc("0.3")).quantize(Dc("0.01"))
        satirlar = [self.satir("360.10.0210", a), self.satir("360.10.0510", b), self.satir("360.10.0710", t - a - b)]
        return kk.harcama_olustur(kart=kart, karsi=None, tutar=tutar, tarih=tarih, aciklama="KDV2 ÖDEMESİ", kullanici=self.su,
                                  gider_satirlari=satirlar, **kw)

    def bakiye(self, kart):
        """Kart hesabının ALACAK yönlü bakiyesi (TL)."""
        a = YevmiyeSatir.objects.filter(hesap_id=kart.muhasebe_id, silindi=False, fis__silindi=False).aggregate(
            b=Sum("borc"), a=Sum("alacak"))
        return (a["a"] or Dc("0")) - (a["b"] or Dc("0"))

    def dokum(self, fis):
        return sorted((s.hesap_id, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False, ana_satir__isnull=True))

    def dengeli(self, fis):
        t = fis.satirlar.filter(silindi=False).aggregate(b=Sum("borc"), a=Sum("alacak"))
        self.assertEqual(t["b"], t["a"])


class ServisOlusturTest(KartGiderSatirTaban):
    def test_tek_satir_bugunku_fisle_ayni(self):
        eski = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.h["770.01"], tutar="1000", tarih=D(2026, 8, 14), kullanici=self.su)
        yeni = kk.harcama_olustur(kart=self.kart, karsi=None, tutar="1000", tarih=D(2026, 8, 14), kullanici=self.su,
                                  gider_satirlari=[self.satir("770.01", None)])           # tutar boş → tamamı
        self.assertEqual(self.dokum(yeni), self.dokum(eski))
        self.assertEqual(yeni.aciklama, eski.aciklama)                                   # "KREDİ KARTI HARCAMA - <hesap adı>"
        self.assertEqual(self.bakiye(self.kart), Dc("2000.00"))

    def test_cok_satir_fis_ve_309_alacagi(self):
        f = self.kdv2("3000")
        self.assertEqual(self.dokum(f), [("309.02", Dc("0.00"), Dc("3000.00")), ("360.10.0210", Dc("1200.00"), Dc("0.00")),
                                         ("360.10.0510", Dc("900.00"), Dc("0.00")), ("360.10.0710", Dc("900.00"), Dc("0.00"))])
        self.dengeli(f)
        self.assertEqual(self.bakiye(self.kart), Dc("3000.00"))
        self.assertEqual(f.kredi_karti_id, self.kart.pk)
        self.assertIn("KDV2", f.aciklama)

    def test_aciklamasiz_cok_satir_otomatik_aciklama(self):
        f = kk.harcama_olustur(kart=self.kart, karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "40"), self.satir("360.10.0210", "60")])
        self.assertEqual(f.aciklama, "KREDİ KARTI HARCAMA - 2 KALEM")

    def test_satir_aciklamasi_ve_iade(self):
        f = kk.hareket_olustur(kart=self.kart, tip="iade", karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "40", ack="a"), self.satir("360.10.0210", "60", ack="b")])
        s = f.satirlar.get(hesap_id="360.10.0210")
        self.assertEqual((s.alacak, s.aciklama), (Dc("60.00"), "B"))                      # iade: gider ALACAK, satır açıklaması (TR büyük harf) korunur
        self.assertEqual(f.satirlar.get(hesap_id="309.02").borc, Dc("100.00"))            # iade: kart borç (borç azalır)
        self.dengeli(f)

    def test_toplam_kart_tutarina_esit_degilse_reddedilir(self):
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
            kk.harcama_olustur(kart=self.kart, karsi=None, tutar="1000", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "400"), self.satir("360.10.0210", "500")])
        self.assertIn("eşit olmalı", str(e.exception))
        self.assertIn("100,00", str(e.exception))                                          # fark
        self.assertEqual(YevmiyeFisi.objects.count(), n)
        self.assertEqual(self.bakiye(self.kart), Dc("0"))

    def test_258_proje_satir_bazinda_zorunlu(self):
        n = YevmiyeFisi.objects.count()
        with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
            kk.harcama_olustur(kart=self.kart, karsi=None, tutar="1000", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "400"), self.satir("258", "600")])
        self.assertIn("Satır 2", str(e.exception))
        self.assertIn("258 hesabı için yatırım projesi", str(e.exception))
        self.assertEqual(YevmiyeFisi.objects.count(), n)
        f = kk.harcama_olustur(kart=self.kart, karsi=None, tutar="1000", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "400"), self.satir("258", "600", self.proje.pk)])
        self.assertEqual(f.satirlar.get(hesap_id="258").yatirim_projesi_id, self.proje.pk)
        self.assertIsNone(f.satirlar.get(hesap_id="770.01").yatirim_projesi_id)
        self.assertEqual(proje_toplami(self.proje), Dc("600.00"))

    def test_proje_258_disi_satirda_secilemez(self):
        with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
            kk.harcama_olustur(kart=self.kart, karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "100", self.proje.pk)])
        self.assertIn("yalnız 258", str(e.exception))

    def test_kartin_kendi_hesabi_ve_gecersiz_hesap_ve_bos_satir(self):
        for satirlar, parca in (([self.satir("309.02", "100")], "kartın kendi hesabı"), ([self.satir("999.99", "100")], "bulunamadı"),
                                ([], "En az bir")):
            with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
                kk.harcama_olustur(kart=self.kart, karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su, gider_satirlari=satirlar)
            self.assertIn(parca, str(e.exception))

    def test_odeme_tipinde_gider_satiri_girilemez(self):
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            kk.hareket_olustur(kart=self.kart, tip="odeme", karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("770.01", "100")])

    def test_doviz_kart_bolunmus_harcama_dengeli_kurus_farki_son_satirda(self):
        # 100 USD x 33,333333 = 3.333,33 TL; 3 eşit olmayan satırın TL toplamı yuvarlamada 3.333,33'ten sapar → fark son satıra yazılır
        f = kk.harcama_olustur(kart=self.usd_kart, karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("360.10.0210", "33.33"), self.satir("360.10.0510", "33.33"),
                                                self.satir("360.10.0710", "33.34")])
        self.dengeli(f)
        kart_s = f.satirlar.get(hesap_id="309.03", silindi=False, ana_satir__isnull=True)
        gider_tl = sum((s.borc for s in f.satirlar.filter(silindi=False, ana_satir__isnull=True, hesap__hesap_kodu__startswith="360")), Dc("0"))
        self.assertEqual(kart_s.alacak, Dc("3333.33"))
        self.assertEqual(gider_tl, kart_s.alacak)
        self.assertTrue(all(s.islem_pb == "USD" for s in f.satirlar.filter(silindi=False, ana_satir__isnull=True)))


class ServisGuncelleTest(KartGiderSatirTaban):
    def test_bolunmus_fis_yeniden_dagitilir_309_bakiyesi_ve_no_ayni(self):
        f = self.kdv2("3000")
        no, once = (f.yil, f.fis_no), self.bakiye(self.kart)
        kk.hareket_guncelle(fis=f, kart=self.kart, aciklama="KDV2 DÜZELTİLDİ", kullanici=self.su,
                            gider_satirlari=[self.satir("360.10.0210", "1000"), self.satir("360.10.0510", "1000"),
                                             self.satir("360.10.0710", "500"), self.satir("770.01", "500", ack="fark")])
        f.refresh_from_db()
        self.assertEqual((f.yil, f.fis_no), no)
        self.assertEqual(f.aciklama, "KDV2 DÜZELTİLDİ")
        self.assertEqual(self.dokum(f), [("309.02", Dc("0.00"), Dc("3000.00")), ("360.10.0210", Dc("1000.00"), Dc("0.00")),
                                         ("360.10.0510", Dc("1000.00"), Dc("0.00")), ("360.10.0710", Dc("500.00"), Dc("0.00")),
                                         ("770.01", Dc("500.00"), Dc("0.00"))])
        self.assertEqual(self.bakiye(self.kart), once)
        self.dengeli(f)

    def test_tek_satiri_bolmek_ve_geri_birlestirmek(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.h["770.01"], tutar="2000", tarih=D(2026, 8, 14), kullanici=self.su)
        once = self.bakiye(self.kart)
        bilgi = kk.duzenleme_bilgisi(f, self.kart)
        self.assertEqual(len(bilgi["karsi_satirlari"]), 1)                                  # mevcut kayıt tek satır olarak açılır
        self.assertTrue(bilgi["gider_duzenlenebilir"])
        kk.hareket_guncelle(fis=f, kart=self.kart, kullanici=self.su,
                            gider_satirlari=[self.satir("360.10.0210", "1500"), self.satir("360.10.0510", "500")])
        self.assertEqual(len(kk.duzenleme_bilgisi(f, self.kart)["karsi_satirlari"]), 2)
        kk.hareket_guncelle(fis=f, kart=self.kart, kullanici=self.su, gider_satirlari=[self.satir("770.01", None)])
        self.assertEqual(self.dokum(f), [("309.02", Dc("0.00"), Dc("2000.00")), ("770.01", Dc("2000.00"), Dc("0.00"))])
        self.assertEqual(self.bakiye(self.kart), once)

    def test_kart_tutari_degistirilemez_toplam_uymazsa_fis_degismez(self):
        f = self.kdv2("3000")
        once = self.dokum(f)
        with self.assertRaises(kk.KrediKartiHareketHatasi) as e:
            kk.hareket_guncelle(fis=f, kart=self.kart, kullanici=self.su,
                                gider_satirlari=[self.satir("360.10.0210", "1000"), self.satir("360.10.0510", "1000")])
        self.assertIn("eşit olmalı", str(e.exception))
        self.assertEqual(self.dokum(f), once)

    def test_bolunmus_hareket_yalniz_aciklama_degisir_satirlar_aynen_kalir(self):
        f = self.kdv2("3000")
        once = self.dokum(f)
        kk.hareket_guncelle(fis=f, kart=self.kart, aciklama="sadece açıklama", kullanici=self.su)
        self.assertEqual(self.dokum(f), once)
        f.refresh_from_db()
        self.assertEqual(f.aciklama, "SADECE AÇIKLAMA")

    def test_taksit_plani_bolunmus_duzenlemede_degismez(self):
        f = self.kdv2("3000", taksit_adedi=3, ilk_vade=D(2026, 9, 20))
        plan = kk_donem.aktif_plan(f)
        once = (plan.pk, plan.taksit_adedi, plan.ilk_vade, plan.toplam_tutar)
        kk.hareket_guncelle(fis=f, kart=self.kart, kullanici=self.su,
                            gider_satirlari=[self.satir("360.10.0210", "3000")])
        plan2 = kk_donem.aktif_plan(f)
        self.assertEqual((plan2.pk, plan2.taksit_adedi, plan2.ilk_vade, plan2.toplam_tutar), once)
        self.assertEqual(self.bakiye(self.kart), Dc("3000.00"))

    def test_cari_hareketi_gider_satiri_ile_degistirilemez(self):
        from core.models import Cari
        cari = Cari.objects.create(kod="C1", unvan="SATICI", muhasebe_kodu="320.01")
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=cari, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su)
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            kk.hareket_guncelle(fis=f, kart=self.kart, kullanici=self.su, gider_satirlari=[self.satir("770.01", "100")])

    def test_doviz_kart_bolunmus_duzenleme_dengeli_ve_kart_tl_ayni(self):
        f = kk.harcama_olustur(kart=self.usd_kart, karsi=None, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su,
                               gider_satirlari=[self.satir("360.10.0210", "50"), self.satir("360.10.0510", "50")])
        once = self.bakiye(self.usd_kart)
        kk.hareket_guncelle(fis=f, kart=self.usd_kart, kullanici=self.su,
                            gider_satirlari=[self.satir("360.10.0210", "33.33"), self.satir("360.10.0510", "33.33"),
                                             self.satir("770.01", "33.34")])
        self.dengeli(f)
        self.assertEqual(self.bakiye(self.usd_kart), once)


class EkranTest(KartGiderSatirTaban):
    def setUp(self):
        EkranYetki.objects.create(kullanici=self.su, ekran_kod="kredi_karti")
        self.client.force_login(self.su)
        self.ekle = reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"])

    def veri(self, *satirlar, **ek):
        return {"tutar": "3.000,00", "tarih": "2026-08-14", "aciklama": "KDV2", "taksit_adedi": "1", **formset_verisi(*satirlar), **ek}

    def test_ekle_formunda_satirlar_ve_satir_ekle(self):
        r = self.client.get(self.ekle)
        self.assertContains(r, "Karşı Hesap Satırları")
        self.assertContains(r, "+ Satır ekle")
        self.assertContains(r, 'name="form-0-hesap"')
        self.assertNotContains(r, 'name="gider"')                                            # tek gider alanı kalktı
        self.assertContains(r, 'name="cari"')

    def test_ekle_cok_satirli(self):
        r = self.client.post(self.ekle, self.veri(("360.10.0210", "1.200,00", "", ""), ("360.10.0510", "900,00", "", ""),
                                                 ("360.10.0710", "900,00", "", "")))
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        f = YevmiyeFisi.objects.get(kredi_karti=self.kart)
        self.assertEqual(self.dokum(f), [("309.02", Dc("0.00"), Dc("3000.00")), ("360.10.0210", Dc("1200.00"), Dc("0.00")),
                                         ("360.10.0510", Dc("900.00"), Dc("0.00")), ("360.10.0710", Dc("900.00"), Dc("0.00"))])
        self.assertEqual(self.bakiye(self.kart), Dc("3000.00"))

    def test_ekle_tek_satir_tutarsiz(self):
        r = self.client.post(self.ekle, self.veri(("770.01", "", "", "")))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.dokum(YevmiyeFisi.objects.get(kredi_karti=self.kart)),
                         [("309.02", Dc("0.00"), Dc("3000.00")), ("770.01", Dc("3000.00"), Dc("0.00"))])

    def test_ekle_toplam_uymazsa_hata_ve_kayit_yok(self):
        r = self.client.post(self.ekle, self.veri(("360.10.0210", "1.200,00", "", ""), ("360.10.0510", "900,00", "", "")))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")
        self.assertContains(r, 'value="360.10.0210" selected')                               # satırlar hata sonrası korunur
        self.assertFalse(YevmiyeFisi.objects.filter(kredi_karti=self.kart).exists())

    def test_ekle_258_satirda_projesiz_hata(self):
        r = self.client.post(self.ekle, self.veri(("770.01", "1.000,00", "", ""), ("258", "2.000,00", "", "")))
        self.assertContains(r, "258 hesabı için yatırım projesi seçilmelidir")
        self.assertFalse(YevmiyeFisi.objects.filter(kredi_karti=self.kart).exists())
        r = self.client.post(self.ekle, self.veri(("770.01", "1.000,00", "", ""), ("258", "2.000,00", self.proje.pk, "")))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(YevmiyeFisi.objects.get(kredi_karti=self.kart).satirlar.get(hesap_id="258").yatirim_projesi_id, self.proje.pk)

    def test_ekle_cari_ve_satir_ya_da_hicbiri_reddedilir(self):
        from core.models import Cari
        cari = Cari.objects.create(kod="C1", unvan="SATICI", muhasebe_kodu="320.01")
        r = self.client.post(self.ekle, self.veri(("770.01", "", "", ""), cari=cari.pk))
        self.assertContains(r, "yalnız birini girin")
        r = self.client.post(self.ekle, self.veri(("", "", "", "")))
        self.assertContains(r, "Karşı taraf seçin")
        self.assertFalse(YevmiyeFisi.objects.filter(kredi_karti=self.kart).exists())
        r = self.client.post(self.ekle, {"tutar": "100", "tarih": "2026-08-14", "cari": cari.pk, "taksit_adedi": "1",
                                         **formset_verisi(("", "", "", ""))})                 # cari + boş satır = cari harcaması
        self.assertEqual(r.status_code, 302)

    def duzenle(self, fis):
        return reverse("core:kredi_karti_hareket_duzenle", args=[self.kart.pk, fis.pk])

    def test_duzenle_bolunmus_satirlar_acilir_ve_kaydedilir(self):
        f = self.kdv2("3000")
        r = self.client.get(self.duzenle(f))
        self.assertContains(r, "Karşı Hesap Satırları")
        self.assertContains(r, 'name="form-2-hesap"')                                        # 3 satır
        self.assertNotContains(r, 'name="form-3-hesap"')
        self.assertContains(r, 'data-toplam="3000.00"')                                      # kart tutarı sabit
        self.assertNotContains(r, 'name="tutar"')                                            # kart tutarı değiştirilemez
        once = self.bakiye(self.kart)
        r = self.client.post(self.duzenle(f), {"aciklama": "KDV2", **formset_verisi(("360.10.0210", "2.000,00", "", ""),
                                                                                    ("360.10.0510", "1.000,00", "", ""), ilk=2)})
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        self.assertEqual(self.dokum(f), [("309.02", Dc("0.00"), Dc("3000.00")), ("360.10.0210", Dc("2000.00"), Dc("0.00")),
                                         ("360.10.0510", Dc("1000.00"), Dc("0.00"))])
        self.assertEqual(self.bakiye(self.kart), once)

    def test_duzenle_tek_satir_kayit_tek_satir_acilir(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.h["770.01"], tutar="500", tarih=D(2026, 8, 14), kullanici=self.su)
        r = self.client.get(self.duzenle(f))
        self.assertContains(r, 'name="form-0-hesap"')
        self.assertNotContains(r, 'name="form-1-hesap"')
        self.assertContains(r, 'value="770.01" selected')

    def test_duzenle_toplam_uymazsa_hata_fis_degismez(self):
        f = self.kdv2("3000")
        once = self.dokum(f)
        r = self.client.post(self.duzenle(f), {"aciklama": "x", **formset_verisi(("360.10.0210", "1.000,00", "", ""), ilk=1)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")
        self.assertEqual(self.dokum(f), once)

    def test_duzenle_258_satirda_proje_zorunlu(self):
        f = self.kdv2("3000")
        r = self.client.post(self.duzenle(f), {"aciklama": "x", **formset_verisi(("258", "3.000,00", "", ""), ilk=1)})
        self.assertContains(r, "258 hesabı için yatırım projesi seçilmelidir")
        self.assertEqual(self.bakiye(self.kart), Dc("3000.00"))

    def test_duzenle_cari_hareketinde_satir_bolumu_yok(self):
        from core.models import Cari
        cari = Cari.objects.create(kod="C1", unvan="SATICI", muhasebe_kodu="320.01")
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=cari, tutar="100", tarih=D(2026, 8, 14), kullanici=self.su)
        r = self.client.get(self.duzenle(f))
        self.assertNotContains(r, "Karşı Hesap Satırları")
        r = self.client.post(self.duzenle(f), {"aciklama": "yeni"})
        self.assertEqual(r.status_code, 302)

    def test_faiz_kisayolu_ilk_satiri_doldurur(self):
        r = self.client.get(self.ekle + "?gider=770.01&tarih=2026-08-26&aciklama=KART%20FAİZİ")
        self.assertContains(r, 'value="770.01" selected')
