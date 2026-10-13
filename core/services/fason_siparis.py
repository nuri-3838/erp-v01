"""FASON > Fason Siparişleri (FS-yyyy-nnnn; 2026-10-12): fasoncuya verilen KESİM SİPARİŞİ belgesi — stok/muhasebe etkisi YOK (stok ve maliyet bugünkü
gibi Fason Dönüş belgesiyle oluşur).

- Satır: kesilmiş parça (stok + varyant; varyant verilmezse üreten tanımın çıktı varyantı/kartın varsayılanı, kayıtta ÇÖZÜLMÜŞ saklanır), sipariş adedi,
  gelen adet (FD onayında işler), rezerve (açık ÜS'lere ayrılan, ``FasonSiparisRezerv``), kalan = adet − gelen − rezerve; bilgi: gerekli profil (üreten
  tanımın girdisi, boy adedi) ve FasonFiyat'tan birim fiyat/tutar (sipariş tarihindeki fiyat, snapshot).
- Durum: TASLAK (planlamaya girmez) → GONDERILDI (Gönder) → KISMI (bir kısmı geldi) → TAMAMLANDI (tüm satırlar gelen ≥ adet); IPTAL (rezervler geri alınır).
  Planlama/kritik stok yalnız AÇIK (GONDERILDI, KISMI) siparişleri görür.
- PLANLAMA ("gelecek stok", ``uretim._plani_hesapla``): kesilmiş parça ihtiyacı serbest stoktan karşılanmazsa açık FS satırlarının rezerve edilmemiş
  kalanından karşılanır → bu kısım için fason istasyon emri AÇILMAZ, ÜS'ye FS rezervi yazılır (``rezervleri_esitle``); yalnız aşan kısım için emir açılır.
  Toplam rezerv satırın kalanını aşamaz. ÜS iptal/kapanışında rezervler silinir.
- FD BAĞI: FD'de FS seçilirse onayda gelen adetler satırın ``gelen``ine işler (fazla gelen = uyarı, engel yok) ve o satırın ÜS rezervleri gelen kadar
  ÜS'nin gerçek stok ayırmasına (``StokAyirma``) dönüşür (en eski ÜS önce). FD geri alınınca gelen düşer; dönüşmüş ayırma geri alınmaz (yumuşak kural).
- FS iptal / adet düşürme: rezervler (fazlası) silinir; etkilenen ÜS'lerde parça ÜS detayında "fason emri gerekli" uyarısıyla görünür; ÜS planı
  KENDİLİĞİNDEN değişmez (revize gerekir).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Max, Prefetch, Sum
from django.utils import timezone

from core.models import Cari, FasonSiparisi, FasonSiparisRezerv, FasonSiparisSatiri, IstasyonEmri, Stok, UretimEmri
from core.sayi import SayiHatasi, parse_tr
from core.services import stok_ayirma
from core.services.fason import FasonHatasi, gecerli_fiyat

SIFIR = Decimal("0")
Durum = FasonSiparisi.Durum
ACIK_DURUMLAR = (Durum.GONDERILDI, Durum.KISMI)
LISTE_DURUMLARI = (Durum.TASLAK, Durum.GONDERILDI, Durum.KISMI)        # liste varsayılanı: açık kalan işler


# === Sorgular / hesaplar ================================================================================

def _satirlar_qs():
    return FasonSiparisSatiri.objects.filter(silindi=False).select_related("stok__uretim_birimi", "varyant", "profil").order_by("sira", "pk")


def aktif_siparisler():
    return (FasonSiparisi.objects.filter(silindi=False).select_related("cari")
            .prefetch_related(Prefetch("satirlar", queryset=_satirlar_qs())).order_by("-yil", "-sira"))


def acik_siparisler():
    return aktif_siparisler().filter(durum__in=ACIK_DURUMLAR)


def aktif_rezervler():
    """Hesaba giren rezervler: silinmemiş satır, açık (ACIK) üretim siparişi, açık FS."""
    return FasonSiparisRezerv.objects.filter(silindi=False, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                             satir__silindi=False, satir__siparis__silindi=False)


def satir_rezerv(satir, haric_emir=None) -> Decimal:
    qs = aktif_rezervler().filter(satir_id=getattr(satir, "pk", satir))
    if haric_emir is not None:
        qs = qs.exclude(uretim_emri_id=getattr(haric_emir, "pk", haric_emir))
    return qs.aggregate(t=Sum("miktar"))["t"] or SIFIR


def satir_kalan(satir, haric_emir=None) -> Decimal:
    """Rezerve edilmemiş kalan = adet − gelen − (başka ÜS'lerin) rezervi; eksi olamaz."""
    return max(SIFIR, satir.adet - satir.gelen - satir_rezerv(satir, haric_emir))


def satir_bilgileri(siparis) -> list:
    """Detay/PDF satırları: {satir, rezerv, kalan, tutar, rezervler [(ÜS, miktar)]}."""
    rezervler = {}
    for r in aktif_rezervler().filter(satir__siparis=siparis).select_related("uretim_emri").order_by("uretim_emri_id"):
        rezervler.setdefault(r.satir_id, []).append(r)
    sonuc = []
    for s in siparis.satirlar.all() if hasattr(siparis, "_prefetched_objects_cache") else _satirlar_qs().filter(siparis=siparis):
        if s.silindi:
            continue
        rez = sum((r.miktar for r in rezervler.get(s.pk, [])), SIFIR)
        sonuc.append({"satir": s, "rezerv": rez, "kalan": max(SIFIR, s.adet - s.gelen - rez), "acik": max(SIFIR, s.adet - s.gelen),
                      "tutar": (s.birim_fiyat * s.adet) if s.birim_fiyat is not None else None, "rezervler": rezervler.get(s.pk, [])})
    return sonuc


def siparis_toplamlari(siparis) -> dict:
    bilgiler = satir_bilgileri(siparis)
    toplam = {}
    for b in bilgiler:
        if b["tutar"] is not None:
            toplam[b["satir"].para_birimi] = toplam.get(b["satir"].para_birimi, SIFIR) + b["tutar"]
    return {"satirlar": bilgiler, "adet": sum((b["satir"].adet for b in bilgiler), SIFIR), "gelen": sum((b["satir"].gelen for b in bilgiler), SIFIR),
            "kalan": sum((b["acik"] for b in bilgiler), SIFIR), "rezerv": sum((b["rezerv"] for b in bilgiler), SIFIR), "tutar": toplam,
            "fiyatsiz": [b["satir"].stok for b in bilgiler if b["satir"].birim_fiyat is None]}


def durum_guncelle(siparis, kullanici=None) -> FasonSiparisi:
    """Gelen adetlere göre GONDERILDI / KISMI / TAMAMLANDI; TASLAK ve IPTAL'e dokunmaz."""
    if siparis.durum in (Durum.TASLAK, Durum.IPTAL):
        return siparis
    satirlar = list(_satirlar_qs().filter(siparis=siparis))
    if satirlar and all(s.gelen >= s.adet for s in satirlar):
        yeni = Durum.TAMAMLANDI
    elif any(s.gelen > 0 for s in satirlar):
        yeni = Durum.KISMI
    else:
        yeni = Durum.GONDERILDI
    if yeni != siparis.durum:
        siparis.durum = yeni
        siparis.updated_by = kullanici
        siparis.save(update_fields=["durum", "updated_by", "updated_at"])
    return siparis


# === Satır çözümü (varyant, profil bilgisi, fiyat) ===========================================================

def satir_varyant_pk(stok: Stok, varyant_id=None):
    """Satırın (stok, varyant) anahtarı: varyantsız kartta None; varyantlı kartta verilen varyant, yoksa üreten tanımın çıktı varyantı
    (SABİT kuralda sabit varyant — fason kesim çıktıları markasız/YOK), o da yoksa kartın varsayılanı."""
    if not stok.varyantli:
        return None
    if varyant_id:
        return getattr(varyant_id, "pk", varyant_id)
    from core.services import planlama_varyant, varyant as varyant_servis
    from core.services.uretim import ureten_operasyon
    op = ureten_operasyon(stok)
    if op is not None:
        sv = planlama_varyant.cikti_varyanti(op, stok, None)
        if sv is not None:
            return sv.pk
    sv = varyant_servis.hareket_varyanti(stok, None)
    return sv.pk if sv is not None else None


def profil_bilgisi(stok: Stok, adet: Decimal) -> tuple:
    """(üreten operasyon|None, ham profil Stok|None, profil boy adedi|None): tam boy kuralıyla ⌈adet / çıktı miktarı⌉ × girdi miktarı; PARÇALA'da
    referans olmayan çıktı için boy adedi None (aynı boydan çıkar)."""
    from core.models import Operasyon
    from core.services.uretim import _yukari_yuvarla, operasyon_girdileri, tanim_ciktilari, ureten_operasyon
    op = ureten_operasyon(stok)
    if op is None:
        return None, None, None
    girdi = next(iter(operasyon_girdileri(op)), None)
    if girdi is None:
        return op, None, None
    ref = tanim_ciktilari(op)
    ref_stok_id = ref[0].stok_id if ref else op.cikti_id
    if op.tur == Operasyon.Tur.PARCALA and ref_stok_id != stok.pk:
        return op, girdi.girdi, None
    cikti_miktar = next((c.miktar for c in ref if c.stok_id == stok.pk), op.cikti_miktar)
    calistirma = adet / cikti_miktar
    if op.tam_calistirma:
        calistirma = _yukari_yuvarla(calistirma)
    return op, girdi.girdi, calistirma * girdi.miktar


def fason_parca_idleri() -> list:
    """Fason istasyonlarının operasyonlarından çıkan kesilmiş parçalar (sürücü çıktılar) — satır stok seçimi."""
    from core.models import OperasyonCikti
    return list(OperasyonCikti.objects.filter(silindi=False, surucu=True, operasyon__silindi=False, operasyon__istasyon__fason=True)
                .values_list("stok_id", flat=True).distinct())


def _adet_coz(ham, satir_no):
    try:
        adet = ham if hasattr(ham, "as_tuple") else parse_tr(str(ham))
    except SayiHatasi:
        raise FasonHatasi(f"{satir_no}. satır: adet geçerli bir sayı olmalı.")
    if adet <= 0:
        raise FasonHatasi(f"{satir_no}. satır: adet sıfırdan büyük olmalı.")
    return adet


def _satirlari_coz(satirlar) -> list:
    """[(stok_id, adet[, varyant_id])] → [(Stok, Decimal, varyant pk|None)]; aynı (stok, varyant) tek satır."""
    sonuc, gorulen = [], set()
    temiz = [s for s in satirlar if s and s[0]]
    if not temiz:
        raise FasonHatasi("En az bir satır (parça + adet) girin.")
    for i, (stok_id, adet, *ek) in enumerate(temiz, start=1):
        stok = Stok.objects.filter(pk=getattr(stok_id, "pk", stok_id), silindi=False).select_related("varsayilan_varyant").first()
        if stok is None or not stok.uretim_urunu:
            raise FasonHatasi(f"{i}. satır: kesilmiş parça bulunamadı (üretim ürünü olmalı).")
        adet = _adet_coz(adet, i)
        vpk = satir_varyant_pk(stok, ek[0] if ek else None)
        if (stok.pk, vpk) in gorulen:
            raise FasonHatasi(f"{i}. satır: {stok.kod} aynı siparişte tekrarlanamaz.")
        gorulen.add((stok.pk, vpk))
        sonuc.append((stok, adet, vpk))
    return sonuc


def _satir_yaz(siparis, stok, adet, vpk, sira, kullanici, mevcut=None):
    op, profil, profil_miktar = profil_bilgisi(stok, adet)
    f = gecerli_fiyat(siparis.cari_id, stok, siparis.tarih)
    alanlar = dict(stok=stok, varyant_id=vpk, adet=adet, sira=sira, profil=profil, profil_miktar=profil_miktar,
                   birim_fiyat=f.birim_fiyat if f else None, para_birimi=f.para_birimi if f else "", fasoncu_kodu=f.fasoncu_kodu if f else "")
    if mevcut is None:
        return FasonSiparisSatiri.objects.create(siparis=siparis, created_by=kullanici, updated_by=kullanici, **alanlar)
    for k, v in alanlar.items():
        setattr(mevcut, k, v)
    mevcut.updated_by = kullanici
    mevcut.save()
    return mevcut


def _cari_coz(cari_id, mevcut_cari_id=None):
    cari = Cari.objects.filter(pk=cari_id, silindi=False).first()
    if cari is None:
        raise FasonHatasi("Fasoncu (cari) bulunamadı.")
    from core.services.cari import pasif_kontrol
    pasif_kontrol(cari, FasonHatasi, mevcut_cari_id=mevcut_cari_id, ne="Yeni fason siparişi")
    return cari


def _sonraki_sira(yil):
    return (FasonSiparisi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"] or 0) + 1


# === Belge ===================================================================================================

@transaction.atomic
def siparis_olustur(*, cari_id, tarih, satirlar, termin=None, aciklama="", kullanici=None) -> FasonSiparisi:
    """TASLAK fason siparişi: ``satirlar`` [(stok_id, adet[, varyant_id])]. Planlamaya girmesi için ``gonder``."""
    cari = _cari_coz(cari_id)
    coz = _satirlari_coz(satirlar)
    yil = tarih.year
    siparis = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_sira(yil)
                siparis = FasonSiparisi.objects.create(yil=yil, sira=sira, no=f"FS-{yil}-{sira:04d}", cari=cari, tarih=tarih, termin=termin,
                                                       aciklama=(aciklama or "").strip(), created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if siparis is None:
        raise FasonHatasi("Belge numarası üretilemedi; tekrar deneyin.")
    for i, (stok, adet, vpk) in enumerate(coz, start=1):
        _satir_yaz(siparis, stok, adet, vpk, i * 10, kullanici)
    return siparis


@transaction.atomic
def siparis_guncelle(siparis: FasonSiparisi, *, cari_id, tarih, satirlar, termin=None, aciklama="", kullanici=None) -> tuple:
    """Taslakta her şey; gönderilmiş/kısmi siparişte satır adedi değişir (gelenin altına inemez), satır eklenir, geleni olmayan satır çıkarılır.
    Adet düşünce fazla rezerv serbest kalır. (sipariş, etkilenen ÜS listesi) döner."""
    if siparis.silindi or siparis.durum in (Durum.TAMAMLANDI, Durum.IPTAL):
        raise FasonHatasi("Tamamlanmış/iptal edilmiş fason siparişi düzenlenemez.")
    cari = _cari_coz(cari_id, mevcut_cari_id=siparis.cari_id)
    coz = _satirlari_coz(satirlar)
    siparis.cari, siparis.tarih, siparis.termin, siparis.aciklama = cari, tarih, termin, (aciklama or "").strip()
    siparis.updated_by = kullanici
    siparis.save()
    mevcut = {(s.stok_id, s.varyant_id): s for s in _satirlar_qs().filter(siparis=siparis)}
    etkilenen = []
    for i, (stok, adet, vpk) in enumerate(coz, start=1):
        s = mevcut.pop((stok.pk, vpk), None)
        if s is not None and adet < s.gelen:
            raise FasonHatasi(f"{stok.kod}: sipariş adedi gelen adedin ({s.gelen.normalize():f}) altına düşürülemez.")
        s = _satir_yaz(siparis, stok, adet, vpk, i * 10, kullanici, mevcut=s)
        etkilenen += _rezerv_fazlasini_sal(s, kullanici)
    for s in mevcut.values():
        if s.gelen > 0:
            raise FasonHatasi(f"{s.stok.kod}: gelen adedi olan satır çıkarılamaz (adedi gelen kadar düşürün).")
        etkilenen += [r.uretim_emri for r in aktif_rezervler().filter(satir=s).select_related("uretim_emri")]
        aktif_rezervler().filter(satir=s).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        s.silindi = True
        s.silindi_at = timezone.now()
        s.updated_by = kullanici
        s.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    durum_guncelle(siparis, kullanici)
    return siparis, _tekil(etkilenen)


def _tekil(emirler):
    gorulen, sonuc = set(), []
    for e in emirler:
        if e.pk not in gorulen:
            gorulen.add(e.pk)
            sonuc.append(e)
    return sonuc


def _rezerv_fazlasini_sal(satir, kullanici=None) -> list:
    """Satırın rezervleri kalanı (adet − gelen) aşıyorsa en yeni ÜS'den başlayarak düşürür; etkilenen ÜS'leri döner."""
    izin = max(SIFIR, satir.adet - satir.gelen)
    etkilenen = []
    for r in aktif_rezervler().filter(satir=satir).select_related("uretim_emri").order_by("uretim_emri_id"):
        x = min(r.miktar, izin)
        izin -= x
        if x < r.miktar:
            etkilenen.append(r.uretim_emri)
            _rezerv_yaz(r, x, kullanici)
    return etkilenen


def _rezerv_yaz(r: FasonSiparisRezerv, miktar: Decimal, kullanici=None):
    if miktar <= 0:
        r.silindi = True
        r.silindi_at = timezone.now()
        r.updated_by = kullanici
        r.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
        return None
    if r.miktar != miktar:
        r.miktar = miktar
        r.updated_by = kullanici
        r.save(update_fields=["miktar", "updated_by", "updated_at"])
    return r


@transaction.atomic
def gonder(siparis: FasonSiparisi, kullanici=None) -> FasonSiparisi:
    """TASLAK → GONDERILDI: sipariş fasoncuya verildi; planlama ve kritik stok artık bu siparişi görür."""
    if siparis.silindi or siparis.durum != Durum.TASLAK:
        raise FasonHatasi("Yalnız taslak sipariş gönderilebilir.")
    if not _satirlar_qs().filter(siparis=siparis).exists():
        raise FasonHatasi("Satırı olmayan sipariş gönderilemez.")
    siparis.durum = Durum.GONDERILDI
    siparis.updated_by = kullanici
    siparis.save(update_fields=["durum", "updated_by", "updated_at"])
    return siparis


@transaction.atomic
def iptal(siparis: FasonSiparisi, kullanici=None) -> list:
    """Siparişi iptal eder; tüm ÜS rezervleri geri alınır. Etkilenen ÜS listesi döner (ekran uyarısı: parça o ÜS'lerde yeniden eksik)."""
    if siparis.silindi or siparis.durum in (Durum.TAMAMLANDI, Durum.IPTAL):
        raise FasonHatasi("Tamamlanmış/iptal edilmiş sipariş iptal edilemez.")
    rezervler = list(aktif_rezervler().filter(satir__siparis=siparis).select_related("uretim_emri"))
    etkilenen = _tekil([r.uretim_emri for r in rezervler])
    for r in rezervler:
        _rezerv_yaz(r, SIFIR, kullanici)
    siparis.durum = Durum.IPTAL
    siparis.updated_by = kullanici
    siparis.save(update_fields=["durum", "updated_by", "updated_at"])
    return etkilenen


@transaction.atomic
def sil(siparis: FasonSiparisi, kullanici=None) -> FasonSiparisi:
    """Yalnız TASLAK silinir."""
    if siparis.silindi:
        return siparis
    if siparis.durum != Durum.TASLAK:
        raise FasonHatasi("Yalnız taslak sipariş silinir; gönderilmiş siparişi iptal edin.")
    siparis.silindi = True
    siparis.silindi_at = timezone.now()
    siparis.updated_by = kullanici
    siparis.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return siparis


# === Planlama: "gelecek stok" ve ÜS rezervleri =============================================================

def acik_kalan_haritasi(haric_emir=None) -> dict:
    """{(stok pk, varyant pk|None): açık FS satırlarının rezerve edilmemiş kalanı} — tek geçiş; ``haric_emir`` kendi rezervini kalan sayar
    (revize/eksik hesabı)."""
    satirlar = list(_satirlar_qs().filter(siparis__silindi=False, siparis__durum__in=ACIK_DURUMLAR))
    if not satirlar:
        return {}
    qs = aktif_rezervler().filter(satir_id__in=[s.pk for s in satirlar])
    if haric_emir is not None:
        qs = qs.exclude(uretim_emri_id=getattr(haric_emir, "pk", haric_emir))
    rezerv = {r["satir_id"]: r["t"] for r in qs.values("satir_id").annotate(t=Sum("miktar"))}
    harita = {}
    for s in satirlar:
        k = max(SIFIR, s.adet - s.gelen - rezerv.get(s.pk, SIFIR))
        if k > 0:
            anahtar = (s.stok_id, s.varyant_id)
            harita[anahtar] = harita.get(anahtar, SIFIR) + k
    return harita


def kalan_icin(harita: dict, stok_pk, varyant_pk=None) -> Decimal:
    """Düğüm için kalan: varyant verilmemişse (kapalı plan / varyantsız kart) kartın tüm satırları."""
    if varyant_pk is None:
        return sum((v for (pk, _v), v in harita.items() if pk == stok_pk), SIFIR)
    return harita.get((stok_pk, varyant_pk), SIFIR)


def gelecek_haritasi(stok_idler) -> dict:
    """Kritik stok 'gelecek': {stok pk: açık FS satırlarının gelmemiş adedi (adet − gelen)} — rezerv düşülmez (bekleyen tüketim zaten ayrı sayılır)."""
    idler = list(stok_idler)
    if not idler:
        return {}
    sonuc = {}
    for s in _satirlar_qs().filter(siparis__silindi=False, siparis__durum__in=ACIK_DURUMLAR, stok_id__in=idler):
        k = max(SIFIR, s.adet - s.gelen)
        if k > 0:
            sonuc[s.stok_id] = sonuc.get(s.stok_id, SIFIR) + k
    return sonuc


@transaction.atomic
def rezervleri_esitle(emir, hedef: dict, kullanici=None) -> list:
    """ÜS'nin FS rezervlerini plana ÇEKER: ``hedef`` {(stok pk, varyant pk|None): fason siparişinden karşılanacak miktar}. Satırlar termin → belge →
    sıra düzeniyle doldurulur; satır başına en çok kalanı (ÜS'nin kendi rezervi serbest sayılır). Hedefte olmayan rezervler kapanır.
    [{"fs", "stok", "varyant", "miktar"}] döner."""
    emir_id = getattr(emir, "pk", emir)
    mevcut = {r.satir_id: r for r in FasonSiparisRezerv.objects.select_for_update()
              .filter(uretim_emri_id=emir_id, silindi=False, satir__silindi=False).select_related("satir")}
    detay = []
    for (stok_pk, vpk), miktar in sorted(hedef.items(), key=lambda x: (x[0][0], x[0][1] or 0)):
        kalan_m = max(SIFIR, Decimal(str(miktar)))
        satirlar = (_satirlar_qs().filter(siparis__silindi=False, siparis__durum__in=ACIK_DURUMLAR, stok_id=stok_pk)
                    .select_related("siparis").order_by("siparis__termin", "siparis__pk", "sira", "pk"))
        if vpk is not None:
            satirlar = satirlar.filter(varyant_id=vpk)
        satirlar = sorted(satirlar, key=lambda s: (s.siparis.termin is None, s.siparis.termin or timezone.localdate(), s.siparis_id, s.sira, s.pk))
        for s in satirlar:
            r = mevcut.pop(s.pk, None)
            kap = satir_kalan(s, haric_emir=emir_id)
            x = min(kalan_m, kap)
            kalan_m -= x
            if r is None:
                if x > 0:
                    r = FasonSiparisRezerv.objects.create(satir=s, uretim_emri_id=emir_id, miktar=x, created_by=kullanici, updated_by=kullanici)
            else:
                r = _rezerv_yaz(r, x, kullanici)
            if r is not None and x > 0:
                detay.append({"fs": s.siparis.no, "stok": s.stok.kod, "varyant": s.varyant_id, "miktar": format(x.normalize(), "f")})
            if kalan_m <= 0:
                break
        if kalan_m > 0:
            from core.services.uretim import UretimHatasi
            raise UretimHatasi(f"Fason siparişi rezervi yetersiz kaldı ({Stok.objects.get(pk=stok_pk).kod}: {kalan_m.normalize():f}); yeniden deneyin.")
    for r in mevcut.values():
        _rezerv_yaz(r, SIFIR, kullanici)
    return detay


def emir_rezervleri(emir):
    """ÜS detayı: ÜS'nin aktif FS rezervleri (satır + sipariş + termin)."""
    return list(aktif_rezervler().filter(uretim_emri_id=getattr(emir, "pk", emir))
                .select_related("satir__siparis__cari", "satir__stok", "satir__varyant").order_by("satir__siparis__termin", "satir__siparis__pk", "satir__sira"))


@transaction.atomic
def rezervleri_sil(emir, kullanici=None) -> int:
    """ÜS iptal/kapanış: tüm FS rezervleri kapanır."""
    n = 0
    for r in FasonSiparisRezerv.objects.select_for_update().filter(uretim_emri_id=getattr(emir, "pk", emir), silindi=False):
        _rezerv_yaz(r, SIFIR, kullanici)
        n += 1
    return n


def us_rezerv_ozeti(siparis) -> list:
    """FS detayı: hangi ÜS'ye ne kadar rezerve — [{"emir", "satirlar": [(stok, miktar)]}]."""
    gruplar = {}
    for r in aktif_rezervler().filter(satir__siparis=siparis).select_related("uretim_emri", "satir__stok", "satir__varyant").order_by("uretim_emri_id", "satir__sira"):
        g = gruplar.setdefault(r.uretim_emri_id, {"emir": r.uretim_emri, "satirlar": [], "toplam": SIFIR})
        g["satirlar"].append(r)
        g["toplam"] += r.miktar
    return list(gruplar.values())


# === Fason Dönüş bağı =======================================================================================

def fd_siparis_coz(fason_siparis_id, cari_id):
    """FD'ye bağlanacak FS: açık (gönderildi/kısmi) ve aynı fasoncu olmalı."""
    if fason_siparis_id in (None, ""):
        return None
    fs = FasonSiparisi.objects.filter(pk=getattr(fason_siparis_id, "pk", fason_siparis_id), silindi=False).select_related("cari").first()
    if fs is None:
        raise FasonHatasi("Fason siparişi bulunamadı.")
    if fs.durum not in ACIK_DURUMLAR:
        raise FasonHatasi(f"{fs.no}: yalnız gönderilmiş/kısmi gelen fason siparişi dönüşe bağlanabilir ({fs.get_durum_display()}).")
    if int(cari_id) != fs.cari_id:
        raise FasonHatasi(f"{fs.no} başka bir fasoncuya ({fs.cari.unvan}) ait.")
    return fs


def _satir_bul(fs, stok_id, varyant_id):
    satirlar = list(_satirlar_qs().filter(siparis=fs, stok_id=stok_id))
    if not satirlar:
        return None
    return next((s for s in satirlar if s.varyant_id == varyant_id), satirlar[0])


@transaction.atomic
def gelen_isle(donus, kullanici=None) -> list:
    """FD ONAYINDAN sonra: her onaylı kaydın her çıktısı (ana + yan) siparişin eşleşen satırına 'gelen' olarak işler; satırın ÜS rezervleri gelen kadar
    gerçek stok ayırmasına dönüşür (en eski ÜS önce). Uyarı metinleri döner (fazla gelen, siparişte olmayan parça)."""
    from core.models import OperasyonKaydi
    fs = donus.fason_siparis
    if fs is None:
        return []
    uyarilar = []
    kayitlar = donus.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI).select_related("uretim_emri").order_by("sira", "pk")
    for kayit in kayitlar:
        for c in kayit.ciktilar.filter(silindi=False).select_related("stok"):
            if c.miktar <= 0:
                continue
            satir = _satir_bul(fs, c.stok_id, c.varyant_id)
            if satir is None:
                uyarilar.append(f"{c.stok.kod}: {fs.no} siparişinde bu parça yok; gelen adet işlenmedi.")
                continue
            satir.gelen = satir.gelen + c.miktar
            satir.updated_by = kullanici
            satir.save(update_fields=["gelen", "updated_by", "updated_at"])
            if satir.gelen > satir.adet:
                uyarilar.append(f"{c.stok.kod}: {fs.no} satırında gelen ({satir.gelen.normalize():f}) sipariş adedini ({satir.adet.normalize():f}) aşıyor.")
            kalan_c = c.miktar
            for r in (FasonSiparisRezerv.objects.select_for_update().filter(satir=satir, silindi=False, uretim_emri__silindi=False,
                                                                           uretim_emri__durum=UretimEmri.Durum.ACIK)
                      .select_related("uretim_emri").order_by("uretim_emri_id")):
                x = min(r.miktar, kalan_c)
                if x <= 0:
                    break
                stok_ayirma.ayirma_ekle(r.uretim_emri, c.stok_id, x, kullanici, varyant=c.varyant_id if r.uretim_emri.varyantli_plan else None)
                _rezerv_yaz(r, r.miktar - x, kullanici)
                kalan_c -= x
    durum_guncelle(fs, kullanici)
    return uyarilar


@transaction.atomic
def gelen_geri_al(donus, kullanici=None) -> None:
    """FD geri alınırken (onaylı kayıtlar silinmeden ÖNCE): gelen adetler düşer; dönüşmüş stok ayırması geri alınmaz (yumuşak kural)."""
    from core.models import OperasyonKaydi
    fs = donus.fason_siparis
    if fs is None:
        return
    for kayit in donus.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI):
        for c in kayit.ciktilar.filter(silindi=False):
            satir = _satir_bul(fs, c.stok_id, c.varyant_id)
            if satir is None:
                continue
            satir.gelen = max(SIFIR, satir.gelen - c.miktar)
            satir.updated_by = kullanici
            satir.save(update_fields=["gelen", "updated_by", "updated_at"])
    durum_guncelle(fs, kullanici)


def fd_satir_onerisi(fs) -> list:
    """FD formu ön doldurma: açık kalanı olan satırlar, üreten operasyon başına tek satır (adet = referans çıktının kalanı ya da ilk satırın kalanı)."""
    from core.services.uretim import ureten_operasyon
    sonuc, gorulen = [], set()
    for b in satir_bilgileri(fs):
        if b["acik"] <= 0:
            continue
        op = ureten_operasyon(b["satir"].stok)
        if op is None or op.pk in gorulen:
            continue
        gorulen.add(op.pk)
        sonuc.append({"operasyon": op.pk, "adet": b["acik"].quantize(Decimal("0.001"))})
    return sonuc


# === Bilgi notları =========================================================================================

def acik_fason_emirleri(stok_idler) -> list:
    """Aynı parça için açık ÜS'lerde halihazırda fason istasyon emri var mı (ihtiyaç iki kez sipariş edilmesin): [(ie, stok, kalan adet)]."""
    idler = set(stok_idler)
    if not idler:
        return []
    from core.services.uretim import _referans_miktar
    sonuc = []
    for ie in (IstasyonEmri.objects.filter(silindi=False, istasyon__fason=True, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                           durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
               .select_related("uretim_emri", "operasyon__cikti", "operasyon__istasyon").order_by("pk")):
        if ie.operasyon.cikti_id in idler:
            kalan = max(SIFIR, ie.planlanan - ie.tamamlanan) * _referans_miktar(ie.operasyon)
            if kalan > 0:
                sonuc.append((ie, ie.operasyon.cikti, kalan))
    return sonuc
