"""SATINALMA — açık alış siparişlerinden GELECEK STOK (2026-10-14): TEK kaynak, VARYANTLI. Kritik stok, stok listesi ve ÜS eksik malzeme aynı haritayı kullanır.

Açık sipariş = ALIŞ + SIPARIS + ONAYLI + silinmemiş. Sipariş artık "gelecek" DEĞİLDİR eğer: ONAYLI (silinmemiş) irsaliyesi varsa (mal depoya girdi — stok girişi irsaliye
onayında yazılır) ya da ONAYLI (silinmemiş) faturaya bağlıysa. TASLAK irsaliye / TASLAK fatura kaynağı açık tutar: hedef onaylanana kadar sipariş gelecek stoktur.
Anahtar (stok pk, varyant pk|None): kalemin varyantı; varyantlı kartta boşsa kartın varsayılan varyantı (hareket/ayırma ile aynı anlam). Miktar üretim birimindedir
(``uretim_miktar`` varsa o, yoksa miktar ÷ çevirici)."""
from __future__ import annotations

from decimal import Decimal

from core.models import Fatura, TeklifSiparis, TeklifSiparisKalem
from core.services.varyant import varyant_kisa

SIFIR = Decimal("0")


def acik_siparis_kalemleri(stok_idler=None):
    onayli_irsaliyeli = TeklifSiparis.objects.filter(silindi=False, belge_tur=TeklifSiparis.BelgeTur.IRSALIYE, kaynak_siparis__isnull=False,
                                                     durum=TeklifSiparis.Durum.ONAYLI).values("kaynak_siparis_id")
    onayli_faturali = Fatura.objects.filter(silindi=False, durum=Fatura.Durum.ONAYLI).values("pk")
    qs = (TeklifSiparisKalem.objects.filter(silindi=False, teklif_siparis__silindi=False, teklif_siparis__belge_tur=TeklifSiparis.BelgeTur.SIPARIS,
                                            teklif_siparis__yon=TeklifSiparis.Yon.ALIS, teklif_siparis__durum=TeklifSiparis.Durum.ONAYLI)
          .exclude(teklif_siparis_id__in=onayli_irsaliyeli).exclude(teklif_siparis__fatura_id__in=onayli_faturali))
    if stok_idler is not None:
        qs = qs.filter(stok_id__in=list(stok_idler))
    return (qs.select_related("stok", "teklif_siparis", "varyant__stok", "varyant__markalama", "varyant__kilavuz", "varyant__afis", "varyant__yan_etiket")
            .order_by("teklif_siparis__tarih", "teklif_siparis_id", "pk"))


def _miktar(k) -> Decimal:
    if k.uretim_miktar is not None:
        return k.uretim_miktar
    return k.miktar / k.stok.cevirici if k.stok.cevirici else k.miktar


def _anahtar(k) -> tuple:
    return (k.stok_id, k.varyant_id or (k.stok.varsayilan_varyant_id if k.stok.varyantli else None))


def acik_siparis_satirlari(stok_idler=None) -> list:
    """[{"siparis", "kalem", "stok_id", "varyant_id", "miktar", "termin"}] — ekranlarda "SAS-… bekleniyor" ayrıntısı için."""
    sonuc = []
    for k in acik_siparis_kalemleri(stok_idler):
        vpk = _anahtar(k)[1]
        etiket = varyant_kisa(k.varyant) if k.varyant_id else (varyant_kisa(vpk) if vpk else "")
        sonuc.append({"siparis": k.teklif_siparis, "kalem": k, "stok_id": k.stok_id, "varyant_id": vpk, "varyant_etiketi": etiket, "miktar": _miktar(k),
                      "termin": k.teklif_siparis.gecerlilik_teslim_tarihi})
    return sonuc


def acik_siparis_haritasi(stok_idler=None) -> dict:
    """{(stok pk, varyant pk|None): açık alış siparişlerindeki miktar (üretim birimi)}."""
    return harita_kur(acik_siparis_satirlari(stok_idler))


def harita_kur(satirlar) -> dict:
    """``acik_siparis_satirlari`` çıktısından {(stok pk, varyant pk|None): miktar} (aynı satırlardan ikinci sorgu atmadan)."""
    harita = {}
    for s in satirlar:
        anahtar = (s["stok_id"], s["varyant_id"])
        harita[anahtar] = harita.get(anahtar, SIFIR) + s["miktar"]
    return harita


def kart_toplami(harita: dict) -> dict:
    """{stok pk: tüm varyantların toplamı} (kart bazlı ekranlar: kritik stok, stok listesi)."""
    sonuc = {}
    for (pk, _v), m in harita.items():
        sonuc[pk] = sonuc.get(pk, SIFIR) + m
    return sonuc


def kalan_icin(harita: dict, stok_pk, varyant_pk=None) -> Decimal:
    """Düğüm için gelecek: varyant verilmemişse (kapalı plan / varyantsız kart) kartın tüm satırları (fason_siparis.kalan_icin ile aynı anlam)."""
    if varyant_pk is None:
        return sum((v for (pk, _v), v in harita.items() if pk == stok_pk), SIFIR)
    return harita.get((stok_pk, varyant_pk), SIFIR)
