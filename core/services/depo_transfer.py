"""Depo transferi: aynı stoğu bir depodan diğerine taşır. İki hareket (çıkış + giriş, kaynak=
TRANSFER) tek ``transfer_grubu`` kimliğiyle yazılır. Ağırlıklı ortalama maliyeti DEĞİŞTİRMEZ
(tek ortalama, tüm depolar — bkz. core.services.stok_ortalama) ve muhasebe fişi ÜRETMEZ.
Fason deposuna gönderim/dönüş de bu servisle yapılır. Çıkış, kaynak depodaki eldeki miktardan
fazla olamaz; transferi silmek hedef depoda yeterli eldeki gerektirir."""
from __future__ import annotations

import uuid

from django.db import transaction

from core.models import Depo, StokHareket
from core.services.hareket import HareketHatasi, hareket_ekle, hareket_sil


@transaction.atomic
def depo_transferi_yap(*, stok_id, kaynak_depo_id, hedef_depo_id, tarih, miktar, aciklama="",
                       kullanici=None) -> uuid.UUID:
    if str(kaynak_depo_id) == str(hedef_depo_id):
        raise HareketHatasi("Kaynak ve hedef depo aynı olamaz.")
    grup = uuid.uuid4()
    kaynak = Depo.objects.filter(pk=kaynak_depo_id, silindi=False).first()
    hedef = Depo.objects.filter(pk=hedef_depo_id, silindi=False).first()
    if kaynak is None or hedef is None:
        raise HareketHatasi("Depo bulunamadı.")
    metin = (aciklama or "").strip()
    cikis = hareket_ekle(
        stok_id=stok_id, depo_id=kaynak.pk, tarih=tarih, tur=StokHareket.Tur.CIKIS,
        miktar=miktar, aciklama=f"Transfer → {hedef.kod}" + (f" · {metin}" if metin else ""),
        kaynak=StokHareket.Kaynak.TRANSFER, transfer_grubu=grup, kullanici=kullanici)
    hareket_ekle(
        stok_id=stok_id, depo_id=hedef.pk, tarih=tarih, tur=StokHareket.Tur.GIRIS,
        miktar=cikis.miktar, aciklama=f"Transfer ← {kaynak.kod}" + (f" · {metin}" if metin else ""),
        kaynak=StokHareket.Kaynak.TRANSFER, transfer_grubu=grup, kullanici=kullanici)
    return grup


@transaction.atomic
def depo_transferi_sil(grup, kullanici=None) -> int:
    """Transferin iki bacağını da siler (önce hedefteki giriş: o depoda yeterli eldeki yoksa
    HareketHatasi). Silinen hareket sayısını döner."""
    hareketler = list(StokHareket.objects.filter(
        transfer_grubu=grup, silindi=False).order_by("-tur", "id"))   # GIRIS önce (G > C)
    for h in hareketler:
        hareket_sil(h, kullanici=kullanici, _transfer_icinden=True)
    return len(hareketler)
