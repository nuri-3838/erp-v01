"""GEÇMİŞ MENTEŞE TÜKETİMİNİ BOYALI MENTEŞEYE ÇEVİRME (core.services.mentese_boyali + mentese_boyali_gecis komutu): kuru çalıştırma hiçbir şey bırakmaz;
uygulamada baslangıç ve sonrası montaj girdileri yerinde ham → boyalı olur (öncesi dokunulmaz), her TARİH için tek fason dönüş (aynı gün iki montaj tek belge),
ham stok toplamı sabit / boyalı 0 / fason deposu 0, faturasız fasoncuya Σ adet × fiyat tahakkuku, montaj çıktısının maliyeti tahakkuk kadar artar, kartlar sıfırdan
hesapla eşit; fiyat yoksa hata ve hiçbir şey değişmez; idempotent."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from core.models import Cari, FasonDonus, HesapPlani, IsIstasyonu, Kur, OperasyonKaydi, OperasyonKaydiGirdi, Stok, StokHareket, YevmiyeFisi
from core.services import fason as fs
from core.services import mentese_boyali as mb
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_olustur
from core.tests.test_fason_donus import FasonBase

D = Decimal
ONCE, G1, G2, G3 = date(2026, 9, 20), date(2026, 9, 25), date(2026, 10, 9), date(2026, 10, 15)


class MenteseBase(FasonBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        HesapPlani.objects.create(hesap_kodu="320.10.0001", hesap_adi="SALİM FASON", rapor_grubu="BILANCO", rapor_kalemi="KVYK", parasal=True, aktif=True)
        Cari.objects.filter(pk=cls.salim.pk).update(fason_faturasiz=True)
        for g in (ONCE, G1, G2, G3):
            Kur.objects.get_or_create(tarih=g, defaults={"usd_alis": D("40")})
        cls.boya_ist = IsIstasyonu.objects.create(kod="55", ad="TOZ BOYA", fason=True, fasoncu=cls.salim)
        cls.sag, cls.sol = cls._stok("151-20-0014", cls.kat_yan), cls._stok("151-20-0015", cls.kat_yan)
        cls.sag_b, cls.sol_b = cls._stok("151-20-0016", cls.kat_yan), cls._stok("151-20-0017", cls.kat_yan)
        cls.mamul = cls._stok("MAMUL", cls.kat_ana)
        cls.op_sag = operasyon_olustur(istasyon_id=cls.boya_ist.pk, cikti_id=cls.sag_b.pk, cikti_miktar=D("1"), satirlar=[(cls.sag, D("1"))])
        cls.op_sol = operasyon_olustur(istasyon_id=cls.boya_ist.pk, cikti_id=cls.sol_b.pk, cikti_miktar=D("1"), satirlar=[(cls.sol, D("1"))])
        cls.montaj_op = operasyon_olustur(istasyon_id=cls.kesim.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"), satirlar=[(cls.sag, D("2")), (cls.sol, D("2"))])

    def setUp(self):
        for st in (self.sag, self.sol):
            hareket_ekle(stok_id=st.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("100"),
                         giris_tutar_try=D("1000"), giris_tutar_usd=D("25"))
        for st in (self.sag_b, self.sol_b):
            fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=st.pk, birim_fiyat=D("1.5"), gecerlilik_baslangic=G1)
        for tarih, hedef in ((ONCE, "5"), (G1, "10"), (G2, "3"), (G2, "2"), (G3, "5")):          # girdi: hedef × 2 SAĞ + hedef × 2 SOL
            k = operasyon_kaydi_olustur(operasyon_id=self.montaj_op.pk, depo_id=self.depo.pk, tarih=tarih, hedef_cikti_miktari=D(hedef))
            operasyon_kaydi_onayla(k)

    def girdi_kodlari(self):
        return sorted((s.kayit.tarih, s.girdi.kod) for s in OperasyonKaydiGirdi.objects.filter(silindi=False, kayit__operasyon=self.montaj_op).select_related("kayit", "girdi"))

    def mamul_degeri(self):
        self.mamul.refresh_from_db()
        return self.mamul.maliyet_deger_try


class KuruCalistirmaTest(MenteseBase):
    def test_kuru_calistirma_hicbir_sey_birakmaz_ama_gercek_rapor_verir(self):
        once = (StokHareket.objects.count(), FasonDonus.objects.count(), YevmiyeFisi.objects.count(), self.girdi_kodlari(), self.mamul_degeri())
        r = mb.gecis(baslangic=G1)
        self.assertFalse(r["uygulandi"])
        self.assertEqual(r["uyarilar"], [])
        self.assertEqual([(x["tarih"], x["donus"].no[:3]) for x in r["tarihler"]], [(G1, "FD-"), (G2, "FD-"), (G3, "FD-")])
        self.assertEqual((StokHareket.objects.count(), FasonDonus.objects.count(), YevmiyeFisi.objects.count(), self.girdi_kodlari(), self.mamul_degeri()), once)
        self.assertEqual(eldeki_miktar(self.sag_b), D("0"))


class AyirmaUyarisiTest(MenteseBase):
    def test_ayirmadan_dusen_varsa_tek_uyari_satiri(self):
        OperasyonKaydiGirdi.objects.filter(kayit__operasyon=self.montaj_op, kayit__tarih=G1).update(ayrilan_dusen=D("4"))
        r = mb.gecis(baslangic=G1)
        self.assertEqual(len(r["uyarilar"]), 1)
        self.assertIn("1 montaj kaydının 2 girdi satırında", r["uyarilar"][0])
        self.assertIn("toplam 8", r["uyarilar"][0])


class UygulamaTest(MenteseBase):
    def test_uygula_yerinde_cevirir_baslangic_oncesine_dokunmaz(self):
        mamul_once = self.mamul_degeri()
        r = mb.gecis(baslangic=G1, uygula=True)
        self.assertTrue(r["uygulandi"])
        kodlar = self.girdi_kodlari()
        self.assertEqual([k for t, k in kodlar if t == ONCE], ["151-20-0014", "151-20-0015"])                      # öncesi ham kalır
        self.assertTrue(all(k in ("151-20-0016", "151-20-0017") for t, k in kodlar if t >= G1))
        self.assertEqual(len([1 for t, _k in kodlar if t >= G1]), 8)                                                # 4 kayıt × (SAĞ + SOL)
        self.assertEqual(sorted(r["tarihler"][i]["tarih"] for i in range(3)), [G1, G2, G3])                          # aynı gün iki montaj tek FD
        self.assertEqual(FasonDonus.objects.filter(silindi=False).count(), 3)
        # tarih başına FD: satırlar SAĞ + SOL, gelen = tüketilen adet (G1: 10×2=20, G2: (3+2)×2=10, G3: 10)
        adetler = {x["tarih"]: {k: v for k, v in x["adetler"].items()} for x in r["tarihler"]}
        self.assertEqual(adetler[G1], {"151-20-0014": D("20"), "151-20-0015": D("20")})
        self.assertEqual(adetler[G2], {"151-20-0014": D("10"), "151-20-0015": D("10")})
        for x in r["tarihler"]:
            d = x["donus"]
            self.assertEqual(d.tarih, x["tarih"])
            self.assertTrue(all(k.durum == OperasyonKaydi.Durum.ONAYLI for k in d.kayitlar.filter(silindi=False)))
            self.assertEqual(sorted(k.hedef_cikti_miktari for k in d.kayitlar.filter(silindi=False)), [adetler[d.tarih]["151-20-0014"]] * 2)
            self.assertIsNotNone(d.tahakkuk_fis_id)
            self.assertEqual(d.tahakkuk_fis.tarih, d.tarih)
        # stok: ham toplam sabit, boyalı 0, fason deposu 0
        for ham, boyali in (("sag", "sag_b"), ("sol", "sol_b")):
            self.assertEqual(eldeki_miktar(getattr(self, ham)), D("50"))                                            # 100 − 10 (öncesi) − 40
            self.assertEqual(eldeki_miktar(getattr(self, ham), self.depo), D("50"))
            self.assertEqual(eldeki_miktar(getattr(self, ham), self.fdepo), D("0"))
            self.assertEqual(eldeki_miktar(getattr(self, boyali)), D("0"))
        # tahakkuk Σ adet × fiyat = (40 + 40) × 1,5 = 120; cari alacağı ve montaj çıktısının maliyeti aynı kadar artar
        self.assertEqual(r["tahakkuk_toplam"], D("120.00"))
        self.assertEqual(r["sonra"]["cari"] - r["once"]["cari"], D("120.00"))
        self.assertEqual(self.mamul_degeri() - mamul_once, D("120.00"))
        self.assertEqual(r["sonra"]["deger"] - r["once"]["deger"], D("120.00"))
        self.assertTrue(all(tamam for _ad, tamam, _a in r["kontrol"]))
        self.assertEqual(r["degisen_cikis"], 0)
        # montaj kaydı fişleri aynı kayıt/fiş no'su: kayıt sayısı değişmedi
        self.assertEqual(OperasyonKaydi.objects.filter(operasyon=self.montaj_op, silindi=False).count(), 5)

    def test_idempotent_ikinci_calistirma_bir_sey_yapmaz(self):
        mb.gecis(baslangic=G1, uygula=True)
        sayilar = (StokHareket.objects.count(), FasonDonus.objects.count(), YevmiyeFisi.objects.count())
        r = mb.gecis(baslangic=G1, uygula=True)
        self.assertEqual(r["tarihler"], [])
        self.assertIn("Çevrilecek kayıt yok", r["uyarilar"][0])
        self.assertEqual((StokHareket.objects.count(), FasonDonus.objects.count(), YevmiyeFisi.objects.count()), sayilar)

    def test_fiyat_yoksa_hata_ve_hicbir_sey_degismez(self):
        from core.models import FasonFiyat
        FasonFiyat.objects.filter(stok=self.sol_b).update(silindi=True)
        once = (StokHareket.objects.count(), FasonDonus.objects.count(), self.girdi_kodlari())
        with self.assertRaises(mb.MenteseGecisHatasi) as c:
            mb.gecis(baslangic=G1, uygula=True)
        self.assertIn("fason fiyatı yok", str(c.exception))
        self.assertEqual((StokHareket.objects.count(), FasonDonus.objects.count(), self.girdi_kodlari()), once)

    def test_fiyat_baslangici_gecmis_tarihten_sonraysa_hata(self):
        from core.models import FasonFiyat
        FasonFiyat.objects.filter(stok=self.sag_b).update(gecerlilik_baslangic=date(2026, 11, 1))
        with self.assertRaises(mb.MenteseGecisHatasi):
            mb.gecis(baslangic=G1, uygula=True)
        self.assertEqual(FasonDonus.objects.count(), 0)


class KalanHamZatenBoyatilmisTest(MenteseBase):
    """CANLI durum (12.11.2026): montaj geçmişte ham menteşeyi tüketti; kalan ham bugün fason dönüşle boyatılıp boyalı stoğa alındı (boyalı eldeki > 0).
    Çevirme sonrası ham ve boyalı eldeki DEĞİŞMEZ (boyalı: bugünkü giriş + geçmiş FD girişleri − montaj tüketimi)."""

    def setUp(self):
        super().setUp()
        from core.services import fason_donus as fd
        from core.services.depo_transfer import depo_transferi_yap
        self.bugun = date(2026, 11, 12)
        Kur.objects.get_or_create(tarih=self.bugun, defaults={"usd_alis": D("40")})
        for ham in (self.sag, self.sol):
            depo_transferi_yap(stok_id=ham.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.fdepo.pk, tarih=self.bugun, miktar=D("50"))
        fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=self.bugun, satirlar=[(self.op_sag.pk, D("50")), (self.op_sol.pk, D("50"))], onayla=True)

    def test_boyali_eldeki_ve_ham_eldeki_degismez_tahakkuk_yalniz_gecmis_icin(self):
        self.assertEqual((eldeki_miktar(self.sag), eldeki_miktar(self.sag_b)), (D("0"), D("50")))
        mamul_once = self.mamul_degeri()
        r = mb.gecis(baslangic=G1, uygula=True)
        self.assertEqual((eldeki_miktar(self.sag), eldeki_miktar(self.sol), eldeki_miktar(self.sag_b), eldeki_miktar(self.sol_b)), (D("0"), D("0"), D("50"), D("50")))
        self.assertEqual(eldeki_miktar(self.sag_b, self.depo), D("50"))
        self.assertEqual(FasonDonus.objects.filter(silindi=False).count(), 4)                                       # bugünkü + 3 geçmiş tarih
        self.assertEqual(r["tahakkuk_toplam"], D("120.00"))                                                        # yalnız geçmiş 80 × 1,5; bugünkü FD'ye dokunulmaz
        self.assertEqual(r["sonra"]["cari"] - r["once"]["cari"], D("120.00"))
        self.assertEqual(self.mamul_degeri() - mamul_once, D("120.00"))
        self.assertTrue(all(tamam for _ad, tamam, _a in r["kontrol"]))
        self.assertEqual(r["degisen_cikis"], 0)
        self.assertEqual(mb.gecis(baslangic=G1, uygula=True)["tarihler"], [])                                       # idempotent


class KomutTest(MenteseBase):
    def test_komut_varsayilan_kuru_uygula_kalici(self):
        cikti = StringIO()
        call_command("mentese_boyali_gecis", "--baslangic", "25.09.2026", stdout=cikti)
        self.assertIn("KURU ÇALIŞTIRMA", cikti.getvalue())
        self.assertIn("[OK]", cikti.getvalue())
        self.assertNotIn("[HATA]", cikti.getvalue())
        self.assertEqual(FasonDonus.objects.count(), 0)
        cikti = StringIO()
        call_command("mentese_boyali_gecis", "--baslangic", "25.09.2026", "--uygula", stdout=cikti)
        self.assertIn("UYGULANDI", cikti.getvalue())
        self.assertIn("FD-2026-", cikti.getvalue())
        self.assertEqual(FasonDonus.objects.filter(silindi=False).count(), 3)
