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


# ----------------------------------------------------------------------------------------------------------------------- ONAY / GERİ ALMA (dilim 3)
# Onay: YALNIZ karar=Düzelt satırları — eksik → ÇIKIŞ, fazla → GİRİŞ (ortalama maliyetle, tarih = sayım tarihi, kaynak SAYIM) + TEK muhasebe fişi (STOK_SAYIM):
#   eksik: 62x BORÇ / stok hesabı ALACAK · fazla: stok hesabı BORÇ / 62x ALACAK. 62x = satış maliyeti kuralıyla aynı (152→620, 153→621, 150/151→623; ara hesap 197/397 YOK).
#   Stok hesabı satırları STOK BAZINDA, 62x tarafı her hesap için YÖNDE TEK satır. Maliyet sonradan değişirse (geç gelen fatura) fiş yerinde yenilenir (stok_ortalama kancası → fis_senkronla).
def _hesaplar(stok):
    """(stok hesabı kodu, maliyet hesabı kodu) — çözülemezse SayimHatasi."""
    from core.services import stok_fis
    try:
        stok_kodu = stok_fis.stok_hesabi_kodu(stok)
        return stok_kodu, stok_fis.maliyet_hesabi_kodu(stok_kodu)
    except stok_ortalama.MaliyetHatasi as e:
        raise SayimHatasi(str(e))


def _etiket(h) -> str:
    from core.services.varyant import varyant_kisa
    return f" [{varyant_kisa(h.varyant)}]" if h.varyant_id else ""


def _fis_satirlari(sayim, hareketler):
    """Fiş satırları: stok bazında stok hesabı satırları + her 62x hesabı için yönde tek satır. ``hareketler``: sayım hareketleri (tutar_try dolu)."""
    from core.services.yevmiye import SatirGirdi
    stok_satirlari, maliyet = [], defaultdict(lambda: {"B": SIFIR, "A": SIFIR})
    for h in sorted(hareketler, key=lambda x: (x.stok.kod, x.varyant_id or 0, x.pk)):
        tutar = h.tutar_try or SIFIR
        if tutar <= 0:
            raise stok_ortalama.MaliyetHatasi(f"{h.stok.kod}: sayım hareketinin maliyeti hesaplanamıyor ({sayim.no}).")
        stok_kodu, maliyet_kodu = _hesaplar(h.stok)
        fazla = h.tur == StokHareket.Tur.GIRIS
        isaret = "+" if fazla else "−"
        aciklama = f"{sayim.no} {h.stok.kod} {h.stok.ad[:48]}{_etiket(h)} {isaret}{format(h.miktar.normalize(), 'f')}"[:190]
        stok_satirlari.append(SatirGirdi(stok_kodu, "B" if fazla else "A", tutar, aciklama=aciklama))
        maliyet[maliyet_kodu]["A" if fazla else "B"] += tutar
    satirlar = list(stok_satirlari)
    for kod in sorted(maliyet):
        for taraf in ("B", "A"):
            if maliyet[kod][taraf] > 0:
                satirlar.append(SatirGirdi(kod, taraf, maliyet[kod][taraf], aciklama=f"{sayim.no} sayım {'eksiği' if taraf == 'B' else 'fazlası'} ({kod})"))
    return satirlar


def _sayim_hareketleri(sayim):
    return list(StokHareket.objects.filter(sayim_satir__sayim=sayim, silindi=False, kaynak=StokHareket.Kaynak.SAYIM).select_related("stok", "varyant", "sayim_satir").order_by("id"))


def _imzali_tutar(h) -> Decimal:
    return (h.tutar_try or SIFIR) * (1 if h.tur == StokHareket.Tur.GIRIS else -1)


@transaction.atomic
def onayla(sayim, kullanici) -> StokSayimi:
    """Girişi kapanmış sayımı onaylar: yalnız Düzelt satırları için stok düzeltme hareketi + tek muhasebe fişi. Hata olursa HİÇBİR ŞEY yazılmaz."""
    from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_ekle
    from core.services.yevmiye import YevmiyeHatasi, fis_olustur
    from core.models import YevmiyeFisi
    sayim = StokSayimi.objects.select_for_update().select_related("depo").get(pk=sayim.pk)
    if sayim.silindi or sayim.durum != Durum.KAPALI:
        raise SayimHatasi("Yalnız girişi kapatılmış sayım onaylanabilir (önce girişi kapatın).")
    veri = fark_satirlari(sayim)["satirlar"]
    duzelt = [d for d in veri if d["karar"] == Karar.DUZELT]
    if not duzelt:
        raise SayimHatasi("Onaylanacak satır yok: karar \"Düzelt\" olan satır bulunmuyor.")
    hatalar = []
    for d in duzelt:
        kod = d["stok"].kod
        if d["fark"] is None or abs(d["fark"]) < ESIK:
            hatalar.append(f"{kod}: artık fark yok / sayılmadı")
        elif d["belirsiz"]:
            hatalar.append(f"{kod}: {d['neden']}")
        else:
            try:
                _hesaplar(d["stok"])
            except SayimHatasi as e:
                hatalar.append(str(e))
    if hatalar:
        raise SayimHatasi("Onaylanamadı: " + "; ".join(hatalar[:15]) + (" …" if len(hatalar) > 15 else ""))
    hareketler = []
    for d in duzelt:
        sat, fark = d["satir"], d["fark"]
        try:
            h = hareket_ekle(stok_id=sat.stok_id, depo_id=sayim.depo_id, tarih=sayim.tarih, tur=StokHareket.Tur.GIRIS if fark > 0 else StokHareket.Tur.CIKIS, miktar=abs(fark),
                             aciklama=f"Sayım {sayim.no}", kaynak=StokHareket.Kaynak.SAYIM, kullanici=kullanici, varyant=sat.varyant_id, giris_ortalama=fark > 0, sayim_satir=sat)
        except HareketHatasi as e:
            raise SayimHatasi(f"{d['stok'].kod}: {e}")
        hareketler.append(h)
    hareketler = [StokHareket.objects.select_related("stok", "varyant", "sayim_satir").get(pk=h.pk) for h in hareketler]       # sonraki hareketler ortalamayı kaydırmış olabilir → taze oku
    for h in hareketler:
        if h.tur == StokHareket.Tur.CIKIS and eldeki_miktar(h.stok, sayim.depo, varyant=h.varyant_id) < 0:
            raise SayimHatasi(f"{h.stok.kod}: eksik düzeltmesi, sayım tarihinden sonraki hareketleri negatif stoğa düşürür; önce o hareketleri düzeltin.")
    try:
        satirlar = _fis_satirlari(sayim, hareketler)
        fis = fis_olustur(tarih=sayim.tarih, satirlar=satirlar, aciklama=f"Stok sayımı {sayim.no} — {sayim.depo.kod} {sayim.depo.ad}"[:300],
                          kaynak=YevmiyeFisi.Kaynak.STOK_SAYIM, kullanici=kullanici)
    except (YevmiyeHatasi, stok_ortalama.MaliyetHatasi) as e:
        raise SayimHatasi(str(e))
    StokHareket.objects.filter(pk__in=[h.pk for h in hareketler]).update(fis=fis)
    tutarlar = {h.sayim_satir_id: _imzali_tutar(h) for h in hareketler}
    toplu = []
    for d in veri:
        sat = d["satir"]
        if d["fark"] is not None:
            sat.onay_referans, sat.onay_sayilan, sat.onay_fark = d["referans"], d["sayilan"], d["fark"]
        if sat.pk in tutarlar:
            sat.onay_tutar_try = tutarlar[sat.pk]
        toplu.append(sat)
    StokSayimSatiri.objects.bulk_update(toplu, ["onay_referans", "onay_sayilan", "onay_fark", "onay_tutar_try"])
    sayim.durum, sayim.onaylayan, sayim.onay_zamani, sayim.fis, sayim.updated_by = Durum.ONAYLI, kullanici, timezone.now(), fis, kullanici
    sayim.save(update_fields=["durum", "onaylayan", "onay_zamani", "fis", "updated_by", "updated_at"])
    return sayim


def fis_senkronla(sayim) -> None:
    """Maliyet sonradan değiştiğinde (geç gelen fatura / geriye tarihli hareket) onaylı sayımın fişini hareket tutarlarına göre yerinde yeniler (stok_ortalama kancası)."""
    from core.services.yevmiye import YevmiyeHatasi, fis_guncelle
    sayim = StokSayimi.objects.select_related("fis").get(pk=sayim.pk)
    if sayim.durum != Durum.ONAYLI or sayim.fis is None or sayim.fis.silindi:
        return                                                         # onay sürerken (fiş henüz yok) ya da geri alınmış
    hareketler = _sayim_hareketleri(sayim)
    if not hareketler:
        return
    try:
        fis_guncelle(sayim.fis, tarih=sayim.fis.tarih, satirlar=_fis_satirlari(sayim, hareketler), aciklama=sayim.fis.aciklama, kur_usd=sayim.fis.kur_usd)
    except YevmiyeHatasi as e:
        raise stok_ortalama.MaliyetHatasi(str(e))
    tutarlar = {h.sayim_satir_id: _imzali_tutar(h) for h in hareketler}
    for sat in StokSayimSatiri.objects.filter(pk__in=tutarlar):
        sat.onay_tutar_try = tutarlar[sat.pk]
        sat.save(update_fields=["onay_tutar_try", "updated_at"])


@transaction.atomic
def geri_al(sayim, kullanici) -> StokSayimi:
    """Onaylı sayımı geri alır: sayım hareketleri silinir (soft), muhasebe fişi iptal edilir. Fazla girişi sonradan tüketilmişse reddedilir (hiçbir şey değişmez)."""
    from core.services.hareket import HareketHatasi, hareket_sil
    sayim = StokSayimi.objects.select_for_update().get(pk=sayim.pk)
    if sayim.silindi or sayim.durum != Durum.ONAYLI:
        raise SayimHatasi("Yalnız onaylı sayım geri alınabilir.")
    hareketler = _sayim_hareketleri(sayim)
    sirali = [h for h in hareketler if h.tur == StokHareket.Tur.CIKIS] + [h for h in hareketler if h.tur == StokHareket.Tur.GIRIS]      # önce eksik çıkışlar, sonra fazla girişler
    for h in sirali:
        try:
            hareket_sil(h, kullanici=kullanici, _sayim_icinden=True)
        except HareketHatasi as e:
            raise SayimHatasi(f"Geri alınamadı — {h.stok.kod}: {e}")
    fis = sayim.fis
    if fis is not None and not fis.silindi:
        from core.services.yevmiye import fis_iptal
        fis_iptal(fis, kullanici=kullanici)
    sayim.durum, sayim.geri_alan, sayim.geri_alma_zamani, sayim.updated_by = Durum.GERI_ALINDI, kullanici, timezone.now(), kullanici
    sayim.save(update_fields=["durum", "geri_alan", "geri_alma_zamani", "updated_by", "updated_at"])
    return sayim


# ----------------------------------------------------------------------------------------------------------------------- stok kartı / rapor (dilim 4)
def son_sayim(stok):
    """Stok kartı "Son sayım": kartın en son ONAYLI sayımı (sayım tarihine göre) — o belgedeki tüm (varyant) satırlarının toplamı. Yoksa None."""
    satir = (StokSayimSatiri.objects.filter(stok=stok, silindi=False, sayim__durum=Durum.ONAYLI, sayim__silindi=False, onay_sayilan__isnull=False)
             .select_related("sayim__depo").order_by("-sayim__tarih", "-sayim__onay_zamani", "-id").first())
    if satir is None:
        return None
    satirlar = list(StokSayimSatiri.objects.filter(sayim=satir.sayim, stok=stok, silindi=False, onay_sayilan__isnull=False))

    def topla(alan):
        return sum((getattr(s, alan) or SIFIR for s in satirlar), SIFIR)
    return {"sayim": satir.sayim, "tarih": satir.sayim.tarih, "depo": satir.sayim.depo, "sayilan": topla("onay_sayilan"), "referans": topla("onay_referans"), "fark": topla("onay_fark"),
            "duzeltildi": any(s.karar == Karar.DUZELT for s in satirlar), "tutar": topla("onay_tutar_try")}


def sayim_gecmisi(*, yil=None, depo_id=None, durum=None) -> list:
    """Sayım geçmişi (belge bazlı): satır / sayılan adedi, Düzelt adedi ve (onaylıda) fazla-eksik tutarı, fiş."""
    qs = StokSayimi.objects.filter(silindi=False).select_related("depo", "fis").order_by("-tarih", "-sira")
    if yil:
        qs = qs.filter(yil=int(yil))
    if depo_id:
        qs = qs.filter(depo_id=int(depo_id))
    if durum in StokSayimi.Durum.values:
        qs = qs.filter(durum=durum)
    sayimlar = list(qs)
    satirlar = defaultdict(list)
    for s in StokSayimSatiri.objects.filter(sayim__in=sayimlar, silindi=False):
        satirlar[s.sayim_id].append(s)
    sayilan_satirlar = defaultdict(set)
    for g in StokSayimGirdisi.objects.filter(satir__sayim__in=sayimlar, silindi=False, gecersiz=False).values("satir__sayim_id", "satir_id"):
        sayilan_satirlar[g["satir__sayim_id"]].add(g["satir_id"])
    sonuc = []
    for sy in sayimlar:
        ss_ = satirlar.get(sy.pk, [])
        duzelt = [s for s in ss_ if s.karar == Karar.DUZELT]
        fazla = sum((s.onay_tutar_try for s in duzelt if s.onay_tutar_try and s.onay_tutar_try > 0), SIFIR)
        eksik = sum((-s.onay_tutar_try for s in duzelt if s.onay_tutar_try and s.onay_tutar_try < 0), SIFIR)
        sonuc.append({"sayim": sy, "satir": len(ss_), "sayilan": len(sayilan_satirlar.get(sy.pk, ())), "duzelt": len(duzelt), "arastir": sum(1 for s in ss_ if s.karar == Karar.ARASTIR),
                      "fazla_tutar": fazla if sy.durum == Durum.ONAYLI else None, "eksik_tutar": eksik if sy.durum == Durum.ONAYLI else None, "fis": sy.fis})
    return sonuc


def stok_fark_gecmisi(stok) -> list:
    """Bir stokun tüm sayım satırları (tarihe göre yeni → eski): referans, sayılan, fark, karar, düzeltme tutarı. Onaylı/geri alınmış belgelerde onay anındaki değerler; açık belgelerde güncel sayılan."""
    satirlar = list(StokSayimSatiri.objects.filter(stok=stok, silindi=False, sayim__silindi=False).select_related("sayim__depo", "varyant").order_by("-sayim__tarih", "-sayim__sira", "varyant_id"))
    canli = {}
    acik_idler = [s.pk for s in satirlar if s.sayim.durum in (Durum.ACIK, Durum.KAPALI)]
    for g in StokSayimGirdisi.objects.filter(satir_id__in=acik_idler, silindi=False, gecersiz=False).values("satir_id").annotate(t=Sum("miktar")):
        canli[g["satir_id"]] = g["t"]
    sonuc = []
    for s in satirlar:
        onayli = s.sayim.durum in (Durum.ONAYLI, Durum.GERI_ALINDI)
        sonuc.append({"satir": s, "sayim": s.sayim, "varyant": s.varyant,
                      "referans": s.onay_referans if onayli else None, "sayilan": s.onay_sayilan if onayli else canli.get(s.pk), "fark": s.onay_fark if onayli else None,
                      "karar": s.karar, "tutar": s.onay_tutar_try if s.sayim.durum == Durum.ONAYLI else None, "devam": not onayli})
    return sonuc
