"""E-2 varyant motoru: varyantlı kartta hareket varyantı (verilen / kartın varsayılanı), eldeki + tarih bazlı çıkış kontrolü + hareket silme
varyant bazında, her varyantın ayrı ortalaması (kart = toplam), ayırma (ÜS, stok, varyant), depo transferi varyantı taşır; varyantsız kart
birebir aynı (varyant boş, varyant verilince hata). Veri göçü komutu: kuru/uygula + ALTIN KONTROL."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.models import KdvOrani, Stok, StokAyirma, StokHareket, StokVaryant, UretimEmri, Varyant
from core.services import stok_ayirma as sa
from core.services import stok_ortalama as so
from core.services import varyant as vs
from core.services.birim import birim_olustur
from core.services.depo import depo_olustur
from core.services.depo_transfer import depo_transferi_yap
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services.kategori import kategori_olustur
from core.services.stok import StokHatasi, stok_guncelle, stok_olustur

D = Decimal
GIRIS, CIKIS = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS


class VaryantMotorTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        ust = kategori_olustur(ad="yarı mamul", kod="151")
        cls.alt = kategori_olustur(ad="kesilmiş", kod="10", ust_id=ust.pk)
        cls.adet = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        cls.depo = depo_olustur(kod="D1", ad="DEPO 1")
        cls.depo2 = depo_olustur(kod="D2", ad="DEPO 2")
        cls.kdv, _ = KdvOrani.objects.get_or_create(oran=D("20"), silindi=False, defaults={"aciklama": "ORAN"})
        cls.semta = Varyant.objects.get(kod="SEMTA")
        cls.yok = Varyant.objects.get(kod="YOK")

    def kart(self, ad="ayak", varyantli=True, boyutlar=("MARKALAMA",), kategori=None):
        return stok_olustur(ad=ad, kategori_id=(kategori or self.alt).pk, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk,
                            cevirici=D("1"), kdv_id=self.kdv.pk, uretim_urunu=True, varyantli=varyantli, varyant_boyutlari=list(boyutlar))

    def giris(self, stok, miktar, tutar, tarih=date(2026, 6, 1), **ek):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih, tur=GIRIS, miktar=D(miktar), giris_tutar_try=D(tutar), **ek)

    def cikis(self, stok, miktar, tarih=date(2026, 6, 10), **ek):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih, tur=CIKIS, miktar=D(miktar), **ek)

    def kontrol_esit(self, stok):
        """DEĞİŞMEZ: her varyantın zinciri sıfırdan hesapla = saklanan; kart önbelleği = varyant toplamı."""
        hs = so.kart_hareketleri(stok)
        gruplar = {}
        for h in hs:
            gruplar.setdefault(h.varyant_id, []).append(h)
        toplam_m = toplam_d = D("0")
        for vid, grup in gruplar.items():
            saf = so.hesapla(grup)
            for h, s in zip(grup, saf):
                self.assertEqual((h.tutar_try, h.sonrasi_miktar, h.sonrasi_deger_try, h.sonrasi_ort_try),
                                 (s.tutar_try, s.sonrasi_miktar, s.sonrasi_deger_try, s.sonrasi_ort_try), msg=f"hareket {h.pk}")
            if vid is not None:
                sv = StokVaryant.objects.get(pk=vid)
                self.assertEqual((sv.maliyet_miktar, sv.maliyet_deger_try, sv.ort_maliyet_try),
                                 (saf[-1].sonrasi_miktar, saf[-1].sonrasi_deger_try, saf[-1].sonrasi_ort_try))
            toplam_m += saf[-1].sonrasi_miktar
            toplam_d += saf[-1].sonrasi_deger_try
        stok.refresh_from_db()
        self.assertEqual((stok.maliyet_miktar, stok.maliyet_deger_try), (toplam_m, toplam_d))


class VaryantMotorTest(VaryantMotorTaban):
    def test_varyantsiz_kart_birebir_ayni(self):
        s = self.kart("perçin", varyantli=False, boyutlar=())
        h = self.giris(s, "10", "100")
        self.assertIsNone(h.varyant_id)
        self.assertEqual((s.varsayilan_varyant_id, eldeki_miktar(s)), (None, D("10")))
        v = self.kart("ayak")
        with self.assertRaisesMessage(HareketHatasi, "varyantlı bir kart değil"):
            self.giris(s, "1", "1", varyant=v.varsayilan_varyant_id)
        self.kontrol_esit(s)

    def test_varsayilan_varyant_ayri_eldeki_kontrol_ve_ortalama(self):
        s = self.kart()
        s.refresh_from_db()
        standart = StokVaryant.objects.get(stok=s, standart=True)
        self.assertEqual(s.varsayilan_varyant_id, standart.pk)                           # varyantlı kartta varsayılan = standart (SEMTA)
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        g1 = self.giris(s, "10", "1000")                                                 # varyant verilmedi → SEMTA
        g2 = self.giris(s, "10", "2000", varyant=yok)                                    # markasız @200
        self.assertEqual((g1.varyant_id, g2.varyant_id), (standart.pk, yok.pk))
        self.assertEqual((eldeki_miktar(s), eldeki_miktar(s, varyant=standart), eldeki_miktar(s, varyant=yok.pk)), (D("20"), D("10"), D("10")))
        standart.refresh_from_db()
        yok.refresh_from_db()
        s.refresh_from_db()
        self.assertEqual((standart.ort_maliyet_try, yok.ort_maliyet_try), (D("100"), D("200")))        # her varyantın kendi ortalaması
        self.assertEqual((s.maliyet_miktar, s.maliyet_deger_try, s.ort_maliyet_try), (D("20"), D("3000.00"), D("150")))   # kart = toplam
        c = self.cikis(s, "4", varyant=yok)
        self.assertEqual((c.tutar_try, c.varyant_id), (D("800.00"), yok.pk))             # markasız ortalamasıyla (200), SEMTA'dan değil
        with self.assertRaisesMessage(HareketHatasi, "Bu tarihte yetersiz: " + s.kod + " [SEMTA] (eldeki 10, gerekli 11)"):
            self.cikis(s, "11")                                                           # SEMTA varyantında 10 var (kart toplamı 16 olsa da)
        self.assertEqual(eldeki_miktar(s), D("16"))
        self.cikis(s, "8")                                                               # SEMTA 10 → 2
        with self.assertRaisesMessage(HareketHatasi, "negatife düşer"):
            hareket_sil(g1)                                                              # SEMTA girişi silinince SEMTA eksiye düşer
        self.kontrol_esit(s)

    def test_yok_girisi_silme_kontrolu_varyant_bazinda(self):
        s = self.kart()
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        g2 = self.giris(s, "10", "2000", varyant=yok)
        self.giris(s, "50", "5000")                                                     # SEMTA bol; kart toplamı 60
        self.cikis(s, "4", varyant=yok)
        with self.assertRaisesMessage(HareketHatasi, "negatife düşer"):                 # YOK: 10 − 4 = 6, giriş 10 silinince −4
            hareket_sil(g2)

    def test_kartin_varsayilan_varyanti_degisince_bos_hareket_oraya_gider(self):
        s = self.kart()
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        Stok.objects.filter(pk=s.pk).update(varsayilan_varyant=yok)
        h = self.giris(s, "3", "300")
        self.assertEqual(h.varyant_id, yok.pk)
        with self.assertRaisesMessage(HareketHatasi, "geçersiz varyant"):
            self.giris(s, "1", "1", varyant=self.kart("başka").varsayilan_varyant_id)   # başka kartın varyantı

    def test_ayirma_varyant_bazinda(self):
        s = self.kart()
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        self.giris(s, "10", "1000")
        self.giris(s, "10", "2000", varyant=yok)
        emir = UretimEmri.objects.create(yil=2026, sira=1, no="ÜS-2026-0001", depo=self.depo, tarih=date(2026, 6, 1))
        a1 = sa.ayirma_ayarla(emir, s, D("5"))
        a2 = sa.ayirma_ayarla(emir, s, D("3"), varyant=yok)
        self.assertEqual((a1.varyant_id, a2.varyant_id), (s.varsayilan_varyant_id, yok.pk))
        self.assertEqual((sa.ayrilan_miktar(emir, s), sa.ayrilan_miktar(emir, s, varyant=yok), sa.kullanilabilir(s)), (D("8"), D("3"), D("12")))
        sa.ayirma_ekle(emir, s, D("-3"), varyant=yok)
        self.assertEqual(StokAyirma.objects.filter(uretim_emri=emir, silindi=False).count(), 1)
        ozet = {x["etiket"]: (x["eldeki"], x["ayrilan"], x["kullanilabilir"]) for x in vs.varyant_ozeti(s)}
        self.assertEqual(ozet, {"Markalama: SEMTA": (D("10"), D("5"), D("5")), "Markalama: YOK": (D("10"), D("0"), D("10"))})
        p = self.kart("perçin", varyantli=False, boyutlar=())
        self.assertIsNone(sa.ayirma_ayarla(emir, p, D("2")).varyant_id)

    def test_depo_transferi_varyanti_tasir(self):
        s = self.kart()
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        self.giris(s, "10", "2000", varyant=yok)
        depo_transferi_yap(stok_id=s.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.depo2.pk, tarih=date(2026, 6, 5), miktar=D("4"), varyant=yok)
        self.assertEqual(set(StokHareket.objects.filter(stok=s, kaynak="TRANSFER").values_list("varyant_id", flat=True)), {yok.pk})
        self.assertEqual((eldeki_miktar(s, self.depo2, varyant=yok), eldeki_miktar(s, self.depo, varyant=yok)), (D("4"), D("6")))
        with self.assertRaisesMessage(HareketHatasi, "[SEMTA]"):                          # varsayılan (SEMTA) varyantında stok yok
            depo_transferi_yap(stok_id=s.pk, kaynak_depo_id=self.depo.pk, hedef_depo_id=self.depo2.pk, tarih=date(2026, 6, 5), miktar=D("1"))
        self.kontrol_esit(s)

    def test_varyantli_hareketi_olan_kart_varyantsiz_yapilamaz(self):
        s = self.kart()
        self.giris(s, "1", "10")
        with self.assertRaisesMessage(StokHatasi, "varyantsız yapılamaz"):
            stok_guncelle(s, ad=s.ad, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk, cevirici=D("1"), kdv_id=self.kdv.pk,
                          uretim_urunu=True, varyantli=False, varyant_boyutlari=[])


class VeriGocuTest(VaryantMotorTaban):
    """Plan alt kümesiyle (--kod): 151-10-0003 → YOK, 150-50-0004 → SEMTA; planda olmayan 150-40-0001 dokunulmaz."""

    def setUp(self):
        ust150 = kategori_olustur(ad="hammadde", kod="150")
        self.k5050 = kategori_olustur(ad="ambalaj", kod="50", ust_id=ust150.pk)
        self.k5040 = kategori_olustur(ad="perçin", kod="40", ust_id=ust150.pk)
        for _ in range(2):
            self.kart("dolgu", varyantli=False, boyutlar=())                              # 151-10-0001, 0002
        self.ayak = self.kart("kesilmiş a tipi ön ayak 4+1", varyantli=False, boyutlar=())   # 151-10-0003
        for _ in range(3):
            self.kart("dolgu", varyantli=False, boyutlar=(), kategori=self.k5050)
        self.kilavuz = self.kart("kullanma kılavuzu", varyantli=False, boyutlar=(), kategori=self.k5050)   # 150-50-0004
        self.percin = self.kart("perçin", varyantli=False, boyutlar=(), kategori=self.k5040)          # 150-40-0001
        self.assertEqual((self.ayak.kod, self.kilavuz.kod, self.percin.kod), ("151-10-0003", "150-50-0004", "150-40-0001"))
        self.giris(self.ayak, "100", "5000", tarih=date(2026, 1, 1))
        self.cikis(self.ayak, "30", tarih=date(2026, 2, 1))
        self.giris(self.ayak, "50", "3000", tarih=date(2026, 3, 1))
        silinen = self.giris(self.ayak, "1", "10", tarih=date(2026, 3, 2))
        hareket_sil(silinen)
        self.giris(self.kilavuz, "1000", "1000", tarih=date(2026, 1, 2))
        self.cikis(self.kilavuz, "200", tarih=date(2026, 2, 2))
        self.giris(self.percin, "500", "250", tarih=date(2026, 1, 3))
        self.emir = UretimEmri.objects.create(yil=2026, sira=2, no="ÜS-2026-0002", depo=self.depo, tarih=date(2026, 6, 1))
        sa.ayirma_ayarla(self.emir, self.ayak, D("20"))
        sa.ayirma_ayarla(self.emir, self.percin, D("5"))

    def anlik(self):
        return {s.kod: (eldeki_miktar(s), s.maliyet_miktar, s.maliyet_deger_try, s.ort_maliyet_try)
                for s in Stok.objects.filter(pk__in=[self.ayak.pk, self.kilavuz.pk, self.percin.pk])}

    def test_kuru_hicbir_sey_yazmaz_rapor_verir(self):
        once = self.anlik()
        out = StringIO()
        call_command("varyant_veri_gocu", "--kod", "151-10-0003", "--kod", "150-50-0004", stdout=out)
        m = out.getvalue()
        self.assertIn("KURU (geri alindi", m)
        self.assertIn("VARYANTLI YAPILACAK KARTLAR (2; planda 2", m)
        self.assertRegex(m, r"151-10-0003 .*MARKALAMA .*YOK +3 +4 +1")        # 3 aktif hareket, 4 tüm, 1 açık ayırma
        self.assertRegex(m, r"150-50-0004 .*KILAVUZ .*SEMTA +2 +2 +0")
        self.assertIn("ALTIN KONTROL: ESIT", m)
        self.assertIn("ÜS-2026-0002: 2 stok", m)
        self.assertEqual(Stok.objects.filter(varyantli=True).count(), 0)
        self.assertFalse(StokHareket.objects.filter(varyant__isnull=False).exists())
        self.assertEqual(self.anlik(), once)

    def test_uygula_tasir_altin_kontrol_esit_ve_idempotent(self):
        once = self.anlik()
        out = StringIO()
        call_command("varyant_veri_gocu", "--kod", "151-10-0003", "--kod", "150-50-0004", "--uygula", stdout=out)
        self.assertIn("UYGULANDI", out.getvalue())
        self.assertIn("ALTIN KONTROL: ESIT", out.getvalue())
        self.assertEqual(self.anlik(), once)                                              # kart önbellekleri birebir
        ayak = Stok.objects.get(pk=self.ayak.pk)
        kil = Stok.objects.get(pk=self.kilavuz.pk)
        self.assertEqual((ayak.varyantli, ayak.varyant_boyutlari, kil.varyantli, kil.varyant_boyutlari), (True, ["MARKALAMA"], True, ["KILAVUZ"]))
        self.assertEqual((ayak.varsayilan_varyant.markalama_id, ayak.varsayilan_varyant.standart), (self.yok.pk, False))
        self.assertEqual((kil.varsayilan_varyant.kilavuz_id, kil.varsayilan_varyant.standart), (self.semta.pk, True))
        self.assertEqual(set(StokHareket.objects.filter(stok=ayak).values_list("varyant_id", flat=True)), {ayak.varsayilan_varyant_id})   # silinen dahil
        self.assertEqual(StokAyirma.objects.get(stok=ayak, silindi=False).varyant_id, ayak.varsayilan_varyant_id)
        self.assertEqual((ayak.varsayilan_varyant.maliyet_miktar, ayak.varsayilan_varyant.ort_maliyet_try), (ayak.maliyet_miktar, ayak.ort_maliyet_try))
        p = Stok.objects.get(pk=self.percin.pk)
        self.assertEqual((p.varyantli, StokHareket.objects.filter(stok=p, varyant__isnull=False).count(),
                          StokAyirma.objects.get(stok=p, silindi=False).varyant_id), (False, 0, None))
        self.assertEqual(sa.ayrilan_miktar(self.emir, ayak), D("20"))
        with self.assertRaisesMessage(HareketHatasi, "[YOK]"):                             # yeni çıkış varsayılan YOK varyantından kontrol edilir
            self.cikis(ayak, "121", tarih=date(2026, 7, 1))
        out2 = StringIO()
        call_command("varyant_veri_gocu", "--kod", "151-10-0003", "--kod", "150-50-0004", "--uygula", stdout=out2)
        self.assertIn("VARYANTLI YAPILACAK KARTLAR (0; planda 2, zaten varyantli 2)", out2.getvalue())
        self.kontrol_esit(ayak)

    def test_planda_olmayan_kod_ve_eksik_kart(self):
        with self.assertRaisesMessage(CommandError, "Planda olmayan kod"):
            call_command("varyant_veri_gocu", "--kod", "150-40-0001", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "bulunamayan kodlar: 152-10-0001"):
            call_command("varyant_veri_gocu", "--kod", "152-10-0001", stdout=StringIO())
