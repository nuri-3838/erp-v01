"""Hata düzeltmeleri: (1) taslak faturada elle girilen kur saklanır ve onayda korunur, (2) irsaliye →
fatura taslağında birim fiyat tam (6 hane) hassasiyetle taşınır, (3) vade fatura tarihine göre
hesaplanır (bugüne göre değil), alış faturası ekranı vadeyi cari/tarih değişince yeniden hesaplar."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import Cari, Depo, EkranYetki, Fatura
from core.services.fatura import fatura_guncelle, fatura_onayla, fatura_taslak_olustur
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_fatura import FaturaTestTemel

D = datetime.date


class TaslakKurTest(FaturaTestTemel):
    def _taslak(self, pb="USD"):
        return fatura_taslak_olustur(
            tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
            satirlar=self._satir(), para_birimi=pb)

    def _duzenle(self, f, kur, pb="USD"):
        fatura_guncelle(f, tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
                        satirlar=self._satir(), para_birimi=pb, kur=kur)

    def test_taslakta_girilen_kur_saklanir_ve_onayda_korunur(self):
        f = self._taslak()
        self._duzenle(f, Decimal("47.4126"))
        f.refresh_from_db()
        self.assertEqual(f.taslak_kur, Decimal("47.412600"))
        fatura_onayla(f)
        f.refresh_from_db()
        self.assertEqual(f.kur, Decimal("47.412600"))              # sistem kuru (30) DEĞİL
        self.assertEqual(f.durum, Fatura.Durum.ONAYLI)

    def test_kur_bossa_onayda_sistem_kuru_atanir(self):
        f = self._taslak()
        self._duzenle(f, None)
        fatura_onayla(f)
        f.refresh_from_db()
        self.assertEqual(f.kur, Decimal("30.000000"))               # Kur tablosundaki 10.03.2026 kuru

    def test_try_faturada_kur_yok_sayilir(self):
        f = self._taslak(pb="TRY")
        self._duzenle(f, Decimal("47"), pb="TRY")
        f.refresh_from_db()
        self.assertIsNone(f.taslak_kur)

    def test_duzenleme_ekrani_taslak_kurunu_gosterir_ve_detay_onayi_korur(self):
        u = User.objects.create_superuser("yon", password="x")
        EkranYetki.objects.create(kullanici=u, ekran_kod="alis_faturalari")
        self.client.force_login(u)
        f = self._taslak()
        self._duzenle(f, Decimal("47.4126"))
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual(r.context["fform"].initial["kur"], Decimal("47.412600"))
        self.client.post(reverse("core:fatura_onayla", args=[f.pk]))
        f.refresh_from_db()
        self.assertEqual((f.durum, f.kur), (Fatura.Durum.ONAYLI, Decimal("47.412600")))


class IrsaliyeFiyatHassasiyetiTest(FaturaTestTemel):
    def _taslak_fatura(self, birim_fiyat, miktar="1000", iskonto=None):
        depo = Depo.objects.create(kod="D9", ad="DEPO 9")
        satir = {"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": birim_fiyat}
        if iskonto:
            satir["iskonto_yuzdesi"] = iskonto
        irsaliye = teklif_siparis_olustur(
            belge_tur="IRSALIYE", yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
            depo_id=depo.pk, satirlar=[satir])
        teklif_siparis_onayla(irsaliye)
        irsaliye.refresh_from_db()
        return irsaliye.fatura

    def test_birim_fiyat_alti_hane_tasinir(self):
        f = self._taslak_fatura("0.476085")
        satir = f.satirlar.get(silindi=False)
        self.assertEqual(satir.birim_fiyat, Decimal("0.476085"))      # 0,4761 DEĞİL
        self.assertEqual(satir.tutar, Decimal("476.09"))               # 1000 x 0,476085 (4 haneyle 476,10 çıkardı)

    def test_iskontolu_net_fiyat_alti_haneyle_tasinir(self):
        f = self._taslak_fatura("0.476085", iskonto="10")
        satir = f.satirlar.get(silindi=False)
        self.assertEqual(satir.birim_fiyat, Decimal("0.428477"))      # 0,476085 x 0,9 = 0,4284765
        self.assertEqual(satir.tutar, Decimal("428.48"))


class VadeFaturaTarihineGoreTest(FaturaTestTemel):
    def setUp(self):
        super().setUp()
        self.u = User.objects.create_user("u", password="x")
        self.client.force_login(self.u)
        Cari.objects.filter(pk=self.tedarikci.pk).update(
            odeme_kosulu=Cari.OdemeKosulu.SONRAKI_AY_GUNU, odeme_gunu=25)

    def _vade(self, tarih):
        r = self.client.get(reverse("core:cari_vade_api", args=[self.tedarikci.pk]), {"tarih": tarih})
        return r.json()["vade_tarihi"]

    def test_vade_verilen_fatura_tarihine_gore_hesaplanir(self):
        self.assertEqual(self._vade("2026-09-23"), "2026-10-25")      # ayı izleyen ayın 25'i (bugüne göre 25.11 DEĞİL)
        self.assertEqual(self._vade("2026-12-05"), "2027-01-25")      # yıl sonu geçişi

    def test_alis_fatura_ekrani_vadeyi_cari_ve_tarih_degisince_yeniden_hesaplar(self):
        EkranYetki.objects.create(kullanici=self.u, ekran_kod="alis_faturalari")
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertContains(r, "vadeIstekNo")                          # eski istek yanıtı yok sayılır
        self.assertContains(r, "vadeUrl")
        self.assertContains(r, "tarihInp.addEventListener('change', vadeDoldur)")
