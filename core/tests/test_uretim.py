"""ÜRETİM modülü — İş İstasyonu + Operasyon (rota) modeli. Bir Operasyon, bir iş istasyonunda,
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
    Birim, Depo, EkranYetki, IsIstasyonu, IstasyonEmri, Kategori, Operasyon, OperasyonKaydi, Stok,
    StokAyirma, StokHareket, UretimEmri, UretimEmriKalemi, UretimEmriRevizyon, YevmiyeFisi,
)
from core.services import stok_ayirma
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import (
    UretimHatasi, aktif_istasyonlar, aktif_operasyonlar, ihtiyac_hesapla,
    istasyon_guncelle, istasyon_olustur, istasyon_sil,
    kaydi_girdi_satirlari, operasyon_girdileri, operasyon_guncelle, operasyon_kaydi_girdi_guncelle,
    operasyon_kaydi_olustur, operasyon_kaydi_onayla, operasyon_kaydi_sil, operasyon_olustur,
    emir_hedef_cikti, operasyon_sil, uretim_emri_ilerleme, uretim_emri_iptal, uretim_emri_olustur, uretim_emri_sil,
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


def _hedefler(emir):
    """{operasyon pk: referans çıktı hedefi} — emrin istasyon emirleri (planlanan çalıştırma × referans miktar)."""
    return {ie.operasyon_id: emir_hedef_cikti(ie) for ie in emir.istasyon_emirleri.filter(silindi=False)}


def _kayit_ac(emir, operasyon, tarih=date(2026, 1, 10)):
    """Emrin istasyon emri hedefiyle bağlı taslak operasyon kaydı açar (emirden kayıt açma servisi adım 5'te gelir)."""
    ie = emir.istasyon_emirleri.get(operasyon=operasyon)
    return operasyon_kaydi_olustur(operasyon_id=operasyon.pk, depo_id=emir.depo_id, tarih=tarih, hedef_cikti_miktari=emir_hedef_cikti(ie),
                                   uretim_emri=emir)


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
            satirlar=[(self.girdi, Decimal("1"))])
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
        from core.models import FaturaTipi, HesapPlani, KategoriHesap
        cls.birim = _birim()
        cls.kat = _kategori()
        # Onayda maliyet aktarım fişi için stok hesabı (aynı kategori/hesap -> net sıfır, fiş yok)
        hesap = HesapPlani.objects.create(
            hesap_kodu="150.10", hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV",
            parasal=True, aktif=True)
        KategoriHesap.objects.create(
            kategori=cls.kat, hesap=hesap,
            fatura_tipi=FaturaTipi.objects.create(ad="ALIŞ ÜT", yon="ALIS"))
        cls.depo = _depo()
        cls.lazer = _istasyon("LAZER")
        cls.bukum = _istasyon("BUKUM")
        cls.profil = _stok(cls.kat, cls.birim, kod="UT-PROFIL", ad="ham profil", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UT-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UT-BUKULMUS", ad="bükülmüş parça")
        cls.kesim_op = operasyon_olustur(
            istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=Decimal("2"),
            satirlar=[(cls.profil, Decimal("1"))])
        cls.bukum_op = operasyon_olustur(
            istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk, cikti_miktar=Decimal("1"),
            satirlar=[(cls.kesilmis, Decimal("1"))])

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
                            giris_tutar_try=(Decimal(miktar) * Decimal(birim_maliyet_try)).quantize(
                                Decimal("0.01")))

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
        self.assertEqual(cikti_hareketi.birim_maliyet_try, Decimal("5.000000"))
        self.assertEqual(cikti_hareketi.miktar, Decimal("10.000"))
        self.assertEqual(cikti_hareketi.maliyet_durumu, StokHareket.MaliyetDurumu.KESIN)

    def test_onayla_iki_katmandan_karisik_fifo_ile_cikti_maliyeti(self):
        """3 profil @10 TL + 10 profil @20 TL; ağırlıklı ortalama (30+200)/13; 10 kesilmiş parça
        hedefi 5 profil gerektirir (5 x 17,692308 = 88,46 TL) -> çıktı birim maliyeti 8,846 TL."""
        self._stok_gir_maliyetli(self.profil, "3", "10", tarih=date(2026, 1, 1))
        self._stok_gir_maliyetli(self.profil, "10", "20", tarih=date(2026, 1, 2))
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertEqual(cikti_hareketi.birim_maliyet_try, Decimal("8.846000"))
        self.assertEqual(cikti_hareketi.giris_tutar_try, Decimal("88.46"))

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
        self.assertEqual(cikti_hareketi.birim_maliyet_try, Decimal("5.000000"))
        self.assertEqual(cikti_hareketi.maliyet_durumu, StokHareket.MaliyetDurumu.KESIN)

    def test_onayla_girdi_katmani_yoksa_cikti_katmansiz_kalir(self):
        """Girdi hiç maliyetli değilse çıktı için 0 TL YAZILMAZ — katman hiç açılmaz."""
        self._stok_gir(self.profil, "1000")     # maliyetsiz
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertIsNone(cikti_hareketi.giris_tutar_try)       # 0 TL YAZILMAZ: maliyet bilinmiyor

    def test_onayla_kismi_karsilamada_tahmini_isaretlenir(self):
        """5 profil gerekiyor: 2 adet @10 TL faturalı + 3 adet fiyatsız (geçici: ortalamayla
        değerlenir). Çıktı maliyeti 5x10/10 = 5 TL hesaplanır ama tahmini=True işaretlenir."""
        self._stok_gir_maliyetli(self.profil, "2", "10")
        self._stok_gir(self.profil, "3")        # maliyetsiz, ama fiziksel stok yeterli olsun
        kayit = operasyon_kaydi_olustur(operasyon_id=self.kesim_op.pk, depo_id=self.depo.pk,
                                        tarih=date(2026, 1, 10), hedef_cikti_miktari=Decimal("10"))
        operasyon_kaydi_onayla(kayit)
        cikti_hareketi = StokHareket.objects.get(
            stok=self.kesilmis, kaynak=StokHareket.Kaynak.URETIM, tur=StokHareket.Tur.GIRIS)
        self.assertEqual(cikti_hareketi.birim_maliyet_try, Decimal("5.000000"))   # 5x10 / 10
        self.assertEqual(cikti_hareketi.maliyet_durumu, StokHareket.MaliyetDurumu.GECICI)

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
        self.assertEqual(cikti_hareketi.maliyet_durumu, StokHareket.MaliyetDurumu.GECICI)


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
            uretim_emri_olustur(kalemler=[{"hedef_urun_id": cıplak.pk, "hedef_miktar": Decimal("1")}],
                                depo_id=self.depo.pk, tarih=date(2026, 1, 10))

    def test_zincirdeki_her_operasyon_icin_istasyon_emri_acilir(self):
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(emir.no, "ÜS-2026-0001")
        self.assertFalse(OperasyonKaydi.objects.filter(uretim_emri=emir).exists())        # kayıtlar istasyon emrinden açılır
        iemirleri = {ie.operasyon_id: ie for ie in emir.istasyon_emirleri.all()}
        self.assertEqual(len(iemirleri), 2)
        # bukum_op bukulmus'un kendisini üretir (10); kesim_op o 10 bukulmus için 10 KESİLMİŞ parça üretir
        # (cikti_miktar=2 → 5 çalıştırma; kayıt hedefi 10, girdide 5 profil).
        self.assertEqual((iemirleri[self.bukum_op.pk].planlanan, iemirleri[self.kesim_op.pk].planlanan), (Decimal("10"), Decimal("5")))
        self.assertEqual(_hedefler(emir), {self.bukum_op.pk: Decimal("10"), self.kesim_op.pk: Decimal("10")})
        self.assertLess(iemirleri[self.bukum_op.pk].seviye, iemirleri[self.kesim_op.pk].seviye)   # mamule yakın op daha üst seviye (düşük no)
        self.assertTrue(all(ie.durum == "BEKLIYOR" and ie.tamamlanan == 0 and ie.istasyon_id == ie.operasyon.istasyon_id for ie in iemirleri.values()))
        self.assertEqual([r.tur for r in emir.revizyonlar.all()], ["ACILIS"])
        self.assertEqual(emir.istasyon_emirleri.first().no[:8], "IE-2026-")
        kayit = _kayit_ac(emir, self.kesim_op)                                            # kayıt hedefi/girdisi eski davranışla aynı
        self.assertEqual((kayit.hedef_cikti_miktari, kaydi_girdi_satirlari(kayit).get(girdi=self.profil).planlanan_miktar),
                         (Decimal("10.000"), Decimal("5.000")))

    def test_yaprak_dugumler_icin_kayit_acilmaz(self):
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("1")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertFalse(emir.istasyon_emirleri.filter(operasyon__cikti=self.profil).exists())

    def test_dongu_hatasinda_hicbir_seyi_kaydetmez(self):
        a = _stok(self.kat, self.birim, kod="UT-DA", ad="a", satis=True)
        b = _stok(self.kat, self.birim, kod="UT-DB", ad="b")
        istasyon = _istasyon("DONGU")
        operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=a.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(b, Decimal("1"))])
        operasyon_olustur(istasyon_id=istasyon.pk, cikti_id=b.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(a, Decimal("1"))])
        onceki_emir = UretimEmri.objects.count()
        onceki_ie = IstasyonEmri.objects.count()
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(kalemler=[{"hedef_urun_id": a.pk, "hedef_miktar": Decimal("1")}],
                                depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(UretimEmri.objects.count(), onceki_emir)
        self.assertEqual(IstasyonEmri.objects.count(), onceki_ie)

    def test_kayitlar_birbirinden_bagimsiz_onaylanir(self):
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kesim_kaydi = _kayit_ac(emir, self.kesim_op)
        bukum_kaydi = _kayit_ac(emir, self.bukum_op)
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        operasyon_kaydi_onayla(kesim_kaydi)                    # yalnız kesim onaylanır
        kesim_kaydi.refresh_from_db()
        bukum_kaydi.refresh_from_db()
        self.assertEqual(kesim_kaydi.durum, OperasyonKaydi.Durum.ONAYLI)
        self.assertEqual(bukum_kaydi.durum, OperasyonKaydi.Durum.TASLAK)   # etkilenmedi

    def test_ilerleme(self):
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(uretim_emri_ilerleme(emir), {"toplam": 2, "onayli": 0})
        IstasyonEmri.objects.filter(uretim_emri=emir, operasyon=self.kesim_op).update(durum=IstasyonEmri.Durum.BITTI)   # biten emir sayısı
        self.assertEqual(uretim_emri_ilerleme(emir), {"toplam": 2, "onayli": 1})
        IstasyonEmri.objects.filter(uretim_emri=emir, operasyon=self.bukum_op).update(durum=IstasyonEmri.Durum.IPTAL)  # iptal sayılmaz
        self.assertEqual(uretim_emri_ilerleme(emir), {"toplam": 1, "onayli": 1})

    def test_sil_kalici_siler(self):
        """uretim_emri_sil: TASLAK'ta bağlı iki Operasyon Kaydı da hâlâ TASLAK'sa
        hiçbir iz kalmadan (hard delete) gider — CLAUDE.md'nin bu ekrana özel bilinçli
        istisnası (kullanıcı isteği)."""
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        emir_pk = emir.pk
        kayit_pks = [_kayit_ac(emir, self.kesim_op).pk, _kayit_ac(emir, self.bukum_op).pk]
        ie_pks = list(emir.istasyon_emirleri.values_list("pk", flat=True))
        self.assertEqual((len(kayit_pks), len(ie_pks)), (2, 2))
        uretim_emri_sil(emir)
        self.assertFalse(UretimEmri.objects.filter(pk=emir_pk).exists())
        self.assertFalse(IstasyonEmri.objects.filter(pk__in=ie_pks).exists())
        self.assertFalse(UretimEmriRevizyon.objects.filter(uretim_emri_id=emir_pk).exists())
        self.assertFalse(OperasyonKaydi.objects.filter(pk__in=kayit_pks).exists())
        from core.models import OperasyonKaydiGirdi
        self.assertFalse(OperasyonKaydiGirdi.objects.filter(kayit_id__in=kayit_pks).exists())

    def test_sil_onayli_kayit_varsa_reddedilir(self):
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        kesim_kaydi = _kayit_ac(emir, self.kesim_op)
        operasyon_kaydi_onayla(kesim_kaydi)
        with self.assertRaises(UretimHatasi):
            uretim_emri_sil(emir)
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())
        self.assertTrue(OperasyonKaydi.objects.filter(pk=kesim_kaydi.pk).exists())

    def test_sil_ayrica_soft_silinmis_taslak_kaydi_da_temizler(self):
        """Emrin bir kaydı daha önce ayrıca operasyon_kaydi_sil ile soft-iptal edilmiş
        olsa bile, emrin kendisi kalıcı silinince o da tamamen gider."""
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kesim_kaydi = _kayit_ac(emir, self.kesim_op)
        operasyon_kaydi_sil(kesim_kaydi)
        uretim_emri_sil(emir)
        self.assertFalse(UretimEmri.objects.filter(pk=emir.pk).exists())
        self.assertFalse(OperasyonKaydi.objects.filter(pk=kesim_kaydi.pk).exists())

    def test_baslamis_siparis_silinmez_iptal_edilir(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        IstasyonEmri.objects.filter(uretim_emri=emir, operasyon=self.kesim_op).update(durum=IstasyonEmri.Durum.BASLADI, tamamlanan=Decimal("1"))
        with self.assertRaisesMessage(UretimHatasi, "başlamış"):
            uretim_emri_sil(emir)
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())
        taslak = _kayit_ac(emir, self.bukum_op)
        stok_ayirma.ayirma_ayarla(emir, self.profil, "3")
        self.assertTrue(StokAyirma.objects.filter(uretim_emri=emir, silindi=False).exists())
        uretim_emri_iptal(emir)
        emir.refresh_from_db()
        taslak.refresh_from_db()
        self.assertEqual((emir.durum, emir.revizyon_no, taslak.silindi), (UretimEmri.Durum.IPTAL, 1, True))
        self.assertEqual(sorted(emir.istasyon_emirleri.values_list("durum", flat=True)), ["IPTAL", "IPTAL"])
        self.assertFalse(StokAyirma.objects.filter(uretim_emri=emir, silindi=False).exists())       # satırın kendisi kapandı (ayrilan_miktar durumdan 0 döner)
        self.assertEqual([r.tur for r in emir.revizyonlar.all()], ["ACILIS", "IPTAL"])
        with self.assertRaisesMessage(UretimHatasi, "Yalnız açık"):                                  # iptal edilmiş (başlamış) sipariş kalıcı silinemez
            uretim_emri_sil(emir)
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())
        with self.assertRaises(UretimHatasi):                                           # iptal edilmiş sipariş tekrar iptal edilemez
            uretim_emri_iptal(emir)

    def test_baslamamis_siparis_ayirmasiyla_silinir(self):
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        stok_ayirma.ayirma_ayarla(emir, self.profil, "3")
        uretim_emri_sil(emir)
        self.assertFalse(StokAyirma.objects.filter(uretim_emri_id=emir.pk).exists())

    def test_net_plan_eldeki_stogu_ayirir(self):
        """Eldeki ayrılmamış stok her seviyede düşülür: 4 bükülmüş eldeki → bukum emri 6; 2 KESİLMİŞ eldeki → kesim emri 2 çalıştırma."""
        hareket_ekle(stok_id=self.bukulmus.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=Decimal("4"))
        hareket_ekle(stok_id=self.kesilmis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=Decimal("2"))
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1), tur=StokHareket.Tur.GIRIS, miktar=Decimal("100"))
        emir = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
                                   depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        ay = {a.stok.kod: a.miktar for a in stok_ayirma.emir_ayirmalari(emir)}
        self.assertEqual(ay, {"UT-BUKULMUS": Decimal("4"), "UT-KESILMIS": Decimal("2"), "UT-PROFIL": Decimal("2")})   # kesilmiş net 4 → 2 çalıştırma → profil 2
        self.assertEqual({ie.operasyon_id: ie.planlanan for ie in emir.istasyon_emirleri.all()}, {self.bukum_op.pk: Decimal("6"), self.kesim_op.pk: Decimal("2")})
        self.assertEqual(emir.kalemler.get().eldeki_ayrilan, Decimal("4"))
        # ikinci sipariş aynı stoğu göremez: kullanılabilir 0 → tamamı üretime
        emir2 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("10")}],
                                    depo_id=self.depo.pk, tarih=date(2026, 1, 11))
        self.assertEqual(emir2.kalemler.get().eldeki_ayrilan, Decimal("0"))
        self.assertEqual(emir2.istasyon_emirleri.get(operasyon=self.bukum_op).planlanan, Decimal("10"))
        # tamamen stoktan karşılanan kalem: istasyon emri yok, yalnız ayırma
        hareket_ekle(stok_id=self.bukulmus.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur=StokHareket.Tur.GIRIS, miktar=Decimal("50"))
        emir3 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("5")}],
                                    depo_id=self.depo.pk, tarih=date(2026, 1, 12))
        self.assertEqual((emir3.istasyon_emirleri.count(), emir3.kalemler.get().eldeki_ayrilan), (0, Decimal("5")))


class UretimEmriCokluKalemServisTest(TestCase):
    """'Tek emir, çoklu kalem': UretimEmri artık birden çok hedef ürün/miktar satırı
    (UretimEmriKalemi) taşıyabilir. IhtiyacHesaplaTest ile aynı üç basamaklı zincir
    (Kesim 1->2, Büküm 1->1, Montaj 1 bükülmüş + 2 civata -> 1 mamul)."""

    @classmethod
    def setUpTestData(cls):
        cls.birim = _birim()
        cls.kat = _kategori()
        cls.depo = _depo()
        cls.lazer = _istasyon("LAZER")
        cls.bukum = _istasyon("BUKUM")
        cls.montaj = _istasyon("MONTAJ")
        cls.profil = _stok(cls.kat, cls.birim, kod="UTC-PROFIL", ad="ham profil", satinalma=True)
        cls.civata = _stok(cls.kat, cls.birim, kod="UTC-CIVATA", ad="civata", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UTC-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UTC-BUKULMUS", ad="bükülmüş parça", satis=True)
        cls.mamul = _stok(cls.kat, cls.birim, kod="UTC-MAMUL", ad="test merdiveni", satis=True)
        cls.lazer_op = operasyon_olustur(istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk,
                                         cikti_miktar=Decimal("2"), satirlar=[(cls.profil, Decimal("1"))])
        cls.bukum_op = operasyon_olustur(istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk,
                                         cikti_miktar=Decimal("1"), satirlar=[(cls.kesilmis, Decimal("1"))])
        cls.montaj_op = operasyon_olustur(istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk,
                                          cikti_miktar=Decimal("1"), satirlar=[
                                              (cls.bukulmus, Decimal("1")), (cls.civata, Decimal("2"))])

    def test_iki_bagimsiz_kok_ayri_kayit_acar(self):
        montaj2 = _istasyon("MONTAJ2")
        civata2 = _stok(self.kat, self.birim, kod="UTC-CIVATA2", ad="ikinci civata", satinalma=True)
        baska_mamul = _stok(self.kat, self.birim, kod="UTC-MAMUL2", ad="başka merdiven", satis=True)
        montaj2_op = operasyon_olustur(istasyon_id=montaj2.pk, cikti_id=baska_mamul.pk,
                                       cikti_miktar=Decimal("1"), satirlar=[(civata2, Decimal("1"))])
        emir = uretim_emri_olustur(kalemler=[
            {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("2")},
            {"hedef_urun_id": baska_mamul.pk, "hedef_miktar": Decimal("3")},
        ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        h = _hedefler(emir)
        self.assertEqual(len(h), 4)                                # MONTAJ, MONTAJ2, BUKUM, LAZER
        self.assertEqual(h[self.montaj_op.pk], Decimal("2"))
        self.assertEqual(h[montaj2_op.pk], Decimal("3"))
        self.assertEqual(h[self.bukum_op.pk], Decimal("2"))
        self.assertEqual(h[self.lazer_op.pk], Decimal("2"))

    def test_paylasilan_ara_bilesen_tek_kayitta_toplanir(self):
        """İki farklı kalem (mamul + ikinci_mamul) aynı ara bileşeni (kesilmiş parça)
        paylaşıyorsa, o bileşen için TEK OperasyonKaydi açılır (toplam miktarla)."""
        ikinci_mamul = _stok(self.kat, self.birim, kod="UTC-MAMUL3", ad="direkt kesilmiş kullanan",
                             satis=True)
        operasyon_olustur(istasyon_id=self.montaj.pk, cikti_id=ikinci_mamul.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(self.kesilmis, Decimal("2"))])
        emir = uretim_emri_olustur(kalemler=[
            {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("2")},
            {"hedef_urun_id": ikinci_mamul.pk, "hedef_miktar": Decimal("3")},
        ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        lazer = emir.istasyon_emirleri.filter(operasyon=self.lazer_op)
        self.assertEqual(lazer.count(), 1)                          # tek, birleşik istasyon emri
        # mamul->bukulmus->kesilmis: 2; ikinci_mamul->kesilmis (direkt): 3*2=6 => toplam 8
        self.assertEqual(emir_hedef_cikti(lazer.first()), Decimal("8"))

    def test_kok_urun_baska_kokun_ara_bileseni_de_ise_tek_kayitta_toplanir(self):
        """En kritik senaryo: bukulmus hem KENDİ kalemi olarak doğrudan sipariş edilmiş,
        hem de mamul'ün ara bileşeni — BUKUM operasyonu için ÇAKIŞAN iki ayrı kayıt değil,
        toplam miktarlı TEK kayıt açılmalı."""
        emir = uretim_emri_olustur(kalemler=[
            {"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("4")},
            {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("6")},
        ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        h = _hedefler(emir)
        self.assertEqual(len(h), 3)                                 # BUKUM, MONTAJ, LAZER — çakışma yok
        self.assertEqual(h[self.bukum_op.pk], Decimal("10"))        # 4 + 6
        self.assertEqual(h[self.montaj_op.pk], Decimal("6"))
        self.assertEqual(h[self.lazer_op.pk], Decimal("10"))

    def test_ayni_urun_iki_kalemde_tekrar_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(kalemler=[
                {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("1")},
                {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("2")},
            ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))

    def test_bir_kalem_operasyonsuzsa_tum_emir_reddedilir_atomik(self):
        cıplak = _stok(self.kat, self.birim, kod="UTC-CIPLAK", ad="operasyonsuz", satis=True)
        onceki_emir = UretimEmri.objects.count()
        onceki_ie = IstasyonEmri.objects.count()
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(kalemler=[
                {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("1")},
                {"hedef_urun_id": cıplak.pk, "hedef_miktar": Decimal("1")},
            ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        self.assertEqual(UretimEmri.objects.count(), onceki_emir)
        self.assertEqual(IstasyonEmri.objects.count(), onceki_ie)

    def test_kalem_sayisi_kadar_uretimemrikalemi_olusur(self):
        emir = uretim_emri_olustur(kalemler=[
            {"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("2")},
            {"hedef_urun_id": self.bukulmus.pk, "hedef_miktar": Decimal("1")},
        ], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kalemler = list(UretimEmriKalemi.objects.filter(uretim_emri=emir).order_by("sira"))
        self.assertEqual(len(kalemler), 2)
        self.assertEqual([k.hedef_urun_id for k in kalemler], [self.mamul.pk, self.bukulmus.pk])
        self.assertEqual([k.sira for k in kalemler], [10, 20])

    def test_bos_kalem_listesi_reddedilir(self):
        with self.assertRaises(UretimHatasi):
            uretim_emri_olustur(kalemler=[], depo_id=self.depo.pk, tarih=date(2026, 1, 10))


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
            "depo": self.depo.pk, "tarih": "2026-01-10", "aciklama": "",
            "satir-TOTAL_FORMS": "1", "satir-INITIAL_FORMS": "0",
            "satir-MIN_NUM_FORMS": "1", "satir-MAX_NUM_FORMS": "1000",
            "satir-0-hedef_urun": self.mamul.pk, "satir-0-hedef_miktar": "10"})
        self.assertEqual(r.status_code, 302)
        emir = UretimEmri.objects.get(kalemler__hedef_urun=self.mamul)
        self.assertEqual(emir.no, "ÜS-2026-0001")

        r = self.client.get(reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, emir.no)

        self.assertEqual(emir.istasyon_emirleri.count(), 1)
        kayit = _kayit_ac(emir, self.operasyon)
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

    def test_uretim_emri_ekle_coklu_kalem_ile_tek_emir_acar(self):
        self.client.force_login(self.yon)
        ikinci = _stok(self.kat, self.birim, kod="UTV-MAMUL2", ad="ikinci mamul", satis=True)
        operasyon_olustur(istasyon_id=self.istasyon.pk, cikti_id=ikinci.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(self.profil, Decimal("1"))])
        r = self.client.post(reverse("core:uretim_emri_ekle"), {
            "depo": self.depo.pk, "tarih": "2026-01-10", "aciklama": "",
            "satir-TOTAL_FORMS": "2", "satir-INITIAL_FORMS": "0",
            "satir-MIN_NUM_FORMS": "1", "satir-MAX_NUM_FORMS": "1000",
            "satir-0-hedef_urun": self.mamul.pk, "satir-0-hedef_miktar": "2",
            "satir-1-hedef_urun": ikinci.pk, "satir-1-hedef_miktar": "3"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(UretimEmri.objects.count(), 1)
        emir = UretimEmri.objects.first()
        self.assertEqual(emir.kalemler.count(), 2)

    def test_uretim_emri_sil_view_kalici_siler(self):
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        r = self.client.post(reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertRedirects(r, reverse("core:uretim_emirleri"))
        self.assertFalse(UretimEmri.objects.filter(pk=emir.pk).exists())

    def test_uretim_emri_sil_view_onayliyken_engellenir_hata_gosterir(self):
        hareket_ekle(stok_id=self.profil.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 1),
                    tur=StokHareket.Tur.GIRIS, miktar=Decimal("1000"))
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("10")}],
            depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        kayit = _kayit_ac(emir, self.operasyon)
        operasyon_kaydi_onayla(kayit)
        r = self.client.post(reverse("core:uretim_emri_sil", args=[emir.pk]))
        self.assertRedirects(r, reverse("core:uretim_emri_detay", args=[emir.pk]))
        self.assertTrue(UretimEmri.objects.filter(pk=emir.pk).exists())

    def test_uretim_emirleri_listesi_arama_ve_sil_dugmesi(self):
        self.client.force_login(self.yon)
        emir = uretim_emri_olustur(
            kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": Decimal("10")}],
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
        self.assertContains(r, "Üretim Siparişleri")
        self.assertContains(r, "İstasyon Emirleri")
        self.assertContains(r, "Operasyon Kayıtları")
