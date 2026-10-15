"""FİŞ KARŞILAŞTIRMA (2026-10-15, form yeniden tasarımı 3. tur): AYNI girdiyle ESKİ form akışı (b2e6d01'deki ekle/düzenle POST akışı + FaturaForm, ``eski_fatura_form_akisi`` içinde DONDURULMUŞ kopya) ile
YENİ form akışının (güncel view) ürettiği fatura / satırlar / yevmiye fişi satırları (hesap, borç, alacak, işlem PB/tutarı, kur, açıklama) / stok hareketleri BİREBİR aynı olmalı.
Her senaryo çiftlerle çalışır: biri eski, biri yeni akışla (farklı fatura no); açıklamalardaki fatura no normalize edilir. Senaryolar: normal alış (stok), karma tip, gider, yalnız KDV, SMM (stopajlı),
ihraç kayıtlı, EUR alış, USD alış, demirbaş/yatırım projesi, tevkifatlı satış, Trendyol tipi satış (maliyet fişi), satış iadesi, alış iadesi, irsaliyeden taslak → düzenle → onay, düzenle ile tutar değiştirme."""
import datetime
from decimal import Decimal

from django.test import RequestFactory
from django.urls import reverse

from core.models import Cari, Fatura, FaturaTipi, KategoriHesap, HesapPlani, StokHareket, TevkifatOrani
from core.services.fatura import fatura_onayla
from core.services.teklif_siparis import irsaliyeyi_faturaya_cevir, teklif_siparis_olustur, teklif_siparis_onayla
from core.services.yatirim_projesi import proje_olustur
from core.tests import eski_fatura_form_akisi as eski
from core.tests.test_fatura import _hesap
from core.tests.test_fatura_karma import KarmaTemel

D = datetime.date
Dc = Decimal


class KarsilastirmaTaban(KarmaTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _hesap("320.10.0002", "EUR TEDARİKÇİ", kalem="KVYK")
        _hesap("320.10.0003", "USD TEDARİKÇİ", kalem="KVYK")
        _hesap("120.10.0132", "TRENDYOL SATIŞLARI")
        _hesap("360.20", "GV STOPAJI", kalem="KVYK")
        _hesap("621", "SATILAN TİCARİ MALLAR MALİYETİ", grup="GELIR_TABLOSU", kalem="B")
        cls.eur = Cari.objects.create(kod="320-10-0002", unvan="EUR TEDARİKÇİ", para_birimi="EUR", muhasebe_kodu="320.10.0002")
        cls.usd = Cari.objects.create(kod="320-10-0003", unvan="USD TEDARİKÇİ", para_birimi="USD", muhasebe_kodu="320.10.0003")
        cls.trendyol = Cari.objects.create(kod="120-10-0132", unvan="TRENDYOL SATIŞLARI", para_birimi="TRY", muhasebe_kodu="120.10.0132")
        cls.tev = TevkifatOrani.objects.create(kod="5/10", pay=5, payda=10, hesap=HesapPlani.objects.get(hesap_kodu="360.10"))
        FaturaTipi.objects.filter(pk=cls.satis.pk).update(maliyet_fisi=FaturaTipi.MaliyetFisi.SATIS)                    # canlıdaki SATIŞ FATURASI gibi: maliyet fişi üretir
        for tip in (cls.yalniz_kdv, cls.smm, cls.gider_tip):
            pass
        cls.proje = proje_olustur(ad="DENEME PROJE")

    # ------------------------------------------------------------------ yardımcılar
    def veri(self, tip, cari, satirlar, no, tarih="2026-03-10", pb="TRY", depo=True, **ek):
        v = {"tip": tip.pk, "cari": cari.pk, "tarih": tarih, "para_birimi": pb, "fatura_no": no, "depo": self.depo.pk if depo else "",
             "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, s in enumerate(satirlar):
            for k, deger in s.items():
                v[f"form-{i}-{k}"] = deger
        v.update(ek)
        return v

    def stok_satir(self, miktar="10", fiyat="100", **ek):
        return {"stok": self.stok.pk, "miktar": miktar, "birim_fiyat": fiyat, **ek}

    def hesap_satir(self, hesap="730.09", tutar="881,48", **ek):
        return {"hesap": hesap, "miktar": "1", "birim_fiyat": tutar, "kdv": self.kdv.pk, **ek}

    def istek(self, veri):
        r = RequestFactory().post("/x/", veri)
        r.user = self.su
        return r

    def eski_ekle(self, yon, veri):
        fatura, fform, formset = eski.eski_ekle_post(self.istek(veri), yon)
        self.assertIsNotNone(fatura, (fform.errors, formset.errors))
        return fatura

    def yeni_ekle(self, yon, veri):
        self.client.force_login(self.su)
        r = self.client.post(reverse(f"core:{yon.lower()}_fatura_ekle" if yon == "ALIS" else "core:satis_fatura_ekle"), veri)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and (r.context["fform"].errors, r.context["formset"].errors))
        return Fatura.objects.get(fatura_no=veri["fatura_no"])

    def eski_duzenle(self, fatura, veri):
        tamam, fform, formset = eski.eski_duzenle_post(self.istek(veri), fatura)
        self.assertTrue(tamam, (fform.errors, formset.errors))
        fatura.refresh_from_db()
        return fatura

    def yeni_duzenle(self, fatura, veri):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fatura_duzenle", args=[fatura.pk]), veri)
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and (r.context["fform"].errors, r.context["formset"].errors))
        fatura.refresh_from_db()
        return fatura

    def ozet(self, f):
        no = f.fatura_no
        norm = lambda m: (m or "").replace(no, "#")
        f = Fatura.objects.get(pk=f.pk)
        fis = f.fis
        hareketler = sorted(((h.stok_id, h.depo_id, h.tur, h.miktar, h.kaynak,
                              sorted((s.hesap_id, s.borc, s.alacak, s.islem_pb, s.islem_tutari, s.islem_kuru) for s in h.fis.satirlar.filter(silindi=False)) if h.fis_id else None)
                             for h in StokHareket.objects.filter(fatura_satir__fatura=f, silindi=False)), key=str)
        return {
            "fatura": (f.tip_id, f.yon, f.durum, f.cari_id, f.tarih, f.para_birimi, f.kur, f.taslak_kur, f.depo_id, f.vade_tarihi, f.sahsi_alis, f.gv_stopaj_orani, (f.aciklama or "").strip()),
            "toplam": (f.ara_toplam, f.kdv_toplam, f.tevkifat_toplam, f.gv_stopaj_tutari, f.genel_toplam, f.odenecek),
            "satirlar": sorted(((s.stok_id, s.hesap_id, s.demirbas_id, s.miktar, s.birim_fiyat, s.kdv_id, s.tevkifat_id, s.yatirim_projesi_id)
                                for s in f.satirlar.filter(silindi=False)), key=str),
            "fis": None if fis is None else (fis.tarih, fis.kaynak, norm(fis.aciklama),
                                              sorted(((s.hesap_id, s.borc, s.alacak, s.islem_pb, s.islem_tutari, s.islem_kuru, norm(s.aciklama))
                                                      for s in fis.satirlar.filter(silindi=False)), key=str)),
            "stok_hareketleri": hareketler,
        }

    def esit(self, ad, eski_f, yeni_f):
        a, b = self.ozet(eski_f), self.ozet(yeni_f)
        self.assertEqual(a, b, f"{ad}: eski ve yeni akış farklı fiş/fatura üretti")
        satir_sayisi = len(a["fis"][3]) if a["fis"] else 0
        print(f"FIS-KARSILASTIRMA | {ad:46} | fiş satırı {satir_sayisi} | toplam {a['toplam'][4]} {a['fatura'][5]} | stok hareketi {len(a['stok_hareketleri'])} | FARK 0")
        return satir_sayisi

    def cift(self, ad, yon, tip, cari, satirlar, **kw):
        """Eski + yeni ekle akışı (farklı fatura no) -> karşılaştır."""
        self._cn = getattr(self, "_cn", 0) + 1                                                            # fatura no: büyük harf ASCII (fiş açıklamasında Türkçe büyük harfe çevrilmesin)
        e = self.eski_ekle(yon, self.veri(tip, cari, satirlar, f"ESKX{self._cn}", **kw))
        y = self.yeni_ekle(yon, self.veri(tip, cari, satirlar, f"YENX{self._cn}", **kw))
        return self.esit(ad, e, y)

    def stok_girisi(self, miktar="500", fiyat="50"):
        self._kes([self._stok(miktar, fiyat)], no=f"GIRIS-{self._sayac if hasattr(self, '_sayac') else 0}")


class EkleAkisiFisKarsilastirmaTest(KarsilastirmaTaban):
    def test_normal_alis_stok(self):
        self.assertGreater(self.cift("normal-alis-stok", "ALIS", self.alis, self.tedarikci, [self.stok_satir("10", "100")]), 2)

    def test_karma_tip_stok_ve_hesap(self):
        self.assertGreater(self.cift("karma-tip5-stok+hesap", "ALIS", self.alis, self.tedarikci, [self.stok_satir("40000", "0,11"), self.hesap_satir("730.09", "881,48")]), 3)

    def test_gider(self):
        self.cift("gider", "ALIS", self.gider_tip, self.tedarikci, [self.hesap_satir("730.09", "1000"), self.hesap_satir("770.03", "250,5")], depo=False)

    def test_yalniz_kdv_tip10(self):
        n = self.cift("yalniz-kdv-tip10", "ALIS", self.yalniz_kdv, self.tedarikci, [self.stok_satir("10", "100")], depo=False)
        self.assertEqual(n, 2)                                                                        # 191 B + cari A (matrah fişe yazılmaz)

    def test_smm_stopajli(self):
        self.cift("smm-stopajli", "ALIS", self.smm, self.tedarikci, [self.hesap_satir("770.03", "5000")], depo=False, gv_stopaj_orani="20")

    def test_ihrac_kayitli_karma(self):
        self.cift("ihrac-kayitli", "ALIS", self.ihrac, self.tedarikci, [self.stok_satir("5", "200"), self.hesap_satir("730.09", "300")])

    def test_eur_alis(self):
        self.cift("eur-alis", "ALIS", self.alis, self.eur, [self.stok_satir("12", "13,5")], pb="EUR")

    def test_usd_alis(self):
        self.cift("usd-alis", "ALIS", self.alis, self.usd, [self.stok_satir("8", "77,25"), self.hesap_satir("730.09", "100,10")], pb="USD")

    def test_demirbas_yatirim_projesi_258(self):
        self.cift("yatirim-projesi-258", "ALIS", self.gider_tip, self.tedarikci,
                  [self.hesap_satir("258", "100000", yatirim_projesi=self.proje.pk)], depo=False)

    def test_tevkifatli_satis(self):
        self.stok_girisi("500", "50")
        from core.models import Stok
        Stok.objects.filter(pk=self.stok.pk).update(tevkifat=self.tev)                                 # stok kartında tevkifat: satırda "stok kartından gelsin"
        self.cift("tevkifatli-satis", "SATIS", self.satis, self.musteri, [self.stok_satir("20", "120")])

    def test_trendyol_tipi_satis_maliyet_fisi(self):
        self.stok_girisi("500", "50")
        n = self.cift("trendyol-satis(maliyet-fisi)", "SATIS", self.satis, self.trendyol, [self.stok_satir("30", "99,9")])
        self.assertGreaterEqual(n, 3)

    def test_satis_iadesi_ve_alis_iadesi(self):
        self.stok_girisi("500", "50")
        self.cift("satis-iadesi", "ALIS", self.satis_iade, self.musteri, [self.stok_satir("4", "120")])
        self.cift("alis-iadesi", "SATIS", self.alis_iade, self.tedarikci, [self.stok_satir("6", "50")])


class DuzenleAkisiFisKarsilastirmaTest(KarsilastirmaTaban):
    def test_irsaliyeden_taslak_duzenle_onay(self):
        sonuc = {}
        for etiket, duz in (("ESKI", self.eski_duzenle), ("YENI", self.yeni_duzenle)):
            irsaliye = teklif_siparis_olustur(belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), depo_id=self.depo.pk,
                                              satirlar=[{"stok_id": self.stok.pk, "miktar": "40000", "birim_fiyat": "0.11"}])
            teklif_siparis_onayla(irsaliye, kullanici=self.su)
            irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.su)
            irsaliye.refresh_from_db()
            taslak = irsaliye.fatura
            self.assertEqual((taslak.durum, taslak.tip_id), ("TASLAK", None))
            veri = self.veri(self.alis, self.tedarikci, [self.stok_satir("40000", "0,11"), self.hesap_satir("730.09", "881,48")], f"{etiket[:3]}X-IRS")
            taslak = duz(taslak, veri)                                                                # tip seçildi + hesap satırı eklendi (düzenle ekranı)
            self.assertEqual(taslak.durum, "TASLAK")
            fatura_onayla(taslak, kullanici=self.su)                                                  # "Onayla" düğmesi
            taslak.refresh_from_db()
            sonuc[etiket] = taslak
        self.esit("irsaliyeden-taslak-duzenle-onay", sonuc["ESKI"], sonuc["YENI"])

    def test_duzenle_ile_tutar_degistirme_onayli_fatura(self):
        sonuc = {}
        for etiket, duz in (("ESKI", self.eski_duzenle), ("YENI", self.yeni_duzenle)):
            f = self._kes([self._stok("100", "50"), self._hesap_satir("730.09", "200")], no=f"{etiket[:3]}X-DZ")
            self.assertEqual(f.durum, "ONAYLI")
            veri = self.veri(self.alis, self.tedarikci, [self.stok_satir("100", "55,5"), self.hesap_satir("730.09", "333,33")], f"{etiket[:3]}X-DZ")      # fiyat + hesap tutarı değişti
            sonuc[etiket] = duz(f, veri)
        n = self.esit("duzenle-tutar-degistirme(onayli)", sonuc["ESKI"], sonuc["YENI"])
        self.assertGreater(n, 3)
        # eski fiş doğru güncellendi: yeni tutarlar fişte, eski tutarlar yok
        fis = {s.hesap_id: (s.borc, s.alacak) for s in sonuc["YENI"].fis.satirlar.filter(silindi=False)}
        self.assertEqual(fis["320.10.0001"][1], Dc("7060.00"))                                          # cari = (5.550,00 + 333,33) × 1,20 (matrah 5.883,33 + KDV 1.176,67); eski tutarlar fişte kalmadı
        self.assertNotIn(Dc("5000.00"), [v[0] for v in fis.values()] + [v[1] for v in fis.values()])
        self.assertEqual(sonuc["YENI"].fis.satirlar.filter(silindi=False).count(), sonuc["ESKI"].fis.satirlar.filter(silindi=False).count())
