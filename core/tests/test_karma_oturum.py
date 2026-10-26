"""KARMA KESİM oturumu + atölye ekranı (Dilim 2): istasyon başına TEK açık karma oturum, tek ekip + sorumlu kuralları (katıl / ayrıl / devret / iptal, adedi yalnız sorumlu girer),
Bitir → karma kesim yazılır ve oturum bağlanır (hata/bütçe aşımında oturum açık kalır), "Adet bekliyor" (mesai kapanışı) + sonradan adet girişi, emirli tekil oturumla çakışma
(iki yönde), ofis "Üretildi" engeli, canlı önizleme, bugünkü son karma kesimi geri alma, ofis/takip/pano/Excel ekranlarının karma oturumla çalışması."""
import datetime
from decimal import Decimal
from io import BytesIO

from django.urls import reverse

from core.models import Birim, FaturaTipi, HesapPlani, IsOturumu, IstasyonEmri, KarmaKesim, KategoriHesap, OperasyonKaydi, StokHareket
from core.services import atolye, is_oturumu, uretim
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import operasyon_olustur, uretim_emri_olustur
from core.tests.test_atolye import IZINLI
from core.tests.test_is_oturumu import OturumTaban
from core.tests.test_uretim import _stok

D = Decimal
PARCA = {"KP1": (D("349.73"), 16), "KP2": (D("368.61"), 16)}          # (parça mm, boy başına adet)


class KarmaOturumTaban(OturumTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        hesap = HesapPlani.objects.create(hesap_kodu="150.10", hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True)
        KategoriHesap.objects.create(kategori=cls.kat, hesap=hesap, fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS"))        # onayda maliyet aktarım fişi için stok hesabı
        cls.boy_birim = Birim.objects.create(ad="BOY", kisa_ad="BOY", ondalik=0)
        cls.kprofil = _stok(cls.kat, cls.boy_birim, kod="KP-PROFIL", ad="karma profil 6000", satinalma=True)
        cls.kprofil.boy_uzunluk_mm = D("6000")
        cls.kprofil.save()
        cls.kparca, cls.kop = {}, {}
        for kod, (mm, adet) in PARCA.items():
            st = _stok(cls.kat, cls.birim, kod=kod, ad=f"karma parça {kod}")
            cls.kparca[kod] = st
            cls.kop[kod] = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=st.pk, cikti_miktar=D(adet), boy_mm=mm, satirlar=[(cls.kprofil, D("1"))])
        cls.kmamul = _stok(cls.kat, cls.birim, kod="KP-MAMUL", ad="karma mamul", satis=True)
        operasyon_olustur(istasyon_id=cls.bukum.pk, cikti_id=cls.kmamul.pk, cikti_miktar=D("1"), satirlar=[(cls.kparca["KP1"], D("10")), (cls.kparca["KP2"], D("8"))])

    def setUp(self):
        super().setUp()
        hareket_ekle(stok_id=self.kprofil.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("10"), giris_tutar_try=D("12000"))
        self.kemir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.kmamul.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=datetime.date(2026, 1, 10))
        self.kie = {k: IstasyonEmri.objects.get(uretim_emri=self.kemir, operasyon=self.kop[k]) for k in PARCA}

    # --- yardımcılar ---
    def karma_basla(self, ekip=(), profil=None, ip=IZINLI, ist=None):
        return self.client.post(reverse("core:atolye_karma_basla", args=[(ist or self.lazer).pk]),
                                {"profil": (profil or self.kprofil).pk, "ekip": [str(p) for p in ekip]}, HTTP_X_REAL_IP=ip, follow=True)

    def karma_oturum(self, durum="ACIK"):
        return IsOturumu.objects.filter(karma=True, durum=durum).order_by("-pk").first()

    def karma_bitir(self, oturum, boy="2", adet=(("KP1", "12"), ("KP2", "8")), ip=IZINLI, **ek):
        veri = {"boy": boy, **{f"a_{self.kparca[k].pk}": v for k, v in adet}, **ek}
        return self.client.post(reverse("core:atolye_karma_bitir", args=[oturum.pk]), veri, HTTP_X_REAL_IP=ip, follow=True)

    def hazir(self):
        """errahman mesaide + karma kesim başlatılmış (tek başına)."""
        self.baslat()
        self.karma_basla()
        return self.karma_oturum()


class BaslatKuraliTest(KarmaOturumTaban):
    def test_baslat_ekip_ve_istasyon_basina_tek_acik(self):
        self.baslat(self.u2)
        self.baslat()
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "KARMA KESİM BAŞLA")
        self.assertContains(r, "KP-PROFIL")
        r = self.karma_basla(ekip=[self.p2.pk])
        self.assertTrue(any("Karma kesim (KP-PROFIL) başladı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o = self.karma_oturum()
        self.assertEqual((o.istasyon_id, o.karma_profil_id, o.istasyon_emri_id, o.uretim_emri_id, o.sorumlu_id, o.baslatan_id, o.durum),
                         (self.lazer.pk, self.kprofil.pk, None, None, self.p.pk, self.p.pk, "ACIK"))
        self.assertEqual(sorted(u.personel_id for u in o.uyeler.all()), sorted([self.p.pk, self.p2.pk]))
        self.assertContains(r, "KARMA KESİMİ BİTİR")                                                    # sorumlu açık işler kartında formu görür
        self.assertContains(r, "Kullanılan boy sayısı")
        self.baslat(self.u3)
        r = self.karma_basla()
        self.assertTrue(any("karma kesim çalışıyor" in m for m in self.mesajlar(r)), self.mesajlar(r))     # istasyonda ikinci açık karma yok
        self.assertEqual(IsOturumu.objects.filter(karma=True).count(), 1)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "EKİBE KATIL")                                                           # mehmet katılabilir
        self.assertNotContains(r, "KARMA KESİMİ BAŞLAT")

    def test_mesai_acik_degilse_ve_profil_uygun_degilse_baslamaz(self):
        self.client.force_login(self.u)
        r = self.karma_basla()                                                                          # errahman mesaisiz
        self.assertTrue(any("mesai" in m.lower() for m in self.mesajlar(r)), self.mesajlar(r))
        self.baslat()
        self.kprofil.boy_uzunluk_mm = None
        self.kprofil.save()
        r = self.karma_basla()
        self.assertTrue(any("karma kesim yapılamaz" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(IsOturumu.objects.count(), 0)

    def test_fabrika_disi_ip_reddedilir(self):
        self.baslat()
        r = self.karma_basla(ip="8.8.8.8")
        self.assertEqual(IsOturumu.objects.filter(karma=True).count(), 0)

    def test_tekil_oturum_varken_karma_acilmaz(self):
        self.baslat()
        self.baslat(self.u2)
        self.client.force_login(self.u2)
        self.client.post(reverse("core:atolye_oturum_baslat", args=[self.kie["KP1"].pk]), {}, HTTP_X_REAL_IP=IZINLI)        # ayşe (operatör) KP1 emrinde tekil iş açar
        self.assertIsNotNone(self.acik_oturum(self.kie["KP1"]))
        self.client.force_login(self.u)
        r = self.karma_basla()
        self.assertTrue(any("karma kesim açılamaz" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(IsOturumu.objects.filter(karma=True).count(), 0)

    def test_karma_acikken_ayni_profili_kullanan_emirde_tekil_is_acilmaz_ve_ofis_uretildi_engellenir(self):
        self.baslat(self.u2)
        self.karma_basla()                                                                              # ayşe karma kesimi başlatır
        self.baslat()
        r = self.client.post(reverse("core:atolye_oturum_baslat", args=[self.kie["KP1"].pk]), {}, HTTP_X_REAL_IP=IZINLI, follow=True)       # errahman KP1 emrinde tekil iş açmak ister
        self.assertTrue(any("KARMA KESİM çalışıyor" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertIsNone(self.acik_oturum(self.kie["KP1"]))
        with self.assertRaisesMessage(uretim.UretimHatasi, "ekibi çalışıyor"):                           # ofis "Üretildi" de engelli (süper kullanıcı notla aşabilir)
            uretim.istasyon_emri_uret(self.kie["KP1"], miktar=D("5"))
        self.assertEqual(OperasyonKaydi.objects.filter(istasyon_emri=self.kie["KP1"]).count(), 0)
        # başka profili kullanan emir (IK bükümü) etkilenmez
        self.client.post(reverse("core:atolye_oturum_baslat", args=[self.ie_bukum.pk]), {}, HTTP_X_REAL_IP=IZINLI)
        self.assertIsNotNone(self.acik_oturum(self.ie_bukum))


class EkipKurallariTest(KarmaOturumTaban):
    def test_katil_ayril_devret_iptal(self):
        o = self.hazir()
        self.baslat(self.u3)
        r = self.client.post(reverse("core:atolye_oturum_katil", args=[o.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("ekibe katıldınız" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 2)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))                       # üye: Bitir göremez, Ayrıl görür
        self.assertNotContains(r, "KARMA KESİMİ BİTİR")
        self.assertContains(r, "Ekipten ayrıl")
        r = self.karma_bitir(o)                                                                         # üye adet giremez
        self.assertTrue(any("yalnızca sorumlu" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual((self.karma_oturum().pk, KarmaKesim.objects.count()), (o.pk, 0))
        self.client.post(reverse("core:atolye_oturum_ayril", args=[o.pk]), HTTP_X_REAL_IP=IZINLI)
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 1)
        self.baslat(self.u2)                                                                              # ayşe katılır; sorumlu ona devreder
        self.client.post(reverse("core:atolye_oturum_katil", args=[o.pk]), HTTP_X_REAL_IP=IZINLI)
        self.client.force_login(self.u)
        self.client.post(reverse("core:atolye_oturum_devret", args=[o.pk]), {"yeni": self.p2.pk}, HTTP_X_REAL_IP=IZINLI)
        o.refresh_from_db()
        self.assertEqual(o.sorumlu_kisi.pk, self.p2.pk)
        r = self.karma_bitir(o)                                                                         # eski sorumlu artık giremez
        self.assertTrue(any("yalnızca sorumlu" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.client.force_login(self.u2)
        self.client.post(reverse("core:atolye_oturum_iptal", args=[o.pk]), HTTP_X_REAL_IP=IZINLI)       # yeni sorumlu iptal eder: kayıt yok, süre kalır
        o.refresh_from_db()
        self.assertEqual((o.durum, o.karma_kesim_id, KarmaKesim.objects.count()), ("IPTAL", None, 0))
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 0)

    def test_tekil_bitir_ve_adet_gir_karma_oturumda_reddedilir(self):
        o = self.hazir()
        r = self.bitir(o, "5")
        self.assertTrue(any("karma kesim formundan" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual((self.karma_oturum().pk, StokHareket.objects.filter(operasyon_kaydi__isnull=False).count()), (o.pk, 0))


class BitirTest(KarmaOturumTaban):
    def test_bitir_karma_kesimi_yazar_ve_oturumu_baglar(self):
        o = self.hazir()
        r = self.karma_bitir(o)                                                                         # KP1×12 (10 emre + 2 serbest), KP2×8 → 2 boy
        self.assertTrue(any("Karma kesim KK-" in m and "2 boy, 20 parça" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        k = o.karma_kesim
        self.assertEqual((o.durum, o.miktar, o.bitiren_id), ("BITTI", D("20"), self.p.pk))
        self.assertEqual((k.boy_sayisi, k.toplam_adet, k.personel_id, k.istasyon_id), (2, 20, self.p.pk, self.lazer.pk))
        self.assertEqual(o.uyeler.filter(ayrilma_zamani__isnull=True).count(), 0)
        self.assertTrue(all(kk.personel_id == self.p.pk for kk in k.kayitlar.all()))
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("8"))
        self.assertEqual(eldeki_miktar(self.kparca["KP1"], self.depo), D("12"))
        ie1, ie2 = self.kie["KP1"], self.kie["KP2"]
        ie1.refresh_from_db(); ie2.refresh_from_db()
        self.assertEqual((ie1.durum, ie2.durum), (IstasyonEmri.Durum.BITTI, IstasyonEmri.Durum.BITTI))      # ihtiyaç karşılandı → planlanan daraldı
        self.assertEqual(k.kayitlar.filter(istasyon_emri__isnull=True).count(), 1)                          # KP1'in 2 fazlası emirsiz → serbest stok
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "Son karma kesimim")
        self.assertContains(r, k.no)

    def test_butce_asimi_oturum_acik_kalir_hicbir_sey_yazilmaz(self):
        o = self.hazir()
        r = self.karma_bitir(o, boy="1", adet=(("KP1", "16"), ("KP2", "2")))                            # 16×349,73 + 2×368,61 + 54 > 5.925
        self.assertTrue(any("Kesim uzunluğu aşıldı" in m and "iş oturumu açık kaldı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual((self.karma_oturum().pk, KarmaKesim.objects.count(), OperasyonKaydi.objects.filter(karma__isnull=False).count()), (o.pk, 0, 0))
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("10"))
        r = self.karma_bitir(o, boy="1", adet=(("KP1", "10"), ("KP2", "4")))                            # düzeltilince aynı oturum bitirilebilir
        self.assertEqual(self.karma_oturum("BITTI").pk, o.pk)

    def test_bos_boy_ve_adet_ve_kesirli_deger_mesajlari(self):
        o = self.hazir()
        for boy, adet, parca in (("", (("KP1", "3"),), "boy sayısını girin"), ("1", (), "En az bir parça"), ("1", (("KP1", "2,5"),), "tam sayı"), ("1,5", (("KP1", "3"),), "tam sayı")):
            r = self.karma_bitir(o, boy=boy, adet=adet)
            self.assertTrue(any(parca in m for m in self.mesajlar(r)), (boy, adet, self.mesajlar(r)))
        self.assertEqual((self.karma_oturum().pk, KarmaKesim.objects.count()), (o.pk, 0))

    def test_yetersiz_profil_stogu_oturum_acik_kalir(self):
        o = self.hazir()
        r = self.karma_bitir(o, boy="12", adet=(("KP1", "80"), ("KP2", "80")))                         # bütçeye sığar ama 12 boy yok: stok 10
        self.assertEqual((self.karma_oturum().pk, KarmaKesim.objects.count()), (o.pk, 0))
        self.assertTrue(any("❌" in m for m in self.mesajlar(r)), self.mesajlar(r))

    def test_fabrika_disi_bitir_reddedilir(self):
        o = self.hazir()
        r = self.karma_bitir(o, ip="8.8.8.8")
        self.assertEqual((self.karma_oturum().pk, KarmaKesim.objects.count()), (o.pk, 0))


class AdetBekliyorTest(KarmaOturumTaban):
    def test_mesai_kapanisi_adet_bekliyor_sonra_adet_girilir(self):
        o = self.hazir()
        r = self.client.post(reverse("core:atolye_mesai"), {"eylem": "bitir", "miktar": "5"}, HTTP_X_REAL_IP=IZINLI, follow=True)            # karma'da tek adet yetmez
        self.assertTrue(any("karma kesim" in m.lower() for m in self.mesajlar(r)), self.mesajlar(r))
        self.assertEqual(self.karma_oturum().pk, o.pk)
        r = self.client.get(reverse("core:atolye_mesai_bitir"))                                         # karma için adet/fire kutusu yok, yalnız "Adet bekliyor" onayı
        self.assertNotContains(r, 'name="miktar"')
        self.assertContains(r, 'name="bekle"')
        self.client.post(reverse("core:atolye_mesai"), {"eylem": "bitir", "bekle": "1"}, HTTP_X_REAL_IP=IZINLI, follow=True)
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")
        self.baslat()                                                                                    # sonraki girişte kart + istasyon sayfası
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "ADET BEKLİYOR")
        self.assertContains(r, "KARMA KESİMİ GİR")
        r = self.karma_bitir(o)
        self.assertTrue(any("Karma kesim KK-" in m for m in self.mesajlar(r)), self.mesajlar(r))
        o.refresh_from_db()
        self.assertEqual((o.durum, o.miktar, o.karma_kesim.tarih, o.bitiren_id), ("BITTI", D("20"), o.bitis_zamani.astimezone(is_oturumu._TR).date(), self.p.pk))

    def test_yonetici_karma_adet_girisi_yonlendirilir_iptal_edebilir(self):
        o = self.hazir()
        is_oturumu.mesai_kapaniyor(self.p, datetime.datetime.now(datetime.timezone.utc), neden="elle", bekle_onay=True)
        o.refresh_from_db()
        self.assertEqual(o.durum, "ADET")
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:is_oturumu_yonetici_islem", args=[o.pk]), {"eylem": "adet_gir", "miktar": "5"}, follow=True)
        self.assertTrue(any("karma kesim formundan" in m for m in self.mesajlar(r)), self.mesajlar(r))
        self.client.post(reverse("core:is_oturumu_yonetici_islem", args=[o.pk]), {"eylem": "iptal", "aciklama": "test"})
        o.refresh_from_db()
        self.assertEqual(o.durum, "IPTAL")


class OnizlemeGeriAlTest(KarmaOturumTaban):
    def onizle(self, **q):
        return self.client.get(reverse("core:atolye_karma_onizleme", args=[self.lazer.pk]), {"profil": self.kprofil.pk, **q}, HTTP_X_REAL_IP=IZINLI).json()

    def test_onizleme_fire_dokumu_ve_dagilim_hicbir_sey_yazmaz(self):
        self.baslat()
        d = self.onizle(boy="2", **{f"a_{self.kparca['KP1'].pk}": "12", f"a_{self.kparca['KP2'].pk}": "8"})
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["toplam_adet"], d["testere_mm"], d["cene_mm"]), (20, "60,00", "150,00"))
        self.assertEqual(d["parca_mm"], "7.145,64")                                                      # 12×349,73 + 8×368,61
        self.assertEqual(d["fire_mm"], "4.854,36")
        self.assertEqual(d["uc_mm"], "4.644,36")                                                         # fire − testere − çene
        self.assertEqual(d["fire_yuzde"], "40,45")
        dag = {(x["kod"], x["emir"]): x["adet"] for x in d["dagilim"]}
        self.assertEqual(dag[("KP1", "serbest stok")], 2)
        self.assertEqual(dag[("KP1", f"{self.kemir.no} / {self.kie['KP1'].no}")], 10)
        self.assertEqual((KarmaKesim.objects.count(), OperasyonKaydi.objects.filter(karma__isnull=False).count()), (0, 0))

    def test_onizleme_hata_ve_bos_durumlari(self):
        self.baslat()
        self.assertEqual(self.onizle(boy="1"), {"ok": False, "hata": ""})                                # parça yok: sessiz
        d = self.onizle(**{f"a_{self.kparca['KP1'].pk}": "3"})
        self.assertEqual((d["ok"], d["hata"]), (False, "Kullanılan boy sayısını girin."))
        d = self.onizle(boy="1", **{f"a_{self.kparca['KP1'].pk}": "16", f"a_{self.kparca['KP2'].pk}": "3"})
        self.assertFalse(d["ok"])
        self.assertIn("Kesim uzunluğu aşıldı", d["hata"])
        self.assertEqual(self.client.get(reverse("core:atolye_karma_onizleme", args=[self.lazer.pk]), HTTP_X_REAL_IP=IZINLI).json()["hata"], "Profil seçilmedi.")

    def test_son_karma_kesimi_geri_al(self):
        o = self.hazir()
        self.karma_bitir(o)
        k = KarmaKesim.objects.get()
        ie1 = self.kie["KP1"]
        r = self.client.post(reverse("core:atolye_karma_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("geri alındı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        k.refresh_from_db(); ie1.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertEqual(eldeki_miktar(self.kprofil, self.depo), D("10"))
        self.assertEqual(eldeki_miktar(self.kparca["KP1"], self.depo), D("0"))
        self.assertEqual((ie1.planlanan, ie1.tamamlanan, ie1.durum), (D("1"), D("0"), IstasyonEmri.Durum.BEKLIYOR))
        # başkası geri alamaz / ikinci kez alınamaz
        self.baslat(self.u2)
        self.client.force_login(self.u2)
        r = self.client.post(reverse("core:atolye_karma_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI)
        self.assertEqual(r.status_code, 404)

    def test_baskasinin_karma_kesimi_geri_alinamaz(self):
        o = self.hazir()
        self.karma_bitir(o)
        k = KarmaKesim.objects.get()
        self.baslat(self.u3)
        r = self.client.post(reverse("core:atolye_karma_geri_al", args=[k.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("SON karma kesiminizi" in m for m in self.mesajlar(r)), self.mesajlar(r))
        k.refresh_from_db()
        self.assertFalse(k.silindi)

    def test_karma_kaydi_tekil_geri_al_butun_grubu_geri_alir(self):
        o = self.hazir()
        self.karma_bitir(o)
        k = KarmaKesim.objects.get()
        kayit = k.kayitlar.filter(istasyon_emri__isnull=False).first()
        r = self.client.post(reverse("core:atolye_geri_al", args=[kayit.pk]), HTTP_X_REAL_IP=IZINLI, follow=True)
        self.assertTrue(any("tüm kayıtları geri alındı" in m for m in self.mesajlar(r)), self.mesajlar(r))
        k.refresh_from_db()
        self.assertTrue(k.silindi)
        self.assertEqual(k.kayitlar.filter(silindi=False).count(), 0)


class EkranlarTest(KarmaOturumTaban):
    def test_ofis_ve_takip_ekranlari_karma_oturumla_calisir(self):
        o = self.hazir()
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:is_oturumlari"), {"durum": "ACIK"})
        self.assertContains(r, "Karma kesim")
        self.assertContains(r, "KP-PROFIL")
        r = self.client.get(reverse("core:is_oturumlari"), {"xlsx": "1"})
        self.assertEqual(r.status_code, 200)
        import openpyxl
        ws = openpyxl.load_workbook(BytesIO(r.content)).active
        self.assertIn("Karma kesim", [c.value for row in ws.iter_rows(min_row=5, max_row=5) for c in row])
        r = self.client.get(reverse("core:atolye_takip"), {"sekme": "simdi"})
        self.assertContains(r, "emire bağlı değil")
        self.assertContains(r, "KP-PROFIL")
        self.client.force_login(self.u)
        self.karma_bitir(o)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:atolye_takip"), {"sekme": "gecmis", "aralik": "bugun"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Karma kesim")
        k = KarmaKesim.objects.get()
        self.assertContains(self.client.get(reverse("core:is_oturumlari"), {"ara": k.no}), k.no)           # karma kesim no ile aranır
        self.assertEqual(self.client.get(reverse("core:atolye_takip"), {"sekme": "gecmis", "aralik": "bugun", "xlsx": "1"}).status_code, 200)
        # karma kayıtları oturum satırıyla birlikte gösterilir: ayrıca "ofis" satırı olarak çıkmaz
        from core.services import is_takibi
        satirlar = is_takibi.gecmis(baslangic=datetime.date.today() - datetime.timedelta(days=1), bitis=datetime.date.today() + datetime.timedelta(days=1))
        self.assertEqual([s["tur"] for s in satirlar].count("kayit"), 0)

    def test_pano_adet_bekleyen_karma_ve_acik_isler_karti(self):
        o = self.hazir()
        is_oturumu.mesai_kapaniyor(self.p, datetime.datetime.now(datetime.timezone.utc), neden="elle", bekle_onay=True)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "Karma kesim")
        self.baslat()                                                                                    # sorumlu ana ekranda kartı görür
        r = self.client.get(reverse("core:atolye"))
        self.assertContains(r, "Karma kesim")
        self.assertContains(r, "KARMA KESİMİ GİR")
        self.assertContains(r, "ADET BEKLİYOR")

    def test_yonetici_izleme_karma_bolumunu_gorur_ama_baslatamaz(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.lazer.pk]))
        self.assertContains(r, "KARMA KESİM")
        self.assertNotContains(r, "KARMA KESİM BAŞLA")

    def test_karma_olmayan_istasyonda_bolum_gorunmez(self):
        self.baslat()
        r = self.client.get(reverse("core:atolye_istasyon", args=[self.bukum.pk]))
        self.assertNotContains(r, "KARMA KESİM")


class OturumModelTest(KarmaOturumTaban):
    def test_veritabani_kisitlari(self):
        from django.db import IntegrityError, transaction
        o = self.hazir()
        with self.assertRaises(IntegrityError), transaction.atomic():                                  # istasyon başına tek açık karma (kısmi benzersizlik)
            IsOturumu.objects.create(istasyon=self.lazer, karma=True, karma_profil=self.kprofil, baslatan=self.p2, is_tarihi=o.is_tarihi, baslangic_zamani=o.baslangic_zamani)
        with self.assertRaises(IntegrityError), transaction.atomic():                                  # karma olmayan oturum emirsiz olamaz
            IsOturumu.objects.create(istasyon=self.bukum, karma=False, baslatan=self.p2, is_tarihi=o.is_tarihi, baslangic_zamani=o.baslangic_zamani)

    def test_etiketler(self):
        o = self.hazir()
        self.assertEqual((o.us_no, o.is_kodu, o.emir_no, o.is_adi), ("Karma kesim", "KP-PROFIL", "", "karma profil 6000"))
        self.karma_bitir(o)
        o.refresh_from_db()
        self.assertTrue(o.emir_no.startswith("KK-"))
        self.baslat(self.u2)
        self.client.force_login(self.u2)
        self.client.post(reverse("core:atolye_oturum_baslat", args=[self.ie_bukum.pk]), {}, HTTP_X_REAL_IP=IZINLI)
        t = self.acik_oturum(self.ie_bukum)
        self.assertEqual((t.us_no, t.is_kodu, t.emir_no), (self.emir.no, "IK-BUKULMUS", self.ie_bukum.no))
