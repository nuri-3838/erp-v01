"""Serbest meslek makbuzu (GV stopajlı alış-gider tipi) + fatura düzenleme ekranında gider
hesabı/yatırım projesinin kayıtlı değerlerle gelmesi. Bkz. FaturaTipi.stopajli,
Fatura.gv_stopaj_orani, core.services.fatura (_hazirla/_muhasebe_satirlari)."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from core.models import (Cari, EkranYetki, FaturaTipi, HesapPlani, KdvOrani, Kur,
                         TevkifatOrani, YevmiyeSatir)
from core.services.fatura import FaturaHatasi, fatura_guncelle, fatura_olustur
from core.services.yatirim_projesi import proje_olustur

D = datetime.date


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup,
                                     rapor_kalemi=kalem, parasal=True, aktif=True)


class SmmTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        Kur.objects.create(tarih=D(2025, 12, 9), usd_alis=Decimal("40"))
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        _hesap("258", "YAPILMAKTA OLAN YATIRIMLAR")
        _hesap("360.20", "ÖDENECEK GV STOPAJI", kalem="KVYK")
        _hesap("320.30.0030", "İBRAHİM ÖZTÜRK", kalem="KVYK")
        cls.tev_hesap = _hesap("360.10.0210", "2/10 TEVKİFAT", kalem="KVYK")
        cls.kdv20 = KdvOrani.objects.create(
            aciklama="GENEL", oran=Decimal("20"),
            hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
            hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        cls.gider = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-GİDER", yon=FaturaTipi.Yon.ALIS, gider=True)
        cls.smm = FaturaTipi.objects.create(
            ad="SERBEST MESLEK MAKBUZU (SMM)", yon=FaturaTipi.Yon.ALIS, gider=True,
            stopajli=True)
        cls.cari = Cari.objects.create(kod="320-30-0030", unvan="İBRAHİM ÖZTÜRK",
                                       para_birimi="TRY", muhasebe_kodu="320.30.0030")
        cls.u = User.objects.create_superuser("yon", password="x")
        cls.proje = proje_olustur(ad="hat", kullanici=cls.u)

    def _satir(self, **ek):
        s = {"hesap_id": "258", "miktar": "1", "birim_fiyat": "4473.32",
             "kdv_id": self.kdv20.pk, "yatirim_projesi_id": self.proje.pk}
        s.update(ek)
        return [s]

    def _kes(self, tip, **kw):
        veri = dict(tip_id=tip.pk, cari_id=self.cari.pk, tarih=D(2025, 12, 9),
                    fatura_no="ESMM-1", satirlar=self._satir(), kullanici=self.u)
        veri.update(kw)
        return fatura_olustur(**veri)

    def _fis(self, f):
        return {s.hesap_id: (s.borc, s.alacak) for s in f.fis.satirlar.filter(silindi=False)}


class SmmFisTest(SmmTemel):
    def test_smm_fisi_kullanici_ornegiyle_birebir(self):
        f = self._kes(self.smm, gv_stopaj_orani=Decimal("20"))
        sat = self._fis(f)
        self.assertEqual(sat["258"], (Decimal("4473.32"), Decimal("0.00")))
        self.assertEqual(sat["191"], (Decimal("894.66"), Decimal("0.00")))
        self.assertEqual(sat["360.20"], (Decimal("0.00"), Decimal("894.66")))
        self.assertEqual(sat["320.30.0030"], (Decimal("0.00"), Decimal("4473.32")))
        self.assertEqual(f.odenecek, Decimal("4473.32"))
        bakiye = YevmiyeSatir.objects.filter(hesap_id="320.30.0030", silindi=False).aggregate(
            a=Sum("alacak"), b=Sum("borc"))
        self.assertEqual(bakiye["a"] - (bakiye["b"] or 0), Decimal("4473.32"))
        self.assertEqual(f.gv_stopaj_orani, Decimal("20.00"))

    def test_stopaj_orani_zorunlu_ve_siniri(self):
        with self.assertRaises(FaturaHatasi):
            self._kes(self.smm, gv_stopaj_orani=None)
        with self.assertRaises(FaturaHatasi):
            self._kes(self.smm, gv_stopaj_orani=Decimal("101"))

    def test_tevkifat_stopajla_birlikte_calisir(self):
        tev = TevkifatOrani.objects.create(kod="2/10-01", pay=2, payda=10, hesap=self.tev_hesap)
        f = self._kes(self.smm, gv_stopaj_orani=Decimal("20"),
                      satirlar=self._satir(birim_fiyat="1000", tevkifat_id=tev.pk))
        sat = self._fis(f)
        self.assertEqual(sat["191"], (Decimal("200.00"), Decimal("0.00")))       # tam KDV
        self.assertEqual(sat["360.10.0210"], (Decimal("0.00"), Decimal("40.00")))  # tevkifat
        self.assertEqual(sat["360.20"], (Decimal("0.00"), Decimal("200.00")))    # stopaj
        # cari = 1000 + (200 − 40) − 200 = 960
        self.assertEqual(sat["320.30.0030"], (Decimal("0.00"), Decimal("960.00")))
        tb = sum(b for b, _ in sat.values())
        ta = sum(a for _, a in sat.values())
        self.assertEqual(tb, ta)

    def test_mevcut_gider_faturasi_smm_tipine_cevrilir(self):
        f = self._kes(self.gider, fatura_no="ESMM-155")
        self.assertNotIn("360.20", self._fis(f))
        self.assertEqual(self._fis(f)["320.30.0030"], (Decimal("0.00"), Decimal("5367.98")))
        eski_fis = f.fis_id
        fatura_guncelle(
            f, tip_id=self.smm.pk, cari_id=self.cari.pk, tarih=D(2025, 12, 9),
            fatura_no="ESMM-155", satirlar=self._satir(),
            gv_stopaj_orani=Decimal("20"), kullanici=self.u)
        f.refresh_from_db()
        self.assertEqual(f.fis_id, eski_fis)          # fiş yerinde yenilendi
        self.assertEqual(f.tip_id, self.smm.pk)
        self.assertEqual(self._fis(f)["320.30.0030"], (Decimal("0.00"), Decimal("4473.32")))
        self.assertEqual(self._fis(f)["360.20"], (Decimal("0.00"), Decimal("894.66")))

    def test_normal_gider_faturasi_etkilenmez(self):
        f = self._kes(self.gider, gv_stopaj_orani=Decimal("20"))   # tip stopajsız -> yok sayılır
        self.assertIsNone(f.gv_stopaj_orani)
        self.assertNotIn("360.20", self._fis(f))


class DuzenlemeEkraniTest(SmmTemel):
    def test_gider_hesabi_ve_proje_kayitli_degerle_gelir(self):
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="alis_faturalari")
        f = self._kes(self.smm, gv_stopaj_orani=Decimal("20"))
        self.client.force_login(self.u)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual(r.status_code, 200)
        ilk = r.context["formset"].forms[0].initial
        self.assertEqual(ilk["hesap"], "258")
        self.assertEqual(ilk["yatirim_projesi"], self.proje.pk)
        self.assertEqual(r.context["fform"].initial["gv_stopaj_orani"], Decimal("20.00"))
        self.assertContains(r, 'id="id_form-0-hesap"')
        # JS init hesabı silmemeli: şahsi değilken temizleme yalnız açık->kapalı geçişinde
        self.assertContains(r, "else if(onceki)")
