"""Gizli yüklemelerin (cari/aday ekleri, çek/senet görselleri) MEDIA_ROOT'tan özel depoya
(IK_OZEL_DIR) taşınması — tek seferlik veri taşıma + geri alma.

Dosyalar aynı GÖRELİ yolla taşınır (cari_aktivite/x.webp → ozel_dosyalar/cari_aktivite/x.webp),
bu yüzden veritabanı satırlarına DOKUNULMAZ. Her dosya: geçici adla kopyala → boyut + SHA-256
doğrula → yerine koy. Kaynak YALNIZ kopya doğrulandıktan sonra ve `sil=True` ile silinir; hedefte
aynı adlı FARKLI içerik varsa çakışma sayılır (asla ezilmez, kaynak silinmez). Tekrar
çalıştırılabilir (zaten doğrulanmış dosya "zaten var" sayılır). `geri=True` yönü tersine çevirir
(özel depo → MEDIA_ROOT): kod geri alınırsa dosyalar da döner.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from core.storage import OZELE_TASINAN_ONEKLER


class OzelTasimaHatasi(Exception):
    pass


@dataclass
class TasimaSonucu:
    kuru: bool = False
    kopyalanan: int = 0                # kuru=True iken: kopyalanacak sayısı
    zaten_var: int = 0                 # hedefte aynı içerikle duruyordu (doğrulandı)
    silinen: int = 0                   # doğrulanmış kaynak kopyalar silindi
    bayt: int = 0
    atlanan: list = field(default_factory=list)    # symlink / düzenli dosya değil
    catisma: list = field(default_factory=list)    # hedefte farklı içerik/tür var
    hata: list = field(default_factory=list)       # kopya doğrulanamadı / silinemedi

    @property
    def temiz(self) -> bool:
        return not (self.catisma or self.hata)


def _sha256(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


def _kokler(geri: bool):
    """(kaynak_kok, hedef_kok). İki kök iç içe olamaz: özel dizin nginx'in sunduğu MEDIA_ROOT'un
    içine düşerse taşıma anlamsızdır (core.E002 aynı şeyi sistem kontrolünde yakalar)."""
    genel = Path(settings.MEDIA_ROOT).resolve()
    ozel = Path(settings.IK_OZEL_DIR).resolve()
    if ozel == genel or genel in ozel.parents or ozel in genel.parents:
        raise OzelTasimaHatasi(
            f"IK_OZEL_DIR ({ozel}) ile MEDIA_ROOT ({genel}) aynı ya da iç içe olamaz.")
    return (ozel, genel) if geri else (genel, ozel)


def _dizin_hazirla(dizin: Path, taban: Path, mod: int):
    """`taban` altındaki eksik dizinleri (üstten alta) `mod` izniyle oluşturur."""
    eksik = []
    d = dizin
    while d != taban and not d.exists():
        eksik.append(d)
        d = d.parent
    if not taban.exists():
        taban.mkdir(parents=True)
        os.chmod(taban, mod)
    for d in reversed(eksik):
        d.mkdir()
        os.chmod(d, mod)


def _tek_dosya(kaynak: Path, kaynak_kok: Path, hedef_kok: Path, s: TasimaSonucu, *,
               kuru: bool, sil: bool, dosya_modu: int, dizin_modu: int):
    goreli = kaynak.relative_to(kaynak_kok)
    ad = goreli.as_posix()
    if kaynak.is_symlink() or not kaynak.is_file():
        s.atlanan.append(ad)
        return
    hedef = hedef_kok / goreli
    boyut = kaynak.stat().st_size
    ozet = _sha256(kaynak)
    if hedef.is_symlink() or (hedef.exists() and not hedef.is_file()):
        s.catisma.append(ad)
        return
    if hedef.exists():
        if hedef.stat().st_size == boyut and _sha256(hedef) == ozet:
            s.zaten_var += 1
        else:
            s.catisma.append(ad)          # farklı içerik: ASLA ezilmez, kaynak silinmez
            return
    elif kuru:
        s.kopyalanan += 1
        s.bayt += boyut
        return
    else:
        gecici = hedef.with_name(hedef.name + ".tasiniyor")
        try:
            _dizin_hazirla(hedef.parent, hedef_kok, dizin_modu)
            shutil.copyfile(kaynak, gecici)
            os.chmod(gecici, dosya_modu)
            if gecici.stat().st_size != boyut or _sha256(gecici) != ozet:
                raise OSError("kopya doğrulanamadı (boyut/SHA-256)")
            os.replace(gecici, hedef)
        except OSError as e:
            s.hata.append(f"{ad}: {e}")
            gecici.unlink(missing_ok=True)
            return
        s.kopyalanan += 1
        s.bayt += boyut
    # Buraya yalnız hedefte doğrulanmış aynı içerik varken gelinir.
    if sil and not kuru:
        try:
            kaynak.unlink()
            s.silinen += 1
        except OSError as e:
            s.hata.append(f"{ad}: kaynak silinemedi ({e})")


def tasi(*, kuru: bool = False, sil: bool = False, geri: bool = False) -> TasimaSonucu:
    """OZELE_TASINAN_ONEKLER altındaki dosyaları MEDIA_ROOT → IK_OZEL_DIR (geri=True: tersi)
    taşır. kuru=True hiçbir şey yazmaz/silmez. sil=True kaynağı yalnız doğrulanmış kopyadan
    sonra siler ve boşalan kaynak dizinlerini kaldırır."""
    kaynak_kok, hedef_kok = _kokler(geri)
    dosya_modu, dizin_modu = (0o644, 0o755) if geri else (0o600, 0o700)
    s = TasimaSonucu(kuru=kuru)
    for onek in OZELE_TASINAN_ONEKLER:
        kaynak_dizin = kaynak_kok / onek
        if kaynak_dizin.is_symlink() or not kaynak_dizin.is_dir():
            continue
        for kok, _dizinler, dosyalar in os.walk(kaynak_dizin):      # dizin symlink'lerine inmez
            for ad in sorted(dosyalar):
                _tek_dosya(Path(kok) / ad, kaynak_kok, hedef_kok, s, kuru=kuru, sil=sil,
                           dosya_modu=dosya_modu, dizin_modu=dizin_modu)
        if sil and not kuru:
            for kok, _dizinler, _dosyalar in os.walk(kaynak_dizin, topdown=False):
                try:
                    Path(kok).rmdir()                                # yalnız boş dizinler gider
                except OSError:
                    pass
    return s


def eksik_kayitlar(*, geri: bool = False) -> list:
    """DB'de yolu kayıtlı ama hedef kökte dosyası OLMAYAN kayıtlar: [(etiket, pk, yol)].
    Silinmiş (soft delete) kayıtlar da sayılır — dosyaları durur. Yalnız bilgi amaçlıdır."""
    from core.models import AdayAktiviteEk, CariAktiviteEk, CekSenet

    _, hedef_kok = _kokler(geri)
    kaynaklar = (
        ("CariAktiviteEk.dosya", CariAktiviteEk.objects.exclude(dosya=""), "dosya"),
        ("AdayAktiviteEk.dosya", AdayAktiviteEk.objects.exclude(dosya=""), "dosya"),
        ("CekSenet.on_yuz", CekSenet.objects.exclude(on_yuz__isnull=True).exclude(on_yuz=""),
         "on_yuz"),
        ("CekSenet.arka_yuz", CekSenet.objects.exclude(arka_yuz__isnull=True).exclude(arka_yuz=""),
         "arka_yuz"),
    )
    eksik = []
    for etiket, qs, alan in kaynaklar:
        for pk, yol in qs.values_list("pk", alan):
            if not (hedef_kok / yol).is_file():
                eksik.append((etiket, pk, yol))
    return eksik
