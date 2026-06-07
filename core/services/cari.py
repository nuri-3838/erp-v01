"""Cari (CARİLER) servis katmanı — cari kartı CRUD + otomatik kod.

- Kod: kategori varsa ``kod_yolu-NNNN`` (örn. 320-10-0001), yoksa ``CAR-NNNN``. Taşımada
  eski kod korunur (kod parametresi verilir).
- UPPER alanlar (unvan/kısa ad/vergi dairesi/adres/sevk adres) TR büyük harfe çevrilir.
- VKN/TCKN ve Tax ID dolu ise silinmemişler arası benzersiz.
- ``sevk_farkli`` değilse sevk alanları temizlenir.
- Silme: soft-delete.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Cari, CariKategori, Sehir, Ulke


class CariHatasi(ValueError):
    """Cari kural ihlali (Türkçe mesaj)."""


def aktif_cariler():
    return (Cari.objects.filter(silindi=False)
            .select_related("kategori", "ulke", "sehir").order_by("unvan"))


def sonraki_cari_kodu(kategori) -> str:
    if kategori is not None and kategori.kod_yolu:
        on = kategori.kod_yolu + "-"
        son = 0
        for k in Cari.objects.filter(kategori=kategori).values_list("kod", flat=True):
            p = k.rsplit("-", 1)[-1]
            if p.isdigit():
                son = max(son, int(p))
        return f"{on}{str(son + 1).zfill(4)}"
    n = 1
    while Cari.objects.filter(kod=f"CAR-{str(n).zfill(4)}").exists():
        n += 1
    return f"CAR-{str(n).zfill(4)}"


def _ulke(ulke_id):
    if not ulke_id:
        return None
    u = Ulke.objects.filter(pk=ulke_id, silindi=False).first()
    if u is None:
        raise CariHatasi("Ülke bulunamadı.")
    return u


def _sehir(sehir_id):
    if not sehir_id:
        return None
    s = Sehir.objects.filter(pk=sehir_id, silindi=False).first()
    if s is None:
        raise CariHatasi("Şehir bulunamadı.")
    return s


def _vergi_benzersiz(vkn_tckn, tax_id, *, haric_pk=None):
    for alan, deger, etiket in (("vkn_tckn", vkn_tckn, "VKN/TCKN"),
                                ("tax_id", tax_id, "Tax ID")):
        d = (deger or "").strip()
        if not d:
            continue
        qs = Cari.objects.filter(silindi=False, **{alan: d})
        if haric_pk is not None:
            qs = qs.exclude(pk=haric_pk)
        if qs.exists():
            raise CariHatasi(f"Bu {etiket} zaten kayıtlı: {d}")


def _para_dogrula(deger, etiket):
    try:
        d = Decimal(deger if deger not in (None, "") else 0)
    except Exception:
        raise CariHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise CariHatasi(f"{etiket} negatif olamaz.")
    return d


def _alanlar(*, kisa_ad, vergi_dairesi, vkn_tckn, tax_id, telefon, telefon_2,
            eposta, web, kep_adresi, adres, posta_kodu, sevk_farkli, sevk_ulke_id,
            sevk_sehir_id, sevk_adres, sevk_posta_kodu, para_birimi, kredi_limiti,
            iskonto_yuzdesi, notlar, ulke, sehir):
    """Ortak alan hazırlığı (create/update paylaşır). dict döner."""
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    if not sevk_farkli:
        sevk_ulke = sevk_sehir = None
        sevk_adres = ""
        sevk_posta_kodu = ""
    else:
        sevk_ulke = _ulke(sevk_ulke_id)
        sevk_sehir = _sehir(sevk_sehir_id)
    return dict(
        kisa_ad=buyuk_harf_tr((kisa_ad or "").strip()),
        vergi_dairesi=buyuk_harf_tr((vergi_dairesi or "").strip()),
        vkn_tckn=(vkn_tckn or "").strip(), tax_id=(tax_id or "").strip(),
        telefon=(telefon or "").strip(), telefon_2=(telefon_2 or "").strip(),
        eposta=(eposta or "").strip().lower(), web=(web or "").strip(),
        kep_adresi=(kep_adresi or "").strip(),
        ulke=ulke, sehir=sehir, adres=buyuk_harf_tr((adres or "").strip()),
        posta_kodu=(posta_kodu or "").strip(),
        sevk_farkli=bool(sevk_farkli), sevk_ulke=sevk_ulke, sevk_sehir=sevk_sehir,
        sevk_adres=buyuk_harf_tr((sevk_adres or "").strip()),
        sevk_posta_kodu=(sevk_posta_kodu or "").strip(),
        para_birimi=para_birimi,
        kredi_limiti=_para_dogrula(kredi_limiti, "Kredi limiti"),
        iskonto_yuzdesi=_para_dogrula(iskonto_yuzdesi, "İskonto"),
        notlar=(notlar or "").strip(),
    )


def cari_olustur(*, unvan, kategori_id=None, kod=None, kullanici=None, **kw) -> Cari:
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise CariHatasi("Unvan boş olamaz.")
    kategori = None
    if kategori_id:
        kategori = CariKategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise CariHatasi("Kategori bulunamadı.")
    _vergi_benzersiz(kw.get("vkn_tckn"), kw.get("tax_id"))
    veri = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_2", "eposta", "web", "kep_adresi", "adres",
                        "posta_kodu", "sevk_farkli", "sevk_ulke_id", "sevk_sehir_id",
                        "sevk_adres", "sevk_posta_kodu", "para_birimi", "kredi_limiti",
                        "iskonto_yuzdesi", "notlar")})
    kod = (kod or "").strip() or sonraki_cari_kodu(kategori)
    if Cari.objects.filter(silindi=False, kod=kod).exists():
        raise CariHatasi(f"Cari kodu zaten kayıtlı: {kod}")
    return Cari.objects.create(kod=kod, unvan=unvan, kategori=kategori,
                               created_by=kullanici, updated_by=kullanici, **veri)


def cari_guncelle(cari: Cari, *, unvan, kategori_id=None, kullanici=None, **kw) -> Cari:
    """Kod DEĞİŞMEZ. Kategori değişebilir (kod yine sabit kalır)."""
    if cari.silindi:
        raise CariHatasi("Silinmiş cari düzenlenemez.")
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise CariHatasi("Unvan boş olamaz.")
    kategori = None
    if kategori_id:
        kategori = CariKategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise CariHatasi("Kategori bulunamadı.")
    _vergi_benzersiz(kw.get("vkn_tckn"), kw.get("tax_id"), haric_pk=cari.pk)
    veri = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_2", "eposta", "web", "kep_adresi", "adres",
                        "posta_kodu", "sevk_farkli", "sevk_ulke_id", "sevk_sehir_id",
                        "sevk_adres", "sevk_posta_kodu", "para_birimi", "kredi_limiti",
                        "iskonto_yuzdesi", "notlar")})
    cari.unvan = unvan
    cari.kategori = kategori
    for alan, deger in veri.items():
        setattr(cari, alan, deger)
    cari.updated_by = kullanici
    cari.save()
    return cari


def cari_sil(cari: Cari, kullanici=None) -> Cari:
    if cari.silindi:
        return cari
    cari.silindi = True
    cari.silindi_at = timezone.now()
    cari.updated_by = kullanici
    cari.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return cari
