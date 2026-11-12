"""``stok_maliyet_duzelt``: fiyatsız (0) alış irsaliyesi girişi fiyatlanırken irsaliye KALEMİNİN birim fiyatı da yazılır (satır fiyatı → giriş maliyeti → kartı tüketen
kayıtların maliyeti), KDV aynı kalır, carinin bakiyesi DEĞİŞMEZ (irsaliye faturalanmadı; muhasebe kaydı yok — değişirse işlem geri alınır), dry-run hiçbir şey
bırakmaz; kalem fiyatı zaten doluysa kalem atlanır."""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import StokHareket, YevmiyeSatir
from core.services import stok_ortalama as so
from core.services.fatura import fatura_onayla
from core.services.hareket import hareket_ekle
from core.services.teklif_siparis import (irsaliyeyi_faturaya_cevir, siparisi_irsaliyeye_cevir, teklif_siparis_olustur, teklif_siparis_onayla,
                                           teklifi_siparise_cevir)
from core.tests.test_stok_ortalama import D, OrtalamaTemel

DEC = Decimal


class IrsaliyeBase(OrtalamaTemel):
    def irsaliye(self, fiyat="100", miktar="10", iskonto=None, faturaya_cevir=False):
        satir = {"stok_id": self.stok.pk, "miktar": miktar, "birim_fiyat": fiyat}
        if iskonto:
            satir["iskonto_yuzdesi"] = iskonto
        teklif = teklif_siparis_olustur(belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk, tarih=D(2026, 6, 28), satirlar=[satir], kullanici=self.u)
        teklif_siparis_onayla(teklif, kullanici=self.u)
        siparis = teklifi_siparise_cevir(teklif, tarih=teklif.tarih, kullanici=self.u)
        teklif_siparis_onayla(siparis, kullanici=self.u)
        irsaliye = siparisi_irsaliyeye_cevir(siparis, tarih=siparis.tarih, depo_id=self.depo.pk, kullanici=self.u)
        teklif_siparis_onayla(irsaliye, kullanici=self.u)
        if faturaya_cevir:
            irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.u)
            irsaliye.refresh_from_db()
        return irsaliye

    def fatura_onayla(self, fatura):
        fatura.tip = self.tip
        fatura.save(update_fields=["tip"])
        fatura_onayla(fatura, kullanici=self.u)
        fatura.refresh_from_db()

    def cari_bakiye(self):
        satirlar = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, hesap__hesap_kodu=self.cari.muhasebe_kodu)
        return sum((s.alacak - s.borc for s in satirlar), DEC("0"))


class KalemFiyatiKomutTest(IrsaliyeBase):
    def komut(self, *ek):
        out = StringIO()
        call_command("stok_maliyet_duzelt", "--fiyat", f"{self.stok.kod}=1", *ek, stdout=out)
        return out.getvalue()

    def test_uygula_kalem_fiyati_giris_maliyeti_ve_cari_bakiye_sabit(self):
        irsaliye = self.irsaliye(fiyat="0", miktar="10")
        kalem = irsaliye.kalemler.get(silindi=False)
        h, = self.hareketler()
        self.assertEqual((kalem.birim_fiyat, h.giris_tutar_try, h.maliyet_durumu), (DEC("0"), None, so.YOK))
        bakiye = self.cari_bakiye()
        metin = self.komut("--uygula")
        kalem.refresh_from_db()
        h, = self.hareketler()
        self.assertEqual(kalem.birim_fiyat, DEC("1"))
        self.assertEqual((kalem.tutar, kalem.kdv_id), (DEC("10.00"), self.kdv.pk))                           # KDV aynı
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu, h.fis_id), (DEC("10.00"), so.KESIN, None))    # fişsiz
        self.assertEqual(self.cari_bakiye(), bakiye)                                                         # cari bakiye değişmez
        self.assertIn("IRSALIYE KALEM FIYATLARI (1;", metin)
        self.assertIn(irsaliye.belge_no, metin)
        self.assertIn("CARI BAKIYESI", metin)
        self.assertIn("= esit", metin)
        self.assertNotIn("<-- DEGISTI", metin)
        self.kontrol_yeniden_hesap_esit()

    def test_dry_run_kalem_ve_hareketi_degistirmez_ama_raporlar(self):
        irsaliye = self.irsaliye(fiyat="0")
        metin = self.komut()
        self.assertIn("DRY-RUN", metin)
        self.assertIn("birim fiyat 0 -> 1", metin)
        self.assertEqual(irsaliye.kalemler.get(silindi=False).birim_fiyat, DEC("0"))
        h, = self.hareketler()
        self.assertIsNone(h.giris_tutar_try)

    def test_karti_tuketen_cikis_yeni_maliyetle_degerlenir(self):
        self.irsaliye(fiyat="0", miktar="10")
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 28), tur=StokHareket.Tur.CIKIS, miktar=DEC("4"))
        cikis = StokHareket.objects.get(stok=self.stok, silindi=False, tur="CIKIS")
        self.assertEqual(cikis.maliyet_durumu, so.YOK)
        self.komut("--uygula")
        cikis.refresh_from_db()
        self.assertEqual((cikis.tutar_try, cikis.maliyet_durumu), (DEC("4.00"), so.KESIN))                    # 4 × 1,00

    def test_kalem_fiyati_dolu_ise_dokunulmaz(self):
        irsaliye = self.irsaliye(fiyat="5")
        metin = self.komut("--uygula")
        self.assertEqual(irsaliye.kalemler.get(silindi=False).birim_fiyat, DEC("5"))
        self.assertIn("[atlandi]", metin)
        self.assertIn("kalem fiyatı zaten 5", metin)
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, DEC("10.00"))                                                    # giriş yine de fiyatlandı (komutun mevcut davranışı)

    def test_cari_bakiyesi_degisirse_islem_geri_alinir(self):
        self.irsaliye(fiyat="0")
        sayac = iter([DEC("0"), DEC("7")])
        with mock.patch("core.management.commands.stok_maliyet_duzelt._cari_bakiye", side_effect=lambda c: next(sayac)):
            with self.assertRaisesMessage(CommandError, "cari bakiyesi değişiyor"):
                self.komut("--uygula")
        h, = self.hareketler()
        self.assertIsNone(h.giris_tutar_try)                                                                  # atomik geri alındı
