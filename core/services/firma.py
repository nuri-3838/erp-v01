"""AYARLAR > Firma Bilgileri — tekil kayıt (CekHesapAyari ile aynı desen) + serbest
sayıda banka hesabı satırı (CariBanka ile aynı alan şekli, TR büyük harf burada)."""
from django.db import transaction
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import Cari, FirmaBanka, FirmaBilgisi


class FirmaHatasi(Exception):
    pass


def firma_bilgisi_getir() -> FirmaBilgisi:
    """Tekil ayar kaydı (yoksa oluşturur)."""
    return FirmaBilgisi.get()


def _metin(v):
    return (v or "").strip()


@transaction.atomic
def firma_bilgisi_kaydet(*, unvan="", vergi_dairesi="", vergi_no="", adres="", telefon="",
                         eposta="", web="", logo=None, bankalar=None, kullanici=None) -> FirmaBilgisi:
    """``bankalar``: [{"banka_adi", "sube", "hesap_sahibi", "iban", "para_birimi"}, ...].
    Var olan banka satırları soft-delete edilip liste sıfırdan yazılır — doğal bir
    upsert anahtarı yok (aynı bankada/para biriminde birden fazla hesap olabilir),
    bu yüzden StokFiyat'taki upsert yerine CariBanka'daki gibi düz alan ataması + burada
    tam yeniden yazım kullanılıyor."""
    firma = FirmaBilgisi.get()
    firma.unvan = buyuk_harf_tr(_metin(unvan))
    firma.vergi_dairesi = buyuk_harf_tr(_metin(vergi_dairesi))
    firma.vergi_no = _metin(vergi_no)
    firma.adres = buyuk_harf_tr(_metin(adres))
    firma.telefon = _metin(telefon)
    firma.eposta = _metin(eposta)
    firma.web = _metin(web)
    if logo is not None:                   # yalnız yeni dosya yüklendiyse değiştir
        firma.logo = logo
    firma.updated_by = kullanici
    firma.save()

    simdi = timezone.now()
    for b in firma.bankalar.filter(silindi=False):
        b.silindi = True
        b.silindi_at = simdi
        b.updated_by = kullanici
        b.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])

    for sira, b in enumerate(bankalar or []):
        banka_adi = buyuk_harf_tr(_metin(b.get("banka_adi")))
        if not banka_adi:
            continue
        para_birimi = b.get("para_birimi") or "TRY"
        if para_birimi not in dict(Cari.PARA_CHOICES):
            raise FirmaHatasi("Geçersiz para birimi.")
        FirmaBanka.objects.create(
            firma=firma, banka_adi=banka_adi, sube=buyuk_harf_tr(_metin(b.get("sube"))),
            hesap_sahibi=buyuk_harf_tr(_metin(b.get("hesap_sahibi"))),
            iban=_metin(b.get("iban")).upper().replace(" ", ""),
            para_birimi=para_birimi, sira=sira,
            created_by=kullanici, updated_by=kullanici)
    return firma
