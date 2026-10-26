"""OFİS karma kesim (Emirsiz Üretim › Karma kesim, ``karma_kesim.ofis_karma_kes``): atölyeyle aynı servis — uzunluk bütçesi, fire (testere + çene + uç, stoğa girmez), profil maliyeti
kesilen uzunluk oranıyla, net ihtiyaca göre emir dağıtımı (Acil önce), canlı önizleme (yazmaz), iş oturumu / işçilik süresi YOK, kaynak OFIS; açık atölye karma oturumu varken
engel (süper kullanıcı notla aşar), ayrılmış profil stoğu onayı, grup geri alma, karma kaydının tekil "Geri al ve sil"inin engellenmesi, liste / detay / filtre, yetki."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import EkranYetki, IsOturumu, IsOturumuOlay, IstasyonEmri, KarmaKesim, OperasyonKaydi, StokHareket, UretimEmri
from core.services import is_oturumu, stok_ayirma
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import uretim_emri_olustur
from core.tarih import tr_bugun
from core.tests import test_karma_oturum as ko
from core.tests.test_atolye import IZINLI

D = Decimal
KARMA_OFIS = (("KP1", "12"), ("KP2", "8"))            # 2 boy: KP1 12 (10 emre + 2 serbest), KP2 8 → 7.145,64 mm


class OfisTaban(ko.KarmaOturumTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ofis = User.objects.create_user("ofiskarma", password="x")
        for k in ("emirsiz_uretim", "operasyon_kayitlari"):
            EkranYetki.objects.create(kullanici=cls.ofis, ekran_kod=k)
        cls.sadece_emirsiz = User.objects.create_user("sadeceemirsiz", password="x")
        EkranYetki.objects.create(kullanici=cls.sadece_emirsiz, ekran_kod="emirsiz_uretim")
        cls.yetkisiz = User.objects.create_user("yetkisizofis", password="x")

    def ofis_kes(self, boy="2", adet=KARMA_OFIS, user=None, follow=False, **ek):
        self.client.force_login(user or self.ofis)
        veri = {"istasyon": self.lazer.pk, "profil": self.kprofil.pk, "boy_sayisi": boy, "depo": self.depo.pk, "tarih": tr_bugun().isoformat(),
                **{f"a_{self.kparca[k].pk}": v for k, v in adet}, **ek}
        return self.client.post(reverse("core:emirsiz_karma"), veri, HTTP_X_REAL_IP=IZINLI, follow=follow)

    def onizle(self, user=None, **q):
        self.client.force_login(user or self.ofis)
        return self.client.get(reverse("core:emirsiz_karma_onizleme"), {"istasyon": self.lazer.pk, "profil": self.kprofil.pk, **q}, HTTP_X_REAL_IP=IZINLI).json()

    def kesim(self):
        return KarmaKesim.objects.order_by("-pk").first()

    def sifir(self):
        self.assertEqual((KarmaKesim.objects.count(), OperasyonKaydi.objects.filter(karma__isnull=False).count()), (0, 0))


class OfisKaydiTest(OfisTaban):
    def test_form_ve_sekmeler(self):
        self.client.force_login(self.ofis)
        r = self.client.get(reverse("core:emirsiz_karma"))
        self.assertContains(r, "Karma Kesim (Ofis)")
        self.assertContains(r, "Kullanılan boy sayısı")
        self.assertEqual([x["kod"] for x in r.context["harita"]], [self.lazer.kod])
        profil = r.context["harita"][0]["profiller"][0]
        self.assertEqual((profil["kod"], profil["boy_mm"], [a["kod"] for a in profil["adaylar"]]), ("KP-PROFIL", "6000.00", ["KP1", "KP2"]))
        self.assertEqual([a["ihtiyac"] for a in profil["adaylar"]], [10, 8])                                          # açık ÜS'nin net ihtiyacı (ipucu)
        r = self.client.get(reverse("core:emirsiz_uretim"))
        self.assertContains(r, reverse("core:emirsiz_karma"))                                                         # emirsiz ekranında "Karma kesim" sekmesi
        self.assertContains(self.client.get(reverse("core:operasyon_kayitlari")), reverse("core:karma_kesimler"))

    def test_kayit_stok_maliyet_fire_ve_oturumsuz(self):
        self.profil_gir = None
        r = self.ofis_kes()
        k = self.kesim()
        self.assertRedirects(r, reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertEqual((k.kaynak, k.boy_sayisi, k.toplam_adet, k.created_by_id), ("OFIS", 2, 20, self.ofis.pk))
        self.assertEqual((k.parca_mm, k.testere_mm, k.cene_mm, k.uc_mm, k.fire_mm), (D("7145.64"), D("60.00"), D("150.00"), D("4644.36"), D("4854.36")))
        self.assertEqual(k.fire_orani.quantize(D("0.0001")), D("0.4045"))
        self.assertEqual(IsOturumu.objects.count(), 0)                                                                # iş oturumu / işçilik süresi yok
        kayitlar = list(k.kayitlar.all())
        self.assertEqual(len(kayitlar), 3)                                                                            # KP1 emre 10 + KP1 serbest 2 + KP2 emre 8
        self.assertTrue(all(x.mesai_kaydi_id is None and x.personel_id is None and x.created_by_id == self.ofis.pk for x in kayitlar))
        self.assertTrue(all(x.aciklama.startswith("Ofis karma kesim KK-") for x in kayitlar))
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("8"))                                              # yalnız 2 boy düştü; fire için hareket yok
        self.assertEqual(eldeki_miktar(self.kparca["KP1"], self.depo), D("12"))
        self.assertEqual(StokHareket.objects.filter(operasyon_kaydi__karma=k, tur=StokHareket.Tur.GIRIS).count(), 3)
        giris = sum(h.tutar_try for h in StokHareket.objects.filter(operasyon_kaydi__karma=k, tur=StokHareket.Tur.GIRIS))
        self.assertAlmostEqual(float(giris), 2400.0, delta=0.05)                                                      # 2 boy × 1.200 TL: fire ayrıca maliyetlenmez
        birim = {x.operasyon.cikti.kod: D(0) for x in kayitlar}
        for h in StokHareket.objects.filter(operasyon_kaydi__karma=k, tur=StokHareket.Tur.GIRIS):
            birim[h.stok.kod] += h.tutar_try
        self.assertAlmostEqual(float(birim["KP1"] / 12), float(D("2400") * D("349.73") / D("7145.64")), delta=0.02)   # parça birim maliyeti = toplam × parça mm / Σ(kesilen mm)
        self.assertAlmostEqual(float(birim["KP2"] / 8), float(D("2400") * D("368.61") / D("7145.64")), delta=0.02)
        ie1 = self.kie["KP1"]; ie1.refresh_from_db()
        self.assertEqual(ie1.durum, IstasyonEmri.Durum.BITTI)                                                         # net ihtiyaç karşılandı → emir daraldı

    def test_personel_secilirse_giren_o_olur_mesai_bagli_degil(self):
        self.baslat(self.u2)                                                                                           # ayşe'nin açık mesaisi var
        self.ofis_kes(personel=self.p2.pk)
        k = self.kesim()
        self.assertEqual(k.personel_id, self.p2.pk)
        self.assertTrue(all(x.personel_id == self.p2.pk and x.mesai_kaydi_id is None for x in k.kayitlar.all()))     # süre / işçilik yazılmaz
        self.assertEqual(IsOturumu.objects.count(), 0)

    def test_uzunluk_asimi_reddi(self):
        r = self.ofis_kes(boy="1", adet=(("KP1", "16"), ("KP2", "3")))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Kesim uzunluğu aşıldı")
        self.sifir()
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("10"))

    def test_dogrulamalar(self):
        for ek, mesaj in ((dict(adet=()), "En az bir parça"), (dict(tarih="2099-01-01"), "Tarih bugünden ileri olamaz"), (dict(adet=(("KP1", "2,5"),)), "tam sayı")):
            kw = {k: v for k, v in ek.items() if k in ("boy", "adet")}
            ekstra = {k: v for k, v in ek.items() if k not in ("boy", "adet")}
            r = self.ofis_kes(**kw, **ekstra)
            self.assertContains(r, mesaj, msg_prefix=str(ek))
        for alan, deger in (("boy_sayisi", "1,5"), ("boy_sayisi", "0"), ("istasyon", self.fason_ist.pk), ("depo", "999999")):          # form alanı hataları: kayıt yazılmaz
            veri = {"istasyon": self.lazer.pk, "profil": self.kprofil.pk, "boy_sayisi": "1", "depo": self.depo.pk, "tarih": tr_bugun().isoformat(),
                    f"a_{self.kparca['KP1'].pk}": "3", alan: deger}
            r = self.client.post(reverse("core:emirsiz_karma"), veri, HTTP_X_REAL_IP=IZINLI)
            self.assertIn(alan, r.context["form"].errors, (alan, deger))
        self.sifir()

    def test_yetersiz_stok_hicbir_sey_yazmaz(self):
        r = self.ofis_kes(boy="12", adet=(("KP1", "80"), ("KP2", "80")))
        self.assertContains(r, "yetersiz")
        self.sifir()

    def test_emir_dagitimi_acil_once(self):
        acil = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.kmamul.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 12),
                                   oncelik=UretimEmri.Oncelik.ACIL)
        self.ofis_kes(adet=(("KP1", "15"),))
        k = self.kesim()
        hedef = {x.istasyon_emri_id: x.hedef_cikti_miktari for x in k.kayitlar.all()}
        ie_acil = IstasyonEmri.objects.get(uretim_emri=acil, operasyon=self.kop["KP1"])
        self.assertEqual(hedef, {ie_acil.pk: D("10"), self.kie["KP1"].pk: D("5")})                                    # Acil ÜS önce (10), kalan 5 normal ÜS'ye
        ie_acil.refresh_from_db()
        self.assertEqual(ie_acil.durum, IstasyonEmri.Durum.BITTI)


class OnizlemeTest(OfisTaban):
    def test_onizleme_fire_dokumu_dagilim_ve_yazmaz(self):
        d = self.onizle(boy="2", **{f"a_{self.kparca['KP1'].pk}": "12", f"a_{self.kparca['KP2'].pk}": "8"})
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["parca_mm"], d["testere_mm"], d["cene_mm"], d["uc_mm"], d["fire_mm"], d["fire_yuzde"]), ("7.145,64", "60,00", "150,00", "4.644,36", "4.854,36", "40,45"))
        dag = {(x["kod"], x["emir"]): x["adet"] for x in d["dagilim"]}
        self.assertEqual(dag[("KP1", "serbest stok")], 2)
        self.assertEqual(dag[("KP1", f"{self.kemir.no} / {self.kie['KP1'].no}")], 10)
        self.assertEqual(d["profil_stok"]["gerekli"], "2")
        self.assertFalse(d["profil_stok"]["onay_gerekli"])
        self.assertEqual(d["engel"], "")
        self.sifir()
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("10"))

    def test_onizleme_hata_ve_bos(self):
        self.assertEqual(self.onizle(boy="1")["ok"], False)
        self.assertEqual(self.onizle(**{f"a_{self.kparca['KP1'].pk}": "3"})["hata"], "Kullanılan boy sayısını girin.")
        self.assertIn("Kesim uzunluğu aşıldı", self.onizle(boy="1", **{f"a_{self.kparca['KP1'].pk}": "16", f"a_{self.kparca['KP2'].pk}": "3"})["hata"])
        self.client.force_login(self.ofis)
        self.assertEqual(self.client.get(reverse("core:emirsiz_karma_onizleme"), HTTP_X_REAL_IP=IZINLI).json(), {"ok": False, "hata": "", "engel": ""})
        self.sifir()

    def test_onizleme_atolye_engelini_bildirir_ama_gosterir(self):
        self.baslat()
        self.karma_basla()
        d = self.onizle(boy="2", **{f"a_{self.kparca['KP1'].pk}": "12"})
        self.assertTrue(d["ok"])
        self.assertIn("ekibi karma kesim yapıyor", d["engel"])


class EngelTest(OfisTaban):
    def test_acik_atolye_karma_oturumu_engeller_super_kullanici_notla_asar(self):
        self.baslat()
        self.karma_basla()
        o = self.karma_oturum()
        r = self.ofis_kes()                                                                                           # yetkili ama süper kullanıcı değil
        self.assertContains(r, "ekibi karma kesim yapıyor")
        self.assertContains(r, "ERRAHMAN OPERATOR")
        self.sifir()
        r = self.ofis_kes(asma_notu="acele var")                                                                      # not yazsa da süper kullanıcı değil → aşamaz
        self.assertContains(r, "ekibi karma kesim yapıyor")
        self.sifir()
        r = self.ofis_kes(user=self.yon)                                                                              # süper kullanıcı notsuz → engel
        self.assertContains(r, "ekibi karma kesim yapıyor")
        self.sifir()
        r = self.ofis_kes(user=self.yon, asma_notu="acele var")                                                       # süper kullanıcı + not → geçer
        k = self.kesim()
        self.assertRedirects(r, reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertEqual(k.kaynak, "OFIS")
        olay = IsOturumuOlay.objects.get(oturum=o, tur=IsOturumuOlay.Tur.OFIS_ASILDI)
        self.assertIn("acele var", olay.aciklama)
        o.refresh_from_db()
        self.assertEqual(o.durum, "ACIK")                                                                              # atölye oturumu açık kalır

    def test_adet_bekleyen_atolye_karma_oturumu_da_engeller(self):
        self.hazir()
        o = self.karma_oturum()
        is_oturumu.mesai_kapaniyor(self.p, datetime.datetime.now(datetime.timezone.utc), neden="elle", bekle_onay=True)
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")
        self.assertContains(self.ofis_kes(), "karma kesimi adet bekliyor")
        self.sifir()

    def test_ayni_profili_kullanan_acik_tekil_oturum_engeller(self):
        self.baslat()
        self.client.post(reverse("core:atolye_oturum_baslat", args=[self.kie["KP1"].pk]), {}, HTTP_X_REAL_IP=IZINLI)
        self.assertIsNotNone(self.acik_oturum(self.kie["KP1"]))
        self.assertContains(self.ofis_kes(), "ekibi çalışıyor")
        self.sifir()

    def test_kapali_oturum_engellemez(self):
        o = self.hazir()
        self.karma_bitir(o, boy="1", adet=(("KP1", "5"),))                                                            # atölye kesimi bitti
        self.assertEqual(KarmaKesim.objects.count(), 1)
        self.ofis_kes()
        self.assertEqual(KarmaKesim.objects.count(), 2)
        self.assertEqual(KarmaKesim.objects.filter(kaynak="OFIS").count(), 1)
        self.assertEqual(KarmaKesim.objects.filter(kaynak="ATOLYE").count(), 1)                                       # atölye akışı kaynak ATOLYE

    def test_ayrilmis_profil_stogu_onay_kutusu(self):
        emir2 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.kmamul.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 11))
        a1, a2 = stok_ayirma.ayrilan_miktar(self.kemir, self.kprofil), stok_ayirma.ayrilan_miktar(emir2, self.kprofil)
        self.assertGreater(min(a1, a2), 0)
        hareket_ekle(stok_id=self.kprofil.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 20), tur=StokHareket.Tur.CIKIS, miktar=D("10") - (a1 + a2))   # eldeki = ayrılanların toplamı
        d = self.onizle(boy="3", **{f"a_{self.kparca['KP1'].pk}": "10"})
        self.assertTrue(d["profil_stok"]["onay_gerekli"], d["profil_stok"])
        r = self.ofis_kes(boy="3", adet=(("KP1", "10"),))
        self.assertContains(r, "AYRILMIŞ")
        self.assertContains(r, 'id="id_ayrilmis_onay"')
        self.sifir()
        r = self.ofis_kes(boy="3", adet=(("KP1", "10"),), ayrilmis_onay="on")
        self.assertEqual(KarmaKesim.objects.count(), 1)
        self.assertEqual(self.kesim().kaynak, "OFIS")


class GeriAlmaTest(OfisTaban):
    def test_grup_geri_alma_ofis_kesimi(self):
        self.ofis_kes()
        k = self.kesim()
        ie1 = self.kie["KP1"]
        self.client.force_login(self.ofis)
        r = self.client.post(reverse("core:karma_kesim_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("tüm kayıtları geri alındı" in str(m) for m in r.context["messages"]))
        k.refresh_from_db(); ie1.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertEqual(k.kayitlar.filter(silindi=False).count(), 0)
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("10"))
        self.assertEqual(eldeki_miktar(self.kparca["KP1"], self.depo), D("0"))
        self.assertEqual((ie1.planlanan, ie1.tamamlanan, ie1.durum), (D("1"), D("0"), IstasyonEmri.Durum.BEKLIYOR))
        self.assertContains(self.client.get(reverse("core:karma_kesim_detay", args=[k.pk])), "Geri alındı")
        self.assertEqual(self.client.post(reverse("core:karma_kesim_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI).status_code, 404)

    def test_atolye_kaynakli_kesim_de_detaydan_geri_alinabilir(self):
        o = self.hazir()
        self.karma_bitir(o)
        k = self.kesim()
        self.assertEqual(k.kaynak, "ATOLYE")
        self.client.force_login(self.ofis)
        self.client.post(reverse("core:karma_kesim_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_tekil_geri_al_sil_karma_kaydi_icin_engellenir(self):
        self.ofis_kes()
        k = self.kesim()
        kayit = k.kayitlar.first()
        self.client.force_login(self.ofis)
        r = self.client.post(reverse("core:operasyon_kaydi_geri_al_sil", args=[kayit.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertRedirects(r, reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertTrue(any("tek başına geri alınamaz" in str(m) for m in r.context["messages"]))
        kayit.refresh_from_db()
        self.assertEqual((kayit.silindi, kayit.durum), (False, "ONAYLI"))
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("8"))                                              # hiçbir şey geri alınmadı
        d = self.client.get(reverse("core:operasyon_kaydi_detay", args=[kayit.pk]))                                  # kayıt detayı: geri al düğmesi yok, karma kesime bağlantı var
        self.assertNotContains(d, "Geri al ve sil")
        self.assertContains(d, reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertContains(d, "Ofis karma kesim")

    def test_normal_kayit_geri_al_sil_calismaya_devam_eder(self):
        from core.services.uretim import operasyon_kaydi_olustur, operasyon_kaydi_onayla
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("5"), giris_tutar_try=D("50"))
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 15), hedef_cikti_miktari=D("2"))
        operasyon_kaydi_onayla(kayit)
        self.client.force_login(self.ofis)
        self.client.post(reverse("core:operasyon_kaydi_geri_al_sil", args=[kayit.pk]), HTTP_X_REAL_IP=IZINLI)
        kayit.refresh_from_db()
        self.assertTrue(kayit.silindi)


class ListeDetayTest(OfisTaban):
    def test_liste_filtre_ve_operasyon_kayitlari_rozeti(self):
        self.ofis_kes()
        o = self.hazir()
        self.karma_bitir(o, boy="1", adet=(("KP1", "5"),))
        ofis, atolye = KarmaKesim.objects.get(kaynak="OFIS"), KarmaKesim.objects.get(kaynak="ATOLYE")
        self.client.force_login(self.ofis)
        r = self.client.get(reverse("core:karma_kesimler"))
        self.assertContains(r, ofis.no)
        self.assertContains(r, atolye.no)
        r = self.client.get(reverse("core:karma_kesimler"), {"kaynak": "OFIS"})
        self.assertContains(r, ofis.no)
        self.assertNotContains(r, atolye.no)
        r = self.client.get(reverse("core:karma_kesimler"), {"kaynak": "ATOLYE", "ara": "KP1"})
        self.assertContains(r, atolye.no)
        self.assertNotContains(r, ofis.no)
        self.assertContains(self.client.get(reverse("core:karma_kesimler"), {"ara": "KP-PROFIL"}), ofis.no)
        r = self.client.get(reverse("core:operasyon_kayitlari"), {"emir": "karma_ofis"})
        self.assertContains(r, f"{ofis.no} · Ofis")
        self.assertNotContains(r, atolye.no)
        r = self.client.get(reverse("core:operasyon_kayitlari"), {"emir": "karma_atolye"})
        self.assertContains(r, f"{atolye.no} · Atölye")
        self.assertNotContains(r, ofis.no)
        r = self.client.get(reverse("core:operasyon_kayitlari"), {"emir": "karma"})
        self.assertContains(r, ofis.no)
        self.assertContains(r, atolye.no)

    def test_detay_fire_dokumu_parcalar_kayitlar_ve_geri_alinan_liste(self):
        self.ofis_kes()
        k = self.kesim()
        self.client.force_login(self.ofis)
        r = self.client.get(reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertContains(r, "Ofis karma kesim")
        self.assertContains(r, "Toplam fire")
        for deger in ("7.145,64", "60,00", "150,00", "4.644,36", "4.854,36", "40,45"):
            self.assertContains(r, deger)
        self.assertContains(r, "Serbest stok")
        self.assertContains(r, "iş oturumu ve işçilik süresi yok")
        self.assertContains(r, "Tümünü geri al")
        self.client.post(reverse("core:karma_kesim_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI)
        self.assertNotIn(k.pk, [x.pk for x in self.client.get(reverse("core:karma_kesimler")).context["kesimler"]])   # varsayılan liste geri alınanı göstermez
        self.assertIn(k.pk, [x.pk for x in self.client.get(reverse("core:karma_kesimler"), {"geri": "1"}).context["kesimler"]])

    def test_atolye_detayi_oturumu_gosterir(self):
        o = self.hazir()
        self.karma_bitir(o)
        k = self.kesim()
        self.client.force_login(self.ofis)
        r = self.client.get(reverse("core:karma_kesim_detay", args=[k.pk]))
        self.assertContains(r, "İş Oturumu")
        self.assertContains(r, "adam-saat")
        self.assertNotContains(r, "iş oturumu ve işçilik süresi yok")


class YetkiTest(OfisTaban):
    def test_yetkisiz_kullanici_giremez(self):
        self.client.force_login(self.yetkisiz)
        for ad, args in (("core:emirsiz_karma", []), ("core:emirsiz_karma_onizleme", []), ("core:karma_kesimler", [])):
            self.assertEqual(self.client.get(reverse(ad, args=args), HTTP_X_REAL_IP=IZINLI).status_code, 403, ad)
        self.assertEqual(self.client.post(reverse("core:emirsiz_karma"), {}, HTTP_X_REAL_IP=IZINLI).status_code, 403)
        self.sifir()

    def test_yalniz_emirsiz_yetkisi_kayit_girer_listeyi_goremez(self):
        self.client.force_login(self.sadece_emirsiz)
        self.assertEqual(self.client.get(reverse("core:emirsiz_karma"), HTTP_X_REAL_IP=IZINLI).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:karma_kesimler"), HTTP_X_REAL_IP=IZINLI).status_code, 403)
        r = self.ofis_kes(user=self.sadece_emirsiz)
        self.assertEqual(KarmaKesim.objects.count(), 1)
        self.assertEqual(self.client.get(reverse("core:karma_kesim_detay", args=[self.kesim().pk]), HTTP_X_REAL_IP=IZINLI).status_code, 403)
