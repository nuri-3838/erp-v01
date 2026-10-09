"""Operasyon Tanımları LİSTE ekranı: arama (çıktı/girdi/yan çıktı), istasyon sekmeleri (sayılar filtrelerden sonra), seri (A/C/ortak/bağlantısız) hesabı,
rozet filtreleri ve birleşimi, 'kullanıldığı yer', gruplama, özet şeridi; sorgu sayısı sabit."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.services import uretim as u
from core.services.uretim import operasyon_liste, operasyon_olustur, operasyon_serileri
from core.tests.test_uretim import _istasyon
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal


class ListeBase(TamBoyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.birl = _istasyon("BIRL")
        cls.profil = cls._s("PROFIL", cls.boy, satinalma=True)
        cls.adet_parca = cls._s("ADETPARCA", cls.adet, satinalma=True)
        k = {}
        for kod in ("151-10-0026", "151-80-0001", "152-10-0001", "151-10-0021", "151-10-0019", "151-80-0011", "152-22-0001",
                    "151-10-0045", "151-10-0099"):
            k[kod] = cls._s(kod)
        cls.k = k
        cls.ops = {
            "a1": cls._op(cls.kesim, k["151-10-0026"], 16, [(cls.profil, 1)]),
            "asm_a": cls._op(cls.birl, k["151-80-0001"], 1, [(k["151-10-0026"], 1)]),
            "ortak": cls._op(cls.kesim, k["151-10-0045"], 15, [(cls.profil, 1)]),
            "c1": operasyon_olustur(istasyon_id=cls.kesim.pk, cikti_id=k["151-10-0021"].pk, cikti_miktar=D("3"),
                                    satirlar=[(cls.profil, D("1"))], boy_mm=D("1292.60"),
                                    yan_ciktilar=[(k["151-10-0019"], D("1"), D("1063.53"))]),
            "yan1": cls._op(cls.kesim, k["151-10-0019"], 4, [(cls.profil, 1)]),             # kendi operasyonu da var; yan çıktı olarak da üretiliyor
            "asm_c": cls._op(cls.birl, k["151-80-0011"], 1, [(k["151-10-0021"], 1)]),
            "root_a": cls._op(cls.montaj, k["152-10-0001"], 1, [(k["151-80-0001"], 1), (k["151-10-0045"], 1)]),
            "root_c": cls._op(cls.montaj, k["152-22-0001"], 1, [(k["151-80-0011"], 1), (k["151-10-0045"], 2)]),
            "yetim": cls._op(cls.kesim, k["151-10-0099"], 5, [(cls.profil, 1)]),             # hiçbir yerde kullanılmıyor
        }

    @classmethod
    def _s(cls, kod, birim=None, **kw):
        from core.tests.test_uretim import _stok
        return _stok(cls.kat, birim or cls.adet, kod=kod, ad=f"ad {kod}".lower(), satinalma=kw.get("satinalma", False))

    @classmethod
    def _op(cls, istasyon, cikti, miktar, girdiler):
        return operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=cikti.pk, cikti_miktar=D(str(miktar)),
                                 satirlar=[(g, D(str(m))) for g, m in girdiler])

    def kodlar(self, sonuc):
        return {r["op"].cikti.kod for r in sonuc["satirlar"]}


class SeriHesabiTest(ListeBase):
    def test_seriler(self):
        sr = operasyon_serileri()
        pk = lambda n: self.ops[n].pk
        self.assertEqual(sr[pk("a1")], "A")                                    # 151-10-0026 1. basamak → A
        self.assertEqual(sr[pk("asm_a")], "A")
        self.assertEqual(sr[pk("root_a")], "A")                                # bitmiş ürünün kendisi
        self.assertEqual(sr[pk("c1")], "C")                                    # C ayak
        self.assertEqual(sr[pk("asm_c")], "C")
        self.assertEqual(sr[pk("root_c")], "C")
        self.assertEqual(sr[pk("ortak")], "ORTAK")                             # hem A hem C ürününde kullanılıyor
        self.assertEqual(sr[pk("yetim")], "BAGLANTISIZ")                       # hiçbir köke ulaşmıyor

    def test_yan_cikti_olarak_uretilen_parca_ureticinin_serisini_alir(self):
        """151-10-0019'un kendi operasyonu hiçbir yerde girdi değil; ama op(151-10-0021)'in yan çıktısı → onun zinciri (C) üzerinden bağlı."""
        self.assertEqual(operasyon_serileri()[self.ops["yan1"].pk], "C")

    def test_dongu_sonsuz_donguye_girmez(self):
        # A çıktısı B'den, B çıktısı A'dan (döngü): hesap sonlanır, köke ulaşmaz → bağlantısız
        a, b = self._s("DONGU-A"), self._s("DONGU-B")
        oa = self._op(self.kesim, a, 1, [(self.profil, 1), (b, 1)])
        ob = self._op(self.birl, b, 1, [(a, 1)])
        sr = operasyon_serileri()
        self.assertEqual((sr[oa.pk], sr[ob.pk]), ("BAGLANTISIZ", "BAGLANTISIZ"))

    def test_tek_yukleme_sorgu_sayisi_sabit(self):
        with CaptureQueriesContext(connection) as az:
            operasyon_liste()
        for i in range(6):                                                      # operasyon sayısını artır
            c = self._s(f"EKSTRA-{i}")
            self._op(self.kesim, c, 2, [(self.profil, 1), (self.adet_parca, 1)])
        with CaptureQueriesContext(connection) as cok:
            operasyon_liste()
        self.assertEqual(len(az), len(cok))
        self.assertLessEqual(len(cok), 6)


class FiltreTest(ListeBase):
    def test_arama_cikti_girdi_ve_yan_cikti_kodu(self):
        self.assertEqual(self.kodlar(operasyon_liste(ara="0026")), {"151-10-0026", "151-80-0001"})   # çıktı kodu + girdi kodu
        self.assertIn("151-10-0021", self.kodlar(operasyon_liste(ara="0019")))     # yan çıktı kodu
        self.assertIn("151-10-0019", self.kodlar(operasyon_liste(ara="0019")))     # çıktı kodu
        girdi = self.kodlar(operasyon_liste(ara="adetparca"))                     # girdi adı (TR büyük harf kuralı; stok adı küçük harfle kayıtlı)
        self.assertEqual(girdi, set())                                            # ADETPARCA hiçbir operasyonda girdi değil
        profil = self.kodlar(operasyon_liste(ara="profil"))
        self.assertEqual(profil, {"151-10-0026", "151-10-0045", "151-10-0021", "151-10-0019", "151-10-0099"})   # PROFİL girdi adı → kesimler
        self.assertEqual(self.kodlar(operasyon_liste(ara="yok boyle bir sey")), set())

    def test_istasyon_sekme_sayilari_diger_filtrelerden_sonra(self):
        tum = operasyon_liste()
        self.assertEqual({x["kod"]: x["sayi"] for x in tum["sekmeler"]}, {"KESIM": 5, "BIRL": 2, "MONTAJ": 2})
        c = operasyon_liste(seri="C")
        self.assertEqual({x["kod"]: x["sayi"] for x in c["sekmeler"]}, {"KESIM": 2, "BIRL": 1, "MONTAJ": 1})
        self.assertEqual(c["tum_sayi"], 4)
        sec = operasyon_liste(seri="C", istasyon=self.kesim.pk)
        self.assertEqual({x["kod"]: x["sayi"] for x in sec["sekmeler"]}, {"KESIM": 2, "BIRL": 1, "MONTAJ": 1})    # istasyon seçimi sayıları değiştirmez
        self.assertEqual(self.kodlar(sec), {"151-10-0021", "151-10-0019"})
        self.assertEqual(sec["gruplar"], [])                                                                      # tek istasyon seçiliyken grup yok

    def test_gruplama_tumunde_istasyona_gore(self):
        g = operasyon_liste()["gruplar"]
        self.assertEqual([(x["kod"], x["sayi"], len(x["satirlar"])) for x in g], [("BIRL", 2, 2), ("KESIM", 5, 5), ("MONTAJ", 2, 2)])

    def test_seri_filtresi(self):
        self.assertEqual(self.kodlar(operasyon_liste(seri="A")), {"151-10-0026", "151-80-0001", "152-10-0001"})
        self.assertEqual(self.kodlar(operasyon_liste(seri="ORTAK")), {"151-10-0045"})
        self.assertEqual(self.kodlar(operasyon_liste(seri="BAGLANTISIZ")), {"151-10-0099"})

    def test_rozet_filtreleri_ve_birlesim(self):
        self.assertEqual(self.kodlar(operasyon_liste(tam_boy=True)), {"151-10-0026", "151-10-0045", "151-10-0021", "151-10-0019", "151-10-0099"})
        self.assertEqual(self.kodlar(operasyon_liste(yan_cikti=True)), {"151-10-0021"})
        self.assertEqual(self.kodlar(operasyon_liste(baglantisiz=True)), {"151-10-0099"})
        self.assertEqual(self.kodlar(operasyon_liste(seri="C", yan_cikti=True)), {"151-10-0021"})                  # birleşim
        self.assertEqual(self.kodlar(operasyon_liste(seri="A", yan_cikti=True)), set())
        self.assertEqual(self.kodlar(operasyon_liste(seri="C", tam_boy=True, ara="0019")), {"151-10-0021", "151-10-0019"})

    def test_kullanildigi_yer_ve_kullanan_filtresi(self):
        satir = {r["op"].cikti.kod: r for r in operasyon_liste()["satirlar"]}
        self.assertEqual(satir["151-10-0045"]["kullanan_sayisi"], 2)                                               # iki bitmiş ürün operasyonu
        self.assertEqual(satir["151-10-0026"]["kullanan_sayisi"], 1)
        self.assertTrue(satir["152-10-0001"]["bitmis_urun"])
        self.assertEqual(satir["152-10-0001"]["kullanan_sayisi"], 0)
        self.assertEqual((satir["151-10-0099"]["kullanan_sayisi"], satir["151-10-0099"]["bitmis_urun"], satir["151-10-0099"]["baglantisiz"]),
                         (0, False, True))
        k = operasyon_liste(kullanan=self.ops["ortak"].pk)
        self.assertEqual(self.kodlar(k), {"152-10-0001", "152-22-0001"})
        self.assertEqual(k["kullanan_op"].pk, self.ops["ortak"].pk)

    def test_ozet(self):
        o = operasyon_liste()["ozet"]
        self.assertEqual(o, {"toplam": 9, "tam_boy": 5, "yan_cikti": 1, "baglantisiz": 1})
        self.assertEqual(operasyon_liste()["seri_dagilimi"], {"A": 3, "C": 4, "ORTAK": 1, "DIGER": 0, "BAGLANTISIZ": 1})

    def test_girdi_ozeti(self):
        satir = {r["op"].cikti.kod: r for r in operasyon_liste()["satirlar"]}
        self.assertEqual(satir["151-10-0026"]["ozet"]["ana"], "1 BOY PROFIL → 16 adet")
        self.assertEqual(satir["151-10-0021"]["ozet"]["yan"], ["+1 × 151-10-0019"])
        cok = satir["152-10-0001"]["ozet"]
        self.assertEqual((cok["ana"], cok["girdi_sayisi"]), ("151-80-0001, 151-10-0045", 2))


class EkranTest(ListeBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("ol_su", password="x"))
        self.url = reverse("core:operasyon_tanimlari")

    def test_tumu_gruplu_ozet_ve_ikonlar(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        h = r.content.decode()
        self.assertIn("KESIM KESIM İSTASYONU · 5 tanım", h)                       # grup başlığı "<kod> <ad> · N tanım"
        self.assertIn("<strong>9</strong> tanım", h)
        self.assertIn("<strong>1</strong> bağlantısız", h)
        self.assertIn("ol-chip kirmizi", h)                                         # bağlantısız > 0 → kırmızı
        self.assertIn('aria-label="Kopyala"', h)
        self.assertIn('aria-label="Sil"', h)
        self.assertIn("Bitmiş ürün", h)
        self.assertIn("⚠ bağlantısız", h)
        self.assertIn("A tipi", h)
        self.assertIn("Ortak", h)

    def test_istasyon_secili_grup_yok_ve_url_parametreleri_kalir(self):
        r = self.client.get(self.url, {"istasyon": self.kesim.pk, "seri": "C", "yan_cikti": "1"})
        h = r.content.decode()
        self.assertNotIn("data-grup-baslik", h)
        self.assertEqual({x["op"].cikti.kod for x in r.context["satirlar"]}, {"151-10-0021"})
        self.assertIn("seri=C", h)                                                   # sekme bağlantıları filtreyi taşır
        self.assertIn("yan_cikti=1", h)
        self.assertIn(">Temizle<", h)

    def test_bos_sonuc(self):
        r = self.client.get(self.url, {"ara": "olmayan-bir-kod"})
        self.assertContains(r, "Bu filtreyle eşleşen operasyon yok")

    def test_kullanan_filtresi_ekranda(self):
        r = self.client.get(self.url, {"kullanan": self.ops["ortak"].pk})
        self.assertContains(r, "çıktısını girdi olarak kullanan operasyonlar")
        self.assertEqual({x["op"].cikti.kod for x in r.context["satirlar"]}, {"152-10-0001", "152-22-0001"})

    def test_gecersiz_seri_yok_sayilir(self):
        r = self.client.get(self.url, {"seri": "XYZ"})
        self.assertEqual(len(r.context["satirlar"]), 9)
