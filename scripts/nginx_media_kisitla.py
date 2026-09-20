#!/usr/bin/env python3
"""nginx: /media/ yolunu GENEL görsel klasörleriyle sınırlar (geri kalan her şey 404).

Sorun: `location /media/ { alias .../media/; }` kimlik doğrulamasız sunar; MEDIA_ROOT'a yanlışlıkla
konan her dosya internete açık olur. Çözüm: yalnız IZINLI_ONEKLER (stok görselleri, banka/firma
logoları) sunulur; gizli yüklemeler MEDIA_ROOT dışındaki özel depoda durur ve yalnız Django'nun
yetkili görünümleriyle sunulur (bkz. core/storage.py). IZINLI_ONEKLER, core/storage.py içindeki
GENEL_MEDYA_ONEKLERI ile AYNI olmak zorundadır (core/tests/test_nginx_media.py denetler).

Kullanım (sunucuda, repo dizininden):
  python3 scripts/nginx_media_kisitla.py --kuru     # ne değişeceğini gösterir (yazmaz, sudo gerekmez)
  sudo python3 scripts/nginx_media_kisitla.py       # uygular: yedek -> yaz -> nginx -t -> reload
                                                    # nginx -t ya da reload başarısızsa OTOMATİK geri alır
Geri alma (elle): sudo cp <config>.bak_media_<zaman> <config> && sudo nginx -t && sudo systemctl reload nginx
Tekrar çalıştırılabilir (zaten uygulanmışsa hiçbir şey yapmaz).
"""
import argparse
import difflib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VARSAYILAN_CONFIG = "/etc/nginx/sites-available/erp-v01"
# core/storage.py::GENEL_MEDYA_ONEKLERI ile aynı (test denetler).
IZINLI_ONEKLER = ("stok_gorsel", "banka_logo", "firma_logo")
ISARET = "# nginx_media_kisitla.py"

_BLOK = re.compile(r"^(?P<girinti>[ \t]*)location[ \t]+/media/[ \t]*\{(?P<govde>[^{}]*)\}[ \t]*\n",
                   re.M)
_ALIAS = re.compile(r"alias[ \t]+(?P<yol>[^;\s]+)[ \t]*;")


class DonusumHatasi(Exception):
    pass


def donustur(metin: str) -> str:
    """Config metnini döndürür: tek `location /media/ {alias ...}` bloğu, /media/ için 404 +
    izinli klasörlerin alias'lı blokları olur. Zaten uygulanmışsa metni AYNEN döndürür."""
    if ISARET in metin:
        return metin
    bloklar = list(_BLOK.finditer(metin))
    if len(bloklar) != 1:
        raise DonusumHatasi(
            f"'location /media/ {{ ... }}' bloğu tam 1 adet olmalı, bulunan: {len(bloklar)}")
    m = bloklar[0]
    alias = _ALIAS.search(m["govde"])
    if not alias:
        raise DonusumHatasi("'location /media/' bloğunda alias yok; beklenmeyen yapı.")
    kok = alias["yol"].rstrip("/")
    g = m["girinti"]
    satirlar = [
        f"{g}{ISARET}: /media/ yalniz genel gorsel klasorleriyle sinirli (girissiz sunulur);",
        f"{g}# gizli yuklemeler ozel depoda, Django'nun yetkili gorunumleriyle sunulur.",
        f"{g}location /media/ {{",
        f"{g}    return 404;",
        f"{g}}}",
    ]
    for onek in IZINLI_ONEKLER:
        satirlar += [
            f"{g}location /media/{onek}/ {{",
            f"{g}    alias {kok}/{onek}/;",
            f"{g}    expires 30d;",
            f"{g}    access_log off;",
            f"{g}}}",
        ]
    return metin[:m.start()] + "\n".join(satirlar) + "\n" + metin[m.end():]


def _calistir(komut):
    """Komut bulunamazsa/çalışamazsa istisna atmaz, başarısız (127) sayar: yeni config
    doğrulanmadan bırakılmasın, çağıran geri alsın."""
    try:
        return subprocess.run(komut, capture_output=True, text=True)
    except OSError as e:
        return subprocess.CompletedProcess(komut, 127, "", str(e))


def _geri_al(config: Path, yedek: Path):
    shutil.copy2(yedek, config)
    t = _calistir(["nginx", "-t"])
    print("GERI ALINDI:", config, "<-", yedek)
    print("  (geri alinan config icin nginx -t:", "gecti" if t.returncode == 0 else "BASARISIZ", ")")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="nginx /media/ yolunu genel klasorlerle sinirlar.")
    p.add_argument("--config", default=VARSAYILAN_CONFIG, help="nginx site config yolu")
    p.add_argument("--kuru", action="store_true", help="yalniz farki goster; yazma")
    a = p.parse_args(argv)
    config = Path(a.config)
    eski = config.read_text(encoding="utf-8")
    try:
        yeni = donustur(eski)
    except DonusumHatasi as e:
        print("HATA:", e, file=sys.stderr)
        return 2
    if yeni == eski:
        print("Zaten uygulanmis; yapilacak bir sey yok.")
        return 0
    fark = "".join(difflib.unified_diff(eski.splitlines(True), yeni.splitlines(True),
                                        str(config), str(config) + " (yeni)"))
    print(fark)
    if a.kuru:
        print("KURU CALISMA: hicbir sey yazilmadi.")
        return 0
    yedek = config.with_name(config.name + ".bak_media_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    try:
        shutil.copy2(config, yedek)
        config.write_text(yeni, encoding="utf-8")
    except PermissionError:
        print("HATA: yazma izni yok — 'sudo python3 scripts/nginx_media_kisitla.py' ile calistirin.",
              file=sys.stderr)
        return 3
    print("Yedek:", yedek)
    t = _calistir(["nginx", "-t"])
    if t.returncode != 0:
        print("nginx -t BASARISIZ:\n" + (t.stderr or t.stdout))
        _geri_al(config, yedek)
        return 1
    r = _calistir(["systemctl", "reload", "nginx"])
    if r.returncode != 0:
        print("systemctl reload nginx BASARISIZ:\n" + (r.stderr or r.stdout))
        _geri_al(config, yedek)
        return 1
    print("TAMAM: nginx yeniden yuklendi. Geri alma icin:")
    print(f"  sudo cp {yedek} {config} && sudo nginx -t && sudo systemctl reload nginx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
