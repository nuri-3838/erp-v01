"""İNSAN KAYNAKLARI > Resmî Tatiller servis katmanı.

Tam gün resmî/dinî tatil takvimi — yoklama (core.services.personel_devam), izin gün
önerisi (core.services.personel_izin.pazarsiz_gun) ve aylık puantaj dökümünde
(core.services.personel_dokum) kullanılır. Arife TANIMLANMAZ (normal iş günü); yarım
gün/ücret etkisi bu modülün kapsamı DIŞINDADIR.

Sabit (her yıl aynı tarihte) resmî tatiller SABIT_TATILLER'de; dinî bayramlar (Ramazan/
Kurban) yıldan yıla kaydığı için burada YOKTUR — ekrandan elle girilir.
"""
from __future__ import annotations

from datetime import date

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import ResmiTatil

SABIT_TATILLER = (
    (1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29),
)


class ResmiTatilHatasi(ValueError):
    """Resmî tatil kural ihlali (Türkçe mesaj)."""


def aktif_tatiller():
    return ResmiTatil.objects.filter(silindi=False).order_by("tarih")


def yil_tatilleri(yil):
    return aktif_tatiller().filter(tarih__year=yil)


def tatil_gunleri_seti(baslangic, bitis) -> set:
    """[baslangic, bitis] (dahil) aralığındaki tatil tarihlerinin kümesi — TEK sorgu."""
    return set(aktif_tatiller().filter(tarih__gte=baslangic, tarih__lte=bitis)
              .values_list("tarih", flat=True))


def gunun_tatili(tarih):
    """O gün tatilse ResmiTatil kaydı, değilse None (günlük yoklama bilgi bandı için)."""
    return ResmiTatil.objects.filter(silindi=False, tarih=tarih).first()


def dini_bayram_var_mi(yil) -> bool:
    """O yıl için SABIT_TATILLER dışında (dinî bayram olabilecek) bir kayıt var mı."""
    sabit_tarihler = {date(yil, ay, gun) for ay, gun in SABIT_TATILLER}
    return yil_tatilleri(yil).exclude(tarih__in=sabit_tarihler).exists()


def _dogrula(*, tarih, ad, haric_pk=None) -> dict:
    if tarih is None:
        raise ResmiTatilHatasi("Tarih zorunlu.")
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise ResmiTatilHatasi("Tatil adı boş olamaz.")
    qs = ResmiTatil.objects.filter(silindi=False, tarih=tarih)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise ResmiTatilHatasi(f"{tarih:%d.%m.%Y} tarihi için zaten bir tatil tanımlı.")
    return {"tarih": tarih, "ad": ad}


def tatil_ekle(*, tarih, ad, kullanici=None) -> ResmiTatil:
    veri = _dogrula(tarih=tarih, ad=ad)
    return ResmiTatil.objects.create(created_by=kullanici, updated_by=kullanici, **veri)


def tatil_guncelle(tatil: ResmiTatil, *, tarih, ad, kullanici=None) -> ResmiTatil:
    if tatil.silindi:
        raise ResmiTatilHatasi("Silinmiş tatil kaydı düzenlenemez.")
    veri = _dogrula(tarih=tarih, ad=ad, haric_pk=tatil.pk)
    tatil.tarih, tatil.ad = veri["tarih"], veri["ad"]
    tatil.updated_by = kullanici
    tatil.save(update_fields=["tarih", "ad", "updated_by", "updated_at"])
    return tatil


def tatil_sil(tatil: ResmiTatil, kullanici=None) -> ResmiTatil:
    if tatil.silindi:
        return tatil
    tatil.silindi = True
    tatil.silindi_at = timezone.now()
    tatil.updated_by = kullanici
    tatil.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return tatil
