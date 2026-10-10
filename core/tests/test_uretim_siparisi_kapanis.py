"""Üretim siparişi KAPANIŞ (plan adım 8 — docs/uretim-siparisi-plan.md): sevk = depolu, onaylı satış faturası. Gerçek sipariş→fatura ekranı/servisiyle:
depolu fatura mamul ayırmasını düşürür (``sevk_dusen``) ve tüm istasyon emirleri bitmişse ÜS KAPALI olur; fatura silinince ayırma geri gelir, ÜS yeniden açılır;
deposuz fatura ÜS'ye dokunmaz ve uyarı verir; kısmen sevk edilmiş ÜS'de revize sevk miktarının altına düşürülemez (gerçek fatura fixture'ıyla)."""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, Fatura, IsIstasyonu, Kategori, Stok, StokHareket, TeklifSiparis, UretimEmri
from core.services import fatura as fatura_servis
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.services.uretim import (
    UretimHatasi, istasyon_emri_kayit_ac, operasyon_kaydi_onayla, operasyon_olustur, siparisten_uretim_emri_olustur, uretim_emri_revize,
)
from core.tests import test_teklif_siparis as tts          # modül olarak içe aktarılır: sınıf test koşucusuna ikinci kez girmesin

D = Decimal
GUN = date(2026, 6, 28)


class KapanisTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        tts.TeklifSiparisFaturayaCevirTest.setUpTestData.__func__(cls)                  # hesaplar, KDV, satış tipi, müşteri, depo DF1, cls.stok (Sf1)
        alt = cls.stok.kategori
        birim = cls.stok.uretim_birimi
        cls.ham = Stok.objects.create(kod="Sf-HAM", ad="HAMMADDE", kategori=alt, kdv=cls.kdv, uretim_birimi=birim, fatura_birimi=birim, satinalma_urunu=True,
                                      uretim_urunu=False)
        cls.stok.satis_urunu = True
        cls.stok.save()
        ist = IsIstasyonu.objects.create(kod="KPN", ad="KAPANIŞ İSTASYONU")
        cls.op = operasyon_olustur(istasyon_id=ist.pk, cikti_id=cls.stok.pk, cikti_miktar=D("1"), satirlar=[(cls.ham, D("1"))])

    def setUp(self):
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("100"), giris_tutar_try=D("1000"))
        self.client.force_login(self.yon)

    def siparis(self, miktar="3"):
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="SATIS", cari_id=self.musteri.pk, tarih=GUN,
                                     satirlar=[{"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": "50"}], kullanici=self.yon)
        return teklif_siparis_onayla(sip, kullanici=self.yon)

    def emir_ac(self, sip):
        return siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=GUN,
                                              kalem_secimleri=[{"kalem_id": k.pk, "hedef_miktar": k.miktar} for k in sip.kalemler.filter(silindi=False)])

    def uret(self, emir):
        for ie in emir.istasyon_emirleri.exclude(durum="IPTAL"):
            operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie))

    def fatura_kes(self, sip, miktar="3", depo=True):
        r = self.client.post(reverse("core:siparis_faturaya_cevir", args=[sip.pk]), {
            "tip": self.satis_tip.pk, "cari": self.musteri.pk, "tarih": "2026-06-28", "para_birimi": "TRY", "depo": self.depo.pk if depo else "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": miktar, "form-0-birim_fiyat": "50"}, follow=True)
        sip.refresh_from_db()
        return r, [str(m) for m in r.context["messages"]]


class KapanisTest(KapanisTaban):
    def test_depolu_fatura_ayirmayi_dusurur_ve_us_kapanir_fatura_silinince_acilir(self):
        sip = self.siparis()
        emir = self.emir_ac(sip)
        self.uret(emir)
        self.assertEqual((sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (D("3"), D("3")))
        r, mesajlar = self.fatura_kes(sip)
        self.assertIsNotNone(sip.fatura_id)
        self.assertFalse(any("kapanmadı" in m or "bitmemiş" in m for m in mesajlar), mesajlar)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.kapanis_tarihi is not None, emir.sevk_dusen), (UretimEmri.Durum.KAPALI, True, {str(self.stok.pk): "3"}))
        self.assertEqual((eldeki_miktar(self.stok), sa.ayrilan_miktar(emir, self.stok)), (D("0"), D("0")))     # stok çıktı, ayırma düştü
        self.assertEqual([x.tur for x in emir.revizyonlar.all()][-1], "KAPANIS")
        with self.assertRaisesMessage(UretimHatasi, "Yalnız açık"):
            uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "2"}])
        fatura_servis.fatura_sil(sip.fatura)                                                              # geri alma: ÜS yeniden açılır, ayırma döner
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.kapanis_tarihi, emir.sevk_dusen), (UretimEmri.Durum.ACIK, None, {}))
        self.assertEqual((eldeki_miktar(self.stok), sa.ayrilan_miktar(emir, self.stok)), (D("3"), D("3")))
        self.assertEqual([x.tur for x in emir.revizyonlar.all()][-1], "YENIDEN_ACILIS")
        r2, _ = self.fatura_kes(sip)                                                                      # tekrar faturalanınca tekrar kapanır
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)

    def test_deposuz_fatura_us_kapatmaz_uyari_verir(self):
        sip = self.siparis()
        emir = self.emir_ac(sip)
        self.uret(emir)
        r, mesajlar = self.fatura_kes(sip, depo=False)
        self.assertTrue(any("stok çıkışı yazılmadı" in m for m in mesajlar), mesajlar)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (UretimEmri.Durum.ACIK, {}, D("3"), D("3")))
        fatura_servis.fatura_sil(sip.fatura)                                                              # deposuz fatura silinince ÜS aynen kalır
        emir.refresh_from_db()
        self.assertEqual((emir.durum, sa.ayrilan_miktar(emir, self.stok), emir.revizyonlar.count()), (UretimEmri.Durum.ACIK, D("3"), 1))   # yalnız açılış revizyonu

    def test_bitmemis_emirli_kismi_sevk_ve_revize_siniri_gercek_fatura(self):
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("2"), giris_tutar_try=D("100"))
        sip = self.siparis()                                                                              # 3 sipariş; eldeki mamul 2 → ayrılan 2, üretilecek 1
        emir = self.emir_ac(sip)
        self.assertEqual((sa.ayrilan_miktar(emir, self.stok), emir.istasyon_emirleri.get().planlanan), (D("2"), D("1")))
        r, mesajlar = self.fatura_kes(sip, miktar="2")                                                    # eldeki 2'yi sevk et (fatura 2)
        self.assertTrue(any("bitmemiş istasyon emirleri" in m for m in mesajlar), mesajlar)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok)), (UretimEmri.Durum.ACIK, {str(self.stok.pk): "2"}, D("0")))
        self.assertEqual(uretim.sevk_edilen_haritasi(emir), {self.stok.pk: D("2")})                       # GERÇEK fatura satırından
        with self.assertRaisesMessage(UretimHatasi, "sevk/fatura edilmiş miktarın (2) altına düşürülemez"):
            uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "1"}], tarih=GUN)
        emir.refresh_from_db()
        self.assertEqual(emir.revizyon_no, 0)                                                              # hiçbir şey değişmedi
        uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "2"}], tarih=GUN)   # eşit: kabul; 2'nin tamamı sevk edilmiş
        emir.refresh_from_db()
        self.assertEqual(emir.istasyon_emirleri.get().durum, "IPTAL")                                     # artık üretim gerekmez (sevk edileni yeniden üretmez)
        self.assertEqual((emir.durum, emir.kalemler.get(silindi=False).hedef_miktar), (UretimEmri.Durum.ACIK, D("2")))
        uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "3"}], tarih=GUN)   # 1 artırılınca yalnız 1 çalıştırma
        emir.refresh_from_db()
        self.assertEqual(emir.istasyon_emirleri.get().planlanan, D("1"))
        self.assertEqual(sa.ayrilan_miktar(emir, self.ham), D("1"))

    def test_son_emir_bitince_sevk_edilmis_us_kapanir(self):
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("2"), giris_tutar_try=D("100"))
        sip = self.siparis()
        emir = self.emir_ac(sip)
        self.fatura_kes(sip, miktar="2")
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ACIK)
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(emir.istasyon_emirleri.get()))                       # kalan 1 adet üretildi
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        self.assertEqual(emir.revizyonlar.latest("no").tur, "KAPANIS")

    def test_us_olmayan_siparis_faturasi_sessiz(self):
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"), giris_tutar_try=D("150"))
        sip = self.siparis()
        r, mesajlar = self.fatura_kes(sip)
        self.assertIsNotNone(sip.fatura_id)
        self.assertEqual(uretim.siparis_sevk_edildi(sip), "")
        self.assertFalse(any("üretim siparişi" in m for m in mesajlar))
        fatura_servis.fatura_sil(sip.fatura)                                                              # ÜS yokken fatura silme sorunsuz
        self.assertIsNone(TeklifSiparis.objects.get(pk=sip.pk).fatura_id)


class FaturaDuzenlemeKilidiTest(KapanisTaban):
    """ÜS'ye bağlı faturada depo/stok/miktar değiştiren düzenleme engellenir (sevk_dusen ve kapanış buna dayanır); fiyat serbest; ÜS'süz fatura serbest."""

    def guncelle(self, fatura, miktar="3", depo=True, fiyat="50"):
        sat = fatura.satirlar.filter(silindi=False).first()
        return fatura_servis.fatura_guncelle(
            fatura, tip_id=fatura.tip_id, cari_id=fatura.cari_id, tarih=fatura.tarih, depo_id=self.depo.pk if depo else None,
            satirlar=[{"stok_id": sat.stok_id, "kdv_id": sat.kdv_id, "miktar": miktar, "birim_fiyat": fiyat}], kullanici=self.yon)

    def test_depo_miktar_degisikligi_engellenir_fiyat_serbest(self):
        sip = self.siparis()
        emir = self.emir_ac(sip)
        self.uret(emir)
        self.fatura_kes(sip)
        fatura = sip.fatura
        for kw in ({"miktar": "2"}, {"depo": False}):
            with self.assertRaisesMessage(fatura_servis.FaturaHatasi, "Faturayı silip yeniden kesin"):
                self.guncelle(fatura, **kw)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, fatura.satirlar.filter(silindi=False).get().miktar), (UretimEmri.Durum.KAPALI, {str(self.stok.pk): "3"}, D("3")))
        self.guncelle(fatura, fiyat="60")                                                                 # yalnız fiyat: serbest
        self.assertEqual(fatura.satirlar.filter(silindi=False).get().birim_fiyat, D("60"))

    def test_iptal_us_ve_us_siz_fatura_serbest(self):
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"), giris_tutar_try=D("150"))
        sip = self.siparis()
        self.fatura_kes(sip)                                                                              # ÜS yok
        self.guncelle(sip.fatura, miktar="2")
        self.assertEqual(sip.fatura.satirlar.filter(silindi=False).get().miktar, D("2"))
