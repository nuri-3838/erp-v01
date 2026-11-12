"""ÜS REVİZE — bu ÜS'ten ZATEN ÜRETİLMİŞ mamul yeniden üretilmez (2026-11-11, ÜS-2026-0024 vakası).

Vaka: stok için ÜS'te bir ürünün zinciri tamamen üretildi, mamul satıldı (serbest stoktan düştü); sonra değişiklik yapılmadan revize edilince kök mamul tavanı 0 olduğundan
ürünün istasyon emirleri 5'ten 10'a çıktı, ara (kesim) emirleri bu fazlayı da içerecek şekilde büyüdü. Kural: üretilecek = hedef − sevk − bu ÜS'ten üretilmiş (onaylı istasyon
kaydı çıktısı); tamamlanan emirler büyütülmez, kalan ihtiyaç kadar açık emir çıkar; kalem çıkarılınca / azalınca ara emirler güncel ihtiyaca iner.
Bölümler: stok için (ürün zinciri üretildi + satıldı → değişiklik yok / azaltma / artış / kalem çıkarma), 3 ürün × 5, sipariş bağlantılı ÜS, detay "Üretilecek" sütunu."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import IstasyonEmri, StokHareket, UretimEmri
from core.services import stok_ayirma as sa
from core.services import uretim
from core.services.hareket import hareket_ekle
from core.services.uretim import (
    istasyon_emri_kayit_ac, operasyon_kaydi_onayla, operasyon_olustur, siparis_revize, uretim_emri_iptal, uretim_emri_olustur, uretim_emri_revize,
)
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_teklif_siparis import SiparisUretimFixture

D = Decimal
BUGUN = date(2026, 1, 15)


class UretilmisTaban(Taban):
    """Taban (profil → kesim 2/çalıştırma → büküm 1:1) + mamul2, mamul3 (ikisi de büküm'de 2 kesilmişten... değil: 1 kesilmişten 1 mamul). Taban'ın ÜS'ü iptal; kendi ÜS'ümüz
    ``self.us``: 3 ürün × 5 (kesilmiş ortak ara mamul: 15 adet → kesim 7,5 çalıştırma)."""

    def setUp(self):
        super().setUp()
        uretim_emri_iptal(self.emir)
        self.mamul2 = type(self.bukulmus).objects.create(kod="IK-MAMUL2", ad="ikinci mamul", kategori=self.kat, uretim_birimi=self.birim, fatura_birimi=self.birim, satis_urunu=True)
        self.mamul3 = type(self.bukulmus).objects.create(kod="IK-MAMUL3", ad="üçüncü mamul", kategori=self.kat, uretim_birimi=self.birim, fatura_birimi=self.birim, satis_urunu=True)
        self.op2 = operasyon_olustur(istasyon_id=self.bukum.pk, cikti_id=self.mamul2.pk, cikti_miktar=D("1"), satirlar=[(self.kesilmis, D("1"))])
        self.op3 = operasyon_olustur(istasyon_id=self.bukum.pk, cikti_id=self.mamul3.pk, cikti_miktar=D("1"), satirlar=[(self.kesilmis, D("1"))])

    def us_ac(self, *kalemler):
        self.us = uretim_emri_olustur(kalemler=[{"hedef_urun_id": s.pk, "hedef_miktar": m} for s, m in kalemler], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        return self.us

    def revize(self, *kalemler):
        return uretim_emri_revize(self.us, kalemler=[{"hedef_urun_id": s.pk, "hedef_miktar": m} for s, m in kalemler], tarih=BUGUN)

    def ie(self, op):
        return self.us.istasyon_emirleri.get(operasyon=op, silindi=False)

    def plan(self):
        return {ie.operasyon_id: (ie.planlanan, ie.tamamlanan, ie.durum) for ie in self.us.istasyon_emirleri.filter(silindi=False)}

    def uret(self, op, hedef=None):
        ie = self.ie(op)
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie, hedef=hedef) if hedef else istasyon_emri_kayit_ac(ie))

    def sat(self, stok, miktar):
        """Serbest stoktan çıkış (ÜS'ten bağımsız satış): ayırma satırına dokunmaz, yalnız eldeki düşer."""
        hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=timezone.localdate(), tur=StokHareket.Tur.CIKIS, miktar=D(str(miktar)))

    def ayr(self, stok):
        return sa.ayrilan_miktar(self.us, stok)


class StokIcinUretilmisTest(UretilmisTaban):
    def test_zinciri_uretilmis_mamul_satildi_revize_buyutmez(self):
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))                                # 2. ürün üretilmedi: ÜS açık kalır (tamamı üretilse stok ÜS'si kapanırdı)
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)                                                            # 5 bükülmüş üretildi, ÜS'ye ayrıldı
        self.assertEqual((self.ayr(self.bukulmus), self.plan()[self.bukum_op.pk]), (D("5"), (D("5"), D("5"), "BITTI")))
        self.sat(self.bukulmus, 1)                                                          # biri satıldı (ÜS'ten bağımsız satış)
        once = self.plan()
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))                               # değişiklik YOK
        self.assertEqual(self.plan(), once, "tamamlanmış emirler revizeyle büyümemeli")
        self.assertEqual(self.ayr(self.bukulmus), D("5"))                                    # üretilmiş kısım ÜS'ye ayrılı kalır (serbest stoğa düşmez)
        kalem = self.us.kalemler.get(silindi=False, hedef_urun=self.bukulmus)
        self.assertEqual(kalem.eldeki_ayrilan, D("5"))
        self.assertEqual(uretim.uretim_emri_eksikleri(self.us), [])                         # güncel plan da fazladan ihtiyaç çıkarmaz
        r = self.us.revizyonlar.get(no=1)
        self.assertEqual(r.detay["uretilmis"], [{"stok": "IK-BUKULMUS", "miktar": "5"}])

    def test_uretilmis_ve_satilmis_ikinci_revize_ve_guncel_plan_tutarli(self):
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        self.sat(self.bukulmus, 2)
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))                               # art arda: yine değişmez
        self.assertEqual(self.plan()[self.bukum_op.pk], (D("5"), D("5"), "BITTI"))
        self.assertEqual(self.plan()[self.kesim_op.pk], (D("5"), D("5"), "BITTI"))
        self.assertEqual(self.plan()[self.op2.pk], (D("5"), D("0"), "BEKLIYOR"))

    def test_azaltma_tamamlananin_altina_dusmez(self):
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        self.sat(self.bukulmus, 5)                                                          # hepsi satıldı
        self.revize((self.bukulmus, "3"), (self.mamul2, "5"))
        self.assertEqual(self.plan()[self.bukum_op.pk], (D("5"), D("5"), "BITTI"))          # planlanan tamamlananın altına inmez, büyümez
        self.assertEqual(self.ayr(self.bukulmus), D("3"))                                    # ayrılan hedefle sınırlı
        self.assertEqual(uretim.uretim_emri_eksikleri(self.us), [])

    def test_artis_yalniz_eksik_kalan_icin_emir_acar(self):
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        self.sat(self.bukulmus, 5)
        self.revize((self.bukulmus, "8"), (self.mamul2, "5"))                               # 5 üretilmiş sayılır → 3 eksik
        p = self.plan()
        self.assertEqual(p[self.bukum_op.pk][:2], (D("8"), D("5")))                          # tamamlanan 5 + yeni 3
        self.assertEqual(p[self.kesim_op.pk][:2], (D("6.5"), D("5")))                       # 8 kesilmiş gerek, 5'i eldeki ayrılı → 3 kesilmiş = 1,5 çalıştırma
        self.assertEqual(self.ayr(self.bukulmus), D("5"))

    def test_gercek_kalan_ihtiyac_stoksuz_da_uretilir(self):
        """Üretilmiş sayılan kısım yalnız gerçekten üretileni kapsar: hiç üretilmemiş bir ÜS'te revize eski gibi tam planlar."""
        self.us_ac((self.bukulmus, "5"))
        self.revize((self.bukulmus, "7"))
        self.assertEqual(self.plan(), {self.kesim_op.pk: (D("3.5"), D("0"), "BEKLIYOR"), self.bukum_op.pk: (D("7"), D("0"), "BEKLIYOR")})
        self.assertNotIn("uretilmis", self.us.revizyonlar.get(no=1).detay)


class UcUrunTest(UretilmisTaban):
    """3 ürün × 5; BİR ürünün zinciri üretildi ve mamul satıldı."""

    def hazirla(self):
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"), (self.mamul3, "5"))
        self.assertEqual(self.plan()[self.kesim_op.pk][0], D("7.5"))                        # ortak ara mamul: 15 kesilmiş
        self.uret(self.kesim_op)                                                            # 15 kesilmiş üretildi
        self.uret(self.bukum_op)                                                            # yalnız 1. ürün: 5 mamul
        self.sat(self.bukulmus, 5)

    def test_degisiklik_yok_uretilmis_urunun_emirleri_buyumez_kesim_kalan_icin(self):
        self.hazirla()
        once = self.plan()
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"), (self.mamul3, "5"))
        sonra = self.plan()
        self.assertEqual(sonra[self.bukum_op.pk], (D("5"), D("5"), "BITTI"))                # üretilmiş ürün: büyümedi, "üretilecek" 0
        self.assertEqual(sonra[self.kesim_op.pk], once[self.kesim_op.pk])                  # kesim: 10 kesilmiş zaten ÜS'ye ayrılı → yeni kesim yok
        self.assertEqual((sonra[self.op2.pk], sonra[self.op3.pk]), ((D("5"), D("0"), "BEKLIYOR"), (D("5"), D("0"), "BEKLIYOR")))   # kalan 2 ürün aynen
        self.assertEqual((self.ayr(self.bukulmus), self.ayr(self.kesilmis)), (D("5"), D("10")))
        self.assertEqual(uretim.uretim_emri_eksikleri(self.us), [])

    def test_kalem_cikarilinca_ara_emirler_azalir(self):
        """Hiç üretilmemiş ÜS: kalem çıkarınca ara (kesim) emri güncel ihtiyaca iner, çıkan ürünün emri iptal olur."""
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"), (self.mamul3, "5"))
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))
        p = self.plan()
        self.assertEqual((p[self.kesim_op.pk][0], p[self.op3.pk][2]), (D("5"), "IPTAL"))
        self.assertEqual(self.ayr(self.profil), D("5"))

    def test_uretilmis_urun_ve_kalem_cikarma(self):
        """Üretilmiş ürün listede kalsın, bir üretilmemiş ürün çıksın: üretilmişin emirleri büyümez, çıkanın emri iptal, kesim yalnız kalan ihtiyaçtan düşer."""
        self.hazirla()
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))
        p = self.plan()
        self.assertEqual(p[self.bukum_op.pk], (D("5"), D("5"), "BITTI"))
        self.assertEqual((p[self.op2.pk], p[self.op3.pk][2]), ((D("5"), D("0"), "BEKLIYOR"), "IPTAL"))
        self.assertEqual(p[self.kesim_op.pk][:2], (D("7.5"), D("7.5")))                    # tamamlanan 7,5'in altına inmez (5 kesilmiş mamul3 için fazla: serbest)
        self.assertEqual(self.ayr(self.kesilmis), D("5"))                                   # yalnız mamul2'nin kalan 5 kesilmişi ayrılı; fazlası serbest

    def test_uretilmis_urun_kalemden_cikarilirsa_emirleri_korunur(self):
        self.hazirla()
        self.revize((self.mamul2, "5"), (self.mamul3, "5"))
        p = self.plan()
        self.assertEqual(p[self.bukum_op.pk][:2], (D("5"), D("5")))                         # üretilmiş kayıt silinmez, planlanan tamamlananın altına inmez
        self.assertEqual(p[self.kesim_op.pk][:2], (D("7.5"), D("7.5")))


class SiparisUretilmisTest(SiparisUretimFixture, TestCase):
    """Sipariş bağlantılı ÜS (SSS): aynı kural."""

    def _ac(self):
        hareket_ekle(stok_id=self.hammadde.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=D("100"))
        sip = self._siparis()
        emir = uretim.siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=BUGUN,
                                                     kalem_secimleri=[{"kalem_id": sip.kalemler.get(stok=self.uretilebilir).pk, "hedef_miktar": "5"}])
        return sip, emir

    def satirlar(self, uretilebilir="5"):
        return [{"stok_id": self.uretilebilir.pk, "miktar": uretilebilir, "birim_fiyat": "100"}, {"stok_id": self.uretilemez.pk, "miktar": "2", "birim_fiyat": "30"}]

    def test_uretilmis_satilmis_siparis_revize_buyutmez(self):
        sip, emir = self._ac()
        ie = emir.istasyon_emirleri.get()
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie))                                  # 5 üretildi → ÜS'ye ayrıldı
        hareket_ekle(stok_id=self.uretilebilir.pk, depo_id=self.depo.pk, tarih=timezone.localdate(), tur=StokHareket.Tur.CIKIS, miktar=D("2"))   # ÜS dışı çıkış
        siparis_revize(sip, satirlar=self.satirlar("5"), kullanici=self.yon)
        ie.refresh_from_db()
        emir.refresh_from_db()
        self.assertEqual((ie.planlanan, ie.tamamlanan, ie.durum), (D("5"), D("5"), "BITTI"))
        self.assertEqual(sa.ayrilan_miktar(emir, self.uretilebilir), D("5"))
        self.assertEqual(emir.kalemler.get(silindi=False).eldeki_ayrilan, D("5"))

    def test_uretilmis_uzerine_artis_yalniz_fark_icin(self):
        sip, emir = self._ac()
        ie = emir.istasyon_emirleri.get()
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie))
        hareket_ekle(stok_id=self.uretilebilir.pk, depo_id=self.depo.pk, tarih=timezone.localdate(), tur=StokHareket.Tur.CIKIS, miktar=D("5"))
        siparis_revize(sip, satirlar=self.satirlar("7"), kullanici=self.yon)
        ie.refresh_from_db()
        self.assertEqual((ie.planlanan, ie.tamamlanan), (D("7"), D("5")))                   # 5 üretilmiş + 2 yeni

    def test_onizleme_uretilmis_miktari_gosterir(self):
        sip, emir = self._ac()
        ie = emir.istasyon_emirleri.get()
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(ie))
        kalemler, sk = uretim.siparis_revize_kalemleri(sip, self.satirlar("5"))
        satirlar = uretim.stoktan_karsilama_onizleme(kalemler, varyantli=emir.varyantli_plan, haric_emir=emir, siparis_kalemleri=sk)
        self.assertEqual((satirlar[0]["uretilmis"], satirlar[0]["uret_hedef"]), (D("5"), D("0")))


class DetayKalemSutunlariTest(UretilmisTaban):
    """Detay "Ayrılan / Üretilecek / İlerleme" aynı mantık: üretilecek = hedef − ayrılan − sevk."""

    def test_sutunlar_tutarli(self):
        from django.contrib.auth import get_user_model
        yon = get_user_model().objects.create_superuser("us_yon", password="x")
        self.client.force_login(yon)
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        self.sat(self.bukulmus, 5)
        self.revize((self.bukulmus, "5"), (self.mamul2, "5"))
        yanit = self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk]))
        self.assertEqual(yanit.status_code, 200)
        satirlar = {s["k"].hedef_urun.kod: s for s in yanit.context["kalemler"]}
        b, m = satirlar["IK-BUKULMUS"], satirlar["IK-MAMUL2"]
        self.assertEqual((b["ayrilan"], b["uretilecek"], b["yuzde"]), (D("5"), D("0"), D("100")))
        self.assertEqual((m["ayrilan"], m["uretilecek"], m["yuzde"]), (D("0"), D("5"), D("0")))
        self.assertEqual(uretim.uretim_emri_yuzde(self.us), D("50"))
        liste = uretim.uretim_emirleri_ozet([UretimEmri.objects.get(pk=self.us.pk)])[self.us.pk]
        self.assertEqual(liste["yuzde"], D("50"))

    def test_kapanmis_stok_us_satir_ilerlemesi_yuzde_100(self):
        """Tüm kalemleri üretilen stok ÜS'si kapanır, ayırmalar serbest kalır; satır İlerleme de genel %100 ile tutarlı, Üretilecek 0."""
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_superuser("us_yon2", password="x"))
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        self.uret(self.op2)
        self.us.refresh_from_db()
        self.assertEqual(self.us.durum, UretimEmri.Durum.KAPALI)
        yanit = self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk]))
        self.assertEqual(yanit.status_code, 200)
        satirlar = yanit.context["kalemler"]
        self.assertEqual(len(satirlar), 2)
        for s in satirlar:
            self.assertEqual((s["ayrilan"], s["uretilecek"], s["yuzde"]), (D("0"), D("0"), D("100")))
        self.assertTrue(yanit.context["tamamlandi"])

    def test_acik_us_satir_ilerlemesi_degismez(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_superuser("us_yon3", password="x"))
        self.us_ac((self.bukulmus, "5"), (self.mamul2, "5"))
        self.uret(self.kesim_op)
        self.uret(self.bukum_op)
        yanit = self.client.get(reverse("core:uretim_emri_detay", args=[self.us.pk]))
        satirlar = {s["k"].hedef_urun.kod: s for s in yanit.context["kalemler"]}
        self.assertEqual(satirlar["IK-BUKULMUS"]["yuzde"], D("100"))
        self.assertEqual((satirlar["IK-MAMUL2"]["yuzde"], satirlar["IK-MAMUL2"]["uretilecek"]), (D("0"), D("5")))
