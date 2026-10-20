"""GOOGLE DRIVE YEDEĞİ (2026-10-20): yerel gecelik yedek (``scripts/db_backup.sh``: DB dump + özel dosyalar + medya) ŞİFRELENİP Drive'daki "ERP_YEDEK" klasörüne yüklenir; eski yedekler saklama kuralına göre silinir.

Güvenlik
* Şifreleme: GnuPG SİMETRİK (AES256, parola dosyadan — komut satırına/ortama konmaz); Drive'a yalnız ``.gpg`` dosyası gider. Parola dosyası sunucuda yalnız uygulama kullanıcısının okuyabildiği
  (0600) bir dosyadır ve repoya girmez; parolanın bir kopyası sunucu DIŞINDA kullanıcıda durur (kaybolursa yedekler açılamaz).
* Drive yetkisi: rclone ``drive.file`` kapsamı — uygulama YALNIZ kendi oluşturduğu dosyaları görür/siler (hesabın diğer dosyalarına erişemez). Token ``~/.config/rclone/rclone.conf`` (0600) içindedir.
* Silme yalnız ERP_YEDEK klasöründe, ``erp_v01_…(sql.gz|tar.gz).gpg`` adlı VE bu modülün durum dosyasında "yükledim" diye kayıtlı dosyalar için yapılır.

Çalışma (``manage.py drive_yedek``; cron gecelik yedekten sonra + tekrar deneme): yerelde son N günün yedek takımları (aynı zaman damgalı sql/ozel/medya) bulunur; her TAKVİM GÜNÜNÜN İLK takımı (gecelik yedek;
deploy yedekleri değil) henüz yüklenmediyse şifrelenir, ``rclone copyto`` ile yüklenir, boyutu Drive'dan doğrulanır, durum dosyasına yazılır. Hata olursa log'a yazılır, çıkış kodu 1 olur,
yüklenemeyen takım bir sonraki çalışmada TEKRAR denenir. Saklama: son 30 gün her gün + son 12 haftanın her haftasının son günü (ISO hafta); gerisi silinir; en yeni takım asla silinmez."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

DESEN = re.compile(r"^erp_v01_(?:(ozel|medya)_)?(\d{8})_(\d{6})\.(sql\.gz|tar\.gz)$")
UZAK_DESEN = re.compile(r"^erp_v01_(?:(?:ozel|medya)_)?(\d{8})_(\d{6})\.(?:sql\.gz|tar\.gz)\.gpg$")
GUNLUK_SAKLAMA = 30
HAFTALIK_SAKLAMA = 12


class DriveYedekHatasi(RuntimeError):
    """Yükleme/şifreleme/saklama hatası (Türkçe mesaj)."""


@dataclass
class Ayar:
    yedek_dizini: Path
    durum_dosyasi: Path
    log_dosyasi: Path
    anahtar_dosyasi: Path
    uzak: str = "erpdrive:ERP_YEDEK"
    rclone: tuple = ("rclone",)
    gpg: str = "gpg"
    geriye_gun: int = 15
    gunluk: int = GUNLUK_SAKLAMA
    haftalik: int = HAFTALIK_SAKLAMA

    @classmethod
    def ortamdan(cls, repo: Path):
        e = os.environ.get
        return cls(
            yedek_dizini=Path(e("DRIVE_YEDEK_DIZIN", repo / "backups")),
            durum_dosyasi=Path(e("DRIVE_YEDEK_DURUM", repo / "logs" / "drive_yedek_durum.json")),
            log_dosyasi=Path(e("DRIVE_YEDEK_LOG", repo / "logs" / "drive_yedek.log")),
            anahtar_dosyasi=Path(e("DRIVE_YEDEK_ANAHTAR", Path.home() / ".erp_yedek_anahtar")),
            uzak=e("DRIVE_YEDEK_UZAK", "erpdrive:ERP_YEDEK"),
            rclone=tuple(shlex.split(e("RCLONE_YOLU", str(Path.home() / "bin" / "rclone")))),
            gpg=e("GPG_YOLU", "gpg"))


# ---------------------------------------------------------------- yardımcılar
def _log(ayar: Ayar, mesaj: str):
    ayar.log_dosyasi.parent.mkdir(parents=True, exist_ok=True)
    with open(ayar.log_dosyasi, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {mesaj}\n")


def _calistir(komut, *, hata_mesaji):
    try:
        s = subprocess.run(komut, capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DriveYedekHatasi(f"{hata_mesaji}: {e}")
    if s.returncode != 0:
        raise DriveYedekHatasi(f"{hata_mesaji}: {(s.stderr or s.stdout).strip()[:400]}")
    return s.stdout


def durum_oku(ayar: Ayar) -> dict:
    try:
        d = json.loads(ayar.durum_dosyasi.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("dosyalar", {})
    return d


def durum_yaz(ayar: Ayar, d: dict):
    ayar.durum_dosyasi.parent.mkdir(parents=True, exist_ok=True)
    gecici = ayar.durum_dosyasi.with_suffix(".tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(gecici, ayar.durum_dosyasi)


def _sha256(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    return h.hexdigest()


def tarih_damga(ad: str):
    """Dosya adından (datetime.date, 'YYYYMMDD_HHMMSS') — .gpg eki varsa atılır; desen dışıysa None."""
    ad = ad[:-4] if ad.endswith(".gpg") else ad
    m = DESEN.match(ad)
    if m is None:
        return None
    return datetime.datetime.strptime(m.group(2), "%Y%m%d").date(), f"{m.group(2)}_{m.group(3)}"


# ---------------------------------------------------------------- yerel takımlar
def yerel_takimlar(ayar: Ayar, bugun: datetime.date) -> dict:
    """{zaman damgası: [dosya yolları]} — son ``geriye_gun`` gün; bir takım = aynı damgalı sql/ozel/medya dosyaları."""
    takimlar = {}
    if not ayar.yedek_dizini.exists():
        return takimlar
    for p in sorted(ayar.yedek_dizini.iterdir()):
        m = DESEN.match(p.name)
        if not m or not p.is_file():
            continue
        td = datetime.datetime.strptime(m.group(2), "%Y%m%d").date()
        if (bugun - td).days > ayar.geriye_gun:
            continue
        takimlar.setdefault(f"{m.group(2)}_{m.group(3)}", []).append(p)
    return takimlar


def yuklenecek_takimlar(takimlar: dict) -> list:
    """Her takvim gününün İLK (en erken) takımı — gecelik yedek; aynı günün deploy yedekleri yüklenmez. Eski → yeni sıralı [(damga, dosyalar)]."""
    ilk = {}
    for damga in sorted(takimlar):
        ilk.setdefault(damga[:8], damga)
    return [(d, takimlar[d]) for d in sorted(ilk.values())]


# ---------------------------------------------------------------- şifreleme + yükleme
def sifrele(ayar: Ayar, kaynak: Path, hedef: Path):
    if not ayar.anahtar_dosyasi.exists() or ayar.anahtar_dosyasi.stat().st_size == 0:
        raise DriveYedekHatasi(f"Şifreleme parola dosyası yok/boş: {ayar.anahtar_dosyasi}")
    _calistir([ayar.gpg, "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback", "--symmetric", "--cipher-algo", "AES256", "--compress-algo", "none",
               "--passphrase-file", str(ayar.anahtar_dosyasi), "-o", str(hedef), str(kaynak)], hata_mesaji=f"{kaynak.name} şifrelenemedi")


def _uzak_boyut(ayar: Ayar, ad: str):
    cikti = _calistir([*ayar.rclone, "lsjson", "--files-only", f"{ayar.uzak}/{ad}"], hata_mesaji=f"{ad} Drive'da doğrulanamadı")
    try:
        liste = json.loads(cikti or "[]")
    except ValueError:
        raise DriveYedekHatasi(f"{ad}: Drive yanıtı okunamadı")
    return liste[0].get("Size") if liste else None


def yukle(ayar: Ayar, durum: dict, damga: str, dosyalar: list):
    """Bir takımın TÜM dosyalarını şifreleyip yükler; her dosya yüklenince ve doğrulanınca durum dosyasına yazılır (yarım kalırsa kalanı sonra tamamlanır)."""
    with tempfile.TemporaryDirectory(prefix="erp_yedek_") as t:
        os.chmod(t, 0o700)
        for p in sorted(dosyalar):
            uzak_ad = p.name + ".gpg"
            if uzak_ad in durum["dosyalar"]:
                continue
            gpg_yol = Path(t) / uzak_ad
            sifrele(ayar, p, gpg_yol)
            boyut = gpg_yol.stat().st_size
            _calistir([*ayar.rclone, "copyto", str(gpg_yol), f"{ayar.uzak}/{uzak_ad}", "--retries", "3"], hata_mesaji=f"{uzak_ad} yüklenemedi")
            uzak_boyut = _uzak_boyut(ayar, uzak_ad)
            if uzak_boyut != boyut:
                try:
                    _calistir([*ayar.rclone, "deletefile", "--drive-use-trash=false", f"{ayar.uzak}/{uzak_ad}"], hata_mesaji="kısmi dosya silinemedi")
                except DriveYedekHatasi:
                    pass
                raise DriveYedekHatasi(f"{uzak_ad}: boyut uyuşmuyor (yerel şifreli {boyut}, Drive {uzak_boyut}); sonraki çalışmada tekrar denenecek")
            durum["dosyalar"][uzak_ad] = {"yuklendi": datetime.datetime.now().isoformat(timespec="seconds"), "boyut": boyut, "sha256": _sha256(gpg_yol), "kaynak_boyut": p.stat().st_size}
            durum_yaz(ayar, durum)
            os.remove(gpg_yol)


# ---------------------------------------------------------------- saklama
def saklama_plani(tarihler, bugun: datetime.date, gunluk=GUNLUK_SAKLAMA, haftalik=HAFTALIK_SAKLAMA):
    """(tutulacak tarihler, silinecek tarihler): son ``gunluk`` gün hepsi; son ``haftalik`` hafta için her ISO haftasının SON tarihi; en yeni tarih asla silinmez."""
    tarihler = sorted(set(tarihler))
    tut = {d for d in tarihler if 0 <= (bugun - d).days < gunluk}
    haftalar = {}
    for d in tarihler:
        if 0 <= (bugun - d).days < 7 * haftalik:
            haftalar.setdefault(d.isocalendar()[:2], []).append(d)
    tut |= {max(v) for v in haftalar.values()}
    if tarihler:
        tut.add(tarihler[-1])
    return sorted(tut), sorted(set(tarihler) - tut)


def saklama_uygula(ayar: Ayar, durum: dict, bugun: datetime.date, kuru=False) -> list:
    """Drive'daki (ve durum dosyasında kayıtlı) yedek dosyalarından saklama dışı kalanları siler. Silinen/silinecek adların listesi döner."""
    cikti = _calistir([*ayar.rclone, "lsjson", "--files-only", ayar.uzak], hata_mesaji="Drive listesi alınamadı")
    try:
        uzak = {x["Name"] for x in json.loads(cikti or "[]")}
    except (ValueError, KeyError):
        raise DriveYedekHatasi("Drive listesi okunamadı")
    adaylar = {}
    for ad in uzak:
        if not UZAK_DESEN.match(ad) or ad not in durum["dosyalar"]:                    # yalnız kendi yüklediğimiz ve desene uyan dosyalar
            continue
        adaylar.setdefault(tarih_damga(ad)[0], []).append(ad)
    _, sil = saklama_plani(adaylar.keys(), bugun, ayar.gunluk, ayar.haftalik)
    silinen = []
    for tarih in sil:
        for ad in sorted(adaylar[tarih]):
            if not kuru:
                _calistir([*ayar.rclone, "deletefile", "--drive-use-trash=false", f"{ayar.uzak}/{ad}"], hata_mesaji=f"{ad} silinemedi")
                durum["dosyalar"].pop(ad, None)
            silinen.append(ad)
    if silinen and not kuru:
        durum_yaz(ayar, durum)
    return silinen


# ---------------------------------------------------------------- ana akış
def calistir(ayar: Ayar, bugun: datetime.date = None, *, yukle_=True, sakla=True, kuru=False) -> dict:
    """Yükleme + saklama; {"yuklenen": [...], "silinen": [...], "hatalar": [...]}. Hatalar toplanır (bir takımın hatası diğerlerini engellemez)."""
    bugun = bugun or datetime.date.today()
    sonuc = {"yuklenen": [], "silinen": [], "hatalar": []}
    durum = durum_oku(ayar)
    if yukle_:
        for damga, dosyalar in yuklenecek_takimlar(yerel_takimlar(ayar, bugun)):
            if all(p.name + ".gpg" in durum["dosyalar"] for p in dosyalar):
                continue
            if kuru:
                sonuc["yuklenen"].append(f"(kuru) {damga}: {len(dosyalar)} dosya")
                continue
            try:
                once = set(durum["dosyalar"])
                yukle(ayar, durum, damga, dosyalar)
                sonuc["yuklenen"] += sorted(set(durum["dosyalar"]) - once)
                _log(ayar, f"OK: {damga} takımı yüklendi ({len(dosyalar)} dosya).")
            except DriveYedekHatasi as e:
                sonuc["hatalar"].append(f"{damga}: {e}")
                _log(ayar, f"HATA: {damga}: {e}")
    if sakla:
        try:
            sonuc["silinen"] = saklama_uygula(ayar, durum, bugun, kuru=kuru)
            if sonuc["silinen"]:
                _log(ayar, f"{'(kuru) ' if kuru else ''}Saklama: {len(sonuc['silinen'])} dosya silindi: {', '.join(sonuc['silinen'][:6])}{'…' if len(sonuc['silinen']) > 6 else ''}")
        except DriveYedekHatasi as e:
            sonuc["hatalar"].append(f"saklama: {e}")
            _log(ayar, f"HATA: saklama: {e}")
    if not kuru:
        durum["son_calisma"] = datetime.datetime.now().isoformat(timespec="seconds")
        if sonuc["hatalar"]:
            durum["son_hata"] = {"zaman": durum["son_calisma"], "hatalar": sonuc["hatalar"]}
        else:
            durum["son_basarili"] = durum["son_calisma"]
            durum.pop("son_hata", None)
        durum_yaz(ayar, durum)
    return sonuc


def ozet(ayar: Ayar) -> dict | None:
    """Yedek ekranı için salt-okunur özet (durum dosyası yoksa None)."""
    if not ayar.durum_dosyasi.exists():
        return None
    d = durum_oku(ayar)
    adlar = sorted(d["dosyalar"])
    tarihler = sorted({tarih_damga(a)[0] for a in adlar if tarih_damga(a)})
    return {"son_basarili": d.get("son_basarili"), "son_calisma": d.get("son_calisma"), "son_hata": d.get("son_hata"), "dosya_sayisi": len(adlar),
            "takim_gun_sayisi": len(tarihler), "en_yeni": tarihler[-1].isoformat() if tarihler else None, "en_eski": tarihler[0].isoformat() if tarihler else None,
            "toplam_boyut": sum(v.get("boyut", 0) for v in d["dosyalar"].values())}
