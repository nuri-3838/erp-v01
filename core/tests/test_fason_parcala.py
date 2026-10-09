"""FASON dönüş × PARÇALA: çıktı bazında GELEN adet girilir, beklenen gelenlerden türetilir (çalıştırma = max_i gelen_i/miktar_i, tam boyda ⌈·⌉),
fire_i = beklenen_i − gelen_i; boş çıktı 0 gelen; referans çıktı gelmese de onay; gelen değişince hedef/girdi planı yenilenir; EŞİT pay gelen adetten (fire
gelenlere yayılır); geri al ve faturasız tahakkuk (151 alt hesabı çıktı stoğundan) PARÇALA'da da çalışır."""
from decimal import Decimal

from core.models import Operasyon, OperasyonKaydiCikti, Stok, StokHareket, YevmiyeFisi
from core.services import fason as fs
from core.services import fason_donus as fd
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar
from core.services.uretim import kaydi_girdi_satirlari, operasyon_olustur
from core.tests.test_fason_donus import TARIH
from core.tests.test_fason_fatura import FaturaBase

D = Decimal


class FasonParcalaBase(FaturaBase):
    def parcala(self, tam_boy=False, sag_m="12", sol_m="12"):
        kat, birim = self.ana.kategori, self.ana.uretim_birimi
        self.sag = Stok.objects.create(kod="151-20-0014", ad="MENTEŞE SAĞ", kategori=kat, uretim_birimi=birim, fatura_birimi=birim, uretim_urunu=True)
        self.sol = Stok.objects.create(kod="151-20-0015", ad="MENTEŞE SOL", kategori=kat, uretim_birimi=birim, fatura_birimi=birim, uretim_urunu=True)
        self.pop = operasyon_olustur(istasyon_id=self.op.istasyon_id, satirlar=[(self.profil, D("1"))], tur=Operasyon.Tur.PARCALA,
                                     pay_anahtari=Operasyon.PayAnahtari.ESIT, tam_boy=tam_boy,
                                     ciktilar=[(self.sag, D(sag_m), None, None), (self.sol, D(sol_m), None, None)])
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.sag.pk, birim_fiyat=D("10"), gecerlilik_baslangic=TARIH)
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.sol.pk, birim_fiyat=D("6"), gecerlilik_baslangic=TARIH)
        self.fason_profil(10, 6400, 160)                                                       # 640 TL / boy
        return self.pop

    def donus(self, sag=None, sol=None, onayla=False):
        satir = (self.pop.pk, sag, None, ({self.sol.pk: sol} if sol is not None else None))
        return fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[satir], onayla=onayla)

    def ciktilar(self, d):
        return {c["stok"].kod: c for c in fd.donus_bilgisi(d)["satirlar"][0]["ciktilar"]}

    def snap(self, k):
        return {c.stok.kod: c for c in OperasyonKaydiCikti.objects.filter(kayit=k, silindi=False).select_related("stok")}


class GelenTest(FasonParcalaBase):
    def test_tam_boy_kapali_beklenen_gelenden_fire_cikti_bazinda(self):
        self.parcala(tam_boy=False)
        d = self.donus(sag="20", sol="10")
        k = d.kayitlar.get()
        self.assertEqual(k.hedef_cikti_miktari, D("20"))                                       # referans SAĞ: çalıştırma 20/12
        self.assertEqual(kaydi_girdi_satirlari(k).get().planlanan_miktar, D("1.666667"))
        c = self.ciktilar(d)
        self.assertEqual((c["151-20-0014"]["beklenen"], c["151-20-0014"]["miktar"], c["151-20-0014"]["fire"]), (D("20"), D("20"), D("0")))
        self.assertEqual((c["151-20-0015"]["beklenen"], c["151-20-0015"]["miktar"], c["151-20-0015"]["fire"]), (D("20"), D("10"), D("10")))
        fd.donus_onayla(d)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("8.333333"))
        self.assertEqual((eldeki_miktar(self.sag, self.depo), eldeki_miktar(self.sol, self.depo)), (D("20"), D("10")))
        s = self.snap(k)
        self.assertEqual((s["151-20-0014"].beklenen_miktar, s["151-20-0014"].miktar, s["151-20-0014"].agirlik), (D("20"), D("20"), D("20")))
        self.assertEqual((s["151-20-0015"].beklenen_miktar, s["151-20-0015"].miktar, s["151-20-0015"].agirlik), (D("20"), D("10"), D("10")))
        g = {h.stok.kod: h for h in StokHareket.objects.filter(operasyon_kaydi=k, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}
        malzeme = D("1066.67")                                                                  # 1,666667 boy × 640
        self.assertEqual(g["151-20-0014"].tutar_try, (malzeme * 20 / 30).quantize(D("0.01")) + D("200.00"))   # EŞİT, gelen adetten: 20/30 + fason 20×10
        self.assertEqual(g["151-20-0015"].tutar_try, malzeme - (malzeme * 20 / 30).quantize(D("0.01")) + D("60.00"))
        self.assertEqual(fd.donus_bilgisi(d)["toplam_fire"], D("10"))

    def test_tam_boy_acik_yukari_yuvarlanir(self):
        self.parcala(tam_boy=True)
        d = self.donus(sag="20", sol="10")
        k = d.kayitlar.get()
        self.assertEqual((k.hedef_cikti_miktari, kaydi_girdi_satirlari(k).get().planlanan_miktar), (D("24"), D("2")))   # ⌈20/12⌉ = 2 boy
        c = self.ciktilar(d)
        self.assertEqual((c["151-20-0014"]["beklenen"], c["151-20-0014"]["fire"], c["151-20-0015"]["beklenen"], c["151-20-0015"]["fire"]), (D("24"), D("4"), D("24"), D("14")))
        fd.donus_onayla(d)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("8"))

    def test_referans_gelmese_de_onaylanir_ve_hepsi_sifir_reddedilir(self):
        self.parcala(tam_boy=False)
        d = self.donus(sag="5")
        k = d.kayitlar.get()
        fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "", f"gelen_{k.pk}_{self.sol.pk}": "5"})     # yalnız SOL geldi (boş = 0)
        k.refresh_from_db()
        self.assertEqual(k.hedef_cikti_miktari, D("5"))
        c = self.ciktilar(d)
        self.assertEqual((c["151-20-0014"]["miktar"], c["151-20-0014"]["fire"], c["151-20-0015"]["miktar"], c["151-20-0015"]["fire"]), (D("0"), D("5"), D("5"), D("0")))
        with self.assertRaisesMessage(FasonHatasi, "en az bir çıktı"):
            fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "", f"gelen_{k.pk}_{self.sol.pk}": ""})
        fd.donus_onayla(d)
        self.assertEqual((eldeki_miktar(self.sag, self.depo), eldeki_miktar(self.sol, self.depo)), (D("0"), D("5")))
        s = self.snap(k)
        self.assertEqual((s["151-20-0014"].miktar, s["151-20-0014"].pay_orani, s["151-20-0015"].pay_orani), (D("0"), D("0"), D("1")))
        g = StokHareket.objects.get(operasyon_kaydi=k, silindi=False, tur=StokHareket.Tur.GIRIS)
        self.assertEqual((g.stok_id, g.tutar_try), (self.sol.pk, D("266.67") + D("30.00")))     # 5/12 boy × 640 = 266,67 tamamı SOL'a + fason 5×6

    def test_gelen_degisince_hedef_ve_girdi_plani_yenilenir(self):
        self.parcala(tam_boy=False)
        d = self.donus(sag="20")
        k = d.kayitlar.get()
        self.assertEqual(kaydi_girdi_satirlari(k).get().planlanan_miktar, D("1.666667"))
        fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "6", f"gelen_{k.pk}_{self.sol.pk}": "24"})     # SOL sürücü: 24/12 = 2
        k.refresh_from_db()
        self.assertEqual((k.hedef_cikti_miktari, kaydi_girdi_satirlari(k).get().planlanan_miktar), (D("24"), D("2")))
        self.assertEqual(k.gelen, {str(self.sag.pk): "6", str(self.sol.pk): "24"})          # tek yapı: referans dahil
        c = self.ciktilar(d)
        self.assertEqual((c["151-20-0014"]["beklenen"], c["151-20-0014"]["fire"], c["151-20-0015"]["fire"]), (D("24"), D("18"), D("0")))
        with self.assertRaisesMessage(FasonHatasi, "negatif"):
            fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "-1"})


class GeriAlTahakkukTest(FasonParcalaBase):
    def test_geri_al_ve_faturasiz_tahakkuk(self):
        self.parcala(tam_boy=False)
        self.salim.fason_faturasiz = True
        self.salim.save(update_fields=["fason_faturasiz"])
        d = self.donus(sag="20", sol="10", onayla=True)
        d.refresh_from_db()
        fis = d.tahakkuk_fis
        self.assertIsNotNone(fis)
        satirlar = {(s.hesap_id, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False)}
        self.assertEqual(satirlar, {("151.10", D("260.00"), D("0.00")), ("320.10.0001", D("0.00"), D("260.00"))})   # 20×10 + 10×6, gelen adetten
        self.assertEqual(self.satir151()["fark"], D("0.00"))
        fd.donus_geri_al_sil(d)
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)
        self.assertEqual((eldeki_miktar(self.profil, self.fdepo), eldeki_miktar(self.sag, self.depo), eldeki_miktar(self.sol, self.depo)), (D("10"), D("0"), D("0")))
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.URETIM, silindi=False).count(), 0)
