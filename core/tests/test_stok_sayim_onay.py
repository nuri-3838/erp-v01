"""Stok Sayımı — dilim 3 (onay / geri alma / maliyet kancası): yalnız Düzelt satırları → SAYIM hareketi (eksik çıkış / fazla giriş, ortalama maliyet, tarih = sayım tarihi) + TEK fiş (STOK_SAYIM):
eksik 62x BORÇ / stok hesabı ALACAK, fazla tersi; 62x = satış maliyeti kuralı (152→620, 153→621, 150/151→623), her hesap için yönde tek satır, stok hesapları stok bazında. Atomiklik, geri alma,
tek başına hareket/fiş silinemez, geç gelen fatura → fiş yerinde yenilenir."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (Birim, Depo, Fatura, FaturaTipi, HesapPlani, Kategori, KategoriHesap, KdvOrani, Kur, Stok, StokHareket, StokSayimi, Varyant, YevmiyeFisi)
from core.services import stok_sayim as ss
from core.services import varyant as vs
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services.stok import stok_olustur
from core.tarih import tr_bugun

D = Decimal
G, C = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
BUGUN = tr_bugun()
DUN = BUGUN - datetime.timedelta(days=1)
GECEN_AY = BUGUN - datetime.timedelta(days=40)


class OnayTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.p1 = User.objects.create_user("p1", password="x")
        for gun in range(0, 61):
            Kur.objects.get_or_create(tarih=BUGUN - datetime.timedelta(days=gun), defaults={"usd_alis": D("30")})
        for kod, ad, grup, kalem in (("150.10", "ALÜMİNYUM", "BILANCO", "DV"), ("151.10", "KESİLMİŞ", "BILANCO", "DV"), ("152.10", "MAMUL", "BILANCO", "DV"), ("153.10", "TİCARİ", "BILANCO", "DV"),
                                     ("620", "SATILAN MAMULLER MALİYETİ", "GELIR_TABLOSU", "C"), ("621", "SATILAN TİCARİ MALLAR MALİYETİ", "GELIR_TABLOSU", "C"),
                                     ("623", "DİĞER SATIŞLARIN MALİYETİ", "GELIR_TABLOSU", "C"), ("191", "KDV", "BILANCO", "DV"), ("391", "HKDV", "BILANCO", "KVYK"), ("320.30.0001", "TED", "BILANCO", "KVYK")):
            HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem, parasal=True, aktif=True)
        cls.kdv = KdvOrani.objects.create(aciklama="G", oran=D("20"), hesap_borc=HesapPlani.objects.get(hesap_kodu="191"), hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.tip = FaturaTipi.objects.create(ad="ALIŞ S", yon="ALIS")
        cls.adet = Birim.objects.create(ad="ADET-S", kisa_ad="ADT", ondalik=0)
        cls.kat = {}
        for kok, hesap in (("150", "150.10"), ("151", "151.10"), ("152", "152.10"), ("153", "153.10")):
            ust = Kategori.objects.create(ad=f"ÜST {kok}", kod=kok)
            alt = Kategori.objects.create(ad=f"ALT {kok}", kod="10", ust=ust)
            KategoriHesap.objects.create(kategori=alt, fatura_tipi=cls.tip, hesap=HesapPlani.objects.get(hesap_kodu=hesap))
            cls.kat[kok] = alt
        cls.depo = Depo.objects.create(kod="D1", ad="DEPO 1")
        cls.n = [0]

    def kart(self, kok, ad=None, varyantli=False):
        self.n[0] += 1
        return stok_olustur(ad=ad or f"kart {kok} {self.n[0]}", kategori_id=self.kat[kok].pk, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk, cevirici=D("1"), kdv_id=self.kdv.pk,
                            uretim_urunu=True, varyantli=varyantli, varyant_boyutlari=["MARKALAMA"] if varyantli else [])

    def giris(self, stok, miktar, tutar, tarih=GECEN_AY, **ek):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih, tur=G, miktar=D(miktar), giris_tutar_try=D(tutar), **ek)

    def sayim(self, sayilanlar, tarih=BUGUN, kararlar=None):
        """{stok: sayılan} → belge açılır, girişler yapılır, karar (varsayılan Düzelt), giriş kapatılır. Döner: sayım."""
        s = ss.sayim_ac(depo_id=self.depo.pk, tarih=tarih, kullanici=self.yon)
        for (stok, varyant), miktar in sayilanlar.items():
            sat = s.satirlar.get(stok=stok, varyant_id=varyant)
            ss.girdi_ekle(sat, kullanici=self.p1, miktar=miktar)
            ss.karar_ver(sat, (kararlar or {}).get((stok, varyant), "DUZELT"), kullanici=self.yon)
        ss.giris_kapat(s, self.yon)
        return s

    def fis_satirlari(self, fis):
        return [(x.hesap_id, x.borc, x.alacak, x.aciklama) for x in fis.satirlar.filter(silindi=False).order_by("id")]


class OnayTest(OnayTaban):
    def test_eksik_ve_fazla_hareket_ve_tek_fis_62x_kurali(self):
        a = self.kart("151")                                   # 10 @100 → sayılan 8: EKSİK 2 (200 TL), 151 → 623
        b = self.kart("152")                                   # 5 @200 → sayılan 7: FAZLA 2 (400 TL), 152 → 620
        c = self.kart("153")                                   # 4 @50 → sayılan 3: EKSİK 1 (50 TL), 153 → 621
        for st, m, t in ((a, "10", "1000"), (b, "5", "1000"), (c, "4", "200")):
            self.giris(st, m, t)
        s = self.sayim({(a, None): "8", (b, None): "7", (c, None): "3"})
        s = ss.onayla(s, self.yon)
        s.refresh_from_db()
        self.assertEqual((s.durum, s.onaylayan_id), ("ONAYLI", self.yon.pk))
        hareketler = {h.stok_id: h for h in StokHareket.objects.filter(kaynak="SAYIM", silindi=False)}
        self.assertEqual({st.pk: (h.tur, h.miktar) for st, h in ((a, hareketler[a.pk]), (b, hareketler[b.pk]), (c, hareketler[c.pk]))},
                         {a.pk: (C, D("2")), b.pk: (G, D("2")), c.pk: (C, D("1"))})
        h = hareketler[a.pk]
        self.assertEqual((h.tarih, h.depo_id, h.tutar_try, h.fis_id, h.sayim_satir.sayim_id), (BUGUN, self.depo.pk, D("200.00"), s.fis_id, s.pk))
        self.assertEqual(hareketler[b.pk].tutar_try, D("400.00"))                     # fazla: ortalama (200) ile
        self.assertEqual((eldeki_miktar(a), eldeki_miktar(b), eldeki_miktar(c)), (D("8"), D("7"), D("3")))
        fis = s.fis
        self.assertEqual((fis.kaynak, fis.tarih, YevmiyeFisi.objects.filter(kaynak="STOK_SAYIM").count()), ("STOK_SAYIM", BUGUN, 1))          # TEK fiş
        satirlar = self.fis_satirlari(fis)
        by = {x[0]: x for x in satirlar}
        self.assertEqual((by["151.10"][1], by["151.10"][2]), (D("0"), D("200.00")))   # eksik: stok hesabı ALACAK
        self.assertEqual((by["152.10"][1], by["152.10"][2]), (D("400.00"), D("0")))   # fazla: stok hesabı BORÇ
        self.assertEqual((by["153.10"][1], by["153.10"][2]), (D("0"), D("50.00")))
        self.assertEqual((by["623"][1], by["623"][2]), (D("200.00"), D("0")))         # 150/151 → 623 BORÇ
        self.assertEqual((by["620"][1], by["620"][2]), (D("0"), D("400.00")))         # 152 fazla → 620 ALACAK
        self.assertEqual((by["621"][1], by["621"][2]), (D("50.00"), D("0")))          # 153 → 621 BORÇ
        self.assertEqual(sum(x[1] for x in satirlar), sum(x[2] for x in satirlar))    # dengeli
        self.assertEqual(len(satirlar), 6)                                            # stok bazında 3 + 62x yönde tek satır 3
        self.assertTrue(all(s.no in x[3] for x in satirlar))                          # açıklamada SY no
        self.assertIn(a.kod, by["151.10"][3])
        self.assertFalse(any(x[0] in ("197", "397") for x in satirlar))              # ara hesap yok
        sat_a = s.satirlar.get(stok=a)
        self.assertEqual((sat_a.onay_referans, sat_a.onay_sayilan, sat_a.onay_fark, sat_a.onay_tutar_try), (D("10"), D("8"), D("-2"), D("-200.00")))
        self.assertEqual(s.satirlar.get(stok=b).onay_tutar_try, D("400.00"))

    def test_ayni_62x_hesabinda_iki_yon_ayri_tek_satir(self):
        a, b, c = self.kart("151"), self.kart("151"), self.kart("150")
        for st in (a, b, c):
            self.giris(st, "10", "1000")
        s = self.sayim({(a, None): "9", (b, None): "12", (c, None): "7"})           # a −1 (100), b +2 (200), c −3 (300): hepsi 623
        s = ss.onayla(s, self.yon)
        by = {}
        for x in self.fis_satirlari(s.fis):
            by.setdefault(x[0], []).append(x)
        self.assertEqual(len(by["623"]), 2)                                         # bir BORÇ (eksik 400) + bir ALACAK (fazla 200)
        self.assertEqual(sorted((x[1], x[2]) for x in by["623"]), [(D("0"), D("200.00")), (D("400.00"), D("0"))])
        self.assertEqual(len(by["151.10"]) + len(by["150.10"]), 3)                  # stok hesabı satırları stok bazında

    def test_arastir_ve_bekliyor_islenmez_onayda_alinan_degerler_kaydedilir(self):
        a, b, c = self.kart("151"), self.kart("151"), self.kart("151")
        for st in (a, b, c):
            self.giris(st, "10", "1000")
        s = self.sayim({(a, None): "8", (b, None): "7", (c, None): "9"}, kararlar={(a, None): "DUZELT", (b, None): "ARASTIR", (c, None): "BEKLIYOR"})
        s = ss.onayla(s, self.yon)
        self.assertEqual(list(StokHareket.objects.filter(kaynak="SAYIM").values_list("stok_id", flat=True)), [a.pk])
        self.assertEqual((eldeki_miktar(b), eldeki_miktar(c)), (D("10"), D("10")))
        sb = s.satirlar.get(stok=b)
        self.assertEqual((sb.karar, sb.onay_fark, sb.onay_tutar_try), ("ARASTIR", D("-3"), None))      # fark kaydı var, düzeltme yok
        self.assertEqual(sum(x[1] for x in self.fis_satirlari(s.fis)), sum(x[2] for x in self.fis_satirlari(s.fis)))

    def test_onay_kosullari(self):
        a = self.kart("151")
        self.giris(a, "10", "1000")
        s = ss.sayim_ac(depo_id=self.depo.pk, tarih=BUGUN, kullanici=self.yon)
        with self.assertRaisesMessage(ss.SayimHatasi, "girişi kapatılmış"):
            ss.onayla(s, self.yon)                                                    # giriş açıkken onay yok
        ss.giris_kapat(s, self.yon)
        with self.assertRaisesMessage(ss.SayimHatasi, "Düzelt"):
            ss.onayla(s, self.yon)                                                    # Düzelt satırı yok
        ss.onayla(self.sayim({(a, None): "8"}), self.yon)
        s2 = self.sayim({(a, None): "7"})
        ss.onayla(s2, self.yon)
        s2.refresh_from_db()
        with self.assertRaisesMessage(ss.SayimHatasi, "girişi kapatılmış"):
            ss.onayla(s2, self.yon)                                                   # iki kez onay yok

    def test_atomik_hesap_eslemesi_eksik_kartta_hicbir_sey_yazilmaz(self):
        a = self.kart("151")
        self.giris(a, "10", "1000")
        ust = Kategori.objects.create(ad="ÜST 999", kod="999")
        alt = Kategori.objects.create(ad="ALT 999", kod="10", ust=ust)               # alış fatura tipinde hesap eşlemesi YOK
        b = stok_olustur(ad="eşlemesiz", kategori_id=alt.pk, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk, cevirici=D("1"), kdv_id=self.kdv.pk, uretim_urunu=True)
        self.giris(b, "5", "500")
        s = self.sayim({(a, None): "8", (b, None): "4"})
        with self.assertRaisesMessage(ss.SayimHatasi, "Onaylanamadı"):
            ss.onayla(s, self.yon)
        self.assertEqual((StokHareket.objects.filter(kaynak="SAYIM").count(), YevmiyeFisi.objects.filter(kaynak="STOK_SAYIM").count(), eldeki_miktar(a)), (0, 0, D("10")))
        s.refresh_from_db()
        self.assertEqual(s.durum, "KAPALI")

    def test_sayim_tarihinden_sonraki_hareketler_negatife_dusuyorsa_atomik_red(self):
        a = self.kart("151")
        self.giris(a, "10", "1000")
        s = self.sayim({(a, None): "6"}, tarih=DUN)                                    # dünkü sayım: sistem 10, sayılan 6 → eksik 4
        hareket_ekle(stok_id=a.pk, depo_id=self.depo.pk, tarih=BUGUN, tur=C, miktar=D("8"))                   # bugün 8 çıktı (eldeki 2)
        with self.assertRaisesMessage(ss.SayimHatasi, "negatif stoğa"):
            ss.onayla(s, self.yon)
        self.assertEqual((StokHareket.objects.filter(kaynak="SAYIM").count(), YevmiyeFisi.objects.filter(kaynak="STOK_SAYIM").count(), eldeki_miktar(a)), (0, 0, D("2")))

    def test_usd_kuru_olmayan_gunde_onay_temiz_hata_verir(self):
        a = self.kart("151")
        self.giris(a, "10", "1000", tarih=BUGUN - datetime.timedelta(days=100))
        eski = BUGUN - datetime.timedelta(days=90)                                   # kur kaydı yok
        s = self.sayim({(a, None): "8"}, tarih=eski)
        with self.assertRaises(ss.SayimHatasi):
            ss.onayla(s, self.yon)
        self.assertEqual((StokHareket.objects.filter(kaynak="SAYIM").count(), eldeki_miktar(a)), (0, D("10")))

    def test_varyantli_kart_varyant_bazli(self):
        v = self.kart("151", varyantli=True)
        yok = vs.stok_varyanti_bul_veya_olustur(v, {"MARKALAMA": Varyant.objects.get(kod="YOK")})
        self.giris(v, "10", "1000")                                                  # SEMTA @100
        self.giris(v, "10", "2000", varyant=yok)                                     # YOK @200
        s = self.sayim({(v, v.varsayilan_varyant_id): "9", (v, yok.pk): "10"}, kararlar={(v, yok.pk): "BEKLIYOR"})
        s = ss.onayla(s, self.yon)
        (h,) = StokHareket.objects.filter(kaynak="SAYIM")
        self.assertEqual((h.varyant_id, h.tur, h.miktar, h.tutar_try), (v.varsayilan_varyant_id, C, D("1"), D("100.00")))      # SEMTA'nın kendi ortalaması
        self.assertEqual(eldeki_miktar(v, varyant=yok.pk), D("10"))


class GeriAlmaVeKoruTest(OnayTaban):
    def kur(self):
        a, b = self.kart("151"), self.kart("152")
        self.giris(a, "10", "1000")
        self.giris(b, "5", "1000")
        s = self.sayim({(a, None): "8", (b, None): "7"})
        s = ss.onayla(s, self.yon)
        return a, b, s

    def test_geri_alma_hareketleri_ve_fisi_geri_sarar(self):
        a, b, s = self.kur()
        fis_pk = s.fis_id
        ss.geri_al(s, self.yon)
        s.refresh_from_db()
        self.assertEqual((s.durum, s.geri_alan_id), ("GERI_ALINDI", self.yon.pk))
        self.assertEqual((eldeki_miktar(a), eldeki_miktar(b)), (D("10"), D("5")))
        self.assertEqual(StokHareket.objects.filter(kaynak="SAYIM", silindi=False).count(), 0)
        self.assertEqual(StokHareket.objects.filter(kaynak="SAYIM", silindi=True).count(), 2)               # soft: iz kalır
        self.assertTrue(YevmiyeFisi.objects.get(pk=fis_pk).silindi)
        a.refresh_from_db()
        self.assertEqual(a.ort_maliyet_try, D("100"))                                                        # ortalama eski haline
        with self.assertRaisesMessage(ss.SayimHatasi, "onaylı sayım"):
            ss.geri_al(s, self.yon)                                                                         # iki kez geri alma yok
        with self.assertRaisesMessage(ss.SayimHatasi, "girişi kapatılmış"):
            ss.onayla(s, self.yon)                                                                          # geri alınan belge yeniden onaylanmaz

    def test_fazla_girisi_sonradan_tuketilmisse_geri_alma_reddedilir_hicbir_sey_degismez(self):
        a, b, s = self.kur()                                                        # b: +2 fazla giriş
        hareket_ekle(stok_id=b.pk, depo_id=self.depo.pk, tarih=BUGUN, tur=C, miktar=D("6"))                  # fazla 2'yi de tüketen çıkış (eldeki 7 → 1)
        with self.assertRaisesMessage(ss.SayimHatasi, "Geri alınamadı"):
            ss.geri_al(s, self.yon)
        s.refresh_from_db()
        self.assertEqual(s.durum, "ONAYLI")
        self.assertEqual(StokHareket.objects.filter(kaynak="SAYIM", silindi=False).count(), 2)
        self.assertFalse(YevmiyeFisi.objects.get(pk=s.fis_id).silindi)
        self.assertEqual(eldeki_miktar(a), D("8"))

    def test_sayim_hareketi_ve_fisi_tek_basina_degistirilemez(self):
        a, b, s = self.kur()
        h = StokHareket.objects.filter(kaynak="SAYIM").first()
        with self.assertRaisesMessage(HareketHatasi, "Sayım hareketi tek başına silinemez"):
            hareket_sil(h)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:stok_hareket_sil", args=[h.pk]), follow=True)
        self.assertContains(r, "Sayım hareketi tek başına silinemez")
        h.refresh_from_db()
        self.assertFalse(h.silindi)
        r = self.client.get(reverse("core:fis_duzenle", args=[s.fis_id]), follow=True)
        self.assertContains(r, "elle düzenlenemez")
        r = self.client.post(reverse("core:fis_sil", args=[s.fis_id]), follow=True)
        self.assertContains(r, "sayım belgesinde onayı geri alın")
        self.assertFalse(YevmiyeFisi.objects.get(pk=s.fis_id).silindi)


class MaliyetKancasiTest(OnayTaban):
    def test_gec_gelen_giris_fatura_degerini_degistirirse_fis_yerinde_yenilenir(self):
        a = self.kart("151")
        self.giris(a, "10", "1000")                                                  # ort 100
        s = self.sayim({(a, None): "8"})
        s = ss.onayla(s, self.yon)
        h = StokHareket.objects.get(kaynak="SAYIM")
        self.assertEqual(h.tutar_try, D("200.00"))
        fis_pk = s.fis_id
        self.giris(a, "10", "2000", tarih=GECEN_AY + datetime.timedelta(days=5))     # geç girilen, sayımdan ÖNCEKİ tarihli pahalı giriş → ort 150
        h.refresh_from_db()
        self.assertEqual(h.tutar_try, D("300.00"))                                   # 2 × 150
        s.refresh_from_db()
        self.assertEqual(s.fis_id, fis_pk)                                           # AYNI fiş, yerinde güncellendi
        satirlar = self.fis_satirlari(s.fis)
        by = {x[0]: x for x in satirlar}
        self.assertEqual((by["151.10"][2], by["623"][1]), (D("300.00"), D("300.00")))
        self.assertEqual(sum(x[1] for x in satirlar), sum(x[2] for x in satirlar))
        self.assertEqual(s.satirlar.get(stok=a).onay_tutar_try, D("-300.00"))
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="STOK_SAYIM", silindi=False).count(), 1)
