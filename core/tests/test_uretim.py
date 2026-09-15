"""ÜRETİM modülü — Ürün Ağacı (BOM) tanımları + Üretim Emirleri. FASON'daki kesilmiş-parça/
kesildigi_profil kavramıyla hiçbir ilişkisi yok — bağımsız, sıfırdan kurulan bir Stok↔Stok
reçetesi. Üretim Emri onayı yalnızca StokHareket (miktar) üretir; hiçbir YevmiyeFisi'ne
dokunmaz — bu dosyadaki testler bu ayrımı da doğrular."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Birim, Depo, EkranYetki, Kategori, Stok, StokHareket, UretimEmri, UrunAgaci,
    YevmiyeFisi,
)
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, aktif_urun_agaclari, emri_satirlari, uretim_emri_olustur,
    uretim_emri_onayla, uretim_emri_satir_guncelle, uretim_emri_sil,
    urun_agaci_guncelle, urun_agaci_olustur, urun_agaci_satirlari, urun_agaci_sil,
)


def _birim():
    return Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)


def _kategori():
    return Kategori.objects.create(kod="UT", ad="ÜRETİM TEST")


def _stok(kategori, birim, *, kod, ad, uretim=True, satinalma=False, satis=False):
    return Stok.objects.create(
        kod=kod, ad=ad, kategori=kategori, uretim_birimi=birim, fatura_birimi=birim,
        uretim_urunu=uretim, satinalma_urunu=satinalma, satis_urunu=satis)


def _depo(kod="MRK"):
    return Depo.objects.create(kod=kod, ad="MERKEZ DEPO")


class UretimServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.mamul = _stok(cls.kat, cls.birim, kod="UT-MAMUL", ad="test merdiveni", satis=True)
        cls.civata = _stok(cls.kat, cls.birim, kod="UT-CIVATA", ad="civata", satinalma=True)
        cls.ayak = _stok(cls.kat, cls.birim, kod="UT-AYAK", ad="plastik ayak", satinalma=True)
        cls.depo = _depo()

    def test_urun_agaci_olustur_ve_satirlar(self):
        agac = urun_agaci_olustur(
            mamul_id=self.mamul.pk,
            satirlar=[(self.civata, Decimal("24")), (self.ayak, Decimal("2"))])
        satirlar = list(urun_agaci_satirlari(agac))
        self.assertEqual(len(satirlar), 2)
        self.assertEqual({s.bilesen_id for s in satirlar}, {self.civata.pk, self.ayak.pk})

    def test_urun_agaci_ayni_mamul_iki_kez_reddedilir(self):
        urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("1"))])
        with self.assertRaises(UretimHatasi):
            urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.ayak, Decimal("1"))])

    def test_urun_agaci_bos_satir_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[])

    def test_urun_agaci_mamul_kendi_bileseni_olamaz(self):
        with self.assertRaises(UretimHatasi):
            urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.mamul, Decimal("1"))])

    def test_urun_agaci_tekrar_eden_bilesen_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[
                (self.civata, Decimal("1")), (self.civata, Decimal("2"))])

    def test_urun_agaci_sifir_miktar_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("0"))])

    def test_urun_agaci_guncelle_eski_satirlari_degistirir(self):
        agac = urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("24"))])
        urun_agaci_guncelle(agac, satirlar=[(self.ayak, Decimal("4"))])
        satirlar = list(urun_agaci_satirlari(agac))
        self.assertEqual(len(satirlar), 1)
        self.assertEqual(satirlar[0].bilesen_id, self.ayak.pk)
        self.assertEqual(satirlar[0].miktar, Decimal("4.000"))

    def test_urun_agaci_sil(self):
        agac = urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("1"))])
        urun_agaci_sil(agac)
        self.assertTrue(UrunAgaci.objects.get(pk=agac.pk).silindi)
        self.assertNotIn(agac, aktif_urun_agaclari())

    def test_urun_agaci_sil_bagli_emir_varsa_reddedilir(self):
        agac = urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("1"))])
        uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        with self.assertRaises(UretimHatasi):
            urun_agaci_sil(agac)

    # --- Üretim Emirleri ---
    def _agac_kur(self):
        return urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[
            (self.civata, Decimal("24")), (self.ayak, Decimal("2"))])

    def test_uretim_emri_olustur_urun_agaci_yoksa_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                depo_id=self.depo.pk, tarih=date(2026, 1, 10))

    def test_uretim_emri_olustur_satirlari_bom_carpimi_ile_doldurur(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        satirlar = {s.bilesen_id: s for s in emri_satirlari(emir)}
        self.assertEqual(satirlar[self.civata.pk].planlanan_miktar, Decimal("240.000"))
        self.assertEqual(satirlar[self.civata.pk].gerceklesen_miktar, Decimal("240.000"))
        self.assertEqual(satirlar[self.ayak.pk].planlanan_miktar, Decimal("20.000"))
        self.assertEqual(emir.durum, UretimEmri.Durum.TASLAK)

    def test_uretim_emri_no_atomik_artan(self):
        self._agac_kur()
        e1 = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                 depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        e2 = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                 depo_id=self.depo.pk, tarih=date(2026, 1, 11))
        self.assertEqual(e1.no, "UE-2026-0001")
        self.assertEqual(e2.no, "UE-2026-0002")

    def test_uretim_emri_satir_guncelle_taslakta_calisir(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        satir = emri_satirlari(emir).get(bilesen=self.civata)
        uretim_emri_satir_guncelle(satir, gerceklesen_miktar=Decimal("245"))
        satir.refresh_from_db()
        self.assertEqual(satir.gerceklesen_miktar, Decimal("245.000"))

    def test_uretim_emri_satir_guncelle_onaylida_reddedilir(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        uretim_emri_onayla(emir)
        satir = emri_satirlari(emir).first()
        with self.assertRaises(UretimHatasi):
            uretim_emri_satir_guncelle(satir, gerceklesen_miktar=Decimal("5"))

    def test_uretim_emri_onayla_stok_hareketleri_dogru(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        uretim_emri_onayla(emir)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ONAYLI)
        self.assertEqual(eldeki_miktar(self.civata, self.depo), Decimal("1000") - Decimal("240"))
        self.assertEqual(eldeki_miktar(self.ayak, self.depo), Decimal("1000") - Decimal("20"))
        self.assertEqual(eldeki_miktar(self.mamul, self.depo), Decimal("10"))
        mamul_hareket = StokHareket.objects.get(
            stok=self.mamul, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertEqual(mamul_hareket.miktar, Decimal("10.000"))

    def test_uretim_emri_onayla_gerceklesen_miktar_kullanilir(self):
        """Onaydan önce satır bazında düzeltilen gerçekleşen miktar (fire/sapma) —
        planlanan DEĞİL, gerçekleşen — stok çıkışında kullanılır."""
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        satir = emri_satirlari(emir).get(bilesen=self.civata)
        uretim_emri_satir_guncelle(satir, gerceklesen_miktar=Decimal("250"))   # 10 fire
        uretim_emri_onayla(emir)
        self.assertEqual(eldeki_miktar(self.civata, self.depo), Decimal("1000") - Decimal("250"))

    def test_uretim_emri_onayla_yetersiz_stok_atomik_geri_alir(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("5"))   # 240 gerekiyor, 5 var
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        with self.assertRaises(UretimHatasi):
            uretim_emri_onayla(emir)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.TASLAK)
        self.assertEqual(
            StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count(), 0)

    def test_uretim_emri_onayla_idempotent(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        uretim_emri_onayla(emir)
        onceki = StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count()
        uretim_emri_onayla(emir)                                       # ikinci çağrı sessiz
        self.assertEqual(
            StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count(), onceki)

    def test_uretim_emri_onayli_iptal_edilemez(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        uretim_emri_onayla(emir)
        with self.assertRaises(UretimHatasi):
            uretim_emri_sil(emir)

    def test_uretim_emri_taslak_iptal_soft_delete(self):
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("1"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        uretim_emri_sil(emir)
        self.assertTrue(UretimEmri.objects.get(pk=emir.pk).silindi)

    def test_uretim_emri_onayla_muhasebeye_dokunmuyor(self):
        """v0.4 kapsam kararı: Üretim Emri onayı hiçbir YevmiyeFisi üretmez — maliyetin
        muhasebeye yansıtılması ay sonu mali müşavirin elle yapacağı ayrı bir iştir."""
        self._agac_kur()
        emir = uretim_emri_olustur(mamul_id=self.mamul.pk, planlanan_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        hareket_ekle(stok_id=self.ayak.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        onceki = YevmiyeFisi.objects.count()
        uretim_emri_onayla(emir)
        self.assertEqual(YevmiyeFisi.objects.count(), onceki)


class UretimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("uretyon", password="x")
        cls.bos = User.objects.create_user("uretbos", password="x")
        cls.agac_yetkili = User.objects.create_user("uretagacyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.agac_yetkili, ekran_kod="uretim_urun_agaci")
        cls.emir_yetkili = User.objects.create_user("uretemiryetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.emir_yetkili, ekran_kod="uretim_emirleri")

        cls.birim = _birim()
        cls.kat = _kategori()
        cls.mamul = _stok(cls.kat, cls.birim, kod="UTV-MAMUL", ad="test merdiveni", satis=True)
        cls.civata = _stok(cls.kat, cls.birim, kod="UTV-CIVATA", ad="civata", satinalma=True)
        cls.depo = _depo("UTVDEPO")

    # --- Ürün Ağacı Tanımları: ekran_gerekli("uretim_urun_agaci") ---
    def test_urun_agaci_anonim_login_yonlenir(self):
        r = self.client.get(reverse("core:uretim_urun_agaclari"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_urun_agaci_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:uretim_urun_agaclari")).status_code, 403)

    def test_urun_agaci_yetkili_girebilir(self):
        self.client.force_login(self.agac_yetkili)
        self.assertEqual(self.client.get(reverse("core:uretim_urun_agaclari")).status_code, 200)

    def _satir_formset_govde(self, satirlar):
        veri = {"satir-TOTAL_FORMS": str(len(satirlar)), "satir-INITIAL_FORMS": "0",
                "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            veri[f"satir-{i}-bilesen"] = s.get("bilesen", "")
            veri[f"satir-{i}-miktar"] = s.get("miktar", "")
        return veri

    def test_urun_agaci_ekle_post(self):
        self.client.force_login(self.yon)
        gövde = {"mamul": self.mamul.pk, "aciklama": "test"}
        gövde.update(self._satir_formset_govde([{"bilesen": self.civata.pk, "miktar": "24"}]))
        r = self.client.post(reverse("core:uretim_urun_agaci_ekle"), gövde)
        self.assertEqual(r.status_code, 302)
        agac = UrunAgaci.objects.get(mamul=self.mamul)
        self.assertEqual(agac.satirlar.filter(silindi=False).count(), 1)

    def test_urun_agaci_duzenle_ve_sil_post(self):
        agac = urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("1"))])
        self.client.force_login(self.yon)
        gövde = {"aciklama": "güncellendi"}
        gövde.update(self._satir_formset_govde([{"bilesen": self.civata.pk, "miktar": "30"}]))
        r = self.client.post(reverse("core:uretim_urun_agaci_duzenle", args=[agac.pk]), gövde)
        self.assertEqual(r.status_code, 302)
        satir = agac.satirlar.filter(silindi=False).get()
        self.assertEqual(satir.miktar, Decimal("30.000"))

        r = self.client.post(reverse("core:uretim_urun_agaci_sil", args=[agac.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(UrunAgaci.objects.get(pk=agac.pk).silindi)

    # --- Üretim Emirleri: ekran_gerekli("uretim_emirleri") ---
    def test_emirleri_anonim_login_yonlenir(self):
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)

    def test_emirleri_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:uretim_emirleri")).status_code, 403)

    def test_agac_yetkisi_emirler_icin_yetmez(self):
        self.client.force_login(self.agac_yetkili)
        self.assertEqual(self.client.get(reverse("core:uretim_emirleri")).status_code, 403)

    def test_emri_ekle_view_ve_detay_onayla(self):
        urun_agaci_olustur(mamul_id=self.mamul.pk, satirlar=[(self.civata, Decimal("24"))])
        hareket_ekle(stok_id=self.civata.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:uretim_emri_ekle"), {
            "mamul": self.mamul.pk, "planlanan_miktar": "10", "depo": self.depo.pk,
            "tarih": "2026-01-10", "aciklama": ""})
        self.assertEqual(r.status_code, 302)
        emir = UretimEmri.objects.get(mamul=self.mamul)
        self.assertEqual(emir.no, "UE-2026-0001")

        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, emir.no)

        r = self.client.post(reverse("core:uretim_emri_onayla", args=[emir.pk]))
        self.assertEqual(r.status_code, 302)
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ONAYLI)
        self.assertEqual(eldeki_miktar(self.mamul, self.depo), Decimal("10"))

    def test_emri_ekle_urun_agaci_olmayan_mamul_secenek_olarak_gelmez(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:uretim_emri_ekle"))
        self.assertNotContains(r, "UTV-MAMUL")

    def test_menude_uretim_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertContains(r, "Üretim")
        self.assertContains(r, "Ürün Ağacı Tanımları")
        self.assertContains(r, "Üretim Emirleri")
