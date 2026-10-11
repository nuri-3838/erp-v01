"""TARİH BAZLI stok çıkış kontrolü (üretim sadeleştirme adım 2): çıkış, kendi tarihi sonu itibarıyla depodaki eldekiyi aşamaz — o gün henüz gelmemiş
stok tüketilemez. Tek mesaj biçimi: "Bu tarihte yetersiz: KOD (eldeki X, gerekli Y) — DEPO, tarih". Üretim girdisi ("Üretildi" satırında aynı mesaj),
satış faturası, numune, sarf ve elle çıkışın hepsi ``hareket.cikis_kontrol``ünden geçer; hata durumunda hiçbir şey yazılmaz."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import Kur, OperasyonKaydi, StokHareket, YevmiyeFisi
from core.services import numune as ns
from core.services.fatura import FaturaHatasi, fatura_olustur
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, sarf_cikis_ekle
from core.services.uretim import _eksik_mesaji
from core.tests import test_istasyon_emri_uret as tiu
from core.tests import test_numune_cikis as tn
from core.tests.test_fatura_stok import FaturaStokTest
from core.tests.test_stok_sarf import SarfTemel

D = Decimal


class EldekiTarihTest(SarfTemel):
    """SarfTemel: 100 boy girişi 01.06.2026."""

    def test_eldeki_tarih_bazli(self):
        self.assertEqual(eldeki_miktar(self.stok, self.depo, date(2026, 5, 31)), D("0"))
        self.assertEqual(eldeki_miktar(self.stok, self.depo, date(2026, 6, 1)), D("100"))
        self.assertEqual(eldeki_miktar(self.stok, self.depo), D("100"))

    def test_geriye_tarihli_cikis_reddedilir_hicbir_sey_yazilmaz(self):
        n = StokHareket.objects.count()
        with self.assertRaises(HareketHatasi) as c:
            hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 5, 31), tur="CIKIS", miktar="10")
        self.assertEqual(str(c.exception), "Bu tarihte yetersiz: 150-20-0002 (eldeki 0, gerekli 10) — 01, 31.05.2026")
        self.assertEqual(StokHareket.objects.count(), n)
        h = hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 6, 1), tur="CIKIS", miktar="10")   # aynı gün: giriş önce sayılır
        self.assertEqual(h.tur, "CIKIS")
        with self.assertRaisesMessage(HareketHatasi, "Bu tarihte yetersiz: 150-20-0002 (eldeki 90, gerekli 95)"):
            hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 6, 2), tur="CIKIS", miktar="95")

    def test_sarf_cikisi_tarih_bazli(self):
        n_fis = YevmiyeFisi.objects.count()
        with self.assertRaisesMessage(HareketHatasi, "Bu tarihte yetersiz: 150-20-0002 (eldeki 0, gerekli 5)"):
            sarf_cikis_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=date(2026, 5, 20), miktar="5", karsi_hesap_id=self.hesap_255.pk,
                            kullanici=self.u)
        self.assertEqual((YevmiyeFisi.objects.count(), eldeki_miktar(self.stok)), (n_fis, D("100")))

    def test_eksik_mesaji_yeni_bicimi_ayristirir(self):
        m = "Bu tarihte yetersiz: Sf-HAM (eldeki 100, gerekli 150) — DF1, 29.06.2026"
        self.assertEqual(_eksik_mesaji(m), m + " — eksik: 50.")
        self.assertEqual(_eksik_mesaji("Depo bulunamadı."), "Depo bulunamadı.")


class UretimGirdisiTarihTest(tiu.UretTaban):
    """UretTaban: ham 100 adet girişi 01.01.2026 (DF1 deposu)."""

    def test_uretildi_geriye_tarihli_satirda_ayni_mesaj(self):
        self.kur("3")
        n_kayit, n_hareket = OperasyonKaydi.objects.count(), StokHareket.objects.count()
        r = self.uret(miktar="2", tarih="2025-12-31")
        self.assertEqual((OperasyonKaydi.objects.count(), StokHareket.objects.count()), (n_kayit, n_hareket))     # hiçbir şey yazılmadı
        self.assertContains(r, 'class="ie-hata"')
        self.assertContains(r, "Bu tarihte yetersiz: Sf-HAM (eldeki 0, gerekli 2) — DF1, 31.12.2025 — eksik: 2.")
        self.assertEqual((self.yenile().tamamlanan, eldeki_miktar(self.ham)), (D("0"), D("100")))
        r = self.uret(miktar="2", tarih="2026-01-01")                                                            # giriş günü üretilebilir
        self.assertContains(r, "üretildi")
        self.assertEqual(self.yenile().tamamlanan, D("2"))


class SatisFaturasiTarihTest(FaturaStokTest):
    def test_giristen_onceki_tarihli_satis_reddedilir(self):
        Kur.objects.create(tarih=date(2026, 3, 9), usd_alis=D("30"))
        fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=date(2026, 3, 10), satirlar=self._satir(miktar="10"), depo_id=self.depo.pk)
        with self.assertRaises(FaturaHatasi) as c:
            fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=date(2026, 3, 9), satirlar=self._satir(miktar="4"), depo_id=self.depo.pk)
        self.assertIn(f"Bu tarihte yetersiz: {self.stok.kod} (eldeki 0, gerekli 4) — 01, 09.03.2026", str(c.exception))
        self.assertEqual(eldeki_miktar(self.stok, self.depo), D("10"))
        fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=date(2026, 3, 10), satirlar=self._satir(miktar="4"), depo_id=self.depo.pk)
        self.assertEqual(eldeki_miktar(self.stok, self.depo), D("6"))


class NumuneTarihTest(tn.NumuneTaban):
    def test_numune_belge_tarihinde_stok_yoksa_reddedilir(self):
        nc = self.nc("4", tarih=date(2025, 12, 31))                                                             # ham girişi 01.01.2026
        n_hareket, n_fis = StokHareket.objects.count(), YevmiyeFisi.objects.count()
        with self.assertRaisesMessage(ns.NumuneHatasi, "Bu tarihte yetersiz: Sf-HAM (eldeki 0, gerekli 4) — DF1, 31.12.2025"):
            ns.numune_onayla(nc)
        self.assertEqual((StokHareket.objects.count(), YevmiyeFisi.objects.count(), eldeki_miktar(self.ham)), (n_hareket, n_fis, D("100")))
        r = self.client.post(reverse("core:numune_onayla", args=[nc.pk]), follow=True)
        self.assertContains(r, "Bu tarihte yetersiz")
