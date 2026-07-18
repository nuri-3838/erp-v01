"""FİNANS — Kasa tanımı servis + view testleri. Bakiye saklanmaz; yaprak muhasebe
hesabına bağlanır (üst hesap reddedilir), ad TR büyük + benzersiz."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import HesapPlani, Kasa
from core.services.finans import FinansHatasi, kasa_guncelle, kasa_olustur, kasa_sil


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class KasaServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _hesap("100", "KASA")             # üst (yaprak değil)
        _hesap("100.01", "MERKEZ KASA")   # yaprak
        _hesap("100.02", "DÖVİZ KASA")    # yaprak

    def test_olustur_tr_buyuk_ve_hesap(self):
        k = kasa_olustur(ad="merkez kasa", para_birimi="TRY", muhasebe_kodu="100.01")
        self.assertEqual(k.ad, "MERKEZ KASA")
        self.assertEqual(k.muhasebe.hesap_kodu, "100.01")

    def test_ust_hesap_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="X", muhasebe_kodu="100")

    def test_hesapsiz_reddedilir(self):
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="Y", muhasebe_kodu="")

    def test_ad_benzersiz(self):
        kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="ana", muhasebe_kodu="100.02")

    def test_guncelle_ve_sil(self):
        k = kasa_olustur(ad="ANA", muhasebe_kodu="100.01")
        kasa_guncelle(k, ad="ANA2", para_birimi="USD", muhasebe_kodu="100.02")
        k.refresh_from_db()
        self.assertEqual((k.ad, k.para_birimi, k.muhasebe.hesap_kodu), ("ANA2", "USD", "100.02"))
        kasa_sil(k)
        k.refresh_from_db()
        self.assertTrue(k.silindi)

    def test_bagli_hesap_silinemez(self):
        """Kasa bağlı bir muhasebe hesabı soft-delete edilemez (öksüz kalmasın)."""
        from core.services.hesap_plani import HesapHatasi, hesap_sil
        kasa_olustur(ad="ANA KASA", muhasebe_kodu="100.01")
        with self.assertRaises(HesapHatasi):
            hesap_sil(kod="100.01")

    def test_yaprak_olmayan_hesap_duzenlemede_kalir(self):
        """100.01'e alt hesap eklenince yaprak olmaktan çıkar; düzenleme formu yine de
        bağlı hesabı listede tutmalı (yoksa kayıt düzenlenemez). Ekleme formu tutmaz."""
        from core.forms import KasaForm
        from core.services.hesap_plani import yaprak_hesaplar
        _hesap("100.01.0001", "ALT")          # 100.01 artık yaprak değil
        self.assertFalse(yaprak_hesaplar().filter(hesap_kodu="100.01").exists())
        duz = KasaForm(mevcut_hesap="100.01")
        self.assertTrue(duz.fields["muhasebe"].queryset.filter(hesap_kodu="100.01").exists())
        ekle = KasaForm()
        self.assertFalse(ekle.fields["muhasebe"].queryset.filter(hesap_kodu="100.01").exists())


class KasaViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="x")
        cls.bos = User.objects.create_user("bos", password="x")
        _hesap("100", "KASA")
        _hesap("100.01", "MERKEZ KASA")

    def test_ekle_post(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kasa_ekle"),
                             {"ad": "merkez", "para_birimi": "TRY", "muhasebe": "100.01"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Kasa.objects.filter(ad="MERKEZ", muhasebe__hesap_kodu="100.01").exists())

    def test_liste_200_ve_yetkisiz_403(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 200)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kasalar")).status_code, 403)

    def test_detay_ekstre_ve_hareket_menu(self):
        k = kasa_olustur(ad="ANA KASA", muhasebe_kodu="100.01")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kasa_detay", args=[k.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Kasa Ekstresi")        # ekstre bölümü
        self.assertContains(r, 'name="aciklama"')      # açıklama filtresi
        for ad in ("Cari Tahsilat", "Cari Ödeme", "Banka Yatan", "Banka Çekilen", "Kasa Virman"):
            self.assertContains(r, ad)                 # 5 hareket aksiyonu
        self.assertEqual(self.client.get(
            reverse("core:kasa_hareket_ekle", args=[k.pk, "cari_tahsilat"])).status_code, 200)
        self.assertEqual(self.client.get(            # geçersiz tip → kasa detaya redirect
            reverse("core:kasa_hareket_ekle", args=[k.pk, "xyz"])).status_code, 302)

    def test_detay_yetkisiz_403(self):
        k = kasa_olustur(ad="ANA KASA", muhasebe_kodu="100.01")
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:kasa_detay", args=[k.pk])).status_code, 403)

    def test_ekstre_aciklama_filtresi(self):
        """Açıklama filtresi yalnız eşleşen hareketleri gösterir; filtresiz hepsi."""
        from decimal import Decimal
        from django.utils import timezone
        from core.services.yevmiye import fis_olustur, SatirGirdi
        _hesap("120.01", "ALICI")
        k = kasa_olustur(ad="ANA KASA", muhasebe_kodu="100.01")
        t = timezone.localdate()
        for ack, tutar in (("KİRA ÖDEMESİ", "1000"), ("MAL SATIŞI", "2000")):
            fis_olustur(tarih=t, aciklama=ack, kur_usd=Decimal("40"), satirlar=[
                SatirGirdi(hesap_kodu="100.01", taraf="B", islem_tutari=Decimal(tutar)),
                SatirGirdi(hesap_kodu="120.01", taraf="A", islem_tutari=Decimal(tutar))])
        self.client.force_login(self.yon)
        url = reverse("core:kasa_detay", args=[k.pk])
        r = self.client.get(url, {"baslangic": t.isoformat(), "bitis": t.isoformat(),
                                  "aciklama": "kira"})   # TR büyük-harf duyarsız (kira→KİRA)
        self.assertContains(r, "KİRA ÖDEMESİ")
        self.assertNotContains(r, "MAL SATIŞI")
        r2 = self.client.get(url)                        # filtresiz (varsayılan son 1 ay)
        self.assertContains(r2, "KİRA ÖDEMESİ")
        self.assertContains(r2, "MAL SATIŞI")


class KasaHareketTest(TestCase):
    """KASA hareket motoru (Slice 2) — Cari Tahsilat → otomatik dengeli fiş (kaynak=KASA)."""

    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from django.utils import timezone
        from core.models import Kur
        cls.yon = User.objects.create_superuser("yonk", password="x")
        cls.bos = User.objects.create_user("bosk", password="x")
        _hesap("100", "KASA")
        _hesap("100.01", "MERKEZ KASA")
        _hesap("100.02", "YEDEK KASA")
        _hesap("102.01", "İŞ BANKASI VADESİZ")
        _hesap("120.01", "MÜŞTERİ A")
        Kur.objects.create(tarih=timezone.localdate(), usd_alis=Decimal("40"))  # fiş USD kuru zorunlu

    def _kasa_cari(self):
        from core.models import Cari
        k = kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        c = Cari.objects.create(kod="CAR-1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                created_by=self.yon, updated_by=self.yon)
        return k, c

    def test_cari_tahsilat_dengeli_fis(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.models import YevmiyeFisi
        from core.services.kasa_hareket import cari_tahsilat
        k, c = self._kasa_cari()
        fis = cari_tahsilat(kasa=k, cari=c, tutar=Decimal("1000"),
                            tarih=timezone.localdate(), kullanici=self.yon)
        self.assertEqual(fis.kaynak, YevmiyeFisi.Kaynak.KASA)
        self.assertEqual(fis.kasa_id, k.pk)
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}
        self.assertEqual(s["100.01"], (Decimal("1000.00"), Decimal("0.00")))   # kasa borç
        self.assertEqual(s["120.01"], (Decimal("0.00"), Decimal("1000.00")))   # cari alacak

    def test_cari_muhasebesiz_reddedilir(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.models import Cari
        from core.services.kasa_hareket import KasaHareketHatasi, cari_tahsilat
        k = kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        c = Cari.objects.create(kod="CAR-2", unvan="MÜŞTERİ B", muhasebe_kodu="",
                                created_by=self.yon, updated_by=self.yon)
        with self.assertRaises(KasaHareketHatasi):
            cari_tahsilat(kasa=k, cari=c, tutar=Decimal("1"),
                          tarih=timezone.localdate(), kullanici=self.yon)

    def test_tahsilat_view_form_ve_post(self):
        from django.utils import timezone
        from core.models import YevmiyeFisi
        k, c = self._kasa_cari()
        self.client.force_login(self.yon)
        url = reverse("core:kasa_hareket_ekle", args=[k.pk, "cari_tahsilat"])
        g = self.client.get(url)
        self.assertContains(g, "Cari Tahsilat")                 # form açılır
        self.assertContains(g, "select.akilli-sec")            # akıllı arama enhancer dahil
        r = self.client.post(url, {"karsi": c.pk, "tutar": "1.500,00",
                                   "tarih": timezone.localdate().isoformat(), "aciklama": ""})
        self.assertRedirects(r, reverse("core:kasa_detay", args=[k.pk]))
        self.assertTrue(YevmiyeFisi.objects.filter(
            kasa=k, kaynak=YevmiyeFisi.Kaynak.KASA, silindi=False).exists())

    def test_kasa_fisi_ham_ekrandan_kilitli(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import cari_tahsilat
        k, c = self._kasa_cari()
        fis = cari_tahsilat(kasa=k, cari=c, tutar=Decimal("100"),
                            tarih=timezone.localdate(), kullanici=self.yon)
        self.client.force_login(self.yon)
        self.assertRedirects(self.client.get(reverse("core:fis_duzenle", args=[fis.pk])),
                             reverse("core:kasa_detay", args=[k.pk]))     # düzenle kilitli
        self.assertRedirects(self.client.post(reverse("core:fis_iptal", args=[fis.pk])),
                             reverse("core:kasa_detay", args=[k.pk]))     # ham iptal kilitli
        fis.refresh_from_db()
        self.assertFalse(fis.silindi)

    def test_hareket_iptal_kasadan(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import cari_tahsilat
        k, c = self._kasa_cari()
        fis = cari_tahsilat(kasa=k, cari=c, tutar=Decimal("100"),
                            tarih=timezone.localdate(), kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:kasa_hareket_iptal", args=[k.pk, fis.pk]))
        self.assertRedirects(r, reverse("core:kasa_detay", args=[k.pk]))
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)

    def test_kasa_fisi_listede_detaya_baglanir(self):
        """Fiş listesinde KASA fişi düzenleme'ye değil DETAY'a bağlanır (kasaya bounce etmez);
        detay açılır + 'kasaya git' notu gösterir."""
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import cari_tahsilat
        k, c = self._kasa_cari()
        fis = cari_tahsilat(kasa=k, cari=c, tutar=Decimal("100"),
                            tarih=timezone.localdate(), kullanici=self.yon)
        self.client.force_login(self.yon)
        lst = self.client.get(reverse("core:fis_listesi"))
        self.assertContains(lst, reverse("core:fis_detay", args=[fis.pk]))      # detaya link
        d = self.client.get(reverse("core:fis_detay", args=[fis.pk]))
        self.assertEqual(d.status_code, 200)                                    # açılır
        self.assertContains(d, "kasa hareketinden")                            # bilgi notu
        self.assertContains(d, reverse("core:kasa_detay", args=[k.pk]))         # kasaya git

    # --- Slice 3: kalan 4 tip (ortak motor) ---
    def _banka_hesap(self, pb="TRY"):
        from core.models import Banka, BankaHesap
        b = Banka.objects.create(ad="İŞ BANKASI", created_by=self.yon, updated_by=self.yon)
        return BankaHesap.objects.create(banka=b, ad="VADESİZ", muhasebe_id="102.01",
                                         para_birimi=pb, created_by=self.yon, updated_by=self.yon)

    def _kasa2(self, pb="TRY"):
        return kasa_olustur(ad="YEDEK KASA", para_birimi=pb, muhasebe_kodu="100.02")

    def test_cari_odeme_kasa_alacak(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import hareket_olustur
        k, c = self._kasa_cari()
        fis = hareket_olustur(kasa=k, tip="cari_odeme", karsi=c, tutar=Decimal("1000"),
                              tarih=timezone.localdate(), kullanici=self.yon)
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}
        self.assertEqual(s["100.01"], (Decimal("0.00"), Decimal("1000.00")))   # kasa alacak (çıkış)
        self.assertEqual(s["120.01"], (Decimal("1000.00"), Decimal("0.00")))   # cari borç

    def test_banka_yatan_ve_cekilen(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import hareket_olustur
        k = kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        bh = self._banka_hesap()
        f1 = hareket_olustur(kasa=k, tip="banka_yatan", karsi=bh, tutar=Decimal("500"),
                             tarih=timezone.localdate(), kullanici=self.yon)
        s1 = {x.hesap_id: (x.borc, x.alacak) for x in f1.satirlar.filter(silindi=False)}
        self.assertEqual(s1["100.01"], (Decimal("0.00"), Decimal("500.00")))   # kasa alacak
        self.assertEqual(s1["102.01"], (Decimal("500.00"), Decimal("0.00")))   # banka borç
        f2 = hareket_olustur(kasa=k, tip="banka_cekilen", karsi=bh, tutar=Decimal("300"),
                             tarih=timezone.localdate(), kullanici=self.yon)
        s2 = {x.hesap_id: (x.borc, x.alacak) for x in f2.satirlar.filter(silindi=False)}
        self.assertEqual(s2["100.01"], (Decimal("300.00"), Decimal("0.00")))   # kasa borç
        self.assertEqual(s2["102.01"], (Decimal("0.00"), Decimal("300.00")))   # banka alacak

    def test_kasa_virman(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import hareket_olustur
        k = kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        k2 = self._kasa2()
        fis = hareket_olustur(kasa=k, tip="kasa_virman", karsi=k2, tutar=Decimal("700"),
                              tarih=timezone.localdate(), kullanici=self.yon)
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}
        self.assertEqual(s["100.01"], (Decimal("0.00"), Decimal("700.00")))    # kaynak alacak
        self.assertEqual(s["100.02"], (Decimal("700.00"), Decimal("0.00")))    # hedef borç
        self.assertEqual(fis.kasa_id, k.pk)                                    # kaynak kasaya bağlı

    def test_banka_farkli_pb_reddedilir(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.kasa_hareket import hareket_olustur, KasaHareketHatasi
        k = kasa_olustur(ad="MERKEZ KASA", para_birimi="TRY", muhasebe_kodu="100.01")
        bh = self._banka_hesap(pb="USD")
        with self.assertRaises(KasaHareketHatasi):
            hareket_olustur(kasa=k, tip="banka_yatan", karsi=bh, tutar=Decimal("1"),
                            tarih=timezone.localdate(), kullanici=self.yon)

    def test_dort_tipin_formu_acilir(self):
        k, c = self._kasa_cari()
        self._banka_hesap()
        self._kasa2()
        self.client.force_login(self.yon)
        for tip, etiket in (("cari_odeme", "Cari (karşı taraf)"), ("banka_yatan", "Banka Hesabı"),
                            ("banka_cekilen", "Banka Hesabı"), ("kasa_virman", "Hedef Kasa")):
            r = self.client.get(reverse("core:kasa_hareket_ekle", args=[k.pk, tip]))
            self.assertEqual(r.status_code, 200, tip)
            self.assertContains(r, etiket)


class FinansDigerServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("dsyon", password="x")
        _hesap("102.01", "İŞ BANKASI")
        _hesap("300.01", "KREDİ HESABI")
        _hesap("309.01", "KREDİ KARTI")

    def test_banka_kurum_ve_hesap(self):
        from core.services.finans import (banka_hesap_olustur, banka_hesaplari,
                                          banka_olustur)
        b = banka_olustur(ad="akbank", kisa_ad="ak", sube="merkez",
                          swift_kod="akbktris", musteri_no="  12345-Ab1  ")
        self.assertEqual(b.ad, "AKBANK")
        self.assertEqual(b.swift_kod, "AKBKTRIS")
        self.assertEqual(b.musteri_no, "12345-Ab1")          # strip; TR büyük-harf YOK (sistem kimliği)
        h = banka_hesap_olustur(banka=b, ad="tl mevduat", iban="tr12 0006 4000",
                                muhasebe_kodu="102.01")
        self.assertEqual(h.ad, "TL MEVDUAT")
        self.assertEqual(h.iban, "TR1200064000")          # boşluksuz + büyük
        self.assertEqual(h.muhasebe.hesap_kodu, "102.01")
        self.assertEqual(list(banka_hesaplari(b)), [h])

    def test_banka_hesapli_silinemez(self):
        from core.services.finans import (FinansHatasi, banka_hesap_olustur,
                                          banka_olustur, banka_sil)
        b = banka_olustur(ad="garanti")
        banka_hesap_olustur(banka=b, ad="tl", muhasebe_kodu="102.01")
        with self.assertRaises(FinansHatasi):
            banka_sil(b)

    def test_para_birimi_servis_dogrular(self):
        """Geçersiz para birimi servis katmanında reddedilir (UI'a güvenilmez)."""
        from core.services.finans import (FinansHatasi, banka_hesap_olustur,
                                          banka_olustur, kasa_olustur)
        with self.assertRaises(FinansHatasi):
            kasa_olustur(ad="GEÇERSİZ PB", para_birimi="XYZ", muhasebe_kodu="102.01")
        b = banka_olustur(ad="finansbank")
        with self.assertRaises(FinansHatasi):
            banka_hesap_olustur(banka=b, ad="usd", para_birimi="usd",
                                muhasebe_kodu="102.01")   # küçük harf → geçersiz

    def test_kredi_karti_gun_araligi_ve_limit(self):
        from decimal import Decimal
        from core.services.finans import FinansHatasi, kredi_karti_olustur
        with self.assertRaises(FinansHatasi):
            kredi_karti_olustur(ad="X", kesim_gunu=40, muhasebe_kodu="309.01")
        k = kredi_karti_olustur(ad="WORLD", limit="10.000,50", kesim_gunu=15,
                                muhasebe_kodu="309.01")
        self.assertEqual(k.limit, Decimal("10000.50"))
        self.assertEqual(k.kesim_gunu, 15)

    def test_kredi_olustur(self):
        from decimal import Decimal
        from core.services.finans import kredi_olustur
        k = kredi_olustur(ad="TAŞIT", anapara="250.000", faiz_orani="3,75",
                          muhasebe_kodu="300.01")
        self.assertEqual(k.anapara, Decimal("250000"))
        self.assertEqual(k.faiz_orani, Decimal("3.75"))


class FinansDigerViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("fyon", password="x")
        _hesap("102.01", "İŞ BANKASI")

    def test_listeler_200(self):
        self.client.force_login(self.yon)
        for ad in ("bankalar", "kredi_kartlari", "krediler"):
            self.assertEqual(self.client.get(reverse("core:" + ad)).status_code, 200)

    def test_banka_kurum_ve_hesap_post(self):
        from core.models import Banka, BankaHesap
        from core.services.finans import banka_olustur
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:banka_kurum_ekle"),
                             {"ad": "akbank", "kisa_ad": "", "sube": "",
                              "swift_kod": "", "adres": ""})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Banka.objects.filter(ad="AKBANK").exists())
        b = banka_olustur(ad="garanti")
        r2 = self.client.post(reverse("core:banka_hesap_ekle", args=[b.pk]),
                              {"ad": "tl", "para_birimi": "TRY", "muhasebe": "102.01"})
        self.assertEqual(r2.status_code, 302)
        self.assertTrue(BankaHesap.objects.filter(banka=b, ad="TL").exists())

    def test_kredi_karti_detay_ve_liste_link(self):
        from decimal import Decimal
        from core.services.finans import kredi_karti_olustur
        from core.models import Banka
        _hesap("309.01", "KREDİ KARTLARI")
        b = Banka.objects.create(ad="GARANTİ BANKASI A.Ş.", kisa_ad="GARANTİ",
                                 created_by=self.yon, updated_by=self.yon)
        k = kredi_karti_olustur(ad="bonus", banka=b, kart_son4="1234",
                                limit=Decimal("10000"), kesim_gunu=15, son_odeme_gunu=25,
                                para_birimi="TRY", muhasebe_kodu="309.01", kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kredi_karti_detay", args=[k.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "BONUS")                 # kart adı TR büyük harf
        self.assertContains(r, "Kart Ekstresi")
        self.assertContains(r, reverse("core:kredi_karti_hareket_ekle", args=[k.pk, "harcama"]))  # aktif buton
        self.assertContains(r, "Güncel Borç")
        self.assertContains(r, "GARANTİ")               # banka KISA ad gösterilir (detay)
        self.assertNotContains(r, "A.Ş.")               # uzun resmi ad değil
        rl = self.client.get(reverse("core:kredi_kartlari"))
        self.assertContains(rl, reverse("core:kredi_karti_detay", args=[k.pk]))
        self.assertContains(rl, "GARANTİ")              # listede de kısa ad
        rf = self.client.get(reverse("core:kredi_karti_ekle"))
        self.assertContains(rf, "GARANTİ")              # dropdown etiketi = kısa ad
        self.assertNotContains(rf, "A.Ş.")


class BankaHareketTest(TestCase):
    """Banka hesabı hareket motoru (5 tip, ortak motor) — kasa deseninin banka karşılığı."""
    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from django.utils import timezone
        from core.models import Kur
        cls.yon = User.objects.create_superuser("byon", password="x")
        cls.bos = User.objects.create_user("bbos", password="x")
        for kod, ad in (("100.01", "MERKEZ KASA"), ("102.01", "İŞ BANKASI VADESİZ"),
                        ("102.02", "İŞ BANKASI 2"), ("120.01", "MÜŞTERİ A")):
            _hesap(kod, ad)
        Kur.objects.create(tarih=timezone.localdate(), usd_alis=Decimal("40"))

    def _kur(self, pb1="TRY", pb2="TRY"):
        from core.models import Banka, BankaHesap, Cari
        b = Banka.objects.create(ad="İŞ BANKASI", created_by=self.yon, updated_by=self.yon)
        bh1 = BankaHesap.objects.create(banka=b, ad="VADESİZ TL", muhasebe_id="102.01",
                                        para_birimi=pb1, created_by=self.yon, updated_by=self.yon)
        bh2 = BankaHesap.objects.create(banka=b, ad="İKİNCİ", muhasebe_id="102.02",
                                        para_birimi=pb2, created_by=self.yon, updated_by=self.yon)
        cari = Cari.objects.create(kod="CAR-1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                   created_by=self.yon, updated_by=self.yon)
        return bh1, bh2, cari

    def _s(self, fis):
        return {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}

    def test_cari_tahsilat_ve_odeme(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur
        bh1, _, cari = self._kur()
        t = timezone.localdate()
        f1 = hareket_olustur(banka_hesap=bh1, tip="cari_tahsilat", karsi=cari,
                             tutar=Decimal("1000"), tarih=t, kullanici=self.yon)
        s1 = self._s(f1)
        self.assertEqual(s1["102.01"], (Decimal("1000.00"), Decimal("0.00")))  # banka borç (giriş)
        self.assertEqual(s1["120.01"], (Decimal("0.00"), Decimal("1000.00")))  # cari alacak
        self.assertEqual(f1.banka_hesap_id, bh1.pk)
        f2 = hareket_olustur(banka_hesap=bh1, tip="cari_odeme", karsi=cari,
                             tutar=Decimal("400"), tarih=t, kullanici=self.yon)
        s2 = self._s(f2)
        self.assertEqual(s2["102.01"], (Decimal("0.00"), Decimal("400.00")))   # banka alacak (çıkış)
        self.assertEqual(s2["120.01"], (Decimal("400.00"), Decimal("0.00")))   # cari borç

    def test_banka_virman(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur
        bh1, bh2, _ = self._kur()
        fis = hareket_olustur(banka_hesap=bh1, tip="banka_virman", karsi=bh2,
                              tutar=Decimal("700"), tarih=timezone.localdate(), kullanici=self.yon)
        s = self._s(fis)
        self.assertEqual(s["102.01"], (Decimal("0.00"), Decimal("700.00")))    # kaynak alacak
        self.assertEqual(s["102.02"], (Decimal("700.00"), Decimal("0.00")))    # hedef borç
        self.assertEqual(fis.banka_hesap_id, bh1.pk)

    def test_banka_yatan_ve_cekilen(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur
        bh1, _, _ = self._kur()
        kasa = kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        t = timezone.localdate()
        f1 = hareket_olustur(banka_hesap=bh1, tip="banka_yatan", karsi=kasa,
                             tutar=Decimal("500"), tarih=t, kullanici=self.yon)
        s1 = self._s(f1)
        self.assertEqual(s1["102.01"], (Decimal("500.00"), Decimal("0.00")))   # banka borç (giriş)
        self.assertEqual(s1["100.01"], (Decimal("0.00"), Decimal("500.00")))   # kasa alacak
        f2 = hareket_olustur(banka_hesap=bh1, tip="banka_cekilen", karsi=kasa,
                             tutar=Decimal("300"), tarih=t, kullanici=self.yon)
        s2 = self._s(f2)
        self.assertEqual(s2["102.01"], (Decimal("0.00"), Decimal("300.00")))   # banka alacak (çıkış)
        self.assertEqual(s2["100.01"], (Decimal("300.00"), Decimal("0.00")))   # kasa borç

    def test_farkli_pb_reddedilir(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur, BankaHareketHatasi
        bh1, bh2, _ = self._kur(pb1="TRY", pb2="USD")
        with self.assertRaises(BankaHareketHatasi):
            hareket_olustur(banka_hesap=bh1, tip="banka_virman", karsi=bh2,
                            tutar=Decimal("1"), tarih=timezone.localdate(), kullanici=self.yon)

    def test_detay_formlar_ve_kilit(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur
        bh1, bh2, cari = self._kur()
        kasa_olustur(ad="MERKEZ KASA", muhasebe_kodu="100.01")
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:banka_hesap_detay", args=[bh1.pk]))
        self.assertEqual(d.status_code, 200)
        self.assertContains(d, "Hesap Ekstresi")
        for ad in ("Cari Tahsilat", "Cari Ödeme", "Banka Virman", "Banka Yatan", "Banka Çekilen"):
            self.assertContains(d, ad)
        for tip, et in (("cari_tahsilat", "Cari (karşı taraf)"), ("cari_odeme", "Cari (karşı taraf)"),
                        ("banka_virman", "Hedef Banka Hesabı"), ("banka_yatan", "— kasa seç —"),
                        ("banka_cekilen", "— kasa seç —")):
            r = self.client.get(reverse("core:banka_hareket_ekle", args=[bh1.pk, tip]))
            self.assertEqual(r.status_code, 200, tip)
            self.assertContains(r, et)
        fis = hareket_olustur(banka_hesap=bh1, tip="cari_tahsilat", karsi=cari,
                              tutar=Decimal("100"), tarih=timezone.localdate(), kullanici=self.yon)
        lst = self.client.get(reverse("core:fis_listesi"))
        self.assertContains(lst, reverse("core:fis_detay", args=[fis.pk]))      # detaya link
        fd = self.client.get(reverse("core:fis_detay", args=[fis.pk]))
        self.assertContains(fd, "banka hareketinden")                          # bilgi notu
        self.assertContains(fd, reverse("core:banka_hesap_detay", args=[bh1.pk]))
        duz = self.client.get(reverse("core:fis_duzenle", args=[fis.pk]))      # ham düzenleme kilitli
        self.assertRedirects(duz, reverse("core:banka_hesap_detay", args=[bh1.pk]))

    def test_hareket_iptal_view(self):
        from decimal import Decimal
        from django.utils import timezone
        from core.services.banka_hareket import hareket_olustur
        bh1, _, cari = self._kur()
        fis = hareket_olustur(banka_hesap=bh1, tip="cari_tahsilat", karsi=cari,
                              tutar=Decimal("100"), tarih=timezone.localdate(), kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:banka_hareket_iptal", args=[bh1.pk, fis.pk]))
        self.assertRedirects(r, reverse("core:banka_hesap_detay", args=[bh1.pk]))
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)

    def test_detay_yetkisiz_403(self):
        bh1, _, _ = self._kur()
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:banka_hesap_detay", args=[bh1.pk])).status_code, 403)


class KrediKartiHareketTest(TestCase):
    """Kredi kartı hareket motoru — Harcama/Ödeme/İade; karşı Cari/Gider/Banka/Kasa."""
    @classmethod
    def setUpTestData(cls):
        import datetime
        from decimal import Decimal
        from core.models import Banka, BankaHesap, Cari, Kasa, Kur
        from core.services.finans import kredi_karti_olustur
        cls.yon = User.objects.create_superuser("kkyon", password="x")
        cls.bos = User.objects.create_user("kkbos", password="x")
        for kod, ad in (("309.01", "KREDİ KARTLARI"), ("309.02", "USD KART"),
                        ("770.01", "GENEL GİDER"), ("120.01", "MÜŞTERİ A"),
                        ("102.01", "İŞ BANKASI"), ("100.01", "MERKEZ KASA")):
            _hesap(kod, ad)
        cls.t = datetime.date(2026, 6, 28)
        Kur.objects.create(tarih=cls.t, usd_alis=Decimal("40"))
        cls.kart = kredi_karti_olustur(ad="bonus", limit=Decimal("10000"),
                                       para_birimi="TRY", muhasebe_kodu="309.01", kullanici=cls.yon)
        cls.gider = HesapPlani.objects.get(hesap_kodu="770.01")
        cls.cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                       created_by=cls.yon, updated_by=cls.yon)
        b = Banka.objects.create(ad="İŞ BANKASI", created_by=cls.yon, updated_by=cls.yon)
        cls.bh = BankaHesap.objects.create(banka=b, ad="VADESİZ", muhasebe_id="102.01",
                                           para_birimi="TRY", created_by=cls.yon, updated_by=cls.yon)
        cls.kasa = Kasa.objects.create(ad="MERKEZ", muhasebe_id="100.01", para_birimi="TRY",
                                       created_by=cls.yon, updated_by=cls.yon)

    def _s(self, fis):
        return {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}

    def test_harcama_cari(self):
        from decimal import Decimal
        from core.models import YevmiyeFisi
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="harcama", karsi=self.cari,
                            tutar=Decimal("1500"), tarih=self.t, kullanici=self.yon)
        self.assertEqual(f.kaynak, YevmiyeFisi.Kaynak.KREDI_KARTI)
        self.assertEqual(f.kredi_karti_id, self.kart.pk)
        s = self._s(f)
        self.assertEqual(s["120.01"], (Decimal("1500.00"), Decimal("0.00")))   # cari borç
        self.assertEqual(s["309.01"], (Decimal("0.00"), Decimal("1500.00")))   # kart alacak (borç artar)

    def test_harcama_gider(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="harcama", karsi=self.gider,
                            tutar=Decimal("800"), tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["770.01"], (Decimal("800.00"), Decimal("0.00")))    # gider borç
        self.assertEqual(s["309.01"], (Decimal("0.00"), Decimal("800.00")))    # kart alacak

    def test_odeme_banka(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="odeme", karsi=self.bh,
                            tutar=Decimal("500"), tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["309.01"], (Decimal("500.00"), Decimal("0.00")))    # kart borç (azalır)
        self.assertEqual(s["102.01"], (Decimal("0.00"), Decimal("500.00")))    # banka alacak (para çıkar)

    def test_odeme_kasa(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="odeme", karsi=self.kasa,
                            tutar=Decimal("300"), tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["309.01"], (Decimal("300.00"), Decimal("0.00")))
        self.assertEqual(s["100.01"], (Decimal("0.00"), Decimal("300.00")))

    def test_iade_cari(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="iade", karsi=self.cari,
                            tutar=Decimal("250"), tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["309.01"], (Decimal("250.00"), Decimal("0.00")))    # kart borç (azalır)
        self.assertEqual(s["120.01"], (Decimal("0.00"), Decimal("250.00")))    # cari alacak

    def test_iptal_ve_yanlis_kart_reddedilir(self):
        from decimal import Decimal
        from core.services.finans import kredi_karti_olustur
        from core.services.kredi_karti_hareket import (KrediKartiHareketHatasi, hareket_iptal,
                                                       hareket_olustur)
        f = hareket_olustur(kart=self.kart, tip="harcama", karsi=self.cari,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)
        _hesap("309.03", "İKİNCİ KART")
        kart2 = kredi_karti_olustur(ad="ikinci", para_birimi="TRY", muhasebe_kodu="309.03",
                                    kullanici=self.yon)
        with self.assertRaises(KrediKartiHareketHatasi):
            hareket_iptal(fis=f, kart=kart2, kullanici=self.yon)   # başka kartın hareketi
        hareket_iptal(fis=f, kart=self.kart, kullanici=self.yon)
        f.refresh_from_db()
        self.assertTrue(f.silindi)

    def test_karsi_tip_uyumsuz_reddedilir(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import KrediKartiHareketHatasi, hareket_olustur
        with self.assertRaises(KrediKartiHareketHatasi):        # harcama'ya banka
            hareket_olustur(kart=self.kart, tip="harcama", karsi=self.bh,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)
        with self.assertRaises(KrediKartiHareketHatasi):        # ödeme'ye cari
            hareket_olustur(kart=self.kart, tip="odeme", karsi=self.cari,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)

    def test_pb_uyusmazligi_reddedilir(self):
        from decimal import Decimal
        from core.services.finans import kredi_karti_olustur
        from core.services.kredi_karti_hareket import KrediKartiHareketHatasi, hareket_olustur
        kart_usd = kredi_karti_olustur(ad="dolar", para_birimi="USD", muhasebe_kodu="309.02",
                                       kullanici=self.yon)
        with self.assertRaises(KrediKartiHareketHatasi):        # USD kart + TRY banka
            hareket_olustur(kart=kart_usd, tip="odeme", karsi=self.bh,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)

    def test_form_tam_bir_karsi(self):
        from core.forms import KrediKartiHareketForm
        ortak = {"tutar": "100", "tarih": "2026-06-28"}
        f0 = KrediKartiHareketForm(ortak, tip="harcama", kart=self.kart)
        self.assertFalse(f0.is_valid())                        # 0 karşı
        f2 = KrediKartiHareketForm({**ortak, "cari": self.cari.pk, "gider": self.gider.pk},
                                   tip="harcama", kart=self.kart)
        self.assertFalse(f2.is_valid())                        # 2 karşı
        f1 = KrediKartiHareketForm({**ortak, "cari": self.cari.pk}, tip="harcama", kart=self.kart)
        self.assertTrue(f1.is_valid())                         # tam 1 karşı

    def test_view_ekle_ve_iptal(self):
        from core.models import YevmiyeFisi
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(
            reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"])).status_code, 200)
        r = self.client.post(
            reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"]),
            {"cari": self.cari.pk, "tutar": "300", "tarih": "2026-06-28"})
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        fis = YevmiyeFisi.objects.filter(kredi_karti=self.kart, silindi=False).get()
        ri = self.client.post(reverse("core:kredi_karti_hareket_iptal", args=[self.kart.pk, fis.pk]))
        self.assertRedirects(ri, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)

    def test_ham_fis_duzenleme_kilidi(self):
        from decimal import Decimal
        from core.services.kredi_karti_hareket import hareket_olustur
        f = hareket_olustur(kart=self.kart, tip="harcama", karsi=self.cari,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fis_duzenle", args=[f.pk]))
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))

    def test_taksitli_harcama_plan_ve_fis(self):
        from decimal import Decimal
        from core.models import KrediKartiTaksit
        from core.services.kredi_karti_hareket import harcama_olustur
        f = harcama_olustur(kart=self.kart, karsi=self.gider, tutar=Decimal("12000"),
                            tarih=self.t, taksit_adedi=3, ilk_vade=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["309.01"], (Decimal("0.00"), Decimal("12000.00")))   # kart alacak TAM tutar
        self.assertEqual(s["770.01"], (Decimal("12000.00"), Decimal("0.00")))   # gider borç TAM tutar
        plan = KrediKartiTaksit.objects.get(fis=f)
        self.assertEqual(plan.taksit_adedi, 3)
        self.assertEqual(plan.toplam_tutar, Decimal("12000.00"))

    def test_pesin_harcama_plan_yok(self):
        from decimal import Decimal
        from core.models import KrediKartiTaksit
        from core.services.kredi_karti_hareket import harcama_olustur
        f = harcama_olustur(kart=self.kart, karsi=self.gider, tutar=Decimal("500"),
                            tarih=self.t, taksit_adedi=1, kullanici=self.yon)
        self.assertFalse(KrediKartiTaksit.objects.filter(fis=f).exists())

    def test_taksit_takvimi_yuvarlama(self):
        from decimal import Decimal
        from core.models import KrediKartiTaksit
        from core.services.kredi_karti_hareket import harcama_olustur, taksit_takvimi
        f = harcama_olustur(kart=self.kart, karsi=self.gider, tutar=Decimal("12001"),
                            tarih=self.t, taksit_adedi=3, ilk_vade=self.t, kullanici=self.yon)
        tk = taksit_takvimi(KrediKartiTaksit.objects.get(fis=f))
        self.assertEqual([r["tutar"] for r in tk],
                         [Decimal("4000.33"), Decimal("4000.33"), Decimal("4000.34")])
        self.assertEqual(sum((r["tutar"] for r in tk), Decimal("0")), Decimal("12001"))

    def test_taksit_vade_ay_ve_yil_gecisi(self):
        import datetime
        from decimal import Decimal
        from core.models import KrediKartiTaksit
        from core.services.kredi_karti_hareket import harcama_olustur, taksit_takvimi
        f = harcama_olustur(kart=self.kart, karsi=self.gider, tutar=Decimal("300"),
                            tarih=self.t, taksit_adedi=3, ilk_vade=datetime.date(2026, 11, 30),
                            kullanici=self.yon)
        tk = taksit_takvimi(KrediKartiTaksit.objects.get(fis=f))
        self.assertEqual([r["vade"] for r in tk],
                         [datetime.date(2026, 11, 30), datetime.date(2026, 12, 30),
                          datetime.date(2027, 1, 30)])

    def test_taksitli_iptal_plan_geri_alinir(self):
        from decimal import Decimal
        from core.models import KrediKartiTaksit
        from core.services.kredi_karti_hareket import harcama_olustur, hareket_iptal
        f = harcama_olustur(kart=self.kart, karsi=self.gider, tutar=Decimal("600"),
                            tarih=self.t, taksit_adedi=2, ilk_vade=self.t, kullanici=self.yon)
        self.assertTrue(KrediKartiTaksit.objects.filter(fis=f, silindi=False).exists())
        hareket_iptal(fis=f, kart=self.kart, kullanici=self.yon)
        self.assertFalse(KrediKartiTaksit.objects.filter(fis=f, silindi=False).exists())

    def test_form_taksit_vade_zorunlu(self):
        from core.forms import KrediKartiHareketForm
        base = {"tutar": "1200", "tarih": "2026-06-28", "gider": self.gider.pk}
        fbad = KrediKartiHareketForm({**base, "taksit_adedi": "3"}, tip="harcama", kart=self.kart)
        self.assertFalse(fbad.is_valid())                      # vade yok -> geçersiz
        fok = KrediKartiHareketForm({**base, "taksit_adedi": "3", "ilk_vade": "2026-07-28"},
                                    tip="harcama", kart=self.kart)
        self.assertTrue(fok.is_valid())

    def test_view_taksitli_harcama_ve_takvim(self):
        from core.models import KrediKartiTaksit
        self.client.force_login(self.yon)
        r = self.client.post(
            reverse("core:kredi_karti_hareket_ekle", args=[self.kart.pk, "harcama"]),
            {"gider": self.gider.pk, "tutar": "9000", "tarih": "2026-06-28",
             "taksit_adedi": "3", "ilk_vade": "2026-07-28"})
        self.assertRedirects(r, reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        self.assertEqual(KrediKartiTaksit.objects.filter(kart=self.kart, silindi=False).count(), 1)
        d = self.client.get(reverse("core:kredi_karti_detay", args=[self.kart.pk]))
        self.assertContains(d, "Taksit Takvimi")
        self.assertContains(d, "1/3")


class KrediHareketTest(TestCase):
    """Kredi hareket motoru Dilim 1 — Kullandırım (kredi alacak / nakit borç) + banka FK/kısa ad."""
    @classmethod
    def setUpTestData(cls):
        import datetime
        from decimal import Decimal
        from core.models import Banka, BankaHesap, Kasa, Kur
        from core.services.finans import kredi_olustur
        cls.yon = User.objects.create_superuser("kryon", password="x")
        cls.bos = User.objects.create_user("krbos", password="x")
        for kod, ad in (("300.01", "BANKA KREDİLERİ"), ("300.02", "USD KREDİ"),
                        ("102.01", "İŞ BANKASI"), ("100.01", "MERKEZ KASA")):
            _hesap(kod, ad)
        cls.t = datetime.date(2026, 6, 28)
        Kur.objects.create(tarih=cls.t, usd_alis=Decimal("40"))
        b = Banka.objects.create(ad="TÜRKİYE İŞ BANKASI A.Ş.", kisa_ad="İŞ BANKASI",
                                 created_by=cls.yon, updated_by=cls.yon)
        cls.banka = b
        cls.kredi = kredi_olustur(ad="ticari kredi", banka=b, anapara=Decimal("100000"),
                                  faiz_orani=Decimal("3.5"), para_birimi="TRY",
                                  muhasebe_kodu="300.01", kullanici=cls.yon)
        cls.bh = BankaHesap.objects.create(banka=b, ad="VADESİZ", muhasebe_id="102.01",
                                           para_birimi="TRY", created_by=cls.yon, updated_by=cls.yon)
        cls.kasa = Kasa.objects.create(ad="MERKEZ", muhasebe_id="100.01", para_birimi="TRY",
                                       created_by=cls.yon, updated_by=cls.yon)

    def _s(self, fis):
        return {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.filter(silindi=False)}

    def test_kullandirim_banka(self):
        from decimal import Decimal
        from core.models import YevmiyeFisi
        from core.services.kredi_hareket import hareket_olustur
        f = hareket_olustur(kredi=self.kredi, tip="kullandirim", karsi=self.bh,
                            tutar=Decimal("100000"), tarih=self.t, kullanici=self.yon)
        self.assertEqual(f.kaynak, YevmiyeFisi.Kaynak.KREDI)
        self.assertEqual(f.kredi_id, self.kredi.pk)
        s = self._s(f)
        self.assertEqual(s["300.01"], (Decimal("0.00"), Decimal("100000.00")))   # kredi alacak (borç doğar)
        self.assertEqual(s["102.01"], (Decimal("100000.00"), Decimal("0.00")))    # banka borç (para girer)

    def test_kullandirim_kasa(self):
        from decimal import Decimal
        from core.services.kredi_hareket import hareket_olustur
        f = hareket_olustur(kredi=self.kredi, tip="kullandirim", karsi=self.kasa,
                            tutar=Decimal("5000"), tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["300.01"], (Decimal("0.00"), Decimal("5000.00")))
        self.assertEqual(s["100.01"], (Decimal("5000.00"), Decimal("0.00")))

    def test_iptal_ve_yanlis_kredi_reddedilir(self):
        from decimal import Decimal
        from core.services.finans import kredi_olustur
        from core.services.kredi_hareket import (KrediHareketHatasi, hareket_iptal, hareket_olustur)
        f = hareket_olustur(kredi=self.kredi, tip="kullandirim", karsi=self.bh,
                            tutar=Decimal("1000"), tarih=self.t, kullanici=self.yon)
        _hesap("300.03", "İKİNCİ KREDİ")
        kredi2 = kredi_olustur(ad="ikinci", para_birimi="TRY", muhasebe_kodu="300.03",
                               kullanici=self.yon)
        with self.assertRaises(KrediHareketHatasi):
            hareket_iptal(fis=f, kredi=kredi2, kullanici=self.yon)
        hareket_iptal(fis=f, kredi=self.kredi, kullanici=self.yon)
        f.refresh_from_db()
        self.assertTrue(f.silindi)

    def test_pb_uyusmazligi_reddedilir(self):
        from decimal import Decimal
        from core.services.finans import kredi_olustur
        from core.services.kredi_hareket import KrediHareketHatasi, hareket_olustur
        kredi_usd = kredi_olustur(ad="dolar kredi", para_birimi="USD", muhasebe_kodu="300.02",
                                  kullanici=self.yon)
        with self.assertRaises(KrediHareketHatasi):
            hareket_olustur(kredi=kredi_usd, tip="kullandirim", karsi=self.bh,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)

    def test_form_nakit_tam_bir(self):
        from core.forms import KrediHareketForm
        base = {"tutar": "1000", "tarih": "2026-06-28"}
        f0 = KrediHareketForm(base, tip="kullandirim", kredi=self.kredi)
        self.assertFalse(f0.is_valid())                       # 0 nakit
        f2 = KrediHareketForm({**base, "banka_hesap": self.bh.pk, "kasa": self.kasa.pk},
                              tip="kullandirim", kredi=self.kredi)
        self.assertFalse(f2.is_valid())                       # 2 nakit
        f1 = KrediHareketForm({**base, "banka_hesap": self.bh.pk}, tip="kullandirim", kredi=self.kredi)
        self.assertTrue(f1.is_valid())

    def test_view_kullandirim_iptal_ve_kisa_ad(self):
        from core.models import YevmiyeFisi
        self.client.force_login(self.yon)
        # detay: banka KISA ad gösterilir, tam ad değil
        d = self.client.get(reverse("core:kredi_detay", args=[self.kredi.pk]))
        self.assertEqual(d.status_code, 200)
        self.assertContains(d, "İŞ BANKASI")
        self.assertNotContains(d, "A.Ş.")
        self.assertContains(d, "Kullandırım")
        # kullandırım POST
        self.assertEqual(self.client.get(
            reverse("core:kredi_hareket_ekle", args=[self.kredi.pk, "kullandirim"])).status_code, 200)
        r = self.client.post(
            reverse("core:kredi_hareket_ekle", args=[self.kredi.pk, "kullandirim"]),
            {"banka_hesap": self.bh.pk, "tutar": "20000", "tarih": "2026-06-28"})
        self.assertRedirects(r, reverse("core:kredi_detay", args=[self.kredi.pk]))
        fis = YevmiyeFisi.objects.filter(kredi=self.kredi, silindi=False).get()
        ri = self.client.post(reverse("core:kredi_hareket_iptal", args=[self.kredi.pk, fis.pk]))
        self.assertRedirects(ri, reverse("core:kredi_detay", args=[self.kredi.pk]))
        fis.refresh_from_db()
        self.assertTrue(fis.silindi)

    def test_ham_fis_duzenleme_kilidi(self):
        from decimal import Decimal
        from core.services.kredi_hareket import hareket_olustur
        f = hareket_olustur(kredi=self.kredi, tip="kullandirim", karsi=self.bh,
                            tutar=Decimal("100"), tarih=self.t, kullanici=self.yon)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:fis_duzenle", args=[f.pk]))
        self.assertRedirects(r, reverse("core:kredi_detay", args=[self.kredi.pk]))

    def test_liste_kisa_ad_ve_detay_link(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:krediler"))
        self.assertContains(r, reverse("core:kredi_detay", args=[self.kredi.pk]))
        self.assertContains(r, "İŞ BANKASI")

    def test_geri_odeme_banka_faizli(self):
        from decimal import Decimal
        from core.services.kredi_hareket import geri_odeme_olustur
        fh = _hesap("780.01", "FAİZ GİDERLERİ", kalem="GIDER")
        f = geri_odeme_olustur(kredi=self.kredi, karsi=self.bh, anapara=Decimal("8000"),
                               faiz=Decimal("1250"), faiz_hesap=fh, tarih=self.t,
                               kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["300.01"], (Decimal("8000.00"), Decimal("0.00")))   # kredi borç (kapanır)
        self.assertEqual(s["780.01"], (Decimal("1250.00"), Decimal("0.00")))   # faiz gideri borç
        self.assertEqual(s["102.01"], (Decimal("0.00"), Decimal("9250.00")))   # banka alacak toplam

    def test_geri_odeme_faizsiz_iki_satir(self):
        from decimal import Decimal
        from core.services.kredi_hareket import geri_odeme_olustur
        f = geri_odeme_olustur(kredi=self.kredi, karsi=self.kasa, anapara=Decimal("500"),
                               faiz=0, tarih=self.t, kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(len(s), 2)                                            # faiz satırı yok
        self.assertEqual(s["300.01"], (Decimal("500.00"), Decimal("0.00")))
        self.assertEqual(s["100.01"], (Decimal("0.00"), Decimal("500.00")))

    def test_geri_odeme_faiz_hesapsiz_reddedilir(self):
        from decimal import Decimal
        from core.services.kredi_hareket import KrediHareketHatasi, geri_odeme_olustur
        with self.assertRaises(KrediHareketHatasi):
            geri_odeme_olustur(kredi=self.kredi, karsi=self.bh, anapara=Decimal("100"),
                               faiz=Decimal("10"), tarih=self.t, kullanici=self.yon)

    def test_geri_odeme_negatif_faiz_reddedilir(self):
        from decimal import Decimal
        from core.services.kredi_hareket import KrediHareketHatasi, geri_odeme_olustur
        with self.assertRaises(KrediHareketHatasi):
            geri_odeme_olustur(kredi=self.kredi, karsi=self.bh, anapara=Decimal("100"),
                               faiz=Decimal("-5"), tarih=self.t, kullanici=self.yon)

    def test_geri_odeme_doviz_kurus_dengesi(self):
        # Dövizde anapara+faiz satır bazında yuvarlanır; nakit satırı tl_override ile denklenir.
        import datetime
        from decimal import Decimal
        from core.models import BankaHesap, Kur
        from core.services.finans import kredi_olustur
        from core.services.kredi_hareket import geri_odeme_olustur
        Kur.objects.create(tarih=datetime.date(2026, 6, 29), usd_alis=Decimal("36.4517"))
        fh = _hesap("780.02", "USD FAİZ GİDERİ", kalem="GIDER")
        _hesap("102.02", "USD BANKA")
        bh_usd = BankaHesap.objects.create(banka=self.banka, ad="USD HESAP", muhasebe_id="102.02",
                                           para_birimi="USD", created_by=self.yon, updated_by=self.yon)
        kredi_usd = kredi_olustur(ad="usd kredi 2", para_birimi="USD", muhasebe_kodu="300.02",
                                  kullanici=self.yon)
        f = geri_odeme_olustur(kredi=kredi_usd, karsi=bh_usd, anapara=Decimal("100.10"),
                               faiz=Decimal("200.30"), faiz_hesap=fh,
                               tarih=datetime.date(2026, 6, 29), kullanici=self.yon)
        s = self._s(f)
        self.assertEqual(s["300.02"], (Decimal("3648.82"), Decimal("0.00")))   # 100,10×36,4517
        self.assertEqual(s["780.02"], (Decimal("7301.28"), Decimal("0.00")))   # 200,30×36,4517 (7301.2755→HALF_UP)
        # tl_override olmasa 10950.09 olurdu (yuvarla(300,40×36,4517)) → fiş dengesizdi
        self.assertEqual(s["102.02"], (Decimal("0.00"), Decimal("10950.10")))
        self.assertEqual(sum(v[0] for v in s.values()), sum(v[1] for v in s.values()))

    def test_form_geri_odeme_dogrulama(self):
        from core.forms import KrediHareketForm
        fh = _hesap("780.03", "FAİZ GİDERİ 3", kalem="GIDER")
        base = {"tarih": "2026-06-28", "banka_hesap": self.bh.pk}
        f0 = KrediHareketForm(base, tip="geri_odeme", kredi=self.kredi)
        self.assertFalse(f0.is_valid())                                        # anapara zorunlu
        f1 = KrediHareketForm({**base, "anapara": "1.000", "faiz": "50"},
                              tip="geri_odeme", kredi=self.kredi)
        self.assertFalse(f1.is_valid())                                        # faiz var, hesap yok
        self.assertIn("faiz_hesap", f1.errors)
        f2 = KrediHareketForm({**base, "anapara": "1.000", "faiz": "50", "faiz_hesap": fh.pk},
                              tip="geri_odeme", kredi=self.kredi)
        self.assertTrue(f2.is_valid())
        f3 = KrediHareketForm({**base, "anapara": "1.000"}, tip="geri_odeme", kredi=self.kredi)
        self.assertTrue(f3.is_valid())                                         # faizsiz de olur

    def test_view_geri_odeme_post_ve_buton(self):
        from decimal import Decimal
        from core.models import YevmiyeFisi
        fh = _hesap("780.04", "FAİZ GİDERİ 4", kalem="GIDER")
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:kredi_detay", args=[self.kredi.pk]))
        self.assertContains(d, reverse("core:kredi_hareket_ekle",
                                       args=[self.kredi.pk, "geri_odeme"]))    # buton aktif
        self.assertEqual(self.client.get(reverse(
            "core:kredi_hareket_ekle", args=[self.kredi.pk, "geri_odeme"])).status_code, 200)
        r = self.client.post(
            reverse("core:kredi_hareket_ekle", args=[self.kredi.pk, "geri_odeme"]),
            {"banka_hesap": self.bh.pk, "anapara": "2.000", "faiz": "300",
             "faiz_hesap": fh.pk, "tarih": "2026-06-28"})
        self.assertRedirects(r, reverse("core:kredi_detay", args=[self.kredi.pk]))
        fis = YevmiyeFisi.objects.filter(kredi=self.kredi, silindi=False).get()
        s = self._s(fis)
        self.assertEqual(s["300.01"], (Decimal("2000.00"), Decimal("0.00")))
        self.assertEqual(s["780.04"], (Decimal("300.00"), Decimal("0.00")))
        self.assertEqual(s["102.01"], (Decimal("0.00"), Decimal("2300.00")))
