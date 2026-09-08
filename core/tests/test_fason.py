"""FASON > Kesim Tanımları + Kesim Listesi Hesapla — 2 katmanlı BOM: bitmiş ürün →
kesilmiş parça (FasonKesim × adet) → o parçanın ham profili (Stok.kesildigi_profil, 1:1)."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Birim, EkranYetki, FasonKesim, Kategori, Stok
from core.services.fason import (
    FasonHatasi, aktif_kesimler, fason_listesi_hesapla, kesim_guncelle, kesim_olustur,
    kesim_sil,
)


def _birim():
    return Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)


def _kategori():
    return Kategori.objects.create(kod="FK", ad="FASON TEST")


def _stok(kategori, birim, *, satinalma=False, satis=False, **kw):
    return Stok.objects.create(
        kod=kw.pop("kod"), ad=kw.pop("ad"), kategori=kategori,
        uretim_birimi=birim, fatura_birimi=birim,
        satinalma_urunu=satinalma, uretim_urunu=not (satinalma or satis), satis_urunu=satis,
        **kw)


class FasonServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        # Katman 1: ham profil (satınalma) -> kesilmiş parça (kesildigi_profil ile bağlı).
        cls.ham_on = _stok(cls.kat, cls.birim, satinalma=True,
                           kod="150-TEST-ON", ad="7378-ön ayak 20x40 (2+1)-5480mm")
        cls.ham_arka = _stok(cls.kat, cls.birim, satinalma=True,
                             kod="150-TEST-ARKA", ad="7377-arka ayak 20x35 (2+1/5+1)-6250mm")
        cls.parca_on = _stok(cls.kat, cls.birim,
                             kod="151-TEST-ON", ad="kesilmiş a tipi ön ayak 2+1",
                             kesildigi_profil=cls.ham_on)
        cls.parca_arka = _stok(cls.kat, cls.birim,
                               kod="151-TEST-ARKA", ad="kesilmiş a tipi arka ayak 2+1",
                               kesildigi_profil=cls.ham_arka)
        # Katman 2: bitmiş ürünler.
        cls.a21 = _stok(cls.kat, cls.birim, satis=True, kod="A21", ad="a tipi 2+1")
        cls.a51 = _stok(cls.kat, cls.birim, satis=True, kod="A51", ad="a tipi 5+1")

    def test_olustur_ve_m2m_yok(self):
        k = kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        self.assertEqual((k.urun_id, k.kesilmis_parca_id, k.adet), (self.a21.pk, self.parca_on.pk, 1))

    def test_adet_sifir_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=0)

    def test_ayni_kombinasyon_iki_kez_reddedilir(self):
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        with self.assertRaises(FasonHatasi):
            kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=2)

    def test_satis_urunu_olmayan_urun_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(urun_id=self.ham_on.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)

    def test_kesildigi_profil_tanimsiz_parca_reddedilir(self):
        cıplak = _stok(self.kat, self.birim, kod="151-TEST-CIPLAK", ad="profilsiz parça")
        with self.assertRaises(FasonHatasi):
            kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=cıplak.pk, adet=1)

    def test_guncelle_ve_sil(self):
        k = kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        kesim_guncelle(k, urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=3)
        k.refresh_from_db()
        self.assertEqual(k.adet, 3)
        kesim_sil(k)
        self.assertTrue(FasonKesim.objects.get(pk=k.pk).silindi)
        self.assertNotIn(k, aktif_kesimler())

    def test_hesapla_tek_urun(self):
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_arka.pk, adet=2)
        sonuc = fason_listesi_hesapla([(self.a21, 5)])
        self.assertEqual(len(sonuc["detay"]), 2)
        ozet = {(o["profil"].pk, o["kesilmis_parca"].pk): o["toplam_adet"] for o in sonuc["ozet"]}
        self.assertEqual(ozet[(self.ham_on.pk, self.parca_on.pk)], 5)      # 1 adet × 5
        self.assertEqual(ozet[(self.ham_arka.pk, self.parca_arka.pk)], 10)  # 2 adet × 5

    def test_hesapla_paylasilan_ham_profil_ayri_ozet_satirlari(self):
        """A21 + A51 farklı kesilmiş parçalar kullanıyor olsa da (5+1 SAĞ/SOL gibi) aynı
        ham profili paylaşabilir — bu durumda ÖZET satırları PARÇA bazında ayrı kalır
        (kesilmiş parça farklı kimlik taşıyor), profil aynı olsa da karışmaz."""
        parca_arka_a51 = _stok(self.kat, self.birim, kod="151-TEST-ARKA-A51",
                               ad="kesilmiş a tipi arka ayak 5+1", kesildigi_profil=self.ham_arka)
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_arka.pk, adet=2)
        kesim_olustur(urun_id=self.a51.pk, kesilmis_parca_id=parca_arka_a51.pk, adet=2)
        sonuc = fason_listesi_hesapla([(self.a21, 3), (self.a51, 4)])
        self.assertEqual(len(sonuc["ozet"]), 2)
        toplam = sum(o["toplam_adet"] for o in sonuc["ozet"])
        self.assertEqual(toplam, 2 * 3 + 2 * 4)                    # 6 + 8 = 14

    def test_hesapla_tanimsiz_urun_bos_sonuc(self):
        sonuc = fason_listesi_hesapla([(self.a21, 1)])
        self.assertEqual(sonuc, {"detay": [], "ozet": []})

    def test_hesapla_sifir_miktar_atlanir(self):
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        sonuc = fason_listesi_hesapla([(self.a21, 0)])
        self.assertEqual(sonuc["detay"], [])


class FasonViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fasyon", password="x")
        cls.bos = User.objects.create_user("fasbos", password="x")
        cls.yetkili = User.objects.create_user("fasyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="fason_hesapla")

        cls.birim = _birim()
        cls.kat = _kategori()
        cls.ham_on = _stok(cls.kat, cls.birim, satinalma=True,
                           kod="150-TEST-ON", ad="7378-ön ayak 20x40 (2+1)-5480mm")
        cls.parca_on = _stok(cls.kat, cls.birim,
                             kod="151-TEST-ON", ad="kesilmiş a tipi ön ayak 2+1",
                             kesildigi_profil=cls.ham_on)
        cls.a21 = _stok(cls.kat, cls.birim, satis=True, kod="A21", ad="a tipi 2+1")

    # --- Kesim Tanımları: yönetici-only ---
    def test_tanimlar_anonim_login_yonlenir(self):
        r = self.client.get(reverse("core:fason_kesim_tanimlari"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_tanimlar_yonetici_olmayan_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:fason_kesim_tanimlari")).status_code, 403)

    def test_tanimlar_fason_hesapla_yetkisi_de_tanimlar_icin_yetmez(self):
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(reverse("core:fason_kesim_tanimlari")).status_code, 403)

    def test_tanimlar_ekle_duzenle_sil(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:fason_kesim_ekle"), {
            "urun": self.a21.pk, "kesilmis_parca": self.parca_on.pk, "adet": "1", "sira": "1"})
        self.assertEqual(r.status_code, 302)
        k = FasonKesim.objects.get(urun=self.a21, kesilmis_parca=self.parca_on)
        r = self.client.post(reverse("core:fason_kesim_duzenle", args=[k.pk]), {
            "urun": self.a21.pk, "kesilmis_parca": self.parca_on.pk, "adet": "2", "sira": "1"})
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertEqual(k.adet, 2)
        r = self.client.post(reverse("core:fason_kesim_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(FasonKesim.objects.get(pk=k.pk).silindi)

    def test_kesilmis_parca_secenekleri_yalniz_profili_tanimli_olanlar(self):
        """Kesildigi_profil'i olmayan bir stok, ekle formunda 'kesilmiş parça' seçeneği
        olarak GELMEMELİ."""
        _stok(self.kat, self.birim, kod="151-CIPLAK", ad="profilsiz")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fason_kesim_ekle"))
        self.assertContains(r, "151-TEST-ON")
        self.assertNotContains(r, "151-CIPLAK")

    # --- Kesim Listesi Hesapla: ekran_gerekli("fason_hesapla") ---
    def test_hesapla_anonim_login_yonlenir(self):
        r = self.client.get(reverse("core:fason_hesapla"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_hesapla_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:fason_hesapla")).status_code, 403)

    def test_hesapla_yetkili_kullanici_girebilir(self):
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(reverse("core:fason_hesapla")).status_code, 200)

    def _formset_govde(self, satirlar):
        veri = {"satir-TOTAL_FORMS": str(len(satirlar)), "satir-INITIAL_FORMS": "0",
                "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            veri[f"satir-{i}-urun"] = s.get("urun", "")
            veri[f"satir-{i}-miktar"] = s.get("miktar", "")
        return veri

    def test_hesapla_post_sonucu_gosterir(self):
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        self.client.force_login(self.yon)
        gövde = {"eylem": "hesapla"}
        gövde.update(self._formset_govde([{"urun": self.a21.pk, "miktar": "10"}]))
        r = self.client.post(reverse("core:fason_hesapla"), gövde)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "151-TEST-ON")
        self.assertContains(r, "10")

    def test_hesapla_post_pdf_indirir(self):
        kesim_olustur(urun_id=self.a21.pk, kesilmis_parca_id=self.parca_on.pk, adet=1)
        self.client.force_login(self.yon)
        gövde = {"eylem": "pdf"}
        gövde.update(self._formset_govde([{"urun": self.a21.pk, "miktar": "10"}]))
        r = self.client.post(reverse("core:fason_hesapla"), gövde)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertGreater(len(r.content), 500)

    def test_menude_fason_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertContains(r, "Fason")
        self.assertContains(r, "Kesim Listesi Hesapla")
