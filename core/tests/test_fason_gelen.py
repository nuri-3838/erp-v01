"""FASON dönüş: GELEN adet ve FİRE. Girdi tüketimi beklenen (tam boya yuvarlanmış) çalıştırmadan; stoğa giren adet ve fason bedeli GELEN adetten;
fark fire olarak kayıtta (beklenen_miktar − miktar); profil maliyeti gelen parçalara (gelen × boy oranıyla) dağılır; gelen > beklenen engellenir."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import OperasyonKaydi, StokHareket
from core.services import fason_donus as fd
from core.services import fason_mutabakat as fm
from core.services.fason import FasonHatasi
from core.services.hareket import eldeki_miktar
from core.services.uretim import UretimHatasi, operasyon_kaydi_onayla
from core.tests.test_fason_donus import TARIH, FasonBase

D = Decimal


class GelenBase(FasonBase):
    def kayit(self, beklenen="3", gelen_ana=None, gelen_yan=None):
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, beklenen, gelen_ana, gelen_yan)])
        return d, d.kayitlar.get()

    def hazir(self):
        self.fason_profil(10, 1000, 25)                      # ort 100 TL / 2,5 USD boy
        self.fiyatlar(ana="10", yan="6")

    def satir(self, k):
        return {c.stok.kod: c for c in k.ciktilar.select_related("stok")}


class GelenAdetTest(GelenBase):
    def test_eksik_gelen_ana_cikti(self):
        self.hazir()
        d, k = self.kayit("3", gelen_ana="2")
        fd.donus_onayla(d)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("9"))                    # girdi: beklenen çalıştırma → 1 boy
        self.assertEqual((eldeki_miktar(self.ana, self.depo), eldeki_miktar(self.yan, self.depo)), (D("2"), D("1")))   # stoğa GELEN adet
        s = self.satir(k)
        self.assertEqual((s["AYAK66"].miktar, s["AYAK66"].beklenen_miktar), (D("2"), D("3")))
        self.assertEqual((s["AYAK55"].miktar, s["AYAK55"].beklenen_miktar), (D("1"), D("1")))
        self.assertEqual(s["AYAK66"].fason_tutar, D("20.00"))                                # gelen × fiyat
        self.assertEqual(s["AYAK55"].fason_tutar, D("6.00"))

    def test_profil_maliyeti_gelen_parcalara_dagilir(self):
        self.hazir()
        d, k = self.kayit("3", gelen_ana="2")
        fd.donus_onayla(d)
        k.refresh_from_db()
        h = {x.stok.kod: x for x in StokHareket.objects.filter(operasyon_kaydi=k, silindi=False, tur=StokHareket.Tur.GIRIS).select_related("stok")}
        ana_malz = h["AYAK66"].giris_tutar_try - D("20")
        yan_malz = h["AYAK55"].giris_tutar_try - D("6")
        self.assertEqual(ana_malz + yan_malz, D("100.00"))                                   # 1 boy'un tamamı gelen parçalara
        beklenen_ana = D("100") * (D("2") * D("1292.60")) / (D("2") * D("1292.60") + D("1063.53"))
        self.assertAlmostEqual(ana_malz, beklenen_ana, places=2)                              # ağırlık = GELEN adet × boy
        self.assertEqual(sum((x.borc for x in k.fis.satirlar.filter(silindi=False)), D("0")), D("100.00"))   # fişte yalnız malzeme

    def test_yan_cikti_hic_gelmedi(self):
        self.hazir()
        d, k = self.kayit("3", gelen_yan={self.yan.pk: "0"})
        fd.donus_onayla(d)
        self.assertEqual(eldeki_miktar(self.yan, self.depo), D("0"))
        s = self.satir(k)
        self.assertEqual((s["AYAK55"].miktar, s["AYAK55"].beklenen_miktar, s["AYAK55"].fason_tutar), (D("0"), D("1"), D("0")))
        giris = StokHareket.objects.filter(operasyon_kaydi=k, silindi=False, tur=StokHareket.Tur.GIRIS)
        self.assertEqual([h.stok.kod for h in giris.select_related("stok")], ["AYAK66"])   # yan için hareket yok
        self.assertEqual(giris.get().giris_tutar_try, D("130.00"))                           # malzemenin tamamı (100) + 3 × 10

    def test_varsayilan_gelen_beklenen(self):
        self.hazir()
        d, k = self.kayit("3")
        fd.donus_onayla(d)
        s = self.satir(k)
        self.assertEqual((s["AYAK66"].miktar - s["AYAK66"].beklenen_miktar, s["AYAK55"].miktar - s["AYAK55"].beklenen_miktar), (D("0"), D("0")))

    def test_tam_boya_yuvarlanmis_beklenen_uzerinden_fire(self):
        self.hazir()
        d, k = self.kayit("4", gelen_ana="4")                                                # beklenen 4 → 2 boy = 6 ana + 2 yan
        self.assertEqual(k.hedef_cikti_miktari, D("6"))
        fd.donus_onayla(d)
        self.assertEqual(eldeki_miktar(self.profil, self.fdepo), D("8"))                      # 2 boy tüketildi
        s = self.satir(k)
        self.assertEqual((s["AYAK66"].miktar, s["AYAK66"].beklenen_miktar), (D("4"), D("6")))   # 2 adet fire
        self.assertEqual((s["AYAK55"].miktar, s["AYAK55"].beklenen_miktar), (D("2"), D("2")))

    def test_gelen_beklenenden_fazla_engellenir(self):
        self.hazir()
        with self.assertRaises(FasonHatasi) as c:
            self.kayit("3", gelen_ana="4")
        self.assertIn("beklenenden", str(c.exception))
        with self.assertRaises(FasonHatasi):
            self.kayit("3", gelen_yan={self.yan.pk: "2"})
        self.assertEqual(OperasyonKaydi.objects.filter(fason_cari=self.salim).count(), 0)    # atomik: belge de yok

    def test_gecersiz_gelen(self):
        self.hazir()
        for kw in (dict(gelen_ana="0"), dict(gelen_ana="-1"), dict(gelen_ana="abc"), dict(gelen_yan={self.yan.pk: "-1"})):
            with self.assertRaises(FasonHatasi, msg=str(kw)):
                self.kayit("3", **kw)

    def test_onayda_model_duzeyinde_de_dogrulanir(self):
        """gelen_ayarla'yı atlayıp modele doğrudan fazla adet yazılsa bile onay engeller."""
        self.hazir()
        _, k = self.kayit("3")
        k.gelen = {str(self.ana.pk): "9"}
        k.save()
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_onayla(k)
        k.refresh_from_db()
        self.assertEqual(k.durum, OperasyonKaydi.Durum.TASLAK)

    def test_onayli_kayitta_gelen_degistirilemez(self):
        self.hazir()
        d, k = self.kayit("3")
        fd.donus_onayla(d)
        k.refresh_from_db()
        with self.assertRaises(FasonHatasi):
            fd.gelen_ayarla(k, "2")

    def test_fason_fiyati_gelen_adetten_beklenen_tutar(self):
        self.hazir()
        d, _ = self.kayit("3", gelen_ana="2")
        fd.donus_onayla(d)
        self.assertEqual(fd.donus_bilgisi(d)["toplam_try"], D("26.00"))                       # 20 + 6
        self.assertEqual(fd.donus_bilgisi(d)["toplam_fire"], D("1"))

    def test_fasonsuz_kayitta_beklenen_snapshot(self):
        self.profil_gir(10, 1000)
        from core.services.uretim import operasyon_kaydi_olustur
        k = operasyon_kaydi_olustur(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=TARIH, hedef_cikti_miktari=D("3"))
        operasyon_kaydi_onayla(k)
        self.assertTrue(all(c.beklenen_miktar == c.miktar for c in k.ciktilar.all()))


class GelenBelgeTest(GelenBase):
    def test_toplu_gelen_guncelle_ve_onay(self):
        self.hazir()
        d, k = self.kayit("3")
        fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "2", f"gelen_{k.pk}_{self.yan.pk}": "0"})
        k.refresh_from_db()
        self.assertEqual(k.gelen, {str(self.ana.pk): "2", str(self.yan.pk): "0"})                        # tek yapı: referans da gelen içinde
        info = fd.donus_bilgisi(d)
        c = {x["stok"].kod: x for x in info["satirlar"][0]["ciktilar"]}
        self.assertEqual((c["AYAK66"]["beklenen"], c["AYAK66"]["miktar"], c["AYAK66"]["fire"]), (D("3"), D("2"), D("1")))
        self.assertEqual((c["AYAK55"]["miktar"], c["AYAK55"]["fire"]), (D("0"), D("1")))
        fd.donus_onayla(d)
        self.assertEqual(fd.donus_bilgisi(d)["toplam_fire"], D("2"))

    def test_toplu_guncelle_hatali_deger_atomik(self):
        self.hazir()
        d, k = self.kayit("3")
        with self.assertRaises(FasonHatasi):
            fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "2", f"gelen_{k.pk}_{self.yan.pk}": "5"})
        k.refresh_from_db()
        self.assertEqual(k.gelen, {})                                                         # ilk alan da yazılmadı

    def test_bos_alan_beklenene_doner(self):
        self.hazir()
        d, k = self.kayit("3", gelen_ana="2")
        fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "", f"gelen_{k.pk}_{self.yan.pk}": ""})
        k.refresh_from_db()
        self.assertEqual(k.gelen, {})

    def test_onayli_belgede_toplu_guncelle_yok(self):
        self.hazir()
        d, k = self.kayit("3")
        fd.donus_onayla(d)
        with self.assertRaises(FasonHatasi):
            fd.gelen_guncelle(d, {f"gelen_{k.pk}_ana": "2"})


class GelenMutabakatTest(GelenBase):
    def test_fire_mutabakatta(self):
        self.hazir()
        d, _ = self.kayit("3", gelen_ana="2", gelen_yan={self.yan.pk: "0"})
        fd.donus_onayla(d)
        m = fm.mutabakat(self.salim)
        p = {x["stok"].kod: x for x in m["parcalar"]}
        self.assertEqual((p["AYAK66"]["beklenen"], p["AYAK66"]["adet"], p["AYAK66"]["fire"]), (D("3"), D("2"), D("1")))
        self.assertEqual((p["AYAK55"]["beklenen"], p["AYAK55"]["adet"], p["AYAK55"]["fire"]), (D("1"), D("0"), D("1")))
        self.assertEqual(m["toplam_fire"], D("2"))
        self.assertEqual(m["toplam"]["try"], D("20.00"))                                       # yalnız gelen parçaların fason bedeli


class GelenEkranTest(GelenBase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("gl_y", password="x"))

    def test_form_gelen_adet_ve_detay_duzenleme(self):
        self.hazir()
        v = {"cari": self.salim.pk, "depo": self.depo.pk, "irsaliye_no": "IRS-5", "tarih": "2026-10-09", "aciklama": "",
             "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
             "satir-0-operasyon": self.op.pk, "satir-0-adet": "3", "satir-0-gelen": "2", "eylem": "taslak"}
        r = self.client.post(reverse("core:fason_donus_ekle"), v)
        from core.models import FasonDonus
        d = FasonDonus.objects.get()
        self.assertRedirects(r, reverse("core:fason_donus_detay", args=[d.pk]))
        k = d.kayitlar.get()
        self.assertEqual(k.gelen[str(self.ana.pk)], "2")
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertContains(det, f'name="gelen_{k.pk}_ana"')
        self.assertContains(det, f'name="gelen_{k.pk}_{self.yan.pk}"')
        self.assertContains(det, "Gelen adetleri kaydet")
        r = self.client.post(reverse("core:fason_donus_gelen", args=[d.pk]), {f"gelen_{k.pk}_ana": "3", f"gelen_{k.pk}_{self.yan.pk}": "0"})
        k.refresh_from_db()
        self.assertEqual(k.gelen, {str(self.ana.pk): "3", str(self.yan.pk): "0"})
        r = self.client.post(reverse("core:fason_donus_gelen", args=[d.pk]), {f"gelen_{k.pk}_ana": "9"}, follow=True)
        self.assertContains(r, "beklenenden")
        self.client.post(reverse("core:fason_donus_onayla", args=[d.pk]))
        det = self.client.get(reverse("core:fason_donus_detay", args=[d.pk]))
        self.assertNotContains(det, "Gelen adetleri kaydet")
        self.assertContains(det, "toplam fire")

    def test_form_gelen_sifir_reddedilir(self):
        v = {"cari": self.salim.pk, "depo": self.depo.pk, "tarih": "2026-10-09", "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0",
             "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000", "satir-0-operasyon": self.op.pk, "satir-0-adet": "3", "satir-0-gelen": "0",
             "eylem": "taslak"}
        r = self.client.post(reverse("core:fason_donus_ekle"), v)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Gelen adet sıfırdan büyük")

    def test_mutabakat_ekraninda_fire(self):
        self.hazir()
        d, _ = self.kayit("3", gelen_ana="2")
        fd.donus_onayla(d)
        r = self.client.get(reverse("core:fason_mutabakat"))
        self.assertContains(r, "Fire")
        self.assertContains(r, "Toplam fire")
