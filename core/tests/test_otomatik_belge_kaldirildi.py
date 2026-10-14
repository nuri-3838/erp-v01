"""2026-10-14 kuralı: ONAY bir sonraki ticari belgeyi otomatik AÇMAZ (ALIŞ Teklif→Sipariş→İrsaliye→Fatura zinciri). Dönüşüm yalnız düğmeyle: ALIŞ için
Siparişe Çevir / İrsaliyeye Çevir (depo) / Faturaya Çevir. Taslak hedef silinince kaynak yeniden çevrilebilir, rozet "… taslağı var" → kalkar. GELECEK STOK tek
kaynak (core.services.satinalma, VARYANTLI): kritik stok, stok listesi (eşiksiz kart dahil) ve ÜS eksik malzeme aynı haritayı kullanır; TASLAK irsaliye/fatura
kaynağı açık tutar, ONAYLI irsaliye/fatura düşürür."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import Depo, Fatura, StokHareket, StokVaryant, TeklifSiparis
from core.services import kritik_stok, satinalma
from core.services import uretim
from core.services.fatura import fatura_sil
from core.services.teklif_siparis import (
    irsaliye_sil, irsaliyeyi_faturaya_cevir, siparisi_irsaliyeye_cevir, teklif_siparis_olustur, teklif_siparis_onayla, teklifi_siparise_cevir,
)
from core.tests.test_kritik_stok import KritikTaban
from core.tests.test_stok import _cari
from core.tests.test_teklif_siparis import SatinalmaZinciriTest
from core.tests.test_varyant_ekranlar import VaryantEkranTaban

D = Decimal


class OnayBelgeAcmazTest(SatinalmaZinciriTest):
    """SatinalmaZinciriTest fixture'ı: stok, cari, depo, alış fatura tipi."""

    def test_onay_hicbir_adimda_belge_acmaz(self):
        teklif = self._teklif()
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        self.assertEqual((teklif.durum, TeklifSiparis.objects.filter(belge_tur="SIPARIS").count()), ("ONAYLI", 0))
        siparis = teklifi_siparise_cevir(teklif, tarih=self.tarih, kullanici=self.yon)
        teklif_siparis_onayla(siparis, kullanici=self.yon)
        self.assertEqual(TeklifSiparis.objects.filter(belge_tur="IRSALIYE").count(), 0)
        irsaliye = siparisi_irsaliyeye_cevir(siparis, tarih=self.tarih, depo_id=self.depo.pk, kullanici=self.yon)
        teklif_siparis_onayla(irsaliye, kullanici=self.yon)
        irsaliye.refresh_from_db()
        self.assertEqual((Fatura.objects.count(), irsaliye.fatura_id, StokHareket.objects.count()), (0, None, 1))    # yalnız stok girişi (hareket) kalır

    def test_manuel_dugmeler_ekrandan_calisir(self):
        self.client.force_login(self.yon)
        teklif = self._teklif()
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[teklif.pk]))
        self.assertContains(r, "Siparişe Çevir")
        r = self.client.post(reverse("core:alis_teklif_siparise_cevir", args=[teklif.pk]), follow=True)
        siparis = teklif.donusen_belgeler.get()
        self.assertEqual(siparis.durum, "TASLAK")
        self.assertContains(r, "Taslak alış siparişi oluşturuldu")
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[teklif.pk])), "Sipariş taslağı var")
        self.assertNotContains(self.client.get(reverse("core:teklif_siparis_detay", args=[teklif.pk])), "Siparişe Çevir")
        teklif_siparis_onayla(siparis, kullanici=self.yon)
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[teklif.pk])), "Siparişe Dönüştü")
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[siparis.pk]))
        self.assertContains(r, "İrsaliyeye Çevir")
        r = self.client.get(reverse("core:siparis_irsaliyeye_cevir", args=[siparis.pk]))
        self.assertContains(r, "Taslak irsaliye oluştur")
        self.assertContains(r, self.depo.kod)
        r = self.client.post(reverse("core:siparis_irsaliyeye_cevir", args=[siparis.pk]), {"depo": self.depo.pk, "tarih": "2026-06-29"}, follow=True)
        irsaliye = siparis.donusen_irsaliyeler.get()
        self.assertEqual((irsaliye.durum, irsaliye.depo_id, irsaliye.tarih), ("TASLAK", self.depo.pk, date(2026, 6, 29)))
        self.assertContains(self.client.get(reverse("core:satinalma_siparisleri")), "İrsaliye taslağı var")
        teklif_siparis_onayla(irsaliye, kullanici=self.yon)
        self.assertContains(self.client.get(reverse("core:satinalma_siparisleri")), "İrsaliyeye Dönüştü")
        r = self.client.get(reverse("core:teklif_siparis_detay", args=[irsaliye.pk]))
        self.assertContains(r, "Faturaya Çevir")
        r = self.client.post(reverse("core:irsaliye_faturaya_cevir", args=[irsaliye.pk]), follow=True)
        irsaliye.refresh_from_db()
        self.assertIsNotNone(irsaliye.fatura_id)
        self.assertEqual(irsaliye.fatura.durum, "TASLAK")
        self.assertContains(r, "Taslak alış faturası açıldı")
        self.assertContains(self.client.get(reverse("core:satinalma_irsaliyeleri")), "Fatura taslağı var")
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[irsaliye.pk])), "Fatura taslağı var")
        # satış siparişi doğrudan faturaya çevrilemez mesajı güncel
        r = self.client.get(reverse("core:siparis_faturaya_cevir", args=[siparis.pk]), follow=True)
        self.assertContains(r, "İrsaliyeye Çevir")

    def test_taslak_hedef_silinince_kaynak_yeniden_cevrilebilir(self):
        teklif = self._teklif()
        teklif_siparis_onayla(teklif, kullanici=self.yon)
        siparis = teklifi_siparise_cevir(teklif, tarih=self.tarih, kullanici=self.yon)
        teklif_siparis_onayla(siparis, kullanici=self.yon)
        irsaliye = siparisi_irsaliyeye_cevir(siparis, tarih=self.tarih, depo_id=self.depo.pk, kullanici=self.yon)
        self.assertEqual(satinalma.kart_toplami(satinalma.acik_siparis_haritasi([self.stok.pk])), {self.stok.pk: D("10")})   # taslak irsaliye: gelecek
        irsaliye_sil(irsaliye, kullanici=self.yon)                                                     # taslak irsaliye silindi
        self.assertEqual(siparis.donusen_irsaliyeler.filter(silindi=False).count(), 0)
        self.client.force_login(self.yon)
        self.assertNotContains(self.client.get(reverse("core:satinalma_siparisleri")), "taslağı var")
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[siparis.pk])), "İrsaliyeye Çevir")
        irsaliye = siparisi_irsaliyeye_cevir(siparis, tarih=self.tarih, depo_id=self.depo.pk, kullanici=self.yon)   # yeniden çevrilebilir
        teklif_siparis_onayla(irsaliye, kullanici=self.yon)
        self.assertEqual(satinalma.acik_siparis_haritasi([self.stok.pk]), {})                           # onaylı irsaliye → gelecek değil
        irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.yon)
        irsaliye.refresh_from_db()
        fatura_sil(irsaliye.fatura, kullanici=self.yon)                                                # taslak fatura silindi
        irsaliye.refresh_from_db()
        self.assertIsNone(irsaliye.fatura_id)
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[irsaliye.pk])), "Faturaya Çevir")
        irsaliyeyi_faturaya_cevir(irsaliye, kullanici=self.yon)                                        # yeniden çevrilebilir


class GelecekStokTekKaynakTest(KritikTaban):
    def siparis(self, miktar="50", stok=None):
        from core.models import KdvOrani, Stok
        stok = stok or self.profil
        kdv = KdvOrani.objects.filter(oran=D("20")).first() or KdvOrani.objects.create(oran=D("20"), aciklama="G")
        Stok.objects.filter(pk=stok.pk).update(satinalma_urunu=True, kdv=kdv)
        cari = _cari("TEDARİKÇİ B", "320-99-0008")
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=cari.pk, tarih=date(2026, 1, 12),
                                     satirlar=[{"stok_id": stok.pk, "miktar": miktar, "birim_fiyat": "10"}], kullanici=self.yon)
        return teklif_siparis_onayla(sip, kullanici=self.yon)

    def test_taslak_fatura_kaynagi_acik_tutar_onayli_fatura_dusurur(self):
        sip = self.siparis()
        self.assertEqual(kritik_stok.acik_satinalma_haritasi([self.profil.pk]), {self.profil.pk: D("50")})
        fatura = Fatura.objects.create(cari=sip.cari, tarih=date(2026, 1, 13), yon="ALIS", durum="TASLAK")
        TeklifSiparis.objects.filter(pk=sip.pk).update(fatura=fatura)
        self.assertEqual(kritik_stok.acik_satinalma_haritasi([self.profil.pk]), {self.profil.pk: D("50")})     # TASLAK fatura: hâlâ gelecek
        Fatura.objects.filter(pk=fatura.pk).update(durum="ONAYLI")
        self.assertEqual(kritik_stok.acik_satinalma_haritasi([self.profil.pk]), {})                            # ONAYLI fatura: düştü
        Fatura.objects.filter(pk=fatura.pk).update(silindi=True)
        self.assertEqual(kritik_stok.acik_satinalma_haritasi([self.profil.pk]), {self.profil.pk: D("50")})     # silinmiş onaylı fatura sayılmaz

    def test_stok_listesi_esiksiz_kartta_gelecek_gosterir_ve_us_eksikten_duser(self):
        from core.models import Stok
        Stok.objects.filter(pk=self.profil.pk).update(kritik_stok=0)                                          # eşik yok → eski ekranda gelecek görünmezdi
        self.siparis("50")
        g = kritik_stok.gelecek_haritasi([self.profil.pk])[self.profil.pk]
        self.assertEqual((g["satinalma"], g["toplam"], [s["siparis"].belge_tur for s in g["siparisler"]]), (D("50"), D("50"), ["SIPARIS"]))
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:stoklar"), {"ara": self.profil.kod})
        self.assertContains(r, "satınalma 50,000")
        # ÜS eksik malzeme: fixture ÜS (10 mamul) profilden 5 ister; profil stoğu 100 → eksik yok. Stoğu sıfırla → eksik 5, siparişten bekleniyor.
        StokHareket.objects.filter(stok=self.profil).update(silindi=True)
        eks = [e for e in uretim.uretim_emri_eksikleri(self.emir) if e["stok"].pk == self.profil.pk]
        self.assertEqual(len(eks), 1)
        e = eks[0]
        self.assertEqual((e["tur"], e["siparis_bekleyen"], e["eksik"]), ("SATINALMA_SIPARIS", D("5"), D("0")))
        self.assertEqual([no for no, _pk, _t in e["siparisler"]][0][:3], "SAS")
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, "Satınalmadan bekleniyor: 1")
        self.assertNotContains(r, "Eksik malzeme:")
        r = self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk]))
        self.assertContains(r, "Satınalma siparişinden bekleniyor")
        # sipariş 3 adet olsaydı: 3 beklenir, 2 gerçek eksik
        TeklifSiparis.objects.filter(belge_tur="SIPARIS").update(silindi=True)
        self.siparis("3")
        e = [x for x in uretim.uretim_emri_eksikleri(self.emir) if x["stok"].pk == self.profil.pk][0]
        self.assertEqual((e["tur"], e["siparis_bekleyen"], e["eksik"]), ("SATINALMA", D("3"), D("2")))
        r = self.client.get(reverse("core:uretim_emirleri"))
        self.assertContains(r, "Eksik malzeme: 1")
        self.assertContains(self.client.get(reverse("core:uretim_emri_detay", args=[self.emir.pk])), "3 adet siparişte")


class VaryantliGelecekTest(VaryantEkranTaban):
    def test_satinalma_haritasi_varyantli(self):
        Depo.objects.filter(pk=self.depo.pk).exists()
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.tedarikci.pk, tarih=date(2026, 3, 1),
                                     satirlar=self.satir(self.afis, "10", "1", f"v:AFIS={self.mop.pk}"), kullanici=self.yon)
        teklif_siparis_onayla(sip, kullanici=self.yon)
        sv = sip.kalemler.get(silindi=False).varyant
        h = satinalma.acik_siparis_haritasi([self.afis.pk])
        self.assertEqual(h, {(self.afis.pk, sv.pk): D("10")})                                         # kalem varyantı (MR. MOP) ile anahtarlanır
        self.assertEqual(satinalma.kalan_icin(h, self.afis.pk, sv.pk), D("10"))
        self.assertEqual(satinalma.kalan_icin(h, self.afis.pk, self.afis.varsayilan_varyant_id), D("0"))   # standart varyant için gelecek YOK
        self.assertEqual(satinalma.kalan_icin(h, self.afis.pk, None), D("10"))                       # kapalı plan / kart toplamı
        from core.services.varyant import varyant_kisa
        self.assertEqual(satinalma.acik_siparis_satirlari([self.afis.pk])[0]["varyant_etiketi"], varyant_kisa(sv))
        # varyantsız kalem → kartın varsayılan varyantı
        sip2 = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=self.tedarikci.pk, tarih=date(2026, 3, 2),
                                      satirlar=self.satir(self.afis, "4", "1"), kullanici=self.yon)
        teklif_siparis_onayla(sip2, kullanici=self.yon)
        h = satinalma.acik_siparis_haritasi([self.afis.pk])
        self.assertEqual(h[(self.afis.pk, self.afis.varsayilan_varyant_id)], D("4"))
        self.assertEqual(satinalma.kart_toplami(h), {self.afis.pk: D("14")})
        self.assertEqual(kritik_stok.acik_satinalma_haritasi([self.afis.pk]), {self.afis.pk: D("14")})   # kritik stok aynı kaynak
