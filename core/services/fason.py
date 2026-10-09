"""FASON servis katmanı: fason fiyat listesi (FasonFiyat) ve Kesim Listesi hesabı (Operasyon Tanımları'ndan, İstasyon 10 · Boru Lazer).
Eski Kesim Tanımları / Kesim Kayıtları (FasonKesim*) ve Stok.kesildigi_profil kaldırıldı — liste yalnız operasyon zincirinden hesaplanır."""
from decimal import Decimal

from django.utils import timezone

from core.models import Cari, FasonFiyat, Stok
from core.sayi import SayiHatasi, parse_tr


class FasonHatasi(Exception):
    pass


# === Fason fiyat listesi ======================================================================================

def aktif_fiyatlar():
    return (FasonFiyat.objects.filter(silindi=False).select_related("cari", "stok")
            .order_by("cari__unvan", "stok__kod", "-gecerlilik_baslangic"))


def gecerli_fiyat(cari, stok, tarih):
    """``tarih``te geçerli fason fiyatı: aynı cari + stok için başlangıcı ``tarih``i GEÇMEYEN EN SON aktif satır; yoksa None.
    (cari/stok nesne ya da pk olabilir.)"""
    cari_id = getattr(cari, "pk", cari)
    stok_id = getattr(stok, "pk", stok)
    return (FasonFiyat.objects.filter(silindi=False, aktif=True, cari_id=cari_id, stok_id=stok_id,
                                      gecerlilik_baslangic__lte=tarih)
            .order_by("-gecerlilik_baslangic", "-pk").first())


def _fiyat_dogrula(cari_id, stok_id, birim_fiyat, para_birimi):
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if not cari:
        raise FasonHatasi("Fasoncu (cari) bulunamadı.")
    stok = Stok.objects.filter(pk=stok_id, silindi=False, uretim_urunu=True).first()
    if not stok:
        raise FasonHatasi("Kesilmiş parça bulunamadı (üretim ürünü olmalı).")
    try:
        fiyat = birim_fiyat if hasattr(birim_fiyat, "as_tuple") else parse_tr(birim_fiyat)
    except SayiHatasi:
        raise FasonHatasi("Birim fiyat geçerli bir sayı olmalı.")
    if fiyat < 0:
        raise FasonHatasi("Birim fiyat negatif olamaz.")
    if para_birimi not in dict(FasonFiyat._meta.get_field("para_birimi").choices):
        raise FasonHatasi("Para birimi geçersiz.")
    return cari, stok, fiyat


def fiyat_olustur(*, cari_id, stok_id, birim_fiyat, para_birimi="TRY", gecerlilik_baslangic, fasoncu_kodu="", aktif=True,
                  kullanici=None) -> FasonFiyat:
    cari, stok, fiyat = _fiyat_dogrula(cari_id, stok_id, birim_fiyat, para_birimi)
    if FasonFiyat.objects.filter(silindi=False, cari=cari, stok=stok, gecerlilik_baslangic=gecerlilik_baslangic).exists():
        raise FasonHatasi("Bu fasoncu + parça için aynı başlangıç tarihli bir fiyat zaten var.")
    return FasonFiyat.objects.create(
        cari=cari, stok=stok, birim_fiyat=fiyat, para_birimi=para_birimi, gecerlilik_baslangic=gecerlilik_baslangic,
        fasoncu_kodu=(fasoncu_kodu or "").strip(), aktif=bool(aktif), created_by=kullanici, updated_by=kullanici)


def fiyat_guncelle(f: FasonFiyat, *, cari_id, stok_id, birim_fiyat, para_birimi="TRY", gecerlilik_baslangic, fasoncu_kodu="",
                   aktif=True, kullanici=None) -> FasonFiyat:
    if f.silindi:
        raise FasonHatasi("Silinmiş kayıt düzenlenemez.")
    cari, stok, fiyat = _fiyat_dogrula(cari_id, stok_id, birim_fiyat, para_birimi)
    if (FasonFiyat.objects.filter(silindi=False, cari=cari, stok=stok, gecerlilik_baslangic=gecerlilik_baslangic)
            .exclude(pk=f.pk).exists()):
        raise FasonHatasi("Bu fasoncu + parça için aynı başlangıç tarihli bir fiyat zaten var.")
    f.cari, f.stok, f.birim_fiyat, f.para_birimi = cari, stok, fiyat, para_birimi
    f.gecerlilik_baslangic, f.fasoncu_kodu, f.aktif = gecerlilik_baslangic, (fasoncu_kodu or "").strip(), bool(aktif)
    f.updated_by = kullanici
    f.save()
    return f


def fiyat_sil(f: FasonFiyat, kullanici=None) -> FasonFiyat:
    if f.silindi:
        return f
    f.silindi = True
    f.silindi_at = timezone.now()
    f.updated_by = kullanici
    f.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return f


# === Kesim listesi: OPERASYON TANIMLARINDAN (İstasyon 10 — Boru Lazer) ================================================
# Fasoncuya gidecek liste artık Kesim Tanımları'ndan değil Operasyon Tanımları'ndan hesaplanır: bitmiş ürün zinciri ``ihtiyac_hesapla`` ile (TAM BOY
# kuralı, ortak parçalar toplanmış talep üzerinden tek yuvarlama) çözülür, İŞ İSTASYONU 10 (Boru Lazer) operasyonları fasonda kesilen işlerdir:
# girdi ham profil (boy adedi), ana çıktı + yan çıktılar kesilmiş parçalardır. Fasoncu seçilirse her parçaya kendi parça kodu ve geçerli birim fiyat eklenir.

FASON_ISTASYON_KODU = "10"


def fason_listesi_operasyondan(kalemler, cari=None, tarih=None) -> dict:
    """``kalemler``: [(urun, miktar), ...]. Döner: {"ozet": [satır], "detay": [...], "toplam_tutar": {para birimi: tutar}, "cari": cari, "fiyat_eksikleri": [...]}.
    Özet satırı: {"profil", "boy" (ham profil adedi), "kesilmis_parca", "toplam_adet", "yan" (yan çıktı mı), "fasoncu_kodu", "birim_fiyat", "para_birimi",
    "tutar"} — operasyon başına bir ana satır + yan çıktılar. Fasoncu yoksa ya da fiyatı tanımsızsa fiyat alanları None."""
    from core.services.uretim import UretimHatasi, ihtiyac_hesapla, operasyon_girdileri
    hedefler = [(u, Decimal(int(m))) for u, m in kalemler if m and int(m) > 0]
    tarih = tarih or timezone.localdate()
    ozet, eksik, toplam = [], [], {}
    if not hedefler:
        return {"ozet": [], "detay": [], "toplam_tutar": {}, "cari": cari, "fiyat_eksikleri": []}
    sonuc = ihtiyac_hesapla(hedefler)
    siralama = {}
    for p in sonuc["plan"]:
        op = p["operasyon"]
        if op.istasyon.kod != FASON_ISTASYON_KODU:
            continue
        girdi = next(iter(operasyon_girdileri(op)), None)
        profil = girdi.girdi if girdi else None
        boy = (p["calistirma"] * girdi.miktar) if girdi else None
        satirlar = [(p["stok"], p["uretilecek"], False)] + [(y["stok"], y["miktar"], True) for y in p["yan_ciktilar"]]
        for i, (parca, adet, yan) in enumerate(satirlar):
            fiyat = gecerli_fiyat(cari, parca, tarih) if cari else None
            tutar = (fiyat.birim_fiyat * adet) if fiyat else None
            if cari and fiyat is None:
                eksik.append(parca)
            if tutar is not None:
                toplam[fiyat.para_birimi] = toplam.get(fiyat.para_birimi, Decimal("0")) + tutar
            ozet.append({"op": p["stok"].kod, "sira_no": i, "profil": profil, "boy": boy if i == 0 else None, "kesilmis_parca": parca, "toplam_adet": adet, "yan": yan,
                         "fasoncu_kodu": fiyat.fasoncu_kodu if fiyat else "", "birim_fiyat": fiyat.birim_fiyat if fiyat else None,
                         "para_birimi": fiyat.para_birimi if fiyat else "", "tutar": tutar})
    ozet.sort(key=lambda r: ((r["profil"].kod if r["profil"] else ""), r["op"], r["sira_no"]))      # profil → operasyon → ana satır, sonra yan çıktılar
    return {"ozet": ozet, "detay": sonuc["plan"], "toplam_tutar": toplam, "cari": cari, "fiyat_eksikleri": eksik}
