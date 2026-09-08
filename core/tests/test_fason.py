"""FASON > Kesim Tanımları + Kesim Listesi Hesapla: bir hammadde profilinden 1 adet
bitmiş ürün için kaç parça kesilmesi gerektiğini tanımlayan basit CRUD + hesaplayıcı."""
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
        cls.on_ayak = _stok(cls.kat, cls.birim, satinalma=True,
                            kod="150-10-0002", ad="7378-ön ayak 20x40 (2+1)-5480mm")
        cls.arka_ayak = _stok(cls.kat, cls.birim, satinalma=True,
                              kod="150-10-0008", ad="7377-arka ayak 20x35 (2+1/5+1)-6250mm")
        cls.a21 = _stok(cls.kat, cls.birim, satis=True, kod="A21", ad="a tipi 2+1")
        cls.a51 = _stok(cls.kat, cls.birim, satis=True, kod="A51", ad="a tipi 5+1")

    def test_olustur_tr_buyuk_harf_ve_m2m(self):
        k = kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                          urun_idler=[self.a21.pk])
        self.assertEqual(k.parca_adi, "ÖN AYAK")
        self.assertEqual(list(k.urunler.values_list("pk", flat=True)), [self.a21.pk])

    def test_adet_sifir_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=0,
                          urun_idler=[self.a21.pk])

    def test_bos_parca_adi_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="  ", adet=1,
                          urun_idler=[self.a21.pk])

    def test_satis_urunu_olmayan_profil_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(profil_id=self.a21.pk, parca_adi="ön ayak", adet=1,
                          urun_idler=[self.a21.pk])           # a21 satinalma_urunu değil

    def test_urun_secilmezse_reddedilir(self):
        with self.assertRaises(FasonHatasi):
            kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1, urun_idler=[])

    def test_guncelle_ve_sil(self):
        k = kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                          urun_idler=[self.a21.pk])
        kesim_guncelle(k, profil_id=self.on_ayak.pk, parca_adi="ön ayak güncel", adet=3,
                       urun_idler=[self.a21.pk, self.a51.pk])
        k.refresh_from_db()
        self.assertEqual((k.parca_adi, k.adet), ("ÖN AYAK GÜNCEL", 3))
        self.assertEqual(set(k.urunler.values_list("pk", flat=True)), {self.a21.pk, self.a51.pk})
        kesim_sil(k)
        self.assertTrue(FasonKesim.objects.get(pk=k.pk).silindi)
        self.assertNotIn(k, aktif_kesimler())

    def test_hesapla_tek_urun(self):
        kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                     urun_idler=[self.a21.pk])
        kesim_olustur(profil_id=self.arka_ayak.pk, parca_adi="arka ayak", adet=2,
                     urun_idler=[self.a21.pk, self.a51.pk])
        sonuc = fason_listesi_hesapla([(self.a21, 5)])
        self.assertEqual(len(sonuc["detay"]), 2)
        ozet = {(o["profil"].pk, o["parca_adi"]): o["toplam_adet"] for o in sonuc["ozet"]}
        self.assertEqual(ozet[(self.on_ayak.pk, "ÖN AYAK")], 5)       # 1 adet × 5
        self.assertEqual(ozet[(self.arka_ayak.pk, "ARKA AYAK")], 10)  # 2 adet × 5

    def test_hesapla_paylasilan_profil_toplanir(self):
        """A21 + A51 aynı arka-ayak satırını paylaşıyor — iki üründen gelen miktarlar
        aynı profil+parça özetinde TOPLANMALI."""
        kesim_olustur(profil_id=self.arka_ayak.pk, parca_adi="arka ayak", adet=2,
                     urun_idler=[self.a21.pk, self.a51.pk])
        sonuc = fason_listesi_hesapla([(self.a21, 3), (self.a51, 4)])
        self.assertEqual(len(sonuc["ozet"]), 1)
        self.assertEqual(sonuc["ozet"][0]["toplam_adet"], 14)          # 2*3 + 2*4

    def test_hesapla_tanimsiz_urun_bos_sonuc(self):
        sonuc = fason_listesi_hesapla([(self.a21, 1)])
        self.assertEqual(sonuc, {"detay": [], "ozet": []})

    def test_hesapla_sifir_miktar_atlanir(self):
        kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                     urun_idler=[self.a21.pk])
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
        cls.on_ayak = _stok(cls.kat, cls.birim, satinalma=True,
                            kod="150-10-0002", ad="7378-ön ayak 20x40 (2+1)-5480mm")
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
            "profil": self.on_ayak.pk, "parca_adi": "ön ayak", "adet": "1", "sira": "1",
            "urunler": [self.a21.pk]})
        self.assertEqual(r.status_code, 302)
        k = FasonKesim.objects.get(profil=self.on_ayak, parca_adi="ÖN AYAK")
        r = self.client.post(reverse("core:fason_kesim_duzenle", args=[k.pk]), {
            "profil": self.on_ayak.pk, "parca_adi": "ön ayak 2", "adet": "2", "sira": "1",
            "urunler": [self.a21.pk]})
        self.assertEqual(r.status_code, 302)
        k.refresh_from_db()
        self.assertEqual((k.parca_adi, k.adet), ("ÖN AYAK 2", 2))
        r = self.client.post(reverse("core:fason_kesim_sil", args=[k.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(FasonKesim.objects.get(pk=k.pk).silindi)

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
        kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                     urun_idler=[self.a21.pk])
        self.client.force_login(self.yon)
        gövde = {"eylem": "hesapla"}
        gövde.update(self._formset_govde([{"urun": self.a21.pk, "miktar": "10"}]))
        r = self.client.post(reverse("core:fason_hesapla"), gövde)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ÖN AYAK")
        self.assertContains(r, "10")

    def test_hesapla_post_pdf_indirir(self):
        kesim_olustur(profil_id=self.on_ayak.pk, parca_adi="ön ayak", adet=1,
                     urun_idler=[self.a21.pk])
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
