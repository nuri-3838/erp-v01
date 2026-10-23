"""FASON > Fason Siparişleri (FS): belge (taslak/gönder/durum/iptal/PDF), kesim listesinden ön dolu form, planlamada "gelecek stok" (fason emri açılmaz,
ÜS rezervi), FD bağı (gelen → satır, rezerv → stok ayırması, fazla gelen uyarı, geri al), FS iptalinde rezerv geri alma + ÜS "fason emri gerekli",
revizede kendi rezervi, ÜS iptalinde rezerv silme, kritik stok "gelecek", ekranlar. Fixture: FasonBase (profil → AYAK66 3 adet/boy + yan AYAK55)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import FasonSiparisi, FasonSiparisRezerv, IstasyonEmri, Stok, UretimEmri
from core.services import fason_donus as fd
from core.services import fason_siparis as fss
from core.services import kritik_stok, planlama_varyant
from core.services import stok_ayirma as sa
from core.services.fason import FasonHatasi
from core.services.hareket import hareket_ekle
from core.services.uretim import istasyon_guncelle, operasyon_olustur, uretim_emri_iptal, uretim_emri_olustur, uretim_emri_revize, uretim_emri_eksikleri, uretim_emri_eksik_tablosu, fason_emri_ac
from core.tests.test_fason_donus import TARIH, FasonBase
from core.tests.test_uretim import _istasyon

D = Decimal
GUN = date(2026, 10, 12)


class FSBase(FasonBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        istasyon_guncelle(cls.kesim, kod=cls.kesim.kod, ad=cls.kesim.ad, fason=True, fasoncu_id=cls.salim.pk)      # kesim = fason istasyonu
        cls.yon = User.objects.create_superuser("fsyon", password="x")
        # Kesilmiş parça (AYAK66) bir MAMULÜN bileşenidir: montaj (fason olmayan istasyon) 1 ayak → 1 mamul; ÜS'ler mamul için açılır
        # (stok için ÜS'de kök hedef kendi stoğundan/FS'den karşılanmaz — kural 2026-10-12).
        cls.montaj = _istasyon("MONTAJ")
        cls.mamul = cls._stok("MAMUL66", cls.kat_ana)
        cls.mamul.satis_urunu = True
        cls.mamul.save()
        cls.montaj_op = operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"), satirlar=[(cls.ana, D("1"))])

    def fs(self, adet="10", gonder=True, termin=None, yan=None):
        satirlar = [(self.ana.pk, adet)] + ([(self.yan.pk, yan)] if yan else [])
        s = fss.siparis_olustur(cari_id=self.salim.pk, tarih=GUN, termin=termin, satirlar=satirlar, kullanici=self.yon)
        return fss.gonder(s) if gonder else s

    def us(self, adet="6"):
        return uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": adet}], depo_id=self.depo.pk, tarih=GUN)

    def fason_ie(self, emir):
        return emir.istasyon_emirleri.filter(silindi=False, operasyon=self.op).exclude(durum="IPTAL").first()

    def rezerv(self, emir, s=None):
        qs = fss.aktif_rezervler().filter(uretim_emri=emir)
        if s is not None:
            qs = qs.filter(satir__siparis=s)
        return sum((r.miktar for r in qs), D("0"))


class BelgeTest(FSBase):
    def test_olustur_profil_fiyat_snapshot_gonder_iptal(self):
        self.fiyatlar(ana="10", yan="6")
        s = self.fs(adet="7", gonder=False, termin=date(2026, 10, 20), yan="2")
        self.assertEqual((s.no, s.durum, s.cari_id, s.termin), ("FS-2026-0001", "TASLAK", self.salim.pk, date(2026, 10, 20)))
        a, y = s.satirlar.order_by("sira")
        self.assertEqual((a.stok_id, a.adet, a.gelen, a.profil_id, a.profil_miktar, a.birim_fiyat, a.para_birimi), (self.ana.pk, D("7"), D("0"), self.profil.pk, D("3"), D("10"), "TRY"))   # ⌈7/3⌉ = 3 boy
        self.assertEqual((y.stok_id, y.birim_fiyat), (self.yan.pk, D("6")))
        t = fss.siparis_toplamlari(s)
        self.assertEqual((t["adet"], t["kalan"], t["tutar"]), (D("9"), D("9"), {"TRY": D("82")}))                  # 7×10 + 2×6
        self.assertEqual(fss.acik_kalan_haritasi(), {})                                                           # taslak planlamaya girmez
        fss.gonder(s)
        self.assertEqual((s.durum, fss.acik_kalan_haritasi()), ("GONDERILDI", {(self.ana.pk, None): D("7"), (self.yan.pk, None): D("2")}))
        with self.assertRaisesMessage(FasonHatasi, "taslak"):
            fss.gonder(s)
        self.assertEqual(fss.iptal(s), [])
        self.assertEqual((s.durum, fss.acik_kalan_haritasi()), ("IPTAL", {}))
        with self.assertRaisesMessage(FasonHatasi, "iptal edilemez"):
            fss.iptal(s)

    def test_dogrulamalar(self):
        with self.assertRaisesMessage(FasonHatasi, "En az bir satır"):
            fss.siparis_olustur(cari_id=self.salim.pk, tarih=GUN, satirlar=[])
        with self.assertRaisesMessage(FasonHatasi, "tekrarlanamaz"):
            fss.siparis_olustur(cari_id=self.salim.pk, tarih=GUN, satirlar=[(self.ana.pk, "1"), (self.ana.pk, "2")])
        with self.assertRaisesMessage(FasonHatasi, "sıfırdan büyük"):
            fss.siparis_olustur(cari_id=self.salim.pk, tarih=GUN, satirlar=[(self.ana.pk, "0")])
        s = self.fs(gonder=False)
        with self.assertRaisesMessage(FasonHatasi, "Satırı olmayan"):
            s.satirlar.update(silindi=True)
            fss.gonder(s)


class PlanlamaTest(FSBase):
    def test_fason_siparisi_gelecek_stok_emir_acilmaz_rezerv_yazilir(self):
        s = self.fs(adet="10")
        e1 = self.us("6")
        self.assertIsNone(self.fason_ie(e1))                                                            # fason emri AÇILMADI
        self.assertEqual((self.rezerv(e1, s), sa.ayrilan_miktar(e1, self.ana)), (D("6"), D("0")))        # FS rezervi 6, stok ayırması 0
        self.assertEqual(fss.satir_kalan(s.satirlar.get()), D("4"))
        self.assertEqual(e1.revizyonlar.get().detay["fason_rezervleri"], [{"fs": s.no, "stok": "AYAK66", "varyant": None, "miktar": "6"}])
        e2 = self.us("6")                                                                                 # kalan 4 FS'den, 2 fason ihtiyacı (tam boy → 1 boy = 3 adet)
        self.assertIsNone(self.fason_ie(e2))                                                              # 2026-10-23: fason emri KENDİLİĞİNDEN açılmaz
        self.assertEqual([(r["tur"], r["eksik"]) for r in uretim_emri_eksik_tablosu(e2) if r["tur"] == "FASON"], [("FASON", D("2"))])
        ie = fason_emri_ac(e2, operasyon_id=self.op.pk, kullanici=self.yon)                               # yönetici düğmesi
        self.assertEqual((self.rezerv(e2, s), ie.planlanan, ie.ihtiyac), (D("4"), D("1"), {str(self.ana.pk): "2"}))
        self.assertEqual(fss.satir_kalan(s.satirlar.get()), D("0"))
        eks = uretim_emri_eksikleri(e1)
        self.assertEqual([(x["tur"], x["stok"].kod, x["gerekli"], x["fs"].no) for x in eks], [("FASON_SIPARIS", "AYAK66", D("6"), s.no)])
        uretim_emri_iptal(e1)                                                                            # ÜS iptal: rezerv serbest
        self.assertEqual((self.rezerv(e1), fss.satir_kalan(s.satirlar.get())), (D("0"), D("6")))

    def test_serbest_stok_once_sonra_fason_siparisi(self):
        hareket_ekle(stok_id=self.ana.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur="GIRIS", miktar=D("4"), giris_tutar_try=D("40"))
        s = self.fs(adet="10")
        e = self.us("6")
        self.assertEqual((sa.ayrilan_miktar(e, self.ana), self.rezerv(e, s)), (D("4"), D("2")))           # önce stok 4, sonra FS 2
        self.assertIsNone(self.fason_ie(e))

    def test_revize_kendi_rezervini_kullanir_ve_azalisi_serbest_birakir(self):
        s = self.fs(adet="10")
        e = self.us("6")
        uretim_emri_revize(e, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "8"}], tarih=GUN)
        self.assertEqual((self.rezerv(e, s), self.fason_ie(e)), (D("8"), None))
        uretim_emri_revize(e, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "3"}], tarih=GUN)
        self.assertEqual((self.rezerv(e, s), fss.satir_kalan(s.satirlar.get())), (D("3"), D("7")))

    def test_fs_iptal_rezerv_geri_alinir_us_fason_emri_gerekli_uyarisi(self):
        s = self.fs(adet="10")
        e = self.us("6")
        self.assertEqual(fss.iptal(s), [e])                                                              # etkilenen ÜS
        self.assertEqual(self.rezerv(e), D("0"))
        eks = uretim_emri_eksikleri(e)
        self.assertEqual([(x["tur"], x["stok"].kod, x["eksik"]) for x in eks if x["tur"] != "SATINALMA"],
                         [("FASON_EMRI_GEREKLI", "AYAK66", D("6"))])                                      # plan değişmedi, uyarı (profil satınalma satırı ayrıca)
        self.assertIsNone(self.fason_ie(e))
        uretim_emri_revize(e, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "6"}], tarih=GUN)                     # revize fason emri AÇMAZ
        self.assertIsNone(self.fason_ie(e))
        fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)                                                          # yönetici açar
        self.assertEqual(self.fason_ie(e).planlanan, D("2"))

    def test_adet_dusurme_fazla_rezervi_salar_gelenin_altina_inemez(self):
        s = self.fs(adet="10")
        e = self.us("6")
        _s, etkilenen = fss.siparis_guncelle(s, cari_id=self.salim.pk, tarih=GUN, satirlar=[(self.ana.pk, "4")])
        self.assertEqual((etkilenen, self.rezerv(e, s)), ([e], D("4")))
        s.satirlar.update(gelen=D("5"))
        with self.assertRaisesMessage(FasonHatasi, "gelen adedin"):
            fss.siparis_guncelle(s, cari_id=self.salim.pk, tarih=GUN, satirlar=[(self.ana.pk, "3")])

    def test_varyantli_planlama_acikken_de_calisir(self):
        planlama_varyant.anahtar_ayarla(True)
        try:
            s = self.fs(adet="10")
            e = self.us("6")
            self.assertTrue(e.varyantli_plan)
            self.assertEqual((self.rezerv(e, s), self.fason_ie(e)), (D("6"), None))
            self.assertEqual(uretim_emri_eksikleri(e)[0]["tur"], "FASON_SIPARIS")
        finally:
            planlama_varyant.anahtar_ayarla(False)

    def test_kritik_stok_gelecek_fason_siparisini_sayar_oneri_baglantisi(self):
        Stok.objects.filter(pk=self.ana.pk).update(kritik_stok=D("20"))
        self.ana.refresh_from_db()
        once = kritik_stok.hesapla([self.ana])[self.ana.pk]
        self.assertEqual((once["gelecek"], once["kalan"], once["oneri"]["tur"]), (D("0"), D("0"), "FASON"))
        self.assertIn(f"{reverse('core:fason_siparis_ekle')}?stok={self.ana.pk}&adet=20", once["oneri"]["url"])
        self.fs(adet="10")
        sonra = kritik_stok.hesapla([self.ana])[self.ana.pk]
        self.assertEqual((sonra["gelecek"], sonra["kalan"], sonra["eksik"]), (D("10"), D("10"), D("10")))


class DonusBagiTest(FSBase):
    def setUp(self):
        self.fason_profil(10, 6400, 160)
        self.fiyatlar()

    def donus(self, s, gelen=None, onayla=True, cari=None):
        return fd.donus_olustur(cari_id=(cari or self.salim).pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "6", gelen)],
                                onayla=onayla, fason_siparis_id=s.pk)

    def test_gelen_satira_isler_rezerv_ayirmaya_donusur_durumlar(self):
        s = self.fs(adet="10", yan="3")
        e = self.us("6")
        self.assertEqual((self.rezerv(e, s), sa.ayrilan_miktar(e, self.ana)), (D("6"), D("0")))
        d = self.donus(s)                                                                                # 6 ana + 2 yan geldi
        self.assertEqual(d.fason_siparis_id, s.pk)
        self.assertEqual(d.fason_uyarilari, [])
        s.refresh_from_db()
        satir = {x.stok_id: x for x in s.satirlar.all()}
        self.assertEqual((satir[self.ana.pk].gelen, satir[self.yan.pk].gelen, s.durum), (D("6"), D("2"), "KISMI"))
        self.assertEqual((self.rezerv(e, s), sa.ayrilan_miktar(e, self.ana)), (D("0"), D("6")))         # rezerv → gerçek ayırma
        self.assertEqual(fss.satir_kalan(satir[self.ana.pk]), D("4"))
        d2 = self.donus(s, gelen="4")                                                                     # 4 ana (fire 2) + 2 yan → yan 4 > 3: fazla uyarı
        s.refresh_from_db()
        self.assertEqual(s.durum, "TAMAMLANDI")
        self.assertTrue(any("aşıyor" in u for u in d2.fason_uyarilari), d2.fason_uyarilari)
        self.assertEqual(sa.ayrilan_miktar(e, self.ana), D("6"))                                           # rezerv kalmamıştı; fazlası serbest
        fd.donus_geri_al_sil(d2)                                                                           # geri al: gelen düşer, ayırma geri alınmaz
        s.refresh_from_db()
        satir = {x.stok_id: x for x in s.satirlar.all()}
        self.assertEqual((satir[self.ana.pk].gelen, satir[self.yan.pk].gelen, s.durum), (D("6"), D("2"), "KISMI"))

    def test_siparissiz_donus_eskisi_gibi_ve_dogrulamalar(self):
        s = self.fs(adet="10")
        d = fd.donus_olustur(cari_id=self.salim.pk, depo_id=self.depo.pk, tarih=TARIH, satirlar=[(self.op.pk, "3")], onayla=True)
        self.assertIsNone(d.fason_siparis_id)
        self.assertEqual(d.fason_uyarilari, [])
        self.assertEqual(s.satirlar.get().gelen, D("0"))
        from core.models import Depo
        Depo.objects.create(kod="998", ad="FASON-DİĞER", fason_cari=self.diger)
        with self.assertRaisesMessage(FasonHatasi, "başka bir fasoncuya"):
            self.donus(s, cari=self.diger)
        taslak = self.fs(adet="1", gonder=False)
        with self.assertRaisesMessage(FasonHatasi, "yalnız gönderilmiş"):
            self.donus(taslak)


class EkranTest(FSBase):
    def setUp(self):
        self.client.force_login(self.yon)
        self.fiyatlar()

    def test_kesim_listesinden_on_dolu_form_kaydet_gonder_liste_detay_pdf(self):
        r = self.client.post(reverse("core:fason_hesapla"), {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-urun": self.mamul.pk,
                                                             "satir-0-miktar": "7", "eylem": "fason_siparis", "cari": self.salim.pk})
        self.assertEqual(r.status_code, 302)
        self.assertIn(f"satir={self.ana.pk}%3A9", r["Location"])                                           # 7 adet → 3 boy = 9 adet (tam boy)
        self.assertIn(f"cari={self.salim.pk}", r["Location"])
        r = self.client.get(r["Location"])
        self.assertContains(r, 'value="9"')
        r = self.client.post(reverse("core:fason_siparis_ekle"), {"cari": self.salim.pk, "tarih": "2026-10-12", "termin": "2026-10-20", "aciklama": "Sip 7",
                                                                  "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0", "satir-0-stok": self.ana.pk,
                                                                  "satir-0-adet": "9", "eylem": "gonder"}, follow=True)
        s = FasonSiparisi.objects.get()
        self.assertEqual((s.durum, s.satirlar.get().adet), ("GONDERILDI", D("9")))
        self.assertContains(r, s.no)
        r = self.client.get(reverse("core:fason_siparisleri"))
        self.assertContains(r, f'data-href="{reverse("core:fason_siparis_detay", args=[s.pk])}"')
        self.assertContains(r, "Gönderildi")
        r = self.client.get(reverse("core:fason_siparis_detay", args=[s.pk]))
        self.assertContains(r, "Dönüş Aç")
        self.assertContains(r, "90,00 TRY")                                                                # 9 × 10
        r = self.client.get(reverse("core:fason_siparis_pdf", args=[s.pk]))
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        r = self.client.get(reverse("core:fason_donus_ekle"), {"fason_siparis": s.pk})                   # FD formu ön dolu
        self.assertContains(r, f'<option value="{s.pk}" selected')
        self.assertRegex(r.content.decode(), r'name="satir-0-adet"[^>]*value="9[.,]?0*"')                  # kalan adet hazır
        r = self.client.get(reverse("core:uretim_emirleri"))                                               # FS ÜS listesinde görünmez
        self.assertNotContains(r, s.no)

    def test_detay_fason_emri_notu_ve_iptal_uyarisi(self):
        e = self.us("6")                                                                                  # FS yokken bile fason emri otomatik açılmaz; yönetici açar
        self.assertIsNone(self.fason_ie(e))
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.assertIsNotNone(ie)
        r = self.client.get(reverse("core:fason_siparis_ekle"), {"stok": self.ana.pk, "adet": "6"})
        self.assertContains(r, f"{e.no} / {ie.no} fason emri zaten açık")
        s = self.fs(adet="10")
        e2 = self.us("6")
        r = self.client.post(reverse("core:fason_siparis_iptal", args=[s.pk]), follow=True)
        self.assertContains(r, f"Rezervi düşen üretim siparişleri: {e2.no}")
        r = self.client.get(reverse("core:uretim_emri_detay", args=[e2.pk]))
        self.assertContains(r, "Eksik — fason gerekli")
