"""İNSAN KAYNAKLARI > İzin Takibi — yıllık izin hak edişi (İş Kanunu m.53, yaş kuralı, 29 Şubat),
bakiye (kalan/planlanan/ayrılan), izin CRUD (çakışma, sınırlar, yarım gün), DB kısıtları,
personel silme koruması + view/yetki. Ücret/bordro hesabı YOK."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, Personel, PersonelIzin
from core.services.personel import PersonelHatasi, personel_guncelle, personel_olustur, personel_sil
from core.services.personel_izin import (
    PersonelIzinHatasi, bakiye, bakiyeler, hak_edilen_gun, izin_ekle, izin_guncelle,
    izin_listele, izin_sil, izinde_olanlar, pazarsiz_gun, yillik_hak_gunu,
)
from core.tarih import tr_bugun

BUGUN = date(2026, 9, 19)          # Cumartesi; 2026-09-14 Pazartesi


def _kur(**kw):
    veri = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": date(2020, 3, 15)}
    veri.update(kw)
    return personel_olustur(**veri)


class YillikHakGunuTest(TestCase):
    def test_kidem_ve_yas_tablosu(self):
        for (yil, yas), beklenen in {
            (0, 30): 0, (1, 30): 14, (5, 30): 14, (6, 30): 20, (14, 30): 20, (15, 30): 26,
            (30, 30): 26,
            (1, 18): 20, (1, 19): 14, (1, 49): 14, (1, 50): 20, (15, 50): 26, (1, None): 14,
            (6, 17): 20, (6, 55): 20,
        }.items():
            self.assertEqual(yillik_hak_gunu(yil, yas), beklenen, (yil, yas))


class HakEdilenGunTest(TestCase):
    def test_kidem_basamaklari(self):
        giris = date(2020, 3, 15)
        self.assertEqual(hak_edilen_gun(giris, None, date(2021, 3, 14)), 0)      # 1. yıl dolmadı
        self.assertEqual(hak_edilen_gun(giris, None, date(2021, 3, 15)), 14)     # yıl dönümü DAHİL
        self.assertEqual(hak_edilen_gun(giris, None, date(2025, 3, 15)), 70)     # 5 x 14
        self.assertEqual(hak_edilen_gun(giris, None, date(2026, 3, 15)), 90)     # 6. yıl 20
        self.assertEqual(hak_edilen_gun(giris, None, date(2034, 3, 15)), 70 + 9 * 20)   # 14. yıl
        self.assertEqual(hak_edilen_gun(giris, None, date(2035, 3, 15)), 70 + 9 * 20 + 26)  # 15. yıl 26

    def test_gelecek_ve_bos_giris(self):
        self.assertEqual(hak_edilen_gun(date(2027, 1, 1), None, date(2026, 9, 19)), 0)
        self.assertEqual(hak_edilen_gun(None, None, date(2026, 9, 19)), 0)

    def test_29_subat_girisi_artik_olmayan_yilda_28_subat(self):
        giris = date(2024, 2, 29)
        self.assertEqual(hak_edilen_gun(giris, None, date(2025, 2, 27)), 0)
        self.assertEqual(hak_edilen_gun(giris, None, date(2025, 2, 28)), 14)
        self.assertEqual(hak_edilen_gun(giris, None, date(2028, 2, 28)), 42)      # 3 yıl
        self.assertEqual(hak_edilen_gun(giris, None, date(2028, 2, 29)), 56)      # 4. yıl dönümü artık yıl

    def test_yas_kurali_50_ve_uzeri_ilk_yildan_20_gun(self):
        giris = date(2020, 3, 15)
        # 2021-03-15'te tam 50 yaşında -> 20; bir gün sonra doğmuşsa 49 -> 14
        self.assertEqual(hak_edilen_gun(giris, date(1971, 3, 15), date(2021, 3, 15)), 20)
        self.assertEqual(hak_edilen_gun(giris, date(1971, 3, 16), date(2021, 3, 15)), 14)
        self.assertEqual(hak_edilen_gun(giris, date(1970, 1, 1), date(2021, 3, 15)), 20)

    def test_yas_kurali_18_ve_alti(self):
        giris = date(2020, 3, 15)
        self.assertEqual(hak_edilen_gun(giris, date(2003, 3, 15), date(2021, 3, 15)), 20)   # 18
        self.assertEqual(hak_edilen_gun(giris, date(2003, 3, 16), date(2021, 3, 15)), 20)   # 17
        self.assertEqual(hak_edilen_gun(giris, date(2002, 3, 15), date(2021, 3, 15)), 14)   # 19

    def test_yas_her_yil_donumunde_yeniden_hesaplanir(self):
        # 2021'de 49 yaşında (14), 2022'de 50 (20)
        giris, dogum = date(2020, 3, 15), date(1972, 3, 15)
        self.assertEqual(hak_edilen_gun(giris, dogum, date(2021, 3, 15)), 14)
        self.assertEqual(hak_edilen_gun(giris, dogum, date(2022, 3, 15)), 14 + 20)

    def test_dogum_tarihi_yoksa_yas_kurali_uygulanmaz(self):
        self.assertEqual(hak_edilen_gun(date(2020, 3, 15), None, date(2021, 3, 15)), 14)


class PazarsizGunTest(TestCase):
    def test_hafta_ici_ve_pazar(self):
        self.assertEqual(pazarsiz_gun(date(2026, 9, 14), date(2026, 9, 20)), Decimal(6))   # Pzt-Paz
        self.assertEqual(pazarsiz_gun(date(2026, 9, 20), date(2026, 9, 20)), Decimal(0))   # yalnız Pazar
        self.assertEqual(pazarsiz_gun(date(2026, 9, 14), date(2026, 9, 14)), Decimal(1))


class BakiyeTest(TestCase):
    def test_kalan_hak_eksi_onceki_eksi_sistemdeki_yillik(self):
        p = _kur(izin_onceki_kullanilan=10)
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))   # 5 gün
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual((b.hak_edilen, b.onceki, b.kullanilan, b.kalan),
                         (Decimal(90), Decimal(10), Decimal(5), Decimal(75)))
        self.assertEqual(b.planlanan, Decimal(0))
        self.assertFalse(b.eksi_mi)

    def test_yalniz_yillik_izin_bakiyeden_duser(self):
        p = _kur()
        izin_ekle(p, tur="RAPOR", baslangic=date(2026, 7, 6), bitis=date(2026, 7, 8))
        izin_ekle(p, tur="MAZERET", baslangic=date(2026, 7, 13), bitis=date(2026, 7, 14))
        izin_ekle(p, tur="UCRETSIZ", baslangic=date(2026, 7, 20), bitis=date(2026, 7, 21))
        izin_ekle(p, tur="DIGER", baslangic=date(2026, 7, 27), bitis=date(2026, 7, 27))
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual(b.kullanilan, Decimal(0))
        self.assertEqual(b.kalan, Decimal(90))

    def test_silinmis_izin_sayilmaz(self):
        p = _kur()
        i = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_sil(i)
        self.assertEqual(bakiye(p, bugun=BUGUN).kullanilan, Decimal(0))

    def test_gelecek_tarihli_izin_planlanan_ve_kullanilana_dahil(self):
        p = _kur()
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))       # geçmiş 5
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 10, 5), bitis=date(2026, 10, 9))     # gelecek 5
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual((b.kullanilan, b.planlanan, b.kalan),
                         (Decimal(10), Decimal(5), Decimal(80)))

    def test_negatif_kalan_engellenmez_eksi_isaretli(self):
        p = _kur(izin_onceki_kullanilan=100)
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual(b.kalan, Decimal(-10))
        self.assertTrue(b.eksi_mi)
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))   # eksiye rağmen eklenir
        self.assertEqual(bakiye(p, bugun=BUGUN).kalan, Decimal(-15))

    def test_ilk_yil_dolmamis_hak_sifir_sonraki_hak_tarihi(self):
        p = _kur(ise_giris_tarihi=date(2026, 1, 10))
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual((b.hak_edilen, b.kalan), (Decimal(0), Decimal(0)))
        self.assertEqual(b.sonraki_hak_tarihi, date(2027, 1, 10))

    def test_gelecekte_ise_giris(self):
        b = bakiye(_kur(ise_giris_tarihi=date(2027, 1, 1)), bugun=BUGUN)
        self.assertEqual(b.hak_edilen, Decimal(0))
        self.assertEqual(b.sonraki_hak_tarihi, date(2028, 1, 1))

    def test_sonraki_hak_tarihi_calisan_icin(self):
        b = bakiye(_kur(), bugun=BUGUN)
        self.assertEqual(b.sonraki_hak_tarihi, date(2027, 3, 15))

    def test_ayrilan_personelde_bakiye_cikis_tarihine_kadar(self):
        p = _kur(isten_cikis_tarihi=date(2023, 6, 30))
        b = bakiye(p, bugun=BUGUN)
        self.assertEqual(b.hak_edilen, Decimal(42))               # 2021-2023 yıl dönümleri
        self.assertIsNone(b.sonraki_hak_tarihi)

    def test_cikis_gunu_hala_calisan_sayilir(self):
        p = _kur(isten_cikis_tarihi=BUGUN)
        self.assertIsNotNone(bakiye(p, bugun=BUGUN).sonraki_hak_tarihi)

    def test_bakiyeler_toplu_tek_tek_ile_ayni(self):
        a = _kur(ad="a", izin_onceki_kullanilan=3)
        b = _kur(ad="b", ise_giris_tarihi=date(2018, 5, 1))
        c = _kur(ad="c", ise_giris_tarihi=date(2026, 1, 1))
        izin_ekle(a, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_ekle(b, tur="YILLIK", baslangic=date(2026, 10, 5), bitis=date(2026, 10, 9))
        toplu = bakiyeler([a, b, c], bugun=BUGUN)
        for p in (a, b, c):
            self.assertEqual(toplu[p.pk], bakiye(p, bugun=BUGUN))


class IzinEkleTest(TestCase):
    def setUp(self):
        self.p = _kur()

    def test_gun_bos_ise_pazarsiz_gun(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 14), bitis=date(2026, 9, 20))
        self.assertEqual(i.gun, Decimal(6))

    def test_yalniz_pazar_araligi_gun_bos_olamaz(self):
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 20), bitis=date(2026, 9, 20))
        # elle gün verilirse serbest
        self.assertEqual(izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 20),
                                   bitis=date(2026, 9, 20), gun=Decimal("1")).gun, Decimal(1))

    def test_yarim_gun(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 14), bitis=date(2026, 9, 14),
                      gun=Decimal("0.5"))
        self.assertEqual(i.gun, Decimal("0.5"))
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 15), bitis=date(2026, 9, 15),
                      gun=Decimal("0.3"))

    def test_gun_gecersiz_degerler(self):
        for gun in (Decimal("0"), Decimal("-1"), Decimal("3")):        # 3 > 2 takvim günü
            with self.assertRaises(PersonelIzinHatasi, msg=str(gun)):
                izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 14), bitis=date(2026, 9, 15),
                          gun=gun)

    def test_bitis_baslangictan_once_olamaz(self):
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 9, 15), bitis=date(2026, 9, 14))

    def test_tarihler_zorunlu_ve_tur_gecerli(self):
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=None, bitis=date(2026, 9, 14))
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="TATIL", baslangic=date(2026, 9, 14), bitis=date(2026, 9, 14))

    def test_ise_giris_oncesi_ve_cikis_sonrasi_reddedilir(self):
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=date(2020, 3, 14), bitis=date(2020, 3, 16))
        ayrilan = _kur(ad="ayrilan", isten_cikis_tarihi=date(2026, 9, 10))
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(ayrilan, tur="YILLIK", baslangic=date(2026, 9, 9), bitis=date(2026, 9, 11))
        izin_ekle(ayrilan, tur="YILLIK", baslangic=date(2026, 9, 9), bitis=date(2026, 9, 10))  # çıkış günü serbest

    def test_cakisma_reddedilir_bitisik_gun_serbest(self):
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        with self.assertRaises(PersonelIzinHatasi) as h:
            izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 6, 5), bitis=date(2026, 6, 9))
        self.assertIn("çakış", str(h.exception))
        with self.assertRaises(PersonelIzinHatasi):                    # tamamen içinde
            izin_ekle(self.p, tur="MAZERET", baslangic=date(2026, 6, 2), bitis=date(2026, 6, 3))
        with self.assertRaises(PersonelIzinHatasi):                    # tamamen kapsayan
            izin_ekle(self.p, tur="MAZERET", baslangic=date(2026, 5, 30), bitis=date(2026, 6, 10))
        izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 6, 6), bitis=date(2026, 6, 8))     # bitişik
        izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 5, 25), bitis=date(2026, 5, 31))   # bitişik (önce)

    def test_baska_personelin_izniyle_cakisma_yok(self):
        diger = _kur(ad="diger")
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_ekle(diger, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))

    def test_silinmis_izinle_cakisma_olmaz(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_sil(i)
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))

    def test_silinmis_personele_izin_girilemez(self):
        personel_sil(self.p)
        with self.assertRaises(PersonelIzinHatasi):
            izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))

    def test_kullanici_audit(self):
        u = User.objects.create_user("izinaudit", password="x")
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5),
                      kullanici=u)
        self.assertEqual((i.created_by, i.updated_by), (u, u))


class IzinGuncelleSilTest(TestCase):
    def setUp(self):
        self.p = _kur()
        self.i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))

    def test_kendini_cakisma_saymaz(self):
        izin_guncelle(self.i, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5),
                      gun=Decimal("4.5"), aciklama="  revize  ")
        self.i.refresh_from_db()
        self.assertEqual((self.i.gun, self.i.aciklama), (Decimal("4.5"), "revize"))

    def test_baska_izinle_cakisan_guncelleme_reddedilir(self):
        diger = izin_ekle(self.p, tur="RAPOR", baslangic=date(2026, 6, 8), bitis=date(2026, 6, 9))
        with self.assertRaises(PersonelIzinHatasi):
            izin_guncelle(diger, tur="RAPOR", baslangic=date(2026, 6, 4), bitis=date(2026, 6, 9))

    def test_tur_degisince_bakiye_degisir(self):
        self.assertEqual(bakiye(self.p, bugun=BUGUN).kullanilan, Decimal(5))
        izin_guncelle(self.i, tur="RAPOR", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.assertEqual(bakiye(self.p, bugun=BUGUN).kullanilan, Decimal(0))

    def test_silinmis_izin_guncellenemez(self):
        izin_sil(self.i)
        with self.assertRaises(PersonelIzinHatasi):
            izin_guncelle(self.i, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))

    def test_sil_soft_ve_idempotent(self):
        izin_sil(self.i)
        self.i.refresh_from_db()
        self.assertTrue(self.i.silindi)
        self.assertIsNotNone(self.i.silindi_at)
        izin_sil(self.i)


class IzinListeleTest(TestCase):
    def setUp(self):
        self.a = _kur(ad="ahmet")
        self.b = _kur(ad="berk")
        self.i1 = izin_ekle(self.a, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.i2 = izin_ekle(self.b, tur="RAPOR", baslangic=date(2026, 6, 3), bitis=date(2026, 6, 4))
        self.i3 = izin_ekle(self.a, tur="MAZERET", baslangic=date(2026, 8, 3), bitis=date(2026, 8, 3))

    def test_filtreler(self):
        self.assertEqual(set(izin_listele()), {self.i1, self.i2, self.i3})
        self.assertEqual(set(izin_listele(personel_id=self.a.pk)), {self.i1, self.i3})
        self.assertEqual(set(izin_listele(tur="RAPOR")), {self.i2})

    def test_tarih_araligi_cakisma_anlaminda(self):
        # 2026-06-05..2026-06-10 aralığına değen: i1 (bitiş 06-05) ve i2 değil (bitiş 06-04)
        self.assertEqual(set(izin_listele(baslangic=date(2026, 6, 5), bitis=date(2026, 6, 10))), {self.i1})
        self.assertEqual(set(izin_listele(baslangic=date(2026, 7, 1))), {self.i3})
        self.assertEqual(set(izin_listele(bitis=date(2026, 6, 2))), {self.i1})

    def test_su_an_izinde_olanlar(self):
        self.assertEqual(set(izinde_olanlar(date(2026, 6, 3))), {self.i1, self.i2})
        self.assertEqual(set(izinde_olanlar(date(2026, 6, 5))), {self.i1})
        self.assertEqual(set(izinde_olanlar(date(2026, 6, 6))), set())

    def test_silinmis_personel_ve_silinmis_izin_listede_yok(self):
        izin_sil(self.i2)
        self.assertNotIn(self.i2, set(izin_listele()))


class IzinDBKisitTest(TestCase):
    def test_bitis_baslangictan_once_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelIzin.objects.create(personel=p, tur="YILLIK", baslangic=date(2026, 6, 5),
                                        bitis=date(2026, 6, 1), gun=1)

    def test_gun_sifir_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonelIzin.objects.create(personel=p, tur="YILLIK", baslangic=date(2026, 6, 1),
                                        bitis=date(2026, 6, 1), gun=0)

    def test_onceki_kullanilan_negatif_yasak(self):
        p = _kur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Personel.objects.filter(pk=p.pk).update(izin_onceki_kullanilan=-1)


class PersonelIzinOncekiKullanilanTest(TestCase):
    def test_verilmezse_varsayilan_sifir_ve_guncellemede_korunur(self):
        p = _kur()
        self.assertEqual(p.izin_onceki_kullanilan, Decimal("0.0"))
        personel_guncelle(p, ad="ali", soyad="veli", ise_giris_tarihi=date(2020, 3, 15),
                          izin_onceki_kullanilan=12)
        personel_guncelle(p, ad="ali", soyad="veli", ise_giris_tarihi=date(2020, 3, 15),
                          notlar="x")                       # alan verilmedi -> 12 korunur
        p.refresh_from_db()
        self.assertEqual(p.izin_onceki_kullanilan, Decimal("12.0"))

    def test_negatif_ve_yarim_gun_disi_reddedilir(self):
        with self.assertRaises(PersonelHatasi):
            _kur(izin_onceki_kullanilan=-1)
        with self.assertRaises(PersonelHatasi):
            _kur(izin_onceki_kullanilan=Decimal("7.3"))
        self.assertEqual(_kur(izin_onceki_kullanilan=Decimal("7.5")).izin_onceki_kullanilan,
                         Decimal("7.5"))

    def test_personel_silme_izin_varken_reddedilir(self):
        p = _kur()
        i = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        with self.assertRaises(PersonelHatasi) as h:
            personel_sil(p)
        self.assertIn("ayrıldı", str(h.exception))
        p.refresh_from_db()
        self.assertFalse(p.silindi)
        izin_sil(i)
        personel_sil(p)                                     # izin silinince serbest
        p.refresh_from_db()
        self.assertTrue(p.silindi)


class IzinViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("izyon", password="x")
        cls.izinci = User.objects.create_user("izinci", password="x")
        EkranYetki.objects.create(kullanici=cls.izinci, ekran_kod="personel_izinleri")
        cls.personelci = User.objects.create_user("izpersonelci", password="x")
        EkranYetki.objects.create(kullanici=cls.personelci, ekran_kod="personel")
        cls.ikisi = User.objects.create_user("izikisi", password="x")
        EkranYetki.objects.create(kullanici=cls.ikisi, ekran_kod="personel")
        EkranYetki.objects.create(kullanici=cls.ikisi, ekran_kod="personel_izinleri")
        cls.bos = User.objects.create_user("izbos", password="x")

    def _govde(self, p, **ek):
        veri = {"personel": p.pk, "tur": "YILLIK", "baslangic": "2026-06-01", "bitis": "2026-06-05",
                "gun": "", "aciklama": ""}
        veri.update(ek)
        return veri

    def test_yetki_403_ve_anonim_302(self):
        p = _kur()
        i = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        urller = [reverse("core:izinler"), reverse("core:izin_bakiyeleri"), reverse("core:izin_ekle"),
                  reverse("core:izin_duzenle", args=[i.pk]), reverse("core:izin_sil", args=[i.pk])]
        for u in urller:
            self.assertEqual(self.client.get(u).status_code, 302, u)
        for kullanici in (self.bos, self.personelci):
            self.client.force_login(kullanici)
            for u in urller:
                self.assertEqual(self.client.get(u).status_code, 403, (kullanici.username, u))
            self.assertEqual(self.client.post(reverse("core:izin_sil", args=[i.pk])).status_code, 403)
        i.refresh_from_db()
        self.assertFalse(i.silindi)

    def test_liste_ve_filtreler(self):
        a, b = _kur(ad="ahmet"), _kur(ad="berk")
        izin_ekle(a, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        izin_ekle(b, tur="RAPOR", baslangic=date(2026, 6, 3), bitis=date(2026, 6, 4))
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izinler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AHMET VELİ")
        self.assertContains(r, "BERK VELİ")
        r2 = self.client.get(reverse("core:izinler"), {"personel": a.pk})
        self.assertContains(r2, "01.06.2026")
        self.assertNotContains(r2, "03.06.2026")
        r3 = self.client.get(reverse("core:izinler"), {"tur": "RAPOR"})
        self.assertContains(r3, "03.06.2026")
        self.assertNotContains(r3, "01.06.2026")
        r4 = self.client.get(reverse("core:izinler"), {"bas": "2026-06-05", "bit": "2026-06-30"})
        self.assertContains(r4, "01.06.2026")            # 06-05'te biten yıllık aralığa değer
        self.assertNotContains(r4, "03.06.2026")
        r5 = self.client.get(reverse("core:izinler"), {"tur": "SAÇMA", "bas": "gecersiz"})
        self.assertEqual(r5.status_code, 200)            # geçersiz parametreler yok sayılır

    def test_su_an_izinde_filtresi(self):
        bugun = tr_bugun()
        a, b = _kur(ad="izindeki"), _kur(ad="calisan")
        izin_ekle(a, tur="YILLIK", baslangic=bugun - timedelta(days=1), bitis=bugun + timedelta(days=1),
                  gun=Decimal("1"))
        izin_ekle(b, tur="YILLIK", baslangic=bugun + timedelta(days=30), bitis=bugun + timedelta(days=31),
                  gun=Decimal("1"))
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izinler"), {"izinde": "1"})
        # (personel filtre listesinde herkes yer alır; asıl kontrol tablodaki kayıtlar)
        self.assertEqual([i.personel.ad for i in r.context["kayitlar"]], ["İZİNDEKİ"])

    def test_izin_ekle_get_on_secim_ve_post(self):
        p = _kur(ad="ahmet")
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izin_ekle"), {"personel": p.pk})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "selected")
        r2 = self.client.post(reverse("core:izin_ekle"), self._govde(p))
        self.assertRedirects(r2, reverse("core:izinler"))
        i = PersonelIzin.objects.get(personel=p)
        self.assertEqual((i.gun, i.created_by), (Decimal(5), self.izinci))

    def test_izin_ekle_cakisma_formda_gosterilir(self):
        p = _kur(ad="ahmet")
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 3), bitis=date(2026, 6, 4))
        self.client.force_login(self.izinci)
        r = self.client.post(reverse("core:izin_ekle"), self._govde(p))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "çakışıyor")
        self.assertEqual(PersonelIzin.objects.count(), 1)

    def test_izin_ekle_gun_virgullu_yarim_gun(self):
        p = _kur()
        self.client.force_login(self.izinci)
        self.client.post(reverse("core:izin_ekle"), self._govde(
            p, baslangic="2026-06-01", bitis="2026-06-01", gun="0,5"))
        self.assertEqual(PersonelIzin.objects.get(personel=p).gun, Decimal("0.5"))

    def test_donus_personel_karti_yetkiye_baglidir(self):
        p = _kur()
        self.client.force_login(self.ikisi)
        r = self.client.post(reverse("core:izin_ekle"), self._govde(p, sonraki="personel"))
        self.assertRedirects(r, reverse("core:personel_detay", args=[p.pk]))
        q = _kur(ad="ikinci")
        self.client.force_login(self.izinci)                       # personel yetkisi yok
        r2 = self.client.post(reverse("core:izin_ekle"), self._govde(q, sonraki="personel"))
        self.assertRedirects(r2, reverse("core:izinler"))

    def test_duzenle_get_dolu_post_gunceller_kisi_degismez(self):
        p, q = _kur(ad="ahmet"), _kur(ad="berk")
        i = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izin_duzenle", args=[i.pk]))
        self.assertContains(r, "AHMET VELİ")
        self.assertContains(r, "2026-06-01")
        r2 = self.client.post(reverse("core:izin_duzenle", args=[i.pk]), {
            "tur": "RAPOR", "baslangic": "2026-06-02", "bitis": "2026-06-03", "gun": "",
            "aciklama": "grip", "personel": q.pk})           # personel alanı yok sayılır
        self.assertRedirects(r2, reverse("core:izinler"))
        i.refresh_from_db()
        self.assertEqual((i.personel, i.tur, i.gun), (p, "RAPOR", Decimal(2)))

    def test_sil_post_soft_delete_get_silmez(self):
        p = _kur()
        i = izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.client.force_login(self.izinci)
        self.assertRedirects(self.client.get(reverse("core:izin_sil", args=[i.pk])),
                             reverse("core:izinler"))
        i.refresh_from_db()
        self.assertFalse(i.silindi)
        self.client.post(reverse("core:izin_sil", args=[i.pk]))
        i.refresh_from_db()
        self.assertTrue(i.silindi)

    def test_bakiyeler_ekrani_eksi_kirmizi_ayrilan_gizli(self):
        _kur(ad="ercan", izin_onceki_kullanilan=100)                       # kalan negatif
        _kur(ad="murat", isten_cikis_tarihi=date(2023, 6, 30))
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izin_bakiyeleri"))
        self.assertContains(r, "ERCAN VELİ")
        self.assertNotContains(r, "MURAT VELİ")
        self.assertContains(r, 'ik-eksi">-')                                 # negatif kalan kırmızı sınıfı
        r2 = self.client.get(reverse("core:izin_bakiyeleri"), {"ayrilan": "1"})
        self.assertContains(r2, "MURAT VELİ")
        self.assertContains(r2, "Ayrıldı")

    def test_personel_detay_izin_karti_yalniz_yetkiliye(self):
        p = _kur()
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.client.force_login(self.ikisi)
        r = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertContains(r, "Yıllık İzin")
        self.assertContains(r, "+ İzin Ekle")
        self.assertContains(r, "Kalan")
        self.client.force_login(self.personelci)
        r2 = self.client.get(reverse("core:personel_detay", args=[p.pk]))
        self.assertEqual(r2.status_code, 200)
        self.assertNotContains(r2, "Yıllık İzin")
        self.assertNotContains(r2, "+ İzin Ekle")
        self.assertContains(r2, "Kıdem")                        # kıdem herkes için

    def test_personel_formunda_izin_alani_yalniz_yetkiliye_ve_yetkisiz_deger_ezmez(self):
        p = _kur(izin_onceki_kullanilan=10)
        govde = {"ad": "ali", "soyad": "veli", "ise_giris_tarihi": "2020-03-15"}
        self.client.force_login(self.personelci)
        self.assertNotContains(self.client.get(reverse("core:personel_duzenle", args=[p.pk])),
                               "izin_onceki_kullanilan")
        self.client.post(reverse("core:personel_duzenle", args=[p.pk]), govde)
        p.refresh_from_db()
        self.assertEqual(p.izin_onceki_kullanilan, Decimal("10.0"))    # yetkisiz form değeri korur
        self.client.force_login(self.ikisi)
        r = self.client.get(reverse("core:personel_duzenle", args=[p.pk]))
        self.assertContains(r, "izin_onceki_kullanilan")
        self.assertContains(r, "hak ediş gösteriyor")
        self.client.post(reverse("core:personel_duzenle", args=[p.pk]),
                         {**govde, "izin_onceki_kullanilan": "7,5"})
        p.refresh_from_db()
        self.assertEqual(p.izin_onceki_kullanilan, Decimal("7.5"))

    def test_personel_ekle_izin_alaniyla(self):
        self.client.force_login(self.ikisi)
        self.client.post(reverse("core:personel_ekle"), {
            "ad": "yeni", "soyad": "kisi", "ise_giris_tarihi": "2024-01-01",
            "izin_onceki_kullanilan": "3"})
        self.assertEqual(Personel.objects.get(ad="YENİ").izin_onceki_kullanilan, Decimal("3.0"))

    def test_personel_sil_izin_varken_hata_mesaji(self):
        p = _kur()
        izin_ekle(p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 5))
        self.client.force_login(self.ikisi)
        r = self.client.post(reverse("core:personel_sil", args=[p.pk]), follow=True)
        self.assertContains(r, "izin kaydı var")
        p.refresh_from_db()
        self.assertFalse(p.silindi)

    def test_menu_izinler_ekrani(self):
        self.client.force_login(self.izinci)
        r = self.client.get(reverse("core:izinler"))
        self.assertContains(r, '<span class="ad">İnsan Kaynakları</span>')
        self.assertNotContains(r, "Personel Kartları")            # yalnız izin yetkisi
