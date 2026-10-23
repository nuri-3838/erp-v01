"""Stoklar listesi kullanılabilirlik düzenlemesi (2026-10-22, yalnız görünüm): üst kategori sekmeleri, süzgeçler (stoku sıfır olanları gizle, son 30 günde hareket,
kritik), sütun başlığıyla sıralama, sayfalama (25/50/100) ve hepsinin URL'de kalması, varyantlı kartta Mevcut ▾ kırılımı, Durum rozetlerinin yalnız değer varsa çıkması,
sabit sorgu sayısı (kart sayısından bağımsız)."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Stok, Varyant
from core.services import varyant as vs
from core.services.kategori import kategori_olustur
from core.tarih import tr_bugun
from core.tests.test_stok_varyant_motor import VaryantMotorTaban

D = Decimal


class StokListeTaban(VaryantMotorTaban):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        ust_h = kategori_olustur(ad="hammadde", kod="150")
        cls.alt_h = kategori_olustur(ad="alüminyum", kod="10", ust_id=ust_h.pk)
        ust_m = kategori_olustur(ad="mamuller", kod="152")
        cls.alt_m = kategori_olustur(ad="merdiven", kod="10", ust_id=ust_m.pk)
        cls.ust_h, cls.ust_y, cls.ust_m = ust_h, cls.alt.ust, ust_m

    def setUp(self):
        self.yon = User.objects.create_superuser("slyon", password="x")
        self.client.force_login(self.yon)

    def liste(self, **q):
        return self.client.get(reverse("core:stoklar"), q)

    @staticmethod
    def kodlar(yanit):
        return re.findall(r'<span class="stk-kod">([^<]+)</span>', yanit.content.decode())

    def kartlar(self):
        """3 üst kategoride birer kart: hammadde (10 adet), yarı mamul (0), mamul (5)."""
        h = self.kart("ham", varyantli=False, kategori=self.alt_h)
        y = self.kart("yarı", varyantli=False)
        m = self.kart("mamul", varyantli=False, kategori=self.alt_m)
        self.giris(h, "10", "100", tarih=tr_bugun())
        self.giris(m, "5", "50", tarih=date(2025, 1, 5))
        return h, y, m


class SekmeSuzgecTest(StokListeTaban):
    def test_sekmeler_ust_kategorilerden_ve_grup_filtresi(self):
        h, y, m = self.kartlar()
        r = self.liste()
        sek = {t["ad"]: t for t in r.context["sekmeler"]}
        self.assertEqual([t["ad"] for t in r.context["sekmeler"]], ["Tümü", "Hammadde", "Yarı mamul", "Mamul"])
        self.assertEqual(sek["Tümü"]["sayi"], 3)
        self.assertEqual((sek["Hammadde"]["sayi"], sek["Yarı mamul"]["sayi"], sek["Mamul"]["sayi"]), (1, 1, 1))
        self.assertTrue(sek["Tümü"]["aktif"])
        r = self.liste(grup=self.ust_m.pk)
        self.assertEqual(self.kodlar(r), [m.kod])
        self.assertTrue({t["ad"]: t for t in r.context["sekmeler"]}["Mamul"]["aktif"])
        self.assertContains(r, 'aria-current="page"')

    def test_stoku_sifir_olanlari_gizle(self):
        h, y, m = self.kartlar()
        self.assertEqual(set(self.kodlar(self.liste())), {h.kod, y.kod, m.kod})
        self.assertEqual(set(self.kodlar(self.liste(sifir="1"))), {h.kod, m.kod})

    def test_son_30_gunde_hareket(self):
        h, y, m = self.kartlar()
        self.assertEqual(self.kodlar(self.liste(hareketli="1")), [h.kod])                       # yarı: hareketsiz, mamul: 2025
        self.giris(m, "1", "10", tarih=tr_bugun() - timedelta(days=29))
        self.assertEqual(set(self.kodlar(self.liste(hareketli="1"))), {h.kod, m.kod})
        self.giris(y, "1", "10", tarih=tr_bugun() - timedelta(days=31))
        self.assertNotIn(y.kod, self.kodlar(self.liste(hareketli="1")))

    def test_suzgecler_birlikte_ve_url_korunur(self):
        h, y, m = self.kartlar()
        r = self.liste(sifir="1", hareketli="1", ara="ham")
        self.assertEqual(self.kodlar(r), [h.kod])
        for t in r.context["sekmeler"]:
            self.assertIn("sifir=1", t["url"])
            self.assertIn("hareketli=1", t["url"])
            self.assertIn("ara=ham", t["url"])
        self.assertContains(r, 'name="sifir" value="1" form="stk-form" class="stk-oto" checked')

    def test_gecersiz_parametreler_hata_vermez(self):
        self.kartlar()
        r = self.liste(grup="x", kategori="'; drop", boyut="9999", sirala="zzz", yon="yan", sayfa="abc")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["boyut"], 50)
        self.assertEqual(r.context["sirala"], "kod")


class SiralamaSayfalamaTest(StokListeTaban):
    def test_siralama_kod_ad_mevcut_kullanilabilir(self):
        h, y, m = self.kartlar()
        self.assertEqual(self.kodlar(self.liste()), sorted([h.kod, y.kod, m.kod]))
        self.assertEqual(self.kodlar(self.liste(sirala="kod", yon="desc")), sorted([h.kod, y.kod, m.kod], reverse=True))
        self.assertEqual(self.kodlar(self.liste(sirala="mevcut", yon="desc"))[:2], [h.kod, m.kod])          # 10 > 5 > 0
        self.assertEqual(self.kodlar(self.liste(sirala="mevcut", yon="asc"))[0], y.kod)
        self.assertEqual(self.kodlar(self.liste(sirala="kullanilabilir", yon="desc"))[0], h.kod)
        adlar = {s.kod: s.ad for s in (h, y, m)}
        beklenen = [k for k, _ in sorted(adlar.items(), key=lambda x: x[1])]
        self.assertEqual(set(self.kodlar(self.liste(sirala="ad"))), set(beklenen))

    def test_baslik_siralama_baglantilari(self):
        self.kartlar()
        r = self.liste(sirala="mevcut", yon="asc")
        self.assertTrue(r.context["sutunlar"]["mevcut"]["aktif"])
        self.assertIn("yon=desc", r.context["sutunlar"]["mevcut"]["url"])                    # aktif sütuna tekrar tıklayınca yön döner
        self.assertIn("sirala=kod", r.context["sutunlar"]["kod"]["url"])
        self.assertContains(r, "Mevcut ▲")

    def test_turkce_ad_sirasi(self):
        a = self.kart("cam", varyantli=False)
        b = self.kart("çam", varyantli=False)
        c = self.kart("dam", varyantli=False)
        sirali = [s.ad for s in sorted([a, b, c], key=lambda s: s.ad)]
        r = self.liste(sirala="ad")
        ad_sirasi = [x.ad for x in r.context["stoklar"]]
        self.assertEqual(ad_sirasi, [a.ad, b.ad, c.ad])                                          # C < Ç < D
        self.assertTrue(sirali)

    def test_sayfalama_boyut_ve_baglantilar(self):
        for i in range(30):
            self.kart(f"kart {i:02d}", varyantli=False)
        r = self.liste(boyut="25")
        self.assertEqual((len(self.kodlar(r)), r.context["toplam"], r.context["sayfa"].paginator.num_pages), (25, 30, 2))
        self.assertContains(r, "boyut=25")
        ikinci = self.liste(boyut="25", sayfa="2")
        self.assertEqual(len(self.kodlar(ikinci)), 5)
        self.assertFalse(set(self.kodlar(r)) & set(self.kodlar(ikinci)))
        self.assertIn("boyut=25", r.context["sonraki_url"])
        self.assertEqual(len(self.kodlar(self.liste())), 30)                                     # varsayılan 50
        self.assertEqual(len(self.kodlar(self.liste(boyut="100"))), 30)
        self.assertEqual(len(self.kodlar(self.liste(boyut="7"))), 30)                            # geçersiz boyut → 50
        self.assertNotContains(self.liste(), 'class="sayfalama"')                                # tek sayfa: sayfalama yok
        self.assertContains(r, 'class="sayfalama"')


class GorunumTest(StokListeTaban):
    def test_varyantli_kartta_mevcut_kirilimi(self):
        s = self.kart("ayak", varyantli=True)
        yok = vs.stok_varyanti_bul_veya_olustur(s, {"MARKALAMA": self.yok})
        self.giris(s, "10", "1000")
        self.giris(s, "5", "500", varyant=yok)
        t = self.kart("tek", varyantli=True)
        self.giris(t, "7", "70")
        r = self.liste()
        html = r.content.decode()
        satir_s = html.split(f'<span class="stk-kod">{s.kod}</span>', 1)[1].split("</tr>", 1)[0]
        self.assertIn("15,000 ▾", satir_s)
        self.assertIn("SEMTA", satir_s)
        self.assertIn("YOK", satir_s)
        self.assertIn("10,000", satir_s)
        self.assertIn("5,000", satir_s)
        satir_t = html.split(f'<span class="stk-kod">{t.kod}</span>', 1)[1].split("</tr>", 1)[0]
        self.assertNotIn("▾", satir_t)                                                           # tek varyant: kırılım toplamla aynı → açılır yok
        self.assertIn("7,000", satir_t)

    def test_durum_rozetleri_yalniz_deger_varsa(self):
        h, y, m = self.kartlar()
        html = self.liste().content.decode()
        satir = html.split(f'<span class="stk-kod">{h.kod}</span>', 1)[1].split("</tr>", 1)[0]
        for yok in ("Kritik", "Eksik", "Gelecek", "Öneri"):
            self.assertNotIn(yok, satir)
        Stok.objects.filter(pk=h.pk).update(kritik_stok=D("50"))
        satir = self.liste().content.decode().split(f'<span class="stk-kod">{h.kod}</span>', 1)[1].split("</tr>", 1)[0]
        self.assertIn("⚠ Kritik", satir)
        self.assertIn("Eksik 40,000", satir)

    def test_yedi_sutun_sabit_cubuk_ve_kart_isaretleri(self):
        self.kartlar()
        html = self.liste().content.decode()
        for ad in ("Stok", "Kategori", "Birim", "Mevcut", "Ayrılan", "Kullanılabilir", "Durum"):
            self.assertIn(f">{ad}", html)
        for eski in ("Bekleyen Tük.", ">Gelecek</th>", ">Öneri</th>", "Üretim Br."):
            self.assertNotIn(eski, html)
        self.assertIn('id="stk-sabit"', html)                                                    # sabit sekme/süzgeç çubuğu
        self.assertIn("position:sticky", html)
        self.assertIn('data-e="Kullanılabilir"', html)                                           # dar ekran kart etiketi
        self.assertIn("@media (max-width: 760px)", html)
        self.assertNotIn('<div class="tablo-kapsayici', html)                                    # yatay kaydırma kapsayıcısı yok (sticky başlık için)
        self.assertIn('data-href="', html)                                                       # tıklanabilir satır deseni korunur
        self.assertIn('class="satir-bag stk-ad"', html)

    def test_bos_durum_mesaji(self):
        self.assertContains(self.liste(), "Henüz stok yok")
        self.kartlar()
        self.assertContains(self.liste(ara="yokboylebir"), "Bu süzgeçle eşleşen stok yok")


class SorguSayisiTest(StokListeTaban):
    def sorgu(self, **q):
        with CaptureQueriesContext(connection) as c:
            self.assertEqual(self.liste(**q).status_code, 200)
        return len(c)

    def test_sorgu_sayisi_kart_sayisindan_bagimsiz(self):
        self.kartlar()
        kucuk = self.sorgu()
        for i in range(40):
            k = self.kart(f"dolgu {i}", varyantli=False, kategori=self.alt_h)
            self.giris(k, "3", "30", tarih=tr_bugun())
        buyuk = self.sorgu()
        self.assertEqual(kucuk, buyuk, f"{kucuk} → {buyuk}")
        self.assertLess(buyuk, 40, f"{buyuk} sorgu")
        self.assertLessEqual(self.sorgu(sifir="1", hareketli="1", boyut="25", sirala="mevcut", yon="desc"), buyuk + 2)
