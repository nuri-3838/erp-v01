"""KRİTİK STOK UYARISI (2026-10-12): kalan = kullanılabilir + gelecek − bekleyen tüketim; kritik_stok 0 → kontrol yok; gelecek = açık satınalma
siparişleri (onaylı irsaliyesi olmayan) + açık istasyon emirlerinin kalan çıktısı; bekleyen = açık emirlerin ayrılmamış girdi ihtiyacı; öneri
(fason / satınalma / stok için ÜS aç — miktar = max(parti, kritik − kalan)); pano kutusu; stok listesinde "Yalnız kritik" süzgeci, rozet ve
sütunlar; ÜS aç ekranı ön dolu."""
import re
from datetime import date
from decimal import Decimal

from django.urls import reverse

from core.models import IsIstasyonu, KdvOrani, Stok, TeklifSiparis
from core.services import kritik_stok
from core.services.teklif_siparis import teklif_siparis_olustur, teklif_siparis_onayla
from core.tests.test_stok import _cari
from core.tests.test_stoklu_uretim import StokluTaban

D = Decimal


class KritikTaban(StokluTaban):
    def setUp(self):
        super().setUp()
        Stok.objects.filter(pk=self.mamul.pk).update(kritik_stok=D("20"), parti_miktari=D("25"))
        Stok.objects.filter(pk=self.kesilmis.pk).update(kritik_stok=D("5"))
        Stok.objects.filter(pk=self.profil.pk).update(kritik_stok=D("200"))
        for s in (self.mamul, self.kesilmis, self.profil, self.bukulmus):
            s.refresh_from_db()

    def satir(self, stok):
        h = kritik_stok.hesapla(Stok.objects.filter(pk__in=[self.mamul.pk, self.kesilmis.pk, self.profil.pk, self.bukulmus.pk]).select_related("kategori"))
        return h.get(stok.pk)


class HesapTest(KritikTaban):
    def test_kalan_gelecek_bekleyen_ve_oneriler(self):
        self.assertIsNone(self.satir(self.bukulmus))                                              # kritik 0: kontrol yok
        m = self.satir(self.mamul)                                                                 # stok 4, gelecek 0 → kalan 4 < 20
        self.assertEqual((m["kullanilabilir"], m["gelecek"], m["bekleyen"], m["kalan"], m["eksik"], m["kritik_mi"]), (D("4"), D("0"), D("0"), D("4"), D("16"), True))
        self.assertEqual((m["oneri"]["tur"], m["oneri"]["miktar"]), ("URETIM", D("25")))         # max(parti 25, eksik 16)
        self.assertIn(f"?urun={self.mamul.pk}&miktar=25", m["oneri"]["url"])
        emir = self.us()                                                                           # ÜS 10 mamul (fixture ÜS: bükülmüş 10 ayrıca)
        m = self.satir(self.mamul)
        self.assertEqual((m["gelecek"], m["kalan"], m["eksik"], m["oneri"]["miktar"]), (D("10"), D("14"), D("6"), D("25")))
        k = self.satir(self.kesilmis)                                                              # kesim emirleri çıktı 2/çalıştırma; büküm emirleri tüketir
        self.assertEqual((k["kullanilabilir"], k["gelecek"], k["bekleyen"], k["kalan"], k["kritik_mi"], k["eksik"]), (D("0"), D("20"), D("20"), D("0"), True, D("5")))
        self.assertEqual((k["oneri"]["tur"], k["oneri"]["metin"], k["oneri"]["miktar"]), ("URETIM", "Stok için ÜS aç", D("5")))
        p = self.satir(self.profil)                                                                # 100 − ayrılan 10 = 90; ayrılan tüketim bekleyen sayılmaz
        self.assertEqual((p["kullanilabilir"], p["gelecek"], p["bekleyen"], p["kalan"], p["eksik"], p["oneri"]["tur"], p["oneri"]["metin"]),
                         (D("90"), D("0"), D("0"), D("90"), D("110"), "SATINALMA", "Satınalma"))      # profil satınalma ürünü
        Stok.objects.filter(pk=self.profil.pk).update(satinalma_urunu=False)
        self.assertEqual((self.satir(self.profil)["oneri"]["tur"], self.satir(self.profil)["oneri"]["metin"]), (None, "—"))   # ne satınalma ne üretilen
        IsIstasyonu.objects.filter(pk=self.lazer.pk).update(fason=True)                            # kesim fason istasyonunda → fason parça
        self.assertEqual(self.satir(self.kesilmis)["oneri"]["metin"], "Fason sipariş")
        emir.istasyon_emirleri.update(durum="IPTAL")                                               # kapanan emirler sayılmaz
        self.assertEqual(self.satir(self.mamul)["gelecek"], D("0"))

    def test_acik_satinalma_siparisi_gelecek(self):
        Stok.objects.filter(pk=self.profil.pk).update(satinalma_urunu=True)
        kdv = KdvOrani.objects.create(oran=D("20"), aciklama="G")
        Stok.objects.filter(pk=self.profil.pk).update(kdv=kdv)
        cari = _cari("TEDARİKÇİ A.Ş.", "320-99-0007")
        sip = teklif_siparis_olustur(belge_tur="SIPARIS", yon="ALIS", cari_id=cari.pk, tarih=date(2026, 1, 12),
                                     satirlar=[{"stok_id": self.profil.pk, "miktar": "50", "birim_fiyat": "10"}], kullanici=self.yon)
        self.assertEqual(self.satir(self.profil)["gelecek"], D("0"))                               # taslak sipariş sayılmaz
        teklif_siparis_onayla(sip, kullanici=self.yon)                                             # onay: taslak irsaliye otomatik açılır
        p = self.satir(self.profil)
        self.assertEqual((p["gelecek"], p["kalan"]), (D("50"), D("145")))                          # 100 − fixture ÜS ayırması 5 + 50
        irs = sip.donusen_irsaliyeler.get(silindi=False)
        TeklifSiparis.objects.filter(pk=irs.pk).update(durum=TeklifSiparis.Durum.ONAYLI)           # irsaliye onaylandı → artık gelecek değil
        self.assertEqual(self.satir(self.profil)["gelecek"], D("0"))
        self.assertEqual(sorted(r["stok"].pk for r in kritik_stok.kritik_kalemler()), sorted([self.mamul.pk, self.kesilmis.pk, self.profil.pk]))


class EkranTest(KritikTaban):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.yon)

    def test_pano_kutusu_liste_suzgeci_ve_us_on_dolu(self):
        r = self.client.get(reverse("core:pano"))
        self.assertContains(r, "Kritik stok: 3 kalem")
        self.assertContains(r, "?kritik=1")
        r = self.client.get(reverse("core:stoklar") + "?kritik=1")
        self.assertContains(r, "⚠ kritik", count=3)
        self.assertContains(r, "Yalnız kritik (3)")
        self.assertContains(r, "Stok için ÜS aç (25")
        self.assertContains(r, "Bekleyen Tük.")
        self.assertNotContains(r, "IK-BUKULMUS")                                                    # eşiği yok → listede değil
        r = self.client.get(reverse("core:stoklar"))
        self.assertContains(r, "IK-BUKULMUS")
        self.assertContains(r, "⚠ kritik", count=3)
        Stok.objects.filter(pk=self.mamul.pk).update(kritik_stok=D("2"))
        r = self.client.get(reverse("core:stoklar") + "?kritik=1")
        self.assertContains(r, "⚠ kritik", count=2)
        self.assertNotContains(r, "152-10-0001")
        self.assertContains(self.client.get(reverse("core:pano")), "Kritik stok: 2 kalem")
        r = self.client.get(reverse("core:uretim_emri_ekle") + f"?urun={self.mamul.pk}&miktar=25")
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r.content.decode(), rf'<option value="{self.mamul.pk}"[^>]*selected')
        self.assertRegex(r.content.decode(), r'name="satir-0-hedef_miktar"[^>]*value="25')
