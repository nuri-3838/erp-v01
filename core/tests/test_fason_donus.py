"""FASON dönüş = fason operasyon kaydı: girdi fason depodan düşer, ana + yan çıktı üretim deposuna girer, her çıktıya kendi FasonFiyat bedeli eklenir
(bölüşüm yok), malzeme maliyeti boy oranıyla paylaşılır, muhasebe fişine YALNIZ malzeme girer, fiyat yoksa onay engellenir; belge çoklu satır, atomik onay."""
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Cari, Depo, FasonDonus, Kur, OperasyonKaydi, OperasyonKaydiCikti, StokHareket
from core.services import fason as fs
from core.services import fason_donus as fd
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import UretimHatasi, operasyon_kaydi_olustur, operasyon_kaydi_onayla
from core.tests.test_uretim_yan_cikti import YanCiktiBase

D = Decimal
TARIH = date(2026, 10, 9)


class FasonBase(YanCiktiBase):
    """profil → AYAK66 (3 adet/boy, 1292,60 mm) + yan çıktı AYAK55 (1 adet, 1063,53 mm) [YanCiktiBase.op]."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.salim = Cari.objects.create(kod="320-10-0001", unvan="SALİM FASON", muhasebe_kodu="320.10.0001")
        cls.diger = Cari.objects.create(kod="320-10-0002", unvan="DİĞER FASON", muhasebe_kodu="320.10.0002")
        cls.fdepo = Depo.objects.create(kod="999", ad="FASON-SALİM", fason_cari=cls.salim)
        cls.pay = D("3") * D("1292.60") / (D("3") * D("1292.60") + D("1063.53"))

    def fason_profil(self, miktar, tl, usd=None):
        """Ham profil fason depoda (maliyetiyle)."""
        return hareket_ekle(stok_id=self.profil.pk, depo_id=self.fdepo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS,
                            miktar=D(str(miktar)), giris_tutar_try=D(str(tl)), giris_tutar_usd=(D(str(usd)) if usd is not None else None))

    def fiyatlar(self, ana="10", yan="6", pb="TRY", basla=date(2026, 1, 1)):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.ana.pk, birim_fiyat=D(ana), para_birimi=pb, gecerlilik_baslangic=basla)
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.yan.pk, birim_fiyat=D(yan), para_birimi=pb, gecerlilik_baslangic=basla)

    def fason_kayit(self, hedef="3", cari=None):
        return operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D(hedef),
                                       fason_cari=cari or self.salim)

    def cikti_hareketleri(self, kayit):
        return {h.stok.kod: h for h in StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}


class FasonOnayTest(FasonBase):
    def test_girdi_fason_depodan_cikti_uretim_deposuna(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        k = self.fason_kayit()
        operasyon_kaydi_onayla(k)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("9"))              # girdi fason depodan düştü
        self.assertEqual(eldeki_miktar(self.profil, self.depo), D("0"))
        self.assertEqual((eldeki_miktar(self.ana, self.depo), eldeki_miktar(self.yan, self.depo)), (D("3"), D("1")))
        cikis = StokHareket.objects.get(operasyon_kaydi=k, tur=StokHareket.Tur.CIKIS)
        self.assertEqual(cikis.depo, self.fdepo)

    def test_girdi_yalniz_ana_depoda_ise_yetersiz_stok(self):
        self.profil_gir(10, 1000)                                                     # ana depoda var, fason depoda yok
        self.fiyatlar()
        k = self.fason_kayit()
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("Yetersiz stok", str(c.exception))
        self.assertIn("999", str(c.exception))
        k.refresh_from_db()
        self.assertEqual(k.durum, OperasyonKaydi.Durum.TASLAK)

    def test_yan_cikti_ayri_fason_bedeli_ve_malzeme_boy_orani(self):
        self.fason_profil(10, 1000, 25)                                               # ort 100 TL / 2,5 USD boy
        self.fiyatlar(ana="10", yan="6")
        k = self.fason_kayit("3")                                                     # 1 boy: 3 ana + 1 yan
        operasyon_kaydi_onayla(k)
        h = self.cikti_hareketleri(k)
        satir = {c.stok.kod: c for c in k.ciktilar.select_related("stok")}
        # bölüşüm yok: her çıktı KENDİ fiyatı × adedi
        self.assertEqual((satir["AYAK66"].fason_tutar, satir["AYAK55"].fason_tutar), (D("30.00"), D("6.00")))
        self.assertEqual((satir["AYAK66"].fason_birim_fiyat, satir["AYAK55"].fason_birim_fiyat), (D("10"), D("6")))
        self.assertEqual((satir["AYAK66"].fason_para_birimi, satir["AYAK66"].fason_kur), ("TRY", D("1")))
        # malzeme (100 TL) boy oranıyla, üstüne fason bedeli
        ana_malzeme = h["AYAK66"].giris_tutar_try - D("30")
        yan_malzeme = h["AYAK55"].giris_tutar_try - D("6")
        self.assertEqual(ana_malzeme + yan_malzeme, D("100.00"))
        self.assertAlmostEqual(ana_malzeme, D("100") * self.pay, places=2)
        self.assertEqual(h["AYAK66"].giris_tutar_try + h["AYAK55"].giris_tutar_try, D("136.00"))

    def test_usd_ayri_ve_kur(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar(ana="10", yan="6")
        k = self.fason_kayit("3")
        operasyon_kaydi_onayla(k)
        satir = {c.stok.kod: c for c in k.ciktilar.select_related("stok")}
        self.assertEqual((satir["AYAK66"].fason_tutar_usd, satir["AYAK55"].fason_tutar_usd), (D("0.75"), D("0.15")))   # TL / 40
        h = self.cikti_hareketleri(k)
        self.assertEqual(h["AYAK66"].giris_tutar_usd + h["AYAK55"].giris_tutar_usd, D("2.50") + D("0.90"))

    def test_muhasebe_fisine_yalniz_malzeme_girer(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        k = self.fason_kayit("3")
        operasyon_kaydi_onayla(k)
        k.refresh_from_db()
        self.assertIsNotNone(k.fis)
        borc = sum((s.borc for s in k.fis.satirlar.all()), D("0"))
        alacak = sum((s.alacak for s in k.fis.satirlar.all()), D("0"))
        self.assertEqual(borc, alacak)                                                # dengeli
        self.assertEqual(borc, D("100.00"))                                           # 136 DEĞİL: fason bedeli fişte yok

    def test_fiyat_yoksa_onay_engellenir(self):
        self.fason_profil(10, 1000)
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.ana.pk, birim_fiyat=D("10"), gecerlilik_baslangic=date(2026, 1, 1))   # yan çıktı fiyatsız
        k = self.fason_kayit("3")
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("AYAK55", str(c.exception))
        self.assertIn("fason fiyatı yok", str(c.exception))
        k.refresh_from_db()
        self.assertEqual(k.durum, OperasyonKaydi.Durum.TASLAK)
        self.assertFalse(StokHareket.objects.filter(operasyon_kaydi=k, silindi=False).exists())     # hiçbir hareket yazılmadı
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("10"))

    def test_tum_fiyatlar_eksikse_hepsi_listelenir(self):
        self.fason_profil(10, 1000)
        k = self.fason_kayit("3")
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("AYAK66", str(c.exception))
        self.assertIn("AYAK55", str(c.exception))

    def test_fiyat_kayit_tarihine_gore(self):
        self.fason_profil(10, 1000)
        self.fiyatlar(ana="10", yan="6", basla=date(2026, 1, 1))
        self.fiyatlar_yeni = fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.ana.pk, birim_fiyat=D("99"), gecerlilik_baslangic=date(2026, 12, 1))
        k = self.fason_kayit("3")                                                     # 09.10.2026: ileri tarihli 99 geçerli değil
        operasyon_kaydi_onayla(k)
        self.assertEqual(k.ciktilar.get(ana_mi=True).fason_birim_fiyat, D("10"))

    def test_baska_carinin_fiyati_gecmez(self):
        self.fason_profil(10, 1000)
        for st, f in ((self.ana, "10"), (self.yan, "6")):
            fs.fiyat_olustur(cari_id=self.diger.pk, stok_id=st.pk, birim_fiyat=D(f), gecerlilik_baslangic=date(2026, 1, 1))
        k = self.fason_kayit("3")
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_onayla(k)

    def test_doviz_fiyat_kayit_tarihindeki_kurla(self):
        self.fason_profil(10, 1000, 25)
        Kur.objects.filter(tarih=TARIH).update(eur_alis=D("44"))
        self.fiyatlar(ana="0.5", yan="0.25", pb="USD")
        k = self.fason_kayit("3")
        operasyon_kaydi_onayla(k)
        a = k.ciktilar.get(ana_mi=True)
        self.assertEqual((a.fason_para_birimi, a.fason_kur, a.fason_tutar, a.fason_tutar_usd), ("USD", D("40"), D("60.00"), D("1.50")))   # 0,5×40×3 ; /40
        y = k.ciktilar.get(ana_mi=False)
        self.assertEqual((y.fason_tutar, y.fason_tutar_usd), (D("10.00"), D("0.25")))
        # EUR
        k2 = self.fason_kayit("3")
        fs.fiyat_guncelle(fs.aktif_fiyatlar().get(stok=self.ana), cari_id=self.salim.pk, stok_id=self.ana.pk, birim_fiyat=D("1"), para_birimi="EUR",
                          gecerlilik_baslangic=date(2026, 1, 1))
        self.fason_profil(10, 1000)
        operasyon_kaydi_onayla(k2)
        a2 = k2.ciktilar.get(ana_mi=True)
        self.assertEqual((a2.fason_kur, a2.fason_tutar, a2.fason_tutar_usd), (D("44"), D("132.00"), D("3.30")))

    def test_kur_yoksa_net_hata(self):
        self.fason_profil(10, 1000)
        self.fiyatlar(ana="1", yan="1", pb="EUR")                                     # EUR kuru yok
        k = self.fason_kayit("3")
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("EUR kuru yok", str(c.exception))

    def test_fason_deposu_yoksa_hata(self):
        self.fiyatlar()
        k = operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("3"), fason_cari=self.diger)
        with self.assertRaises(UretimHatasi) as c:
            operasyon_kaydi_onayla(k)
        self.assertIn("fason deposu yok", str(c.exception))

    def test_fason_bedeli_tahmini_ve_ortalama(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        k = self.fason_kayit("3")
        operasyon_kaydi_onayla(k)
        h = self.cikti_hareketleri(k)
        self.assertTrue(h["AYAK66"].giris_tahmini and h["AYAK55"].giris_tahmini)       # faturası gelmedi → fason bedeli tahmini
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.maliyet_deger_try, h["AYAK66"].giris_tutar_try)       # ortalama/değer fasonu içerir

    def test_fasonsuz_kayit_etkilenmez(self):
        self.profil_gir(10, 1000)
        k = operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("3"))
        operasyon_kaydi_onayla(k)
        h = self.cikti_hareketleri(k)
        self.assertEqual(h["AYAK66"].giris_tutar_try + h["AYAK55"].giris_tutar_try, D("100.00"))
        self.assertFalse(h["AYAK66"].giris_tahmini)
        self.assertIsNone(k.ciktilar.get(ana_mi=True).fason_tutar)


class DonusBelgeTest(FasonBase):
    def test_tek_belge_cok_satir(self):
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, irsaliye_no="IRS-77",
                             satirlar=[(self.op.pk, "3"), (self.op.pk, "6")])
        self.assertTrue(d.no.startswith("FD-2026-"))
        ks = list(d.kayitlar.order_by("pk"))
        self.assertEqual(len(ks), 2)
        self.assertTrue(all(k.fason_donus_id == d.pk and k.fason_cari_id == self.salim.pk and k.durum == "TASLAK" for k in ks))
        self.assertEqual(fd.donus_durumu(d), "TASLAK")
        fd.donus_onayla(d)
        self.assertEqual(fd.donus_durumu(d), "ONAYLI")
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("7"))              # 1 + 2 boy
        b = fd.donus_bilgisi(d)
        self.assertEqual(b["toplam_try"], D("90.00") + D("18.00"))                    # (30+6) + (60+12)... 3→36, 6→72
        self.assertEqual(b["toplam_try"], D("36") + D("72"))

    def test_atomik_onay_biri_hata_verirse_hicbiri(self):
        self.fason_profil(1, 100)                                                     # yalnız 1 boy: ikinci satır yetersiz
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3"), (self.op.pk, "3")])
        with self.assertRaises(FasonHatasi) as c:
            fd.donus_onayla(d)
        self.assertIn("Yetersiz stok", str(c.exception))
        self.assertEqual(fd.donus_durumu(d), "TASLAK")
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("1"))
        self.assertFalse(StokHareket.objects.filter(operasyon_kaydi__fason_donus=d, silindi=False).exists())

    def test_olustur_ve_onayla_birlikte_ve_hata(self):
        self.fason_profil(10, 1000)
        with self.assertRaises(FasonHatasi):                                          # fiyat yok → bütün işlem (belge dahil) geri alınır
            fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        self.assertEqual(FasonDonus.objects.count(), 0)
        self.assertEqual(OperasyonKaydi.objects.filter(fason_cari=self.salim).count(), 0)

    def test_dogrulamalar(self):
        self.fiyatlar()
        ortak = dict(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        for kw in (dict(cari_id=self.diger.pk), dict(cari_id=999999), dict(depo_id=self.fdepo.pk), dict(depo_id=999999), dict(satirlar=[]),
                   dict(satirlar=[(self.op.pk, "0")]), dict(satirlar=[(self.op.pk, "abc")]), dict(satirlar=[(999999, "3")])):
            with self.assertRaises(FasonHatasi, msg=str(kw)):
                fd.donus_olustur(**{**ortak, **kw})
        self.assertEqual(FasonDonus.objects.count(), 0)

    def test_sil_taslak_evet_onayli_hayir(self):
        self.fason_profil(10, 1000)
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        fd.donus_sil(d)
        d.refresh_from_db()
        self.assertTrue(d.silindi)
        self.assertEqual(OperasyonKaydi.objects.filter(silindi=False, fason_cari=self.salim).count(), 0)
        d2 = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        with self.assertRaises(FasonHatasi):
            fd.donus_sil(d2)

    def test_taslak_belge_bilgisi_fiyat_eksikleri(self):
        fs.fiyat_olustur(cari_id=self.salim.pk, stok_id=self.ana.pk, birim_fiyat=D("10"), gecerlilik_baslangic=date(2026, 1, 1))
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        b = fd.donus_bilgisi(d)
        self.assertEqual([s.kod for s in b["eksikler"]], ["AYAK55"])
        self.assertEqual(b["durum"], "TASLAK")


class DonusEkranTest(FasonBase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_superuser("fdu_y", password="x"))

    def post_veri(self, eylem, adet="3", **ek):
        v = {"cari": self.salim.pk, "depo": self.depo.pk, "irsaliye_no": "IRS-1", "tarih": "2026-10-09", "aciklama": "",
             "satir-TOTAL_FORMS": "2", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
             "satir-0-operasyon": self.op.pk, "satir-0-adet": adet, "satir-1-operasyon": "", "satir-1-adet": "", "eylem": eylem}
        v.update(ek)
        return v

    def test_form_yalniz_fason_depolu_cariler_ve_fason_olmayan_depolar(self):
        from django.urls import reverse
        r = self.client.get(reverse("core:fason_donus_ekle"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(list(r.context["baslik"].fields["cari"].queryset), [self.salim])
        self.assertNotIn(self.fdepo, list(r.context["baslik"].fields["depo"].queryset))
        self.assertContains(r, "Kaydet ve Onayla")

    def test_taslak_kaydet_detay_onayla(self):
        from django.urls import reverse
        self.fason_profil(10, 1000, 25)
        self.fiyatlar()
        r = self.client.post(reverse("core:fason_donus_ekle"), self.post_veri("taslak"))
        d = FasonDonus.objects.get()
        self.assertRedirects(r, reverse("core:fason_donus_detay", args=[d.pk]))
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, d.no)
        self.assertContains(det, "Taslak")
        self.assertContains(det, "AYAK55")                                            # yan çıktı satırı
        self.assertNotContains(det, "fiyat yok")
        r = self.client.post(reverse("core:fason_donus_onayla", args=[d.pk]))
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "Onaylı")
        self.assertContains(det, "36,00")                                             # 30 + 6 TL toplam fason bedeli
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("9"))
        liste = self.client.get(reverse("core:fason_donusleri"))
        self.assertContains(liste, d.no)
        self.assertContains(liste, "IRS-1")

    def test_kaydet_ve_onayla_fiyat_yoksa_hata_mesaji(self):
        from django.urls import reverse
        self.fason_profil(10, 1000)
        r = self.client.post(reverse("core:fason_donus_ekle"), self.post_veri("onayla"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "fason fiyatı yok")
        self.assertEqual(FasonDonus.objects.count(), 0)

    def test_taslakta_eksik_fiyat_uyarisi_ve_sil(self):
        from django.urls import reverse
        self.client.post(reverse("core:fason_donus_ekle"), self.post_veri("taslak"))
        d = FasonDonus.objects.get()
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, "geçerli fason fiyatı yok")
        self.client.post(reverse("core:fason_donus_sil", args=[d.pk]))
        d.refresh_from_db()
        self.assertTrue(d.silindi)

    def test_kayit_detayinda_fason_bilgisi(self):
        from django.urls import reverse
        self.fason_profil(10, 1000)
        self.fiyatlar()
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")])
        k = d.kayitlar.get()
        r = self.client.get(reverse("core:operasyon_kaydi_detay", args=[k.pk]))
        self.assertContains(r, "SALİM FASON")
        self.assertContains(r, d.no)

    def test_yetki(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        from core.models import EkranYetki
        u = User.objects.create_user("fdu_u", password="x")
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse("core:fason_donusleri")).status_code, 403)
        EkranYetki.objects.create(kullanici=u, ekran_kod="fason_donusleri")
        self.assertEqual(self.client.get(reverse("core:fason_donusleri")).status_code, 200)
