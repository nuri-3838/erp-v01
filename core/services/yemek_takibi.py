"""DİĞER > Yemek Takibi servis katmanı — günlük kişi sayımı CRUD + aylık özet.

- Aynı cari+tarih için tek aktif kayıt (DB kısıtı + servis kontrolü); ikinci kez eklenmez,
  düzenlenir.
- Kişi başı ücret her kayıtta AYRI tutulur (snapshot) — ileride ücret değişse bile geçmiş
  kayıtlar bozulmaz. `son_birim_fiyat` yeni kayıt formunun varsayılanı için kullanılır.
- Fatura/yevmiye ÜRETMEZ — yalnız firmanın kestiği faturayla karşılaştırmak için kontrol kaydı.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.models import YemekSayimi
from core.sayi import SayiHatasi, parse_tr


class YemekTakibiHatasi(ValueError):
    """Yemek takibi kural ihlali (Türkçe mesaj)."""


def aktif_kayitlar(cari=None, baslangic=None, bitis=None):
    qs = YemekSayimi.objects.filter(silindi=False).select_related("cari")
    if cari is not None:
        qs = qs.filter(cari=cari)
    if baslangic is not None:
        qs = qs.filter(tarih__gte=baslangic)
    if bitis is not None:
        qs = qs.filter(tarih__lte=bitis)
    return qs.order_by("-tarih")


def son_birim_fiyat(cari):
    """O carinin en son girilen birim fiyatı — yeni kayıt formunda varsayılan olarak
    kullanılır (kullanıcı her gün fiyatı yeniden girmesin)."""
    son = (YemekSayimi.objects.filter(cari=cari, silindi=False)
           .order_by("-tarih", "-id").first())
    return son.birim_fiyat if son else None


def aylik_ozet(kayitlar):
    """(gün sayısı, toplam kişi, toplam tutar) — verilen kayıt kümesi üzerinden Python'da
    toplanır (küçük veri seti; DB agregasyonu gereksiz karmaşıklık katardı)."""
    gun = 0
    kisi = 0
    tutar = Decimal("0")
    for k in kayitlar:
        gun += 1
        kisi += k.kisi_sayisi
        tutar += k.tutar
    return gun, kisi, tutar


def _kisi_sayisi_dogrula(deger):
    try:
        d = int(deger)
    except (TypeError, ValueError):
        raise YemekTakibiHatasi("Kişi sayısı geçerli bir tam sayı olmalı.")
    if d < 0:
        raise YemekTakibiHatasi("Kişi sayısı negatif olamaz.")
    return d


def _birim_fiyat_dogrula(deger):
    try:
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise YemekTakibiHatasi("Kişi başı ücret geçerli bir sayı olmalı.")
    if d < 0:
        raise YemekTakibiHatasi("Kişi başı ücret negatif olamaz.")
    return d


def kayit_ekle(*, cari, tarih, kisi_sayisi, birim_fiyat, notlar="",
               kullanici=None) -> YemekSayimi:
    if not tarih:
        raise YemekTakibiHatasi("Tarih boş olamaz.")
    if YemekSayimi.objects.filter(cari=cari, tarih=tarih, silindi=False).exists():
        raise YemekTakibiHatasi(f"{tarih} için bu cariye zaten kayıt girilmiş; düzenleyin.")
    kisi_sayisi = _kisi_sayisi_dogrula(kisi_sayisi)
    birim_fiyat = _birim_fiyat_dogrula(birim_fiyat)
    return YemekSayimi.objects.create(
        cari=cari, tarih=tarih, kisi_sayisi=kisi_sayisi, birim_fiyat=birim_fiyat,
        notlar=(notlar or "").strip(), created_by=kullanici, updated_by=kullanici)


def kayit_guncelle(kayit: YemekSayimi, *, tarih, kisi_sayisi, birim_fiyat, notlar="",
                   kullanici=None) -> YemekSayimi:
    if kayit.silindi:
        raise YemekTakibiHatasi("Silinmiş kayıt düzenlenemez.")
    if not tarih:
        raise YemekTakibiHatasi("Tarih boş olamaz.")
    if (YemekSayimi.objects.filter(cari=kayit.cari, tarih=tarih, silindi=False)
            .exclude(pk=kayit.pk).exists()):
        raise YemekTakibiHatasi(f"{tarih} için bu cariye zaten başka bir kayıt var.")
    kayit.tarih = tarih
    kayit.kisi_sayisi = _kisi_sayisi_dogrula(kisi_sayisi)
    kayit.birim_fiyat = _birim_fiyat_dogrula(birim_fiyat)
    kayit.notlar = (notlar or "").strip()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["tarih", "kisi_sayisi", "birim_fiyat", "notlar",
                              "updated_by", "updated_at"])
    return kayit


def kayit_sil(kayit: YemekSayimi, kullanici=None) -> YemekSayimi:
    if kayit.silindi:
        return kayit
    kayit.silindi = True
    kayit.silindi_at = timezone.now()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kayit
