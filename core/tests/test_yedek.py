"""Yedek ekranı (AYARLAR, yönetici-only) testleri.

Kapsam: yalnız yönetici girer (403 aksi); liste tarih/boyut + son yedek; indirme
(attachment) + path-traversal guard; "Şimdi Yedek Al" Aşama 1 motorunu çağırır
(subprocess MOCK'lanır — testte gerçek pg_dump çalışmaz); menüde yönetici görür.
GERİ YÜKLEME ekranda YOK — onu da doğrularız.
"""
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import EkranYetki
from core.services import yedek as yedek_servis


def _yedek_yaz(dizin, ad, icerik=b"BZh-fake-gzip"):
    (Path(dizin) / ad).write_bytes(icerik)


class YedekTestTemel(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.yon = User.objects.create_superuser("yon", password="parola1234")
        # Yönetici değil; yalnız mizan ekran yetkisi var (yedek yine yönetici-only kalır)
        cls.normal = User.objects.create_user("kis", password="x")
        EkranYetki.objects.create(kullanici=cls.normal, ekran_kod="mizan")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.over = override_settings(BACKUP_DIR=self.tmp)
        self.over.enable()
        self.addCleanup(self.over.disable)


class YedekServisTest(YedekTestTemel):
    def test_listele_en_yeni_once(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "erp_v01_20260603_030000.sql.gz")
        _yedek_yaz(self.tmp, "erp_v01_20260602_030000.sql.gz")
        adlar = [y.ad for y in yedek_servis.yedekleri_listele()]
        self.assertEqual(adlar, [
            "erp_v01_20260603_030000.sql.gz",
            "erp_v01_20260602_030000.sql.gz",
            "erp_v01_20260601_030000.sql.gz",
        ])
        self.assertEqual(yedek_servis.son_yedek().ad, "erp_v01_20260603_030000.sql.gz")

    def test_listele_yabanci_dosya_haric(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "semta_erp_20260601.sql.gz")   # eski sistem -> görünmez
        _yedek_yaz(self.tmp, "rastgele.txt")
        self.assertEqual(len(yedek_servis.yedekleri_listele()), 1)

    def test_boyut_h(self):
        self.assertEqual(yedek_servis.YedekDosya("a", 512, None).boyut_h, "512 B")
        self.assertEqual(yedek_servis.YedekDosya("a", 2048, None).boyut_h, "2.0 KB")

    def test_yol_guvenlik(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        self.assertIsNotNone(yedek_servis.yedek_yolu("erp_v01_20260601_030000.sql.gz"))
        for kotu in ["../../etc/passwd", "erp_v01_x.sql.gz", "yok_20260601_030000.sql.gz",
                     "erp_v01_20260601_030000.sql.gz/../x", ""]:
            self.assertIsNone(yedek_servis.yedek_yolu(kotu), kotu)

    def test_yedek_al_motoru_cagirir(self):
        # subprocess MOCK: gerçek pg_dump çalışmaz; sadece çağrıldığını + sonucu doğrula
        sahte = mock.Mock(returncode=0, stdout="OK", stderr="")
        with mock.patch("core.services.yedek.subprocess.run", return_value=sahte) as m:
            basari, mesaj = yedek_servis.yedek_al()
        self.assertTrue(basari)
        self.assertTrue(m.called)
        self.assertIn("bash", m.call_args[0][0][0])

    def test_yedek_al_hata_kodu(self):
        sahte = mock.Mock(returncode=1, stdout="", stderr="pg_dump: bağlantı yok")
        with mock.patch("core.services.yedek.subprocess.run", return_value=sahte):
            basari, mesaj = yedek_servis.yedek_al()
        self.assertFalse(basari)
        self.assertIn("başarısız", mesaj)

    def test_yedek_al_arkaplan_baslatir(self):
        # Popen MOCK: gerçek süreç başlatılmaz; çağrıldığını + "arka planda" mesajını doğrula
        with mock.patch("core.services.yedek.subprocess.Popen") as m:
            basari, mesaj = yedek_servis.yedek_al_arkaplan()
        self.assertTrue(basari)
        self.assertTrue(m.called)
        self.assertIn("arka planda", mesaj)


class YedekEkranTest(YedekTestTemel):
    def test_yetkisiz_403(self):
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("core:yedek")).status_code, 403)

    def test_yonetici_200_liste(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz", b"x" * 2048)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "erp_v01_20260601_030000.sql.gz")
        self.assertContains(r, "2.0 KB")
        self.assertContains(r, "Son yedek")
        self.assertContains(r, "Şimdi Yedek Al")

    def test_geri_yukleme_ekranda_yok(self):
        # Geri yükleme aksiyonu/butonu/URL'i ekranda OLMAMALI (yalnız yardım metninde
        # "geri yükleme ... yer almaz" geçer — o küçük harfli, buton değil).
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek"))
        self.assertNotContains(r, "Geri Yükle")
        self.assertNotContains(r, "yedek_geri")
        self.assertNotContains(r, 'name="restore"')

    def test_indir(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz", b"YEDEK-ICERIK")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek_indir", args=["erp_v01_20260601_030000.sql.gz"]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), b"YEDEK-ICERIK")

    def test_indir_gecersiz_404(self):
        self.client.force_login(self.yon)
        self.assertEqual(
            self.client.get(reverse("core:yedek_indir", args=["yok_20260601_030000.sql.gz"]))
            .status_code, 404)

    def test_indir_yetkisiz_403(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        self.client.force_login(self.normal)
        self.assertEqual(
            self.client.get(reverse("core:yedek_indir", args=["erp_v01_20260601_030000.sql.gz"]))
            .status_code, 403)

    def test_simdi_yedek_al_post(self):
        # Artık ARKA PLANDA (Popen) çalışır; web isteğini bekletmez.
        self.client.force_login(self.yon)
        with mock.patch("core.services.yedek.subprocess.Popen") as m:
            r = self.client.post(reverse("core:yedek"), follow=True)
        self.assertTrue(m.called)                       # Aşama 1 motoru arka planda tetiklendi
        self.assertContains(r, "arka planda başlatıldı")

    def test_menude_yonetici_gorur(self):
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek"))
        self.assertContains(r, "Yedek")

    def test_menude_yetkisize_ayarlar_gizli(self):
        self.client.force_login(self.normal)
        r = self.client.get(reverse("core:mizan"))
        self.assertNotContains(r, reverse("core:yedek"))


DB_AD = "erp_v01_20260601_030000.sql.gz"
EVRAK_AD = "erp_v01_ozel_20260601_030000.tar.gz"


class YedekEvrakServisTest(YedekTestTemel):
    """İK özlük evrakları arşivi (erp_v01_ozel_*.tar.gz) — DB yedeğiyle aynı ekranda."""

    def test_iki_tur_listelenir_tur_bilgisiyle(self):
        _yedek_yaz(self.tmp, DB_AD)
        _yedek_yaz(self.tmp, EVRAK_AD)
        tur = {y.ad: y.tur for y in yedek_servis.yedekleri_listele()}
        self.assertEqual(tur, {DB_AD: "DB", EVRAK_AD: "EVRAK"})
        adlar = {y.ad: y.tur_ad for y in yedek_servis.yedekleri_listele()}
        self.assertEqual(adlar[DB_AD], "Veritabanı")
        self.assertEqual(adlar[EVRAK_AD], "Özlük evrakları")

    def test_zaman_damgasi_ada_gore_yeni_once(self):
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "erp_v01_ozel_20260602_030000.tar.gz")
        _yedek_yaz(self.tmp, "erp_v01_20260603_030000.sql.gz")
        self.assertEqual([y.ad for y in yedek_servis.yedekleri_listele()], [
            "erp_v01_20260603_030000.sql.gz", "erp_v01_ozel_20260602_030000.tar.gz",
            "erp_v01_20260601_030000.sql.gz"])
        self.assertEqual(yedek_servis.yedekleri_listele()[1].tarih.day, 2)

    def test_son_yedek_yalniz_db(self):
        # Aynı saniyede damgalı evrak arşivi 'son yedek' kartını DB'den başka türe çevirmez.
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "erp_v01_ozel_20260601_030000.tar.gz")
        _yedek_yaz(self.tmp, "erp_v01_ozel_20260701_030000.tar.gz")       # daha yeni, ama evrak
        son = yedek_servis.son_yedek()
        self.assertEqual((son.ad, son.tur), ("erp_v01_20260601_030000.sql.gz", "DB"))

    def test_yalniz_evrak_varsa_son_yedek_yok(self):
        _yedek_yaz(self.tmp, EVRAK_AD)
        self.assertIsNone(yedek_servis.son_yedek())

    def test_yarim_ve_yabanci_dosyalar_haric(self):
        _yedek_yaz(self.tmp, EVRAK_AD + ".part")                 # script yarım arşivi
        _yedek_yaz(self.tmp, "erp_v01_ozel_x.tar.gz")
        _yedek_yaz(self.tmp, "erp_v01_ozel_20260601_030000.zip")
        _yedek_yaz(self.tmp, "erp_v01_ozel_20260601_030000.sql.gz")
        self.assertEqual(yedek_servis.yedekleri_listele(), [])

    def test_yol_guvenligi_evrak_adi(self):
        _yedek_yaz(self.tmp, EVRAK_AD)
        _yedek_yaz(self.tmp, EVRAK_AD + ".part")
        self.assertIsNotNone(yedek_servis.yedek_yolu(EVRAK_AD))
        for kotu in (EVRAK_AD + ".part", "../" + EVRAK_AD, "erp_v01_ozel_x.tar.gz",
                     "erp_v01_ozel_20260601_030000.tar.gz/../x", "yok_ozel_20260601_030000.tar.gz",
                     "erp_v01_ozel_20260602_030000.tar.gz"):   # sonuncusu diskte yok
            self.assertIsNone(yedek_servis.yedek_yolu(kotu), kotu)

    def test_boyut_h_evrak(self):
        _yedek_yaz(self.tmp, EVRAK_AD, b"x" * 3072)
        self.assertEqual(yedek_servis.yedekleri_listele()[0].boyut_h, "3.0 KB")


class YedekEvrakEkranTest(YedekTestTemel):
    def test_ekranda_evrak_arsivi_gorunur_ve_son_yedek_db(self):
        _yedek_yaz(self.tmp, DB_AD, b"x" * 2048)
        _yedek_yaz(self.tmp, EVRAK_AD, b"y" * 4096)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek"))
        self.assertContains(r, EVRAK_AD)
        self.assertContains(r, "Özlük evrakları")
        self.assertContains(r, "4.0 KB")
        self.assertContains(r, reverse("core:yedek_indir", args=[EVRAK_AD]))
        self.assertEqual(r.context["son_yedek"].ad, DB_AD)
        self.assertEqual(len(r.context["yedekler"]), 2)

    def test_evrak_arsivi_indirilir_attachment(self):
        _yedek_yaz(self.tmp, EVRAK_AD, b"TAR-ICERIK")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek_indir", args=[EVRAK_AD]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn(EVRAK_AD, r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), b"TAR-ICERIK")

    def test_evrak_arsivi_yetkisiz_403_gecersiz_404(self):
        _yedek_yaz(self.tmp, EVRAK_AD)
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("core:yedek_indir", args=[EVRAK_AD])).status_code, 403)
        self.client.force_login(self.yon)
        for kotu in (EVRAK_AD + ".part", "erp_v01_ozel_20260609_030000.tar.gz"):
            self.assertEqual(self.client.get(reverse("core:yedek_indir", args=[kotu])).status_code,
                             404, kotu)


MEDYA_AD = "erp_v01_medya_20260601_030000.tar.gz"


class YedekMedyaServisTest(YedekTestTemel):
    """Yüklenen görseller arşivi (erp_v01_medya_*.tar.gz) — DB/evrak yedeğiyle aynı ekranda."""

    def test_uc_tur_listelenir_tur_bilgisiyle(self):
        _yedek_yaz(self.tmp, DB_AD)
        _yedek_yaz(self.tmp, EVRAK_AD)
        _yedek_yaz(self.tmp, MEDYA_AD)
        tur = {y.ad: y.tur for y in yedek_servis.yedekleri_listele()}
        self.assertEqual(tur, {DB_AD: "DB", EVRAK_AD: "EVRAK", MEDYA_AD: "MEDYA"})
        adlar = {y.ad: y.tur_ad for y in yedek_servis.yedekleri_listele()}
        self.assertEqual(adlar[MEDYA_AD], "Yüklenen görseller")

    def test_zaman_damgasina_gore_yeni_once(self):
        _yedek_yaz(self.tmp, "erp_v01_medya_20260602_030000.tar.gz")
        _yedek_yaz(self.tmp, "erp_v01_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "erp_v01_medya_20260603_030000.tar.gz")
        self.assertEqual([y.ad for y in yedek_servis.yedekleri_listele()], [
            "erp_v01_medya_20260603_030000.tar.gz", "erp_v01_medya_20260602_030000.tar.gz",
            "erp_v01_20260601_030000.sql.gz"])

    def test_son_yedek_yalniz_db(self):
        _yedek_yaz(self.tmp, DB_AD)
        _yedek_yaz(self.tmp, "erp_v01_medya_20260701_030000.tar.gz")        # daha yeni, ama medya
        self.assertEqual(yedek_servis.son_yedek().ad, DB_AD)

    def test_yalniz_medya_varsa_son_yedek_yok(self):
        _yedek_yaz(self.tmp, MEDYA_AD)
        self.assertIsNone(yedek_servis.son_yedek())

    def test_yarim_ve_yabanci_dosyalar_haric(self):
        _yedek_yaz(self.tmp, MEDYA_AD + ".part")                 # script yarım arşivi
        _yedek_yaz(self.tmp, "erp_v01_medya_x.tar.gz")
        _yedek_yaz(self.tmp, "erp_v01_medya_20260601_030000.zip")
        _yedek_yaz(self.tmp, "erp_v01_medya_20260601_030000.sql.gz")
        _yedek_yaz(self.tmp, "semta_erp_medya_20260601_030000.tar.gz")
        self.assertEqual(yedek_servis.yedekleri_listele(), [])

    def test_yol_guvenligi_medya_adi(self):
        _yedek_yaz(self.tmp, MEDYA_AD)
        _yedek_yaz(self.tmp, MEDYA_AD + ".part")
        self.assertIsNotNone(yedek_servis.yedek_yolu(MEDYA_AD))
        for kotu in (MEDYA_AD + ".part", "../" + MEDYA_AD, "erp_v01_medya_x.tar.gz",
                     "erp_v01_medya_20260601_030000.tar.gz/../x",
                     "erp_v01_medya_20260602_030000.tar.gz"):   # sonuncusu diskte yok
            self.assertIsNone(yedek_servis.yedek_yolu(kotu), kotu)


class YedekMedyaEkranTest(YedekTestTemel):
    def test_ekranda_medya_arsivi_gorunur_ve_son_yedek_db(self):
        _yedek_yaz(self.tmp, DB_AD, b"x" * 2048)
        _yedek_yaz(self.tmp, MEDYA_AD, b"y" * 4096)
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek"))
        self.assertContains(r, MEDYA_AD)
        self.assertContains(r, "Yüklenen görseller")
        self.assertContains(r, "4.0 KB")
        self.assertContains(r, reverse("core:yedek_indir", args=[MEDYA_AD]))
        self.assertEqual(r.context["son_yedek"].ad, DB_AD)
        self.assertEqual(len(r.context["yedekler"]), 2)

    def test_medya_arsivi_indirilir_attachment(self):
        _yedek_yaz(self.tmp, MEDYA_AD, b"TAR-ICERIK")
        self.client.force_login(self.yon)
        r = self.client.get(reverse("core:yedek_indir", args=[MEDYA_AD]))
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertIn(MEDYA_AD, r["Content-Disposition"])
        self.assertEqual(b"".join(r.streaming_content), b"TAR-ICERIK")

    def test_medya_arsivi_yetkisiz_403_gecersiz_404(self):
        _yedek_yaz(self.tmp, MEDYA_AD)
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("core:yedek_indir", args=[MEDYA_AD])).status_code, 403)
        self.client.force_login(self.yon)
        for kotu in (MEDYA_AD + ".part", "erp_v01_medya_20260609_030000.tar.gz"):
            self.assertEqual(self.client.get(reverse("core:yedek_indir", args=[kotu])).status_code,
                             404, kotu)
