"""BELGE BAZLI KG + ADET (2026-11-20, ERP2026000005162 / fatura #321 vakası): naylon kartları (stok birimi AD, fatura birimi KG, kartta SABİT çevirici) her teslimatta farklı
KG/AD oranıyla gelir; sabit çevirici stoğa yanlış adet yazıyordu. Satırda iki miktar birlikte girilir: fatura miktarı (KG) ve stoğa giren miktar (AD) — tutar/KDV KG × fiyat,
stok hareketi AD ile, satırın gerçek oranı (KG/AD) saklanır ve gösterilir. AD boşsa eski davranış (kart çeviricisi). Kapsam: doğrudan stoklu alış faturası (ekle/düzenle),
alış irsaliyesi → fatura (KG + AD devralınır, maliyet AD başına), gösterim."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Birim, Fatura, FaturaSatir, Stok, StokHareket
from core.services import birim_cevrim
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur, fatura_onayla
from core.services.hareket import eldeki_miktar
from core.services.teklif_siparis import irsaliyeleri_faturaya_cevir, irsaliyeyi_faturaya_cevir, teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_birim_cevrim import CevrimTemel

D = datetime.date
Dc = Decimal
TARIH = D(2026, 10, 8)


class NaylonTemel(CevrimTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.naylon = Stok.objects.create(kod="150-60-0002", ad="NAYLON 4 BASAMAK (3+1)", kategori=cls.alt, uretim_birimi=cls.adet_b, fatura_birimi=cls.kg,
                                         cevirici=Dc("0.1027"), kdv=cls.kdv)

    def _satir(self, kg="123.4", ad="1070", fiyat="120.9461", stok=None):
        s = {"stok_id": (stok or self.naylon).pk, "miktar": kg, "birim_fiyat": fiyat}
        if ad is not None:
            s["stok_miktar"] = ad
        return s

    def _fatura(self, satirlar=None, no="NY-1", **kw):
        return fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=TARIH, fatura_no=no, depo_id=self.depo.pk,
                              satirlar=satirlar or [self._satir()], kullanici=self.su, **kw)

    def _guncelle(self, fatura, satirlar):
        return fatura_guncelle(fatura, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=TARIH, satirlar=satirlar, fatura_no=fatura.fatura_no,
                               depo_id=self.depo.pk, kullanici=self.su)

    def _hareketler(self, stok=None):
        return list(StokHareket.objects.filter(stok=stok or self.naylon, silindi=False).order_by("id"))


class YardimciTest(NaylonTemel):
    def test_oran_ve_metin(self):
        self.assertEqual(birim_cevrim.oran(Dc("123.4"), Dc("1070")).quantize(Dc("0.00001")), Dc("0.11533"))
        self.assertIsNone(birim_cevrim.oran(Dc("1"), None))
        self.assertEqual(birim_cevrim.satir_metni(self.naylon, Dc("123.4"), Dc("1070")), "123,4 KG = 1.070 AD (oran 0,11533)")
        self.assertEqual(birim_cevrim.satir_metni(self.naylon, Dc("203.2"), Dc("1500")), "203,2 KG = 1.500 AD (oran 0,13547)")
        self.assertIn("kart çeviricisi 0,1027", birim_cevrim.satir_metni(self.naylon, Dc("100")))                    # AD yok → kart çeviricisi

    def test_farkli_birim_mi(self):
        self.assertTrue(birim_cevrim.farkli_birim_mi(self.naylon))
        self.assertFalse(birim_cevrim.farkli_birim_mi(self.stok))                                                       # AD / AD, çevirici 1


class DogrudanStokluFaturaTest(NaylonTemel):
    def test_kg_ve_ad_birlikte_stok_ad_ile_tutar_kg_ile(self):
        f = self._fatura()
        (s,) = f.satirlar.filter(silindi=False)
        self.assertEqual((s.miktar, s.stok_miktar), (Dc("123.400"), Dc("1070.000")))                                    # satırda gerçek oran saklı
        self.assertEqual(s.tutar, Dc("14924.75"))                                                                       # tutar = KG × birim fiyat
        (h,) = self._hareketler()
        self.assertEqual((h.tur, h.miktar), ("GIRIS", Dc("1070")))                                                      # stok AD ile (çevirici ile 1201,558 DEĞİL)
        self.assertEqual(eldeki_miktar(self.naylon.pk, self.depo.pk), Dc("1070"))
        self.assertEqual(h.giris_tutar_try, Dc("14924.75"))                                                             # maliyet = satır tutarı
        self.assertEqual((h.giris_tutar_try / h.miktar).quantize(Dc("0.0001")), Dc("13.9484"))                          # AD başına = tutar ÷ AD
        self.assertEqual(f.fis.satirlar.filter(silindi=False, hesap_id="153.10").get().borc, Dc("14924.75"))            # fiş KG × fiyat üzerinden

    def test_ad_bos_eski_davranis_kart_ceviricisi(self):
        f = self._fatura([self._satir(ad=None)])
        (s,) = f.satirlar.filter(silindi=False)
        self.assertIsNone(s.stok_miktar)
        (h,) = self._hareketler()
        self.assertEqual(h.miktar, Dc("1201.558"))                                                                      # 123,4 ÷ 0,1027
        self.assertEqual(self._fatura([self._satir(ad="")], no="NY-2").satirlar.get().stok_miktar, None)               # boş metin de boş

    def test_dort_satirlik_gercek_vaka_oranlari(self):
        kartlar = []
        for ad, cev in (("3+1", "0.1027"), ("4+1", "0.13469"), ("5+1", "0.14778"), ("6+1", "0.13775")):
            kartlar.append(Stok.objects.create(kod=f"150-61-{len(kartlar):04d}", ad=f"NAYLON {ad}", kategori=self.alt, uretim_birimi=self.adet_b, fatura_birimi=self.kg,
                                               cevirici=Dc(cev), kdv=self.kdv))
        veri = [("123.4", "1070"), ("203.2", "1500"), ("107.65", "680"), ("112.9", "660")]
        self._fatura([self._satir(kg, ad, stok=k) for k, (kg, ad) in zip(kartlar, veri)], no="NY-4")
        for k, (kg, ad) in zip(kartlar, veri):
            (h,) = self._hareketler(k)
            self.assertEqual(h.miktar, Dc(ad))

    def test_ad_gecersiz_sifir_negatif_reddedilir(self):
        for kotu in ("0", "-5"):
            with self.assertRaises(FaturaHatasi):
                self._fatura([self._satir(ad=kotu)], no=f"NY-K{kotu}")
        self.assertEqual(Fatura.objects.count(), 0)

    def test_ayni_birimli_kartta_ad_yok_sayilir(self):
        f = self._fatura([{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "100", "stok_miktar": "5"}], no="AYNI-1")
        self.assertIsNone(f.satirlar.get().stok_miktar)
        (h,) = self._hareketler(self.stok)
        self.assertEqual(h.miktar, Dc("10"))

    def test_satis_faturasinda_reddedilir(self):
        self._fatura()                                                                                                  # satacak stok olsun
        with self.assertRaisesMessage(FaturaHatasi, "yalnız alış faturasında"):
            fatura_olustur(tip_id=self.satis.pk, cari_id=self.musteri.pk, tarih=TARIH, fatura_no="S-1", depo_id=self.depo.pk, kullanici=self.su,
                           satirlar=[self._satir(kg="10", ad="90", fiyat="200")])

    def test_taslak_ad_saklanir_onayda_stok_ad_ile(self):
        from core.services.fatura import fatura_taslak_olustur
        t = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=TARIH, tip_id=self.alis.pk, fatura_no="T-1", depo_id=self.depo.pk, kullanici=self.su,
                                  satirlar=[self._satir()])
        self.assertEqual(t.satirlar.get().stok_miktar, Dc("1070.000"))
        self.assertEqual(self._hareketler(), [])                                                                        # taslak stok yazmaz
        fatura_onayla(t, kullanici=self.su)
        (h,) = self._hareketler()
        self.assertEqual(h.miktar, Dc("1070"))


class FaturaDuzenleTest(NaylonTemel):
    def test_ad_degisince_stok_hareketi_yeniden_yazilir(self):
        f = self._fatura()
        (h0,) = self._hareketler()
        f = self._guncelle(f, [self._satir(ad="680")])
        (h1,) = self._hareketler()
        self.assertEqual(h1.miktar, Dc("680"))                                                                          # AD değişti → hareket güncel
        self.assertEqual(f.satirlar.filter(silindi=False).get().stok_miktar, Dc("680.000"))
        self.assertEqual(eldeki_miktar(self.naylon.pk, self.depo.pk), Dc("680"))
        self.assertEqual(f.satirlar.filter(silindi=False).get().tutar, Dc("14924.75"))                                  # KG/fiyat aynı → tutar/fiş aynı
        self.assertEqual(h1.giris_tutar_try, Dc("14924.75"))                                                            # maliyet AD başına yeni oran
        self.assertEqual(StokHareket.objects.filter(stok=self.naylon).exclude(silindi=True).count(), 1)

    def test_ad_eklenince_ve_kaldirilinca(self):
        f = self._fatura([self._satir(ad=None)])
        self.assertEqual(self._hareketler()[0].miktar, Dc("1201.558"))
        f = self._guncelle(f, [self._satir(ad="1070")])
        self.assertEqual([h.miktar for h in self._hareketler()], [Dc("1070")])
        f = self._guncelle(f, [self._satir(ad=None)])
        self.assertEqual([h.miktar for h in self._hareketler()], [Dc("1201.558")])                                      # AD silinince kart çeviricisine döner
        self.assertIsNone(f.satirlar.filter(silindi=False).get().stok_miktar)

    def test_hicbir_sey_degismediyse_hareket_yeniden_yazilmaz(self):
        f = self._fatura()
        (h0,) = self._hareketler()
        self._guncelle(f, [self._satir()])
        (h1,) = self._hareketler()
        self.assertEqual(h0.pk, h1.pk)                                                                                  # imza AYNI → yalnız başlık güncellenir

    def test_sadece_fiyat_degisince_hareket_yerinde_guncellenir(self):
        f = self._fatura()
        (h0,) = self._hareketler()
        self._guncelle(f, [self._satir(fiyat="130")])
        (h1,) = self._hareketler()
        self.assertEqual((h0.pk, h1.miktar, h1.giris_tutar_try), (h1.pk, Dc("1070"), Dc("16042.00")))                  # AD aynı: hareket yerinde, tutar yeni


class IrsaliyedenFaturaTest(NaylonTemel):
    def _irsaliye(self, kg="123.4", ad="1070", fiyat="120.9461", no=None):
        satir = {"stok_id": self.naylon.pk, "miktar": kg, "birim_fiyat": fiyat}
        if ad is not None:
            satir["uretim_miktar"] = ad
        ir = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=TARIH, depo_id=self.depo.pk, satirlar=[satir], kullanici=self.su)
        teklif_siparis_onayla(ir, kullanici=self.su)
        return ir

    def _onayla(self, fatura):
        fatura.tip = self.alis
        fatura.save(update_fields=["tip"])
        fatura_onayla(fatura, kullanici=self.su)
        fatura.refresh_from_db()

    def test_irsaliye_kg_ve_ad_faturaya_devralinir_stok_tekrar_hareket_etmez(self):
        ir = self._irsaliye()
        (h,) = self._hareketler()
        self.assertEqual(h.miktar, Dc("1070"))                                                                          # irsaliye AD ile girdi
        ir = irsaliyeyi_faturaya_cevir(ir, kullanici=self.su)
        (s,) = ir.fatura.satirlar.filter(silindi=False)
        self.assertEqual((s.miktar, s.stok_miktar), (Dc("123.400"), Dc("1070.000")))                                    # KG + AD devralındı
        self._onayla(ir.fatura)
        (h,) = self._hareketler()
        self.assertEqual(h.miktar, Dc("1070"))                                                                          # stok TEKRAR hareket etmedi
        self.assertEqual(eldeki_miktar(self.naylon.pk, self.depo.pk), Dc("1070"))
        self.assertEqual(h.giris_tutar_try, Dc("14924.75"))                                                             # maliyet = fatura satır tutarı
        self.assertEqual((h.giris_tutar_try / h.miktar).quantize(Dc("0.0001")), Dc("13.9484"))                          # AD başına = tutar ÷ AD

    def test_fatura_fiyati_degisince_maliyet_ad_basina_yeni_tutar(self):
        ir = irsaliyeyi_faturaya_cevir(self._irsaliye(), kullanici=self.su)
        s = ir.fatura.satirlar.get(silindi=False)
        FaturaSatir.objects.filter(pk=s.pk).update(birim_fiyat=Dc("125"))                                              # fatura gerçek fiyatı getirdi
        self._onayla(ir.fatura)
        (h,) = self._hareketler()
        self.assertEqual(h.giris_tutar_try, Dc("15425.00"))                                                             # 123,4 KG × 125
        self.assertEqual(h.miktar, Dc("1070"))

    def test_irsaliyede_ad_yoksa_faturada_da_yok_eski_davranis(self):
        ir = irsaliyeyi_faturaya_cevir(self._irsaliye(ad=None), kullanici=self.su)
        self.assertIsNone(ir.fatura.satirlar.get(silindi=False).stok_miktar)
        self._onayla(ir.fatura)
        self.assertEqual(self._hareketler()[0].miktar, Dc("1201.558"))

    def test_coklu_irsaliye_satirlari_kg_ad_tasir(self):
        i1, i2 = self._irsaliye(), self._irsaliye(kg="203.2", ad="1500")
        fatura, _ = irsaliyeleri_faturaya_cevir([i1, i2], kullanici=self.su)
        satirlar = list(fatura.satirlar.filter(silindi=False).order_by("id"))
        self.assertEqual([(s.miktar, s.stok_miktar) for s in satirlar], [(Dc("123.400"), Dc("1070.000")), (Dc("203.200"), Dc("1500.000"))])
        self._onayla(fatura)
        self.assertEqual(sorted(h.miktar for h in self._hareketler()), [Dc("1070"), Dc("1500")])                        # yalnız iki irsaliye girişi

    def test_irsaliyeli_faturada_ad_duzenlenirken_stok_degismez(self):
        ir = irsaliyeyi_faturaya_cevir(self._irsaliye(), kullanici=self.su)
        self._onayla(ir.fatura)
        s = ir.fatura.satirlar.get(silindi=False)
        self._guncelle(ir.fatura, [self._satir(ad="1070", fiyat="130")])
        (h,) = self._hareketler()
        self.assertEqual(h.miktar, Dc("1070"))                                                                          # irsaliye hareketi korunur
        self.assertEqual(h.giris_tutar_try, Dc("16042.00"))
        self.assertEqual(StokHareket.objects.filter(stok=self.naylon, silindi=False).count(), 1)


class EkranTest(NaylonTemel):
    def _post_veri(self, ad="1070", no="E-1"):
        return {"tip": self.alis.pk, "cari": self.tedarikci.pk, "tarih": "2026-10-08", "fatura_no": no, "para_birimi": "TRY", "depo": self.depo.pk,
                "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
                "form-0-tur": "STOK", "form-0-stok": self.naylon.pk, "form-0-miktar": "123,4", "form-0-stok_miktar": ad, "form-0-birim_fiyat": "120,9461"}

    def test_fatura_formu_kayit_ve_gosterim(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._post_veri(), follow=True)
        self.assertEqual(r.status_code, 200)
        f = Fatura.objects.get(fatura_no="E-1")
        self.assertEqual(f.satirlar.get().stok_miktar, Dc("1070.000"))
        self.assertEqual(self._hareketler()[0].miktar, Dc("1070"))
        self.assertContains(r, "123,4 KG = 1.070 AD (oran 0,11533)")                                                    # fatura detayı
        r = self.client.get(reverse("core:stok_detay", args=[self.naylon.pk]))
        self.assertContains(r, "123,4 KG = 1.070 AD (oran 0,11533)")                                                    # stok kartı hareket listesi

    def test_fatura_formu_ad_bos_eski_davranis(self):
        self.client.force_login(self.su)
        self.client.post(reverse("core:alis_fatura_ekle"), self._post_veri(ad="", no="E-2"))
        self.assertEqual(self._hareketler()[0].miktar, Dc("1201.558"))

    def test_duzenle_ekrani_ad_degerini_getirir_ve_kaydeder(self):
        f = self._fatura()
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertContains(r, 'name="form-0-stok_miktar"')
        self.assertContains(r, 'value="1.070,000"')
        self.assertContains(r, "stok-miktar-alani")
        meta = r.context["stok_cevrim"]
        self.assertIn(str(self.naylon.pk), meta)                                                                         # birimleri farklı kart: alan açılır
        veri = self._post_veri(ad="680", no=f.fatura_no)
        veri.update({"form-INITIAL_FORMS": "1"})
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), veri)
        self.assertEqual(r.status_code, 302)
        self.assertEqual([h.miktar for h in self._hareketler()], [Dc("680")])

    def test_irsaliyeli_fatura_duzenle_alani_kilitli_isaretlenir(self):
        ir = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=TARIH, depo_id=self.depo.pk, kullanici=self.su,
                                    satirlar=[{"stok_id": self.naylon.pk, "miktar": "123.4", "birim_fiyat": "120.9461", "uretim_miktar": "1070"}])
        teklif_siparis_onayla(ir, kullanici=self.su)
        ir = irsaliyeyi_faturaya_cevir(ir, kullanici=self.su)
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[ir.fatura.pk]))
        self.assertTrue(r.context["stok_miktar_kilitli"])
        r = self.client.get(reverse("core:fatura_duzenle", args=[self._fatura(no="DOGRUDAN").pk]))
        self.assertFalse(r.context["stok_miktar_kilitli"])

    def test_irsaliye_detay_ve_form_js(self):
        ir = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=TARIH, depo_id=self.depo.pk, kullanici=self.su,
                                    satirlar=[{"stok_id": self.naylon.pk, "miktar": "123.4", "birim_fiyat": "120.9461", "uretim_miktar": "1070"}])
        self.client.force_login(self.su)
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[ir.pk])), "123,4 KG = 1.070 AD (oran 0,11533)")
        html = self.client.get(reverse("core:satinalma_irsaliye_ekle")).content.decode()
        self.assertIn("mi.dataset.otomatik", html)                                                                       # KG elle girildiyse AD yazınca ÜZERİNE YAZILMAZ (JS kuralı)
        self.assertIn("(oran '", html)                                                                                   # iki miktar birlikte: canlı oran notu
