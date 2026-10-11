"""Stoklar listesi + stok kartı: Ayrılan / Kullanılabilir (mevcut StokAyirma hesabı), ÜS dökümü açılır listesi, "Yalnız mamuller (152)" filtresi,
sabit sorgu sayısı."""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import StokHareket
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.tests import test_uretim_siparisi_kapanis as kt

D = Decimal


class AyrilanSutunTest(kt.KapanisTaban):
    def liste(self, **q):
        return self.client.get(reverse("core:stoklar"), q)

    def satir(self, yanit, stok):
        h = yanit.content.decode()
        return h.split(f'<div class="kod">{stok.kod}</div>', 1)[1].split("</tr>", 1)[0]

    def test_ayirma_varken_ayrilan_ve_kullanilabilir_dogru_acilir_liste_us_musteri_miktar(self):
        emir = self.emir_ac(self.siparis("3"))                                     # ham 3 ayrılır; ham eldeki 100
        satir = self.satir(self.liste(), self.ham)
        self.assertIn("3,000 ▾", satir)                                            # ayrılan
        self.assertIn("97,000", satir)                                             # kullanılabilir
        self.assertIn(emir.no, satir)
        self.assertIn(self.musteri.unvan, satir)
        self.assertIn(reverse("core:uretim_emri_detay", args=[emir.pk]), satir)
        d = self.client.get(reverse("core:stok_detay", args=[self.ham.pk]))
        self.assertContains(d, "Ayrılan 3,000")
        self.assertContains(d, "Kullanılabilir 97,000")
        self.assertContains(d, "Mevcut 100,000")

    def test_ayirma_yokken_tire_ve_kapaninca_ayrilan_duser(self):
        satir = self.satir(self.liste(), self.ham)
        self.assertNotIn("▾", satir)                                               # ayrılan yok → "—"
        self.assertIn("100,000", satir)
        emir = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "3"}], depo_id=self.depo.pk, tarih=kt.GUN)
        self.assertIn("3,000 ▾", self.satir(self.liste(), self.ham))
        self.assertIn("Stok için üretim", self.satir(self.liste(), self.ham))
        uretim.operasyon_kaydi_onayla(uretim.istasyon_emri_kayit_ac(emir.istasyon_emirleri.get()))     # stok ÜS %100 → tamamlanır, ayırma düşer
        satir = self.satir(self.liste(), self.stok)
        self.assertNotIn("▾", satir)
        self.assertEqual(sa.kullanilabilir(self.stok), D("3"))

    def test_negatif_kullanilabilir_kirmizi(self):
        self.emir_ac(self.siparis("3"))
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=date(2026, 7, 1), tur=StokHareket.Tur.CIKIS, miktar=D("99"))   # ayrılan 3 > eldeki 1
        satir = self.satir(self.liste(), self.ham)
        self.assertIn("stk-eksi", satir)
        self.assertIn("-2,000", satir)

    def test_yalniz_mamuller_filtresi(self):
        from core.models import Stok
        mamul = Stok.objects.create(kod="152-10-0999", ad="MAMUL TEST", kategori=self.ham.kategori, kdv=self.kdv, uretim_birimi=self.ham.uretim_birimi,
                                    fatura_birimi=self.ham.fatura_birimi)
        r = self.liste(mamul="1")
        self.assertContains(r, "152-10-0999")
        self.assertNotContains(r, f'<div class="kod">{self.ham.kod}</div>')
        self.assertContains(self.liste(), f'<div class="kod">{self.ham.kod}</div>')
        self.assertTrue(mamul.pk)

    def test_sabit_sorgu_sayisi(self):
        self.emir_ac(self.siparis("3"))
        def say():
            with CaptureQueriesContext(connection) as c:
                self.assertEqual(self.liste().status_code, 200)
            return len(c)
        bir = say()
        from core.models import Stok
        for i in range(6):
            s = Stok.objects.create(kod=f"152-10-09{i:02d}", ad=f"X{i}", kategori=self.ham.kategori, kdv=self.kdv, uretim_birimi=self.ham.uretim_birimi,
                                    fatura_birimi=self.ham.fatura_birimi)
            uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.stok.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=kt.GUN)
        self.assertEqual(say(), bir)                                               # stok/ayırma sayısı arttı, sorgu sayısı aynı
