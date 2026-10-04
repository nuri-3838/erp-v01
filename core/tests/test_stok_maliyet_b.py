"""Dilim B: stok hesabı yalnız 15x, depo transferi (maliyeti değiştirmez), üretim onayında 15x→15x
maliyet aktarım fişi + zincirleme güncelleme, satış/satış iadesi maliyet fişi (620/621/623),
otomatik fişlerin elle düzenlenememesi."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import (EkranYetki, Fatura, FaturaTipi, HesapPlani, Kategori, KategoriHesap,
                         Kur, Stok, StokHareket, YevmiyeFisi)
from core.services import depo_transfer
from core.services.fatura import FaturaHatasi, fatura_olustur, fatura_sil
from core.services.hareket import HareketHatasi, hareket_sil
from core.services.kategori import KategoriHatasi, stok_muhasebe_hesabi
from core.services.uretim import (operasyon_kaydi_olustur, operasyon_kaydi_onayla,
                                  operasyon_olustur)
from core.tests.test_fatura import FaturaTestTemel, _hesap
from core.tests.test_uretim import _istasyon

D = datetime.date


class MaliyetBTemel(FaturaTestTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for t in (D(2026, 3, 1), D(2026, 3, 5), D(2026, 3, 12)):
            Kur.objects.create(tarih=t, usd_alis=Decimal("30"))
        for kod, ad in (("150.10", "İLK MADDE"), ("151.10", "YARI MAMUL"), ("152.10", "MAMUL"),
                        ("610.10", "SATIŞTAN İADELER")):
            _hesap(kod, ad)
        for kod, ad in (("620", "SATILAN MAMULLER MALİYETİ"), ("621", "SATILAN TİCARİ MALLAR MALİYETİ"),
                        ("623", "DİĞER SATIŞLARIN MALİYETİ")):
            _hesap(kod, ad, grup="GELIR_TABLOSU", kalem="C")
        cls.satis.maliyet_fisi = FaturaTipi.MaliyetFisi.SATIS
        cls.satis.save()
        cls.iade = FaturaTipi.objects.create(
            ad="ALIŞ FATURASI-SATIŞ İADE", yon=FaturaTipi.Yon.ALIS,
            maliyet_fisi=FaturaTipi.MaliyetFisi.SATIS_IADE)
        adet = cls.stok.uretim_birimi
        cls.kart = {}
        for anahtar, hesap, kod in (("ham", "150.10", "150-10-9001"), ("yari", "151.10", "151-10-9001"),
                                    ("yari2", "151.10", "151-10-9002"), ("mamul", "152.10", "152-10-9001")):
            kat = Kategori.objects.create(ad=f"K-{anahtar}", kod=f"9{len(cls.kart)}")
            for tip, h in ((cls.alis, hesap), (cls.satis, "600")):
                KategoriHesap.objects.create(kategori=kat, fatura_tipi=tip,
                                             hesap=HesapPlani.objects.get(hesap_kodu=h))
            cls.kart[anahtar] = Stok.objects.create(
                kod=kod, ad=anahtar.upper(), kategori=kat, uretim_birimi=adet, fatura_birimi=adet,
                kdv=cls.kdv, satis_urunu=True)
        # ticari mal kartının kategorisine satış iadesi tipi eşlemesi (610: stok hesabı SAYILMAZ)
        KategoriHesap.objects.create(kategori=cls.alt, fatura_tipi=cls.iade,
                                     hesap=HesapPlani.objects.get(hesap_kodu="610.10"))
        from core.services.depo import depo_olustur
        cls.da = depo_olustur(kod="DA", ad="DEPO A")
        cls.db = depo_olustur(kod="DB", ad="DEPO B")

    def alis_yap(self, tarih, miktar, fiyat, stok=None, depo=None):
        return fatura_olustur(
            tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=tarih,
            satirlar=[{"stok_id": (stok or self.stok).pk, "miktar": miktar, "birim_fiyat": fiyat}],
            depo_id=(depo or self.da).pk)

    def satis_yap(self, tarih, miktar, stok=None, tip=None, cari=None, fiyat="500"):
        return fatura_olustur(
            tip_id=(tip or self.satis).pk, cari_id=(cari or self.musteri).pk, tarih=tarih,
            satirlar=[{"stok_id": (stok or self.stok).pk, "miktar": miktar, "birim_fiyat": fiyat}],
            depo_id=self.da.pk)

    @staticmethod
    def fis_satirlari(fis):
        return {s.hesap_id: (s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False)}


class StokHesabiTest(MaliyetBTemel):
    def test_yalniz_15x_hesaplari_stok_hesabi_sayilir(self):
        # ticari mal kategorisinin hem 153.10 (alış) hem 610.10 (satış iadesi) eşlemesi var
        self.assertEqual(stok_muhasebe_hesabi(self.stok).hesap_kodu, "153.10")

    def test_yalniz_610_eslemesi_varsa_hata(self):
        kat = Kategori.objects.create(ad="SADECE İADE", kod="98")
        KategoriHesap.objects.create(kategori=kat, fatura_tipi=self.iade,
                                     hesap=HesapPlani.objects.get(hesap_kodu="610.10"))
        st = Stok.objects.create(kod="X-1", ad="X", kategori=kat, kdv=self.kdv,
                                 uretim_birimi=self.stok.uretim_birimi,
                                 fatura_birimi=self.stok.uretim_birimi)
        with self.assertRaises(KategoriHatasi):
            stok_muhasebe_hesabi(st)


class DepoTransferiTest(MaliyetBTemel):
    def test_transfer_maliyeti_degistirmez_fis_uretmez_depo_bakiyesi_tasinir(self):
        from core.services.hareket import eldeki_miktar
        self.alis_yap(D(2026, 3, 5), "10", "100")
        fis_sayisi = YevmiyeFisi.objects.count()
        grup = depo_transfer.depo_transferi_yap(
            stok_id=self.stok.pk, kaynak_depo_id=self.da.pk, hedef_depo_id=self.db.pk,
            tarih=D(2026, 3, 10), miktar="4")
        self.assertEqual((eldeki_miktar(self.stok, self.da), eldeki_miktar(self.stok, self.db)),
                         (Decimal("6.000"), Decimal("4.000")))
        self.stok.refresh_from_db()
        self.assertEqual((self.stok.maliyet_miktar, self.stok.maliyet_deger_try,
                          self.stok.ort_maliyet_try),
                         (Decimal("10.000"), Decimal("1000.00"), Decimal("100.000000")))
        self.assertEqual(YevmiyeFisi.objects.count(), fis_sayisi)
        bacaklar = StokHareket.objects.filter(transfer_grubu=grup)
        self.assertEqual(sorted(b.tur for b in bacaklar), ["CIKIS", "GIRIS"])
        self.assertTrue(all(b.kaynak == "TRANSFER" for b in bacaklar))

    def test_yetersiz_stok_ve_ayni_depo_reddedilir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        with self.assertRaises(HareketHatasi):
            depo_transfer.depo_transferi_yap(
                stok_id=self.stok.pk, kaynak_depo_id=self.da.pk, hedef_depo_id=self.db.pk,
                tarih=D(2026, 3, 10), miktar="11")
        with self.assertRaises(HareketHatasi):
            depo_transfer.depo_transferi_yap(
                stok_id=self.stok.pk, kaynak_depo_id=self.da.pk, hedef_depo_id=self.da.pk,
                tarih=D(2026, 3, 10), miktar="1")
        self.assertFalse(StokHareket.objects.filter(kaynak="TRANSFER").exists())   # atomik

    def test_tek_bacak_silinemez_transfer_birlikte_silinir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        grup = depo_transfer.depo_transferi_yap(
            stok_id=self.stok.pk, kaynak_depo_id=self.da.pk, hedef_depo_id=self.db.pk,
            tarih=D(2026, 3, 10), miktar="4")
        with self.assertRaises(HareketHatasi):
            hareket_sil(StokHareket.objects.filter(transfer_grubu=grup, tur="GIRIS").get())
        self.assertEqual(depo_transfer.depo_transferi_sil(grup), 2)
        self.assertFalse(StokHareket.objects.filter(transfer_grubu=grup, silindi=False).exists())

    def test_hedefte_tuketilmis_transfer_silinemez(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        grup = depo_transfer.depo_transferi_yap(
            stok_id=self.stok.pk, kaynak_depo_id=self.da.pk, hedef_depo_id=self.db.pk,
            tarih=D(2026, 3, 10), miktar="4")
        from core.services.hareket import hareket_ekle
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.db.pk, tarih=D(2026, 3, 12),
                     tur="CIKIS", miktar="3")
        with self.assertRaises(HareketHatasi):
            depo_transfer.depo_transferi_sil(grup)
        self.assertEqual(StokHareket.objects.filter(transfer_grubu=grup, silindi=False).count(), 2)


class SatisMaliyetFisiTest(MaliyetBTemel):
    def test_ticari_mal_621_fisi_ve_fatura_silinince_fis_gider(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        f = self.satis_yap(D(2026, 3, 10), "4")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(h.tutar_try, Decimal("400.00"))
        self.assertEqual(h.fis.kaynak, YevmiyeFisi.Kaynak.STOK_SATIS)
        self.assertEqual(self.fis_satirlari(h.fis),
                         {"621": (Decimal("400.00"), Decimal("0.00")),
                          "153.10": (Decimal("0.00"), Decimal("400.00"))})
        fatura_sil(f)
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="STOK_SATIS").exists())

    def test_mamul_620_ilk_madde_623(self):
        for anahtar, hesap, maliyet in (("mamul", "152.10", "620"), ("ham", "150.10", "623")):
            kart = self.kart[anahtar]
            self.alis_yap(D(2026, 3, 5), "10", "100", stok=kart)
            f = self.satis_yap(D(2026, 3, 10), "2", stok=kart)
            h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
            self.assertEqual(self.fis_satirlari(h.fis),
                             {maliyet: (Decimal("200.00"), Decimal("0.00")),
                              hesap: (Decimal("0.00"), Decimal("200.00"))})

    def test_gec_gelen_alis_satis_maliyet_fisini_gunceller(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        f = self.satis_yap(D(2026, 3, 10), "4")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(h.tutar_try, Decimal("400.00"))
        self.alis_yap(D(2026, 3, 1), "10", "200")            # geç gelen, daha pahalı alış
        h.refresh_from_db()
        self.assertEqual(h.tutar_try, Decimal("600.00"))      # 4 x 150
        self.assertEqual(self.fis_satirlari(h.fis)["621"], (Decimal("600.00"), Decimal("0.00")))
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="STOK_SATIS", silindi=False).count(), 1)

    def test_tip_maliyet_fisi_bossa_fis_yok(self):
        self.satis.maliyet_fisi = ""
        self.satis.save()
        self.alis_yap(D(2026, 3, 5), "10", "100")
        f = self.satis_yap(D(2026, 3, 10), "4")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(h.tutar_try, Decimal("400.00"))
        self.assertIsNone(h.fis_id)

    def test_satis_iadesi_ortalamadan_girer_ters_fis(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        self.satis_yap(D(2026, 3, 10), "4")
        f = fatura_olustur(
            tip_id=self.iade.pk, cari_id=self.musteri.pk, tarih=D(2026, 3, 12),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "2", "birim_fiyat": "500"}],
            depo_id=self.da.pk)
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual((h.tur, h.tutar_try), ("GIRIS", Decimal("200.00")))     # 2 x ortalama 100
        self.assertEqual(self.fis_satirlari(h.fis),
                         {"153.10": (Decimal("200.00"), Decimal("0.00")),
                          "621": (Decimal("0.00"), Decimal("200.00"))})
        self.stok.refresh_from_db()
        self.assertEqual(self.stok.ort_maliyet_try, Decimal("100.000000"))       # ortalama bozulmadı

    def test_stok_hesabi_tanimsizsa_satis_engellenir_atomik(self):
        kat = Kategori.objects.create(ad="TANIMSIZ", kod="97")
        KategoriHesap.objects.create(kategori=kat, fatura_tipi=self.satis,
                                     hesap=HesapPlani.objects.get(hesap_kodu="600"))
        st = Stok.objects.create(kod="X-2", ad="X2", kategori=kat, kdv=self.kdv,
                                 uretim_birimi=self.stok.uretim_birimi,
                                 fatura_birimi=self.stok.uretim_birimi)
        from core.services.hareket import hareket_ekle
        hareket_ekle(stok_id=st.pk, depo_id=self.da.pk, tarih=D(2026, 3, 5), tur="GIRIS",
                     miktar="5", giris_tutar_try=Decimal("50.00"))
        n = Fatura.objects.count()
        with self.assertRaises(FaturaHatasi):
            self.satis_yap(D(2026, 3, 10), "2", stok=st)
        self.assertEqual(Fatura.objects.count(), n)

    def test_otomatik_fis_elle_duzenlenemez_iptal_edilemez(self):
        u = User.objects.create_superuser("yon", password="x")
        EkranYetki.objects.create(kullanici=u, ekran_kod="fis_listesi")
        self.client.force_login(u)
        self.alis_yap(D(2026, 3, 5), "10", "100")
        f = self.satis_yap(D(2026, 3, 10), "4")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(self.client.get(reverse("core:fis_duzenle", args=[h.fis_id])).status_code, 302)
        self.client.post(reverse("core:fis_sil", args=[h.fis_id]))
        h.fis.refresh_from_db()
        self.assertFalse(h.fis.silindi)


class UretimMaliyetAktarimiTest(MaliyetBTemel):
    def setUp(self):
        super().setUp()
        k = self.kart
        self.ist = _istasyon("LAZER")
        self.kesim = operasyon_olustur(
            istasyon_id=self.ist.pk, cikti_id=k["yari"].pk, cikti_miktar=Decimal("2"),
            satirlar=[(k["ham"], Decimal("1"))], ad="Kesim")
        self.bukum = operasyon_olustur(
            istasyon_id=_istasyon("BUKUM").pk, cikti_id=k["yari2"].pk, cikti_miktar=Decimal("1"),
            satirlar=[(k["yari"], Decimal("1"))], ad="Büküm")

    def _onayla(self, op, tarih, hedef):
        kayit = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.da.pk, tarih=tarih,
                                        hedef_cikti_miktari=Decimal(hedef))
        operasyon_kaydi_onayla(kayit)
        kayit.refresh_from_db()
        return kayit

    def test_onayda_15x_fisi_ve_gec_gelen_fatura_zincirleme_gunceller(self):
        k = self.kart
        self.alis_yap(D(2026, 3, 5), "100", "10", stok=k["ham"])
        kesim = self._onayla(self.kesim, D(2026, 3, 10), "10")           # 5 ham x 10 = 50 TL
        self.assertEqual(kesim.fis.kaynak, YevmiyeFisi.Kaynak.URETIM)
        self.assertEqual(self.fis_satirlari(kesim.fis),
                         {"151.10": (Decimal("50.00"), Decimal("0.00")),
                          "150.10": (Decimal("0.00"), Decimal("50.00"))})
        bukum = self._onayla(self.bukum, D(2026, 3, 12), "10")           # 10 yarı mamul -> yari2
        self.assertIsNone(bukum.fis_id)                                   # 151.10 -> 151.10: fiş yok
        cikti2 = StokHareket.objects.get(operasyon_kaydi=bukum, tur="GIRIS")
        self.assertEqual(cikti2.giris_tutar_try, Decimal("50.00"))

        self.alis_yap(D(2026, 3, 1), "100", "30", stok=k["ham"])         # geç gelen pahalı alış
        kesim.refresh_from_db()
        self.assertEqual(self.fis_satirlari(kesim.fis),                  # ortalama (3000+1000)/200=20
                         {"151.10": (Decimal("100.00"), Decimal("0.00")),
                          "150.10": (Decimal("0.00"), Decimal("100.00"))})
        cikti1 = StokHareket.objects.get(operasyon_kaydi=kesim, tur="GIRIS")
        self.assertEqual(cikti1.giris_tutar_try, Decimal("100.00"))
        cikti2.refresh_from_db()
        self.assertEqual(cikti2.giris_tutar_try, Decimal("100.00"))      # ikinci seviyeye yayıldı
        k["yari2"].refresh_from_db()
        self.assertEqual(k["yari2"].maliyet_deger_try, Decimal("100.00"))

    def test_hesap_tanimsiz_cikti_onayi_engeller(self):
        kat = Kategori.objects.create(ad="TANIMSIZ ÇIKTI", kod="96")
        yeni = Stok.objects.create(
            kod="151-10-9100", ad="TANIMSIZ", kategori=kat, kdv=self.kdv,
            uretim_birimi=self.stok.uretim_birimi, fatura_birimi=self.stok.uretim_birimi)
        op = operasyon_olustur(
            istasyon_id=_istasyon("TEST").pk, cikti_id=yeni.pk, cikti_miktar=Decimal("1"),
            satirlar=[(self.kart["ham"], Decimal("1"))], ad="Tanımsız")
        self.alis_yap(D(2026, 3, 5), "10", "10", stok=self.kart["ham"])
        from core.services.uretim import UretimHatasi
        kayit = operasyon_kaydi_olustur(operasyon_id=op.pk, depo_id=self.da.pk,
                                        tarih=D(2026, 3, 10), hedef_cikti_miktari=Decimal("2"))
        with self.assertRaises(UretimHatasi):
            operasyon_kaydi_onayla(kayit)
        kayit.refresh_from_db()
        self.assertEqual(kayit.durum, "TASLAK")                           # atomik geri alındı


class TanimKomutuTest(MaliyetBTemel):
    def _komut(self, *ek):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("stok_hesap_tanimla", *ek, stdout=out)
        return out.getvalue()

    def setUp(self):
        super().setUp()
        HesapPlani.objects.filter(hesap_kodu="623").delete()      # canlıdaki gibi: 623 yok
        for ad in ("SATIŞ FATURASI-İHRACAT", "SATIŞ FATURASI-İHRAÇ KAYITLI"):
            FaturaTipi.objects.get_or_create(ad=ad, defaults={"yon": FaturaTipi.Yon.SATIS})
        self.satis.maliyet_fisi = ""
        self.satis.save()
        FaturaTipi.objects.filter(pk=self.iade.pk).update(maliyet_fisi="")
        ust = Kategori.objects.create(ad="YARI MAMULLER", kod="95")
        self.kat = Kategori.objects.create(ad="KESİLMİŞ PARÇALAR", kod="10", ust=ust)

    def test_dry_run_yazmaz_uygula_tanimlar_tekrar_idempotent(self):
        c = self._komut()
        self.assertIn("DRY-RUN", c)
        self.assertFalse(HesapPlani.objects.filter(hesap_kodu="623", silindi=False).exists())
        self.assertFalse(KategoriHesap.objects.filter(kategori=self.kat).exists())
        self._komut("--uygula")
        self.assertTrue(HesapPlani.objects.filter(hesap_kodu="623", silindi=False).exists())
        self.assertEqual(
            KategoriHesap.objects.get(kategori=self.kat, fatura_tipi=self.alis).hesap_id, "151.10")
        self.satis.refresh_from_db()
        self.iade.refresh_from_db()
        self.assertEqual((self.satis.maliyet_fisi, self.iade.maliyet_fisi), ("SATIS", "SATIS_IADE"))
        self.assertIn("zaten", self._komut("--uygula"))


class AlisIadesiTest(MaliyetBTemel):
    """Tedarikçiye iade (satış yönlü 'ALIŞ İADE' tipi): stok çıkışı ortalamayla DEĞİL iade faturasının
    tutarıyla değerlenir, fiş yok; ortalama kalan miktar/değerden yeniden hesaplanır ve stok değeri
    ile 15x mizanı eşit kalır."""

    def setUp(self):
        super().setUp()
        self.alis_iade = FaturaTipi.objects.create(
            ad="SATIŞ FATURASI-ALIŞ İADE", yon=FaturaTipi.Yon.SATIS,
            maliyet_fisi=FaturaTipi.MaliyetFisi.ALIS_IADE)
        KategoriHesap.objects.create(kategori=self.alt, fatura_tipi=self.alis_iade,
                                     hesap=HesapPlani.objects.get(hesap_kodu="153.10"))

    def test_cikis_iade_faturasi_tutariyla_degerlenir_fis_yok_mizan_esit(self):
        from core.services import stok_ortalama
        self.alis_yap(D(2026, 3, 5), "10", "100")
        self.alis_yap(D(2026, 3, 5), "10", "200")             # ortalama 150
        f = fatura_olustur(
            tip_id=self.alis_iade.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "200"}],
            depo_id=self.da.pk)
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual((h.tur, h.tutar_try, h.maliyet_durumu), ("CIKIS", Decimal("800.00"), "KESIN"))
        self.assertIsNone(h.fis_id)                             # maliyet fişi yok
        self.assertFalse(YevmiyeFisi.objects.filter(kaynak="STOK_SATIS").exists())
        self.stok.refresh_from_db()
        self.assertEqual((self.stok.maliyet_miktar, self.stok.maliyet_deger_try,
                          self.stok.ort_maliyet_try),
                         (Decimal("16.000"), Decimal("2200.00"), Decimal("137.500000")))
        k153 = next(x for x in stok_ortalama.degerleme_raporu()["karsilastirma"] if x["kod"] == "153")
        self.assertEqual((k153["stok_degeri"], k153["mizan"], k153["fark"]),
                         (Decimal("2200.00"), Decimal("2200.00"), Decimal("0.00")))

    def test_iade_sonrasi_satis_yeni_ortalamayla_degerlenir(self):
        self.alis_yap(D(2026, 3, 5), "10", "100")
        self.alis_yap(D(2026, 3, 5), "10", "200")
        fatura_olustur(
            tip_id=self.alis_iade.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10),
            satirlar=[{"stok_id": self.stok.pk, "miktar": "4", "birim_fiyat": "200"}],
            depo_id=self.da.pk)
        f = self.satis_yap(D(2026, 3, 12), "2")
        h = StokHareket.objects.get(fatura_satir__fatura=f, silindi=False)
        self.assertEqual(h.tutar_try, Decimal("275.00"))        # 2 x 137,5


class PlastikVirmanTest(MaliyetBTemel):
    """PLASTİK PARÇALAR: kategori eşlemesi 150.30 → 151.40 ve bugüne kadarki bakiyenin virmanı."""

    def setUp(self):
        super().setUp()
        _hesap("150.30", "PLASTİK İLK MADDE")
        _hesap("151.40", "PLASTİK PARÇALAR")
        ust = Kategori.objects.create(ad="YARI MAMULLER", kod="95")
        self.kat = Kategori.objects.create(ad="PLASTİK PARÇALAR", kod="40", ust=ust)
        KategoriHesap.objects.create(kategori=self.kat, fatura_tipi=self.alis,
                                     hesap=HesapPlani.objects.get(hesap_kodu="150.30"))
        self.plastik = Stok.objects.create(
            kod="151-40-9001", ad="PLASTİK TAPA", kategori=self.kat, kdv=self.kdv,
            uretim_birimi=self.stok.uretim_birimi, fatura_birimi=self.stok.uretim_birimi)
        self.alis_yap(D(2026, 3, 5), "3", "100", stok=self.plastik)         # 300 TL, 150.30'a işlendi

    def _komut(self, *ek):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("stok_hesap_tanimla", *ek, stdout=out)
        return out.getvalue()

    def _bakiye(self, kod):
        from core.services.stok_ortalama import _mizan_bakiyesi
        return _mizan_bakiyesi(kod)

    def test_dry_run_gosterir_uygula_virman_yapar_tekrarda_yapmaz(self):
        c = self._komut("--virman")
        self.assertIn("VİRMAN FİŞİ", c)
        self.assertIn("300,00", c.replace(".", ","))               # tutar raporda
        self.assertEqual(self._bakiye("150.30"), Decimal("300.00"))   # dry-run geri alındı
        self.assertEqual(self._bakiye("151.40"), Decimal("0.00"))
        self._komut("--uygula", "--virman")
        self.assertEqual((self._bakiye("150.30"), self._bakiye("151.40")),
                         (Decimal("0.00"), Decimal("300.00")))
        self.assertEqual(KategoriHesap.objects.get(kategori=self.kat, fatura_tipi=self.alis,
                                                   silindi=False).hesap_id, "151.40")
        fis = YevmiyeFisi.objects.get(aciklama__startswith="PLASTİK PARÇALAR STOK VİRMANI")
        self.assertEqual(self.fis_satirlari(fis), {"151.40": (Decimal("300.00"), Decimal("0.00")),
                                                   "150.30": (Decimal("0.00"), Decimal("300.00"))})
        from core.services import stok_ortalama
        rapor = {x["kod"]: x for x in stok_ortalama.degerleme_raporu()["karsilastirma"]}
        self.assertEqual((rapor["150"]["fark"], rapor["151"]["fark"]), (Decimal("0.00"), Decimal("0.00")))
        self.assertIn("zaten yapılmış", self._komut("--uygula", "--virman"))
        self.assertEqual(YevmiyeFisi.objects.filter(
            aciklama__startswith="PLASTİK PARÇALAR STOK VİRMANI", silindi=False).count(), 1)

    def test_virman_varsayilan_kapali_eslemeyi_degistirir_fis_yazmaz(self):
        c = self._komut("--uygula")
        self.assertNotIn("VİRMAN", c)
        self.assertFalse(YevmiyeFisi.objects.filter(
            aciklama__startswith="PLASTİK PARÇALAR STOK VİRMANI").exists())
        self.assertEqual(KategoriHesap.objects.get(kategori=self.kat, fatura_tipi=self.alis,
                                                   silindi=False).hesap_id, "151.40")
        self.assertEqual(self._bakiye("150.30"), Decimal("300.00"))     # bakiye yerinde (fatura düzeltilecek)
