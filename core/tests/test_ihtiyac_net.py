"""NET ihtiyaç motoru (plan adım 2 — docs/uretim-siparisi-plan.md): ``ihtiyac_hesapla(kullanilabilir=)`` her seviyede toplanmış talepten
kullanılabilir stoğu düşer (ayrılan = min(talep, kalan), net = talep − ayrılan; çalıştırma NETTEN, tam boy ⌈·⌉, PARÇALA max; fazla serbest),
yaprakta eksik = satınalma ihtiyacı; eksi kullanılabilir 0; çağrılabilir biçimi zincir keşfinden sonra tek kez çağrılır; çoklu kökte toplanmış talep
üzerinden tek ayırma; ağaç dalları net oranıyla küçülür. ``None`` iken brüt hesap birebir aynı (golden ayrıca). İhtiyaç Hesapla ekranı "Kullanılabilir
stoğu düş" seçeneği (varsayılan brüt)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Operasyon, StokHareket
from core.services import stok_ayirma as sa
from core.services.hareket import hareket_ekle
from core.services.uretim import ihtiyac_hesapla, operasyon_olustur, uretim_emri_olustur
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal


def ozet(sonuc):
    return {o["stok"].kod: o for o in sonuc["ozet"]}


def plan(sonuc):
    return {p["stok"].kod: p for p in sonuc["plan"]}


class NetBase(TamBoyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.profil = cls._stok_c("PROFIL", cls.boy, satinalma=True, uretim=False)
        cls.kesilmis = cls._stok_c("KESILMIS")
        cls.mamul = cls._stok_c("MAMUL", satis=True)
        cls.mamul2 = cls._stok_c("MAMUL2", satis=True)
        operasyon_olustur(istasyon_id=cls.kesim.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=D("18"), satirlar=[(cls.profil, D("1"))])   # tam boy (BOY)
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"), satirlar=[(cls.kesilmis, D("1"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul2.pk, cikti_miktar=D("1"), satirlar=[(cls.kesilmis, D("2"))])

    @classmethod
    def _stok_c(cls, kod, birim=None, **kw):
        from core.tests.test_uretim import _stok
        return _stok(cls.kat, birim or cls.adet, kod=kod, ad=kod.lower(), **kw)


class MotorTest(NetBase):
    def test_uret_tam_boy_zinciri_net(self):
        brut = ihtiyac_hesapla([(self.mamul, D("5"))])
        self.assertFalse(brut["net_mod"])
        self.assertEqual((ozet(brut)["KESILMIS"]["ihtiyac"], ozet(brut)["KESILMIS"]["uretilecek_miktar"], ozet(brut)["KESILMIS"]["fazla_miktar"], ozet(brut)["PROFIL"]["ihtiyac"]),
                         (D("5"), D("18"), D("13"), D("1")))
        self.assertEqual((ozet(brut)["KESILMIS"]["ayrilan"], ozet(brut)["KESILMIS"]["net"], ozet(brut)["PROFIL"]["eksik"], ozet(brut)["KESILMIS"]["eksik"]), (D("0"), D("5"), D("1"), None))
        net = ihtiyac_hesapla([(self.mamul, D("5"))], kullanilabilir={self.mamul.pk: D("2"), self.kesilmis.pk: D("1"), self.profil.pk: D("0")})
        self.assertTrue(net["net_mod"])
        kok = plan(net)["MAMUL"]
        self.assertEqual((kok["ihtiyac"], kok["ayrilan"], kok["net"], kok["uretilecek"], kok["fazla"]), (D("5"), D("2"), D("3"), D("3"), D("0")))
        k = ozet(net)["KESILMIS"]
        self.assertEqual((k["ihtiyac"], k["ayrilan"], k["net"], k["calistirma_sayisi"], k["uretilecek_miktar"], k["fazla_miktar"]), (D("3"), D("1"), D("2"), D("1"), D("18"), D("16")))
        p = ozet(net)["PROFIL"]
        self.assertEqual((p["ihtiyac"], p["ayrilan"], p["eksik"], p["yaprak"]), (D("1"), D("0"), D("1"), True))
        self.assertEqual(net["ayrilan"], {self.mamul.pk: D("2"), self.kesilmis.pk: D("1")})
        # kesilmiş tamamen stoktan karşılanırsa kesim gerekmez, profil talebi 0
        net2 = ihtiyac_hesapla([(self.mamul, D("5"))], kullanilabilir={self.kesilmis.pk: D("9")})
        self.assertEqual((ozet(net2)["KESILMIS"]["net"], ozet(net2)["KESILMIS"]["uretilecek_miktar"], ozet(net2)["PROFIL"]["ihtiyac"]), (D("0"), D("0"), D("0")))

    def test_parcala_kardes_ve_tam_boy(self):
        sag, sol, u = self._stok_c("SAG"), self._stok_c("SOL"), self._stok_c("U4")
        boy = self._stok_c("BOY2", self.boy, satinalma=True, uretim=False)
        pop = operasyon_olustur(istasyon_id=self.kesim.pk, satirlar=[(boy, D("1"))], tur=Operasyon.Tur.PARCALA, pay_anahtari=Operasyon.PayAnahtari.ESIT,
                                tam_boy=False, ciktilar=[(sag, D("64"), None, None), (sol, D("64"), None, None)])
        operasyon_olustur(istasyon_id=self.montaj.pk, cikti_id=u.pk, cikti_miktar=D("1"), satirlar=[(sag, D("2")), (sol, D("2"))])
        net = ihtiyac_hesapla([(u, D("1"))], kullanilabilir={sag.pk: D("2")})
        c = {x["stok"].kod: x for x in plan(net)["SAG"]["ciktilar"]}
        self.assertEqual((c["SAG"]["ihtiyac"], c["SAG"]["ayrilan"], c["SAG"]["net"], c["SAG"]["uretilecek"], c["SAG"]["fazla"]), (D("2"), D("2"), D("0"), D("2"), D("2")))
        self.assertEqual((c["SOL"]["ihtiyac"], c["SOL"]["ayrilan"], c["SOL"]["net"], c["SOL"]["uretilecek"], c["SOL"]["fazla"]), (D("2"), D("0"), D("2"), D("2"), D("0")))
        self.assertEqual((plan(net)["SAG"]["calistirma"], ozet(net)["BOY2"]["ihtiyac"]), (D("2") / 64, D("2") / 64))
        # SAĞ da SOL da stoktan: çalıştırma 0, boy talebi 0
        net0 = ihtiyac_hesapla([(u, D("1"))], kullanilabilir={sag.pk: D("5"), sol.pk: D("5")})
        self.assertEqual((plan(net0)["SAG"]["calistirma"], ozet(net0)["BOY2"]["ihtiyac"]), (D("0"), D("0")))
        # tam boy açık: net SOL 2 → çalıştırma 1 boy; SAĞ 64 üretilir (fazla 64), SOL fazla 62
        from core.services.uretim import operasyon_guncelle
        operasyon_guncelle(pop, istasyon_id=self.kesim.pk, satirlar=[(boy, D("1"))], tam_boy=True, tur=Operasyon.Tur.PARCALA,
                           pay_anahtari=Operasyon.PayAnahtari.ESIT, ciktilar=[(sag, D("64"), None, None), (sol, D("64"), None, None)])
        nett = ihtiyac_hesapla([(u, D("1"))], kullanilabilir={sag.pk: D("2")})
        c = {x["stok"].kod: x for x in plan(nett)["SAG"]["ciktilar"]}
        self.assertEqual((plan(nett)["SAG"]["calistirma"], c["SAG"]["uretilecek"], c["SAG"]["fazla"], c["SOL"]["uretilecek"], c["SOL"]["fazla"], ozet(nett)["BOY2"]["ihtiyac"]),
                         (D("1"), D("64"), D("64"), D("64"), D("62"), D("1")))

    def test_eksi_kullanilabilir_cagrilabilir_ve_bos_sozluk(self):
        net = ihtiyac_hesapla([(self.mamul, D("5"))], kullanilabilir={self.kesilmis.pk: D("-3")})
        self.assertEqual((ozet(net)["KESILMIS"]["ayrilan"], ozet(net)["KESILMIS"]["net"]), (D("0"), D("5")))
        gorulen = {}

        def harita(idler):
            gorulen["idler"] = set(idler)
            return {self.kesilmis.pk: D("1")}
        net = ihtiyac_hesapla([(self.mamul, D("5"))], kullanilabilir=harita)
        self.assertEqual(gorulen["idler"], {self.mamul.pk, self.kesilmis.pk, self.profil.pk})      # zincir keşfedildikten sonra tek çağrı
        self.assertEqual(ozet(net)["KESILMIS"]["net"], D("4"))
        bos = ihtiyac_hesapla([(self.mamul, D("5"))], kullanilabilir={})
        brut = ihtiyac_hesapla([(self.mamul, D("5"))])
        for kod in ("KESILMIS", "PROFIL"):
            for alan in ("ihtiyac", "calistirma_sayisi", "uretilecek_miktar", "fazla_miktar", "ayrilan", "net"):
                self.assertEqual(ozet(bos)[kod][alan], ozet(brut)[kod][alan], (kod, alan))
        self.assertTrue(bos["net_mod"])

    def test_coklu_kok_toplanmis_talepte_tek_ayirma(self):
        net = ihtiyac_hesapla([(self.mamul, D("1")), (self.mamul2, D("1"))], kullanilabilir={self.kesilmis.pk: D("2")})   # talep 1 + 2 = 3
        k = ozet(net)["KESILMIS"]
        self.assertEqual((k["ihtiyac"], k["ayrilan"], k["net"], k["uretilecek_miktar"]), (D("3"), D("2"), D("1"), D("18")))
        self.assertEqual(sum(1 for p in net["plan"] if p["stok"].kod == "KESILMIS"), 1)

    def test_agac_dallari_net_oraniyla_ve_dugum_alanlari(self):
        kesilmis2 = self._stok_c("KES2")
        m = self._stok_c("MAMUL3", satis=True)
        operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=kesilmis2.pk, cikti_miktar=D("2"), satirlar=[(self.profil, D("1"))], tam_boy=False)
        operasyon_olustur(istasyon_id=self.montaj.pk, cikti_id=m.pk, cikti_miktar=D("1"), satirlar=[(kesilmis2, D("4"))])
        net = ihtiyac_hesapla([(m, D("1"))], kullanilabilir={kesilmis2.pk: D("1")})         # talep 4, ayrılan 1, net 3 → profil 1,5
        kok = net["agac"][0]
        kes = kok["cocuklar"][0]
        self.assertEqual((kok["ayrilan_toplam"], kok["net_toplam"]), (D("0"), D("1")))
        self.assertEqual((kes["miktar"], kes["ayrilan_toplam"], kes["net_toplam"]), (D("4"), D("1"), D("3")))
        prof = kes["cocuklar"][0]
        self.assertEqual((prof["miktar"], prof["yaprak"], prof["ayrilan_toplam"], prof["eksik_toplam"]), (D("1.5"), True, D("0"), D("1.5")))
        self.assertEqual(ozet(net)["PROFIL"]["ihtiyac"], D("1.5"))
        brut = ihtiyac_hesapla([(m, D("1"))])
        self.assertEqual(brut["agac"][0]["cocuklar"][0]["cocuklar"][0]["miktar"], D("2"))

    def test_brut_varsayilan_degismedi(self):
        a = ihtiyac_hesapla([(self.mamul, D("5")), (self.mamul2, D("3"))])
        b = ihtiyac_hesapla([(self.mamul, D("5")), (self.mamul2, D("3"))], kullanilabilir=None)
        self.assertEqual([(o["stok"].kod, o["ihtiyac"], o["calistirma_sayisi"], o["uretilecek_miktar"], o["fazla_miktar"]) for o in a["ozet"]],
                         [(o["stok"].kod, o["ihtiyac"], o["calistirma_sayisi"], o["uretilecek_miktar"], o["fazla_miktar"]) for o in b["ozet"]])
        self.assertEqual((a["net_mod"], a["ayrilan"]), (False, {}))
        self.assertTrue(all(p["ayrilan"] == 0 and p["net"] == p["ihtiyac"] for p in a["plan"]))


class EkranTest(NetBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("ih_net", password="x"))
        self.url = reverse("core:ihtiyac_hesapla")
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("3"), giris_tutar_try=D("30"))
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul2.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=date(2026, 10, 1))
        sa.ayirma_ayarla(emir, self.kesilmis, "2")                                         # kullanılabilir 3 − 2 = 1

    def govde(self, **ek):
        v = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-hedef": self.mamul.pk, "satir-0-miktar": "5"}
        v.update(ek)
        return v

    def test_brut_varsayilan_ve_stok_dus(self):
        r = self.client.post(self.url, self.govde())
        h = r.content.decode()
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Net İhtiyaç / Eksik", h)
        self.assertNotIn("net — stok düşüldü", h)
        self.assertFalse(r.context["stok_dus"])
        self.assertEqual({o["stok"].kod: o["ihtiyac"] for o in r.context["sonuc"]["ozet"]}["PROFIL"], D("1"))
        r = self.client.post(self.url, self.govde(stok_dus="1"))
        h = r.content.decode()
        for parca in ("Net İhtiyaç / Eksik", "net — stok düşüldü", 'name="stok_dus" value="1" checked', "stoktan 1 · net 4", "eksik 1"):
            self.assertIn(parca, h, parca)
        o = {x["stok"].kod: x for x in r.context["sonuc"]["ozet"]}
        self.assertEqual((o["KESILMIS"]["ayrilan"], o["KESILMIS"]["net"], o["PROFIL"]["eksik"]), (D("1"), D("4"), D("1")))
        r = self.client.get(self.url)
        self.assertContains(r, 'name="stok_dus"')
        self.assertNotContains(r, 'value="1" checked')
