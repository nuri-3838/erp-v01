"""Satınalma siparişinde KISMİ TESLİM (2026-10-20): bir alış siparişi birden çok irsaliyeye çevrilebilir; her ONAYLI irsaliye sipariş kaleminin kalanından düşer (sipariş / teslim / kalan);
kalan 0 → "Tamamlandı", kalan varken "Kısmi teslim"; irsaliye silinince / onayı geri alınınca kalan geri artar (hesap türetilmiştir); taslak irsaliye varken yenisi açılamaz;
siparişte olmayan kalem teslim hesabına girmez; gelecek stok KALAN miktardır."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Stok, TeklifSiparis
from core.services import satinalma, siparis_teslim
from core.services.teklif_siparis import (TeklifSiparisHatasi, siparisi_irsaliyeye_cevir, teklif_siparis_guncelle, teklif_siparis_iptal, teklif_siparis_olustur,
                                           teklif_siparis_onayi_geri_al, teklif_siparis_onayla, teklifi_siparise_cevir)
from core.tests.test_stok_ortalama import OrtalamaTemel

D = datetime.date
Dc = Decimal


class KismiTaban(OrtalamaTemel):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stok_b = Stok.objects.create(kod="SO2", ad="ÜRÜN B", kategori=cls.kat, kdv=cls.kdv, uretim_birimi=cls.adet, fatura_birimi=cls.adet)
        cls.stok_c = Stok.objects.create(kod="SO3", ad="ÜRÜN C", kategori=cls.kat, kdv=cls.kdv, uretim_birimi=cls.adet, fatura_birimi=cls.adet)
        cls.stok_y = Stok.objects.create(kod="SO4", ad="YAN ETİKET", kategori=cls.kat, kdv=cls.kdv, uretim_birimi=cls.adet, fatura_birimi=cls.adet)

    def siparis(self, satirlar=None):
        satirlar = satirlar or [{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"},
                                {"stok_id": self.stok_c.pk, "miktar": "20", "birim_fiyat": "4"}]
        t = teklif_siparis_olustur(belge_tur="TEKLIF", yon="ALIS", cari_id=self.cari.pk, tarih=D(2026, 6, 5), satirlar=satirlar, kullanici=self.u)
        teklif_siparis_onayla(t, kullanici=self.u)
        s = teklifi_siparise_cevir(t, tarih=t.tarih, kullanici=self.u)
        teklif_siparis_onayla(s, kullanici=self.u)
        s.refresh_from_db()
        return s

    def irsaliye_taslagi(self, siparis, tarih=D(2026, 6, 10)):
        return siparisi_irsaliyeye_cevir(siparis, tarih=tarih, depo_id=self.depo.pk, kullanici=self.u)

    def duzenle(self, irsaliye, satirlar):
        teklif_siparis_guncelle(irsaliye, cari_id=irsaliye.cari_id, tarih=irsaliye.tarih, satirlar=satirlar, para_birimi="TRY", depo_id=self.depo.pk, kullanici=self.u)
        irsaliye.refresh_from_db()
        return irsaliye

    def onayli_irsaliye(self, siparis, satirlar, tarih=D(2026, 6, 10)):
        i = self.irsaliye_taslagi(siparis, tarih=tarih)
        self.duzenle(i, satirlar)
        teklif_siparis_onayla(i, kullanici=self.u)
        i.refresh_from_db()
        return i

    def satir(self, d, stok):
        return next(s for s in d["kalemler"] if s["kalem"].stok_id == stok.pk)

    def kalan(self, siparis):
        d = siparis_teslim.siparis_durumu(siparis)
        return {s["kalem"].stok.kod: (s["teslim"], s["kalan"], s["fazla"]) for s in d["kalemler"]}, d


class HesapTest(KismiTaban):
    def test_ilk_irsaliye_siparis_teslim_kalan_ve_siparissiz_kalem(self):
        s = self.siparis()
        i1 = self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"},
                                      {"stok_id": self.stok_y.pk, "miktar": "7", "birim_fiyat": "1"}])          # C gelmedi, siparişte olmayan yan etiket geldi
        k, d = self.kalan(s)
        self.assertEqual(k, {"SO1": (Dc("60"), Dc("40"), Dc("0")), "SO2": (Dc("50"), Dc("0"), Dc("0")), "SO3": (Dc("0"), Dc("20"), Dc("0"))})
        self.assertEqual(d["durum"], siparis_teslim.KISMI)
        self.assertEqual([(x["stok_id"], x["miktar"]) for x in d["siparissiz"]], [(self.stok_y.pk, Dc("7"))])           # siparişsiz teslim, kalandan düşmez
        self.assertTrue(d["kalan_var"] and d["teslim_var"])
        self.assertEqual(i1.durum, "ONAYLI")

    def test_taslak_irsaliye_teslim_sayilmaz_ve_ikinci_taslak_acilmaz(self):
        s = self.siparis()
        i1 = self.irsaliye_taslagi(s)
        self.assertEqual(sorted((x.stok.kod, x.miktar) for x in i1.kalemler.filter(silindi=False)), [("SO1", Dc("100")), ("SO2", Dc("50")), ("SO3", Dc("20"))])   # ilk çevirme: hepsi
        _, d = self.kalan(s)
        self.assertEqual((d["durum"], d["teslim_var"]), (siparis_teslim.BEKLIYOR, False))                                   # onaylanana kadar teslim yok
        with self.assertRaisesMessage(TeklifSiparisHatasi, "onaylanmamış bir irsaliye taslağı var"):
            self.irsaliye_taslagi(s)

    def test_kalan_ikinci_irsaliyeye_cevrilir_ve_tamamlandi(self):
        s = self.siparis()
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"}])
        i2 = self.irsaliye_taslagi(s, tarih=D(2026, 6, 20))
        self.assertEqual(sorted((x.stok.kod, x.miktar, x.birim_fiyat) for x in i2.kalemler.filter(silindi=False)), [("SO1", Dc("40"), Dc("2")), ("SO3", Dc("20"), Dc("4"))])   # yalnız KALAN
        teklif_siparis_onayla(i2, kullanici=self.u)
        k, d = self.kalan(s)
        self.assertEqual((d["durum"], d["kalan_var"]), (siparis_teslim.TAMAM, False))
        self.assertTrue(all(kalan == 0 for _, kalan, _ in k.values()))
        with self.assertRaisesMessage(TeklifSiparisHatasi, "tüm kalemleri teslim alınmış"):
            self.irsaliye_taslagi(s, tarih=D(2026, 6, 25))
        self.assertEqual(s.donusen_irsaliyeler.filter(silindi=False).count(), 2)

    def test_silinince_ya_da_onayi_geri_alinince_kalan_geri_artar(self):
        s = self.siparis()
        i1 = self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}])
        i2 = self.irsaliye_taslagi(s, tarih=D(2026, 6, 20))
        teklif_siparis_onayla(i2, kullanici=self.u)                                              # 40 + 50 + 20 kalan teslim → TAMAM
        self.assertEqual(self.kalan(s)[1]["durum"], siparis_teslim.TAMAM)
        teklif_siparis_onayi_geri_al(i2, kullanici=self.u)                                        # onay geri alındı → kalan geri geldi
        k, d = self.kalan(s)
        self.assertEqual(k["SO1"][:2], (Dc("60"), Dc("40")))
        self.assertEqual(d["durum"], siparis_teslim.KISMI)
        teklif_siparis_onayla(i2, kullanici=self.u)
        teklif_siparis_iptal(i2, kullanici=self.u)                                                # silindi → kalan geri geldi
        k, d = self.kalan(s)
        self.assertEqual((k["SO1"][:2], k["SO2"][:2], k["SO3"][:2]), ((Dc("60"), Dc("40")), (Dc("0"), Dc("50")), (Dc("0"), Dc("20"))))
        teklif_siparis_iptal(i1, kullanici=self.u)
        self.assertEqual(self.kalan(s)[1]["durum"], siparis_teslim.BEKLIYOR)
        self.irsaliye_taslagi(s, tarih=D(2026, 6, 30))                                            # silinen irsaliyeler engel değil: tekrar çevrilebilir

    def test_siparis_ustu_teslim_fazla_olarak_gosterilir_kalan_negatif_olmaz(self):
        s = self.siparis()
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "120", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"},
                                 {"stok_id": self.stok_c.pk, "miktar": "20", "birim_fiyat": "4"}])
        k, d = self.kalan(s)
        self.assertEqual(k["SO1"], (Dc("100"), Dc("0"), Dc("20")))
        self.assertEqual(d["durum"], siparis_teslim.TAMAM)

    def test_ayni_stok_iki_kalem_teslim_sirayla_dagitilir(self):
        s = self.siparis([{"stok_id": self.stok.pk, "miktar": "30", "birim_fiyat": "2"}, {"stok_id": self.stok.pk, "miktar": "70", "birim_fiyat": "2"}])
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "50", "birim_fiyat": "2"}])
        d = siparis_teslim.siparis_durumu(s)
        self.assertEqual([(x["siparis"], x["teslim"], x["kalan"]) for x in d["kalemler"]], [(Dc("30"), Dc("30"), Dc("0")), (Dc("70"), Dc("20"), Dc("50"))])


class GelecekStokTest(KismiTaban):
    def test_gelecek_stok_kalan_miktardir_taslak_dusmez(self):
        s = self.siparis()
        h = satinalma.acik_siparis_haritasi()
        self.assertEqual((h[(self.stok.pk, None)], h[(self.stok_b.pk, None)], h[(self.stok_c.pk, None)]), (Dc("100"), Dc("50"), Dc("20")))
        self.irsaliye_taslagi(s)                                                                  # taslak: hâlâ gelecek
        self.assertEqual(satinalma.acik_siparis_haritasi()[(self.stok.pk, None)], Dc("100"))
        self.onayli_irsaliye_taslaktan(s)
        h = satinalma.acik_siparis_haritasi()
        self.assertEqual((h.get((self.stok.pk, None)), h.get((self.stok_b.pk, None)), h.get((self.stok_c.pk, None))), (Dc("40"), None, Dc("20")))   # kalan; B tamamen geldi
        satirlar = satinalma.acik_siparis_satirlari()
        self.assertEqual(sorted((x["siparis"].belge_no, x["miktar"]) for x in satirlar), sorted([(s.belge_no, Dc("40")), (s.belge_no, Dc("20"))]))

    def onayli_irsaliye_taslaktan(self, siparis):
        i = siparis.donusen_irsaliyeler.get(silindi=False)
        self.duzenle(i, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"}])
        teklif_siparis_onayla(i, kullanici=self.u)

    def test_tamamen_teslim_edilen_siparis_gelecek_degildir(self):
        s = self.siparis()
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "100", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"},
                                 {"stok_id": self.stok_c.pk, "miktar": "20", "birim_fiyat": "4"}])
        self.assertEqual(satinalma.acik_siparis_haritasi(), {})


class EkranTest(KismiTaban):
    def giris(self):
        self.client.force_login(self.u)

    def test_liste_ve_detay_rozetleri_sipariss_teslim_kalan_ve_buton(self):
        s = self.siparis()
        self.giris()
        detay = reverse("core:teklif_siparis_detay", args=[s.pk])
        r = self.client.get(detay)
        self.assertContains(r, "Kalan")
        self.assertContains(r, "İrsaliyeye Çevir")
        self.assertNotContains(r, "Kalanı İrsaliyeye Çevir")
        self.assertNotContains(r, "Kısmi teslim")
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"},
                                 {"stok_id": self.stok_y.pk, "miktar": "7", "birim_fiyat": "1"}])
        r = self.client.get(detay)
        self.assertContains(r, "Kısmi teslim")
        self.assertContains(r, "Kalanı İrsaliyeye Çevir")
        self.assertContains(r, "Siparişte olmayan teslimler")
        self.assertContains(r, "SO4")
        self.assertEqual(r.context["kalem_sutun"], 5)
        k = {x.stok.kod: x.teslim_satir for x in r.context["kalemler"]}
        self.assertEqual((k["SO1"]["teslim"], k["SO1"]["kalan"], k["SO3"]["kalan"]), (Dc("60"), Dc("40"), Dc("20")))
        self.assertContains(self.client.get(reverse("core:satinalma_siparisleri")), "Kısmi teslim")

    def test_cevirme_ekrani_kalani_gosterir_ikinci_irsaliyeyi_acar_taslakken_buton_gizlenir(self):
        s = self.siparis()
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"}])
        self.giris()
        url = reverse("core:siparis_irsaliyeye_cevir", args=[s.pk])
        r = self.client.get(url)
        self.assertContains(r, "kopyalanacak kalan miktarlar")
        self.assertEqual(sorted((x["kalem"].stok.kod, x["kalan"]) for x in r.context["kalan_satirlari"]), [("SO1", Dc("40")), ("SO3", Dc("20"))])
        r = self.client.post(url, {"depo": self.depo.pk, "tarih": "2026-06-20"}, follow=True)
        i2 = s.donusen_irsaliyeler.get(silindi=False, durum="TASLAK")
        self.assertEqual(sorted((x.stok.kod, x.miktar) for x in i2.kalemler.filter(silindi=False)), [("SO1", Dc("40")), ("SO3", Dc("20"))])
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[s.pk]))
        self.assertContains(r, "İrsaliye taslağı var")
        self.assertNotContains(r, "Kalanı İrsaliyeye Çevir")                                      # taslak varken yeni irsaliye düğmesi yok
        r = self.client.post(url, {"depo": self.depo.pk, "tarih": "2026-06-21"}, follow=True)
        self.assertContains(r, "onaylanmamış bir irsaliye taslağı var")
        teklif_siparis_onayla(i2, kullanici=self.u)
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[s.pk]))
        self.assertContains(r, "Tamamlandı")
        self.assertNotContains(r, "İrsaliyeye Çevir")
        self.assertContains(self.client.get(reverse("core:satinalma_siparisleri")), "Tamamlandı")
