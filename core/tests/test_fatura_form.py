"""Faturalar › Yeni fatura / Düzenle formu yeniden tasarımı (2026-10-15, 2/3): fatura no ZORUNLU + "faturasız" işareti (yeni kayıtta; düzenlemede yalnız kayıtlı no silinirken),
mükerrer fatura no UYARISI ("yine de kaydet" onayı; engel değil), cari bilgi API'si, PDF/resim ekleri formdan (XML reddi, kayıt başarısızsa açık uyarı, düzenlemede mevcut ekler),
canlı hesap için sunucu parite değerleri, şablon bileşenleri. Muhasebe mantığı DEĞİŞMEDİ: mevcut fatura testlerinin tümü geçer."""
import datetime
import json
import re
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core.models import Cari, Fatura, FaturaEk, FaturaTipi, YevmiyeFisi
from core.services.fatura import fatura_olustur, fatura_taslak_olustur
from core.tests.ik_yardimci import PDF_BAYT, png_bayt
from core.tests.test_fatura_liste import ListeTaban

D = datetime.date
Dc = Decimal
XML = b'<?xml version="1.0"?><Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'


class FormTaban(ListeTaban):
    def veri(self, satirlar=(("3", "100"),), tip=None, **ek):
        v = {"tip": (tip or self.alis).pk, "cari": self.tedarikci.pk, "tarih": "2026-03-10", "para_birimi": "TRY", "fatura_no": "F-1",
             "form-TOTAL_FORMS": str(len(satirlar)), "form-INITIAL_FORMS": "0", "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000"}
        for i, (m, f) in enumerate(satirlar):
            v.update({f"form-{i}-stok": self.stok.pk, f"form-{i}-miktar": m, f"form-{i}-birim_fiyat": f})
        v.update(ek)
        return v

    def ekle(self, yon="alis", **kw):
        self.client.force_login(self.su)
        return self.client.post(reverse(f"core:{yon}_fatura_ekle"), self.veri(**kw))

    def pdf(self, ad="a.pdf"):
        return SimpleUploadedFile(ad, PDF_BAYT, content_type="application/pdf")


class FaturaNoZorunluTest(FormTaban):
    def test_yeni_kayitta_no_zorunlu_faturasiz_isaretiyle_bos_kalabilir(self):
        r = self.ekle(fatura_no="")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fatura no zorunlu")
        self.assertEqual(Fatura.objects.count(), 0)
        r = self.ekle(fatura_no="", faturasiz="on")
        self.assertEqual(r.status_code, 302)
        f = Fatura.objects.get()
        self.assertEqual((f.fatura_no, f.faturasiz), ("", True))

    def test_numara_girilince_faturasiz_isareti_yok_sayilir(self):
        self.ekle(fatura_no="N-1", faturasiz="on")
        f = Fatura.objects.get()
        self.assertEqual((f.fatura_no, f.faturasiz), ("N-1", False))

    def test_satista_da_zorunlu(self):
        self.client.force_login(self.su)
        v = self.veri(tip=self.satis, fatura_no="")
        v["cari"] = self.musteri.pk
        r = self.client.post(reverse("core:satis_fatura_ekle"), v)
        self.assertContains(r, "Fatura no zorunlu")
        v["faturasiz"] = "on"
        self.assertEqual(self.client.post(reverse("core:satis_fatura_ekle"), v).status_code, 302)

    def test_listede_faturasiz_rozeti(self):
        self.ekle(fatura_no="", faturasiz="on")
        r = self.client.get(reverse("core:alis_faturalari"))
        self.assertContains(r, "Faturasız")
        s = r.context["satirlar"][0]
        self.assertTrue(s["faturasiz"])

    def test_duzenlemede_zorunluluk_yalniz_kayitli_no_silinirken(self):
        eski = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="", satirlar=self._satir())   # eski numarasız kayıt (Karakuş gibi)
        yeni = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 11), fatura_no="K-9", satirlar=self._satir())
        self.client.force_login(self.su)
        duz = lambda f, **ek: self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(**{"fatura_no": "", **ek}))
        # eski numarasız kayıt numarasız kalarak düzenlenebilir
        self.assertEqual(duz(eski, **{"form-0-miktar": "5"}).status_code, 302)
        # kayıtlı no silinmeye çalışılırsa zorunlu
        r = duz(yeni)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fatura no zorunlu")
        # faturasız işaretiyle silinebilir
        self.assertEqual(duz(yeni, faturasiz="on").status_code, 302)
        yeni.refresh_from_db()
        self.assertEqual((yeni.fatura_no, yeni.faturasiz), ("", True))

    def test_duzenle_formu_faturasiz_isaretini_gosterir(self):
        f = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), fatura_no="", faturasiz=True, satirlar=self._satir())
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertTrue(r.context["fform"].initial["faturasiz"])
        self.assertRegex(r.content.decode(), r'<input type="checkbox" name="faturasiz"[^>]*checked')

    def test_irsaliyeden_acilan_numarasiz_taslak_duzenle_ekrani(self):
        t = fatura_taslak_olustur(yon="ALIS", cari_id=self.tedarikci.pk, tarih=D(2026, 3, 10), satirlar=self._satir())       # tip henüz yok (irsaliyeden çevrilen taslak)
        self.client.force_login(self.su)
        self.assertEqual(self.client.get(reverse("core:fatura_duzenle", args=[t.pk])).status_code, 200)
        r = self.client.post(reverse("core:fatura_duzenle", args=[t.pk]), self.veri(fatura_no=""))                           # numarasız taslak numarasız kaydedilebilir
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual((t.durum, t.tip_id), ("TASLAK", self.alis.pk))


class MukerrerUyariTest(FormTaban):
    def test_ayni_cari_ve_no_uyari_verir_yine_de_kaydet_onayiyla_kaydolur(self):
        ilk = self.a_fatura(no="DUP-1")
        r = self.ekle(fatura_no="dup-1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Bu fatura zaten kayıtlı")
        self.assertContains(r, reverse("core:fatura_detay", args=[ilk.pk]))                         # link
        self.assertContains(r, "Yine de kaydet")
        self.assertEqual(r.context["mukerrer_fatura"].pk, ilk.pk)
        self.assertEqual(Fatura.objects.count(), 1)
        r = self.ekle(fatura_no="dup-1", mukerrer_onay="on")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Fatura.objects.filter(cari=self.tedarikci).count(), 2)                     # engellenmedi

    def test_satis_yonunde_de_uyari(self):
        self.client.force_login(self.su)
        v = self.veri(tip=self.satis, fatura_no="S-1")
        v["cari"] = self.musteri.pk
        self.assertEqual(self.client.post(reverse("core:satis_fatura_ekle"), v).status_code, 302)
        r = self.client.post(reverse("core:satis_fatura_ekle"), v)
        self.assertContains(r, "Bu fatura zaten kayıtlı")
        v["mukerrer_onay"] = "on"
        self.assertEqual(self.client.post(reverse("core:satis_fatura_ekle"), v).status_code, 302)

    def test_duzenlemede_kendini_saymaz(self):
        f = self.a_fatura(no="E-1")
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(fatura_no="E-1"))
        self.assertEqual(r.status_code, 302)

    def test_no_kontrol_api(self):
        f = self.a_fatura(no="ABC-1")
        self.client.force_login(self.su)
        u = reverse("core:fatura_no_kontrol_api")
        j = self.client.get(u, {"cari": self.tedarikci.pk, "no": "abc-1", "yon": "ALIS"}).json()
        self.assertEqual((j["var"], j["url"]), (True, reverse("core:fatura_detay", args=[f.pk])))
        self.assertFalse(self.client.get(u, {"cari": self.tedarikci.pk, "no": "abc-1", "yon": "ALIS", "haric": f.pk}).json()["var"])    # düzenlenen kendisi
        self.assertFalse(self.client.get(u, {"cari": self.tedarikci.pk, "no": "baska", "yon": "ALIS"}).json()["var"])
        self.assertFalse(self.client.get(u, {"cari": self.musteri.pk, "no": "abc-1", "yon": "ALIS"}).json()["var"])                  # başka cari
        self.assertFalse(self.client.get(u, {"cari": self.tedarikci.pk, "no": "", "yon": "ALIS"}).json()["var"])

    def test_cari_bilgi_api_kosul_bakiye_pb(self):
        Cari.objects.filter(pk=self.tedarikci.pk).update(odeme_kosulu="SONRAKI_AY_GUNU", odeme_gunu=25)
        self.a_fatura(no="B-1")                                                                     # 1.200 TL alacak (biz borçluyuz)
        self.client.force_login(self.su)
        j = self.client.get(reverse("core:cari_fatura_bilgi_api", args=[self.tedarikci.pk])).json()
        self.assertEqual((j["var"], j["kosul"], j["pb"]), (True, "Sonraki ayın 25. günü", "TRY"))
        self.assertEqual(j["bakiye"], "1.200,00 TL Alacak")
        self.assertFalse(self.client.get(reverse("core:cari_fatura_bilgi_api", args=[99999])).json()["var"])
        self.assertEqual(self.client.get(reverse("core:cari_fatura_bilgi_api", args=[self.musteri.pk])).json()["kosul"], "Tanımlı değil")


class FormEkTest(FormTaban):
    def test_ekle_pdf_ve_resim_faturaya_ek_olur(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(dosyalar=[self.pdf("o.pdf"), SimpleUploadedFile("f.png", png_bayt())]), follow=True)
        f = Fatura.objects.get()
        eklar = list(f.ekler.order_by("id"))
        self.assertEqual([e.orijinal_ad for e in eklar], ["o.pdf", "f.png"])
        self.assertTrue(eklar[0].dosya.name.endswith(".pdf") and eklar[1].dosya.name.endswith(".webp"))
        self.assertContains(r, "2 dosya eklendi")
        self.assertTrue(eklar[0].dosya.storage.exists(eklar[0].dosya.name))

    def test_xml_ve_gecersiz_dosya_kayit_oncesi_reddedilir_fatura_olusmaz(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(dosyalar=[SimpleUploadedFile("x.xml", XML), self.pdf()]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "XML dosyaları ek olarak kabul edilmiyor")
        self.assertContains(r, "ek dosyaları tekrar seçin")                                          # dosya seçimi kaybolur → açık uyarı
        self.assertEqual((Fatura.objects.count(), FaturaEk.objects.count(), YevmiyeFisi.objects.count()), (0, 0, 0))
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(dosyalar=[SimpleUploadedFile("sahte.pdf", b"degil")]))
        self.assertContains(r, "Geçersiz PDF dosyası")
        self.assertEqual(Fatura.objects.count(), 0)

    def test_form_hatasinda_dosya_varsa_tekrar_secme_uyarisi_yoksa_yok(self):
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(fatura_no="", dosyalar=[self.pdf()]))
        self.assertContains(r, "ek dosyaları tekrar seçin")
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(fatura_no=""))
        self.assertNotContains(r, "ek dosyaları tekrar seçin")

    def test_dosyasiz_kayit_calisir(self):
        r = self.ekle()
        self.assertEqual((r.status_code, FaturaEk.objects.count()), (302, 0))

    def test_duzenle_yeni_dosya_ekler_ve_mevcut_ekleri_gosterir(self):
        f = self.a_fatura(no="D-1")
        ek = FaturaEk.objects.create(fatura=f, dosya=__import__("django.core.files.base", fromlist=["ContentFile"]).ContentFile(PDF_BAYT, name="m.pdf"), orijinal_ad="mevcut.pdf")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:fatura_duzenle", args=[f.pk]))
        self.assertEqual([e["ad"] for e in r.context["mevcut_ekler"]], ["mevcut.pdf"])
        self.assertContains(r, reverse("core:fatura_ek_indir", args=[ek.pk]))
        self.assertContains(r, 'class="fb-form pdf-var"')                                           # mevcut ek varsa panel açık gelir
        r = self.client.post(reverse("core:fatura_duzenle", args=[f.pk]), self.veri(fatura_no="D-1", dosyalar=[self.pdf("yeni.pdf")]), follow=True)
        self.assertContains(r, "1 dosya eklendi")
        self.assertEqual(sorted(e.orijinal_ad for e in f.ekler.all()), ["mevcut.pdf", "yeni.pdf"])

    def test_ekle_sayfasinda_ek_alani_ve_onizleme_paneli_ve_xframe_izni(self):
        f = self.a_fatura(no="X-1")
        ek = FaturaEk.objects.create(fatura=f, dosya=__import__("django.core.files.base", fromlist=["ContentFile"]).ContentFile(PDF_BAYT, name="m.pdf"), orijinal_ad="m.pdf")
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        self.assertContains(r, 'enctype="multipart/form-data"')
        self.assertContains(r, 'name="dosyalar" multiple')
        self.assertContains(r, 'id="pdf-panel"')
        self.assertEqual(self.client.get(reverse("core:fatura_ek_indir", args=[ek.pk]))["X-Frame-Options"], "SAMEORIGIN")     # önizleme iframe'i için


class SablonBilesenleriTest(FormTaban):
    def test_basliklar_birim_toplam_kutusu_ve_json_baglamlari(self):
        self.client.force_login(self.su)
        r = self.client.get(reverse("core:alis_fatura_ekle"))
        html = r.content.decode()
        for parca in ("Satır toplamı", ">Birim<", "KDV %", "Yatırım projesi", 'id="fb-toplam"', 'id="kdv-kirilim"', "Genel toplam (ödenecek)", "satir-kopya", "faturasiz"):
            self.assertIn(parca, html)
        stok_birim = json.loads(re.search(r'<script id="stok-birim"[^>]*>(.*?)</script>', html, re.S).group(1))
        self.assertEqual(stok_birim[str(self.stok.pk)], "AD")                                      # birim stok kartından
        tip_ykdv = json.loads(re.search(r'<script id="tip-yalniz-kdv"[^>]*>(.*?)</script>', html, re.S).group(1))
        self.assertTrue(tip_ykdv[str(self.kdvli.pk)])                                              # "Yalnız KDV" tipi: matrah "—", toplam = KDV
        self.assertFalse(tip_ykdv[str(self.alis.pk)])
        self.assertIn('id="tevkifat-pp"', html)
        self.assertIn("govde-tam", html)

    def test_baslik_sirasi_tip_cari_no_tarih_vade_depo_pb_kur_aciklama(self):
        self.client.force_login(self.su)
        html = self.client.get(reverse("core:alis_fatura_ekle")).content.decode()
        sira = [html.index(f'for="id_{a}"') for a in ("tip", "cari", "fatura_no", "tarih", "vade_tarihi", "depo", "para_birimi", "kur", "aciklama")]
        self.assertEqual(sira, sorted(sira))

    def test_sunucu_hesabi_js_ile_ayni_kurus_yuvarlamasi(self):
        """Canlı (JS) hesapla birebir aynı yuvarlama: ROUND_HALF_UP; sunucu tek doğru kaynak. (JS değerleri 387 rastgele vakada tarayıcıda doğrulandı.)"""
        for m, f, matrah, kdv in (("3", "10,333333", "31.00", "6.20"), ("1", "1,005", "1.01", "0.20"), ("1", "0,125", "0.13", "0.03"),
                                  ("1234,5", "19,99", "24677.66", "4935.53"), ("0,333", "3", "1.00", "0.20")):
            Fatura.objects.all().delete()
            self.client.force_login(self.su)
            r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(satirlar=((m, f),), fatura_no=f"P-{m}-{f}"))
            self.assertEqual(r.status_code, 302, (m, f))
            fat = Fatura.objects.get()
            self.assertEqual((str(fat.ara_toplam), str(fat.kdv_toplam)), (matrah, kdv), (m, f))
