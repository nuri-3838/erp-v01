"""ÇEK/SENET (yeniden inşa, bordro mantığı) — Slice 1: muhasebe hesap eşlemesi
(CekHesapAyari, tekil) + ana sayfa iskeleti. Bordro motoru sonraki dilimlerde."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Cari, CekHesapAyari, HesapPlani


def _hesap(kod, ad, kalem="DV"):
    return HesapPlani.objects.create(hesap_kodu=kod, hesap_adi=ad,
                                     rapor_grubu="BILANCO", rapor_kalemi=kalem, parasal=True)


class CekHesapAyariServisTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("cayon", password="x")
        _hesap("101.01", "ALINAN ÇEKLER")
        _hesap("121.01", "ALACAK SENETLERİ")
        _hesap("103.01", "VERİLEN ÇEKLER")
        _hesap("321.01", "BORÇ SENETLERİ")

    def test_kaydet_ve_oku(self):
        from core.services.cek import hesap_ayari, hesap_ayari_kaydet
        hesap_ayari_kaydet({"portfoy_cek": "101.01", "portfoy_senet": "121.01",
                            "verilen_cek": "103.01", "verilen_senet": "321.01"},
                           kullanici=self.yon)
        a = hesap_ayari()
        self.assertEqual(a.portfoy_cek.hesap_kodu, "101.01")
        self.assertEqual(a.portfoy_senet.hesap_kodu, "121.01")
        self.assertEqual(a.verilen_cek.hesap_kodu, "103.01")
        self.assertEqual(a.verilen_senet.hesap_kodu, "321.01")
        self.assertIsNone(a.tahsilde_cek)                 # boş bırakılan
        self.assertEqual(CekHesapAyari.objects.count(), 1)   # tekil kayıt

    def test_bos_alani_none_yapar(self):
        from core.services.cek import hesap_ayari, hesap_ayari_kaydet
        hesap_ayari_kaydet({"portfoy_cek": "101.01"}, kullanici=self.yon)
        self.assertEqual(hesap_ayari().portfoy_cek.hesap_kodu, "101.01")
        hesap_ayari_kaydet({"portfoy_cek": ""}, kullanici=self.yon)   # geri boşalt
        self.assertIsNone(hesap_ayari().portfoy_cek)

    def test_yaprak_olmayan_reddedilir(self):
        from core.services.cek import CekHatasi, hesap_ayari_kaydet
        _hesap("400", "ÜST HESAP")
        _hesap("400.01", "ALT HESAP")          # 400 artık üst (yaprak değil)
        with self.assertRaises(CekHatasi):
            hesap_ayari_kaydet({"portfoy_cek": "400"}, kullanici=self.yon)


class CekSayfaViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("csyon", password="x")
        cls.bos = User.objects.create_user("csbos", password="x")
        _hesap("101.01", "ALINAN ÇEKLER")

    def test_ana_sayfa_200(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cek_senetler"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Muhasebe Hesap Kodları")
        self.assertContains(r, "Cari Giriş")
        self.assertContains(r, "Banka Teminat")
        self.assertContains(r, "Bordrolar")

    def test_ana_sayfa_yetkisiz_403(self):
        self.client.force_login(self.bos)
        self.assertEqual(self.client.get(reverse("core:cek_senetler")).status_code, 403)

    def test_hesap_ayari_get_ve_post(self):
        from core.services.cek import hesap_ayari
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:cek_hesap_ayari")).status_code, 200)
        r = self.client.post(reverse("core:cek_hesap_ayari"), {"portfoy_cek": "101.01"})
        self.assertRedirects(r, reverse("core:cek_senetler"))
        self.assertEqual(hesap_ayari().portfoy_cek.hesap_kodu, "101.01")

    def test_menude_cek_senet_var(self):
        from core.moduller import MODULLER
        finans = next(m for m in MODULLER if m.kod == "FINANS")
        self.assertIn("cek_senet", [e.kod for e in finans.ekranlar])


class CariGirisBordroTest(TestCase):
    """Slice 2: Cari Giriş bordrosu — çok çek/senet → tek birleşik fiş; geri-al."""
    @classmethod
    def setUpTestData(cls):
        import datetime
        from decimal import Decimal
        from core.models import Kur
        from core.services.cek import hesap_ayari_kaydet
        cls.yon = User.objects.create_superuser("cgyon", password="x")
        _hesap("101.01", "ALINAN ÇEKLER")
        _hesap("121.01", "ALACAK SENETLERİ")
        _hesap("103.01", "VERİLEN ÇEKLER")
        _hesap("321.01", "BORÇ SENETLERİ")
        _hesap("120.01", "ALICILAR")
        _hesap("320.01", "SATICILAR")
        hesap_ayari_kaydet({"portfoy_cek": "101.01", "portfoy_senet": "121.01",
                            "verilen_cek": "103.01", "verilen_senet": "321.01"}, kullanici=cls.yon)
        cls.cari = Cari.objects.create(kod="C1", unvan="MÜŞTERİ A", muhasebe_kodu="120.01",
                                       created_by=cls.yon, updated_by=cls.yon)
        cls.satici = Cari.objects.create(kod="S1", unvan="SATICI A", muhasebe_kodu="320.01",
                                         created_by=cls.yon, updated_by=cls.yon)
        Kur.objects.create(tarih=datetime.date(2026, 6, 28), usd_alis=Decimal("40"))

    def _olustur(self, satirlar):
        import datetime
        from core.services.cek import cari_giris_bordrosu_olustur
        return cari_giris_bordrosu_olustur(
            cari_id=self.cari.pk, tarih=datetime.date(2026, 6, 28), para_birimi="TRY",
            satirlar=satirlar, kullanici=self.yon)

    def test_giris_bordrosu_olusur_ve_fis(self):
        import datetime
        from decimal import Decimal
        b = self._olustur([
            {"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 9, 1)},
            {"tip": "CEK", "tutar": "2.000", "vade": datetime.date(2026, 10, 1)},
            {"tip": "SENET", "tutar": "500", "vade": datetime.date(2026, 11, 1)}])
        self.assertEqual(b.cek_senetler.count(), 3)
        self.assertTrue(all(c.durum == "PORTFOYDE" and c.yon == "ALINAN"
                            for c in b.cek_senetler.all()))
        fis = b.fisler.get()
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.all()}
        self.assertEqual(s["101.01"], (Decimal("3000.00"), Decimal("0.00")))   # çek toplam borç
        self.assertEqual(s["121.01"], (Decimal("500.00"), Decimal("0.00")))    # senet toplam borç
        self.assertEqual(s["120.01"], (Decimal("0.00"), Decimal("3500.00")))   # cari alacak

    def test_config_eksik_reddedilir(self):
        import datetime
        from core.models import CekHesapAyari
        from core.services.cek import CekHatasi
        CekHesapAyari.objects.all().delete()
        with self.assertRaises(CekHatasi):
            self._olustur([{"tip": "CEK", "tutar": "100", "vade": datetime.date(2026, 9, 1)}])

    def test_bordro_sil_fisi_iptal_eder(self):
        import datetime
        from core.services.cek import bordro_sil
        b = self._olustur([{"tip": "CEK", "tutar": "100", "vade": datetime.date(2026, 9, 1)}])
        fis = b.fisler.get()
        bordro_sil(b, kullanici=self.yon)
        b.refresh_from_db(); fis.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertTrue(fis.silindi)
        self.assertEqual(b.cek_senetler.filter(silindi=False).count(), 0)

    def test_view_post_olusturur(self):
        from core.models import CekBordrosu
        self.client.force_login(self.yon)
        data = {
            "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-tip": "CEK", "form-0-tutar": "1.500", "form-0-vade": "2026-09-01",
            "form-0-belge_no": "A1", "form-0-kesideci": "ahmet",
            "form-1-tip": "SENET", "form-1-tutar": "750", "form-1-vade": "2026-10-01",
            "form-1-belge_no": "", "form-1-kesideci": "",
        }
        r = self.client.post(reverse("core:cek_cari_giris"), data)
        b = CekBordrosu.objects.filter(silindi=False).first()
        self.assertIsNotNone(b)
        self.assertRedirects(r, reverse("core:cek_bordro_detay", args=[b.pk]))
        self.assertEqual(b.cek_senetler.count(), 2)

    def test_ana_sayfa_cari_giris_aktif(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:cek_senetler"))
        self.assertContains(r, reverse("core:cek_cari_giris"))   # Cari Giriş aktif link

    def test_gorsel_yuklenir_ve_kuculur(self):
        import io, tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from core.models import CekSenet
        buf = io.BytesIO()
        Image.new("RGB", (2400, 1200), "white").save(buf, "PNG")   # büyük görsel
        on = SimpleUploadedFile("on.png", buf.getvalue(), content_type="image/png")
        self.client.force_login(self.yon)
        data = {
            "cari": self.cari.pk, "tarih": "2026-06-28", "para_birimi": "TRY",
            "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0",
            "form-MIN_NUM_FORMS": "1", "form-MAX_NUM_FORMS": "1000",
            "form-0-tip": "CEK", "form-0-tutar": "1.000", "form-0-vade": "2026-09-01",
            "form-0-belge_no": "B9", "form-0-kesideci": "",
            "form-0-on_yuz": on,
        }
        with override_settings(MEDIA_ROOT=tempfile.mkdtemp()):
            r = self.client.post(reverse("core:cek_cari_giris"), data)
            self.assertEqual(r.status_code, 302)
            c = CekSenet.objects.filter(silindi=False, belge_no="B9").get()
            self.assertTrue(c.on_yuz)                              # görsel kaydedildi
            self.assertTrue(c.on_yuz.name.endswith(".webp"))      # WebP'ye çevrildi
            self.assertFalse(c.arka_yuz)                           # arka yüklenmedi
            im = Image.open(c.on_yuz.path)
            self.assertLessEqual(max(im.size), 1600)              # en uzun kenar ≤ 1600

    def test_ortalama_vade_agirlikli(self):
        import datetime
        from decimal import Decimal
        from core.services.cek import ortalama_vade
        baz = datetime.date(2026, 6, 28)
        # 1000 @ +30g, 3000 @ +60g → (1000·30 + 3000·60)/4000 = 52,5 → 53 (HALF_UP)
        ov, gun = ortalama_vade(
            [(Decimal("1000"), baz + datetime.timedelta(days=30)),
             (Decimal("3000"), baz + datetime.timedelta(days=60))], baz)
        self.assertEqual(gun, 53)
        self.assertEqual(ov, baz + datetime.timedelta(days=53))
        # eşit ağırlık → ortalama 45 gün
        _, gun2 = ortalama_vade(
            [(Decimal("1000"), baz + datetime.timedelta(days=30)),
             (Decimal("1000"), baz + datetime.timedelta(days=60))], baz)
        self.assertEqual(gun2, 45)
        self.assertEqual(ortalama_vade([], baz), (None, None))    # boş

    def test_firma_cikis_bordrosu_ve_fis(self):
        import datetime
        from decimal import Decimal
        from core.models import CekBordrosu
        from core.services.cek import firma_cikis_bordrosu_olustur
        b = firma_cikis_bordrosu_olustur(
            cari_id=self.cari.pk, tarih=datetime.date(2026, 6, 28), para_birimi="TRY",
            satirlar=[{"tip": "CEK", "tutar": "5.000", "vade": datetime.date(2026, 9, 1)},
                      {"tip": "SENET", "tutar": "2.000", "vade": datetime.date(2026, 10, 1)}],
            kullanici=self.yon)
        self.assertEqual(b.tur, CekBordrosu.Tur.FIRMA_CIKIS)
        self.assertTrue(all(c.yon == "VERILEN" and c.durum == "VERILDI"
                            for c in b.cek_senetler.all()))
        fis = b.fisler.get()
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.all()}
        self.assertEqual(s["120.01"], (Decimal("7000.00"), Decimal("0.00")))   # cari borç toplam
        self.assertEqual(s["103.01"], (Decimal("0.00"), Decimal("5000.00")))   # verilen çek alacak
        self.assertEqual(s["321.01"], (Decimal("0.00"), Decimal("2000.00")))   # verilen senet alacak

    def test_firma_cikis_bordro_sil(self):
        import datetime
        from core.services.cek import bordro_sil, firma_cikis_bordrosu_olustur
        b = firma_cikis_bordrosu_olustur(
            cari_id=self.cari.pk, tarih=datetime.date(2026, 6, 28), para_birimi="TRY",
            satirlar=[{"tip": "CEK", "tutar": "5.000", "vade": datetime.date(2026, 9, 1)}],
            kullanici=self.yon)
        fis = b.fisler.get()
        bordro_sil(b, kullanici=self.yon)   # VERİLDİ evrak da silinebilmeli (giriş durumu)
        b.refresh_from_db(); fis.refresh_from_db()
        self.assertTrue(b.silindi)
        self.assertTrue(fis.silindi)
        self.assertEqual(b.cek_senetler.filter(silindi=False).count(), 0)

    def test_firma_cikis_view_ve_aktif_buton(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:cek_firma_cikis")).status_code, 200)
        r = self.client.get(reverse("core:cek_senetler"))
        self.assertContains(r, reverse("core:cek_firma_cikis"))   # aktif buton

    def test_cari_ciro_bordrosu_ve_geri_al(self):
        import datetime
        from decimal import Decimal
        from core.models import CekBordrosu, CekSenet
        from core.services.cek import bordro_sil, cari_ciro_bordrosu_olustur
        g = self._olustur([{"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 9, 1)},
                           {"tip": "CEK", "tutar": "2.000", "vade": datetime.date(2026, 10, 1)}])
        cek_ids = list(g.cek_senetler.values_list("pk", flat=True))
        cb = cari_ciro_bordrosu_olustur(ciro_cari_id=self.satici.pk, tarih=datetime.date(2026, 6, 28),
                                        cek_ids=cek_ids, kullanici=self.yon)
        self.assertEqual(cb.tur, CekBordrosu.Tur.CARI_CIRO)
        self.assertEqual(cb.evrak_qs().count(), 2)
        self.assertTrue(all(c.durum == "CIRO" for c in CekSenet.objects.filter(pk__in=cek_ids)))
        fis = cb.fisler.get()
        s = {x.hesap_id: (x.borc, x.alacak) for x in fis.satirlar.all()}
        self.assertEqual(s["320.01"], (Decimal("3000.00"), Decimal("0.00")))   # ciro carisi borç
        self.assertEqual(s["101.01"], (Decimal("0.00"), Decimal("3000.00")))   # portföy çek alacak
        # geri-al → portföye döner
        bordro_sil(cb, kullanici=self.yon)
        cb.refresh_from_db(); fis.refresh_from_db()
        self.assertTrue(cb.silindi)
        self.assertTrue(fis.silindi)
        self.assertTrue(all(c.durum == "PORTFOYDE" for c in CekSenet.objects.filter(pk__in=cek_ids)))

    def test_ciro_portfoyde_olmayan_reddedilir(self):
        import datetime
        from core.services.cek import CekHatasi, cari_ciro_bordrosu_olustur
        g = self._olustur([{"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 9, 1)}])
        cek_ids = list(g.cek_senetler.values_list("pk", flat=True))
        cari_ciro_bordrosu_olustur(ciro_cari_id=self.satici.pk, tarih=datetime.date(2026, 6, 28),
                                   cek_ids=cek_ids, kullanici=self.yon)
        with self.assertRaises(CekHatasi):           # artık CIRO; tekrar ciro edilemez
            cari_ciro_bordrosu_olustur(ciro_cari_id=self.satici.pk, tarih=datetime.date(2026, 6, 28),
                                       cek_ids=cek_ids, kullanici=self.yon)

    def test_ciro_view_ve_aktif_buton(self):
        self.client.force_login(self.yon)
        self.assertEqual(self.client.get(reverse("core:cek_cari_ciro")).status_code, 200)
        r = self.client.get(reverse("core:cek_senetler"))
        self.assertContains(r, reverse("core:cek_cari_ciro"))

    def test_detay_ortalama_vade_ve_pdf(self):
        import datetime
        b = self._olustur([
            {"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 9, 1)},
            {"tip": "CEK", "tutar": "1.000", "vade": datetime.date(2026, 11, 1)}])
        self.client.force_login(self.yon)
        d = self.client.get(reverse("core:cek_bordro_detay", args=[b.pk]))
        self.assertContains(d, "Ortalama Vade")
        self.assertContains(d, reverse("core:cek_bordro_pdf", args=[b.pk]))   # PDF butonu
        p = self.client.get(reverse("core:cek_bordro_pdf", args=[b.pk]))      # gerçek PDF üretilir
        self.assertEqual(p.status_code, 200)
        self.assertEqual(p["Content-Type"], "application/pdf")
        self.assertEqual(p.content[:5], b"%PDF-")
