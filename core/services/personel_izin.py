"""İNSAN KAYNAKLARI > İzin Takibi servis katmanı.

Yalnız KAYIT: ücret/bordro hesabı yok. Yıllık izin hak edişi İş Kanunu m.53'e göre kıdemden
HESAPLANIR (saklanmaz): her TAMAMLANAN hizmet yılı için 1-5. yıl 14, 6-14. yıl 20, 15+ yıl 26
gün; işçi 18 yaş ve altı ya da 50 yaş ve üzeriyse o yıl için en az 20 gün. Bakiye =
hak edilen − (sisteme geçmeden önce kullanılan + sistemde girilmiş YILLIK izin günleri);
negatif olabilir (engellenmez, kırmızı gösterilir). Rapor/mazeret/ücretsiz/diğer izinler
yalnız kayıttır, bakiyeyi düşürmez.

Gün sayısı Pazar günleri hariç takvim günü olarak ÖNERİLİR; resmî tatil takvimi bilinçli
YOKTUR (kullanıcı gün sayısını yarım gün adımlarıyla elle düzeltebilir).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Personel, PersonelIzin
from core.tarih import tamamlanan_yil, tr_bugun, yil_donumu

SIFIR = Decimal("0.0")


class PersonelIzinHatasi(ValueError):
    """İzin kural ihlali (Türkçe mesaj)."""


# === Yıllık izin hak edişi (saf fonksiyonlar, veritabanı yok) ===

def yillik_hak_gunu(hizmet_yili: int, yas) -> int:
    """hizmet_yili: tamamlanan hizmet yılı sırası (1, 2, ...). yas: o yıl dönümündeki tamamlanmış
    yaş (bilinmiyorsa None → yaş kuralı uygulanmaz)."""
    if hizmet_yili < 1:
        return 0
    temel = 14 if hizmet_yili <= 5 else (20 if hizmet_yili <= 14 else 26)
    if yas is not None and (yas <= 18 or yas >= 50):
        temel = max(temel, 20)
    return temel


def hak_edilen_gun(giris, dogum, tarih) -> int:
    """tarih (dahil) itibarıyla tamamlanan her hizmet yılı için toplam yıllık izin hakkı."""
    if giris is None or tarih < giris:
        return 0
    toplam, n = 0, 1
    while (d := yil_donumu(giris, n)) <= tarih:
        yas = tamamlanan_yil(dogum, d) if dogum else None
        toplam += yillik_hak_gunu(n, yas)
        n += 1
    return toplam


@dataclass(frozen=True)
class IzinBakiye:
    hak_edilen: Decimal
    onceki: Decimal
    kullanilan: Decimal          # sistemde girilmiş YILLIK izinler (planlananlar dahil)
    planlanan: Decimal           # kullanilan'ın başlangıcı bugünden SONRA olan kısmı
    kalan: Decimal
    sonraki_hak_tarihi: date | None

    @property
    def eksi_mi(self) -> bool:
        return self.kalan < 0


def _as_of(personel, bugun):
    """Ayrılmış personelin bakiyesi çıkış tarihi itibarıyla hesaplanır."""
    if personel.isten_cikis_tarihi is not None and personel.isten_cikis_tarihi < bugun:
        return personel.isten_cikis_tarihi
    return bugun


def _bakiye_hesapla(personel, kullanilan, planlanan, bugun) -> IzinBakiye:
    tarih = _as_of(personel, bugun)
    hak = Decimal(hak_edilen_gun(personel.ise_giris_tarihi, personel.dogum_tarihi, tarih))
    onceki = Decimal(personel.izin_onceki_kullanilan or 0)
    kullanilan = Decimal(kullanilan or 0)
    planlanan = Decimal(planlanan or 0)
    ayrildi = personel.isten_cikis_tarihi is not None and personel.isten_cikis_tarihi < bugun
    sonraki = None
    if not ayrildi:
        sonraki = yil_donumu(personel.ise_giris_tarihi,
                             tamamlanan_yil(personel.ise_giris_tarihi, tarih) + 1)
    return IzinBakiye(
        hak_edilen=hak, onceki=onceki, kullanilan=kullanilan, planlanan=planlanan,
        kalan=hak - onceki - kullanilan, sonraki_hak_tarihi=sonraki)


def bakiye(personel: Personel, *, bugun=None) -> IzinBakiye:
    bugun = bugun or tr_bugun()
    yillik = PersonelIzin.objects.filter(
        personel=personel, silindi=False, tur=PersonelIzin.Tur.YILLIK)
    kullanilan = yillik.aggregate(t=Sum("gun"))["t"] or SIFIR
    planlanan = yillik.filter(baslangic__gt=bugun).aggregate(t=Sum("gun"))["t"] or SIFIR
    return _bakiye_hesapla(personel, kullanilan, planlanan, bugun)


def bakiyeler(personeller, *, bugun=None) -> dict:
    """{personel.pk: IzinBakiye} — TEK gruplu sorguyla (kişi başına sorgu yok)."""
    bugun = bugun or tr_bugun()
    liste = list(personeller)
    yillik = (PersonelIzin.objects
              .filter(silindi=False, tur=PersonelIzin.Tur.YILLIK,
                      personel_id__in=[p.pk for p in liste])
              .values("personel_id")
              .annotate(t=Sum("gun"), pl=Sum("gun", filter=Q(baslangic__gt=bugun))))
    toplamlar = {r["personel_id"]: (r["t"], r["pl"]) for r in yillik}
    return {p.pk: _bakiye_hesapla(p, *toplamlar.get(p.pk, (SIFIR, SIFIR)), bugun)
            for p in liste}


# === İzin kayıtları ===

def pazarsiz_gun(baslangic: date, bitis: date) -> Decimal:
    """baslangic..bitis (dahil) arası takvim günü eksi Pazar günleri. Resmî tatil DÜŞÜLMEZ."""
    n = 0
    d = baslangic
    while d <= bitis:
        if d.weekday() != 6:
            n += 1
        d += timedelta(days=1)
    return Decimal(n)


def aktif_izinler():
    return (PersonelIzin.objects.filter(silindi=False, personel__silindi=False)
            .select_related("personel"))


def izin_listele(*, personel_id=None, tur="", baslangic=None, bitis=None, izinde_bugun=None):
    """Filtreler: kişi, tür, tarih aralığı (ÇAKIŞMA anlamıyla: aralığa değen her izin) ve
    `izinde_bugun` (verilen günü kapsayan izinler)."""
    qs = aktif_izinler()
    if personel_id:
        qs = qs.filter(personel_id=personel_id)
    if tur:
        qs = qs.filter(tur=tur)
    if baslangic:
        qs = qs.filter(bitis__gte=baslangic)
    if bitis:
        qs = qs.filter(baslangic__lte=bitis)
    if izinde_bugun:
        qs = qs.filter(baslangic__lte=izinde_bugun, bitis__gte=izinde_bugun)
    return qs


def izinde_olanlar(bugun=None):
    bugun = bugun or tr_bugun()
    return izin_listele(izinde_bugun=bugun)


def _dogrula(personel, *, tur, baslangic, bitis, gun, aciklama) -> dict:
    if tur not in PersonelIzin.Tur.values:
        raise PersonelIzinHatasi("Geçersiz izin türü.")
    if baslangic is None or bitis is None:
        raise PersonelIzinHatasi("Başlangıç ve bitiş tarihi zorunlu.")
    if bitis < baslangic:
        raise PersonelIzinHatasi("Bitiş tarihi başlangıçtan önce olamaz.")
    if baslangic < personel.ise_giris_tarihi:
        raise PersonelIzinHatasi(
            f"İzin, işe giriş tarihinden ({personel.ise_giris_tarihi:%d.%m.%Y}) önce başlayamaz.")
    if personel.isten_cikis_tarihi is not None and bitis > personel.isten_cikis_tarihi:
        raise PersonelIzinHatasi(
            f"İzin, işten çıkış tarihinden ({personel.isten_cikis_tarihi:%d.%m.%Y}) sonra "
            f"sürdürülemez.")
    takvim = Decimal((bitis - baslangic).days + 1)
    if gun is None or gun == "":
        gun = pazarsiz_gun(baslangic, bitis)
        if gun <= 0:
            raise PersonelIzinHatasi(
                "Seçilen aralıkta sayılacak gün yok (yalnız Pazar). Gün sayısını elle girin.")
    else:
        gun = Decimal(gun)
        if gun <= 0:
            raise PersonelIzinHatasi("Gün sayısı sıfırdan büyük olmalı.")
        if (gun * 2) % 1 != 0:
            raise PersonelIzinHatasi("Gün sayısı yarım gün adımlarıyla girilmeli (örn. 2 veya 2,5).")
        if gun > takvim:
            raise PersonelIzinHatasi(
                f"Gün sayısı ({gun}) seçilen aralıktaki takvim gününden ({takvim}) fazla olamaz.")
    return {"tur": tur, "baslangic": baslangic, "bitis": bitis, "gun": gun,
            "aciklama": (aciklama or "").strip()}


def _cakisma_kontrol(personel, baslangic, bitis, *, haric_pk=None):
    qs = PersonelIzin.objects.filter(
        personel=personel, silindi=False, baslangic__lte=bitis, bitis__gte=baslangic)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    mevcut = qs.order_by("baslangic").first()
    if mevcut is not None:
        raise PersonelIzinHatasi(
            f"Bu personelin {mevcut.baslangic:%d.%m.%Y} – {mevcut.bitis:%d.%m.%Y} tarihli "
            f"({mevcut.get_tur_display()}) izniyle çakışıyor.")


def _kilitle(personel_id):
    """Aynı kişiye eşzamanlı iki izin kaydı çakışma kontrolünü atlatmasın diye satır kilidi."""
    p = Personel.objects.select_for_update().filter(pk=personel_id).first()
    if p is None or p.silindi:
        raise PersonelIzinHatasi("Silinmiş personele izin girilemez / düzenlenemez.")
    return p


@transaction.atomic
def izin_ekle(personel: Personel, *, tur, baslangic, bitis, gun=None, aciklama="",
              kullanici=None) -> PersonelIzin:
    p = _kilitle(personel.pk)
    veri = _dogrula(p, tur=tur, baslangic=baslangic, bitis=bitis, gun=gun, aciklama=aciklama)
    _cakisma_kontrol(p, veri["baslangic"], veri["bitis"])
    return PersonelIzin.objects.create(
        personel=p, created_by=kullanici, updated_by=kullanici, **veri)


@transaction.atomic
def izin_guncelle(izin: PersonelIzin, *, tur, baslangic, bitis, gun=None, aciklama="",
                  kullanici=None) -> PersonelIzin:
    if izin.silindi:
        raise PersonelIzinHatasi("Silinmiş izin kaydı düzenlenemez.")
    p = _kilitle(izin.personel_id)
    veri = _dogrula(p, tur=tur, baslangic=baslangic, bitis=bitis, gun=gun, aciklama=aciklama)
    _cakisma_kontrol(p, veri["baslangic"], veri["bitis"], haric_pk=izin.pk)
    for alan, deger in veri.items():
        setattr(izin, alan, deger)
    izin.updated_by = kullanici
    izin.save(update_fields=[*veri.keys(), "updated_by", "updated_at"])
    return izin


def izin_sil(izin: PersonelIzin, kullanici=None) -> PersonelIzin:
    if izin.silindi:
        return izin
    izin.silindi = True
    izin.silindi_at = timezone.now()
    izin.updated_by = kullanici
    izin.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return izin
