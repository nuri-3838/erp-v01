"""STOK SAYIMI (2026-10-16) — belge (SY-YYYY-NNNN), snapshot, çok kişili toplamsal giriş, fark hesabı, karar. Onay/geri alma (stok hareketi + muhasebe fişi) bu modülün sonraki dilimidir.

Kurallar (kullanıcı kararları):
- Sayım belgesi TEK depo içindir; her depo ayrı belge (fason deposu dahil). Satır = (stok, varyant): varyantlı kartta varyant bazında, varyantsızda boş.
- Açılışta kapsamdaki (hizmet olmayan; kategori ya da stok süzgeci) kartların depo eldekisi ``sistem_snapshot`` olarak kaydedilir (eldeki ≠ 0 olanlar; ``sifirlar_dahil``: hareketi olan hepsi).
  Sayımda sistemde 0 görünen ürün çıkarsa ``satir_ekle``.
- Girişler TOPLAMSAL: satırın sayılanı = geçerli girişlerin toplamı; hiç giriş yoksa "sayılmadı" (fark yok — 0 sayılmaz). Yönetici girişi geçersiz işaretleyebilir.
- FARK referansı: ``sayim.tarih`` itibarıyla sistem eldekisi (geriye tarihli GEÇ girilmiş hareketler dahil); sayım tarihi = kesme günü ise, o gün kesmeden SONRA girilen hareketler sayımdan
  sonrası sayılıp referanstan düşülür. fark = sayılan − referans. Tutar = fark × stok kartının güncel ağırlıklı ortalama maliyeti (bilgi; onayda hareketin gerçek tutarı).
- Maliyeti belirsiz kart (fiyatsız/geçici giriş var ya da ortalama yok): fark gösterilir ama karar ``Düzelt`` verilemez.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import connection, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from core.models import Depo, Kategori, Stok, StokHareket, StokSayimGirdisi, StokSayimi, StokSayimSatiri, StokVaryant
from core.sayi import SayiHatasi, parse_tr
from core.services import stok_ortalama
from core.tarih import tr_bugun

SIFIR = Decimal("0")
ESIK = Decimal("0.0000005")
SAYIM_NO_KILIDI = 7410002          # pg_advisory_xact_lock: sayım numarası üretimi
Durum, Karar = StokSayimi.Durum, StokSayimSatiri.Karar


class SayimHatasi(ValueError):
    """Sayım kural ihlali (Türkçe mesaj)."""


def _kilit():
    with connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", [SAYIM_NO_KILIDI])


def _sonraki_sira(yil) -> int:
    return (StokSayimi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"] or 0) + 1


def kategori_idleri(kategori_id) -> set:
    """Kategori ve tüm alt kategorileri (kart alt kategoriye bağlıdır; üst seçilirse altlar da kapsanır)."""
    sonuc, yeni = {int(kategori_id)}, {int(kategori_id)}
    while yeni:
        yeni = set(Kategori.objects.filter(ust_id__in=yeni, silindi=False).values_list("pk", flat=True)) - sonuc
        sonuc |= yeni
    return sonuc


def birim_maliyet(stok, varyant=None):
    """Satırın güncel ağırlıklı ortalama maliyeti (TL): varyantlı kartta VARYANTIN kendi ortalaması (maliyet zinciri varyant bazındadır), değilse kartınki; yoksa None."""
    return varyant.ort_maliyet_try if varyant is not None else stok.ort_maliyet_try


def _eldeki_gruplari(depo, stok_idler) -> dict:
    """{(stok_id, varyant_id): eldeki} — depo bazında, hareketi olan tüm (stok, varyant) çiftleri (TEK gruplu sorgu)."""
    gruplu = (StokHareket.objects.filter(depo=depo, stok_id__in=list(stok_idler), silindi=False).values("stok_id", "varyant_id", "tur").annotate(t=Sum("miktar")))
    sonuc = {}
    for g in gruplu:
        anahtar = (g["stok_id"], g["varyant_id"])
        sonuc[anahtar] = sonuc.get(anahtar, SIFIR) + (g["t"] if g["tur"] == StokHareket.Tur.GIRIS else -g["t"])
    return sonuc


# ----------------------------------------------------------------------------------------------------------------------- belge
@transaction.atomic
def sayim_ac(*, depo_id, tarih, kategori_id=None, stok_idler=None, kor=False, sifirlar_dahil=False, aciklama="", kullanici=None) -> StokSayimi:
    """Sayım belgesi açar ve kesme anındaki sistem miktarlarını (varyant bazında) satır snapshot'ı olarak kaydeder."""
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if depo is None:
        raise SayimHatasi("Depo bulunamadı.")
    if tarih is None or tarih > tr_bugun():
        raise SayimHatasi("Sayım tarihi bugünden sonra olamaz.")
    kategori = None
    stoklar = Stok.objects.filter(silindi=False).select_related("kategori").exclude(kategori__hizmet_kategorisi=True)
    if kategori_id:
        kategori = Kategori.objects.filter(pk=kategori_id, silindi=False).first()
        if kategori is None:
            raise SayimHatasi("Kategori bulunamadı.")
        stoklar = stoklar.filter(kategori_id__in=kategori_idleri(kategori.pk))
    if stok_idler:
        stoklar = stoklar.filter(pk__in=list(stok_idler))
    stoklar = {s.pk: s for s in stoklar}
    eldeki = _eldeki_gruplari(depo, stoklar)
    _kilit()
    yil = tarih.year
    sira = _sonraki_sira(yil)
    sayim = StokSayimi.objects.create(
        yil=yil, sira=sira, no=f"SY-{yil}-{sira:04d}", depo=depo, kategori=kategori, tarih=tarih, kesme_zamani=timezone.now(), kor=bool(kor),
        aciklama=(aciklama or "").strip()[:300], created_by=kullanici, updated_by=kullanici)
    varyantlar = {v.pk: v for v in StokVaryant.objects.filter(stok_id__in=list(stoklar))}
    satirlar = []
    for (stok_id, varyant_id), miktar in sorted(eldeki.items(), key=lambda x: (stoklar[x[0][0]].kod, x[0][1] or 0)):
        if abs(miktar) < ESIK and not sifirlar_dahil:
            continue
        satirlar.append(StokSayimSatiri(sayim=sayim, stok_id=stok_id, varyant_id=varyant_id, sistem_snapshot=miktar,
                                        ort_maliyet_snapshot=birim_maliyet(stoklar[stok_id], varyantlar.get(varyant_id)), created_by=kullanici, updated_by=kullanici))
    StokSayimSatiri.objects.bulk_create(satirlar)
    return sayim


def _duzenlenebilir(sayim, *, kapali_dahil=False):
    sayim.refresh_from_db(fields=["durum", "silindi"])
    izinli = (Durum.ACIK, Durum.KAPALI) if kapali_dahil else (Durum.ACIK,)
    if sayim.silindi or sayim.durum not in izinli:
        raise SayimHatasi("Bu sayım belgesi artık düzenlenemez." if kapali_dahil else "Sayım girişi kapalı ya da belge sonlanmış.")


@transaction.atomic
def satir_ekle(sayim, *, stok_id, varyant_id=None, kullanici=None) -> StokSayimSatiri:
    """Snapshot'ta olmayan (sistemde 0 görünen) ürünü sayıma ekler (snapshot = o anki depo eldekisi)."""
    sayim = StokSayimi.objects.select_for_update().get(pk=sayim.pk)
    _duzenlenebilir(sayim)
    stok = Stok.objects.filter(pk=stok_id, silindi=False).select_related("kategori").first()
    if stok is None or (stok.kategori_id and stok.kategori.hizmet_kategorisi):
        raise SayimHatasi("Stok bulunamadı ya da hizmet kartı (stok tutulmaz).")
    if stok.varyantli:
        if not varyant_id or not StokVaryant.objects.filter(pk=varyant_id, stok=stok, silindi=False).exists():
            raise SayimHatasi(f"{stok.kod} varyantlı bir karttır; geçerli bir varyant seçin.")
    else:
        varyant_id = None
    if StokSayimSatiri.objects.filter(sayim=sayim, stok=stok, varyant_id=varyant_id).exists():
        raise SayimHatasi("Bu ürün (varyant) sayım belgesinde zaten var.")
    mevcut = _eldeki_gruplari(sayim.depo, [stok.pk]).get((stok.pk, varyant_id), SIFIR)
    return StokSayimSatiri.objects.create(sayim=sayim, stok=stok, varyant_id=varyant_id, sistem_snapshot=mevcut, ort_maliyet_snapshot=birim_maliyet(stok, StokVaryant.objects.filter(pk=varyant_id).first() if varyant_id else None),
                                         eklenen=True, created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def giris_kapat(sayim, kullanici=None) -> StokSayimi:
    sayim = StokSayimi.objects.select_for_update().get(pk=sayim.pk)
    _duzenlenebilir(sayim)
    sayim.durum = Durum.KAPALI
    sayim.updated_by = kullanici
    sayim.save(update_fields=["durum", "updated_by", "updated_at"])
    return sayim


@transaction.atomic
def giris_ac(sayim, kullanici=None) -> StokSayimi:
    sayim = StokSayimi.objects.select_for_update().get(pk=sayim.pk)
    if sayim.silindi or sayim.durum != Durum.KAPALI:
        raise SayimHatasi("Yalnız girişi kapanmış sayımın girişi yeniden açılabilir.")
    sayim.durum = Durum.ACIK
    sayim.updated_by = kullanici
    sayim.save(update_fields=["durum", "updated_by", "updated_at"])
    return sayim


@transaction.atomic
def sayim_sil(sayim, kullanici=None) -> None:
    """Onaylanmamış belgeyi siler (muhasebe/stok etkisi yoktur; soft delete). Onaylı belge yalnız geri alınır."""
    sayim = StokSayimi.objects.select_for_update().get(pk=sayim.pk)
    if sayim.silindi:
        return
    if sayim.durum not in (Durum.ACIK, Durum.KAPALI):
        raise SayimHatasi("Onaylanmış ya da geri alınmış sayım silinemez.")
    sayim.silindi = True
    sayim.silindi_at = timezone.now()
    sayim.updated_by = kullanici
    sayim.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])


# ----------------------------------------------------------------------------------------------------------------------- giriş
def _miktar(deger) -> Decimal:
    try:
        m = parse_tr(deger)
    except SayiHatasi:
        raise SayimHatasi("Sayılan miktar geçerli bir sayı olmalı.")
    if m < 0:
        raise SayimHatasi("Sayılan miktar negatif olamaz.")
    return m


@transaction.atomic
def girdi_ekle(satir, *, kullanici, miktar, kaynak=StokSayimGirdisi.Kaynak.EKRAN, not_="") -> StokSayimGirdisi:
    """Satıra bir sayım girişi ekler (TOPLAMSAL: önceki girişlere eklenir). Yalnız giriş açıkken (ACIK)."""
    satir = StokSayimSatiri.objects.select_related("sayim").get(pk=satir.pk)
    _duzenlenebilir(satir.sayim)
    return StokSayimGirdisi.objects.create(satir=satir, kullanici=kullanici, miktar=_miktar(miktar), kaynak=kaynak, aciklama=(not_ or "").strip()[:200],
                                          created_by=kullanici, updated_by=kullanici)


@transaction.atomic
def girdi_gecersiz(girdi, *, kullanici, gecersiz=True) -> StokSayimGirdisi:
    """Yöneticinin bir girişi geçersiz (ya da yeniden geçerli) işaretlemesi — belge onaylanana dek."""
    girdi = StokSayimGirdisi.objects.select_related("satir__sayim").get(pk=girdi.pk)
    _duzenlenebilir(girdi.satir.sayim, kapali_dahil=True)
    girdi.gecersiz = bool(gecersiz)
    girdi.gecersiz_yapan = kullanici if gecersiz else None
    girdi.gecersiz_zamani = timezone.now() if gecersiz else None
    girdi.updated_by = kullanici
    girdi.save(update_fields=["gecersiz", "gecersiz_yapan", "gecersiz_zamani", "updated_by", "updated_at"])
    return girdi


def sayilanlar(sayim) -> dict:
    """{satir id: geçerli girişlerin toplamı} — hiç geçerli girişi olmayan satır sözlükte YOK (sayılmadı)."""
    gruplu = (StokSayimGirdisi.objects.filter(satir__sayim=sayim, silindi=False, gecersiz=False).values("satir_id").annotate(t=Sum("miktar")))
    return {g["satir_id"]: g["t"] for g in gruplu}


# ----------------------------------------------------------------------------------------------------------------------- fark
def referans_sistem(sayim) -> dict:
    """{(stok_id, varyant_id): sayım tarihi itibarıyla sistem eldekisi} — geç girilmiş geriye tarihli hareketler DAHİL; sayım tarihi = kesme günü ise o gün kesmeden sonra girilenler HARİÇ."""
    stok_idler = list(sayim.satirlar.filter(silindi=False).values_list("stok_id", flat=True).distinct())
    kosul = Q(tarih__lt=sayim.tarih)
    if sayim.tarih == timezone.localtime(sayim.kesme_zamani).date():
        kosul |= Q(tarih=sayim.tarih, created_at__lte=sayim.kesme_zamani)
    else:
        kosul |= Q(tarih=sayim.tarih)
    gruplu = (StokHareket.objects.filter(kosul, depo_id=sayim.depo_id, stok_id__in=stok_idler, silindi=False).values("stok_id", "varyant_id", "tur").annotate(t=Sum("miktar")))
    sonuc = {}
    for g in gruplu:
        anahtar = (g["stok_id"], g["varyant_id"])
        sonuc[anahtar] = sonuc.get(anahtar, SIFIR) + (g["t"] if g["tur"] == StokHareket.Tur.GIRIS else -g["t"])
    return sonuc


def fark_satirlari(sayim, *, satir_idleri=None) -> dict:
    """Fark ekranı verisi: {"satirlar": [...], "ozet": {...}}. Satır: satir, stok, varyant, snapshot, referans, degisen (referans − snapshot: sayım sonrası/geç hareket), sayilan (None = sayılmadı),
    fark (None = sayılmadı), birim_maliyet, tutar, belirsiz (maliyeti belirsiz → Düzelt verilemez) + neden, karar. Özet: sayılan satır adedi, fazla/eksik tutarı (tüm sayılanlar) ve yalnız Düzelt satırları."""
    qs = sayim.satirlar.filter(silindi=False).select_related("stok", "varyant")
    if satir_idleri is not None:
        qs = qs.filter(pk__in=list(satir_idleri))
    satirlar = list(qs.order_by("stok__kod", "varyant_id", "id"))
    referans = referans_sistem(sayim)
    sayilan = sayilanlar(sayim)
    stok_idler = {s.stok_id for s in satirlar}
    fiyatsiz = stok_ortalama.fiyatsiz_girisli_stoklar(stok_idler)
    cikti, ozet = [], {"sayilan_adet": 0, "fazla_tutar": SIFIR, "eksik_tutar": SIFIR, "duzelt_adet": 0, "duzelt_fazla_tutar": SIFIR, "duzelt_eksik_tutar": SIFIR, "belirsiz_adet": 0}
    for s in satirlar:
        ref = referans.get((s.stok_id, s.varyant_id), SIFIR)
        sayim_m = sayilan.get(s.pk)
        fark = None if sayim_m is None else sayim_m - ref
        birim = birim_maliyet(s.stok, s.varyant)
        neden = ""
        if s.stok_id in fiyatsiz:
            neden = "Fiyatsız (geçici) girişi olan kart: maliyeti belirsiz"
        elif birim is None or birim <= 0:
            neden = "Ortalama maliyet yok"
        tutar = None if (fark is None or birim is None) else (fark * birim).quantize(Decimal("0.01"))
        satir_sonuc = {"satir": s, "stok": s.stok, "varyant": s.varyant, "snapshot": s.sistem_snapshot, "referans": ref, "degisen": ref - s.sistem_snapshot, "sayilan": sayim_m, "fark": fark,
                       "birim_maliyet": birim, "tutar": tutar, "belirsiz": bool(neden), "neden": neden, "karar": s.karar}
        cikti.append(satir_sonuc)
        if fark is None:
            continue
        ozet["sayilan_adet"] += 1
        if neden:
            ozet["belirsiz_adet"] += 1
        if tutar is not None and abs(fark) >= ESIK:
            yon = "fazla_tutar" if fark > 0 else "eksik_tutar"
            ozet[yon] += abs(tutar)
            if s.karar == Karar.DUZELT:
                ozet["duzelt_adet"] += 1
                ozet["duzelt_" + yon] += abs(tutar)
    return {"satirlar": cikti, "ozet": ozet}


@transaction.atomic
def karar_ver(satir, karar, *, kullanici, not_="") -> StokSayimSatiri:
    """Satır kararı: Düzelt / Araştır / Bekliyor. Düzelt için satır sayılmış, fark ≠ 0 ve kartın maliyeti belirli olmalı; Araştır satırlar onayda işlenmez."""
    if karar not in StokSayimSatiri.Karar.values:
        raise SayimHatasi("Geçersiz karar.")
    satir = StokSayimSatiri.objects.select_related("sayim", "stok").get(pk=satir.pk)
    _duzenlenebilir(satir.sayim, kapali_dahil=True)
    if karar == Karar.DUZELT:
        (d,) = fark_satirlari(satir.sayim, satir_idleri=[satir.pk])["satirlar"]
        if d["fark"] is None:
            raise SayimHatasi(f"{satir.stok.kod}: satır sayılmadı; düzeltilemez.")
        if abs(d["fark"]) < ESIK:
            raise SayimHatasi(f"{satir.stok.kod}: fark yok; düzeltme gerekmez.")
        if d["belirsiz"]:
            raise SayimHatasi(f"{satir.stok.kod}: {d['neden']} — Düzelt verilemez (önce faturayı bağlayın / maliyeti netleştirin).")
    satir.karar = karar
    satir.karar_notu = (not_ or "").strip()[:300]
    satir.karar_veren = kullanici if karar != Karar.BEKLIYOR else None
    satir.updated_by = kullanici
    satir.save(update_fields=["karar", "karar_notu", "karar_veren", "updated_by", "updated_at"])
    return satir
