"""Fatura ekleri (YALNIZ PDF ve resim) servis katmanı. Dosyalar özel depoda UUID adıyla tutulur, özgün
ad yalnız `orijinal_ad` kolonunda. Doğrulama: uzantı beyaz listesi + boyut + içerik imzası (PDF için
`%PDF-`, resim için Pillow'un gerçekten çözebilmesi). XML (UBL e-fatura/e-irsaliye) KABUL EDİLMEZ
(2026-10-15 kullanıcı kararı: ek olarak yalnız PDF/görsel; mevcut XML'ler ``xml_ek_temizle`` komutuyla temizlendi).
Resim en uzun kenar 2500px/%92 WebP'ye küçültülür (okunabilirlik için); PDF olduğu gibi saklanır. Silme: soft-delete (dosya diskte kalır). Muhasebeye/stoğa dokunmaz —
onaylı faturaya da ek eklenebilir/silinebilir."""
from __future__ import annotations

import os

from django.utils import timezone
from PIL import Image

from core import gorsel
from core.models import Fatura, FaturaEk

RESIM_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IZINLI_UZANTI = RESIM_UZANTI | {".pdf"}
MAKS_BOYUT = 10 * 1024 * 1024            # 10 MB (nginx client_max_body_size ile aynı)
_RESIM_FORMATLARI = {"JPEG", "PNG", "WEBP", "GIF"}
# Makbuz/fatura fotoğrafları okunur kalmalı: genel 1600px/%80 kuralından bilinçli sapma
# (kullanıcı kararı) — en uzun kenar 2500 px'e kadar korunur, WebP kalitesi yüksek.
RESIM_MAKS_KENAR = 2500
RESIM_KALITE = 92


class FaturaEkHatasi(ValueError):
    """Fatura eki kural ihlali (Türkçe mesaj)."""


def ek_listele(fatura: Fatura):
    return FaturaEk.objects.filter(fatura=fatura, silindi=False)


def dosya_kontrol(dosya):
    """Yüklenen dosyayı YALNIZ doğrular (dönüştürmez; kayıt öncesi form kontrolü için) -> (özgün ad, uzantı). Kurallar ``dosya_hazirla`` ile aynı: uzantı beyaz listesi
    (XML reddedilir) + 10 MB + içerik imzası (PDF ``%PDF-``, resim Pillow ile çözülebilir). Hata: FaturaEkHatasi."""
    if dosya is None:
        raise FaturaEkHatasi("Dosya seçilmedi.")
    ad = os.path.basename(getattr(dosya, "name", "") or "dosya")
    uz = os.path.splitext(ad)[1].lower()
    if uz == ".xml":
        raise FaturaEkHatasi(
            f"XML dosyaları ek olarak kabul edilmiyor (yalnız PDF ve görsel): {ad}. e-faturanın PDF görüntüsünü yükleyin.")
    if uz not in IZINLI_UZANTI:
        raise FaturaEkHatasi(
            f"Desteklenmeyen dosya türü: {ad} (izinli: {', '.join(sorted(IZINLI_UZANTI))}).")
    if dosya.size > MAKS_BOYUT:
        raise FaturaEkHatasi(f"Dosya çok büyük (en fazla 10 MB): {ad}")
    if uz == ".pdf":
        bas = dosya.read(1024)
        dosya.seek(0)
        if b"%PDF-" not in bas:
            raise FaturaEkHatasi(f"Geçersiz PDF dosyası: {ad}")
    else:
        try:
            with Image.open(dosya) as im:
                if im.format not in _RESIM_FORMATLARI:
                    raise ValueError("izinsiz resim biçimi")
                im.verify()
        except Exception:
            raise FaturaEkHatasi(f"Geçersiz resim dosyası: {ad}")
        finally:
            dosya.seek(0)
    return ad, uz


def dosya_hazirla(dosya):
    """Yüklenen dosyayı doğrular (``dosya_kontrol``) ve saklanacak hale getirir -> (saklanan dosya, özgün ad). Fatura ve irsaliye ekleri ortak kullanır:
    resim WebP'ye küçültülür, PDF aynen saklanır. Hata: FaturaEkHatasi."""
    ad, uz = dosya_kontrol(dosya)
    if uz == ".pdf":
        return dosya, ad
    try:
        saklanan = gorsel.kucult_webp(
            dosya, max_kenar=RESIM_MAKS_KENAR, kalite=RESIM_KALITE, ad="fatura_ek", duzelt_yon=True)
    except Exception:
        raise FaturaEkHatasi(f"Geçersiz resim dosyası: {ad}")
    return saklanan, ad


def ek_ekle(fatura: Fatura, *, dosya, kullanici=None) -> FaturaEk:
    """Faturaya tek dosya ekler (çoklu yükleme view katmanında döngüyle çağırır)."""
    if fatura.silindi:
        raise FaturaEkHatasi("Silinmiş faturaya dosya eklenemez.")
    saklanan, ad = dosya_hazirla(dosya)
    return FaturaEk.objects.create(
        fatura=fatura, dosya=saklanan, orijinal_ad=ad[:255],
        created_by=kullanici, updated_by=kullanici)


def ek_sil(ek: FaturaEk, kullanici=None) -> FaturaEk:
    if ek.silindi:
        return ek
    ek.silindi = True
    ek.silindi_at = timezone.now()
    ek.updated_by = kullanici
    ek.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ek
