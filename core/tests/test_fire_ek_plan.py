"""Fire sonrası ÜS yeniden planlama (2026-10-22, core.services.uretim_fire): fire'lı atölye kaydı onaylanınca ÜS'nin AÇIK kalan ihtiyacı için (1) serbest stoktan ek ayırma,
(2) yetmezse girdiyi üreten istasyon emrinin artırılması / yeniden açılması (zincirleme), (3) fason emri artarsa rozet, (4) kayıt geri alınırsa ekin geri sarılması,
(5) ÜS detayında not. Tamamlanmış emirde ek yok; fire'sız akış ve ÜS revizesi etkilenmez; planlama hatası üretim kaydını engellemez; geriye dönük komut."""
from datetime import date
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.urls import reverse

from core.models import IsIstasyonu, IstasyonEmri, Operasyon, OperasyonKaydi, StokHareket, UretimEmriRevizyon
from core.services import stok_ayirma as sa
from core.services import uretim, uretim_fire
from core.services.hareket import hareket_ekle
from core.tests.test_stoktan_karsilama import StoktanTaban

D = Decimal
GUN = date(2026, 1, 12)


class FireTaban(StoktanTaban):
    """profil → kesim (1 profil = 2 kesilmiş) → büküm (1:1) → paketleme (1:1) → mamul. Stok için ÜS: 10 mamul (kesim 5 çalıştırma, profil 5 ayrılı)."""

    def setUp(self):
        super().setUp()
        uretim.uretim_emri_iptal(self.emir)
        self.us = self.ac("10")
        self.ie_kesim = self.ie(self.kesim_op)
        self.ie_bukum = self.ie(self.bukum_op)
        self.ie_pak = self.ie(self.pak_op)

    def ie(self, op):
        return IstasyonEmri.objects.get(uretim_emri=self.us, operasyon=op, silindi=False)

    def uret(self, ie, miktar=None, fire=None):
        kayit, _uyari = uretim.istasyon_emri_uret(ie, miktar=miktar, tarih=GUN, kullanici=self.yon, fire=fire)
        return kayit

    def ayr(self, stok):
        return sa.ayrilan_miktar(self.us, stok)

    def yenile(self, *ieler):
        for ie in ieler:
            ie.refresh_from_db()

    def kesimi_uret(self):
        self.uret(self.ie_kesim, miktar="10")                                # 5 çalıştırma → 10 kesilmiş (ÜS'ye ayrılı), profil tüketildi
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.durum, self.ie_kesim.planlanan, self.ayr(self.kesilmis), self.ayr(self.profil)), ("BITTI", D("5"), D("10"), D("0")))

    def fire_kayit(self):
        """Büküm: 9 sağlam + 1 fire → 10 kesilmiş tüketilir, kalan büküm 1, ÜS'ye ayrılı kesilmiş 0."""
        self.kesimi_uret()
        return self.uret(self.ie_bukum, miktar="9", fire="1")


class SerbestStokTest(FireTaban):
    def test_serbest_stok_varsa_uses_ayrilir_emir_artmaz(self):
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"))      # serbest kesilmiş 3
        kayit = self.fire_kayit()
        self.yenile(self.ie_kesim, self.ie_bukum)
        self.assertEqual((self.ie_bukum.tamamlanan, self.ie_bukum.kalan), (D("9"), D("1")))
        self.assertEqual(self.ayr(self.kesilmis), D("1"))                      # kalan büküm 1 → 1 kesilmiş serbest stoktan ÜS'ye ayrıldı
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.durum, self.ie_kesim.fire_ek), (D("5"), "BITTI", {}))          # kesim emri artmadı
        kayit.refresh_from_db()
        ek = kayit.fire_ek
        self.assertEqual([(a["kod"], a["miktar"]) for a in ek["ayirma"]], [("IK-KESILMIS", "1")])
        self.assertEqual((ek["istasyon_emirleri"], ek["fire"]), ([], "1"))
        r = self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN).get()
        self.assertIn(kayit.no, r.aciklama)

    def test_ek_ihtiyac_ust_siniri_fire_kadardir(self):
        """Serbest stok bol olsa da ayrılan ek, fire'dan (1) fazla olmaz."""
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("50"))
        self.fire_kayit()
        self.assertEqual(self.ayr(self.kesilmis), D("1"))


class EmirArtisiTest(FireTaban):
    def test_serbest_stok_yoksa_girdi_emri_artar_yeniden_acilir_ve_zincirlenir(self):
        kayit = self.fire_kayit()
        self.yenile(self.ie_kesim, self.ie_bukum)
        self.assertEqual(self.ayr(self.kesilmis), D("0"))
        # girdiyi üreten KESİM emri: 5 → 5,5 (1 kesilmiş = 0,5 çalıştırma), BİTTİ'den yeniden açıldı; ihtiyaç 10 → 11
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.tamamlanan, self.ie_kesim.durum), (D("5.5"), D("5"), "BASLADI"))
        self.assertEqual(self.ie_kesim.ihtiyac, {str(self.kesilmis.pk): "11"})
        # zincir: kesim emrinin girdisi (profil) serbest stoktan ÜS'ye ayrıldı (0,5)
        self.assertEqual(self.ayr(self.profil), D("0.5"))
        kayit.refresh_from_db()
        x = kayit.fire_ek["istasyon_emirleri"][0]
        self.assertEqual((x["no"], x["once"], x["sonra"], x["ek"], x["yeni"], x["durum_once"], x["durum_sonra"]),
                         (self.ie_kesim.no, "5", "5.5", "0.5", False, "BITTI", "BASLADI"))
        self.assertEqual([(a["kod"], a["miktar"]) for a in kayit.fire_ek["ayirma"]], [("IK-PROFIL", "0.5")])
        # ek parça üretilince ÜS'ye ayrılır (ihtiyaç genişledi) → büküm kalanı tamamlanabilir
        self.uret(self.ie_kesim)
        self.assertEqual(self.ayr(self.kesilmis), D("1"))
        self.uret(self.ie_bukum)
        self.yenile(self.ie_bukum)
        self.assertEqual((self.ie_bukum.durum, self.ie_bukum.tamamlanan), ("BITTI", D("10")))

    def test_iki_seviye_zincirleme_paketlemede_fire(self):
        """Paketleme fire'ı: girdisi bükülmüşü üreten BÜKÜM emri yeniden açılır, onun girdisi kesilmişi üreten KESİM emri de, profil ayrılır."""
        self.kesimi_uret()
        self.uret(self.ie_bukum, miktar="10")
        self.yenile(self.ie_bukum)
        self.assertEqual((self.ie_bukum.durum, self.ayr(self.bukulmus)), ("BITTI", D("10")))
        self.uret(self.ie_pak, miktar="9", fire="1")
        self.yenile(self.ie_bukum, self.ie_kesim, self.ie_pak)
        self.assertEqual((self.ie_pak.tamamlanan, self.ie_pak.kalan), (D("9"), D("1")))
        self.assertEqual((self.ie_bukum.planlanan, self.ie_bukum.durum), (D("11"), "BASLADI"))
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.durum), (D("5.5"), "BASLADI"))
        self.assertEqual(self.ayr(self.profil), D("0.5"))
        self.assertEqual(self.ie_bukum.ihtiyac, {str(self.bukulmus.pk): "11"})

    def test_emir_yoksa_yeni_istasyon_emri_acilir(self):
        """Girdiyi üreten emir ÜS'de hiç yoksa (stoktan karşılanmıştı) fire için yeni emir açılır; geri alınınca İPTAL olur."""
        self.kesimi_uret()
        IstasyonEmri.objects.filter(pk=self.ie_kesim.pk).update(silindi=True)
        kayit = self.uret(self.ie_bukum, miktar="9", fire="1")
        yeni = IstasyonEmri.objects.get(uretim_emri=self.us, operasyon=self.kesim_op, silindi=False)
        self.assertEqual((yeni.planlanan, yeni.tamamlanan, yeni.fire_ek), (D("0.5"), D("0"), {"ek": "0.5"}))
        kayit.refresh_from_db()
        self.assertTrue(kayit.fire_ek["istasyon_emirleri"][0]["yeni"])
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        yeni.refresh_from_db()
        self.assertEqual((yeni.durum, yeni.planlanan), ("IPTAL", D("0")))


class TamamlanmisEmirTest(FireTaban):
    def test_kalani_olmayan_emirde_ek_yok(self):
        self.kesimi_uret()
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("1"))
        kayit = self.uret(self.ie_bukum, miktar="10", fire="1")                # 10 sağlam + 1 fire: büküm TAMAM (kalan 0), 11 kesilmiş tüketildi
        self.yenile(self.ie_bukum)
        self.assertEqual((self.ie_bukum.durum, self.ie_bukum.kalan), ("BITTI", D("0")))
        kayit.refresh_from_db()
        self.assertEqual(kayit.fire_ek, {})
        self.assertFalse(self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN).exists())
        self.assertEqual(uretim_fire.fire_notlari(self.us), [])

    def test_firesiz_akis_degismez(self):
        self.kesimi_uret()
        kayit = self.uret(self.ie_bukum, miktar="9")
        kayit.refresh_from_db()
        self.assertEqual((kayit.fire_ek, self.ayr(self.kesilmis)), ({}, D("1")))
        self.assertFalse(self.us.revizyonlar.filter(tur__in=[UretimEmriRevizyon.Tur.FIRE_EK_PLAN, UretimEmriRevizyon.Tur.FIRE_GERI_ALMA]).exists())


class FasonRozetTest(FireTaban):
    def test_fason_istasyon_emri_artarsa_rozet_cikar_ve_fason_donusle_kalkar(self):
        self.kesimi_uret()
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)        # kesim istasyonu fason
        kayit = self.uret(self.ie_bukum, miktar="9", fire="1")
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.fire_ek), (D("5.5"), {"ek": "0.5"}))
        kayit.refresh_from_db()
        self.assertTrue(kayit.fire_ek["istasyon_emirleri"][0]["fason"])
        satirlar = [e for e in uretim.uretim_emri_eksikleri(self.us) if e["tur"] == "FASON_EMRI_GEREKLI"]
        self.assertEqual([(e["stok"].pk, e["eksik"], e.get("fire")) for e in satirlar], [(self.kesilmis.pk, D("1"), True)])       # 0,5 çalıştırma × 2
        self.client.force_login(self.yon)
        html = self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk]) + "?sekme=malzeme").content.decode()
        self.assertIn("fason emri gerekli — fire kaynaklı", html)
        self.assertIn("Fire Kaynaklı Ek Plan", html)
        self.assertIn(kayit.no, html)
        # parça fason dönüşle gelince (kalan düşer) rozet kalkar
        IstasyonEmri.objects.filter(pk=self.ie_kesim.pk).update(tamamlanan=D("5.5"))
        self.assertEqual([e for e in uretim.uretim_emri_eksikleri(self.us) if e["tur"] == "FASON_EMRI_GEREKLI"], [])

    def test_revize_plani_bastan_kurar_rozet_ve_geri_alma_izi_temizlenir(self):
        self.kesimi_uret()
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)
        kayit = self.uret(self.ie_bukum, miktar="9", fire="1")
        uretim.uretim_emri_revize(self.us, kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "10"}], kullanici=self.yon)
        self.yenile(self.ie_kesim)
        self.assertEqual(self.ie_kesim.fire_ek, {})
        kayit.refresh_from_db()
        self.assertTrue(kayit.fire_ek["yenilendi"])
        self.assertEqual([e for e in uretim.uretim_emri_eksikleri(self.us) if e.get("fire")], [])
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)      # yenilenmiş ek geri sarılmaz (revize planı zaten baştan kurdu)
        self.yenile(self.ie_kesim)
        self.assertFalse(self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_GERI_ALMA).exists())


class GeriAlmaTest(FireTaban):
    def test_geri_alinca_ek_ayirma_ve_emir_artisi_geri_sarilir(self):
        kayit = self.fire_kayit()
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        self.yenile(self.ie_kesim, self.ie_bukum)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.durum, self.ie_kesim.ihtiyac, self.ie_kesim.fire_ek), (D("5"), "BITTI", {str(self.kesilmis.pk): "10"}, {}))
        self.assertEqual((self.ayr(self.profil), self.ayr(self.kesilmis), self.ayr(self.bukulmus)), (D("0"), D("10"), D("0")))     # büküm öncesi duruma döndü
        self.assertEqual((self.ie_bukum.tamamlanan, self.ie_bukum.durum), (D("0"), "BEKLIYOR"))
        r = self.us.revizyonlar.get(tur=UretimEmriRevizyon.Tur.FIRE_GERI_ALMA)
        self.assertIn(kayit.no, r.aciklama)
        self.assertEqual(uretim_fire.fire_notlari(self.us), [])

    def test_geri_alinca_serbest_stoktan_ayirma_da_dusar(self):
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=D("3"))
        kayit = self.fire_kayit()
        self.assertEqual(self.ayr(self.kesilmis), D("1"))
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        self.assertEqual(self.ayr(self.kesilmis), D("10"))                       # yalnız fire ekinin 1'i düştü; kaydın tükettiği ayırma aynen geri geldi

    def test_ek_uretildiyse_planlanan_tamamlananin_altina_inmez(self):
        kayit = self.fire_kayit()
        self.uret(self.ie_kesim)                                                 # ek 0,5 çalıştırma üretildi (tamamlanan 5,5)
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.tamamlanan, self.ie_kesim.durum), (D("5.5"), "BITTI"))
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.tamamlanan, self.ie_kesim.durum), (D("5.5"), D("5.5"), "BITTI"))

    def test_iki_kez_geri_alma_idempotent(self):
        kayit = self.fire_kayit()
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        n = self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_GERI_ALMA).count()
        self.assertIsNone(uretim_fire.fire_ek_geri_al(kayit, self.yon))
        self.assertEqual(self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_GERI_ALMA).count(), n)


class DayaniklilikTest(FireTaban):
    def test_ek_plan_hatasi_uretim_kaydini_engellemez(self):
        self.kesimi_uret()
        with mock.patch.object(uretim, "_plani_hesapla", side_effect=uretim.UretimHatasi("deneme hatası")):
            kayit = self.uret(self.ie_bukum, miktar="9", fire="1")
        kayit.refresh_from_db()
        self.yenile(self.ie_bukum, self.ie_kesim)
        self.assertEqual((kayit.durum, self.ie_bukum.tamamlanan, kayit.fire_ek), ("ONAYLI", D("9"), {}))
        self.assertEqual((self.ie_kesim.planlanan, self.ayr(self.profil)), (D("5"), D("0")))                  # kısmi yazım kalmadı
        r = self.us.revizyonlar.get(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN)
        self.assertIn("yapılamadı", r.aciklama)
        self.assertEqual(r.detay["hata"], "deneme hatası")

    def test_ek_iki_kez_uygulanmaz(self):
        kayit = self.fire_kayit()
        kayit.refresh_from_db()
        n = self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN).count()
        self.assertIsNone(uretim_fire.fire_ek_planla(kayit, self.yon))
        self.assertEqual(self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN).count(), n)


class GeriyeDonukKomutTest(FireTaban):
    def eski_fire_kayit(self):
        """Fire kuralı yokken onaylanmış kaydı taklit eder."""
        self.kesimi_uret()
        with mock.patch.object(uretim_fire, "fire_ek_planla", return_value=None):
            return self.uret(self.ie_bukum, miktar="9", fire="1")

    def komut(self, *args):
        cikti = StringIO()
        call_command("fire_ek_uygula", *args, stdout=cikti)
        return cikti.getvalue()

    def test_kuru_yazmaz_uygula_yazar_ikinci_kez_atlar(self):
        kayit = self.eski_fire_kayit()
        self.assertEqual(self.ayr(self.profil), D("0"))
        cikti = self.komut(kayit.no)
        self.assertIn("KURU", cikti)
        self.assertIn("IK-PROFIL", cikti)
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ayr(self.profil)), (D("5"), D("0")))                      # kuru: hiçbir şey yazılmadı
        cikti = self.komut(kayit.no, "--uygula")
        self.assertIn("UYGULANDI", cikti)
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ayr(self.profil)), (D("5.5"), D("0.5")))
        kayit.refresh_from_db()
        self.assertTrue(kayit.fire_ek["geriye_donuk"])
        self.assertIn("zaten uygulanmış", self.komut(kayit.no, "--uygula"))
        self.yenile(self.ie_kesim)
        self.assertEqual(self.ie_kesim.planlanan, D("5.5"))

    def test_bulunamayan_kayit_hata(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.komut("OP-2026-9999")


class PlanFazlasiTest(FireTaban):
    """Tam boy yuvarlaması (4,5 → 5 koşum) planda fazlalık bırakır: fire için yeni koşum açmadan ÜRETEN emrin ihtiyacı artar (canlıda IE-0375 fason +2 koşum hatası)."""

    def setUp(self):
        super().setUp()
        uretim.uretim_emri_iptal(self.us)
        Operasyon.objects.filter(pk=self.kesim_op.pk).update(tam_calistirma=True)
        self.us = self.ac("9")
        self.ie_kesim, self.ie_bukum, self.ie_pak = self.ie(self.kesim_op), self.ie(self.bukum_op), self.ie(self.pak_op)

    def hazirla(self):
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac), (D("5"), {str(self.kesilmis.pk): "9"}))        # 5 koşum = 10 parça, ihtiyaç 9 → fazla 1
        self.uret(self.ie_kesim, miktar="8")                                                                              # 4 koşum üretildi, 1 koşum kaldı
        return self.uret(self.ie_bukum, miktar="7", fire="1")                                                             # 8 kesilmiş tüketildi, ayırma 0, büküm kalan 2

    def test_plan_fazlasi_kullanilir_yeni_kosum_acilmaz(self):
        kayit = self.hazirla()
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac, self.ie_kesim.fire_ek), (D("5"), {str(self.kesilmis.pk): "10"}, {}))
        kayit.refresh_from_db()
        x = kayit.fire_ek["istasyon_emirleri"][0]
        self.assertEqual((x["no"], x["ek"], x["sadece_ihtiyac"], x["ihtiyac_ek"]), (self.ie_kesim.no, "0", True, {str(self.kesilmis.pk): "1"}))
        self.assertEqual(self.ayr(self.profil), D("0.5") * 0 + self.ayr(self.profil))
        self.uret(self.ie_kesim)                                                                                          # kalan koşum: 2 parça, ihtiyaç 10 → ÜSye


class PlanFazlasiTest(FireTaban):
    """Tam boy yuvarlaması (4,5 → 5 koşum) planda fazlalık bırakır: fire için yeni koşum açmadan ÜRETEN emrin ihtiyacı artar (canlıda IE-0375 fason +2 koşum hatası)."""

    def setUp(self):
        super().setUp()
        uretim.uretim_emri_iptal(self.us)
        Operasyon.objects.filter(pk=self.kesim_op.pk).update(tam_calistirma=True)
        self.us = self.ac("9")
        self.ie_kesim, self.ie_bukum, self.ie_pak = self.ie(self.kesim_op), self.ie(self.bukum_op), self.ie(self.pak_op)

    def hazirla(self):
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac), (D("5"), {str(self.kesilmis.pk): "9"}))        # 5 koşum = 10 parça, ihtiyaç 9 → fazla 1
        self.uret(self.ie_kesim, miktar="8")                                                                              # 4 koşum üretildi, 1 koşum kaldı
        return self.uret(self.ie_bukum, miktar="7", fire="1")                                                             # 8 kesilmiş tüketildi, ayırma 0, büküm kalan 2

    def test_plan_fazlasi_kullanilir_yeni_kosum_acilmaz(self):
        kayit = self.hazirla()
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac, self.ie_kesim.fire_ek), (D("5"), {str(self.kesilmis.pk): "10"}, {}))
        kayit.refresh_from_db()
        x = kayit.fire_ek["istasyon_emirleri"][0]
        self.assertEqual((x["no"], x["ek"], x["sadece_ihtiyac"], x["ihtiyac_ek"]), (self.ie_kesim.no, "0", True, {str(self.kesilmis.pk): "1"}))
        self.uret(self.ie_kesim)                                                                                          # kalan koşum: 2 parça, ihtiyaç 10 → ÜS'ye 2 ayrılır
        self.assertEqual(self.ayr(self.kesilmis), D("2"))
        self.uret(self.ie_bukum)
        self.yenile(self.ie_bukum)
        self.assertEqual((self.ie_bukum.durum, self.ie_bukum.tamamlanan), ("BITTI", D("9")))
        r = self.us.revizyonlar.get(tur=UretimEmriRevizyon.Tur.FIRE_EK_PLAN)
        self.assertIn("plan fazlası", r.aciklama)

    def test_geri_alinca_ihtiyac_eski_haline_doner(self):
        kayit = self.hazirla()
        uretim.operasyon_kaydi_sil(kayit, kullanici=self.yon, onayli_geri_al=True)
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac), (D("5"), {str(self.kesilmis.pk): "9"}))

    def test_fason_rozeti_cikmaz(self):
        self.hazirla()
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)
        self.assertEqual([e for e in uretim.uretim_emri_eksikleri(self.us) if e["tur"] == "FASON_EMRI_GEREKLI"], [])

    def test_yeniden_komutu_ekle_gunceller(self):
        kayit = self.hazirla()
        cikti = StringIO()
        call_command("fire_ek_uygula", kayit.no, "--yeniden", "--uygula", stdout=cikti)
        self.assertIn("geri sarıldı", cikti.getvalue())
        self.yenile(self.ie_kesim)
        self.assertEqual((self.ie_kesim.planlanan, self.ie_kesim.ihtiyac), (D("5"), {str(self.kesilmis.pk): "10"}))
        kayit.refresh_from_db()
        self.assertTrue(kayit.fire_ek and not kayit.fire_ek.get("geri_alindi"))
        self.assertEqual(self.us.revizyonlar.filter(tur=UretimEmriRevizyon.Tur.FIRE_GERI_ALMA).count(), 1)
