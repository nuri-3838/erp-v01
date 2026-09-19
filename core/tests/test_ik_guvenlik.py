"""İNSAN KAYNAKLARI dosya güvenliği (KVKK): evrak/fotoğraf MEDIA_ROOT DIŞINDA özel depoda,
URL üretmez, yalnız yetkili görünümle sunulur; path-traversal/sahte kayıt 404; ayar hatası
(core.E002) sistem kontrolünde yakalanır."""
import os
import stat
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse

from core import checks
from core.models import EkranYetki, PersonelBelge
from core.services.personel_belge import belge_ekle, belge_sil, foto_ayarla
from core.storage import ik_ozel_depo
from core.tests.ik_yardimci import (
    PDF_BAYT, OzelDizinTestTemel, personel_kur, png_bayt, yuklenen,
)


class OzelDepoTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.p = personel_kur()
        self.b = belge_ekle(self.p, tur="KIMLIK", dosya=yuklenen("Kimlik.pdf", PDF_BAYT))
        foto_ayarla(self.p, yuklenen("ben.png", png_bayt()))
        self.p.refresh_from_db()

    def test_dosyalar_ozel_dizinde_medya_kokunde_degil(self):
        for ad in (self.b.dosya.name, self.p.foto.name):
            self.assertTrue((self.ozel / ad).is_file(), ad)
            self.assertFalse((self.medya / ad).exists(), ad)
        self.assertEqual([p for p in self.medya.rglob("*") if p.is_file()], [])
        self.assertEqual(Path(ik_ozel_depo().location), self.ozel)     # override_settings etkili

    def test_url_uretmez(self):
        with self.assertRaises(ValueError):
            self.b.dosya.url
        with self.assertRaises(ValueError):
            self.p.foto.url

    def test_izinler_yalniz_sahibine(self):
        if os.name != "posix":
            self.skipTest("POSIX izinleri")
        dosya_modu = stat.S_IMODE((self.ozel / self.b.dosya.name).stat().st_mode)
        dizin_modu = stat.S_IMODE((self.ozel / "personel_belge").stat().st_mode)
        self.assertEqual(dosya_modu, 0o600)
        self.assertEqual(dizin_modu, 0o700)

    def test_sayfalarda_media_yolu_ve_depolanan_ad_yok(self):
        yon = User.objects.create_superuser("gvyon", password="x")
        self.client.force_login(yon)
        depolanan = os.path.basename(self.b.dosya.name)
        for url in (reverse("core:personel_detay", args=[self.p.pk]), reverse("core:belgeler"),
                    reverse("core:belge_uyarilari"), reverse("core:personeller")):
            html = self.client.get(url).content.decode()
            self.assertNotIn("/media/", html, url)
            self.assertNotIn(depolanan, html, url)                     # UUID ad sızmaz
            self.assertNotIn("personel_belge/", html, url)

    def test_yuklenen_dosya_adi_yolu_etkilemez(self):
        b = belge_ekle(self.p, tur="DIGER",
                       dosya=yuklenen("../../etc/passwd.pdf", PDF_BAYT))
        self.assertRegex(b.dosya.name, r"^personel_belge/[0-9a-f]{32}\.pdf$")
        self.assertEqual(b.orijinal_ad, "passwd.pdf")                  # yol bileşenleri atılır
        self.assertTrue((self.ozel / b.dosya.name).is_file())


class SistemKontrolTest(OzelDizinTestTemel):
    def test_varsayilan_ayar_temiz(self):
        self.assertEqual(checks.ik_ozel_dizin_medya_disinda(None), [])

    def test_medya_altinda_hata(self):
        with override_settings(IK_OZEL_DIR=self.medya / "ik"):
            hatalar = checks.ik_ozel_dizin_medya_disinda(None)
        self.assertEqual([h.id for h in hatalar], ["core.E002"])
        self.assertIn("MEDIA_ROOT", hatalar[0].msg)

    def test_medya_koku_ile_ayni_hata(self):
        with override_settings(IK_OZEL_DIR=self.medya):
            self.assertEqual([h.id for h in checks.ik_ozel_dizin_medya_disinda(None)], ["core.E002"])

    def test_static_root_altinda_hata(self):
        with override_settings(STATIC_ROOT=self.medya / "static", MEDIA_ROOT=self.ozel,
                               IK_OZEL_DIR=self.medya / "static" / "ik"):
            hatalar = checks.ik_ozel_dizin_medya_disinda(None)
        self.assertEqual([h.id for h in hatalar], ["core.E002"])
        self.assertIn("STATIC_ROOT", hatalar[0].msg)

    def test_ayar_yoksa_hata(self):
        with override_settings():
            del settings.IK_OZEL_DIR
            hatalar = checks.ik_ozel_dizin_medya_disinda(None)
        self.assertEqual([h.id for h in hatalar], ["core.E002"])

    def test_projenin_gercek_ayarlari_medya_disinda(self):
        # Test override'ı olmadan config.settings'teki gerçek değerler de temiz olmalı.
        from config import settings as gercek
        with override_settings(IK_OZEL_DIR=gercek.IK_OZEL_DIR, MEDIA_ROOT=gercek.MEDIA_ROOT,
                               STATIC_ROOT=getattr(gercek, "STATIC_ROOT", None)):
            self.assertEqual(checks.ik_ozel_dizin_medya_disinda(None), [])


class BelgeIndirmeTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.belgeci = User.objects.create_user("gvbelgeci", password="x")
        EkranYetki.objects.create(kullanici=self.belgeci, ekran_kod="personel_belgeleri")
        self.personelci = User.objects.create_user("gvpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.bos = User.objects.create_user("gvbos", password="x")
        self.p = personel_kur()
        self.b = belge_ekle(self.p, tur="SAGLIK_RAPORU",
                            dosya=yuklenen("Sağlık Raporu 2026.pdf", PDF_BAYT))
        self.url = reverse("core:belge_indir", args=[self.b.pk])

    def test_anonim_giris_sayfasina_yonlenir(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_yetkisiz_ve_yalniz_personel_yetkisi_403(self):
        for kullanici in (self.bos, self.personelci):
            self.client.force_login(kullanici)
            self.assertEqual(self.client.get(self.url).status_code, 403, kullanici.username)

    def test_yetkili_pdf_alir_guvenlik_basliklariyla(self):
        self.client.force_login(self.belgeci)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertIn(".pdf", r["Content-Disposition"])
        self.assertNotIn(os.path.basename(self.b.dosya.name), r["Content-Disposition"])   # UUID sızmaz
        self.assertIn("no-store", r["Cache-Control"])
        self.assertIn("private", r["Cache-Control"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(b"".join(r.streaming_content), PDF_BAYT)

    def test_resim_belge_webp_olarak_sunulur(self):
        b = belge_ekle(self.p, tur="DIPLOMA", dosya=yuklenen("diploma.png", png_bayt()))
        self.client.force_login(self.belgeci)
        r = self.client.get(reverse("core:belge_indir", args=[b.pk]))
        self.assertEqual(r["Content-Type"], "image/webp")
        self.assertIn("diploma.webp", r["Content-Disposition"])

    def test_silinmis_belge_404(self):
        self.client.force_login(self.belgeci)
        belge_sil(self.b)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_silinmis_personelin_aktif_belgesi_404(self):
        # servis korumayı atlayıp doğrudan işaretlense bile indirme kapalı
        self.client.force_login(self.belgeci)
        self.p.silindi = True
        self.p.save(update_fields=["silindi"])
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_bozuk_kayit_yolu_404(self):
        self.client.force_login(self.belgeci)
        for kotu in ("../../etc/passwd", "../../etc/passwd.pdf", "/etc/passwd.pdf", "",
                     "personel_belge/yok_dosya.pdf", "personel_belge/x.exe", "personel_belge/x.html"):
            PersonelBelge.objects.filter(pk=self.b.pk).update(dosya=kotu)
            self.assertEqual(self.client.get(self.url).status_code, 404, kotu)

    def test_diskten_silinmis_dosya_404(self):
        self.client.force_login(self.belgeci)
        (self.ozel / self.b.dosya.name).unlink()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_olmayan_belge_404(self):
        self.client.force_login(self.belgeci)
        self.assertEqual(self.client.get(reverse("core:belge_indir", args=[999999])).status_code, 404)


class FotoIndirmeTest(OzelDizinTestTemel):
    def setUp(self):
        super().setUp()
        self.personelci = User.objects.create_user("gfpersonelci", password="x")
        EkranYetki.objects.create(kullanici=self.personelci, ekran_kod="personel")
        self.belgeci = User.objects.create_user("gfbelgeci", password="x")
        EkranYetki.objects.create(kullanici=self.belgeci, ekran_kod="personel_belgeleri")
        self.bos = User.objects.create_user("gfbos", password="x")
        self.p = personel_kur()
        self.url = reverse("core:personel_foto", args=[self.p.pk])

    def test_foto_yokken_404(self):
        self.client.force_login(self.personelci)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_yetkiler(self):
        foto_ayarla(self.p, yuklenen("ben.png", png_bayt()))
        self.assertEqual(self.client.get(self.url).status_code, 302)          # anonim
        for kullanici in (self.bos, self.belgeci):                            # belge yetkisi yetmez
            self.client.force_login(kullanici)
            self.assertEqual(self.client.get(self.url).status_code, 403, kullanici.username)
        self.client.force_login(self.personelci)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/webp")
        self.assertIn("no-store", r["Cache-Control"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(r.streaming_content).startswith(b"RIFF"))

    def test_silinmis_personelin_fotosu_404(self):
        foto_ayarla(self.p, yuklenen("ben.png", png_bayt()))
        self.p.silindi = True
        self.p.save(update_fields=["silindi"])
        self.client.force_login(self.personelci)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_bozuk_foto_yolu_404(self):
        foto_ayarla(self.p, yuklenen("ben.png", png_bayt()))
        self.client.force_login(self.personelci)
        from core.models import Personel
        for kotu in ("../../etc/passwd.webp", "personel_foto/yok.webp", "personel_foto/x.svg"):
            Personel.objects.filter(pk=self.p.pk).update(foto=kotu)
            self.assertEqual(self.client.get(self.url).status_code, 404, kotu)
