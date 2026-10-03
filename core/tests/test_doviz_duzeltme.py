"""Döviz (USD/EUR) kalemlerin TL karşılığına çevrilmeden toplanması bugu + düzeltmesi:
proje toplamı, duran varlık kart maliyeti, aktifleştirme tutarı, kontrol raporundaki
258 satırı, manuel fişte yatırım projesi alanı (bkz. FaturaSatir.tutar_tl,
core.services.duran_varlik, core.services.yatirim_projesi, core.services.yevmiye)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Cari, FaturaTipi, HesapPlani, KdvOrani, Kur, YevmiyeSatir
from core.sayi import yuvarla
from core.services.duran_varlik import duran_varlik_olustur, kontrol_raporu
from core.services.fatura import fatura_olustur
from core.services.yatirim_projesi import proje_aktiflestir, proje_olustur, proje_toplami
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_olustur

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class DovizTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 8, 10), usd_alis=Decimal("47"), eur_alis=Decimal("53"))
        _hesap("100", "KASA")
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", kalem="KVYK")
        cls.hesap_258 = _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        cls.hesap_253 = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv0 = KdvOrani.objects.create(aciklama="KDV YOK", oran=Decimal("0"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="USD",
                                       muhasebe_kodu="320.10.0001")
        cls.u = User.objects.create_superuser("yon", password="x")

    def _fatura(self, *, hesap_kodu, tutar, pb, kur, proje=None, no="G-1"):
        satir = {"hesap_id": hesap_kodu, "miktar": "1", "birim_fiyat": tutar,
                "kdv_id": self.kdv0.pk}
        if proje:
            satir["yatirim_projesi_id"] = proje.pk
        return fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 8, 10), fatura_no=no,
            para_birimi=pb, kur=kur, satirlar=[satir], kullanici=self.u)


class ProjeToplamiDovizTest(DovizTemel):
    def test_usd_kalemli_proje_toplami_tl_karsiligi(self):
        """Kullanıcının YP-0005 örneği: 118.636 USD × 47,3751 TL karşılığı toplanmalı,
        döviz tutarı (118.636,00) değil."""
        proje = proje_olustur(ad="imalat tezgahlari", kullanici=self.u)
        self._fatura(hesap_kodu="258", tutar="118636", pb="USD", kur=Decimal("47.3751"),
                    proje=proje, no="26JJMM004")
        beklenen = yuvarla(Decimal("118636") * Decimal("47.3751"), 2)
        self.assertEqual(beklenen, Decimal("5620392.36"))   # kullanıcının verdiği örnek
        self.assertEqual(proje_toplami(proje), beklenen)

    def test_try_kalem_degismez_regresyon(self):
        proje = proje_olustur(ad="try proje", kullanici=self.u)
        self._fatura(hesap_kodu="258", tutar="1000", pb="TRY", kur=None, proje=proje)
        self.assertEqual(proje_toplami(proje), Decimal("1000.00"))


class KartMaliyetiDovizTest(DovizTemel):
    def test_eur_faturaya_bagli_kart_maliyeti_tl_karsiligi(self):
        """Kullanıcının DV-0016 örneği: 2.140 EUR × 53,5484 TL karşılığı kart
        maliyeti olmalı, 2.140,00 (döviz tutarı) değil."""
        f = self._fatura(hesap_kodu="253", tutar="2140", pb="EUR", kur=Decimal("53.5484"),
                         no="FRM2026000000731")
        satir = f.satirlar.get()
        dv = duran_varlik_olustur(
            ad="test demirbas", hesap_id=self.hesap_253.pk, aktiflestirme_tarihi=D(2026, 8, 10),
            maliyet=None, fatura_satirlari=[satir.pk], kullanici=self.u)
        beklenen = yuvarla(Decimal("2140") * Decimal("53.5484"), 2)
        self.assertEqual(beklenen, Decimal("114593.58"))    # kullanıcının verdiği örnek
        self.assertEqual(dv.maliyet, beklenen)


class AktiflestirmeDovizTest(DovizTemel):
    def test_aktiflestirme_tutari_tl_karsiligi(self):
        proje = proje_olustur(ad="proje usd", kullanici=self.u)
        self._fatura(hesap_kodu="258", tutar="1000", pb="USD", kur=Decimal("47"), proje=proje)
        toplam = proje_toplami(proje)
        self.assertEqual(toplam, Decimal("47000.00"))
        proje_aktiflestir(proje, tarih=D(2026, 8, 10),
                          satirlar=[{"hesap_id": self.hesap_253.pk, "varlik_adi": "tezgah",
                                     "tutar": toplam}], kullanici=self.u)
        dv = self.hesap_253.duran_varliklar.get()
        self.assertEqual(dv.maliyet, Decimal("47000.00"))


class KontrolRaporu258SatiriTest(DovizTemel):
    def test_258_satiri_proje_toplami_mizan_esit(self):
        proje = proje_olustur(ad="proje usd", kullanici=self.u)
        self._fatura(hesap_kodu="258", tutar="1000", pb="USD", kur=Decimal("47"), proje=proje)
        rapor = kontrol_raporu()
        satir_258 = next(r for r in rapor if r["hesap"] and r["hesap"].hesap_kodu == "258")
        self.assertEqual(satir_258["kart_toplami"], Decimal("47000.00"))
        self.assertEqual(satir_258["mizan_bakiye"], Decimal("47000.00"))
        self.assertEqual(satir_258["fark"], Decimal("0.00"))


class ManuelFisProjeAlaniTest(DovizTemel):
    def test_manuel_fiste_258_satirina_proje_baglanir_ve_toplama_dahil(self):
        proje = proje_olustur(ad="gumrukcu", kullanici=self.u)
        fis = fis_olustur(
            tarih=D(2026, 8, 10), kullanici=self.u,
            satirlar=[
                SatirGirdi("258", "B", "5000", yatirim_projesi_id=proje.pk),
                SatirGirdi("100", "A", "5000"),
            ])
        satir_258 = fis.satirlar.get(hesap_id="258")
        self.assertEqual(satir_258.yatirim_projesi_id, proje.pk)
        self.assertEqual(proje_toplami(proje), Decimal("5000.00"))

    def test_proje_disi_hesapta_yatirim_projesi_reddedilir(self):
        proje = proje_olustur(ad="x", kullanici=self.u)
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(
                tarih=D(2026, 8, 10), kullanici=self.u,
                satirlar=[
                    SatirGirdi("253", "B", "1000", yatirim_projesi_id=proje.pk),
                    SatirGirdi("100", "A", "1000"),
                ])

    def test_aktiflesmis_projeye_manuel_satir_reddedilir(self):
        proje = proje_olustur(ad="bitmis", kullanici=self.u)
        proje.durum = "AKTIFLESTI"
        proje.save(update_fields=["durum"])
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(
                tarih=D(2026, 8, 10), kullanici=self.u,
                satirlar=[
                    SatirGirdi("258", "B", "1000", yatirim_projesi_id=proje.pk),
                    SatirGirdi("100", "A", "1000"),
                ])


class FisEkleEkranProjeAlaniTest(DovizTemel):
    def test_ekranda_proje_secenegi_gorunur_ve_kaydeder(self):
        from django.urls import reverse

        from core.models import EkranYetki
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="fis_listesi")
        proje = proje_olustur(ad="gumrukcu", kullanici=self.u)
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:fis_ekle"))
        self.assertContains(r, proje.kod)
        r = self.client.post(reverse("core:fis_ekle"), {
            "tarih": "2026-08-10", "aciklama": "test",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "2", "form-MAX_NUM_FORMS": "1000",
            "form-0-hesap": "258", "form-0-islem_pb": "TRY", "form-0-borc": "5.000,00",
            "form-0-alacak": "", "form-0-islem_kuru": "1", "form-0-yatirim_projesi": str(proje.pk),
            "form-1-hesap": "100", "form-1-islem_pb": "TRY", "form-1-alacak": "5.000,00",
            "form-1-borc": "", "form-1-islem_kuru": "1",
        })
        self.assertEqual(r.status_code, 302)
        satir = YevmiyeSatir.objects.get(hesap_id="258")
        self.assertEqual(satir.yatirim_projesi_id, proje.pk)
        self.assertEqual(proje_toplami(proje), Decimal("5000.00"))
