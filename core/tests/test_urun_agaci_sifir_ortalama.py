"""ÜRÜN AĞACI maliyeti — yalnız MALİYETSİZ girişlerle (irsaliye fatura bekliyor / manuel açılış) oluşan 0 ortalama "ortalama" sayılmaz:
kart alış fiyatı (döviz çevrimi dahil) → o da yoksa "maliyet yok"; fiyatlı + maliyetsiz karışık → mevcut ortalama; hiç giriş yoksa eski kural.
Kaynak notu "stokta maliyetsiz giriş var" (ekran rozeti, Excel kaynak sütunu, Karşılaştır), maliyetsiz sayacı; stok kartında "maliyet henüz belli
değil" aynı kuralla. Ortalama motoru / değerleme raporu DEĞİŞMEZ (ort 0, değer 0 kalır)."""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from core.models import Kur, Stok, StokHareket
from core.services import stok_ortalama
from core.services import urun_agaci as ua
from core.services.hareket import hareket_ekle
from core.services.uretim import operasyon_olustur
from core.services.urun_agaci_xlsx import karsilastir_xlsx, maliyet_xlsx
from core.tests.test_urun_agaci_gorunum import GorunumBase

D = Decimal
BUGUN = date(2026, 10, 10)
NOT = "stokta maliyetsiz giriş var"


class SifirOrtalamaBase(GorunumBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Kur.objects.get_or_create(tarih=BUGUN, defaults={"usd_alis": D("40")})
        cls.tapa = cls._stok("151-40-0007", cls.kat_hird, satinalma=True, uretim=False)                  # Q16 tapa: irsaliye, alış 0,50
        cls.mamul_t = cls._stok("152-10-0007", cls.kat_ana)
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul_t.pk, cikti_miktar=D("1"), satirlar=[(cls.tapa, D("4")), (cls.civata, D("2"))])
        Stok.objects.filter(pk=cls.civata.pk).update(alis_fiyati=D("2"), alis_fiyati_pb="TRY")
        Stok.objects.filter(pk=cls.tapa.pk).update(alis_fiyati=D("0.50"), alis_fiyati_pb="TRY")

    def setUp(self):
        self.giris(self.profil, 2, 200, 5)                                                              # fiyatlı → ortalama 100
        self.fiyatsiz(self.profil, 1)                                                                   # karışık: ortalama 100 kalır
        self.fiyatsiz(self.civata, 25)                                                                  # yalnız maliyetsiz (manuel açılış) → ort 0
        self.fiyatsiz(self.tapa, 40)                                                                    # yalnız maliyetsiz → ort 0
        self.fiyatsiz(self.boya, 5)                                                                     # maliyetsiz + alış fiyatı da yok

    def fiyatsiz(self, stok, miktar):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D(str(miktar)))

    def graf(self):
        return ua.graf_yukle(BUGUN)


class ServisTest(SifirOrtalamaBase):
    def test_birim_maliyet_kurali(self):
        g = self.graf()
        self.assertTrue({self.profil.pk, self.civata.pk, self.tapa.pk, self.boya.pk} <= g.fiyatsiz_girisli)
        profil, civata, boya = (Stok.objects.get(pk=s.pk) for s in (self.profil, self.civata, self.boya))
        self.assertEqual((civata.ort_maliyet_try, civata.maliyet_deger_try, profil.ort_maliyet_try), (D("0"), D("0"), D("100")))  # motor aynen
        b = ua.birim_maliyet(civata, g.kurlar, g.fiyatsiz_girisli)
        self.assertEqual((b["kaynak"], b["try"], b["usd"], b["ortalama_notu"]), ("KART", D("2"), D("0.05"), NOT))               # alış fiyatı
        b = ua.birim_maliyet(profil, g.kurlar, g.fiyatsiz_girisli)
        self.assertEqual((b["kaynak"], b["try"], b["ortalama_notu"]), ("ORTALAMA", D("100"), ""))                                 # karışık: ortalama
        b = ua.birim_maliyet(boya, g.kurlar, g.fiyatsiz_girisli)
        self.assertEqual((b["kaynak"], b["try"], b["ortalama_notu"]), ("YOK", None, NOT))                                          # alış fiyatı da yok
        # ortalama 0 ama fiyatsız giriş YOK (bedelsiz fiyatlı giriş) → ortalama 0 geçerli; hiç girişi olmayan kart → eski kural (kart, notsuz)
        bedelsiz = self._stok("BEDELSIZ", self.kat_hird, satinalma=True, uretim=False)
        Stok.objects.filter(pk=bedelsiz.pk).update(ort_maliyet_try=D("0"), ort_maliyet_usd=D("0"), alis_fiyati=D("9"), alis_fiyati_pb="TRY")
        bedelsiz.refresh_from_db()
        self.assertEqual(ua.birim_maliyet(bedelsiz, g.kurlar, stok_ortalama.fiyatsiz_girisli_stoklar([bedelsiz.pk]))["kaynak"], "ORTALAMA")
        bos = self._stok("BOS", self.kat_hird, satinalma=True, uretim=False)
        Stok.objects.filter(pk=bos.pk).update(alis_fiyati=D("3"), alis_fiyati_pb="TRY")
        bos.refresh_from_db()
        self.assertEqual((ua.birim_maliyet(bos, g.kurlar, g.fiyatsiz_girisli)["kaynak"], ua.birim_maliyet(bos, g.kurlar, g.fiyatsiz_girisli)["ortalama_notu"]), ("KART", ""))
        # depo transferi bacağı fiyatsız giriş sayılmaz
        self.assertEqual(stok_ortalama.fiyatsiz_girisli_stoklar([]), set())

    def test_urun_maliyet_karsilastir_ve_sayaclar(self):
        g = self.graf()
        m = ua.urun_maliyet(g, self.mamul_a, D("1"))
        satir = {s["stok"].kod: s for gr in m["gruplar"] for s in gr["satirlar"]}
        self.assertEqual((satir["PROFIL"]["kaynak"], satir["PROFIL"]["tutar_try"], satir["PROFIL"]["ortalama_notu"]), ("ORTALAMA", D("50"), ""))
        self.assertEqual((satir["CIVATA"]["kaynak"], satir["CIVATA"]["ort_try"], satir["CIVATA"]["tutar_try"], satir["CIVATA"]["ortalama_notu"]), ("KART", D("2"), D("8"), NOT))
        self.assertEqual((satir["BOYA"]["kaynak"], satir["BOYA"]["maliyet_yok"], satir["BOYA"]["ortalama_notu"]), ("YOK", True, NOT))
        self.assertEqual((m["toplam_try"], m["maliyetsiz"], m["kart_sayi"], m["kart_notlu"], m["ortalama_sayi"]), (D("58"), 1, 1, 2, 1))
        t = ua.urun_maliyet(g, self.mamul_t, D("1"))
        satir = {s["stok"].kod: s for gr in t["gruplar"] for s in gr["satirlar"]}
        self.assertEqual((satir["151-40-0007"]["tutar_try"], satir["CIVATA"]["tutar_try"], t["toplam_try"], t["maliyetsiz"], t["kart_sayi"], t["kart_notlu"]),
                         (D("2.00"), D("4"), D("6.00"), 0, 2, 2))                                                                   # tapa 4 × 0,50
        k = ua.karsilastir(g, [self.mamul_a, self.mamul_t], "maliyet", "TL")
        rows = {s["stok"].kod: s for gr in k["gruplar"] for s in gr["satirlar"]}
        self.assertEqual((rows["CIVATA"]["kaynak"], rows["CIVATA"]["ortalama_notu"], rows["CIVATA"]["hucreler"][0]["deger"], rows["CIVATA"]["hucreler"][1]["deger"]),
                         ("KART", NOT, D("8"), D("4")))
        self.assertEqual((rows["PROFIL"]["ortalama_notu"], rows["BOYA"]["hucreler"][0]["maliyet_yok"]), ("", True))
        self.assertEqual([(x["toplam"], x["maliyetsiz"]) for x in k["toplamlar"]], [(D("58"), 1), (D("6.00"), 0)])

    def test_degerleme_ve_motor_degismedi(self):
        rapor = stok_ortalama.degerleme_raporu()
        satir = {r["stok"].kod: r for r in rapor["satirlar"]}
        self.assertEqual((satir["CIVATA"]["miktar"], satir["CIVATA"]["ort_try"], satir["CIVATA"]["deger_try"]), (D("25"), D("0"), D("0")))   # gösterim kuralı değerlemeye girmez
        self.assertEqual((satir["PROFIL"]["ort_try"], satir["PROFIL"]["deger_try"]), (D("100"), D("300")))
        h = StokHareket.objects.get(stok=self.civata, silindi=False)
        self.assertEqual((h.maliyet_durumu, h.tutar_try, h.birim_maliyet_try), (StokHareket.MaliyetDurumu.YOK, D("0"), D("0")))

    def test_excel_kaynak_notu(self):
        g = self.graf()
        ws = load_workbook(BytesIO(maliyet_xlsx(self.mamul_a, D("1"), ua.urun_maliyet(g, self.mamul_a, D("1"))))).active
        satir = {row[1]: row for row in ws.iter_rows(min_row=5, values_only=True) if row[1]}
        self.assertEqual((satir["CIVATA"][4], satir["BOYA"][4], satir["PROFIL"][4]), (f"Alış fiyatı ({NOT})", f"Yok ({NOT})", "Ortalama"))
        self.assertEqual(D(str(satir["CIVATA"][8])), D("8"))
        wb = load_workbook(BytesIO(karsilastir_xlsx([ua.karsilastir(g, [self.mamul_a], "maliyet", "TL")])))
        satir = {row[1]: row for row in wb.active.iter_rows(min_row=5, values_only=True) if row[1]}
        self.assertEqual((satir["CIVATA"][4], D(str(satir["CIVATA"][5]))), (f"Alış fiyatı ({NOT})", D("8")))


class EkranTest(SifirOrtalamaBase):
    def setUp(self):
        super().setUp()
        Kur.objects.get_or_create(tarih=timezone.localdate(), defaults={"usd_alis": D("40")})
        self.client.force_login(User.objects.create_superuser("ua_so", password="x"))

    def test_maliyet_sekmesi_rozet_ve_sayac(self):
        h = self.client.get(reverse("core:urun_agaci"), {"urun": self.mamul_a.pk, "sekme": "maliyet"}).content.decode()
        for parca in (f"Alış fiyatı ({NOT})", f"Yok ({NOT})", "<strong>1</strong> kalem maliyetsiz", "<strong>1</strong> kalem alış fiyatı",
                      f"2 kalemde {NOT} (ortalama 0 kullanılmadı)", "58,00 TL"):
            self.assertIn(parca, h, parca)
        self.assertNotIn("Alış fiyatı (kart)", h)                                                       # tek kart kalemi (CIVATA) notlu
        h = self.client.get(reverse("core:urun_agaci"), {"urun": self.mamul_t.pk, "sekme": "maliyet"}).content.decode()
        for parca in ("<strong>0</strong> kalem maliyetsiz", "6,00 TL", "2,00", "0,5000 TL"):
            self.assertIn(parca, h, parca)
        self.assertNotIn("Alış fiyatı (kart)", h)                                                       # ikisi de notlu
        h = self.client.get(reverse("core:urun_agaci"), {"gorunum": "karsilastir", "mod": "maliyet", "urunler": [self.mamul_t.pk]}).content.decode()
        self.assertIn(f"({NOT})", h)
        self.assertIn("6,00", h)

    def test_stok_karti_maliyet_belirsiz(self):
        r = self.client.get(reverse("core:stok_detay", args=[self.civata.pk]))
        self.assertContains(r, "Maliyet henüz belli değil (eldeki girişler fatura tutarıyla fiyatlanmamış")
        self.assertContains(r, "ortalama yerine kart alış fiyatı kullanılır")
        self.assertContains(r, "2,0000")
        self.assertNotContains(r, "0,0000</span> <span class=\"birim\">TL")
        r = self.client.get(reverse("core:stok_detay", args=[self.boya.pk]))
        self.assertContains(r, "alış fiyatı da tanımlı değil")
        r = self.client.get(reverse("core:stok_detay", args=[self.profil.pk]))
        self.assertContains(r, "100,0000")
        self.assertNotContains(r, "Maliyet henüz belli değil")
        bos = self._stok("BOS2", self.kat_hird, satinalma=True, uretim=False)
        r = self.client.get(reverse("core:stok_detay", args=[bos.pk]))
        self.assertContains(r, "Maliyet henüz belli değil (fatura tutarıyla fiyatlanmış giriş yok)")
