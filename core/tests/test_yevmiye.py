"""Yevmiye servis + model testleri (spec bölüm 2-3): dengeli fiş, satır kuralları,
yabancı PB türetme, müteselsil no, kur_usd, iptal."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from core.models import Kur, YevmiyeFisi, YevmiyeSatir, HesapPlani
from core.services.yevmiye import SatirGirdi, YevmiyeHatasi, fis_iptal, fis_olustur

D = datetime.date


def _try_satir(hesap_kodu, taraf, tutar, aciklama=""):
    return SatirGirdi(hesap_kodu=hesap_kodu, taraf=taraf, islem_tutari=tutar,
                      islem_pb="TRY", islem_kuru=Decimal("1"), aciklama=aciklama)


class YevmiyeTestTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_hesap_plani")
        from core.models import Kur as _Kur
        from decimal import Decimal as _Dec
        import datetime as _dtk
        _b0 = _dtk.date(2024, 1, 1)
        _Kur.objects.bulk_create([_Kur(tarih=_b0 + _dtk.timedelta(days=_i), usd_alis=_Dec("30"))
                                  for _i in range((_dtk.date(2027, 12, 31) - _b0).days + 1)])


class GecerliFisTest(YevmiyeTestTemel):
    def test_dengeli_try_fis_kaydedilir(self):
        fis = fis_olustur(
            tarih=D(2026, 3, 10),
            aciklama="kasa tahsilatı",
            satirlar=[
                _try_satir("100", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ],
        )
        self.assertEqual(fis.yil, 2026)
        self.assertEqual(fis.fis_no, 1)
        self.assertEqual(fis.aciklama, "KASA TAHSİLATI")  # TR büyük harf
        self.assertEqual(fis.satirlar.count(), 2)
        borc = fis.satirlar.get(hesap_id="100")
        self.assertEqual(borc.borc, Decimal("1000.00"))
        self.assertEqual(borc.alacak, Decimal("0.00"))

    def test_bakiye_alani_yok(self):
        # "Bakiyeler hesaplanır, saklanmaz": modelde bakiye alanı olmamalı.
        alanlar = {f.name for f in YevmiyeSatir._meta.get_fields()}
        self.assertNotIn("bakiye", alanlar)


class DengeVeSatirKurallariTest(YevmiyeTestTemel):
    def test_dengesiz_fis_reddedilir_ve_yazilmaz(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(
                tarih=D(2026, 3, 10),
                satirlar=[
                    _try_satir("100", "B", "1.000,00"),
                    _try_satir("600", "A", "900,00"),
                ],
            )
        self.assertEqual(YevmiyeFisi.objects.count(), 0)
        self.assertEqual(YevmiyeSatir.objects.count(), 0)

    def test_en_az_iki_satir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10),
                        satirlar=[_try_satir("100", "B", "1.000,00")])

    def test_sifir_tutar_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("100", "B", "0,00"),
                _try_satir("600", "A", "0,00"),
            ])

    def test_negatif_tutar_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("100", "B", "-5,00"),
                _try_satir("600", "A", "-5,00"),
            ])

    def test_try_kuru_bir_olmali(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                SatirGirdi("100", "B", "1.000,00", "TRY", Decimal("2")),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_gecersiz_kur_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                SatirGirdi("100", "B", "1.000,00", "USD", Decimal("0")),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_pasif_hesap_reddedilir(self):
        HesapPlani.objects.filter(hesap_kodu="153").update(aktif=False)
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("153", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ])

    def test_olmayan_hesap_reddedilir(self):
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("999", "B", "1.000,00"),
                _try_satir("600", "A", "1.000,00"),
            ])


class YabanciParaTest(YevmiyeTestTemel):
    def test_eur_tl_turetilir(self):
        # 1.000 EUR × 35 = 35.000 TL (borç); karşı TRY 35.000 alacak
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            SatirGirdi("153", "B", "1.000,00", "EUR", Decimal("35")),
            _try_satir("320", "A", "35.000,00"),
        ])
        satir = fis.satirlar.get(hesap_id="153")
        self.assertEqual(satir.borc, Decimal("35000.00"))
        self.assertEqual(satir.islem_pb, "EUR")
        self.assertEqual(satir.islem_tutari, Decimal("1000.00"))
        self.assertEqual(satir.islem_kuru, Decimal("35"))

    def test_tl_round_half_up(self):
        # 100,00 USD × 30,123456 = 3012,3456 -> 3012,35 (ROUND_HALF_UP)
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            SatirGirdi("100", "B", "100,00", "USD", Decimal("30.123456")),
            _try_satir("600", "A", "3.012,35"),
        ])
        self.assertEqual(fis.satirlar.get(hesap_id="100").borc, Decimal("3012.35"))


class MuteselsilNoTest(YevmiyeTestTemel):
    def test_yil_icinde_artar_yil_basinda_sifirlanir(self):
        f1 = fis_olustur(tarih=D(2026, 1, 5), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        f2 = fis_olustur(tarih=D(2026, 6, 5), satirlar=[
            _try_satir("100", "B", "20,00"), _try_satir("600", "A", "20,00")])
        f3 = fis_olustur(tarih=D(2027, 1, 2), satirlar=[
            _try_satir("100", "B", "30,00"), _try_satir("600", "A", "30,00")])
        self.assertEqual((f1.yil, f1.fis_no), (2026, 1))
        self.assertEqual((f2.yil, f2.fis_no), (2026, 2))
        self.assertEqual((f3.yil, f3.fis_no), (2027, 1))

    def test_no_carpismasinda_retry(self):
        # #3: numara çakışmasını (yarış koşulu) taklit et — _sonraki_fis_no ilk
        # çağrıda kapılmış (1) döndürsün, sonra doğru (2). fis_olustur retry'lemeli.
        from unittest.mock import patch
        f1 = fis_olustur(tarih=D(2026, 5, 1), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(f1.fis_no, 1)
        with patch("core.services.yevmiye._sonraki_fis_no", side_effect=[1, 2]):
            f2 = fis_olustur(tarih=D(2026, 5, 2), satirlar=[
                _try_satir("100", "B", "20,00"), _try_satir("600", "A", "20,00")])
        self.assertEqual(f2.fis_no, 2)                 # çakışmadan sonra ikinci deneme tuttu
        self.assertEqual(YevmiyeFisi.objects.filter(yil=2026).count(), 2)


class IptalTest(YevmiyeTestTemel):
    def test_iptal_numarayi_korur(self):
        f1 = fis_olustur(tarih=D(2026, 3, 1), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        fis_iptal(f1)
        f1.refresh_from_db()
        self.assertTrue(f1.silindi)
        self.assertTrue(all(s.silindi for s in f1.satirlar.all()))
        # Yeni fiş numarayı yeniden KULLANMAZ -> 2
        f2 = fis_olustur(tarih=D(2026, 3, 2), satirlar=[
            _try_satir("100", "B", "20,00"), _try_satir("600", "A", "20,00")])
        self.assertEqual(f2.fis_no, 2)


class FisGuncelleSoftDeleteTest(YevmiyeTestTemel):
    def test_guncelleme_eski_satirlari_soft_siler(self):
        # #2: düzenlemede eski satırlar fiziksel silinmez, silindi=True işaretlenir.
        from decimal import Decimal as _D
        from django.db.models import Sum
        from core.services.yevmiye import fis_guncelle
        f = fis_olustur(tarih=D(2026, 4, 1), satirlar=[
            _try_satir("100", "B", "100,00"), _try_satir("600", "A", "100,00")])
        eski_ids = list(f.satirlar.values_list("id", flat=True))
        fis_guncelle(f, tarih=D(2026, 4, 1), satirlar=[
            _try_satir("100", "B", "150,00"), _try_satir("600", "A", "150,00")])
        # Eski satırlar DB'de DURUYOR ama silindi=True (iz korunur)
        eski = YevmiyeSatir.objects.filter(id__in=eski_ids)
        self.assertEqual(eski.count(), 2)
        self.assertTrue(all(s.silindi for s in eski))
        # Aktif satırlar yeni tutarla; gizli satırlar toplama girmez
        aktif = f.satirlar.filter(silindi=False)
        self.assertEqual(aktif.count(), 2)
        self.assertEqual(aktif.aggregate(t=Sum("borc"))["t"], _D("150.00"))


class KurUsdTest(YevmiyeTestTemel):
    def _kur(self, y, m, d, usd):
        obj, _ = Kur.objects.update_or_create(
            tarih=D(y, m, d),
            defaults=dict(usd_alis=Decimal(usd), eur_alis=Decimal("38"),
                          gbp_alis=Decimal("44"), silindi=False))
        return obj

    def test_otomatik_fis_tarihine_gore(self):
        self._kur(2026, 3, 10, "32.5")
        fis = fis_olustur(tarih=D(2026, 3, 10), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(fis.kur_usd, Decimal("32.500000"))

    def test_kur_yoksa_eski_kur_tasinmaz(self):
        # Önceki tarihte kur var ama fiş tarihinde BİREBİR kur yok -> eski kur
        # taşınmaz, fiş kaydedilmez (kullanıcının bildirdiği hata: 25.06.2026).
        Kur.objects.all().delete()
        self._kur(2026, 3, 13, "33")  # yalnız önceki tarihte kur
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 14), satirlar=[
                _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_kur_yoksa_fis_kaydedilmez(self):
        Kur.objects.all().delete()   # hiç kur yok
        with self.assertRaises(YevmiyeHatasi):
            fis_olustur(tarih=D(2026, 3, 10), satirlar=[
                _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(YevmiyeFisi.objects.count(), 0)

    def test_elle_override(self):
        self._kur(2026, 3, 10, "32")
        fis = fis_olustur(tarih=D(2026, 3, 10), kur_usd=Decimal("35"), satirlar=[
            _try_satir("100", "B", "10,00"), _try_satir("600", "A", "10,00")])
        self.assertEqual(fis.kur_usd, Decimal("35"))
