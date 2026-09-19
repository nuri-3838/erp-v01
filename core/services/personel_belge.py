"""İNSAN KAYNAKLARI > Özlük Belgeleri + personel fotoğrafı servis katmanı.

Dosyalar KVKK hassas veridir: MEDIA_ROOT DIŞINDAKİ özel depoya (core.storage) UUID adıyla
yazılır, özgün ad yalnız `orijinal_ad` kolonunda tutulur. Doğrulama (mevcut Cari/Aday ek
servislerinden daha sıkı): uzantı beyaz listesi + boyut sınırı + içerik kontrolü (PDF için
`%PDF-` imzası, resim için Pillow'un gerçekten çözebilmesi ve biçiminin izinli olması) —
yalnız uzantıya güvenilmez. Resimler ~1600px/%80 WebP'ye küçültülür ve EXIF yönüne göre
döndürülür (EXIF metadata'sı atılır); PDF'ler olduğu gibi saklanır.

Bir belge kaydı = bir dosya. Yenileme = aynı türden YENİ kayıt; eski kayıt geçmiş olarak
kalır. "Süresi dolacaklar" yalnız her (personel, tür) grubunun EN GEÇ bitişli kaydına bakar;
grupta bitiş tarihi olmayan (süresiz) bir kayıt varsa grup süresiz sayılır ve uyarı vermez.
Silme: soft-delete (fiziksel dosya diskte kalır — "fiziksel silme yok" invariant'ı).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from PIL import Image

from core import gorsel
from core.models import Personel, PersonelBelge
from core.tarih import tr_bugun

RESIM_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IZINLI_UZANTI = RESIM_UZANTI | {".pdf"}
MAKS_BOYUT = 10 * 1024 * 1024            # 10 MB (nginx client_max_body_size ile aynı)
_RESIM_FORMATLARI = {"JPEG", "PNG", "WEBP", "GIF"}


class PersonelBelgeHatasi(ValueError):
    """Özlük belgesi kural ihlali (Türkçe mesaj)."""


def _dosya_hazirla(dosya, *, yalniz_resim=False):
    """(saklanacak_icerik, ozgun_ad) döner; geçersizse PersonelBelgeHatasi."""
    ad = os.path.basename(getattr(dosya, "name", "") or "dosya")
    uz = os.path.splitext(ad)[1].lower()
    izinli = RESIM_UZANTI if yalniz_resim else IZINLI_UZANTI
    if uz not in izinli:
        raise PersonelBelgeHatasi(
            f"Desteklenmeyen dosya türü: {ad} (izinli: {', '.join(sorted(izinli))}).")
    if dosya.size > MAKS_BOYUT:
        raise PersonelBelgeHatasi(f"Dosya çok büyük (en fazla 10 MB): {ad}")
    if uz == ".pdf":
        bas = dosya.read(1024)
        dosya.seek(0)
        if b"%PDF-" not in bas:
            raise PersonelBelgeHatasi(f"Geçersiz PDF dosyası: {ad}")
        return dosya, ad
    try:
        with Image.open(dosya) as im:
            if im.format not in _RESIM_FORMATLARI:
                raise ValueError("izinsiz resim biçimi")
            im.verify()
        dosya.seek(0)
        return gorsel.kucult_webp(
            dosya, max_kenar=1600, kalite=80, ad="ik", duzelt_yon=True), ad
    except Exception:
        raise PersonelBelgeHatasi(f"Geçersiz resim dosyası: {ad}")


# === Belge kayıtları ===

def aktif_belgeler():
    return (PersonelBelge.objects.filter(silindi=False, personel__silindi=False)
            .select_related("personel"))


def belge_listele(*, personel_id=None, tur=""):
    qs = aktif_belgeler()
    if personel_id:
        qs = qs.filter(personel_id=personel_id)
    if tur:
        qs = qs.filter(tur=tur)
    return qs


def belge_ekle(personel: Personel, *, tur, dosya, aciklama="", bitis_tarihi=None,
               kullanici=None) -> PersonelBelge:
    if personel.silindi:
        raise PersonelBelgeHatasi("Silinmiş personele belge eklenemez.")
    if tur not in PersonelBelge.Tur.values:
        raise PersonelBelgeHatasi("Geçersiz belge türü.")
    if dosya is None:
        raise PersonelBelgeHatasi("Dosya seçilmedi.")
    icerik, ozgun_ad = _dosya_hazirla(dosya)
    return PersonelBelge.objects.create(
        personel=personel, tur=tur, aciklama=(aciklama or "").strip(), dosya=icerik,
        orijinal_ad=ozgun_ad[:255], bitis_tarihi=bitis_tarihi,
        created_by=kullanici, updated_by=kullanici)


def belge_guncelle(belge: PersonelBelge, *, tur, aciklama="", bitis_tarihi=None,
                   kullanici=None) -> PersonelBelge:
    """Yalnız tür/açıklama/bitiş tarihi — DOSYA değişmez (yenileme = yeni kayıt)."""
    if belge.silindi:
        raise PersonelBelgeHatasi("Silinmiş belge düzenlenemez.")
    if tur not in PersonelBelge.Tur.values:
        raise PersonelBelgeHatasi("Geçersiz belge türü.")
    belge.tur = tur
    belge.aciklama = (aciklama or "").strip()
    belge.bitis_tarihi = bitis_tarihi
    belge.updated_by = kullanici
    belge.save(update_fields=["tur", "aciklama", "bitis_tarihi", "updated_by", "updated_at"])
    return belge


def belge_sil(belge: PersonelBelge, kullanici=None) -> PersonelBelge:
    if belge.silindi:
        return belge
    belge.silindi = True
    belge.silindi_at = timezone.now()
    belge.updated_by = kullanici
    belge.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return belge


# === Fotoğraf ===

def foto_ayarla(personel: Personel, dosya, kullanici=None) -> Personel:
    if personel.silindi:
        raise PersonelBelgeHatasi("Silinmiş personele fotoğraf eklenemez.")
    if dosya is None:
        raise PersonelBelgeHatasi("Dosya seçilmedi.")
    icerik, _ = _dosya_hazirla(dosya, yalniz_resim=True)
    personel.foto.save("foto.webp", icerik, save=False)     # eski dosya diskte kalır
    personel.updated_by = kullanici
    personel.save(update_fields=["foto", "updated_by", "updated_at"])
    return personel


def foto_kaldir(personel: Personel, kullanici=None) -> Personel:
    if not personel.foto:
        return personel
    personel.foto = ""
    personel.updated_by = kullanici
    personel.save(update_fields=["foto", "updated_by", "updated_at"])
    return personel


# === Süresi dolacaklar ===

_SINIRSIZ = object()


def _en_gec_belgeler(satirlar):
    """{(personel_id, tur): en geç bitişli belge | _SINIRSIZ}. Bitişi olmayan kayıt grubu
    süresiz yapar (yenilenen belge eskiyi susturur, süresiz kayıt hepsini)."""
    en_gec = {}
    for b in satirlar:
        k = (b.personel_id, b.tur)
        if b.bitis_tarihi is None:
            en_gec[k] = _SINIRSIZ
            continue
        m = en_gec.get(k)
        if m is _SINIRSIZ:
            continue
        if m is None or (b.bitis_tarihi, b.pk) > (m.bitis_tarihi, m.pk):
            en_gec[k] = b
    return en_gec


@dataclass(frozen=True)
class BelgeUyari:
    belge: PersonelBelge
    kalan_gun: int                  # negatif => süresi dolmuş

    @property
    def doldu(self) -> bool:
        return self.kalan_gun < 0


def _calisan_q(bugun):
    return (Q(personel__isten_cikis_tarihi__isnull=True)
            | Q(personel__isten_cikis_tarihi__gte=bugun))


def suresi_dolacaklar(*, gun=30, bugun=None):
    """Süresi dolmuş (her zaman) + `gun` gün içinde dolacak belgeler, bitişe göre artan.
    Ayrılmış personel uyarı vermez."""
    bugun = bugun or tr_bugun()
    sinir = bugun + timedelta(days=gun)
    satirlar = list(aktif_belgeler().filter(_calisan_q(bugun)))
    en_gec = _en_gec_belgeler(satirlar)
    uyarilar = [BelgeUyari(b, (b.bitis_tarihi - bugun).days)
                for b in en_gec.values()
                if b is not _SINIRSIZ and b.bitis_tarihi <= sinir]
    return sorted(uyarilar, key=lambda u: (u.belge.bitis_tarihi, u.belge.pk))


def durum_haritasi(belgeler, *, bugun=None, yaklasan_gun=30):
    """{belge.pk: (durum, kalan_gun)} — durum: SURESIZ / GECERLI / YAKLASIYOR / SURESI_DOLDU /
    ESKI (aynı türde daha geç bitişli bir kayıt var ya da grup süresiz). Gruplama doğru olsun
    diye `belgeler`, ilgili kişilerin TÜM aktif belgelerini içermelidir."""
    bugun = bugun or tr_bugun()
    belgeler = list(belgeler)
    en_gec = _en_gec_belgeler(belgeler)
    sonuc = {}
    for b in belgeler:
        if b.bitis_tarihi is None:
            sonuc[b.pk] = ("SURESIZ", None)
            continue
        kalan = (b.bitis_tarihi - bugun).days
        if en_gec.get((b.personel_id, b.tur)) is not b:
            sonuc[b.pk] = ("ESKI", kalan)
        elif kalan < 0:
            sonuc[b.pk] = ("SURESI_DOLDU", kalan)
        elif kalan <= yaklasan_gun:
            sonuc[b.pk] = ("YAKLASIYOR", kalan)
        else:
            sonuc[b.pk] = ("GECERLI", kalan)
    return sonuc


def belge_durumlari(personel: Personel, *, bugun=None, yaklasan_gun=30):
    """Personel kartındaki belge tablosu için: [(belge, durum, kalan_gun)], yeni yüklenen önce."""
    belgeler = list(PersonelBelge.objects.filter(personel=personel, silindi=False)
                    .order_by("-created_at", "-id"))
    haritasi = durum_haritasi(belgeler, bugun=bugun, yaklasan_gun=yaklasan_gun)
    return [(b, *haritasi[b.pk]) for b in belgeler]
