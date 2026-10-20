"""SATIŞ İRSALİYESİ (2026-10-20): mal sevki ile fatura farklı günlere düşer. Onaylanınca irsaliye gününde stok ÇIKIŞI + satış maliyeti fişi (620/621/623 ↔ stok) yazılır; irsaliyeli faturada
stok/maliyet TEKRAR yazılmaz (çift çıkış yok), yalnız gelir/KDV/cari fişi; çoklu irsaliye → tek fatura; ÜS "Sevk bekliyor" irsaliye ile kapanır (kısmi sevkte açık kalır);
faturaya bağlı irsaliye silinemez/geri alınamaz, bağsız irsaliye silinince stok ve maliyet fişi geri sarılır; irsaliyesiz fatura eskisi gibi çalışır."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Cari, CariSevkAdresi, EkranYetki, Fatura, FaturaTipi, Kur, StokHareket, TeklifSiparis, UretimEmri, YevmiyeFisi
from core.moduller import MODULLER
from core.services import fatura as fatura_servis
from core.services import siparis_teslim, stok_ayirma as sa, uretim
from core.services.fatura import FaturaHatasi, fatura_onayla, fatura_sil
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.teklif_siparis import (TeklifSiparisHatasi, irsaliye_sil, irsaliyeleri_faturaya_cevir, siparisi_irsaliyeye_cevir, teklif_siparis_guncelle,
                                           teklif_siparis_olustur, teklif_siparis_onayi_geri_al, teklif_siparis_onayla)
from core.tarih import tr_bugun
from core.tests.test_fatura import _hesap
from core.tests.test_stok_maliyet_b import MaliyetBTemel
from core.tests.test_uretim_siparisi_kapanis import KapanisTaban

D = date
Dc = Decimal
GECERLI_TCKN = "10000000146"


class SatisIrsaliyeTaban(MaliyetBTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Kur.objects.create(tarih=tr_bugun(), usd_alis=Dc("30"))                 # irsaliyeli fatura taslağı bugünle açılır; onayda USD kuru şart

    def irsaliye(self, tarih, miktar, *, stok=None, fiyat="500", cari=None, onayla=True, **kw):
        i = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="SATIS", cari_id=(cari or self.musteri).pk, tarih=tarih, depo_id=self.da.pk,
                                   satirlar=[{"stok_id": (stok or self.stok).pk, "miktar": miktar, "birim_fiyat": fiyat}], kullanici=None, **kw)
        if onayla:
            teklif_siparis_onayla(i)
            i.refresh_from_db()
        return i

    def hareketler_irsaliye(self, i):
        return list(StokHareket.objects.filter(teklif_siparis_kalem__teklif_siparis=i, silindi=False))

    def maliyet_fisleri(self):
        return YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.STOK_SATIS, silindi=False)

    def faturalandir(self, irsaliyeler, tip=None):
        f, _ = irsaliyeleri_faturaya_cevir(irsaliyeler)
        f.tip = tip or self.satis
        f.save()
        return fatura_onayla(f)


class OnayTest(SatisIrsaliyeTaban):
    def test_onay_irsaliye_gununde_stok_cikisi_ve_maliyet_fisi(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        self.assertTrue(i.belge_no.startswith("SSI-2026-"))
        h, = self.hareketler_irsaliye(i)
        self.assertEqual((h.tur, h.kaynak, h.tarih, h.miktar, h.depo_id, h.tutar_try), ("CIKIS", "IRSALIYE", D(2026, 3, 10), Dc("4"), self.da.pk, Dc("400.00")))
        self.assertEqual(eldeki_miktar(self.stok), Dc("6"))
        fis, = self.maliyet_fisleri()
        self.assertEqual((fis.tarih, h.fis_id), (D(2026, 3, 10), fis.pk))                                    # fiş IRSALİYE tarihli
        self.assertEqual(self.fis_satirlari(fis), {"621": (Dc("400.00"), Dc("0.00")), "153.10": (Dc("0.00"), Dc("400.00"))})

    def test_taslakta_stok_ve_fis_yok(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4", onayla=False)
        self.assertEqual((self.hareketler_irsaliye(i), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), ([], 0, Dc("10")))

    def test_tarih_bazli_yetersiz_stok_reddedilir_ve_hicbir_sey_yazilmaz(self):
        self.alis_yap(D(2026, 3, 12), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4", onayla=False)                # stok 12'sinde geliyor; 10'unda çıkış olamaz
        with self.assertRaisesMessage(TeklifSiparisHatasi, "yetersiz"):
            teklif_siparis_onayla(i)
        i.refresh_from_db()
        self.assertEqual((i.durum, self.maliyet_fisleri().count(), StokHareket.objects.filter(tur="CIKIS", silindi=False).count()), ("TASLAK", 0, 0))

    def test_fiyatsiz_kalem_kabul_edilir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "2", fiyat="")
        self.assertEqual(i.kalemler.get().birim_fiyat, Dc("0"))

    def test_gec_gelen_alis_irsaliye_maliyet_fisini_gunceller(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        self.alis_yap(D(2026, 3, 1), "10", "200")                           # geç gelen daha pahalı alış → ortalama 150
        h, = self.hareketler_irsaliye(i)
        h.refresh_from_db()
        self.assertEqual(h.tutar_try, Dc("600.00"))
        fis, = self.maliyet_fisleri()
        self.assertEqual(self.fis_satirlari(fis)["621"], (Dc("600.00"), Dc("0.00")))
        self.assertEqual(fis.tarih, D(2026, 3, 10))

    def test_onay_geri_al_stok_ve_fis_geri_gider_tekrar_onay_yeniden_yazar(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        teklif_siparis_onayi_geri_al(i)
        i.refresh_from_db()
        self.assertEqual((i.durum, self.hareketler_irsaliye(i), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), ("TASLAK", [], 0, Dc("10")))
        teklif_siparis_onayla(i)
        self.assertEqual((len(self.hareketler_irsaliye(i)), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), (1, 1, Dc("6")))

    def test_irsaliyesiz_fatura_eskisi_gibi_stok_ve_maliyet_yazar(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        f = self.satis_yap(D(2026, 3, 10), "4")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual((h.kaynak, h.tutar_try, eldeki_miktar(self.stok)), ("FATURA", Dc("400.00"), Dc("6")))
        self.assertEqual(self.maliyet_fisleri().count(), 1)


class SevkAlanlariTest(SatisIrsaliyeTaban):
    def test_sevk_bilgileri_kaydedilir_plaka_normalize(self):
        adres = CariSevkAdresi.objects.create(cari=self.musteri, ad="FABRİKA", adres="OSB 1. CAD.", varsayilan=True)
        i = self.irsaliye(D(2026, 3, 10), "1", onayla=False, sevk_adresi_id=adres.pk, plaka="34 abc 123", sofor_adi="  Ali Veli ", sofor_tckn=GECERLI_TCKN)
        self.assertEqual((i.sevk_adresi_id, i.plaka, i.sofor_adi, i.sofor_tckn), (adres.pk, "34ABC123", "Ali Veli", GECERLI_TCKN))

    def test_baska_carinin_sevk_adresi_reddedilir(self):
        diger = Cari.objects.create(kod="120-10-0002", unvan="DİĞER", para_birimi="TRY", muhasebe_kodu="120.10.0001")
        adres = CariSevkAdresi.objects.create(cari=diger, ad="BAŞKA")
        with self.assertRaisesMessage(TeklifSiparisHatasi, "Sevk adresi bu cariye ait değil"):
            self.irsaliye(D(2026, 3, 10), "1", onayla=False, sevk_adresi_id=adres.pk)

    def test_gecersiz_sofor_tckn_reddedilir(self):
        with self.assertRaisesMessage(TeklifSiparisHatasi, "TCKN geçersiz"):
            self.irsaliye(D(2026, 3, 10), "1", onayla=False, sofor_tckn="12345678901")

    def test_alis_irsaliyesinde_sevk_alanlari_yok_sayilir(self):
        i = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), depo_id=self.da.pk,
                                   satirlar=[{"stok_id": self.stok.pk, "miktar": "1", "birim_fiyat": "1"}], plaka="34ABC123", sofor_adi="X")
        self.assertEqual((i.plaka, i.sofor_adi, i.sevk_adresi_id), ("", "", None))


class FaturaBaglantisiTest(SatisIrsaliyeTaban):
    def test_irsaliyeli_fatura_stok_ve_maliyet_yazmaz_gelir_kdv_cari_yazar(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        f = self.faturalandir([i])
        i.refresh_from_db()
        self.assertEqual((f.durum, f.yon, i.fatura_id), ("ONAYLI", "SATIS", f.pk))
        self.assertEqual(StokHareket.objects.filter(tur="CIKIS", silindi=False).count(), 1)                    # hâlâ yalnız irsaliyenin çıkışı
        self.assertEqual((StokHareket.objects.filter(fatura_satir__fatura=f).count(), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), (0, 1, Dc("6")))
        sat = self.fis_satirlari(f.fis)
        self.assertEqual(sat["600"], (Dc("0.00"), Dc("2000.00")))                                              # gelir 4 × 500
        self.assertEqual(sat["391"], (Dc("0.00"), Dc("400.00")))
        self.assertEqual(sat["120.10.0001"], (Dc("2400.00"), Dc("0.00")))
        self.assertEqual(f.satirlar.get().irsaliye_kalem_id, i.kalemler.get().pk)                              # satır kaleme bağlı

    def test_fatura_tarihi_irsaliyeden_sonra_bugunle_acilir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        f, _ = irsaliyeleri_faturaya_cevir([i])
        self.assertGreater(f.tarih, D(2026, 3, 10))

    def test_maliyet_fisi_olmayan_tip_irsaliyeli_faturada_reddedilir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        f, _ = irsaliyeleri_faturaya_cevir([i])
        tip = FaturaTipi.objects.create(ad="SATIŞ-MALİYETSİZ", yon="SATIS", maliyet_fisi="")
        f.tip = tip
        f.save()
        with self.assertRaisesMessage(FaturaHatasi, "satış maliyeti fişli bir SATIŞ tipi"):
            fatura_onayla(f)

    def test_fatura_silinince_irsaliye_stok_ve_maliyeti_kalir_faturalanmamis_olur(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        f = self.faturalandir([i])
        fatura_sil(f)
        i.refresh_from_db()
        self.assertEqual((i.fatura_id, len(self.hareketler_irsaliye(i)), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), (None, 1, 1, Dc("6")))

    def test_faturaya_bagli_irsaliye_geri_alinamaz_ve_silinemez(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        self.faturalandir([i])
        i.refresh_from_db()
        with self.assertRaisesMessage(TeklifSiparisHatasi, "faturaya dönüştürülmüş"):
            teklif_siparis_onayi_geri_al(i)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "önce o faturayı silin"):
            irsaliye_sil(i)
        self.assertEqual((len(self.hareketler_irsaliye(i)), eldeki_miktar(self.stok)), (1, Dc("6")))

    def test_bagsiz_irsaliye_silinince_stok_ve_maliyet_fisi_geri_sarilir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        pk = i.pk
        irsaliye_sil(i)
        self.assertEqual((TeklifSiparis.objects.filter(pk=pk).exists(), self.maliyet_fisleri().count(), YevmiyeFisi.objects.filter(kaynak="STOK_SATIS").count(),
                          StokHareket.objects.filter(tur="CIKIS").count(), eldeki_miktar(self.stok)), (False, 0, 0, 0, Dc("10")))

    def test_coklu_irsaliye_tek_fatura(self):
        self.alis_yap(D(2026, 3, 5), "20", "100")
        i1 = self.irsaliye(D(2026, 3, 10), "4")
        i2 = self.irsaliye(D(2026, 3, 12), "6", fiyat="450")
        f = self.faturalandir([i1, i2])
        satirlar = list(f.satirlar.filter(silindi=False).order_by("id"))
        self.assertEqual([(s.miktar, s.irsaliye_kalem_id) for s in satirlar], [(Dc("4"), i1.kalemler.get().pk), (Dc("6"), i2.kalemler.get().pk)])
        self.assertEqual((StokHareket.objects.filter(tur="CIKIS", silindi=False).count(), self.maliyet_fisleri().count(), eldeki_miktar(self.stok)), (2, 2, Dc("10")))
        for i in (i1, i2):
            i.refresh_from_db()
            self.assertEqual(i.fatura_id, f.pk)
        self.assertEqual(sum(x.alacak for x in f.fis.satirlar.filter(silindi=False, hesap_id="600")), Dc("4700.00"))      # 4×500 + 6×450

    def test_farkli_cari_ve_faturalanmis_irsaliye_reddedilir(self):
        self.alis_yap(D(2026, 3, 5), "20", "100")
        diger = Cari.objects.create(kod="120-10-0002", unvan="DİĞER", para_birimi="TRY", muhasebe_kodu="120.10.0001")
        i1 = self.irsaliye(D(2026, 3, 10), "2")
        i2 = self.irsaliye(D(2026, 3, 10), "2", cari=diger)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "farklı carilere"):
            irsaliyeleri_faturaya_cevir([i1, i2])
        self.faturalandir([i1])
        i1.refresh_from_db()
        with self.assertRaisesMessage(TeklifSiparisHatasi, "zaten bir faturaya"):
            irsaliyeleri_faturaya_cevir([i1])

    def test_taslak_irsaliye_faturaya_cevrilemez(self):
        i = self.irsaliye(D(2026, 3, 10), "2", onayla=False)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "onaylı"):
            irsaliyeleri_faturaya_cevir([i])


class SevkTaban(KapanisTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("621", "SATILAN TİCARİ MALLAR MALİYETİ", grup="GELIR_TABLOSU", kalem="C")           # satış maliyeti fişi (stok hesabı 153.x)
        cls.satis_tip.maliyet_fisi = FaturaTipi.MaliyetFisi.SATIS
        cls.satis_tip.save()
        Kur.objects.create(tarih=tr_bugun(), usd_alis=Dc("30"))


class SiparisSevkTest(SevkTaban):
    """Satış siparişi → kısmi sevk irsaliyeleri; ÜS "Sevk bekliyor" kapanışı."""

    def irsaliye_ac(self, sip, miktar=None, tarih=D(2026, 6, 28)):
        i = siparisi_irsaliyeye_cevir(sip, tarih=tarih, depo_id=self.depo.pk, kullanici=self.yon)
        if miktar is not None:
            teklif_siparis_guncelle(i, cari_id=i.cari_id, tarih=i.tarih, para_birimi="TRY", depo_id=self.depo.pk, kullanici=self.yon,
                                    satirlar=[{"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": "50"}])
        return i

    def onayla(self, i):
        teklif_siparis_onayla(i, kullanici=self.yon)
        i.refresh_from_db()
        return i

    def test_kismi_sevk_us_acik_kalir_tam_sevk_kapanir_geri_alinca_acilir(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        ilerleme = uretim.uretim_emri_ilerleme(emir)
        self.assertTrue(uretim.sevk_bekliyor_mu(emir, ilerleme))
        i1 = self.onayla(self.irsaliye_ac(sip, "2"))
        self.assertEqual(i1.belge_no[:4], "SSI-")
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (UretimEmri.Durum.ACIK, {str(self.stok.pk): "2"}, Dc("1"), Dc("1")))
        self.assertTrue(uretim.sevk_bekliyor_mu(emir, uretim.uretim_emri_ilerleme(emir)))                        # kısmi sevk: hâlâ Sevk bekliyor
        d = siparis_teslim.siparis_durumu(sip)
        self.assertEqual((d["durum"], d["kalan_by_kalem"][sip.kalemler.get().pk]), ("KISMI", Dc("1")))
        i2 = self.onayla(self.irsaliye_ac(sip))                                                                 # kalan 1
        self.assertEqual(i2.kalemler.get().miktar, Dc("1"))
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (UretimEmri.Durum.KAPALI, {str(self.stok.pk): "3"}, Dc("0"), Dc("0")))
        self.assertEqual([x.tur for x in emir.revizyonlar.all()][-1], "KAPANIS")
        self.assertEqual(siparis_teslim.siparis_durumu(sip)["durum"], "TAMAM")
        teklif_siparis_onayi_geri_al(i2, kullanici=self.yon)                                                    # ikinci sevk geri alınır → ÜS yeniden açılır, ayırma döner
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (UretimEmri.Durum.ACIK, {str(self.stok.pk): "2"}, Dc("1"), Dc("1")))
        self.assertEqual([x.tur for x in emir.revizyonlar.all()][-1], "YENIDEN_ACILIS")
        self.assertTrue(uretim.sevk_bekliyor_mu(emir, uretim.uretim_emri_ilerleme(emir)))

    def test_tam_sevk_irsaliye_silinince_ayirma_geri_gelir_us_acilir(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        i = self.onayla(self.irsaliye_ac(sip))
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        irsaliye_sil(i, kullanici=self.yon)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, sa.ayrilan_miktar(emir, self.stok), eldeki_miktar(self.stok)), (UretimEmri.Durum.ACIK, {}, Dc("3"), Dc("3")))
        self.assertEqual(siparis_teslim.siparis_durumu(sip)["durum"], "BEKLIYOR")

    def test_taslak_irsaliye_us_ye_dokunmaz_ve_yenisi_acilmaz(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        i = self.irsaliye_ac(sip, "2")
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, eldeki_miktar(self.stok)), (UretimEmri.Durum.ACIK, {}, Dc("3")))
        with self.assertRaisesMessage(TeklifSiparisHatasi, "irsaliye taslağı var"):
            siparisi_irsaliyeye_cevir(sip, tarih=D(2026, 6, 28), depo_id=self.depo.pk, kullanici=self.yon)
        self.assertEqual(i.durum, "TASLAK")

    def test_tum_miktar_sevk_edilince_yeni_irsaliye_acilmaz(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        self.onayla(self.irsaliye_ac(sip))
        with self.assertRaisesMessage(TeklifSiparisHatasi, "sevk edilmiş"):
            siparisi_irsaliyeye_cevir(sip, tarih=D(2026, 6, 28), depo_id=self.depo.pk, kullanici=self.yon)

    def test_siparis_faturaya_cevir_sevk_edilmis_irsaliye_varsa_once_onu_onerir(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        self.onayla(self.irsaliye_ac(sip, "2"))
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, "core/siparis_fatura_oneri.html")
        self.assertContains(r, "Faturalanmamış irsaliyeler")
        self.assertContains(r, "irsaliyesiz=1")                                                                # kalan (sevk edilmemiş) 1 adet için devam bağlantısı
        r2 = self.client.get(reverse("core:siparis_faturaya_cevir", args=[sip.pk]) + "?irsaliyesiz=1")
        self.assertTemplateUsed(r2, "core/fatura_ekle.html")
        formset = r2.context["formset"]
        self.assertEqual([f.initial["miktar"] for f in formset.forms], [Dc("1")])                              # yalnız sevk edilmemiş kalan

    def test_irsaliyeli_fatura_us_sevkini_ikinci_kez_dusurmez(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        i = self.onayla(self.irsaliye_ac(sip))
        f, _ = irsaliyeleri_faturaya_cevir([i], kullanici=self.yon)
        f.tip = self.satis_tip
        f.save()
        fatura_servis.fatura_onayla(f, kullanici=self.yon)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen, eldeki_miktar(self.stok)), (UretimEmri.Durum.KAPALI, {str(self.stok.pk): "3"}, Dc("0")))   # çift çıkış/düşüm yok
        self.assertEqual(StokHareket.objects.filter(fatura_satir__fatura=f).count(), 0)
        self.assertEqual(uretim.sevk_edilen_haritasi(emir), {self.stok.pk: Dc("3")})                           # irsaliyeye bağlı fatura satırı çifte sayılmaz

    def test_direkt_fatura_yoluyla_sevk_eskisi_gibi(self):
        sip = self.siparis("3")
        emir = self.emir_ac(sip)
        self.uret(emir)
        r, mesajlar = self.fatura_kes(sip)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, emir.sevk_dusen), (UretimEmri.Durum.KAPALI, {str(self.stok.pk): "3"}))
        self.assertEqual(siparis_teslim.siparis_durumu(sip)["durum"], "TAMAM")                                  # depolu fatura sevk sayılır


class EkranMenuTest(SatisIrsaliyeTaban):
    def setUp(self):
        self.yon = User.objects.create_superuser("yon2", password="x")
        self.client.force_login(self.yon)

    def test_menu_faturalar_ve_irsaliyeler_sirasi_ve_satinalmada_kisayol_yok(self):
        modul = next(m for m in MODULLER if m.kod == "FATURALAR")
        self.assertEqual(modul.ad, "Faturalar ve İrsaliyeler")
        self.assertEqual([(e.kod, e.ad) for e in modul.ekranlar], [("satis_faturalari", "Satış Faturaları"), ("alis_faturalari", "Alış Faturaları"),
                                                                    ("satis_irsaliyeleri", "Satış İrsaliyeleri"), ("satinalma_irsaliyeleri", "Alış İrsaliyeleri")])
        satinalma = next(m for m in MODULLER if m.kod == "SATINALMA")
        self.assertNotIn("satinalma_irsaliyeleri", [e.kod for e in satinalma.ekranlar])
        self.assertEqual(reverse("core:satinalma_irsaliyeleri"), "/satinalma/irsaliyeler/")                    # URL değişmedi

    def test_liste_ekle_detay_ve_faturalanmamis_sekmesi(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        adres = CariSevkAdresi.objects.create(cari=self.musteri, ad="DEPO ADRESİ", varsayilan=True)
        r = self.client.get(reverse("core:satis_irsaliye_ekle"))
        self.assertContains(r, "Sevk adresi")
        self.assertContains(r, "Şoför TCKN")
        self.assertContains(r, "e-İrsaliye No")
        r = self.client.post(reverse("core:satis_irsaliye_ekle"), {
            "cari": self.musteri.pk, "tarih": "2026-03-10", "irsaliye_no": "IRS2026000000001", "depo": self.da.pk, "sevk_adresi": adres.pk, "plaka": "34 ABC 12",
            "sofor_adi": "AHMET", "sofor_tckn": GECERLI_TCKN, "para_birimi": "TRY", "aciklama": "",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-stok": self.stok.pk, "form-0-miktar": "4", "form-0-birim_fiyat": "500"})
        self.assertEqual(r.status_code, 302, getattr(getattr(r, "context", None), "get", lambda x: None)("bform"))
        i = TeklifSiparis.objects.get(belge_tur="IRSALIYE", yon="SATIS")
        self.assertEqual((i.irsaliye_no, i.plaka, i.durum), ("IRS2026000000001", "34ABC12", "TASLAK"))
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[i.pk]))
        self.assertContains(d, "34ABC12")
        self.assertContains(d, "DEPO ADRESİ")
        self.assertContains(d, "e-İrsaliye No")
        self.client.post(reverse("core:teklif_siparis_onayla", args=[i.pk]))
        i.refresh_from_db()
        self.assertEqual(i.durum, "ONAYLI")
        self.assertEqual(eldeki_miktar(self.stok), Dc("6"))
        liste = self.client.get(reverse("core:satis_irsaliyeleri"))
        self.assertContains(liste, "Faturalanmadı")
        self.assertContains(liste, "faturalanmamis-sekme")
        self.assertContains(self.client.get(reverse("core:satis_irsaliyeleri") + "?faturalanmamis=1"), i.belge_no)
        self.faturalandir([i])
        self.assertNotContains(self.client.get(reverse("core:satis_irsaliyeleri") + "?faturalanmamis=1"), i.belge_no)       # faturalanınca süzgeçten düşer
        self.assertContains(self.client.get(reverse("core:satis_irsaliyeleri")), "Faturaya Dönüştü")

    def test_irsaliye_detaydan_faturaya_cevir_ve_fatura_listesi_kisayolu(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        d = self.client.get(reverse("core:teklif_siparis_detay", args=[i.pk]))
        self.assertContains(d, reverse("core:irsaliye_faturaya_cevir", args=[i.pk]))
        r = self.client.post(reverse("core:irsaliye_faturaya_cevir", args=[i.pk]))
        i.refresh_from_db()
        self.assertRedirects(r, reverse("core:fatura_detay", args=[i.fatura_id]))
        self.assertEqual(i.fatura.yon, "SATIS")
        liste = self.client.get(reverse("core:satis_faturalari"))
        self.assertContains(liste, "irsaliyeden-fatura")
        self.assertContains(liste, "/satis/irsaliyeler/?faturalanmamis=1")

    def test_yetkisiz_kullanici_satis_irsaliyesini_goremez(self):
        diger = User.objects.create_user("kisi", password="x")
        EkranYetki.objects.create(kullanici=diger, ekran_kod="satinalma_irsaliyeleri")
        self.client.force_login(diger)
        self.assertEqual(self.client.get(reverse("core:satis_irsaliyeleri")).status_code, 403)
        self.assertEqual(self.client.get(reverse("core:satinalma_irsaliyeleri")).status_code, 200)
        EkranYetki.objects.create(kullanici=diger, ekran_kod="satis_irsaliyeleri")
        self.assertEqual(self.client.get(reverse("core:satis_irsaliyeleri")).status_code, 200)


class SayfaSagligiTest(SatisIrsaliyeTaban):
    """Yeni/etkilenen sayfalar 500 vermesin: irsaliyeli satış faturası detayı, irsaliye düzenle (sevk alanları dolu), PDF, siparişten irsaliyeye çevir formu, alış irsaliye listesi."""

    def setUp(self):
        self.yon = User.objects.create_superuser("yon3", password="x")
        self.client.force_login(self.yon)

    def test_fatura_detay_irsaliye_duzenle_pdf_alis_listesi(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        adres = CariSevkAdresi.objects.create(cari=self.musteri, ad="ŞUBE", varsayilan=True)
        i = self.irsaliye(D(2026, 3, 10), "4", onayla=False, sevk_adresi_id=adres.pk, plaka="34ABC12", sofor_adi="VELİ", sofor_tckn=GECERLI_TCKN)
        r = self.client.get(reverse("core:teklif_siparis_duzenle", args=[i.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "34ABC12")
        self.assertContains(r, GECERLI_TCKN)
        teklif_siparis_onayla(i)
        i.refresh_from_db()
        self.assertEqual(self.client.get(reverse("core:teklif_siparis_pdf", args=[i.pk])).status_code, 200)
        f = self.faturalandir([i])
        d = self.client.get(reverse("core:fatura_detay", args=[f.pk]))
        self.assertEqual(d.status_code, 200)
        self.assertContains(d, i.belge_no)
        self.assertEqual(self.client.get(reverse("core:satinalma_irsaliyeleri")).status_code, 200)
        self.assertContains(self.client.get(reverse("core:satinalma_irsaliyeleri")), "Alış İrsaliyeleri")
        self.assertEqual(self.client.get(reverse("core:alis_faturalari")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:fis_detay", args=[self.maliyet_fisleri().get().pk])).status_code, 200)

    def test_maliyet_fisi_silme_irsaliyeye_yonlendirir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        i = self.irsaliye(D(2026, 3, 10), "4")
        fis = self.maliyet_fisleri().get()
        r = self.client.post(reverse("core:fis_sil", args=[fis.pk]))
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[i.pk]), fetch_redirect_response=False)
        fis.refresh_from_db()
        self.assertFalse(fis.silindi)


class SiparisEkranTest(SevkTaban):
    """Satış siparişi detay/liste/irsaliyeye çevir ekranları (kısmi sevk)."""

    def test_detay_liste_ve_irsaliyeye_cevir_ekrani(self):
        sip = self.siparis("3")
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk]))
        self.assertContains(r, "İrsaliyeye Çevir")
        form = self.client.get(reverse("core:siparis_irsaliyeye_cevir", args=[sip.pk]))
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "satış irsaliyesine")
        r = self.client.post(reverse("core:siparis_irsaliyeye_cevir", args=[sip.pk]), {"depo": self.depo.pk, "tarih": "2026-06-28"})
        i = TeklifSiparis.objects.get(belge_tur="IRSALIYE", yon="SATIS", kaynak_siparis=sip)
        self.assertRedirects(r, reverse("core:teklif_siparis_detay", args=[i.pk]), fetch_redirect_response=False)
        self.assertEqual((i.durum, i.kalemler.get().miktar), ("TASLAK", Dc("3")))
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk])), "İrsaliye taslağı var")
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 1), tur=StokHareket.Tur.GIRIS, miktar=Dc("3"), giris_tutar_try=Dc("150"))      # sevk edilecek mamul stokta
        teklif_siparis_onayla(i, kullanici=self.yon)
        liste = self.client.get(reverse("core:satis_siparisleri"))
        self.assertContains(liste, "Sevk edildi")
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[sip.pk])), "Faturalanmamış sevk")

    def test_alis_siparisi_ekrani_degismedi(self):
        self.assertEqual(self.client.get(reverse("core:satinalma_siparisleri")).status_code, 200)
