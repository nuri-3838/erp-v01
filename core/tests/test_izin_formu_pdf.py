"""İzin Talep ve Onay Formu PDF'i (2026-10-12): A5 dikey, siyah-beyaz, gri logo, alt bilgi yok, personel adı PERSONEL tablosunda basılı (TC ve işe
giriş de), imza kutusundaki Ad Soyad/Tarih/İmza boş; tam gün / saatlik içerik; yıllık izinde bakiye tablosu; imza kutuları. İmzalı form yükleme: izne bağlı + Özlük Belgeleri'ne
"İzin formu – tarih", ✅ İmzalı rozeti ve dosya bağlantısı, yeniden yükleme eskisini soft-siler, yetki personel kartı."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse

from core.models import EkranYetki, PersonelBelge, PersonelIzin, ResmiTatil
from core.services import personel_izin as izin_servis
from core.services.firma import firma_bilgisi_getir, firma_bilgisi_kaydet
from core.services.personel_izin import PersonelIzinHatasi, imzali_form_yukle, izin_ekle, izin_formu_baglam
from core.tests.ik_yardimci import PDF_BAYT, OzelDizinTestTemel, personel_kur, png_bayt, yuklenen

D = Decimal


class IzinFormuTest(OzelDizinTestTemel):
    @classmethod
    def setUpTestData(cls):
        cls.p = personel_kur(ad="metehan", soyad="kaya", ise_giris_tarihi=date(2020, 3, 15), tc_kimlik_no="10000000146", departman="ÜRETİM", gorev="USTA")
        cls.yon = User.objects.create_superuser("ifyon", password="x")
        cls.ik = User.objects.create_user("ifik", password="x")
        EkranYetki.objects.create(kullanici=cls.ik, ekran_kod="personel")
        cls.izinci = User.objects.create_user("ifizin", password="x")
        EkranYetki.objects.create(kullanici=cls.izinci, ekran_kod="personel_izinleri")
        firma_bilgisi_kaydet(unvan="SEMTA ALÜMİNYUM A.Ş.", adres="Organize Sanayi 5. Cad. No:7 Kayseri", kullanici=cls.yon)
        ResmiTatil.objects.get_or_create(tarih=date(2026, 7, 15), silindi=False, defaults={"ad": "Demokrasi ve Millî Birlik Günü"})   # seed zaten ekler

    def html(self, izin, tarih=date(2026, 7, 10)):
        return render_to_string("core/izin_formu_pdf.html", {**izin_formu_baglam(izin), "firma": firma_bilgisi_getir(), "belge_tarihi": tarih})

    def test_baglam_tam_gun_yillik(self):
        izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 1), bitis=date(2026, 6, 3), kullanici=self.yon)       # 3 gün (önce)
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 7, 13), bitis=date(2026, 7, 14), kullanici=self.yon)  # Pzt–Sal; 15'i tatil, 16 Perşembe
        b = izin_formu_baglam(i)
        self.assertEqual((b["yillik"], b["ise_baslama"], b["sure_metni"]), (True, date(2026, 7, 16), ""))
        bk = b["bakiye"]
        self.assertEqual((bk["hak_edilen"], bk["once_kullanilan"], bk["bu_izin"], bk["kalan"]), (D("90"), D("3"), D("2"), D("85")))   # 5 yıl × 14 + 6. yıl 20
        self.assertEqual((bk["once_metni"], bk["bu_izin_metni"], bk["kalan_metni"]), ("3 gün", "2 gün", "85 gün"))
        self.assertEqual(izin_servis.ise_baslama_tarihi(date(2026, 6, 6)), date(2026, 6, 8))                             # Cumartesi → Pazartesi

    def test_baglam_saatlik_ve_diger_tur(self):
        i = izin_ekle(self.p, tur="MAZERET", baslangic=date(2026, 6, 10), saatlik=True, baslangic_saati="09:00", bitis_saati="11:30", kullanici=self.yon)
        b = izin_formu_baglam(i)
        self.assertEqual((b["yillik"], b["bakiye"], b["ise_baslama"], b["sure_metni"]), (False, None, None, "2 sa 30 dk"))
        s = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 6, 11), saatlik=True, baslangic_saati="13:00", bitis_saati="16:00", kullanici=self.yon)
        bk = izin_formu_baglam(s)["bakiye"]
        self.assertEqual((bk["bu_izin"], bk["bu_izin_metni"], bk["kalan_metni"]), (D("0.4"), "3 sa", "89 gün 4,5 sa"))

    def test_pdf_a5_siyah_beyaz_icerik_ve_dugmeler(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 7, 13), bitis=date(2026, 7, 14), aciklama="yaz tatili", kullanici=self.yon)
        self.client.force_login(self.ik)
        url = reverse("core:izin_formu_pdf", args=[i.pk])
        r = self.client.get(url)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        html = self.html(i)
        self.assertIn("size: A5 portrait", html)
        self.assertIn("grayscale(100%)", html)
        self.assertIn(firma_bilgisi_getir().adres, html)
        for metin in ("İzin Talep ve Onay Formu", "SEMTA ALÜMİNYUM A.Ş.", "10000000146", "15.03.2020", "Yıllık İzin", "13.07.2026 – 14.07.2026",
                      "2,0 gün", "16.07.2026", "Hak Edilen", "Bu İzinden Önce Kullanılan", "Kalan", "yaz tatili", "İzni Talep Eden Personel",
                      "Onaylayan / İşveren Vekili", "Belge tarihi", "İmza / Kaşe", self.p.ad_soyad):
            self.assertIn(metin, html, metin)
        self.assertIn('<td class="et">Adı Soyadı</td><td class="dg">%s</td>' % self.p.ad_soyad, html)                 # ad PERSONEL tablosunda
        imza = html[html.index('<div class="imza">'):]
        self.assertNotIn(self.p.ad_soyad, imza)                                                                       # imza kutusu boş (elle)
        self.assertEqual(imza.count('<span class="bos"></span>'), 6)
        for metin in ("ÜRETİM", "USTA", "4857", "SEMTA ERP", "@bottom", "#15294d", "#f07c00"):
            self.assertNotIn(metin, html, metin)                                                                      # departman/görev, kanun notu, footer, renk yok
        m = izin_ekle(self.p, tur="MAZERET", baslangic=date(2026, 6, 10), saatlik=True, baslangic_saati="09:00", bitis_saati="11:30", kullanici=self.yon)
        html = self.html(m)
        for metin in ("Mazeret İzni (saatlik)", "09:00 – 11:30", "2 sa 30 dk"):
            self.assertIn(metin, html)
        self.assertIn(self.p.ad_soyad, html)
        for metin in ("Hak Edilen", "İşe Başlama"):
            self.assertNotIn(metin, html)
        self.assertEqual(self.client.get(reverse("core:izin_formu_pdf", args=[m.pk])).status_code, 200)
        # düğmeler
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertNotContains(r, "Form (PDF)")                                                                       # izin bölümü izin yetkisi ister
        EkranYetki.objects.create(kullanici=self.ik, ekran_kod="personel_izinleri")
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertContains(r, url)
        self.assertContains(r, "🖨 Form (PDF)")
        self.assertContains(r, "İmzalı formu yükle")
        r = self.client.get(reverse("core:izinler"))
        self.assertContains(r, url)
        self.assertContains(r, "İmzalı formu yükle")
        self.client.force_login(self.izinci)                                                                          # yalnız izin yetkisi: düğme/PDF/yükleme yok
        r = self.client.get(reverse("core:izinler"))
        self.assertNotContains(r, "Form (PDF)")
        self.assertNotContains(r, "İmzalı formu yükle")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("core:izin_imzali_yukle", args=[i.pk]), {"dosya": yuklenen("f.pdf", PDF_BAYT)}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_imzali_form_yukleme_rozet_ve_yeniden_yukleme(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 7, 13), bitis=date(2026, 7, 14), kullanici=self.yon)
        self.client.force_login(self.ik)
        EkranYetki.objects.create(kullanici=self.ik, ekran_kod="personel_izinleri")
        url = reverse("core:izin_imzali_yukle", args=[i.pk])
        r = self.client.post(url, {"dosya": yuklenen("imzali.pdf", PDF_BAYT), "sonraki": "personel"}, follow=True)
        self.assertRedirects(r, reverse("core:personel_detay", args=[self.p.pk]))
        i.refresh_from_db()
        b1 = i.imzali_belge
        self.assertEqual((b1.personel_id, b1.tur, b1.aciklama, b1.orijinal_ad, b1.silindi), (self.p.pk, "DIGER", "İzin formu – 13.07.2026", "imzali.pdf", False))
        self.assertContains(r, "✅ İmzalı")
        self.assertContains(r, reverse("core:belge_indir", args=[b1.pk]))
        self.assertContains(r, "Yeniden yükle")
        r = self.client.get(reverse("core:izinler"))
        self.assertContains(r, "✅ İmzalı")
        # yeniden yükleme (PNG): eski belge soft-silinir, yeni bağlanır
        r = self.client.post(url, {"dosya": yuklenen("imzali2.png", png_bayt())}, follow=True)
        self.assertRedirects(r, reverse("core:izinler"))
        i.refresh_from_db()
        b1.refresh_from_db()
        self.assertTrue(b1.silindi)
        self.assertEqual((i.imzali_belge_id != b1.pk, i.imzali_belge.orijinal_ad, PersonelBelge.objects.filter(personel=self.p, silindi=False).count()),
                         (True, "imzali2.png", 1))
        # geçersiz dosya: hata mesajı, bağ değişmez
        r = self.client.post(url, {"dosya": yuklenen("x.txt", b"merhaba")}, follow=True)
        mesajlar = [str(x) for x in r.context["messages"]]
        self.assertTrue(any("x.txt" in m or "uzant" in m.lower() or "dosya" in m.lower() for m in mesajlar), mesajlar)
        i.refresh_from_db()
        self.assertEqual(i.imzali_belge.orijinal_ad, "imzali2.png")
        with self.assertRaisesMessage(PersonelIzinHatasi, "Dosya seçilmedi"):
            imzali_form_yukle(i, None, kullanici=self.yon)
        r = self.client.post(url, {}, follow=True)
        self.assertTrue(any("Dosya seçilmedi" in str(x) for x in r.context["messages"]))
        # izin silinince belge Özlük'te kalır
        izin_servis.izin_sil(i, kullanici=self.yon)
        self.assertEqual(PersonelBelge.objects.filter(personel=self.p, silindi=False).count(), 1)
