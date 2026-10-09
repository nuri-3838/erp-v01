"""Operasyonda YAN ÇIKTI + boy oranına göre maliyet paylaştırma: tanım kısıtları, onayda ana + yan GİRİŞ, TL/USD paylaştırma (yuvarlama farkı ana
çıktıda), maliyet aktarım fişi, sonradan değişen girdi maliyetinin yeniden paylaştırılması, onaylı kaydın geri alınması, ihtiyaç hesabında yalnız
bilgi, ekranlar; yan çıktısız operasyonlar birebir aynı."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Birim, EkranYetki, FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur, Stok, StokHareket,
)
from core.services import stok_ortalama
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, ihtiyac_hesapla, operasyon_guncelle, operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_kaydi_sil,
    operasyon_olustur, operasyon_yan_ciktilari,
)
from core.tests.test_uretim import _depo, _istasyon

D = Decimal


def _hesap(kod, ad):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)


class YanCiktiBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (date(2026, 1, 1), date(2026, 10, 9), date(2026, 10, 10)):
            Kur.objects.create(tarih=g, usd_alis=D("40"))
        cls.adet = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.boy = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        ft = FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS")
        # girdi (150.10) / ana çıktı (151.10) / yan çıktı (151.20) AYRI stok hesaplarında → maliyet aktarım fişi görünür
        cls.kat_girdi = Kategori.objects.create(kod="UG", ad="ÜT GİRDİ")
        cls.kat_ana = Kategori.objects.create(kod="UA", ad="ÜT ANA")
        cls.kat_yan = Kategori.objects.create(kod="UY", ad="ÜT YAN")
        for kat, kod in ((cls.kat_girdi, "150.10"), (cls.kat_ana, "151.10"), (cls.kat_yan, "151.20")):
            KategoriHesap.objects.create(kategori=kat, hesap=_hesap(kod, f"STOK {kod}"), fatura_tipi=ft)
        cls.depo = _depo()
        cls.kesim = _istasyon("KESIM")
        cls.profil = cls._stok("PROFIL", cls.kat_girdi, cls.boy, satinalma=True)
        cls.ana = cls._stok("AYAK66", cls.kat_ana)                    # 6+6 SAĞ (3 ad/boy, 1292,60 mm)
        cls.yan = cls._stok("AYAK55", cls.kat_yan)                    # 5+5 SAĞ (yan çıktı, 1063,53 mm)
        cls.op = operasyon_olustur(
            istasyon_id=cls.kesim.pk, cikti_id=cls.ana.pk, cikti_miktar=D("3"), satirlar=[(cls.profil, D("1"))], boy_mm=D("1292.60"), yan_ciktilar=[(cls.yan, D("1"), D("1063.53"))])

    @classmethod
    def _stok(cls, kod, kat, birim=None, **kw):
        return Stok.objects.create(kod=kod, ad=kod.lower(), kategori=kat, uretim_birimi=birim or cls.adet, fatura_birimi=birim or cls.adet,
                                   uretim_urunu=kw.get("uretim", True), satinalma_urunu=kw.get("satinalma", False) or not kw.get("uretim", True), satis_urunu=False)

    def profil_gir(self, miktar, tl, usd=None):
        return hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS,
                            miktar=D(str(miktar)), giris_tutar_try=D(str(tl)), giris_tutar_usd=(D(str(usd)) if usd is not None else None))

    def kayit(self, hedef="3", op=None):
        return operasyon_kaydi_olustur(operasyon_id=(op or self.op).pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D(hedef))

    def girisler(self, kayit):
        return {h.stok.kod: h for h in StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}


class KisitTest(YanCiktiBase):
    def yeni_op(self, cikti, yan, girdi=None, boy="100", **kw):
        return operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=cikti.pk, cikti_miktar=D("1"), satirlar=[((girdi or self.profil), D("1"))],
                                 boy_mm=D(boy) if boy else None, yan_ciktilar=yan, **kw)

    def test_yan_cikti_ana_cikti_ile_ayni_olamaz(self):
        c = self._stok("C1", self.kat_ana)
        with self.assertRaisesMessage(UretimHatasi, "ana çıktıyla aynı"):
            self.yeni_op(c, [(c, D("1"), D("50"))])

    def test_yan_cikti_operasyonun_girdisi_olamaz(self):
        c = self._stok("C2", self.kat_ana)
        with self.assertRaisesMessage(UretimHatasi, "girdisi"):
            self.yeni_op(c, [(self.profil, D("1"), D("50"))])

    def test_ayni_yan_cikti_tekrar_edemez(self):
        c = self._stok("C3", self.kat_ana)
        y = self._stok("Y3", self.kat_yan)
        with self.assertRaisesMessage(UretimHatasi, "tekrarlanamaz"):
            self.yeni_op(c, [(y, D("1"), D("50")), (y, D("2"), D("40"))])

    def test_yan_cikti_varsa_ana_boy_zorunlu(self):
        c = self._stok("C4", self.kat_ana)
        y = self._stok("Y4", self.kat_yan)
        with self.assertRaisesMessage(UretimHatasi, "ana çıktının boyu"):
            self.yeni_op(c, [(y, D("1"), D("50"))], boy=None)

    def test_miktar_ve_boy_pozitif(self):
        c = self._stok("C5", self.kat_ana)
        y = self._stok("Y5", self.kat_yan)
        for miktar, boy in ((D("0"), D("5")), (D("1"), D("0")), (D("1"), None)):
            with self.assertRaises(UretimHatasi):
                self.yeni_op(c, [(y, miktar, boy)])

    def test_stok_baska_operasyonun_ana_cikti_olup_bu_operasyonun_yan_ciktisi_olabilir(self):
        """5+5 SAĞ'ın kendi operasyonu da var; 6+6'nın yan çıktısı da — 'çıktı başına 1 operasyon' yalnız ANA çıktı için."""
        self.yeni_op(self.yan, [], girdi=self.profil, boy="1063.53")                    # AYAK55'in kendi (ana çıktı) operasyonu
        self.assertEqual(self.op.yan_ciktilar.filter(silindi=False, stok=self.yan).count(), 1)     # setUp'taki yan çıktı bağı duruyor

    def test_yan_cikti_uretim_urunu_olmali(self):
        c = self._stok("C6", self.kat_ana)
        y = self._stok("Y6", self.kat_yan, uretim=False)
        with self.assertRaisesMessage(UretimHatasi, "üretim ürünü değil"):
            self.yeni_op(c, [(y, D("1"), D("50"))])

    def test_guncelle_koru_degistir_sil(self):
        op = self.op
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))])
        self.assertEqual((op.boy_mm, op.yan_ciktilar.filter(silindi=False).count()), (D("1292.60"), 1))                    # korunur
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))],
                           yan_ciktilar=[(self.yan, D("2"), D("1000"))])
        y = operasyon_yan_ciktilari(op).get()
        self.assertEqual((y.miktar, y.boy_mm), (D("2"), D("1000")))
        with self.assertRaises(UretimHatasi):                                                                             # ana boy silinemez
            operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))], boy_mm="")
        operasyon_guncelle(op, istasyon_id=self.kesim.pk, cikti_miktar=D("3"), satirlar=[(self.profil, D("1"))], yan_ciktilar=[], boy_mm="")
        op.refresh_from_db()
        self.assertEqual((op.boy_mm, op.yan_ciktilar.filter(silindi=False).count()), (None, 0))


class OnayVeMaliyetTest(YanCiktiBase):
    def test_onayda_ana_ve_yan_giris_miktarlari(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")                                                  # 1 çalıştırma (1 boy)
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        self.assertEqual((g["AYAK66"].miktar, g["AYAK55"].miktar), (D("3"), D("1")))
        self.assertEqual(eldeki_miktar(self.ana, self.depo), D("3"))
        self.assertEqual(eldeki_miktar(self.yan, self.depo), D("1"))
        self.assertEqual(eldeki_miktar(self.profil, self.depo), D("9"))

    def test_iki_calistirma_yan_miktar_carpilir(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("6")
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        self.assertEqual((g["AYAK66"].miktar, g["AYAK55"].miktar), (D("6"), D("2")))

    def test_maliyet_boy_oranina_gore_tl_ve_usd_toplam_korunur(self):
        self.profil_gir(10, 10000, 250)                                      # birim 1.000 TL / 25 USD
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        a, y = 3 * D("1292.60"), D("1063.53")                                # 3877,80 ve 1063,53
        beklenen_yan_try = (D("1000") * y / (a + y)).quantize(D("0.01"))     # ≈ %21,5
        beklenen_yan_usd = (D("25") * y / (a + y)).quantize(D("0.01"))
        self.assertEqual(g["AYAK55"].tutar_try, beklenen_yan_try)
        self.assertEqual(g["AYAK66"].tutar_try, D("1000.00") - beklenen_yan_try)             # fark ana çıktıda
        self.assertEqual(g["AYAK55"].tutar_usd, beklenen_yan_usd)
        self.assertEqual(g["AYAK66"].tutar_usd, D("25.00") - beklenen_yan_usd)
        self.assertEqual(g["AYAK55"].tutar_try + g["AYAK66"].tutar_try, D("1000.00"))        # toplam BİREBİR
        self.assertEqual(g["AYAK55"].tutar_usd + g["AYAK66"].tutar_usd, D("25.00"))
        self.assertAlmostEqual(float(g["AYAK55"].tutar_try / D("1000")), 0.2152, places=3)

    def test_yuvarlama_farki_ana_cikti(self):
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=self._stok("ANA3", self.kat_ana).pk, cikti_miktar=D("1"),
                               satirlar=[(self.profil, D("1"))], boy_mm=D("100"),
                               yan_ciktilar=[(self._stok("YA", self.kat_yan), D("1"), D("100")), (self._stok("YB", self.kat_yan), D("1"), D("100"))])
        self.profil_gir(1, "100.00", "3.33")
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("1"))
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        self.assertEqual(g["YA"].tutar_try, D("33.33"))
        self.assertEqual(g["YB"].tutar_try, D("33.33"))
        self.assertEqual(g["ANA3"].tutar_try, D("33.34"))                                  # 100,00 − 33,33 − 33,33 (fark anada)
        self.assertEqual(g["YA"].tutar_usd + g["YB"].tutar_usd + g["ANA3"].tutar_usd, D("3.33"))

    def test_maliyet_aktarim_fisi_dengeli_ve_kapsar(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        k.refresh_from_db()
        fis = k.fis
        self.assertIsNotNone(fis)
        satirlar = {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak) for s in fis.satirlar.filter(silindi=False)}
        g = self.girisler(k)
        self.assertEqual(satirlar[("150.10", "A")], D("1000.00"))
        self.assertEqual(satirlar[("151.10", "B")], g["AYAK66"].tutar_try)
        self.assertEqual(satirlar[("151.20", "B")], g["AYAK55"].tutar_try)                   # yan çıktı fişte
        self.assertEqual(sum(s.borc for s in fis.satirlar.filter(silindi=False)), sum(s.alacak for s in fis.satirlar.filter(silindi=False)))

    def test_sonradan_degisen_girdi_maliyeti_yeniden_paylastirilir(self):
        h = self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        StokHareket.objects.filter(pk=h.pk).update(giris_tutar_try=D("20000"), giris_tutar_usd=D("500"))      # geç gelen fatura: maliyet 2×
        stok_ortalama.yeniden_hesapla(self.profil)
        g = self.girisler(k)
        for x in g.values():
            x.refresh_from_db()
        self.assertEqual(g["AYAK55"].tutar_try + g["AYAK66"].tutar_try, D("2000.00"))
        self.assertAlmostEqual(float(g["AYAK55"].tutar_try / D("2000")), 0.2152, places=3)
        k.refresh_from_db()
        satirlar = {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak) for s in k.fis.satirlar.filter(silindi=False)}
        self.assertEqual(satirlar[("150.10", "A")], D("2000.00"))
        self.assertEqual(satirlar[("151.20", "B")], g["AYAK55"].tutar_try)

    def test_maliyeti_bilinmeyen_girdide_cikti_katmansiz_kalir(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("5"))
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        self.assertEqual((g["AYAK55"].miktar, g["AYAK66"].miktar), (D("1"), D("3")))
        self.assertTrue(all(x.maliyet_durumu == StokHareket.MaliyetDurumu.YOK or not x.tutar_try for x in g.values()))

    def test_yan_ciktisiz_operasyon_birebir_ayni(self):
        ana = self._stok("TEK", self.kat_ana)
        op = operasyon_olustur(istasyon_id=self.kesim.pk, cikti_id=ana.pk, cikti_miktar=D("2"), satirlar=[(self.profil, D("1"))])
        self.profil_gir(10, 1000, 25)
        k = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 9), hedef_cikti_miktari=D("4"))
        operasyon_kaydi_onayla(k)
        g = self.girisler(k)
        self.assertEqual(list(g), ["TEK"])
        self.assertEqual((g["TEK"].miktar, g["TEK"].tutar_try, g["TEK"].tutar_usd), (D("4"), D("200.00"), D("5.00")))     # 2 profil × 100


class GeriAlmaTest(YanCiktiBase):
    def test_onayli_kayit_varsayilan_iptal_edilemez(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_sil(k)

    def test_onayli_geri_al_ana_ve_yan_hareketleri_ve_fis_geri_alinir(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        k.refresh_from_db()
        fis_pk = k.fis_id
        operasyon_kaydi_sil(k, onayli_geri_al=True)
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertFalse(StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).exists())
        self.assertEqual((eldeki_miktar(self.ana, self.depo), eldeki_miktar(self.yan, self.depo), eldeki_miktar(self.profil, self.depo)),
                         (D("0"), D("0"), D("10")))
        from core.models import YevmiyeFisi
        self.assertTrue(YevmiyeFisi.objects.get(pk=fis_pk).silindi)
        self.profil.refresh_from_db()
        self.assertEqual(self.profil.maliyet_miktar, D("10"))

    def test_yan_cikti_baska_yerde_tuketildiyse_geri_alinamaz_hicbir_sey_degismez(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        hareket_ekle(stok_id=self.yan.pk, depo_id=self.depo.pk, tarih=date(2026, 10, 10), tur=StokHareket.Tur.CIKIS, miktar=D("1"))
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_sil(k, onayli_geri_al=True)
        k.refresh_from_db()
        self.assertFalse(k.silindi)
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).count(), 3)       # 1 girdi çıkışı + ana + yan


class IhtiyacBilgiTest(YanCiktiBase):
    def test_yan_cikti_yalniz_bilgi_hesaba_katilmaz(self):
        sonuc = ihtiyac_hesapla([(self.ana, D("6"))])
        dugum = sonuc["agac"][0]
        self.assertEqual((dugum["calistirma"], dugum["uretilecek"]), (D("2"), D("6")))
        self.assertEqual([(y["stok"].kod, y["miktar"], y["boy_mm"]) for y in dugum["yan_ciktilar"]], [("AYAK55", D("2"), D("1063.53"))])
        o = {x["stok"].kod: x for x in sonuc["ozet"]}
        self.assertEqual(o["PROFIL"]["toplam_miktar"], D("2"))                                            # yan çıktı talebi değiştirmedi
        self.assertNotIn("AYAK55", o)                                                                      # ayrı ihtiyaç satırı da açılmadı
        self.assertEqual(sonuc["plan"][0]["yan_ciktilar"][0]["miktar"], D("2"))

    def test_ihtiyac_ekraninda_bilgi_gorunur(self):
        u = User.objects.create_user("yc", password="x")
        for kod in ("ihtiyac_hesapla", "urun_agaci"):
            EkranYetki.objects.create(kullanici=u, ekran_kod=kod)
        self.client.force_login(u)
        r = self.client.get(reverse("core:urun_agaci"), {"urun": self.ana.pk, "miktar": "3"})
        self.assertContains(r, "bu kesimden ayrıca 1 adet AYAK55")


class EkranTest(YanCiktiBase):
    def setUp(self):
        self.su = User.objects.create_superuser("ycsu", password="x")
        self.client.force_login(self.su)

    def test_liste_rozeti_ve_duzenle_formu(self):
        r = self.client.get(reverse("core:operasyon_tanimlari"))
        self.assertContains(r, "+1 yan çıktı")
        r = self.client.get(reverse("core:operasyon_duzenle", args=[self.op.pk]))
        self.assertContains(r, 'id="yan-ekle"')
        self.assertContains(r, "1.292,60")                                                                # ana boy
        self.assertContains(r, "1.063,53")                                                                # yan çıktı boyu

    def test_duzenle_post_yan_cikti_kaydeder(self):
        veri = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-girdi": self.profil.pk, "satir-0-miktar": "1",
                "yan-TOTAL_FORMS": "1", "yan-INITIAL_FORMS": "0", "yan-0-stok": self.yan.pk, "yan-0-miktar": "2", "yan-0-boy_mm": "900,5",
                "istasyon": self.kesim.pk, "cikti_miktar": "3", "boy_mm": "1.292,60", "tam_calistirma": "on", "ad": "", "aciklama": ""}
        r = self.client.post(reverse("core:operasyon_duzenle", args=[self.op.pk]), veri)
        self.assertEqual(r.status_code, 302)
        y = operasyon_yan_ciktilari(self.op).get()
        self.assertEqual((y.miktar, y.boy_mm), (D("2"), D("900.50")))

    def test_kayit_detayi_dagilimi_gosterir(self):
        self.profil_gir(10, 10000, 250)
        k = self.kayit("3")
        operasyon_kaydi_onayla(k)
        r = self.client.get(reverse("core:operasyon_kaydi_detay", args=[k.pk]))
        self.assertContains(r, "AYAK55")
        self.assertContains(r, "yan")
        self.assertContains(r, "%21,5")
        self.assertContains(r, "%78,5")
