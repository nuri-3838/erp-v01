"""İndirilen belgelerin (teklif/proforma/sipariş PDF + Excel) dosya adı ve Content-Disposition başlığı — tek yer.

Ad: ``<BELGE NO>_<CARİ ADI>.<uzantı>``; dosya sisteminde geçersiz karakterler temizlenir, sondaki nokta/boşluk atılır ("ŞTİ." → ".." olmaz), toplam ad
(uzantı dahil) en fazla ``MAKS_UZUNLUK`` karakter. Başlıkta ASCII yedek (``filename=``, Türkçe harfler karşılığına çevrilir: Ç→C Ğ→G İ→I Ö→O Ş→S Ü→U,
küçükleri de) + RFC 6266 ``filename*=UTF-8''`` (Türkçe asıl ad) birlikte gider."""
import re
import unicodedata
from urllib.parse import quote

MAKS_UZUNLUK = 100

_GECERSIZ = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_TR_ASCII = str.maketrans({
    "Ç": "C", "Ğ": "G", "İ": "I", "Ö": "O", "Ş": "S", "Ü": "U",
    "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ş": "s", "ü": "u",
})


def _parca(deger) -> str:
    """Dosya adı parçası: geçersiz karakter atılır, boşluklar tekilleşir, baştaki boşluk/nokta ve sondaki boşluk/nokta temizlenir."""
    s = _GECERSIZ.sub("", str(deger if deger is not None else ""))
    return re.sub(r"\s+", " ", s).strip(" .")


def belge_dosya_adi(belge_no, cari_adi, uzanti: str) -> str:
    """``<BELGE NO>_<CARİ ADI>.<uzantı>`` — toplam ``MAKS_UZUNLUK`` karakteri geçmez; ad boşsa 'belge'."""
    uz = "." + str(uzanti).lstrip(".").lower()
    govde = "_".join(p for p in (_parca(belge_no), _parca(cari_adi)) if p)
    govde = govde[: MAKS_UZUNLUK - len(uz)].rstrip(" ._-")        # kesilince sonda nokta/boşluk/ayraç kalmasın
    return (govde or "belge") + uz


def ascii_ad(dosya_adi: str) -> str:
    """ASCII yedek ad: Türkçe harfler karşılığına çevrilir (silinmez), kalan aksanlar atılır."""
    cevrilmis = str(dosya_adi).translate(_TR_ASCII)
    ascii_ = unicodedata.normalize("NFKD", cevrilmis).encode("ascii", "ignore").decode("ascii")
    return ascii_.strip(" ._-") and ascii_ or "belge"


def content_disposition(dosya_adi: str, ek: str = "attachment") -> str:
    """``attachment`` (indir) ya da ``inline`` (tarayıcıda göster) + ASCII yedek + UTF-8 asıl ad."""
    return f"{ek}; filename=\"{ascii_ad(dosya_adi)}\"; filename*=UTF-8''{quote(dosya_adi, safe='')}"
