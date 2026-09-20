"""ÜRETİM > Ürün Ağacı — salt-okunur GET görünümü. Ağaç ihtiyac_hesapla()'dan gelir; yeni tablo
ve hiçbir yazma yoktur. Zincir: Kesim (1 profil -> 2 kesilmiş parça) -> Büküm (1 kesilmiş ->
1 bükülmüş) -> Montaj (1 bükülmüş + 2 civata -> 1 mamul)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import (
    Birim, EkranYetki, IsIstasyonu, Kategori, Operasyon, OperasyonGirdi, OperasyonKaydi, Stok,
    StokHareket, UretimEmri,
)
from core.services.uretim import kok_operasyonlar, operasyon_olustur, operasyon_sil
from core.templatetags.core_extras import tr_kur, tr_miktar_sade
from core.yetki import kullanici_menusu


def _stok(kategori, birim, *, kod, ad, satinalma=False, satis=False):
    return Stok.objects.create(
        kod=kod, ad=ad, kategori=kategori, uretim_birimi=birim, fatura_birimi=birim,
        uretim_urunu=True, satinalma_urunu=satinalma, satis_urunu=satis)


class ZincirTaban(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("uayon", password="x")
        cls.bos = User.objects.create_user("uabos", password="x")
        cls.yetkili = User.objects.create_user("uayetkili", password="x")
        EkranYetki.objects.create(kullanici=cls.yetkili, ekran_kod="urun_agaci")
        cls.hesapla_yetkili = User.objects.create_user("uahesapla", password="x")
        EkranYetki.objects.create(kullanici=cls.hesapla_yetkili, ekran_kod="ihtiyac_hesapla")

        cls.birim = Birim.objects.create(ad="ADET", kisa_ad="AD", ondalik=0)
        cls.kat = Kategori.objects.create(kod="UA", ad="ÜRÜN AĞACI TEST")
        cls.lazer = IsIstasyonu.objects.create(kod="LAZER", ad="LAZER İSTASYONU")
        cls.bukum = IsIstasyonu.objects.create(kod="BUKUM", ad="BÜKÜM İSTASYONU")
        cls.montaj = IsIstasyonu.objects.create(kod="MONTAJ", ad="MONTAJ İSTASYONU")
        cls.profil = _stok(cls.kat, cls.birim, kod="UA-PROFIL", ad="ham profil", satinalma=True)
        cls.civata = _stok(cls.kat, cls.birim, kod="UA-CIVATA", ad="civata", satinalma=True)
        cls.kesilmis = _stok(cls.kat, cls.birim, kod="UA-KESILMIS", ad="kesilmiş parça")
        cls.bukulmus = _stok(cls.kat, cls.birim, kod="UA-BUKULMUS", ad="bükülmüş parça")
        cls.mamul = _stok(cls.kat, cls.birim, kod="UA-MAMUL", ad="test merdiveni", satis=True)
        cls.op_kesim = operasyon_olustur(
            istasyon_id=cls.lazer.pk, cikti_id=cls.kesilmis.pk, cikti_miktar=Decimal("2"),
            satirlar=[(cls.profil, Decimal("1"))])
        cls.op_bukum = operasyon_olustur(
            istasyon_id=cls.bukum.pk, cikti_id=cls.bukulmus.pk, cikti_miktar=Decimal("1"),
            satirlar=[(cls.kesilmis, Decimal("1"))])
        cls.op_montaj = operasyon_olustur(
            istasyon_id=cls.montaj.pk, cikti_id=cls.mamul.pk, cikti_miktar=Decimal("1"),
            satirlar=[(cls.bukulmus, Decimal("1")), (cls.civata, Decimal("2"))])


class KokOperasyonlarTest(ZincirTaban):
    def test_yalniz_zincirin_en_ustu_doner(self):
        self.assertEqual([o.cikti for o in kok_operasyonlar()], [self.mamul])

    def test_girdi_sayisi_hesaplanir(self):
        self.assertEqual(kok_operasyonlar().get().girdi_sayisi, 2)

    def test_bagimsiz_ikinci_mamul_de_listelenir(self):
        ikinci = _stok(self.kat, self.birim, kod="UA-MAMUL2", ad="ikinci merdiven", satis=True)
        operasyon_olustur(istasyon_id=self.montaj.pk, cikti_id=ikinci.pk,
                          cikti_miktar=Decimal("1"), satirlar=[(self.civata, Decimal("3"))])
        self.assertEqual({o.cikti for o in kok_operasyonlar()}, {self.mamul, ikinci})

    def test_silinen_ust_operasyon_alt_operasyonu_kok_yapar(self):
        operasyon_sil(self.op_montaj)
        self.assertEqual([o.cikti for o in kok_operasyonlar()], [self.bukulmus])


class UrunAgaciViewTest(ZincirTaban):
    def _al(self, **params):
        self.client.force_login(self.yon)
        return self.client.get(reverse("core:urun_agaci"), params)

    def _cocuk(self, dugum, stok):
        return next(c for c in dugum["cocuklar"] if c["stok"] == stok)

    # --- Yetki: kendi ekran kodunu ister, başka ekranın yetkisi yetmez ---
    def test_izinler(self):
        url = reverse("core:urun_agaci")
        r = self.client.get(url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login/", r.url)
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.hesapla_yetkili)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.yetkili)
        self.assertEqual(self.client.get(url).status_code, 200)

    # --- Açılış listesi ---
    def test_acilis_yalniz_bitmis_urunleri_listeler(self):
        r = self._al()
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["agac"])
        self.assertEqual([o.cikti for o in r.context["kokler"]], [self.mamul])
        self.assertContains(r, "Bitmiş Ürünler")
        self.assertContains(r, f"?urun={self.mamul.pk}")

    def test_operasyon_yoksa_bos_durum_mesaji(self):
        Operasyon.objects.all().delete()
        r = self._al()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Henüz tanımlı operasyon yok")

    # --- Ağaç ---
    def test_agac_miktarlari_ust_seviyeden_alta_hesaplanir(self):
        agac = self._al(urun=self.mamul.pk, miktar="10").context["agac"]
        self.assertEqual(agac["stok"], self.mamul)
        self.assertEqual(agac["miktar"], Decimal("10"))
        bukulmus = self._cocuk(agac, self.bukulmus)
        self.assertEqual(bukulmus["miktar"], Decimal("10"))
        kesilmis = self._cocuk(bukulmus, self.kesilmis)
        self.assertEqual(kesilmis["miktar"], Decimal("10"))
        profil = self._cocuk(kesilmis, self.profil)
        self.assertEqual(profil["miktar"], Decimal("5"))            # 1 çalıştırma -> 2 parça
        self.assertTrue(profil["yaprak"])
        civata = self._cocuk(agac, self.civata)
        self.assertEqual(civata["miktar"], Decimal("20"))
        self.assertTrue(civata["yaprak"])

    def test_html_ic_ice_dugumleri_ve_tr_bicimi_gosterir(self):
        r = self._al(urun=self.mamul.pk, miktar="10")
        self.assertContains(r, "<details")
        self.assertContains(r, "hazır stok")
        for kod in ("LAZER", "BUKUM", "MONTAJ", "UA-PROFIL", "UA-CIVATA"):
            self.assertContains(r, kod)
        self.assertContains(r, ">× 20</span>")                      # tam sayı: ondalıksız
        self.assertContains(r, ">× 5</span>")
        self.assertIsNone(r.context["kokler"])                      # ağaç varken liste gizli

    def test_miktar_bos_ise_bir_adet_icin_agac(self):
        agac = self._al(urun=self.mamul.pk).context["agac"]
        self.assertEqual(agac["miktar"], Decimal("1"))
        profil = self._cocuk(self._cocuk(self._cocuk(agac, self.bukulmus), self.kesilmis),
                             self.profil)
        self.assertEqual(profil["miktar"], Decimal("0.5"))

    def test_miktar_tr_bicimi_cozulur(self):
        """Tek parser: virgül ondalık, nokta binlik ('1.000' = bin, bir değil)."""
        agac = self._al(urun=self.mamul.pk, miktar="1,5").context["agac"]
        self.assertEqual(agac["miktar"], Decimal("1.5"))
        agac = self._al(urun=self.mamul.pk, miktar="1.000").context["agac"]
        self.assertEqual(agac["miktar"], Decimal("1000"))
        self.assertEqual(self._cocuk(agac, self.civata)["miktar"], Decimal("2000"))

    def test_ara_bir_urunun_kendi_agaci_da_gosterilir(self):
        agac = self._al(urun=self.bukulmus.pk, miktar="4").context["agac"]
        self.assertEqual(agac["stok"], self.bukulmus)
        self.assertEqual(self._cocuk(self._cocuk(agac, self.kesilmis), self.profil)["miktar"],
                         Decimal("2"))

    # --- Sunum: sade miktar, istasyon adı, hizalı kolonlar, açılım, yaprak rozeti ---
    def _agac_html(self, **params):
        """Yalnız ağaç bölümü (sonrasındaki base.html JS/CSS sayıları karışmasın)."""
        html = self._al(**params).content.decode()
        return html.split('class="ua-agac"')[1].split("</section>")[0]

    def test_tam_sayi_miktar_ondaliksiz_kesirli_uc_ondalik(self):
        agac = self._agac_html(urun=self.mamul.pk, miktar="1")
        self.assertIn(">× 1</span>", agac)                          # kök
        self.assertIn(">× 2</span>", agac)                          # civata: tam sayı
        self.assertIn(">× 0,500</span>", agac)                      # profil: kesirli -> 3 ondalık
        self.assertNotIn(",000", agac)
        agac = self._agac_html(urun=self.mamul.pk, miktar="10")
        self.assertIn(">× 5</span>", agac)                          # profil 5: tam sayı
        self.assertIn(">× 20</span>", agac)
        self.assertNotIn(",000", agac)

    def test_istasyon_rozeti_kod_ve_ad_gosterir(self):
        agac = self._agac_html(urun=self.mamul.pk)
        for rozet in ("LAZER · LAZER İSTASYONU", "BUKUM · BÜKÜM İSTASYONU",
                      "MONTAJ · MONTAJ İSTASYONU"):
            self.assertIn(rozet, agac)

    def test_her_satirda_ayni_uc_kolon(self):
        """5 düğüm (mamul, bükülmüş, kesilmiş, profil, civata): hepsinde metin + miktar + rozet."""
        agac = self._agac_html(urun=self.mamul.pk)
        self.assertEqual(agac.count('class="ua-ad"'), 5)
        self.assertEqual(agac.count('class="ua-sag"'), 5)           # miktar+rozet tek sağ grup
        self.assertEqual(agac.count('class="ua-miktar mono"'), 5)
        self.assertEqual(agac.count('class="ua-ist"'), 5)

    def test_varsayilan_acilim_kok_ve_bir_alt_seviye(self):
        """mamul(0) > bükülmüş(1) > kesilmiş(2) > profil; civata yaprak. Derinlik 0 ve 1 açık,
        2 ve sonrası kapalı başlar."""
        agac = self._agac_html(urun=self.mamul.pk)
        self.assertEqual(agac.count('<details class="ua-dugum" open>'), 2)
        self.assertEqual(agac.count('<details class="ua-dugum">'), 1)

    def test_hepsini_ac_kapat_yalniz_agac_varken(self):
        html = self._al(urun=self.mamul.pk).content.decode()
        for parca in ('data-ua="ac"', 'data-ua="kapat"', "Hepsini Aç", "Hepsini Kapat"):
            self.assertIn(parca, html)
        self.assertNotIn('data-ua="ac"', self._al().content.decode())

    def test_yaprak_rozeti_ayri_ve_kisa(self):
        agac = self._agac_html(urun=self.mamul.pk)
        self.assertEqual(agac.count('class="ua-hazir"'), 2)         # profil + civata
        self.assertNotIn("yaprak (hazır stok)", agac)

    # --- Hatalı girdi: 500 yok, ağaç yok, alan hatası var ---
    def test_gecersiz_miktar_hata_verir(self):
        for miktar in ("abc", "0", "-2"):
            with self.subTest(miktar=miktar):
                r = self._al(urun=self.mamul.pk, miktar=miktar)
                self.assertEqual(r.status_code, 200)
                self.assertIsNone(r.context["agac"])
                self.assertIn("miktar", r.context["form"].errors)

    def test_operasyonsuz_veya_olmayan_urun_secilemez(self):
        for urun in (self.profil.pk, 999999, "abc"):
            with self.subTest(urun=urun):
                r = self._al(urun=urun)
                self.assertEqual(r.status_code, 200)
                self.assertIsNone(r.context["agac"])
                self.assertIn("urun", r.context["form"].errors)
                self.assertIsNotNone(r.context["kokler"])           # açılış listesi geri gelir

    def test_dongu_500_degil_hata_mesaji_verir(self):
        a = _stok(self.kat, self.birim, kod="UA-DONGU-A", ad="a")
        b = _stok(self.kat, self.birim, kod="UA-DONGU-B", ad="b")
        ist = IsIstasyonu.objects.create(kod="DONGU", ad="DÖNGÜ İSTASYONU")
        operasyon_olustur(istasyon_id=ist.pk, cikti_id=a.pk, cikti_miktar=Decimal("1"),
                          satirlar=[(b, Decimal("1"))])
        operasyon_olustur(istasyon_id=ist.pk, cikti_id=b.pk, cikti_miktar=Decimal("1"),
                          satirlar=[(a, Decimal("1"))])
        r = self._al(urun=a.pk)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context["agac"])
        self.assertContains(r, "döngü tespit edildi")

    # --- Salt-okunur: hiçbir şey yazmaz ---
    def test_hicbir_kayit_veya_hareket_uretmez(self):
        def sayimlar():
            return (Operasyon.objects.count(), OperasyonGirdi.objects.count(),
                    OperasyonKaydi.objects.count(), UretimEmri.objects.count(),
                    StokHareket.objects.count(), Stok.objects.count())

        onceki = sayimlar()
        self._al()
        self._al(urun=self.mamul.pk, miktar="10")
        self._al(urun=self.mamul.pk, miktar="abc")
        self.assertEqual(sayimlar(), onceki)

    # --- Menü ---
    def test_menude_yetkiye_gore_gorunur(self):
        def uretim_kodlari(kullanici):
            menu = kullanici_menusu(kullanici)
            return [e.kod for m in menu if m.kod == "URETIM" for e in m.ekranlar]

        self.assertIn("urun_agaci", uretim_kodlari(self.yon))
        self.assertEqual(uretim_kodlari(self.yetkili), ["urun_agaci"])
        self.assertEqual(uretim_kodlari(self.hesapla_yetkili), ["ihtiyac_hesapla"])
        self.assertEqual(uretim_kodlari(self.bos), [])


class TrMiktarSadeFiltreTest(SimpleTestCase):
    def test_tam_sayi_ondaliksiz(self):
        self.assertEqual(tr_miktar_sade(Decimal("1.000")), "1")
        self.assertEqual(tr_miktar_sade(Decimal("1")), "1")
        self.assertEqual(tr_miktar_sade(Decimal("1000")), "1.000")   # nokta = binlik
        self.assertEqual(tr_miktar_sade(3), "3")

    def test_kesirli_uc_ondalik_kalir(self):
        self.assertEqual(tr_miktar_sade(Decimal("0.056")), "0,056")
        self.assertEqual(tr_miktar_sade(Decimal("12.5")), "12,500")
        self.assertEqual(tr_miktar_sade(Decimal("1234.5")), "1.234,500")
        self.assertEqual(tr_miktar_sade("2.50"), "2,500")

    def test_yuvarlanmis_deger_tam_sayiysa_ondaliksiz(self):
        """Zincir bölmesi 10/3*3 = 9,999…: ekranda '10,000' değil '10' görünmeli."""
        self.assertEqual(tr_miktar_sade(Decimal(10) / Decimal(3) * Decimal(3)), "10")
        self.assertEqual(tr_miktar_sade(Decimal("10.0004")), "10")
        self.assertEqual(tr_miktar_sade(Decimal("10.0005")), "10,001")   # ROUND_HALF_UP

    def test_bos_degerler(self):
        self.assertEqual(tr_miktar_sade(None), "")
        self.assertEqual(tr_miktar_sade(""), "")

    def test_tr_kur_degismedi(self):
        """tr_kur 30 şablonda kur/tutar için kullanılıyor — tam sayıda ondalık ATMAMALI."""
        self.assertEqual(tr_kur(Decimal("30"), 6), "30,000000")
        self.assertEqual(tr_kur(Decimal("1"), 3), "1,000")
