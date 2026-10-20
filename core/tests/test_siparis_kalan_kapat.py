"""Satınalma siparişinde "KALANI KAPAT (gelmeyecek)" (2026-10-20): kısmen teslim alınmış siparişin kalanı kalem bazında ya da toplu kapatılır (açıklama zorunlu); kapatılan miktar teslim
sayılmaz (stok girişi yok), kalandan düşer → "Tamamlandı"; gelecek stoktan düşer; kapatılmış siparişten irsaliye açılamaz; "Kapatmayı geri al" kalanı geri açar (hesap türetilmiş:
kapatmadan sonra teslim edilen miktar düşülür, kalan sipariş−teslim'i aşmaz); belgede/listede "iptal" kelimesi geçmez."""
import re
from decimal import Decimal

from django.urls import reverse

from core.models import SiparisKalanKapatma, StokHareket
from core.services import satinalma, siparis_teslim
from core.services.siparis_teslim import KalanKapatmaHatasi, kalani_kapat, kapatmayi_geri_al
from core.services.teklif_siparis import TeklifSiparisHatasi, siparisi_irsaliyeye_cevir, teklif_siparis_onayla
from core.tests.test_siparis_kismi_teslim import KismiTaban

Dc = Decimal


class KapatTaban(KismiTaban):
    def kismi_siparis(self):
        """A 100 / B 50 / C 20 sipariş; irsaliye: A 60 + B 50 → kalan A 40, C 20 (KISMI)."""
        s = self.siparis()
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"}])
        return s

    def satir(self, d, stok):
        return next(x for x in d["kalemler"] if x["kalem"].stok_id == stok.pk)


class KapatmaTest(KapatTaban):
    def test_toplu_kapatma_kalan_sifir_tamamlandi_teslim_degismez_stok_girisi_yok(self):
        s = self.kismi_siparis()
        hareket_once = StokHareket.objects.filter(silindi=False).count()
        kayitlar = kalani_kapat(s, aciklama="tedarikçi göndermeyecek", kullanici=self.u)
        self.assertEqual(sorted((k.kalem.stok.kod, k.miktar) for k in kayitlar), [("SO1", Dc("40")), ("SO3", Dc("20"))])
        d = siparis_teslim.siparis_durumu(s)
        self.assertEqual((d["durum"], d["kalan_var"], d["teslim_var"], d["kapatma_var"]), (siparis_teslim.TAMAM, False, True, True))
        a, c = self.satir(d, self.stok), self.satir(d, self.stok_c)
        self.assertEqual((a["teslim"], a["kapatilan"], a["kalan"]), (Dc("60"), Dc("40"), Dc("0")))      # kapatılan teslim SAYILMAZ
        self.assertEqual((c["teslim"], c["kapatilan"], c["kalan"]), (Dc("0"), Dc("20"), Dc("0")))
        self.assertEqual(StokHareket.objects.filter(silindi=False).count(), hareket_once)                # stok girişi YOK
        k = kayitlar[0]
        self.assertEqual((k.aciklama, k.created_by_id, k.siparis_id, k.aktif), ("tedarikçi göndermeyecek", self.u.pk, s.pk, True))
        self.assertEqual(len({x.grup for x in kayitlar}), 1)

    def test_kalem_bazinda_kapatma(self):
        s = self.kismi_siparis()
        k1 = kalani_kapat(s, aciklama="C gelmeyecek", kullanici=self.u, kalem_miktarlari={self.satir(siparis_teslim.siparis_durumu(s), self.stok_c)["kalem"].pk: "20"})
        self.assertEqual([(x.kalem.stok.kod, x.miktar) for x in k1], [("SO3", Dc("20"))])
        d = siparis_teslim.siparis_durumu(s)
        self.assertEqual((d["durum"], self.satir(d, self.stok)["kalan"], self.satir(d, self.stok_c)["kalan"]), (siparis_teslim.KISMI, Dc("40"), Dc("0")))
        kalem_a = self.satir(d, self.stok)["kalem"].pk
        kalani_kapat(s, aciklama="A'nın bir kısmı gelmeyecek", kullanici=self.u, kalem_miktarlari={kalem_a: "15"})
        d = siparis_teslim.siparis_durumu(s)
        a = self.satir(d, self.stok)
        self.assertEqual((a["kapatilan"], a["kalan"], d["durum"]), (Dc("15"), Dc("25"), siparis_teslim.KISMI))
        self.assertEqual(SiparisKalanKapatma.objects.filter(siparis=s).count(), 2)

    def test_dogrulamalar(self):
        s = self.siparis()
        with self.assertRaisesMessage(KalanKapatmaHatasi, "henüz hiçbir irsaliye onaylanmamış"):         # teslim yok → kısmi değil
            kalani_kapat(s, aciklama="x", kullanici=self.u)
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}, {"stok_id": self.stok_b.pk, "miktar": "50", "birim_fiyat": "3"}])
        for bos in ("", "   ", None):
            with self.assertRaisesMessage(KalanKapatmaHatasi, "Açıklama zorunlu"):
                kalani_kapat(s, aciklama=bos, kullanici=self.u)
        d = siparis_teslim.siparis_durumu(s)
        a = self.satir(d, self.stok)["kalem"].pk
        with self.assertRaisesMessage(KalanKapatmaHatasi, "kalanı (40) aşamaz"):
            kalani_kapat(s, aciklama="x", kullanici=self.u, kalem_miktarlari={a: "41"})
        with self.assertRaisesMessage(KalanKapatmaHatasi, "sıfırdan büyük"):
            kalani_kapat(s, aciklama="x", kullanici=self.u, kalem_miktarlari={a: "-1"})
        with self.assertRaisesMessage(KalanKapatmaHatasi, "geçerli bir sayı"):
            kalani_kapat(s, aciklama="x", kullanici=self.u, kalem_miktarlari={a: "abc"})
        with self.assertRaisesMessage(KalanKapatmaHatasi, "seçilmedi"):
            kalani_kapat(s, aciklama="x", kullanici=self.u, kalem_miktarlari={a: "0"})
        with self.assertRaisesMessage(KalanKapatmaHatasi, "bu siparişe ait değil"):
            kalani_kapat(s, aciklama="x", kullanici=self.u, kalem_miktarlari={999999: "1"})
        i2 = siparisi_irsaliyeye_cevir(s, tarih=__import__("datetime").date(2026, 6, 20), depo_id=self.depo.pk, kullanici=self.u)
        with self.assertRaisesMessage(KalanKapatmaHatasi, "irsaliye taslağı var"):                       # taslak irsaliye varken kapatılamaz
            kalani_kapat(s, aciklama="x", kullanici=self.u)
        teklif_siparis_onayla(i2, kullanici=self.u)                                                      # kalan da teslim edildi
        with self.assertRaisesMessage(KalanKapatmaHatasi, "Kapatılacak kalan yok"):
            kalani_kapat(s, aciklama="x", kullanici=self.u)
        self.assertEqual(SiparisKalanKapatma.objects.count(), 0)                                         # hatalarda kayıt oluşmadı

    def test_kapatilmis_siparisten_irsaliye_reddedilir_geri_alinca_acilir(self):
        s = self.kismi_siparis()
        kayitlar = kalani_kapat(s, aciklama="gelmeyecek", kullanici=self.u)
        with self.assertRaisesMessage(TeklifSiparisHatasi, "kalanı kapatılmış (gelmeyecek)"):
            self.irsaliye_taslagi(s, tarih=__import__("datetime").date(2026, 6, 25))
        for k in kayitlar:
            kapatmayi_geri_al(k, kullanici=self.u)
        i = self.irsaliye_taslagi(s, tarih=__import__("datetime").date(2026, 6, 25))
        self.assertEqual(sorted((x.stok.kod, x.miktar) for x in i.kalemler.filter(silindi=False)), [("SO1", Dc("40")), ("SO3", Dc("20"))])

    def test_geri_alma_kalan_geri_acilir_gecmis_kalir_cift_geri_alma_olmaz(self):
        s = self.kismi_siparis()
        kayitlar = kalani_kapat(s, aciklama="gelmeyecek", kullanici=self.u)
        k = kayitlar[0]
        geri = kapatmayi_geri_al(k, kullanici=self.u)
        self.assertFalse(geri.aktif)
        self.assertIsNotNone(geri.geri_alindi_at)
        self.assertEqual(geri.geri_alan_id, self.u.pk)
        with self.assertRaisesMessage(KalanKapatmaHatasi, "zaten geri alınmış"):
            kapatmayi_geri_al(k, kullanici=self.u)
        d = siparis_teslim.siparis_durumu(s)
        self.assertEqual(d["durum"], siparis_teslim.KISMI)
        kalanlar = {x["kalem"].stok.kod: (x["kapatilan"], x["kalan"]) for x in d["kalemler"]}
        self.assertEqual(sum(v[1] for v in kalanlar.values()), Dc("40") if k.kalem.stok.kod == "SO1" else Dc("20"))   # yalnız geri alınan kalem açıldı
        self.assertEqual(SiparisKalanKapatma.objects.filter(siparis=s).count(), 2)                       # geçmiş silinmedi
        for x in kayitlar[1:]:
            kapatmayi_geri_al(x, kullanici=self.u)
        self.assertFalse(siparis_teslim.siparis_durumu(s)["kapatma_var"])

    def test_kapatmadan_sonra_gelen_teslim_kalani_dogru_hesaplatir(self):
        """Kapatma sonrası (başka kalemin kalanından açılan irsaliyede) aynı kalemden fazladan teslim gelirse: geri alınca kalan sipariş − teslim'i aşmaz."""
        s = self.kismi_siparis()
        kalem_a = self.satir(siparis_teslim.siparis_durumu(s), self.stok)["kalem"].pk
        kalani_kapat(s, aciklama="A gelmeyecek", kullanici=self.u, kalem_miktarlari={kalem_a: "40"})   # kalan: yalnız C 20
        i = self.irsaliye_taslagi(s, tarih=__import__("datetime").date(2026, 6, 20))
        self.assertEqual([(x.stok.kod, x.miktar) for x in i.kalemler.filter(silindi=False)], [("SO3", Dc("20"))])
        self.duzenle(i, [{"stok_id": self.stok_c.pk, "miktar": "20", "birim_fiyat": "4"}, {"stok_id": self.stok.pk, "miktar": "30", "birim_fiyat": "2"}])   # tedarikçi A'dan 30 gönderdi
        teklif_siparis_onayla(i, kullanici=self.u)
        d = siparis_teslim.siparis_durumu(s)
        a = self.satir(d, self.stok)
        self.assertEqual((a["teslim"], a["kapatilan"], a["kalan"]), (Dc("90"), Dc("10"), Dc("0")))        # kapatılan, teslim edilenle sınırlı
        kapatmayi_geri_al(SiparisKalanKapatma.objects.get(siparis=s), kullanici=self.u)
        a = self.satir(siparis_teslim.siparis_durumu(s), self.stok)
        self.assertEqual((a["teslim"], a["kapatilan"], a["kalan"]), (Dc("90"), Dc("0"), Dc("10")))        # kalan = sipariş − teslim (doğru)


class GelecekStokVeEkranTest(KapatTaban):
    def test_gelecek_stoktan_duser_geri_alinca_geri_gelir(self):
        s = self.kismi_siparis()
        h = satinalma.acik_siparis_haritasi()
        self.assertEqual((h[(self.stok.pk, None)], h[(self.stok_c.pk, None)]), (Dc("40"), Dc("20")))
        kayitlar = kalani_kapat(s, aciklama="gelmeyecek", kullanici=self.u)
        self.assertEqual(satinalma.acik_siparis_haritasi(), {})
        kapatmayi_geri_al(next(k for k in kayitlar if k.kalem.stok.kod == "SO3"), kullanici=self.u)
        self.assertEqual(satinalma.acik_siparis_haritasi(), {(self.stok_c.pk, None): Dc("20")})

    def test_kismi_kapatma_gelecek_stoku_orantili_dusurur(self):
        s = self.kismi_siparis()
        kalem_a = self.satir(siparis_teslim.siparis_durumu(s), self.stok)["kalem"].pk
        kalani_kapat(s, aciklama="kısmen gelmeyecek", kullanici=self.u, kalem_miktarlari={kalem_a: "15"})
        self.assertEqual(satinalma.acik_siparis_haritasi()[(self.stok.pk, None)], Dc("25"))

    def giris(self):
        self.client.force_login(self.u)

    def test_ekran_kapat_formu_kayitlar_ve_iptal_kelimesi_gecmez(self):
        s = self.kismi_siparis()
        self.giris()
        detay = reverse("core:teklif_siparis_detay", args=[s.pk])
        r = self.client.get(detay)
        self.assertContains(r, "Kalanı kapat (gelmeyecek)")
        self.assertContains(r, "Tüm kalanı kapat")
        a = self.satir(siparis_teslim.siparis_durumu(s), self.stok)["kalem"].pk
        url = reverse("core:siparis_kalan_kapat", args=[s.pk])
        r = self.client.post(url, {f"miktar_{a}": "15", "aciklama": ""}, follow=True)                     # açıklamasız reddedilir
        self.assertContains(r, "Açıklama zorunlu")
        self.assertEqual(SiparisKalanKapatma.objects.count(), 0)
        r = self.client.post(url, {f"miktar_{a}": "15", "aciklama": "tedarikçi göndermeyecek"}, follow=True)
        self.assertContains(r, "Kalan kapatıldı (gelmeyecek)")
        r = self.client.post(url, {"tumu": "1", "aciklama": "geri kalan da gelmeyecek"}, follow=True)
        self.assertContains(r, "Kalan kapatıldı")
        r = self.client.get(detay)
        self.assertContains(r, "Kapatılan:")
        self.assertContains(r, "tedarikçi göndermeyecek")
        self.assertContains(r, "Kapatmayı geri al")
        self.assertContains(r, "Tamamlandı")
        self.assertContains(r, "Kapatılan</th>")
        self.assertNotContains(r, "Kalanı kapat (gelmeyecek)</summary>")                                  # kalan yok → form yok
        html = r.content.decode()
        for parca in (re.search(r'id="kalan-kapatmalar".*?</div>\s*</div>', html, re.S).group(0), " ".join(re.findall(r'class="d-rozet[^"]*"[^>]*>[^<]*</span>', html))):
            self.assertNotIn("iptal", parca.lower())                                                       # kapatma kayıtlarında ve rozetlerde "iptal" kelimesi geçmez
        liste = self.client.get(reverse("core:satinalma_siparisleri")).content.decode()
        self.assertIn("Tamamlandı", liste)
        self.assertNotIn("iptal", " ".join(re.findall(r'class="rozet[^"]*"[^>]*>[^<]*</span>', liste)).lower())      # listedeki rozetlerde yok
        kk = SiparisKalanKapatma.objects.filter(siparis=s).order_by("id").first()
        r = self.client.post(reverse("core:siparis_kalan_kapatma_geri_al", args=[kk.pk]), follow=True)
        self.assertContains(r, "Kapatma geri alındı")
        self.assertContains(r, "Geri alınan kapatma:")
        self.assertEqual(siparis_teslim.siparis_durumu(s)["durum"], siparis_teslim.KISMI)
        r = self.client.post(reverse("core:siparis_kalan_kapatma_geri_al", args=[kk.pk]), follow=True)
        self.assertContains(r, "zaten geri alınmış")

    def test_kapatma_formu_taslak_irsaliye_ve_teslimsiz_siparislerde_gorunmez(self):
        s = self.siparis()
        self.giris()
        self.assertNotContains(self.client.get(reverse("core:teklif_siparis_detay", args=[s.pk])), "Kalanı kapat (gelmeyecek)")      # teslim yok
        self.onayli_irsaliye(s, [{"stok_id": self.stok.pk, "miktar": "60", "birim_fiyat": "2"}])
        self.assertContains(self.client.get(reverse("core:teklif_siparis_detay", args=[s.pk])), "Kalanı kapat (gelmeyecek)")
        self.irsaliye_taslagi(s, tarih=__import__("datetime").date(2026, 6, 22))
        self.assertNotContains(self.client.get(reverse("core:teklif_siparis_detay", args=[s.pk])), "Kalanı kapat (gelmeyecek)")      # taslak irsaliye var
