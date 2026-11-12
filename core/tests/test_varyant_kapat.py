"""varyant_kapat (2026-11-12, CS ayak SOL 12 kart): yanlışlıkla varyantlı açılmış kart varyantsız olur — varyant bağları (hareket — silinmişler dahil —, belge kalemi…) NULL, kart ayarı kapanır,
varyant satırı kapanır; miktar/değer/ortalama/hareket tutarları/mizan/depo eldeki/ihtiyaç planı DEĞİŞMEZ; kuru çalıştırma iz bırakmaz; iki varyantlı kartta DURUR; SABİT kural TASI'ye döner;
diğer varyantlı kartlara dokunulmaz; idempotent; denetim dökümü yazılır."""
import datetime
import json
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from core.models import OperasyonGirdi, Stok, StokHareket, StokVaryant, TeklifSiparisKalem
from core.services import stok_ortalama
from core.services import varyant as vs
from core.services import varyant_kapat as vk
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle
from core.services.teklif_siparis import teklif_siparis_olustur
from core.tests.test_varyant_operasyon import OperasyonVaryantTaban

D = Decimal
G, C = StokHareket.Tur.GIRIS, StokHareket.Tur.CIKIS
SABIT = "SABIT"


class KapatTaban(OperasyonVaryantTaban):
    def setUp(self):
        self.kes = self.kart("151-10-0064", boyutlar=["MARKALAMA"])                            # kesilmiş SOL (varsayılan SEMTA satırı)
        self.boy = self.kart("151-10-0076", boyutlar=["MARKALAMA"])                            # boyalı SOL
        self.diger = self.kart("151-10-0001", boyutlar=["MARKALAMA"])                          # dokunulmayacak başka varyantlı kart
        self.profil = self.kart("150-10-0001")
        self.mamul = self.kart("152-10-0001")
        self.op_kes = self.op("10", self.kes, [(self.profil, D("1"))])
        self.op_boy = self.op("12", self.boy, [(self.kes, D("1"))])
        self.op_mon = self.op("70", self.mamul, [(self.boy, D("1"))])
        self.sv_kes = StokVaryant.objects.get(stok=self.kes, silindi=False)
        self.sv_boy = StokVaryant.objects.get(stok=self.boy, silindi=False)
        self.kodlar = ["151-10-0064", "151-10-0076"]

    def hareket(self, stok, tur, miktar, gun, tutar=None, silindi=False):
        h = hareket_ekle(stok_id=stok.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 10, gun), tur=tur, miktar=D(miktar),
                         giris_tutar_try=None if tutar is None else D(tutar))
        if silindi:
            h.silindi = True
            h.save(update_fields=["silindi"])
        return h

    def kur(self):
        self.hareket(self.kes, G, "10", 1, "100")                                              # 10 @ 10,00
        self.hareket(self.kes, C, "4", 2)                                                      # kesilmiş tüketildi
        self.hareket(self.kes, G, "1", 3, "9", silindi=True)                                   # silinmiş hareket de varyantsıza çevrilir
        self.hareket(self.boy, G, "6", 4, "120")                                               # boyalı 6 @ 20,00
        self.hareket(self.boy, C, "1", 5)
        stok_ortalama.yeniden_hesapla(self.kes)
        stok_ortalama.yeniden_hesapla(self.boy)
        self.kalem = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.cari().pk, tarih=datetime.date(2026, 10, 1),
                                            satirlar=[{"stok_id": self.boy.pk, "miktar": "5", "birim_fiyat": "2", "varyant": self.sv_boy.pk}])

    def cari(self):
        from core.models import Cari
        return Cari.objects.create(kod="CVK1", unvan="TED VK", para_birimi="TRY", muhasebe_kodu="320.30.0001")

    def calistir(self, *arg, kodlar=None):
        cikti = StringIO()
        with tempfile.TemporaryDirectory() as t, override_settings(BASE_DIR=Path(t)):
            args = []
            for k in (kodlar or self.kodlar):
                args += ["--stok", k]
            call_command("varyant_kapat", *args, *arg, stdout=cikti)
            loglar = list((Path(t) / "logs").glob("varyant_kapat_*.json")) if (Path(t) / "logs").exists() else []
            veri = json.loads(loglar[0].read_text(encoding="utf-8")) if loglar else None
        return cikti.getvalue(), veri

    def anlik(self, stok):
        s = Stok.objects.get(pk=stok.pk)
        return (s.maliyet_miktar, s.maliyet_deger_try, s.ort_maliyet_try,
                sorted((h.tarih, h.tur, h.miktar, h.tutar_try, h.maliyet_durumu) for h in StokHareket.objects.filter(stok=s, silindi=False)))


class KuruCalistirmaTest(KapatTaban):
    def test_kuru_hicbir_sey_yazmaz_ama_rapor_verir(self):
        self.kur()
        once = {k.pk: self.anlik(k) for k in (self.kes, self.boy)}
        cikti, log = self.calistir()
        self.assertIn("KURU ÇALIŞTIRMA", cikti)
        self.assertIn("StokHareket.varyant: 5", cikti)                                         # kes 3 (1 silinmiş) + boy 2
        self.assertIn("TeklifSiparisKalem.varyant: 1", cikti)
        self.assertIn("varyantı kaldırılan hareket: 5 (aktif 4, silinmiş 1)", cikti)
        self.assertIsNone(log)
        for k in (self.kes, self.boy):
            k.refresh_from_db()
            self.assertTrue(k.varyantli)
            self.assertEqual(k.varyant_boyutlari, ["MARKALAMA"])
            self.assertIsNotNone(k.varsayilan_varyant_id)
        self.assertEqual(StokHareket.objects.filter(stok__in=[self.kes, self.boy], varyant__isnull=True).count(), 0)
        self.assertEqual(StokVaryant.objects.filter(stok__in=[self.kes, self.boy], silindi=False).count(), 2)
        self.assertEqual({k.pk: self.anlik(k) for k in (self.kes, self.boy)}, once)


class UygulamaTest(KapatTaban):
    def test_uygula_varyantsiz_yapar_miktar_tutar_degismez(self):
        self.kur()
        once = {k.pk: self.anlik(k) for k in (self.kes, self.boy)}
        eldeki_once = {k.pk: eldeki_miktar(k) for k in (self.kes, self.boy)}
        cikti, log = self.calistir("--uygula")
        self.assertIn("UYGULANDI", cikti)
        self.assertIn("[OK]", cikti)
        self.assertIn("İHTİYAÇ PLANI (bitmiş ürün × 10 adet; kart bazlı + varyantlı planlama): AYNI", cikti)
        self.assertIn("152-10-0001", cikti)                                                                                       # planı karşılaştırılan bitmiş ürün
        self.assertNotIn("[HATA]", cikti)
        for k in (self.kes, self.boy):
            k.refresh_from_db()
            self.assertEqual((k.varyantli, k.varyant_boyutlari, k.varsayilan_varyant_id), (False, [], None))
        self.assertEqual(StokHareket.objects.filter(stok__in=[self.kes, self.boy]).exclude(varyant__isnull=True).count(), 0)      # silinmişler dahil
        self.assertEqual(StokVaryant.objects.filter(stok__in=[self.kes, self.boy], silindi=False).count(), 0)                    # satırlar kapandı (iz kalır)
        self.assertEqual(StokVaryant.objects.filter(stok__in=[self.kes, self.boy], silindi=True).count(), 2)
        self.assertEqual({k.pk: self.anlik(k) for k in (self.kes, self.boy)}, once)                                               # miktar / değer / ort / hareket tutarları
        self.assertEqual({k.pk: eldeki_miktar(k) for k in (self.kes, self.boy)}, eldeki_once)
        self.assertEqual(TeklifSiparisKalem.objects.get(stok=self.boy).varyant_id, None)                                          # belge kalemi de varyantsız
        self.kontrol_sifirdan_hesap_esit(self.kes)
        self.kontrol_sifirdan_hesap_esit(self.boy)
        # denetim dökümü
        self.assertEqual(sorted(log["kartlar"]), sorted(self.kodlar))
        self.assertEqual(len(log["degisen_hareket_idleri"]), 5)                                                                   # kes 3 (1 silinmiş) + boy 2

    def kontrol_sifirdan_hesap_esit(self, stok):
        hs = stok_ortalama.kart_hareketleri(stok)
        for h, s in zip(hs, stok_ortalama.hesapla(hs)):
            self.assertEqual((h.maliyet_durumu, h.tutar_try, h.sonrasi_deger_try), (s.durum, s.tutar_try, s.sonrasi_deger_try))

    def test_uygulamadan_sonra_yeni_hareket_varyantsiz_yazilir(self):
        self.kur()
        self.calistir("--uygula")
        h = self.hareket(self.boy, G, "2", 9, "40")
        self.assertIsNone(h.varyant_id)
        with self.assertRaises(Exception):
            hareket_ekle(stok_id=self.boy.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 10, 10), tur=G, miktar=D("1"), varyant=self.sv_boy.pk)

    def test_ikinci_calistirma_is_cikarmaz(self):
        self.kur()
        self.calistir("--uygula")
        cikti, log = self.calistir("--uygula")
        self.assertIn("Zaten varyantsız", cikti)
        self.assertIn("Yapılacak iş yok", cikti)
        self.assertIsNone(log)

    def test_diger_varyantli_kartlara_dokunulmaz(self):
        self.kur()
        self.hareket(self.diger, G, "5", 6, "50")
        sv = StokVaryant.objects.get(stok=self.diger, silindi=False)
        self.calistir("--uygula")
        self.diger.refresh_from_db()
        self.assertTrue(self.diger.varyantli)
        self.assertEqual(StokHareket.objects.get(stok=self.diger).varyant_id, sv.pk)
        self.assertFalse(StokVaryant.objects.get(pk=sv.pk).silindi)


class KuralVeKorumaTest(KapatTaban):
    def test_sabit_kural_tasiya_alinir_tasi_dokunulmaz(self):
        self.kur()
        mon2 = self.op("20", self.kart("151-80-0001", boyutlar=["MARKALAMA"]), [(self.kes, D("1"), SABIT, self.yok)])           # varyantlı girdi, SABİT YOK
        self.assertEqual(mon2.girdiler.get(silindi=False).varyant_kurali, SABIT)
        cikti, _ = self.calistir("--uygula")
        g = mon2.girdiler.get(silindi=False)
        self.assertEqual((g.varyant_kurali, g.sabit_varyant_id), ("TASI", None))
        self.assertIn("SABİT kural → TASI düzeltilen: 1", cikti)
        for op in (self.op_kes, self.op_boy, self.op_mon):                                                                        # varsayılan TASI kurallar aynen
            self.assertEqual({x.varyant_kurali for x in OperasyonGirdi.objects.filter(operasyon=op, silindi=False)}, {"TASI"})
        self.assertIn(str(self.op_kes.pk), cikti)

    def test_iki_varyantli_kartta_durur_ve_hicbir_sey_degismez(self):
        self.kur()
        sv2 = vs.stok_varyanti_bul_veya_olustur(self.kes, {"MARKALAMA": self.yok})
        hareket_ekle(stok_id=self.kes.pk, depo_id=self.depo.pk, tarih=datetime.date(2026, 10, 7), tur=G, miktar=D("2"), varyant=sv2.pk, giris_tutar_try=D("20"))
        with self.assertRaisesMessage(CommandError, "tek aktif varyant satırı olmalı"):
            self.calistir("--uygula")
        self.kes.refresh_from_db()
        self.assertTrue(self.kes.varyantli)
        self.assertEqual(StokHareket.objects.filter(stok=self.kes, varyant__isnull=True).count(), 0)

    def test_hareketlerde_baska_varyant_durur(self):
        self.kur()
        sv2 = vs.stok_varyanti_bul_veya_olustur(self.kes, {"MARKALAMA": self.yok})
        StokVaryant.objects.filter(pk=sv2.pk).update(silindi=True)                                                              # aktif satır tek ama hareket başka varyantta
        StokHareket.objects.filter(pk=StokHareket.objects.filter(stok=self.kes).first().pk).update(varyant=sv2)
        with self.assertRaisesMessage(CommandError, "hareketlerde birden çok varyant"):
            self.calistir("--uygula")

    def test_bilinmeyen_kod_hata(self):
        with self.assertRaisesMessage(CommandError, "Stok bulunamadı: YOK-KOD"):
            self.calistir(kodlar=["YOK-KOD"])

    def test_varsayilan_12_kod_listesi(self):
        self.assertEqual(len(vk.VARSAYILAN_KODLAR), 12)
        self.assertEqual(vk.VARSAYILAN_KODLAR[0], "151-10-0064")
        self.assertEqual(vk.VARSAYILAN_KODLAR[-1], "151-10-0086")
