"""ÇEK/SENET modülü (BORDRO mantığı) — servis katmanı.

Slice 1: muhasebe hesap eşlemesi (CekHesapAyari) oku/kaydet. Her durum için çek ve
senet ayrı yaprak hesaba bağlanır; bordro işlemleri yevmiye fişini bu eşlemeden,
evrak tipine (çek/senet) bakarak üretir. Bordro motoru sonraki dilimlerde.
"""
from __future__ import annotations

from django.db import transaction

from core.models import CekHesapAyari
from core.services.finans import FinansHatasi, _yaprak_hesap_coz


class CekHatasi(ValueError):
    """Çek/senet kural ihlali (Türkçe mesaj)."""


# Ayar alanları (durum + tip). Form/DB/initial hep bu listeyi kullanır.
AYAR_ALANLARI = (
    "portfoy_cek", "portfoy_senet",
    "tahsilde_cek", "tahsilde_senet",
    "teminatta_cek", "teminatta_senet",
    "verilen_cek", "verilen_senet",
)


def hesap_ayari() -> CekHesapAyari:
    """Tekil çek/senet hesap ayarı (yoksa oluşturur)."""
    return CekHesapAyari.get()


@transaction.atomic
def hesap_ayari_kaydet(kodlar: dict, kullanici=None) -> CekHesapAyari:
    """kodlar: {alan: hesap_kodu | ""}. Dolu olanlar YAPRAK doğrulanıp atanır;
    boş olanlar None'a çekilir (o durum henüz tanımlanmadı)."""
    ayar = CekHesapAyari.get()
    for alan in AYAR_ALANLARI:
        kod = (kodlar.get(alan) or "").strip()
        if kod:
            try:
                hesap = _yaprak_hesap_coz(kod)
            except FinansHatasi as e:
                raise CekHatasi(str(e))
        else:
            hesap = None
        setattr(ayar, alan, hesap)
    ayar.updated_by = kullanici
    ayar.save()
    return ayar
