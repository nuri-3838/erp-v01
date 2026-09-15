"""Satış Teklifi/Proforması — Aday Müşteriye (CRM lead) belge verme: normal Cari akışıyla
birebir aynı ekranlardan, cari/aday_musteri karşılıklı dışlayıcı
(ck_teklif_siparis_cari_xor_aday_musteri). Aday müşteri yalnız Teklif ve Proforma
aşamalarında geçerli — Sipariş (dolayısıyla İrsaliye/Fatura) zinciri gerçek Cari ister
(bkz. proformayi_siparise_cevir guard'ı); aday bu arada Cariye dönüştürülürse (bkz.
core.services.aday.aday_cariye_donustur) engel kalkar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Birim, Cari, HesapPlani, KdvOrani, Kategori, TanimSecenegi, TeklifSiparis,
)
from core.services.aday import aday_cariye_donustur, aday_musteri_olustur
from core.services.stok import stok_olustur


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True)


def _secenek(kategori, **f):
    return TanimSecenegi.objects.filter(silindi=False, kategori=kategori, **f).first()


class SatisTeklifAdayMusteriTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("stadyon", password="x")
        cls.bos = User.objects.create_user("stadybos", password="x")
        _hesap("120.02", "MÜŞTERİ SATIŞ TEKLİF ADAY")
        cls.cari = Cari.objects.create(
            kod="C2", unvan="GERÇEK MÜŞTERİ", muhasebe_kodu="120.02",
            para_birimi="TRY", iskonto_yuzdesi=Decimal("5"),
            created_by=cls.yon, updated_by=cls.yon)
        cls.aday = aday_musteri_olustur(
            unvan="potansiyel musteri ltd", para_birimi="USD",
            iskonto_yuzdesi=Decimal("15"), kullanici=cls.yon)
        # Zaten cariye dönüşmüş bir aday — dropdown'da/aday_meta'da görünmemeli.
        cls.donusmus_aday = aday_musteri_olustur(
            unvan="donusmus aday", para_birimi="TRY", kullanici=cls.yon)
        cls.donusmus_aday.donusen_cari = cls.cari
        cls.donusmus_aday.save(update_fields=["donusen_cari"])

        ust = Kategori.objects.create(kod="150", ad="MAMUL", created_by=cls.yon,
                                      updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="10", ad="MERDİVEN", ust=ust,
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.urun = stok_olustur(
            ad="a tipi merdiven", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, model_kodu="a21", basamak_sayisi=3, yukseklik="58",
            fiyat_try="12000", fiyat_usd="350", kullanici=cls.yon)
        cls.sekil = _secenek("YUKLEME_SEKLI", ad="FOB İZMİR")
        cls.kosul = _secenek("ODEME_KOSULU", ad="PEŞİN")

    # --- servis katmanı -----------------------------------------------------------------

    def test_servis_aday_musteriye_teklif_olusturur(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.assertIsNone(ts.cari_id)
        self.assertEqual(ts.aday_musteri_id, self.aday.pk)
        self.assertEqual(ts.taraf, self.aday)

    def test_servis_aday_musteri_satinalma_teklifinde_reddedilir(self):
        """Aday müşteri yalnız SATIŞ+TEKLİF'te kabul edilir — diğer 4 ekran (satınalma
        teklif/sipariş/irsaliye, satış siparişi) gerçek Cari ister."""
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.ALIS,
                aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
                satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
                kullanici=self.yon)

    def test_servis_aday_musteri_satis_siparisinde_reddedilir(self):
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur=TeklifSiparis.BelgeTur.SIPARIS, yon=TeklifSiparis.Yon.SATIS,
                aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
                satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
                kullanici=self.yon)

    def test_servis_cari_ve_aday_musteri_ayni_anda_reddedilir(self):
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                cari_id=self.cari.pk, aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
                satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
                kullanici=self.yon)

    def test_servis_ikisi_de_bossa_reddedilir(self):
        from core.services.teklif_siparis import TeklifSiparisHatasi, teklif_siparis_olustur
        with self.assertRaises(TeklifSiparisHatasi):
            teklif_siparis_olustur(
                belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                tarih=datetime.date(2026, 9, 13),
                satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
                kullanici=self.yon)

    def test_servis_guncelle_cariden_adaya_gecirir(self):
        from core.services.teklif_siparis import teklif_siparis_guncelle, teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            cari_id=self.cari.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_guncelle(
            ts, aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        ts.refresh_from_db()
        self.assertIsNone(ts.cari_id)
        self.assertEqual(ts.aday_musteri_id, self.aday.pk)

    def test_servis_teklifi_proformaya_cevir_aday_icin_calisir(self):
        """Teklif → Proforma aşaması aday müşteride de çalışır — henüz muhasebe/stok'a
        dokunmuyor (bkz. _ADAY_MUSTERI_IZINLI). Cariye dönüşüm zorunluluğu bir sonraki
        adımda, Proforma → Sipariş'te devreye girer."""
        from core.services.teklif_siparis import (
            teklif_siparis_olustur, teklif_siparis_onayla, teklifi_proformaya_cevir,
        )
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(ts, kullanici=self.yon)
        proforma = teklifi_proformaya_cevir(ts, tarih=datetime.date(2026, 9, 13), kullanici=self.yon)
        self.assertIsNone(proforma.cari_id)
        self.assertEqual(proforma.aday_musteri_id, self.aday.pk)

    def test_servis_proformayi_siparise_cevir_aday_icin_engellenir(self):
        from core.services.teklif_siparis import (
            TeklifSiparisHatasi, teklif_siparis_olustur, teklif_siparis_onayla,
            proformayi_siparise_cevir,
        )
        proforma = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(proforma, kullanici=self.yon)
        with self.assertRaisesMessage(
                TeklifSiparisHatasi, "aday müşteriye ait; siparişe çevirmeden önce"):
            proformayi_siparise_cevir(proforma, tarih=datetime.date(2026, 9, 13), kullanici=self.yon)
        self.assertFalse(proforma.donusen_siparisler.filter(silindi=False).exists())

    def test_servis_proformayi_siparise_cevir_aday_sonradan_cariye_donusunce_calisir(self):
        """Proforma açıldığında aday hâlâ adaydı; ARADAN aday Cariye dönüştürülürse (proforma
        kaydının KENDİ cari alanı geriye dönük güncellenmez) sipariş dönüşümü artık engel
        olmadan çalışmalı — aday_musteri.donusen_cari'ye taze bakılır."""
        from core.services.teklif_siparis import (
            teklif_siparis_olustur, teklif_siparis_onayla, proformayi_siparise_cevir,
        )
        proforma = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(proforma, kullanici=self.yon)
        yeni_cari = aday_cariye_donustur(self.aday, kullanici=self.yon)
        siparis = proformayi_siparise_cevir(
            proforma, tarih=datetime.date(2026, 9, 13), kullanici=self.yon)
        self.assertEqual(siparis.cari_id, yeni_cari.pk)
        self.assertIsNone(siparis.aday_musteri_id)
        proforma.refresh_from_db()
        self.assertIsNone(proforma.cari_id)          # proformanın kendisi hâlâ aday'a bağlı
        self.assertEqual(proforma.aday_musteri_id, self.aday.pk)

    def test_model_constraint_ikisi_de_bos_reddedilir(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeklifSiparis.objects.create(
                    belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                    tarih=datetime.date(2026, 9, 13), belge_no="X-2026-0001",
                    created_by=self.yon, updated_by=self.yon)

    def test_model_constraint_ikisi_de_doluysa_reddedilir(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeklifSiparis.objects.create(
                    belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
                    cari=self.cari, aday_musteri=self.aday,
                    tarih=datetime.date(2026, 9, 13), belge_no="X-2026-0002",
                    created_by=self.yon, updated_by=self.yon)

    # --- view/form katmanı ---------------------------------------------------------------

    def _post_govde(self, **over):
        govde = {
            "karsi_taraf_tip": "aday", "aday_musteri": self.aday.pk,
            "tarih": "2026-09-13", "para_birimi": "USD",
            "yukleme_sekli": self.sekil.pk, "odeme_kosulu": self.kosul.pk,
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.urun.pk, "form-0-dahil": "on",
            "form-0-iskonto_yuzdesi": "15", "form-0-birim_fiyat": "350",
        }
        govde.update(over)
        return govde

    def test_aday_meta_pb_ve_iskonto_dogru_ve_donusmus_haric(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        meta = r.context["aday_meta"]
        self.assertEqual(meta[str(self.aday.pk)], {"pb": "USD", "iskonto": 15.0})
        self.assertNotIn(str(self.donusmus_aday.pk), meta)

    def test_aday_dropdown_donusmus_adayi_icermez(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_ekle"))
        qs = r.context["bform"].fields["aday_musteri"].queryset
        self.assertIn(self.aday, qs)
        self.assertNotIn(self.donusmus_aday, qs)

    def test_post_aday_musteriye_teklif_olusturur(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde())
        ts = TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", aday_musteri=self.aday).latest("id")
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertIsNone(ts.cari_id)
        self.assertEqual(ts.para_birimi, "USD")

    def test_post_aday_tip_ama_aday_secilmezse_hata(self):
        self.client.force_login(self.yon)
        r = self.client.post(
            reverse("core:satis_teklif_ekle"), self._post_govde(aday_musteri=""))
        self.assertContains(r, "Aday müşteri seçin.")

    def test_post_cari_tip_ama_cari_secilmezse_hata(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:satis_teklif_ekle"), self._post_govde(
            karsi_taraf_tip="cari", aday_musteri="", cari=""))
        self.assertContains(r, "Cari seçin.")

    def test_duzenle_aday_teklifi_dogru_initial_ile_acilir(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklif_duzenle", args=[ts.pk]))
        bform = r.context["bform"]
        self.assertEqual(bform["karsi_taraf_tip"].value(), "aday")
        self.assertEqual(bform["aday_musteri"].value(), self.aday.pk)

    def test_duzenle_adaydan_cariye_gecis_calisir(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        govde = self._post_govde(karsi_taraf_tip="cari", aday_musteri="", cari=self.cari.pk,
                                 para_birimi="TRY")
        r = self.client.post(
            reverse("core:satis_teklif_duzenle", args=[ts.pk]), govde)
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[ts.pk]))
        ts.refresh_from_db()
        self.assertEqual(ts.cari_id, self.cari.pk)
        self.assertIsNone(ts.aday_musteri_id)

    def test_detay_sayfasi_aday_teklifini_kirilmadan_gosterir(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[ts.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "POTANSİYEL MUSTERİ LTD")
        self.assertContains(r, "Aday Müşteri")

    def test_pdf_aday_teklifi_icin_kirilmadan_calisir_tr_ve_en(self):
        """Kritik regresyon: ts.cari None olduğunda satis_teklif_pdf_baglam/teklif_siparis_pdf
        AttributeError atmamalı — taraf property'si üzerinden çözülmeli (bkz. views.py)."""
        from core.services.teklif_siparis import teklif_siparis_olustur
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        for dil in ("tr", "en"):
            r = self.client.get(reverse("core:teklif_siparis_pdf", args=[ts.pk]) + f"?dil={dil}")
            self.assertEqual(r.status_code, 200, dil)
            self.assertEqual(r["Content-Type"], "application/pdf")

    def test_proformaya_cevir_view_aday_teklifinde_calisir(self):
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(ts, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:teklif_proformaya_cevir", args=[ts.pk]), follow=True)
        proforma = TeklifSiparis.objects.get(kaynak_teklif=ts)
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[proforma.pk]))
        self.assertEqual(proforma.aday_musteri_id, self.aday.pk)

    def test_siparise_cevir_view_aday_proformasinda_hata_mesaji_gosterir(self):
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        proforma = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(proforma, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:proforma_siparise_cevir", args=[proforma.pk]), follow=True)
        self.assertContains(r, "aday müşteriye ait; siparişe çevirmeden önce")
        self.assertFalse(proforma.donusen_siparisler.filter(silindi=False).exists())

    def test_liste_arama_aday_unvaniyla_bulur(self):
        from core.services.teklif_siparis import teklif_siparis_olustur
        teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:satis_teklifleri"), {"ara": "POTANSİYEL"})
        self.assertContains(r, "POTANSİYEL MUSTERİ LTD")
