"""FASON > Kesim Tanımları + Kesim Listesi hesaplayıcı — 2 katmanlı BOM:

  1) Stok.kesildigi_profil (1:1): bir "kesilmiş parça" (fasoncunun bir ham profilden
     kestiği ara ürün, ör. "KESİLMİŞ A TİPİ ÖN AYAK 2+1") hangi ham profilden (satinalma
     ürünü) kesiliyor — o parçanın kendi tanımının sabit bir özelliği.
  2) FasonKesim (bu dosya): bir bitmiş ürün (satis_urunu=True) 1 adet üretmek için hangi
     kesilmiş parça(lar)dan kaç adet gerektiği.

Fasoncuya gönderilecek liste iki katman birlikte hesaplanarak üretilir: bitmiş ürün →
kesilmiş parça (katman 2) → o parçanın ham profili (katman 1) → profil bazında toplam."""
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from core.models import FasonKesim, FasonKesimKaydi, FasonKesimKaydiKalemi, Stok


class FasonHatasi(Exception):
    pass


def aktif_kesimler():
    return (FasonKesim.objects.filter(silindi=False)
            .select_related("urun", "kesilmis_parca", "kesilmis_parca__kesildigi_profil")
            .order_by("sira", "pk"))


def kesilmis_parca_secenekleri():
    """Kesim Tanımları formunda seçilebilecek 'kesilmiş parça' adayları — kendi ham
    profiline (kesildigi_profil) bağlı Stok kartları (bu, bir kartın 'kesilmiş parça'
    olduğunun tanımıdır — ayrı bir bayrak yok)."""
    return (Stok.objects.filter(silindi=False, kesildigi_profil__isnull=False)
            .select_related("kesildigi_profil").order_by("kod"))


def _urun_coz(urun_id):
    urun = Stok.objects.filter(pk=urun_id, silindi=False, satis_urunu=True).first()
    if not urun:
        raise FasonHatasi("Ürün bulunamadı.")
    return urun


def _kesilmis_parca_coz(kesilmis_parca_id):
    parca = Stok.objects.filter(
        pk=kesilmis_parca_id, silindi=False, kesildigi_profil__isnull=False).first()
    if not parca:
        raise FasonHatasi(
            "Kesilmiş parça bulunamadı (önce ilgili stok kartına 'kesildiği ham profil' "
            "tanımlanmalı).")
    return parca


def kesim_olustur(*, urun_id, kesilmis_parca_id, adet, sira=0, kullanici=None) -> FasonKesim:
    urun = _urun_coz(urun_id)
    parca = _kesilmis_parca_coz(kesilmis_parca_id)
    adet = int(adet or 0)
    if adet < 1:
        raise FasonHatasi("Adet en az 1 olmalı.")
    if FasonKesim.objects.filter(silindi=False, urun=urun, kesilmis_parca=parca).exists():
        raise FasonHatasi("Bu ürün + kesilmiş parça kombinasyonu zaten tanımlı.")
    return FasonKesim.objects.create(
        urun=urun, kesilmis_parca=parca, adet=adet, sira=int(sira or 0),
        created_by=kullanici, updated_by=kullanici)


def kesim_guncelle(k: FasonKesim, *, urun_id, kesilmis_parca_id, adet, sira=0,
                   kullanici=None) -> FasonKesim:
    if k.silindi:
        raise FasonHatasi("Silinmiş kayıt düzenlenemez.")
    urun = _urun_coz(urun_id)
    parca = _kesilmis_parca_coz(kesilmis_parca_id)
    adet = int(adet or 0)
    if adet < 1:
        raise FasonHatasi("Adet en az 1 olmalı.")
    if (FasonKesim.objects.filter(silindi=False, urun=urun, kesilmis_parca=parca)
            .exclude(pk=k.pk).exists()):
        raise FasonHatasi("Bu ürün + kesilmiş parça kombinasyonu zaten tanımlı.")
    k.urun = urun
    k.kesilmis_parca = parca
    k.adet = adet
    k.sira = int(sira or 0)
    k.updated_by = kullanici
    k.save(update_fields=["urun", "kesilmis_parca", "adet", "sira", "updated_by", "updated_at"])
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
    """``kalemler``: [(urun, miktar), ...] (bitmiş ürün + istenen adet).

    Döner: {"detay": [{"urun","miktar","kesim","toplam_adet"}, ...],
            "ozet":  [{"profil","parca_kod_ad","toplam_adet"}, ...]}

    ``ozet`` ham profil + kesilmiş parça adına göre gruplu — fasoncuya gönderilecek asıl
    liste (birden fazla ürün aynı kesilmiş parçayı/profili paylaşıyorsa toplamları
    birleşir, örn. Çift Çıkışlı modellerin hepsi aynı ön-ayak ham profilini kullanıyor)."""
    detay = []
    for urun, miktar in kalemler:
        miktar = int(miktar or 0)
        if miktar <= 0:
            continue
        kesimler = (FasonKesim.objects.filter(silindi=False, urun=urun)
                   .select_related("kesilmis_parca", "kesilmis_parca__kesildigi_profil")
                   .order_by("sira", "pk"))
        for k in kesimler:
            detay.append({"urun": urun, "miktar": miktar, "kesim": k,
                          "toplam_adet": k.adet * miktar})

    ozet_map = {}
    ozet_sira = {}
    for d in detay:
        parca = d["kesim"].kesilmis_parca
        profil = parca.kesildigi_profil
        key = (profil.pk if profil else None, parca.pk)
        if key not in ozet_map:
            ozet_map[key] = {"profil": profil, "kesilmis_parca": parca, "toplam_adet": 0}
            ozet_sira[key] = d["kesim"].sira
        ozet_map[key]["toplam_adet"] += d["toplam_adet"]
    ozet = [ozet_map[key] for key in
           sorted(ozet_map, key=lambda key: (ozet_sira[key], key))]
    return {"detay": detay, "ozet": ozet}


def _sonraki_sira(yil):
    m = FasonKesimKaydi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


@transaction.atomic
def fason_kaydi_olustur(*, kalemler, kullanici=None) -> FasonKesimKaydi:
    """kalemler: [(Stok, miktar), ...] — dolu satırlar (fason_hesapla view'ında formset'ten
    zaten filtrelenmiş halde gelir). Kaydın kendisi yalnızca ürün+miktar girdisini saklar;
    kesim sonucu SAKLANMAZ — bkz. kayit_sonucu()."""
    if not kalemler:
        raise FasonHatasi("En az bir ürün satırı gerekli.")
    yil = timezone.localdate().year
    kayit = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(yil)
                kayit = FasonKesimKaydi.objects.create(
                    yil=yil, sira=sira, no=f"FKL-{yil}-{sira:04d}",
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if kayit is None:
        raise FasonHatasi("Kayıt numarası üretilemedi; tekrar deneyin.")
    for i, (urun, miktar) in enumerate(kalemler, start=1):
        FasonKesimKaydiKalemi.objects.create(
            kayit=kayit, urun=urun, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return kayit


def kayit_kalemleri(kayit):
    return [(k.urun, k.miktar) for k in
            kayit.kalemler.filter(silindi=False).select_related("urun").order_by("sira", "pk")]


def kayit_sonucu(kayit):
    """Kayıttaki ürün/miktarları GÜNCEL Kesim Tanımları'na göre yeniden hesaplar (bkz.
    fason_listesi_hesapla) — kayıt oluşturulduğu andaki değil, ŞU ANKİ tanımlara göre."""
    return fason_listesi_hesapla(kayit_kalemleri(kayit))
