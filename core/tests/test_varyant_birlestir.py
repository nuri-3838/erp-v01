"""varyant_birlestir komutu (2026-10-20, 150-50-0002 YAN ETİKET SEMTA → YOK): önizleme hiçbir şey yazmaz; uygulama TÜM varyant bağlarını (hareket — silinmişler dahil —, belge kalemleri,
kartın varsayılan varyantı) hedefe çevirir, tanımlı operasyon girdilerini SABİT YOK yapar, kaynak satırı kapatır, ortalamayı birleştirir; miktar/maliyet/çıkış tutarları/mizan DEĞİŞMEZ;
çıkış maliyetini değiştirecek durumda DURUR; ikinci çalıştırma iş çıkarmaz; denetim dökümü yazılır."""
import datetime
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from core.models import OperasyonGirdi, StokHareket, StokVaryant, TeklifSiparisKalem
from core.services import uretim
from core.services import varyant as vs
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.teklif_siparis import teklif_siparis_olustur
from core.tests.test_varyant_operasyon import OperasyonVaryantTaban

D = Decimal
G, C = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS


class BirlestirTaban(OperasyonVaryantTaban):
    def setUp(self):
        self.yan = self.kart("150-50-0002", boyutlar=["YAN_ETIKET"])                        # varsayılan = SEMTA
        self.sv_semta = StokVaryant.objects.get(stok=self.yan, silindi=False)
        self.sv_yok = vs.stok_varyanti_bul_veya_olustur(self.yan, {"YAN_ETIKET": self.yok})
        self.urun = self.kart("152-10-0001")

    def giris(self, sv, miktar, tutar, gun):
        return hareket_ekle(stok_id=self.yan.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 10, gun), tur=G, miktar=D(miktar), varyant=sv.pk,
                            giris_tutar_try=D(tutar), giris_ortalama=False)

    def cikis(self, sv, miktar, gun):
        return hareket_ekle(stok_id=self.yan.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 10, gun), tur=C, miktar=D(miktar), varyant=sv.pk)

    def calistir(self, *arg):
        cikti = StringIO()
        with tempfile.TemporaryDirectory() as t, override_settings(BASE_DIR=Path(t)):
            call_command("varyant_birlestir", "--stok", "150-50-0002", "--kaynak", "SEMTA", "--hedef", "YOK", *arg, stdout=cikti)
            loglar = list((Path(t) / "logs").glob("varyant_birlestir_*.json")) if (Path(t) / "logs").exists() else []
            veri = json.loads(loglar[0].read_text(encoding="utf-8")) if loglar else None
        return cikti.getvalue(), veri

    def operasyon(self):
        return self.op("80", self.urun, [(self.yan, D("1"))])


class UygulamaTest(BirlestirTaban):
    def kur(self):
        self.g1 = self.giris(self.sv_semta, "10", "20", 1)                                   # 10 @ 2,00
        self.c1 = self.cikis(self.sv_semta, "3", 2)                                           # 3 çıkış (6,00) — YOK girişinden ÖNCE
        self.g2 = self.giris(self.sv_yok, "4", "6", 10)                                       # 4 @ 1,50 (SAI girişi gibi)
        silinen = self.giris(self.sv_semta, "1", "2", 3)
        silinen.silindi = True
        silinen.save(update_fields=["silindi"])
        from core.services import stok_ortalama
        stok_ortalama.yeniden_hesapla(self.yan)                                              # önbellek tutarlı (gerçek veride de öyle)
        self.op_ = self.operasyon()
        self.sevk = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari_olustur().pk, tarih=datetime.date(2026, 10, 1),
                                           satirlar=[{"stok_id": self.yan.pk, "miktar": "5", "birim_fiyat": "2", "varyant": self.sv_semta.pk}])

    def cari_olustur(self):
        from core.models import Cari
        return Cari.objects.create(kod="CBR1", unvan="TED BR", para_birimi="TRY", muhasebe_kodu="320.30.0001")

    def test_onizleme_hicbir_sey_yazmaz(self):
        self.kur()
        cikti, log = self.calistir()
        self.assertIn("ÖNİZLEME", cikti)
        self.assertIn("StokHareket.varyant", cikti)
        self.assertIsNone(log)                                                                # önizlemede log yok
        self.assertEqual(StokHareket.objects.filter(varyant=self.sv_semta).count(), 3)       # silinmiş dahil
        self.yan.refresh_from_db()
        self.assertEqual(self.yan.varsayilan_varyant_id, self.sv_semta.pk)
        self.assertFalse(StokVaryant.objects.get(pk=self.sv_semta.pk).silindi)
        self.assertEqual(OperasyonGirdi.objects.get(girdi=self.yan, silindi=False).varyant_kurali, "TASI")

    def test_uygula_tum_baglari_ceviri_maliyet_birlesir_cikis_tutari_degismez(self):
        self.kur()
        c_once = StokHareket.objects.get(pk=self.c1.pk).tutar_try
        cikti, log = self.calistir("--uygula")
        self.assertIn("UYGULANDI", cikti)
        self.assertEqual(StokHareket.objects.filter(stok=self.yan, varyant=self.sv_semta).count(), 0)
        self.assertEqual(StokHareket.objects.filter(stok=self.yan, varyant=self.sv_yok).count(), 4)           # 3 aktif + 1 silinmiş + YOK girişi
        self.assertEqual(TeklifSiparisKalem.objects.get(teklif_siparis=self.sevk).varyant_id, self.sv_yok.pk)  # belge kalemi de çevrildi
        self.yan.refresh_from_db()
        self.assertEqual(self.yan.varsayilan_varyant_id, self.sv_yok.pk)
        sk = StokVaryant.objects.get(pk=self.sv_semta.pk)
        self.assertTrue(sk.silindi)
        self.assertEqual((sk.maliyet_miktar, sk.maliyet_deger_try), (D("0"), D("0")))
        g = OperasyonGirdi.objects.get(girdi=self.yan, silindi=False)
        self.assertEqual((g.varyant_kurali, g.sabit_varyant_id), ("SABIT", self.yok.pk))
        self.assertEqual(eldeki_miktar(self.yan.pk), D("11"))                                              # 10 − 3 + 4
        self.assertEqual(StokHareket.objects.get(pk=self.c1.pk).tutar_try, c_once)                         # çıkış maliyeti DEĞİŞMEDİ
        yok = StokVaryant.objects.get(pk=self.sv_yok.pk)
        self.assertEqual((yok.maliyet_miktar, yok.maliyet_deger_try), (D("11"), D("20.00")))               # 14 + 6 = 20 TL, tek zincir; ort 1,818182
        self.assertEqual(self.yan.ort_maliyet_try, D("1.818182"))
        self.assertTrue(log["mizan_ayni"])
        self.assertEqual(log["degisen_satirlar"]["StokHareket.varyant"].__len__(), 3)
        self.assertEqual(len(log["operasyon_girdileri"]), 1)
        self.assertTrue(log["kaynak_satiri_kapatildi"])

    def test_ikinci_calistirma_is_cikarmaz(self):
        self.kur()
        self.calistir("--uygula")
        cikti, log = self.calistir("--uygula")
        self.assertIn("yapılacak iş yok", cikti)
        self.assertIsNone(log)

    def test_kaynak_satirini_kapatma_secenegi(self):
        self.kur()
        self.calistir("--uygula", "--kaynak-satirini-kapatma")
        self.assertFalse(StokVaryant.objects.get(pk=self.sv_semta.pk).silindi)


class GuvenlikTest(BirlestirTaban):
    def test_cikis_maliyeti_degisecekse_durur_ve_hicbir_sey_yazmaz(self):
        self.giris(self.sv_semta, "10", "20", 1)
        self.giris(self.sv_yok, "4", "6", 5)
        self.cikis(self.sv_semta, "3", 10)                                                    # YOK girişinden SONRA: zincirler birleşince maliyeti değişirdi
        with self.assertRaisesMessage(CommandError, "DURDURULDU"):
            self.calistir("--uygula")
        self.assertEqual(StokHareket.objects.filter(varyant=self.sv_semta).count(), 2)
        self.yan.refresh_from_db()
        self.assertEqual(self.yan.varsayilan_varyant_id, self.sv_semta.pk)
        self.assertFalse(StokVaryant.objects.get(pk=self.sv_semta.pk).silindi)

    def test_gecersiz_girdiler(self):
        with self.assertRaisesMessage(CommandError, "varyantlı stok kartı bulunamadı"):
            call_command("varyant_birlestir", "--stok", "YOK-123", "--kaynak", "SEMTA", "--hedef", "YOK", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "bulunamadı ya da aynı"):
            call_command("varyant_birlestir", "--stok", "150-50-0002", "--kaynak", "SEMTA", "--hedef", "SEMTA", stdout=StringIO())
        tek = self.kart("151-10-0001", boyutlar=["MARKALAMA", "AFIS"])
        with self.assertRaisesMessage(CommandError, "TEK boyutlu"):
            call_command("varyant_birlestir", "--stok", tek.kod, "--kaynak", "SEMTA", "--hedef", "YOK", stdout=StringIO())


class KartKaydiTest(BirlestirTaban):
    """Kart kaydedilince (stok_varyantlarini_esitle) varsayılan olmayan boş standart (SEMTA) satır yeniden AÇILMAZ; kapatılmış kapalı kalır."""

    def aktif(self):
        return sorted(StokVaryant.objects.filter(stok=self.yan, silindi=False).values_list("anahtar", flat=True))

    def test_varsayilani_yok_olan_kartta_kapali_semta_satiri_yeniden_acilmaz(self):
        self.giris(self.sv_yok, "5", "7.5", 5)
        self.calistir("--uygula")                                                             # SEMTA satırı kapanır, varsayılan YOK
        self.yan.refresh_from_db()
        self.assertEqual(self.aktif(), [self.sv_yok.anahtar])
        vs.stok_varyantlarini_esitle(self.yan)                                                # kart kaydı
        self.assertEqual(self.aktif(), [self.sv_yok.anahtar])
        self.assertEqual(StokVaryant.objects.filter(stok=self.yan).count(), 2)                # yeni satır AÇILMADI
        self.assertEqual(eldeki_miktar(self.yan.pk), D("5"))

    def test_varsayilani_yok_olup_hic_standart_satiri_olmayan_kartta_da_acilmaz(self):
        StokVaryant.objects.filter(pk=self.sv_semta.pk).delete()                              # hiç standart satır yok
        Stok = type(self.yan)
        Stok.objects.filter(pk=self.yan.pk).update(varsayilan_varyant=self.sv_yok)
        self.yan.refresh_from_db()
        vs.stok_varyantlarini_esitle(self.yan)
        self.assertEqual(self.aktif(), [self.sv_yok.anahtar])

    def test_acik_bos_standart_satir_olan_kartta_dokunulmaz(self):
        Stok = type(self.yan)
        Stok.objects.filter(pk=self.yan.pk).update(varsayilan_varyant=self.sv_yok)            # kesilmiş parça kartlarındaki gibi: varsayılan YOK + boş SEMTA* satırı açık
        self.yan.refresh_from_db()
        vs.stok_varyantlarini_esitle(self.yan)
        self.assertEqual(self.aktif(), sorted([self.sv_semta.anahtar, self.sv_yok.anahtar]))   # kapatılmaz, silinmez

    def test_normal_kartta_standart_satir_hala_garanti_edilir(self):
        urun = self.kart("151-10-0001", boyutlar=["MARKALAMA"])                               # varsayılan = standart (SEMTA)
        std = StokVaryant.objects.get(stok=urun, silindi=False)
        StokVaryant.objects.filter(pk=std.pk).update(silindi=True)                            # standart satır kapanmış
        Stok = type(urun)
        Stok.objects.filter(pk=urun.pk).update(varsayilan_varyant=None)
        urun.refresh_from_db()
        vs.stok_varyantlarini_esitle(urun)
        yeni = StokVaryant.objects.filter(stok=urun, silindi=False)
        self.assertEqual([(v.standart, v.anahtar) for v in yeni], [(True, std.anahtar)])
