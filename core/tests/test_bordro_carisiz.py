"""Bordro: net ödenen 0 satırda personel carisi zorunlu değil ('ad soyad (cari yok)'); personel carisine ALACAK yazılmaz, gider ve 360.20/361 satırları
normal oluşur. Net > 0 satırda cari zorunluluğu ve brüt − kesintiler = net kontrolü değişmez."""
import datetime
from decimal import Decimal

from django.urls import reverse

from core.models import Cari, HesapPlani, PersonelBordro
from core.services import bordro as bd
from core.services import cari as cari_servis
from core.tests import test_bordro as tb

D = datetime.date
Dc = Decimal


class BordroCarisizSatirTest(tb.BordroBase):
    def zorlu(self, **kw):
        s = self.satir(None, "1321.20", "1233.12", "88.08", "0", "0", "1475.34", "176.16")
        s.update({"ad_soyad": "abdurahman zorlu"}, **kw)
        return s

    def normal(self):
        return self.satir(self.p[0], "10000", "1400", "100", "300", "20", "900", "200")

    def test_net_sifir_carisiz_satir_fis_yapisi(self):
        b = self.olustur([self.normal(), self.zorlu()])
        fis = bd.bordro_fisi(b)
        s = bd.satirlar(b)[1]
        self.assertEqual((s.cari_id, s.ad_soyad, s.net, s.personel_adi), (None, "ABDURAHMAN ZORLU", Dc("0.00"), "ABDURAHMAN ZORLU"))
        satirlar = self.fis_satirlari(fis)
        self.assertEqual([t for h, tr, t in satirlar if h == "730.06"], [Dc("11100.00"), Dc("2972.70")])    # brüt + işveren payları
        self.assertEqual([h for h, tr, t in satirlar if h.startswith("335.")], ["335.10.0001"])             # carisiz satıra 335 ALACAK yok
        self.assertEqual([t for h, tr, t in satirlar if h == "360.20"], [Dc("320.00")])
        self.assertEqual([t for h, tr, t in satirlar if h == "361"],
                         [Dc("1400") + Dc("100") + Dc("900") + Dc("200") + Dc("1233.12") + Dc("88.08") + Dc("1475.34") + Dc("176.16")])
        self.assertEqual(sum(x.borc for x in fis.satirlar.all()), sum(x.alacak for x in fis.satirlar.all()))
        self.assertEqual(bd.ozet(b)["kisi"], 2)

    def test_net_pozitif_satirda_cari_zorunlu_kalir(self):
        s = self.satir(None, "1000", "100", "10", "0", "0", "0", "0")                      # net 890 > 0, carisiz ad yazılmış
        s["ad_soyad"] = "CARİSİZ KİŞİ"
        with self.assertRaises(bd.BordroHatasi):
            self.olustur([s])
        with self.assertRaises(bd.BordroHatasi):                                           # ne cari ne yeni ad
            self.olustur([self.satir(None, "1000", "100", "10", "0", "0", "0", "0")])
        self.assertEqual(PersonelBordro.objects.count(), 0)

    def test_net_sifir_gecersiz_kombinasyonlar_ve_kontrol_degismedi(self):
        n = Cari.objects.count()
        for degis in ({"ad_soyad": ""},                                                   # ne cari ne ad
                      {"yeni_ad": "YENİ KİŞİ"},                                          # net 0 için yeni cari AÇILMAZ
                      {"cari": self.p[0]},                                               # cari + ad_soyad birlikte
                      {"net": Dc("1")}):                                                 # brüt − kesintiler = 0 ≠ 1
            with self.assertRaises(bd.BordroHatasi):
                self.olustur([self.zorlu(**degis)])
        s = self.zorlu()
        s["brut"] += Dc("5")                                                              # brüt − kesintiler = 5 ≠ net 0
        with self.assertRaises(bd.BordroHatasi):
            self.olustur([s])
        self.assertEqual((Cari.objects.count(), PersonelBordro.objects.count()), (n, 0))

    def test_net_sifir_cari_secili_satirda_alacak_yazilmaz(self):
        b = self.olustur([self.satir(self.p[1], "1321.20", "1233.12", "88.08", "0", "0", "0", "0")])
        self.assertEqual([h for h, tr, t in self.fis_satirlari(bd.bordro_fisi(b)) if h.startswith("335.")], [])

    def test_ayni_carisiz_ad_iki_kez_olmaz(self):
        with self.assertRaises(bd.BordroHatasi):
            self.olustur([self.zorlu(), self.zorlu()])

    def test_duzenle_carisize_cevirme_ve_cari_silinebilir(self):
        """Haziran senaryosu: personel cari satırı net 0 + carisiz olarak düzenlenir; cari hareketsiz kalır ve silinebilir."""
        eski = cari_servis.cari_olustur(unvan="ABDURAHMAN ZORLU", kategori_id=self.kat.pk, para_birimi="TRY", kullanici=self.su)
        ilk = self.satir(eski, "1321.20", "1233.12", "88.08", "0", "0", "1475.34", "176.16")
        b = self.olustur([self.normal(), ilk])
        bd.bordro_guncelle(b, yil=2026, ay=9, tahakkuk_tarihi=D(2026, 9, 30), satirlar=[self.normal(), self.zorlu()], kullanici=self.su)
        self.assertEqual([h for h, tr, t in self.fis_satirlari(bd.bordro_fisi(b)) if h == eski.muhasebe_kodu], [])
        self.assertEqual(tb._bak(eski.muhasebe_kodu), Dc("0"))
        cari_servis.cari_sil(eski, kullanici=self.su)
        eski.refresh_from_db()
        self.assertTrue(eski.silindi)
        self.assertTrue(HesapPlani.objects.get(hesap_kodu=eski.muhasebe_kodu).silindi)             # hareketsiz hesap da gizlendi
        self.client.force_login(self.su)                                                           # düzenle/detay ekranı carisiz satırı gösterir
        self.assertContains(self.client.get(reverse("core:bordro_duzenle", args=[b.pk])), "ABDURAHMAN ZORLU")
        self.assertContains(self.client.get(reverse("core:bordro_detay", args=[b.pk])), "(cari yok)")

    def test_ekran_carisiz_satir_kaydi(self):
        self.client.force_login(self.su)
        v = tb.BordroEkranTest.post_verisi(None, [self.normal(), {**self.zorlu(), "cari": None}])
        v["form-1-ad_soyad"] = "abdurahman zorlu"
        r = self.client.post(reverse("core:bordro_ekle"), v)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(bd.satirlar(PersonelBordro.objects.get())[1].ad_soyad, "ABDURAHMAN ZORLU")
