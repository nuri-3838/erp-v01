"""Üretim Siparişleri iyileştirme Faz 1 (2026-10-14): termin + öncelik (servis, form, revize, hızlı düzenleme + geçmiş), Mamul % / İş %, türetilmiş "Sevk bekliyor"
(liste süzgeçleri), Acil → termin → tarih sıralaması (ÜS listesi, İstasyon Emirleri, atölye), dar ekranda "eşleşen yok" satırı, "?" ipuçları, fason depo reddi."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.forms import UretimEmriBaslikForm
from core.models import Depo, TeklifSiparis, UretimEmri
from core.services import us_detay, uretim
from core.services import us_detay_yukle as yukle_servis
from core.services.uretim import (UretimHatasi, istasyon_emri_kayit_ac, operasyon_kaydi_onayla, siparisten_uretim_emri_olustur,
                                  termin_oncelik_guncelle, uretim_emri_revize, uretim_emirleri_ozet)
from core.tests.test_atolye import AtolyeTaban
from core.tests.test_stoktan_karsilama import StoktanTaban
from core.tests.test_uretim_siparisi_kapanis import GUN, KapanisTaban

D = Decimal


class SiparisliTaban(KapanisTaban):
    def yeni_us(self, termin=None, oncelik="NORMAL", miktar="1"):
        sip = self.siparis(miktar)
        emir = siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=GUN, termin=termin, oncelik=oncelik,
                                              kalem_secimleri=[{"kalem_id": k.pk, "hedef_miktar": k.miktar} for k in sip.kalemler.filter(silindi=False)])
        return sip, emir

    def liste(self, **q):
        return self.client.get(reverse("core:uretim_emirleri"), q)

    def sira(self, **q):
        return [s["e"].pk for s in self.liste(**q).context["emirler"]]


class TerminOncelikServisTest(SiparisliTaban):
    def test_siparisten_acilista_termin_varsayilan_sipariş_teslim_tarihi(self):
        sip = self.siparis("2")
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=date(2026, 3, 15))
        sip.refresh_from_db()
        emir = siparisten_uretim_emri_olustur(siparis=sip, depo_id=self.depo.pk, tarih=GUN, kalem_secimleri=[{"kalem_id": k.pk, "hedef_miktar": k.miktar} for k in sip.kalemler.all()])
        self.assertEqual((emir.termin, emir.oncelik), (date(2026, 3, 15), "NORMAL"))

    def test_acik_termin_none_bos_birakir_ve_degistirilebilir(self):
        sip, emir = self.yeni_us(termin=None)
        self.assertIsNone(emir.termin)
        self.assertIsNone(uretim.etkin_termin(emir))
        TeklifSiparis.objects.filter(pk=sip.pk).update(gecerlilik_teslim_tarihi=date(2026, 4, 1))
        emir.refresh_from_db()
        self.assertEqual(uretim.etkin_termin(emir), date(2026, 4, 1))                                                   # eski ÜS: ÜS terminı boşsa kaynak sipariş

    def test_gecersiz_oncelik_reddedilir(self):
        with self.assertRaisesMessage(UretimHatasi, "Öncelik geçersiz"):
            self.yeni_us(oncelik="COK_ACIL")

    def test_fason_depo_uretim_siparisi_deposu_olamaz(self):
        fason = Depo.objects.create(kod="F9", ad="FASON TEST", fason_cari=self.musteri)
        sip = self.siparis()
        with self.assertRaisesMessage(UretimHatasi, "Fason deposu"):
            siparisten_uretim_emri_olustur(siparis=sip, depo_id=fason.pk, tarih=GUN, kalem_secimleri=[{"kalem_id": k.pk, "hedef_miktar": k.miktar} for k in sip.kalemler.all()])
        self.assertEqual(UretimEmri.objects.count(), 0)
        self.assertNotIn(fason, UretimEmriBaslikForm().fields["depo"].queryset)
        self.assertIn(self.depo, UretimEmriBaslikForm().fields["depo"].queryset)
        r = self.client.get(reverse("core:uretim_emri_ekle"))
        self.assertNotContains(r, "FASON TEST")
        self.assertContains(r, 'name="termin"')
        self.assertContains(r, 'name="oncelik"')

    def test_revize_termin_ve_oncelik_uygular_ve_gecmise_yazar(self):
        sip, emir = self.yeni_us(termin=date(2026, 2, 1), miktar="3")
        kalemler = [{"hedef_urun_id": self.stok.pk, "hedef_miktar": "3"}]
        uretim_emri_revize(emir, kalemler=kalemler, kullanici=self.yon, siparis_kalemleri=list(sip.kalemler.all()))             # termin/öncelik verilmedi → korunur
        emir.refresh_from_db()
        self.assertEqual((emir.termin, emir.oncelik), (date(2026, 2, 1), "NORMAL"))
        self.assertNotIn("termin_oncelik", emir.revizyonlar.order_by("no").last().detay)
        uretim_emri_revize(emir, kalemler=kalemler, kullanici=self.yon, siparis_kalemleri=list(sip.kalemler.all()), termin=date(2026, 2, 20), oncelik="ACIL")
        emir.refresh_from_db()
        self.assertEqual((emir.termin, emir.oncelik), (date(2026, 2, 20), "ACIL"))
        self.assertEqual(emir.revizyonlar.order_by("no").last().detay["termin_oncelik"],
                         {"once": {"termin": "2026-02-01", "oncelik": "NORMAL"}, "sonra": {"termin": "2026-02-20", "oncelik": "ACIL"}})
        self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, "Termin: 01.02.2026 → 20.02.2026")
        self.assertContains(r, "Öncelik: Normal → Acil")


class HizliDuzenlemeTest(SiparisliTaban):
    def post(self, emir, **veri):
        return self.client.post(reverse("core:uretim_emri_termin_oncelik", args=[emir.pk]), veri, follow=True)

    def test_degisiklik_kim_ne_zaman_eski_yeni_ile_gecmise_yazilir(self):
        sip, emir = self.yeni_us()
        once = emir.revizyonlar.count()
        r = self.post(emir, termin="2026-05-01", oncelik="ACIL")
        emir.refresh_from_db()
        self.assertEqual((emir.termin, emir.oncelik, emir.revizyonlar.count()), (date(2026, 5, 1), "ACIL", once + 1))
        k = emir.revizyonlar.order_by("no").last()
        self.assertEqual((k.tur, k.created_by_id, k.detay), ("DUZENLEME", self.yon.pk, {"once": {"termin": None, "oncelik": "NORMAL"}, "sonra": {"termin": "2026-05-01", "oncelik": "ACIL"}}))
        self.assertContains(r, "Termin/öncelik güncellendi")
        self.assertContains(r, "Termin: — → 01.05.2026")
        self.assertContains(r, "Öncelik: Normal → Acil")
        self.assertContains(r, "Termin/öncelik düzenleme")                                                                # olay adı
        self.assertContains(r, self.yon.username)                                                                           # kullanıcı sütunu
        self.assertContains(r, "ACİL")                                                                                      # başlık rozeti
        r = self.post(emir, termin="2026-06-10", oncelik="ACIL")                                                          # yalnız termin değişti
        k = emir.revizyonlar.order_by("no").last()
        self.assertEqual(k.detay["once"], {"termin": "2026-05-01", "oncelik": "ACIL"})
        self.assertContains(r, "Termin: 01.05.2026 → 10.06.2026")

    def test_degisiklik_yoksa_kayit_yazilmaz_ve_plan_dokunulmaz(self):
        sip, emir = self.yeni_us(termin=date(2026, 5, 1))
        ie_once = list(emir.istasyon_emirleri.values_list("pk", "planlanan", "durum"))
        n = emir.revizyonlar.count()
        r = self.post(emir, termin="2026-05-01", oncelik="NORMAL")
        self.assertContains(r, "Değişiklik yok")
        self.assertEqual(emir.revizyonlar.count(), n)
        self.post(emir, termin="", oncelik="ACIL")                                                                        # termin temizlendi + acil
        emir.refresh_from_db()
        self.assertEqual((emir.termin, emir.oncelik), (None, "ACIL"))
        self.assertEqual(list(emir.istasyon_emirleri.values_list("pk", "planlanan", "durum")), ie_once)                  # plan aynı (revize değil)

    def test_gecersiz_tarih_ve_oncelik_ve_kapali_us_reddedilir(self):
        sip, emir = self.yeni_us()
        r = self.post(emir, termin="31.02.2026", oncelik="NORMAL")
        self.assertContains(r, "Termin geçerli bir tarih olmalı")
        r = self.post(emir, termin="", oncelik="SUPER")
        self.assertContains(r, "Öncelik geçersiz")
        emir.refresh_from_db()
        self.assertEqual((emir.termin, emir.oncelik), (None, "NORMAL"))
        uretim.uretim_emri_iptal(emir, self.yon)
        r = self.post(emir, termin="2026-05-01", oncelik="ACIL")
        self.assertContains(r, "Yalnız açık üretim siparişinin")
        with self.assertRaises(UretimHatasi):
            termin_oncelik_guncelle(emir, termin=None, oncelik="ACIL")

    def test_detay_formu_yalniz_acikta(self):
        sip, emir = self.yeni_us(termin=date(2026, 5, 1))
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertContains(r, reverse("core:uretim_emri_termin_oncelik", args=[emir.pk]))
        self.assertContains(r, 'value="2026-05-01"')
        uretim.uretim_emri_iptal(emir, self.yon)
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertNotContains(r, reverse("core:uretim_emri_termin_oncelik", args=[emir.pk]))


class IlerlemeVeDurumTest(SiparisliTaban):
    def test_is_yuzdesi_liste_ve_detay_ayni_ve_hesap_dogru(self):
        sip, emir = self.yeni_us(miktar="3")
        o = uretim_emirleri_ozet([emir])[emir.pk]
        self.assertEqual((o["is_yuzde"], o["yuzde"]), (D("0"), D("0")))
        operasyon_kaydi_onayla(istasyon_emri_kayit_ac(emir.istasyon_emirleri.get(), hedef="1", tarih=GUN))
        o = uretim_emirleri_ozet([emir])[emir.pk]
        self.assertEqual(round(o["is_yuzde"], 2), D("33.33"))                                                               # 1 / 3
        yk = yukle_servis.Yukleme(emir)
        self.assertEqual(us_detay.is_yuzdesi(yk.gruplar()), o["is_yuzde"])                                                 # detay = liste
        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertEqual(r.context["is_yuzde"], o["is_yuzde"])
        self.assertContains(r, "İş %: tamamlanan ÷ planlanan")
        liste = self.liste(durum="ACIK")
        self.assertContains(liste, "Mamul %")
        self.assertContains(liste, "İş %33")
        self.assertContains(liste, "ue-bar-is")

    def test_emri_olmayan_ve_iptal_us_is_yuzdesi_tire(self):
        sip, emir = self.yeni_us()
        uretim.uretim_emri_iptal(emir, self.yon)
        o = uretim_emirleri_ozet([UretimEmri.objects.get(pk=emir.pk)])[emir.pk]
        self.assertIsNone(o["is_yuzde"])
        self.assertContains(self.liste(durum="IPTAL"), "İş —")

    def test_sevk_bekliyor_turetilir_ve_suzgecler(self):
        sip, emir = self.yeni_us(miktar="3")
        sip2, uretimde = self.yeni_us(miktar="2")
        self.uret(emir)                                                                                                     # üretim bitti, sevk yok
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.ACIK)
        o = uretim_emirleri_ozet([emir, uretimde])
        self.assertEqual((o[emir.pk]["sevk_bekliyor"], o[uretimde.pk]["sevk_bekliyor"]), (True, False))
        self.assertEqual(set(self.sira()), {emir.pk, uretimde.pk})                                                          # varsayılan: Açık + Sevk bekliyor
        self.assertEqual(self.sira(durum="AKTIF"), self.sira())
        self.assertEqual(self.sira(durum="ACIK"), [uretimde.pk])                                                            # yalnız üretimde
        self.assertEqual(self.sira(durum="SEVK"), [emir.pk])
        self.assertEqual(self.sira(durum="KAPALI"), [])
        self.assertContains(self.liste(durum="SEVK"), "Sevk bekliyor")
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk])), "🚚 Sevk bekliyor")
        self.assertNotContains(self.client.get(reverse("core:uretim_emri_detay", args=[uretimde.pk])), "🚚 Sevk bekliyor")
        r, _ = self.fatura_kes(sip, miktar="3")                                                                             # sevk → ÜS kapanır → Tamamlandı
        emir.refresh_from_db()
        self.assertEqual(emir.durum, UretimEmri.Durum.KAPALI)
        self.assertEqual(self.sira(durum="SEVK"), [])
        self.assertEqual(self.sira(durum="KAPALI"), [emir.pk])
        self.assertEqual(set(self.sira(durum="TUMU")), {emir.pk, uretimde.pk})

    def test_suzgec_secenekleri_ve_bilinmeyen_deger(self):
        sip, emir = self.yeni_us()
        r = self.liste()
        for etiket in ("Açık + Sevk bekliyor", "Açık (üretimde)", "Sevk bekliyor", "Tamamlandı", "İptal", "Tümü"):
            self.assertContains(r, f">{etiket}</option>")
        self.assertEqual(r.context["durum"], "AKTIF")
        self.assertEqual(self.liste(durum="BOZUK").context["durum"], "AKTIF")
        self.assertEqual(self.liste(durum="").context["durum"], "TUMU")


class SiralamaTest(SiparisliTaban):
    def kur(self):
        _, self.a = self.yeni_us(termin=date(2026, 2, 1))
        _, self.b = self.yeni_us(termin=None, oncelik="ACIL")
        _, self.c = self.yeni_us(termin=date(2026, 1, 15))
        _, self.d = self.yeni_us(termin=None)

    def test_acik_listelerde_acil_termin_tarih_tamamlandi_iptal_yeni_tarih_once(self):
        self.kur()
        self.assertEqual(self.sira(), [self.b.pk, self.c.pk, self.a.pk, self.d.pk])                                         # Acil → termin (boş sonda) → ÜS no
        self.assertEqual(self.sira(durum="ACIK"), [self.b.pk, self.c.pk, self.a.pk, self.d.pk])
        self.assertEqual(self.sira(durum="TUMU"), [self.d.pk, self.c.pk, self.b.pk, self.a.pk])                            # Tümü: yeni tarih önce
        uretim.uretim_emri_iptal(self.a, self.yon)
        uretim.uretim_emri_iptal(self.c, self.yon)
        self.assertEqual(self.sira(durum="IPTAL"), [self.c.pk, self.a.pk])                                                  # İptal: yeni önce (termin sırası DEĞİL)

    def test_ayni_oncelik_ve_termin_icinde_tarih_ve_no(self):
        _, x = self.yeni_us(termin=date(2026, 2, 1))
        _, y = self.yeni_us(termin=date(2026, 2, 1))
        UretimEmri.objects.filter(pk=y.pk).update(tarih=date(2025, 12, 1))
        self.assertEqual(self.sira(), [y.pk, x.pk])                                                                         # aynı termin: eski ÜS tarihi önce

    def test_termin_etkin_eski_us_icin_kaynak_siparisten(self):
        _, x = self.yeni_us(termin=None)
        sip_y, y = self.yeni_us(termin=None)
        TeklifSiparis.objects.filter(pk=sip_y.pk).update(gecerlilik_teslim_tarihi=date(2026, 1, 5))
        self.assertEqual(self.sira(), [y.pk, x.pk])                                                                         # ÜS terminı boş → siparişin teslim tarihi sayılır

    def test_istasyon_emirleri_ayni_sirayi_kullanir(self):
        self.kur()
        r = self.client.get(reverse("core:istasyon_emirleri"))
        sira = []
        for s in r.context["satirlar"]:
            if s["ie"].uretim_emri_id not in sira:
                sira.append(s["ie"].uretim_emri_id)
        self.assertEqual(sira, [self.b.pk, self.c.pk, self.a.pk, self.d.pk])
        self.assertContains(r, "ACİL")                                                                                      # satırda Acil rozeti
        self.assertContains(r, "Sıralama: Acil üretim siparişi önce")                                                       # ipucunda

    def test_arama_ile_distinct_siralama_hata_vermez(self):
        self.kur()
        r = self.liste(ara="Sf1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["e"].pk for s in r.context["emirler"]], [self.b.pk, self.c.pk, self.a.pk, self.d.pk])


class AtolyeSiralamaTest(AtolyeTaban):
    def test_acil_us_atolyede_one_alinir(self):
        emir2 = uretim.uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": D("2")}], depo_id=self.depo.pk, tarih=date(2026, 1, 5))
        self.assertEqual([s["ie"].uretim_emri_id for s in uretim_siralama_atolye(self)], [emir2.pk, self.emir.pk])           # varsayılan: tarihi yakın önce
        UretimEmri.objects.filter(pk=self.emir.pk).update(oncelik="ACIL")
        satirlar = uretim_siralama_atolye(self)
        self.assertEqual([s["ie"].uretim_emri_id for s in satirlar], [self.emir.pk, emir2.pk])
        self.assertTrue(satirlar[0]["acil"] and not satirlar[1]["acil"])
        UretimEmri.objects.filter(pk=self.emir.pk).update(oncelik="NORMAL", termin=date(2026, 12, 1))
        UretimEmri.objects.filter(pk=emir2.pk).update(termin=date(2026, 12, 31))
        self.assertEqual([s["ie"].uretim_emri_id for s in uretim_siralama_atolye(self)], [self.emir.pk, emir2.pk])           # termin yakın olan önce (malzeme eşit)


def uretim_siralama_atolye(test):
    from core.services import atolye
    return atolye.acik_emirler(test.lazer)


class MetinVeEkranTest(SiparisliTaban):
    def test_baslik_ipucu_ve_dar_ekran_bos_satir(self):
        self.yeni_us()
        r = self.liste()
        self.assertContains(r, "Satış siparişi ve stok için üretimler")
        self.assertNotContains(r, "Satış siparişinden açılan üretimler")
        h = r.content.decode()
        self.assertIn("#ue-tablo tr[hidden], #ue-tablo tr.ue-bos-js:not(.gorunur) { display:none; }", h)                  # mobil display:block ezmesine karşı
        self.assertIn('<tr class="ue-bos-js"', h)                                                                           # varsayılan gorunur sınıfı YOK
        self.assertNotIn("ue-bos-js gorunur", h)

    def test_uzun_aciklamalar_ipucuna_donustu(self):
        h = self.client.get(reverse("core:istasyon_emirleri")).content.decode()
        self.assertIn('data-ipucu="İstasyon sekmeleri açık', h)
        self.assertNotIn('<p class="yardim">İstasyon sekmeleri', h)
        h = self.client.get(reverse("core:uretim_emri_ekle")).content.decode()
        self.assertIn('data-ipucu="Eldeki kullanılabilir stok', h)
        self.assertNotIn('<p class="yardim" style="margin-top:0">Eldeki', h)
        h = self.client.get(reverse("core:urun_agaci") + "?gorunum=ihtiyac").content.decode()
        self.assertIn('data-ipucu="Birden çok hedef ürün', h)
        self.assertNotIn("Birden çok hedef ürün ve miktar gir; bitmiş üründen geriye doğru TÜM operasyon zinciri\n", h)
        self.assertIn(".ipucu::after", h)                                                                                    # ortak stil base.html'de


class ManuelUsRevizeFormuTest(StoktanTaban):
    def test_revize_formu_termin_oncelik_ile_gelir(self):
        self.client.force_login(self.yon)
        emir = self.ac("10", stoktan={(self.mamul.pk, None): D("0")})
        termin_oncelik_guncelle(emir, termin=date(2026, 7, 1), oncelik="ACIL", kullanici=self.yon)
        r = self.client.get(reverse("core:uretim_emri_revize", args=[emir.pk]))
        self.assertContains(r, 'name="termin"')
        self.assertContains(r, 'value="2026-07-01"')
        self.assertContains(r, '<option value="ACIL" selected>')
        self.assertContains(r, 'data-ipucu="Kalem miktarını artırıp')                                                       # revize açıklaması ipucuna taşındı
