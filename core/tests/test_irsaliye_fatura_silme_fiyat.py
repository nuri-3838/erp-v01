"""KORUMA: irsaliyeden oluşan fatura SİLİNİNCE irsaliye girişleri fiyatsıza değil irsaliye kaleminin birim fiyatına (iskontolu net tutar, KDV hariç) döner — fatura gelene
kadar irsaliye fiyatı geçerlidir; kalem fiyatı 0 ise eskisi gibi fiyatsız; yeni fatura gelince fatura tutarı kalem tahminini yerinde ezer; kartı tüketen çıkışlar
silinme sonrası da kalem fiyatıyla değerlenir."""
from core.models import StokHareket
from core.services import stok_ortalama as so
from core.services.fatura import fatura_sil
from core.services.hareket import hareket_ekle
from core.services.teklif_siparis import irsaliyeyi_faturaya_cevir
from core.tests.test_irsaliye_kalem_fiyati import DEC, IrsaliyeBase
from core.tests.test_stok_ortalama import D


class SilinceKalemFiyatinaDonmeTest(IrsaliyeBase):
    def test_fatura_silinince_giris_kalem_fiyatina_doner(self):
        irsaliye = self.irsaliye(fiyat="100", miktar="10", faturaya_cevir=True)
        self.fatura_onayla(irsaliye.fatura)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.maliyet_fatura_satir_id is not None), (DEC("1000.00"), True))     # fatura fiyatladı
        fatura_sil(irsaliye.fatura, kullanici=self.u)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu, h.maliyet_fatura_satir_id, h.fis_id), (DEC("1000.00"), so.KESIN, None, None))
        self.kontrol_yeniden_hesap_esit()

    def test_iskontolu_kalem_net_tutara_doner(self):
        irsaliye = self.irsaliye(fiyat="100", miktar="10", iskonto="10", faturaya_cevir=True)
        self.fatura_onayla(irsaliye.fatura)
        fatura_sil(irsaliye.fatura, kullanici=self.u)
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, DEC("900.00"))

    def test_kalem_fiyati_sifirsa_eskisi_gibi_fiyatsiz(self):
        irsaliye = self.irsaliye(fiyat="0", miktar="10", faturaya_cevir=True)
        fatura = irsaliye.fatura
        sat = fatura.satirlar.get(silindi=False)
        sat.birim_fiyat = DEC("50")                                                                         # fatura gerçek fiyatı getirdi, irsaliye kalemi 0 kalmış
        sat.save()
        self.fatura_onayla(fatura)
        h, = self.hareketler()
        self.assertEqual(h.giris_tutar_try, DEC("500.00"))
        fatura_sil(fatura, kullanici=self.u)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu), (None, so.YOK))                              # kalem fiyatı 0: fiyatsız

    def test_yeniden_fatura_gelince_fatura_tutari_ezer(self):
        irsaliye = self.irsaliye(fiyat="100", miktar="10", faturaya_cevir=True)
        self.fatura_onayla(irsaliye.fatura)
        fatura_sil(irsaliye.fatura, kullanici=self.u)                                                        # → 1000 (kalem fiyatı)
        irsaliye.refresh_from_db()
        irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.u)
        irsaliye.refresh_from_db()
        fatura = irsaliye.fatura
        sat = fatura.satirlar.get(silindi=False)
        sat.birim_fiyat = DEC("120")
        sat.save()
        self.fatura_onayla(fatura)
        h, = self.hareketler()
        self.assertEqual((h.giris_tutar_try, h.maliyet_durumu), (DEC("1200.00"), so.KESIN))                  # fatura, kalem tahminini yerinde ezer
        self.assertEqual(StokHareket.objects.filter(stok=self.stok, silindi=False).count(), 1)

    def test_karti_tuketen_cikis_silinme_sonrasi_kalem_fiyatiyla_degerlenir(self):
        irsaliye = self.irsaliye(fiyat="100", miktar="10", faturaya_cevir=True)
        self.fatura_onayla(irsaliye.fatura)
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 28), tur=StokHareket.Tur.CIKIS, miktar=DEC("3"))
        fatura_sil(irsaliye.fatura, kullanici=self.u)
        cikis = StokHareket.objects.get(stok=self.stok, silindi=False, tur="CIKIS")
        self.assertEqual((cikis.tutar_try, cikis.maliyet_durumu), (DEC("300.00"), so.KESIN))                  # 3 × 100: fiyatsıza düşseydi 0 / YOK olurdu
        self.kontrol_yeniden_hesap_esit()
