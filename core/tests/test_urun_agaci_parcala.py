"""Operasyon PARÇALA × görünümler: ürün ağacı (ağaç/malzeme/maliyet/nerede kullanılıyor/karşılaştır), İhtiyaç Hesapla ekranı, fason kesim listesi (ekran +
PDF) ve Excel — PARÇALA'nın her çıktısı tanıma bağlı, diğer çıktılar "aynı boydan" notuyla, fazla mahsup bilgisiyle görünür."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Cari, Operasyon, Stok
from core.services import fason as fs
from core.services import urun_agaci as ua
from core.services.fason import fason_listesi_operasyondan
from core.services.uretim import ihtiyac_hesapla, operasyon_olustur
from core.services.urun_agaci_xlsx import agac_xlsx
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
BUGUN = date(2026, 10, 9)


class GorunumBase(TamBoyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from core.models import IsIstasyonu
        cls.lazer = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER")
        s = lambda kod, birim=None, **kw: Stok.objects.create(kod=kod, ad=kod.lower(), kategori=cls.kat, uretim_birimi=birim or cls.adet, fatura_birimi=birim or cls.adet,
                                                              uretim_urunu=kw.pop("uretim", True), satinalma_urunu=kw.pop("satinalma", False), satis_urunu=kw.pop("satis", False))
        cls.profil = s("150-20-0002", cls.boy, uretim=False, satinalma=True)
        Stok.objects.filter(pk=cls.profil.pk).update(ort_maliyet_try=D("640"), ort_maliyet_usd=D("16"))
        cls.sag, cls.sol = s("151-20-0014"), s("151-20-0015")
        cls.u1, cls.u2 = s("152-22-0001", satis=True), s("152-22-0002", satis=True)
        cls.pop = operasyon_olustur(istasyon_id=cls.lazer.pk, satirlar=[(cls.profil, D("1"))], tur=Operasyon.Tur.PARCALA, pay_anahtari=Operasyon.PayAnahtari.ESIT,
                                    tam_boy=False, ciktilar=[(cls.sag, D("64"), None, None), (cls.sol, D("64"), None, None)])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.u1.pk, cikti_miktar=D("1"), satirlar=[(cls.sag, D("2")), (cls.sol, D("2"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.u2.pk, cikti_miktar=D("1"), satirlar=[(cls.sol, D("4"))])
        cls.salim = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")
        fs.fiyat_olustur(cari_id=cls.salim.pk, stok_id=cls.sag.pk, birim_fiyat=D("1"), gecerlilik_baslangic=date(2026, 1, 1), fasoncu_kodu="GZ-SAG")
        fs.fiyat_olustur(cari_id=cls.salim.pk, stok_id=cls.sol.pk, birim_fiyat=D("2"), gecerlilik_baslangic=date(2026, 1, 1), fasoncu_kodu="GZ-SOL")


class ServisTest(GorunumBase):
    def test_graf_birim_tuketim_ve_malzeme_maliyet(self):
        graf = ua.graf_yukle(BUGUN)
        self.assertEqual({k.kod for k in graf.kokler()}, {"152-22-0001", "152-22-0002"})
        t = graf.birim_tuketim(self.u1)
        self.assertEqual(t[self.profil.pk]["ihtiyac"].quantize(D("0.000001")), D("0.031250"))        # (2/64)×½ + (2/64)×½ = 1/32 boy
        self.assertEqual(graf.birim_tuketim(self.u2)[self.profil.pk]["ihtiyac"].quantize(D("0.000001")), D("0.031250"))   # (4/64)×½
        m = ua.urun_malzeme(graf, self.u2, D("16"))                                                     # 64 SOL → 1 boy (tam boy kapalı: 64/64)
        self.assertEqual(m["gruplar"][0]["satirlar"][0]["gerekli"], D("1"))
        c = ua.urun_maliyet(graf, self.u1, D("1"))
        self.assertEqual(c["toplam_try"].quantize(D("0.01")), D("20.00"))                                 # 1/32 × 640
        k = ua.karsilastir(graf, [self.u1, self.u2], "miktar")
        self.assertEqual([h["miktar"].quantize(D("0.000001")) for h in k["gruplar"][0]["satirlar"][0]["hucreler"]], [D("0.031250"), D("0.031250")])

    def test_nerede_kullaniliyor_ve_secenekler(self):
        graf = ua.graf_yukle(BUGUN)
        n = ua.nerede_kullaniliyor(graf, self.profil)
        satirlar = {r["urun"].kod: r for r in n["gruplar"][0]["satirlar"]}
        self.assertEqual(set(satirlar), {"152-22-0001", "152-22-0002"})
        self.assertEqual([s.kod for s in satirlar["152-22-0002"]["yol"]], ["150-20-0002", "151-20-0015", "152-22-0002"])   # SOL üzerinden yol (referans değil)
        n2 = ua.nerede_kullaniliyor(graf, self.sol)
        self.assertEqual({r["urun"].kod for r in n2["gruplar"][0]["satirlar"]}, {"152-22-0001", "152-22-0002"})
        self.assertEqual(n2["yan_cikti_ureten"], [])                                                       # PARÇALA çıktısı yan değil
        gruplar = dict(ua.kullanim_secenekleri(graf))
        self.assertIn((self.sol.pk, f"{self.sol.kod}  {self.sol.ad}"), gruplar["Ara parçalar"])          # SOL da ara parça

    def test_fason_kesim_listesi(self):
        s = fason_listesi_operasyondan([(self.u1, 10), (self.u2, 10)], cari=self.salim, tarih=BUGUN)      # SAĞ 20, SOL 20+40=60 → 60/64 boy
        rows = {(r["kesilmis_parca"].kod, r["sira_no"]): r for r in s["ozet"]}
        sag, sol = rows[("151-20-0014", 0)], rows[("151-20-0015", 1)]
        self.assertEqual((sag["profil"].kod, sag["boy"].quantize(D("0.000001")), sag["toplam_adet"].quantize(D("0.000001")), sag["ihtiyac"], sag["fazla"].quantize(D("0.000001")), sag["yan"]),
                         ("150-20-0002", D("0.9375"), D("60"), D("20"), D("40"), False))
        self.assertEqual((sol["boy"], sol["toplam_adet"], sol["fazla"], sol["tur"], sol["fasoncu_kodu"]), (None, D("60"), D("0"), "PARCALA", "GZ-SOL"))
        self.assertEqual(s["toplam_tutar"], {"TRY": D("60") + D("120")})                                   # 60×1 + 60×2

    def test_xlsx_notu(self):
        sonuc = ihtiyac_hesapla([(self.u2, D("16"))])
        icerik = agac_xlsx(sonuc["agac"][0], D("16"))
        self.assertGreater(len(icerik), 1000)


class EkranTest(GorunumBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("uap_y", password="x"))

    def test_ihtiyac_hesapla_ekrani_parcala_notu(self):
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-hedef": self.u2.pk, "satir-0-miktar": "16"}
        r = self.client.post(reverse("core:ihtiyac_hesapla"), veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "parçala")
        self.assertContains(r, "aynı boydan")
        self.assertContains(r, "151-20-0014")                                                              # SAĞ: ihtiyaç 0, fazla 64 → notta

    def test_urun_agaci_ve_fason_hesapla_ekranlari(self):
        r = self.client.get(reverse("core:urun_agaci"), {"urun": self.u2.pk, "miktar": "16"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "parçala")
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
                "satir-0-urun": self.u2.pk, "satir-0-miktar": "16", "eylem": "hesapla", "cari": self.salim.pk}
        r = self.client.post(reverse("core:fason_hesapla"), veri)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "aynı boydan")
        self.assertContains(r, "GZ-SAG")
        r = self.client.post(reverse("core:fason_hesapla"), {**veri, "eylem": "pdf"})
        self.assertEqual(r["Content-Type"], "application/pdf")
