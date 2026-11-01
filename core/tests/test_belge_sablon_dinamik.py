"""Belge Şablonları dinamik yapı (2026-11-01): yeni şablon ekleme (ad/tür/sıra/aktif/imza/metin), 4 sabit şablonun korunması, silme yok + pasife alma,
yeni yer tutucu adları ({tckn}, {firma_kep}…), sözleşme her zaman ilk, paket PDF sayfa sayısı, "Şablondan üret" listesi ve «özlük belgelerine kaydet»
(imzasız basım işareti: kontrol listesi "Yüklü" saymaz)."""
from contextlib import contextmanager
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from weasyprint.document import Document

from core.models import BelgeSablonu, PersonelBelge
from core.services import belge_sablon as bs
from core.services import ise_giris as ig
from core.services import personel_belge as pb
from core.services.firma import firma_bilgisi_getir, firma_bilgisi_kaydet
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, yuklenen
from core.tests.test_ise_giris_paket import dolu_firma, dolu_personel, kullanici


@contextmanager
def sayfa_yakala():
    """İçerideki ilk PDF üretiminin sayfa sayısını listeye yazar (WeasyPrint PDF'i nesne akışıyla sıkıştırdığı için bayttan sayılamaz)."""
    sayilar = []
    gercek = Document.write_pdf

    def sarmal(self, *a, **kw):
        sayilar.append(len(self.pages))
        return gercek(self, *a, **kw)
    with mock.patch.object(Document, "write_pdf", sarmal):
        yield sayilar


KISA = "{ad_soyad} adlı personele teslim edilmiştir."


class SablonServisTest(TestCase):
    def test_sabit_dort_sablon_icerigiyle_korunur(self):
        liste = bs.hazirla()
        self.assertEqual([s.kod for s in liste], ["SOZLESME", "KVKK_AYDINLATMA", "KVKK_ACIK_RIZA", "FAZLA_CALISMA"])
        for s in liste:
            v = bs.VARSAYILAN[s.kod]
            self.assertEqual((s.baslik, s.metin.strip(), s.sira, s.aktif), (v["baslik"], v["metin"].strip(), v["sira"], True))
        self.assertEqual([(s.tur, s.imza) for s in liste], [("SOZLESME", "iki_taraf"), ("KVKK", "isci"), ("KVKK", "isci"), ("FAZLA_MESAI_ONAYI", "iki_taraf")])

    def test_olustur_kod_sira_ve_dogrulama(self):
        a = bs.olustur(baslik="  zimmet   tutanağı ", metin=KISA, tur="ZIMMET", imza="iki_taraf", imza_notu="Teslim eden / Teslim alan")
        b = bs.olustur(baslik="KKD teslim", metin=KISA, tur="KKD_TESLIM", sira=2, aktif=False)
        self.assertEqual((a.kod, b.kod), ("OZEL_0001", "OZEL_0002"))
        self.assertEqual((a.baslik, a.sira, a.aktif, a.tur, a.imza), ("zimmet tutanağı", 5, True, "ZIMMET", "iki_taraf"))      # 4 sabitten sonra
        self.assertEqual((b.sira, b.aktif), (2, False))
        self.assertEqual(BelgeSablonu.objects.filter(silindi=False).count(), 6)
        for ek in ({"baslik": " "}, {"metin": ""}, {"tur": "SACMA"}, {"imza": "sacma"}, {"metin": "{adsoyad}"}, {"imza_notu": "x" * 201}):
            with self.assertRaises(bs.SablonHatasi, msg=ek):
                bs.olustur(**{"baslik": "b", "metin": KISA, "tur": "DIGER", **ek})
        self.assertEqual(BelgeSablonu.objects.filter(silindi=False).count(), 6)

    def test_pasife_alma_ve_gorunum_listesi(self):
        ozel = bs.olustur(baslik="İç düzen teslim", metin=KISA, tur="IC_DUZEN_TESLIM")
        self.assertIn(ozel.kod, [s["kod"] for s in bs.gorunum_listesi() if s["aktif"]])
        bs.kaydet(ozel, baslik=ozel.baslik, metin=ozel.metin, aktif=False)
        self.assertNotIn(ozel.kod, [s["kod"] for s in bs.gorunum_listesi() if s["aktif"]])
        self.assertIn(ozel.kod, [s["kod"] for s in bs.gorunum_listesi()])                    # silinmez, pasif olarak listede kalır
        s = bs.getir("SOZLESME")
        bs.kaydet(s, baslik=s.baslik, metin=s.metin, aktif=True)
        self.assertEqual((s.tur, s.imza), ("SOZLESME", "iki_taraf"))                          # tur/imza verilmeyince korunur

    def test_varsayilana_don_yalniz_sabit(self):
        ozel = bs.olustur(baslik="x", metin=KISA, tur="DIGER")
        with self.assertRaises(bs.SablonHatasi):
            bs.varsayilana_don(ozel)

    def test_sozlesme_her_zaman_ilk(self):
        bs.hazirla()
        ozel = bs.olustur(baslik="en başa", metin=KISA, tur="ZIMMET", sira=0)
        sirali = bs.paket_sirala(BelgeSablonu.objects.filter(silindi=False))
        self.assertEqual(sirali[0].kod, "SOZLESME")
        self.assertEqual(sirali[1].kod, ozel.kod)                                            # sıra 0 olan, sözleşmeden sonra ama diğerlerinden önce
        self.assertEqual(bs.gorunum_listesi()[0]["kod"], "SOZLESME")

    def test_imza_etiketleri(self):
        self.assertEqual(bs.imza_etiketleri("Teslim eden / Teslim alan"), ("TESLİM EDEN", "TESLİM ALAN"))
        self.assertIsNone(bs.imza_etiketleri("Metni okudum, anladım, bir nüshasını teslim aldım."))
        self.assertIsNone(bs.imza_etiketleri("Bu çok uzun bir beyan metnidir ve etiket değildir / ikinci kısım"))
        self.assertIsNone(bs.imza_etiketleri(""))


class YerTutucuTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = dolu_personel()
        firma_bilgisi_kaydet(unvan="semta a.ş.", adres="organize sanayi\nkayseri", vergi_no="1234567890", kep_adresi=" SEMTA@hs01.KEP.tr ")
        self.firma = firma_bilgisi_getir()

    def test_firma_kep_kaydi_ve_korunmasi(self):
        self.assertEqual(self.firma.kep_adresi, "semta@hs01.kep.tr")
        firma_bilgisi_kaydet(unvan="semta a.ş.")                                             # kep verilmedi → korunur
        self.assertEqual(firma_bilgisi_getir().kep_adresi, "semta@hs01.kep.tr")

    def test_yeni_adlar_eskileriyle_ayni_degeri_basar(self):
        d = ig.yer_tutucu_degerleri(self.p, self.firma, ig.kayit_oku(self.p), bugun=date(2026, 11, 1))
        metin = ("{tckn}|{ise_giris_tarihi}|{gorev_unvani}|{departman}|{firma_adres}|{firma_kep}|{firma_vergi_no}|{personel_adres}|{personel_tel}"
                 "||{tc}|{ise_giris}|{gorev}|{adres}|{telefon}")
        cikti = bs.doldur(metin, d)
        yeni, eski = cikti.split("||")
        y = yeni.split("|")
        self.assertEqual(y[0], "10000000146")
        self.assertEqual(y[1], "02.11.2026")
        self.assertEqual((y[2], y[3]), (self.p.gorev, self.p.departman))
        self.assertTrue(y[2] and y[3])
        self.assertEqual((y[4], y[5], y[6]), (" ".join(self.firma.adres.split()), "semta@hs01.kep.tr", "1234567890"))
        self.assertEqual(y[7], " ".join(self.p.adres.split()))
        self.assertTrue(y[4] and y[7])
        self.assertEqual(y[8], self.p.telefon)
        self.assertTrue(y[8])
        self.assertEqual(eski.split("|")[:3], [y[0], y[1], y[2]])                            # eski adlar çalışmaya devam eder
        self.assertEqual(eski.split("|")[3:], [y[7], y[8]])

    def test_yeni_adlar_listede_ve_kayitta_kabul_edilir(self):
        for ad in ("tckn", "ise_giris_tarihi", "gorev_unvani", "departman", "firma_adres", "firma_kep", "firma_vergi_no", "personel_adres", "personel_tel",
                   "tc", "ise_giris", "gorev", "adres", "telefon"):
            self.assertIn(ad, bs.YER_TUTUCULAR, ad)
        self.assertIn("{tc} ile aynı", bs.YER_TUTUCULAR["tckn"])
        bs.olustur(baslik="x", metin="{tckn} {firma_kep} {personel_tel}", tur="DIGER")

    def test_kep_bos_ise_noktali_cizgi(self):
        firma_bilgisi_kaydet(unvan="semta a.ş.", kep_adresi="")
        d = ig.yer_tutucu_degerleri(self.p, firma_bilgisi_getir(), ig.kayit_oku(self.p), bugun=date(2026, 11, 1))
        self.assertEqual(bs.doldur("{firma_kep}", d), bs.BOS)


class PaketVeKaydetTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("dsyon", password="x")
        self.belgeci = kullanici("dsbelgeci", "personel", "personel_belgeleri")
        self.p = dolu_personel()
        dolu_firma()
        self.url = reverse("core:ise_giris_paket_pdf", args=[self.p.pk])
        self.zimmet = bs.olustur(baslik="Zimmet tutanağı", metin=KISA, tur="ZIMMET", imza="iki_taraf", imza_notu="Teslim eden / Teslim alan")
        self.kkd = bs.olustur(baslik="KKD teslim tutanağı", metin=KISA, tur="KKD_TESLIM", imza="isci", imza_notu="Teslim aldım.")

    def post(self, belgeler, **ek):
        self.client.force_login(self.belgeci)
        return self.client.post(self.url, {"belge": belgeler, **ek})

    def test_paket_sayfa_sayisi_her_belge_yeni_sayfa(self):
        with sayfa_yakala() as iki:
            r = self.post([self.zimmet.kod, self.kkd.kod])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertEqual(iki, [2])                                                            # iki kısa belge = iki sayfa (her biri yeni sayfadan)
        with sayfa_yakala() as tek:
            self.post([self.kkd.kod])
        self.assertEqual(tek, [1])
        with sayfa_yakala() as hepsi:
            self.post(["SOZLESME", self.zimmet.kod, self.kkd.kod])
        self.assertGreater(hepsi[0], 3)                                                       # sözleşme birkaç sayfa + 2 belge
        with sayfa_yakala() as sozlesme:
            self.post(["SOZLESME"])
        self.assertEqual(hepsi[0], sozlesme[0] + 2)                                           # eklenen her belge tam 1 sayfa ekler

    def test_paket_siralamasi_sozlesme_basta_ve_pasif_basilmaz(self):
        bs.kaydet(self.kkd, baslik=self.kkd.baslik, metin=self.kkd.metin, aktif=False, sira=0)
        bs.kaydet(self.zimmet, baslik=self.zimmet.baslik, metin=self.zimmet.metin, sira=0)
        gelen = {}
        from unittest import mock
        gercek = render_to_string

        def sahte(ad, ctx=None, *a, **kw):
            if ad == "core/ise_giris_paket_pdf.html":
                gelen.setdefault("liste", [b["kod"] for b in ctx["belgeler"]])
            return gercek(ad, ctx, *a, **kw)
        with mock.patch("core.views.render_to_string", side_effect=sahte):
            r = self.post(["SOZLESME", self.zimmet.kod, self.kkd.kod])                        # pasif şablon seçilse de basılmaz
        self.assertEqual(r.status_code, 200)
        self.assertEqual(gelen["liste"], ["SOZLESME", self.zimmet.kod])

    def test_imza_etiketleri_pdf_html(self):
        d = ig.paket_hazirla(self.p, firma_bilgisi_getir(), ig.kayit_oku(self.p), [self.zimmet, self.kkd])
        self.assertEqual((d[0]["sol_rol"], d[0]["sag_rol"], d[0]["imza_notu"]), ("TESLİM EDEN", "TESLİM ALAN", ""))
        self.assertEqual((d[1]["sol_rol"], d[1]["sag_rol"], d[1]["imza_notu"]), ("İŞVEREN", "İŞÇİ", "Teslim aldım."))
        html = render_to_string("core/ise_giris_paket_pdf.html", {"firma": firma_bilgisi_getir(), "logo_b64": None, "belgeler": d, "belge_tarihi": date(2026, 11, 1)})
        self.assertIn("TESLİM EDEN", html)
        self.assertIn("TESLİM ALAN", html)
        self.assertIn("Teslim aldım.", html)
        yok = bs.olustur(baslik="imzasız", metin=KISA, tur="DIGER", imza="yok")
        h2 = render_to_string("core/ise_giris_paket_pdf.html", {"firma": firma_bilgisi_getir(), "logo_b64": None, "belge_tarihi": date(2026, 11, 1),
                                                                "belgeler": ig.paket_hazirla(self.p, firma_bilgisi_getir(), ig.kayit_oku(self.p), [yok])})
        self.assertNotIn('class="imza"', h2)

    def test_kaydet_kapali_ise_belge_olusmaz(self):
        self.post([self.zimmet.kod, self.kkd.kod])
        self.assertEqual(PersonelBelge.objects.count(), 0)

    def test_kaydet_acik_her_sablon_ayri_pdf_sablon_turuyle(self):
        r = self.post(["SOZLESME", self.zimmet.kod, self.kkd.kod], kaydet="1")
        self.assertEqual(r.status_code, 200)
        belgeler = PersonelBelge.objects.filter(personel=self.p).order_by("id")
        self.assertEqual([b.tur for b in belgeler], ["SOZLESME", "ZIMMET", "KKD_TESLIM"])
        for b in belgeler:
            self.assertTrue(b.imzasiz)
            self.assertIn("imzasız basım", b.aciklama)
            self.assertTrue(b.orijinal_ad.endswith(".pdf"))
            with b.dosya.open("rb") as f:
                self.assertTrue(f.read(5).startswith(b"%PDF"))
        self.assertEqual(len(self.ozel_dosyalar()), 3)

    def test_imzasiz_kayit_kontrol_listesinde_yuklu_sayilmaz(self):
        self.post(["SOZLESME"], kaydet="1")
        sat = lambda: next(s for s in ig.kontrol_listesi(self.p)["satirlar"] if s["tur"] == "SOZLESME")
        self.assertEqual(sat()["durum"], "EKSIK")                                             # yalnız imzasız basım var
        pb.belge_ekle(self.p, tur="SOZLESME", dosya=yuklenen("imzali.pdf", PDF_BAYT))      # imzalı tarama normal kayıt
        self.assertEqual(sat()["durum"], "YUKLU")

    def test_imzasiz_kayit_imzali_belgeyi_eski_yapmaz(self):
        bitis = date.today() + timedelta(days=200)
        pb.belge_ekle(self.p, tur="ZIMMET", dosya=yuklenen("imzali.pdf", PDF_BAYT), bitis_tarihi=bitis)
        self.post([self.zimmet.kod], kaydet="1")                                              # daha yeni, süresiz imzasız kayıt
        durumlar = {b.imzasiz: d for b, d, _ in pb.belge_durumlari(self.p)}
        self.assertEqual(durumlar[False], "GECERLI")
        self.assertEqual(pb.suresi_dolacaklar(gun=365)[0].belge.imzasiz, False)

    def test_donus_parametresi_ve_yetki(self):
        self.client.force_login(self.belgeci)
        r = self.client.post(self.url, {"donus": "belgeler"})                                  # belge seçilmedi
        self.assertRedirects(r, reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=belgeler", fetch_redirect_response=False)
        r = self.client.post(self.url, {"donus": "http://kotu.example"})
        self.assertRedirects(r, reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=ise_giris", fetch_redirect_response=False)
        self.client.force_login(kullanici("dssade", "personel"))
        self.assertEqual(self.client.post(self.url, {"belge": [self.zimmet.kod], "kaydet": "1"}).status_code, 403)
        self.assertEqual(PersonelBelge.objects.count(), 0)


class SablonEkranTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.yon = User.objects.create_superuser("seyon", password="x")
        self.belgeci = kullanici("sebelgeci", "personel", "personel_belgeleri")
        self.p = dolu_personel()

    def form(self, **ek):
        return {"baslik": "ZİMMET TUTANAĞI", "tur": "ZIMMET", "sira": "", "aktif": "on", "imza": "iki_taraf",
                "imza_notu": "Teslim eden / Teslim alan", "metin": KISA, **ek}

    def test_yeni_sablon_ekrani_yalniz_yonetici(self):
        url = reverse("core:belge_sablonu_ekle")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.belgeci)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.form()).status_code, 403)
        self.assertEqual(BelgeSablonu.objects.count(), 0)

    def test_yeni_sablon_ekle_liste_duzenle_pasife_al(self):
        self.client.force_login(self.yon)
        liste = self.client.get(reverse("core:belge_sablonlari"))
        self.assertContains(liste, "+ Yeni Şablon")
        self.assertContains(liste, "{tckn}")
        self.assertContains(liste, "{firma_kep}")
        form = self.client.get(reverse("core:belge_sablonu_ekle"))
        for parca in ("İmza düzeni", "İmza notu", "Belge türü", "Zimmet Tutanağı", "İç Düzen Yönetmeliği Teslim Tutanağı", "İki taraf", "Yalnız işçi", "İmza alanı yok",
                      "{personel_tel}"):
            self.assertContains(form, parca)
        r = self.client.post(reverse("core:belge_sablonu_ekle"), self.form())
        self.assertRedirects(r, reverse("core:belge_sablonlari"))
        s = BelgeSablonu.objects.get(kod="OZEL_0001")
        self.assertEqual((s.tur, s.imza, s.imza_notu, s.sira, s.aktif), ("ZIMMET", "iki_taraf", "Teslim eden / Teslim alan", 5, True))
        liste = self.client.get(reverse("core:belge_sablonlari"))
        self.assertContains(liste, "ZİMMET TUTANAĞI")
        self.assertContains(liste, reverse("core:belge_sablonu_varsayilan", args=[BelgeSablonu.objects.get(kod="SOZLESME").pk]))
        self.assertNotContains(liste, reverse("core:belge_sablonu_varsayilan", args=[s.pk]))   # eklenen şablonda «Varsayılana dön» yok
        self.assertNotContains(liste, "🗑️")                                                     # silme düğmesi yok
        # hatalı yer tutucu / tür
        r = self.client.post(reverse("core:belge_sablonu_ekle"), self.form(metin="{yanlis_ad}"))
        self.assertContains(r, "Tanımsız yer tutucu")
        r = self.client.post(reverse("core:belge_sablonu_ekle"), self.form(tur="SACMA"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(BelgeSablonu.objects.filter(kod__startswith="OZEL_").count(), 1)
        # düzenle → pasife al
        url = reverse("core:belge_sablonu_duzenle", args=[s.pk])
        self.assertContains(self.client.get(url), "Teslim eden / Teslim alan")
        self.client.post(url, self.form(aktif="", baslik="ZİMMET (PASİF)", sira="9"))
        s.refresh_from_db()
        self.assertEqual((s.aktif, s.baslik, s.sira), (False, "ZİMMET (PASİF)", 9))
        self.assertFalse(BelgeSablonu.objects.get(pk=s.pk).silindi)
        # varsayılana dön: eklenen şablonda reddedilir, metin değişmez
        self.client.post(reverse("core:belge_sablonu_varsayilan", args=[s.pk]))
        s.refresh_from_db()
        self.assertEqual(s.metin, KISA)

    def test_sabit_sablon_icerigi_duzenleme_ekraninda_degismeden_gelir(self):
        self.client.force_login(self.yon)
        s = bs.getir("KVKK_ACIK_RIZA")
        r = self.client.get(reverse("core:belge_sablonu_duzenle", args=[s.pk]))
        self.assertContains(r, "KİŞİSEL VERİLERİN İŞLENMESİNE İLİŞKİN AÇIK RIZA METNİ")
        self.assertEqual(r.context["form"].initial["tur"], "KVKK")

    def test_personel_belgeler_sekmesi_sablondan_uret(self):
        bs.olustur(baslik="Zimmet tutanağı", metin=KISA, tur="ZIMMET")
        pasif = bs.olustur(baslik="Gizli pasif şablon", metin=KISA, tur="DIGER", aktif=False)
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=belgeler")
        self.assertContains(r, "Şablondan Üret")
        self.assertContains(r, "Zimmet tutanağı")
        self.assertContains(r, "BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ")
        self.assertNotContains(r, pasif.baslik)
        self.assertContains(r, 'name="belge" value="OZEL_0001"')
        self.assertContains(r, 'name="donus" value="belgeler"')
        self.assertContains(r, "Özlük belgelerine de kaydet")

    def test_belgeler_sekmesi_get_sablon_olusturmaz(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=belgeler")
        self.assertContains(r, "BELİRSİZ SÜRELİ İŞ SÖZLEŞMESİ")                              # kayıt yokken sabit şablonlar varsayılanla listelenir
        self.assertEqual(BelgeSablonu.objects.count(), 0)

    def test_imzasiz_rozeti_belge_listesinde(self):
        pb.belge_ekle(self.p, tur="ZIMMET", dosya=yuklenen("a.pdf", PDF_BAYT), imzasiz=True, aciklama="x (imzasız basım)")
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]) + "?sekme=belgeler")
        self.assertContains(r, "İmzasız basım")

    def test_firma_bilgileri_ekraninda_kep(self):
        self.client.force_login(self.yon)
        self.assertContains(self.client.get(reverse("core:firma_bilgileri")), "KEP Adresi")


class VeriMigrationTest(TestCase):
    def test_sabit_dort_sablonun_yeni_alanlari_dolar_metin_degismez(self):
        import importlib
        from django.apps import apps
        mig = importlib.import_module("core.migrations.0252_belge_sablon_sabit_dort_veri")
        for kod, baslik in (('SOZLESME', 'S'), ('KVKK_AYDINLATMA', 'K1'), ('KVKK_ACIK_RIZA', 'K2'), ('FAZLA_CALISMA', 'F')):
            BelgeSablonu.objects.create(kod=kod, baslik=baslik, metin='eski metin ' + kod, sira=1)       # tur/imza varsayılanda (DIGER / isci)
        ozel = BelgeSablonu.objects.create(kod='OZEL_0001', baslik='x', metin='m', sira=9)
        mig.doldur(apps, None)
        sonuc = {s.kod: (s.tur, s.imza, s.baslik, s.metin) for s in BelgeSablonu.objects.all()}
        self.assertEqual(sonuc['SOZLESME'][:2], ('SOZLESME', 'iki_taraf'))
        self.assertEqual(sonuc['KVKK_AYDINLATMA'][:2], ('KVKK', 'isci'))
        self.assertEqual(sonuc['KVKK_ACIK_RIZA'][:2], ('KVKK', 'isci'))
        self.assertEqual(sonuc['FAZLA_CALISMA'][:2], ('FAZLA_MESAI_ONAYI', 'iki_taraf'))
        self.assertEqual(sonuc['SOZLESME'][2:], ('S', 'eski metin SOZLESME'))                          # içerik aynen korunur
        self.assertEqual(sonuc['OZEL_0001'][:2], ('DIGER', 'isci'))                                    # sabit olmayana dokunulmaz
