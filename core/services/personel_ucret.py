"""İNSAN KAYNAKLARI > Personel Ücretleri servis katmanı.

Yalnız KAYIT: bordro/brüt/SGK/vergi hesabı YOK. Bir kayıt, ``gecerlilik_baslangic``
tarihinden İTİBAREN (bir sonraki kayda kadar) geçerli ücreti temsil eder — "o tarihte
geçerli ücret", personelin gecerlilik_baslangic'i o tarihte veya öncesinde olan
kayıtlarının EN SON (en büyük başlangıçlı) olanıdır. Zam/ücret değişikliği yeni bir kayıt
eklemekle yapılır; eski kayıt SİLİNMEZ (ücret geçmişi olarak kalır).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import Personel, PersonelUcret
from core.tarih import tr_bugun


class PersonelUcretHatasi(ValueError):
    """Personel ücret kural ihlali (Türkçe mesaj)."""


def aktif_ucretler():
    return (PersonelUcret.objects.filter(silindi=False, personel__silindi=False)
            .select_related("personel"))


def ucretler(personel: Personel):
    """Bir personelin tüm ücret geçmişi, en yeni başlangıç önce."""
    return PersonelUcret.objects.filter(personel=personel, silindi=False) \
        .order_by("-gecerlilik_baslangic", "-id")


def gecerli_ucret(personel: Personel, tarih=None):
    """``tarih`` (dahil) itibarıyla geçerli ücret kaydı — o tarihte veya öncesinde başlamış
    kayıtların en son başlayanı; hiç uygun kayıt yoksa (ör. hepsi ileri tarihli) None."""
    tarih = tarih or tr_bugun()
    return (PersonelUcret.objects.filter(
                personel=personel, silindi=False, gecerlilik_baslangic__lte=tarih)
            .order_by("-gecerlilik_baslangic", "-id").first())


def gecerli_ucretler(personeller, *, tarih=None) -> dict:
    """{personel.pk: PersonelUcret|None} — TEK sorguyla (kişi başına sorgu yok); personel_belge
    servisindeki "en son kayıt" gruplama deseniyle aynı (Python tarafında, DB bağımsız)."""
    tarih = tarih or tr_bugun()
    kimlikler = [p.pk for p in personeller]
    if not kimlikler:
        return {}
    satirlar = PersonelUcret.objects.filter(
        silindi=False, personel_id__in=kimlikler, gecerlilik_baslangic__lte=tarih)
    en_son = {}
    for u in satirlar:
        mevcut = en_son.get(u.personel_id)
        if mevcut is None or (u.gecerlilik_baslangic, u.pk) > (mevcut.gecerlilik_baslangic, mevcut.pk):
            en_son[u.personel_id] = u
    return {pid: en_son.get(pid) for pid in kimlikler}


def _kilitle(personel_id):
    """Aynı kişiye eşzamanlı iki ücret kaydı benzersizlik kontrolünü atlatmasın diye satır kilidi."""
    p = Personel.objects.select_for_update().filter(pk=personel_id).first()
    if p is None or p.silindi:
        raise PersonelUcretHatasi("Silinmiş personele ücret kaydı girilemez / düzenlenemez.")
    return p


def _dogrula(personel, *, gecerlilik_baslangic, tip, net_tutar=None, aciklama="") -> dict:
    if tip not in PersonelUcret.Tip.values:
        raise PersonelUcretHatasi("Geçersiz ücret tipi.")
    if gecerlilik_baslangic is None:
        raise PersonelUcretHatasi("Geçerlilik başlangıcı zorunlu.")
    if gecerlilik_baslangic < personel.ise_giris_tarihi:
        raise PersonelUcretHatasi(
            f"Geçerlilik başlangıcı, işe giriş tarihinden "
            f"({personel.ise_giris_tarihi:%d.%m.%Y}) önce olamaz.")
    if tip == PersonelUcret.Tip.ASGARI:
        net_tutar = None
    else:
        if net_tutar is None or net_tutar == "":
            raise PersonelUcretHatasi("Net ücret tipinde tutar zorunlu.")
        net_tutar = Decimal(net_tutar)
        if net_tutar <= 0:
            raise PersonelUcretHatasi("Net tutar sıfırdan büyük olmalı.")
    return {"gecerlilik_baslangic": gecerlilik_baslangic, "tip": tip, "net_tutar": net_tutar,
            "aciklama": (aciklama or "").strip()}


def _benzersizlik_kontrol(personel, gecerlilik_baslangic, *, haric_pk=None):
    qs = PersonelUcret.objects.filter(
        personel=personel, silindi=False, gecerlilik_baslangic=gecerlilik_baslangic)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    if qs.exists():
        raise PersonelUcretHatasi(
            f"Bu personel için {gecerlilik_baslangic:%d.%m.%Y} başlangıçlı bir ücret kaydı "
            f"zaten var.")


@transaction.atomic
def ucret_ekle(personel: Personel, *, gecerlilik_baslangic, tip, net_tutar=None, aciklama="",
              kullanici=None) -> PersonelUcret:
    p = _kilitle(personel.pk)
    veri = _dogrula(p, gecerlilik_baslangic=gecerlilik_baslangic, tip=tip, net_tutar=net_tutar,
                    aciklama=aciklama)
    _benzersizlik_kontrol(p, veri["gecerlilik_baslangic"])
    return PersonelUcret.objects.create(
        personel=p, created_by=kullanici, updated_by=kullanici, **veri)


@transaction.atomic
def ucret_guncelle(ucret: PersonelUcret, *, gecerlilik_baslangic, tip, net_tutar=None,
                   aciklama="", kullanici=None) -> PersonelUcret:
    if ucret.silindi:
        raise PersonelUcretHatasi("Silinmiş ücret kaydı düzenlenemez.")
    p = _kilitle(ucret.personel_id)
    veri = _dogrula(p, gecerlilik_baslangic=gecerlilik_baslangic, tip=tip, net_tutar=net_tutar,
                    aciklama=aciklama)
    _benzersizlik_kontrol(p, veri["gecerlilik_baslangic"], haric_pk=ucret.pk)
    for alan, deger in veri.items():
        setattr(ucret, alan, deger)
    ucret.updated_by = kullanici
    ucret.save(update_fields=[*veri.keys(), "updated_by", "updated_at"])
    return ucret


def ucret_sil(ucret: PersonelUcret, kullanici=None) -> PersonelUcret:
    if ucret.silindi:
        return ucret
    ucret.silindi = True
    ucret.silindi_at = timezone.now()
    ucret.updated_by = kullanici
    ucret.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ucret
