"""Yedek (DB + özlük evrakları + yüklenen görseller) dosya işlemleri — Aşama 1 motorunu
(scripts/db_backup.sh) sarar.

Ekran katmanı yalnızca: listeleme, elle tetikleme (yedek_al) ve indirme için yol çözümü.
GERİ YÜKLEME burada KASITLI olarak YOKTUR (tehlikeli; yanlış basılır).

Üç tür yedek dosyası vardır (aynı script, aynı zaman damgası):
- ``erp_v01_YYYYMMDD_HHMMSS.sql.gz``       — PostgreSQL dökümü (tur="DB")
- ``erp_v01_ozel_YYYYMMDD_HHMMSS.tar.gz``  — İK özlük evrakları + fotoğraflar (tur="EVRAK";
  IK_OZEL_DIR'in arşivi; dizin boşsa üretilmez)
- ``erp_v01_medya_YYYYMMDD_HHMMSS.tar.gz`` — yüklenen görseller (tur="MEDYA"; MEDIA_ROOT'un
  arşivi: stok görselleri, banka/firma logoları; dizin boşsa üretilmez)
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
# erp_v01_ozel_YYYYMMDD_HHMMSS.tar.gz — İK özel dosya arşivi (Dilim 3).
_OZEL_DESEN = re.compile(r"^erp_v01_ozel_(\d{8})_(\d{6})\.tar\.gz$")
# erp_v01_medya_YYYYMMDD_HHMMSS.tar.gz — yüklenen görseller (MEDIA_ROOT) arşivi.
_MEDYA_DESEN = re.compile(r"^erp_v01_medya_(\d{8})_(\d{6})\.tar\.gz$")
_DESENLER = (("DB", _AD_DESEN), ("EVRAK", _OZEL_DESEN), ("MEDYA", _MEDYA_DESEN))
_TUR_AD = {"DB": "Veritabanı", "EVRAK": "Özlük evrakları", "MEDYA": "Yüklenen görseller"}


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
    tur: str = "DB"  # "DB" | "EVRAK" | "MEDYA"

    @property
    def tur_ad(self) -> str:
        return _TUR_AD.get(self.tur, "Veritabanı")

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


def _tur_ve_eslesme(ad: str):
    for tur, desen in _DESENLER:
        m = desen.match(ad or "")
        if m:
            return tur, m
    return None, None


def _ad_tarihi(ad: str, yol: Path) -> datetime:
    _, m = _tur_ve_eslesme(ad)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(yol.stat().st_mtime)


def yedekleri_listele() -> list:
    """Mevcut yedek dosyaları (YedekDosya; DB + evrak + medya), en yeni önce."""
    d = yedek_dizini()
    if not d.is_dir():
        return []
    out = []
    for kalip in ("erp_v01_*.sql.gz", "erp_v01_ozel_*.tar.gz", "erp_v01_medya_*.tar.gz"):
        for yol in d.glob(kalip):
            tur, _ = _tur_ve_eslesme(yol.name)
            if yol.is_file() and tur:
                out.append(YedekDosya(ad=yol.name, boyut=yol.stat().st_size,
                                      tarih=_ad_tarihi(yol.name, yol), tur=tur))
    out.sort(key=lambda y: y.tarih, reverse=True)
    return out


def son_yedek():
    """Son VERİTABANI yedeği (evrak/medya arşivleri aynı zaman damgasını taşır; 'son yedek' kartı
    DB'yi göstermeli)."""
    for y in yedekleri_listele():
        if y.tur == "DB":
            return y
    return None


def yedek_yolu(ad: str):
    """İndirme için güvenli yol çözümü. Geçersiz ad / dizin dışı / yok => None.

    Hem ad deseni (DB dökümü, evrak ya da medya arşivi) hem de gerçek üst-dizin kontrolü ile
    path-traversal engellenir.
    """
    tur, _ = _tur_ve_eslesme(ad)
    if not tur:
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
