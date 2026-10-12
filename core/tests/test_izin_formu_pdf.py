"""İzin Talep ve Onay Formu PDF'i (2026-10-12): izin listesi ve personel kartında "🖨 Form (PDF)" düğmesi; WeasyPrint A4 PDF; yetki personel
kartı; bağlam: firma ünvan/adres, personel (departman/görev yok), tam gün (tarihler, gün, işe başlama = bitişten sonraki ilk iş günü) /
saatlik (tarih, saat aralığı, süre), yıllık izinde bakiye kutusu (hak edilen, önce kullanılan, bu izin, kalan) ve kanun notu; diğer türde yok."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import EkranYetki, ResmiTatil
from core.services import personel_izin as izin_servis
from core.services.firma import firma_bilgisi_kaydet
from core.services.personel_izin import izin_ekle, izin_formu_baglam
from core.tests.ik_yardimci import personel_kur

D = Decimal


class IzinFormuTest(TestCase):
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

    def test_pdf_uretimi_icerik_ve_dugmeler(self):
        i = izin_ekle(self.p, tur="YILLIK", baslangic=date(2026, 7, 13), bitis=date(2026, 7, 14), aciklama="yaz tatili", kullanici=self.yon)
        self.client.force_login(self.ik)
        url = reverse("core:izin_formu_pdf", args=[i.pk])
        r = self.client.get(url)
        self.assertEqual((r.status_code, r["Content-Type"]), (200, "application/pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("izin-formu", r["Content-Disposition"])
        from django.template.loader import render_to_string
        from core.services.firma import firma_bilgisi_getir
        html = render_to_string("core/izin_formu_pdf.html", {**izin_formu_baglam(i), "firma": firma_bilgisi_getir(), "belge_tarihi": date(2026, 7, 10)})
        self.assertIn(firma_bilgisi_getir().adres, html)
        for metin in ("İzin Talep ve Onay Formu", "SEMTA ALÜMİNYUM A.Ş.", "METEHAN KAYA", "10000000146", "15.03.2020",
                      "Yıllık İzin", "13.07.2026 – 14.07.2026", "2,0 gün", "16.07.2026", "Hak Edilen", "Bu İzinden Önce Kullanılan", "Kalan",
                      "yaz tatili", "İzni Talep Eden Personel", "Onaylayan / İşveren Vekili", "4857 sayılı İş Kanunu", "Belge tarihi"):
            self.assertIn(metin, html, metin)
        for metin in ("ÜRETİM", "USTA"):
            self.assertNotIn(metin, html, metin)                                                                     # departman/görev yazılmaz
        m = izin_ekle(self.p, tur="MAZERET", baslangic=date(2026, 6, 10), saatlik=True, baslangic_saati="09:00", bitis_saati="11:30", kullanici=self.yon)
        html = render_to_string("core/izin_formu_pdf.html", {**izin_formu_baglam(m), "firma": firma_bilgisi_getir(), "belge_tarihi": date(2026, 6, 10)})
        for metin in ("Mazeret İzni (saatlik)", "09:00 – 11:30", "2 sa 30 dk"):
            self.assertIn(metin, html)
        for metin in ("4857 sayılı", "Hak Edilen", "İşe Başlama"):
            self.assertNotIn(metin, html)
        self.assertEqual(self.client.get(reverse("core:izin_formu_pdf", args=[m.pk])).status_code, 200)
        # düğmeler
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertNotContains(r, "Form (PDF)")                                                                       # izin bölümü izin yetkisi ister
        EkranYetki.objects.create(kullanici=self.ik, ekran_kod="personel_izinleri")
        r = self.client.get(reverse("core:personel_detay", args=[self.p.pk]))
        self.assertContains(r, url)
        self.assertContains(r, "🖨 Form (PDF)")
        self.assertContains(self.client.get(reverse("core:izinler")), url)
        # yalnız izin yetkisi: liste görür ama form düğmesi/PDF yok (personel kartı yetkisi gerekir)
        self.client.force_login(self.izinci)
        self.assertNotContains(self.client.get(reverse("core:izinler")), "Form (PDF)")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
