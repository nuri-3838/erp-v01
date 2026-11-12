"""ÜRÜN AĞACI — Maliyet sekmesinde İŞLEM / FASON BEDELİ grubu (Ürün/Maliyet, Excel, Karşılaştır): ağaçtaki her üretilen çıktı (PARÇALA'da tüm
çıktılar, ürünün kendisi dahil) için birim ihtiyaç adedi × fasoncunun seçilen tarihte geçerli FasonFiyat'ı; döviz fiyat TCMB kuruyla TL; fasoncu ve
tarih seçimi; fiyatsız üretilen parça sayısı; Malzeme / İşlem-fason / GENEL toplamları. Malzeme hesabı değişmez.
ÇOKLU FASONCU (2026-11-12): fasoncu seçimi YOK — her parçanın fasoncusu, onu üreten operasyon tanımının istasyonundaki ``IsIstasyonu.fasoncu``dur; fasoncusu olmayan
istasyon = kendi üretimimiz (fiyat aranmaz); özet ve satırlar fasoncu bazında ayrı. Fason Hesapla seçilen fasoncunun istasyonlarıyla sınırlanır.
Fixture canlı 152-22-0003 (4+4) zincirinin kopyası: malzeme 784,15 TL; Gürbüz fason 151-10-0017 ×2 ×16,06 + 151-10-0018 ×2 ×16,06 +
151-20-0014 ×2 ×5,93 + 151-20-0015 ×2 ×5,93 = 87,96 TL → genel 872,11 TL."""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from core.models import Cari, FasonFiyat, IsIstasyonu, Kur, Operasyon, Stok
from core.services import fason as fs
from core.services import urun_agaci as ua
from core.services.uretim import operasyon_olustur
from core.services.urun_agaci_xlsx import karsilastir_xlsx, maliyet_xlsx
from core.tests.test_uretim_tam_boy import TamBoyBase

D = Decimal
BUGUN = date(2026, 10, 9)


class FasonMaliyetBase(TamBoyBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lazer = IsIstasyonu.objects.create(kod="10", ad="BORU LAZER", fason=True)
        s = lambda kod, birim=None, **kw: Stok.objects.create(kod=kod, ad=kod.lower(), kategori=cls.kat, uretim_birimi=birim or cls.adet, fatura_birimi=birim or cls.adet,
                                                              uretim_urunu=kw.pop("uretim", True), satinalma_urunu=kw.pop("satinalma", False), satis_urunu=kw.pop("satis", False))
        cls.p_mentese = s("150-20-0002", cls.boy, uretim=False, satinalma=True)
        cls.p_on = s("150-10-0007", cls.boy, uretim=False, satinalma=True)
        cls.p_arka = s("150-10-0008", cls.boy, uretim=False, satinalma=True)
        Stok.objects.filter(pk=cls.p_mentese.pk).update(ort_maliyet_try=D("453.12"), ort_maliyet_usd=D("11.328"))      # 0,03125 BOY → 14,16 TL
        for p in (cls.p_on, cls.p_arka):
            Stok.objects.filter(pk=p.pk).update(ort_maliyet_try=D("384.995"), ort_maliyet_usd=D("9.624875"))          # 1 BOY → 384,995 TL
        cls.on, cls.arka = s("151-10-0017"), s("151-10-0018")
        cls.sag, cls.sol = s("151-20-0014"), s("151-20-0015")
        cls.urun = s("152-22-0003", satis=True)
        cls.a41 = s("152-10-0003", satis=True)
        operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.on.pk, cikti_miktar=D("2"), satirlar=[(cls.p_on, D("1"))])
        operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.arka.pk, cikti_miktar=D("2"), satirlar=[(cls.p_arka, D("1"))])
        operasyon_olustur(istasyon_id=cls.lazer.pk, satirlar=[(cls.p_mentese, D("1"))], tur=Operasyon.Tur.PARCALA, pay_anahtari=Operasyon.PayAnahtari.ESIT,
                          tam_boy=False, ciktilar=[(cls.sag, D("64"), None, None), (cls.sol, D("64"), None, None)])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.urun.pk, cikti_miktar=D("1"),
                          satirlar=[(cls.on, D("2")), (cls.arka, D("2")), (cls.sag, D("2")), (cls.sol, D("2"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.a41.pk, cikti_miktar=D("1"), satirlar=[(cls.on, D("2"))])     # A41: yalnız ön ayak
        cls.gurbuz = Cari.objects.create(kod="320-10-0001", unvan="GÜRBÜZ FASON", muhasebe_kodu="320.10.0001")
        cls.salim = Cari.objects.create(kod="320-10-0002", unvan="SALİM FASON", muhasebe_kodu="320.10.0002")
        IsIstasyonu.objects.filter(pk=cls.lazer.pk).update(fasoncu=cls.gurbuz)                                   # fasoncu: istasyondan
        cls.boya = IsIstasyonu.objects.create(kod="55", ad="TOZ BOYA", fason=True, fasoncu=cls.salim)
        cls.sag_b = s("151-20-0016")                                                                           # boyalı SAĞ menteşe (SALİM boyar)
        operasyon_olustur(istasyon_id=cls.boya.pk, cikti_id=cls.sag_b.pk, cikti_miktar=D("1"), satirlar=[(cls.sag, D("1"))])
        cls.urun2 = s("152-22-0004", satis=True)                                                               # ön ayak (GÜRBÜZ) + boyalı menteşe (SALİM) + kendi montajımız
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.urun2.pk, cikti_miktar=D("1"), satirlar=[(cls.on, D("2")), (cls.sag_b, D("2"))])
        for st, f in ((cls.on, "16.06"), (cls.arka, "16.06"), (cls.sag, "5.93"), (cls.sol, "5.93")):
            fs.fiyat_olustur(cari_id=cls.gurbuz.pk, stok_id=st.pk, birim_fiyat=D(f), gecerlilik_baslangic=date(2026, 1, 1), fasoncu_kodu="GZ-" + st.kod[-4:])
        fs.fiyat_olustur(cari_id=cls.salim.pk, stok_id=cls.sag_b.pk, birim_fiyat=D("0.2"), para_birimi="USD", gecerlilik_baslangic=date(2026, 1, 1))
        Kur.objects.create(tarih=BUGUN, usd_alis=D("40"), eur_alis=D("44"))

    def graf(self):
        return ua.graf_yukle(BUGUN)


class ServisTest(FasonMaliyetBase):
    def test_canli_rakamlar_fason_grubu_ve_genel_toplam(self):
        m = ua.urun_maliyet(self.graf(), self.urun, D("1"))
        self.assertEqual(m["toplam_try"], D("784.15"))                                                       # malzeme aynen
        f = m["fason"]
        self.assertEqual(sorted((s["stok"].kod, s["tuketim"], s["birim_try"], s["tutar_try"], s["fasoncu_kodu"]) for s in f["satirlar"]),
                         [("151-10-0017", D("2"), D("16.06"), D("32.12"), "GZ-0017"), ("151-10-0018", D("2"), D("16.06"), D("32.12"), "GZ-0018"),
                          ("151-20-0014", D("2"), D("5.93"), D("11.86"), "GZ-0014"), ("151-20-0015", D("2"), D("5.93"), D("11.86"), "GZ-0015")])
        self.assertEqual((f["toplam_try"], m["genel_try"]), (D("87.96"), D("872.11")))
        self.assertEqual((f["fiyatli"], f["fiyatsiz"], f["kendi"], f["uretilen"]), (4, 0, 1, 5))             # 152-22-0003 (montaj) fasoncusuz istasyon = kendi üretimimiz
        self.assertEqual([(g["cari"].pk, g["fiyatli"], g["fiyatsiz"]) for g in f["fasoncular"]], [(self.gurbuz.pk, 4, 0)])
        self.assertEqual({s["fasoncu"] for s in f["satirlar"]}, {self.gurbuz})
        self.assertEqual(f["toplam_usd"], D("2.199"))                                                        # 87,96 / 40
        self.assertEqual((m["genel_usd"], m["toplam_usd"]), (m["toplam_usd"] + f["toplam_usd"], D("19.60375")))
        self.assertEqual((f["tarih"], f["kursuz"], f["usd_eksik"]), (BUGUN, 0, 0))
        self.assertEqual(f["kur_notu"], "Fason fiyatı çevrimi: TCMB 09.10.2026, USD 40,0000")               # TRY fiyat: USD sütunu için kur
        m3 = ua.urun_maliyet(self.graf(), self.urun, D("3"))
        self.assertEqual((m3["fason"]["toplam_try_n"], m3["genel_try_n"], m3["fason"]["satirlar"][0]["tutar_try_n"]), (D("263.88"), D("2616.33"), D("96.36")))

    def test_parcala_ciktilari_ayri_satir_ve_urunun_kendisi(self):
        m = ua.urun_maliyet(self.graf(), self.sag, D("1"))                                                   # ara parça: kendisi 1 adet
        self.assertEqual([(s["stok"].kod, s["tuketim"]) for s in m["fason"]["satirlar"]], [("151-20-0014", D("1"))])
        self.assertEqual((m["fason"]["toplam_try"], m["toplam_try"], m["genel_try"]), (D("5.93"), D("3.54"), D("9.47")))   # 1/64 × ½ × 453,12
        m = ua.urun_maliyet(self.graf(), self.urun, D("1"))
        self.assertEqual({s["stok"].kod for s in m["fason"]["satirlar"]} & {"151-20-0014", "151-20-0015"}, {"151-20-0014", "151-20-0015"})

    def test_iki_fasoncu_istasyondan_ayri_ayri_ve_doviz_fiyat(self):
        """urun2: ön ayak (lazer → GÜRBÜZ) + boyalı SAĞ menteşe (toz boya → SALİM; ham SAĞ kesimi GÜRBÜZ) + kendi montajımız: fiyatlar fasoncuya göre ayrı aranır."""
        m = ua.urun_maliyet(self.graf(), self.urun2, D("1"))
        f = m["fason"]
        self.assertEqual([(g["cari"], g["fiyatli"], g["fiyatsiz"], g["toplam_try"]) for g in f["gruplar"]],
                         [(self.gurbuz, 2, 0, D("43.98")), (self.salim, 1, 0, D("16"))])                    # ön ayak 2×16,06 + ham SAĞ 2×5,93 | boyalı 2×0,2 USD×40
        self.assertEqual({s["stok"].kod: s["fasoncu"] for s in f["satirlar"]},
                         {"151-10-0017": self.gurbuz, "151-20-0014": self.gurbuz, "151-20-0016": self.salim})
        boyali = next(s for s in f["satirlar"] if s["stok"].kod == "151-20-0016")
        self.assertEqual((boyali["pb"], boyali["fiyat"], boyali["birim_try"], boyali["birim_usd"], boyali["tutar_try"], boyali["tutar_usd"]),
                         ("USD", D("0.2"), D("8"), D("0.2"), D("16"), D("0.4")))
        self.assertEqual((f["toplam_try"], f["fiyatli"], f["fiyatsiz"], f["kendi"], f["uretilen"]), (D("59.98"), 3, 0, 1, 4))
        self.assertEqual(f["kur_notu"], "Fason fiyatı çevrimi: TCMB 09.10.2026, USD 40,0000")
        self.assertEqual([(g["cari"].pk, g["fiyatli"], g["fiyatsiz"]) for g in ua.fason_bedeli(self.graf(), [self.urun2])["fasoncular"]],
                         [(self.gurbuz.pk, 2, 0), (self.salim.pk, 1, 0)])
        # eski fasoncu_id parametresi yok sayılır (eski bağlantılar bozulmasın): sonuç aynı
        eski = ua.urun_maliyet(self.graf(), self.urun2, D("1"), fasoncu_id=self.salim.pk)["fason"]
        self.assertEqual((eski["toplam_try"], [g["cari"] for g in eski["gruplar"]]), (D("59.98"), [self.gurbuz, self.salim]))

    def test_fasoncusuz_istasyon_fiyat_aranmaz_fasoncu_fiyati_eksikse_ayri_sayilir(self):
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fasoncu=None)                                    # lazer fason ama fasoncusu tanımsız → kendi üretimimiz sayılır
        f = ua.urun_maliyet(self.graf(), self.urun2, D("1"))["fason"]
        self.assertEqual([(s["stok"].kod, s["fasoncu"]) for s in f["satirlar"]], [("151-20-0016", self.salim)])
        self.assertEqual((f["fiyatli"], f["fiyatsiz"], f["kendi"], f["uretilen"]), (1, 0, 3, 4))            # ön ayak, ham SAĞ, montaj = kendi
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fasoncu=self.gurbuz)
        FasonFiyat.objects.filter(cari=self.gurbuz, stok=self.on).delete()                                    # fasoncusu var, fiyat yok → "fiyatı eksik"
        f = ua.urun_maliyet(self.graf(), self.urun2, D("1"))["fason"]
        self.assertEqual((f["fiyatli"], f["fiyatsiz"], f["kendi"]), (2, 1, 1))
        self.assertEqual([(g["cari"], g["fiyatli"], g["fiyatsiz"]) for g in f["fasoncular"]], [(self.gurbuz, 1, 1), (self.salim, 1, 0)])

    def test_tarih_secimi(self):
        fs.fiyat_olustur(cari_id=self.gurbuz.pk, stok_id=self.sag.pk, birim_fiyat=D("7"), gecerlilik_baslangic=date(2026, 7, 1))
        Kur.objects.create(tarih=date(2026, 6, 30), usd_alis=D("38"))
        g = self.graf()
        self.assertEqual(ua.urun_maliyet(g, self.urun, D("1"))["fason"]["toplam_try"], D("90.10"))            # bugün: 7,00 geçerli
        eski = ua.urun_maliyet(g, self.urun, D("1"), tarih=date(2026, 6, 30))
        self.assertEqual((eski["fason"]["toplam_try"], eski["fason"]["tarih"]), (D("87.96"), date(2026, 6, 30)))   # başlangıcı geçmeyen en son
        on = next(s for s in eski["fason"]["satirlar"] if s["stok"].kod == "151-10-0017")
        self.assertEqual((on["birim_usd"], eski["fason"]["kur_notu"]), (D("0.422632"), "Fason fiyatı çevrimi: TCMB 30.06.2026, USD 38,0000"))   # 16,06 / 38, 6 ondalık
        self.assertEqual(eski["fason"]["toplam_usd"], sum(s["tutar_usd"] for s in eski["fason"]["satirlar"]))
        hic = ua.urun_maliyet(g, self.urun, D("1"), tarih=date(2025, 12, 31))
        self.assertEqual((hic["fason"]["satirlar"], [(g["cari"], g["fiyatli"], g["fiyatsiz"]) for g in hic["fason"]["fasoncular"]], hic["fason"]["fiyatsiz"], hic["fason"]["kendi"]),
                         ([], [(self.gurbuz, 0, 4)], 4, 1))                                                  # fasoncusu var ama o tarihte fiyat yok → fiyatsız; montaj kendi
        self.assertEqual(hic["genel_try"], hic["toplam_try"])

    def test_kur_yoksa_doviz_fiyat_toplama_girmez(self):
        m = ua.urun_maliyet(self.graf(), self.urun2, D("1"), tarih=date(2026, 9, 1))                              # 7 gün içinde kur yok
        f = m["fason"]
        boyali = next(s for s in f["satirlar"] if s["stok"].kod == "151-20-0016")
        self.assertTrue(boyali["maliyet_yok"])
        self.assertEqual((f["toplam_try"], f["kursuz"], f["fiyatli"]), (D("0"), 3, 3))                        # TCMB kuru yok: hiçbir fason fiyatı çevrilemez, toplama girmez
        self.assertIn("151-20-0016: fason fiyatı (USD) çevrilemedi — USD için TCMB kuru bulunamadı", f["kur_uyarilari"])
        self.assertEqual(m["genel_try"], m["toplam_try"])

    def test_pasif_silinmis_fiyat_sayilmaz(self):
        f = FasonFiyat.objects.get(cari=self.salim, stok=self.sag_b)
        f.aktif = False
        f.save()
        r = ua.urun_maliyet(self.graf(), self.urun2, D("1"))["fason"]
        self.assertEqual([(g["cari"].pk, g["fiyatli"], g["fiyatsiz"]) for g in r["fasoncular"]], [(self.gurbuz.pk, 2, 0), (self.salim.pk, 0, 1)])
        f.aktif = True
        f.save()
        fs.fiyat_sil(f)
        r = ua.urun_maliyet(self.graf(), self.urun2, D("1"))["fason"]
        self.assertEqual([(g["cari"].pk, g["fiyatli"], g["fiyatsiz"]) for g in r["fasoncular"]], [(self.gurbuz.pk, 2, 0), (self.salim.pk, 0, 1)])

    def test_malzeme_hesabi_degismedi(self):
        m = ua.urun_maliyet(self.graf(), self.urun, D("2"))
        satir = {s["stok"].kod: s for gr in m["gruplar"] for s in gr["satirlar"]}
        self.assertEqual(set(satir), {"150-20-0002", "150-10-0007", "150-10-0008"})                        # fason satırları malzeme gruplarında değil
        self.assertEqual(satir["150-20-0002"]["tuketim"], D("0.03125"))
        self.assertEqual((m["miktar_toplam_try"], m["satir_sayisi"], m["maliyetsiz"]), (D("1568.30"), 3, 0))
        self.assertEqual(sum(s["pay"] for s in satir.values()).quantize(D("0.000001")), D("100"))         # pay malzeme toplamına göre
        self.assertEqual(sum(gr["toplam_try"] for gr in m["gruplar"]), m["toplam_try"])

    def test_karsilastir_genel_toplam(self):
        g = self.graf()
        k = ua.karsilastir(g, [self.urun, self.a41], "maliyet", "TL")
        self.assertEqual([s["stok"].kod for s in k["fason"]["satirlar"]], ["151-10-0017", "151-10-0018", "151-20-0014", "151-20-0015"])
        t0, t1 = k["toplamlar"]
        self.assertEqual((t0["toplam"], t0["fason"], t0["genel"], t0["fiyatsiz"], t0["kendi"]), (D("784.15"), D("87.96"), D("872.11"), 0, 1))
        self.assertEqual((t1["toplam"], t1["fason"], t1["genel"], t1["fiyatsiz"], t1["kendi"]), (D("384.995"), D("32.12"), D("417.115"), 0, 1))
        on = next(s for s in k["fason"]["satirlar"] if s["stok"].kod == "151-10-0017")
        self.assertEqual([(h["deger"], h["miktar"], h["pb"]) for h in on["hucreler"]], [(D("32.12"), D("2"), "TRY"), (D("32.12"), D("2"), "TRY")])
        sag = next(s for s in k["fason"]["satirlar"] if s["stok"].kod == "151-20-0014")
        self.assertIsNone(sag["hucreler"][1])                                                             # A41 menteşe kullanmıyor
        self.assertEqual((k["fason"]["tarih"], [s["cari"].pk for s in k["fason"]["fasoncular"]]), (BUGUN, [self.gurbuz.pk]))
        usd = ua.karsilastir(g, [self.urun], "maliyet", "USD")
        self.assertEqual(usd["toplamlar"][0]["fason"], D("2.199"))
        self.assertEqual(usd["toplamlar"][0]["genel"], usd["toplamlar"][0]["toplam"] + usd["toplamlar"][0]["fason"])
        self.assertIsNone(ua.karsilastir(g, [self.urun], "miktar")["fason"])
        ks = ua.karsilastir(g, [self.urun2], "maliyet", "TL", tarih=date(2026, 9, 1))                              # Ürün görünümüyle aynı kural
        self.assertEqual((ks["toplamlar"][0]["fason"], ks["toplamlar"][0]["fason_eksik"]), (D("0"), 3))
        boyali = next(s for s in ks["fason"]["satirlar"] if s["stok"].kod == "151-20-0016")
        self.assertTrue(boyali["hucreler"][0]["maliyet_yok"])
        iki = ua.karsilastir(g, [self.urun, self.urun2], "maliyet", "TL")                                       # iki fasoncu aynı matriste, fasoncu bazında gruplu
        self.assertEqual([(s["fasoncu"].unvan, s["stok"].kod) for s in iki["fason"]["satirlar"]],
                         [("GÜRBÜZ FASON", "151-10-0017"), ("GÜRBÜZ FASON", "151-10-0018"), ("GÜRBÜZ FASON", "151-20-0014"), ("GÜRBÜZ FASON", "151-20-0015"),
                          ("SALİM FASON", "151-20-0016")])
        self.assertEqual([(t["fason"], t["kendi"]) for t in iki["toplamlar"]], [(D("87.96"), 1), (D("59.98"), 1)])

    def test_excel_maliyet_ve_karsilastir(self):
        g = self.graf()
        ws = load_workbook(BytesIO(maliyet_xlsx(self.urun, D("1"), ua.urun_maliyet(g, self.urun, D("1"))))).active
        satirlar = list(ws.iter_rows(min_row=5, values_only=True))
        etiketler = [str(r[0]) for r in satirlar]
        self.assertIn("MALZEME TOPLAMI", etiketler)
        self.assertTrue(any(e.startswith("İŞLEM / FASON BEDELİ · 09.10.2026") for e in etiketler))
        self.assertIn("Fasoncu: GÜRBÜZ FASON", etiketler)
        kod = {r[1]: r for r in satirlar if r[1]}
        self.assertEqual((kod["151-20-0014"][0], kod["151-20-0014"][4], D(str(kod["151-20-0014"][5])), D(str(kod["151-20-0014"][8]))),
                         ("İşlem / fason bedeli", "Fason fiyatı (TRY) · GZ-0014", D("2"), D("11.86")))
        fason = next(r for r in satirlar if str(r[0]).startswith("İŞLEM/FASON TOPLAMI · fasoncusu olup fiyatı olmayan parça: 0 · kendi üretimimiz: 1"))
        genel = next(r for r in satirlar if str(r[0]).startswith("GENEL TOPLAM"))
        malzeme = next(r for r in satirlar if r[0] == "MALZEME TOPLAMI")
        self.assertEqual((D(str(malzeme[8])), D(str(fason[8])), D(str(genel[8]))), (D("784.15"), D("87.96"), D("872.11")))
        self.assertIn("GÜRBÜZ FASON: 4 parça fason fiyatlı, fasoncusu olup fiyatı olmayan parça: 0, kendi üretimimiz: 1", ws["A2"].value)
        ws2 = load_workbook(BytesIO(maliyet_xlsx(self.urun2, D("1"), ua.urun_maliyet(g, self.urun2, D("1"))))).active           # iki fasoncu: ayrı başlıklar
        et2 = [str(r[0]) for r in ws2.iter_rows(min_row=5, values_only=True)]
        self.assertTrue("Fasoncu: GÜRBÜZ FASON" in et2 and "Fasoncu: SALİM FASON" in et2)
        self.assertIn("GÜRBÜZ FASON: 2 parça fason fiyatlı, SALİM FASON: 1 parça fason fiyatlı", ws2["A2"].value)
        wb = load_workbook(BytesIO(karsilastir_xlsx([ua.karsilastir(g, [self.urun, self.a41], "maliyet", pb) for pb in ("TL", "USD")])))
        satirlar = list(wb["Maliyet TL"].iter_rows(min_row=5, values_only=True))
        genel = next(r for r in satirlar if str(r[0]).startswith("GENEL TOPLAM (TL)"))
        self.assertEqual((D(str(genel[5])), D(str(genel[6]))), (D("872.11"), D("417.115")))
        fason = next(r for r in satirlar if str(r[0]).startswith("İŞLEM/FASON TOPLAMI (TL)"))
        self.assertEqual((D(str(fason[5])), D(str(fason[6]))), (D("87.96"), D("32.12")))
        self.assertTrue(any(r[1] == "151-20-0015" and r[4] == "Fason fiyatı" and r[6] is None for r in satirlar))      # A41 hücresi boş
        self.assertFalse(any(str(r[0]).startswith("fasoncusu olup fason fiyatı olmayan parça sayısı") for r in satirlar))      # fiyatı eksik fasoncu parçası yok
        self.assertIn("GENEL TOPLAM (USD)", [str(r[0]) for r in wb["Maliyet USD"].iter_rows(min_row=5, values_only=True)])


class EkranTest(FasonMaliyetBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("uaf_y", password="x"))
        Kur.objects.get_or_create(tarih=timezone.localdate(), defaults={"usd_alis": D("40")})
        self.url = reverse("core:urun_agaci")

    def test_maliyet_sekmesi_fason_grubu(self):
        r = self.client.get(self.url, {"urun": self.urun.pk, "sekme": "maliyet", "tarih": "2026-10-09"})
        h = r.content.decode()
        for parca in ("İşlem / fason bedeli · 09.10.2026", "Fasoncu: GÜRBÜZ FASON", "Malzeme toplamı", "İşlem/fason toplamı", "GENEL TOPLAM", "87,96", "872,11", "784,15",
                      "kendi üretimimiz (fiyat aranmadı): <strong>1</strong>", 'name="tarih"', 'value="2026-10-09"', "GZ-0014", "kaynak-fason",
                      "<strong>4</strong> parça fason fiyatlı (GÜRBÜZ FASON)"):
            self.assertIn(parca, h)
        self.assertNotIn('name="fasoncu"', h)                                                                 # fasoncu seçici kalktı
        r = self.client.get(self.url, {"urun": self.urun2.pk, "sekme": "maliyet", "tarih": "2026-10-09"})       # iki fasoncu aynı sayfada
        h = r.content.decode()
        for parca in ("Fasoncu: GÜRBÜZ FASON", "Fasoncu: SALİM FASON", "<strong>2</strong> parça fason fiyatlı (GÜRBÜZ FASON)", "<strong>1</strong> parça fason fiyatlı (SALİM FASON)",
                      "Fason fiyatı (USD)", "59,98", "Fason fiyatı çevrimi: TCMB 09.10.2026, USD 40,0000", "GÜRBÜZ FASON: 43,98 TL", "SALİM FASON: 16,00 TL"):
            self.assertIn(parca, h)
        r = self.client.get(self.url, {"urun": self.urun2.pk, "sekme": "maliyet", "tarih": "2026-10-09", "fasoncu": self.gurbuz.pk})      # eski parametre yok sayılır
        self.assertEqual((r.status_code, r.context["maliyet"]["fason"]["toplam_try"]), (200, D("59.98")))
        r = self.client.get(self.url, {"urun": self.urun.pk, "sekme": "maliyet", "tarih": "2025-12-31"})
        self.assertContains(r, "fasoncusu olup fason fiyatı olmayan parça: <strong>4</strong>")
        self.assertContains(r, "5 üretilen parça için 31.12.2025 tarihinde geçerli fason fiyatı yok")
        self.assertNotContains(r, 'name="fasoncu"')
        r = self.client.get(self.url, {"urun": self.urun.pk, "sekme": "maliyet", "tarih": "bozuk"})         # geçersiz tarih → bugün
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["fason_tarih"], timezone.localdate().isoformat())
        r = self.client.get(self.url, {"urun": self.urun.pk, "sekme": "maliyet", "tarih": "2026-10-09", "miktar": "3", "xlsx": "1"})
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertIn("kendi üretimimiz: 1", load_workbook(BytesIO(r.content)).active["A2"].value)

    def test_karsilastir_ekrani(self):
        r = self.client.get(self.url, {"gorunum": "karsilastir", "mod": "maliyet", "urunler": [self.urun.pk, self.a41.pk], "tarih": "2026-10-09"})
        h = r.content.decode()
        for parca in ("MALZEME TOPLAMI (TL)", "İŞLEM/FASON TOPLAMI (TL)", "GENEL TOPLAM (TL)", "872,11", "417,12", "fasoncusu olup fiyatsız parça: 0 · kendi üretimimiz: 1",
                      "İşlem / fason bedeli · 09.10.2026", "Fasoncu: GÜRBÜZ FASON"):
            self.assertIn(parca, h)
        self.assertNotIn('name="fasoncu"', h)
        r = self.client.get(self.url, {"gorunum": "karsilastir", "mod": "maliyet", "urunler": [self.urun.pk, self.urun2.pk], "tarih": "2026-10-09", "pb": "USD"})
        self.assertContains(r, "GENEL TOPLAM (USD)")
        self.assertContains(r, "Fasoncu: SALİM FASON")
        self.assertEqual(r.context["sonuc"]["toplamlar"][1]["fason"], D("1.49950"))                          # 59,98 / 40
        r = self.client.get(self.url, {"gorunum": "karsilastir", "mod": "miktar", "urunler": [self.urun.pk]})
        self.assertNotContains(r, "GENEL TOPLAM")
        self.assertNotContains(r, 'name="tarih"')


class FasonHesaplaVeMutabakatTest(FasonMaliyetBase):
    """Fason Hesapla seçilen fasoncunun istasyonlarıyla sınırlanır; Mutabakat fason deposu olan her fasoncuyu (Kaygun gibi 2. fasoncu dahil) kapsar."""

    def liste(self, cari):
        return fs.fason_listesi_operasyondan([(self.urun2, D("4"))], cari=cari)

    def test_fason_hesapla_secilen_fasoncunun_istasyonlariyla_sinirli(self):
        tum = {o["kesilmis_parca"].kod for o in self.liste(None)["ozet"]}
        self.assertEqual(tum, {"151-10-0017", "151-20-0014", "151-20-0015", "151-20-0016"})                 # cari yok: tüm fason istasyonlar (eski davranış)
        g = self.liste(self.gurbuz)
        self.assertEqual({o["kesilmis_parca"].kod for o in g["ozet"]}, {"151-10-0017", "151-20-0014", "151-20-0015"})   # toz boya (SALİM) listede yok
        self.assertEqual(g["fiyat_eksikleri"], [])
        sl = self.liste(self.salim)
        self.assertEqual([o["kesilmis_parca"].kod for o in sl["ozet"]], ["151-20-0016"])
        self.assertEqual((sl["toplam_tutar"], sl["fiyat_eksikleri"]), ({"USD": D("1.6")}, []))               # 8 boyalı SAĞ × 0,2 USD; Gürbüz'ün lazer işleri fiyatsız satır olarak çıkmaz

    def test_fason_hesapla_ekrani(self):
        self.client.force_login(User.objects.create_superuser("fh_y", password="x"))
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
                "satir-0-urun": self.urun2.pk, "satir-0-miktar": "4", "cari": self.salim.pk}
        r = self.client.post(reverse("core:fason_hesapla"), veri)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([c.pk for c in r.context["cariler"]], [self.gurbuz.pk, self.salim.pk])             # Kaygun benzeri 2. fasoncu seçilebilir
        self.assertEqual([o["kesilmis_parca"].kod for o in r.context["sonuc"]["ozet"]], ["151-20-0016"])

    def test_mutabakat_iki_fasoncuyu_da_kapsar(self):
        from core.services import depo as depo_servis
        from core.services import fason_mutabakat as fm
        depo_servis.depo_olustur(kod="FG", ad="FASON-GÜRBÜZ", fason_cari=self.gurbuz)
        depo_servis.depo_olustur(kod="FS", ad="FASON-SALİM", fason_cari=self.salim)
        self.assertEqual([c.pk for c in fm.fasoncular()], [self.gurbuz.pk, self.salim.pk])
        self.client.force_login(User.objects.create_superuser("fm_y", password="x"))
        for c in (self.gurbuz, self.salim):
            r = self.client.get(reverse("core:fason_mutabakat"), {"cari": c.pk})
            self.assertEqual((r.status_code, r.context["cari"]), (200, c))
