"""İNSAN KAYNAKLARI > Devam / Yoklama servis katmanı.

Yalnız KAYIT: saat, mesai, fazla mesai, ücret/bordro hesabı YOK. Günlük yoklamada her personel için
Geldi / Yarım Gün / Gelmedi girilir; satırı boş bırakmak "girilmemiş" demektir (kayıt tutulmaz).

İzinli / Raporlu durumu SAKLANMAZ: o gün PersonelIzin kaydıyla örtüşüyorsa durum izinden TÜRETİLİR
(tür RAPOR ise Raporlu, diğer türler İzinli; aynı güne birden çok izin değiyorsa RAPOR öncelikli)
ve o satır yoklamada kilitlidir — yoklamaya elle girilemez, POST edilse bile kaydedilmez. Günlük
ızgara ile aylık özet AYNI kuralı (`turet_durum`) kullanır; sonradan girilen bir izin, aynı güne
daha önce girilmiş yoklamayı gölgeler, izin silinince yoklama geri görünür.

Aylık özet, gün sayımında: Pazar günü kayıt yoksa (ve izin de yoksa) günü saymaz — hafta tatili;
Pazar günü kayıt varsa sayar. İzinli/Raporlu günlerde Pazar da sayılmaz (izin günü Pazar hariç
hesaplanır — bkz. personel_izin.pazarsiz_gun). Resmî tatil takvimi bilinçli YOKTUR: tatil günleri
"girilmemiş" görünebilir. Personelin işe giriş/çıkış aralığı ve bugünden sonrası sayılmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Personel, PersonelDevam, PersonelIzin
from core.services.personel import aktif_personeller
from core.tarih import ay_araligi, tr_bugun

DURUMLAR = tuple(PersonelDevam.Durum.values)
IZINLI = "IZINLI"
RAPORLU = "RAPORLU"
MAKS_NOT = 200


class PersonelDevamHatasi(ValueError):
    """Yoklama kural ihlali (Türkçe mesaj)."""


def turet_durum(kayit_durumu, izin_turu) -> str:
    """O günün gösterilecek durumu: izin varsa RAPORLU (tür RAPOR) / IZINLI, yoksa kayıttaki durum,
    o da yoksa "" (girilmemiş)."""
    if izin_turu == PersonelIzin.Tur.RAPOR:
        return RAPORLU
    if izin_turu:
        return IZINLI
    return kayit_durumu or ""


def _izin_turu_sec(turler) -> Optional[str]:
    turler = list(turler)
    if not turler:
        return None
    return PersonelIzin.Tur.RAPOR.value if PersonelIzin.Tur.RAPOR in turler else turler[0]


def gun_uygun_personel(tarih):
    """O gün yoklaması alınabilecek personel: işe girmiş (giriş günü dahil) ve ayrılmamış
    (çıkış günü dahil). Silinmiş kartlar hariç."""
    return (aktif_personeller().filter(ise_giris_tarihi__lte=tarih)
            .filter(Q(isten_cikis_tarihi__isnull=True) | Q(isten_cikis_tarihi__gte=tarih)))


# === Günlük yoklama ===

@dataclass(frozen=True)
class GunSatiri:
    personel: Personel
    kayit: Optional[PersonelDevam]
    izin_turu: Optional[str]

    @property
    def kilitli(self) -> bool:
        return self.izin_turu is not None

    @property
    def durum(self) -> str:
        return turet_durum(self.kayit.durum if self.kayit else "", self.izin_turu)

    @property
    def notlar(self) -> str:
        return self.kayit.notlar if self.kayit else ""


def gun_satirlari(tarih) -> list:
    """O günün yoklama satırları (uygun her personel için 1 satır; ad/soyad sırasıyla). Sabit 3
    sorgu: personel + o günün kayıtları + o güne değen izinler."""
    personeller = list(gun_uygun_personel(tarih))
    ids = [p.pk for p in personeller]
    kayitlar = {k.personel_id: k for k in PersonelDevam.objects.filter(
        silindi=False, tarih=tarih, personel_id__in=ids)}
    izinler = {}
    for pid, tur in PersonelIzin.objects.filter(
            silindi=False, personel_id__in=ids, baslangic__lte=tarih, bitis__gte=tarih
    ).values_list("personel_id", "tur"):
        izinler.setdefault(pid, []).append(tur)
    return [GunSatiri(p, kayitlar.get(p.pk), _izin_turu_sec(izinler.get(p.pk, ()))) for p in personeller]


def gun_ozeti(satirlar) -> dict:
    """Günlük ızgaranın durum sayıları (başlıktaki özet için)."""
    sayac = {"GELDI": 0, "YARIM_GUN": 0, "GELMEDI": 0, IZINLI: 0, RAPORLU: 0, "": 0}
    for s in satirlar:
        sayac[s.durum] += 1
    return {"geldi": sayac["GELDI"], "yarim": sayac["YARIM_GUN"], "gelmedi": sayac["GELMEDI"],
            "izinli": sayac[IZINLI], "raporlu": sayac[RAPORLU], "girilmemis": sayac[""]}


def gunluk_kaydet(tarih, satirlar, *, kullanici=None):
    """`satirlar`: {personel_id: (durum, not)}. durum: GELDI / GELMEDI / YARIM_GUN ya da "" (girilmemiş
    → o güne ait mevcut kayıt soft-delete). Gönderilmeyen personele dokunulmaz. O gün uygun
    olmayan (henüz girmemiş / ayrılmış) ya da izinli personel ATLANIR. Gelecek tarih reddedilir.
    Tek transaction; (kaydedilen, temizlenen, atlanan) döner."""
    if tarih > tr_bugun():
        raise PersonelDevamHatasi("Gelecek tarih için yoklama girilemez.")
    temiz = {}
    for pid, (durum, notu) in satirlar.items():
        durum = (durum or "").strip()
        notu = (notu or "").strip()
        if durum and durum not in DURUMLAR:
            raise PersonelDevamHatasi("Geçersiz devam durumu.")
        if len(notu) > MAKS_NOT:
            raise PersonelDevamHatasi(f"Not en fazla {MAKS_NOT} karakter olabilir.")
        temiz[int(pid)] = (durum, notu)

    kaydedilen = temizlenen = atlanan = 0
    with transaction.atomic():
        uygun = {p.pk: p for p in gun_uygun_personel(tarih).select_for_update()}   # eşzamanlı kayıtları sıraya dizer
        izinli = set(PersonelIzin.objects.filter(
            silindi=False, personel_id__in=list(uygun), baslangic__lte=tarih, bitis__gte=tarih
        ).values_list("personel_id", flat=True))
        mevcut = {k.personel_id: k for k in PersonelDevam.objects.filter(
            silindi=False, tarih=tarih, personel_id__in=list(uygun))}
        for pid, (durum, notu) in temiz.items():
            if pid not in uygun or pid in izinli:
                atlanan += 1
                continue
            kayit = mevcut.get(pid)
            if not durum:
                if kayit:
                    kayit.silindi = True
                    kayit.silindi_at = timezone.now()
                    kayit.updated_by = kullanici
                    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
                    temizlenen += 1
                continue
            if kayit is None:
                PersonelDevam.objects.create(
                    personel=uygun[pid], tarih=tarih, durum=durum, notlar=notu,
                    created_by=kullanici, updated_by=kullanici)
                kaydedilen += 1
            elif (kayit.durum, kayit.notlar) != (durum, notu):
                kayit.durum = durum
                kayit.notlar = notu
                kayit.updated_by = kullanici
                kayit.save(update_fields=["durum", "notlar", "updated_by", "updated_at"])
                kaydedilen += 1
    return kaydedilen, temizlenen, atlanan


# === Aylık özet ===

@dataclass(frozen=True)
class AylikSatir:
    personel: Optional[Personel]
    geldi: int = 0
    yarim: int = 0
    gelmedi: int = 0
    izinli: int = 0
    raporlu: int = 0
    girilmemis: int = 0


_SAYAC_ALANLARI = ("geldi", "yarim", "gelmedi", "izinli", "raporlu", "girilmemis")
_DURUM_SAYAC = {"GELDI": "geldi", "YARIM_GUN": "yarim", "GELMEDI": "gelmedi"}


def aylik_ozet(yil, ay, *, bugun=None, personel_ids=None) -> list:
    """Ayın personel bazlı özeti: [AylikSatir]. Yalnız o ay (bugüne kadar) çalışmış kişiler; sabit
    3 sorgu + bellekte gün döngüsü. `personel_ids` verilirse yalnız o kişiler."""
    bugun = bugun or tr_bugun()
    ilk, son = ay_araligi(yil, ay)
    qs = (aktif_personeller().filter(ise_giris_tarihi__lte=son)
          .filter(Q(isten_cikis_tarihi__isnull=True) | Q(isten_cikis_tarihi__gte=ilk)))
    if personel_ids is not None:
        qs = qs.filter(pk__in=list(personel_ids))
    personeller = list(qs)
    ids = [p.pk for p in personeller]
    kayitlar = {(pid, t): d for pid, t, d in PersonelDevam.objects.filter(
        silindi=False, personel_id__in=ids, tarih__range=(ilk, son)
    ).values_list("personel_id", "tarih", "durum")}
    izinler = {}
    for pid, b, e, tur in PersonelIzin.objects.filter(
            silindi=False, personel_id__in=ids, baslangic__lte=son, bitis__gte=ilk
    ).values_list("personel_id", "baslangic", "bitis", "tur"):
        izinler.setdefault(pid, []).append((b, e, tur))

    satirlar = []
    for p in personeller:
        bas = max(ilk, p.ise_giris_tarihi)
        bit = min(son, p.isten_cikis_tarihi or son, bugun)
        if bas > bit:
            continue
        sayac = dict.fromkeys(_SAYAC_ALANLARI, 0)
        kisi_izinleri = izinler.get(p.pk, ())
        gun = bas
        while gun <= bit:
            pazar = gun.weekday() == 6
            izin_turu = _izin_turu_sec(t for b, e, t in kisi_izinleri if b <= gun <= e)
            if izin_turu:
                if not pazar:
                    sayac["raporlu" if turet_durum("", izin_turu) == RAPORLU else "izinli"] += 1
            elif (p.pk, gun) in kayitlar:
                sayac[_DURUM_SAYAC[kayitlar[(p.pk, gun)]]] += 1
            elif not pazar:
                sayac["girilmemis"] += 1
            gun += timedelta(days=1)
        satirlar.append(AylikSatir(personel=p, **sayac))
    return satirlar


def aylik_toplam(satirlar) -> AylikSatir:
    return AylikSatir(personel=None, **{a: sum(getattr(s, a) for s in satirlar)
                                        for a in _SAYAC_ALANLARI})
