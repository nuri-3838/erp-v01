"""KARMA KESİM (Dilim 1: veri + ayar + servis): uzunluk bütçesi / fire dökümü, stok + maliyet (profil toplam maliyeti parçalara kesilen uzunluk oranıyla), uzunluk aşımı
reddi, emir dağıtımı (net ihtiyaç, Acil önce, artan serbest stok), emir planlananının daraltılması, grup geri alma, tek ölçülü tam boy akışının bozulmaması."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import (
    Birim, FaturaTipi, HesapPlani, IstasyonEmri, KarmaKesim, KategoriHesap, OperasyonKaydi, StokHareket, UretimAyari, UretimEmri,
)
from core.services import karma_kesim as kk, stok_ayirma
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, kaydi_girdi_satirlari, operasyon_kaydi_girdi_guncelle, operasyon_kaydi_olustur, operasyon_olustur, uretim_emri_olustur,
)
from core.tests.test_uretim import _depo, _istasyon, _kategori, _stok

D = Decimal
GUN = date(2026, 10, 9)
PARCALAR = {"P1": (D("349.73"), 16), "P2": (D("368.61"), 16), "P3": (D("387.48"), 15), "P4": (D("406.37"), 14)}      # (parça mm, boy başına adet)


class KarmaTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.boy = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        cls.kat = _kategori()
        hesap = HesapPlani.objects.create(hesap_kodu="150.10", hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
        KategoriHesap.objects.create(kategori=cls.kat, hesap=hesap, fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS"))
        cls.depo = _depo()
        cls.kesim = _istasyon("KESIM")
        cls.montaj = _istasyon("MONTAJ")
        cls.profil = _stok(cls.kat, cls.boy, kod="PROFIL", ad="profil 6000", satinalma=True)
        cls.profil.boy_uzunluk_mm = D("6000")
        cls.profil.save()
        cls.parca, cls.kesim_op = {}, {}
        for kod, (mm, adet) in PARCALAR.items():
            st = _stok(cls.kat, cls.adet, kod=kod, ad=f"parça {kod}")
            cls.parca[kod] = st
            cls.kesim_op[kod] = operasyon_olustur(istasyon_id=cls.kesim.pk, cikti_id=st.pk, cikti_miktar=D(adet), boy_mm=mm, satirlar=[(cls.profil, D("1"))])
        cls.mamul = _stok(cls.kat, cls.adet, kod="MAMUL", ad="mamul", satis=True)
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"),
                          satirlar=[(cls.parca["P1"], D("10")), (cls.parca["P2"], D("8"))])

    def profil_gir(self, boy=10, tl=12000):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D(boy), giris_tutar_try=D(tl))

    def kes(self, boy, satirlar, **kw):
        return kk.karma_kes(istasyon_id=self.kesim.pk, profil_id=self.profil.pk, depo_id=self.depo.pk, boy_sayisi=boy, tarih=GUN,
                            satirlar=[(self.parca[k].pk, a) for k, a in satirlar], **kw)

    def emir_ac(self, **kw):
        return uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=GUN, **kw)

    def ie(self, emir, kod):
        return IstasyonEmri.objects.get(uretim_emri=emir, operasyon=self.kesim_op[kod])

    def eldeki(self, stok):
        return eldeki_miktar(stok, self.depo)


KARMA_28 = [("P1", 10), ("P2", 8), ("P3", 6), ("P4", 4)]            # 28 parça, Σ 10.396,54 mm


class ButceTest(KarmaTaban):
    def test_fire_dokumu_testere_cene_uc(self):
        b = kk.butce(2, D("6000"), D("3"), D("75"), [(a, PARCALAR[k][0]) for k, a in KARMA_28])
        self.assertEqual(b["parca_mm"], D("10396.54"))
        self.assertEqual((b["toplam_adet"], b["testere_mm"], b["cene_mm"]), (28, D("84"), D("150")))
        self.assertEqual(b["fire_mm"], D("1603.46"))                                     # 12000 − 10396,54
        self.assertEqual(b["uc_mm"], D("1369.46"))                                       # fire − testere − çene
        self.assertEqual(b["fire_mm"], b["testere_mm"] + b["cene_mm"] + b["uc_mm"])
        self.assertEqual(b["fire_orani"].quantize(D("0.0001")), D("0.1336"))
        self.assertEqual((b["kullanilabilir_mm"], b["gerekli_mm"]), (D("11850"), D("10480.54")))
        self.assertLessEqual(b["fazla_mm"], 0)

    def test_sinir_tam_sigan_kabul_bir_mm_fazla_ret(self):
        b = kk.butce(1, D("6000"), D("3"), D("75"), [(1, D("5922"))])                   # 5922 + 3 = 5925 = 6000 − 75
        self.assertEqual(b["fazla_mm"], 0)
        self.assertGreater(kk.butce(1, D("6000"), D("3"), D("75"), [(1, D("5922.01"))])["fazla_mm"], 0)


class AdayTest(KarmaTaban):
    def test_adaylar_istasyondaki_tek_girdili_profil_parcalari(self):
        a = kk.parca_adaylari(self.kesim.pk, self.profil.pk)
        self.assertEqual([(x["stok"].kod, x["parca_mm"]) for x in a], [(k, v[0]) for k, v in PARCALAR.items()])
        self.assertEqual(kk.parca_adaylari(self.montaj.pk, self.profil.pk), [])


class StokMaliyetTest(KarmaTaban):
    def test_stok_maliyet_ve_fire_kaydi(self):
        self.profil_gir(10, 12000)                                                       # 1.200 TL / boy
        karma = self.kes(2, KARMA_28)
        self.assertEqual(self.eldeki(self.profil), D("8"))                               # yalnız 2 BOY düştü (fire ayrı stok hareketi YAZMAZ)
        for k, a in KARMA_28:
            self.assertEqual(self.eldeki(self.parca[k]), D(a))
        cikis = sum((g.gerceklesen_miktar for kayit in karma.kayitlar.all() for g in kaydi_girdi_satirlari(kayit)), D("0"))
        self.assertEqual(cikis, D("2"))                                                  # Σ profil payı tam boy sayısı
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi__karma=karma, tur=StokHareket.Tur.GIRIS).count(), 4)   # fire için giriş YOK
        toplam_mm = D("10396.54")
        for k, a in KARMA_28:
            tutar = sum(h.tutar_try for h in StokHareket.objects.filter(operasyon_kaydi__karma=karma, tur=StokHareket.Tur.GIRIS, stok=self.parca[k]))
            beklenen = D("2400") * a * PARCALAR[k][0] / toplam_mm                        # toplam profil maliyeti (fire dahil) uzunluk oranıyla
            self.assertAlmostEqual(float(tutar), float(beklenen), delta=0.02, msg=k)
            birim = tutar / a                                                            # parça birim maliyeti = toplam × parça mm / Σ(kesilen uzunluk)
            self.assertAlmostEqual(float(birim), float(D("2400") * PARCALAR[k][0] / toplam_mm), delta=0.01, msg=k)
        genel = sum(h.tutar_try for h in StokHareket.objects.filter(operasyon_kaydi__karma=karma, tur=StokHareket.Tur.GIRIS))
        self.assertAlmostEqual(float(genel), 2400.0, delta=0.05)                         # fire ayrıca maliyetlenmez: tüm tutar parçalarda
        self.assertEqual((karma.fire_mm, karma.testere_mm, karma.cene_mm, karma.uc_mm), (D("1603.46"), D("84"), D("150"), D("1369.46")))
        self.assertEqual((karma.boy_sayisi, karma.toplam_adet, karma.parca_mm), (2, 28, D("10396.54")))
        self.assertEqual(karma.fire_orani.quantize(D("0.0001")), D("0.1336"))
        self.assertEqual({s.stok.kod: s.adet for s in karma.satirlar.all()}, dict(KARMA_28))

    def test_ayar_degisince_gecmis_kayit_snapshotlari_korunur(self):
        self.profil_gir()
        karma = self.kes(2, KARMA_28)
        UretimAyari.objects.update_or_create(pk=1, defaults={"testere_payi_mm": D("5"), "cene_payi_mm": D("100")})
        karma.refresh_from_db()
        self.assertEqual((karma.testere_payi_mm, karma.cene_payi_mm, karma.boy_mm), (D("3"), D("75"), D("6000")))
        self.assertEqual(kk.ayarlar(), {"testere_payi_mm": D("5"), "cene_payi_mm": D("100")})


class RetTest(KarmaTaban):
    def sifir(self):
        self.assertEqual(KarmaKesim.objects.count(), 0)
        self.assertEqual(OperasyonKaydi.objects.count(), 0)
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi__isnull=False).count(), 0)

    def test_uzunluk_asimi_reddi_net_mesaj(self):
        self.profil_gir()
        with self.assertRaises(kk.KarmaKesimHatasi) as cm:
            self.kes(2, [("P1", 10), ("P2", 8), ("P3", 8), ("P4", 6)])                   # 11.984,24 + 32 × 3 = 12.080,24 > 11.850
        self.assertIn("Kesim uzunluğu aşıldı: gerekli 12.080,24 mm", str(cm.exception))
        self.assertIn("kullanılabilir 11.850,00 mm", str(cm.exception))
        self.assertIn("230,24 mm fazla", str(cm.exception))
        self.sifir()

    def test_yetersiz_stok_hicbir_sey_birakmaz(self):
        self.profil_gir(1, 1200)
        with self.assertRaises(UretimHatasi):
            self.kes(2, KARMA_28)
        self.sifir()
        self.assertEqual(self.eldeki(self.profil), D("1"))

    def test_boy_sayisi_tam_sayi_ve_pozitif(self):
        self.profil_gir()
        for ham in ("1,5", "0", "-1", "", "abc"):
            with self.assertRaises(kk.KarmaKesimHatasi, msg=ham):
                self.kes(ham, [("P1", 1)])
        self.sifir()

    def test_profil_boyu_tanimsiz_reddi(self):
        self.profil_gir()
        self.profil.boy_uzunluk_mm = None
        self.profil.save()
        with self.assertRaisesMessage(kk.KarmaKesimHatasi, "profil kartında boy uzunluğu (mm) tanımlı değil"):
            self.kes(1, [("P1", 1)])

    def test_aday_olmayan_ve_boyu_tanimsiz_parca_reddi(self):
        self.profil_gir()
        with self.assertRaisesMessage(kk.KarmaKesimHatasi, "bu istasyonda bu profilden kesilen bir parça değil"):
            kk.karma_kes(istasyon_id=self.kesim.pk, profil_id=self.profil.pk, depo_id=self.depo.pk, boy_sayisi=1, tarih=GUN, satirlar=[(self.mamul.pk, 1)])
        self.kesim_op["P1"].boy_mm = None
        self.kesim_op["P1"].save()
        self.kesim_op["P1"].ciktilar.update(boy_mm=None)
        with self.assertRaisesMessage(kk.KarmaKesimHatasi, "parça boyu (mm) tanımlı değil"):
            self.kes(1, [("P1", 1)])
        self.sifir()

    def test_adet_ve_tarih_kurallari(self):
        self.profil_gir()
        with self.assertRaises(kk.KarmaKesimHatasi):
            self.kes(1, [("P1", "2,5")])
        with self.assertRaises(kk.KarmaKesimHatasi):
            self.kes(1, [])
        with self.assertRaisesMessage(kk.KarmaKesimHatasi, "Tarih bugünden ileri olamaz."):
            kk.karma_kes(istasyon_id=self.kesim.pk, profil_id=self.profil.pk, depo_id=self.depo.pk, boy_sayisi=1, tarih=date(2099, 1, 1),
                         satirlar=[(self.parca["P1"].pk, 1)])
        self.sifir()


class EmirTest(KarmaTaban):
    def setUp(self):
        self.profil_gir(10, 12000)

    def test_net_ihtiyaca_gore_dagitim_artan_serbest_stoga_ve_emir_daralir(self):
        emir = self.emir_ac()
        ie1, ie2 = self.ie(emir, "P1"), self.ie(emir, "P2")
        self.assertEqual((ie1.planlanan, ie2.planlanan), (D("1"), D("1")))               # tam boy planı: 16 parça
        karma = self.kes(2, [("P1", 12), ("P2", 8)])                                      # P1: 10 emre + 2 serbest, P2: 8 emre
        p1 = karma.kayitlar.filter(operasyon=self.kesim_op["P1"])
        self.assertEqual(sorted((k.istasyon_emri_id, k.hedef_cikti_miktari) for k in p1 if k.istasyon_emri_id), [(ie1.pk, D("10"))])
        self.assertEqual([k.hedef_cikti_miktari for k in p1 if k.istasyon_emri_id is None], [D("2")])        # emri olmayan çıktı serbest stoğa
        self.assertEqual(karma.kayitlar.count(), 3)
        self.assertEqual(stok_ayirma.ayrilan_miktar(emir, self.parca["P1"].pk), D("10"))                 # ÜS'ye yalnız net ihtiyaç ayrıldı
        self.assertEqual(stok_ayirma.ayrilan_miktar(emir, self.parca["P2"].pk), D("8"))
        self.assertEqual(self.eldeki(self.parca["P1"]), D("12"))                          # fazlası serbest stokta
        ie1.refresh_from_db(); ie2.refresh_from_db()
        self.assertEqual((ie1.tamamlanan, ie1.planlanan, ie1.durum), (D("0.625"), D("0.625"), IstasyonEmri.Durum.BITTI))     # ihtiyaç karşılandı → planlanan daraldı
        self.assertEqual((ie2.tamamlanan, ie2.planlanan, ie2.durum), (D("0.5"), D("0.5"), IstasyonEmri.Durum.BITTI))
        kayit = karma.kayitlar.get(istasyon_emri=ie1)
        self.assertEqual(kayit.karma_iz, {"ie": ie1.pk, "onceki_planlanan": "1"})

    def test_acil_us_once_sonra_termin_normal_us_kalani_alir(self):
        normal = self.emir_ac()
        acil = self.emir_ac(oncelik=UretimEmri.Oncelik.ACIL)
        karma = self.kes(2, [("P1", 15)])
        ie_acil, ie_normal = self.ie(acil, "P1"), self.ie(normal, "P1")
        hedef = {k.istasyon_emri_id: k.hedef_cikti_miktari for k in karma.kayitlar.all()}
        self.assertEqual(hedef, {ie_acil.pk: D("10"), ie_normal.pk: D("5")})              # Acil ÜS ihtiyacı (10) tamamen, kalan 5 normal ÜS'ye
        ie_acil.refresh_from_db(); ie_normal.refresh_from_db()
        self.assertEqual(ie_acil.durum, IstasyonEmri.Durum.BITTI)
        self.assertEqual((ie_normal.durum, ie_normal.planlanan, ie_normal.tamamlanan), (IstasyonEmri.Durum.BASLADI, D("1"), D("5") / D("16")))   # ihtiyaç karşılanmadı: daralmaz

    def test_ikinci_karma_kalan_ihtiyaci_tamamlar_ve_daraltir(self):
        emir = self.emir_ac()
        self.kes(1, [("P1", 6)])
        ie = self.ie(emir, "P1")
        ie.refresh_from_db()
        self.assertEqual((ie.planlanan, ie.durum), (D("1"), IstasyonEmri.Durum.BASLADI))
        k2 = self.kes(1, [("P1", 6)])                                                     # kalan net ihtiyaç 4 → emre 4, 2 serbest
        ie.refresh_from_db()
        self.assertEqual((ie.durum, ie.planlanan, ie.tamamlanan), (IstasyonEmri.Durum.BITTI, D("0.625"), D("0.625")))
        self.assertEqual(sorted(k.hedef_cikti_miktari for k in k2.kayitlar.all()), [D("2"), D("4")])

    def test_grup_geri_alma_her_seyi_eski_haline_dondurur(self):
        emir = self.emir_ac()
        ie1 = self.ie(emir, "P1")
        ayirma_once = stok_ayirma.ayrilan_miktar(emir, self.profil.pk)
        karma = self.kes(2, [("P1", 12), ("P2", 8)])
        kk.karma_geri_al(karma)
        karma.refresh_from_db()
        self.assertTrue(karma.silindi)
        self.assertEqual(OperasyonKaydi.objects.filter(karma=karma, silindi=False).count(), 0)
        self.assertEqual(self.eldeki(self.profil), D("10"))
        self.assertEqual(self.eldeki(self.parca["P1"]), D("0"))
        ie1.refresh_from_db()
        self.assertEqual((ie1.planlanan, ie1.tamamlanan, ie1.durum), (D("1"), D("0"), IstasyonEmri.Durum.BEKLIYOR))
        self.assertEqual(stok_ayirma.ayrilan_miktar(emir, self.parca["P1"].pk), D("0"))
        self.assertEqual(stok_ayirma.ayrilan_miktar(emir, self.profil.pk), ayirma_once)  # profil ayırması da geri geldi
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi__karma=karma, silindi=False).count(), 0)
        kk.karma_geri_al(karma)                                                           # ikinci çağrı sessiz

    def test_toplam_profil_cikisi_tam_boy_sayisi_yuvarlama_farki_dahil(self):
        self.emir_ac()
        karma = self.kes(3, [("P1", 17), ("P2", 13), ("P3", 7)])
        cikis = sum((g.gerceklesen_miktar for k in karma.kayitlar.all() for g in kaydi_girdi_satirlari(k)), D("0"))
        self.assertEqual(cikis, D("3"))
        self.assertEqual(self.eldeki(self.profil), D("7"))


class MevcutAkisTest(KarmaTaban):
    def test_tek_olculu_kayit_tam_boy_kurali_aynen(self):
        self.profil_gir()
        k = operasyon_kaydi_olustur(operasyon_id=self.kesim_op["P1"].pk, depo_id=self.depo.pk, tarih=GUN, hedef_cikti_miktari=D("10"))
        self.assertEqual(k.hedef_cikti_miktari, D("16"))                                  # karma dışı: çıktı miktarının katına yükselir
        self.assertIsNone(k.karma_id)
        satir = kaydi_girdi_satirlari(k).get(girdi=self.profil)
        with self.assertRaisesMessage(UretimHatasi, "Bu kesim tam boy kesilmelidir; girdi miktarı tam sayı olmalı."):
            operasyon_kaydi_girdi_guncelle(satir, gerceklesen_miktar=D("0.5"))
