"""Faturaya BAĞLI onaylı alış irsaliyesinde miktar düzeltmesi (2026-10-22, Karakuş SAI-2026-0016): kalem pk'si ve fatura satırı bağı korunur (ekran düzenlemesi kapalıdır),
fatura satırları/fişi DEĞİŞMEZ, giriş hareketi miktarı güncellenir, faturalıysa maliyet = bağlı satır tutarı + faturasız fark × kalem fiyatı, kart yeniden hesaplanır, negatife
düşüren düzeltme hiçbir şey yazmaz; genel fiyatlama (irsaliye_girislerini_fiyatla) eskisi gibi kalır (fark seyrelir)."""
import datetime
from decimal import Decimal

from core.models import FaturaSatir, StokHareket, TeklifSiparis
from core.services import stok_ortalama as so
from core.services.fatura import fatura_onayla, irsaliye_miktar_farklari
from core.services.hareket import eldeki_miktar, hareket_ekle
from core.services.teklif_siparis import TeklifSiparisHatasi, irsaliye_miktar_duzelt, teklif_siparis_guncelle
from core.tests.test_coklu_irsaliye_fatura import CokluTaban

D = datetime.date
Dc = Decimal


class MiktarDuzeltTest(CokluTaban):
    def faturali(self, irsaliye_miktar="500", fatura_miktar="500", fiyat="2"):
        """Karakuş benzeri: ÇOKLU irsaliyeden açılan fatura (satırlar kalemlerine BAĞLI); düzeltilen irsaliye ilk, ikincisi başka stoktan."""
        i = self.irsaliye([{"stok_id": self.stok.pk, "miktar": irsaliye_miktar, "birim_fiyat": fiyat}], tarih=D(2026, 6, 5))
        j = self.irsaliye([{"stok_id": self.stok_c.pk, "miktar": "100", "birim_fiyat": "3"}], tarih=D(2026, 6, 5))
        fatura = self.cevir(i, j)
        self.duzenle(fatura, self.girdiler(fatura, {0: {"miktar": Dc(fatura_miktar)}}))
        fatura.refresh_from_db()
        fatura_onayla(fatura, kullanici=self.u)
        fatura.refresh_from_db()
        i.refresh_from_db()
        return i, fatura

    def hareket(self, kalem):
        return StokHareket.objects.get(teklif_siparis_kalem=kalem, silindi=False)

    def test_faturali_irsaliyede_yerinde_duzeltme_bag_ve_fatura_korunur_fark_kalem_fiyatiyla(self):
        i, fatura = self.faturali()
        kalem = i.kalemler.get()
        satir_once = list(fatura.satirlar.filter(silindi=False).values_list("pk", "miktar", "birim_fiyat", "irsaliye_kalem_id"))
        fis_once = (fatura.fis_id, sorted((s.hesap_id, s.borc, s.alacak) for s in fatura.fis.satirlar.filter(silindi=False)))
        with self.assertRaises(TeklifSiparisHatasi):                                             # ekrandan düzenleme kapalı (faturaya dönüştürülmüş)
            teklif_siparis_guncelle(i, cari_id=i.cari_id, tarih=i.tarih, para_birimi="TRY", depo_id=self.depo.pk, satirlar=[{"stok_id": self.stok.pk, "miktar": "550", "birim_fiyat": "2"}])
        sonuc = irsaliye_miktar_duzelt(i, [(kalem.pk, "550")], kullanici=self.u, aciklama="teslimat belgesine göre 500→550")
        self.assertEqual([(r["eski"], r["yeni"]) for r in sonuc], [(Dc("500"), Dc("550"))])
        kalem.refresh_from_db()
        h = self.hareket(kalem)
        self.assertEqual((kalem.pk, kalem.miktar, h.miktar), (kalem.pk, Dc("550"), Dc("550")))
        self.assertEqual(h.giris_tutar_try, Dc("1100.00"))                                        # 500×2 (fatura satırı) + 50×2 (faturasız fark, kalem fiyatı)
        self.assertEqual(h.maliyet_fatura_satir_id, fatura.satirlar.filter(silindi=False, irsaliye_kalem=kalem).get().pk)
        self.assertEqual(eldeki_miktar(self.stok), Dc("550"))
        fatura.refresh_from_db()
        self.assertEqual(list(fatura.satirlar.filter(silindi=False).values_list("pk", "miktar", "birim_fiyat", "irsaliye_kalem_id")), satir_once)       # fatura satırı DEĞİŞMEDİ, bağ korunur
        self.assertEqual((fatura.fis_id, sorted((s.hesap_id, s.borc, s.alacak) for s in fatura.fis.satirlar.filter(silindi=False))), fis_once)       # muhasebe fişi aynı
        self.assertEqual([(s.pk, ir, fa) for s, ir, fa in irsaliye_miktar_farklari(fatura)], [(self.stok.pk, Dc("550"), Dc("500"))])   # "fatura miktarı irsaliyeden farklı" uyarısı (normal)
        i.refresh_from_db()
        self.assertIn("teslimat belgesine göre", i.aciklama)
        self.assertEqual(i.fatura_id, fatura.pk)

    def test_genel_fiyatlama_eskisi_gibi_fark_seyrelir(self):
        i, fatura = self.faturali()
        irsaliye_miktar_duzelt(i, [(i.kalemler.get().pk, "550")], kullanici=self.u)
        so.irsaliye_girislerini_fiyatla(fatura)                                                   # fatura sonradan yeniden fiyatlanırsa genel kural (8bbfc11): maliyet = fatura tutarı
        self.assertEqual(self.hareket(i.kalemler.get()).giris_tutar_try, Dc("1000.00"))
        self.assertEqual(self.hareket(i.kalemler.get()).miktar, Dc("550"))

    def test_azaltma_ve_negatif_stok_korumasi_hicbir_sey_yazilmaz(self):
        i, fatura = self.faturali()
        kalem = i.kalemler.get()
        hareket_ekle(stok_id=self.stok.pk, depo_id=self.depo.pk, tarih=D(2026, 6, 10), tur=StokHareket.Tur.CIKIS, miktar=Dc("450"), kullanici=self.u)         # 450 tüketildi
        irsaliye_miktar_duzelt(i, [(kalem.pk, "460")], kullanici=self.u)                           # 500→460: kalan 10 ≥ 0, olur
        self.assertEqual(eldeki_miktar(self.stok), Dc("10"))
        with self.assertRaisesMessage(TeklifSiparisHatasi, "negatife düşürüyor"):
            irsaliye_miktar_duzelt(i, [(kalem.pk, "400")], kullanici=self.u)
        kalem.refresh_from_db()
        self.assertEqual((kalem.miktar, self.hareket(kalem).miktar, eldeki_miktar(self.stok)), (Dc("460"), Dc("460"), Dc("10")))             # geri sarıldı

    def test_faturasiz_onayli_irsaliyede_de_calisir_fiyatsiz_kalir(self):
        i = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "2"}])
        irsaliye_miktar_duzelt(i, [(i.kalemler.get().pk, "120")], kullanici=self.u)
        h = self.hareket(i.kalemler.get())
        self.assertEqual((h.miktar, h.giris_tutar_try), (Dc("120"), None))
        self.assertEqual(eldeki_miktar(self.stok), Dc("120"))

    def test_hata_durumlari(self):
        i = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "2"}])
        k = i.kalemler.get().pk
        with self.assertRaisesMessage(TeklifSiparisHatasi, "Düzeltilecek kalem verilmedi"):
            irsaliye_miktar_duzelt(i, [])
        with self.assertRaisesMessage(TeklifSiparisHatasi, "birden çok kez"):
            irsaliye_miktar_duzelt(i, [(k, "5"), (k, "6")])
        with self.assertRaisesMessage(TeklifSiparisHatasi, "bu irsaliyede yok"):
            irsaliye_miktar_duzelt(i, [(999999, "5")])
        with self.assertRaises(TeklifSiparisHatasi):
            irsaliye_miktar_duzelt(i, [(k, "0")])
        taslak = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "10", "birim_fiyat": "2"}], onayli=False)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "onaylı irsaliye içindir"):
            irsaliye_miktar_duzelt(taslak, [(taslak.kalemler.get().pk, "5")])
        self.assertEqual(self.hareket(i.kalemler.get()).miktar, Dc("100"))

    def test_uretim_miktari_dolu_kalem_orantili_olceklenir(self):
        i = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "2", "uretim_miktar": "100"}])
        irsaliye_miktar_duzelt(i, [(i.kalemler.get().pk, "130")], kullanici=self.u)
        k = i.kalemler.get()
        self.assertEqual((k.miktar, k.uretim_miktar, self.hareket(k).miktar), (Dc("130"), Dc("130"), Dc("130")))


    def test_tek_irsaliyeden_acilan_bagsiz_faturada_genel_kural(self):
        i = self.irsaliye([{"stok_id": self.stok.pk, "miktar": "500", "birim_fiyat": "2"}], tarih=D(2026, 6, 5))
        fatura = self.cevir(i)                                                                     # tek irsaliye → bağsız satır (eski akış)
        self.duzenle(fatura, self.girdiler(fatura))
        fatura.refresh_from_db()
        fatura_onayla(fatura, kullanici=self.u)
        irsaliye_miktar_duzelt(i, [(i.kalemler.get().pk, "550")], kullanici=self.u)
        h = self.hareket(i.kalemler.get())
        self.assertEqual((h.miktar, h.giris_tutar_try), (Dc("550"), Dc("1000.00")))                  # bağsızda genel kural: maliyet fatura tutarı
