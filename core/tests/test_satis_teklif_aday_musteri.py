"""Satış Teklifi/Proforması — Aday Müşteriye (CRM lead) belge verme: normal Cari akışıyla
birebir aynı ekranlardan, cari/aday_musteri karşılıklı dışlayıcı
(ck_teklif_siparis_cari_xor_aday_musteri). Aday müşteri yalnız Teklif ve Proforma
aşamalarında geçerli — Sipariş (dolayısıyla İrsaliye/Fatura) zinciri gerçek Cari ister
(bkz. proformayi_siparise_cevir guard'ı); aday bu arada Cariye dönüştürülürse (bkz.
core.services.aday_donustur.yeni_cari_ac) engel kalkar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import (
    AdayAsamaTanim, AdayTipTanim, Birim, Cari, HesapPlani, KdvOrani, Kategori, TanimSecenegi,
    TeklifSiparis,
)
from core.services.aday import aday_musteri_olustur as _aday_musteri_olustur_ham
from core.tests.aday_yardimci import varsayilan_kaynak_id


def aday_musteri_olustur(**kw):
    kw.setdefault("tip_id", AdayTipTanim.objects.get(sistem_kodu="ADAY").pk)
    kw.setdefault("asama_id", AdayAsamaTanim.objects.get(sistem_kodu="YENI").pk)
    kw.setdefault("kategori_id", varsayilan_kaynak_id())
    return _aday_musteri_olustur_ham(**kw)
from core.services.aday_donustur import yeni_cari_ac
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
        cls.donusmus_aday.cari = cls.cari
        cls.donusmus_aday.save(update_fields=["cari"])

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
            taslak_olarak_kaydet=True, kullanici=self.yon)
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
            teklif_siparis_olustur, teklif_kabul_et, teklifi_proformaya_cevir,
        )
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_kabul_et(ts, kullanici=self.yon)
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
        olmadan çalışmalı — aday_musteri.cari'ye taze bakılır."""
        from core.services.teklif_siparis import (
            teklif_siparis_olustur, teklif_siparis_onayla, proformayi_siparise_cevir,
        )
        proforma = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.PROFORMA, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_siparis_onayla(proforma, kullanici=self.yon)
        yeni_cari = yeni_cari_ac(self.aday, kullanici=self.yon, unvan=self.aday.unvan,
                                para_birimi=self.aday.para_birimi)
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
            taslak_olarak_kaydet=True, kullanici=self.yon)
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
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_kabul_et
        ts = teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "350"}],
            kullanici=self.yon)
        teklif_kabul_et(ts, kullanici=self.yon)
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


class TeklifCrmAsamaOtomasyonuTest(TestCase):
    """Satış Teklifi Gönderildi/Kabul olunca aday müşterinin CRM aşaması otomatik ilerler —
    yalnız TEKLIF/SIPARIS rollü bir aşama ATANMIŞSA (bkz. core.services.aday_donustur.
    aday_asama_ilerlet, _cari_rollu_asama ile aynı 'rol yoksa no-op' deseni)."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tcaoyon", password="x")
        cls.aday = aday_musteri_olustur(
            unvan="asama test aday", para_birimi="TRY", kullanici=cls.yon)
        ust = Kategori.objects.create(kod="151", ad="MAMUL2", created_by=cls.yon,
                                      updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="11", ad="MERDİVEN2", ust=ust,
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET2", kisa_ad="AD2", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel2",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.urun = stok_olustur(
            ad="asama test urun", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, fiyat_try="1000", kullanici=cls.yon)

    def _teklif(self, **over):
        from core.services.teklif_siparis import teklif_siparis_olustur
        govde = dict(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            aday_musteri_id=self.aday.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "1000"}],
            kullanici=self.yon)
        govde.update(over)
        return teklif_siparis_olustur(**govde)

    def test_rol_atanmamissa_asama_degismez(self):
        onceki_asama_id = self.aday.asama_id
        self._teklif()
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, onceki_asama_id)

    def test_gonderilince_teklif_rollu_asamaya_gecer(self):
        from core.services.aday_tanim import asama_olustur
        teklif_asamasi = asama_olustur(
            ad="TEKLİF VERİLDİ", rol=AdayAsamaTanim.Rol.TEKLIF, kullanici=self.yon)
        self._teklif()                                   # varsayılan: doğrudan GONDERILDI
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, teklif_asamasi.pk)
        self.assertTrue(
            self.aday.aktiviteler.filter(aciklama__contains="Aşama:").exists())

    def test_taslak_olarak_kaydedilince_asama_degismez_gonderilince_degisir(self):
        from core.services.aday_tanim import asama_olustur
        from core.services.teklif_siparis import teklif_gonder
        teklif_asamasi = asama_olustur(
            ad="TEKLİF VERİLDİ 2", rol=AdayAsamaTanim.Rol.TEKLIF, kullanici=self.yon)
        onceki_asama_id = self.aday.asama_id
        t = self._teklif(taslak_olarak_kaydet=True)
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, onceki_asama_id)   # taslakken değişmez
        teklif_gonder(t, kullanici=self.yon)
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, teklif_asamasi.pk)

    def test_kabul_edilince_siparis_rollu_asamaya_gecer(self):
        from core.services.aday_tanim import asama_olustur
        from core.services.teklif_siparis import teklif_kabul_et
        siparis_asamasi = asama_olustur(
            ad="SİPARİŞ BEKLENİYOR", rol=AdayAsamaTanim.Rol.SIPARIS, kullanici=self.yon)
        t = self._teklif()
        teklif_kabul_et(t, kullanici=self.yon)
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, siparis_asamasi.pk)

    def test_cariye_bagli_teklifte_otomasyon_calismaz(self):
        """taraf bir Cari ise (aday_musteri yok) CRM aşama otomasyonu hiç tetiklenmez."""
        from core.services.aday_tanim import asama_olustur
        from core.services.teklif_siparis import teklif_siparis_olustur
        asama_olustur(ad="TEKLİF VERİLDİ 3", rol=AdayAsamaTanim.Rol.TEKLIF, kullanici=self.yon)
        _hesap("120.09", "ASAMA CARI")
        cari = Cari.objects.create(kod="C9", unvan="ASAMA CARI", muhasebe_kodu="120.09",
                                   created_by=self.yon, updated_by=self.yon)
        onceki_asama_id = self.aday.asama_id
        teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            cari_id=cari.pk, tarih=datetime.date(2026, 9, 13),
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "1000"}],
            kullanici=self.yon)
        self.aday.refresh_from_db()
        self.assertEqual(self.aday.asama_id, onceki_asama_id)


class TeklifSuresiDolanlariIsaretleTest(TestCase):
    """core.services.teklif_siparis.teklif_suresi_dolanlari_isaretle — bkz.
    core/management/commands/teklif_suresi_kontrol.py (günlük cron)."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("tsdiyon", password="x")
        _hesap("120.08", "SÜRESİ DOLAN MÜŞTERİ")
        cls.cari = Cari.objects.create(kod="C8", unvan="SÜRESİ DOLAN MÜŞTERİ",
                                       muhasebe_kodu="120.08",
                                       created_by=cls.yon, updated_by=cls.yon)
        ust = Kategori.objects.create(kod="152", ad="MAMUL3", created_by=cls.yon,
                                      updated_by=cls.yon)
        cls.kat = Kategori.objects.create(kod="12", ad="MERDİVEN3", ust=ust,
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.birim = Birim.objects.create(ad="ADET3", kisa_ad="AD3", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=Decimal("20"), aciklama="Genel3",
                                          created_by=cls.yon, updated_by=cls.yon)
        cls.urun = stok_olustur(
            ad="süresi dolan urun", kategori_id=cls.kat.pk, uretim_birimi_id=cls.birim.pk,
            fatura_birimi_id=cls.birim.pk, kdv_id=cls.kdv.pk,
            satis_urunu=True, fiyat_try="1000", kullanici=cls.yon)

    def _teklif(self, gecerlilik):
        from core.services.teklif_siparis import teklif_siparis_olustur
        return teklif_siparis_olustur(
            belge_tur=TeklifSiparis.BelgeTur.TEKLIF, yon=TeklifSiparis.Yon.SATIS,
            cari_id=self.cari.pk, tarih=datetime.date(2026, 9, 1),
            gecerlilik_teslim_tarihi=gecerlilik,
            satirlar=[{"stok_id": self.urun.pk, "miktar": "1", "birim_fiyat": "1000"}],
            kullanici=self.yon)

    def test_gecmis_tarihli_gonderilmis_teklif_isaretlenir(self):
        from core.services.teklif_siparis import teklif_suresi_dolanlari_isaretle
        t = self._teklif(datetime.date(2026, 9, 10))
        sayisi = teklif_suresi_dolanlari_isaretle(bugun=datetime.date(2026, 9, 20))
        self.assertEqual(sayisi, 1)
        t.refresh_from_db()
        self.assertEqual(t.durum, "SURESI_DOLDU")

    def test_gelecek_tarihli_teklif_isaretlenmez(self):
        from core.services.teklif_siparis import teklif_suresi_dolanlari_isaretle
        t = self._teklif(datetime.date(2026, 9, 30))
        teklif_suresi_dolanlari_isaretle(bugun=datetime.date(2026, 9, 20))
        t.refresh_from_db()
        self.assertEqual(t.durum, "GONDERILDI")

    def test_taslak_teklif_isaretlenmez(self):
        from core.services.teklif_siparis import teklif_suresi_dolanlari_isaretle
        t = self._teklif(datetime.date(2026, 9, 10))
        t.durum = "TASLAK"
        t.save(update_fields=["durum"])
        teklif_suresi_dolanlari_isaretle(bugun=datetime.date(2026, 9, 20))
        t.refresh_from_db()
        self.assertEqual(t.durum, "TASLAK")

    def test_suresi_dolmus_teklif_hala_kabul_edilebilir(self):
        from core.services.teklif_siparis import teklif_kabul_et, teklif_suresi_dolanlari_isaretle
        t = self._teklif(datetime.date(2026, 9, 10))
        teklif_suresi_dolanlari_isaretle(bugun=datetime.date(2026, 9, 20))
        t.refresh_from_db()
        teklif_kabul_et(t, kullanici=self.yon)
        t.refresh_from_db()
        self.assertEqual(t.durum, "KABUL")

    def test_gecerlilik_tarihi_yoksa_isaretlenmez(self):
        from core.services.teklif_siparis import teklif_suresi_dolanlari_isaretle
        t = self._teklif(None)
        teklif_suresi_dolanlari_isaretle(bugun=datetime.date(2026, 9, 20))
        t.refresh_from_db()
        self.assertEqual(t.durum, "GONDERILDI")
