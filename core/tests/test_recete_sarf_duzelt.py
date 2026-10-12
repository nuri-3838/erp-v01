"""``recete_sarf_duzelt`` komutu / ``core.services.recete_sarf``: reçetede sonradan artırılan girdi miktarı için geçmiş onaylı kayıtların eksik sarfı —
bugün tarihli sabit tutarlı çıkış, ORAN bazında dağıtım (ara mamul stokta kalan / mamul stokta kalan / satılan / numune), 0 adetlik maliyet
düzeltmesi girişleri, TEK STOK_DUZELTME fişi; kuru çalıştırma hiçbir şey bırakmaz, --uygula idempotent; stok değeri − mizan farkı değişmez."""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from core.models import (Birim, Depo, FaturaTipi, HesapPlani, IsIstasyonu, Kategori, KategoriHesap, KdvOrani, Kur, Stok, StokHareket,
                         YevmiyeFisi)
from core.services import recete_sarf
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle, hareket_sil
from core.services.uretim import UretimHatasi, operasyon_guncelle, operasyon_kaydi_sil, operasyon_olustur, uretim_emri_olustur, uretim_emri_toplu_uret

D = Decimal
GUN = date(2026, 2, 1)                                    # düzeltme günü


def _hesap(kod, ad, grup="BILANCO", kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad, rapor_grubu=grup, rapor_kalemi=kalem, parasal=True)


class ReceteSarfTaban(TestCase):
    """Kayış (151.10) → montaj → paketsiz ara mamul (151.99) → paketleme → mamul (152.10). Montaj reçetesi kayış 1 → sonradan 2'ye düzeltilir."""

    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("rsyon", password="x")
        for t in (date(2026, 1, 2), date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 14), GUN):
            Kur.objects.get_or_create(tarih=t, defaults={"usd_alis": D("40")})
        _hesap("191", "İNDİRİLECEK KDV")
        _hesap("391", "HESAPLANAN KDV", kalem="KVYK")
        for kod, ad in (("151.10", "KESİLMİŞ PARÇALAR"), ("151.99", "PAKETSİZ MERDİVENLER"), ("152.10", "MAMUL A TİPİ")):
            _hesap(kod, ad)
        _hesap("620", "SATILAN MAMULLER MALİYETİ", grup="GELIR_TABLOSU", kalem="A")
        _hesap("760.06", "NUMUNE GİDERLERİ", grup="GELIR_TABLOSU", kalem="A")
        cls.kdv = KdvOrani.objects.create(aciklama="RS", oran=D("20"), hesap_borc=HesapPlani.objects.get(hesap_kodu="191"),
                                          hesap_alacak=HesapPlani.objects.get(hesap_kodu="391"))
        alis = FaturaTipi.objects.create(ad="ALIŞ RS", yon=FaturaTipi.Yon.ALIS)
        birim = Birim.objects.create(ad="ADET RS", kisa_ad="ADRS", ondalik=0)
        ust = Kategori.objects.create(ad="RS ÜST", kod="rsu")

        def kart(kod, ad, hesap, **kw):
            kat = Kategori.objects.create(ad=f"RS {hesap}", kod=f"rs{hesap}", ust=ust)
            KategoriHesap.objects.create(kategori=kat, fatura_tipi=alis, hesap=HesapPlani.objects.get(hesap_kodu=hesap))
            return Stok.objects.create(kod=kod, ad=ad, kategori=kat, kdv=cls.kdv, uretim_birimi=birim, fatura_birimi=birim, **kw)

        cls.kayis = kart("RS-KAYIS", "KESİLMİŞ KAYIŞ", "151.10", satinalma_urunu=True, uretim_urunu=False)
        cls.ara = kart("RS-ARA", "PAKETSİZ MERDİVEN", "151.99", uretim_urunu=True)
        cls.mamul = kart("RS-MAMUL", "MERDİVEN 5+1", "152.10", uretim_urunu=True, satis_urunu=True)
        cls.depo = Depo.objects.create(kod="RSD", ad="RS DEPO")
        ist = IsIstasyonu.objects.create(kod="RSM", ad="MONTAJ RS", sira=10)
        cls.op_montaj = operasyon_olustur(istasyon_id=ist.pk, cikti_id=cls.ara.pk, cikti_miktar=D("1"), satirlar=[(cls.kayis, D("1"))])
        cls.op_paket = operasyon_olustur(istasyon_id=ist.pk, cikti_id=cls.mamul.pk, cikti_miktar=D("1"), satirlar=[(cls.ara, D("1"))])

    def setUp(self):
        hareket_ekle(stok_id=self.kayis.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 2), tur="GIRIS", miktar=D("100"), giris_tutar_try=D("300"))
        emir1 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.ara.pk, "hedef_miktar": "10"}], depo_id=self.depo.pk, tarih=date(2026, 1, 10))
        uretim_emri_toplu_uret(emir1, tarih=date(2026, 1, 10))                                        # montaj: 10 ara, kayış 10 tüketildi (×1)
        self.kayit = emir1.operasyon_kayitlari.get(silindi=False)
        emir2 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "8"}], depo_id=self.depo.pk, tarih=date(2026, 1, 11))
        uretim_emri_toplu_uret(emir2, tarih=date(2026, 1, 11))                                        # paketleme: 8 ara → 8 mamul; 2 ara kaldı
        hareket_ekle(stok_id=self.mamul.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 12), tur="CIKIS", miktar=D("3"), kaynak="FATURA")      # satıldı
        hareket_ekle(stok_id=self.mamul.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 13), tur="CIKIS", miktar=D("1"), kaynak="NUMUNE")      # numune
        operasyon_guncelle(self.op_montaj, istasyon_id=self.op_montaj.istasyon_id, cikti_miktar=D("1"), satirlar=[(self.kayis, D("2"))])  # reçete düzeltildi

    def kos(self, *args):
        out = StringIO()
        call_command("recete_sarf_duzelt", "--girdi", "RS-KAYIS", "--tarih", "01.02.2026", "--aciklama", "Kayış reçete düzeltmesi – eksik sarf", *args, stdout=out)
        return out.getvalue()

    def fis_satirlari(self, fis):
        return sorted((s.hesap.hesap_kodu, s.borc, s.alacak) for s in fis.satirlar.filter(silindi=False))


class TespitVeDagitimTest(ReceteSarfTaban):
    def test_tespit_eksik_sarf_ve_kayit_tarihindeki_birim(self):
        satirlar, atlanan, tanimlar = recete_sarf.tespit(self.kayis)
        self.assertEqual({o.pk: m for o, m in tanimlar.items()}, {self.op_montaj.pk: D("2")})
        self.assertEqual(len(satirlar), 1)
        s = satirlar[0]
        self.assertEqual((s.kayit.pk, s.cikti.pk, s.uretilen, s.beklenen, s.gercek, s.eksik), (self.kayit.pk, self.ara.pk, D("10"), D("20"), D("10"), D("10")))
        self.assertEqual((s.birim, s.birim_kaynagi, s.tutar), (D("3.000000"), "kayıt tarihi", D("30.00")))
        self.assertEqual(atlanan, [])

    def test_dagitim_oran_bazinda_zincirleme(self):
        paylar = recete_sarf.dagit(self.ara, None, D("30.00"))
        ozet = {(p.stok.kod, p.kova, p.seviye): (p.miktar, p.toplam_akis, p.tutar) for p in paylar}
        self.assertEqual(ozet, {("RS-ARA", "STOK", 0): (D("2"), D("10"), D("6.00")), ("RS-ARA", "URETIM", 0): (D("8"), D("10"), D("24.00")),
                                ("RS-MAMUL", "STOK", 1): (D("4"), D("8"), D("12.00")), ("RS-MAMUL", "SATIS", 1): (D("3"), D("8"), D("9.00")),
                                ("RS-MAMUL", "NUMUNE", 1): (D("1"), D("8"), D("3.00"))})
        self.assertEqual(sum((p.tutar for p in paylar if p.kova != "URETIM"), D("0")), D("30.00"))      # üretim payı alt satırlara geçti, toplam birebir

    def test_satis_iadesi_satilandan_duser_yuvarlama_kalintisi_en_buyuge(self):
        hareket_ekle(stok_id=self.mamul.pk, depo_id=self.depo.pk, tarih=date(2026, 1, 14), tur="GIRIS", miktar=D("1"), kaynak="FATURA", giris_ortalama=True)
        akis = recete_sarf.kart_akisi(self.mamul)
        self.assertEqual((akis["STOK"], akis["SATIS"], akis["NUMUNE"]), (D("5"), D("2"), D("1")))
        paylar = {p.kova: p.tutar for p in recete_sarf.dagit(self.mamul, None, D("1.00"))}
        self.assertEqual(paylar, {"STOK": D("0.62"), "SATIS": D("0.25"), "NUMUNE": D("0.13")})      # 5/8, 2/8, 1/8 (0,625 / 0,25 / 0,125): kalıntı en büyük kovaya, toplam 1,00


class KomutTest(ReceteSarfTaban):
    def test_kuru_calistirma_rapor_verir_hicbir_sey_birakmaz(self):
        emir3 = uretim_emri_olustur(kalemler=[{"hedef_urun_id": self.mamul.pk, "hedef_miktar": "1"}], depo_id=self.depo.pk, tarih=date(2026, 1, 14))
        uretim_emri_toplu_uret(emir3, tarih=date(2026, 1, 14))                       # ikinci paketleme kaydı: aynı mamule iki yoldan pay → birleşik satır
        fis_once = YevmiyeFisi.objects.filter(silindi=False).count()
        metin = self.kos()
        self.assertEqual(len([l for l in metin.splitlines() if "RS-MAMUL" in l and "stokta kalan" in l]), 2)      # dağıtım + özet: tek satır
        self.assertTrue(any("RS-MAMUL" in l and "stokta kalan" in l and "5 / 9" in l for l in metin.splitlines()), metin)   # ara 1/10 stokta, 9 paketlendi
        self.assertIn("KURU ÇALIŞTIRMA", metin)
        self.assertIn("EKSİK SARFLI ONAYLI KAYITLAR (1)", metin)
        self.assertIn("TOPLAM eksik sarf: 10 adet = 30.00 TL", metin)
        self.assertIn("fiziki sayımla karşılaştırılacak eldeki: 80 adet", metin)
        self.assertIn("151.99", metin)
        self.assertIn("= değişmedi", metin)
        self.assertEqual(YevmiyeFisi.objects.filter(silindi=False).count(), fis_once)
        self.assertFalse(StokHareket.objects.filter(kaynak="DUZELTME").exists())
        self.assertEqual(eldeki_miktar(self.kayis), D("90"))

    def test_uygula_cikis_fis_dagitim_ve_idempotent(self):
        metin = self.kos("--uygula")
        self.assertIn("UYGULANDI", metin)
        fis = YevmiyeFisi.objects.get(kaynak="STOK_DUZELTME", silindi=False)
        self.assertEqual((fis.tarih, fis.aciklama), (GUN, "KAYIŞ REÇETE DÜZELTMESİ – EKSİK SARF"))
        self.assertEqual(self.fis_satirlari(fis), sorted([("151.10", D("0.00"), D("30.00")), ("151.99", D("6.00"), D("0.00")), ("152.10", D("12.00"), D("0.00")),
                                                          ("620", D("9.00"), D("0.00")), ("760.06", D("3.00"), D("0.00"))]))
        cikis = StokHareket.objects.get(stok=self.kayis, kaynak="DUZELTME")
        self.assertEqual((cikis.tur, cikis.tarih, cikis.miktar, cikis.tutar_try, cikis.maliyet_durumu, cikis.fis_id, cikis.operasyon_kaydi_id),
                         ("CIKIS", GUN, D("10"), D("30.00"), "KESIN", fis.pk, self.kayit.pk))
        self.assertEqual(cikis.aciklama, f"KAYIŞ REÇETE DÜZELTMESİ – EKSİK SARF ({self.kayit.no})")
        ara_g = StokHareket.objects.get(stok=self.ara, kaynak="DUZELTME")
        mamul_g = StokHareket.objects.get(stok=self.mamul, kaynak="DUZELTME")
        self.assertEqual((ara_g.tur, ara_g.miktar, ara_g.giris_tutar_try, ara_g.tutar_try, ara_g.fis_id), ("GIRIS", D("0"), D("6.00"), D("6.00"), fis.pk))
        self.assertEqual((mamul_g.tur, mamul_g.miktar, mamul_g.giris_tutar_try, mamul_g.fis_id), ("GIRIS", D("0"), D("12.00"), fis.pk))
        kayis, ara, mamul = (Stok.objects.get(pk=s.pk) for s in (self.kayis, self.ara, self.mamul))
        self.assertEqual((kayis.maliyet_miktar, kayis.maliyet_deger_try, kayis.ort_maliyet_try), (D("80"), D("240.00"), D("3.000000")))
        self.assertEqual((ara.maliyet_miktar, ara.maliyet_deger_try, ara.ort_maliyet_try), (D("2"), D("12.00"), D("6.000000")))          # 6 + 6
        self.assertEqual((mamul.maliyet_miktar, mamul.maliyet_deger_try, mamul.ort_maliyet_try), (D("4"), D("24.00"), D("6.000000")))    # 12 + 12
        self.assertEqual(eldeki_miktar(self.ara), D("2"))                                                                                # 0 adetlik giriş eldekiyi değiştirmez
        # üretim maliyet senkronu düzeltme çıkışını DIŞLAR: kaydın aktarım fişi 30 TL kalır
        self.assertEqual(sum(s.borc for s in self.kayit.fis.satirlar.filter(silindi=False)), D("30.00"))
        self.kayit.refresh_from_db()
        from core.services import stok_fis
        self.assertEqual(stok_fis.uretim_senkronla(self.kayit), [])
        self.assertEqual(StokHareket.objects.get(operasyon_kaydi=self.kayit, tur="GIRIS", kaynak="URETIM").giris_tutar_try, D("30.00"))
        # ikinci koşu: tüketim artık tam → düzeltme yok
        metin2 = self.kos("--uygula")
        self.assertIn("Eksik sarflı kayıt yok", metin2)
        self.assertIn("tüketim tam (20 ≥ beklenen 20)", metin2)
        self.assertEqual(YevmiyeFisi.objects.filter(kaynak="STOK_DUZELTME", silindi=False).count(), 1)

    def test_duzeltme_hareketi_ve_fis_korumali_kayit_geri_alinamaz(self):
        self.kos("--uygula")
        fis = YevmiyeFisi.objects.get(kaynak="STOK_DUZELTME", silindi=False)
        with self.assertRaisesMessage(HareketHatasi, "tek başına silinemez"):
            hareket_sil(StokHareket.objects.get(stok=self.ara, kaynak="DUZELTME"))
        self.client.force_login(self.yon)
        r = self.client.post(reverse("core:stok_hareket_sil", args=[StokHareket.objects.get(stok=self.kayis, kaynak="DUZELTME").pk]), follow=True)
        self.assertTrue(any("tek başına silinemez" in str(m) for m in r.context["messages"]))
        r = self.client.post(reverse("core:fis_sil", args=[fis.pk]), follow=True)
        self.assertTrue(any("elle silinemez" in str(m) for m in r.context["messages"]))
        fis.refresh_from_db()
        self.assertFalse(fis.silindi)
        with self.assertRaisesMessage(UretimHatasi, "düzeltmesi yapılmış"):
            operasyon_kaydi_sil(self.kayit, onayli_geri_al=True)
        r = self.client.get(reverse("core:stok_detay", args=[self.kayis.pk]))
        self.assertContains(r, f"Fiş {fis.yil}/{fis.fis_no}")                                                       # hareket satırında fiş bağlantısı

    def test_tanimda_artis_yoksa_atlanir_hatali_argumanlar(self):
        operasyon_guncelle(self.op_montaj, istasyon_id=self.op_montaj.istasyon_id, cikti_miktar=D("1"), satirlar=[(self.kayis, D("1"))])
        self.assertIn("Eksik sarflı kayıt yok", self.kos())
        with self.assertRaisesMessage(CommandError, "Stok bulunamadı: YOK"):
            self.kos("--girdi", "YOK")
        with self.assertRaisesMessage(CommandError, "gg.aa.yyyy"):
            call_command("recete_sarf_duzelt", "--girdi", "RS-KAYIS", "--tarih", "2026-02-01", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "USD kuru yok"):
            call_command("recete_sarf_duzelt", "--girdi", "RS-KAYIS", "--tarih", "03.02.2026", stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "operasyon tanımı yok"):
            self.kos("--operasyon", str(self.op_paket.pk))
