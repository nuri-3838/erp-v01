"""İstasyon emrinden operasyon kaydı (plan adım 5 — docs/uretim-siparisi-plan.md): kayıt açma (varsayılan hedef = açık kalan × referans, kalan yoksa hedef zorunlu,
fazla hedef uyarıyla serbest), kayıt onayında ÜS ayırmasının düşmesi / üretilen parçanın net ihtiyaca kadar ayrılması (fazlası serbest), tamamlanan ve
durum (BEKLIYOR → BASLADI → BITTI), onaylı kaydın geri alınmasında ayırma izleri + tamamlananın aynen geri sarılması, iptal edilmiş emrin kaydının onaylanamaması."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import IstasyonEmri, OperasyonKaydi, OperasyonKaydiCikti, OperasyonKaydiGirdi, StokHareket, UretimEmri
from core.services import stok_ayirma as sa
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, istasyon_emri_kayit_ac, kayit_fazla_uyarisi, operasyon_kaydi_onayla, operasyon_kaydi_sil, operasyon_olustur, uretim_emri_iptal,
    uretim_emri_olustur,
)
from core.tests.test_uretim import _birim, _depo, _istasyon, _kategori, _stok

D = Decimal


class Taban(TestCase):
    """Profil (ham) → Kesim (2 KESİLMİŞ/çalıştırma) → Büküm (1:1 BÜKÜLMÜŞ). ÜS: 10 bükülmüş, profil stokta."""

    @classmethod
    def setUpTestData(cls):
        cls.birim, cls.kat, cls.depo = _birim(), _kategori(), _depo()
        cls.lazer, cls.bukum = _istasyon("LAZER"), _istasyon("BUKUM")
        cls.profil = _stok(cls.kat, cls.birim, kod="IK-PROFIL", ad="ham profil", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="IK-KESILMIS", ad="kesilmiş")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="IK-BUKULMUS", ad="bükülmüş", satis=True)
        cls.kesim_op = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=D("2"), satirlar=[(cls.profil, D("1"))])
        cls.bukum_op = operasyon_olustur(istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk, cikti_miktar=D("1"), satirlar=[(cls.kesilmis, D("1"))])

    def setUp(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("100"))
        self.emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": D("10")}], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.ie_kesim = self.emir.istasyon_emirleri.get(operasyon=self.kesim_op)
        self.ie_bukum = self.emir.istasyon_emirleri.get(operasyon=self.bukum_op)

    def ayr(self, stok):
        return sa.ayrilan_miktar(self.emir, stok)

    def durum(self, ie):
        ie.refresh_from_db()
        return ie.durum, ie.tamamlanan


class AcmaTest(Taban):
    def test_acilis_snapshot_ve_ayirma(self):
        self.assertEqual(self.ie_kesim.ihtiyac, {str(self.kesilmis.pk): "10"})
        self.assertEqual(self.ie_bukum.ihtiyac, {str(self.bukulmus.pk): "10"})
        self.assertEqual(self.ayr(self.profil), D("5"))                                    # yaprak: kesim 5 çalıştırma × 1

    def test_varsayilan_hedef_kalan_ve_baglar(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim)
        self.assertEqual((k.hedef_cikti_miktari, k.istasyon_emri_id, k.uretim_emri_id, k.depo_id, k.durum), (D("10"), self.ie_kesim.pk, self.emir.pk, self.depo.pk, "TASLAK"))
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BASLADI, D("0")))   # açık taslak = başladı
        self.assertEqual(kayit_fazla_uyarisi(k), "")
        with self.assertRaisesMessage(UretimHatasi, "kalan miktar yok"):                    # açık taslak kalanı tüketti
            istasyon_emri_kayit_ac(self.ie_kesim)
        k2 = istasyon_emri_kayit_ac(self.ie_kesim, hedef="4")                               # fazla: engellenmez, uyarı verir
        self.assertIn("kalanını aşıyor", kayit_fazla_uyarisi(k2))
        self.assertEqual(k2.hedef_cikti_miktari, D("4"))

    def test_taslak_silinince_emir_bekliyor(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim)
        operasyon_kaydi_sil(k)
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BEKLIYOR, D("0")))
        self.assertEqual(istasyon_emri_kayit_ac(self.ie_kesim).hedef_cikti_miktari, D("10"))   # kalan geri döndü

    def test_iptal_ve_kapali_emirde_kayit_acilmaz(self):
        uretim_emri_iptal(self.emir)
        with self.assertRaisesMessage(UretimHatasi, "açık üretim siparişinin"):
            istasyon_emri_kayit_ac(self.ie_kesim)


class OnayTest(Taban):
    def test_zincir_ayirma_tamamlanan_ve_durum(self):
        kesim = istasyon_emri_kayit_ac(self.ie_kesim)
        operasyon_kaydi_onayla(kesim)
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BITTI, D("5")))
        self.assertEqual(self.ayr(self.profil), D("0"))                                    # ayrılmış hammadde tüketildi
        self.assertEqual(self.ayr(self.kesilmis), D("10"))                                 # üretilen parça ÜS'ye ayrıldı
        self.assertEqual(kesim.girdi_satirlari.get().ayrilan_dusen, D("5"))
        self.assertEqual(kesim.ciktilar.get(stok=self.kesilmis).ayrilan, D("10"))
        self.assertEqual(eldeki_miktar(self.kesilmis), D("10"))
        bukum = istasyon_emri_kayit_ac(self.ie_bukum)
        operasyon_kaydi_onayla(bukum)
        self.assertEqual(self.durum(self.ie_bukum), (IstasyonEmri.Durum.BITTI, D("10")))
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.bukulmus)), (D("0"), D("10")))
        self.assertEqual(sa.kullanilabilir(self.bukulmus), D("0"))                         # başka siparişe kullanılabilir değil
        self.assertEqual(sa.kullanilabilir(self.bukulmus, haric_emir=self.emir), D("10"))

    def test_kismi_uretim_basladi(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim, hedef="4")                               # 2 çalıştırma
        operasyon_kaydi_onayla(k)
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BASLADI, D("2")))
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.profil)), (D("4"), D("3")))
        k2 = istasyon_emri_kayit_ac(self.ie_kesim)                                         # kalan 3 çalıştırma = 6 adet
        self.assertEqual(k2.hedef_cikti_miktari, D("6"))
        operasyon_kaydi_onayla(k2)
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BITTI, D("5")))
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.profil)), (D("10"), D("0")))

    def test_fazla_uretim_serbest_stok(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim, hedef="12")                              # 6 çalıştırma, ihtiyaç 10
        operasyon_kaydi_onayla(k)
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BITTI, D("6")))
        self.assertEqual(eldeki_miktar(self.kesilmis), D("12"))
        self.assertEqual(self.ayr(self.kesilmis), D("10"))                                 # yalnız net ihtiyaç ayrılır
        self.assertEqual(sa.kullanilabilir(self.kesilmis), D("2"))                         # fazla 2 serbest
        self.assertEqual(self.ayr(self.profil), D("0"))                                    # girdi 6 > ayrılan 5: ayırma sıfırlandı, fazlası serbest stoktan
        self.assertEqual(k.girdi_satirlari.get().ayrilan_dusen, D("5"))

    def test_geri_al_ayirma_ve_tamamlanan_geri_sarilir(self):
        kesim = istasyon_emri_kayit_ac(self.ie_kesim)
        operasyon_kaydi_onayla(kesim)
        bukum = istasyon_emri_kayit_ac(self.ie_bukum, hedef="6")
        operasyon_kaydi_onayla(bukum)
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.bukulmus)), (D("4"), D("6")))
        operasyon_kaydi_sil(bukum, onayli_geri_al=True)
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.bukulmus)), (D("10"), D("0")))     # tüketilen ayırma geri geldi, çıktı ayırması kalktı
        self.assertEqual(self.durum(self.ie_bukum), (IstasyonEmri.Durum.BEKLIYOR, D("0")))
        operasyon_kaydi_sil(kesim, onayli_geri_al=True)
        self.assertEqual((self.ayr(self.kesilmis), self.ayr(self.profil)), (D("0"), D("5")))
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BEKLIYOR, D("0")))
        self.assertEqual(eldeki_miktar(self.profil), D("100"))
        yeni = istasyon_emri_kayit_ac(self.ie_kesim)                                       # yeniden açılabilir, aynı sonuç
        operasyon_kaydi_onayla(yeni)
        self.assertEqual((self.ayr(self.kesilmis), self.durum(self.ie_kesim)), (D("10"), (IstasyonEmri.Durum.BITTI, D("5"))))

    def test_onayli_kayit_olan_emir_silinemez_iptal_edilir(self):
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(self.ie_kesim))
        with self.assertRaisesMessage(UretimHatasi, "başlamış"):
            from core.services.uretim import uretim_emri_sil
            uretim_emri_sil(self.emir)
        uretim_emri_iptal(self.emir)
        self.assertEqual(sa.kullanilabilir(self.kesilmis), D("10"))                        # iptalle ayırma bırakıldı, üretilen parça serbest
        self.assertEqual(eldeki_miktar(self.kesilmis), D("10"))

    def test_iptal_edilmis_emrin_taslak_kaydi_onaylanamaz(self):
        k = istasyon_emri_kayit_ac(self.ie_kesim)
        UretimEmri.objects.filter(pk=self.emir.pk).update(durum=UretimEmri.Durum.IPTAL)    # taslağı iptal etmeden durum değişmiş (tutarsızlık koruması)
        k.refresh_from_db()
        with self.assertRaisesMessage(UretimHatasi, "iptal edilmiş ya da kapanmış"):
            operasyon_kaydi_onayla(k)
        self.assertEqual(OperasyonKaydi.objects.get(pk=k.pk).durum, "TASLAK")

    def test_bagimsiz_kayit_ayirmaya_dokunmaz(self):
        from core.services.uretim import operasyon_kaydi_olustur
        k = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 11), hedef_cikti_miktari=D("4"))
        operasyon_kaydi_onayla(k)
        self.assertEqual((self.ayr(self.profil), self.ayr(self.kesilmis)), (D("5"), D("0")))
        self.assertEqual(self.durum(self.ie_kesim), (IstasyonEmri.Durum.BEKLIYOR, D("0")))

    def test_onay_ve_geri_al_istasyon_emri_satirini_kilitler(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        k = istasyon_emri_kayit_ac(self.ie_kesim)
        with CaptureQueriesContext(connection) as q:
            operasyon_kaydi_onayla(k)
        self.assertTrue(any("core_istasyon_emri" in x["sql"] and "FOR UPDATE" in x["sql"] for x in q.captured_queries))
        with CaptureQueriesContext(connection) as q:
            operasyon_kaydi_sil(k, onayli_geri_al=True)
        self.assertTrue(any("core_istasyon_emri" in x["sql"] and "FOR UPDATE" in x["sql"] for x in q.captured_queries))

    def test_ayni_emrin_iki_kaydi_ayirmayi_iki_kez_saymaz(self):
        k1 = istasyon_emri_kayit_ac(self.ie_kesim, hedef="6")
        k2 = istasyon_emri_kayit_ac(self.ie_kesim, hedef="6")                             # toplam 12 > ihtiyaç 10
        operasyon_kaydi_onayla(k1)
        operasyon_kaydi_onayla(k2)
        self.assertEqual(self.ayr(self.kesilmis), D("10"))
        self.assertEqual(sum(c.ayrilan for c in OperasyonKaydiCikti.objects.filter(kayit__istasyon_emri=self.ie_kesim, stok=self.kesilmis)), D("10"))
        operasyon_kaydi_sil(k1, onayli_geri_al=True)                                      # ilk kayıt geri alınınca k2'nin ayırması korunur
        self.assertEqual(self.ayr(self.kesilmis), D("4"))

