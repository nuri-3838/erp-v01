"""E-6: VARYANTLI PLANLAMA anahtarı (AYARLAR › Üretim Ayarları, varsayılan KAPALI) — docs/varyant_kararlari.md.

KAPALI: planlama/ÜS/istasyon emirleri kart bazında, BİREBİR eski motor (lazer emri yok, varyant alanları boş). AÇIK: plan (stok, varyant)
düğümleriyle: markalama gereken ayak için LAZER MARKALAMA (dönüştür) emri, çift çıkış birleştirme emri markalı/markasız ikiye, özel varyantlı
mamul standart 152 stoğundan karşılanmaz, YOK afiş tüketilmez; kayıt/onay/geri alma varyant bazında ayırma ve hareket; LAZER ve PAKETLEME'de
fazla üretim reddi. ALTIN KONTROL: standart ÜS planı açık = kapalı + lazer emirleri + birleştirme ayrımı."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.models import FaturaTipi, HesapPlani, IsIstasyonu, KategoriHesap, Kur, Operasyon, OperasyonKaydi, StokHareket, UretimAyari, UretimEmri
from core.services import planlama_varyant as pv
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services import varyant as vs
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.tests.test_teklif_siparis import _hesap
from core.tests.test_varyant_operasyon import OperasyonVaryantTaban

D = Decimal
GUN = date(2026, 10, 12)
DORT = ["MARKALAMA", "KILAVUZ", "AFIS", "YAN_ETIKET"]
TASI, SABIT = Operasyon.VaryantKurali.TASI, Operasyon.VaryantKurali.SABIT


class PlanlamaTaban(OperasyonVaryantTaban):
    maxDiff = None
    """Canlı zincirin küçük kopyası (E-5 hazırlık SONRASI hâli): kesim SABİT YOK + lazer DÖNÜŞTÜR, ÇÇ montaj ×1 TAŞI + ×1 SABİT YOK."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        IsIstasyonu.objects.filter(pk=cls.ist["10"].pk).update(fason=False)          # bu testlerde kesim fabrikada (Üretildi ile)
        cls.ist["10"].fason = False
        cls.ist["11"] = IsIstasyonu.objects.create(kod="11", ad="LAZER MARKALAMA", sira=15)
        Kur.objects.create(tarih=GUN, usd_alis=D("40"))                                                  # onay fişi USD karşılığı
        alis = FaturaTipi.objects.create(ad="ALIŞ FATURASI (E6)", yon=FaturaTipi.Yon.ALIS)                  # onay fişi için kategori → 15x hesabı
        for kok, hesap_kodu in (("150", "150.10"), ("151", "151.10"), ("152", "152.10")):
            _hesap(hesap_kodu, f"STOK {kok}")
            h = HesapPlani.objects.get(hesap_kodu=hesap_kodu)
            for k, kat in cls.kat.items():
                if k.startswith(kok):
                    KategoriHesap.objects.create(kategori=kat, fatura_tipi=alis, hesap=h)

    def setUp(self):
        self.profil = self.kart("150-10-0001", "PROFİL")
        self.afis = self.kart("150-50-0001", "AFİŞ A", boyutlar=["AFIS"])
        self.afis_c = self.kart("150-50-0003", "AFİŞ C", boyutlar=["AFIS"])
        self.kilavuz = self.kart("150-50-0004", "KILAVUZ", boyutlar=["KILAVUZ"])
        self.yan_etiket = self.kart("150-50-0002", "YAN ETİKET", boyutlar=["YAN_ETIKET"])
        self.naylon = self.kart("150-50-0009", "NAYLON")
        # A tipi: profil → kesim (SABİT YOK) ön ayak → [lazer] → büküm → montaj → paketleme (+afiş, naylon, kılavuz, yan etiket)
        self.ayak_on = self.kart("151-10-0003", "KESİLMİŞ ÖN AYAK", boyutlar=["MARKALAMA"], varsayilan_yok=True)
        self.kesim_on = self.op("10", self.ayak_on, [(self.profil, D("1"))], cikti_varyant_kurali=SABIT, cikti_sabit_varyant_id=self.yok.pk)
        self.lazer_on = self.op("11", self.ayak_on, [(self.ayak_on, D("1"), SABIT, self.yok)], tur=Operasyon.Tur.DONUSTUR)
        self.bukulmus = self.kart("151-20-0003", "BÜKÜLMÜŞ ÖN AYAK", boyutlar=["MARKALAMA"])
        self.bukum = self.op("20", self.bukulmus, [(self.ayak_on, D("1"))])
        self.paketsiz_a = self.kart("151-99-0004", "PAKETSİZ A", boyutlar=["MARKALAMA"])
        self.montaj_a = self.op("70", self.paketsiz_a, [(self.bukulmus, D("1"))])
        self.mamul_a = self.kart("152-10-0003", "A 4+1", boyutlar=DORT)
        self.pak_a = self.op("80", self.mamul_a, [(self.paketsiz_a, D("1")), (self.afis, D("1")), (self.naylon, D("1")), (self.kilavuz, D("1")),
                                                   (self.yan_etiket, D("1"))])
        # Çift çıkış: SAĞ (varyantsız) + SOL (SABİT YOK, lazer) → birleştirme (TAŞI) → montaj (×1 TAŞI + ×1 SABİT YOK) → paketleme
        self.sag = self.kart("151-10-0017", "SAĞ AYAK")
        self.kesim_sag = self.op("10", self.sag, [(self.profil, D("1"))])
        self.sol = self.kart("151-10-0018", "SOL AYAK", boyutlar=["MARKALAMA"], varsayilan_yok=True)
        self.kesim_sol = self.op("10", self.sol, [(self.profil, D("1"))], cikti_varyant_kurali=SABIT, cikti_sabit_varyant_id=self.yok.pk)
        self.lazer_sol = self.op("11", self.sol, [(self.sol, D("1"), SABIT, self.yok)], tur=Operasyon.Tur.DONUSTUR)
        self.bir = self.kart("151-80-0013", "BİRLEŞTİRİLMİŞ ÖN AYAK 4+4", boyutlar=["MARKALAMA"])
        self.birlestirme = self.op("50", self.bir, [(self.sol, D("1")), (self.sag, D("1"))])
        self.mon_cc = self.kart("151-99-0008", "PAKETSİZ ÇÇ", boyutlar=["MARKALAMA"])
        self.montaj_cc = self.op("70", self.mon_cc, [(self.bir, D("1"), TASI, None), (self.bir, D("1"), SABIT, self.yok)])
        self.mamul_cc = self.kart("152-22-0003", "ÇÇ 4+4", boyutlar=DORT)
        self.pak_cc = self.op("80", self.mamul_cc, [(self.mon_cc, D("1")), (self.afis_c, D("1")), (self.kilavuz, D("1")), (self.yan_etiket, D("1"))])
        self.giris(self.profil, "1000")
        self.std_a = vs.standart_varyant(self.mamul_a)
        self.ozel_a = vs.stok_varyanti_bul_veya_olustur(self.mamul_a, {"MARKALAMA": self.mop, "KILAVUZ": self.semta, "AFIS": self.yok,
                                                                        "YAN_ETIKET": self.semta})
        self.ayak_yok = self.ayak_on.varsayilan_varyant
        self.ayak_semta = vs.standart_varyant(self.ayak_on)

    def giris(self, stok, miktar, varyant=None, tarih=date(2026, 1, 5)):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih, tur=StokHareket.Tur.GIRIS, miktar=D(miktar), giris_tutar_try=D(miktar),
                            varyant=varyant)

    def ac(self, *kalemler):
        """kalemler: (stok, miktar[, varyant]) → ÜS."""
        k = []
        for satir in kalemler:
            d = {"hedef_urun_id": satir[0].pk, "hedef_miktar": satir[1]}
            if len(satir) > 2 and satir[2] is not None:
                d["varyant_id"] = satir[2].pk
            k.append(d)
        return uretim.uretim_emri_olustur(kalemler=k, depo_id=self.depo.pk, tarih=GUN, kullanici=self.yon)

    @staticmethod
    def plan(emir):
        """{(operasyon pk, çıktı varyant etiketi): planlanan}"""
        return {(ie.operasyon_id, pv.etiket(ie.cikti_varyant)): ie.planlanan
                for ie in emir.istasyon_emirleri.filter(silindi=False).exclude(durum="IPTAL")}

    @staticmethod
    def eksik(emir):
        return {(e["stok"].kod, e["varyant_etiketi"]): e["eksik"] for e in uretim.uretim_emri_eksikleri(emir)}

    def ie(self, emir, op, etiket=None):
        qs = emir.istasyon_emirleri.filter(silindi=False, operasyon=op)
        if etiket is not None:
            qs = [x for x in qs if pv.etiket(x.cikti_varyant) == etiket]
            self.assertEqual(len(qs), 1)
            return qs[0]
        return qs.get()


class AnahtarKapaliTest(PlanlamaTaban):
    def test_kapali_birebir_eski_motor(self):
        self.assertFalse(pv.acik())
        emir = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))
        self.assertFalse(emir.varyantli_plan)
        self.assertFalse(emir.istasyon_emirleri.filter(istasyon__kod="11").exists())                       # lazer emri YOK
        self.assertFalse(emir.istasyon_emirleri.filter(cikti_varyant__isnull=False).exists())
        self.assertTrue(all(k.varyant_id is None for k in emir.kalemler.all()))
        self.assertEqual(self.plan(emir), {
            (self.kesim_on.pk, ""): D("10"), (self.bukum.pk, ""): D("10"), (self.montaj_a.pk, ""): D("10"), (self.pak_a.pk, ""): D("10"),
            (self.kesim_sag.pk, ""): D("20"), (self.kesim_sol.pk, ""): D("20"), (self.birlestirme.pk, ""): D("20"), (self.montaj_cc.pk, ""): D("10"),
            (self.pak_cc.pk, ""): D("10")})
        for ie in emir.istasyon_emirleri.all():
            self.assertTrue(all(":" not in k for k in ie.ihtiyac), ie.ihtiyac)                               # anahtarlar düz stok pk
        self.assertEqual(self.eksik(emir), {("150-50-0001", ""): D("10"), ("150-50-0003", ""): D("10"), ("150-50-0004", ""): D("20"),
                                            ("150-50-0002", ""): D("20"), ("150-50-0009", ""): D("10")})
        ayirmalar = {(a.stok.kod, a.varyant_id) for a in sa.emir_ayirmalari(emir)}
        self.assertEqual(ayirmalar, {("150-10-0001", None)})                                                 # profil varyantsız (kart varsayılanı)
        uretim.uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.mamul_a.pk, "hedef_miktar": "12"}], kullanici=self.yon)
        self.assertEqual(self.plan(emir)[(self.kesim_on.pk, "")], D("12"))
        # kapalıyken kayıt: çıktı/girdi varyantı boş (hareket kartın varsayılanına gider)
        kayit, _ = uretim.istasyon_emri_uret(self.ie(emir, self.kesim_on), tarih=GUN, kullanici=self.yon)
        self.assertIsNone(kayit.cikti_varyant_id)
        self.assertTrue(all(g.varyant_id is None for g in kayit.girdi_satirlari.all()))
        self.assertEqual(kayit.ciktilar.get(ana_mi=True).varyant_id, None)
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=kayit, tur="GIRIS").varyant_id, self.ayak_yok.pk)
        self.assertEqual(sa.ayrilan_miktar(emir, self.ayak_on), D("12"))

    def test_ayar_ekrani(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_ayarlari"))
        self.assertContains(r, "Varyantlı planlama")
        self.assertContains(r, "KAPALI")
        r = self.client.post(reverse("core:uretim_ayarlari"), {"varyantli_planlama": "on"}, follow=True)
        self.assertTrue(pv.acik())
        self.assertContains(r, 'checked')
        r = self.client.post(reverse("core:uretim_ayarlari"), {}, follow=True)
        self.assertFalse(pv.acik())
        self.assertEqual(UretimAyari.objects.count(), 1)
        r = self.client.get(reverse("core:tanim_listeleri"))
        self.assertContains(r, reverse("core:uretim_ayarlari"))


class AnahtarAcikTest(PlanlamaTaban):
    def setUp(self):
        super().setUp()
        pv.anahtar_ayarla(True, self.yon)

    def golden(self, acik_plan, kapali_plan):
        """Açık plan = kapalı plan + lazer emirleri (markalama) + birleştirme emrinin markalı/markasız ayrılması; diğer her şey aynı."""
        lazer = {self.lazer_on.pk, self.lazer_sol.pk}
        toplam = {}
        for (opk, _etiket), m in acik_plan.items():
            if opk not in lazer:
                toplam[opk] = toplam.get(opk, D("0")) + m
        self.assertEqual(toplam, {opk: m for (opk, _e), m in kapali_plan.items()})

    def test_standart_plan_altin_kontrol_ve_lazer_emirleri(self):
        pv.anahtar_ayarla(False)
        kapali = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))
        kapali_plan, kapali_eksik = self.plan(kapali), self.eksik(kapali)
        uretim.uretim_emri_iptal(kapali, self.yon)
        pv.anahtar_ayarla(True)
        emir = self.ac((self.mamul_a, "10"), (self.mamul_cc, "10"))
        self.assertTrue(emir.varyantli_plan)
        self.assertEqual({k.hedef_urun.kod: k.varyant.anahtar for k in emir.kalemler.all()},
                         {"152-10-0003": self.std_a.anahtar, "152-22-0003": vs.standart_varyant(self.mamul_cc).anahtar})
        plan = self.plan(emir)
        self.golden(plan, kapali_plan)
        self.assertEqual(plan, {
            (self.kesim_on.pk, "YOK"): D("10"), (self.lazer_on.pk, "SEMTA"): D("10"), (self.bukum.pk, "SEMTA"): D("10"),
            (self.montaj_a.pk, "SEMTA"): D("10"), (self.pak_a.pk, "SEMTA/SEMTA/SEMTA/SEMTA"): D("10"),
            (self.kesim_sag.pk, ""): D("20"), (self.kesim_sol.pk, "YOK"): D("20"), (self.lazer_sol.pk, "SEMTA"): D("10"),
            (self.birlestirme.pk, "SEMTA"): D("10"), (self.birlestirme.pk, "YOK"): D("10"),                   # ÇÇ birleştirme ikiye
            (self.montaj_cc.pk, "SEMTA"): D("10"), (self.pak_cc.pk, "SEMTA/SEMTA/SEMTA/SEMTA"): D("10")})
        self.assertEqual({k: v for k, v in self.eksik(emir).items()}, {
            ("150-50-0001", "SEMTA"): D("10"), ("150-50-0003", "SEMTA"): D("10"), ("150-50-0004", "SEMTA"): D("20"),
            ("150-50-0002", "SEMTA"): D("20"), ("150-50-0009", ""): D("10")})
        self.assertEqual({(k[0], v) for k, v in self.eksik(emir).items()}, {(k[0], v) for k, v in kapali_eksik.items()})   # eksikler aynı
        lazer = self.ie(emir, self.lazer_on)
        self.assertEqual((lazer.istasyon.kod, lazer.cikti_varyant_id, lazer.ihtiyac), ("11", self.ayak_semta.pk, {f"{self.ayak_on.pk}:{self.ayak_semta.pk}": "10"}))
        self.assertEqual(self.ie(emir, self.kesim_on).ihtiyac, {f"{self.ayak_on.pk}:{self.ayak_yok.pk}": "10"})
        self.assertEqual(sa.ayrilan_miktar(emir, self.profil), D("50"))                                      # profil: 10 ön + 20 sağ + 20 sol
        # ÜS detayı varyant rozetleri
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, "LAZER MARKALAMA")
        self.assertContains(r, 'title="Çıktı varyantı">SEMTA</span>')
        self.assertContains(r, 'title="Çıktı varyantı">YOK</span>')

    def test_ara_stok_her_iki_modda_ayni_kesim_ve_mamul_stogu(self):
        """Markasız ayak stoğu (4) ve standart mamul stoğu (3): kapalıda kesim 3 / paketleme 7; açıkta da kesim 3 / paketleme 7 (lazer 7)."""
        self.giris(self.ayak_on, "4")                                                                        # varsayılan YOK
        self.giris(self.mamul_a, "3")                                                                        # varsayılan SEMTA
        pv.anahtar_ayarla(False)
        kapali = self.ac((self.mamul_a, "10"))
        kp = self.plan(kapali)
        self.assertEqual((kp[(self.kesim_on.pk, "")], kp[(self.pak_a.pk, "")]), (D("3"), D("7")))
        uretim.uretim_emri_iptal(kapali, self.yon)
        pv.anahtar_ayarla(True)
        emir = self.ac((self.mamul_a, "10"))
        p = self.plan(emir)
        self.golden(p, kp)
        self.assertEqual((p[(self.kesim_on.pk, "YOK")], p[(self.lazer_on.pk, "SEMTA")], p[(self.pak_a.pk, "SEMTA/SEMTA/SEMTA/SEMTA")]), (D("3"), D("7"), D("7")))
        self.assertEqual(sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_yok), D("4"))
        self.assertEqual(sa.ayrilan_miktar(emir, self.mamul_a, varyant=self.std_a), D("3"))
        self.assertEqual(emir.kalemler.get().eldeki_ayrilan, D("3"))

    def test_ozel_varyant_mr_mop_afis_yok(self):
        self.giris(self.mamul_a, "3")                                                                        # standart stok: özel için KULLANILMAZ
        self.giris(self.afis, "5")
        emir = self.ac((self.mamul_a, "10", self.ozel_a))
        self.assertEqual(emir.kalemler.get().varyant_id, self.ozel_a.pk)
        p = self.plan(emir)
        self.assertEqual((p[(self.pak_a.pk, "MR. MOP/SEMTA/YOK/SEMTA")], p[(self.lazer_on.pk, "MR. MOP")], p[(self.bukum.pk, "MR. MOP")],
                          p[(self.kesim_on.pk, "YOK")]), (D("10"), D("10"), D("10"), D("10")))
        self.assertEqual(sa.ayrilan_miktar(emir, self.mamul_a), D("0"))                                      # 152 serbest (standart) stoğu dokunulmadı
        self.assertEqual(sa.ayrilan_miktar(emir, self.afis), D("0"))                                         # afiş YOK: tüketim yok
        self.assertEqual(self.eksik(emir), {("150-50-0004", "SEMTA"): D("10"), ("150-50-0002", "SEMTA"): D("10"), ("150-50-0009", ""): D("10")})
        self.assertEqual(uretim.uretim_emri_yuzde(emir), D("0"))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, "MR. MOP/SEMTA/YOK/SEMTA")
        self.assertContains(r, 'title="Çıktı varyantı">MR. MOP</span>')
        # özel varyantı üretemeyen kart (dönüştür tanımı yok): açık hata
        uretim.operasyon_sil(self.lazer_on, self.yon)
        with self.assertRaisesMessage(uretim.UretimHatasi, "MR. MOP varyantı üretilemiyor"):
            self.ac((self.mamul_a, "1", self.ozel_a))

    def test_kayit_onay_geri_alma_ve_fazla_uretim_reddi(self):
        for s in (self.afis, self.kilavuz, self.yan_etiket, self.naylon):
            self.giris(s, "20")
        emir = self.ac((self.mamul_a, "10"))
        ie_kesim, ie_lazer, ie_pak = self.ie(emir, self.kesim_on), self.ie(emir, self.lazer_on), self.ie(emir, self.pak_a)
        # kesim: çıktı YOK varyantına girer, ÜS'ye (ayak, YOK) ayrılır; fazlası serbest (kesimde fazla üretim serbest)
        kayit, uyari = uretim.istasyon_emri_uret(ie_kesim, miktar=D("12"), tarih=GUN, kullanici=self.yon)
        self.assertIn("aşıyor", uyari)
        self.assertEqual(kayit.cikti_varyant_id, self.ayak_yok.pk)
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=kayit, tur="GIRIS").varyant_id, self.ayak_yok.pk)
        self.assertEqual(kayit.ciktilar.get(ana_mi=True).varyant_id, self.ayak_yok.pk)
        self.assertEqual((sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_yok), eldeki_miktar(self.ayak_on, varyant=self.ayak_yok)), (D("10"), D("12")))
        # lazer: fazla üretim REDDEDİLİR; normal üretim (ayak, YOK) tüketir → (ayak, SEMTA) üretir ve ayırır
        with self.assertRaisesMessage(uretim.UretimHatasi, "fazla üretim yapılamaz"):
            uretim.istasyon_emri_uret(ie_lazer, miktar=D("11"), tarih=GUN, kullanici=self.yon)
        with self.assertRaisesMessage(uretim.UretimHatasi, "fazla üretim yapılamaz"):
            uretim.istasyon_emri_uret(ie_pak, miktar=D("11"), tarih=GUN, kullanici=self.yon)
        lk, _ = uretim.istasyon_emri_uret(ie_lazer, tarih=GUN, kullanici=self.yon)
        g = lk.girdi_satirlari.get()
        self.assertEqual((g.girdi_id, g.varyant_id, g.gerceklesen_miktar, g.ayrilan_dusen), (self.ayak_on.pk, self.ayak_yok.pk, D("10"), D("10")))
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=lk, tur="CIKIS").varyant_id, self.ayak_yok.pk)
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=lk, tur="GIRIS").varyant_id, self.ayak_semta.pk)
        self.assertEqual((sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_yok), sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_semta)),
                         (D("0"), D("10")))
        self.assertEqual((eldeki_miktar(self.ayak_on, varyant=self.ayak_yok), eldeki_miktar(self.ayak_on, varyant=self.ayak_semta)), (D("2"), D("10")))
        ie_lazer.refresh_from_db()
        self.assertEqual((ie_lazer.tamamlanan, ie_lazer.durum), (D("10"), "BITTI"))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:operasyon_kaydi_detay", args=[lk.pk]))
        self.assertContains(r, 'title="Çıktı varyantı">SEMTA</span>')
        self.assertContains(r, 'title="Girdi varyantı">YOK</span>')
        # geri alma: ayırmalar aynen geri sarılır
        uretim.operasyon_kaydi_sil(lk, kullanici=self.yon, onayli_geri_al=True)
        self.assertEqual((sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_yok), sa.ayrilan_miktar(emir, self.ayak_on, varyant=self.ayak_semta)),
                         (D("10"), D("0")))
        ie_lazer.refresh_from_db()
        self.assertEqual((ie_lazer.tamamlanan, ie_lazer.durum), (D("0"), "BEKLIYOR"))
        # tümünü üret: zincir tamamlanır, mamul standart varyanta girer, stok ÜS'si kapanır
        uretim.uretim_emri_toplu_uret(emir, tarih=GUN, kullanici=self.yon)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        self.assertEqual(eldeki_miktar(self.mamul_a, varyant=self.std_a), D("10"))
        pk_kayit = OperasyonKaydi.objects.get(istasyon_emri=ie_pak, silindi=False)
        self.assertEqual(pk_kayit.ciktilar.get(ana_mi=True).varyant_id, self.std_a.pk)
        self.assertEqual({g.girdi.kod: g.varyant_id for g in pk_kayit.girdi_satirlari.all()},
                         {"151-99-0004": vs.standart_varyant(self.paketsiz_a).pk, "150-50-0001": vs.standart_varyant(self.afis).pk,
                          "150-50-0009": None, "150-50-0004": vs.standart_varyant(self.kilavuz).pk, "150-50-0002": vs.standart_varyant(self.yan_etiket).pk})

    def test_ozel_uretim_afis_tuketilmez_mamul_ozel_varyanta_girer(self):
        for s in (self.afis, self.kilavuz, self.yan_etiket, self.naylon):
            self.giris(s, "20")
        emir = self.ac((self.mamul_a, "2", self.ozel_a))
        uretim.uretim_emri_toplu_uret(emir, tarih=GUN, kullanici=self.yon)
        pk_kayit = OperasyonKaydi.objects.get(istasyon_emri=self.ie(emir, self.pak_a), silindi=False)
        self.assertEqual(pk_kayit.cikti_varyant_id, self.ozel_a.pk)
        self.assertEqual({g.girdi.kod for g in pk_kayit.girdi_satirlari.all()}, {"151-99-0004", "150-50-0009", "150-50-0004", "150-50-0002"})   # afiş yok
        self.assertEqual(eldeki_miktar(self.afis), D("20"))
        self.assertEqual((eldeki_miktar(self.mamul_a, varyant=self.ozel_a), eldeki_miktar(self.mamul_a, varyant=self.std_a)), (D("2"), D("0")))
        mop_ayak = vs.stok_varyanti_bul_veya_olustur(self.ayak_on, {"MARKALAMA": self.mop})
        lk = OperasyonKaydi.objects.get(istasyon_emri=self.ie(emir, self.lazer_on), silindi=False)
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=lk, tur="GIRIS").varyant_id, mop_ayak.pk)
        emir.refresh_from_db()
        self.assertEqual((emir.durum, uretim.uretim_emri_yuzde(emir)), (UretimEmri.Durum.KAPALI, D("100")))

    def test_revize_varyantli_plan_ve_cc_ikiye_ayrilma_korunur(self):
        emir = self.ac((self.mamul_cc, "10"))
        self.assertEqual((self.plan(emir)[(self.birlestirme.pk, "SEMTA")], self.plan(emir)[(self.birlestirme.pk, "YOK")]), (D("10"), D("10")))
        uretim.uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.mamul_cc.pk, "hedef_miktar": "4"}], kullanici=self.yon)
        p = self.plan(emir)
        self.assertEqual((p[(self.birlestirme.pk, "SEMTA")], p[(self.birlestirme.pk, "YOK")], p[(self.lazer_sol.pk, "SEMTA")], p[(self.kesim_sol.pk, "YOK")]),
                         (D("4"), D("4"), D("4"), D("8")))
        self.assertEqual(emir.istasyon_emirleri.filter(silindi=False).count(), 7)                           # yeni emir açılmadı, hepsi güncellendi
        uretim.uretim_emri_iptal(emir, self.yon)
        self.assertFalse(sa.emir_ayirmalari(emir).exists())
