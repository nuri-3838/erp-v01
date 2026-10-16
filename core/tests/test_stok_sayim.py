"""Stok Sayımı — dilim 1 (model + servis): belge/numara, depo bazlı varyantlı snapshot, çok kişili TOPLAMSAL giriş (+geçersiz), "sayılmadı" ≠ 0, fark referansı (geç girilen geriye tarihli hareket dahil,
kesmeden sonraki aynı gün hariç), varyant bazlı maliyet, karar kuralları (maliyeti belirsiz kartta Düzelt yok), satır ekleme, silme."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import KdvOrani, Stok, StokHareket, StokSayimGirdisi, StokSayimi, StokSayimSatiri, Varyant
from core.services import stok_sayim as ss
from core.services import varyant as vs
from core.services.birim import birim_olustur
from core.services.depo import depo_olustur
from core.services.hareket import hareket_ekle
from core.services.kategori import kategori_olustur
from core.services.stok import stok_olustur
from core.tarih import tr_bugun

D = Decimal
G, C = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
BUGUN = tr_bugun()
DUN = BUGUN - datetime.timedelta(days=1)
GECEN_AY = BUGUN - datetime.timedelta(days=40)


class SayimTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ust = kategori_olustur(ad="yarı mamul", kod="151")
        cls.alt = kategori_olustur(ad="kesilmiş", kod="10", ust_id=cls.ust.pk)
        cls.alt2 = kategori_olustur(ad="bükülmüş", kod="20", ust_id=cls.ust.pk)
        hust = kategori_olustur(ad="hizmet üst", kod="HZ")
        cls.hizmet_kat = kategori_olustur(ad="hizmetler", kod="10", ust_id=hust.pk, hizmet=True)
        cls.adet = birim_olustur(ad="adet", kisa_ad="ad", ondalik=0)
        cls.depo = depo_olustur(kod="D1", ad="DEPO 1")
        cls.depo2 = depo_olustur(kod="D2", ad="DEPO 2")
        cls.kdv, _ = KdvOrani.objects.get_or_create(oran=D("20"), silindi=False, defaults={"aciklama": "ORAN"})
        cls.semta = Varyant.objects.get(kod="SEMTA")
        cls.yok = Varyant.objects.get(kod="YOK")
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.p1 = User.objects.create_user("p1", password="x")
        cls.p2 = User.objects.create_user("p2", password="x")

    def kart(self, ad, kategori=None, varyantli=False):
        return stok_olustur(ad=ad, kategori_id=(kategori or self.alt).pk, uretim_birimi_id=self.adet.pk, fatura_birimi_id=self.adet.pk, cevirici=D("1"), kdv_id=self.kdv.pk,
                            uretim_urunu=True, varyantli=varyantli, varyant_boyutlari=["MARKALAMA"] if varyantli else [])

    def giris(self, stok, miktar, tutar="100", depo=None, tarih=GECEN_AY, **ek):
        return hareket_ekle(stok_id=stok.pk, depo_id=(depo or self.depo).pk, tarih=tarih, tur=G, miktar=D(miktar), giris_tutar_try=(None if tutar is None else D(tutar)), **ek)

    def cikis(self, stok, miktar, depo=None, tarih=GECEN_AY + datetime.timedelta(days=1), **ek):
        return hareket_ekle(stok_id=stok.pk, depo_id=(depo or self.depo).pk, tarih=tarih, tur=C, miktar=D(miktar), **ek)

    def ac(self, **kw):
        kw.setdefault("depo_id", self.depo.pk)
        kw.setdefault("tarih", BUGUN)
        kw.setdefault("kullanici", self.yon)
        return ss.sayim_ac(**kw)

    def satir(self, sayim, stok, varyant=None):
        return sayim.satirlar.get(stok=stok, varyant=varyant)


class BelgeTest(SayimTaban):
    def test_snapshot_depo_bazli_varyant_bazli_hizmet_haric(self):
        a = self.kart("ayak a")
        b = self.kart("ayak b", kategori=self.alt2)
        v = self.kart("ayak v", varyantli=True)
        hz = self.kart("hizmet", kategori=self.hizmet_kat)
        self.giris(a, "10")
        self.giris(a, "7", depo=self.depo2)                                       # başka depo: kapsam dışı
        self.giris(b, "5")
        self.giris(v, "6")                                                       # SEMTA (varsayılan)
        yok = vs.stok_varyanti_bul_veya_olustur(v, {"MARKALAMA": self.yok})
        self.giris(v, "4", varyant=yok)
        self.cikis(b, "5")                                                       # eldeki 0 → varsayılan olarak satır yok
        s = self.ac()
        self.assertEqual(s.no, f"SY-{BUGUN.year}-0001")
        self.assertEqual((s.depo_id, s.durum, s.kor), (self.depo.pk, "ACIK", False))
        satirlar = {(x.stok.kod, x.varyant_id): x.sistem_snapshot for x in s.satirlar.all()}
        self.assertEqual(satirlar[(a.kod, None)], D("10"))                       # depo 2'deki 7 sayılmaz
        v_semta = v.varsayilan_varyant_id
        self.assertEqual(satirlar[(v.kod, v_semta)], D("6"))
        self.assertEqual(satirlar[(v.kod, yok.pk)], D("4"))
        self.assertNotIn((b.kod, None), satirlar)
        self.assertNotIn((hz.kod, None), satirlar)
        self.assertEqual(len(satirlar), 3)
        self.assertEqual(self.satir(s, a).ort_maliyet_snapshot, D("11.764706"))                     # kart ortalaması tüm depolar için tek: (100 + 100) / 17
        s2 = self.ac(depo_id=self.depo2.pk)                                       # her depo ayrı belge
        self.assertEqual((s2.no, s2.satirlar.count()), (f"SY-{BUGUN.year}-0002", 1))

    def test_kategori_ve_stok_suzgeci_sifirlar_dahil(self):
        a, b = self.kart("a"), self.kart("b", kategori=self.alt2)
        self.giris(a, "3")
        self.giris(b, "2")
        self.cikis(b, "2")
        self.assertEqual({x.stok_id for x in self.ac(kategori_id=self.alt.pk).satirlar.all()}, {a.pk})
        self.assertEqual({x.stok_id for x in self.ac(kategori_id=self.ust.pk).satirlar.all()}, {a.pk})          # üst kategori altları kapsar
        self.assertEqual({x.stok_id for x in self.ac(stok_idler=[b.pk]).satirlar.all()}, set())                 # eldeki 0
        self.assertEqual({x.stok_id for x in self.ac(stok_idler=[b.pk], sifirlar_dahil=True).satirlar.all()}, {b.pk})

    def test_gelecek_tarih_ve_bilinmeyen_depo_reddedilir(self):
        with self.assertRaisesMessage(ss.SayimHatasi, "bugünden sonra"):
            self.ac(tarih=BUGUN + datetime.timedelta(days=1))
        with self.assertRaisesMessage(ss.SayimHatasi, "Depo bulunamadı"):
            self.ac(depo_id=9999)

    def test_snapshot_sonradan_degismez_ve_satir_ekle(self):
        a = self.kart("a")
        self.giris(a, "10")
        s = self.ac()
        self.giris(a, "5", tarih=BUGUN)                                           # kesmeden sonra
        self.assertEqual(self.satir(s, a).sistem_snapshot, D("10"))
        yeni = self.kart("yeni")
        sat = ss.satir_ekle(s, stok_id=yeni.pk, kullanici=self.yon)
        self.assertEqual((sat.sistem_snapshot, sat.eklenen), (D("0"), True))
        with self.assertRaisesMessage(ss.SayimHatasi, "zaten var"):
            ss.satir_ekle(s, stok_id=yeni.pk)
        v = self.kart("v", varyantli=True)
        with self.assertRaisesMessage(ss.SayimHatasi, "varyant seçin"):
            ss.satir_ekle(s, stok_id=v.pk)
        v_semta = v.varsayilan_varyant_id
        self.assertEqual(ss.satir_ekle(s, stok_id=v.pk, varyant_id=v_semta).varyant_id, v_semta)

    def test_kapat_ac_sil(self):
        a = self.kart("a")
        self.giris(a, "10")
        s = self.ac()
        ss.giris_kapat(s, self.yon)
        s.refresh_from_db()
        self.assertEqual(s.durum, "KAPALI")
        with self.assertRaisesMessage(ss.SayimHatasi, "kapalı"):
            ss.girdi_ekle(self.satir(s, a), kullanici=self.p1, miktar="1")
        ss.giris_ac(s, self.yon)
        ss.girdi_ekle(self.satir(s, a), kullanici=self.p1, miktar="1")
        ss.sayim_sil(s, self.yon)
        s.refresh_from_db()
        self.assertTrue(s.silindi)
        s3 = self.ac()
        StokSayimi.objects.filter(pk=s3.pk).update(durum="ONAYLI")
        with self.assertRaisesMessage(ss.SayimHatasi, "silinemez"):
            ss.sayim_sil(s3)


class GirisTest(SayimTaban):
    def kur(self):
        a = self.kart("a")
        self.giris(a, "10")
        s = self.ac()
        return a, s, self.satir(s, a)

    def test_girisler_toplamsal_coklu_kullanici_ve_gecersiz(self):
        a, s, sat = self.kur()
        self.assertEqual(ss.sayilanlar(s), {})                                    # hiç giriş yok → sayılmadı
        g1 = ss.girdi_ekle(sat, kullanici=self.p1, miktar="4")
        g2 = ss.girdi_ekle(sat, kullanici=self.p2, miktar="3,5", not_="raf B")
        self.assertEqual(ss.sayilanlar(s), {sat.pk: D("7.5")})
        self.assertEqual((g2.kullanici_id, g2.aciklama, g2.kaynak), (self.p2.pk, "raf B", "EKRAN"))
        ss.girdi_gecersiz(g1, kullanici=self.yon)                                 # yönetici ilk girişi geçersiz yapar
        self.assertEqual(ss.sayilanlar(s), {sat.pk: D("3.5")})
        g1.refresh_from_db()
        self.assertEqual((g1.gecersiz, g1.gecersiz_yapan_id), (True, self.yon.pk))
        ss.girdi_gecersiz(g1, kullanici=self.yon, gecersiz=False)
        self.assertEqual(ss.sayilanlar(s), {sat.pk: D("7.5")})
        ss.girdi_gecersiz(g1, kullanici=self.yon)
        ss.girdi_gecersiz(g2, kullanici=self.yon)
        self.assertEqual(ss.sayilanlar(s), {})                                    # hepsi geçersiz → yine "sayılmadı" (0 değil)

    def test_gecersiz_miktar_reddedilir(self):
        a, s, sat = self.kur()
        for kotu in ("-1", "abc", ""):
            with self.assertRaisesMessage(ss.SayimHatasi, "geçerli bir sayı" if kotu != "-1" else "negatif"):
                ss.girdi_ekle(sat, kullanici=self.p1, miktar=kotu)
        self.assertEqual(StokSayimGirdisi.objects.count(), 0)
        ss.girdi_ekle(sat, kullanici=self.p1, miktar="0")                         # 0 geçerli bir sayımdır (rafta yok)
        self.assertEqual(ss.sayilanlar(s), {sat.pk: D("0")})


class FarkTest(SayimTaban):
    def test_fark_tutar_ozet_ve_sayilmadi(self):
        a, b, c = self.kart("a"), self.kart("b"), self.kart("c")
        self.giris(a, "10", "1000")                                               # ort 100
        self.giris(b, "20", "400")                                                # ort 20
        self.giris(c, "5", "50")
        s = self.ac()
        ss.girdi_ekle(self.satir(s, a), kullanici=self.p1, miktar="8")           # eksik 2 × 100
        ss.girdi_ekle(self.satir(s, b), kullanici=self.p1, miktar="23")          # fazla 3 × 20
        sonuc = ss.fark_satirlari(s)
        d = {x["stok"].pk: x for x in sonuc["satirlar"]}
        self.assertEqual((d[a.pk]["referans"], d[a.pk]["fark"], d[a.pk]["tutar"], d[a.pk]["belirsiz"]), (D("10"), D("-2"), D("-200.00"), False))
        self.assertEqual((d[b.pk]["fark"], d[b.pk]["tutar"]), (D("3"), D("60.00")))
        self.assertIsNone(d[c.pk]["fark"])                                        # sayılmadı: fark yok (−5 DEĞİL)
        self.assertEqual((sonuc["ozet"]["sayilan_adet"], sonuc["ozet"]["fazla_tutar"], sonuc["ozet"]["eksik_tutar"]), (2, D("60.00"), D("200.00")))
        self.assertEqual(sonuc["ozet"]["duzelt_adet"], 0)

    def test_referans_gec_girilen_geriye_tarihli_dahil_kesmeden_sonraki_ayni_gun_haric(self):
        a = self.kart("a")
        self.giris(a, "10", "1000")
        s = self.ac(tarih=BUGUN)                                                  # sayım tarihi = bugün = kesme günü
        self.giris(a, "3", "300", tarih=DUN)                                      # kesmeden sonra girilmiş, ama DÜN tarihli → sayım tarihi itibarıyla sistemde vardı
        self.giris(a, "5", "500", tarih=BUGUN)                                    # kesmeden sonra girilen bugünkü hareket → sayımdan SONRA
        d = ss.fark_satirlari(s)["satirlar"][0]
        self.assertEqual((d["snapshot"], d["referans"], d["degisen"]), (D("10"), D("13"), D("3")))
        ss.girdi_ekle(self.satir(s, a), kullanici=self.p1, miktar="13")
        self.assertEqual(ss.fark_satirlari(s)["satirlar"][0]["fark"], D("0"))

    def test_gecmis_tarihli_sayimda_sonraki_hareketler_haric_gec_girilenler_dahil(self):
        a = self.kart("a")
        self.giris(a, "10", "1000", tarih=GECEN_AY)
        s = self.ac(tarih=DUN)                                                    # dünün sayımı, bugün açıldı
        self.giris(a, "2", "200", tarih=DUN)                                      # dünkü geç giriş: dahil
        self.giris(a, "9", "900", tarih=BUGUN)                                    # bugünkü: sayımdan sonra → hariç
        d = ss.fark_satirlari(s)["satirlar"][0]
        self.assertEqual(d["referans"], D("12"))

    def test_varyant_bazli_maliyet_ve_fark(self):
        v = self.kart("v", varyantli=True)
        yok = vs.stok_varyanti_bul_veya_olustur(v, {"MARKALAMA": self.yok})
        self.giris(v, "10", "1000")                                               # SEMTA @100
        self.giris(v, "10", "2000", varyant=yok)                                  # YOK @200
        s = self.ac()
        ss.girdi_ekle(self.satir(s, v, v.varsayilan_varyant_id), kullanici=self.p1, miktar="9")
        ss.girdi_ekle(self.satir(s, v, yok), kullanici=self.p1, miktar="9")
        d = {x["varyant"].pk: x for x in ss.fark_satirlari(s)["satirlar"]}
        self.assertEqual((d[v.varsayilan_varyant_id]["tutar"], d[yok.pk]["tutar"]), (D("-100.00"), D("-200.00")))      # her varyantın KENDİ ortalaması

    def test_belirsiz_maliyet_isaretlenir(self):
        fiyatsiz = self.kart("fiyatsiz")
        self.giris(fiyatsiz, "5", tutar=None)                                     # irsaliye benzeri fiyatsız giriş
        iyi = self.kart("iyi")
        self.giris(iyi, "5", "50")
        s = self.ac()
        for st in (fiyatsiz, iyi):
            ss.girdi_ekle(self.satir(s, st), kullanici=self.p1, miktar="4")
        d = {x["stok"].pk: x for x in ss.fark_satirlari(s)["satirlar"]}
        self.assertTrue(d[fiyatsiz.pk]["belirsiz"])
        self.assertTrue(d[fiyatsiz.pk]["neden"])
        self.assertFalse(d[iyi.pk]["belirsiz"])
        self.assertEqual(ss.fark_satirlari(s)["ozet"]["belirsiz_adet"], 1)


class KararTest(SayimTaban):
    def kur(self):
        a = self.kart("a")
        self.giris(a, "10", "1000")
        s = self.ac()
        return a, s, self.satir(s, a)

    def test_duzelt_kosullari(self):
        a, s, sat = self.kur()
        with self.assertRaisesMessage(ss.SayimHatasi, "sayılmadı"):
            ss.karar_ver(sat, "DUZELT", kullanici=self.yon)
        ss.girdi_ekle(sat, kullanici=self.p1, miktar="10")
        with self.assertRaisesMessage(ss.SayimHatasi, "fark yok"):
            ss.karar_ver(sat, "DUZELT", kullanici=self.yon)
        ss.girdi_ekle(sat, kullanici=self.p2, miktar="2")                          # toplam 12 → fark +2
        k = ss.karar_ver(sat, "DUZELT", kullanici=self.yon, not_="onaylandı")
        self.assertEqual((k.karar, k.karar_notu, k.karar_veren_id), ("DUZELT", "onaylandı", self.yon.pk))
        o = ss.fark_satirlari(s)["ozet"]
        self.assertEqual((o["duzelt_adet"], o["duzelt_fazla_tutar"], o["duzelt_eksik_tutar"]), (1, D("200.00"), D("0")))
        k = ss.karar_ver(sat, "ARASTIR", kullanici=self.yon)
        self.assertEqual(ss.fark_satirlari(s)["ozet"]["duzelt_adet"], 0)           # araştır satırı düzeltme toplamına girmez
        self.assertEqual(ss.karar_ver(sat, "BEKLIYOR", kullanici=self.yon).karar_veren_id, None)
        with self.assertRaisesMessage(ss.SayimHatasi, "Geçersiz karar"):
            ss.karar_ver(sat, "X", kullanici=self.yon)

    def test_maliyeti_belirsiz_kartta_duzelt_yok_arastir_var(self):
        f = self.kart("fiyatsiz")
        self.giris(f, "5", tutar=None)
        s = self.ac()
        sat = self.satir(s, f)
        ss.girdi_ekle(sat, kullanici=self.p1, miktar="3")
        with self.assertRaisesMessage(ss.SayimHatasi, "Düzelt verilemez"):
            ss.karar_ver(sat, "DUZELT", kullanici=self.yon)
        self.assertEqual(ss.karar_ver(sat, "ARASTIR", kullanici=self.yon).karar, "ARASTIR")

    def test_onayli_belgede_karar_degismez(self):
        a, s, sat = self.kur()
        ss.girdi_ekle(sat, kullanici=self.p1, miktar="9")
        StokSayimi.objects.filter(pk=s.pk).update(durum="ONAYLI")
        with self.assertRaisesMessage(ss.SayimHatasi, "düzenlenemez"):
            ss.karar_ver(sat, "ARASTIR", kullanici=self.yon)
        with self.assertRaisesMessage(ss.SayimHatasi, "kapalı ya da"):
            ss.girdi_ekle(sat, kullanici=self.p1, miktar="1")
