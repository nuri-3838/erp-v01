"""Üretim siparişi ŞEMASI (plan adım 3 — docs/uretim-siparisi-plan.md): ``UretimEmri`` durum/revizyon/kapanış alanları ve ``ÜS-yyyy-nnnn`` numarası
(eski UE- kayıtlar aynen, sıra ortak), ``UretimEmriKalemi.siparis_kalem`` + ``eldeki_ayrilan``, ``IstasyonEmri`` (sipariş×operasyon tek aktif satır,
çalıştırma biriminde planlanan/tamamlanan, kalan), ``OperasyonKaydi.istasyon_emri``, ``UretimEmriRevizyon`` (sipariş içinde sıralı, JSON detay) ve
SATIŞ siparişi SERVİS kilitleri: aktif üretim siparişi varken düzenleme / onay geri alma / iptal reddedilir, iptal edilmiş üretim siparişi kilidi kaldırır."""
from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import IstasyonEmri, OperasyonKaydi, TeklifSiparis, UretimEmri, UretimEmriKalemi, UretimEmriRevizyon
from core.services import teklif_siparis as ts_servis
from core.services.uretim import operasyon_kaydi_olustur, siparisten_uretim_emri_olustur, ureten_operasyon, uretim_emri_olustur
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
        ie = IstasyonEmri.objects.create(uretim_emri=emir, operasyon=op, istasyon=op.istasyon, yil=2026, sira=1, no="IE-2026-0001", seviye=0,
                                         planlanan=D("5"), tamamlanan=D("2"))
        self.assertEqual((ie.kalan, ie.durum, str(ie)), (D("3"), IstasyonEmri.Durum.BEKLIYOR, "IE-2026-0001"))
        ie.tamamlanan = D("9")
        self.assertEqual(ie.kalan, D("0"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IstasyonEmri.objects.create(uretim_emri=emir, operasyon=op, istasyon=op.istasyon, yil=2026, sira=2, no="IE-2026-0002", planlanan=D("1"))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
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

    def test_revizyon_kaydi(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.uretilebilir.pk, "hedef_miktar": "2"}], depo_id=self.depo.pk, tarih=date(2026, 10, 2))
        r = UretimEmriRevizyon.objects.create(uretim_emri=emir, no=0, tur=UretimEmriRevizyon.Tur.ACILIS, tarih=date(2026, 10, 2),
                                              detay={"kalemler": [{"stok": self.uretilebilir.kod, "once": None, "sonra": "2"}]})
        self.assertEqual((str(r), r.detay["kalemler"][0]["sonra"]), (f"{emir.pk} #0 ACILIS", "2"))
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
