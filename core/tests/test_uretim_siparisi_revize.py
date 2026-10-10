"""Üretim siparişi REVİZE (plan adım 7 — docs/uretim-siparisi-plan.md): adet artışı/azalışı, kalem ekleme/çıkarma, başlamış emir, kısmen üretilmiş parça,
taslak kayıt hedefi düşürme/iptal, sevk/fatura edilmiş miktarın altına düşürme reddi, art arda iki revize, iptal edilmiş sipariş, atomik hata; onaylı satış
siparişi kalem revizesi (``siparis_revize``) aynı işlemde ÜS'yi revize eder. HER revize sonrası ortak ``tutarlilik_dogrula``: ayırma toplamı = güncel net
planın ayrılanı, her aktif istasyon emrinde planlanan = tamamlanan + yeni net çalıştırma, ihtiyaç snapshot'ı kümülatif, açık taslaklar kalana sığar."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from core.models import IstasyonEmri, OperasyonKaydi, StokHareket, TeklifSiparis, UretimEmri
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.services.uretim import (
    UretimHatasi, ihtiyac_hesapla, istasyon_emri_kayit_ac, operasyon_kaydi_onayla, operasyon_olustur, siparis_revize, uretim_emri_iptal,
    uretim_emri_olustur, uretim_emri_revize,
)
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_teklif_siparis import SiparisUretimFixture

D = Decimal
BUGUN = date(2026, 1, 15)


def tutarlilik_dogrula(tc, emir):
    """Revize sonrası değişmez kurallar (tüm revize testlerinde çağrılır)."""
    emir.refresh_from_db()
    kalemler = [(k.hedef_urun, k.hedef_miktar) for k in emir.kalemler.filter(silindi=False)]
    sonuc = ihtiyac_hesapla(kalemler, kullanilabilir=lambda idler: sa.kullanilabilir_haritasi(idler, haric_emir=emir))
    # 1) ayırma toplamı = güncel net planın ayrılanı (eldeki ihtiyaçla tutarlı), hiçbir stok eksik/fazla ayrılmış değil
    tc.assertEqual({a.stok_id: a.miktar for a in sa.emir_ayirmalari(emir)}, {pk: m for pk, m in sonuc["ayrilan"].items() if m > 0}, "ayırma ≠ plan")
    # 2) istasyon emirleri
    plan = {p["operasyon"].pk: p for p in sonuc["plan"]}
    for ie in emir.istasyon_emirleri.filter(silindi=False):
        p = plan.get(ie.operasyon_id)
        net = p["calistirma"] if p else D("0")
        if ie.durum == IstasyonEmri.Durum.IPTAL:
            tc.assertEqual((net, ie.tamamlanan), (D("0"), D("0")), f"{ie.no} iptal ama gerekli/başlamış")
            continue
        tc.assertEqual(ie.planlanan, ie.tamamlanan + net, f"{ie.no} planlanan ≠ tamamlanan + net")
        taslak = sum((uretim.kayit_calistirma(k) for k in ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK)), D("0"))
        tc.assertLessEqual(taslak, net, f"{ie.no} açık taslaklar kalana sığmıyor")
        if p:
            for c in p["ciktilar"]:                                   # ihtiyaç snapshot'ı = yeni net + daha önce onaylıdan ayrılan (kümülatif)
                if c["surucu"] and (c["net"] > 0 or uretim._onceki_ayrilan(ie, c["stok"].pk) > 0):
                    tc.assertEqual(D(ie.ihtiyac[str(c["stok"].pk)]), c["net"] + uretim._onceki_ayrilan(ie, c["stok"].pk), f"{ie.no} ihtiyaç")
        tc.assertEqual(ie.durum, IstasyonEmri.Durum.BITTI if net <= 0 else (IstasyonEmri.Durum.BASLADI if (ie.tamamlanan > 0 or taslak > 0) else IstasyonEmri.Durum.BEKLIYOR))
    for opk, p in plan.items():
        if p["calistirma"] > 0:
            tc.assertTrue(emir.istasyon_emirleri.filter(silindi=False, operasyon_id=opk).exclude(durum=IstasyonEmri.Durum.IPTAL).exists(), "gereken op için aktif emir yok")
    # 3) kalem snapshot'ı planla tutarlı
    for k in emir.kalemler.filter(silindi=False):
        tc.assertLessEqual(k.eldeki_ayrilan, k.hedef_miktar)


class RevizeTaban(Taban):
    """Taban: profil(100 stokta) → kesim 2/çalıştırma → büküm 1:1; ÜS 10 bükülmüş (kesim 5, büküm 10 çalıştırma; profil 5 ayrılı)."""

    def setUp(self):
        super().setUp()
        self.mamul2 = type(self.bukulmus).objects.create(kod="IK-MAMUL2", ad="ikinci mamul", kategori=self.kat, uretim_birimi=self.birim, fatura_birimi=self.birim, satis_urunu=True)
        self.mamul2_op = operasyon_olustur(istasyon_id=self.bukum.pk, cikti_id=self.mamul2.pk, cikti_miktar=D("1"), satirlar=[(self.kesilmis, D("2"))])

    def revize(self, *kalemler, **ek):
        return uretim_emri_revize(self.emir, kalemler=[{"hedef_urun_id": s.pk, "hedef_miktar": m} for s, m in kalemler], tarih=BUGUN, **ek)

    def planlanan(self):
        return {ie.operasyon_id: (ie.planlanan, ie.tamamlanan, ie.durum) for ie in self.emir.istasyon_emirleri.filter(silindi=False)}

    def ayr(self, stok):
        return sa.ayrilan_miktar(self.emir, stok)


class RevizeTest(RevizeTaban):
    def test_adet_artisi(self):
        self.revize((self.bukulmus, "14"))
        self.assertEqual(self.planlanan(), {self.kesim_op.pk: (D("7"), D("0"), "BEKLIYOR"), self.bukum_op.pk: (D("14"), D("0"), "BEKLIYOR")})
        self.assertEqual((self.ayr(self.profil), self.emir.revizyon_no, self.emir.kalemler.get(silindi=False).hedef_miktar), (D("7"), 1, D("14")))
        r = self.emir.revizyonlar.get(no=1)
        self.assertEqual((r.tur, r.detay["once"]["kalemler"][0]["miktar"], r.detay["sonra"]["kalemler"][0]["miktar"]), ("REVIZE", "10", "14"))
        tutarlilik_dogrula(self, self.emir)

    def test_adet_azalisi_fazla_ayirma_serbest_kalir(self):
        self.revize((self.bukulmus, "6"))
        self.assertEqual(self.planlanan(), {self.kesim_op.pk: (D("3"), D("0"), "BEKLIYOR"), self.bukum_op.pk: (D("6"), D("0"), "BEKLIYOR")})
        self.assertEqual((self.ayr(self.profil), sa.kullanilabilir(self.profil)), (D("3"), D("97")))
        tutarlilik_dogrula(self, self.emir)

    def test_kalem_ekleme_ve_cikarma(self):
        self.revize((self.bukulmus, "10"), (self.mamul2, "3"))                              # mamul2: 3 × 2 kesilmiş = 6 → kesim 8 çalıştırma
        p = self.planlanan()
        self.assertEqual((p[self.kesim_op.pk][0], p[self.mamul2_op.pk], self.ayr(self.profil)), (D("8"), (D("3"), D("0"), "BEKLIYOR"), D("8")))
        self.assertEqual(self.emir.kalemler.filter(silindi=False).count(), 2)
        tutarlilik_dogrula(self, self.emir)
        self.revize((self.bukulmus, "10"))                                                   # kalem çıkarıldı → mamul2 emri IPTAL, kesim 5'e döner
        p = self.planlanan()
        self.assertEqual((p[self.kesim_op.pk][0], p[self.mamul2_op.pk][2], self.ayr(self.profil)), (D("5"), "IPTAL", D("5")))
        tutarlilik_dogrula(self, self.emir)
        self.revize((self.bukulmus, "10"), (self.mamul2, "1"))                               # yeniden eklendi → iptal emir yeniden açılır (yeni satır değil)
        self.assertEqual(self.emir.istasyon_emirleri.filter(operasyon=self.mamul2_op).count(), 1)
        self.assertEqual(self.planlanan()[self.mamul2_op.pk], (D("1"), D("0"), "BEKLIYOR"))
        tutarlilik_dogrula(self, self.emir)

    def test_baslamis_emir_azalis_ve_artis(self):
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.ie_kesim))                       # kesim bitti: 10 kesilmiş üretildi, ÜS'ye ayrıldı
        self.revize((self.bukulmus, "6"))
        self.assertEqual(self.planlanan()[self.kesim_op.pk], (D("5"), D("5"), "BITTI"))      # başlamış/bitmiş korunur
        self.assertEqual((self.ayr(self.kesilmis), sa.kullanilabilir(self.kesilmis), self.ayr(self.profil)), (D("6"), D("4"), D("0")))
        tutarlilik_dogrula(self, self.emir)
        self.revize((self.bukulmus, "14"))                                                   # artış: 4 kesilmiş eldekinden, 4 daha üretilecek (2 çalıştırma)
        self.assertEqual(self.planlanan()[self.kesim_op.pk], (D("7"), D("5"), "BASLADI"))
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.profil)), (D("10"), D("2")))
        self.ie_kesim.refresh_from_db()
        self.assertEqual(self.ie_kesim.ihtiyac, {str(self.kesilmis.pk): "14"})               # kümülatif: 4 yeni + 10 önceden ayrılan
        tutarlilik_dogrula(self, self.emir)
        k = istasyon_emri_kayit_ac(self.ie_kesim)                                             # kalan 2 çalıştırma = 4 adet
        self.assertEqual(k.hedef_cikti_miktari, D("4"))
        operasyon_kaydi_onayla(k)
        self.assertEqual((self.ayr(self.kesilmis), self.planlanan()[self.kesim_op.pk]), (D("14"), (D("7"), D("7"), "BITTI")))

    def test_kismen_uretilmis_parca_azalis(self):
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.ie_kesim, hedef="4"))             # 2 çalıştırma yapıldı, 4 kesilmiş ayrılı
        self.revize((self.bukulmus, "3"))
        self.assertEqual(self.planlanan()[self.kesim_op.pk], (D("2"), D("2"), "BITTI"))      # artık üretim gerekmez
        self.assertEqual((self.ayr(self.kesilmis), sa.kullanilabilir(self.kesilmis), self.ayr(self.profil)), (D("3"), D("1"), D("0")))
        tutarlilik_dogrula(self, self.emir)
        self.revize((self.bukulmus, "5"))                                                    # 4 eldekinden + 1 daha → 0,5 çalıştırma (tam boy kapalı: kesirli)
        self.assertEqual(self.planlanan()[self.kesim_op.pk], (D("2.5"), D("2"), "BASLADI"))
        tutarlilik_dogrula(self, self.emir)

    def test_taslak_kayit_hedefi_dusurulur_ve_iptal_edilir(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim)                                             # taslak hedef 10 (5 çalıştırma)
        self.revize((self.bukulmus, "6"))
        k.refresh_from_db()
        self.assertEqual((k.hedef_cikti_miktari, k.silindi), (D("6"), False))               # 3 çalıştırmaya sığdırıldı
        self.assertEqual(k.girdi_satirlari.get().planlanan_miktar, D("3"))
        r = self.emir.revizyonlar.latest("no")
        self.assertEqual(r.detay["taslak_kayitlar"], [{"kayit": k.no, "once": "10", "sonra": "6"}])
        tutarlilik_dogrula(self, self.emir)
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("50"))
        self.revize((self.bukulmus, "6"))                                                    # kesilmiş artık stoktan: kesim gerekmez → taslak iptal, emir IPTAL
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertEqual(self.planlanan()[self.kesim_op.pk], (D("0"), D("0"), "IPTAL"))
        self.assertEqual(self.ayr(self.kesilmis), D("6"))
        tutarlilik_dogrula(self, self.emir)

    def test_sevk_edilmis_miktarin_altina_dusurme_reddi(self):
        with mock.patch.object(uretim, "sevk_edilen_haritasi", return_value={self.bukulmus.pk: D("8")}):
            with self.assertRaisesMessage(UretimHatasi, "sevk/fatura edilmiş miktarın (8) altına düşürülemez"):
                self.revize((self.bukulmus, "6"))
            self.assertEqual((self.emir.revizyon_no, self.planlanan()[self.bukum_op.pk][0]), (0, D("10")))   # hiçbir şey değişmedi
            self.revize((self.bukulmus, "8"))                                                # eşit: kabul
        self.assertEqual(self.planlanan()[self.bukum_op.pk][0], D("8"))
        tutarlilik_dogrula(self, self.emir)

    def test_art_arda_iki_revize_ve_gecmis(self):
        self.revize((self.bukulmus, "14"), aciklama="müşteri artırdı")
        tutarlilik_dogrula(self, self.emir)
        self.revize((self.bukulmus, "8"))
        self.assertEqual((self.emir.revizyon_no, [r.tur for r in self.emir.revizyonlar.all()]), (2, ["ACILIS", "REVIZE", "REVIZE"]))
        self.assertEqual(self.emir.revizyonlar.get(no=1).aciklama, "müşteri artırdı")
        self.assertEqual(self.planlanan()[self.kesim_op.pk][0], D("4"))
        self.assertEqual(self.emir.kalemler.filter(silindi=False).count(), 1)
        self.assertEqual(self.emir.kalemler.count(), 3)                                      # eski kalemler soft-silinmiş geçmiş
        tutarlilik_dogrula(self, self.emir)

    def test_iptal_sipariste_ve_hatali_kalemde_degisiklik_yok(self):
        with self.assertRaisesMessage(UretimHatasi, "operasyon yok"):
            self.revize((self.bukulmus, "7"), (self.profil, "1"))
        self.assertEqual((self.emir.revizyon_no, self.planlanan()[self.bukum_op.pk][0], self.ayr(self.profil)), (0, D("10"), D("5")))
        uretim_emri_iptal(self.emir)
        with self.assertRaisesMessage(UretimHatasi, "Yalnız açık"):
            self.revize((self.bukulmus, "7"))


class SiparisRevizeTest(SiparisUretimFixture, TestCase):
    """Onaylı satış siparişi kalem revizesi → ÜS aynı işlemde revize (karar 6/10)."""

    def _ac(self):
        sip = self._siparis()                                                                # 5 üretilebilir + 2 ticari
        emir = uretim.siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=BUGUN,
                                                     kalem_secimleri=[{"kalem_id": sip.kalemler.get(stok=self.uretilebilir).pk, "hedef_miktar": "5"}])
        return sip, emir

    def satirlar(self, uretilebilir="7", ticari="2"):
        s = [{"stok_id": self.uretilebilir.pk, "miktar": uretilebilir, "birim_fiyat": "100"}] if uretilebilir else []
        if ticari:
            s.append({"stok_id": self.uretilemez.pk, "miktar": ticari, "birim_fiyat": "30"})
        return s

    def test_siparis_kalemi_degisince_us_revize(self):
        sip, emir = self._ac()
        siparis_revize(sip, satirlar=self.satirlar("7", "1"), kullanici=self.yon)
        emir.refresh_from_db()
        kalem = emir.kalemler.get(silindi=False)
        yeni_satir = sip.kalemler.get(silindi=False, stok=self.uretilebilir)
        self.assertEqual((kalem.hedef_miktar, kalem.siparis_kalem_id, yeni_satir.miktar, emir.revizyon_no), (D("7"), yeni_satir.pk, D("7"), 1))
        self.assertEqual(sip.kalemler.get(silindi=False, stok=self.uretilemez).miktar, D("1"))   # ticari satır sipariş tarafında güncellendi, ÜS'de yok
        self.assertEqual(emir.istasyon_emirleri.get().planlanan, D("7"))
        self.assertEqual(sip.kalemler.count(), 4)                                             # eski 2 satır soft-silinmiş geçmiş
        self.assertIn("revizesi", emir.revizyonlar.get(no=1).aciklama)
        tutarlilik_dogrula(self, emir)

    def test_uretime_uygun_kalem_kalmazsa_red_ve_atomik(self):
        sip, emir = self._ac()
        with self.assertRaisesMessage(UretimHatasi, "üretime uygun kalem kalmıyor"):
            siparis_revize(sip, satirlar=self.satirlar(uretilebilir=None, ticari="3"), kullanici=self.yon)
        self.assertEqual((sip.kalemler.filter(silindi=False).count(), emir.kalemler.get(silindi=False).hedef_miktar), (2, D("5")))

    def test_sipariş_kilidi_normal_duzenlemeyi_hala_engeller(self):
        from core.services import teklif_siparis as ts
        sip, emir = self._ac()
        with self.assertRaises(ts.TeklifSiparisHatasi):
            ts.teklif_siparis_guncelle(sip, cari_id=sip.cari_id, tarih=sip.tarih, satirlar=self.satirlar())
        with self.assertRaisesMessage(UretimHatasi, "açık bir üretim siparişi yok"):
            uretim_emri_iptal(emir)
            siparis_revize(sip, satirlar=self.satirlar(), kullanici=self.yon)
        self.assertEqual(TeklifSiparis.objects.get(pk=sip.pk).durum, "ONAYLI")
