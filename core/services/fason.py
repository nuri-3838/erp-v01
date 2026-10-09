"""FASON > Kesim Tanımları + Kesim Listesi hesaplayıcı — 2 katmanlı BOM:

  1) Stok.kesildigi_profil (1:1): bir "kesilmiş parça" (fasoncunun bir ham profilden
     kestiği ara ürün, ör. "KESİLMİŞ A TİPİ ÖN AYAK 2+1") hangi ham profilden (satinalma
     ürünü) kesiliyor — o parçanın kendi tanımının sabit bir özelliği.
  2) FasonKesim (bu dosya): bir bitmiş ürün (satis_urunu=True) 1 adet üretmek için hangi
     kesilmiş parça(lar)dan kaç adet gerektiği.

Fasoncuya gönderilecek liste iki katman birlikte hesaplanarak üretilir: bitmiş ürün →
kesilmiş parça (katman 2) → o parçanın ham profili (katman 1) → profil bazında toplam."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Cari, FasonFiyat, FasonKesim, FasonKesimKaydi, FasonKesimKaydiKalemi, Stok
from core.sayi import SayiHatasi, parse_tr


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
    """ESKİ hesap (Kesim Tanımları tabanlı); ekranlar artık ``fason_listesi_operasyondan`` kullanır — Kesim Tanımları ekranı ve bu hesap yalnız
    geriye dönük/kontrol amaçlı duruyor. ``kalemler``: [(urun, miktar), ...] (bitmiş ürün + istenen adet).

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
def fason_kaydi_olustur(*, kalemler, cari=None, kullanici=None) -> FasonKesimKaydi:
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
                    yil=yil, sira=sira, no=f"FKL-{yil}-{sira:04d}", cari=cari,
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


def kayit_sonucu(kayit):
    """Kayıttaki ürün/miktarları GÜNCEL Operasyon Tanımları'na ve (kayıtta fasoncu varsa) güncel fason fiyatlarına göre yeniden hesaplar."""
    return fason_listesi_operasyondan(kayit_kalemleri(kayit), cari=kayit.cari)
