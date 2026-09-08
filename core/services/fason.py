"""FASON > Kesim Tanımları + Kesim Listesi hesaplayıcı. Bir hammadde profilinden
(Stok, satinalma_urunu=True) 1 adet bitmiş ürün (Stok, satis_urunu=True) için kaç parça
kesilmesi gerektiğini tanımlar (KdvOrani ile aynı basit CRUD deseni), ve verilen bir
ürün×miktar listesinden fasoncuya gönderilecek toplam kesim listesini hesaplar."""
from core.metin import buyuk_harf_tr
from core.models import FasonKesim, Stok


class FasonHatasi(Exception):
    pass


def aktif_kesimler():
    return (FasonKesim.objects.filter(silindi=False)
            .select_related("profil").prefetch_related("urunler")
            .order_by("sira", "pk"))


def _profil_coz(profil_id):
    profil = Stok.objects.filter(
        pk=profil_id, silindi=False, satinalma_urunu=True).first()
    if not profil:
        raise FasonHatasi("Profil bulunamadı.")
    return profil


def _urunleri_coz(urun_idler):
    urunler = list(Stok.objects.filter(
        pk__in=(urun_idler or []), silindi=False, satis_urunu=True))
    if not urunler:
        raise FasonHatasi("En az bir ürün seçilmelidir.")
    return urunler


def kesim_olustur(*, profil_id, parca_adi, adet, urun_idler, sira=0, kullanici=None) -> FasonKesim:
    profil = _profil_coz(profil_id)
    parca_adi = buyuk_harf_tr((parca_adi or "").strip())
    if not parca_adi:
        raise FasonHatasi("Parça adı boş olamaz.")
    adet = int(adet or 0)
    if adet < 1:
        raise FasonHatasi("Adet en az 1 olmalı.")
    urunler = _urunleri_coz(urun_idler)
    k = FasonKesim.objects.create(
        profil=profil, parca_adi=parca_adi, adet=adet, sira=int(sira or 0),
        created_by=kullanici, updated_by=kullanici)
    k.urunler.set(urunler)
    return k


def kesim_guncelle(k: FasonKesim, *, profil_id, parca_adi, adet, urun_idler, sira=0,
                   kullanici=None) -> FasonKesim:
    if k.silindi:
        raise FasonHatasi("Silinmiş kayıt düzenlenemez.")
    profil = _profil_coz(profil_id)
    parca_adi = buyuk_harf_tr((parca_adi or "").strip())
    if not parca_adi:
        raise FasonHatasi("Parça adı boş olamaz.")
    adet = int(adet or 0)
    if adet < 1:
        raise FasonHatasi("Adet en az 1 olmalı.")
    urunler = _urunleri_coz(urun_idler)
    k.profil = profil
    k.parca_adi = parca_adi
    k.adet = adet
    k.sira = int(sira or 0)
    k.updated_by = kullanici
    k.save(update_fields=["profil", "parca_adi", "adet", "sira", "updated_by", "updated_at"])
    k.urunler.set(urunler)
    return k


def kesim_sil(k: FasonKesim, kullanici=None) -> FasonKesim:
    from django.utils import timezone
    if k.silindi:
        return k
    k.silindi = True
    k.silindi_at = timezone.now()
    k.updated_by = kullanici
    k.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return k


def fason_listesi_hesapla(kalemler):
    """``kalemler``: [(stok, miktar), ...] (satış ürünü + istenen adet).

    Döner: {"detay": [{"urun","miktar","kesim","toplam_adet"}, ...],
            "ozet":  [{"profil","parca_adi","toplam_adet"}, ...]}  — ``ozet`` profil+parça
    adına göre gruplu, fasoncuya gönderilecek asıl liste (birden fazla ürün aynı profili
    kullanıyorsa toplamları birleşir, ör. A21+A51 aynı arka-ayak satırını paylaşıyorsa)."""
    detay = []
    for stok, miktar in kalemler:
        miktar = int(miktar or 0)
        if miktar <= 0:
            continue
        kesimler = (FasonKesim.objects.filter(silindi=False, urunler=stok)
                   .select_related("profil").order_by("sira", "pk"))
        for k in kesimler:
            detay.append({"urun": stok, "miktar": miktar, "kesim": k,
                          "toplam_adet": k.adet * miktar})

    ozet_map = {}
    ozet_sira = {}
    for d in detay:
        k = d["kesim"]
        key = (k.profil_id, k.parca_adi)
        if key not in ozet_map:
            ozet_map[key] = {"profil": k.profil, "parca_adi": k.parca_adi, "toplam_adet": 0}
            ozet_sira[key] = k.sira
        ozet_map[key]["toplam_adet"] += d["toplam_adet"]
    ozet = [ozet_map[key] for key in
           sorted(ozet_map, key=lambda key: (ozet_sira[key], key))]
    return {"detay": detay, "ozet": ozet}
