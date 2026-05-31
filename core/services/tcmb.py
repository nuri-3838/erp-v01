"""TCMB günlük döviz kuru çekme + KAYDIRMALI KUR yazma (Aşama 1).

Kural (KAYDIRMALI): TCMB'nin D iş gününde yayınladığı kur, D+1 takvim gününden
itibaren bir sonraki yayına kadarki günler için geçerlidir. Bu yüzden D günü yayını
KUR tablosuna ``tarih = D+1`` olarak yazılır; mevcut ``kur_usd_bul`` (tarih ≤ X, en
yakın) bu satırları otomatik ileriye taşır. TCMB yayını OLMAYAN gün (hafta sonu/resmî
tatil) atlanır — o gün XML 404 döner, ayrı tatil listesi tutulmaz.

USD/EUR/GBP için 4 kur:
  ForexBuying    = MB Alış          -> <pb>_alis        (mevcut, kur_usd buradan)
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
AZAMI_GUN = 62  # tek çekimde üst sınır (sunucu zaman aşımı + uzun işlem koruması)

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


def kurlari_guncelle(baslangic: _dt.date, bitis: _dt.date, *,
                     cekici=tcmb_gunluk_cek, kullanici=None) -> dict:
    """[baslangic..bitis] her günü çeker ve KAYDIRMALI yazar (D yayını -> tarih D+1).

    Yayını olmayan gün atlanır. ``cekici`` enjekte edilebilir (test ağsız çalışsın).
    Idempotenttir (aynı aralık tekrar çekilebilir). Döner:
    ``{'yayin', 'yazilan', 'atlanan'}``.
    """
    if baslangic > bitis:
        raise TcmbHatasi("Başlangıç tarihi bitişten sonra olamaz.")
    if (bitis - baslangic).days + 1 > AZAMI_GUN:
        raise TcmbHatasi(
            f"Tek seferde en fazla {AZAMI_GUN} gün çekilebilir; aralığı bölün."
        )

    birgun = _dt.timedelta(days=1)
    yayin = yazilan = atlanan = 0
    gun = baslangic
    while gun <= bitis:
        kurlar = cekici(gun)
        if not kurlar:
            atlanan += 1
            gun += birgun
            continue
        yayin += 1
        hedef = gun + birgun  # KAYDIRMA
        alanlar = {}
        for pb in PARA_BIRIMLERI:
            d = kurlar.get(pb) or {}
            on = pb.lower()
            for alan, _etiket in _KUR_TIPLERI:
                alanlar[f"{on}_{alan}"] = d.get(alan)
        defaults = {**alanlar, "updated_by": kullanici,
                    "silindi": False, "silindi_at": None}
        create_defaults = {**alanlar, "created_by": kullanici, "updated_by": kullanici}
        Kur.objects.update_or_create(
            tarih=hedef, defaults=defaults, create_defaults=create_defaults,
        )
        yazilan += 1
        gun += birgun
    return {"yayin": yayin, "yazilan": yazilan, "atlanan": atlanan}
