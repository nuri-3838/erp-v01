"""Tek seferlik ``uretim_tarih_tasi`` komutu: onaylı üretim kayıtlarının tarihini (kayıt + stok hareketleri + maliyet aktarım fişi tarihi/USD kuru) YERİNDE düzeltir.
Varsayılan dry-run (hiçbir şey kalıcı yazılmaz); ``--uygula`` kalıcı; yeni negatif bakiye oluşuyorsa her iki modda hata ve her şey geri alınır; fiş no/yıl değişmez."""
import datetime
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur, OperasyonKaydi, StokHareket
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.uretim import emirsiz_uret, operasyon_olustur
from core.tarih import tr_bugun
from core.tests.test_istasyon_emri_kayit import Taban
from core.tests.test_uretim import _stok

D = Decimal
BUGUN = tr_bugun()
YIL = BUGUN.year
HEDEF = datetime.date(YIL, 1, 2)


class UretimTarihTasiTest(Taban):
    """Taban + ham(150.10) → mamul(151.10) operasyonu (hesaplar farklı → maliyet aktarım fişi oluşur); iki emirsiz kayıt BUGÜN tarihli."""

    def setUp(self):
        super().setUp()
        Kur.objects.get_or_create(tarih=BUGUN, defaults={"usd_alis": D("30")})
        self.kur_hedef = D("31")
        Kur.objects.update_or_create(tarih=HEDEF, defaults={"usd_alis": self.kur_hedef})
        fatura_tipi = FaturaTipi.objects.create(ad="ALIŞ TT", yon="ALIS")
        kat_ham, kat_mamul = Kategori.objects.create(kod="HT", ad="HAM TT"), Kategori.objects.create(kod="MT", ad="MAMUL TT")
        for kat, kod in ((kat_ham, "150.10"), (kat_mamul, "151.10")):
            KategoriHesap.objects.create(kategori=kat, fatura_tipi=fatura_tipi, hesap=HesapPlani.objects.create(
                hesap_kodu=kod, hesap_adi="TEST STOK", rapor_grubu="BILANCO", rapor_kalemi="DV", parasal=True, aktif=True))
        self.ham = _stok(kat_ham, self.birim, kod="IK-HAM-TT", ad="ham tt", satinalma=True)
        self.mamul = _stok(kat_mamul, self.birim, kod="IK-MAMUL-TT", ad="mamul tt")
        self.op = operasyon_olustur(istasyon_id=self.lazer.pk, cikti_id=self.mamul.pk, cikti_miktar=D("1"), satirlar=[(self.ham, D("2"))])

    def _giris(self, gun):
        hareket_ekle(stok_id=self.ham.pk, depo_id=self.depo.pk, tarih=gun, tur="GIRIS", miktar=D("100"), giris_tutar_try=D("1000"))

    def _kayitlar(self):
        self._giris(datetime.date(YIL, 1, 1))                                   # hedef tarihten ÖNCE: taşıma sonrası da stok var
        k1, _ = emirsiz_uret(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam="4")
        k2, _ = emirsiz_uret(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam="6")
        return k1, k2

    def _calistir(self, k1, k2, uygula, **kw):
        cikti = StringIO()
        args = dict(kayit_bas=k1.pk, kayit_son=k2.pk, eski_tarih=BUGUN.isoformat(), tarih=HEDEF.isoformat(), uygula=uygula, stdout=cikti)
        args.update(kw)
        call_command("uretim_tarih_tasi", **args)
        return cikti.getvalue()

    def _durum(self, k1, k2):
        ks = list(OperasyonKaydi.objects.filter(pk__in=[k1.pk, k2.pk]).select_related("fis").order_by("pk"))
        hs = StokHareket.objects.filter(operasyon_kaydi_id__in=[k1.pk, k2.pk], silindi=False)
        return ([k.tarih for k in ks], sorted({h.tarih for h in hs}), [(k.fis.tarih, k.fis.kur_usd, k.fis.yil, k.fis.fis_no) for k in ks], hs.count())

    def test_dry_run_hicbir_sey_yazmaz(self):
        k1, k2 = self._kayitlar()
        once = self._durum(k1, k2)
        cikti = self._calistir(k1, k2, uygula=False)
        self.assertIn("DRY-RUN", cikti)
        self.assertIn("KAPSAM", cikti)
        self.assertEqual(self._durum(k1, k2), once)                              # hiçbir şey değişmedi
        self.assertEqual(once[0], [BUGUN, BUGUN])

    def test_uygula_kayit_hareket_fis_tarihleri_yerinde_tasinir_fis_no_ayni(self):
        k1, k2 = self._kayitlar()
        tarihler0, hareket_tarihleri0, fisler0, hareket_sayisi0 = self._durum(k1, k2)
        eldeki0, mamul0 = eldeki_miktar(self.ham), eldeki_miktar(self.mamul)
        self.assertEqual(len({f[3] for f in fisler0}), 2)                         # iki ayrı maliyet fişi var
        cikti = self._calistir(k1, k2, uygula=True)
        self.assertIn("UYGULANDI", cikti)
        tarihler, hareket_tarihleri, fisler, hareket_sayisi = self._durum(k1, k2)
        self.assertEqual(tarihler, [HEDEF, HEDEF])
        self.assertEqual(hareket_tarihleri, [HEDEF])                              # girdi çıkışı + çıktı girişi hepsi
        self.assertEqual(hareket_sayisi, hareket_sayisi0)                         # hareket eklenmedi/silinmedi
        for (f_tarih, f_kur, f_yil, f_no), (_t, _k, y0, n0) in zip(fisler, fisler0):
            self.assertEqual((f_tarih, f_kur), (HEDEF, self.kur_hedef))           # fiş tarihi + yeni tarihin USD kuru
            self.assertEqual((f_yil, f_no), (y0, n0))                             # fiş no / yıl AYNI
        self.assertEqual((eldeki_miktar(self.ham), eldeki_miktar(self.mamul)), (eldeki0, mamul0))
        ort = StokHareket.objects.filter(stok=self.mamul, tur="GIRIS", silindi=False).values_list("tutar_try", flat=True)
        self.assertTrue(all(t is not None and t > 0 for t in ort))                # maliyet bozulmadı (fiyatlı giriş: 10 TL/adet)

    def test_yeni_negatif_bakiye_olusuyorsa_hata_ve_geri_alma(self):
        self._giris(datetime.date(YIL, 1, 3))                                     # stok HEDEF'ten SONRA geliyor: tüketimi HEDEF'e çekmek negatif yapar
        k1, _ = emirsiz_uret(operasyon_id=self.op.pk, depo_id=self.depo.pk, tarih=BUGUN, saglam="4")
        once = self._durum(k1, k1)
        with self.assertRaisesMessage(CommandError, "negatif"):
            self._calistir(k1, k1, uygula=True)
        self.assertEqual(self._durum(k1, k1), once)                               # her şey geri alındı

    def test_kapsam_dogrulamasi_yanlis_eski_tarih(self):
        k1, k2 = self._kayitlar()
        with self.assertRaisesMessage(CommandError, "tarihi"):
            self._calistir(k1, k2, uygula=True, eski_tarih="2026-01-01")
        self.assertEqual(self._durum(k1, k2)[0], [BUGUN, BUGUN])
