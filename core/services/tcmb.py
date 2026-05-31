"""TCMB günlük döviz kuru çekme + KAYDIRMALI/YOĞUN KUR yazma (Aşama 1).

Kural (KAYDIRMALI): bir takvim gününün kuru = o günden ÖNCEki son iş gününde (TCMB
yayını olan gün) yayınlanan kurdur. Hafta sonu ve resmî tatillerde TCMB yayın yapmaz;
bu günlere de KUR satırı açılır ve bir önceki iş gününün kuru yazılır (YOĞUN: aralıktaki
her takvim gününe satır). Ayrı resmî tatil listesi tutulmaz — bir gün TCMB yayını yoksa
(XML 404) iş günü değildir.

Örnekler: Cuma kuru → Cmt+Paz+(sonraki)Pzt; Salı kuru → Çarşamba. Aralık başına etki
eden kuru yazabilmek için başlangıçtan geriye doğru son yayın (seed) bulunur.

USD/EUR/GBP için 4 kur:
  ForexBuying    = MB Alış          -> <pb>_alis        (mevcut; kur_usd buradan)
  ForexSelling   = MB Satış         -> <pb>_satis
  BanknoteBuying = MB Efektif Alış  -> <pb>_efektif_alis
  BanknoteSelling= MB Efektif Satış -> <pb>_efektif_satis

Ağ erişimi yalnız :func:`tcmb_gunluk_cek`'te; :func:`parse_tcmb_xml` saf/test edilebilir.
:func:`kurlari_guncelle` ``cekici`` parametresiyle ağsız test edilebilir.
"""
from __future__ import annotations

import datetime as _dt
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from core.models import Kur

PARA_BIRIMLERI = ("USD", "EUR", "GBP")
AZAMI_GUN = 62          # tek çekimde aralık üst sınırı (zaman aşımı koruması)
SEED_AZAMI_GERI = 16    # başlangıç için geriye doğru en fazla kaç gün yayın aranır

_BASLIK = {"User-Agent": "Mozilla/5.0 (SEMTA-ERP TCMB kur cekici)"}
_ZAMAN_ASIMI = 20  # saniye

# model alan öneki -> TCMB XML etiketi
_KUR_TIPLERI = (
    ("alis", "ForexBuying"),
    ("satis", "ForexSelling"),
    ("efektif_alis", "BanknoteBuying"),
    ("efektif_satis", "BanknoteSelling"),
)


class TcmbHatasi(RuntimeError):
    """TCMB çekme/işleme hatası (ağ veya geçersiz girdi)."""


def tcmb_url(tarih: _dt.date) -> str:
    """Belirli bir günün TCMB XML adresi (yayın yoksa 404)."""
    return f"https://www.tcmb.gov.tr/kurlar/{tarih:%Y%m}/{tarih:%d%m%Y}.xml"


def _dec(metin):
    """TCMB XML değerini Decimal'e çevirir (ondalık ayıracı NOKTA). Boş/geçersiz -> None."""
    if metin is None:
        return None
    metin = metin.strip()
    if not metin:
        return None
    try:
        return Decimal(metin)
    except InvalidOperation:
        return None


def parse_tcmb_xml(xml_bytes) -> dict:
    """TCMB XML -> {PB: {alis, satis, efektif_alis, efektif_satis}} (yalnız USD/EUR/GBP).

    Birim (Unit) > 1 ise kur Unit'e bölünür (USD/EUR/GBP için Unit=1). Saf fonksiyon.
    """
    kok = ET.fromstring(xml_bytes)
    sonuc: dict[str, dict] = {}
    for cur in kok.findall("Currency"):
        kod = (cur.get("Kod") or "").strip().upper()
        if kod not in PARA_BIRIMLERI:
            continue
        birim = _dec(cur.findtext("Unit")) or Decimal("1")
        if birim <= 0:
            birim = Decimal("1")
        d = {}
        for alan, etiket in _KUR_TIPLERI:
            ham = _dec(cur.findtext(etiket))
            d[alan] = (ham / birim) if ham is not None else None
        sonuc[kod] = d
    return sonuc


def _indir(url: str):
    """URL içeriğini indirir. 404 (yayın yok) -> None; diğer hata -> TcmbHatasi."""
    istek = urllib.request.Request(url, headers=_BASLIK)
    try:
        with urllib.request.urlopen(istek, timeout=_ZAMAN_ASIMI) as yanit:
            return yanit.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise TcmbHatasi(f"TCMB HTTP {e.code}: {url}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise TcmbHatasi(f"TCMB bağlantı hatası: {e}") from e


def tcmb_gunluk_cek(tarih: _dt.date):
    """Bir günün TCMB kurları. Yayın yoksa (404) None döner."""
    ham = _indir(tcmb_url(tarih))
    if ham is None:
        return None
    return parse_tcmb_xml(ham)


def _yaz(hedef: _dt.date, kurlar: dict, kullanici):
    """Bir takvim gününe (hedef) verilen kur setini upsert eder (4 kur × 3 PB)."""
    alanlar = {}
    for pb in PARA_BIRIMLERI:
        d = kurlar.get(pb) or {}
        on = pb.lower()
        for alan, _etiket in _KUR_TIPLERI:
            alanlar[f"{on}_{alan}"] = d.get(alan)
    defaults = {**alanlar, "updated_by": kullanici, "silindi": False, "silindi_at": None}
    create_defaults = {**alanlar, "created_by": kullanici, "updated_by": kullanici}
    Kur.objects.update_or_create(
        tarih=hedef, defaults=defaults, create_defaults=create_defaults,
    )


def _hafta_ici(gun: _dt.date) -> bool:
    """Pzt-Cum -> True. (Cmt/Paz'da TCMB yapısal olarak yayın yapmaz; sorulmaz.)"""
    return gun.weekday() < 5


def kurlari_guncelle(baslangic: _dt.date, bitis: _dt.date, *,
                     cekici=tcmb_gunluk_cek, kullanici=None) -> dict:
    """[baslangic..bitis] aralığındaki HER takvim gününe KUR satırı yazar (YOĞUN).

    Her güne, o günden önceki son iş gününün (TCMB yayını olan gün) kuru yazılır
    (KAYDIRMALI). Hafta sonu/tatil günlerine de bir önceki iş günü kuru yazılır.
    ``cekici`` enjekte edilebilir (test ağsız). Idempotenttir.

    Döner: ``{'yayin', 'yazilan', 'atlanan'}`` — yayin: aralıkta TCMB yayını olan gün,
    atlanan: yayını olmayan gün (hafta sonu/tatil; yine de satır yazılır), yazilan:
    yazılan toplam satır.
    """
    if baslangic > bitis:
        raise TcmbHatasi("Başlangıç tarihi bitişten sonra olamaz.")
    if (bitis - baslangic).days + 1 > AZAMI_GUN:
        raise TcmbHatasi(
            f"Tek seferde en fazla {AZAMI_GUN} gün çekilebilir; aralığı bölün."
        )

    birgun = _dt.timedelta(days=1)

    # 1) Seed: başlangıca etki eden son yayını geriye doğru bul (hafta sonu atla).
    son = None
    geri = baslangic - birgun
    sinir = baslangic - _dt.timedelta(days=SEED_AZAMI_GERI)
    while geri >= sinir:
        if _hafta_ici(geri):
            k = cekici(geri)
            if k:
                son = k
                break
        geri -= birgun

    # 2) İleri yürü: her gün için KUR[gün] = (günden önceki son yayın = 'son').
    yayin = yazilan = atlanan = 0
    gun = baslangic
    while gun <= bitis:
        bugun_kur = cekici(gun) if _hafta_ici(gun) else None
        if bugun_kur:
            yayin += 1
        else:
            atlanan += 1
        if son is not None:
            _yaz(gun, son, kullanici)
            yazilan += 1
        if bugun_kur:        # bugünün yayını ertesi günden itibaren geçerli olur
            son = bugun_kur
        gun += birgun

    return {"yayin": yayin, "yazilan": yazilan, "atlanan": atlanan}
