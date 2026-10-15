"""Faturalar › form yeniden tasarımı 3. tur (2026-10-15): ek silme KAYITLA (onay penceresi, vazgeçilirse ek kalır, onaylı faturada da), sabit kaydet çubuğu, hata özeti + alan vurgusu +
girilenlerin korunması, liste fatura no sıralamasında numarasız/faturasız SONDA (ekran + Excel)."""
import datetime
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.urls import reverse
from openpyxl import load_workbook

from core.models import EkranYetki, Fatura, FaturaEk
from core.services.fatura import fatura_olustur
from core.tests.ik_yardimci import PDF_BAYT
from core.tests.test_fatura_form import FormTaban

D = datetime.date


class EkSilmeKayitlaTest(FormTaban):
    def kur(self):
        self.f = self.a_fatura(no="E-1")                                                          # ONAYLI
        self.ek = FaturaEk.objects.create(fatura=self.f, dosya=ContentFile(PDF_BAYT, name="a.pdf"), orijinal_ad="a.pdf")
        self.ek2 = FaturaEk.objects.create(fatura=self.f, dosya=ContentFile(PDF_BAYT, name="b.pdf"), orijinal_ad="b.pdf")
        self.client.force_login(self.su)
        return reverse("core:fatura_duzenle", args=[self.f.pk])

    def test_silme_yalniz_kaydet_ile_uygulanir_onayli_faturada_da(self):
        u = self.kur()
        self.assertEqual(self.f.durum, "ONAYLI")
        r = self.client.post(u, self.veri(fatura_no="E-1", silinecek_ek=[self.ek.pk]), follow=True)
        self.assertContains(r, "1 ek silindi")
        self.ek.refresh_from_db()
        self.ek2.refresh_from_db()
        self.assertTrue(self.ek.silindi)
        self.assertFalse(self.ek2.silindi)                                                        # işaretlenmeyen ek yerinde

    def test_isaret_yoksa_ek_yerinde_kalir_vazgecme(self):
        u = self.kur()
        self.client.post(u, self.veri(fatura_no="E-1"))                                           # kaydet, silme işareti yok (vazgeçildi)
        self.ek.refresh_from_db()
        self.assertFalse(self.ek.silindi)

    def test_form_hatasinda_ek_silinmez_isaret_korunur(self):
        u = self.kur()
        r = self.client.post(u, self.veri(fatura_no="", silinecek_ek=[self.ek.pk]))               # no zorunlu → hata
        self.assertEqual(r.status_code, 200)
        self.ek.refresh_from_db()
        self.assertFalse(self.ek.silindi)
        self.assertEqual(r.context["silinecek_ek_idleri"], [self.ek.pk])                          # işaret sayfada yeniden kurulur
        self.assertContains(r, f"var SILINECEK = [{self.ek.pk}]")

    def test_baska_faturanin_eki_silinmez(self):
        u = self.kur()
        diger = self.a_fatura(no="E-2", tarih=D(2026, 3, 11))
        yabanci = FaturaEk.objects.create(fatura=diger, dosya=ContentFile(PDF_BAYT, name="y.pdf"), orijinal_ad="y.pdf")
        self.client.post(u, self.veri(fatura_no="E-1", silinecek_ek=[yabanci.pk, "x"]))
        yabanci.refresh_from_db()
        self.assertFalse(yabanci.silindi)

    def test_taslak_faturada_da_calisir_ve_yetki_detaydaki_ile_ayni(self):
        u = self.kur()
        sade = User.objects.create_user("eksade", password="x")
        self.client.force_login(sade)
        self.assertEqual(self.client.post(u, self.veri(fatura_no="E-1", silinecek_ek=[self.ek.pk])).status_code, 403)           # yetkisiz
        EkranYetki.objects.create(kullanici=sade, ekran_kod="alis_faturalari")                      # detaydaki ek silme ile aynı yetki (alış/satış faturaları ekranı)
        self.assertEqual(self.client.post(u, self.veri(fatura_no="E-1", silinecek_ek=[self.ek.pk])).status_code, 302)
        self.ek.refresh_from_db()
        self.assertTrue(self.ek.silindi)

    def test_arayuz_onay_penceresi_ve_anlik_silme_yok(self):
        u = self.kur()
        html = self.client.get(u).content.decode()
        self.assertIn("Bu ek kalıcı olarak silinecek", html)
        self.assertIn('id="silinecek-kap"', html)
        self.assertNotIn("fetch(o.sil", html)                                                      # artık tıklayınca anında silinmez


class KaydetCubuguVeHataOzetiTest(FormTaban):
    def test_sabit_kaydet_cubugu(self):
        self.client.force_login(self.su)
        html = self.client.get(reverse("core:alis_fatura_ekle")).content.decode()
        for parca in ('id="fb-altbar"', 'id="bar-genel"', 'id="bar-tl"', "Geri / İptal", 'id="kaydet-dugme"', "position:sticky; bottom:.5rem", "max-width:560px"):
            self.assertIn(parca, html)
        self.assertNotIn('class="form-altbar"', html)
        self.assertNotIn("Taslak kaydet", html)                                                    # formda taslak akışı yok (mevcut davranış korunur)

    def test_hata_ozeti_alan_vurgusu_ve_girilenler_korunur(self):
        self.client.force_login(self.su)
        v = self.veri(fatura_no="", aciklama="BU AÇIKLAMA KORUNMALI", satirlar=(("3", "100"), ("7", "55,5")))
        v["form-1-miktar"] = "abc"                                                                  # ikinci kalemde geçersiz sayı
        r = self.client.post(reverse("core:alis_fatura_ekle"), v)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('id="hata-ozeti"', html)
        self.assertIn('href="#id_fatura_no"', html)                                                 # özet → ilgili alana bağlantı
        self.assertIn("Fatura no zorunlu", html)
        self.assertIn("Kalem 2", html)
        self.assertIn('href="#satir-1"', html)
        self.assertRegex(html, r'class="fb-alan[^"]*hatali')                                         # alan kırmızı vurgu
        self.assertIn("satir-hata", html)                                                           # hatalı satır
        self.assertEqual(Fatura.objects.count(), 0)
        # girilenler kaybolmaz
        self.assertIn("BU AÇIKLAMA KORUNMALI", html)
        self.assertIn("55,5", html)
        self.assertEqual(r.context["formset"].total_form_count(), 2)
        self.assertEqual(r.context["fform"]["tip"].value(), str(self.alis.pk))
        self.assertIn("ozet.scrollIntoView", html)                                                  # sayfa özete kayar

    def test_form_gecerli_satir_hatali_ise_de_ozet_gosterir(self):
        self.client.force_login(self.su)
        v = self.veri()
        v["form-0-miktar"] = "0"
        r = self.client.post(reverse("core:alis_fatura_ekle"), v)
        self.assertContains(r, 'id="hata-ozeti"')
        self.assertContains(r, "Miktar sıfırdan büyük olmalı")

    def test_hata_yoksa_ozet_yok(self):
        self.client.force_login(self.su)
        html = self.client.get(reverse("core:alis_fatura_ekle")).content.decode()
        self.assertNotIn('id="hata-ozeti"', html)

    def test_dosya_tekrar_sec_uyarisi_ozetle_birlikte(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.su)
        r = self.client.post(reverse("core:alis_fatura_ekle"), self.veri(fatura_no="", dosyalar=[SimpleUploadedFile("a.pdf", PDF_BAYT)]))
        self.assertContains(r, 'id="hata-ozeti"')
        self.assertContains(r, "ek dosyaları tekrar seçin")


class ListeFaturaNoSiralamaTest(FormTaban):
    def test_numarasiz_ve_faturasiz_iki_yonde_de_sonda_excel_ayni_sira(self):
        a = self.a_fatura(no="B-2", tarih=D(2026, 3, 10))
        b = self.a_fatura(no="A-9", tarih=D(2026, 3, 11))
        c = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 12), fatura_no="", satirlar=self._satir())                       # eski numarasız
        d = fatura_olustur(tip_id=self.alis.pk, cari_id=self.tedarikci.pk, tarih=D(2026, 3, 13), fatura_no="", faturasiz=True, satirlar=self._satir())        # faturasız
        e = self.a_fatura(no="C-1", tarih=D(2026, 3, 9))
        self.client.force_login(self.su)
        sira = lambda **q: [s["pk"] for s in self.client.get(reverse("core:alis_faturalari"), q).context["satirlar"]]
        self.assertEqual(sira(sirala="no", dir="asc"), [b.pk, a.pk, e.pk, d.pk, c.pk])             # A-9, B-2, C-1 sonra numarasızlar (tarih azalan)
        self.assertEqual(sira(sirala="no", dir="desc"), [e.pk, a.pk, b.pk, d.pk, c.pk])           # azalanda da numarasızlar SONDA
        for yon, beklenen in (("asc", [b.pk, a.pk, e.pk, d.pk, c.pk]), ("desc", [e.pk, a.pk, b.pk, d.pk, c.pk])):
            r = self.client.get(reverse("core:alis_faturalari"), {"sirala": "no", "dir": yon, "xlsx": "1"})
            ws = load_workbook(BytesIO(r.content)).active
            satir = [[c_.value for c_ in row] for row in ws.iter_rows(min_row=5) if row[0].value is not None]
            self.assertEqual([x[1] or "" for x in satir], [Fatura.objects.get(pk=p).fatura_no for p in beklenen], yon)         # Excel'de aynı sıra
