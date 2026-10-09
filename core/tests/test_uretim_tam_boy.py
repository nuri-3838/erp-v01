"""Kesimde TAM BOY zorunluluğu (Operasyon.tam_calistirma): çalıştırma sayısı toplanmış talep üzerinden TEK kez yukarı yuvarlanır; fazla parça
stoğa girer; tam_calistirma=False operasyonların davranışı aynen kalır; üretim emri hedef/planlanan miktarları; onayda küsuratlı BOY girdisi
reddi; veri migration'ı BOY girdili operasyonları işaretler."""
import importlib
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.test import TestCase

from core.models import Birim, Depo, FaturaTipi, HesapPlani, KategoriHesap, Operasyon, OperasyonKaydi, StokHareket
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, ihtiyac_hesapla, kaydi_girdi_satirlari, operasyon_guncelle, operasyon_kaydi_girdi_guncelle,
    operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur, uretim_emri_olustur,
)
from core.tests.test_uretim import _depo, _istasyon, _kategori, _stok

D = Decimal


class TamBoyBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.boy = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        cls.mt = Birim.objects.create(ad="METRE", kisa_ad="MT", ondalik=3)
        cls.kat = _kategori()
        hesap = HesapPlani.objects.create(hesap_kodu="150.10", hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV",
                                          parasal=True, aktif=True)
        KategoriHesap.objects.create(kategori=cls.kat, hesap=hesap, fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS"))
        cls.depo = _depo()
        cls.kesim = _istasyon("KESIM")
        cls.montaj = _istasyon("MONTAJ")

    def stok(self, kod, birim=None, **kw):
        return _stok(self.kat, birim or self.adet, kod=kod, ad=kod.lower(), **kw)

    def op(self, istasyon, cikti, cikti_miktar, girdiler):
        return operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=cikti.pk, cikti_miktar=D(str(cikti_miktar)),
                                 satirlar=[(g, D(str(m))) for g, m in girdiler])

    def ozet(self, sonuc):
        return {o["stok"].kod: o for o in sonuc["ozet"]}


class TekUrunTest(TamBoyBase):
    def test_bir_adet_bir_boy_fazla_17(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        kesilmis = self.stok("KESILMIS")
        mamul = self.stok("MAMUL", satis=True)
        self.op(self.kesim, kesilmis, 18, [(profil, 1)])
        self.op(self.montaj, mamul, 1, [(kesilmis, 1)])
        sonuc = ihtiyac_hesapla([(mamul, D("1"))])
        o = self.ozet(sonuc)
        self.assertEqual((o["KESILMIS"]["ihtiyac"], o["KESILMIS"]["calistirma_sayisi"], o["KESILMIS"]["uretilecek_miktar"],
                          o["KESILMIS"]["fazla_miktar"]), (D("1"), D("1"), D("18"), D("17")))
        self.assertTrue(o["KESILMIS"]["tam"])
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("1"))                       # hammadde TAM boy
        self.assertEqual(o["PROFIL"]["toplam_miktar"], o["PROFIL"]["toplam_miktar"].to_integral_value())
        kes = sonuc["agac"][0]["cocuklar"][0]                                         # ağaç düğümü de aynı
        self.assertEqual((kes["calistirma"], kes["uretilecek"], kes["fazla"], kes["ihtiyac_toplam"]), (D("1"), D("18"), D("17"), D("1")))
        self.assertEqual(kes["cocuklar"][0]["miktar"], D("1"))

    def test_kesirli_boy_hic_cikmaz(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        kesilmis = self.stok("KESILMIS")
        mamul = self.stok("MAMUL", satis=True)
        self.op(self.kesim, kesilmis, 7, [(profil, 1)])
        self.op(self.montaj, mamul, 1, [(kesilmis, 3)])
        for adet in range(1, 12):
            o = self.ozet(ihtiyac_hesapla([(mamul, D(adet))]))
            self.assertEqual(o["PROFIL"]["toplam_miktar"], o["PROFIL"]["toplam_miktar"].to_integral_value(), adet)

    def test_ekran_cikti_bilgileri(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        from core.models import EkranYetki
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        kesilmis = self.stok("KESILMIS")
        mamul = self.stok("MAMUL", satis=True)
        self.op(self.kesim, kesilmis, 18, [(profil, 1)])
        self.op(self.montaj, mamul, 1, [(kesilmis, 1)])
        u = User.objects.create_user("tb", password="x")
        for k in ("ihtiyac_hesapla", "urun_agaci"):
            EkranYetki.objects.create(kullanici=u, ekran_kod=k)
        self.client.force_login(u)
        r = self.client.get(reverse("core:urun_agaci"), {"urun": mamul.pk, "miktar": "1"})
        self.assertContains(r, "çalıştırma: 1 boy")
        self.assertContains(r, "üretilecek: 18")
        self.assertContains(r, "fazla: 17 (stoğa)")
        self.assertNotContains(r, "0,056")


class OrtakParcaTest(TamBoyBase):
    """Aynı kesilmiş parça (1. basamak) birden çok mamul/dalda ortak: TÜM talep toplanır, sonra TEK kez yuvarlanır."""

    def kur(self, adet_model=5):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        ortak = self.stok("ORTAK")
        self.op(self.kesim, ortak, 18, [(profil, 1)])
        modeller = []
        for i in range(adet_model):
            m = self.stok(f"MODEL{i}", satis=True)
            self.op(self.montaj, m, 1, [(ortak, 1)])
            modeller.append(m)
        return profil, ortak, modeller

    def test_toplam_talep_uzerinden_tek_yuvarlama_dal_bazinda_degil(self):
        profil, ortak, modeller = self.kur(5)
        sonuc = ihtiyac_hesapla([(m, D("1")) for m in modeller])
        o = self.ozet(sonuc)
        self.assertEqual(o["ORTAK"]["ihtiyac"], D("5"))
        self.assertEqual(o["ORTAK"]["calistirma_sayisi"], D("1"))                  # dal bazında yuvarlansaydı 5 olurdu
        self.assertEqual((o["ORTAK"]["uretilecek_miktar"], o["ORTAK"]["fazla_miktar"]), (D("18"), D("13")))
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("1"))

    def test_esik_asilinca_ikinci_boy(self):
        profil, ortak, modeller = self.kur(5)
        o = self.ozet(ihtiyac_hesapla([(m, D("4")) for m in modeller]))              # 20 talep → ceil(20/18) = 2
        self.assertEqual((o["ORTAK"]["ihtiyac"], o["ORTAK"]["calistirma_sayisi"], o["PROFIL"]["toplam_miktar"]), (D("20"), D("2"), D("2")))

    def test_agacta_ortak_stok_bir_kez_acilir_digerleri_tekrar(self):
        profil, ortak, modeller = self.kur(3)
        agac = ihtiyac_hesapla([(m, D("1")) for m in modeller])["agac"]
        ortak_dugumleri = [d["cocuklar"][0] for d in agac]
        self.assertEqual([bool(d.get("tekrar")) for d in ortak_dugumleri], [False, True, True])
        self.assertTrue(all(d["calistirma"] == D("1") and d["ihtiyac_toplam"] == D("3") for d in ortak_dugumleri))


class CokSeviyeTest(TamBoyBase):
    def test_zincir_ve_elmas_yapi(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        b = self.stok("B", self.boy)                                          # A, B'den (BOY birimli) kesilir → A da otomatik tam boy
        a = self.stok("A")
        y = self.stok("Y")
        r = self.stok("R", satis=True)
        self.op(self.kesim, b, 3, [(profil, 1)])
        self.op(self.kesim, a, 4, [(b, 1)])                         # A ← B (iki kesim basamağı)
        self.op(self.montaj, y, 1, [(a, 1)])
        self.op(self.montaj, r, 1, [(a, 1), (y, 1)])                          # R: A doğrudan + Y üzerinden (A iki derinlikte)
        o = self.ozet(ihtiyac_hesapla([(r, D("2"))]))
        # A talebi: R'den 2 + Y'den 2 = 4 → ceil(4/4) = 1 çalıştırma (A ikinci seviyeden de beslendiği için en derin seviyede toplanır)
        self.assertEqual((o["A"]["ihtiyac"], o["A"]["calistirma_sayisi"], o["A"]["uretilecek_miktar"]), (D("4"), D("1"), D("4")))
        self.assertEqual((o["B"]["ihtiyac"], o["B"]["calistirma_sayisi"], o["B"]["uretilecek_miktar"], o["B"]["fazla_miktar"]),
                         (D("1"), D("1"), D("3"), D("2")))
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("1"))

    def test_cok_seviyeli_zincirde_yukari_yuvarlama_zinciri(self):
        profil = self.stok("PROFIL", self.boy, satinalma=True)
        b = self.stok("B", self.boy)
        a = self.stok("A")
        self.op(self.kesim, b, 3, [(profil, 1)])
        self.op(self.kesim, a, 4, [(b, 1)])
        o = self.ozet(ihtiyac_hesapla([(a, D("5"))]))                          # ceil(5/4)=2 A çalıştırması → 2 B → ceil(2/3)=1 → 1 profil
        self.assertEqual((o["B"]["ihtiyac"], o["B"]["calistirma_sayisi"], o["PROFIL"]["toplam_miktar"]), (D("2"), D("1"), D("1")))


class TamOlmayanRegresyonTest(TamBoyBase):
    def test_tam_calistirma_false_kesirli_calisir_eski_sonuc(self):
        profil = self.stok("PROFIL", self.mt, satinalma=True)
        kesilmis = self.stok("KESILMIS")
        mamul = self.stok("MAMUL", satis=True)
        self.op(self.kesim, kesilmis, 4, [(profil, 1)])            # kayış gibi: kesirli kalır
        self.op(self.montaj, mamul, 1, [(kesilmis, 1)])
        o = self.ozet(ihtiyac_hesapla([(mamul, D("10"))]))
        self.assertEqual((o["KESILMIS"]["toplam_miktar"], o["KESILMIS"]["calistirma_sayisi"], o["KESILMIS"]["fazla_miktar"]),
                         (D("10"), D("2.5"), D("0")))
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("2.5"))
        self.assertFalse(o["KESILMIS"]["tam"])


class UretimEmriTamBoyTest(TamBoyBase):
    def setUp(self):
        self.profil = self.stok("PROFIL", self.boy, satinalma=True)
        self.kesilmis = self.stok("KESILMIS")
        self.mamul = self.stok("MAMUL", satis=True)
        self.kesim_op = self.op(self.kesim, self.kesilmis, 18, [(self.profil, 1)])
        self.mamul_op = self.op(self.montaj, self.mamul, 1, [(self.kesilmis, 1)])

    def test_emirde_hedef_ve_planlanan_miktarlar_tam_boy(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": D("1")}], depo_id=self.depo.pk,
                                   tarih=date(2026, 10, 9))
        kayitlar = {k.operasyon_id: k for k in emir.operasyon_kayitlari.filter(silindi=False)}
        kesim = kayitlar[self.kesim_op.pk]
        self.assertEqual(kesim.hedef_cikti_miktari, D("18.000"))                       # çalıştırma 1 × 18
        self.assertEqual(kaydi_girdi_satirlari(kesim).get(girdi=self.profil).planlanan_miktar, D("1.000"))
        self.assertEqual(kayitlar[self.mamul_op.pk].hedef_cikti_miktari, D("1.000"))

    def test_fazla_parca_onayda_stoga_girer(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("5"))
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": D("1")}], depo_id=self.depo.pk,
                                   tarih=date(2026, 10, 9))
        kayitlar = {k.operasyon_id: k for k in emir.operasyon_kayitlari.filter(silindi=False)}
        operasyon_kaydi_onayla(kayitlar[self.kesim_op.pk])
        self.assertEqual(eldeki_miktar(self.kesilmis, self.depo), D("18"))
        self.assertEqual(eldeki_miktar(self.profil, self.depo), D("4"))
        operasyon_kaydi_onayla(kayitlar[self.mamul_op.pk])
        self.assertEqual(eldeki_miktar(self.kesilmis, self.depo), D("17"))              # fazla 17 stokta kaldı
        self.assertEqual(eldeki_miktar(self.mamul, self.depo), D("1"))

    def test_elle_kayitta_hedef_cikti_miktarinin_katina_yukselir(self):
        k = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9),
                                    hedef_cikti_miktari=D("5"))
        self.assertEqual(k.hedef_cikti_miktari, D("18.000"))
        self.assertEqual(kaydi_girdi_satirlari(k).get(girdi=self.profil).planlanan_miktar, D("1.000"))

    def test_kesirli_boy_girdisi_onayda_ve_guncellemede_reddedilir(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("5"))
        k = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9),
                                    hedef_cikti_miktari=D("18"))
        satir = kaydi_girdi_satirlari(k).get(girdi=self.profil)
        with self.assertRaisesMessage(UretimHatasi, "Bu kesim tam boy kesilmelidir; girdi miktarı tam sayı olmalı."):
            operasyon_kaydi_girdi_guncelle(satir, gerceklesen_miktar=D("0.5"))
        satir.refresh_from_db()
        self.assertEqual(satir.gerceklesen_miktar, D("1.000"))
        OperasyonKaydi  # noqa
        from core.models import OperasyonKaydiGirdi
        OperasyonKaydiGirdi.objects.filter(pk=satir.pk).update(gerceklesen_miktar=D("0.5"))      # arkadan sızan kesirli değer
        k.refresh_from_db()
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_onayla(k)
        self.assertFalse(StokHareket.objects.filter(operasyon_kaydi=k).exists())
        operasyon_kaydi_girdi_guncelle(kaydi_girdi_satirlari(k).get(girdi=self.profil), gerceklesen_miktar=D("2"))   # tam sayı serbest

    def test_tam_olmayan_kesimde_kesirli_girdi_serbest(self):
        mt = self.stok("KAYIS", self.mt, satinalma=True)
        kes = self.stok("KAYISKES")
        op = self.op(self.kesim, kes, 4, [(mt, 1)])
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("10"))
        s = kaydi_girdi_satirlari(k).get(girdi=mt)
        self.assertEqual(s.planlanan_miktar, D("2.500"))
        operasyon_kaydi_girdi_guncelle(s, gerceklesen_miktar=D("2.75"))              # hata yok


class OperasyonGuncelleTest(TamBoyBase):
    def test_tam_calistirma_girdi_birimine_gore_otomatik_ve_girdi_degisince_guncellenir(self):
        boy_profil = self.stok("BOYPROFIL", self.boy, satinalma=True)
        adet_parca = self.stok("ADETPARCA", self.adet, satinalma=True)
        mt_kayis = self.stok("KAYIS", self.mt, satinalma=True)
        kes = self.stok("KES")
        op = self.op(self.kesim, kes, 2, [(adet_parca, 1)])
        self.assertFalse(op.tam_calistirma)                                                                   # AD → False
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("2"), satirlar=[(boy_profil, D("1"))])
        op.refresh_from_db()
        self.assertTrue(op.tam_calistirma)                                                                    # BOY → True
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("2"), satirlar=[(mt_kayis, D("1"))])
        op.refresh_from_db()
        self.assertFalse(op.tam_calistirma)                                                                   # MT → False
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("2"), satirlar=[(mt_kayis, D("1")), (boy_profil, D("2"))])
        op.refresh_from_db()
        self.assertTrue(op.tam_calistirma)                                                                    # girdilerden biri BOY → True

    def test_olusturmada_birim_boy_ise_true(self):
        boy_profil = self.stok("BOYPROFIL2", self.boy, satinalma=True)
        self.assertTrue(self.op(self.kesim, self.stok("K1"), 2, [(boy_profil, 1)]).tam_calistirma)
        self.assertFalse(self.op(self.kesim, self.stok("K2"), 2, [(self.stok("AD1", self.adet, satinalma=True), 1)]).tam_calistirma)

    def test_ad_aciklama_olmadan_olusturma_guncelleme_ve_str(self):
        profil = self.stok("PROFIL3", self.boy, satinalma=True)
        kes = self.stok("KES3")
        op = self.op(self.kesim, kes, 2, [(profil, 1)])
        self.assertFalse(hasattr(op, "ad") or hasattr(op, "aciklama"))
        self.assertEqual(str(op), "KESIM KES3")                                                               # "<istasyon kodu> <çıktı kodu>"
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(profil, D("1"))])
        op.refresh_from_db()
        self.assertEqual(op.cikti_miktar, D("3"))


class VeriMigrationTest(TamBoyBase):
    def test_boy_girdili_aktif_operasyonlar_isaretlenir(self):
        mig = importlib.import_module("core.migrations.0178_operasyon_tam_calistirma_veri")
        boy_profil = self.stok("BOYPROFIL", self.boy, satinalma=True)
        mt_kayis = self.stok("KAYIS", self.mt, satinalma=True)
        kesilen = self.stok("KESILEN")
        kayis_kesim = self.stok("KAYISKES")
        silinmis_cikti = self.stok("SILINMIS")
        boy_op = self.op(self.kesim, kesilen, 2, [(boy_profil, 1)])
        mt_op = self.op(self.kesim, kayis_kesim, 4, [(mt_kayis, 1)])
        sil_op = self.op(self.kesim, silinmis_cikti, 2, [(boy_profil, 1)])
        from core.services.uretim import operasyon_sil
        operasyon_sil(sil_op)
        Operasyon.objects.update(tam_calistirma=False)                                  # migration öncesi durum (eski veri)
        mig.isaretle(apps, None)
        for o in (boy_op, mt_op, sil_op):
            o.refresh_from_db()
        self.assertTrue(boy_op.tam_calistirma)
        self.assertFalse(mt_op.tam_calistirma)
        self.assertFalse(sil_op.tam_calistirma)                                         # silinmiş operasyona dokunulmaz
