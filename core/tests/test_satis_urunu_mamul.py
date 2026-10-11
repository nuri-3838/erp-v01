"""Satış ürünü (teklif/proforma/sipariş listesi) yalnız 152 MAMULLER kök kategorisindeki kartlar: servis kuralı (oluştur/güncelle/kopyala),
0214 veri temizliği (yanlış işaretli 151-99 ara mamuller listeden çıkar), satış ürünü listesi; akıllı-seç klavye (Enter formu göndermez)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from core.models import KdvOrani, Stok
from core.services import stok as stok_servis
from core.services.birim import birim_olustur
from core.services.kategori import kategori_olustur, kok_kategori_kodu, mamul_kategorisi_mi, satis_urunu_olabilir_mi

D = Decimal


class SatisUrunuMamulTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("sumyon", password="x")
        cls.adet = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        cls.kdv, _ = KdvOrani.objects.get_or_create(oran=D("20"), silindi=False, defaults={"aciklama": "ORAN"})
        ym = kategori_olustur(ad="YARI MAMUL", kod="151")
        cls.k151 = kategori_olustur(ad="PAKETSİZ", kod="99", ust_id=ym.pk)
        mam = kategori_olustur(ad="MAMULLER", kod="152")
        cls.k152 = kategori_olustur(ad="ÇİFT ÇIKIŞ", kod="22", ust_id=mam.pk)

    def kur(self, kat, **kw):
        a = {"ad": "X", "kategori_id": kat.pk, "uretim_birimi_id": self.adet.pk, "fatura_birimi_id": self.adet.pk, "kdv_id": self.kdv.pk,
             "satinalma_urunu": False, "uretim_urunu": True, "satis_urunu": False}
        a.update(kw)
        return stok_servis.stok_olustur(**a)

    def test_kok_kategori_ve_mamul(self):
        self.assertEqual((kok_kategori_kodu(self.k151), kok_kategori_kodu(self.k152)), ("151", "152"))
        self.assertTrue(mamul_kategorisi_mi(self.k152))
        self.assertFalse(mamul_kategorisi_mi(self.k151))
        self.assertEqual((satis_urunu_olabilir_mi(self.k152), satis_urunu_olabilir_mi(self.k151)), (True, False))

    def test_satis_urunu_yalniz_152(self):
        with self.assertRaisesMessage(stok_servis.StokHatasi, "yarı mamul (151) kartlarında işaretlenemez"):
            self.kur(self.k151, satis_urunu=True)
        mamul = self.kur(self.k152, ad="ÇÇ 4+4", satis_urunu=True, fiyat_try=D("100"))
        self.assertTrue(mamul.satis_urunu)
        ara = self.kur(self.k151, ad="PAKETSİZ ÇÇ")
        with self.assertRaisesMessage(stok_servis.StokHatasi, "yarı mamul (151) kartlarında işaretlenemez"):
            stok_servis.stok_guncelle(ara, ad=ara.ad, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk, cevirici=D("1"),
                                      kdv_id=self.kdv.pk, satinalma_urunu=False, uretim_urunu=True, satis_urunu=True)
        kopya = stok_servis.stok_kopyala(mamul)                                               # mamul kopyası satış ürünü kalır (aynı kategori)
        self.assertTrue(kopya.satis_urunu)
        self.assertEqual([s.kod for s in stok_servis.satis_urunleri_fiyatlariyla()], [mamul.kod, kopya.kod])

    def test_0214_veri_temizligi_yanlis_isaretli_ara_mamulu_listeden_cikarir(self):
        from django.apps import apps
        from core.migrations import __name__ as _m
        import importlib
        mig = importlib.import_module("core.migrations.0214_satis_urunu_yalniz_mamul")
        mamul = self.kur(self.k152, ad="ÇÇ 4+4", satis_urunu=True, fiyat_try=D("100"))
        ara = self.kur(self.k151, ad="PAKETSİZ ÇÇ")
        Stok.objects.filter(pk=ara.pk).update(satis_urunu=True)                               # eski veri: kural öncesi işaretlenmiş
        yalniz_satis = self.kur(self.k151, ad="TEK GRUP")
        Stok.objects.filter(pk=yalniz_satis.pk).update(satis_urunu=True, uretim_urunu=False)
        self.assertEqual({s.kod for s in stok_servis.satis_urunleri_fiyatlariyla()}, {mamul.kod, ara.kod, yalniz_satis.kod})
        mig.temizle(apps, None)
        ara.refresh_from_db()
        yalniz_satis.refresh_from_db()
        self.assertEqual((ara.satis_urunu, ara.uretim_urunu), (False, True))
        self.assertTrue(yalniz_satis.satis_urunu)                                             # tek grubu olan karta dokunulmaz
        self.assertEqual({s.kod for s in stok_servis.satis_urunleri_fiyatlariyla()}, {mamul.kod, yalniz_satis.kod})
        mig.temizle(apps, None)                                                               # idempotent

    def test_teklif_ve_proforma_listesinde_yalniz_mamul(self):
        mamul = self.kur(self.k152, ad="ÇÇ 4+4", satis_urunu=True, fiyat_try=D("100"))
        ara = self.kur(self.k151, ad="PAKETSİZ ÇÇ")
        self.client.force_login(self.yon)
        for url in ("core:satis_teklif_ekle", "core:satis_proforma_ekle"):
            r = self.client.get(reverse(url))
            self.assertContains(r, mamul.kod)
            self.assertNotContains(r, ara.kod)
        r = self.client.get(reverse("core:stok_ekle"))
        self.assertEqual(r.status_code, 200)

    def test_akilli_sec_klavye(self):
        html = render_to_string("core/_akilli_hesap.html")
        self.assertIn("ev.key === 'Enter'", html)
        self.assertIn("ev.preventDefault();", html)
        self.assertIn("ArrowDown", html)
