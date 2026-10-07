"""Duran varlık BÖLME (kısmi satış): eşit/eşit olmayan bölme, kuruş ve amortisman dağıtımı, muhasebe fişi/hesap bakiyesi değişmez, satışta yalnız yeni kart kapanır,
bölünmüş kart satılamaz/düzenlenemez/silinemez, geri alma (satış varsa engel), ekranlar."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import DuranVarlik, YevmiyeFisi, YevmiyeSatir
from core.services import duran_varlik as dv_servis
from core.services import fatura as fs
from core.services import raporlar
from core.services.yevmiye import SatirGirdi, fis_olustur
from core.tests.test_satis_hesap_demirbas import SatisHesapDemirbasTestBase

D = datetime.date
Dc = Decimal


def _bak(kod):
    return raporlar._devir(kod, D(2100, 1, 1))[0]


class BolmeBase(SatisHesapDemirbasTestBase):
    def kart(self, maliyet="460000", amort="0", ad="50W FİBER LAZER MARKALAMA MAKİNESİ (2 ADET)"):
        """253 hesabında kart + defterde aynı tutarda bakiye (alış)."""
        fis_olustur(tarih=D(2026, 3, 10), aciklama="ALIŞ", kullanici=self.su, satirlar=[
            SatirGirdi(hesap_kodu="253", taraf="B", islem_tutari=maliyet), SatirGirdi(hesap_kodu="320.01", taraf="A", islem_tutari=maliyet)])
        v = dv_servis.duran_varlik_olustur(ad=ad, hesap_id="253", aktiflestirme_tarihi=D(2026, 3, 10), maliyet=Dc(maliyet), kullanici=self.su)
        if Dc(amort):
            DuranVarlik.objects.filter(pk=v.pk).update(birikmis_amortisman=Dc(amort))
            v.refresh_from_db()
        return v

    def bol_esit(self, v, adet=2):
        return dv_servis.bol(v, dv_servis.bolme_onerisi(v, adet), kullanici=self.su)


class BolmeServisTest(BolmeBase):
    def test_esit_bolme_dv0011_ornegi(self):
        v = self.kart("460000")
        n_fis, bakiye = YevmiyeFisi.objects.count(), _bak("253")
        yeniler = self.bol_esit(v)
        self.assertEqual([y.maliyet for y in yeniler], [Dc("230000.00"), Dc("230000.00")])
        v.refresh_from_db()
        self.assertEqual((v.durum, v.silindi, v.maliyet), ("BOLUNDU", False, Dc("460000.00")))           # geçmiş olarak durur
        for y in yeniler:
            self.assertEqual((y.hesap_id, y.durum, y.bolunen_kart_id, y.aktiflestirme_tarihi, y.kaynak), ("253", "AKTIF", v.pk, v.aktiflestirme_tarihi, v.kaynak))
            self.assertIn(v.demirbas_kodu, y.notlar)
        self.assertEqual([y.ad for y in yeniler], [f"{v.ad} - 1/2", f"{v.ad} - 2/2"])
        self.assertEqual((YevmiyeFisi.objects.count(), _bak("253")), (n_fis, bakiye))                     # fiş yok, hesap bakiyesi aynı

    def test_kurus_dagitimi_son_karta(self):
        v = self.kart("13331.67", ad="UPS (2 ADET)")
        self.assertEqual([y.maliyet for y in self.bol_esit(v)], [Dc("6665.83"), Dc("6665.84")])
        v2 = self.kart("100", ad="X")
        self.assertEqual([y.maliyet for y in self.bol_esit(v2, 3)], [Dc("33.33"), Dc("33.33"), Dc("33.34")])
        self.assertEqual(sum(o["maliyet"] for o in dv_servis.bolme_onerisi(v2, 7)), Dc("100.00"))

    def test_esit_olmayan_bolme_ve_toplam_kontrolu(self):
        v = self.kart("460000")
        with self.assertRaises(dv_servis.DuranVarlikHatasi) as cm:
            dv_servis.bol(v, [{"ad": "A", "maliyet": "300.000,00"}, {"ad": "B", "maliyet": "159.999,99"}], kullanici=self.su)
        self.assertIn("eşit olmalı", str(cm.exception))
        v.refresh_from_db()
        self.assertEqual((v.durum, DuranVarlik.objects.count()), ("AKTIF", 1))
        yeniler = dv_servis.bol(v, [{"ad": "A", "maliyet": "300.000,00"}, {"ad": "B", "maliyet": "160.000,00"}], kullanici=self.su)
        self.assertEqual([(y.ad, y.maliyet) for y in yeniler], [("A", Dc("300000.00")), ("B", Dc("160000.00"))])

    def test_gecersiz_parcalar(self):
        v = self.kart("1000")
        for parcalar in ([{"ad": "A", "maliyet": "1000"}], [{"ad": "", "maliyet": "500"}, {"ad": "B", "maliyet": "500"}],
                         [{"ad": "A", "maliyet": "0"}, {"ad": "B", "maliyet": "1000"}], [{"ad": "A", "maliyet": "x"}, {"ad": "B", "maliyet": "1"}]):
            with self.assertRaises(dv_servis.DuranVarlikHatasi):
                dv_servis.bol(v, parcalar, kullanici=self.su)
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.bolme_onerisi(v, 1)
        self.assertEqual(DuranVarlik.objects.count(), 1)

    def test_amortismanli_kart_toplamlar_birebir(self):
        v = self.kart("460000", amort="46000.01")
        yeniler = self.bol_esit(v)
        self.assertEqual([y.birikmis_amortisman for y in yeniler], [Dc("23000.00"), Dc("23000.01")])
        self.assertEqual(sum(y.birikmis_amortisman for y in yeniler), Dc("46000.01"))
        v2 = self.kart("460000", amort="100000")
        y2 = dv_servis.bol(v2, [{"ad": "A", "maliyet": "300000"}, {"ad": "B", "maliyet": "160000"}], kullanici=self.su)
        self.assertEqual([x.birikmis_amortisman for x in y2], [Dc("65217.39"), Dc("34782.61")])         # maliyetle orantılı
        self.assertEqual(sum(x.birikmis_amortisman for x in y2), Dc("100000.00"))
        self.assertTrue(all(x.birikmis_amortisman <= x.maliyet for x in y2))

    def test_kontrol_raporu_kart_toplami_mizanla_ayni(self):
        v = self.kart("460000")
        self.bol_esit(v)
        satir = next(r for r in dv_servis.kontrol_raporu() if r["hesap"].hesap_kodu == "253")
        self.assertEqual((satir["kart_toplami"], satir["mizan_bakiye"], satir["fark"]), (Dc("460000.00"), Dc("460000.00"), Dc("0.00")))

    def test_bolunemeyen_kartlar(self):
        v = self.kart("1000")
        self.bol_esit(v)
        v.refresh_from_db()
        with self.assertRaises(dv_servis.DuranVarlikHatasi):                                           # bölünmüş kart tekrar bölünmez
            dv_servis.bol(v, dv_servis.bolme_onerisi(v, 2), kullanici=self.su)
        p = self.kart("500")
        dv_servis.durum_degistir(p, durum="PASIF", kullanici=self.su)
        self.assertFalse(dv_servis.bolunebilir_mi(p))

    def test_bolunmus_kart_duzenlenemez_silinemez_durumu_degismez(self):
        v = self.kart("1000")
        yeniler = self.bol_esit(v)
        v.refresh_from_db()
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.duran_varlik_guncelle(v, ad="X", maliyet=Dc("5"), kullanici=self.su)
        for kart in (v, yeniler[0]):
            self.assertFalse(dv_servis.silinebilir_mi(kart))
            with self.assertRaises(dv_servis.DuranVarlikHatasi):
                dv_servis.varlik_sil(kart, kullanici=self.su)
        for durum in ("PASIF", "AKTIF"):
            with self.assertRaises(dv_servis.DuranVarlikHatasi):
                dv_servis.durum_degistir(v, durum=durum, kullanici=self.su)
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.satir_bagla(v, 1, kullanici=self.su)

    def test_ayni_hesapta_birden_cok_kart_otomatik_eslestirme_yapmaz(self):
        v = self.kart("1000")
        yeniler = self.bol_esit(v)
        self.assertIsNone(dv_servis.hesaptaki_kart("253"))                                            # belirsiz
        dv_servis.kart_maliyetini_yenile("253", kullanici=self.su)
        for y in yeniler:
            y.refresh_from_db()
        self.assertEqual([y.maliyet for y in yeniler], [Dc("500.00"), Dc("500.00")])                  # maliyet tüm bakiyeye eşitlenmedi


class SatisVeGeriAlmaTest(BolmeBase):
    def test_satista_yalniz_yeni_kart_kapanir_bolunmus_secilemez(self):
        v = self.kart("460000", amort="46000")
        y1, y2 = self.bol_esit(v)                                                                      # 230.000 maliyet / 23.000 amortisman
        with self.assertRaises(fs.FaturaHatasi):                                                      # orijinal (Bölündü) satılamaz
            self._fatura([self._dv_satir(v, "300000")])
        f = self._fatura([self._dv_satir(y1, "300000")])
        s = self._fis(f)
        self.assertEqual(s[("253", "A")][0], Dc("230000.00"))                                           # yalnız yeni kartın maliyeti kapanır
        self.assertEqual(s[("257", "B")][0], Dc("23000.00"))
        self.assertEqual(s[("649", "A")][0], Dc("93000.00"))                                            # 300.000 − (230.000 − 23.000)
        y1.refresh_from_db()
        y2.refresh_from_db()
        v.refresh_from_db()
        self.assertEqual((y1.durum, y2.durum, v.durum), ("SATILDI", "AKTIF", "BOLUNDU"))
        self.assertEqual(_bak("253"), Dc("230000.00"))                                                  # kalan bakiye = kalan aktif kart
        satir = next(r for r in dv_servis.kontrol_raporu() if r["hesap"].hesap_kodu == "253")
        self.assertEqual((satir["kart_toplami"], satir["fark"]), (Dc("230000.00"), Dc("0.00")))

    def test_satis_form_listesi_yalniz_aktif_kartlar(self):
        from core.forms import SatirForm
        v = self.kart("1000")
        y1, y2 = self.bol_esit(v)
        f = SatirForm(tur_hazir=False) if False else None
        qs = DuranVarlik.objects.filter(durum=DuranVarlik.Durum.AKTIF, silindi=False)
        self.assertEqual(sorted(x.pk for x in qs), sorted([y1.pk, y2.pk]))
        self.assertNotIn(v.pk, [x.pk for x in qs])

    def test_demirbas_satisi_onizleme_maliyet_kapanisi_canli_ornekler(self):
        from core.services.fatura import _demirbas_satirlari
        a = self.kart("460000")
        b = self.kart("13331.67", ad="UPS (2 ADET)")
        ya = self.bol_esit(a)[0]
        yb = self.bol_esit(b)[0]
        for kart, maliyet in ((ya, Dc("230000.00")), (yb, Dc("6665.83"))):
            satirlar, borc, alacak = _demirbas_satirlari(kart, Dc("100000"))
            self.assertEqual([(x.hesap_kodu, x.taraf, x.islem_tutari) for x in satirlar if x.taraf == "A" and x.hesap_kodu == "253"], [("253", "A", maliyet)])

    def test_geri_alma_satilmamis(self):
        v = self.kart("460000", amort="1000")
        yeniler = self.bol_esit(v)
        n_fis, bakiye = YevmiyeFisi.objects.count(), _bak("253")
        self.assertEqual(dv_servis.bolme_geri_alinabilir_mi(v), "")
        dv_servis.bolmeyi_geri_al(v, kullanici=self.su)
        v.refresh_from_db()
        self.assertEqual((v.durum, v.maliyet, v.birikmis_amortisman), ("AKTIF", Dc("460000.00"), Dc("1000.00")))
        for y in yeniler:
            y.refresh_from_db()
            self.assertTrue(y.silindi)
        self.assertEqual(list(dv_servis.yeni_kartlar(v)), [])
        self.assertEqual((YevmiyeFisi.objects.count(), _bak("253")), (n_fis, bakiye))
        self.assertTrue(dv_servis.bolunebilir_mi(v))                                                     # tekrar bölünebilir
        self.bol_esit(v)

    def test_satilmis_veya_degismis_kartta_geri_alma_engellenir(self):
        v = self.kart("1000")
        y1, y2 = self.bol_esit(v)
        f = self._fatura([self._dv_satir(y1, "800")])
        self.assertIn("satıldı", dv_servis.bolme_geri_alinabilir_mi(v).lower())
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.bolmeyi_geri_al(v, kullanici=self.su)
        fs.fatura_sil(f, kullanici=self.su)                                                              # satış faturası silinince kart aktife döner
        self.assertEqual(dv_servis.bolme_geri_alinabilir_mi(v), "")
        DuranVarlik.objects.filter(pk=y2.pk).update(maliyet=Dc("499.00"))                                  # hareket gördü (düzenlendi)
        self.assertIn("düzenlenmiş", dv_servis.bolme_geri_alinabilir_mi(v))
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.bolmeyi_geri_al(v, kullanici=self.su)
        with self.assertRaises(dv_servis.DuranVarlikHatasi):
            dv_servis.bolmeyi_geri_al(y1, kullanici=self.su)                                             # bölünmemiş kartta anlamsız

    def test_orijinal_fatura_baglantisi_korunur(self):
        satis_alis = fs.fatura_olustur(tip_id=self.gider.pk, cari_id=self.satici.pk, tarih=D(2026, 3, 10), fatura_no="G9", kullanici=self.su,
                                       satirlar=[{"hesap_id": "253", "miktar": "1", "birim_fiyat": "1000", "kdv_id": self.kdv0.pk}])
        satir = satis_alis.satirlar.get()
        v = dv_servis.duran_varlik_olustur(ad="MAKİNE (2 ADET)", hesap_id="253", aktiflestirme_tarihi=D(2026, 3, 10), maliyet=None,
                                           fatura_satirlari=[satir.pk], kullanici=self.su)
        yeniler = self.bol_esit(v)
        self.assertEqual([s.pk for s in dv_servis.kaynak_satirlari(yeniler[0])], [satir.pk])
        self.assertEqual([s.pk for s in dv_servis.kaynak_satirlari(v)], [satir.pk])
        v.refresh_from_db()
        self.assertEqual(list(v.fatura_satirlari.values_list("pk", flat=True)), [satir.pk])               # orijinal bağları korunur


class BolmeEkranTest(BolmeBase):
    def setUp(self):
        self.client.force_login(self.su)

    def test_detay_bol_formu_kayit_ve_geri_alma(self):
        v = self.kart("13331.67", ad="UPS (2 ADET)")
        detay = reverse("core:duran_varlik_detay", args=[v.pk])
        self.assertContains(self.client.get(detay), "Böl")
        bol = reverse("core:duran_varlik_bol", args=[v.pk])
        r = self.client.get(bol + "?adet=2")
        self.assertContains(r, "6.665,83")
        self.assertContains(r, "6.665,84")
        r = self.client.post(bol, {"adet": "2", "islem": "kaydet", "ad_1": "UPS 1", "maliyet_1": "6.665,83", "ad_2": "UPS 2", "maliyet_2": "6.665,84"})
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.durum, "BOLUNDU")
        yeniler = list(dv_servis.yeni_kartlar(v))
        self.assertEqual([(y.ad, y.maliyet) for y in yeniler], [("UPS 1", Dc("6665.83")), ("UPS 2", Dc("6665.84"))])
        r = self.client.get(detay)
        self.assertContains(r, "Yeni Kartlar")
        self.assertContains(r, "Bölmeyi geri al")
        self.assertContains(self.client.get(reverse("core:duran_varlik_detay", args=[yeniler[0].pk])), "kartından bölünerek oluştu")
        self.assertContains(self.client.get(reverse("core:duran_varliklar")), "Bölündü")
        r = self.client.post(reverse("core:duran_varlik_bolmeyi_geri_al", args=[v.pk]))
        self.assertEqual(r.status_code, 302)
        v.refresh_from_db()
        self.assertEqual(v.durum, "AKTIF")

    def test_hatali_kayit_formda_kalir_ve_bolunmus_karta_bol_yok(self):
        v = self.kart("1000")
        bol = reverse("core:duran_varlik_bol", args=[v.pk])
        r = self.client.post(bol, {"adet": "2", "islem": "kaydet", "ad_1": "A", "maliyet_1": "600", "ad_2": "B", "maliyet_2": "300"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "eşit olmalı")
        v.refresh_from_db()
        self.assertEqual(v.durum, "AKTIF")
        self.bol_esit(v)
        self.assertEqual(self.client.get(bol).status_code, 302)                                            # bölünmüş karta bölme ekranı açılmaz
        self.assertNotContains(self.client.get(reverse("core:duran_varlik_detay", args=[v.pk])), bol)


class AyniKartCokSatirTest(BolmeBase):
    """Aynı demirbaş kartı tek satış faturasında birden çok 'Demirbaş satışı' satırıyla satılabilir: bedeller toplanır, maliyet/amortisman/kâr-zarar TEK kez."""

    def kartlar(self, maliyet="460000", amort="0"):
        return self.bol_esit(self.kart(maliyet, amort))

    def test_iki_satir_ayni_kart_maliyet_tek_kez_kar_zarar_sifir(self):
        y1, y2 = self.kartlar()
        f = self._fatura([self._dv_satir(y1, "100000", self.kdv20), self._dv_satir(y1, "130000", self.kdv0)])
        s = self._fis(f)
        self.assertEqual(s[("120.01", "B")][0], Dc("250000.00"))                                       # 100.000 + 20.000 KDV + 130.000
        self.assertEqual(s[("391.20", "A")][0], Dc("20000.00"))
        self.assertEqual(s[("253", "A")][0], Dc("230000.00"))                                           # maliyet TEK kez
        self.assertNotIn(("649", "A"), s)
        self.assertNotIn(("659", "B"), s)                                                            # kâr-zarar 0
        self.assertEqual(sum(x.borc for x in f.fis.satirlar.all()), sum(x.alacak for x in f.fis.satirlar.all()))
        self.assertEqual(f.satirlar.filter(silindi=False, demirbas=y1).count(), 2)                      # iki fatura satırı
        y1.refresh_from_db()
        y2.refresh_from_db()
        self.assertEqual((y1.durum, y1.satis_faturasi_id, y2.durum), ("SATILDI", f.pk, "AKTIF"))
        self.assertEqual(_bak("253"), Dc("230000.00"))

    def test_amortisman_bir_kez_ve_kar_toplam_bedelden(self):
        y1, _ = self.kartlar(amort="46000")                                                              # kart: 230.000 maliyet, 23.000 amortisman
        f = self._fatura([self._dv_satir(y1, "100000", self.kdv20), self._dv_satir(y1, "130000", self.kdv0)])
        s = self._fis(f)
        self.assertEqual((s[("253", "A")][0], s[("257", "B")][0]), (Dc("230000.00"), Dc("23000.00")))
        self.assertEqual(s[("649", "A")][0], Dc("23000.00"))                                            # 230.000 − (230.000 − 23.000)

    def test_toplam_bedel_defter_degerinden_dusukse_zarar_tek_satir(self):
        y1, _ = self.kartlar()
        f = self._fatura([self._dv_satir(y1, "100000", self.kdv20), self._dv_satir(y1, "100000", self.kdv0)])
        s = self._fis(f)
        self.assertEqual((s[("253", "A")][0], s[("659", "B")][0]), (Dc("230000.00"), Dc("30000.00")))

    def test_uc_satir_iki_kart(self):
        y1, y2 = self.kartlar()
        f = self._fatura([self._dv_satir(y1, "100000", self.kdv20), self._dv_satir(y2, "230000", self.kdv0), self._dv_satir(y1, "130000", self.kdv0)])
        s = self._fis(f)
        self.assertEqual(sorted(x.alacak for x in f.fis.satirlar.filter(hesap_id="253", silindi=False)), [Dc("230000.00"), Dc("230000.00")])   # her kartın maliyeti 1 kez
        self.assertEqual(s[("120.01", "B")][0], Dc("480000.00"))
        self.assertEqual(_bak("253"), Dc("0.00"))

    def test_baska_faturada_ikinci_kez_satilamaz(self):
        y1, _ = self.kartlar()
        self._fatura([self._dv_satir(y1, "100000"), self._dv_satir(y1, "130000", self.kdv0)])
        with self.assertRaises(fs.FaturaHatasi):
            self._fatura([self._dv_satir(y1, "10")])

    def test_taslak_onay_guncelle_ve_silme(self):
        y1, y2 = self.kartlar()
        t = fs.fatura_taslak_olustur(cari_id=self.cari.pk, tarih=D(2026, 3, 10), tip_id=self.satis.pk, kullanici=self.su,
                                     satirlar=[self._dv_satir(y1, "100000", self.kdv20), self._dv_satir(y1, "130000", self.kdv0)])
        y1.refresh_from_db()
        self.assertEqual(y1.durum, "AKTIF")                                                              # taslakta satılmaz
        fs.fatura_onayla(t, kullanici=self.su)
        t.refresh_from_db()
        s = self._fis(t)
        self.assertEqual((s[("253", "A")][0], s[("120.01", "B")][0]), (Dc("230000.00"), Dc("250000.00")))
        y1.refresh_from_db()
        self.assertEqual(y1.durum, "SATILDI")
        fs.fatura_guncelle(t, tip_id=self.satis.pk, cari_id=self.cari.pk, tarih=D(2026, 3, 10), kullanici=self.su,
                           satirlar=[self._dv_satir(y1, "120000", self.kdv20), self._dv_satir(y1, "110000", self.kdv0)])
        t.refresh_from_db()
        s = self._fis(t)
        self.assertEqual((s[("253", "A")][0], s[("391.20", "A")][0]), (Dc("230000.00"), Dc("24000.00")))   # maliyet yine tek kez
        fs.fatura_sil(t, kullanici=self.su)
        y1.refresh_from_db()
        self.assertEqual(y1.durum, "AKTIF")                                                              # silinince kart aktife döner
        self.assertEqual(_bak("253"), Dc("460000.00"))
