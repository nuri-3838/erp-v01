"""Çoklu irsaliye → TEK fatura (2026-10-20): aynı cariye ait onaylı, faturalanmamış birden çok alış irsaliyesi tek taslak faturaya çevrilir; her fatura satırı kendi irsaliye
kalemine BAĞLANIR (stok/varyant kilitli, miktar/fiyat serbest); maliyet bağlı kaleme göre dağılır (fatura miktarı irsaliyeden azsa stok irsaliye miktarı kalır, maliyet fatura
tutarı); fatura silinince tüm irsaliyeler serbest kalır; farklı cari/PB/faturalanmış/onaysız irsaliye açık hatayla reddedilir; tek irsaliye akışı AYNEN çalışır."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Cari, Fatura, FaturaSatir, Kategori, Stok, StokHareket, TeklifSiparis
from core.services import stok_ortalama as so
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_onayla, fatura_sil, irsaliye_miktar_farklari
from core.services.teklif_siparis import (TeklifSiparisHatasi, irsaliyeleri_faturaya_cevir, irsaliyeyi_faturaya_cevir, teklif_siparis_olustur,
                                           teklif_siparis_onayla)
from core.tests.test_stok_ortalama import OrtalamaTemel

D = datetime.date
Dc = Decimal


class CokluTaban(OrtalamaTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stok_c = Stok.objects.create(kod="SO2", ad="ÜRÜN C", kategori=cls.kat, kdv=cls.kdv, uretim_birimi=cls.adet, fatura_birimi=cls.adet)
        cls.stok_y = Stok.objects.create(kod="SO3", ad="YAN ETİKET", kategori=cls.kat, kdv=cls.kdv, uretim_birimi=cls.adet, fatura_birimi=cls.adet)
        cls.cari2 = Cari.objects.create(kod="CO2", unvan="BAŞKA TED", para_birimi="TRY", muhasebe_kodu="320.30.0001")

    def irsaliye(self, satirlar, tarih=D(2026, 6, 5), cari=None, onayli=True, **ek):
        i = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=(cari or self.cari).pk, tarih=tarih, satirlar=satirlar,
                                   depo_id=self.depo.pk, kullanici=self.u, **ek)
        if onayli:
            teklif_siparis_onayla(i, kullanici=self.u)
            i.refresh_from_db()
        return i

    def iki_irsaliye(self):
        """SAI-0013 benzeri: A 550 + C 550; ikinci teslimat: A 100 + yan etiket 1000 (siparişte olmayan)."""
        i1 = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "550", "birim_fiyat": "100"}, {"stok_id": self.stok_c.pk, "miktar": "550", "birim_fiyat": "100"}], tarih=D(2026, 6, 5))
        i2 = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "100"}, {"stok_id": self.stok_y.pk, "miktar": "1000", "birim_fiyat": "2"}], tarih=D(2026, 6, 10))
        return i1, i2

    def girdiler(self, fatura, degisim=None):
        """Kayıtlı satırlardan formun posta atacağı servis girdisi (bağ korunur); degisim: {sıra: {alan: değer}}."""
        degisim = degisim or {}
        cikti = []
        for n, s in enumerate(fatura.satirlar.filter(silindi=False).order_by("id")):
            g = {"stok_id": s.stok_id, "miktar": s.miktar, "birim_fiyat": s.birim_fiyat, "varyant": None, "irsaliye_kalem_id": s.irsaliye_kalem_id}
            g.update(degisim.get(n, {}))
            cikti.append(g)
        return cikti

    def duzenle(self, fatura, satirlar, tip=True):
        return fatura_guncelle(fatura, tip_id=self.tip.pk if tip else None, cari_id=self.cari.pk, tarih=fatura.tarih, satirlar=satirlar, depo_id=self.depo.pk,
                               fatura_no="KAR-276", kullanici=self.u)

    def cevir(self, *irsaliyeler):
        fatura, kaynaklar = irsaliyeleri_faturaya_cevir(list(irsaliyeler), kullanici=self.u)
        fatura.refresh_from_db()
        return fatura


class CevirmeTest(CokluTaban):
    def test_iki_irsaliye_tek_taslak_fatura_satirlar_kalemlere_bagli(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        self.assertEqual((fatura.durum, fatura.tip_id, fatura.cari_id, fatura.tarih, fatura.para_birimi, fatura.yon), ("TASLAK", None, self.cari.pk, D(2026, 6, 10), "TRY", "ALIS"))
        satirlar = list(fatura.satirlar.filter(silindi=False).order_by("id"))
        self.assertEqual([(s.stok_id, s.miktar, s.birim_fiyat) for s in satirlar],
                         [(self.stok.pk, Dc("550"), Dc("100")), (self.stok_c.pk, Dc("550"), Dc("100")), (self.stok.pk, Dc("100"), Dc("100")), (self.stok_y.pk, Dc("1000"), Dc("2"))])
        kalemler = [k for i in (i1, i2) for k in i.kalemler.filter(silindi=False).order_by("id")]
        self.assertEqual([s.irsaliye_kalem_id for s in satirlar], [k.pk for k in kalemler])
        for i in (i1, i2):
            i.refresh_from_db()
            self.assertEqual(i.fatura_id, fatura.pk)                                  # her irsaliye faturaya bağlı
        self.assertEqual(Fatura.objects.count(), 1)

    def test_tek_irsaliye_eski_akis_aynen_bagsiz(self):
        i1, _ = self.iki_irsaliye()
        fatura, kaynaklar = irsaliyeleri_faturaya_cevir([i1], kullanici=self.u)
        self.assertEqual([i.pk for i in kaynaklar], [i1.pk])
        self.assertTrue(all(s.irsaliye_kalem_id is None for s in fatura.satirlar.filter(silindi=False)))
        eski = irsaliyeyi_faturaya_cevir(self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}]), kullanici=self.u)
        self.assertTrue(all(s.irsaliye_kalem_id is None for s in eski.fatura.satirlar.filter(silindi=False)))

    def test_acik_hatalar(self):
        i1, i2 = self.iki_irsaliye()
        baska = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}], cari=self.cari2)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "farklı carilere ait"):
            irsaliyeleri_faturaya_cevir([i1, baska], kullanici=self.u)
        usd = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}])
        TeklifSiparis.objects.filter(pk=usd.pk).update(para_birimi="USD")
        usd.refresh_from_db()
        with self.assertRaisesMessage(TeklifSiparisHatasi, "para birimi farklı"):
            irsaliyeleri_faturaya_cevir([i1, usd], kullanici=self.u)
        taslak = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}], onayli=False)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "onaylı değil"):
            irsaliyeleri_faturaya_cevir([i1, taslak], kullanici=self.u)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "Aynı irsaliye birden çok"):
            irsaliyeleri_faturaya_cevir([i1, i1], kullanici=self.u)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "seçilmedi"):
            irsaliyeleri_faturaya_cevir([], kullanici=self.u)
        self.cevir(i1, i2)
        i1.refresh_from_db()
        i3 = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}])
        with self.assertRaisesMessage(TeklifSiparisHatasi, "zaten bir faturaya dönüştürülmüş"):
            irsaliyeleri_faturaya_cevir([i1, i3], kullanici=self.u)
        self.assertEqual(Fatura.objects.count(), 1)                                   # hatalarda yeni fatura/bağ oluşmadı
        i3.refresh_from_db()
        self.assertIsNone(i3.fatura_id)


class MaliyetStokTest(CokluTaban):
    def onayli_fatura(self, fatura_miktar_a="500"):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        self.duzenle(fatura, self.girdiler(fatura, {0: {"miktar": Dc(fatura_miktar_a)}, 1: {"miktar": Dc("500")}}))      # 550 teslim / 500 fatura
        fatura.refresh_from_db()
        fatura_onayla(fatura, kullanici=self.u)
        fatura.refresh_from_db()
        return i1, i2, fatura

    def hareket(self, kalem):
        return StokHareket.objects.get(teklif_siparis_kalem=kalem, silindi=False)

    def test_fatura_miktari_az_stok_irsaliyedeki_gibi_maliyet_fatura_tutari(self):
        i1, i2, fatura = self.onayli_fatura()
        k = {x.stok_id: x for x in i1.kalemler.filter(silindi=False)}
        ha, hc = self.hareket(k[self.stok.pk]), self.hareket(k[self.stok_c.pk])
        self.assertEqual((ha.miktar, hc.miktar), (Dc("550"), Dc("550")))                                  # stok irsaliyedeki gibi
        self.assertEqual((ha.giris_tutar_try, hc.giris_tutar_try), (Dc("50000.00"), Dc("50000.00")))     # 500 × 100 (bedelsiz fazla teslim)
        self.assertEqual((ha.maliyet_durumu, ha.maliyet_fatura_satir.fatura_id), (so.KESIN, fatura.pk))
        k2 = {x.stok_id: x for x in i2.kalemler.filter(silindi=False)}
        self.assertEqual(self.hareket(k2[self.stok.pk]).giris_tutar_try, Dc("10000.00"))                  # AYNI stok, KENDİ satırından (stok bazında dağıtılsaydı 60.000 bölüşülürdü)
        self.assertEqual(self.hareket(k2[self.stok_y.pk]).giris_tutar_try, Dc("2000.00"))
        self.stok.refresh_from_db()
        self.assertEqual(self.stok.maliyet_miktar, Dc("650.000"))
        self.assertEqual(StokHareket.objects.filter(silindi=False).count(), 4)                            # fatura stok hareketi YAZMADI (çifte giriş yok)
        self.kontrol_yeniden_hesap_esit(self.stok)

    def test_fis_alis_faturasi_mantigiyla_ayni(self):
        _, _, fatura = self.onayli_fatura()
        matrah = Dc("50000") * 2 + Dc("10000") + Dc("2000")
        borc, alacak = {}, {}
        for s in fatura.fis.satirlar.filter(silindi=False).select_related("hesap"):
            borc[s.hesap.hesap_kodu] = borc.get(s.hesap.hesap_kodu, Dc("0")) + s.borc
            alacak[s.hesap.hesap_kodu] = alacak.get(s.hesap.hesap_kodu, Dc("0")) + s.alacak
        self.assertEqual(borc["153.20"], matrah)
        self.assertEqual(borc["191"], (matrah * Dc("0.20")).quantize(Dc("0.01")))
        self.assertEqual(alacak["320.30.0001"], (matrah * Dc("1.20")).quantize(Dc("0.01")))
        self.assertEqual(sum(s.borc for s in fatura.fis.satirlar.filter(silindi=False)), sum(s.alacak for s in fatura.fis.satirlar.filter(silindi=False)))

    def test_miktar_farki_uyarisi_satir_bazinda(self):
        i1, i2, fatura = self.onayli_fatura()
        farklar = irsaliye_miktar_farklari(fatura)
        self.assertEqual(sorted((s.kod, ir, fa) for s, ir, fa in farklar), [("SO1", Dc("550"), Dc("500")), ("SO2", Dc("550"), Dc("500"))])       # 2. irsaliyedeki A (100/100) farksız

    def test_silinince_tum_irsaliyeler_serbest_maliyet_geri_gider(self):
        i1, i2, fatura = self.onayli_fatura()
        fatura_sil(fatura, kullanici=self.u)
        for i in (i1, i2):
            i.refresh_from_db()
            self.assertIsNone(i.fatura_id)
        for h in StokHareket.objects.filter(silindi=False):
            self.assertEqual((h.giris_tutar_try, h.maliyet_fatura_satir_id), (None, None))               # fiyat geri gitti (fatura bekliyor)
        self.assertEqual(self.irsaliye_serbest_listesi(), 2)
        yeni = self.cevir(i1, i2)                                                       # yeniden çevrilebilir
        self.assertEqual(yeni.satirlar.filter(silindi=False).count(), 4)

    def irsaliye_serbest_listesi(self):
        return TeklifSiparis.objects.filter(belge_tur="IRSALIYE", silindi=False, durum="ONAYLI", fatura__isnull=True).count()

    def test_onayli_fatura_duzenlenince_yeniden_fiyatlanir_ve_bag_korunur(self):
        i1, i2, fatura = self.onayli_fatura()
        self.duzenle(fatura, self.girdiler(fatura, {0: {"birim_fiyat": Dc("110")}}))
        fatura.refresh_from_db()
        k = {x.stok_id: x for x in i1.kalemler.filter(silindi=False)}
        self.assertEqual(self.hareket(k[self.stok.pk]).giris_tutar_try, Dc("55000.00"))                   # 500 × 110
        self.assertEqual(fatura.satirlar.filter(silindi=False, irsaliye_kalem__isnull=False).count(), 4)  # bağlar düzenlemede korundu
        self.kontrol_yeniden_hesap_esit(self.stok)


class KilitTest(CokluTaban):
    def test_stok_varyant_kilitli_miktar_fiyat_serbest(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        with self.assertRaisesMessage(FaturaHatasi, "stok değiştirilemez"):
            self.duzenle(fatura, self.girdiler(fatura, {0: {"stok_id": self.stok_y.pk}}))
        self.duzenle(fatura, self.girdiler(fatura, {0: {"miktar": Dc("123"), "birim_fiyat": Dc("77")}}))          # serbest
        s = fatura.satirlar.filter(silindi=False).order_by("id").first()
        self.assertEqual((s.miktar, s.birim_fiyat, s.irsaliye_kalem_id is not None), (Dc("123"), Dc("77"), True))

    def test_gecersiz_bag_reddedilir(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        baska = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}])
        yabanci = baska.kalemler.first()
        with self.assertRaisesMessage(FaturaHatasi, "irsaliye bağı geçersiz"):                          # bu faturanın irsaliyesine ait olmayan kalem
            self.duzenle(fatura, self.girdiler(fatura, {0: {"irsaliye_kalem_id": yabanci.pk}}))
        ilk = fatura.satirlar.filter(silindi=False).order_by("id").first().irsaliye_kalem_id
        g = self.girdiler(fatura)
        g.append({"stok_id": self.stok.pk, "miktar": Dc("1"), "birim_fiyat": Dc("1"), "irsaliye_kalem_id": ilk})
        with self.assertRaisesMessage(FaturaHatasi, "birden çok satır bağlanamaz"):                      # aynı kalem iki satır
            self.duzenle(fatura, g)
        yeni = [{"stok_id": self.stok.pk, "miktar": Dc("1"), "birim_fiyat": Dc("1"), "irsaliye_kalem_id": ilk}]
        with self.assertRaisesMessage(FaturaHatasi, "irsaliye bağı geçersiz"):                          # yeni (irsaliyesiz) faturaya bağ kurulamaz
            from core.services.fatura import fatura_taslak_olustur
            fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 6, 10), satirlar=yeni, yon="ALIS", kullanici=self.u)

    def test_bagsiz_satir_eklenebilir_ve_satir_silinebilir(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        g = self.girdiler(fatura)[:3]                                                                   # yan etiket satırı silindi
        g.append({"stok_id": self.stok_c.pk, "miktar": Dc("3"), "birim_fiyat": Dc("9"), "irsaliye_kalem_id": None})      # elle eklenen bağsız satır
        self.duzenle(fatura, g)
        self.assertEqual(fatura.satirlar.filter(silindi=False).count(), 4)
        self.assertEqual(irsaliye_miktar_farklari(fatura)[0][0].kod, "SO3")                              # silinen satırın irsaliye miktarı uyarıda (1000 / 0)


class EkranTest(CokluTaban):
    def giris(self):
        self.client.force_login(self.u)

    def test_liste_onayli_faturalanmamislara_onay_kutusu_ve_toplu_dugme(self):
        i1, i2 = self.iki_irsaliye()
        taslak = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}], onayli=False)
        self.giris()
        r = self.client.get(reverse("core:satinalma_irsaliyeleri"))
        self.assertContains(r, "Seçilenleri Faturaya Çevir")
        html = r.content.decode()
        self.assertEqual(html.count('class="coklu-sec"'), 2)                                              # yalnız onaylı + faturalanmamış 2 irsaliye
        self.assertIn(f'value="{i1.pk}"', html)
        self.assertNotIn(f'name="irsaliye" value="{taslak.pk}"', html)
        self.cevir(i1)
        self.assertEqual(self.client.get(reverse("core:satinalma_irsaliyeleri")).content.decode().count('class="coklu-sec"'), 1)

    def test_toplu_cevirme_ve_acik_hata_mesajlari(self):
        i1, i2 = self.iki_irsaliye()
        baska = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "5", "birim_fiyat": "100"}], cari=self.cari2)
        self.giris()
        url = reverse("core:irsaliyeleri_faturaya_cevir")
        r = self.client.post(url, {"irsaliye": [i1.pk, baska.pk]}, follow=True)
        self.assertContains(r, "farklı carilere ait")
        self.assertEqual(Fatura.objects.count(), 0)
        r = self.client.post(url, {}, follow=True)
        self.assertContains(r, "listeden seçin")
        r = self.client.post(url, {"irsaliye": [i1.pk, i2.pk]})
        fatura = Fatura.objects.get()
        self.assertRedirects(r, reverse("core:fatura_detay", args=[fatura.pk]))
        self.assertEqual(self.client.get(url).status_code, 302)                                          # GET: listeye döner

    def test_detayda_satir_basina_kaynak_irsaliye_ve_duzenle_formunda_kilit(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        self.giris()
        r = self.client.get(reverse("core:fatura_detay", args=[fatura.pk]))
        self.assertContains(r, i1.belge_no)
        self.assertContains(r, i2.belge_no)
        self.assertContains(r, "irsaliye miktarı")
        r = self.client.get(reverse("core:fatura_duzenle", args=[fatura.pk]))
        formlar = r.context["formset"].forms
        self.assertEqual([f.initial["irsaliye_kalem"] for f in formlar], [s.irsaliye_kalem_id for s in fatura.satirlar.filter(silindi=False).order_by("id")])
        self.assertEqual(r.context["irsaliye_kalem_etiket"][str(formlar[0].initial["irsaliye_kalem"])], i1.belge_no)
        self.assertContains(r, 'class="irsaliye-etiket"')
        self.assertContains(r, "function irsaliyeKilitle")

    def test_duzenle_formu_kaydedince_bag_ve_maliyet_korunur(self):
        i1, i2 = self.iki_irsaliye()
        fatura = self.cevir(i1, i2)
        self.giris()
        satirlar = list(fatura.satirlar.filter(silindi=False).order_by("id"))
        v = {"tip": self.tip.pk, "cari": self.cari.pk, "tarih": "2026-06-10", "para_birimi": "TRY", "fatura_no": "KAR-276", "depo": self.depo.pk,
             "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for n, s in enumerate(satirlar):
            v.update({f"form-{n}-stok": s.stok_id, f"form-{n}-miktar": "500" if n == 0 else s.miktar, f"form-{n}-birim_fiyat": s.birim_fiyat,
                      f"form-{n}-kdv": s.kdv_id, f"form-{n}-irsaliye_kalem": s.irsaliye_kalem_id})
        r = self.client.post(reverse("core:fatura_duzenle", args=[fatura.pk]), v)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        fatura.refresh_from_db()
        yeni = list(fatura.satirlar.filter(silindi=False).order_by("id"))
        self.assertEqual([s.irsaliye_kalem_id for s in yeni], [s.irsaliye_kalem_id for s in satirlar])
        self.assertEqual(yeni[0].miktar, Dc("500"))
        self.assertEqual(fatura.durum, "TASLAK")                                                          # taslak düzenleme: fiş/maliyet onayda
        fatura_onayla(fatura, kullanici=self.u)
        h = StokHareket.objects.get(teklif_siparis_kalem=yeni[0].irsaliye_kalem, silindi=False)
        self.assertEqual((h.miktar, h.giris_tutar_try), (Dc("550"), Dc("50000.00")))
        v["form-0-stok"] = self.stok_y.pk                                                                # bağlı satırda stok oynanırsa sunucu reddeder
        r = self.client.post(reverse("core:fatura_duzenle", args=[fatura.pk]), v)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "stok değiştirilemez")
