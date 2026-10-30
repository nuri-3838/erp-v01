"""İNSAN KAYNAKLARI > İşe giriş: özlük kontrol listesi + işe giriş kaydı (personel kartı "İşe Giriş" sekmesi).

Yalnız KAYIT/TAKİP: resmî SGK bildirimini ERP yapmaz; muhasebeye bilgi paketi (Dilim 3) ve evrak basımı (Dilim 2) bu servisin
üstüne kurulur. Kontrol listesi KODDA tanımlıdır (``KONTROL_LISTESI``); mali müşavir/İSG uzmanı ile netleşince buradan düzeltilir.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction

from core.models import IseGirisKaydi, Personel, PersonelBelge
from core.services import personel_belge as belge_servis
from core.tarih import tr_bugun

T = PersonelBelge.Tur

KAYNAKLAR = {
    "IMZALI": "İşverence hazırlanır, personel imzalar",
    "PERSONEL": "Personelden alınır",
    "ISG": "İSG uzmanı / işyeri hekimi",
    "MUHASEBE": "Muhasebe / SGK",
}

# (kaynak, belge türü, görünen ad, zorunlu mu). Zorunlu = işe başlamadan/başlarken özlükte bulunması beklenen temel evrak.
KONTROL_LISTESI = (
    ("IMZALI", T.SOZLESME, "Belirsiz süreli iş sözleşmesi (imzalı)", True),
    ("IMZALI", T.KVKK, "KVKK aydınlatma + açık rıza (imzalı)", True),
    ("IMZALI", T.FAZLA_MESAI_ONAYI, "Fazla çalışma muvafakatnamesi (imzalı)", True),
    ("IMZALI", T.IC_DUZEN_TESLIM, "İç düzen yönetmeliği teslim tutanağı", False),
    ("IMZALI", T.ZIMMET, "Zimmet tutanağı", False),
    ("PERSONEL", T.KIMLIK, "Kimlik fotokopisi", True),
    ("PERSONEL", T.IKAMETGAH, "İkametgâh belgesi (e-Devlet)", True),
    ("PERSONEL", T.VESIKALIK, "Vesikalık fotoğraf", True),
    ("PERSONEL", T.ADLI_SICIL, "Adli sicil kaydı", True),
    ("PERSONEL", T.IBAN_BILDIRIMI, "IBAN bildirimi", True),
    ("PERSONEL", T.DIPLOMA, "Diploma / sertifika", False),
    ("PERSONEL", T.MYK_BELGESI, "Mesleki yeterlilik (MYK) belgesi — ilgili meslekte", False),
    ("PERSONEL", T.ASKERLIK, "Askerlik durum belgesi (erkek personel)", False),
    ("PERSONEL", T.EHLIYET, "Ehliyet (araç/forklift kullanacaksa)", False),
    ("PERSONEL", T.HIZMET_DOKUMU, "Önceki işyeri hizmet belgesi (yıllık izin kıdemi için)", False),
    ("PERSONEL", T.ENGELLI_RAPORU, "Engelli sağlık kurulu raporu (varsa)", False),
    ("PERSONEL", T.IS_BASVURU, "İş başvuru formu / CV", False),
    ("ISG", T.SAGLIK_RAPORU, "İşe giriş sağlık muayenesi / raporu", True),
    ("ISG", T.ISG_TUTANAK, "İSG işe giriş eğitimi tutanağı", True),
    ("ISG", T.KKD_TESLIM, "KKD teslim tutanağı", False),
    ("MUHASEBE", T.SGK_GIRIS, "SGK işe giriş bildirgesi (kopya)", True),
)

# Eski/benzer türler aynı satırı karşılar (ör. eskiden yüklenmiş "İSG Eğitim Belgesi" yeni "İSG tutanağı" satırını da doldurur).
_ESDEGER = {
    T.ISG_TUTANAK: (T.ISG_TUTANAK, T.ISG_EGITIMI),
    T.MYK_BELGESI: (T.MYK_BELGESI, T.OPERATOR_BELGESI),
}

DURUM_ADLARI = {"EKSIK": "Eksik", "YUKLU": "Yüklü", "YAKLASIYOR": "Süresi yaklaşıyor", "SURESI_DOLDU": "Süresi doldu"}


class IseGirisHatasi(ValueError):
    """İşe giriş kaydı kural ihlali (Türkçe mesaj)."""


def kontrol_listesi(personel: Personel, *, belgeler=None, bugun=None) -> dict:
    """Özlük kontrol listesi. ``belgeler`` = ``belge_servis.belge_durumlari(personel)`` çıktısı (verilirse sorgu yapılmaz).
    Döner: {"satirlar": [{tur, ad, kaynak, kaynak_ad, zorunlu, durum, belge, kalan}], "eksik_zorunlu": n, "eksik": n, "yuklu": n, "toplam": n}."""
    bugun = bugun or tr_bugun()
    if belgeler is None:
        belgeler = belge_servis.belge_durumlari(personel, bugun=bugun)
    satirlar = []
    for kaynak, tur, ad, zorunlu in KONTROL_LISTESI:
        turler = _ESDEGER.get(tur, (tur,))
        aday = [(b, d, k) for b, d, k in belgeler if b.tur in turler and d != "ESKI"]     # belgeler yeni → eski sıralı: ilk = en yeni
        if not aday:
            durum, belge, kalan = "EKSIK", None, None
        else:
            belge, d, kalan = aday[0]
            durum = d if d in ("SURESI_DOLDU", "YAKLASIYOR") else "YUKLU"
        satirlar.append({"tur": tur.value, "ad": ad, "kaynak": kaynak, "kaynak_ad": KAYNAKLAR[kaynak], "zorunlu": zorunlu,
                         "durum": durum, "durum_ad": DURUM_ADLARI[durum], "belge": belge, "kalan": kalan})
    eksik = [s for s in satirlar if s["durum"] == "EKSIK"]
    return {"satirlar": satirlar, "eksik_zorunlu": sum(1 for s in eksik if s["zorunlu"]), "eksik": len(eksik),
            "yuklu": len(satirlar) - len(eksik), "toplam": len(satirlar),
            "uyari": any(s["durum"] == "SURESI_DOLDU" for s in satirlar)}


def kayit_oku(personel: Personel) -> IseGirisKaydi:
    """Personelin işe giriş kaydı; yoksa KAYDEDİLMEMİŞ boş nesne döner (GET yazmaz)."""
    return IseGirisKaydi.objects.filter(personel=personel).first() or IseGirisKaydi(personel=personel)      # ilişki önbelleğine güvenilmez (bayat nesne)


def _ondalik(deger, ad):
    if deger in (None, ""):
        return None
    try:
        return Decimal(str(deger).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise IseGirisHatasi(f"{ad} sayı olmalı.")


@transaction.atomic
def kayit_kaydet(personel: Personel, *, deneme_suresi_ay=None, haftalik_calisma_saati=None, calisma_gunleri="", calisma_saatleri="",
                 ucret_odeme="", notlar="", kullanici=None) -> IseGirisKaydi:
    """Sözleşme parametrelerini (basımda yer tutucu olarak kullanılır) kaydeder. Evrak basım ve muhasebe gönderim alanlarına DOKUNMAZ."""
    if personel.silindi:
        raise IseGirisHatasi("Silinmiş personel için işe giriş kaydı tutulamaz.")
    if deneme_suresi_ay in (None, ""):
        deneme = None
    else:
        try:
            deneme = int(deneme_suresi_ay)
        except (TypeError, ValueError):
            raise IseGirisHatasi("Deneme süresi ay olarak sayı olmalı.")
        if not 0 <= deneme <= 4:
            raise IseGirisHatasi("Deneme süresi 0 ile 4 ay arasında olmalı (yasal üst sınır 2 ay; toplu iş sözleşmesiyle 4 ay).")
    saat = _ondalik(haftalik_calisma_saati, "Haftalık çalışma saati")
    if saat is not None and not 0 < saat <= 45:
        raise IseGirisHatasi("Haftalık çalışma saati 0'dan büyük ve en çok 45 olmalı (fazla çalışma ayrıca yazılır).")
    kayit = kayit_oku(personel)
    kayit.deneme_suresi_ay = deneme
    kayit.haftalik_calisma_saati = saat
    kayit.calisma_gunleri = " ".join((calisma_gunleri or "").split())
    kayit.calisma_saatleri = " ".join((calisma_saatleri or "").split())
    kayit.ucret_odeme = " ".join((ucret_odeme or "").split())
    kayit.notlar = (notlar or "").strip()
    kayit.updated_by = kullanici
    if kayit.pk is None:
        kayit.created_by = kullanici
        kayit.save()
    else:                                                               # yalnız parametre alanları: basım/muhasebe alanlarına dokunulmaz
        kayit.save(update_fields=["deneme_suresi_ay", "haftalik_calisma_saati", "calisma_gunleri", "calisma_saatleri", "ucret_odeme",
                                  "notlar", "updated_by", "updated_at"])
    return kayit
