"""KARMA ALIŞ FATURASI (ALIŞ FATURASI / İHRAÇ KAYITLI): STOK satırı + HESAP satırı (gider·258·dönemsel) TEK faturada, TEK fişte.

Vaka (Otto OTT2026000011765): M6 PUL 40.000 ad 4.400 TL (→ 150 depo) + stok kartı olmayan sarf (matkap uçları 881,48 TL → 730.09). Kurallar: stok → stok hesabı B,
hesap → seçilen hesap B (proje/dönemsel kuralı tip 8 ile aynı), KDV 191 B (+ tevkifat 360 A), cari 320 A; stok hareketi YALNIZ stok satırı için; depo yalnız karma faturada
stoklu satır varsa zorunlu; GİDER tipinde stok yasağı sürer; düzenle/sil/irsaliyeden fatura/alış iadesi karma faturada doğru çalışır."""
import datetime
import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import (Depo, DonemselDagitim, Fatura, FaturaTipi, HesapPlani, KategoriHesap, StokHareket, TevkifatOrani, YevmiyeFisi)
from core.services import hesap_plani as hp
from core.services.depo import depo_olustur
from core.services.fatura import (FaturaHatasi, fatura_guncelle, fatura_olustur, fatura_onayla, fatura_sil, fatura_taslak_olustur, karma_alis_tipi_mi)
from core.services.teklif_siparis import irsaliyeyi_faturaya_cevir, teklif_siparis_olustur, teklif_siparis_onayla
from core.services.yatirim_projesi import proje_olustur, proje_toplami
from core.tests.test_fatura import FaturaTestTemel, _hesap

D = datetime.date
Dc = Decimal


class KarmaTemel(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("730.09", "MATKAP UÇLARI / SARF", grup="MALIYET", kalem="")
        _hesap("770.03", "BANKA MASRAF GİDERLERİ", grup="MALIYET", kalem="")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR", kalem="DDV")
        _hesap("180", "GELECEK AYLARA AİT GİDERLER")
        hp.hesap_olustur(kod="180.01", ad="KİRA", ust_kodu="180")
        _hesap("360.10", "ÖDENECEK KDV TEVKİFATI", kalem="KVYK")
        cls.depo = depo_olustur(kod="DA", ad="DEPO A")
        cls.gider_tip = FaturaTipi.objects.create(ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.ihrac = FaturaTipi.objects.create(ad="ALIŞ FATURASI-İHRAÇ KAYITLI", yon=FaturaTipi.Yon.ALIS)
        cls.satis_iade = FaturaTipi.objects.create(ad="ALIŞ FATURASI-SATIŞ İADE", yon=FaturaTipi.Yon.ALIS, maliyet_fisi=FaturaTipi.MaliyetFisi.SATIS_IADE)
        cls.yalniz_kdv = FaturaTipi.objects.create(ad="ALIŞ FATURASI-KDV", yon=FaturaTipi.Yon.ALIS, yalniz_kdv=True)
        cls.smm = FaturaTipi.objects.create(ad="SERBEST MESLEK MAKBUZU", yon=FaturaTipi.Yon.ALIS, gider=True, stopajli=True)
        cls.alis_iade = FaturaTipi.objects.create(ad="SATIŞ FATURASI-ALIŞ İADE", yon=FaturaTipi.Yon.SATIS, maliyet_fisi=FaturaTipi.MaliyetFisi.ALIS_IADE)
        for tip in (cls.ihrac, cls.alis_iade, cls.satis_iade):
            KategoriHesap.objects.create(kategori=cls.alt, fatura_tipi=tip, hesap=HesapPlani.objects.get(hesap_kodu="153.10"))
        cls.su = User.objects.create_superuser("karma", password="x")

    # --- girdiler
    def _stok(self, miktar="40000", fiyat="0.11"):
        return {"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": fiyat}

    def _hesap_satir(self, hesap="730.09", tutar="881.48", kdv=True, **ek):
        return {"tur": "HESAP", "hesap_id": hesap, "miktar": "1", "birim_fiyat": tutar, "kdv_id": self.kdv.pk if kdv else None, **ek}

    def _kes(self, satirlar, tip=None, depo=True, no="", **kw):
        self._sayac = getattr(self, "_sayac", 0) + 1
        return fatura_olustur(tip_id=(tip or self.alis).pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no=no or f"K-{self._sayac}",
                              satirlar=satirlar, depo_id=(self.depo.pk if depo else None), kullanici=self.su, **kw)

    def _fis(self, fatura):
        return {(s.hesap_id, "B" if s.borc else "A"): (s.borc or s.alacak) for s in fatura.fis.satirlar.filter(silindi=False)}

    def _denge(self, fatura):
        satirlar = fatura.fis.satirlar.filter(silindi=False)
        self.assertEqual(sum(s.borc for s in satirlar), sum(s.alacak for s in satirlar))

    def _hareketler(self, fatura):
        return list(StokHareket.objects.filter(fatura_satir__fatura=fatura, silindi=False))


class KarmaFisTest(KarmaTemel):
    def test_karma_tip_tanimi(self):
        self.assertTrue(all(karma_alis_tipi_mi(t) for t in (self.alis, self.ihrac)))
        self.assertFalse(any(karma_alis_tipi_mi(t) for t in (self.gider_tip, self.satis_iade, self.yalniz_kdv, self.smm, self.alis_iade, self.satis, None)))

    def test_otto_vakasi_tek_fis_stok_hesap_kdv_cari(self):
        f = self._kes([self._stok(), self._hesap_satir()], no="OTT2026000011765")
        s = self._fis(f)
        self.assertEqual(s[("153.10", "B")], Dc("4400.00"))                    # stok satırı → stok hesabı B
        self.assertEqual(s[("730.09", "B")], Dc("881.48"))                    # hesap satırı → seçilen hesap B
        self.assertEqual(s[("191", "B")], Dc("1056.30"))                      # 880,00 + 176,30 (satır satır KDV)
        self.assertEqual(s[("320.10.0001", "A")], Dc("6337.78"))              # cari A: 4.400 + 881,48 + 1.056,30
        self.assertEqual(len(s), 4)                                           # TEK fiş, 4 satır
        self._denge(f)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak=YevmiyeFisi.Kaynak.FATURA).count(), 1)
        self.assertEqual([x.satir_tipi for x in f.satirlar.order_by("id")], ["STOK", "HESAP"])
        self.assertEqual((f.ara_toplam, f.kdv_toplam, f.genel_toplam), (Dc("5281.48"), Dc("1056.30"), Dc("6337.78")))

    def test_stok_hareketi_yalniz_stok_satiri_icin(self):
        f = self._kes([self._stok(), self._hesap_satir()])
        (h,) = self._hareketler(f)
        self.assertEqual((h.stok_id, h.tur, h.miktar, h.depo_id, h.giris_tutar_try), (self.stok.pk, "GIRIS", Dc("40000"), self.depo.pk, Dc("4400.00")))
        self.assertEqual(h.fatura_satir.satir_tipi, "STOK")
        self.assertEqual(StokHareket.objects.count(), 1)

    def test_ihrac_kayitli_tipi_de_karma(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("770.03", "50")], tip=self.ihrac)
        s = self._fis(f)
        self.assertEqual((s[("153.10", "B")], s[("770.03", "B")]), (Dc("1000.00"), Dc("50.00")))
        self._denge(f)

    def test_yalniz_hesap_satirli_tip5_depo_zorunlu_degil(self):
        f = self._kes([self._hesap_satir("730.09", "1000")], depo=False)
        self.assertIsNone(f.depo_id)
        s = self._fis(f)
        self.assertEqual((s[("730.09", "B")], s[("191", "B")], s[("320.10.0001", "A")]), (Dc("1000.00"), Dc("200.00"), Dc("1200.00")))
        self.assertEqual(StokHareket.objects.count(), 0)                       # stok/depoya dokunmaz

    def test_depo_verilse_bile_hesap_satiri_hareket_uretmez(self):
        f = self._kes([self._hesap_satir("730.09", "1000")], depo=True)
        self.assertEqual(self._hareketler(f), [])

    def test_stok_ve_hesap_birlikte_depo_yoksa_reddedilir_atomik(self):
        with self.assertRaisesMessage(FaturaHatasi, "stok satırları için depo seçin"):
            self._kes([self._stok(), self._hesap_satir()], depo=False)
        self.assertEqual((Fatura.objects.count(), YevmiyeFisi.objects.count(), StokHareket.objects.count()), (0, 0, 0))

    def test_yalniz_stok_satirli_fatura_eski_davranis_depo_istenmez(self):
        f = self._kes([self._stok("10", "100")], depo=False)                  # eski davranış: depo seçilmezse hareket yazılmaz (uyarı ekranda)
        self.assertEqual(self._hareketler(f), [])

    def test_gider_tipinde_stok_yasagi_suruyor(self):
        with self.assertRaisesMessage(FaturaHatasi, "gider faturasında stok kullanılamaz"):
            self._kes([self._stok()], tip=self.gider_tip, depo=False)
        with self.assertRaisesMessage(FaturaHatasi, "gider faturasında stok kullanılamaz"):
            self._kes([self._hesap_satir(), self._stok("1", "1")], tip=self.gider_tip, depo=False)
        self.assertEqual(Fatura.objects.count(), 0)

    def test_karma_olmayan_alis_tiplerinde_hesap_satiri_reddedilir(self):
        for tip in (self.satis_iade, self.yalniz_kdv):
            with self.subTest(tip.ad), self.assertRaisesMessage(FaturaHatasi, "yalnız gider faturası tipinde"):
                self._kes([self._stok("1", "1"), self._hesap_satir()], tip=tip)
        self.assertEqual(Fatura.objects.count(), 0)

    def test_hesap_kumesi_tip8_ile_ayni_gecersiz_hesaplar_reddedilir(self):
        for kod in ("153.10", "600", "730", "YOK", "320.10.0001"):
            with self.subTest(kod), self.assertRaisesMessage(FaturaHatasi, "geçerli bir gider veya duran varlık hesabı"):
                self._kes([self._stok("1", "1"), self._hesap_satir(kod)])
        self.assertEqual(Fatura.objects.count(), 0)

    def test_stok_ve_hesap_ayni_satirda_reddedilir(self):
        with self.assertRaises(FaturaHatasi):
            self._kes([{**self._stok(), "hesap_id": "730.09", "tur": "HESAP"}])

    def test_258_yatirim_projesi_karma_faturada(self):
        proje = proje_olustur(ad="ATÖLYE YATIRIMI", grup_kodu=None, kullanici=self.su)
        f = self._kes([self._stok("10", "100"), self._hesap_satir("258", "5000", yatirim_projesi_id=proje.pk)])
        self.assertEqual(proje_toplami(proje), Dc("5000.00"))
        self.assertIn(("258", "B"), self._fis(f))
        self._denge(f)
        with self.assertRaisesMessage(FaturaHatasi, "yatırım projesi seçimi zorunludur"):
            self._kes([self._stok("1", "1"), self._hesap_satir("258", "10")])

    def test_donemsel_gider_karma_faturada(self):
        satir = self._hesap_satir("770.03", "12000", donemsel=True, donem_grup="180.01", donem_baslangic=D(2026, 3, 1), donem_bitis=D(2027, 2, 28),
                                  donem_aciklama="KİRA")
        f = self._kes([self._stok("10", "100"), satir])
        s = self._fis(f)
        h180 = [k for k in s if k[0].startswith("180.01.")]
        self.assertEqual((len(h180), s[h180[0]]), (1, Dc("12000.00")))         # gider değil 180.01.000N hesabı B
        self.assertNotIn(("770.03", "B"), s)
        self.assertTrue(DonemselDagitim.objects.exists())
        self._denge(f)

    def test_tevkifat_hesap_satirinda_360_alacak(self):
        tev = TevkifatOrani.objects.create(kod="5/10", pay=5, payda=10, hesap=HesapPlani.objects.get(hesap_kodu="360.10"))
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "1000", tevkifat_id=tev.pk)])
        s = self._fis(f)
        self.assertEqual(s[("360.10", "A")], Dc("100.00"))                    # 1.000 × %20 KDV = 200 → 5/10 = 100
        self.assertEqual(s[("191", "B")], Dc("400.00"))                       # 200 (stok) + 200 (hesap) — alışta KDV tam
        self.assertEqual(s[("320.10.0001", "A")], Dc("2300.00"))              # 1.000 + 1.000 + 400 − 100
        self._denge(f)

    def test_doviz_karma_fatura(self):
        f = self._kes([self._stok("10", "10"), self._hesap_satir("730.09", "100")], para_birimi="USD")
        s = {x.hesap_id: x for x in f.fis.satirlar.filter(silindi=False)}
        self.assertEqual((s["153.10"].borc, s["730.09"].borc, s["153.10"].islem_pb), (Dc("3000.00"), Dc("3000.00"), "USD"))
        self._denge(f)


class KarmaDuzenleSilTest(KarmaTemel):
    def _guncelle(self, f, satirlar, depo=True, **kw):
        veri = dict(tip_id=f.tip_id, cari_id=f.cari_id, tarih=f.tarih, fatura_no=f.fatura_no, satirlar=satirlar, depo_id=(self.depo.pk if depo else None), kullanici=self.su)
        veri.update(kw)
        return fatura_guncelle(f, **veri)

    def test_duzenle_hesap_satiri_ekle_sil_fis_yeniden_dogru_yazilir(self):
        f = self._kes([self._stok("10", "100")])
        self.assertEqual(set(self._fis(f)), {("153.10", "B"), ("191", "B"), ("320.10.0001", "A")})
        self._guncelle(f, [self._stok("10", "100"), self._hesap_satir("730.09", "500"), self._hesap_satir("770.03", "300", kdv=False)])   # 2 hesap satırı eklendi
        f.refresh_from_db()
        s = self._fis(f)
        self.assertEqual((s[("730.09", "B")], s[("770.03", "B")], s[("191", "B")], s[("320.10.0001", "A")]), (Dc("500.00"), Dc("300.00"), Dc("300.00"), Dc("2100.00")))
        self._denge(f)
        self.assertEqual(f.fis.satirlar.filter(silindi=False).count(), 5)
        self.assertEqual(len(self._hareketler(f)), 1)                           # stok hareketi hâlâ tek
        self._guncelle(f, [self._stok("10", "100"), self._hesap_satir("730.09", "500")])                                                   # 770.03 satırı silindi
        f.refresh_from_db()
        s = self._fis(f)
        self.assertNotIn(("770.03", "B"), s)
        self.assertEqual((s[("191", "B")], s[("320.10.0001", "A")]), (Dc("300.00"), Dc("1800.00")))
        self._denge(f)

    def test_duzenle_stok_satiri_silinip_yalniz_hesap_kalir_hareket_geri_alinir(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "500")])
        self.assertEqual(len(self._hareketler(f)), 1)
        self._guncelle(f, [self._hesap_satir("730.09", "500")], depo=False)
        f.refresh_from_db()
        self.assertEqual((self._hareketler(f), StokHareket.objects.filter(silindi=False).count()), ([], 0))
        self.assertEqual(set(self._fis(f)), {("730.09", "B"), ("191", "B"), ("320.10.0001", "A")})
        self._denge(f)

    def test_duzenle_hesap_ve_stok_birlikte_depo_yoksa_reddedilir_eski_hal_korunur(self):
        f = self._kes([self._hesap_satir("730.09", "500")], depo=False)
        fis_no = f.fis.fis_no
        with self.assertRaisesMessage(FaturaHatasi, "stok satırları için depo seçin"):
            self._guncelle(f, [self._stok("10", "100"), self._hesap_satir("730.09", "500")], depo=False)
        f.refresh_from_db()
        self.assertEqual((f.fis.fis_no, f.satirlar.filter(silindi=False).count()), (fis_no, 1))

    def test_duzenle_tutar_degisimi_stok_hareketi_yerinde_guncellenir(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "500")])
        h_id = self._hareketler(f)[0].pk
        self._guncelle(f, [self._stok("10", "120"), self._hesap_satir("730.09", "700")])
        f.refresh_from_db()
        (h,) = self._hareketler(f)
        self.assertEqual((h.pk, h.giris_tutar_try), (h_id, Dc("1200.00")))
        self.assertEqual(self._fis(f)[("730.09", "B")], Dc("700.00"))
        self._denge(f)

    def test_sil_fis_ve_hareket_tamamen_kalkar(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "500")])
        fis_pk = f.fis_id
        fatura_sil(f, kullanici=self.su)
        self.assertEqual((Fatura.objects.count(), StokHareket.objects.count(), YevmiyeFisi.objects.filter(pk=fis_pk).count()), (0, 0, 0))

    def test_taslak_karma_duzenle_ve_onayla(self):
        t = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), tip_id=self.alis.pk, satirlar=[self._stok("10", "100"), self._hesap_satir()],
                                  depo_id=self.depo.pk, kullanici=self.su)
        self.assertEqual((t.durum, StokHareket.objects.count(), YevmiyeFisi.objects.count()), ("TASLAK", 0, 0))
        self._guncelle(t, [self._stok("10", "100"), self._hesap_satir("770.03", "40")])
        fatura_onayla(t, kullanici=self.su)
        t.refresh_from_db()
        self.assertIn(("770.03", "B"), self._fis(t))
        self.assertEqual(len(self._hareketler(t)), 1)

    def test_tipsiz_alis_taslagi_hesap_satiri_kabul_onayda_tipe_gore_dogrulanir(self):
        t = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), tip_id=None, yon="ALIS", satirlar=[self._stok("10", "100"), self._hesap_satir()],
                                  depo_id=self.depo.pk, kullanici=self.su)
        Fatura.objects.filter(pk=t.pk).update(tip=self.satis_iade)                  # karma OLMAYAN tip seçilirse onay reddeder
        t.refresh_from_db()
        with self.assertRaisesMessage(FaturaHatasi, "hesap satırı yalnız gider faturasında"):
            fatura_onayla(t, kullanici=self.su)
        Fatura.objects.filter(pk=t.pk).update(tip=self.alis)
        t.refresh_from_db()
        fatura_onayla(t, kullanici=self.su)
        self.assertEqual(t.durum, "ONAYLI")

    def test_tipsiz_taslakta_stok_ve_hesap_depo_yoksa_onayda_yakalanir(self):
        t = fatura_taslak_olustur(cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), tip_id=None, yon="ALIS", satirlar=[self._stok("10", "100"), self._hesap_satir()],
                                  depo_id=None, kullanici=self.su)
        Fatura.objects.filter(pk=t.pk).update(tip=self.alis)
        t.refresh_from_db()
        with self.assertRaisesMessage(FaturaHatasi, "stok satırları için depo seçin"):
            fatura_onayla(t, kullanici=self.su)


class KarmaIrsaliyeVeIadeTest(KarmaTemel):
    def _irsaliyeli_taslak(self):
        irsaliye = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), depo_id=self.depo.pk,
                                          satirlar=[{"stok_id": self.stok.pk, "miktar": "40000", "birim_fiyat": "0.11"}])
        teklif_siparis_onayla(irsaliye, kullanici=self.su)
        irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.su)
        irsaliye.refresh_from_db()
        return irsaliye, irsaliye.fatura

    def test_irsaliyeden_gelen_faturaya_hesap_satiri_eklenir_stok_cifte_yazilmaz(self):
        irsaliye, taslak = self._irsaliyeli_taslak()
        self.assertEqual((taslak.durum, taslak.tip_id), ("TASLAK", None))
        self.assertEqual(StokHareket.objects.filter(silindi=False).count(), 1)         # irsaliye girişi
        fatura_guncelle(taslak, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="OTT2026000011765",
                        satirlar=[{"stok_id": self.stok.pk, "miktar": "40000", "birim_fiyat": "0.11"}, self._hesap_satir("730.09", "881.48")],
                        depo_id=self.depo.pk, kullanici=self.su)                         # tip seçildi + hesap satırı eklendi
        fatura_onayla(taslak, kullanici=self.su)
        taslak.refresh_from_db()
        s = self._fis(taslak)
        self.assertEqual((s[("153.10", "B")], s[("730.09", "B")], s[("320.10.0001", "A")]), (Dc("4400.00"), Dc("881.48"), Dc("6337.78")))
        self._denge(taslak)
        hareketler = StokHareket.objects.filter(silindi=False)
        self.assertEqual(hareketler.count(), 1)                                          # fatura ikinci giriş YAZMADI
        self.assertEqual(hareketler.get().giris_tutar_try, Dc("4400.00"))                # irsaliye girişi fatura tutarıyla fiyatlandı (yalnız stok satırı)

    def test_irsaliyeli_karma_faturada_depo_sorulmaz_ve_duzenlenebilir(self):
        irsaliye, taslak = self._irsaliyeli_taslak()
        fatura_guncelle(taslak, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="X1",
                        satirlar=[{"stok_id": self.stok.pk, "miktar": "40000", "birim_fiyat": "0.11"}, self._hesap_satir("730.09", "100")], depo_id=None, kullanici=self.su)
        fatura_onayla(taslak, kullanici=self.su)
        taslak.refresh_from_db()
        fatura_guncelle(taslak, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="X1",
                        satirlar=[{"stok_id": self.stok.pk, "miktar": "40000", "birim_fiyat": "0.12"}, self._hesap_satir("730.09", "150")], depo_id=None, kullanici=self.su)
        taslak.refresh_from_db()
        self.assertEqual(self._fis(taslak)[("730.09", "B")], Dc("150.00"))
        self.assertEqual(StokHareket.objects.filter(silindi=False).count(), 1)

    def test_alis_iadesi_karma_stok_cikisi_ve_hesap_alacak(self):
        self._kes([self._stok("1000", "1")], no="ALIS-1")                                  # eldeki 1.000
        iade = fatura_olustur(tip_id=self.alis_iade.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="IADE-1", depo_id=self.depo.pk, kullanici=self.su,
                              satirlar=[{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "1"}, self._hesap_satir("730.09", "50")])
        s = self._fis(iade)
        self.assertEqual((s[("153.10", "A")], s[("730.09", "A")]), (Dc("100.00"), Dc("50.00")))      # stok + gider ALACAK
        self.assertEqual((s[("391", "A")], s[("320.10.0001", "B")]), (Dc("30.00"), Dc("180.00")))          # KDV %20 (stok 20 + hesap 10) 391'e; cari BORÇ 100 + 50 + 30
        self._denge(iade)
        (h,) = self._hareketler(iade)
        self.assertEqual((h.tur, h.miktar, h.fatura_satir.satir_tipi), ("CIKIS", Dc("100"), "STOK"))          # yalnız stok satırı için çıkış
        fatura_sil(iade, kullanici=self.su)
        self.assertFalse(Fatura.objects.filter(fatura_no="IADE-1").exists())


class KarmaEkranTest(KarmaTemel):
    def _veri(self, satirlar, tip=None, depo=True):
        veri = {"tip": (tip or self.alis).pk, "cari": self.tedarikci.pk, "tarih": "2026-03-10", "fatura_no": "EK-1", "para_birimi": "TRY",
                "depo": self.depo.pk if depo else "", "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            for k, v in s.items():
                veri[f"form-{i}-{k}"] = v
        return veri

    STOK = {"tur": "STOK", "miktar": "40.000", "birim_fiyat": "0,11"}

    def _stok_satir(self):
        return {**self.STOK, "stok": str(self.stok.pk)}

    def _hesap_satir_form(self, hesap="730.09", tutar="881,48"):
        return {"tur": "HESAP", "hesap": hesap, "miktar": "1", "birim_fiyat": tutar, "kdv": str(self.kdv.pk), "tevkifat": ""}

    def test_form_karma_tipi_ve_satir_turu_secicisi(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertContains(r, 'id="tip-karma"')
        self.assertEqual(r.context["tip_karma"][str(self.alis.pk)], True)
        self.assertEqual((r.context["tip_karma"][str(self.gider_tip.pk)], r.context["tip_karma"][str(self.satis_iade.pk)]), (False, False))
        self.assertContains(r, "Kalem (stok · hesap)")
        self.assertContains(r, "Hesap satırı")
        self.assertNotContains(r, '<option value="DEMIRBAS"')                         # alışta demirbaş satışı seçeneği yok
        self.assertContains(r, "karmaMi()")

    def test_ekrandan_karma_kayit(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._veri([self._stok_satir(), self._hesap_satir_form()]))
        f = Fatura.objects.get(fatura_no="EK-1")
        self.assertRedirects(r, reverse("core:fatura_detay", args=[f.pk]))
        s = self._fis(f)
        self.assertEqual((s[("153.10", "B")], s[("730.09", "B")], s[("320.10.0001", "A")]), (Dc("4400.00"), Dc("881.48"), Dc("6337.78")))
        self.assertEqual(len(self._hareketler(f)), 1)

    def test_ekran_stok_ve_hesap_depo_bos_hata_formda_kayit_yok(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._veri([self._stok_satir(), self._hesap_satir_form()], depo=False))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "stok satırları için depo seçin")
        self.assertEqual(Fatura.objects.count(), 0)

    def test_ekran_yalniz_hesap_depo_bos_sorunsuz_ve_depo_uyarisi_yok(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self._veri([self._hesap_satir_form("730.09", "1.000,00")], depo=False), follow=True)
        self.assertEqual(Fatura.objects.count(), 1)
        self.assertNotContains(r, "Depo seçilmedi")                                    # hesap satırlı faturada stok uyarısı anlamsız
        self.assertContains(r, "fiş")

    def test_detay_satir_turu_gorunur(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "500")])
        self.client.force_login(self.su)
        h = self.client.get(reverse("core:fatura_detay", args=[f.pk])).content.decode()
        self.assertIn("Stok / Hesap", h)
        rozetler = re.findall(r'data-label="Tür"><span class="ft-rozet-kucuk">([^<]+)</span>', h)
        self.assertEqual(rozetler, ["Stok", "Hesap"])
        self.assertIn("730.09", h)
        # yalnız stok alış faturasında başlık eskisi gibi
        g = self._kes([self._stok("1", "1")])
        h2 = self.client.get(reverse("core:fatura_detay", args=[g.pk])).content.decode()
        self.assertNotIn("Kalem (stok · hesap)", h2)

    def test_duzenle_ekrani_hesap_satirini_hesap_turuyle_acar_ve_kaydeder(self):
        f = self._kes([self._stok("10", "100"), self._hesap_satir("730.09", "500")])
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        turler = [fm.initial["tur"] for fm in r.context["formset"]]
        self.assertEqual(turler, ["STOK", "HESAP"])
        veri = self._veri([{**self._stok_satir(), "miktar": "10", "birim_fiyat": "100"}, {**self._hesap_satir_form("730.09", "700")}])
        veri["fatura_no"] = f.fatura_no
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), veri)
        self.assertRedirects(r, reverse("core:fatura_detay", args=[f.pk]))
        f.refresh_from_db()
        self.assertEqual(self._fis(f)[("730.09", "B")], Dc("700.00"))
