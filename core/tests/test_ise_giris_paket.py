"""İşe giriş Dilim 2: evrak paketi (iş sözleşmesi, KVKK, fazla çalışma muvafakatnamesi) — yer tutucu değerleri, eksik bilgi uyarıları, paket bağlamı,
A4 PDF çıktısı, basım zamanı, yetki (ücret yalnız ücret yetkisi olana), GET yazmaz."""
import re
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from core.models import BelgeSablonu, EkranYetki, IkAyari, IseGirisKaydi
from core.services import belge_sablon as bs
from core.services import ise_giris as ig
from core.services.firma import firma_bilgisi_getir, firma_bilgisi_kaydet
from core.services.personel_ucret import ucret_ekle
from core.tarih import tr_bugun
from core.tests.ik_yardimci import OzelDizinTestTemel, personel_kur


def kullanici(ad, *kodlar):
    u = User.objects.create_user(ad, password="x")
    for k in kodlar:
        EkranYetki.objects.create(kullanici=u, ekran_kod=k)
    return u


def dolu_personel(**kw):
    veri = dict(ad="ayşe", soyad="demir", tc_kimlik_no="10000000146", dogum_tarihi=date(1995, 4, 12), dogum_yeri="kayseri", baba_adi="hasan",
                ana_adi="fatma", adres="organize sanayi 5. cad.\nno:7 kayseri", telefon="05321112233", gorev="kaynakçı", departman="imalat",
                iban="TR330006100519786457841326", ise_giris_tarihi=date(2026, 11, 2))
    veri.update(kw)
    return personel_kur(**veri)


def dolu_firma():
    firma_bilgisi_kaydet(unvan="semta alüminyum a.ş.", adres="organize sanayi\nkayseri", vergi_dairesi="kayseri", vergi_no="1234567890",
                         isveren_vekili_adi="nuri özer", isveren_vekili_unvani="yönetim kurulu başkanı", mersis_no="0123456789012345",
                         sgk_isyeri_sicil_no="1.2345.67")
    return firma_bilgisi_getir()


class DegerlerTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = dolu_personel()
        self.firma = dolu_firma()
        ig.kayit_kaydet(self.p, deneme_suresi_ay="2", haftalik_calisma_saati="45", calisma_gunleri="Pazartesi – Cuma",
                        calisma_saatleri="08:00 – 17:30", ucret_odeme="Her ayın 5'i")

    def test_ucret_metni(self):
        self.assertEqual(ig.ucret_metni(self.p, bugun=date(2026, 10, 30)), "")                  # kayıt yok
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 11, 2), tip="NET", net_tutar=Decimal("40000"))
        self.assertEqual(ig.ucret_metni(self.p, bugun=date(2026, 10, 30)), "40.000,00 TL")       # işe giriş ileride: o gün başlayan kayıt alınır
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 12, 1), tip="ASGARI")
        self.assertEqual(ig.ucret_metni(self.p, bugun=date(2026, 11, 20)), "40.000,00 TL")
        self.assertEqual(ig.ucret_metni(self.p, bugun=date(2026, 12, 5)), "asgari ücret")

    def test_yer_tutucu_degerleri(self):
        v = ig.yer_tutucu_degerleri(self.p, self.firma, ig.kayit_oku(self.p), ucret="40.000,00 TL", bugun=date(2026, 10, 30))
        self.assertEqual((v["ad_soyad"], v["tc"], v["dogum_tarihi"], v["dogum_yeri"]), ("AYŞE DEMİR", "10000000146", "12.04.1995", "KAYSERİ"))
        self.assertEqual((v["baba_adi"], v["ana_adi"], v["gorev"], v["ise_giris"], v["bugun"]), ("HASAN", "FATMA", "KAYNAKÇI", "02.11.2026", "30.10.2026"))
        self.assertEqual((v["deneme_suresi"], v["haftalik_saat"], v["calisma_gunleri"], v["ucret"]), ("2 ay", "45", "Pazartesi – Cuma", "40.000,00 TL"))
        self.assertEqual((v["firma_unvan"], v["vekil_ad"], v["mersis_no"], v["sgk_sicil_no"]), ("SEMTA ALÜMİNYUM A.Ş.", "NURİ ÖZER", "0123456789012345", "1.2345.67"))
        self.assertNotIn("\n", v["adres"])                                                     # çok satırlı adres tek satıra
        self.assertEqual(v["iban"], "TR33 0006 1005 1978 6457 8413 26")
        self.assertEqual(set(v), set(bs.YER_TUTUCULAR))                                         # tanımlı her yer tutucunun değeri üretiliyor

    def test_eksik_bilgiler(self):
        bos = personel_kur(ad="boş", soyad="kişi")
        eksik = ig.eksik_bilgiler(bos, firma_bilgisi_getir(), ig.kayit_oku(bos), ucret_gorebilir=True)
        alanlar = {(e["nerede"], e["alan"]) for e in eksik}
        for beklenen in (("personel", "TC kimlik no"), ("personel", "Doğum yeri"), ("personel", "IBAN"), ("ucret", "Ücret kaydı")):
            self.assertIn(beklenen, alanlar)
        self.assertFalse([e for e in eksik if e["nerede"] == "sozlesme"])                       # sözleşme bilgileri firma varsayılanından gelir
        a = IkAyari.get()                                                                       # varsayılan da boşsa eksik sayılır
        a.sozlesme_calisma_gunleri, a.sozlesme_calisma_saatleri = "", ""
        a.save()
        eksik = ig.eksik_bilgiler(bos, firma_bilgisi_getir(), ig.kayit_oku(bos), ucret_gorebilir=False)
        self.assertEqual({e["alan"] for e in eksik if e["nerede"] == "sozlesme"}, {"Çalışma günleri", "Günlük çalışma saatleri"})
        tam = ig.eksik_bilgiler(self.p, self.firma, ig.kayit_oku(self.p), ucret_gorebilir=False)
        self.assertEqual(tam, [])                                                              # ücret yetkisi yoksa ücret eksiği listelenmez

    def test_paket_hazirla_sozlesme_nusha_ve_doldurma(self):
        bs.hazirla()
        sablonlar = list(BelgeSablonu.objects.filter(silindi=False).order_by("sira"))
        belgeler = ig.paket_hazirla(self.p, self.firma, ig.kayit_oku(self.p), sablonlar, nusha=2, ucret="40.000,00 TL", bugun=date(2026, 10, 30))
        self.assertEqual([(b["kod"], b["nusha_no"], b["nusha_adet"]) for b in belgeler],
                         [("SOZLESME", 1, 2), ("SOZLESME", 2, 2), ("KVKK_AYDINLATMA", 1, 1), ("KVKK_ACIK_RIZA", 1, 1), ("FAZLA_CALISMA", 1, 1)])
        soz = belgeler[0]
        metin = "\n\n".join(soz["paragraflar"])
        for parca in ("AYŞE DEMİR", "10000000146", "KAYSERİ / 12.04.1995", "2 ay deneme süresi", "40.000,00 TL tutarında ücret", "Pazartesi – Cuma",
                      "NURİ ÖZER (YÖNETİM KURULU BAŞKANI)", "30.10.2026"):
            self.assertIn(parca, metin)
        self.assertIsNone(re.search(r"\{[a-z_0-9]+\}", metin))                                  # doldurulmamış yer tutucu kalmadı
        self.assertEqual([b["imza"] for b in belgeler], ["iki_taraf", "iki_taraf", "isci", "isci", "iki_taraf"])
        self.assertGreater(len(soz["paragraflar"]), 10)                                         # boş satırlarla paragraflara bölündü

    def test_eksik_deger_noktali_cizgi_ve_enjeksiyon_yok(self):
        bos = personel_kur(ad="a{tc}b", soyad="{{ 7*7 }}")
        bs.hazirla()
        belgeler = ig.paket_hazirla(bos, firma_bilgisi_getir(), ig.kayit_oku(bos), [bs.getir("FAZLA_CALISMA")])
        metin = "\n".join(belgeler[0]["paragraflar"])
        self.assertIn("A{TC}B {{ 7*7 }}", metin)                                                # personel adındaki süslü parantez yeniden işlenmez
        self.assertIn(bs.BOS, metin)                                                            # TC/görev boş → noktalı çizgi


class PdfSablonuTest(OzelDizinTestTemel):
    def render(self, nusha=2):
        p, firma = dolu_personel(), dolu_firma()
        bs.hazirla()
        sablonlar = list(BelgeSablonu.objects.filter(silindi=False).order_by("sira"))
        belgeler = ig.paket_hazirla(p, firma, ig.kayit_oku(p), sablonlar, nusha=nusha, bugun=date(2026, 10, 30))
        return render_to_string("core/ise_giris_paket_pdf.html", {"firma": firma, "logo_b64": None, "belgeler": belgeler,
                                                                 "belge_tarihi": date(2026, 10, 30), "personel": p}), belgeler

    def test_a4_siyah_beyaz_imza_alanlari(self):
        html, belgeler = self.render()
        self.assertIn("size: A4 portrait", html)
        self.assertIn("counter(pages)", html)
        self.assertNotRegex(re.search(r"<style>.*?</style>", html, re.S).group(0), r"#(?!000\b)[0-9a-fA-F]{3,6}\b")      # siyah-beyaz: #000 dışında renk yok
        self.assertEqual(html.count('class="belge'), len(belgeler))
        self.assertEqual(html.count("İŞVEREN</div>"), 3)                                         # sözleşme ×2 + fazla çalışma
        self.assertEqual(html.count("İŞÇİ</div>"), 5)
        self.assertEqual(html.count("Nüsha 1 / 2") + html.count("Nüsha 2 / 2"), 2)
        self.assertIn("Bu sözleşmeyi okudum", html)
        self.assertIn("Açık rızamı özgür irademle veriyorum.", html)
        for baslik in ("BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ", "MUVAFAKATNAMESİ"):
            self.assertIn(baslik, html)

    def test_tek_nusha(self):
        html, belgeler = self.render(nusha=1)
        self.assertEqual(len(belgeler), 4)
        self.assertNotIn("Nüsha", html)


class PaketGorunumTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("pkyon", password="x")
        self.belgeci = kullanici("pkbelgeci", "personel", "personel_belgeleri")
        self.ucretli = kullanici("pkucretli", "personel", "personel_belgeleri", "personel_ucret")
        self.sade = kullanici("pksade", "personel")
        self.p = dolu_personel()
        dolu_firma()
        self.url = reverse("core:ise_giris_paket_pdf", args=[self.p.pk])
        self.sekme = reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=ise_giris"

    def post(self, kisi=None, **veri):
        self.client.force_login(kisi or self.belgeci)
        veri = {"belge": ["SOZLESME", "KVKK_AYDINLATMA", "KVKK_ACIK_RIZA", "FAZLA_CALISMA"], "sozlesme_nusha": "2", **veri}
        return self.client.post(self.url, veri)

    def yakala(self, kisi):
        """POST sırasında PDF şablonuna giden bağlamı yakalar."""
        from django.template.loader import render_to_string as gercek
        yakalanan = {}

        def sahte(ad, ctx=None, *a, **kw):
            if ad == "core/ise_giris_paket_pdf.html":
                yakalanan.update(ctx)
            return gercek(ad, ctx, *a, **kw)
        with mock.patch("core.views.render_to_string", side_effect=sahte):
            r = self.post(kisi)
        return r, yakalanan

    def test_pdf_uretilir_basim_zamani_yazilir(self):
        r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("ise-giris-evraklari", r["Content-Disposition"])
        k = IseGirisKaydi.objects.get(personel=self.p)
        self.assertIsNotNone(k.paket_basim_zamani)
        self.assertEqual(k.updated_by, self.belgeci)

    def test_basim_sozlesme_parametrelerini_ve_muhasebe_alanini_bozmaz(self):
        ig.kayit_kaydet(self.p, deneme_suresi_ay="2")
        IseGirisKaydi.objects.filter(personel=self.p).update(muhasebe_gonderim_tarihi=date(2026, 11, 1))
        self.post()
        k = IseGirisKaydi.objects.get(personel=self.p)
        self.assertEqual((k.deneme_suresi_ay, k.muhasebe_gonderim_tarihi), (2, date(2026, 11, 1)))

    def test_get_yazmaz_post_olmadan_pdf_yok(self):
        self.client.force_login(self.belgeci)
        self.assertRedirects(self.client.get(self.url), self.sekme, fetch_redirect_response=False)
        self.client.get(self.sekme)
        self.assertEqual((BelgeSablonu.objects.count(), IseGirisKaydi.objects.count()), (0, 0))   # kart GET'i şablon/kayıt oluşturmaz

    def test_yetki(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {"belge": ["SOZLESME"]}).status_code, 302)
        for kisi in (self.sade, kullanici("pkyalnizbelge", "personel_belgeleri")):               # personel + personel_belgeleri ikisi de gerekli
            self.assertEqual(self.post(kisi).status_code, 403)
        self.assertEqual(self.post(self.yon).status_code, 200)

    def test_secim_bos_pasif_ve_gecersiz(self):
        self.client.force_login(self.belgeci)
        r = self.client.post(self.url, {"sozlesme_nusha": "2"}, follow=True)
        self.assertRedirects(r, self.sekme)
        self.assertContains(r, "Basılacak belge seçilmedi")
        bs.hazirla()
        s = BelgeSablonu.objects.get(kod="KVKK_ACIK_RIZA")
        s.aktif = False
        s.save()
        r, ctx = self.yakala(self.belgeci)
        kodlar = [b["kod"] for b in ctx["belgeler"]]
        self.assertNotIn("KVKK_ACIK_RIZA", kodlar)                                              # pasif şablon POST'ta olsa bile basılmaz
        self.assertEqual(kodlar.count("SOZLESME"), 2)
        r = self.client.post(self.url, {"belge": ["YOK_BOYLE"]}, follow=True)
        self.assertContains(r, "Basılacak belge seçilmedi")

    def test_ucret_yalniz_yetkiliye_basilir(self):
        ucret_ekle(self.p, gecerlilik_baslangic=date(2026, 11, 2), tip="NET", net_tutar=Decimal("40000"))
        _r, ctx = self.yakala(self.ucretli)
        self.assertIn("40.000,00 TL tutarında ücret", "\n".join(ctx["belgeler"][0]["paragraflar"]))
        _r, ctx = self.yakala(self.belgeci)
        metin = "\n".join(ctx["belgeler"][0]["paragraflar"])
        self.assertNotIn("40.000", metin)
        self.assertIn(f"aylık net {bs.BOS} tutarında ücret", metin)

    def test_sablon_duzenlemesi_ciktiya_yansir(self):
        s = bs.getir("FAZLA_CALISMA")
        bs.kaydet(s, baslik="ÖZEL BAŞLIK", metin="Ben {ad_soyad}, özel metin.", aktif=True)
        _r, ctx = self.yakala(self.belgeci)
        b = [x for x in ctx["belgeler"] if x["kod"] == "FAZLA_CALISMA"][0]
        self.assertEqual((b["baslik"], b["paragraflar"]), ("ÖZEL BAŞLIK", ["Ben AYŞE DEMİR, özel metin."]))

    def test_sekme_paket_formu_ve_eksikler(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(self.sekme)
        self.assertContains(r, "Evrak Paketi")
        self.assertContains(r, self.url)
        self.assertContains(r, 'name="belge" value="SOZLESME" checked')
        self.assertContains(r, 'name="sozlesme_nusha"')
        self.assertContains(r, "Ücret görme yetkiniz olmadığı")
        self.assertNotContains(r, "Eksik bilgiler")                                              # kişi/firma dolu; sözleşme bilgileri firma varsayılanından
        self.assertNotContains(r, "Son basım")
        self.post()
        self.assertContains(self.client.get(self.sekme), "Son basım")
        r = self.client.get(self.sekme)
        self.client.force_login(self.ucretli)
        r = self.client.get(self.sekme)
        self.assertNotContains(r, "Ücret görme yetkiniz olmadığı")
        self.assertContains(r, "Ücret kaydı")                                                   # ücret yetkisi var, kayıt yok → eksik

    def test_pasif_sablon_formda_gelmez(self):
        bs.hazirla()
        BelgeSablonu.objects.filter(kod="KVKK_ACIK_RIZA").update(aktif=False)
        self.client.force_login(self.belgeci)
        r = self.client.get(self.sekme)
        self.assertContains(r, 'value="SOZLESME"')
        self.assertNotContains(r, 'value="KVKK_ACIK_RIZA"')

    def test_eski_sekme_ve_yeni_dilim1_ozellikleri_bozulmadi(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(self.sekme)
        self.assertContains(r, "Özlük Kontrol Listesi")
        self.assertContains(r, 'id="sozlesme-bilgileri"')
        self.assertEqual(r.context["aktif_ekran"], "core:personeller")
