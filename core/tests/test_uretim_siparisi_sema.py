"""Üretim siparişi ŞEMASI (plan adım 3 — docs/uretim-siparisi-plan.md): ``UretimEmri`` durum/revizyon/kapanış alanları ve ``ÜS-yyyy-nnnn`` numarası
(eski UE- kayıtlar aynen, sıra ortak), ``UretimEmriKalemi.siparis_kalem`` + ``eldeki_ayrilan``, ``IstasyonEmri`` (sipariş×operasyon tek aktif satır,
çalıştırma biriminde planlanan/tamamlanan, kalan), ``OperasyonKaydi.istasyon_emri``, ``UretimEmriRevizyon`` (sipariş içinde sıralı, JSON detay) ve
SATIŞ siparişi SERVİS kilitleri: aktif üretim siparişi varken düzenleme / onay geri alma / iptal reddedilir, iptal edilmiş üretim siparişi kilidi kaldırır."""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import IstasyonEmri, OperasyonKaydi, StokHareket, TeklifSiparis, TeklifSiparisKalem, UretimEmri, UretimEmriKalemi, UretimEmriRevizyon
from core.services import stok_ayirma
from core.services import teklif_siparis as ts_servis
from core.services.hareket import hareket_ekle
from core.services.uretim import (
    UretimHatasi, operasyon_kaydi_olustur, siparisten_uretim_emri_olustur, ureten_operasyon, uretim_emri_iptal, uretim_emri_olustur,
)
from core.tests.test_teklif_siparis import SiparisUretimFixture

D = Decimal


class SemaTest(SiparisUretimFixture, TestCase):
    """Fixture: hammadde → uretilebilir (ISTU istasyonu, 1:1), satış siparişi yardımcısı ``_siparis``."""

    def test_us_numarasi_ve_varsayilan_alanlar(self):
        eski = UretimEmri.objects.create(yil=2026, sira=1, no="UE-2026-0001", depo=self.depo, tarih=date(2026, 10, 1))   # eski kayıt aynen
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.uretilebilir.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=date(2026, 10, 2))
        self.assertEqual((emir.no, emir.durum, emir.revizyon_no, emir.kapanis_tarihi), ("ÜS-2026-0002", UretimEmri.Durum.ACIK, 0, None))   # sıra ortak
        self.assertEqual(eski.no, "UE-2026-0001")
        kalem = emir.kalemler.get()
        self.assertEqual((kalem.siparis_kalem_id, kalem.eldeki_ayrilan), (None, D("0")))

    def test_siparisten_acilan_kalem_siparis_kalemine_baglanabilir(self):
        sip = self._siparis(iki_kalemli=False)
        emir = siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=date(2026, 10, 2),
                                              kalem_secimleri=[{"kalem_id": sip.kalemler.get().pk, "hedef_miktar": "5"}])
        kalem = emir.kalemler.get()
        kalem.siparis_kalem = sip.kalemler.get()
        kalem.eldeki_ayrilan = D("1.5")
        kalem.save()
        self.assertEqual(UretimEmriKalemi.objects.get(pk=kalem.pk).siparis_kalem.teklif_siparis_id, sip.pk)
        self.assertEqual([k.pk for k in sip.kalemler.get().uretim_emri_kalemleri.all()], [kalem.pk])

    def test_istasyon_emri_kisitlar_ve_kalan(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.uretilebilir.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=date(2026, 10, 2))
        op = ureten_operasyon(self.uretilebilir)
        ie = emir.istasyon_emirleri.get()                                                  # açılış servisi emri yazar
        self.assertEqual((ie.no, ie.planlanan, ie.tamamlanan, ie.durum), ("IE-2026-0001", D("2"), D("0"), IstasyonEmri.Durum.BEKLIYOR))
        ie.planlanan, ie.tamamlanan = D("5"), D("2")
        self.assertEqual((ie.kalan, str(ie)), (D("3"), "IE-2026-0001"))
        ie.tamamlanan = D("9")
        self.assertEqual(ie.kalan, D("0"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IstasyonEmri.objects.create(uretim_emri=emir, operasyon=op, istasyon=op.istasyon, yil=2026, sira=2, no="IE-2026-0002", planlanan=D("1"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IstasyonEmri.objects.filter(pk=ie.pk).update(silindi=True)                       # aktif-op kısıtını devre dışı bırak: yalnız (yıl, sıra) çakışması kalsın
                IstasyonEmri.objects.create(uretim_emri=emir, operasyon=op, istasyon=op.istasyon, yil=2026, sira=1, no="IE-2026-0001", planlanan=D("1"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IstasyonEmri.objects.create(uretim_emri=emir, operasyon=op, istasyon=op.istasyon, yil=2026, sira=3, no="IE-2026-0003", planlanan=D("-1"))
        kayit = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 3), hedef_cikti_miktari=D("1"), uretim_emri=emir)
        kayit.istasyon_emri = ie
        kayit.save()
        self.assertEqual([k.pk for k in ie.kayitlar.all()], [kayit.pk])
        self.assertIsNone(OperasyonKaydi.objects.filter(uretim_emri__isnull=True).first().istasyon_emri_id if OperasyonKaydi.objects.filter(uretim_emri__isnull=True).exists() else None)
        self.assertEqual(list(emir.istasyon_emirleri.values_list("no", flat=True)), ["IE-2026-0001"])
        self.assertEqual(emir.istasyon_emirleri.get().kayitlar.get().pk, kayit.pk)

    def test_revizyon_kaydi(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.uretilebilir.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=date(2026, 10, 2))
        r = emir.revizyonlar.get()                                                         # açılış servisi 0 numaralı kaydı yazar
        self.assertEqual((str(r), r.tur, r.detay["kalemler"][0]["miktar"]), (f"{emir.pk} #0 ACILIS", "ACILIS", "2"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                UretimEmriRevizyon.objects.create(uretim_emri=emir, no=0, tur=UretimEmriRevizyon.Tur.REVIZE, tarih=date(2026, 10, 3))
        UretimEmriRevizyon.objects.create(uretim_emri=emir, no=1, tur=UretimEmriRevizyon.Tur.REVIZE, tarih=date(2026, 10, 3))
        self.assertEqual([x.no for x in emir.revizyonlar.all()], [0, 1])


class SiparisKilitTest(SiparisUretimFixture, TestCase):
    def _emirli_siparis(self):
        sip = self._siparis(iki_kalemli=False)
        emir = siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=date(2026, 10, 2),
                                              kalem_secimleri=[{"kalem_id": sip.kalemler.get().pk, "hedef_miktar": "5"}])
        return sip, emir

    def test_servis_kilitleri(self):
        sip, emir = self._emirli_siparis()
        with self.assertRaisesMessage(ts_servis.TeklifSiparisHatasi, "üretim siparişine dönüştürülmüş; onayı geri alınamaz"):
            ts_servis.teklif_siparis_onayi_geri_al(sip)
        with self.assertRaisesMessage(ts_servis.TeklifSiparisHatasi, "üretim siparişine dönüştürülmüş; iptal edilemez"):
            ts_servis.teklif_siparis_iptal(sip)
        sip.refresh_from_db()
        self.assertEqual((sip.durum, sip.silindi), (TeklifSiparis.Durum.ONAYLI, False))
        # onay geri alınsa bile (taslağa düşürülmüş sayalım) düzenleme de kilitli
        TeklifSiparis.objects.filter(pk=sip.pk).update(durum=TeklifSiparis.Durum.TASLAK)
        sip.refresh_from_db()
        kalem = sip.kalemler.get()
        with self.assertRaisesMessage(ts_servis.TeklifSiparisHatasi, "üretim siparişine dönüştürülmüş; düzenlenemez"):
            ts_servis.teklif_siparis_guncelle(sip, cari_id=sip.cari_id, tarih=sip.tarih, satirlar=[{"stok_id": kalem.stok_id, "miktar": "5", "birim_fiyat": "1"}])

    def test_iptal_edilmis_uretim_siparisi_kilidi_kaldirir(self):
        sip, emir = self._emirli_siparis()
        UretimEmri.objects.filter(pk=emir.pk).update(durum=UretimEmri.Durum.IPTAL)
        ts_servis.teklif_siparis_onayi_geri_al(sip)
        sip.refresh_from_db()
        self.assertEqual(sip.durum, TeklifSiparis.Durum.TASLAK)
        UretimEmri.objects.filter(pk=emir.pk).update(durum=UretimEmri.Durum.ACIK, silindi=True)
        ts_servis.teklif_siparis_iptal(sip)                                                   # silinmiş üretim siparişi de kilitlemez
        sip.refresh_from_db()
        self.assertTrue(sip.silindi)


class SiparistenAcilisTest(SiparisUretimFixture, TestCase):
    """Siparişten ÜS açılışı (adım 4): kalem bağı, miktar = sipariş kalem miktarı (karar 6/10), mükerrer stok satırı, iptalden sonra yeni ÜS."""

    def _ac(self, sip, miktar="5", **ek):
        return siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=date(2026, 10, 2),
                                              kalem_secimleri=[{"kalem_id": k.pk, "hedef_miktar": miktar} for k in sip.kalemler.filter(stok=self.uretilebilir)], **ek)

    def test_kalem_baglanir_ve_miktar_esit_olmali(self):
        sip = self._siparis(iki_kalemli=False)
        with self.assertRaisesMessage(UretimHatasi, "sipariş miktarına (5) eşit olmalı"):
            self._ac(sip, "4")
        with self.assertRaisesMessage(UretimHatasi, "sipariş miktarına (5) eşit olmalı"):
            self._ac(sip, "6")
        self.assertFalse(UretimEmri.objects.exists())
        emir = self._ac(sip, "5")
        k = emir.kalemler.get()
        self.assertEqual((k.siparis_kalem_id, k.hedef_miktar), (sip.kalemler.get().pk, D("5")))
        self.assertEqual(emir.istasyon_emirleri.get().planlanan, D("5"))
        self.assertFalse(OperasyonKaydi.objects.filter(uretim_emri=emir).exists())

    def test_ayni_kalem_iki_kez_secilemez_ve_mukerrer_stok_satiri_toplanir(self):
        sip = self._siparis(iki_kalemli=False)
        kalem = sip.kalemler.get()
        with self.assertRaisesMessage(UretimHatasi, "birden fazla kez"):
            siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=date(2026, 10, 2),
                                           kalem_secimleri=[{"kalem_id": kalem.pk, "hedef_miktar": "5"}, {"kalem_id": kalem.pk, "hedef_miktar": "5"}])
        # aynı stok iki ayrı sipariş satırında: iki ÜS kalemi, netleme toplanmış talepten
        kalem2 = TeklifSiparisKalem.objects.create(teklif_siparis=sip, stok=self.uretilebilir, miktar=D("3"), birim_fiyat=D("100"), kdv=kalem.kdv)
        emir = siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=date(2026, 10, 2),
                                              kalem_secimleri=[{"kalem_id": kalem.pk, "hedef_miktar": "5"}, {"kalem_id": kalem2.pk, "hedef_miktar": "3"}])
        self.assertEqual([(k.siparis_kalem_id, k.hedef_miktar) for k in emir.kalemler.all()], [(kalem.pk, D("5")), (kalem2.pk, D("3"))])
        self.assertEqual(emir.istasyon_emirleri.get().planlanan, D("8"))

    def test_eldeki_mamul_ayrilir_kalem_snapshot(self):
        hareket_ekle(stok_id=self.uretilebilir.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 1), tur=StokHareket.Tur.GIRIS, miktar=D("2"))
        sip = self._siparis(iki_kalemli=False)
        emir = self._ac(sip, "5")
        self.assertEqual(emir.kalemler.get().eldeki_ayrilan, D("2"))
        self.assertEqual(stok_ayirma.ayrilan_miktar(emir, self.uretilebilir), D("2"))
        self.assertEqual(emir.istasyon_emirleri.get().planlanan, D("3"))                      # net 3
        self.assertEqual(emir.revizyonlar.get().detay["ayirmalar"], [{"stok": "Su1", "miktar": "2"}])

    def test_iptalden_sonra_yeni_siparis_acilir(self):
        sip = self._siparis(iki_kalemli=False)
        emir = self._ac(sip, "5")
        with self.assertRaisesMessage(UretimHatasi, "zaten bir üretim siparişi açılmış"):
            self._ac(sip, "5")
        uretim_emri_iptal(emir)
        emir2 = self._ac(sip, "5")
        self.assertNotEqual(emir2.pk, emir.pk)
        self.assertEqual(sip.uretim_emirleri.count(), 2)
