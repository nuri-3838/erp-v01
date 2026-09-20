"""scripts/nginx_media_kisitla.py — nginx /media/ yolunu genel klasörlerle sınırlar.

Betik gerçek nginx'e dokunmadan sınanır: config dönüşümü (donustur) saf fonksiyon olarak, komut
satırı davranışı (yedek, `nginx -t` başarısızsa/reload başarısızsa/nginx yoksa OTOMATİK geri alma,
idempotans, --kuru) geçici config + sahte `nginx`/`systemctl` kabuk betikleriyle çalıştırılır."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import skipIf

from django.conf import settings
from django.test import SimpleTestCase

from core.storage import GENEL_MEDYA_ONEKLERI, OZELE_TASINAN_ONEKLER

BETIK = Path(settings.BASE_DIR) / "scripts" / "nginx_media_kisitla.py"

_spec = importlib.util.spec_from_file_location("nginx_media_kisitla", BETIK)
betik = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(betik)

# Canlı config'in yapısı (sertifika yolları vb. sadeleştirilmiş).
CANLI_ORNEK = """\
server {
    listen 80;
    listen [::]:80;
    server_name test.semtahome.com;
    return 404;
}

server {
    server_name erp.semtahome.com;

    client_max_body_size 10M;

    location /static/ {
        alias /home/nuri/erp_v01/staticfiles/;
        expires 30d;
        access_log off;
    }
    location /media/ {
        alias /home/nuri/erp_v01/media/;
        expires 30d;
        access_log off;
    }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_redirect off;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/erp.semtahome.com/fullchain.pem; # managed by Certbot
}

server {
    if ($host = erp.semtahome.com) {
        return 301 https://$host$request_uri;
    } # managed by Certbot

    listen 80;
    server_name erp.semtahome.com;
    return 404; # managed by Certbot
}
"""


class DonusumTest(SimpleTestCase):
    def test_izinli_onekler_uygulama_ile_ayni(self):
        self.assertEqual(betik.IZINLI_ONEKLER, GENEL_MEDYA_ONEKLERI)

    def test_izinli_listede_gizli_klasor_yok(self):
        self.assertEqual(set(betik.IZINLI_ONEKLER) & set(OZELE_TASINAN_ONEKLER), set())
        self.assertEqual(set(betik.IZINLI_ONEKLER) & {"personel_belge", "personel_foto"}, set())

    def test_media_koku_404_ve_izinli_klasorler_alias_ile(self):
        yeni = betik.donustur(CANLI_ORNEK)
        self.assertRegex(yeni, r"location /media/ \{\n\s+return 404;\n\s+\}")
        for onek in GENEL_MEDYA_ONEKLERI:
            self.assertRegex(
                yeni, rf"location /media/{onek}/ \{{\n\s+alias /home/nuri/erp_v01/media/{onek}/;")
        # Çıplak kök alias (tüm media/) KALMAMALI.
        self.assertNotRegex(yeni, r"alias /home/nuri/erp_v01/media/;")
        # Her /media/... alias'ı izinli bir klasöre işaret eder.
        aliaslar = re.findall(r"alias (/home/nuri/erp_v01/media/[^;]*);", yeni)
        self.assertEqual(sorted(aliaslar),
                         sorted(f"/home/nuri/erp_v01/media/{o}/" for o in GENEL_MEDYA_ONEKLERI))

    def test_bloklarin_disi_aynen_korunur(self):
        yeni = betik.donustur(CANLI_ORNEK)
        blok = re.search(r"    location /media/ \{.*?\n    \}\n", CANLI_ORNEK, re.S)
        bas, son = CANLI_ORNEK[:blok.start()], CANLI_ORNEK[blok.end():]
        self.assertTrue(yeni.startswith(bas))
        self.assertTrue(yeni.endswith(son))
        for korunan in ("location /static/ {", "location / {", "proxy_pass http://127.0.0.1:8001;",
                        "client_max_body_size 10M;", "# managed by Certbot"):
            self.assertIn(korunan, yeni)

    def test_idempotent(self):
        bir = betik.donustur(CANLI_ORNEK)
        self.assertEqual(betik.donustur(bir), bir)
        self.assertEqual(bir.count("location /media/ {"), 1)

    def test_girinti_korunur(self):
        sekme = CANLI_ORNEK.replace("    location /media/", "\tlocation /media/").replace(
            "        alias /home/nuri/erp_v01/media/;", "\t\talias /home/nuri/erp_v01/media/;")
        yeni = betik.donustur(sekme)
        self.assertIn("\tlocation /media/stok_gorsel/ {", yeni)
        self.assertIn("\t    alias /home/nuri/erp_v01/media/stok_gorsel/;", yeni)

    def test_beklenmeyen_yapilarda_hata(self):
        with self.assertRaises(betik.DonusumHatasi):
            betik.donustur("server {\n    location / {\n        return 200;\n    }\n}\n")
        iki = CANLI_ORNEK + "\nserver {\n    location /media/ {\n        alias /x/;\n    }\n}\n"
        with self.assertRaises(betik.DonusumHatasi):
            betik.donustur(iki)
        aliassiz = CANLI_ORNEK.replace("        alias /home/nuri/erp_v01/media/;\n", "")
        with self.assertRaises(betik.DonusumHatasi):
            betik.donustur(aliassiz)


class KomutSatiriTest(SimpleTestCase):
    def setUp(self):
        self.dizin = Path(tempfile.mkdtemp(prefix="nginx_test_"))
        self.addCleanup(shutil.rmtree, self.dizin, ignore_errors=True)
        self.config = self.dizin / "erp-v01"
        self.config.write_text(CANLI_ORNEK, encoding="utf-8")
        self.sahte = self.dizin / "sahte_bin"
        self.sahte.mkdir()

    def _sahte(self, ad, cikis):
        yol = self.sahte / ad
        yol.write_text(f"#!/bin/sh\necho \"{ad} calisti\"\nexit {cikis}\n")
        yol.chmod(0o755)

    def calistir(self, *args):
        env = dict(os.environ, PATH=str(self.sahte))        # gerçek nginx/systemctl görünmez
        return subprocess.run([sys.executable, str(BETIK), "--config", str(self.config), *args],
                              env=env, capture_output=True, text=True, timeout=30)

    def yedekler(self):
        return sorted(self.dizin.glob("erp-v01.bak_media_*"))

    def test_kuru_calisma_yazmaz(self):
        s = self.calistir("--kuru")
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertIn("location /media/stok_gorsel/", s.stdout)
        self.assertIn("KURU CALISMA", s.stdout)
        self.assertEqual(self.config.read_text(encoding="utf-8"), CANLI_ORNEK)
        self.assertEqual(self.yedekler(), [])

    def test_uygular_yedek_alir_nginx_test_ve_reload(self):
        self._sahte("nginx", 0)
        self._sahte("systemctl", 0)
        s = self.calistir()
        self.assertEqual(s.returncode, 0, s.stdout + s.stderr)
        self.assertIn("TAMAM", s.stdout)
        self.assertEqual(self.config.read_text(encoding="utf-8"), betik.donustur(CANLI_ORNEK))
        (yedek,) = self.yedekler()
        self.assertEqual(yedek.read_text(encoding="utf-8"), CANLI_ORNEK)     # tam yedek
        self.assertIn(f"sudo cp {yedek} {self.config}", s.stdout)           # geri alma komutu

    def test_tekrar_calistirma_yeni_yedek_almaz(self):
        self._sahte("nginx", 0)
        self._sahte("systemctl", 0)
        self.calistir()
        s = self.calistir()
        self.assertEqual(s.returncode, 0)
        self.assertIn("Zaten uygulanmis", s.stdout)
        self.assertEqual(len(self.yedekler()), 1)

    def test_nginx_test_basarisizsa_otomatik_geri_alinir(self):
        self._sahte("nginx", 1)
        self._sahte("systemctl", 0)
        s = self.calistir()
        self.assertEqual(s.returncode, 1)
        self.assertIn("nginx -t BASARISIZ", s.stdout)
        self.assertIn("GERI ALINDI", s.stdout)
        self.assertEqual(self.config.read_text(encoding="utf-8"), CANLI_ORNEK)

    def test_reload_basarisizsa_otomatik_geri_alinir(self):
        self._sahte("nginx", 0)
        self._sahte("systemctl", 1)
        s = self.calistir()
        self.assertEqual(s.returncode, 1)
        self.assertIn("reload nginx BASARISIZ", s.stdout)
        self.assertEqual(self.config.read_text(encoding="utf-8"), CANLI_ORNEK)

    def test_nginx_yoksa_dogrulanmamis_config_birakilmaz(self):
        s = self.calistir()                                  # sahte_bin bos: nginx bulunamaz
        self.assertEqual(s.returncode, 1)
        self.assertIn("GERI ALINDI", s.stdout)
        self.assertEqual(self.config.read_text(encoding="utf-8"), CANLI_ORNEK)

    def test_beklenmeyen_config_hata_kodu_2_dokunmaz(self):
        self.config.write_text("server { listen 80; }\n", encoding="utf-8")
        s = self.calistir()
        self.assertEqual(s.returncode, 2)
        self.assertIn("HATA", s.stderr)
        self.assertEqual(self.config.read_text(encoding="utf-8"), "server { listen 80; }\n")

    @skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root için yazma izni hatası oluşmaz")
    def test_yazma_izni_yoksa_sudo_ipucu(self):
        self.config.chmod(0o444)
        s = self.calistir()
        self.assertEqual(s.returncode, 3)
        self.assertIn("sudo", s.stderr)
        self.assertEqual(self.config.read_text(encoding="utf-8"), CANLI_ORNEK)
