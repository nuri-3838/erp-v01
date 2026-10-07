"""Kartsız mevcut yaprak hesaba (253.02.0003) fatura kalemlerinden duran varlık kartı açma: ekle ekranı yanlış
"zaten bir kartın hesabında" demez; kart mevcut hesaba bağlanır, FİŞ YAZILMAZ; kartı olan hesaba ikinci kart açılmaz."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, DuranVarlik, EkranYetki, FaturaTipi, HesapPlani, Kur, YevmiyeFisi
from core.services.duran_varlik import DuranVarlikHatasi, duran_varlik_olustur, hesaptaki_kart, kontrol_raporu
from core.services.fatura import fatura_olustur

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem,
                                     parasal=(grup == "BILANCO"))


class KartsizYaprakTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 10, 6), usd_alis=Decimal("30"))
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        _hesap("253.02", "KALIPLAR")
        _hesap("253.02.0003", "A TİPİ KALIPLAR")
        _hesap("253.02.0004", "GENİŞ BASAMAK KALIPLARI")
        _hesap("320.10.0001", "TEDARİKÇİ", kalem="KVYK")
        cls.tip = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ", para_birimi="TRY", muhasebe_kodu="320.10.0001")
        cls.fatura = fatura_olustur(
            tip_id=cls.tip.pk, cari_id=cls.cari.pk, tarih=D(2026, 10, 6), fatura_no="F-210",
            satirlar=[{"hesap_id": "253.02.0003", "miktar": "1", "birim_fiyat": "430000"},
                      {"hesap_id": "253.02.0003", "miktar": "1", "birim_fiyat": "210000"},
                      {"hesap_id": "253.02.0004", "miktar": "1", "birim_fiyat": "400000"}])
        cls.u = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.u, ekran_kod="duran_varliklar")

    def _satirlar(self, hesap):
        return list(self.fatura.satirlar.filter(hesap_id=hesap).order_by("id"))

    def test_kartsiz_yaprakta_ekle_ekrani_acilir(self):
        s = self._satirlar("253.02.0003")[0]
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:duran_varlik_ekle") + f"?satir={s.pk}")
        self.assertEqual(r.status_code, 200)                                   # eskiden 302 + "zaten bir kartın hesabında"
        self.assertContains(r, "640.000,00")                                   # iki kalemin toplamı

    def test_kartsiz_yaprakta_kart_acilir_fis_yazilmaz(self):
        sat = self._satirlar("253.02.0003")
        fis_once = YevmiyeFisi.objects.count()
        self.client.force_login(self.u)
        r = self.client.post(reverse("core:duran_varlik_ekle"), {
            "ad": "a tipi kalıplar", "hesap": "253.02.0003", "aktiflestirme_tarihi": "2026-10-06", "maliyet": "",
            "marka_model": "", "seri_no": "", "notlar": "Üst tabla 430.000", "satir": sat[0].pk,
            "fatura_satir_ids": [s.pk for s in sat]})
        self.assertEqual(r.status_code, 302)
        v = DuranVarlik.objects.get()
        self.assertEqual((v.hesap_id, v.maliyet, v.kaynak), ("253.02.0003", Decimal("640000.00"), DuranVarlik.Kaynak.FATURA))
        self.assertEqual(v.fatura_satirlari.count(), 2)
        self.assertEqual(YevmiyeFisi.objects.count(), fis_once)               # fiş yok
        kr = [x for x in kontrol_raporu() if x["hesap"].hesap_kodu == "253.02.0003"]
        self.assertEqual(kr[0]["fark"], Decimal("0.00"))

    def test_kartli_hesapta_yeni_kart_acilmaz(self):
        sat = self._satirlar("253.02.0004")
        v = duran_varlik_olustur(ad="x", hesap_id="253.02.0004", aktiflestirme_tarihi=D(2026, 10, 6), maliyet=None,
                                 fatura_satirlari=[s.pk for s in sat])
        self.assertEqual(hesaptaki_kart("253.02.0004").pk, v.pk)
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="y", hesap_id="253.02.0004", aktiflestirme_tarihi=D(2026, 10, 6), maliyet=Decimal("1"))
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:duran_varlik_ekle") + f"?satir={sat[0].pk}")
        self.assertRedirects(r, reverse("core:duran_varlik_detay", args=[v.pk]), fetch_redirect_response=False)

    def test_form_yalniz_kartsiz_yapraklari_listeler(self):
        from core.forms import DuranVarlikForm
        duran_varlik_olustur(ad="x", hesap_id="253.02.0004", aktiflestirme_tarihi=D(2026, 10, 6), maliyet=Decimal("5"))
        kodlar = set(DuranVarlikForm().fields["hesap"].queryset.values_list("hesap_kodu", flat=True))
        self.assertIn("253.02", kodlar)
        self.assertIn("253.02.0003", kodlar)
        self.assertNotIn("253.02.0004", kodlar)
