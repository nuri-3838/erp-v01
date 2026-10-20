"""Google Drive yedeği (core.services.drive_yedek): gerçek gpg ile simetrik şifreleme, taklit rclone ile yükleme/doğrulama/silme (core/tests/fake_rclone.py); gecelik takım seçimi, durum dosyası,
hatada log + tekrar deneme, boyut doğrulaması, saklama kuralı (30 gün günlük + 12 hafta haftalık; yalnız kendi yüklediği ve desene uyan dosyalar), komut çıktısı/çıkış kodu."""
import datetime
import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from core.services import drive_yedek as dy

D = datetime.date
SAHTE = str(Path(__file__).with_name("fake_rclone.py"))


class DriveTaban(SimpleTestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        k = Path(self.t.name)
        self.yedek, self.kok, self.gnupg = k / "backups", k / "drive", k / "gnupg"
        for d in (self.yedek, self.kok, self.gnupg):
            d.mkdir()
        os.chmod(self.gnupg, 0o700)
        self.anahtar = k / "anahtar"
        self.anahtar.write_text("test-parolasi-cok-gizli-123", encoding="utf-8")
        os.chmod(self.anahtar, 0o600)
        self.ortam = {"FAKE_RCLONE_ROOT": str(self.kok), "GNUPGHOME": str(self.gnupg), "FAKE_RCLONE_FAIL": str(k / "hata"), "FAKE_RCLONE_KESIK": str(k / "kesik")}
        eski = {a: os.environ.get(a) for a in self.ortam}
        os.environ.update(self.ortam)
        self.addCleanup(lambda: [os.environ.pop(a) if v is None else os.environ.__setitem__(a, v) for a, v in eski.items()])
        self.ayar = dy.Ayar(yedek_dizini=self.yedek, durum_dosyasi=k / "durum.json", log_dosyasi=k / "log.txt", anahtar_dosyasi=self.anahtar,
                            uzak="erpdrive:ERP_YEDEK", rclone=(sys.executable, SAHTE))
        self.k = k

    def takim(self, damga, icerik="x"):
        for onek, uzanti in (("", "sql.gz"), ("ozel_", "tar.gz"), ("medya_", "tar.gz")):
            (self.yedek / f"erp_v01_{onek}{damga}.{uzanti}").write_bytes(f"{icerik}-{onek}-{damga}".encode() * 50)

    def uzak(self):
        d = self.kok / "ERP_YEDEK"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def coz(self, uzak_ad):
        sonuc = subprocess.run(["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase-file", str(self.anahtar), "-d", str(self.kok / "ERP_YEDEK" / uzak_ad)], capture_output=True)
        self.assertEqual(sonuc.returncode, 0, sonuc.stderr)
        return sonuc.stdout


class YuklemeTest(DriveTaban):
    def test_gecelik_takimlar_sifreli_yuklenir_deploy_takimi_yuklenmez_ikinci_calisma_bos(self):
        self.takim("20261018_000001")
        self.takim("20261018_093000")                       # aynı günün deploy yedeği: yüklenmez
        self.takim("20261019_000002")
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertEqual(r["hatalar"], [])
        self.assertEqual(self.uzak(), sorted(f"{a}.gpg" for a in (p.name for p in self.yedek.iterdir()) if "0930" not in a))
        self.assertEqual(len(self.uzak()), 6)
        self.assertEqual(self.coz("erp_v01_20261019_000002.sql.gz.gpg"), (self.yedek / "erp_v01_20261019_000002.sql.gz").read_bytes())       # şifre çözülünce özdeş
        self.assertNotIn(b"20261019", (self.kok / "ERP_YEDEK" / "erp_v01_20261019_000002.sql.gz.gpg").read_bytes())                          # Drive'daki dosya şifreli (düz metin yok)
        d = dy.durum_oku(self.ayar)
        self.assertEqual(len(d["dosyalar"]), 6)
        self.assertEqual(d["dosyalar"]["erp_v01_ozel_20261018_000001.tar.gz.gpg"]["kaynak_boyut"], (self.yedek / "erp_v01_ozel_20261018_000001.tar.gz").stat().st_size)
        self.assertIsNotNone(d["son_basarili"])
        self.assertEqual(dy.calistir(self.ayar, D(2026, 10, 19))["yuklenen"], [])                                                            # ikinci çalışma: yüklenecek yok

    def test_hata_log_ve_sonraki_calismada_tekrar_deneme(self):
        self.takim("20261019_000002")
        (self.k / "hata").write_text("1")
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertEqual(len(r["hatalar"]), 1)
        self.assertEqual(self.uzak(), [])
        self.assertIn("HATA:", self.ayar.log_dosyasi.read_text(encoding="utf-8"))
        d = dy.durum_oku(self.ayar)
        self.assertEqual((d["dosyalar"], "son_hata" in d), ({}, True))
        (self.k / "hata").unlink()
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertEqual((r["hatalar"], len(self.uzak())), ([], 3))
        self.assertNotIn("son_hata", dy.durum_oku(self.ayar))

    def test_yarim_kalan_takim_kalan_dosyalarla_tamamlanir(self):
        self.takim("20261019_000002")
        d = dy.durum_oku(self.ayar)
        ilk = sorted(self.yedek.iterdir())[0]
        dy.yukle(self.ayar, d, "20261019_000002", [ilk])                                  # takımın yalnız bir dosyası yüklenmiş
        self.assertEqual(len(self.uzak()), 1)
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertEqual((r["hatalar"], len(self.uzak()), len(r["yuklenen"])), ([], 3, 2))

    def test_boyut_uyusmazligi_kismi_dosya_silinir_hata(self):
        self.takim("20261019_000002")
        (self.k / "kesik").write_text("1")
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertTrue(any("boyut uyuşmuyor" in h for h in r["hatalar"]))
        self.assertEqual(self.uzak(), [])
        self.assertEqual(dy.durum_oku(self.ayar)["dosyalar"], {})

    def test_parola_dosyasi_yoksa_hata_ve_hicbir_sey_yuklenmez(self):
        self.takim("20261019_000002")
        self.anahtar.unlink()
        r = dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertTrue(any("parola dosyası" in h for h in r["hatalar"]))
        self.assertEqual(self.uzak(), [])

    def test_kuru_calisma_yazmaz(self):
        self.takim("20261019_000002")
        r = dy.calistir(self.ayar, D(2026, 10, 19), kuru=True)
        self.assertEqual((self.uzak(), len(r["yuklenen"])), ([], 1))
        self.assertFalse(self.ayar.durum_dosyasi.exists())

    def test_geriye_gun_siniri(self):
        self.takim("20261001_000001")                                                      # 18 gün önce: yerel saklama (15) dışı → yüklenmez
        self.takim("20261010_000001")
        dy.calistir(self.ayar, D(2026, 10, 19))
        self.assertTrue(all("20261010" in a for a in self.uzak()))


class SaklamaTest(DriveTaban):
    def test_plan_30_gun_gunluk_12_hafta_haftalik_en_yeni_silinmez(self):
        bugun = D(2026, 10, 20)
        tum = [bugun - datetime.timedelta(days=i) for i in range(0, 120)]
        tut, sil = dy.saklama_plani(tum, bugun)
        self.assertTrue(all(d in tut for d in tum if (bugun - d).days < 30))                # son 30 gün hepsi
        haftalik = [d for d in tut if (bugun - d).days >= 30]
        self.assertTrue(all((bugun - d).days < 84 for d in haftalik))
        self.assertEqual(len({d.isocalendar()[:2] for d in haftalik}), len(haftalik))      # hafta başına tek
        self.assertEqual(max(tum), max(tut))
        self.assertTrue(all((bugun - d).days >= 30 for d in sil))
        self.assertTrue(all(d not in tut for d in sil))
        self.assertEqual(len(tut) + len(sil), len(tum))
        self.assertTrue(all((bugun - d).days >= 84 or d.isoweekday() != 7 for d in sil))   # 84 günden yeni ve pazar olan haftalık yedek silinmez
        self.assertLessEqual(len(tut), 30 + 12)

    def test_plan_bos_ve_tek(self):
        self.assertEqual(dy.saklama_plani([], D(2026, 10, 20)), ([], []))
        self.assertEqual(dy.saklama_plani([D(2020, 1, 1)], D(2026, 10, 20)), ([D(2020, 1, 1)], []))   # tek yedek eski olsa da (en yeni) silinmez

    def kur_uzak(self, gunler, bugun):
        d = dy.durum_oku(self.ayar)
        (self.kok / "ERP_YEDEK").mkdir(exist_ok=True)
        for g in gunler:
            t = bugun - datetime.timedelta(days=g)
            for onek, uz in (("", "sql.gz"), ("ozel_", "tar.gz"), ("medya_", "tar.gz")):
                ad = f"erp_v01_{onek}{t:%Y%m%d}_000001.{uz}.gpg"
                (self.kok / "ERP_YEDEK" / ad).write_bytes(b"x")
                d["dosyalar"][ad] = {"boyut": 1}
        dy.durum_yaz(self.ayar, d)

    def test_saklama_yalniz_kendi_yukledigi_desene_uyan_dosyalari_siler(self):
        bugun = D(2026, 10, 20)
        self.kur_uzak(range(0, 100), bugun)
        yabanci = self.kok / "ERP_YEDEK" / "benim_belgem.pdf"
        yabanci.write_bytes(b"y")
        kayitsiz = self.kok / "ERP_YEDEK" / "erp_v01_20250101_000001.sql.gz.gpg"                  # desene uyar ama durum dosyasında yok
        kayitsiz.write_bytes(b"z")
        sonuc = dy.calistir(self.ayar, bugun, yukle_=False)
        self.assertEqual(sonuc["hatalar"], [])
        self.assertTrue(yabanci.exists() and kayitsiz.exists())
        kalan = {dy.tarih_damga(a)[0] for a in self.uzak() if dy.tarih_damga(a) and dy.tarih_damga(a)[0].year == 2026}
        tut, _ = dy.saklama_plani([bugun - datetime.timedelta(days=g) for g in range(0, 100)], bugun)
        self.assertEqual(kalan, set(tut))
        self.assertTrue(all(len([a for a in self.uzak() if dy.tarih_damga(a) and dy.tarih_damga(a)[0] == t]) == 3 for t in kalan))              # takım bütün (3 dosya) silinir/kalır
        self.assertEqual(sorted(dy.durum_oku(self.ayar)["dosyalar"]), sorted(a for a in self.uzak() if dy.tarih_damga(a) and dy.tarih_damga(a)[0].year == 2026))   # durum dosyası senkron

    def test_saklama_kuru_calisma_silmez(self):
        bugun = D(2026, 10, 20)
        self.kur_uzak(range(0, 60), bugun)
        once = self.uzak()
        sonuc = dy.calistir(self.ayar, bugun, yukle_=False, kuru=True)
        self.assertGreater(len(sonuc["silinen"]), 0)
        self.assertEqual(self.uzak(), once)

    def test_haftalik_yedek_pazar_gunu_korunur(self):
        bugun = D(2026, 10, 20)                                                             # Salı
        self.kur_uzak(range(0, 100), bugun)
        dy.calistir(self.ayar, bugun, yukle_=False)
        eski = sorted({dy.tarih_damga(a)[0] for a in self.uzak() if (bugun - dy.tarih_damga(a)[0]).days >= 30})
        self.assertTrue(eski)
        self.assertTrue(all(d.isoweekday() == 7 for d in eski))                            # 30+ gün olanlar yalnız haftanın son günü (Pazar)
        self.assertEqual(len(eski), len(set(d.isocalendar()[:2] for d in eski)))
        self.assertLessEqual((bugun - eski[0]).days, 83)


class KomutTest(DriveTaban):
    def test_komut_ozet_ve_hata_cikis_kodu(self):
        self.takim("20261019_000002")
        env = {"DRIVE_YEDEK_DIZIN": str(self.yedek), "DRIVE_YEDEK_DURUM": str(self.ayar.durum_dosyasi), "DRIVE_YEDEK_LOG": str(self.ayar.log_dosyasi), "DRIVE_YEDEK_ANAHTAR": str(self.anahtar),
               "RCLONE_YOLU": f"{sys.executable} {SAHTE}"}
        eski = {a: os.environ.get(a) for a in env}
        os.environ.update(env)
        try:
            cikti = StringIO()
            call_command("drive_yedek", "--bugun", "2026-10-19", stdout=cikti)
            self.assertIn("Yüklenen: 3", cikti.getvalue())
            self.assertIn("Tamam.", cikti.getvalue())
            self.assertEqual(dy.ozet(self.ayar)["dosya_sayisi"], 3)
            self.takim("20261020_000003")
            (self.k / "hata").write_text("1")
            with self.assertRaisesMessage(CommandError, "sonraki çalışmada tekrar denenir"):
                call_command("drive_yedek", "--bugun", "2026-10-20", stdout=StringIO(), stderr=StringIO())
        finally:
            for a, v in eski.items():
                os.environ.pop(a) if v is None else os.environ.__setitem__(a, v)
        self.assertIsNotNone(dy.ozet(self.ayar)["son_hata"])
