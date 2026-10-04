"""Kur farkı motoru: döviz hesaplarında hareketli ağırlıklı ortalama kur; çıkışta ortalama kurla
yazım + 646/656 kur farkı satırı; sıfırlanan döviz bakiyesinde TL bakiye tam sıfır; kısmi çıkış;
bakiye işaret değiştirmesi; cari fatura/ödeme kur farkı; geriye dönük giriş/silme/düzenleme."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Kur, YevmiyeFisi, YevmiyeSatir
from core.services import fis_sil as fs
from core.services import kur_farki as kf
from core.services.finans import banka_hesap_olustur, banka_olustur
from core.services.yevmiye import SatirGirdi, fis_guncelle, fis_olustur
from core.tests.test_banka_hesap_hareketi import _hesap

D = datetime.date
Dc = Decimal


def _g(hesap, taraf, dvz, kur, pb="USD"):
    return SatirGirdi(hesap_kodu=hesap, taraf=taraf, islem_tutari=dvz, islem_pb=pb, islem_kuru=kur)


class SafHavuzTest(TestCase):
    """havuz_hesapla saf fonksiyonu (DB'siz)."""

    def _gir(self, yon, dvz, ham, pk=None):
        return kf.GirdiSatir(pk=pk, yon=yon, dvz=Dc(dvz), ham=Dc(ham))

    def test_ortalama_ve_cikis(self):
        sonuc, q, v = kf.havuz_hesapla([
            self._gir(+1, 1000, 40000, 1), self._gir(+1, 1000, 50000, 2),
            self._gir(-1, 500, 24000, 3)])        # 500 USD @48 satış → ortalama 45
        self.assertEqual(sonuc[2].yeni_tl, Dc("22500.00"))
        self.assertEqual(sonuc[2].ort_kur, Dc("45"))
        self.assertEqual(sonuc[2].fark, Dc("1500.00"))         # alacak azaldı → kâr
        self.assertEqual((q, v), (Dc("1500"), Dc("67500.00")))

    def test_son_cikis_tl_bakiyeyi_tam_sifirlar(self):
        sonuc, q, v = kf.havuz_hesapla([
            self._gir(+1, 3, 100, 1),                 # ort 33,3333…
            self._gir(-1, 1, 40, 2), self._gir(-1, 2, 90, 3)])
        self.assertEqual(sonuc[1].yeni_tl, Dc("33.33"))
        self.assertEqual(sonuc[2].yeni_tl, Dc("66.67"))        # kalan TL'nin tamamı (yuvarlama farkı dahil)
        self.assertEqual((q, v), (Dc("0"), Dc("0.00")))

    def test_bakiye_isaret_degistirir(self):
        sonuc, q, v = kf.havuz_hesapla([
            self._gir(+1, 100, 4000, 1),
            self._gir(-1, 150, 7500, 2)])             # 100'ü kapatır (ort 40), 50'si yeni (alacak) havuz
        self.assertEqual(sonuc[1].yeni_tl, Dc("4000.00") + Dc("2500.00"))
        self.assertEqual(sonuc[1].fark, Dc("7500") - Dc("6500"))
        self.assertEqual((q, v), (Dc("-50"), Dc("-2500.00")))

    def test_alacak_bakiyeli_hesapta_alacak_giris_borc_cikistir(self):
        # cari: fatura (alacak) 1000 USD @40, ödeme (borç) 1000 USD @45
        sonuc, q, v = kf.havuz_hesapla([self._gir(-1, 1000, 40000, 1), self._gir(+1, 1000, 45000, 2)])
        self.assertEqual(sonuc[1].yeni_tl, Dc("40000.00"))     # borç, fatura kuruyla kapanır
        self.assertEqual(sonuc[1].fark, Dc("5000.00"))          # borç satırı azaldı → zarar
        self.assertEqual((q, v), (Dc("0"), Dc("0.00")))

    def test_degerleme_satiri_yalniz_v_degistirir(self):
        deg = kf.GirdiSatir(pk=9, yon=+1, dvz=Dc("0"), ham=Dc("500.00"), degerleme=True)
        sonuc, q, v = kf.havuz_hesapla([self._gir(+1, 100, 4000, 1), deg, self._gir(-1, 100, 5000, 2)])
        self.assertEqual((q, v), (Dc("0"), Dc("0.00")))
        self.assertEqual(sonuc[2].yeni_tl, Dc("4500.00"))      # ortalama değerleme sonrası 45


class MotorTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        for g in (D(2026, 1, 5), D(2026, 2, 5), D(2026, 3, 5), D(2026, 4, 5)):
            Kur.objects.create(tarih=g, usd_alis=Dc("40"))
        _hesap("102.02.0001", "USD BANKA")
        _hesap("102.01.0001", "TL BANKA")
        _hesap("131.01", "ORTAK")
        _hesap("320.01", "SATICI")
        _hesap("646", "KAMBİYO KÂRLARI", kalem="E", grup="GELIR_TABLOSU")
        _hesap("656", "KAMBİYO ZARARLARI (-)", kalem="F", grup="GELIR_TABLOSU")
        b = banka_olustur(ad="b")
        cls.usd = banka_hesap_olustur(banka=b, ad="usd", para_birimi="USD", muhasebe_kodu="102.02.0001")
        cls.tl = banka_hesap_olustur(banka=b, ad="tl", para_birimi="TRY", muhasebe_kodu="102.01.0001")
        cls.su = User.objects.create_superuser("kf", password="x")

    def _giris(self, tarih, dvz, kur):
        """USD banka borç (giriş), ortak alacak (aynı kur)."""
        return fis_olustur(tarih=tarih, satirlar=[_g("102.02.0001", "B", dvz, kur), _g("131.01", "A", dvz, kur)])

    def _cikis(self, tarih, dvz, kur):
        """USD banka alacak (çıkış), ortak borç (işlem kuruyla)."""
        return fis_olustur(tarih=tarih, satirlar=[_g("131.01", "B", dvz, kur), _g("102.02.0001", "A", dvz, kur)])

    def _bakiye(self, kod="102.02.0001"):
        return kf.havuz_bakiyesi(kod, "USD")

    def _kur_farki(self, fis):
        return {s.hesap_id: (s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False, ana_satir__isnull=False)}

    def test_alis_satis_farkli_kurlar_kar(self):
        self._giris(D(2026, 1, 5), "1000", "40")
        self._giris(D(2026, 2, 5), "1000", "50")                 # ortalama 45
        f = self._cikis(D(2026, 3, 5), "500", "48")              # 500 USD @48 → kâr 1.500
        banka = f.satirlar.get(hesap_id="102.02.0001")
        self.assertEqual((banka.borc, banka.alacak, banka.ort_kur, banka.ham_tl),
                         (Dc("0.00"), Dc("22500.00"), Dc("45.000000"), Dc("24000.00")))
        karsi = f.satirlar.get(hesap_id="131.01")
        self.assertEqual(karsi.borc, Dc("24000.00"))              # karşı taraf işlem kuruyla
        self.assertEqual(self._kur_farki(f), {"646": (Dc("0.00"), Dc("1500.00"))})
        self.assertEqual(sum(s.borc for s in f.satirlar.all()), sum(s.alacak for s in f.satirlar.all()))
        self.assertEqual(self._bakiye(), (Dc("1500.00"), Dc("67500.00")))

    def test_zarar_656(self):
        self._giris(D(2026, 1, 5), "1000", "50")
        f = self._cikis(D(2026, 2, 5), "400", "40")              # ortalama 50, satış 40 → zarar 4.000
        self.assertEqual(self._kur_farki(f), {"656": (Dc("4000.00"), Dc("0.00"))})

    def test_kismi_cikis_ve_sifirlanan_bakiyede_tl_sifir(self):
        self._giris(D(2026, 1, 5), "1000", "40")
        self._giris(D(2026, 1, 5), "2000", "43")                 # ortalama 42
        self._cikis(D(2026, 2, 5), "1000", "45")                 # kısmi
        q, v = self._bakiye()
        self.assertEqual((q, v), (Dc("2000.00"), Dc("84000.00")))
        f = self._cikis(D(2026, 3, 5), "2000", "41")             # kalan tamamı
        self.assertEqual(self._bakiye(), (Dc("0.00"), Dc("0.00")))   # döviz 0 → TL tam 0
        self.assertEqual(self._kur_farki(f), {"656": (Dc("2000.00"), Dc("0.00"))})

    def test_yuvarlama_farki_son_cikista_kur_farkina_gider(self):
        self._giris(D(2026, 1, 5), "3", "33.3333")               # 99.9999 -> 100.00
        self._cikis(D(2026, 2, 5), "1", "40")
        self._cikis(D(2026, 3, 5), "2", "40")
        self.assertEqual(self._bakiye(), (Dc("0.00"), Dc("0.00")))

    def test_cari_fatura_odeme_kur_farki_ve_tl_kalinti_kalmaz(self):
        # Satıcı (320) USD: fatura 1000 USD @40 (alacak), ödeme 1000 USD @45 (borç) — banka TL hesabından
        fis_olustur(tarih=D(2026, 1, 5), satirlar=[
            _g("131.01", "B", "1000", "40"), _g("320.01", "A", "1000", "40")])
        f = fis_olustur(tarih=D(2026, 2, 5), satirlar=[
            _g("320.01", "B", "1000", "45"), SatirGirdi(hesap_kodu="102.01.0001", taraf="A", islem_tutari="45000")])
        cari = f.satirlar.get(hesap_id="320.01")
        self.assertEqual(cari.borc, Dc("40000.00"))              # fatura (ortalama) kuruyla kapanır
        self.assertEqual(self._kur_farki(f), {"656": (Dc("5000.00"), Dc("0.00"))})   # 45 > 40 → zarar
        self.assertEqual(kf.havuz_bakiyesi("320.01", "USD"), (Dc("0.00"), Dc("0.00")))

    def test_tl_hesap_dovizli_satir_havuz_olusturmaz(self):
        f = fis_olustur(tarih=D(2026, 1, 5), satirlar=[
            _g("102.01.0001", "B", "100", "40"), _g("131.01", "A", "100", "40")])    # TL banka, USD satır
        self.assertEqual(kf.etkilenen_havuzlar(f), set())

    def test_geriye_donuk_giris_sonraki_cikisi_yeniden_hesaplar(self):
        self._giris(D(2026, 2, 5), "1000", "50")
        f = self._cikis(D(2026, 4, 5), "1000", "52")             # ort 50 → kâr 2.000
        self.assertEqual(self._kur_farki(f), {"646": (Dc("0.00"), Dc("2000.00"))})
        self._giris(D(2026, 1, 5), "1000", "40")                 # GERİYE DÖNÜK: ortalama 45 olur
        q, v = self._bakiye()
        self.assertEqual((q, v), (Dc("1000.00"), Dc("45000.00")))
        f2 = YevmiyeFisi.objects.get(pk=f.pk)
        self.assertEqual(self._kur_farki(f2), {"646": (Dc("0.00"), Dc("7000.00"))})   # 52000 − 45000

    def test_fis_silince_kur_farki_yeniden_hesaplanir(self):
        g1 = self._giris(D(2026, 1, 5), "1000", "40")
        self._giris(D(2026, 2, 5), "1000", "50")
        f = self._cikis(D(2026, 3, 5), "1000", "50")             # ort 45 → kâr 5.000
        self.assertEqual(self._kur_farki(f), {"646": (Dc("0.00"), Dc("5000.00"))})
        fs.fis_sil(g1, kullanici=self.su)                        # ilk giriş silindi → ort 50 → kâr 0
        f.refresh_from_db()
        self.assertEqual(self._kur_farki(f), {})
        self.assertFalse(YevmiyeSatir.objects.filter(hesap_id="646", ana_satir__isnull=False).exists())

    def test_duzenleme_kur_farkini_yeniden_hesaplar_ve_formda_gorunmez(self):
        self._giris(D(2026, 1, 5), "1000", "40")
        f = self._cikis(D(2026, 2, 5), "1000", "42")             # kâr 2.000
        fis_guncelle(f, tarih=D(2026, 2, 5), satirlar=[_g("131.01", "B", "1000", "47"),
                                                       _g("102.02.0001", "A", "1000", "47")])
        self.assertEqual(self._kur_farki(f), {"646": (Dc("0.00"), Dc("7000.00"))})
        self.assertEqual(f.satirlar.filter(silindi=False).count(), 3)      # 2 ana + 1 kur farkı
        self.assertEqual(self._bakiye(), (Dc("0.00"), Dc("0.00")))

    def test_motor_idempotent(self):
        self._giris(D(2026, 1, 5), "1000", "40")
        f = self._cikis(D(2026, 2, 5), "300", "45")
        once = list(YevmiyeSatir.objects.order_by("id").values_list("id", "borc", "alacak"))
        kf.havuz_yeniden_hesapla("102.02.0001", "USD")
        kf.havuz_yeniden_hesapla("102.02.0001", "USD")
        self.assertEqual(once, list(YevmiyeSatir.objects.order_by("id").values_list("id", "borc", "alacak")))

    def test_virman_cikis_ortalamayla_giris_islem_kuruyla(self):
        _hesap("102.02.0002", "USD BANKA 2")
        banka_hesap_olustur(banka=self.usd.banka, ad="usd2", para_birimi="USD", muhasebe_kodu="102.02.0002")
        self._giris(D(2026, 1, 5), "1000", "40")
        f = fis_olustur(tarih=D(2026, 2, 5), satirlar=[_g("102.02.0002", "B", "1000", "44"),
                                                       _g("102.02.0001", "A", "1000", "44")])
        self.assertEqual(f.satirlar.get(hesap_id="102.02.0001").alacak, Dc("40000.00"))   # ortalama
        self.assertEqual(f.satirlar.get(hesap_id="102.02.0002").borc, Dc("44000.00"))     # işlem kuru
        self.assertEqual(self._kur_farki(f), {"646": (Dc("0.00"), Dc("4000.00"))})


class CariKartiTest(TestCase):
    def test_doviz_cari_kartinda_ortalama_kur_yontemi_gorunur(self):
        from django.urls import reverse
        from core.models import Cari
        su = User.objects.create_superuser("ck", password="x")
        usd = Cari.objects.create(kod="C-USD", unvan="DÖVİZ CARİ", para_birimi="USD", created_by=su, updated_by=su)
        tl = Cari.objects.create(kod="C-TL", unvan="TL CARİ", para_birimi="TRY", created_by=su, updated_by=su)
        self.client.force_login(su)
        self.assertContains(self.client.get(reverse("core:cari_detay", args=[usd.pk])), "Ortalama kur (hareketli ağırlıklı)")
        self.assertNotContains(self.client.get(reverse("core:cari_detay", args=[tl.pk])), "Ortalama kur (hareketli ağırlıklı)")
