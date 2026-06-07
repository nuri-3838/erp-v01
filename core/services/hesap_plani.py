"""Hesap planı servis katmanı — hesap oluşturma/ad güncelleme/silme + yaprak kuralı (Aşama 1).

Kurallar tek noktada (UI'a güvenilmez):
- Kod: nokta ayraçlı, EN FAZLA 3 SEVİYE; segmentler rakam. Ana hesap noktasız; alt hesap
  kodu üst kodun '<ust>.' önekiyle ve tam bir seviye altta (örn. 320 -> 320.10 -> 320.10.0001).
- Alt hesap rapor_grubu/rapor_kalemi/parasal'ı üst hesaptan MİRAS alır (tutarlılık).
- Yaprak hesap = aktif alt hesabı olmayan hesap. Fiş yalnızca yaprağa kesilir (bu kural
  ayrıca yevmiye servisinde de zorlanır).
- Silme (soft-delete): yevmiye satırı olan VEYA aktif alt hesabı olan hesap silinemez.

Aşama 2 (sonra): rapor roll-up (alt -> ana toplama), otomatik kod üretimi (stok/cari).
"""
from __future__ import annotations

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import HesapPlani, YevmiyeSatir


class HesapHatasi(ValueError):
    """Hesap planı kural ihlali (Türkçe mesaj)."""


# rapor_grubu -> kabul edilen rapor_kalemi değerleri (mevcut seed/şema ile birebir;
# raporlar.py AKTIF_KALEM/PASIF_KALEM bilanço kodları + gelir tablosu bölümleri A..J).
BILANCO_KALEMLERI = ("DV", "DDV", "KVYK", "UVYK", "OZK")
GELIR_KALEMLERI = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
# Boş kalemle (gelir tablosu satırı olmayan) açılabilen ÖZET/sonuç gelir hesapları (690).
GELIR_OZET_KODLARI = ("690",)


def _kalem_dogrula(rapor_grubu, rapor_kalemi, hesap_kodu=""):
    """rapor_kalemi'nin rapor_grubu'na uygun GEÇERLİ bir değer olduğunu zorlar.

    Aksi halde hesap raporlardan sessizce düşer: yanlış/boş kalemli bir BİLANÇO hesabı
    ne aktif ne pasif gruba girer -> aktif ≠ pasif (bilanço dengesizleşir). Mevcut 84
    hesabın hepsi şu anki kalemleriyle geçerlidir; yalnız yeni hatalı giriş engellenir.
    """
    kalem = (rapor_kalemi or "").strip()
    if rapor_grubu == HesapPlani.RaporGrubu.BILANCO:
        if kalem not in BILANCO_KALEMLERI:
            raise HesapHatasi(
                "Bilanço hesabı için rapor kalemi DV (Dönen Varlıklar), DDV (Duran "
                "Varlıklar), KVYK, UVYK veya OZK olmalı; aksi halde hesap bilançoda "
                f"yer almaz ve bilanço dengesizleşir. Girilen: {kalem or '(boş)'}."
            )
    elif rapor_grubu == HesapPlani.RaporGrubu.GELIR_TABLOSU:
        if not kalem:
            # Boş kalem yalnız özet/sonuç gelir hesabında (690) geçerli; aksi halde
            # hesap standart A–J bölümlerinde görünmez (yansıtılmamış satırına düşer).
            if (hesap_kodu or "").split(".")[0] not in GELIR_OZET_KODLARI:
                raise HesapHatasi(
                    "Gelir tablosu hesabı için rapor kalemi A–J arası bir bölüm "
                    "seçilmeli; boş bırakılamaz (boş yalnız 690 gibi özet hesaplarda olur)."
                )
        elif kalem not in GELIR_KALEMLERI:
            raise HesapHatasi(
                "Gelir tablosu hesabı için rapor kalemi A–J arası bir bölüm olmalı. "
                f"Girilen: {kalem}."
            )
    elif rapor_grubu == HesapPlani.RaporGrubu.MALIYET:
        if kalem:
            raise HesapHatasi(
                "Maliyet (7/A) hesabı rapor kalemi kullanmaz; boş bırakın. "
                f"Girilen: {kalem}."
            )
    else:
        raise HesapHatasi(f"Geçersiz rapor grubu: {rapor_grubu!r}.")


def yaprak_mi(hesap: HesapPlani) -> bool:
    """Hesabın aktif alt hesabı yoksa yapraktır (fişe kesilebilir)."""
    return not hesap.alt_hesaplar.filter(silindi=False).exists()


def yaprak_hesaplar():
    """Fişe kesilebilir hesaplar: aktif + yaprak (üst/ara hesaplar hariç)."""
    ust_kodlari = HesapPlani.objects.filter(
        silindi=False, ust_hesap__isnull=False
    ).values_list("ust_hesap_id", flat=True)
    return (
        HesapPlani.objects.filter(aktif=True, silindi=False)
        .exclude(hesap_kodu__in=ust_kodlari)
        .order_by("hesap_kodu")
    )


def alt_kod_oner(ust: HesapPlani) -> str:
    """Üst hesabın altına makul bir sonraki kod önerir (elle değiştirilebilir).

    L2 (ana altı): 2 hane, 10'ar (320.10, 320.20). L3: 4 hane, 1'er (320.10.0001).
    """
    seviye = ust.hesap_kodu.count(".") + 1
    if seviye == 1:
        genislik, artis, ilk = 2, 10, 10
    else:
        genislik, artis, ilk = 4, 1, 1
    sonlar = []
    for c in ust.alt_hesaplar.filter(silindi=False):
        son = c.hesap_kodu.split(".")[-1]
        if son.isdigit():
            sonlar.append(int(son))
    sonraki = (max(sonlar) + artis) if sonlar else ilk
    return f"{ust.hesap_kodu}.{str(sonraki).zfill(genislik)}"


def sonraki_alt_kod(ust_kodu: str, *, genislik=None) -> str:
    """Üst hesabın altında SIRADAKİ BOŞ alt kodu üretir (stok/cari altyapısı).

    En küçük kullanılmamış sıra numarasını bulur (boşlukları doldurur); çakışma
    olmaması için SİLİNMİŞ (soft-delete) hesaplar dahil tüm mevcut kodlarla
    karşılaştırır. L2 -> 2 hane, L3 -> 4 hane (genislik ile değiştirilebilir).
    """
    ust = HesapPlani.objects.filter(hesap_kodu=ust_kodu, silindi=False).first()
    if ust is None:
        raise HesapHatasi(f"Üst hesap bulunamadı: {ust_kodu}")
    cocuk_nokta = ust_kodu.count(".") + 1
    if cocuk_nokta > 2:
        raise HesapHatasi("En fazla 3 seviye; bu hesabın altına alt hesap açılamaz.")
    if genislik is None:
        genislik = 2 if cocuk_nokta == 1 else 4
    on = ust_kodu + "."
    mevcut = set()
    for k in HesapPlani.objects.filter(hesap_kodu__startswith=on).values_list("hesap_kodu", flat=True):
        if k.count(".") == cocuk_nokta:        # yalnız doğrudan çocuklar
            son = k.split(".")[-1]
            if son.isdigit():
                mevcut.add(int(son))
    n = 1
    while n in mevcut:
        n += 1
    return f"{ust_kodu}.{str(n).zfill(genislik)}"


def _kod_dogrula(kod: str, ust):
    if not kod:
        raise HesapHatasi("Hesap kodu boş olamaz.")
    parcalar = kod.split(".")
    if len(parcalar) > 3:
        raise HesapHatasi("Hesap kodu en fazla 3 seviye olabilir (örn. 320.10.0001).")
    if any(not p.isdigit() for p in parcalar):
        raise HesapHatasi("Hesap kodu yalnızca rakam ve nokta içerebilir.")
    if HesapPlani.objects.filter(hesap_kodu=kod).exists():
        raise HesapHatasi(f"{kod} kodu zaten kayıtlı.")
    if ust is None:
        if "." in kod:
            raise HesapHatasi("Ana hesap kodu noktasız olmalı (örn. 320).")
    else:
        on = ust.hesap_kodu + "."
        if not kod.startswith(on):
            raise HesapHatasi(f"Alt hesap kodu '{on}' ile başlamalı.")
        if kod.count(".") != ust.hesap_kodu.count(".") + 1:
            raise HesapHatasi("Alt hesap, üst hesabın tam bir seviye altında olmalı.")


def hesap_olustur(*, kod, ad, ust_kodu=None, rapor_grubu=None,
                  rapor_kalemi="", parasal=None, kullanici=None) -> HesapPlani:
    """Yeni ana ya da alt hesap oluşturur (kurallar + miras). HesapHatasi yükseltebilir."""
    kod = (kod or "").strip()
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise HesapHatasi("Hesap adı boş olamaz.")
    ust = None
    if ust_kodu:
        ust = HesapPlani.objects.filter(hesap_kodu=ust_kodu, silindi=False).first()
        if ust is None:
            raise HesapHatasi(f"Üst hesap bulunamadı: {ust_kodu}")
        if ust.hesap_kodu.count(".") >= 2:
            raise HesapHatasi("En fazla 3 seviye; bu hesabın altına alt hesap açılamaz.")
    _kod_dogrula(kod, ust)
    if ust is not None:                       # MİRAS (tutarlılık)
        rapor_grubu = ust.rapor_grubu
        rapor_kalemi = ust.rapor_kalemi
        parasal = ust.parasal
    elif rapor_grubu not in HesapPlani.RaporGrubu.values:
        raise HesapHatasi("Ana hesap için geçerli bir rapor grubu seçin.")
    # Kalem grubuyla uyumlu mu? (ana: kullanıcı girdisi; alt: üstten miras — yine de kontrol)
    _kalem_dogrula(rapor_grubu, rapor_kalemi, kod)
    return HesapPlani.objects.create(
        hesap_kodu=kod, hesap_adi=ad, ust_hesap=ust,
        rapor_grubu=rapor_grubu, rapor_kalemi=(rapor_kalemi or ""),
        parasal=parasal, aktif=True,
        created_by=kullanici, updated_by=kullanici,
    )


def hesap_adi_guncelle(*, kod, yeni_ad, kullanici=None) -> HesapPlani:
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise HesapHatasi("Hesap bulunamadı.")
    yeni_ad = buyuk_harf_tr((yeni_ad or "").strip())
    if not yeni_ad:
        raise HesapHatasi("Hesap adı boş olamaz.")
    h.hesap_adi = yeni_ad
    h.updated_by = kullanici
    h.save(update_fields=["hesap_adi", "updated_by", "updated_at"])
    return h


def hesap_sil(*, kod, kullanici=None) -> HesapPlani:
    """Soft-delete. Yevmiye satırı olan ya da aktif alt hesabı olan hesap silinemez."""
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise HesapHatasi("Hesap bulunamadı.")
    if YevmiyeSatir.objects.filter(hesap_id=kod, silindi=False).exists():
        raise HesapHatasi("Bu hesaba kesilmiş yevmiye satırı var; silinemez.")
    if h.alt_hesaplar.filter(silindi=False).exists():
        raise HesapHatasi("Bu hesabın alt hesabı var; önce alt hesapları silin.")
    h.silindi = True
    h.silindi_at = timezone.now()
    h.updated_by = kullanici
    h.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return h
