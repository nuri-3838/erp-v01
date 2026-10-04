"""Duran varlık / yatırım hesaplarını VARLIK/PROJE bazında detaylandırma (hesap planı tarafı).

Yapı (3 seviye): ``253`` (ana) → ``253.01`` (GRUP) → ``253.01.0001`` (varlık/proje hesabı = YAPRAK; adı kart/proje adı).
  * 253/254/255/260: her duran varlık kartı (``DuranVarlik.hesap``) kendi 000N hesabına sahiptir.
  * 258: her yatırım projesi (``YatirimProjesi.hesap``) kendi 000N hesabına sahiptir; projeye yazılan TÜM 258 satırları o
    hesaba gider (fatura/banka/kart/kesinti/stok sarf/kur farkı).
Hesap, kart satılınca/silinince ya da proje aktifleşince KALIR (geçmiş için) ve yeni karta verilmez (kod hep ilerler).
"""
from __future__ import annotations

import re

from core.models import HesapPlani
from core.services import hesap_plani as hp

GRUPLAR = {
    "253": [("253.01", "ÜRETİM MAKİNELERİ"), ("253.02", "KALIPLAR"), ("253.03", "ATÖLYE EKİPMAN VE EL ALETLERİ"),
            ("253.04", "DEPOLAMA VE TAŞIMA EKİPMANLARI"), ("253.05", "TESİSATLAR")],
    "254": [("254.01", "BİNEK OTOMOBİLLER"), ("254.02", "TİCARİ ARAÇLAR")],
    "255": [("255.01", "OFİS MOBİLYA VE DONANIMLARI"), ("255.02", "BİLGİSAYAR VE ELEKTRONİK"),
            ("255.03", "TABELA VE REKLAM")],
    "258": [("258.01", "MAKİNE-TEÇHİZAT YATIRIMLARI"), ("258.02", "TAŞIT YATIRIMLARI"),
            ("258.03", "DEMİRBAŞ YATIRIMLARI"), ("258.04", "ARSA YATIRIMLARI")],
    "260": [("260.01", "MARKA VE TESCİL"), ("260.02", "YAZILIM LİSANSLARI")],
}
KART_AILELERI = ("253", "254", "255", "260")        # duran varlık kartı açılabilen ana hesaplar
GRUP_DESENI = re.compile(r"^(253|254|255|258|260)\.\d{2}$")
VARLIK_DESENI = re.compile(r"^(253|254|255|258|260)\.\d{2}\.\d{4}$")


class DuranHesapHatasi(ValueError):
    """Varlık/proje hesabı kuralı ihlali (Türkçe mesaj)."""


def grup_hesaplari(aileler=None):
    """Seçilebilir GRUP hesapları (253.01 …): aktif, silinmemiş. ``aileler``: ör. KART_AILELERI ya da ("258",)."""
    aileler = tuple(aileler or tuple(GRUPLAR))
    return (HesapPlani.objects.filter(silindi=False, aktif=True, hesap_kodu__regex=r"^(" + "|".join(aileler) + r")\.\d{2}$")
            .order_by("hesap_kodu"))


def grup_mu(kod: str) -> bool:
    return bool(GRUP_DESENI.match(kod or ""))


def varlik_hesabi_mi(kod: str) -> bool:
    """253.01.0001 biçimi (kart/proje hesabı)."""
    return bool(VARLIK_DESENI.match(kod or ""))


def sonraki_hesap_kodu(grup_kodu: str) -> str:
    """Grubun sıradaki 000N kodu. Silinmiş kodlar da sayılır (PK soft-delete'te korunur) → kod hiç yeniden verilmez."""
    n = 0
    for k in HesapPlani.objects.filter(hesap_kodu__startswith=grup_kodu + ".").values_list("hesap_kodu", flat=True):
        son = k.rsplit(".", 1)[-1]
        if son.isdigit():
            n = max(n, int(son))
    return f"{grup_kodu}.{n + 1:04d}"


def _grup_dogrula(grup_kodu, aileler):
    g = HesapPlani.objects.filter(hesap_kodu=grup_kodu, silindi=False, aktif=True).first()
    if g is None or not grup_mu(grup_kodu) or grup_kodu.split(".")[0] not in aileler:
        raise DuranHesapHatasi(f"Geçerli bir grup hesabı seçin ({', '.join(aileler)} ailesinden).")
    return g


def varlik_hesabi_ac(grup_kodu, ad, *, aileler=KART_AILELERI, kullanici=None) -> HesapPlani:
    """Grubun altında sıradaki 000N hesabını (ad = kart/proje adı) açar."""
    _grup_dogrula(grup_kodu, aileler)
    ad = (ad or "").strip()[:200]
    if not ad:
        raise DuranHesapHatasi("Hesap adı boş olamaz.")
    return hp.hesap_olustur(kod=sonraki_hesap_kodu(grup_kodu), ad=ad, ust_kodu=grup_kodu, kullanici=kullanici)


def proje_hesabi_ac(grup_kodu, ad, *, kullanici=None) -> HesapPlani:
    return varlik_hesabi_ac(grup_kodu, ad, aileler=("258",), kullanici=kullanici)


def proje_hesap_kodu(proje, secilen_kod):
    """258 satırı yazarken kullanılacak hesap kodu: projenin hesabı varsa O (seçilen hesabı ezer), yoksa seçilen."""
    if proje is not None and getattr(proje, "hesap_id", None):
        return proje.hesap_id
    return secilen_kod


def proje_hesabi_mi(kod: str) -> bool:
    return (kod or "").startswith("258.")
