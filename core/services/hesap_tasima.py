"""Hesap TAŞIMA — hareket görmüş bir hesap alt hesaplara bölünürken mevcut yevmiye satırlarını alt hesaplara taşır.

Neden: alt hesap açılan hesap ÜST hesap olur ve fişe yalnız yaprak hesap kesilir; üst hesapta kalan eski satırlar tutarsızlık
üretir. Bu modül iki kullanım sunar:
  * ``tum_satirlari_tasi``: hesap planı ekranı — hareketli bir yaprağa İLK alt hesap açılırken tüm satırlar o alt hesaba.
  * ``plan`` / ``tasi``: kural bazlı bölme (hedef hesap + fiş/satır açıklamasında geçen anahtar kelimeler) — komutlar için.
Satırın hesabı değişir; tutar/döviz/tarih/fiş aynı kalır. Banka/kasa/kart hareketlerinde karşı hesap zaten fişin satırıdır, yani
"karşı hesap" da aynı işlemle güncellenir. Eşleşmeyen (ya da birden çok kurala uyan) satıra DOKUNULMAZ, raporlanır.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, YevmiyeSatir
from core.services.hesap_plani import ust_kodu


class TasimaHatasi(ValueError):
    """Hesap taşıma kural ihlali (Türkçe mesaj)."""


@dataclass
class TasimaPlani:
    kaynak: str
    eslesen: dict = field(default_factory=dict)       # hedef_kod -> [YevmiyeSatir]
    eslesmeyen: list = field(default_factory=list)    # [(satir, neden)]

    @property
    def tasinacak(self):
        return sum(len(v) for v in self.eslesen.values())


def _metin(satir):
    return buyuk_harf_tr(f"{satir.aciklama or ''} | {satir.fis.aciklama or ''}")


def _hedef_dogrula(kaynak_kod, hedef_kod):
    h = HesapPlani.objects.filter(hesap_kodu=hedef_kod, silindi=False, aktif=True).first()
    if h is None:
        raise TasimaHatasi(f"Hedef hesap bulunamadı/aktif değil: {hedef_kod}")
    if not hedef_kod.startswith(kaynak_kod + "."):
        raise TasimaHatasi(f"{hedef_kod}, {kaynak_kod} hesabının alt hesabı değil.")
    if HesapPlani.objects.filter(hesap_kodu__startswith=hedef_kod + ".", silindi=False).exists():
        raise TasimaHatasi(f"{hedef_kod} yaprak hesap değil (alt hesabı var).")
    return h


def plan(kaynak_kod, kurallar) -> TasimaPlani:
    """``kurallar``: [(hedef_kod, [anahtar kelime, ...]), ...] — satır/fiş açıklamasında (büyük harfe çevrilmiş) herhangi
    bir anahtar geçen satır o hedefe gider. DB'ye YAZMAZ."""
    for hedef, _ in kurallar:
        _hedef_dogrula(kaynak_kod, hedef)
    p = TasimaPlani(kaynak=kaynak_kod)
    satirlar = (YevmiyeSatir.objects.filter(hesap_id=kaynak_kod).select_related("fis")
                .order_by("fis__tarih", "fis__fis_no", "id"))
    for s in satirlar:
        metin = _metin(s)
        hedefler = [h for h, anahtarlar in kurallar if any(buyuk_harf_tr(a) in metin for a in anahtarlar)]
        if len(hedefler) == 1:
            p.eslesen.setdefault(hedefler[0], []).append(s)
        else:
            p.eslesmeyen.append((s, "hiçbir kurala uymuyor" if not hedefler else f"birden çok kurala uyuyor: {hedefler}"))
    return p


@transaction.atomic
def uygula(p: TasimaPlani, *, kullanici=None) -> int:
    """Planı yazar: eşleşen satırların hesabını değiştirir. Taşınan satır sayısını döner."""
    n = 0
    for hedef, satirlar in p.eslesen.items():
        for s in satirlar:
            s.hesap_id = hedef
            s.updated_by = kullanici
            s.save(update_fields=["hesap", "updated_by", "updated_at"])
            n += 1
    return n


@transaction.atomic
def tum_satirlari_tasi(kaynak_kod, hedef_kod, *, kullanici=None) -> int:
    """Kaynak hesabın TÜM satırlarını (pasif/düzenleme geçmişi dahil) tek bir alt hesaba taşır."""
    _hedef_dogrula(kaynak_kod, hedef_kod)
    n = 0
    for s in YevmiyeSatir.objects.filter(hesap_id=kaynak_kod):
        s.hesap_id = hedef_kod
        s.updated_by = kullanici
        s.save(update_fields=["hesap", "updated_by", "updated_at"])
        n += 1
    return n
