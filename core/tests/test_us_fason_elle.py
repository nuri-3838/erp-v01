"""ÜS fason emrini OTOMATİK AÇMAZ (2026-10-23): ÜS açılışı / revize / fire ek planı fason istasyon emrini açmaz, artırmaz, azaltmaz; fason ihtiyacı "Eksik — fason gerekli"
olarak görünür; yönetici "Fason emri aç / güncelle" düğmesiyle (``uretim.fason_emri_ac``) o anki ihtiyaç kadar açar; sonradan ihtiyaç değişirse emir kendiliğinden değişmez,
fark eksik/fazla görünür ve aynı düğmeyle güncellenir; hiçbir ÜS yolu satınalma/fason BELGESİ açmaz; yeni eksik tablosu İhtiyaç | Stokta ayrılan | Yolda | EKSİK;
fason OLMAYAN istasyonlar eskisi gibi otomatik açılır."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import FasonSiparisi, IstasyonEmri, StokHareket, TeklifSiparis, UretimEmriRevizyon
from core.services import uretim
from core.services.uretim import UretimHatasi, fason_emri_ac, uretim_emri_eksik_tablosu, uretim_emri_eksikleri, uretim_emri_olustur, uretim_emri_revize
from core.tests import test_fason_siparis as tfs
from core.tests import test_kritik_stok as tks

D = Decimal
GUN = tfs.GUN


class FasonElleTaban(tfs.FSBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)

    def fason_satir(self, emir):
        return [r for r in uretim_emri_eksik_tablosu(emir) if r["tur"] == "FASON"]

    def revize(self, emir, adet):
        return uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": str(adet)}], tarih=GUN, kullanici=self.yon)

    def belge_sayilari(self):
        return TeklifSiparis.objects.count(), FasonSiparisi.objects.count()


class AcilisTest(FasonElleTaban):
    def test_us_acilisinda_fason_emri_acilmaz_eksik_gorunur_fason_olmayan_acilir(self):
        once = self.belge_sayilari()
        e = self.us("6")
        self.assertIsNone(self.fason_ie(e))                                                              # fason emri YOK
        self.assertEqual(e.istasyon_emirleri.filter(operasyon=self.montaj_op).count(), 1)                # fason OLMAYAN montaj emri eskisi gibi açıldı
        satir = self.fason_satir(e)[0]
        self.assertEqual((satir["stok"].pk, satir["ihtiyac"], satir["ayrilan"], satir["yolda_toplam"], satir["eksik"]), (self.ana.pk, D("6"), D("0"), D("0"), D("6")))
        self.assertEqual(satir["fason"]["buton"], "ac")
        self.assertIn(("FASON_EMRI_GEREKLI", D("6")), [(x["tur"], x["eksik"]) for x in uretim_emri_eksikleri(e)])
        self.assertEqual(self.belge_sayilari(), once)                                                    # hiçbir satınalma / fason BELGESİ açılmadı

    def test_onizleme_fason_gerekli_gosterir_emir_saymaz_gercek_acilisla_ayni(self):
        from core.services import us_onizleme
        o = us_onizleme.onizle(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "6"}])
        self.assertEqual([(f["cikti"].pk, f["adet"]) for f in o["fason_gerekli"]], [(self.ana.pk, D("6"))])
        e = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "6"}], depo_id=self.depo.pk, tarih=GUN)
        self.assertEqual(o["ie_sayisi"], e.istasyon_emirleri.exclude(durum="IPTAL").count())
        self.assertFalse([g for g in o["istasyonlar"] if g["fason"]])


class DugmeTest(FasonElleTaban):
    def test_dugme_fason_emrini_ihtiyac_kadar_acar_gecmise_yazar(self):
        e = self.us("6")
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.assertEqual((ie.planlanan, ie.ihtiyac, ie.durum), (D("2"), {str(self.ana.pk): "6"}, "BEKLIYOR"))      # 6 adet / 3 adet-boy = 2 koşum
        r = e.revizyonlar.get(tur=UretimEmriRevizyon.Tur.FASON_EMRI)
        self.assertEqual((r.detay["ie"], r.detay["once"], r.detay["sonra"], r.detay["yeni"], r.created_by), (ie.no, "0", "2", True, self.yon))
        satir = self.fason_satir(e)[0]
        self.assertEqual((satir["yolda_toplam"], satir["eksik"], satir["fason"]["buton"]), (D("6"), D("0"), None))      # açık emir kalanı "yolda"
        self.assertEqual([(y["tur"], y["belge"]) for y in satir["yolda"]], [("FASON_EMRI", ie.no)])
        self.assertEqual([x for x in uretim_emri_eksikleri(e) if x["tur"] == "FASON_EMRI_GEREKLI"], [])

    def test_fason_olmayan_istasyon_icin_reddedilir(self):
        e = self.us("6")
        with self.assertRaisesMessage(UretimHatasi, "Yalnız fason istasyon emri"):
            fason_emri_ac(e, operasyon_id=self.montaj_op.pk, kullanici=self.yon)

    def test_gorunum_yalniz_yonetici_post_acar(self):
        e = self.us("6")
        url = reverse("core:uretim_emri_fason_emri", args=[e.pk])
        normal = User.objects.create_user("normal", password="x")
        self.client.force_login(normal)
        self.assertEqual(self.client.post(url, {"operasyon": self.op.pk}).status_code, 403)
        self.assertIsNone(self.fason_ie(e))
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(url).status_code, 302)                                           # GET hiçbir şey yazmaz
        self.assertIsNone(self.fason_ie(e))
        r = self.client.post(url, {"operasyon": self.op.pk, "varyant": ""}, follow=True)
        self.assertContains(r, "Fason emri IE-")
        self.assertEqual(self.fason_ie(e).planlanan, D("2"))
        r = self.client.post(url, {"operasyon": "abc"}, follow=True)                                      # geçersiz girdi: hata mesajı, 500 yok
        self.assertEqual(r.status_code, 200)

    def test_detay_yonetici_dugmeleri_ve_eksiksiz_gizli(self):
        e = self.us("6")
        html = self.client.get(reverse("core:uretim_emri_detay", args=[e.pk])).content.decode()
        self.assertIn("Fason emri aç (6)", html)
        self.assertIn("Eksik — fason gerekli: 6 adet", html)
        self.assertIn(f'{reverse("core:fason_siparis_ekle")}?satir={self.ana.pk}:6', html)                 # ön dolu fason siparişi formu
        self.fs(adet="10")                                                                                # FS gelecek stok: ikinci ÜS'nin fason parçası yolda → eksik 0 → gizli satır
        e2 = self.us("6")
        html2 = self.client.get(reverse("core:uretim_emri_detay", args=[e2.pk])).content.decode()
        self.assertRegex(html2, r'<tr class="ud-eksiksiz" hidden>')
        self.assertIn('id="ud-eksiksiz-goster"', html2)
        self.assertNotIn("Fason emri aç (", html2)
        normal = User.objects.create_user("normal2", password="x")
        from core.models import EkranYetki
        for kod in ("uretim_emirleri",):
            EkranYetki.objects.create(kullanici=normal, ekran_kod=kod)
        self.client.force_login(normal)
        html = self.client.get(reverse("core:uretim_emri_detay", args=[e.pk])).content.decode()
        self.assertNotIn("Fason emri aç (", html)                                                         # yönetici değil: düğme yok
        self.assertIn("Eksik — fason gerekli", html)                                                      # ama eksik görünür


class FarkTest(FasonElleTaban):
    def test_revize_emri_degistirmez_fark_eksik_gorunur_dugme_gunceller(self):
        e = self.us("6")
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.revize(e, 8)                                                                                 # ihtiyaç 8 → 3 koşum (9 adet); emir 2 koşum
        ie.refresh_from_db()
        self.assertEqual(ie.planlanan, D("2"))                                                            # emir KENDİLİĞİNDEN değişmedi
        satir = self.fason_satir(e)[0]
        self.assertEqual((satir["eksik"], satir["fason"]["buton"], satir["fason"]["emir_birim"], satir["fason"]["gereken_birim"]), (D("2"), "guncelle", D("6"), D("8") + D("1")))
        self.assertIn(("FASON_EMRI_GEREKLI", D("2")), [(x["tur"], x["eksik"]) for x in uretim_emri_eksikleri(e)])
        html = self.client.get(reverse("core:uretim_emri_detay", args=[e.pk])).content.decode()
        self.assertIn("emir 6, ihtiyaç 9", html)
        self.assertIn("Fason emrini güncelle", html)
        fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        ie.refresh_from_db()
        self.assertEqual(ie.planlanan, D("3"))
        self.assertEqual(self.fason_satir(e)[0]["eksik"], D("0"))
        self.assertEqual(e.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FASON_EMRI).count(), 2)

    def test_ihtiyac_dusunce_fazla_gorunur_ayni_dugme_azaltir_tamamlananin_altina_inmez(self):
        e = self.us("9")
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.assertEqual(ie.planlanan, D("3"))
        self.revize(e, 3)                                                                                 # ihtiyaç 1 koşum; emir 3
        ie.refresh_from_db()
        self.assertEqual(ie.planlanan, D("3"))
        satir = self.fason_satir(e)[0]
        self.assertEqual((satir["eksik"], satir["fazla"], satir["fason"]["buton"]), (D("0"), D("6"), "guncelle"))
        self.assertIn("FASON_EMRI_FAZLA", [x["tur"] for x in uretim_emri_eksikleri(e)])
        IstasyonEmri.objects.filter(pk=ie.pk).update(tamamlanan=D("2"))                                    # 2 koşum üretilmiş
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.assertGreaterEqual(ie.planlanan, ie.tamamlanan)                                              # tamamlananın altına inmez
        self.assertEqual(ie.tamamlanan, D("2"))

    def test_ihtiyac_kalmadiysa_ve_uretilmediyse_emir_iptal_olur(self):
        e = self.us("6")
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        hareket = StokHareket.objects.filter(stok=self.ana).count()
        self.assertEqual(hareket, 0)
        from core.models import UretimEmriKalemi
        # ihtiyaç tamamen stoktan karşılanır: ana parça stoğu girilir → plan fason ihtiyacı 0
        from core.services.hareket import hareket_ekle
        hareket_ekle(stok_id=self.ana.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur="GIRIS", miktar=D("6"), giris_tutar_try=D("60"))
        self.revize(e, 6)
        ie.refresh_from_db()
        self.assertEqual(ie.planlanan, D("2"))                                                            # revize hâlâ dokunmadı
        ie = fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.assertEqual((ie.durum, ie.planlanan), ("IPTAL", D("0")))
        self.assertTrue(UretimEmriKalemi.objects.filter(uretim_emri=e).exists())

    def test_fason_siparisi_rezervi_yolda_belge_no_ile(self):
        s = self.fs(adet="10")
        e = self.us("6")
        satir = self.fason_satir(e)[0]
        self.assertEqual((satir["ihtiyac"], satir["yolda_toplam"], satir["eksik"]), (D("6"), D("6"), D("0")))
        self.assertEqual([(y["tur"], y["belge"], y["miktar"]) for y in satir["yolda"]], [("FASON_SIPARIS", s.no, D("6"))])
        self.assertIsNone(self.fason_ie(e))                                                               # FS karşıladı: emir de gerekmez, eksik 0

    def test_hicbir_us_yolu_belge_acmaz(self):
        once = self.belge_sayilari()
        e = self.us("6")
        self.revize(e, 9)
        fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        self.revize(e, 4)
        fason_emri_ac(e, operasyon_id=self.op.pk, kullanici=self.yon)
        uretim.uretim_emri_iptal(e)
        self.assertEqual(self.belge_sayilari(), once)


class StokUsKapanisTest(FasonElleTaban):
    def test_fason_eksigi_varken_stok_us_tamamlanmaz(self):
        e = self.us("6")
        IstasyonEmri.objects.filter(uretim_emri=e).update(durum="BITTI", tamamlanan=D("6"))               # tüm (fason olmayan) emirler bitti
        self.assertFalse(uretim.stok_us_tamamla(e))                                                       # fason ihtiyacı açık: Tamamlandı olmaz
        e.refresh_from_db()
        self.assertEqual(e.durum, "ACIK")
        with mock.patch.object(uretim, "uretim_emri_eksikleri", return_value=[]):
            self.assertTrue(uretim.stok_us_tamamla(e))


class SatinalmaYoldaTest(tks.KritikTaban):
    """Eksik tablosu: İhtiyaç | Stokta ayrılan | Yolda (açık satınalma siparişi, belge no) | EKSİK; eksiksiz satırlar tabloda var, ekranda gizli."""

    def siparis(self, miktar):
        from core.models import KdvOrani, Stok
        from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
        from core.tests.test_kritik_stok import _cari
        kdv = KdvOrani.objects.filter(oran=D("20")).first() or KdvOrani.objects.create(oran=D("20"), aciklama="G")
        Stok.objects.filter(pk=self.profil.pk).update(satinalma_urunu=True, kdv=kdv)
        cari = _cari("TEDARİKÇİ Y", "320-99-0011")
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=cari.pk, tarih=date(2026, 1, 12),
                                     satirlar=[{"stok_id": self.profil.pk, "miktar": miktar, "birim_fiyat": "10"}], kullanici=self.yon)
        return teklif_siparis_onayla(sip, kullanici=self.yon)

    def test_ihtiyac_ayrilan_yolda_eksik(self):
        StokHareket.objects.filter(stok=self.profil).update(silindi=True)                                # profil stoğu 0; ÜS 5 profil ister
        satir = [r for r in uretim_emri_eksik_tablosu(self.emir) if r["stok"].pk == self.profil.pk][0]
        self.assertEqual((satir["tur"], satir["ihtiyac"], satir["ayrilan"], satir["yolda_toplam"], satir["eksik"]), ("SATINALMA", D("5"), D("0"), D("0"), D("5")))
        sip = self.siparis("3")
        satir = [r for r in uretim_emri_eksik_tablosu(self.emir) if r["stok"].pk == self.profil.pk][0]
        self.assertEqual((satir["yolda_toplam"], satir["eksik"], [(y["tur"], y["belge"]) for y in satir["yolda"]]), (D("3"), D("2"), [("SATINALMA", sip.belge_no)]))
        TeklifSiparis.objects.filter(pk=sip.pk).update(silindi=True)
        self.siparis("50")
        satir = [r for r in uretim_emri_eksik_tablosu(self.emir) if r["stok"].pk == self.profil.pk][0]
        self.assertEqual((satir["yolda_toplam"], satir["eksik"]), (D("5"), D("0")))                       # yolda ihtiyacı aşmaz: eksik 0 → ekranda gizli
        self.client.force_login(self.yon)
        html = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])).content.decode()
        self.assertRegex(html, r'<tr class="ud-eksiksiz" hidden>\s*<td><a href="[^"]*"><span class="mono">' + self.profil.kod)

    def test_ayrilan_stok_eksigi_azaltir_hicbir_belge_acilmaz(self):
        once = (TeklifSiparis.objects.count(), FasonSiparisi.objects.count())
        satir = [r for r in uretim_emri_eksik_tablosu(self.emir) if r["stok"].pk == self.profil.pk]
        self.assertTrue(not satir or satir[0]["eksik"] == 0)                                              # profil 100 var: eksik yok
        self.assertEqual((TeklifSiparis.objects.count(), FasonSiparisi.objects.count()), once)
