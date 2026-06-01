"""Yedek (DB backup) dosya işlemleri — Aşama 1 motorunu (scripts/db_backup.sh) sarar.

Ekran katmanı yalnızca: listeleme, elle tetikleme (yedek_al) ve indirme için yol çözümü.
GERİ YÜKLEME burada KASITLI olarak YOKTUR (tehlikeli; yanlış basılır).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings

# erp_v01_YYYYMMDD_HHMMSS.sql.gz  — Aşama 1 scriptinin ürettiği ad biçimi.
_AD_DESEN = re.compile(r"^erp_v01_(\d{8})_(\d{6})\.sql\.gz$")


def yedek_dizini() -> Path:
    return Path(getattr(settings, "BACKUP_DIR", settings.BASE_DIR / "backups"))


def _script_yolu() -> Path:
    return Path(getattr(settings, "BACKUP_SCRIPT",
                        settings.BASE_DIR / "scripts" / "db_backup.sh"))


@dataclass(frozen=True)
class YedekDosya:
    ad: str
    boyut: int       # bayt
    tarih: datetime  # dosya adındaki zaman damgası (yoksa dosya mtime)

    @property
    def boyut_h(self) -> str:
        b = self.boyut
        if b < 1024:
            return f"{b} B"
        if b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        if b < 1024 ** 3:
            return f"{b / 1024 ** 2:.1f} MB"
        return f"{b / 1024 ** 3:.2f} GB"


def _ad_tarihi(ad: str, yol: Path) -> datetime:
    m = _AD_DESEN.match(ad)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(yol.stat().st_mtime)


def yedekleri_listele() -> list:
    """Mevcut yedek dosyaları (YedekDosya), en yeni önce."""
    d = yedek_dizini()
    if not d.is_dir():
        return []
    out = []
    for yol in d.glob("erp_v01_*.sql.gz"):
        if yol.is_file() and _AD_DESEN.match(yol.name):
            out.append(YedekDosya(ad=yol.name, boyut=yol.stat().st_size,
                                  tarih=_ad_tarihi(yol.name, yol)))
    out.sort(key=lambda y: y.tarih, reverse=True)
    return out


def son_yedek():
    yedekler = yedekleri_listele()
    return yedekler[0] if yedekler else None


def yedek_yolu(ad: str):
    """İndirme için güvenli yol çözümü. Geçersiz ad / dizin dışı / yok => None.

    Hem ad deseni hem de gerçek üst-dizin kontrolü ile path-traversal engellenir.
    """
    if not _AD_DESEN.match(ad or ""):
        return None
    d = yedek_dizini().resolve()
    yol = (d / ad).resolve()
    if yol.parent != d or not yol.is_file():
        return None
    return yol


def yedek_al(timeout: int = 600):
    """Aşama 1 motorunu (scripts/db_backup.sh) çalıştırır.

    (basari: bool, mesaj: str) döner. Script kendi içinde pg_dump + gzip + bütünlük
    testi + retention + log yapar; burada yalnız tetiklenir ve sonucu raporlanır.
    """
    script = _script_yolu()
    if not script.is_file():
        return False, "Yedek scripti bulunamadı (scripts/db_backup.sh)."
    try:
        sonuc = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Yedek zaman aşımına uğradı."
    except OSError as e:
        return False, f"Yedek başlatılamadı: {e}"
    if sonuc.returncode != 0:
        ayrinti = (sonuc.stderr or sonuc.stdout or "").strip()
        return False, f"Yedek başarısız (kod {sonuc.returncode}). {ayrinti[-300:]}"
    return True, "Yedek başarıyla alındı."


def yedek_al_arkaplan():
    """Yedeği ARKA PLANDA başlatır (web isteğini bekletmez); büyük DB'de sayfa donmaz.

    Süreç ayrı bir oturumda (start_new_session) başlatılır; sonuç + bütünlük testi
    ``logs/backup.log``'a yazılır, yeni dosya birkaç saniye içinde listede görünür.
    (basari: bool, mesaj: str) — yalnız BAŞLATILABİLDİ mi bilgisini döner.
    """
    script = _script_yolu()
    if not script.is_file():
        return False, "Yedek scripti bulunamadı (scripts/db_backup.sh)."
    try:
        subprocess.Popen(
            ["bash", str(script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        return False, f"Yedek başlatılamadı: {e}"
    return True, "Yedek arka planda başlatıldı; birkaç saniye içinde listede görünür (sayfayı yenileyin)."
