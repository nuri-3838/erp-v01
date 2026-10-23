"""E-5: operasyon girdi/çıktı varyant kuralları (TAŞI/SABİT), DÖNÜŞTÜR tipi (aynı kart, sürücü değil → planlama görmez), form ekranı,
``varyant_operasyon_hazirla`` komutu (kuru/uygula): LAZER MARKALAMA istasyonu, kesim çıktısı SABİT YOK, 11 dönüştür tanımı, ÇÇ montaj ayak
bölme, yan etiketin montajdan paketlemeye taşınması; altın kontrol: standart plan yalnız yan etiket istasyonunda değişir, ÜS açılışında lazer
emri açılmaz."""
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.management.commands.varyant_veri_gocu import AYAK_YOK
from core.models import Birim, Depo, EkranYetki, IsIstasyonu, KdvOrani, Operasyon, OperasyonGirdi, Stok, UretimEmri, Varyant
from core.services import uretim
from core.services import varyant as vs
from core.services.kategori import kategori_olustur

D = Decimal
TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT


class OperasyonVaryantTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("ovyon", password="x")
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kdv = KdvOrani.objects.create(oran=D("20"), aciklama="G")
        cls.depo = Depo.objects.create(kod="D1", ad="DEPO")
        cls.kat = {}
        for ust_kod, altlar in (("150", ("10", "50")), ("151", ("10", "20", "80", "99")), ("152", ("10", "22"))):
            ust = kategori_olustur(ad=f"K{ust_kod}", kod=ust_kod)
            for a in altlar:
                cls.kat[f"{ust_kod}-{a}"] = kategori_olustur(ad=f"K{ust_kod}{a}", kod=a, ust_id=ust.pk)
        cls.semta, cls.yok, cls.mop = (Varyant.objects.get(kod=k) for k in ("SEMTA", "YOK", "MR. MOP"))
        cls.ist = {}
        for kod, ad, sira, fason in (("10", "BORU LAZER", 10, True), ("12", "MANUEL TESTERE", 20, False), ("20", "BÜKÜM", 40, False),
                                     ("50", "ÖN AYAK BİRLEŞTİRME", 80, False), ("70", "MONTAJ", 100, False), ("80", "PAKETLEME", 110, False)):
            cls.ist[kod] = IsIstasyonu.objects.create(kod=kod, ad=ad, sira=sira, fason=fason)

    @classmethod
    def kart(cls, kod, ad=None, boyutlar=None, varsayilan_yok=False):
        s = Stok.objects.create(kod=kod, ad=ad or kod, kategori=cls.kat[kod[:6]], uretim_birimi=cls.adet, fatura_birimi=cls.adet, kdv=cls.kdv,
                                uretim_urunu=True, varyantli=bool(boyutlar), varyant_boyutlari=list(boyutlar or []))
        if boyutlar:
            vs.standart_varyant(s)
            if varsayilan_yok:
                sv = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": cls.yok})
                Stok.objects.filter(pk=s.pk).update(varsayilan_varyant=sv)
                s.refresh_from_db()
        return s

    def op(self, ist, cikti, satirlar, **ek):
        return uretim.operasyon_olustur(istasyon_id=self.ist[ist].pk, cikti_id=cikti.pk, cikti_miktar=D("1"), satirlar=satirlar, tam_boy=False, **ek)


class KuralServisTest(OperasyonVaryantTaban):
    def test_girdi_ve_cikti_kurallari_kaydedilir_ve_donustur(self):
        profil = self.kart("150-10-0001")
        ayak = self.kart("151-10-0001", boyutlar=["MARKALAMA"], varsayilan_yok=True)
        kesim = self.op("10", ayak, [(profil, D("1"))], cikti_varyant_kurali=SABIT, cikti_sabit_varyant_id=self.yok.pk)
        self.assertEqual((kesim.cikti_varyant_kurali, kesim.cikti_sabit_varyant_id), (SABIT, self.yok.pk))
        with self.assertRaisesMessage(uretim.UretimHatasi, "zaten aktif bir operasyon"):
            self.op("12", ayak, [(profil, D("1"))])
        lazer = self.op("12", ayak, [(ayak, D("1"), SABIT, self.yok)], tur=Operasyon.Tur.DONUSTUR)
        g = lazer.girdiler.get(silindi=False)
        self.assertEqual((lazer.tur, g.varyant_kurali, g.sabit_varyant_id, lazer.cikti_varyant_kurali), ("DONUSTUR", SABIT, self.yok.pk, TASI))
        self.assertFalse(lazer.ciktilar.get(silindi=False).surucu)                                   # sürücü değil
        self.assertEqual(uretim.ureten_operasyon(ayak).pk, kesim.pk)                                   # kartı üreten tanım kesim kalır
        with self.assertRaisesMessage(uretim.UretimHatasi, "zaten aktif bir dönüştür"):
            self.op("12", ayak, [(ayak, D("1"), SABIT, self.yok)], tur=Operasyon.Tur.DONUSTUR)
        ayak2 = self.kart("151-10-0002", boyutlar=["MARKALAMA"])
        with self.assertRaisesMessage(uretim.UretimHatasi, "SABİT varyant kurallı olmalı"):
            self.op("12", ayak2, [(ayak2, D("1"))], tur=Operasyon.Tur.DONUSTUR)                       # dönüşen girdi TAŞI olamaz
        with self.assertRaisesMessage(uretim.UretimHatasi, "varyantlı bir kart değil"):
            self.op("20", self.kart("151-20-0001"), [(profil, D("1"), SABIT, self.yok)])
        with self.assertRaisesMessage(uretim.UretimHatasi, "sabit varyant kuralı için değer"):
            self.op("20", self.kart("151-20-0002"), [(ayak, D("1"), SABIT, None)])
        # aynı kart iki satır: TAŞI + SABİT YOK (çift çıkış montajı deseni); aynı kuralla ikinci satır reddedilir
        bir = self.kart("151-80-0011", boyutlar=["MARKALAMA"])
        montaj = self.op("70", self.kart("151-99-0002", boyutlar=["MARKALAMA"]), [(bir, D("1"), TASI, None), (bir, D("1"), SABIT, self.yok)])
        self.assertEqual(montaj.girdiler.filter(silindi=False).count(), 2)
        with self.assertRaisesMessage(uretim.UretimHatasi, "birden fazla satırda tekrarlanamaz"):
            self.op("70", self.kart("151-99-0003", boyutlar=["MARKALAMA"]), [(bir, D("1")), (bir, D("1"))])
        # güncelleme kuralları korur/değiştirir; dönüştür türü değiştirilemez
        uretim.operasyon_guncelle(kesim, istasyon_id=self.ist["10"].pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))])
        kesim.refresh_from_db()
        self.assertEqual((kesim.cikti_varyant_kurali, kesim.cikti_sabit_varyant_id), (SABIT, self.yok.pk))          # _KORU
        uretim.operasyon_guncelle(kesim, istasyon_id=self.ist["10"].pk, cikti_miktar=D("1"), satirlar=[(profil, D("1"))], cikti_varyant_kurali=TASI)
        kesim.refresh_from_db()
        self.assertEqual((kesim.cikti_varyant_kurali, kesim.cikti_sabit_varyant_id), (TASI, None))
        with self.assertRaisesMessage(uretim.UretimHatasi, "Dönüştür tanımı diğer türlere"):
            uretim.operasyon_guncelle(lazer, istasyon_id=self.ist["12"].pk, cikti_miktar=D("1"), satirlar=[(ayak, D("1"), SABIT, self.yok)], tur=Operasyon.Tur.URET)

    def test_operasyon_formu_kural_alanlari(self):
        EkranYetki.objects.create(kullanici=self.yon, ekran_kod="operasyon_tanimlari")
        self.client.force_login(self.yon)
        profil = self.kart("150-10-0001")
        ayak = self.kart("151-10-0001", boyutlar=["MARKALAMA"])
        r = self.client.post(reverse("core:operasyon_ekle"), {
            "istasyon": self.ist["10"].pk, "cikti": ayak.pk, "cikti_miktar": "1", "tur": "URET", "tam_boy_var": "1",
            "cikti_varyant_kurali": "SABIT", "cikti_sabit_varyant": self.yok.pk,
            "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
            "satir-0-girdi": profil.pk, "satir-0-miktar": "1", "satir-0-varyant_kurali": "TASI",
            "yan-TOTAL_FORMS": "0", "yan-INITIAL_FORMS": "0", "yan-MIN_NUM_FORMS": "0", "yan-MAX_NUM_FORMS": "1000",
            "pc-TOTAL_FORMS": "0", "pc-INITIAL_FORMS": "0", "pc-MIN_NUM_FORMS": "0", "pc-MAX_NUM_FORMS": "1000"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context.get("hatalar"))
        op = uretim.ureten_operasyon(ayak)
        self.assertEqual((op.cikti_varyant_kurali, op.cikti_sabit_varyant_id), (SABIT, self.yok.pk))
        r = self.client.get(reverse("core:operasyon_duzenle", args=[op.pk]))
        self.assertContains(r, 'name="cikti_varyant_kurali"')
        self.assertContains(r, 'value="SABIT" selected')
        self.assertContains(r, 'name="satir-0-varyant_kurali"')
        self.assertContains(r, "Dönüştür — aynı kart")


class HazirlikKomutuTest(OperasyonVaryantTaban):
    """Canlıdaki zincirin küçük kopyası: 11 ayak (kesim fason) + A tipi 4+1 ve ÇÇ 4+4 zinciri + 6 ÇÇ montaj/paketleme + yan etiket montajda."""

    def setUp(self):
        self.profil = self.kart("150-10-0001", "PROFİL")
        self.afis = self.kart("150-50-0001", "AFİŞ A", boyutlar=["AFIS"])
        self.yan_etiket = self.kart("150-50-0002", "YAN ETİKET", boyutlar=["YAN_ETIKET"])
        self.ayaklar = {}
        for kod in AYAK_YOK:
            a = self.kart(kod, boyutlar=["MARKALAMA"], varsayilan_yok=True)
            self.op("10", a, [(self.profil, D("1"))])
            self.ayaklar[kod] = a
        # A tipi 4+1: ayak 0003 → büküm 151-20-0003 → montaj 151-99-0004 (+ yan etiket) → paketleme 152-10-0003 (+ afiş)
        bukulmus = self.kart("151-20-0003", boyutlar=["MARKALAMA"])
        self.op("20", bukulmus, [(self.ayaklar["151-10-0003"], D("1"))])
        self.paketsiz_a = self.kart("151-99-0004", boyutlar=["MARKALAMA"])
        self.op("70", self.paketsiz_a, [(bukulmus, D("1")), (self.yan_etiket, D("1"))])
        self.mamul_a = self.kart("152-10-0003", boyutlar=["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"])
        self.op("80", self.mamul_a, [(self.paketsiz_a, D("1")), (self.afis, D("1"))])
        # ÇÇ: SOL 0014/16/18/20/22/24 + SAĞ (varyantsız) → birleştirilmiş 151-80-0011..16 → montaj 151-99-0002,0007..0011 (×2 + yan etiket) → paketleme 152-22-000x
        cc = list(zip(["151-10-0014", "151-10-0016", "151-10-0018", "151-10-0020", "151-10-0022", "151-10-0024"],
                      [f"151-80-{i:04d}" for i in range(11, 17)], ["151-99-0002"] + [f"151-99-{i:04d}" for i in range(7, 12)],
                      [f"152-22-{i:04d}" for i in range(1, 7)]))
        self.cc_montaj = {}
        for i, (sol, bir_kod, mon_kod, mam_kod) in enumerate(cc):
            sag = self.kart(f"151-10-00{90 + i}", "SAĞ")
            bir = self.kart(bir_kod, boyutlar=["MARKALAMA"])
            self.op("50", bir, [(self.ayaklar[sol], D("1")), (sag, D("1"))])
            mon = self.kart(mon_kod, boyutlar=["MARKALAMA"])
            self.cc_montaj[mon_kod] = self.op("70", mon, [(bir, D("2")), (self.yan_etiket, D("1"))])
            mam = self.kart(mam_kod, boyutlar=["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"])
            self.op("80", mam, [(mon, D("1")), (self.afis, D("1"))])
        self.mamul_cc = Stok.objects.get(kod="152-22-0003")

    def plan(self):
        from core.management.commands.varyant_operasyon_hazirla import golden
        return golden((("152-10-0003", 10), ("152-22-0003", 10)))

    def test_kuru_sonra_uygula_altin_kontrol(self):
        once = self.plan()
        out = StringIO()
        call_command("varyant_operasyon_hazirla", stdout=out)
        m = out.getvalue()
        self.assertIn("KURU (geri alindi", m)
        self.assertIn("ISTASYON: YENİ 11 LAZER MARKALAMA (sıra 15)", m)
        self.assertIn("KESIM CIKTI KURALI (SABIT YOK) (11)", m)
        self.assertIn("DONUSTUR TANIMLARI (lazer markalama) (11)", m)
        self.assertIn("CIFT CIKIS MONTAJ AYAK BOLME (6)", m)
        self.assertIn("YAN ETIKET DEGISEN TANIM SATIRI: 14 (cikar 7 + ekle 7)", m)
        self.assertIn("ALTIN KONTROL: yalniz yan etiketin istasyonu degisti", m)
        self.assertFalse(IsIstasyonu.objects.filter(kod="11").exists())
        self.assertFalse(Operasyon.objects.filter(tur="DONUSTUR").exists())
        self.assertEqual(self.plan(), once)
        call_command("varyant_operasyon_hazirla", "--uygula", stdout=StringIO())
        self.assertTrue(IsIstasyonu.objects.filter(kod="11", sira=15).exists())
        self.assertEqual(Operasyon.objects.filter(tur="DONUSTUR", silindi=False).count(), 11)
        kesim = uretim.ureten_operasyon(self.ayaklar["151-10-0003"])
        self.assertEqual((kesim.istasyon.kod, kesim.cikti_varyant_kurali, kesim.cikti_sabit_varyant_id), ("10", SABIT, self.yok.pk))
        mon = Operasyon.objects.get(pk=self.cc_montaj["151-99-0008"].pk)
        satirlar = sorted((g.girdi.kod, g.miktar, g.varyant_kurali, g.sabit_varyant_id) for g in uretim.operasyon_girdileri(mon))
        self.assertEqual(satirlar, [("151-80-0013", D("1"), SABIT, self.yok.pk), ("151-80-0013", D("1"), TASI, None)])    # yan etiket gitti
        pak = uretim.ureten_operasyon(Stok.objects.get(kod="152-22-0003"))
        self.assertEqual({g.girdi.kod for g in uretim.operasyon_girdileri(pak)}, {"151-99-0008", "150-50-0001", "150-50-0002"})
        self.assertFalse(OperasyonGirdi.objects.filter(girdi=self.yan_etiket, silindi=False, operasyon__istasyon__kod="70").exists())
        sonra = self.plan()
        self.assertEqual(sonra["150-50-0002"], once["150-50-0002"])                                     # miktar/çalıştırma aynı
        self.assertEqual((once["KULLANIM 150-50-0002"], sonra["KULLANIM 150-50-0002"]), (("70",), ("80",)))
        for k in once:
            if k != "KULLANIM 150-50-0002":
                self.assertEqual(sonra[k], once[k], k)                                                  # yan etiket dışında birebir
        self.assertFalse(any(k.startswith("PLAN ") and v[0] == "11" for k, v in sonra.items()))        # lazer planda yok
        emir = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "10"}], depo_id=self.depo.pk,
                                          tarih=__import__("datetime").date(2026, 10, 12))
        self.assertFalse(emir.istasyon_emirleri.filter(istasyon__kod="11").exists())                   # lazer emri AÇILMAZ (E-6'ya kadar)
        self.assertFalse(emir.istasyon_emirleri.filter(istasyon__kod="10").exists())                   # fason (10 BORU LAZER) emri KENDİLİĞİNDEN açılmaz (2026-10-23): eksik tablosunda fason gerekli
        self.assertTrue([r for r in uretim.uretim_emri_eksik_tablosu(emir) if r["tur"] == "FASON"])
        out2 = StringIO()
        call_command("varyant_operasyon_hazirla", stdout=out2)                                         # idempotent: ikinci koşu değişiklik yok
        m2 = out2.getvalue()
        self.assertIn("ATLANAN (11)", m2)
        self.assertIn("YAN ETIKET DEGISEN TANIM SATIRI: 0", m2)
        self.assertIn("CIFT CIKIS MONTAJ AYAK BOLME (0)", m2)
