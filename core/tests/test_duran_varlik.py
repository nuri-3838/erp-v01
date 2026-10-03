"""Duran Varlık kartı (DURAN VARLIK FAZ 2) — model/servis: otomatik kod (DV-NNNN),
hesap kısıtı (253/254/255/260, 258 HARİÇ), kaynak FATURA/ACILIS, kontrol raporu;
ekranlar: liste (+filtre+hesap özeti), ekle (fatura kaleminden ön-dolu, birden fazla
kalem seçimiyle), detay, durum değiştirme, kalem bağlama/çıkarma, yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, DuranVarlik, EkranYetki, FaturaTipi, HesapPlani, KdvOrani, Kur
from core.services.duran_varlik import (
    DuranVarlikHatasi, baglanabilir_satirlar, baglanti_toplami, duran_varlik_guncelle,
    duran_varlik_olustur, durum_degistir, hesap_bazli_toplam, kontrol_raporu, satir_bagla,
    satir_cikar, silinebilir_mi, sonraki_demirbas_kodu, varlik_sil, varliklar,
)
from core.services.fatura import fatura_olustur

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DDV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=(grup == "BILANCO"))


class KodUretimiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")

    def test_ilk_kod_dv_0001(self):
        self.assertEqual(sonraki_demirbas_kodu(), "DV-0001")

    def test_bosluk_doldurur(self):
        h = HesapPlani.objects.get(hesap_kodu="253")
        v1 = duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                  maliyet=Decimal("100"))
        duran_varlik_olustur(ad="b", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                             maliyet=Decimal("100"))
        v1.silindi = True
        v1.save(update_fields=["silindi"])
        self.assertEqual(sonraki_demirbas_kodu(), "DV-0001")


class OlusturTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        _hesap("264", "ÖZEL MALİYETLER")

    def test_buyuk_harf_ve_kod_ve_kaynak_acilis(self):
        h = HesapPlani.objects.get(hesap_kodu="253")
        v = duran_varlik_olustur(ad="cnc lazer kesim", hesap_id=h.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("1000"))
        self.assertEqual(v.demirbas_kodu, "DV-0001")
        self.assertEqual(v.ad, "CNC LAZER KESİM")
        self.assertEqual(v.kaynak, DuranVarlik.Kaynak.ACILIS)
        self.assertEqual(v.durum, DuranVarlik.Durum.AKTIF)

    def test_bos_ad_reddedilir(self):
        h = HesapPlani.objects.get(hesap_kodu="253")
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="  ", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"))

    def test_negatif_maliyet_reddedilir(self):
        h = HesapPlani.objects.get(hesap_kodu="253")
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("-1"))

    def test_258_hesabi_reddedilir(self):
        h = HesapPlani.objects.get(hesap_kodu="258")
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"))

    def test_264_hesabi_reddedilir(self):
        h = HesapPlani.objects.get(hesap_kodu="264")
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"))


class FaturaKaynakliTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")

    def _fatura(self):
        return fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": self.kdv20.pk}])

    def test_satir_verilince_kaynak_fatura_ve_m2m_baglanir(self):
        f = self._fatura()
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.assertEqual(v.kaynak, DuranVarlik.Kaynak.FATURA)
        self.assertEqual(list(v.fatura_satirlari.all()), [satir])
        self.assertEqual(v.maliyet, Decimal("5000.00"))

    def test_satir_verilince_gonderilen_maliyet_yok_sayilir_otomatik_hesaplanir(self):
        f = self._fatura()
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("1"),
                                 fatura_satirlari=[satir.pk])
        self.assertEqual(v.maliyet, Decimal("5000.00"))   # 1 değil — satırın matrahı

    def test_bulunamayan_satir_reddedilir(self):
        h = HesapPlani.objects.get(hesap_kodu="253")
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"), fatura_satirlari=[99999])

    def test_hesap_bazli_toplam(self):
        f = self._fatura()
        satir = f.satirlar.first()
        duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                             aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"))
        duran_varlik_olustur(ad="pres", hesap_id=satir.hesap_id,
                             aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("2000"))
        ozet = hesap_bazli_toplam(varliklar())
        self.assertEqual(len(ozet), 1)
        self.assertEqual(ozet[0]["toplam"], Decimal("5000.00"))
        self.assertEqual(ozet[0]["adet"], 2)

    def test_kontrol_raporu_fark_sifir(self):
        f = self._fatura()
        satir = f.satirlar.first()
        duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                             aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                             fatura_satirlari=[satir.pk])
        rapor = {r["hesap"].hesap_kodu: r for r in kontrol_raporu()}
        self.assertEqual(rapor["253"]["kart_toplami"], Decimal("5000.00"))
        self.assertEqual(rapor["253"]["mizan_bakiye"], Decimal("5000.00"))
        self.assertEqual(rapor["253"]["fark"], Decimal("0.00"))

    def test_kontrol_raporu_fark_karti_girilmemis_faturayi_yansitir(self):
        self._fatura()   # fatura kesildi ama kart hiç girilmedi
        rapor = {r["hesap"].hesap_kodu: r for r in kontrol_raporu()}
        self.assertEqual(rapor["253"]["kart_toplami"], Decimal("0.00"))
        self.assertEqual(rapor["253"]["mizan_bakiye"], Decimal("5000.00"))
        self.assertEqual(rapor["253"]["fark"], Decimal("-5000.00"))

    def test_durum_degistir(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=HesapPlani.objects.get(hesap_kodu="253").pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("100"))
        durum_degistir(v, durum=DuranVarlik.Durum.PASIF)
        v.refresh_from_db()
        self.assertEqual(v.durum, DuranVarlik.Durum.PASIF)


class EkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.fatura = fatura_olustur(
            tip_id=cls.gider.pk, cari_id=cls.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": cls.kdv20.pk}])
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="duran_varliklar")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="alis_faturalari")
        cls.kisitli = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=cls.kisitli, ekran_kod="mizan")

    def test_yetkisiz_403(self):
        self.client.force_login(self.kisitli)
        self.assertEqual(self.client.get(reverse("core:duran_varliklar")).status_code, 403)

    def test_liste_bos(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varliklar"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Henüz duran varlık kartı yok.")

    def test_ekle_satirdan_on_dolu_ve_kaydedilir(self):
        satir = self.fatura.satirlar.first()
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_ekle") + f"?satir={satir.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "5.000,00")

        r2 = self.client.post(reverse("core:duran_varlik_ekle"), {
            "ad": "cnc lazer kesim", "hesap": satir.hesap_id,
            "aktiflestirme_tarihi": "2026-03-10", "maliyet": "5.000,00",
            "marka_model": "", "seri_no": "", "notlar": "",
            "satir": satir.pk, "fatura_satir_ids": [satir.pk],
        })
        self.assertEqual(r2.status_code, 302)
        v = DuranVarlik.objects.get(demirbas_kodu="DV-0001")
        self.assertEqual(v.kaynak, DuranVarlik.Kaynak.FATURA)
        self.assertEqual(v.maliyet, Decimal("5000.00"))

    def test_fatura_detay_kart_olustur_butonu_gorunur(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:fatura_detay", args=[self.fatura.pk]))
        self.assertContains(r, "Duran varlık kartı oluştur")

    def test_detay_ve_durum_degistir(self):
        satir = self.fatura.satirlar.first()
        v = duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_detay", args=[v.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "TEDARİKÇİ A")

        r2 = self.client.post(reverse("core:duran_varlik_durum_degistir", args=[v.pk]))
        self.assertEqual(r2.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.durum, DuranVarlik.Durum.PASIF)

    def test_fatura_detay_kart_varsa_link_gosterir_buton_gostermez(self):
        satir = self.fatura.satirlar.first()
        v = duran_varlik_olustur(ad="cnc", hesap_id=satir.hesap_id,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:fatura_detay", args=[self.fatura.pk]))
        self.assertContains(r, v.demirbas_kodu)
        self.assertNotContains(r, "Duran varlık kartı oluştur")

    def test_hesap_filtresi(self):
        self.client.force_login(self.yetkili)
        h = HesapPlani.objects.get(hesap_kodu="253")
        duran_varlik_olustur(ad="cnc", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 3, 10),
                             maliyet=Decimal("100"))
        r = self.client.get(reverse("core:duran_varliklar"), {"hesap": h.pk})
        self.assertContains(r, "DV-0001")
        r2 = self.client.get(reverse("core:duran_varliklar"), {"durum": "PASIF"})
        self.assertContains(r2, "Henüz duran varlık kartı yok.")


class KalemBaglamaTest(TestCase):
    """253 hesabına 2 kalemli bir fatura; kart-kalem bağlama/çıkarma + çoklu ön-dolu seçim."""

    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        _hesap("254", "TAŞITLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.h253 = HesapPlani.objects.get(hesap_kodu="253")
        cls.h254 = HesapPlani.objects.get(hesap_kodu="254")
        cls.fatura = fatura_olustur(
            tip_id=cls.gider.pk, cari_id=cls.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "3000",
                      "kdv_id": cls.kdv20.pk},
                     {"hesap_id": "253", "miktar": "1", "birim_fiyat": "2000",
                      "kdv_id": cls.kdv20.pk}])
        cls.satir1, cls.satir2 = list(cls.fatura.satirlar.all())
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="duran_varliklar")

    def test_baglanabilir_satirlar_kartta_bagli_olani_disinda_hepsini_gosterir(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        aday = list(baglanabilir_satirlar(v))
        self.assertEqual(aday, [self.satir2])

    def test_satir_bagla_ve_cikar(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        satir_bagla(v, self.satir2.pk)
        self.assertEqual(set(v.fatura_satirlari.values_list("pk", flat=True)),
                         {self.satir1.pk, self.satir2.pk})
        self.assertEqual(baglanti_toplami(v), Decimal("5000.00"))

        satir_cikar(v, self.satir2.pk)
        self.assertEqual(list(v.fatura_satirlari.values_list("pk", flat=True)), [self.satir1.pk])
        self.assertEqual(baglanti_toplami(v), Decimal("3000.00"))

    def test_baska_karta_bagli_kalem_listelenmez_ve_baglanamaz(self):
        v1 = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                  aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                  fatura_satirlari=[self.satir1.pk])
        v2 = duran_varlik_olustur(ad="pres", hesap_id=self.h253.pk,
                                  aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("2000"),
                                  fatura_satirlari=[self.satir2.pk])
        self.assertEqual(list(baglanabilir_satirlar(v1)), [])
        with self.assertRaises(DuranVarlikHatasi):
            satir_bagla(v1, self.satir2.pk)

    def test_farkli_hesaptaki_kalem_baglanamaz(self):
        v = duran_varlik_olustur(ad="tasit", hesap_id=self.h254.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("100"))
        with self.assertRaises(DuranVarlikHatasi):
            satir_bagla(v, self.satir1.pk)

    def test_cikarma_karta_bagli_olmayan_kalemde_hata(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        with self.assertRaises(DuranVarlikHatasi):
            satir_cikar(v, self.satir2.pk)

    def test_on_doldurmada_maliyet_ayni_faturanin_bagli_olmayan_kalemlerinin_toplami(self):
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_ekle") + f"?satir={self.satir1.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "5.000,00")   # 3000 + 2000, ikisi de henuz bagli degil
        form = r.context["form"]
        self.assertEqual(set(form.initial["fatura_satir_ids"]),
                         {self.satir1.pk, self.satir2.pk})

    def test_on_doldurmada_baska_karta_bagli_kardes_kalem_otomatik_secilmez(self):
        duran_varlik_olustur(ad="pres", hesap_id=self.h253.pk,
                             aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("2000"),
                             fatura_satirlari=[self.satir2.pk])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_ekle") + f"?satir={self.satir1.pk}")
        self.assertContains(r, "3.000,00")
        self.assertNotContains(r, "5.000,00")
        form = r.context["form"]
        self.assertEqual(set(form.initial["fatura_satir_ids"]), {self.satir1.pk})

    def test_kalem_bagla_ve_cikar_view(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:duran_varlik_kalem_bagla", args=[v.pk]),
                             {"satir_id": self.satir2.pk})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(v.fatura_satirlari.count(), 2)

        r2 = self.client.post(
            reverse("core:duran_varlik_kalem_cikar", args=[v.pk, self.satir2.pk]))
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(v.fatura_satirlari.count(), 1)

    def test_satir_baglayinca_maliyet_otomatik_guncellenir(self):
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        self.assertEqual(v.maliyet, Decimal("3000.00"))
        satir_bagla(v, self.satir2.pk)
        v.refresh_from_db()
        self.assertEqual(v.maliyet, Decimal("5000.00"))   # artık otomatik senkron — fark yok

        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_detay", args=[v.pk]))
        self.assertNotContains(r, "uyuşmuyor")

    def test_detay_eski_veriden_kalma_fark_hala_uyari_gosterir(self):
        # Yeni bağlama/çıkarma işlemleri artık hiç uyuşmazlık bırakmaz; bu test yalnız
        # UYARI GÖSTERİMİNİN kendisini (geçmişten kalma, elle bozulmuş bir kayıt
        # senaryosuyla) doğrular — servis fonksiyonları ÜZERİNDEN bu duruma ulaşılamaz.
        v = duran_varlik_olustur(ad="cnc", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Decimal("3000"),
                                 fatura_satirlari=[self.satir1.pk])
        DuranVarlik.objects.filter(pk=v.pk).update(maliyet=Decimal("1.00"))
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_detay", args=[v.pk]))
        self.assertContains(r, "uyuşmuyor")


class KontrolRaporuAktifFiltresiTest(TestCase):
    def test_pasif_kart_kontrol_raporuna_girmez(self):
        h = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        v = duran_varlik_olustur(ad="aktif kart", hesap_id=h.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("1000"))
        v2 = duran_varlik_olustur(ad="pasif kart", hesap_id=h.pk,
                                  aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("2000"))
        durum_degistir(v2, durum=DuranVarlik.Durum.PASIF)
        rapor = {r["hesap"].hesap_kodu: r for r in kontrol_raporu()}
        self.assertEqual(rapor["253"]["kart_toplami"], Decimal("1000.00"))   # yalnız aktif


class VarlikSilTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.h253 = HesapPlani.objects.get(hesap_kodu="253")

    def test_bagli_kalemsiz_ve_kaynak_acilis_silinebilir(self):
        v = duran_varlik_olustur(ad="acilis karti", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        self.assertTrue(silinebilir_mi(v))
        varlik_sil(v)
        v.refresh_from_db()
        self.assertTrue(v.silindi)

    def test_bagli_kalemi_olan_kart_silinemez(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": self.kdv20.pk}])
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="fatura karti", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.assertFalse(silinebilir_mi(v))
        with self.assertRaises(DuranVarlikHatasi):
            varlik_sil(v)
        v.refresh_from_db()
        self.assertFalse(v.silindi)

    def test_kaynagi_proje_olan_kart_silinemez(self):
        from core.services.yatirim_projesi import proje_aktiflestir, proje_olustur
        proje = proje_olustur(ad="test proje")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-2",
            satirlar=[{"hesap_id": "258", "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": self.kdv20.pk, "yatirim_projesi_id": proje.pk}])
        proje_aktiflestir(proje, tarih=D(2026, 3, 10), satirlar=[
            {"hesap_id": self.h253.pk, "varlik_adi": "proje varligi", "tutar": Decimal("1000")}])
        v = DuranVarlik.objects.get(yatirim_projesi=proje)
        self.assertEqual(v.kaynak, DuranVarlik.Kaynak.PROJE)
        self.assertFalse(silinebilir_mi(v))
        with self.assertRaises(DuranVarlikHatasi):
            varlik_sil(v)


class SilinmisKartGorunurlukTest(TestCase):
    """Soft-delete edilmiş bir kart hiçbir yerde (liste, hesap bazlı toplam, kontrol
    raporu, fatura detayındaki "mevcut kart" bağlantısı) görünmemeli — 2026-10-03:
    fatura_detay.html'de prefetch_related('duran_varliklar') silindi=False filtrelemediği
    için eski (silinmiş) kart hâlâ "mevcut kart" olarak gösteriliyor, üstelik "+ kart
    oluştur" linki de bu yüzden hiç çıkmıyordu (M2M bağı soft-delete ile temizlenmez)."""

    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        cls.h253 = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="duran_varliklar")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="alis_faturalari")

    def test_silinmis_kart_listede_gorunmez(self):
        v = duran_varlik_olustur(ad="eski kart", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        varlik_sil(v)
        self.assertNotIn(v.pk, list(varliklar().values_list("pk", flat=True)))

    def test_silinmis_kart_hesap_bazli_toplamda_sayilmaz(self):
        v = duran_varlik_olustur(ad="eski kart", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        varlik_sil(v)
        ozet = hesap_bazli_toplam(varliklar())
        self.assertEqual(ozet, [])

    def test_silinmis_kart_kontrol_raporunda_sayilmaz(self):
        v = duran_varlik_olustur(ad="eski kart", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        varlik_sil(v)
        rapor = {r["hesap"].hesap_kodu: r for r in kontrol_raporu()}
        self.assertEqual(rapor["253"]["kart_toplami"], Decimal("0.00"))

    def test_silinmis_kart_fatura_detayinda_gorunmez_yeniden_kart_olustur_linki_cikar(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": self.kdv20.pk}])
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="eski kart", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])

        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertContains(r, v.demirbas_kodu)
        self.assertNotContains(r, "Duran varlık kartı oluştur")

        # Bağlı kalemi olan kartlar normal "Kartı Sil" ile silinemez (varlik_sil reddeder) —
        # ama FAZ 3 "Aktifleştirmeyi Geri Al" akışı tam da böyle (satır bağlı) kartları
        # soft-delete eder (bkz. core.services.yatirim_projesi.proje_geri_al). O senaryoyu
        # modelin kendisi üzerinden simüle ediyoruz.
        from django.utils import timezone
        v.silindi = True
        v.silindi_at = timezone.now()
        v.save(update_fields=["silindi", "silindi_at"])

        r2 = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertNotContains(r2, v.demirbas_kodu)
        self.assertContains(r2, "Duran varlık kartı oluştur")


class DuranVarlikGuncelleTest(TestCase):
    def test_duzenlenebilir_alanlar_guncellenir(self):
        h = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        v = duran_varlik_olustur(ad="eski ad", hesap_id=h.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        guncellenen = duran_varlik_guncelle(
            v, ad="yeni ad", maliyet=Decimal("250"), marka_model="bosch", seri_no="sn-1",
            notlar="not")
        self.assertEqual(guncellenen.ad, "YENİ AD")
        self.assertEqual(guncellenen.maliyet, Decimal("250"))
        self.assertEqual(guncellenen.marka_model, "bosch")
        self.assertEqual(guncellenen.seri_no, "sn-1")
        self.assertEqual(guncellenen.notlar, "not")
        self.assertEqual(guncellenen.hesap_id, h.pk)   # değişmedi

    def test_bos_ad_reddedilir(self):
        h = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        v = duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"))
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_guncelle(v, ad="  ", maliyet=Decimal("100"))

    def test_negatif_maliyet_reddedilir(self):
        h = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        v = duran_varlik_olustur(ad="a", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 1, 1),
                                 maliyet=Decimal("100"))
        with self.assertRaises(DuranVarlikHatasi):
            duran_varlik_guncelle(v, ad="a", maliyet=Decimal("-5"))

    def test_bagli_kalemli_kartta_gonderilen_maliyet_yok_sayilir(self):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        h = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        gider = FaturaTipi.objects.create(ad="ALIŞ-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                   muhasebe_kodu="320.10.0001")
        f = fatura_olustur(
            tip_id=gider.pk, cari_id=cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "1000",
                      "kdv_id": kdv20.pk}])
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="cnc", hesap_id=h.pk, aktiflestirme_tarihi=D(2026, 3, 10),
                                 maliyet=satir.tutar, fatura_satirlari=[satir.pk])
        duran_varlik_guncelle(v, ad="cnc 2", maliyet=Decimal("999999"))
        v.refresh_from_db()
        self.assertEqual(v.ad, "CNC 2")
        self.assertEqual(v.maliyet, Decimal("1000.00"))   # 999999 yok sayıldı


class VarlikSilVeDuzenleEkranTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2026, 3, 10), usd_alis=Decimal("30"))
        _hesap("191", "İNDİRİLECEK KDV", grup="BILANCO", kalem="DV")
        _hesap("391", "HESAPLANAN KDV", grup="BILANCO", kalem="KVYK")
        _hesap("320.10.0001", "TEDARİKÇİ A", grup="BILANCO", kalem="KVYK")
        cls.h253 = _hesap("253", "TESİS MAKİNE VE CİHAZLAR")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.cari = Cari.objects.create(kod="320-10-0001", unvan="TEDARİKÇİ A", para_birimi="TRY",
                                       muhasebe_kodu="320.10.0001")
        cls.yetkili = User.objects.create_user("yet", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="duran_varliklar")

    def test_duzenle_view_get_ve_post(self):
        v = duran_varlik_olustur(ad="eski ad", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_duzenle", args=[v.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ESKİ AD")
        self.assertNotContains(r, 'name="hesap"')   # hesap formda hiç yok

        r2 = self.client.post(reverse("core:duran_varlik_duzenle", args=[v.pk]), {
            "ad": "yeni ad", "maliyet": "250,00", "marka_model": "", "seri_no": "", "notlar": "",
        })
        self.assertEqual(r2.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.ad, "YENİ AD")
        self.assertEqual(v.maliyet, Decimal("250.00"))

    def test_detay_silinebilir_kartta_buton_gorunur(self):
        v = duran_varlik_olustur(ad="acilis", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_detay", args=[v.pk]))
        self.assertContains(r, "Kartı Sil")

    def test_detay_bagli_kalemli_kartta_buton_gorunmez(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": self.kdv20.pk}])
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="fatura karti", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.client.force_login(self.yetkili)
        r = self.client.get(reverse("core:duran_varlik_detay", args=[v.pk]))
        self.assertNotContains(r, "Kartı Sil")

    def test_sil_view_basarili(self):
        v = duran_varlik_olustur(ad="acilis", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:duran_varlik_sil", args=[v.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("core:duran_varliklar"))
        v.refresh_from_db()
        self.assertTrue(v.silindi)

    def test_sil_view_bagli_kalemliyse_hata_mesajiyla_detaya_doner(self):
        f = fatura_olustur(
            tip_id=self.gider.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), fatura_no="G-1",
            satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "5000",
                      "kdv_id": self.kdv20.pk}])
        satir = f.satirlar.first()
        v = duran_varlik_olustur(ad="fatura karti", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 3, 10), maliyet=satir.tutar,
                                 fatura_satirlari=[satir.pk])
        self.client.force_login(self.yetkili)
        r = self.client.post(reverse("core:duran_varlik_sil", args=[v.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("core:duran_varlik_detay", args=[v.pk]))
        v.refresh_from_db()
        self.assertFalse(v.silindi)

    def test_yetkisiz_403(self):
        v = duran_varlik_olustur(ad="acilis", hesap_id=self.h253.pk,
                                 aktiflestirme_tarihi=D(2026, 1, 1), maliyet=Decimal("100"))
        kisitli = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=kisitli, ekran_kod="mizan")
        self.client.force_login(kisitli)
        self.assertEqual(
            self.client.get(reverse("core:duran_varlik_duzenle", args=[v.pk])).status_code, 403)
        self.assertEqual(
            self.client.post(reverse("core:duran_varlik_sil", args=[v.pk])).status_code, 403)
