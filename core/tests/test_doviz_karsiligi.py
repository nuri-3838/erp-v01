"""Banka (cari ödeme) ve kredi kartı (harcama) hareketlerinde döviz karşılığı: sayılan para birimi + sayılan döviz tutarı → kur = TL / döviz.
Yeni kayıtta ve Düzenle'de; fiş yeniden yazılır, kur farkı motoru çalışır, silme/iptal yok."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import YevmiyeFisi
from core.services import banka_hareket as bh
from core.services import doviz_cari
from core.services import kredi_karti_hareket as kk
from core.tests.test_doviz_cari_odeme import DovizCariBase

D = datetime.date
Dc = Decimal


class _Yardimci:
    def _s(self, fis, kod):
        return fis.satirlar.get(hesap_id=kod, silindi=False)


class YeniKayitDovizTutariTest(_Yardimci, DovizCariBase):
    def test_banka_cari_odeme_doviz_tutari_kuru_belirler(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="8400", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_pb="USD", sayilan_doviz="190")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("USD", Dc("190.00"), Dc("44.210526")))   # 8400 / 190
        self.assertEqual(self._s(f, "102.01.0001").alacak, Dc("8400.00"))                          # banka TL aynı
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))
        # kur farkı motoru: havuz ortalaması 40 → cari TL 7.600, fark 800 zarar (656)
        self.assertEqual(s.borc, Dc("7600.00"))
        self.assertEqual(self._s(f, "656").borc, Dc("800.00"))

    def test_doviz_tutari_bos_ise_tcmb_alis_surer(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="8400", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_pb="USD", sayilan_doviz="")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_tutari, s.islem_kuru), (Dc("200.00"), Dc("42.000000")))

    def test_banka_dovizi_ana_para_birimi_varsayilan(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="8400", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_doviz="190")
        self.assertEqual(self._cari_satir(f).islem_pb, "USD")                                                 # cari ana pb (USD)

    def test_doviz_tutari_gecersiz_kombinasyonlar(self):
        n = YevmiyeFisi.objects.count()
        for kw, karsi in ((dict(sayilan_pb="TRY", sayilan_doviz="100"), self.formal),     # TL (çevirme) + döviz tutarı
                          (dict(sayilan_doviz="100"), self.tl_cari),                       # TL cari + döviz tutarı
                          (dict(sayilan_pb="USD", sayilan_doviz="0"), self.formal)):       # sıfır
            with self.assertRaises(bh.BankaHareketHatasi):
                bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=karsi, tutar="1000", tarih=D(2026, 7, 1),
                                   kullanici=self.su, **kw)
        with self.assertRaises(bh.BankaHareketHatasi):                                  # tahsilat tipinde döviz tutarı yok
            bh.hareket_olustur(banka_hesap=self.banka, tip="cari_tahsilat", karsi=self.formal, tutar="1000", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_doviz="100")
        self.assertEqual(YevmiyeFisi.objects.count(), n)

    def test_kredi_karti_harcamasi_doviz_tutari(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1), kullanici=self.su,
                               sayilan_pb="EUR", sayilan_doviz="100")
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("EUR", Dc("100.00"), Dc("46.000000")))
        self.assertEqual(self._s(f, "309.02").alacak, Dc("4600.00"))
        with self.assertRaises(kk.KrediKartiHareketHatasi):                              # gider harcamasında döviz tutarı olmaz
            kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self._gider(), tutar="100", tarih=D(2026, 7, 1),
                               kullanici=self.su, sayilan_doviz="10")

    def _gider(self):
        from core.models import HesapPlani
        return HesapPlani.objects.get(hesap_kodu="770.01")

    def test_kur_alani_hassasiyeti_ve_cari_satiri_dogrudan(self):
        s = doviz_cari.cari_satiri(self.formal, Dc("191256.36"), D(2026, 7, 1), "B", "EUR", doviz_tutar=Dc("3720"))
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru, s.tl_override), ("EUR", Dc("3720.00"), Dc("51.413000"), Dc("191256.36")))


class BankaDuzenleTest(_Yardimci, DovizCariBase):
    def _odeme(self, **kw):
        return bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1),
                                  kullanici=self.su, sayilan_pb="TRY", **kw)           # düz TL satır (dönüşüm öncesi gibi)

    def test_duzenle_doviz_tutari_fisi_yeniden_yazar(self):
        f = self._odeme()
        no, n = (f.yil, f.fis_no), YevmiyeFisi.objects.filter(silindi=False).count()
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, sayilan_pb="EUR", sayilan_doviz="100", kullanici=self.su)
        f.refresh_from_db()
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("EUR", Dc("100.00"), Dc("46.000000")))   # 4.600 / 100
        self.assertEqual((f.yil, f.fis_no, f.tarih, f.silindi), (*no, D(2026, 7, 1), False))                    # aynı fiş, silinmedi
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), n)
        self.assertEqual(self._s(f, "102.01.0001").alacak, Dc("4600.00"))                          # banka tarafı değişmez
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))

    def test_duzenle_kur_farki_motoru_ortalama_kurla(self):
        f = self._odeme()
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, sayilan_pb="EUR", sayilan_doviz="100", kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual(s.islem_kuru, Dc("46.000000"))                                  # 4.600 / 100
        self.assertEqual(s.borc, Dc("4400.00"))                                          # EUR havuz ortalaması 44 → 100 × 44
        self.assertEqual(self._s(f, "656").borc, Dc("200.00"))              # 4.600 ödendi, 4.400 borç → zarar

    def test_duzenle_bos_alanlar_satiri_degistirmez_sadece_pb_tcmb_alis(self):
        f = self._odeme()
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, aciklama="yeni açıklama", kullanici=self.su)
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")                            # hiçbir şey seçilmedi → aynen
        self.assertEqual(f.aciklama, "YENİ AÇIKLAMA")
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, sayilan_pb="USD", kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("USD", Dc("109.52"), Dc("42.000000")))   # TCMB alış
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, sayilan_pb="TRY", kullanici=self.su)                 # çevirmeye geri dön
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")

    def test_duzenle_hatali_girdi_fisi_degistirmez(self):
        f = self._odeme()
        for kw in (dict(sayilan_pb="TRY", sayilan_doviz="100"), dict(sayilan_pb="EUR", sayilan_doviz="-5")):
            with self.assertRaises(bh.BankaHareketHatasi):
                bh.hareket_guncelle(fis=f, banka_hesap=self.banka, kullanici=self.su, **kw)
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")
        tl = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_odeme", karsi=self.tl_cari, tutar="500", tarih=D(2026, 7, 1),
                                kullanici=self.su)
        with self.assertRaises(bh.BankaHareketHatasi):                                   # TL cari: döviz tutarı girilemez
            bh.hareket_guncelle(fis=tl, banka_hesap=self.banka, sayilan_doviz="10", kullanici=self.su)

    def test_duzenlenemeyen_hareketler(self):
        tah = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_tahsilat", karsi=self.formal, tutar="500", tarih=D(2026, 7, 1),
                                 kullanici=self.su)
        with self.assertRaises(bh.BankaHareketHatasi):
            bh.hareket_guncelle(fis=tah, banka_hesap=self.banka, sayilan_pb="USD", kullanici=self.su)

    def test_duzenle_sonrasi_silinebilir_iptal_yok(self):
        f = self._odeme()
        bh.hareket_guncelle(fis=f, banka_hesap=self.banka, sayilan_pb="EUR", sayilan_doviz="100", kullanici=self.su)
        bh.hareket_sil(fis=f, banka_hesap=self.banka, kullanici=self.su)
        self.assertFalse(YevmiyeFisi.objects.filter(pk=f.pk).exists())


class KartDuzenleTest(_Yardimci, DovizCariBase):
    def test_kart_harcamasi_duzenle_doviz_tutari(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1), kullanici=self.su,
                               sayilan_pb="TRY")
        self.assertEqual(self._cari_satir(f).islem_pb, "TRY")
        kk.hareket_guncelle(fis=f, kart=self.kart, sayilan_pb="EUR", sayilan_doviz="100", kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("EUR", Dc("100.00"), Dc("46.000000")))
        self.assertEqual(s.borc, Dc("4400.00"))                                          # ortalama kur 44; fark 656
        self.assertEqual(self._s(f, "656").borc, Dc("200.00"))
        self.assertEqual(self._s(f, "309.02").alacak, Dc("4600.00"))
        self.assertEqual(sum(x.borc for x in f.satirlar.all()), sum(x.alacak for x in f.satirlar.all()))

    def test_kart_duzenle_sadece_aciklama_satiri_degistirmez_ve_gider_reddi(self):
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1), kullanici=self.su,
                               sayilan_pb="EUR", sayilan_doviz="100")
        kk.hareket_guncelle(fis=f, kart=self.kart, aciklama="not", kullanici=self.su)
        s = self._cari_satir(f)
        self.assertEqual((s.islem_pb, s.islem_tutari, s.islem_kuru), ("EUR", Dc("100.00"), Dc("46.000000")))
        g = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self._gider(), tutar="100", tarih=D(2026, 7, 1), kullanici=self.su)
        with self.assertRaises(kk.KrediKartiHareketHatasi):
            kk.hareket_guncelle(fis=g, kart=self.kart, sayilan_doviz="10", kullanici=self.su)

    def _gider(self):
        from core.models import HesapPlani
        return HesapPlani.objects.get(hesap_kodu="770.01")


class EkranTest(_Yardimci, DovizCariBase):
    def setUp(self):
        self.client.force_login(self.su)

    def test_banka_kayit_ve_duzenle_ekrani(self):
        r = self.client.get(reverse("core:banka_hareket_ekle", args=[self.banka.pk, "cari_odeme"]))
        self.assertContains(r, "Sayılan döviz tutarı")
        r = self.client.post(reverse("core:banka_hareket_ekle", args=[self.banka.pk, "cari_odeme"]), {
            "karsi": self.formal.pk, "tutar": "4.600,00", "tarih": "2026-07-01", "sayilan_pb": "EUR", "sayilan_doviz": "100,00",
            "aciklama": ""})
        self.assertEqual(r.status_code, 302)
        f = YevmiyeFisi.objects.filter(kaynak="BANKA").latest("pk")
        self.assertEqual(self._cari_satir(f).islem_tutari, Dc("100.00"))
        url = reverse("core:banka_hareket_duzenle", args=[self.banka.pk, f.pk])
        r = self.client.get(url)
        self.assertContains(r, "Sayılan döviz tutarı")
        self.assertContains(r, "100,00")                                                # mevcut döviz tutarı dolu gelir
        r = self.client.post(url, {"aciklama": "x", "sayilan_pb": "EUR", "sayilan_doviz": "99,50"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._cari_satir(f).islem_tutari, Dc("99.50"))
        r = self.client.get(reverse("core:banka_hesap_detay", args=[self.banka.pk]) + "?baslangic=2026-06-01&bitis=2026-12-31")
        self.assertContains(r, url)                                                     # ekstrede Düzenle bağlantısı
        # Hatalı giriş formda hata olarak döner, fiş değişmez
        r = self.client.post(url, {"aciklama": "x", "sayilan_pb": "TRY", "sayilan_doviz": "10"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._cari_satir(f).islem_tutari, Dc("99.50"))

    def test_banka_duzenle_uygunsuz_hareket_yonlendirir(self):
        f = bh.hareket_olustur(banka_hesap=self.banka, tip="cari_tahsilat", karsi=self.formal, tutar="500", tarih=D(2026, 7, 1),
                               kullanici=self.su)
        r = self.client.get(reverse("core:banka_hareket_duzenle", args=[self.banka.pk, f.pk]))
        self.assertEqual(r.status_code, 302)

    def test_kart_kayit_ve_duzenle_ekrani(self):
        r = self.client.get(reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"]))
        self.assertContains(r, "Sayılan döviz tutarı")
        f = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self.formal, tutar="4600", tarih=D(2026, 7, 1), kullanici=self.su,
                               sayilan_pb="TRY")
        url = reverse("core:kredi_karti_hareket_duzenle", args=[self.kart.pk, f.pk])
        self.assertContains(self.client.get(url), "Sayılan döviz tutarı")
        r = self.client.post(url, {"aciklama": "", "sayilan_pb": "EUR", "sayilan_doviz": "100,00"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual((self._cari_satir(f).islem_pb, self._cari_satir(f).islem_tutari), ("EUR", Dc("100.00")))
        # gider harcamasının düzenleme ekranında döviz alanı yok
        g = kk.hareket_olustur(kart=self.kart, tip="harcama", karsi=self._gider(), tutar="100", tarih=D(2026, 7, 1), kullanici=self.su)
        self.assertNotContains(self.client.get(reverse("core:kredi_karti_hareket_duzenle", args=[self.kart.pk, g.pk])),
                               "Sayılan döviz tutarı")

    def _gider(self):
        from core.models import HesapPlani
        return HesapPlani.objects.get(hesap_kodu="770.01")
