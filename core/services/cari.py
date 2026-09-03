"""Cari (CARİLER) servis katmanı — cari kartı CRUD + otomatik kod.

- Kod: kategori varsa ``kod_yolu-NNNN`` (örn. 320-10-0001), yoksa ``CAR-NNNN``. Taşımada
  eski kod korunur (kod parametresi verilir).
- UPPER alanlar (unvan/kısa ad/vergi dairesi/adres) TR büyük harfe çevrilir.
- VKN/TCKN ve Tax ID dolu ise silinmemişler arası benzersiz.
- Sevk (teslimat) adresleri artık ayrı ``CariSevkAdresi`` tablosunda (çoklu, banka
  hesapları/yetkili kişilerle aynı desen).
- Silme: soft-delete.
"""
from __future__ import annotations

import os

from django.db.models import Prefetch
from django.utils import timezone

from core import gorsel
from core.metin import buyuk_harf_tr
from core.models import (
    Cari, CariAktivite, CariAktiviteEk, CariBanka, CariKategori, CariSevkAdresi, CariYetkili,
    HesapPlani, Sehir,
    Ulke,
)
from core.sayi import SayiHatasi, parse_tr
from core.services import hesap_plani as hp

AKTIVITE_IZINLI_UZANTI = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf"}
AKTIVITE_MAKS_BOYUT = 10 * 1024 * 1024   # 10 MB


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
        d = parse_tr(deger if deger not in (None, "") else 0)
    except SayiHatasi:
        raise CariHatasi(f"{etiket} geçerli bir sayı olmalı.")
    if d < 0:
        raise CariHatasi(f"{etiket} negatif olamaz.")
    return d


def _alanlar(*, kisa_ad, vergi_dairesi, vkn_tckn, tax_id, telefon, telefon_2,
            eposta, web, kep_adresi, adres, posta_kodu, para_birimi, kredi_limiti,
            iskonto_yuzdesi, notlar, ulke, sehir):
    """Ortak alan hazırlığı (create/update paylaşır). dict döner."""
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    return dict(
        kisa_ad=buyuk_harf_tr((kisa_ad or "").strip()),
        vergi_dairesi=buyuk_harf_tr((vergi_dairesi or "").strip()),
        vkn_tckn=(vkn_tckn or "").strip(), tax_id=(tax_id or "").strip(),
        telefon=(telefon or "").strip(), telefon_2=(telefon_2 or "").strip(),
        eposta=(eposta or "").strip().lower(), web=(web or "").strip(),
        kep_adresi=(kep_adresi or "").strip(),
        ulke=ulke, sehir=sehir, adres=buyuk_harf_tr((adres or "").strip()),
        posta_kodu=(posta_kodu or "").strip(),
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
        if kategori.ust_id is None:
            raise CariHatasi("Cari yalnız alt kategoriye bağlanabilir; üst kategori seçilemez.")
    _vergi_benzersiz(kw.get("vkn_tckn"), kw.get("tax_id"))
    veri = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_2", "eposta", "web", "kep_adresi", "adres",
                        "posta_kodu", "para_birimi", "kredi_limiti",
                        "iskonto_yuzdesi", "notlar")})
    kod = (kod or "").strip() or sonraki_cari_kodu(kategori)
    if Cari.objects.filter(silindi=False, kod=kod).exists():
        raise CariHatasi(f"Cari kodu zaten kayıtlı: {kod}")
    cari = Cari.objects.create(kod=kod, unvan=unvan, kategori=kategori,
                               created_by=kullanici, updated_by=kullanici, **veri)
    muh = muhasebe_hesabi_ac(cari, kullanici=kullanici)   # hesap planında otomatik aç
    if muh and cari.muhasebe_kodu != muh:
        cari.muhasebe_kodu = muh
        cari.save(update_fields=["muhasebe_kodu"])
    return cari


def muhasebe_hesabi_ac(cari: Cari, kullanici=None) -> str:
    """Cari kodundan muhasebe hesabını (eksik ara hesaplarla birlikte) hesap planında
    açar ve noktalı muhasebe kodunu döndürür. ÜST grup/kalem/parasal üstten miras alınır.
    Ara hesap adı = cari kategorisi, yaprak hesap adı = cari unvanı. Best-effort: kök yoksa
    ya da kod rakamsal değilse (CAR-...) boş döner. İdempotent (var olanı yeniden açmaz)."""
    kod = (cari.kod or "").strip()
    if not kod:
        return ""
    seg = kod.replace("-", ".").split(".")
    if len(seg) < 2 or not all(s.isdigit() for s in seg):
        return ""   # ör. CAR-0001 -> hesap planı kod kuralına uymaz
    noktali = ".".join(seg)
    if not HesapPlani.objects.filter(hesap_kodu=seg[0], silindi=False).exists():
        return ""   # kök hesap (320 vb.) yoksa açma
    for i in range(1, len(seg)):
        prefix = ".".join(seg[:i + 1])
        ust = ".".join(seg[:i])
        if HesapPlani.objects.filter(hesap_kodu=prefix, silindi=False).exists():
            continue
        leaf = (i == len(seg) - 1)
        ad = cari.unvan if leaf else (cari.kategori.ad if cari.kategori_id else prefix)
        try:
            hp.hesap_olustur(kod=prefix, ad=ad, ust_kodu=ust, kullanici=kullanici)
        except hp.HesapHatasi:
            return ""
    return noktali


def cari_guncelle(cari: Cari, *, unvan, kategori_id=None, kullanici=None, **kw) -> Cari:
    """Kod DEĞİŞMEZ. Kategori değişebilir (kod yine sabit kalır)."""
    if cari.silindi:
        raise CariHatasi("Silinmiş cari düzenlenemez.")
    unvan = buyuk_harf_tr((unvan or "").strip())
    if not unvan:
        raise CariHatasi("Unvan boş olamaz.")
    eski_unvan = cari.unvan
    kategori = None
    if kategori_id:
        kategori = CariKategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise CariHatasi("Kategori bulunamadı.")
        if kategori.ust_id is None:
            raise CariHatasi("Cari yalnız alt kategoriye bağlanabilir; üst kategori seçilemez.")
    _vergi_benzersiz(kw.get("vkn_tckn"), kw.get("tax_id"), haric_pk=cari.pk)
    veri = _alanlar(ulke=_ulke(kw.get("ulke_id")), sehir=_sehir(kw.get("sehir_id")),
                    **{k: kw.get(k) for k in (
                        "kisa_ad", "vergi_dairesi", "vkn_tckn", "tax_id", "telefon",
                        "telefon_2", "eposta", "web", "kep_adresi", "adres",
                        "posta_kodu", "para_birimi", "kredi_limiti",
                        "iskonto_yuzdesi", "notlar")})
    cari.unvan = unvan
    cari.kategori = kategori
    for alan, deger in veri.items():
        setattr(cari, alan, deger)
    cari.updated_by = kullanici
    cari.save()
    # Muhasebe hesabının (yaprak) adını cari unvanıyla senkron tut.
    if cari.muhasebe_kodu and unvan != eski_unvan:
        try:
            hp.hesap_adi_guncelle(kod=cari.muhasebe_kodu, yeni_ad=unvan,
                                  kullanici=kullanici)
        except hp.HesapHatasi:
            pass   # hesap silinmiş/bulunamamış olabilir — cari yine güncellenir
    return cari


def cari_sil(cari: Cari, kullanici=None) -> Cari:
    if cari.silindi:
        return cari
    if cari.tedarik_stoklari.filter(silindi=False).exists():
        raise CariHatasi(
            "Bu cari stoklarda tedarikçi olarak kullanılıyor; önce ilgili stoklardan kaldırın.")
    cari.silindi = True
    cari.silindi_at = timezone.now()
    cari.updated_by = kullanici
    cari.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    # Hareketsizse cari'nin yaprak muhasebe hesabını da gizle. Yevmiye satırı
    # varsa hesap_sil zaten reddeder (hesap korunur) — ara hesaba dokunulmaz.
    if cari.muhasebe_kodu:
        try:
            hp.hesap_sil(kod=cari.muhasebe_kodu, kullanici=kullanici)
        except hp.HesapHatasi:
            pass
    return cari


# --- Banka hesapları ---------------------------------------------------------
def aktif_bankalar(cari):
    return cari.banka_hesaplari.filter(silindi=False).order_by("-varsayilan", "banka_adi")


def _diger_varsayilanlari_kapat(cari, haric):
    cari.banka_hesaplari.filter(silindi=False, varsayilan=True).exclude(
        pk=haric.pk).update(varsayilan=False)


def banka_ekle(cari, *, banka_adi, hesap_sahibi="", iban="", swift="",
               para_birimi="TRY", aciklama="", varsayilan=False, kullanici=None) -> CariBanka:
    banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    if not banka_adi:
        raise CariHatasi("Banka adı boş olamaz.")
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    ilk = not cari.banka_hesaplari.filter(silindi=False).exists()
    b = CariBanka.objects.create(
        cari=cari, banka_adi=banka_adi,
        hesap_sahibi=buyuk_harf_tr((hesap_sahibi or "").strip()),
        iban=(iban or "").strip().upper().replace(" ", ""),
        swift=(swift or "").strip().upper(),
        para_birimi=para_birimi, aciklama=(aciklama or "").strip(),
        varsayilan=bool(varsayilan) or ilk,
        created_by=kullanici, updated_by=kullanici)
    if b.varsayilan:
        _diger_varsayilanlari_kapat(cari, b)
    return b


def banka_guncelle(banka: CariBanka, *, banka_adi, hesap_sahibi="", iban="", swift="",
                   para_birimi="TRY", aciklama="", varsayilan=False, kullanici=None) -> CariBanka:
    if banka.silindi:
        raise CariHatasi("Silinmiş banka hesabı düzenlenemez.")
    banka_adi = buyuk_harf_tr((banka_adi or "").strip())
    if not banka_adi:
        raise CariHatasi("Banka adı boş olamaz.")
    if para_birimi not in dict(Cari.PARA_CHOICES):
        raise CariHatasi("Geçersiz para birimi.")
    idi_varsayilan = banka.varsayilan
    banka.banka_adi = banka_adi
    banka.hesap_sahibi = buyuk_harf_tr((hesap_sahibi or "").strip())
    banka.iban = (iban or "").strip().upper().replace(" ", "")
    banka.swift = (swift or "").strip().upper()
    banka.para_birimi = para_birimi
    banka.aciklama = (aciklama or "").strip()
    banka.varsayilan = bool(varsayilan)
    banka.updated_by = kullanici
    banka.save()
    if banka.varsayilan:
        _diger_varsayilanlari_kapat(banka.cari, banka)
    elif idi_varsayilan and not banka.cari.banka_hesaplari.filter(
            silindi=False, varsayilan=True).exists():
        # Tek/son varsayılan hesabın işareti kaldırıldı -> kalan ilk hesabı varsayılan yap
        # (banka_sil'deki "her zaman bir varsayılan olsun" invaryantıyla aynı desen).
        kalan = banka.cari.banka_hesaplari.filter(silindi=False).exclude(
            pk=banka.pk).order_by("banka_adi").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return banka


def banka_sil(banka: CariBanka, kullanici=None) -> CariBanka:
    if banka.silindi:
        return banka
    cari = banka.cari
    idi_varsayilan = banka.varsayilan
    banka.silindi = True
    banka.silindi_at = timezone.now()
    banka.varsayilan = False
    banka.updated_by = kullanici
    banka.save(update_fields=["silindi", "silindi_at", "varsayilan",
                              "updated_by", "updated_at"])
    if idi_varsayilan:   # kalan ilk hesabı varsayılan yap
        kalan = cari.banka_hesaplari.filter(silindi=False).order_by("banka_adi").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return banka


# --- Sevk (teslimat) adresleri -----------------------------------------------
def aktif_sevk_adresleri(cari):
    return cari.sevk_adresleri.filter(silindi=False).order_by("-varsayilan", "ad")


def _diger_sevk_varsayilanlari_kapat(cari, haric):
    cari.sevk_adresleri.filter(silindi=False, varsayilan=True).exclude(
        pk=haric.pk).update(varsayilan=False)


def sevk_adresi_ekle(cari, *, ad, ulke_id=None, sehir_id=None, adres="",
                     varsayilan=False, kullanici=None) -> CariSevkAdresi:
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise CariHatasi("Adres adı boş olamaz.")
    ilk = not cari.sevk_adresleri.filter(silindi=False).exists()
    s = CariSevkAdresi.objects.create(
        cari=cari, ad=ad, ulke=_ulke(ulke_id), sehir=_sehir(sehir_id),
        adres=buyuk_harf_tr((adres or "").strip()),
        varsayilan=bool(varsayilan) or ilk,
        created_by=kullanici, updated_by=kullanici)
    if s.varsayilan:
        _diger_sevk_varsayilanlari_kapat(cari, s)
    return s


def sevk_adresi_guncelle(sevk: CariSevkAdresi, *, ad, ulke_id=None, sehir_id=None, adres="",
                         varsayilan=False, kullanici=None) -> CariSevkAdresi:
    if sevk.silindi:
        raise CariHatasi("Silinmiş sevk adresi düzenlenemez.")
    ad = buyuk_harf_tr((ad or "").strip())
    if not ad:
        raise CariHatasi("Adres adı boş olamaz.")
    idi_varsayilan = sevk.varsayilan
    sevk.ad = ad
    sevk.ulke = _ulke(ulke_id)
    sevk.sehir = _sehir(sehir_id)
    sevk.adres = buyuk_harf_tr((adres or "").strip())
    sevk.varsayilan = bool(varsayilan)
    sevk.updated_by = kullanici
    sevk.save()
    if sevk.varsayilan:
        _diger_sevk_varsayilanlari_kapat(sevk.cari, sevk)
    elif idi_varsayilan and not sevk.cari.sevk_adresleri.filter(
            silindi=False, varsayilan=True).exists():
        # Tek/son varsayılan adresin işareti kaldırıldı -> kalan ilk adresi varsayılan yap
        # (banka_guncelle/banka_sil'deki "her zaman bir varsayılan olsun" invaryantıyla aynı desen).
        kalan = sevk.cari.sevk_adresleri.filter(silindi=False).exclude(
            pk=sevk.pk).order_by("ad").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return sevk


def sevk_adresi_sil(sevk: CariSevkAdresi, kullanici=None) -> CariSevkAdresi:
    if sevk.silindi:
        return sevk
    cari = sevk.cari
    idi_varsayilan = sevk.varsayilan
    sevk.silindi = True
    sevk.silindi_at = timezone.now()
    sevk.varsayilan = False
    sevk.updated_by = kullanici
    sevk.save(update_fields=["silindi", "silindi_at", "varsayilan",
                             "updated_by", "updated_at"])
    if idi_varsayilan:   # kalan ilk adresi varsayılan yap
        kalan = cari.sevk_adresleri.filter(silindi=False).order_by("ad").first()
        if kalan is not None:
            kalan.varsayilan = True
            kalan.save(update_fields=["varsayilan", "updated_at"])
    return sevk


# --- Yetkili kişiler ---------------------------------------------------------
def aktif_yetkililer(cari):
    return cari.yetkililer.filter(silindi=False).order_by("ad_soyad")


def yetkili_ekle(cari, *, ad_soyad, unvan="", telefon="", eposta="", notlar="",
                 kullanici=None) -> CariYetkili:
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise CariHatasi("Ad soyad boş olamaz.")
    return CariYetkili.objects.create(
        cari=cari, ad_soyad=ad_soyad, unvan=buyuk_harf_tr((unvan or "").strip()),
        telefon=(telefon or "").strip(), eposta=(eposta or "").strip().lower(),
        notlar=(notlar or "").strip(), created_by=kullanici, updated_by=kullanici)


def yetkili_guncelle(yetkili: CariYetkili, *, ad_soyad, unvan="", telefon="",
                     eposta="", notlar="", kullanici=None) -> CariYetkili:
    if yetkili.silindi:
        raise CariHatasi("Silinmiş yetkili düzenlenemez.")
    ad_soyad = buyuk_harf_tr((ad_soyad or "").strip())
    if not ad_soyad:
        raise CariHatasi("Ad soyad boş olamaz.")
    yetkili.ad_soyad = ad_soyad
    yetkili.unvan = buyuk_harf_tr((unvan or "").strip())
    yetkili.telefon = (telefon or "").strip()
    yetkili.eposta = (eposta or "").strip().lower()
    yetkili.notlar = (notlar or "").strip()
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["ad_soyad", "unvan", "telefon", "eposta", "notlar",
                                "updated_by", "updated_at"])
    return yetkili


def yetkili_sil(yetkili: CariYetkili, kullanici=None) -> CariYetkili:
    if yetkili.silindi:
        return yetkili
    yetkili.silindi = True
    yetkili.silindi_at = timezone.now()
    yetkili.updated_by = kullanici
    yetkili.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return yetkili


# --- Aktiviteler (görüşme/temas kayıtları) -----------------------------------
def aktif_aktiviteler(cari):
    return (cari.aktiviteler.filter(silindi=False)
            .select_related("created_by")
            .prefetch_related(Prefetch(
                "ekler", queryset=CariAktiviteEk.objects.filter(silindi=False)))
            .order_by("-tarih", "-id"))


def aktivite_ekle(cari, *, tarih, tur, aciklama, kullanici=None) -> CariAktivite:
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise CariHatasi("Açıklama boş olamaz.")
    if tur not in CariAktivite.Tur.values:
        raise CariHatasi("Geçersiz aktivite türü.")
    return CariAktivite.objects.create(
        cari=cari, tarih=tarih, tur=tur, aciklama=aciklama,
        created_by=kullanici, updated_by=kullanici)


def aktivite_guncelle(aktivite: CariAktivite, *, tarih, tur, aciklama,
                      kullanici=None) -> CariAktivite:
    if aktivite.silindi:
        raise CariHatasi("Silinmiş aktivite düzenlenemez.")
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise CariHatasi("Açıklama boş olamaz.")
    if tur not in CariAktivite.Tur.values:
        raise CariHatasi("Geçersiz aktivite türü.")
    aktivite.tarih = tarih
    aktivite.tur = tur
    aktivite.aciklama = aciklama
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["tarih", "tur", "aciklama", "updated_by", "updated_at"])
    return aktivite


def aktivite_sil(aktivite: CariAktivite, kullanici=None) -> CariAktivite:
    if aktivite.silindi:
        return aktivite
    aktivite.silindi = True
    aktivite.silindi_at = timezone.now()
    aktivite.updated_by = kullanici
    aktivite.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return aktivite


def aktivite_ek_ekle(aktivite: CariAktivite, *, dosya, kullanici=None) -> CariAktiviteEk:
    """Aktiviteye tek dosya ekler (çoklu yükleme view katmanında döngüyle bu fonksiyonu çağırır).
    Resim ise WebP'ye küçültülür (spec görsel invariant'ı: en uzun kenar ~1600px, ~%80 kalite);
    PDF olduğu gibi saklanır."""
    if aktivite.silindi:
        raise CariHatasi("Silinmiş aktiviteye dosya eklenemez.")
    ad = dosya.name or "dosya"
    uzanti = os.path.splitext(ad)[1].lower()
    if uzanti not in AKTIVITE_IZINLI_UZANTI:
        raise CariHatasi(f"Desteklenmeyen dosya türü: {ad} (yalnız resim veya PDF).")
    if dosya.size > AKTIVITE_MAKS_BOYUT:
        raise CariHatasi(f"Dosya çok büyük (10 MB üzeri): {ad}")
    if uzanti == ".pdf":
        saklanan = dosya
    else:
        try:
            saklanan = gorsel.kucult_webp(dosya, max_kenar=1600, kalite=80, ad="cari_aktivite")
        except Exception:
            raise CariHatasi(f"Geçersiz resim dosyası: {ad}")
    return CariAktiviteEk.objects.create(
        aktivite=aktivite, dosya=saklanan, orijinal_ad=ad,
        created_by=kullanici, updated_by=kullanici)


def aktivite_ek_sil(ek: CariAktiviteEk, kullanici=None) -> CariAktiviteEk:
    if ek.silindi:
        return ek
    ek.silindi = True
    ek.silindi_at = timezone.now()
    ek.updated_by = kullanici
    ek.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return ek
