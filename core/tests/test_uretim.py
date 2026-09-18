"""ÜRETİM modülü — İş İstasyonu + Operasyon (rota) modeli. FASON'daki kesilmiş-parça/
kesildigi_profil kavramıyla hiçbir ilişkisi yok. Bir Operasyon, bir iş istasyonunda,
bir/daha fazla GİRDİ stoktan TEK bir ÇIKTI stok üretir (oranlı dönüşüm). Bir Üretim Emri
("N adet [hedef ürün] istiyorum"), İhtiyaç Hesapla ile aynı özyinelemeli algoritmayla
zinciri hesaplayıp zincirdeki HER operasyon için ayrı bir TASLAK Operasyon Kaydı açar; her
istasyon kendi kaydını kendi zamanında onaylar (StokHareket, Kaynak=URETIM) — muhasebeye
hiç dokunmaz."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Birim, Depo, EkranYetki, IsIstasyonu, Kategori, Operasyon, OperasyonKaydi, Stok,
    StokHareket, UretimEmri, YevmiyeFisi,
)
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, aktif_istasyonlar, aktif_operasyonlar, ihtiyac_hesapla,
    istasyon_guncelle, istasyon_olustur, istasyon_sil,
    kaydi_girdi_satirlari, operasyon_girdileri, operasyon_guncelle, operasyon_kaydi_girdi_guncelle,
    operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_kaydi_sil, operasyon_olustur,
    operasyon_sil, uretim_emri_ilerleme, uretim_emri_olustur, uretim_emri_sil,
)


def _birim():
    return Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)


def _kategori():
    return Kategori.objects.create(kod="UT", ad="ÜRETİM TEST")


def _stok(kategori, birim, *, kod, ad, uretim=True, satinalma=False, satis=False):
    return Stok.objects.create(
        kod=kod, ad=ad, kategori=kategori, uretim_birimi=birim, fatura_birimi=birim,
        uretim_urunu=uretim, satinalma_urunu=satinalma, satis_urunu=satis)


def _depo(kod="MRK"):
    return Depo.objects.create(kod=kod, ad="MERKEZ DEPO")


def _istasyon(kod="LAZER"):
    return IsIstasyonu.objects.create(kod=kod, ad=f"{kod} İSTASYONU")


class IsIstasyonuServisTest(TestCase):
    def test_olustur_ve_tr_buyuk_harf(self):
        i = istasyon_olustur(kod="lazer", ad="lazer kesim")
        self.assertEqual((i.kod, i.ad), ("LAZER", "LAZER KESİM"))

    def test_ayni_kod_iki_kez_reddedilir(self):
        istasyon_olustur(kod="LAZER", ad="LAZER KESİM")
        with self.assertRaises(UretimHatasi):
            istasyon_olustur(kod="LAZER", ad="BAŞKA AD")

    def test_ayni_ad_iki_kez_reddedilir(self):
        istasyon_olustur(kod="LAZER", ad="KESİM")
        with self.assertRaises(UretimHatasi):
            istasyon_olustur(kod="BUKUM", ad="KESİM")

    def test_guncelle(self):
        i = istasyon_olustur(kod="LAZER", ad="LAZER KESİM")
        istasyon_guncelle(i, kod="LAZER2", ad="LAZER KESİM 2")
        i.refresh_from_db()
        self.assertEqual((i.kod, i.ad), ("LAZER2", "LAZER KESİM 2"))

    def test_sil(self):
        i = istasyon_olustur(kod="LAZER", ad="LAZER KESİM")
        istasyon_sil(i)
        self.assertTrue(IsIstasyonu.objects.get(pk=i.pk).silindi)
        self.assertNotIn(i, aktif_istasyonlar())

    def test_sil_bagli_operasyon_varsa_reddedilir(self):
        i = istasyon_olustur(kod="LAZER", ad="LAZER KESİM")
        birim, kat = _birim(), _kategori()
        cikti = _stok(kat, birim, kod="UT-CIKTI", ad="çıktı", satis=True)
        girdi = _stok(kat, birim, kod="UT-GIRDI", ad="girdi", satinalma=True)
        operasyon_olustur(istasyon_id=i.pk, cikti_id=cikti.pk, cikti_miktar=Decimal("1"),
                          satirlar=[(girdi, Decimal("1"))])
        with self.assertRaises(UretimHatasi):
            istasyon_sil(i)


class OperasyonServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.istasyon = _istasyon("LAZER")
        cls.cikti = _stok(cls.kat, cls.birim, kod="UT-CIKTI", ad="kesilmiş parça")
        cls.girdi = _stok(cls.kat, cls.birim, kod="UT-GIRDI", ad="ham profil", satinalma=True)

    def test_olustur(self):
        op = operasyon_olustur(
            istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk, cikti_miktar=Decimal("2"),
            satirlar=[(self.girdi, Decimal("1"))], ad="Kesim")
        self.assertEqual(op.cikti_miktar, Decimal("2.000"))
        self.assertEqual(list(operasyon_girdileri(op))[0].girdi_id, self.girdi.pk)

    def test_ayni_cikti_icin_ikinci_operasyon_reddedilir(self):
        operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(self.girdi, Decimal("1"))])
        with self.assertRaises(UretimHatasi):
            operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                              cikti_miktar=Decimal("1"), satirlar=[(self.girdi, Decimal("1"))])

    def test_bos_girdi_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                              cikti_miktar=Decimal("1"), satirlar=[])

    def test_cikti_kendi_girdisi_olamaz(self):
        with self.assertRaises(UretimHatasi):
            operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                              cikti_miktar=Decimal("1"), satirlar=[(self.cikti, Decimal("1"))])

    def test_tekrar_eden_girdi_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                              cikti_miktar=Decimal("1"), satirlar=[
                                  (self.girdi, Decimal("1")), (self.girdi, Decimal("2"))])

    def test_cikti_miktar_sifir_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                              cikti_miktar=Decimal("0"), satirlar=[(self.girdi, Decimal("1"))])

    def test_guncelle_girdileri_degistirir_cikti_sabit_kalir(self):
        op = operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                               cikti_miktar=Decimal("2"), satirlar=[(self.girdi, Decimal("1"))])
        girdi2 = _stok(self.kat, self.birim, kod="UT-GIRDI2", ad="vida", satinalma=True)
        istasyon2 = _istasyon("BUKUM")
        operasyon_guncelle(op, istasyon_id=istasyon2.pk, cikti_miktar=Decimal("3"),
                           satirlar=[(girdi2, Decimal("4"))])
        op.refresh_from_db()
        self.assertEqual(op.istasyon_id, istasyon2.pk)
        self.assertEqual(op.cikti_id, self.cikti.pk)          # değişmez
        self.assertEqual(op.cikti_miktar, Decimal("3.000"))
        satirlar = list(operasyon_girdileri(op))
        self.assertEqual(len(satirlar), 1)
        self.assertEqual(satirlar[0].girdi_id, girdi2.pk)

    def test_sil(self):
        op = operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                               cikti_miktar=Decimal("1"), satirlar=[(self.girdi, Decimal("1"))])
        operasyon_sil(op)
        self.assertTrue(Operasyon.objects.get(pk=op.pk).silindi)
        self.assertNotIn(op, aktif_operasyonlar())

    def test_sil_bagli_kayit_varsa_reddedilir(self):
        op = operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=self.cikti.pk,
                               cikti_miktar=Decimal("1"), satirlar=[(self.girdi, Decimal("1"))])
        depo = _depo()
        hareket_ekle(stok_id=self.girdi.pk, depo_id=depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=depo.pk, tarih=date(2026, 1, 5),
                                hedef_cikti_miktari=Decimal("1"))
        with self.assertRaises(UretimHatasi):
            operasyon_sil(op)


class OperasyonKaydiServisTest(TestCase):
    """Kesim (1 profil -> 2 kesilmiş parça, cikti_miktar=2) -> Büküm (1 kesilmiş parça ->
    1 bükülmüş parça, cikti_miktar=1) iki basamaklı bir zincir üzerinden."""

    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.depo = _depo()
        cls.lazer = _istasyon("LAZER")
        cls.bukum = _istasyon("BUKUM")
        cls.profil = _stok(cls.kat, cls.birim, kod="UT-PROFIL", ad="ham profil", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UT-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UT-BUKULMUS", ad="bükülmüş parça")
        cls.kesim_op = operasyon_olustur(
            istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=Decimal("2"),
            satirlar=[(cls.profil, Decimal("1"))], ad="Kesim")
        cls.bukum_op = operasyon_olustur(
            istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk, cikti_miktar=Decimal("1"),
            satirlar=[(cls.kesilmis, Decimal("1"))], ad="Büküm")

    def test_olustur_oran_matematigi(self):
        """10 adet kesilmiş parça hedefi, cikti_miktar=2 olan kesim operasyonunda
        5 adet profil girdisi gerektirmeli (10 * 1 / 2 = 5)."""
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        satir = kaydi_girdi_satirlari(kayit).get(girdi=self.profil)
        self.assertEqual(satir.planlanan_miktar, Decimal("5.000"))
        self.assertEqual(satir.gerceklesen_miktar, Decimal("5.000"))
        self.assertEqual(kayit.durum, OperasyonKaydi.Durum.TASLAK)

    def test_numaralama_atomik_artan(self):
        k1 = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                     tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("2"))
        k2 = operasyon_kaydi_olustur(operasyon_id=self.bukum_op.pk, depo_id=self.depo.pk,
                                     tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("1"))
        self.assertEqual(k1.no, "OP-2026-0001")
        self.assertEqual(k2.no, "OP-2026-0002")

    def test_girdi_guncelle_taslakta_calisir(self):
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        satir = kaydi_girdi_satirlari(kayit).get(girdi=self.profil)
        operasyon_kaydi_girdi_guncelle(satir, gerceklesen_miktar=Decimal("5.5"))
        satir.refresh_from_db()
        self.assertEqual(satir.gerceklesen_miktar, Decimal("5.500"))

    def _stok_gir(self, stok, miktar):
        hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal(miktar))

    def _stok_gir_maliyetli(self, stok, miktar, birim_maliyet_try, tarih=date(2026, 1, 1)):
        return hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=tarih,
                            tur=StokHareket.Tur.GIRIS, miktar=Decimal(miktar),
                            birim_maliyet_try=Decimal(birim_maliyet_try))

    def test_onayla_stok_hareketleri_dogru(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        kayit.refresh_from_db()
        self.assertEqual(kayit.durum, OperasyonKaydi.Durum.ONAYLI)
        self.assertEqual(eldeki_miktar(self.profil, self.depo), Decimal("1000") - Decimal("5"))
        self.assertEqual(eldeki_miktar(self.kesilmis, self.depo), Decimal("10"))
        cikis = StokHareket.objects.get(stok=self.profil, kaynak=StokHareket.Kaynak.URETIM,
                                        tur=StokHareket.Tur.CIKIS)
        self.assertEqual(cikis.operasyon_kaydi_girdi.kayit_id, kayit.pk)
        giris = StokHareket.objects.get(stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM,
                                        tur=StokHareket.Tur.GIRIS)
        self.assertEqual(giris.miktar, Decimal("10.000"))

    def test_onayla_gerceklesen_sifir_satir_hatasiz_atlanir(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        satir = kaydi_girdi_satirlari(kayit).get(girdi=self.profil)
        operasyon_kaydi_girdi_guncelle(satir, gerceklesen_miktar=Decimal("0"))
        operasyon_kaydi_onayla(kayit)                          # hata fırlatmamalı
        self.assertFalse(StokHareket.objects.filter(
            stok=self.profil, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.CIKIS).exists())
        self.assertTrue(StokHareket.objects.filter(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS).exists())

    def test_onayla_yetersiz_stok_atomik_geri_alir(self):
        self._stok_gir(self.profil, "2")                       # 5 gerekiyor, 2 var
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_onayla(kayit)
        kayit.refresh_from_db()
        self.assertEqual(kayit.durum, OperasyonKaydi.Durum.TASLAK)
        self.assertEqual(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count(), 0)

    def test_onayla_idempotent(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        onceki = StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count()
        operasyon_kaydi_onayla(kayit)
        self.assertEqual(StokHareket.objects.filter(kaynak=StokHareket.Kaynak.URETIM).count(), onceki)

    def test_girdi_guncelle_onaylida_reddedilir(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        satir = kaydi_girdi_satirlari(kayit).first()
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_girdi_guncelle(satir, gerceklesen_miktar=Decimal("1"))

    def test_onayli_iptal_edilemez(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("2"))
        operasyon_kaydi_onayla(kayit)
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_sil(kayit)

    def test_taslak_iptal_soft_delete(self):
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("2"))
        operasyon_kaydi_sil(kayit)
        self.assertTrue(OperasyonKaydi.objects.get(pk=kayit.pk).silindi)

    def test_onayla_muhasebeye_dokunmuyor(self):
        self._stok_gir(self.profil, "1000")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        onceki = YevmiyeFisi.objects.count()
        operasyon_kaydi_onayla(kayit)
        self.assertEqual(YevmiyeFisi.objects.count(), onceki)

    def test_bagimsiz_serbest_kayit_uretim_emrisiz_calisir(self):
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("2"))
        self.assertIsNone(kayit.uretim_emri)

    def test_onayla_cikti_maliyeti_hesaplanir(self):
        """Kullanıcının senaryosu: 1 boy profilin FIFO maliyeti / cikti_miktar = kesilmiş
        parçanın birim maliyeti. 5 profil @ 10 TL = 50 TL; 10 kesilmiş parça -> 5 TL/adet."""
        self._stok_gir_maliyetli(self.profil, "1000", "10")
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        katman = cikti_hareketi.maliyet_katmani
        self.assertEqual(katman.birim_maliyet_try, Decimal("5.000000"))
        self.assertEqual(katman.giris_miktar, Decimal("10.000"))
        self.assertFalse(katman.tahmini)

    def test_onayla_iki_katmandan_karisik_fifo_ile_cikti_maliyeti(self):
        """3 profil @10 TL + 10 profil @20 TL katmanı; 10 kesilmiş parça hedefi 5 profil
        gerektirir (3x10 + 2x20 = 70 TL) -> çıktı birim maliyeti 70/10 = 7 TL."""
        self._stok_gir_maliyetli(self.profil, "3", "10", tarih=date(2026, 1, 1))
        self._stok_gir_maliyetli(self.profil, "10", "20", tarih=date(2026, 1, 2))
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertEqual(cikti_hareketi.maliyet_katmani.birim_maliyet_try, Decimal("7.000000"))

    def test_onayla_zincirde_maliyet_bir_sonraki_operasyona_tasinir(self):
        """Kesim çıktısının katmanı, Büküm'ün girdisi olarak FIFO'ya OTOMATİK girer —
        ek özyineleme kodu gerekmeden (bkz. plan: 'katman zaten normal bir katman')."""
        self._stok_gir_maliyetli(self.profil, "1000", "10")
        kesim = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kesim)          # kesilmiş parça: 10 adet @ 5 TL katmanı açılır

        bukum = operasyon_kaydi_olustur(operasyon_id=self.bukum_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 11), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(bukum)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.bukulmus, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        # bukum_op cikti_miktar=1, girdi oranı 1:1 -> maliyet aynen taşınır (5 TL).
        self.assertEqual(cikti_hareketi.maliyet_katmani.birim_maliyet_try, Decimal("5.000000"))
        self.assertFalse(cikti_hareketi.maliyet_katmani.tahmini)

    def test_onayla_girdi_katmani_yoksa_cikti_katmansiz_kalir(self):
        """Girdi hiç maliyetli değilse çıktı için 0 TL YAZILMAZ — katman hiç açılmaz."""
        self._stok_gir(self.profil, "1000")     # maliyetsiz
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertFalse(hasattr(cikti_hareketi, "maliyet_katmani"))

    def test_onayla_kismi_karsilamada_tahmini_isaretlenir(self):
        """5 profil gerekiyor: 2 adet @10 TL katmanlı + 3 adet maliyetsiz. Çıktı maliyeti
        yine de (kısmi veriyle) hesaplanır ama tahmini=True işaretlenir."""
        self._stok_gir_maliyetli(self.profil, "2", "10")
        self._stok_gir(self.profil, "3")        # maliyetsiz, ama fiziksel stok yeterli olsun
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        katman = cikti_hareketi.maliyet_katmani
        self.assertEqual(katman.birim_maliyet_try, Decimal("2.000000"))   # 2x10 / 10
        self.assertTrue(katman.tahmini)

    def test_onayla_tahmini_bayragi_ikinci_seviyeye_miras_alinir(self):
        """Kesim çıktısı (kısmi veriden) tahmini=True ise, Büküm bu katmanı MİKTAR olarak
        TAM tüketse bile kendi çıktısını da tahmini işaretlemeli (bayrak zincirde kaybolmaz)."""
        self._stok_gir_maliyetli(self.profil, "2", "10")
        self._stok_gir(self.profil, "3")
        kesim = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kesim)

        bukum = operasyon_kaydi_olustur(operasyon_id=self.bukum_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 11), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(bukum)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.bukulmus, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertTrue(cikti_hareketi.maliyet_katmani.tahmini)


class IhtiyacHesaplaTest(TestCase):
    """Kesim (1 profil -> 2 kesilmiş parça) -> Büküm (1 kesilmiş -> 1 bükülmüş) ->
    Montaj (1 bükülmüş + 2 civata -> 1 mamul) üç basamaklı zincir."""

    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.lazer = _istasyon("LAZER")
        cls.bukum = _istasyon("BUKUM")
        cls.montaj = _istasyon("MONTAJ")
        cls.profil = _stok(cls.kat, cls.birim, kod="UT-PROFIL", ad="ham profil", satinalma=True)
        cls.civata = _stok(cls.kat, cls.birim, kod="UT-CIVATA", ad="civata", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UT-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UT-BUKULMUS", ad="bükülmüş parça")
        cls.mamul = _stok(cls.kat, cls.birim, kod="UT-MAMUL", ad="test merdiveni", satis=True)
        operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk,
                          cikti_miktar=Decimal("2"), satirlar=[(cls.profil, Decimal("1"))])
        operasyon_olustur(istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(cls.kesilmis, Decimal("1"))])
        operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk,
                          cikti_miktar=Decimal("1"), satirlar=[
                              (cls.bukulmus, Decimal("1")), (cls.civata, Decimal("2"))])

    def test_uc_seviyeli_zincir_oran_dogrulugu(self):
        sonuc = ihtiyac_hesapla([(self.mamul, Decimal("10"))])
        ozet = {o["stok"].pk: o for o in sonuc["ozet"]}
        self.assertEqual(ozet[self.bukulmus.pk]["toplam_miktar"], Decimal("10"))
        self.assertEqual(ozet[self.kesilmis.pk]["toplam_miktar"], Decimal("10"))
        self.assertEqual(ozet[self.profil.pk]["toplam_miktar"], Decimal("5"))     # 10/2
        self.assertEqual(ozet[self.civata.pk]["toplam_miktar"], Decimal("20"))    # 10*2
        self.assertNotIn(self.mamul.pk, ozet)                  # kök hariç tutulur

    def test_yaprak_dugumler_dogru_isaretli(self):
        sonuc = ihtiyac_hesapla([(self.mamul, Decimal("1"))])
        ozet = {o["stok"].pk: o for o in sonuc["ozet"]}
        self.assertTrue(ozet[self.profil.pk]["yaprak"])
        self.assertTrue(ozet[self.civata.pk]["yaprak"])
        self.assertFalse(ozet[self.kesilmis.pk]["yaprak"])
        self.assertIsNone(ozet[self.profil.pk]["istasyon"])
        self.assertEqual(ozet[self.kesilmis.pk]["istasyon"], self.lazer)

    def test_hedef_operasyonsuz_ise_kendisi_yaprak_olarak_doner(self):
        sonuc = ihtiyac_hesapla([(self.profil, Decimal("3"))])
        self.assertEqual(len(sonuc["agac"]), 1)
        self.assertTrue(sonuc["agac"][0]["yaprak"])
        self.assertEqual(sonuc["ozet"], [])                    # kök hariç, yaprağın altı yok

    def test_paylasilan_dal_ozette_birlesir(self):
        """İki bağımsız hedef aynı yaprağı (civata) paylaşıyorsa özet TEK satırda toplanır."""
        baska_mamul = _stok(self.kat, self.birim, kod="UT-MAMUL2", ad="başka merdiven", satis=True)
        operasyon_olustur(istasyon_id=self.montaj.pk, cikti_id=baska_mamul.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(self.civata, Decimal("3"))])
        sonuc = ihtiyac_hesapla([(self.mamul, Decimal("1")), (baska_mamul, Decimal("1"))])
        ozet = {o["stok"].pk: o for o in sonuc["ozet"]}
        self.assertEqual(ozet[self.civata.pk]["toplam_miktar"], Decimal("5"))     # 2 + 3

    def test_dongu_tespit_edilir(self):
        a = _stok(self.kat, self.birim, kod="UT-DONGU-A", ad="a")
        b = _stok(self.kat, self.birim, kod="UT-DONGU-B", ad="b")
        istasyon = _istasyon("DONGU")
        op_a = operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=a.pk,
                                 cikti_miktar=Decimal("1"), satirlar=[(b, Decimal("1"))])
        operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=b.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(a, Decimal("1"))])
        with self.assertRaises(UretimHatasi):
            ihtiyac_hesapla([(a, Decimal("1"))])

    def test_coklu_hedef_satiri_girisi(self):
        sonuc = ihtiyac_hesapla([(self.mamul, Decimal("2")), (self.kesilmis, Decimal("5"))])
        self.assertEqual(len(sonuc["agac"]), 2)

    def test_sifir_ve_negatif_miktar_atlanir(self):
        sonuc = ihtiyac_hesapla([(self.mamul, Decimal("0")), (self.mamul, Decimal("-1"))])
        self.assertEqual(sonuc["agac"], [])
        self.assertEqual(sonuc["ozet"], [])

    def test_hicbir_kayit_veya_hareket_uretmez(self):
        onceki_emir = UretimEmri.objects.count()
        onceki_kayit = OperasyonKaydi.objects.count()
        onceki_hareket = StokHareket.objects.count()
        ihtiyac_hesapla([(self.mamul, Decimal("10"))])
        self.assertEqual(UretimEmri.objects.count(), onceki_emir)
        self.assertEqual(OperasyonKaydi.objects.count(), onceki_kayit)
        self.assertEqual(StokHareket.objects.count(), onceki_hareket)


class UretimEmriServisTest(TestCase):
    """Üst-düzey tetikleyici: zincirdeki HER operasyon için ayrı TASLAK Operasyon Kaydı."""

    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.depo = _depo()
        cls.lazer = _istasyon("LAZER")
        cls.bukum = _istasyon("BUKUM")
        cls.profil = _stok(cls.kat, cls.birim, kod="UT-PROFIL", ad="ham profil", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UT-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UT-BUKULMUS", ad="bükülmüş parça", satis=True)
        cls.kesim_op = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk,
                                         cikti_miktar=Decimal("2"), satirlar=[(cls.profil, Decimal("1"))])
        cls.bukum_op = operasyon_olustur(istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk,
                                         cikti_miktar=Decimal("1"), satirlar=[(cls.kesilmis, Decimal("1"))])

    def test_hedef_operasyonsuz_reddedilir(self):
        cıplak = _stok(self.kat, self.birim, kod="UT-CIPLAK", ad="operasyonsuz", satis=True)
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(hedef_urun_id=cıplak.pk, hedef_miktar=Decimal("1"),
                                depo_id=self.depo.pk, tarih=date(2026, 1, 10))

    def test_zincirdeki_her_operasyon_icin_taslak_kayit_acilir(self):
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(emir.no, "UE-2026-0001")
        kayitlar = {k.operasyon_id: k for k in OperasyonKaydi.objects.filter(uretim_emri=emir)}
        self.assertEqual(len(kayitlar), 2)
        # bukum_op'un kaydı bukulmus'un kendisini üretir (hedef 10); kesim_op'un kaydı ise
        # o 10 bukulmus için gereken 10 KESİLMİŞ PARÇAYI üretir (hedef 10, cikti_miktar=2
        # olduğu için o kaydın KENDİ girdi satırında 5 profil gerektiği ortaya çıkar).
        self.assertEqual(kayitlar[self.bukum_op.pk].hedef_cikti_miktari, Decimal("10.000"))
        self.assertEqual(kayitlar[self.kesim_op.pk].hedef_cikti_miktari, Decimal("10.000"))
        kesim_girdi = kaydi_girdi_satirlari(kayitlar[self.kesim_op.pk]).get(girdi=self.profil)
        self.assertEqual(kesim_girdi.planlanan_miktar, Decimal("5.000"))
        self.assertTrue(all(k.durum == OperasyonKaydi.Durum.TASLAK for k in kayitlar.values()))
        self.assertTrue(all(k.depo_id == self.depo.pk for k in kayitlar.values()))

    def test_yaprak_dugumler_icin_kayit_acilmaz(self):
        uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("1"),
                            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertFalse(OperasyonKaydi.objects.filter(operasyon__cikti=self.profil).exists())

    def test_dongu_hatasinda_hicbir_seyi_kaydetmez(self):
        a = _stok(self.kat, self.birim, kod="UT-DA", ad="a", satis=True)
        b = _stok(self.kat, self.birim, kod="UT-DB", ad="b")
        istasyon = _istasyon("DONGU")
        operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=a.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(b, Decimal("1"))])
        operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=b.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(a, Decimal("1"))])
        onceki_emir = UretimEmri.objects.count()
        onceki_kayit = OperasyonKaydi.objects.count()
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(hedef_urun_id=a.pk, hedef_miktar=Decimal("1"),
                                depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(UretimEmri.objects.count(), onceki_emir)
        self.assertEqual(OperasyonKaydi.objects.count(), onceki_kayit)

    def test_kayitlar_birbirinden_bagimsiz_onaylanir(self):
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kesim_kaydi = OperasyonKaydi.objects.get(uretim_emri=emir, operasyon=self.kesim_op)
        bukum_kaydi = OperasyonKaydi.objects.get(uretim_emri=emir, operasyon=self.bukum_op)
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        operasyon_kaydi_onayla(kesim_kaydi)                    # yalnız kesim onaylanır
        kesim_kaydi.refresh_from_db()
        bukum_kaydi.refresh_from_db()
        self.assertEqual(kesim_kaydi.durum, OperasyonKaydi.Durum.ONAYLI)
        self.assertEqual(bukum_kaydi.durum, OperasyonKaydi.Durum.TASLAK)   # etkilenmedi

    def test_ilerleme(self):
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(uretim_emri_ilerleme(emir), {"toplam": 2, "onayli": 0})
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        kesim_kaydi = OperasyonKaydi.objects.get(uretim_emri=emir, operasyon=self.kesim_op)
        operasyon_kaydi_onayla(kesim_kaydi)
        self.assertEqual(uretim_emri_ilerleme(emir), {"toplam": 2, "onayli": 1})

    def test_sil_kalici_siler(self):
        """uretim_emri_sil: TASLAK'ta bağlı iki Operasyon Kaydı da hâlâ TASLAK'sa
        hiçbir iz kalmadan (hard delete) gider — CLAUDE.md'nin bu ekrana özel bilinçli
        istisnası (kullanıcı isteği)."""
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        emir_pk = emir.pk
        kayit_pks = list(OperasyonKaydi.objects.filter(uretim_emri=emir).values_list("pk", flat=True))
        self.assertEqual(len(kayit_pks), 2)
        uretim_emri_sil(emir)
        self.assertFalse(UretimEmri.objects.filter(pk=emir_pk).exists())
        self.assertFalse(OperasyonKaydi.objects.filter(pk__in=kayit_pks).exists())
        from core.models import OperasyonKaydiGirdi
        self.assertFalse(OperasyonKaydiGirdi.objects.filter(kayit_id__in=kayit_pks).exists())

    def test_sil_onayli_kayit_varsa_reddedilir(self):
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        kesim_kaydi = OperasyonKaydi.objects.get(uretim_emri=emir, operasyon=self.kesim_op)
        operasyon_kaydi_onayla(kesim_kaydi)
        with self.assertRaises(UretimHatasi):
            uretim_emri_sil(emir)
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())
        self.assertTrue(OperasyonKaydi.objects.filter(pk=kesim_kaydi.pk).exists())

    def test_sil_ayrica_soft_silinmis_taslak_kaydi_da_temizler(self):
        """Emrin bir kaydı daha önce ayrıca operasyon_kaydi_sil ile soft-iptal edilmiş
        olsa bile, emrin kendisi kalıcı silinince o da tamamen gider."""
        emir = uretim_emri_olustur(hedef_urun_id=self.bukulmus.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kesim_kaydi = OperasyonKaydi.objects.get(uretim_emri=emir, operasyon=self.kesim_op)
        operasyon_kaydi_sil(kesim_kaydi)
        uretim_emri_sil(emir)
        self.assertFalse(UretimEmri.objects.filter(pk=emir.pk).exists())
        self.assertFalse(OperasyonKaydi.objects.filter(pk=kesim_kaydi.pk).exists())


class UretimViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("uretyon", password="x")
        cls.bos = User.objects.create_user("uretbos", password="x")
        cls.istasyon_yetkili = User.objects.create_user("uretistyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.istasyon_yetkili, ekran_kod="is_istasyonlari")
        cls.operasyon_yetkili = User.objects.create_user("uretopyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.operasyon_yetkili, ekran_kod="operasyon_tanimlari")
        cls.hesapla_yetkili = User.objects.create_user("uretihyetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.hesapla_yetkili, ekran_kod="ihtiyac_hesapla")
        cls.emir_yetkili = User.objects.create_user("uretemiryetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.emir_yetkili, ekran_kod="uretim_emirleri")
        cls.kayit_yetkili = User.objects.create_user("uretkayityetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.kayit_yetkili, ekran_kod="operasyon_kayitlari")

        cls.birim = _birim()
        cls.kat = _kategori()
        cls.depo = _depo("UTVDEPO")
        cls.istasyon = _istasyon("LAZER")
        cls.profil = _stok(cls.kat, cls.birim, kod="UTV-PROFIL", ad="ham profil", satinalma=True)
        cls.mamul = _stok(cls.kat, cls.birim, kod="UTV-MAMUL", ad="test merdiveni", satis=True)
        cls.operasyon = operasyon_olustur(istasyon_id=cls.istasyon.pk, cikti_id=cls.mamul.pk,
                                          cikti_miktar=Decimal("1"), satirlar=[(cls.profil, Decimal("1"))])

    # --- İzin ayrımı: her ekran kendi yetki kodunu ister ---
    def _ekran_izin_testleri(self, url_adi, dogru_yetkili):
        r = self.client.get(reverse(url_adi))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse(url_adi)).status_code, 403)
        self.client.force_login(dogru_yetkili)
        self.assertEqual(self.client.get(reverse(url_adi)).status_code, 200)

    def test_is_istasyonlari_izinleri(self):
        self._ekran_izin_testleri("core:is_istasyonlari", self.istasyon_yetkili)

    def test_operasyon_tanimlari_izinleri(self):
        self._ekran_izin_testleri("core:operasyon_tanimlari", self.operasyon_yetkili)

    def test_ihtiyac_hesapla_izinleri(self):
        self._ekran_izin_testleri("core:ihtiyac_hesapla", self.hesapla_yetkili)

    def test_uretim_emirleri_izinleri(self):
        self._ekran_izin_testleri("core:uretim_emirleri", self.emir_yetkili)

    def test_operasyon_kayitlari_izinleri(self):
        self._ekran_izin_testleri("core:operasyon_kayitlari", self.kayit_yetkili)

    def test_ekranlar_arasi_izin_karismaz(self):
        self.client.force_login(self.istasyon_yetkili)
        self.assertEqual(self.client.get(reverse("core:operasyon_tanimlari")).status_code, 403)

    # --- Akışlar ---
    def test_operasyon_ekle_post(self):
        self.client.force_login(self.yon)
        girdi = _stok(self.kat, self.birim, kod="UTV-GIRDI2", ad="ek girdi", satinalma=True)
        cikti2 = _stok(self.kat, self.birim, kod="UTV-CIKTI2", ad="ikinci çıktı")
        gövde = {"istasyon": self.istasyon.pk, "cikti": cikti2.pk, "cikti_miktar": "1", "ad": "", "aciklama": ""}
        gövde.update({"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0",
                      "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
                      "satir-0-girdi": girdi.pk, "satir-0-miktar": "3"})
        r = self.client.post(reverse("core:operasyon_ekle"), gövde)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Operasyon.objects.filter(cikti=cikti2).exists())

    def test_ihtiyac_hesapla_post_sonuc_gosterir(self):
        self.client.force_login(self.yon)
        gövde = {"satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0",
                 "satir-MIN_NUM_FORMS": "0", "satir-MAX_NUM_FORMS": "1000",
                 "satir-0-hedef": self.mamul.pk, "satir-0-miktar": "10"}
        r = self.client.post(reverse("core:ihtiyac_hesapla"), gövde)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.profil.kod)
        self.assertContains(r, self.istasyon.kod)

    def test_uretim_emri_ekle_detay_ve_bagli_kayit_onayla_akisi(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:uretim_emri_ekle"), {
            "hedef_urun": self.mamul.pk, "hedef_miktar": "10", "depo": self.depo.pk,
            "tarih": "2026-01-10", "aciklama": ""})
        self.assertEqual(r.status_code, 302)
        emir = UretimEmri.objects.get(hedef_urun=self.mamul)
        self.assertEqual(emir.no, "UE-2026-0001")

        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, emir.no)

        kayit = OperasyonKaydi.objects.get(uretim_emri=emir)
        r = self.client.post(reverse("core:operasyon_kaydi_onayla", args=[kayit.pk]))
        self.assertEqual(r.status_code, 302)
        kayit.refresh_from_db()
        self.assertEqual(kayit.durum, OperasyonKaydi.Durum.ONAYLI)
        self.assertEqual(eldeki_miktar(self.mamul, self.depo), Decimal("10"))

    def test_uretim_emri_ekle_operasyonsuz_urun_secenek_olarak_gelmez(self):
        self.client.force_login(self.yon)
        cıplak = _stok(self.kat, self.birim, kod="UTV-CIPLAK", ad="operasyonsuz", satis=True)
        r = self.client.get(reverse("core:uretim_emri_ekle"))
        self.assertNotContains(r, "UTV-CIPLAK")

    def test_uretim_emri_sil_view_kalici_siler(self):
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(hedef_urun_id=self.mamul.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        r = self.client.post(reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertRedirects(r, reverse("core:uretim_emirleri"))
        self.assertFalse(UretimEmri.objects.filter(pk=emir.pk).exists())

    def test_uretim_emri_sil_view_onayliyken_engellenir_hata_gosterir(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(hedef_urun_id=self.mamul.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kayit = OperasyonKaydi.objects.get(uretim_emri=emir)
        operasyon_kaydi_onayla(kayit)
        r = self.client.post(reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertRedirects(r, reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())

    def test_uretim_emirleri_listesi_arama_ve_sil_dugmesi(self):
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(hedef_urun_id=self.mamul.pk, hedef_miktar=Decimal("10"),
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, emir.no)
        self.assertContains(r, reverse("core:uretim_emri_sil", args=[emir.pk]))
        r2 = self.client.get(reverse("core:uretim_emirleri"), {"ara": "yok-boyle-bir-sey"})
        self.assertNotContains(r2, emir.no)

    def test_operasyon_kaydi_ekle_bagimsiz(self):
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:operasyon_kaydi_ekle"), {
            "operasyon": self.operasyon.pk, "hedef_cikti_miktari": "1", "depo": self.depo.pk,
            "tarih": "2026-01-10", "aciklama": ""})
        self.assertEqual(r.status_code, 302)
        kayit = OperasyonKaydi.objects.get(operasyon=self.operasyon)
        self.assertIsNone(kayit.uretim_emri)

    def test_menude_uretim_ekranlari_gorunur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:kullanici_listesi"))
        self.assertContains(r, "Üretim")
        self.assertContains(r, "İş İstasyonları")
        self.assertContains(r, "Operasyon Tanımları")
        self.assertContains(r, "İhtiyaç Hesapla")
        self.assertContains(r, "Üretim Emirleri")
        self.assertContains(r, "Operasyon Kayıtları")
