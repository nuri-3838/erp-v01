"""scripts/db_backup.sh — özlük evrakı (ozel) ve yüklenen görseller (medya) arşiv adımları.

GERÇEK betik geçici bir REPO dizininde (ERP_REPO) çalıştırılır; `pg_dump` sahte bir kabuk
betiğiyle değiştirilir (gerçek veritabanına/dizinlere dokunulmaz). Hata senaryolarında `tar`
da sahte bir sarmalayıcıyla bozulur: betik exit 1 dönmeli, yarım (.part) dosya kalmamalı.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

BETIK = Path(settings.BASE_DIR) / "scripts" / "db_backup.sh"
ARACLAR = ("bash", "tar", "gzip", "gunzip", "find", "du")
HAZIR = BETIK.is_file() and all(shutil.which(a) for a in ARACLAR)


@skipUnless(HAZIR, "bash/tar/gzip araçları ya da yedek betiği yok")
class YedekBetigiTest(SimpleTestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="yedek_repo_"))
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        (self.repo / "logs").mkdir()
        (self.repo / ".env").write_text(
            "DB_NAME=x\nDB_USER=x\nDB_PASSWORD=x\nDB_HOST=127.0.0.1\nDB_PORT=5432\n")
        self.sahte_bin = self.repo / "sahte_bin"
        self.sahte_bin.mkdir()
        self._betik("pg_dump", "#!/bin/sh\necho 'SELECT 1;'\n")
        self.yedekler = self.repo / "backups"

    def _betik(self, ad, icerik):
        yol = self.sahte_bin / ad
        yol.write_text(icerik)
        yol.chmod(0o755)

    def _dosya(self, *parcalar, icerik=b"x"):
        yol = self.repo.joinpath(*parcalar)
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_bytes(icerik)
        return yol

    def calistir(self):
        env = dict(os.environ, ERP_REPO=str(self.repo),
                   PATH=f"{self.sahte_bin}{os.pathsep}{os.environ['PATH']}")
        return subprocess.run(["bash", str(BETIK)], env=env, capture_output=True, text=True,
                              timeout=60)

    def yedek_adlari(self, desen):
        return sorted(p.name for p in self.yedekler.glob(desen))

    def log(self):
        return (self.repo / "logs" / "backup.log").read_text(encoding="utf-8")

    def arsiv_icerigi(self, yol):
        s = subprocess.run(["tar", "-tzf", str(yol)], capture_output=True, text=True, check=True)
        return s.stdout.split()

    def test_medya_arsivi_uretilir_gecerli_ve_ozel_izinli(self):
        self._dosya("media", "stok_gorsel", "a21.webp", icerik=b"RIFF-sahte")
        self._dosya("media", "banka_logo", "ziraat.webp")
        s = self.calistir()
        self.assertEqual(s.returncode, 0, s.stderr)
        (medya,) = self.yedek_adlari("erp_v01_medya_*.tar.gz")
        yol = self.yedekler / medya
        icerik = self.arsiv_icerigi(yol)
        self.assertIn("media/stok_gorsel/a21.webp", icerik)
        self.assertIn("media/banka_logo/ziraat.webp", icerik)
        self.assertEqual(stat.S_IMODE(yol.stat().st_mode), 0o600)
        self.assertEqual(len(self.yedek_adlari("erp_v01_*.sql.gz")), 1)   # DB dökümü de alındı
        self.assertEqual(self.yedek_adlari("*.part"), [])
        self.assertIn(f"OK: {medya}", self.log())

    def test_medya_dizini_yoksa_ya_da_bossa_atlanir(self):
        s = self.calistir()                                    # media/ hiç yok
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(self.yedek_adlari("erp_v01_medya_*"), [])
        (self.repo / "media").mkdir()                          # var ama boş
        s = self.calistir()
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(self.yedek_adlari("erp_v01_medya_*"), [])

    def test_ozel_ve_medya_ayri_arsivlerdir(self):
        self._dosya("media", "stok_gorsel", "a21.webp")
        self._dosya("ozel_dosyalar", "cari_aktivite", "gizli.webp")
        s = self.calistir()
        self.assertEqual(s.returncode, 0, s.stderr)
        (medya,) = self.yedek_adlari("erp_v01_medya_*.tar.gz")
        (ozel,) = self.yedek_adlari("erp_v01_ozel_*.tar.gz")
        self.assertCountEqual(self.arsiv_icerigi(self.yedekler / medya),
                              ["media/", "media/stok_gorsel/", "media/stok_gorsel/a21.webp"])
        self.assertIn("ozel_dosyalar/cari_aktivite/gizli.webp",
                      self.arsiv_icerigi(self.yedekler / ozel))
        # Özel dosyalar (gizli) medya arşivine SIZMAMALI.
        self.assertFalse([i for i in self.arsiv_icerigi(self.yedekler / medya)
                          if "gizli" in i or i.startswith("ozel_dosyalar")])

    def test_tar_hatasinda_exit_1_ve_yarim_dosya_kalmaz(self):
        self._dosya("media", "stok_gorsel", "a21.webp")
        gercek = shutil.which("tar")
        self._betik("tar", f"#!/bin/sh\ncase \"$*\" in *medya*) echo 'tar: hata' >&2; exit 2;; "
                           f"esac\nexec {gercek} \"$@\"\n")
        s = self.calistir()
        self.assertEqual(s.returncode, 1)
        self.assertEqual(self.yedek_adlari("erp_v01_medya_*"), [])       # .part dahil
        self.assertEqual(len(self.yedek_adlari("erp_v01_*.sql.gz")), 1)  # DB dökümü korunur
        self.assertIn("HATA: erp_v01_medya_", self.log())

    def test_bozuk_arsiv_dogrulamada_yakalanir(self):
        self._dosya("media", "stok_gorsel", "a21.webp")
        gercek = shutil.which("tar")
        # 'tar -czf <medya .part>' başarılı görünür ama bozuk dosya yazar → tar -tzf yakalamalı.
        self._betik("tar", "#!/bin/sh\nif [ \"$1\" = \"-czf\" ]; then case \"$2\" in "
                           "*medya*) printf 'bozuk' > \"$2\"; exit 0;; esac; fi\n"
                           f"exec {gercek} \"$@\"\n")
        s = self.calistir()
        self.assertEqual(s.returncode, 1)
        self.assertEqual(self.yedek_adlari("erp_v01_medya_*"), [])
        self.assertIn("HATA: erp_v01_medya_", self.log())

    def test_retention_eski_medya_silinir_diger_turler_ve_yeni_kalir(self):
        self._dosya("media", "stok_gorsel", "a21.webp")
        eski_ad, yeni_ad = "erp_v01_medya_20200101_000000.tar.gz", "erp_v01_medya_20990101_000000.tar.gz"
        ozel_eski = "erp_v01_ozel_20200101_000000.tar.gz"
        self.yedekler.mkdir()
        for ad in (eski_ad, yeni_ad, ozel_eski):
            (self.yedekler / ad).write_bytes(b"x")
        yirmi_gun_once = time.time() - 20 * 86400
        for ad in (eski_ad, ozel_eski):
            os.utime(self.yedekler / ad, (yirmi_gun_once, yirmi_gun_once))
        s = self.calistir()
        self.assertEqual(s.returncode, 0, s.stderr)
        adlar = self.yedek_adlari("erp_v01_*.tar.gz")
        self.assertNotIn(eski_ad, adlar)          # 15 günden eski medya arşivi silindi
        self.assertIn(yeni_ad, adlar)
        self.assertIn(ozel_eski, adlar)           # medya retention'ı özel arşive dokunmaz
        medyalar = [a for a in adlar if a.startswith("erp_v01_medya_")]
        self.assertEqual(len(medyalar), 2)        # 2099'lu (dokunulmayan) + bu çalıştırmanınki
