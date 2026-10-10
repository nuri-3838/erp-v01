"""ÜRETİM modülü servis katmanı — İş İstasyonu + Operasyon (rota) modeli.

Bağımsız, sıfırdan kurulan bir Stok↔Stok rota modeli. Bitmiş bir ürün, farklı iş
istasyonlarında art arda yapılan operasyonlarla adım adım ortaya çıkar: her Operasyon,
bir istasyonda, bir/daha fazla GİRDİ stoktan TEK bir ÇIKTI stok üretir (oranlı dönüşüm).

Üç katman:
- Operasyon/OperasyonGirdi: rota TANIMI (statik).
- UretimEmri: "N adet [hedef ürün] istiyorum" tetikleyicisi — zinciri (ihtiyac_hesapla ile
  aynı özyinelemeli algoritma) hesaplayıp zincirdeki HER operasyon için ayrı bir TASLAK
  OperasyonKaydi açar (tek atomik işlemde, hepsi aynı depoyu kullanır). Kendi başına stok
  hareketi ÜRETMEZ.
- OperasyonKaydi/OperasyonKaydiGirdi: bir operasyonun GERÇEKTEN çalıştırılma kaydı — her
  istasyon KENDİ kaydını kendi zamanında onaylar (Üretim Emri'nden bağımsız), onay anında
  StokHareket (Kaynak=URETIM) yazılır. Hiçbir adım YevmiyeFisi/YevmiyeSatir'e dokunmaz —
  maliyetin muhasebeye yansıtılması ay sonu mali müşavirin elle yapacağı ayrı bir iştir
  (bkz. docs/ERP_v0.1_kapsam.md v0.4).
"""
from __future__ import annotations

import re

from decimal import ROUND_CEILING, Decimal

from django.db import IntegrityError, connection, transaction
from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (
    Depo, IsIstasyonu, IstasyonEmri, Operasyon, OperasyonCikti, OperasyonGirdi, OperasyonKaydi, OperasyonKaydiCikti, OperasyonKaydiGirdi,
    Stok, StokHareket, TeklifSiparis, UretimEmri, UretimEmriKalemi, UretimEmriRevizyon,
)
from core.sayi import SayiHatasi, parse_tr, yuvarla
from core.services import stok_ayirma
from core.services.hareket import HareketHatasi, hareket_ekle


class UretimHatasi(ValueError):
    """Üretim modülü kural ihlali (Türkçe mesaj)."""


def _sayi_coz(deger, hata_mesaji):
    if isinstance(deger, Decimal):
        return deger
    try:
        return parse_tr(deger)
    except SayiHatasi:
        raise UretimHatasi(hata_mesaji)


def _yukari_yuvarla(deger: Decimal) -> Decimal:
    """Tam sayıya YUKARI yuvarlar (6 ondalıkta temizledikten sonra: 1,0000000001 gibi bölme artıkları fazladan bir çalıştırma doğurmasın)."""
    return yuvarla(deger, 6).to_integral_value(rounding=ROUND_CEILING)


def _boy_birimli_mi(stok: Stok) -> bool:
    """Stoğun ÜRETİM birimi BOY mu (kesimde tam sayı olması gereken profil)."""
    b = stok.uretim_birimi
    return (b.ad or "").strip().upper() == "BOY" or (b.kisa_ad or "").strip().upper() == "BOY"


TAM_BOY_HATASI = "Bu kesim tam boy kesilmelidir; girdi miktarı tam sayı olmalı."


# === İş İstasyonları (Depo servisiyle birebir aynı desen) ===

def aktif_istasyonlar():
    return IsIstasyonu.objects.filter(silindi=False).order_by("kod")


def _istasyon_dogrula(kod, ad, *, haric_pk=None):
    kod = buyuk_harf_tr((kod or "").strip())
    ad = buyuk_harf_tr((ad or "").strip())
    if not kod:
        raise UretimHatasi("İş istasyonu kodu boş olamaz.")
    if not ad:
        raise UretimHatasi("İş istasyonu adı boş olamaz.")
    for alan, deger, etiket in (("kod", kod, "kod"), ("ad", ad, "ad")):
        qs = IsIstasyonu.objects.filter(silindi=False, **{alan: deger})
        if haric_pk is not None:
            qs = qs.exclude(pk=haric_pk)
        if qs.exists():
            raise UretimHatasi(f"Bu {etiket} zaten kayıtlı: {deger}")
    return kod, ad


def istasyon_olustur(*, kod, ad, kullanici=None) -> IsIstasyonu:
    kod, ad = _istasyon_dogrula(kod, ad)
    return IsIstasyonu.objects.create(kod=kod, ad=ad, created_by=kullanici, updated_by=kullanici)


def istasyon_guncelle(istasyon: IsIstasyonu, *, kod, ad, kullanici=None) -> IsIstasyonu:
    if istasyon.silindi:
        raise UretimHatasi("Silinmiş iş istasyonu düzenlenemez.")
    kod, ad = _istasyon_dogrula(kod, ad, haric_pk=istasyon.pk)
    istasyon.kod, istasyon.ad = kod, ad
    istasyon.updated_by = kullanici
    istasyon.save(update_fields=["kod", "ad", "updated_by", "updated_at"])
    return istasyon


def istasyon_sil(istasyon: IsIstasyonu, kullanici=None) -> IsIstasyonu:
    if istasyon.silindi:
        return istasyon
    if istasyon.operasyonlar.filter(silindi=False).exists():
        raise UretimHatasi("Bu istasyona bağlı operasyon tanımı var; silinemez.")
    istasyon.silindi = True
    istasyon.silindi_at = timezone.now()
    istasyon.updated_by = kullanici
    istasyon.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return istasyon


# === Operasyon Tanımları ===

def aktif_operasyonlar():
    return (Operasyon.objects.filter(silindi=False)
            .select_related("istasyon", "cikti")
            .annotate(girdi_sayisi=Count("girdiler", filter=Q(girdiler__silindi=False), distinct=True),
                      yan_cikti_sayisi=Count("ciktilar", filter=Q(ciktilar__silindi=False) & ~Q(ciktilar__sira=0), distinct=True))
            .order_by("istasyon__kod", "cikti__kod"))


# === Operasyon Tanımları LİSTESİ: seri (A/C/ortak), bağlantı, filtre, sekme, özet — tek yükleme, bellekte, sabit sorgu sayısı ===

SERI_ETIKET = {"A": "A tipi", "C": "C tipi", "ORTAK": "Ortak", "DIGER": "Diğer", "BAGLANTISIZ": "Bağlantısız"}
SERI_KOK_ONEKI = {"A": "152-10-", "C": "152-22-"}      # bitmiş ürün (kök) kodu öneki → seri


def operasyonlari_yukle() -> list:
    """Aktif operasyonlar + girdiler + çıktı satırları (OperasyonCikti) + stok/birim bilgisi: TOPLAM 4 sorgu (operasyon sayısından bağımsız)."""
    return list(
        Operasyon.objects.filter(silindi=False)
        .select_related("istasyon", "cikti__uretim_birimi")
        .prefetch_related(
            Prefetch("girdiler", queryset=OperasyonGirdi.objects.filter(silindi=False).select_related("girdi__uretim_birimi").order_by("sira", "pk")),
            Prefetch("ciktilar", queryset=OperasyonCikti.objects.filter(silindi=False).select_related("stok").order_by("sira", "pk")))
        .order_by("istasyon__kod", "cikti__kod"))


def tanim_ciktilari(operasyon: Operasyon) -> list:
    """Tanımın aktif çıktı satırları (OperasyonCikti; sıra 0 = referans/ana çıktı). ``ciktilar`` prefetch edilmişse sorgu yok."""
    return [c for c in operasyon.ciktilar.all() if not c.silindi]


def ek_ciktilar(operasyon: Operasyon) -> list:
    """Referans dışındaki çıktı satırları: ÜRET'te yan çıktılar, PARÇALA'da diğer çıktılar (``miktar``, ``boy_mm``, ``stok`` alanlı)."""
    return [c for c in tanim_ciktilari(operasyon) if c.sira != 0]


def ureten_operasyon(stok) -> Operasyon | None:
    """``stok``u üreten aktif tanım — OperasyonCikti.surucu satırı üzerinden (ÜRET'te ana çıktı, PARÇALA'da her çıktı). Yoksa None."""
    c = (OperasyonCikti.objects.filter(stok=stok, silindi=False, surucu=True, operasyon__silindi=False)
         .select_related("operasyon__istasyon", "operasyon__cikti").first())
    return c.operasyon if c else None


def operasyon_serileri(operasyonlar=None) -> dict:
    """{operasyon.pk: "A" | "C" | "ORTAK" | "DIGER" | "BAGLANTISIZ"}. Her operasyonun çıktısından (ana + yan çıktılar) zincir YUKARI izlenir: bir stoğu
    girdi ya da yan çıktı olarak kullanan operasyonların ana çıktısına geçilir → … → artık hiçbir operasyonun kullanmadığı uç stok. Uç stok kodu
    152-10-* ise kök A, 152-22-* ise C; ulaşılan kökler arasında ikisi de varsa ORTAK, başka 152-* kök varsa DIGER, hiç kök yoksa BAGLANTISIZ.
    Tüm aktif operasyonlar için TEK seferde, bellekte (``operasyonlar`` verilirse sorgu yok)."""
    operasyonlar = operasyonlari_yukle() if operasyonlar is None else operasyonlar
    kullanan = {}                                          # stok pk -> {operasyon pk} (girdi VEYA yan çıktı olarak kullanan)
    cikti_of = {op.pk: op.cikti_id for op in operasyonlar}
    kod_of = {op.cikti_id: op.cikti.kod for op in operasyonlar}
    for op in operasyonlar:
        for g in op.girdiler.all():
            kullanan.setdefault(g.girdi_id, set()).add(op.pk)
            kod_of.setdefault(g.girdi_id, g.girdi.kod)
        for y in ek_ciktilar(op):
            kullanan.setdefault(y.stok_id, set()).add(op.pk)
            kod_of.setdefault(y.stok_id, y.stok.kod)
    kok_memo = {}

    def kokler(basla):
        gorulen, yigin, bulunan = set(), list(basla), set()
        while yigin:
            stok = yigin.pop()
            if stok in gorulen:
                continue
            gorulen.add(stok)
            sonraki = kullanan.get(stok)
            if not sonraki:                                # uç stok
                if kod_of.get(stok, "").startswith("152-"):
                    bulunan.add(kod_of[stok])
                continue
            yigin.extend(cikti_of[o] for o in sonraki)
        return bulunan

    sonuc = {}
    for op in operasyonlar:
        baslar = (op.cikti_id,) + tuple(y.stok_id for y in ek_ciktilar(op))
        anahtar = frozenset(baslar)
        if anahtar not in kok_memo:
            kok_memo[anahtar] = kokler(baslar)
        k = kok_memo[anahtar]
        a = any(x.startswith(SERI_KOK_ONEKI["A"]) for x in k)
        c = any(x.startswith(SERI_KOK_ONEKI["C"]) for x in k)
        sonuc[op.pk] = "ORTAK" if (a and c) else "A" if a else "C" if c else ("DIGER" if k else "BAGLANTISIZ")
    return sonuc


def _girdi_ozeti(op) -> dict:
    """Liste 'Girdi özeti': tek girdili → '1 BOY 150-10-0007 → 3 adet' (+ yan çıktı); çok girdili → ilk 2 kod + '+N girdi' (hover'da tam liste)."""
    from core.sayi import format_tr, yuvarla
    def sade(d):                                   # tam sayı ondalıksız; kesirli 6 ondalığa kadar (sondaki sıfırlar kırpılır)
        q = yuvarla(d, 6).normalize()
        return format_tr(q, max(0, -q.as_tuple().exponent))
    girdiler = list(op.girdiler.all())
    yanlar = ek_ciktilar(op)
    birim = (op.cikti.uretim_birimi.ad or "").lower()
    tam_liste = "\n".join(f"{sade(g.miktar)} {g.girdi.uretim_birimi.kisa_ad or g.girdi.uretim_birimi.ad} {g.girdi.kod}" for g in girdiler)
    if op.tur == Operasyon.Tur.PARCALA:                                      # '1 BOY 150-20-0002 → 64 × 151-20-0014 + 64 × 151-20-0015'
        g = girdiler[0] if girdiler else None
        ana = (f"{sade(g.miktar)} {g.girdi.uretim_birimi.kisa_ad or g.girdi.uretim_birimi.ad} {g.girdi.kod} → " if g else "") + \
              " + ".join(f"{sade(c.miktar)} × {c.stok.kod}" for c in tanim_ciktilari(op))
        return {"ana": ana, "yan": [], "tam_liste": tam_liste, "girdi_sayisi": len(girdiler)}
    yan_metin = [f"+{sade(y.miktar)} × {y.stok.kod}" for y in yanlar]
    if len(girdiler) == 1:
        g = girdiler[0]
        ana = f"{sade(g.miktar)} {g.girdi.uretim_birimi.kisa_ad or g.girdi.uretim_birimi.ad} {g.girdi.kod} → {sade(op.cikti_miktar)} {birim}"
    else:
        ilk = ", ".join(g.girdi.kod for g in girdiler[:2])
        ana = ilk + (f" +{len(girdiler) - 2} girdi" if len(girdiler) > 2 else "")
    return {"ana": ana, "yan": yan_metin, "tam_liste": tam_liste, "girdi_sayisi": len(girdiler)}


def operasyon_liste(*, ara="", istasyon=None, seri="", tam_boy=False, yan_cikti=False, baglantisiz=False, kullanan=None) -> dict:
    """Operasyon Tanımları listesi — filtre/sekme/özet/gruplama (hepsi bellekte, tek yükleme). Döner:
      satirlar (tüm filtreler dâhil), sekmeler (istasyon sekmeleri; sayılar İSTASYON HARİÇ diğer filtrelerden SONRA), tum_sayi, ozet (filtresiz
      toplamlar), gruplar (istasyon seçili değilse istasyona göre), kullanan_op (filtre rozeti için)."""
    from core.metin import buyuk_harf_tr
    ops = operasyonlari_yukle()
    seriler = operasyon_serileri(ops)
    tuketen = {}                                           # stok pk -> [operasyon] (girdi olarak kullanan)
    for op in ops:
        for g in op.girdiler.all():
            tuketen.setdefault(g.girdi_id, []).append(op)
    kullanan_op = next((o for o in ops if str(o.pk) == str(kullanan)), None) if kullanan else None
    kullananlar = {o.pk for o in tuketen.get(kullanan_op.cikti_id, [])} if kullanan_op else None

    def zenginlestir(op):
        kul = tuketen.get(op.cikti_id, [])
        kok = op.cikti.kod.startswith("152-") and not kul
        return {
            "op": op, "seri": seriler[op.pk], "seri_etiket": SERI_ETIKET[seriler[op.pk]], "baglantisiz": seriler[op.pk] == "BAGLANTISIZ",
            "tam_boy": op.tam_calistirma, "yan_sayisi": len(ek_ciktilar(op)) if op.tur == Operasyon.Tur.URET else 0,
            "parcala": op.tur == Operasyon.Tur.PARCALA, "cikti_sayisi": len(tanim_ciktilari(op)), "kullanan_sayisi": len({o.pk for o in kul}),
            "bitmis_urun": kok, "ozet": _girdi_ozeti(op),
            "ara_metin": buyuk_harf_tr(" ".join([op.cikti.kod, op.cikti.ad] + [f"{g.girdi.kod} {g.girdi.ad}" for g in op.girdiler.all()]
                                                + [f"{y.stok.kod} {y.stok.ad}" for y in ek_ciktilar(op)]))}

    satirlar = [zenginlestir(op) for op in ops]
    ozet = {"toplam": len(satirlar), "tam_boy": sum(1 for r in satirlar if r["tam_boy"]),
            "yan_cikti": sum(1 for r in satirlar if r["yan_sayisi"]), "baglantisiz": sum(1 for r in satirlar if r["baglantisiz"])}
    seri_dagilimi = {k: sum(1 for r in satirlar if r["seri"] == k) for k in SERI_ETIKET}

    aranan = buyuk_harf_tr((ara or "").strip())
    seri = (seri or "").upper()
    diger = [r for r in satirlar
             if (not aranan or aranan in r["ara_metin"])
             and (not seri or r["seri"] == ("BAGLANTISIZ" if seri == "BAGLANTISIZ" else seri))
             and (not tam_boy or r["tam_boy"]) and (not yan_cikti or r["yan_sayisi"]) and (not baglantisiz or r["baglantisiz"])
             and (kullananlar is None or r["op"].pk in kullananlar)]
    istasyonlar = {}
    for r in diger:
        i = r["op"].istasyon
        istasyonlar.setdefault(i.pk, {"pk": i.pk, "kod": i.kod, "ad": i.ad, "sayi": 0})["sayi"] += 1
    sekmeler = sorted(istasyonlar.values(), key=lambda x: x["kod"])
    secili = next((x for x in sekmeler if str(x["pk"]) == str(istasyon)), None) if istasyon else None
    liste = [r for r in diger if secili is None or r["op"].istasyon_id == secili["pk"]]
    gruplar = []
    if secili is None:
        for x in sekmeler:
            gruplar.append({**x, "satirlar": [r for r in liste if r["op"].istasyon_id == x["pk"]]})
    return {"satirlar": liste, "sekmeler": sekmeler, "tum_sayi": len(diger), "secili_istasyon": secili, "gruplar": gruplar,
            "ozet": ozet, "seri_dagilimi": seri_dagilimi, "kullanan_op": kullanan_op}


def operasyon_girdileri(operasyon: Operasyon):
    return (operasyon.girdiler.filter(silindi=False)
            .select_related("girdi").order_by("sira", "pk"))


def operasyonlu_stok_idler():
    """Aktif bir tanımın ÜRETTİĞİ stoklar (OperasyonCikti.surucu)."""
    return OperasyonCikti.objects.filter(silindi=False, surucu=True, operasyon__silindi=False).values_list("stok_id", flat=True)


def kok_operasyonlar():
    """Zincirin EN ÜSTÜ: çıktısı başka hiçbir aktif operasyonun girdisi olmayan aktif
    operasyonlar (bitmiş ürünler). ÜRETİM > Ürün Ağacı ekranının açılış listesi —
    salt-okunur, hiçbir kayıt üretmez."""
    girdi_idler = (OperasyonGirdi.objects
                   .filter(silindi=False, operasyon__silindi=False)
                   .values_list("girdi_id", flat=True))
    return (aktif_operasyonlar().filter(cikti__silindi=False)
            .exclude(cikti_id__in=girdi_idler))


def _cikti_coz(cikti_id) -> Stok:
    cikti = Stok.objects.filter(pk=cikti_id, silindi=False, uretim_urunu=True).first()
    if not cikti:
        raise UretimHatasi("Çıktı stok bulunamadı.")
    return cikti


def _istasyon_coz(istasyon_id) -> IsIstasyonu:
    istasyon = IsIstasyonu.objects.filter(pk=istasyon_id, silindi=False).first()
    if not istasyon:
        raise UretimHatasi("İş istasyonu bulunamadı.")
    return istasyon


def _girdi_satirlarini_dogrula(cikti, satirlar):
    """satirlar: [(Stok, Decimal), ...]. En az 1 satır, girdi tekrarsız, çıktı kendi
    girdisi olamaz, miktar > 0."""
    if not satirlar:
        raise UretimHatasi("En az bir girdi satırı gerekli.")
    gorulen = set()
    for girdi, miktar in satirlar:
        if girdi.pk == cikti.pk:
            raise UretimHatasi("Çıktı kendi girdisi olamaz.")
        if girdi.pk in gorulen:
            raise UretimHatasi(f"{girdi.kod} birden fazla satırda tekrarlanamaz.")
        gorulen.add(girdi.pk)
        if miktar <= 0:
            raise UretimHatasi("Girdi miktarı sıfırdan büyük olmalı.")


_KORU = KORU = object()          # operasyon_guncelle: "bu alana dokunma"


def operasyon_yan_ciktilari(operasyon: Operasyon):
    """ÜRET tanımın yan çıktı satırları (OperasyonCikti, sürücü olmayan) — queryset; PARÇALA'da boş (yan çıktı kavramı yok)."""
    return operasyon.ciktilar.filter(silindi=False, surucu=False).select_related("stok").order_by("sira", "pk")


def ana_cikti_payi(cikti_miktar, boy_mm, yanlar) -> Decimal:
    """Bir çalıştırmada ANA çıktının girdi maliyetinden/tüketiminden alacağı pay (0-1): ağırlık = miktar × boy_mm (ana + yan çıktılar).
    Onaydaki maliyet paylaştırmasıyla AYNI kural: yan çıktı yoksa ya da herhangi bir ağırlık eksik/sıfırsa ana çıktı %100. ``yanlar``:
    ``miktar`` ve ``boy_mm`` alanlı satırlar (OperasyonCikti yan satırları), bir çalıştırma başına miktarlarla."""
    yanlar = list(yanlar)
    if not yanlar:
        return Decimal("1")
    ana = (cikti_miktar * boy_mm) if boy_mm else Decimal("0")
    agirliklar = [ana] + [(y.miktar * y.boy_mm) if y.boy_mm else Decimal("0") for y in yanlar]
    if any(a <= 0 for a in agirliklar):
        return Decimal("1")
    return ana / sum(agirliklar, Decimal("0"))


def _boy_coz(deger, mesaj):
    if deger in (None, ""):
        return None
    b = _sayi_coz(deger, mesaj)
    if b <= 0:
        raise UretimHatasi("Boy (mm) sıfırdan büyük olmalı.")
    return b


def _yan_ciktilari_dogrula(cikti, satirlar, yan_ciktilar, boy_mm):
    """yan_ciktilar: [(Stok, miktar Decimal, boy_mm Decimal), ...]. Yan çıktı: ana çıktıyla aynı olamaz, operasyonun kendi girdisi
    olamaz, aynı operasyonda tekrar edemez, miktar ve boy > 0; yan çıktısı olan operasyonda ANA çıktının boyu (mm) zorunlu."""
    gorulen = set()
    girdi_idler = {g.pk for g, _ in satirlar}
    for stok, miktar, boy in yan_ciktilar:
        if stok.pk == cikti.pk:
            raise UretimHatasi("Yan çıktı, ana çıktıyla aynı stok olamaz.")
        if stok.pk in girdi_idler:
            raise UretimHatasi(f"{stok.kod} operasyonun girdisi; aynı zamanda yan çıktısı olamaz.")
        if stok.pk in gorulen:
            raise UretimHatasi(f"{stok.kod} aynı operasyonda birden fazla yan çıktı olarak tekrarlanamaz.")
        gorulen.add(stok.pk)
        if not stok.uretim_urunu:
            raise UretimHatasi(f"{stok.kod} üretim ürünü değil; yan çıktı olamaz.")
        if miktar is None or miktar <= 0:
            raise UretimHatasi("Yan çıktı miktarı sıfırdan büyük olmalı.")
        if boy is None or boy <= 0:
            raise UretimHatasi(f"{stok.kod} yan çıktısı için boy (mm) girin (maliyet boy oranına göre paylaştırılır).")
    if yan_ciktilar and (boy_mm is None or boy_mm <= 0):
        raise UretimHatasi("Yan çıktısı olan operasyonda ana çıktının boyu (mm) zorunludur.")


def operasyon_ciktilari(operasyon: Operasyon):
    """Tanımın çıktı satırları (OperasyonCikti): sıra 0 referans/ana çıktı, sonra yan (ÜRET) ya da diğer (PARÇALA) çıktılar."""
    return operasyon.ciktilar.filter(silindi=False).select_related("stok").order_by("sira", "pk")


def _ciktilari_esitle(operasyon: Operasyon, kullanici, satirlar):
    """Tanımın çıktı satırlarını (OperasyonCikti — çıktıların TEK kaynağı) yeniden yazar: mevcut aktif satırlar soft-delete, ``satirlar``
    [(stok, miktar, boy_mm, yuzde, surucu), ...] sırasıyla (sıra 0 = referans) yazılır. ``Operasyon.cikti``/``cikti_miktar``/``boy_mm`` referans
    satırın (sıra 0) kopyasıdır (liste/form/kayıt kolaylığı; tanım servisleri birlikte günceller)."""
    operasyon.ciktilar.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    for i, (stok, miktar, boy, yuzde, surucu) in enumerate(satirlar):
        OperasyonCikti.objects.create(operasyon=operasyon, stok=stok, miktar=miktar, boy_mm=boy, yuzde=yuzde, sira=i * 10, surucu=surucu,
                                      created_by=kullanici, updated_by=kullanici)


def _uret_cikti_satirlari(operasyon, yanlar):
    """ÜRET: [(ana, cm, boy, None, True)] + yan çıktılar (sürücü değil)."""
    return [(operasyon.cikti, operasyon.cikti_miktar, operasyon.boy_mm, None, True)] + [(stok, miktar, boy, None, False) for stok, miktar, boy in yanlar]


def cikti_agirliklari(operasyon, ciktilar, miktarlar=None) -> dict:
    """{stok_id: paylaştırma ağırlığı} — ``pay_anahtari``ne göre: BOY = miktar × boy_mm (boy yoksa 0), ESIT = miktar (adet başı eşit),
    YUZDE = tanımdaki yüzde (miktardan bağımsız). ``miktarlar`` ({stok_id: GELEN miktar}) verilirse BOY/ESIT ağırlığı gelen adetten hesaplanır
    (fire malzemeyi gelen parçalara yayar); YÜZDE sabittir. ``ciktilar``: ``stok_id``, ``miktar``, ``boy_mm``, ``yuzde`` alanlı satırlar."""
    anahtar = operasyon.pay_anahtari
    agirlik = {}
    for c in ciktilar:
        m = miktarlar.get(c.stok_id, c.miktar) if miktarlar is not None else c.miktar
        if anahtar == Operasyon.PayAnahtari.YUZDE:
            agirlik[c.stok_id] = c.yuzde or Decimal("0")
        elif anahtar == Operasyon.PayAnahtari.ESIT:
            agirlik[c.stok_id] = m
        else:
            agirlik[c.stok_id] = (m * c.boy_mm) if c.boy_mm else Decimal("0")
    return agirlik


def cikti_paylari(operasyon, ciktilar) -> dict:
    """{stok_id: pay (0-1)} — bir çalıştırmada her çıktının girdi maliyetinden/tüketiminden alacağı pay (``ana_cikti_payi`` kuralının
    genellemesi): tek çıktı ya da ağırlıklardan biri eksik/sıfırsa referans (sıra 0) %100, diğerleri 0."""
    ciktilar = list(ciktilar)
    if not ciktilar:
        return {}
    agirlik = cikti_agirliklari(operasyon, ciktilar)
    idler = [c.stok_id for c in ciktilar]
    if len(idler) <= 1 or any(agirlik[i] <= 0 for i in idler):
        return {i: (Decimal("1") if k == 0 else Decimal("0")) for k, i in enumerate(idler)}
    toplam = sum((agirlik[i] for i in idler), Decimal("0"))
    return {i: agirlik[i] / toplam for i in idler}


def _tam_boy_mi(satirlar) -> bool:
    """Tam boy VARSAYILANI: girdilerden en az birinin üretim birimi BOY ise işaretli (kullanıcı formda kaldırabilir)."""
    return any(_boy_birimli_mi(girdi) for girdi, _ in satirlar)


def boy_stok_idleri():
    """Üretim birimi BOY olan stokların pk listesi (Operasyon formunda 'Tam boy: Evet/Hayır' rozetini JS ile göstermek için)."""
    return [s.pk for s in Stok.objects.filter(silindi=False).select_related("uretim_birimi") if _boy_birimli_mi(s)]


def stok_bilgi_haritasi() -> dict:
    """{stok pk: {"k": kod, "b": üretim birimi kısa adı, "boy": BOY birimli mi}} — Operasyon formunda satır birimi / özet / tam boy rozeti (JS)."""
    return {s.pk: {"k": s.kod, "b": s.uretim_birimi.kisa_ad or s.uretim_birimi.ad, "boy": _boy_birimli_mi(s)}
            for s in Stok.objects.filter(silindi=False).select_related("uretim_birimi")}


YUZDE_TOLERANS = Decimal("0.0001")


def _pay_anahtari_coz(deger):
    deger = (deger or Operasyon.PayAnahtari.BOY)
    if deger not in Operasyon.PayAnahtari.values:
        raise UretimHatasi("Geçersiz maliyet pay anahtarı.")
    return deger


def _parcala_ciktilari_dogrula(satirlar, ciktilar, pay_anahtari, haric_op=None):
    """PARÇALA: tek girdi; ``ciktilar`` [(Stok, miktar, boy_mm|None, yuzde|None), ...] en az 1 satır, ilk satır referans. Her çıktı üretim ürünü,
    girdiyle aynı değil, tekrarsız, miktar > 0; BOY anahtarında her çıktının boyu, YÜZDE'de her çıktının yüzdesi (toplam 100) zorunlu; bir çıktı
    başka bir aktif tanımdan üretiliyorsa (sürücü) reddedilir. Döner: [(stok, miktar, boy, yuzde)] temizlenmiş."""
    if len(satirlar) != 1:
        raise UretimHatasi("PARÇALA tanımında tek girdi olur (1 girdi → N çıktı).")
    if not ciktilar:
        raise UretimHatasi("PARÇALA tanımında en az bir çıktı satırı gerekli.")
    girdi = satirlar[0][0]
    gorulen, temiz = set(), []
    for satir in ciktilar:
        stok, miktar, boy, yuzde = (list(satir) + [None, None])[:4]
        if stok is None:
            raise UretimHatasi("Çıktı seçin.")
        if stok.pk == girdi.pk:
            raise UretimHatasi(f"{stok.kod} operasyonun girdisi; aynı zamanda çıktısı olamaz.")
        if stok.pk in gorulen:
            raise UretimHatasi(f"{stok.kod} aynı tanımda birden fazla çıktı olarak tekrarlanamaz.")
        gorulen.add(stok.pk)
        if not stok.uretim_urunu:
            raise UretimHatasi(f"{stok.kod} üretim ürünü değil; çıktı olamaz.")
        m = _sayi_coz(miktar, f"{stok.kod}: çıktı miktarı geçerli bir sayı olmalı.")
        if m <= 0:
            raise UretimHatasi(f"{stok.kod}: çıktı miktarı sıfırdan büyük olmalı.")
        b = _boy_coz(boy, f"{stok.kod}: boy (mm) geçerli bir sayı olmalı.")
        y = None if yuzde in (None, "") else _sayi_coz(yuzde, f"{stok.kod}: maliyet payı (%) geçerli bir sayı olmalı.")
        if y is not None and not (0 < y <= 100):
            raise UretimHatasi(f"{stok.kod}: maliyet payı (%) 0 ile 100 arasında olmalı.")
        if pay_anahtari == Operasyon.PayAnahtari.BOY and b is None:
            raise UretimHatasi(f"{stok.kod} için boy (mm) girin (maliyet boy oranına göre paylaştırılır).")
        if pay_anahtari == Operasyon.PayAnahtari.YUZDE and y is None:
            raise UretimHatasi(f"{stok.kod} için maliyet payı (%) girin.")
        ureten = ureten_operasyon(stok)
        if ureten is not None and (haric_op is None or ureten.pk != haric_op.pk):
            raise UretimHatasi(f"{stok.kod} zaten {ureten.cikti.kod} tanımından üretiliyor; bir stok tek bir tanımdan üretilir.")
        temiz.append((stok, m, b, y))
    if pay_anahtari == Operasyon.PayAnahtari.YUZDE:
        toplam = sum((y for _s, _m, _b, y in temiz), Decimal("0"))
        if abs(toplam - 100) > YUZDE_TOLERANS:
            raise UretimHatasi(f"Maliyet payları toplamı %100 olmalı (girilen: %{toplam.normalize():f}).")
    return temiz


@transaction.atomic
def operasyon_olustur(*, istasyon_id, cikti_id=None, cikti_miktar=None, satirlar, kullanici=None, boy_mm=None, yan_ciktilar=None, tam_boy=None,
                      tur=None, ciktilar=None, pay_anahtari=None) -> Operasyon:
    """``tam_boy`` None ise varsayılan: BOY birimli girdi varsa True; True/False verilirse kullanıcının seçimi.
    ``tur`` ÜRET (varsayılan): ``cikti_id`` + ``cikti_miktar`` (+ ``boy_mm``, ``yan_ciktilar``). PARÇALA: ``ciktilar`` [(Stok, miktar, boy_mm, yuzde)]
    — ilk satır referans çıktı (``cikti_id``/``cikti_miktar``/``boy_mm`` verilmişse onunla uyuşmalı, verilmezse oradan türetilir); ``pay_anahtari``
    BOY / ESIT / YUZDE (varsayılan BOY). PARÇALA'nın HER çıktısı bu tanımdan üretilir sayılır (stok başına tek aktif üreten tanım)."""
    istasyon = _istasyon_coz(istasyon_id)
    tur = tur or Operasyon.Tur.URET
    if tur not in Operasyon.Tur.values:
        raise UretimHatasi("Geçersiz operasyon türü.")
    if tur == Operasyon.Tur.PARCALA:
        anahtar = _pay_anahtari_coz(pay_anahtari)
        if not satirlar:
            raise UretimHatasi("En az bir girdi satırı gerekli.")
        temiz = _parcala_ciktilari_dogrula(satirlar, ciktilar or [], anahtar)
        referans = temiz[0]
        if cikti_id not in (None, "") and str(referans[0].pk) != str(cikti_id):
            raise UretimHatasi("PARÇALA tanımında referans çıktı, çıktı listesinin ilk satırıdır.")
        cikti, cm, boy = referans[0], referans[1], referans[2]
        _girdi_satirlarini_dogrula(cikti, satirlar)
        operasyon = Operasyon.objects.create(
            istasyon=istasyon, cikti=cikti, cikti_miktar=cm, boy_mm=boy, tur=tur, pay_anahtari=anahtar,
            tam_calistirma=_tam_boy_mi(satirlar) if tam_boy is None else bool(tam_boy), created_by=kullanici, updated_by=kullanici)
        for i, (girdi, miktar) in enumerate(satirlar, start=1):
            OperasyonGirdi.objects.create(operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10, created_by=kullanici, updated_by=kullanici)
        _ciktilari_esitle(operasyon, kullanici, [(st, m, b, y, True) for st, m, b, y in temiz])
        return operasyon
    cikti = _cikti_coz(cikti_id)
    if ureten_operasyon(cikti) is not None:
        raise UretimHatasi("Bu çıktı için zaten aktif bir operasyon tanımlı.")
    cm = _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı.")
    if cm <= 0:
        raise UretimHatasi("Çıktı miktarı sıfırdan büyük olmalı.")
    _girdi_satirlarini_dogrula(cikti, satirlar)
    boy = _boy_coz(boy_mm, "Ana çıktı boyu (mm) geçerli bir sayı olmalı.")
    yan_ciktilar = list(yan_ciktilar or [])
    _yan_ciktilari_dogrula(cikti, satirlar, yan_ciktilar, boy)
    operasyon = Operasyon.objects.create(
        istasyon=istasyon, cikti=cikti, cikti_miktar=cm, tam_calistirma=_tam_boy_mi(satirlar) if tam_boy is None else bool(tam_boy), boy_mm=boy,
        created_by=kullanici, updated_by=kullanici)
    for i, (girdi, miktar) in enumerate(satirlar, start=1):
        OperasyonGirdi.objects.create(
            operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    _ciktilari_esitle(operasyon, kullanici, _uret_cikti_satirlari(operasyon, yan_ciktilar))
    return operasyon


@transaction.atomic
def operasyon_guncelle(operasyon: Operasyon, *, istasyon_id, cikti_miktar=None, satirlar,
                       kullanici=None, boy_mm=_KORU, yan_ciktilar=None, tam_boy=None, tur=None, ciktilar=None, pay_anahtari=None) -> Operasyon:
    """``tam_boy`` verilmezse (None) mevcut seçim KORUNUR (girdi birimi değişse de otomatik değişmez). ``boy_mm`` verilmezse ana çıktı boyu, ``yan_ciktilar``
    verilmezse (None) yan çıktılar KORUNUR; [] verilirse yan çıktılar silinir. ``tur`` verilirse ÜRET ↔ PARÇALA geçişi yapılabilir (onaylı kayıtlar
    snapshot'larıyla etkilenmez). PARÇALA'da ``ciktilar`` [(Stok, miktar, boy_mm, yuzde)] — ilk satır referans (tanımın çıktısı, değiştirilemez); verilmezse
    mevcut çıktı satırları korunur (referans miktarı ``cikti_miktar`` ile güncellenir)."""
    if operasyon.silindi:
        raise UretimHatasi("Silinmiş operasyon düzenlenemez.")
    istasyon = _istasyon_coz(istasyon_id)
    yeni_tur = tur or operasyon.tur
    if yeni_tur not in Operasyon.Tur.values:
        raise UretimHatasi("Geçersiz operasyon türü.")
    if yeni_tur == Operasyon.Tur.PARCALA:
        anahtar = _pay_anahtari_coz(pay_anahtari or (operasyon.pay_anahtari if operasyon.tur == Operasyon.Tur.PARCALA else None))
        if ciktilar is None:
            if operasyon.tur != Operasyon.Tur.PARCALA:
                raise UretimHatasi("PARÇALA'ya geçerken çıktı satırlarını girin.")
            mevcut = [(c.stok, c.miktar, c.boy_mm, c.yuzde) for c in tanim_ciktilari(operasyon)]
            if cikti_miktar not in (None, "") and mevcut:
                mevcut[0] = (mevcut[0][0], _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı."), mevcut[0][2], mevcut[0][3])
            ciktilar = mevcut
        if not satirlar:
            raise UretimHatasi("En az bir girdi satırı gerekli.")
        temiz = _parcala_ciktilari_dogrula(satirlar, ciktilar, anahtar, haric_op=operasyon)
        if temiz[0][0].pk != operasyon.cikti_id:
            raise UretimHatasi("Tanımın referans çıktısı (ilk satır) sonradan değiştirilemez; yeni tanım açın.")
        _girdi_satirlarini_dogrula(operasyon.cikti, satirlar)
        operasyon.girdiler.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        operasyon.istasyon, operasyon.tur, operasyon.pay_anahtari = istasyon, yeni_tur, anahtar
        operasyon.cikti_miktar, operasyon.boy_mm = temiz[0][1], temiz[0][2]
        if tam_boy is not None:
            operasyon.tam_calistirma = bool(tam_boy)
        operasyon.updated_by = kullanici
        operasyon.save(update_fields=["istasyon", "tur", "pay_anahtari", "cikti_miktar", "tam_calistirma", "boy_mm", "updated_by", "updated_at"])
        for i, (girdi, miktar) in enumerate(satirlar, start=1):
            OperasyonGirdi.objects.create(operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10, created_by=kullanici, updated_by=kullanici)
        _ciktilari_esitle(operasyon, kullanici, [(st, m, b, y, True) for st, m, b, y in temiz])
        return operasyon
    cm = _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı.")
    if cm <= 0:
        raise UretimHatasi("Çıktı miktarı sıfırdan büyük olmalı.")
    _girdi_satirlarini_dogrula(operasyon.cikti, satirlar)
    yeni_boy = operasyon.boy_mm if boy_mm is _KORU else _boy_coz(boy_mm, "Ana çıktı boyu (mm) geçerli bir sayı olmalı.")
    if operasyon.tur == Operasyon.Tur.PARCALA and yan_ciktilar is None:
        yan_ciktilar = []                                                      # PARÇALA'dan ÜRET'e geçiş: yan çıktı yok
    yeni_yanlar = ([(y.stok, y.miktar, y.boy_mm) for y in operasyon_yan_ciktilari(operasyon)]
                   if yan_ciktilar is None else list(yan_ciktilar))
    _yan_ciktilari_dogrula(operasyon.cikti, satirlar, yeni_yanlar, yeni_boy)
    operasyon.girdiler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    operasyon.boy_mm = yeni_boy
    operasyon.istasyon = istasyon
    operasyon.cikti_miktar = cm
    operasyon.tur, operasyon.pay_anahtari = Operasyon.Tur.URET, Operasyon.PayAnahtari.BOY
    if tam_boy is not None:
        operasyon.tam_calistirma = bool(tam_boy)
    operasyon.updated_by = kullanici
    operasyon.save(update_fields=[
        "istasyon", "tur", "pay_anahtari", "cikti_miktar", "tam_calistirma", "boy_mm", "updated_by", "updated_at"])
    for i, (girdi, miktar) in enumerate(satirlar, start=1):
        OperasyonGirdi.objects.create(
            operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    _ciktilari_esitle(operasyon, kullanici, _uret_cikti_satirlari(operasyon, yeni_yanlar))
    return operasyon


def operasyon_sil(operasyon: Operasyon, kullanici=None) -> Operasyon:
    if operasyon.silindi:
        return operasyon
    if operasyon.kayitlar.filter(silindi=False).exists():
        raise UretimHatasi("Bu operasyona bağlı kayıt var; silinemez.")
    operasyon.ciktilar.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)   # sürücü kilidi serbest kalır
    operasyon.silindi = True
    operasyon.silindi_at = timezone.now()
    operasyon.updated_by = kullanici
    operasyon.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return operasyon


# === İhtiyaç Hesapla — özyinelemeli, salt-okunur, hiçbir kayıt/stok hareketi üretmez ===

def ihtiyac_hesapla(kalemler, *, boy_yuvarla=True, pay_dus=False, graf=None, kullanilabilir=None):
    """kalemler: [(Stok hedef, Decimal miktar), ...].

    ``boy_yuvarla=False`` → tam boy yukarı yuvarlaması KAPALI: bütün çalıştırmalar kesirli (gerçek tüketim; maliyet görünümleri için).
    ``pay_dus=True`` → yan çıktılı kesimde girdi talebi ana çıktının boy payı (``ana_cikti_payi``) kadar düşülür. Varsayılanlarla
    davranış eskisiyle BİREBİR aynıdır. ``graf`` (core.services.urun_agaci.Graf) verilirse operasyon/girdi/yan çıktı bilgisi bellekten
    okunur (sorgu yok).

    ``kullanilabilir`` → NET ihtiyaç modu (üretim siparişi / "stoku düş"; bkz. docs/uretim-siparisi-plan.md): {stok pk: kullanılabilir miktar}
    sözlüğü ya da zincirdeki stok pk listesini alıp o sözlüğü döndüren bir çağrılabilir (zincir keşfedildikten sonra TEK kez çağrılır).
    Her seviyede, toplanmış talep üzerinden: ayrılan = min(talep, kalan kullanılabilir) (eksi kullanılabilir 0 sayılır), net = talep − ayrılan;
    çalıştırma NET talepten (tam boy ⌈·⌉, PARÇALA max), girdilere net çalıştırma aktarılır; fazla = üretilecek − net (serbest stok). Yaprakta
    ayrılan = min(talep, kullanılabilir), eksik = talep − ayrılan (satınalma ihtiyacı). ``None`` iken (varsayılan) kod yolu brüt hesapla
    BİREBİR aynıdır (ayrılan 0, net = talep).

    İKİ AŞAMALI, SEVİYE BAZLI hesap (low-level code):
      1) Zincirdeki her stoğun EN DERİN seviyesi bulunur (köklerden en uzun yol; döngü varsa UretimHatasi).
      2) Stoklar seviye sırasıyla işlenir: bir stoğun TÜM talebi (kökten gelen + üst seviyelerin girdi talepleri) toplanmadan
         işlenmez. ``tam_calistirma=True`` operasyonda çalıştırma = ceil(TOPLAM talep / çıktı miktarı) — yuvarlama dal bazında DEĞİL,
         toplanmış talep üzerinde TEK kez yapılır; girdilere (yuvarlanmış çalıştırma × girdi miktarı) talep aktarılır. ``tam_calistirma=False``
         operasyonda çalıştırma kesirli kalır (eski davranış).

    Döner: {"agac": [...], "ozet": [...], "plan": [...]}
      agac — her hedef satırı için, kökten yapraklara iç içe ağaç:
        {"stok", "miktar"(bu dalın talebi), "istasyon", "operasyon"(None ise yaprak), "yaprak": bool, "cocuklar": [...]}
        + yaprak olmayanda: "tam", "calistirma", "uretilecek", "fazla", "ihtiyac_toplam" (stoğun ZİNCİR TOPLAMI üzerinden). ``tam``
        operasyonlu bir stok ağaçta ilk geçtiği yerde açılır; sonraki geçişlerde "tekrar": True (çocuksuz) — kesirli boy hiç görünmez.
      ozet — kökler HARİÇ zincirde geçilen HER stok için TEK satır: {"stok", "istasyon", "operasyon", "toplam_miktar"(kök talebi hariç
        gelen talep), "calistirma_sayisi"(yaprakta None), "uretilecek_miktar", "fazla_miktar", "ihtiyac"(zincir toplamı), "tam",
        "yaprak"}.
      plan — operasyonu olan HER stok (kökler önce) için üretim planı: {"stok", "operasyon", "ihtiyac", "calistirma", "uretilecek",
        "fazla", "tam"} — Üretim Emri kayıt açarken ``uretilecek`` miktarını kullanır.

    Döngü koruması: bir stok kendi ata zincirinde tekrar ederse UretimHatasi fırlatılır (sessizce kesilip yanlış/eksik rakam göstermek
    yerine — üretim planlamasında yanıltıcı olur)."""
    hedefler = [(stok, miktar) for stok, miktar in kalemler if miktar is not None and miktar > 0]
    onbellek = {}                                      # stok.pk -> (operasyon|None, girdi satırları)
    cikti_onbellek = {}                                # operasyon.pk -> tanım çıktı satırları (OperasyonCikti; sıra 0 referans)
    PARCALA = Operasyon.Tur.PARCALA

    def op_bul(stok):
        if stok.pk not in onbellek:
            if graf is not None:
                operasyon = graf.op_of.get(stok.pk)
                satirlar = list(graf.girdiler.get(operasyon.pk, [])) if operasyon else []
                if operasyon is not None:
                    cikti_onbellek.setdefault(operasyon.pk, list(graf.ciktilar.get(operasyon.pk, [])))
            else:
                operasyon = ureten_operasyon(stok)
                satirlar = (list(operasyon.girdiler.filter(silindi=False).select_related("girdi").order_by("sira", "pk"))
                            if operasyon else [])
                if operasyon is not None:
                    cikti_onbellek.setdefault(operasyon.pk, tanim_ciktilari(operasyon))
            onbellek[stok.pk] = (operasyon, satirlar)
        return onbellek[stok.pk]

    # --- 1. aşama: zinciri keşfet (döngü kontrolü), ebeveynleri topla, seviyeleri bul
    stoklar, sira_no, ebeveynler, bitti = {}, {}, {}, set()

    def kesfet(stok, yol):
        if stok.pk in yol:
            raise UretimHatasi(
                f"Operasyon zincirinde döngü tespit edildi: {stok.kod} kendi üretim zincirinde tekrar ediyor.")
        if stok.pk not in stoklar:
            stoklar[stok.pk] = stok
            sira_no[stok.pk] = len(sira_no)
        if stok.pk in bitti:
            return
        operasyon, satirlar = op_bul(stok)
        if operasyon is not None and operasyon.tur == PARCALA:         # PARÇALA: kardeş çıktılar da aynı çalıştırmadan çıkar → planda yer alır
            for c in cikti_onbellek[operasyon.pk]:
                if c.surucu and c.stok_id not in stoklar:
                    stoklar[c.stok_id] = c.stok
                    sira_no[c.stok_id] = len(sira_no)
        for satir in satirlar:
            ebeveynler.setdefault(satir.girdi_id, set()).add(stok.pk)
            kesfet(satir.girdi, yol | {stok.pk})
        bitti.add(stok.pk)

    for stok, _ in hedefler:
        kesfet(stok, frozenset())

    # NET mod: kullanılabilir stok (eksi → 0); her stok için tek ayırma (stok başına tek sürücü çıktı / tek yaprak satırı)
    if callable(kullanilabilir):
        kullanilabilir = kullanilabilir(list(stoklar))
    kalan = None if kullanilabilir is None else {pk: max(Decimal("0"), Decimal(str(v))) for pk, v in kullanilabilir.items()}
    ayrilan = {}

    def _ayir(pk, talep_m):
        if kalan is None or talep_m <= 0:
            return Decimal("0")
        a = min(talep_m, kalan.get(pk, Decimal("0")))
        if a > 0:
            kalan[pk] = kalan[pk] - a
            ayrilan[pk] = ayrilan.get(pk, Decimal("0")) + a
        return a

    seviye = {}

    def seviye_bul(pk):
        if pk not in seviye:
            seviye[pk] = 0 if not ebeveynler.get(pk) else 1 + max(seviye_bul(p) for p in ebeveynler[pk])
        return seviye[pk]

    for pk in stoklar:
        seviye_bul(pk)

    # --- 2. aşama: OPERASYON sırasıyla (seviye = sürücü çıktılarının en derini) toplanmış talep → çalıştırma → girdilere aktarım
    #   ÜRET: çalıştırma = talep(ana) / çıktı miktarı. PARÇALA: çalıştırma = max_i(talep_i / miktar_i) (tüm sürücü çıktılar aynı çalıştırmadan
    #   çıkar); tam boyda yukarı yuvarlanır. Üretilecek_i = çalıştırma × miktar_i, fazla_i = üretilecek_i − talep_i — fazla, aynı hesapta zaten
    #   toplanmış talepten karşılandığından ayrıca mahsup gerekmez (çift sayım yok).
    kok_talep = {}
    for stok, miktar in hedefler:
        kok_talep[stok.pk] = kok_talep.get(stok.pk, Decimal("0")) + miktar
    talep = dict(kok_talep)
    plan_map, pay_map, plan_op = {}, {}, {}
    op_of_pk, op_stoklar = {}, {}
    for pk in stoklar:
        operasyon, _ = op_bul(stoklar[pk])
        if operasyon is not None:
            op_of_pk[operasyon.pk] = operasyon
            op_stoklar.setdefault(operasyon.pk, []).append(pk)
    op_sira = sorted(op_of_pk, key=lambda o: (max(seviye[p] for p in op_stoklar[o]), min(sira_no[p] for p in op_stoklar[o])))
    for opk in op_sira:
        operasyon = op_of_pk[opk]
        _, satirlar = op_bul(stoklar[op_stoklar[opk][0]])
        ciktilar = cikti_onbellek[opk]
        surucu = [c for c in ciktilar if c.surucu]
        paylar = cikti_paylari(operasyon, ciktilar) if pay_dus else {c.stok_id: Decimal("1") for c in ciktilar}
        tam = operasyon.tam_calistirma and boy_yuvarla
        net = {}                                             # sürücü çıktı başına NET talep (brütte = talep)
        for c in surucu:
            t = talep.get(c.stok_id, Decimal("0"))
            net[c.stok_id] = t - _ayir(c.stok_id, t)
        ham = {c.stok_id: net[c.stok_id] / c.miktar for c in surucu}                            # çıktı başına gereken çalıştırma
        calistirma = max(ham.values())
        if tam:
            calistirma = _yukari_yuvarla(calistirma)
        for satir in satirlar:
            if pay_dus:                                     # her sürücü çıktının kendi talebi × girdi × kendi payı (ÜRET: ana × ana payı)
                ek = sum((((_yukari_yuvarla(h) if tam else h) * satir.miktar * paylar[sid]) for sid, h in ham.items()), Decimal("0"))
            else:
                ek = calistirma * satir.miktar * Decimal("1")
            talep[satir.girdi_id] = talep.get(satir.girdi_id, Decimal("0")) + ek
        plan_ciktilar = []
        for c in ciktilar:
            t_i = talep.get(c.stok_id, Decimal("0")) if c.surucu else Decimal("0")
            n_i = net.get(c.stok_id, Decimal("0")) if c.surucu else Decimal("0")
            if not c.surucu:
                u_i = calistirma * c.miktar                  # ÜRET yan çıktı: bilgi (talebi etkilemez)
            elif tam or ham[c.stok_id] != calistirma:
                u_i = calistirma * c.miktar
            else:
                u_i = n_i                                    # kesirli çalıştırmada sürücü NET talebin kendisi (bölme artığı yok; brütte net = talep)
            plan_ciktilar.append({"stok": c.stok, "miktar": c.miktar, "boy_mm": c.boy_mm, "yuzde": c.yuzde, "surucu": c.surucu,
                                  "ihtiyac": t_i, "net": n_i, "ayrilan": t_i - n_i, "uretilecek": u_i, "fazla": u_i - n_i, "pay": paylar[c.stok_id]})
        yan_bilgi = [{"stok": y.stok, "miktar": calistirma * y.miktar, "boy_mm": y.boy_mm} for y in ciktilar if not y.surucu]
        for pc in plan_ciktilar:
            if not pc["surucu"]:
                continue
            sid = pc["stok"].pk
            pay_map[sid] = paylar[sid]
            plan_map[sid] = {"stok": pc["stok"], "operasyon": operasyon, "ihtiyac": pc["ihtiyac"], "net": pc["net"], "ayrilan": pc["ayrilan"],
                             "seviye": max(seviye[p] for p in op_stoklar[opk]),
                             "calistirma": calistirma, "uretilecek": pc["uretilecek"], "fazla": pc["fazla"], "tam": tam, "yan_ciktilar": yan_bilgi,
                             "tur": operasyon.tur, "ciktilar": plan_ciktilar, "cikti_miktar": pc["miktar"]}
        plan_op[opk] = plan_map[ciktilar[0].stok_id] if ciktilar and ciktilar[0].stok_id in plan_map else plan_map[op_stoklar[opk][0]]

    # yapraklar (operasyonsuz hazır stok): NET modda toplanmış talepten ayrılan, kalanı eksik (satınalma ihtiyacı)
    for pk in stoklar:
        if op_bul(stoklar[pk])[0] is None:
            _ayir(pk, talep.get(pk, Decimal("0")))

    # --- ağaç (gösterim) + özet sırası: DFS, tam çalıştırmalı operasyon yalnız İLK geçtiği yerde açılır
    ozet_sira = []
    acildi = set()

    def gez(stok, miktar, kok=False):
        operasyon, satirlar = op_bul(stok)
        if not kok and stok.pk not in ozet_sira:
            ozet_sira.append(stok.pk)
        toplam = talep.get(stok.pk, Decimal("0"))
        ayr = ayrilan.get(stok.pk, Decimal("0"))
        if operasyon is None:
            return {"stok": stok, "miktar": miktar, "istasyon": None, "operasyon": None, "yaprak": True, "cocuklar": [],
                    "ihtiyac_toplam": toplam, "ayrilan_toplam": ayr, "eksik_toplam": toplam - ayr}
        p = plan_map[stok.pk]
        dugum = {"stok": stok, "miktar": miktar, "istasyon": operasyon.istasyon, "operasyon": operasyon, "yaprak": False,
                 "tam": p["tam"], "calistirma": p["calistirma"], "uretilecek": p["uretilecek"], "fazla": p["fazla"],
                 "ihtiyac_toplam": p["ihtiyac"], "ayrilan_toplam": p["ayrilan"], "net_toplam": p["net"],
                 "yan_ciktilar": p["yan_ciktilar"], "tur": p["tur"], "ciktilar": p["ciktilar"], "cocuklar": []}
        if p["tam"]:
            if operasyon.pk in acildi:
                dugum["tekrar"] = True
                return dugum
            acildi.add(operasyon.pk)
            calistirma = p["calistirma"]
        else:
            oran = (p["net"] / p["ihtiyac"]) if (kalan is not None and p["ihtiyac"]) else Decimal("1")    # net mod: dal talebi net oranıyla
            calistirma = miktar * oran / p["cikti_miktar"]
        pay = pay_map[stok.pk]
        dugum["cocuklar"] = [gez(satir.girdi, calistirma * satir.miktar * pay) for satir in satirlar]
        return dugum

    agac = [gez(stok, miktar, kok=True) for stok, miktar in hedefler]

    ozet = []
    for pk in ozet_sira:
        operasyon, _ = op_bul(stoklar[pk])
        p = plan_map.get(pk)
        ayr = ayrilan.get(pk, Decimal("0"))
        ozet.append({
            "stok": stoklar[pk], "operasyon": operasyon, "istasyon": operasyon.istasyon if operasyon else None,
            "toplam_miktar": talep.get(pk, Decimal("0")) - kok_talep.get(pk, Decimal("0")), "yaprak": operasyon is None,
            "ihtiyac": talep.get(pk, Decimal("0")), "tam": bool(p and p["tam"]),
            "calistirma_sayisi": p["calistirma"] if p else None,
            "uretilecek_miktar": p["uretilecek"] if p else None, "fazla_miktar": p["fazla"] if p else None,
            "yan_ciktilar": p["yan_ciktilar"] if p else [], "tur": p["tur"] if p else None, "ciktilar": p["ciktilar"] if p else [],
            "ayrilan": ayr, "net": talep.get(pk, Decimal("0")) - ayr,                               # net mod: stoktan ayrılan / net ihtiyaç
            "eksik": (talep.get(pk, Decimal("0")) - ayr) if operasyon is None else None})             # yaprakta satınalma ihtiyacı

    plan, planda = [], set()                              # OPERASYON başına tek satır ("stok" = referans çıktı); önce kökler, sonra özet sırası
    for stok, _ in hedefler:
        operasyon, _ = op_bul(stok)
        if operasyon is not None and operasyon.pk not in planda:
            plan.append(plan_op[operasyon.pk])
            planda.add(operasyon.pk)
    for pk in ozet_sira:
        operasyon, _ = op_bul(stoklar[pk])
        if operasyon is not None and operasyon.pk not in planda:
            plan.append(plan_op[operasyon.pk])
            planda.add(operasyon.pk)
    return {"agac": agac, "ozet": ozet, "plan": plan, "net_mod": kalan is not None, "ayrilan": ayrilan}


# === Üretim Emirleri — üst-düzey tetikleyici ===

def _sonraki_emir_sira(yil):
    m = UretimEmri.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


URETIM_PLANLAMA_KILIDI = 7410001      # pg_advisory_xact_lock anahtarı: ÜS açılış/revize planlaması (ayırma yarışını önler)


def _sonraki_ie_sira(yil):
    m = IstasyonEmri.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


def emir_hedef_cikti(istasyon_emri: IstasyonEmri, calistirma=None) -> Decimal:
    """İstasyon emrinin ``calistirma`` (varsayılan: planlanan) çalıştırması için REFERANS çıktı adedi (6 ondalık) — operasyon kaydının hedefi."""
    tanim = tanim_ciktilari(istasyon_emri.operasyon)
    ref = tanim[0].miktar if tanim else istasyon_emri.operasyon.cikti_miktar
    c = istasyon_emri.planlanan if calistirma is None else calistirma
    return (c * ref).quantize(Decimal("0.000001"))


def _revizyon_yaz(emir: UretimEmri, tur, tarih, aciklama="", detay=None, kullanici=None) -> UretimEmriRevizyon:
    """Üretim siparişi olay geçmişine satır ekler (açılış = 0, sonrakiler 1, 2…); ``UretimEmri.revizyon_no`` son numaraya çekilir. Revizyonun
    tarihi İŞLEM ANIDIR (bugün; ekranda ``created_at`` saatiyle) — sipariş/belge tarihi değil; ``tarih`` parametresi geriye uyumluluk içindir."""
    son = emir.revizyonlar.aggregate(m=Max("no"))["m"]
    no = 0 if son is None else son + 1
    r = UretimEmriRevizyon.objects.create(uretim_emri=emir, no=no, tur=tur, tarih=timezone.localdate(), aciklama=(aciklama or "")[:300], detay=detay or {},
                                          created_by=kullanici, updated_by=kullanici)
    UretimEmri.objects.filter(pk=emir.pk).update(revizyon_no=no)
    emir.revizyon_no = no
    return r


def _ds(x) -> str:
    return format(Decimal(x).normalize(), "f")


@transaction.atomic
def uretim_emri_olustur(*, kalemler, depo_id, tarih, aciklama="", kaynak_siparis=None,
                        kullanici=None, siparis_kalemleri=None) -> UretimEmri:
    """ÜRETİM SİPARİŞİ açar. kalemler: [{"hedef_urun_id": int, "hedef_miktar": Decimal|str}, ...] — en az 1 satır. ``siparis_kalemleri``
    (isteğe bağlı, ``kalemler`` ile aynı uzunlukta TeklifSiparisKalem listesi): siparişten açılışta kalem bağı; bu durumda aynı ürün birden çok
    satırda olabilir (netleme stok bazında toplanmış talepten yapılır).

    NET PLAN (docs/uretim-siparisi-plan.md): ``ihtiyac_hesapla(kullanilabilir=)`` — her seviyede eldeki AYRILMAMIŞ stok (mamul, yarı mamul,
    hammadde) düşülür. Sonuç: (1) ayrılan her stok için ``StokAyirma`` (stok hareketi YOK, yumuşak kural), (2) çalıştırması gereken her operasyon için
    ``IstasyonEmri`` (planlanan = net çalıştırma, seviye sırasıyla), (3) kalem başına ``eldeki_ayrilan`` snapshot'ı, (4) açılış revizyon kaydı.
    OperasyonKaydi AÇILMAZ — kayıtlar istasyon emrinden açılır. depo/tarih kayıtların varsayılanı olarak emirde durur."""
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if not depo:
        raise UretimHatasi("Depo bulunamadı.")
    coz = _kalemleri_coz(kalemler, siparis_kalemleri)
    _planlama_kilidi()
    sonuc = ihtiyac_hesapla(coz, kullanilabilir=stok_ayirma.kullanilabilir_haritasi)       # önce hesapla: döngü hatası hiçbir şey yazdırmaz

    yil = tarih.year
    emir = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_emir_sira(yil)
                emir = UretimEmri.objects.create(
                    yil=yil, sira=sira, no=f"ÜS-{yil}-{sira:04d}", depo=depo, tarih=tarih,      # üretim siparişi (eski UE- kayıtlar aynen)
                    aciklama=(aciklama or "").strip(), kaynak_siparis=kaynak_siparis,
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if emir is None:
        raise UretimHatasi("Emir numarası üretilemedi; tekrar deneyin.")

    kalem_detay = _kalemleri_yaz(emir, coz, sonuc["ayrilan"], siparis_kalemleri, kullanici)
    ayirma_detay = _ayirmalari_esitle(emir, sonuc["ayrilan"], kullanici)

    emir_detay = []
    for p in sonuc["plan"]:
        if p["calistirma"] <= 0:                    # net ihtiyaç 0: tamamı stoktan karşılandı, istasyon emri gerekmez
            continue
        ie = _istasyon_emri_ac(emir, p, kullanici)
        emir_detay.append({"no": ie.no, "operasyon": p["operasyon"].cikti.kod, "planlanan": _ds(ie.planlanan)})
    _revizyon_yaz(emir, UretimEmriRevizyon.Tur.ACILIS, tarih, "Açılış",
                  {"kalemler": kalem_detay, "ayirmalar": ayirma_detay, "istasyon_emirleri": emir_detay}, kullanici)
    return emir


def _planlama_kilidi():
    """Eş zamanlı iki ÜS açılışı/revizesi aynı stoğu iki kez ayırmasın: planlama transaction'ı boyunca tek danışma kilidi."""
    with connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", [URETIM_PLANLAMA_KILIDI])


def _kalemleri_coz(kalemler, siparis_kalemleri=None):
    """[(Stok, Decimal), ...] — her kalemin operasyonu olmalı, miktar > 0; siparişsizde aynı ürün tek satır."""
    if not kalemler:
        raise UretimHatasi("En az bir hedef ürün satırı gerekli.")
    if siparis_kalemleri is not None and len(siparis_kalemleri) != len(kalemler):
        raise UretimHatasi("Sipariş kalemi listesi kalem listesiyle aynı uzunlukta olmalı.")
    coz, gorulen = [], set()
    for satir in kalemler:
        urun = _cikti_coz(satir["hedef_urun_id"])
        if urun.pk in gorulen and siparis_kalemleri is None:
            raise UretimHatasi(f"{urun.kod} birden fazla satırda tekrarlanamaz.")
        gorulen.add(urun.pk)
        if ureten_operasyon(urun) is None:
            raise UretimHatasi(
                f"{urun.kod} için tanımlı bir operasyon yok; önce Operasyon Tanımları'ndan ekleyin.")
        miktar = _sayi_coz(satir["hedef_miktar"], "Hedef miktar geçerli bir sayı olmalı.")
        if miktar <= 0:
            raise UretimHatasi("Hedef miktar sıfırdan büyük olmalı.")
        coz.append((urun, miktar))
    return coz


def _kalemleri_yaz(emir, coz, ayrilan, siparis_kalemleri, kullanici):
    """ÜS kalemlerini yazar; ``eldeki_ayrilan`` kök stoğun ayrılanından sırayla (kalem miktarını aşmaz). Detay listesi döner."""
    havuz = dict(ayrilan)
    detay = []
    for i, (urun, miktar) in enumerate(coz, start=1):
        a = min(miktar, havuz.get(urun.pk, Decimal("0")))
        havuz[urun.pk] = havuz.get(urun.pk, Decimal("0")) - a
        UretimEmriKalemi.objects.create(
            uretim_emri=emir, hedef_urun=urun, hedef_miktar=miktar, sira=i * 10, eldeki_ayrilan=a,
            siparis_kalem=siparis_kalemleri[i - 1] if siparis_kalemleri else None,
            created_by=kullanici, updated_by=kullanici)
        detay.append({"stok": urun.kod, "miktar": _ds(miktar), "eldeki_ayrilan": _ds(a)})
    return detay


def _ayirmalari_esitle(emir, ayrilan, kullanici):
    """ÜS ayırmalarını plan ayrılanına ÇEKER (artan/azalan; planda olmayan stoklar kapanır). Yeni durumun detay listesi döner."""
    mevcut = {a.stok_id for a in stok_ayirma.emir_ayirmalari(emir)}
    detay = []
    for stok_pk in sorted(mevcut | set(ayrilan)):
        miktar = ayrilan.get(stok_pk, Decimal("0"))
        stok_ayirma.ayirma_ayarla(emir, stok_pk, max(miktar, Decimal("0")), kullanici)
        if miktar > 0:
            detay.append({"stok": Stok.objects.get(pk=stok_pk).kod, "miktar": _ds(miktar)})
    return detay


def _istasyon_emri_ac(emir: UretimEmri, plan_satiri: dict, kullanici=None) -> IstasyonEmri:
    """Plan satırından (ihtiyac_hesapla ``plan``) istasyon emri yazar (IE-yyyy-nnnn; 10 denemeli iyimser numara)."""
    op = plan_satiri["operasyon"]
    yil = emir.tarih.year
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_ie_sira(yil)
                return IstasyonEmri.objects.create(
                    uretim_emri=emir, operasyon=op, istasyon_id=op.istasyon_id, yil=yil, sira=sira, no=f"IE-{yil}-{sira:04d}",
                    seviye=plan_satiri["seviye"], planlanan=plan_satiri["calistirma"], tamamlanan=Decimal("0"),
                    ihtiyac={str(c["stok"].pk): _ds(c["net"]) for c in plan_satiri["ciktilar"] if c["surucu"] and c["net"] > 0},
                    durum=IstasyonEmri.Durum.BEKLIYOR, created_by=kullanici, updated_by=kullanici)
        except IntegrityError:
            continue
    raise UretimHatasi("İstasyon emri numarası üretilemedi; tekrar deneyin.")


def siparis_uretilebilir_kalemleri(siparis):
    """(uygun, uygun_degil) döner — uygun_degil: [(TeklifSiparisKalem, sebep_metni), ...].
    Üretime uygun = stok.uretim_urunu=True VE o stok için aktif bir Operasyon tanımlı."""
    uygun, uygun_degil = [], []
    for k in siparis.kalemler.filter(silindi=False).select_related("stok"):
        if not k.stok.uretim_urunu:
            uygun_degil.append((k, "üretim ürünü işaretli değil"))
        elif ureten_operasyon(k.stok) is None:
            uygun_degil.append((k, "tanımlı operasyonu yok"))
        else:
            uygun.append(k)
    return uygun, uygun_degil


@transaction.atomic
def siparisten_uretim_emri_olustur(*, siparis, depo_id, tarih, kalem_secimleri, aciklama="",
                                   kullanici=None) -> UretimEmri:
    """SATIŞ+SIPARIS+ONAYLI bir belgeden TEK, çok kalemli bir Üretim Emri açar ('tek emir,
    çoklu kalem'). kalem_secimleri ZORUNLU: [{"kalem_id": int, "hedef_miktar": Decimal|str}, ...]
    — görüntüleme ekranından (kullanıcı satır çıkarmış/miktar düzeltmiş olabilir) gelir; boş
    liste de dahil olmak üzere HER ZAMAN görüntüleme ekranındaki formdan üretilir — servis
    kendiliğinden 'seçilmemişse hepsini al' varsayımı YAPMAZ (bilerek tüm satırları
    kaldırmış olma ihtimaliyle 'hiç seçim yapılmadı' durumunu karıştırmamak için)."""
    if siparis.belge_tur != TeklifSiparis.BelgeTur.SIPARIS or siparis.yon != TeklifSiparis.Yon.SATIS:
        raise UretimHatasi("Yalnız SATIŞ siparişinden üretim emri açılabilir.")
    if siparis.durum != TeklifSiparis.Durum.ONAYLI:
        raise UretimHatasi("Yalnız onaylı sipariş için üretim emri açılabilir.")
    if siparis.uretim_emirleri.filter(silindi=False).exclude(durum=UretimEmri.Durum.IPTAL).exists():      # tüm ÜS'leri iptalse yenisi açılabilir
        raise UretimHatasi("Bu siparişten zaten bir üretim siparişi açılmış.")

    uygun, _ = siparis_uretilebilir_kalemleri(siparis)
    uygun_map = {k.pk: k for k in uygun}

    if not kalem_secimleri:
        raise UretimHatasi(
            "Bu siparişte üretime uygun (üretim ürünü + tanımlı operasyonu olan) hiçbir "
            "kalem seçilmedi.")
    secim = []
    gorulen = set()
    for satir in kalem_secimleri:
        kalem = uygun_map.get(satir["kalem_id"])
        if kalem is None:
            raise UretimHatasi("Geçersiz veya üretime uygun olmayan bir sipariş kalemi seçildi.")
        if kalem.pk in gorulen:
            raise UretimHatasi("Aynı sipariş kalemi birden fazla kez seçilemez.")
        gorulen.add(kalem.pk)
        miktar = _sayi_coz(satir["hedef_miktar"], "Hedef miktar geçerli bir sayı olmalı.")
        if miktar != kalem.miktar:                  # karar 6/10: siparişli ÜS kalemi = sipariş kalem miktarı
            raise UretimHatasi(
                f"{kalem.stok.kod}: üretim siparişi miktarı sipariş miktarına ({kalem.miktar.normalize():f}) eşit olmalı.")
        secim.append((kalem, miktar))

    kalemler = [{"hedef_urun_id": kalem.stok_id, "hedef_miktar": miktar} for kalem, miktar in secim]
    return uretim_emri_olustur(
        kalemler=kalemler, depo_id=depo_id, tarih=tarih, aciklama=aciklama,
        kaynak_siparis=siparis, kullanici=kullanici, siparis_kalemleri=[kalem for kalem, _ in secim])

# === İstasyon emrinden operasyon kaydı: açma, onay/geri alma etkileri, durum ===

def _referans_miktar(operasyon) -> Decimal:
    tanim = tanim_ciktilari(operasyon)
    return tanim[0].miktar if tanim else operasyon.cikti_miktar


def kayit_calistirma(kayit: OperasyonKaydi) -> Decimal:
    """Kaydın ÇALIŞTIRMA birimindeki miktarı. Onaylı ÜRET kaydında ana çıktının GİRİŞ miktarından (fasonda fire sonrası gelen adet),
    diğerlerinde hedeften (PARÇALA'da hedef zaten gelenden türetilir): çalıştırma = referans çıktı adedi / referans miktar."""
    op = kayit.operasyon
    ref = _referans_miktar(op)
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI and op.tur == Operasyon.Tur.URET:
        ana = kayit.ciktilar.filter(ana_mi=True, silindi=False).first()
        if ana is not None:
            return ana.miktar / ref
    return kayit.hedef_cikti_miktari / ref


def istasyon_emri_acik_kalan(ie: IstasyonEmri, haric_kayit=None) -> Decimal:
    """Henüz bir kayda bağlanmamış çalıştırma: planlanan − tamamlanan − açık TASLAK kayıtların çalıştırması (eksiye inebilir = fazla planlandı)."""
    acik = Decimal("0")
    for k in ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK).select_related("operasyon"):
        if haric_kayit is None or k.pk != haric_kayit.pk:
            acik += kayit_calistirma(k)
    return ie.planlanan - ie.tamamlanan - acik


def istasyon_emri_durum_guncelle(ie: IstasyonEmri) -> IstasyonEmri:
    """Durumu olaylardan yeniden hesaplar: IPTAL kalır; tamamlanan ≥ planlanan → BITTI; tamamlanan > 0 ya da açık taslak kayıt var → BASLADI; yoksa BEKLIYOR."""
    ie.refresh_from_db()
    if ie.durum == IstasyonEmri.Durum.IPTAL:
        return ie
    if ie.tamamlanan >= ie.planlanan:
        yeni = IstasyonEmri.Durum.BITTI
    elif ie.tamamlanan > 0 or ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK).exists():
        yeni = IstasyonEmri.Durum.BASLADI
    else:
        yeni = IstasyonEmri.Durum.BEKLIYOR
    if yeni != ie.durum:
        ie.durum = yeni
        ie.save(update_fields=["durum", "updated_at"])
    return ie


def kayit_fazla_uyarisi(kayit: OperasyonKaydi) -> str:
    """Kayıt, istasyon emrinin kalanını aşıyorsa uyarı metni ('' = aşmıyor) — engellenmez (karar 7: fazlası serbest stok)."""
    ie = kayit.istasyon_emri
    if ie is None:
        return ""
    kalan = istasyon_emri_acik_kalan(ie, haric_kayit=kayit)
    c = kayit_calistirma(kayit)
    if c > kalan:
        return (f"Bu kayıt istasyon emrinin kalanını aşıyor ({c.normalize():f} çalıştırma, kalan {max(kalan, Decimal('0')).normalize():f}); "
                "fazla üretilen parça serbest stoğa girer.")
    return ""


@transaction.atomic
def istasyon_emri_kayit_ac(ie: IstasyonEmri, *, hedef=None, depo_id=None, tarih=None, aciklama="", kullanici=None) -> OperasyonKaydi:
    """İstasyon emrinden TASLAK operasyon kaydı açar (``uretim_emri`` + ``istasyon_emri`` bağlı). ``hedef`` (referans çıktı adedi) verilmezse
    açık kalan çalıştırma × referans miktar; kalan yoksa hedef zorunlu. Hedef kalanı AŞABİLİR (uyarı: ``kayit_fazla_uyarisi``; fazlası serbest stok).
    Emir satırı kilitlenir (aynı emirden eş zamanlı iki açılış kalanı iki kez kullanmasın). Yalnız açık ÜS'nin bitmemiş/iptal olmamış emri için."""
    ie = IstasyonEmri.objects.select_for_update().select_related("uretim_emri", "operasyon").get(pk=ie.pk)
    emir = ie.uretim_emri
    if ie.silindi or emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        raise UretimHatasi("Yalnız açık üretim siparişinin istasyon emrinden kayıt açılabilir.")
    if ie.durum == IstasyonEmri.Durum.IPTAL:
        raise UretimHatasi("İptal edilmiş istasyon emrinden kayıt açılamaz.")
    ref = _referans_miktar(ie.operasyon)
    if hedef in (None, ""):
        kalan = istasyon_emri_acik_kalan(ie)
        if kalan <= 0:
            raise UretimHatasi("Bu istasyon emrinde açılacak kalan miktar yok; fazla üretim için hedef miktarı girin.")
        hedef = (kalan * ref).quantize(Decimal("0.000001"))
    kayit = operasyon_kaydi_olustur(
        operasyon_id=ie.operasyon_id, depo_id=depo_id or emir.depo_id, tarih=tarih or timezone.localdate(), hedef_cikti_miktari=hedef,
        uretim_emri=emir, istasyon_emri=ie, aciklama=aciklama, kullanici=kullanici)
    istasyon_emri_durum_guncelle(ie)
    return kayit


def _eksik_mesaji(mesaj: str) -> str:
    """Stok yetersizliği mesajına ("Yetersiz stok: 150 deposunda KOD için eldeki X, çıkış Y olamaz.") eksik miktarı ekler; diğer mesajlar aynen."""
    m = re.search(r"eldeki (-?[\d.,]+), çıkış ([\d.,]+) olamaz", mesaj)
    if not m:
        return mesaj
    try:
        eldeki, gereken = Decimal(m.group(1)), Decimal(m.group(2))
    except Exception:
        return mesaj
    return f"{mesaj.rstrip('.')} — eksik: {(gereken - eldeki).normalize():f}."


@transaction.atomic
def istasyon_emri_uret(ie: IstasyonEmri, *, miktar=None, tarih=None, kullanici=None):
    """TEK TIKLA ÜRETİM ("Üretildi"): istasyon emrinden operasyon kaydı açar (girdi/çıktı operasyon tanımından, miktar oranlı) ve MEVCUT onay servisiyle
    hemen ONAYLAR — hepsi tek işlemde; stok yetersizliği vb. bir hata olursa kayıt da dahil hiçbir şey kalmaz. ``miktar`` referans çıktı adedidir
    (boş = açık kalan); açık kalandan fazlası yazılabilir (fazlası serbest stok). Fason istasyonunda üretim fason dönüşle yapılır, burada reddedilir.
    (onaylı kayıt, uyarı metni) döner; uyarı = kalanı aşıyor ('' = aşmıyor)."""
    from core.services.fason import FASON_ISTASYON_KODU
    ie = IstasyonEmri.objects.select_related("istasyon", "uretim_emri").get(pk=ie.pk)
    if ie.istasyon.kod == FASON_ISTASYON_KODU:
        raise UretimHatasi("Fason istasyonunda üretim, fason dönüş belgesiyle yapılır (\"Fason dönüş aç\").")
    kayit = istasyon_emri_kayit_ac(ie, hedef=miktar, tarih=tarih, kullanici=kullanici)
    uyari = kayit_fazla_uyarisi(kayit)                           # onaydan ÖNCE: onay sonrası tamamlanan zaten artmış olur
    try:
        operasyon_kaydi_onayla(kayit, kullanici=kullanici)
    except UretimHatasi as e:
        raise UretimHatasi(_eksik_mesaji(str(e)))
    return OperasyonKaydi.objects.get(pk=kayit.pk), uyari


def uretim_emri_yuzde(emir: UretimEmri) -> Decimal:
    """ÜS ilerleme yüzdesi: Σ min(hedef, ayrılan + sevk edilen) ÷ Σ hedef × 100 (0–100)."""
    ayrilan = {a.stok_id: a.miktar for a in stok_ayirma.emir_ayirmalari(emir)}
    sevk = emir.sevk_dusen or {}
    toplam = hazir = Decimal("0")
    for k in emir.kalemler.filter(silindi=False):
        toplam += k.hedef_miktar
        hazir += min(k.hedef_miktar, ayrilan.get(k.hedef_urun_id, Decimal("0")) + Decimal(str(sevk.get(str(k.hedef_urun_id), "0"))))
    return (hazir * 100 / toplam) if toplam else Decimal("0")


def _istasyon_emri_onay_etkisi(kayit: OperasyonKaydi, kullanici=None) -> None:
    """Onaylanan, istasyon emrine bağlı kayıt: (1) girdi tüketimi kadar ÜS'nin o stok ayırması düşer (ayrılmış malzeme tüketildi), (2) üretilen
    parça ÜS'nin kalan NET ihtiyacına kadar ayrılır (fazlası serbest), (3) tamamlanan artar, durum güncellenir. İzler kayıt satırlarına yazılır."""
    ie, emir = kayit.istasyon_emri, kayit.uretim_emri
    if ie is None or emir is None:
        return
    ie = IstasyonEmri.objects.select_for_update().get(pk=ie.pk)      # aynı emrin iki kaydı eş zamanlı onaylanırsa sıraya girer (çift ayırma olmaz)
    for satir in kaydi_girdi_satirlari(kayit):
        if satir.gerceklesen_miktar <= 0:
            continue
        dus = min(stok_ayirma.ayrilan_miktar(emir, satir.girdi_id), satir.gerceklesen_miktar)
        if dus > 0:
            stok_ayirma.ayirma_ekle(emir, satir.girdi_id, -dus, kullanici)
            satir.ayrilan_dusen = dus
            satir.save(update_fields=["ayrilan_dusen", "updated_at"])
    ihtiyac = {int(k): Decimal(str(v)) for k, v in (ie.ihtiyac or {}).items()}
    for c in kayit.ciktilar.filter(silindi=False):
        if c.miktar <= 0 or c.stok_id not in ihtiyac:
            continue
        onceki = sum((x.ayrilan for x in OperasyonKaydiCikti.objects.filter(
            kayit__istasyon_emri=ie, kayit__durum=OperasyonKaydi.Durum.ONAYLI, kayit__silindi=False, stok_id=c.stok_id, silindi=False).exclude(pk=c.pk)), Decimal("0"))
        a = min(c.miktar, max(Decimal("0"), ihtiyac[c.stok_id] - onceki))
        if a > 0:
            stok_ayirma.ayirma_ekle(emir, c.stok_id, a, kullanici)
            c.ayrilan = a
            c.save(update_fields=["ayrilan", "updated_at"])
    ie.tamamlanan = ie.tamamlanan + kayit_calistirma(kayit)
    ie.save(update_fields=["tamamlanan", "updated_at"])
    istasyon_emri_durum_guncelle(ie)
    emir.refresh_from_db()
    _kapanis_kontrol(emir, kullanici)                          # sevk edilmiş sipariş: son emir bitince kapanır


def _istasyon_emri_geri_al_etkisi(kayit: OperasyonKaydi, kullanici=None) -> None:
    """Onaylı kaydın geri alınması (stok hareketleri geri alındıktan sonra): ayırma izleri aynen geri sarılır, tamamlanan düşer, durum güncellenir."""
    ie, emir = kayit.istasyon_emri, kayit.uretim_emri
    if ie is None or emir is None:
        return
    ie = IstasyonEmri.objects.select_for_update().get(pk=ie.pk)
    if emir.durum == UretimEmri.Durum.ACIK:
        for c in kayit.ciktilar.filter(silindi=False):
            if c.ayrilan > 0:
                stok_ayirma.ayirma_ekle(emir, c.stok_id, -c.ayrilan, kullanici)
        for satir in kaydi_girdi_satirlari(kayit):
            if satir.ayrilan_dusen > 0:
                stok_ayirma.ayirma_ekle(emir, satir.girdi_id, satir.ayrilan_dusen, kullanici)
    ie.tamamlanan = max(Decimal("0"), ie.tamamlanan - kayit_calistirma(kayit))
    ie.save(update_fields=["tamamlanan", "updated_at"])
    istasyon_emri_durum_guncelle(ie)

# === Üretim siparişi REVİZE (adım 7) ===

def sevk_edilen_haritasi(emir: UretimEmri) -> dict:
    """{stok pk: sevk/faturalanmış miktar} — siparişli ÜS'de kaynak siparişin bağlı (silinmemiş) satış faturası satırlarından; manuel ÜS'de boş.
    Revize bunun altına düşürmeyi reddeder (adım 8 kapanışı aynı kaynağı kullanır)."""
    sip = emir.kaynak_siparis
    if sip is None or not sip.fatura_id or sip.fatura.silindi:
        return {}
    h = {}
    for sat in sip.fatura.satirlar.filter(silindi=False, stok__isnull=False):
        h[sat.stok_id] = h.get(sat.stok_id, Decimal("0")) + sat.miktar
    return h


def plan_kalemleri(coz, sevk: dict) -> list:
    """Planlanacak kalemler: ÜS kalemlerinden sevk/fatura edilmiş miktar (stok başına, sırayla) düşülür — sevk edilen mamul zaten çıktı, yeniden
    üretilmez/ayrılmaz. Sıfıra inenler dışarıda kalır (hepsi sevk edilmişse plan boş)."""
    kalan = dict(sevk or {})
    sonuc = []
    for urun, miktar in coz:
        d = min(miktar, kalan.get(urun.pk, Decimal("0")))
        kalan[urun.pk] = kalan.get(urun.pk, Decimal("0")) - d
        if miktar - d > 0:
            sonuc.append((urun, miktar - d))
    return sonuc


def uretim_emri_eksikleri(emir: UretimEmri, graf=None) -> list:
    """Açık ÜS'nin SATINALMA ihtiyacı: kalan net planda eksik > 0 olan yaprak stoklar → [{"stok", "gerekli", "kullanilabilir", "eksik"}] (stok koduna
    göre). Revizenin kullandığı hesapla aynı: ÜS'nin kendi ayırmaları ona açık, başkalarının ayırmaları düşülmüş kullanılabilir; sevk edilen mamul
    plan dışı. Kapalı/iptal ÜS için boş. ``graf`` (urun_agaci.graf_yukle()) verilirse liste ekranında sorgu sayısı düşer."""
    if emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        return []
    coz = [(k.hedef_urun, k.hedef_miktar) for k in emir.kalemler.filter(silindi=False).select_related("hedef_urun").order_by("sira", "pk")]
    if not coz:
        return []
    sonuc = ihtiyac_hesapla(plan_kalemleri(coz, sevk_edilen_haritasi(emir)), graf=graf,
                            kullanilabilir=lambda idler: stok_ayirma.kullanilabilir_haritasi(idler, haric_emir=emir))
    eksikler = [{"stok": o["stok"], "gerekli": o["ihtiyac"], "kullanilabilir": o["ayrilan"], "eksik": o["eksik"]}
                for o in sonuc["ozet"] if o["yaprak"] and o["eksik"] is not None and o["eksik"] > 0]
    return sorted(eksikler, key=lambda x: x["stok"].kod)


def _onceki_ayrilan(ie: IstasyonEmri, stok_id) -> Decimal:
    """Bu emrin ONAYLI kayıtlarının bu çıktı için ÜS'ye ayırdığı toplam (kayıt çıktı satırı izleri)."""
    return sum((c.ayrilan for c in OperasyonKaydiCikti.objects.filter(
        kayit__istasyon_emri=ie, kayit__durum=OperasyonKaydi.Durum.ONAYLI, kayit__silindi=False, stok_id=stok_id, silindi=False)), Decimal("0"))


def _taslaklari_sigdir(ie: IstasyonEmri, yeni_net, kullanici=None) -> list:
    """Emrin açık TASLAK kayıtlarını yeni net çalıştırmaya sığdırır (eski sıradan): sığmayan hedef düşürülür (``kayit_hedefini_guncelle``), pay kalmayan
    kayıt iptal edilir. [{"kayit", "once", "sonra"}] döner (sonra None = iptal)."""
    ref = _referans_miktar(ie.operasyon)
    izin = max(yeni_net, Decimal("0"))
    degisen = []
    for k in ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK).select_related("operasyon").order_by("pk"):
        run = kayit_calistirma(k)
        if izin <= 0:
            operasyon_kaydi_sil(k, kullanici=kullanici)
            degisen.append({"kayit": k.no, "once": _ds(k.hedef_cikti_miktari), "sonra": None})
            continue
        if run > izin:
            once = k.hedef_cikti_miktari
            kayit_hedefini_guncelle(k, (izin * ref).quantize(Decimal("0.000001")), kullanici)
            degisen.append({"kayit": k.no, "once": _ds(once), "sonra": _ds(k.hedef_cikti_miktari)})
            run = izin
        izin -= run
    return degisen


def _emir_ozeti(emir: UretimEmri) -> dict:
    return {
        "kalemler": [{"stok": k.hedef_urun.kod, "miktar": _ds(k.hedef_miktar), "eldeki_ayrilan": _ds(k.eldeki_ayrilan)}
                     for k in emir.kalemler.filter(silindi=False).select_related("hedef_urun")],
        "ayirmalar": [{"stok": a.stok.kod, "miktar": _ds(a.miktar)} for a in stok_ayirma.emir_ayirmalari(emir)],
        "istasyon_emirleri": [{"no": ie.no, "operasyon": ie.operasyon.cikti.kod, "planlanan": _ds(ie.planlanan), "tamamlanan": _ds(ie.tamamlanan),
                               "durum": ie.durum} for ie in emir.istasyon_emirleri.filter(silindi=False).select_related("operasyon__cikti")],
    }


@transaction.atomic
def uretim_emri_revize(emir: UretimEmri, *, kalemler, tarih=None, aciklama="", kullanici=None, siparis_kalemleri=None) -> UretimEmri:
    """ÜS kalemlerini ``kalemler`` ile DEĞİŞTİRİR (adet ±, ekle/çıkar) ve ayırma + istasyon emirlerini yeniden planlar (docs/uretim-siparisi-plan.md):
    (1) sevk/fatura edilmiş miktarın altına düşürme reddedilir; (2) net plan, ÜS'nin kendi ayırmaları kullanılabilir sayılarak çözülür (kendi
    ürettiği/ayırdığı stok ona açık); (3) ayırmalar plana çekilir (azalan fark serbest); (4) her operasyon: planlanan = tamamlanan + yeni net çalıştırma,
    ihtiyaç = yeni net + daha önce onaylıdan ayrılan (kümülatif); yeni net 0 ve tamamlanan 0 ise IPTAL (taslak kayıtları iptal), tamamlanan varsa
    planlanan = tamamlanan (BITTI); gereken yeni/iptal edilmiş operasyona emir açılır/yeniden açılır; açık taslak kayıtlar yeni nete sığdırılır;
    (5) REVIZE revizyonu (önce/sonra). Yalnız ACIK sipariş. Tamamen atomik: doğrulama hatası hiçbir şey değiştirmez."""
    _planlama_kilidi()
    verilen = emir
    emir = UretimEmri.objects.select_for_update(of=("self",)).select_related("kaynak_siparis", "depo").get(pk=emir.pk)
    if emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        raise UretimHatasi("Yalnız açık üretim siparişi revize edilebilir.")
    coz = _kalemleri_coz(kalemler, siparis_kalemleri)
    yeni_toplam = {}
    for urun, miktar in coz:
        yeni_toplam[urun.pk] = yeni_toplam.get(urun.pk, Decimal("0")) + miktar
    sevk_haritasi = sevk_edilen_haritasi(emir)
    for stok_pk, sevk in sevk_haritasi.items():
        if yeni_toplam.get(stok_pk, Decimal("0")) < sevk:
            raise UretimHatasi(f"{Stok.objects.get(pk=stok_pk).kod}: sevk/fatura edilmiş miktarın ({sevk.normalize():f}) altına düşürülemez.")
    once = _emir_ozeti(emir)
    sonuc = ihtiyac_hesapla(plan_kalemleri(coz, sevk_haritasi), kullanilabilir=lambda idler: stok_ayirma.kullanilabilir_haritasi(idler, haric_emir=emir))

    emir.kalemler.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    _kalemleri_yaz(emir, coz, sonuc["ayrilan"], siparis_kalemleri, kullanici)
    _ayirmalari_esitle(emir, sonuc["ayrilan"], kullanici)

    taslak_degisen = []
    plan = {p["operasyon"].pk: p for p in sonuc["plan"]}
    mevcut = {ie.operasyon_id: ie for ie in IstasyonEmri.objects.select_for_update().filter(uretim_emri=emir, silindi=False).select_related("operasyon")}
    for opk, p in plan.items():
        ie = mevcut.get(opk)
        net = p["calistirma"]
        if ie is None:
            if net > 0:
                _istasyon_emri_ac(emir, p, kullanici)
            continue
        ie.seviye = p["seviye"]
        ie.planlanan = ie.tamamlanan + max(net, Decimal("0"))
        ie.ihtiyac = {}
        for c in p["ciktilar"]:
            if not c["surucu"]:
                continue
            toplam = c["net"] + _onceki_ayrilan(ie, c["stok"].pk)
            if toplam > 0:
                ie.ihtiyac[str(c["stok"].pk)] = _ds(toplam)
        if net <= 0 and ie.tamamlanan <= 0:
            ie.durum = IstasyonEmri.Durum.IPTAL
            ie.updated_by = kullanici
            ie.save()
            taslak_degisen += _taslaklari_sigdir(ie, Decimal("0"), kullanici)
            continue
        if ie.durum == IstasyonEmri.Durum.IPTAL:
            ie.durum = IstasyonEmri.Durum.BEKLIYOR                      # yeniden gerekli oldu
        ie.updated_by = kullanici
        ie.save()
        taslak_degisen += _taslaklari_sigdir(ie, net, kullanici)
        istasyon_emri_durum_guncelle(ie)
    for opk, ie in mevcut.items():
        if opk in plan or ie.durum == IstasyonEmri.Durum.IPTAL:
            continue
        ie.planlanan = ie.tamamlanan                                     # artık gerekmiyor
        if ie.tamamlanan <= 0:
            ie.durum = IstasyonEmri.Durum.IPTAL
        ie.updated_by = kullanici
        ie.save()
        taslak_degisen += _taslaklari_sigdir(ie, Decimal("0"), kullanici)
        istasyon_emri_durum_guncelle(ie)
    sonra = _emir_ozeti(emir)
    _revizyon_yaz(emir, UretimEmriRevizyon.Tur.REVIZE, tarih or timezone.localdate(), aciklama or "Revize",
                  {"once": once, "sonra": sonra, "taslak_kayitlar": taslak_degisen}, kullanici)
    if verilen is not emir:
        verilen.refresh_from_db()                                         # çağıranın nesnesi de güncel (revizyon_no vb.)
    return emir


@transaction.atomic
def siparis_revize(siparis, *, satirlar, tarih=None, aciklama="", kullanici=None) -> UretimEmri:
    """Onaylı SATIŞ siparişinin kalemlerini ``satirlar`` (teklif_siparis_olustur satır biçimi) ile DEĞİŞTİRİR ve aynı işlemde bağlı açık üretim
    siparişini revize eder (karar 6): ÜS kalemleri = siparişin üretime uygun kalemleri (miktar = sipariş kalem miktarı); üretime uygun kalem
    kalmazsa reddedilir (önce ÜS iptal edilmeli). Sipariş başlığı/fiyat mantığı teklif_siparis servisininkiyle aynıdır."""
    from core.services import teklif_siparis as ts
    if siparis.belge_tur != TeklifSiparis.BelgeTur.SIPARIS or siparis.yon != TeklifSiparis.Yon.SATIS:
        raise UretimHatasi("Yalnız SATIŞ siparişi revize edilebilir.")
    if siparis.silindi or siparis.durum != TeklifSiparis.Durum.ONAYLI:
        raise UretimHatasi("Yalnız onaylı sipariş revize edilebilir.")
    emir = siparis.uretim_emirleri.filter(silindi=False, durum=UretimEmri.Durum.ACIK).first()
    if emir is None:
        raise UretimHatasi("Bu siparişin açık bir üretim siparişi yok; siparişi normal yoldan düzenleyin.")
    try:
        _cari, _aday, hazir = ts._hazirla(cari_id=siparis.cari_id, satirlar=satirlar)
    except ts.TeklifSiparisHatasi as e:
        raise UretimHatasi(str(e))
    siparis.kalemler.filter(silindi=False).update(silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    ts._kalemleri_yaz(siparis, hazir, kullanici)
    uygun, _ = siparis_uretilebilir_kalemleri(siparis)
    if not uygun:
        raise UretimHatasi("Revize sonrası üretime uygun kalem kalmıyor; önce üretim siparişini iptal edin.")
    return uretim_emri_revize(emir, kalemler=[{"hedef_urun_id": k.stok_id, "hedef_miktar": k.miktar} for k in uygun],
                              tarih=tarih, aciklama=aciklama or f"Sipariş {siparis.belge_no} revizesi", kullanici=kullanici, siparis_kalemleri=list(uygun))

# === Üretim siparişi KAPANIŞ (adım 8): sevk = depolu satış faturası ===

def sevk_depolu_mu(siparis) -> bool:
    """Kaynak siparişin bağlı satış faturası var, silinmemiş, ONAYLI ve DEPOLU (depo seçili → stok çıkışı yazılmış) mı? Deposuz fatura stok
    çıkarmaz; bu yüzden sevk sayılmaz (karar 5)."""
    f = siparis.fatura if siparis.fatura_id else None
    return bool(f is not None and not f.silindi and f.depo_id and f.durum == f.Durum.ONAYLI)


def _kapanis_kontrol(emir: UretimEmri, kullanici=None, tarih=None) -> bool:
    """Sevk edilmiş (``sevk_dusen`` dolu) açık ÜS'nin tüm istasyon emirleri BITTI/IPTAL ise ÜS'yi KAPATIR (revizyon KAPANIS). Kapattıysa True."""
    if emir.durum != UretimEmri.Durum.ACIK or not emir.sevk_dusen:
        return False
    if emir.istasyon_emirleri.filter(silindi=False).exclude(durum__in=[IstasyonEmri.Durum.BITTI, IstasyonEmri.Durum.IPTAL]).exists():
        return False
    emir.durum = UretimEmri.Durum.KAPALI
    emir.kapanis_tarihi = tarih or timezone.localdate()
    emir.updated_by = kullanici
    emir.save(update_fields=["durum", "kapanis_tarihi", "updated_by", "updated_at"])
    _revizyon_yaz(emir, UretimEmriRevizyon.Tur.KAPANIS, emir.kapanis_tarihi, "Kapanış (sevk: satış faturası)", {"sevk_dusen": emir.sevk_dusen}, kullanici)
    return True


@transaction.atomic
def siparis_sevk_edildi(siparis, kullanici=None, tarih=None) -> str:
    """Satış siparişi faturalandığında (``siparis.fatura`` bağlandıktan SONRA) çağrılır. Depolu ve onaylı faturada: fatura satırı kadar ÜS'nin o stok
    ayırması düşer (sevk edilen mamul artık ayrılı değil; ``sevk_dusen``e yazılır), tüm istasyon emirleri bitmişse ÜS KAPALI olur. Deposuz fatura ÜS'ye
    dokunmaz ve UYARI metni döner ('' = uyarı yok). Açık ÜS yoksa sessiz ''."""
    emir = siparis.uretim_emirleri.select_for_update().filter(silindi=False, durum=UretimEmri.Durum.ACIK).first()
    if emir is None or not siparis.fatura_id:
        return ""
    if not sevk_depolu_mu(siparis):
        return ("Fatura depo seçilmeden/onaysız oluştu: stok çıkışı yazılmadı, üretim siparişi kapanmadı ve mamul ayırması düşmedi. "
                "Faturaya depo seçip yeniden oluşturun.")
    satis = {}
    for sat in siparis.fatura.satirlar.filter(silindi=False, stok__isnull=False):
        satis[sat.stok_id] = satis.get(sat.stok_id, Decimal("0")) + sat.miktar
    dusen = {int(k): Decimal(str(v)) for k, v in (emir.sevk_dusen or {}).items()}
    for stok_pk, miktar in satis.items():
        dus = min(stok_ayirma.ayrilan_miktar(emir, stok_pk), max(Decimal("0"), miktar - dusen.get(stok_pk, Decimal("0"))))
        if dus > 0:
            stok_ayirma.ayirma_ekle(emir, stok_pk, -dus, kullanici)
            dusen[stok_pk] = dusen.get(stok_pk, Decimal("0")) + dus
    emir.sevk_dusen = {str(k): _ds(v) for k, v in dusen.items()}
    emir.updated_by = kullanici
    emir.save(update_fields=["sevk_dusen", "updated_by", "updated_at"])
    if not _kapanis_kontrol(emir, kullanici, tarih):
        return "Sevk edildi; üretim siparişinin bitmemiş istasyon emirleri var, tamamlanınca kapanacak."
    return ""


@transaction.atomic
def siparis_fatura_silindi(siparis, kullanici=None, tarih=None) -> None:
    """Satış faturası silinince (sipariş bağı kopmuş olarak) çağrılır: sevk nedeniyle düşen ayırmalar aynen geri eklenir; ÜS KAPALI ise yeniden AÇILIR
    (revizyon YENIDEN_ACILIS). Sevkten etkilenmemiş ÜS'ye dokunmaz."""
    emir = (siparis.uretim_emirleri.select_for_update().filter(silindi=False, durum__in=[UretimEmri.Durum.ACIK, UretimEmri.Durum.KAPALI])
            .order_by("-pk").first())
    if emir is None or (emir.durum == UretimEmri.Durum.ACIK and not emir.sevk_dusen):
        return
    detay = {"sevk_dusen": emir.sevk_dusen}
    for k, v in (emir.sevk_dusen or {}).items():
        stok_ayirma.ayirma_ekle(emir, int(k), Decimal(str(v)), kullanici)
    emir.sevk_dusen = {}
    yeniden = emir.durum == UretimEmri.Durum.KAPALI
    emir.durum = UretimEmri.Durum.ACIK
    emir.kapanis_tarihi = None
    emir.updated_by = kullanici
    emir.save(update_fields=["sevk_dusen", "durum", "kapanis_tarihi", "updated_by", "updated_at"])
    _revizyon_yaz(emir, UretimEmriRevizyon.Tur.YENIDEN_ACILIS, tarih or timezone.localdate(),
                  "Yeniden açıldı (satış faturası silindi)" if yeniden else "Sevk geri alındı (satış faturası silindi)", detay, kullanici)


def uretim_emri_ilerleme(emir: UretimEmri):
    """{"toplam": iptal olmayan istasyon emri, "onayli": biten istasyon emri} — istasyon emri yoksa (eski emirler) operasyon kayıtlarından."""
    emirler = emir.istasyon_emirleri.filter(silindi=False).exclude(durum=IstasyonEmri.Durum.IPTAL)
    if emirler.exists():
        return {"toplam": emirler.count(), "onayli": emirler.filter(durum=IstasyonEmri.Durum.BITTI).count()}
    kayitlar = emir.operasyon_kayitlari.filter(silindi=False)
    return {"toplam": kayitlar.count(), "onayli": kayitlar.filter(durum=OperasyonKaydi.Durum.ONAYLI).count()}


def uretim_emri_silinebilir(emir: UretimEmri) -> bool:
    """Açık ve başlamamış ÜS kalıcı silinebilir; başlamış/kapalı/iptal olan yalnız iptal edilir (karar 9)."""
    return emir.durum == UretimEmri.Durum.ACIK and not _basladi_mi(emir)


def _basladi_mi(emir: UretimEmri) -> bool:
    """Başlamış üretim siparişi: bir istasyon emri BASLADI/BITTI ya da tamamlanan > 0, ya da onaylı / emre bağlı bir operasyon kaydı var."""
    if emir.istasyon_emirleri.filter(silindi=False).filter(Q(durum__in=[IstasyonEmri.Durum.BASLADI, IstasyonEmri.Durum.BITTI]) | Q(tamamlanan__gt=0)).exists():
        return True
    if emir.operasyon_kayitlari.filter(silindi=False).filter(Q(durum=OperasyonKaydi.Durum.ONAYLI) | Q(istasyon_emri__isnull=False)).exists():
        return True
    return StokHareket.objects.filter(operasyon_kaydi__uretim_emri=emir).exists()       # geri alınmış (soft-silinmiş) kayıtların hareketleri de iz bırakır (PROTECT)


@transaction.atomic
def uretim_emri_sil(emir: UretimEmri, kullanici=None) -> None:
    """Üretim siparişini KALICI olarak siler (CLAUDE.md'nin bu ekrana özel BİLİNÇLİ istisnası) — YALNIZ başlamamış olanı: bağlı taslak operasyon
    kayıtları, istasyon emirleri, ayırmalar ve revizyon kayıtları hiçbir iz kalmadan gider (stok hareketi zaten yoktur). Başlamış
    (istasyon emri başlamış/bitmiş, onaylı ya da emirden açılmış kayıt) sipariş silinmez, ``uretim_emri_iptal`` ile iptal edilir (karar 9)."""
    if emir.durum != UretimEmri.Durum.ACIK:
        raise UretimHatasi("Yalnız açık ve başlamamış üretim siparişi silinebilir; kapalı/iptal siparişin kaydı korunur.")
    if _basladi_mi(emir):
        raise UretimHatasi("Bu üretim siparişi başlamış (onaylı/emirden açılmış operasyon kaydı ya da başlamış istasyon emri var); silinemez, iptal edin.")
    emir.operasyon_kayitlari.all().delete()   # OperasyonKaydiGirdi CASCADE ile gider
    emir.delete()                             # istasyon emirleri, ayırmalar, revizyonlar, kalemler CASCADE


@transaction.atomic
def uretim_emri_iptal(emir: UretimEmri, kullanici=None, tarih=None) -> UretimEmri:
    """Üretim siparişini İPTAL eder (başlamış sipariş için tek çıkış): taslak operasyon kayıtları iptal edilir, bitmemiş istasyon emirleri IPTAL
    olur, ayırmalar kapanır (stok serbest kalır), durum IPTAL, revizyon kaydı yazılır. Onaylı kayıtların stok hareketleri/üretilen parçalar KALIR
    (serbest stok). Yalnız ACIK sipariş iptal edilebilir."""
    if emir.silindi or emir.durum != UretimEmri.Durum.ACIK:
        raise UretimHatasi("Yalnız açık üretim siparişi iptal edilebilir.")
    for k in emir.operasyon_kayitlari.filter(silindi=False, durum=OperasyonKaydi.Durum.TASLAK):
        operasyon_kaydi_sil(k, kullanici=kullanici)
    emir.istasyon_emirleri.filter(silindi=False).exclude(durum=IstasyonEmri.Durum.BITTI).update(durum=IstasyonEmri.Durum.IPTAL, updated_by=kullanici)
    for a in list(emir.ayirmalar.filter(silindi=False)):
        stok_ayirma.ayirma_ayarla(emir, a.stok_id, 0, kullanici)
    emir.durum = UretimEmri.Durum.IPTAL
    emir.updated_by = kullanici
    emir.save(update_fields=["durum", "updated_by", "updated_at"])
    _revizyon_yaz(emir, UretimEmriRevizyon.Tur.IPTAL, tarih or timezone.localdate(), "İptal", {}, kullanici)
    return emir


# === Operasyon Kayıtları — bir operasyonun fiilen çalıştırılması ===

def _sonraki_kayit_sira(yil):
    m = OperasyonKaydi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


def kaydi_girdi_satirlari(kayit: OperasyonKaydi):
    return (kayit.girdi_satirlari.filter(silindi=False)
            .select_related("girdi").order_by("sira", "pk"))


@transaction.atomic
def operasyon_kaydi_olustur(*, operasyon_id, depo_id, tarih, hedef_cikti_miktari,
                            uretim_emri=None, aciklama="", kullanici=None, fason_cari=None, fason_donus=None,
                            istasyon_emri=None) -> OperasyonKaydi:
    operasyon = (Operasyon.objects.filter(pk=operasyon_id, silindi=False)
                .select_related("istasyon", "cikti").first())
    if not operasyon:
        raise UretimHatasi("Operasyon bulunamadı.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if not depo:
        raise UretimHatasi("Depo bulunamadı.")
    miktar = _sayi_coz(hedef_cikti_miktari, "Hedef çıktı miktarı geçerli bir sayı olmalı.")
    if miktar <= 0:
        raise UretimHatasi("Hedef çıktı miktarı sıfırdan büyük olmalı.")
    girdi_satirlari = list(operasyon_girdileri(operasyon))
    if not girdi_satirlari:
        raise UretimHatasi("Operasyonda hiç girdi satırı yok.")
    if operasyon.tam_calistirma:                       # tam boy: çalıştırma sayısı tam sayı (hedef çıktı çıktı miktarının katına yükseltilir)
        miktar = _yukari_yuvarla(miktar / operasyon.cikti_miktar) * operasyon.cikti_miktar

    yil = tarih.year
    kayit = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_kayit_sira(yil)
                kayit = OperasyonKaydi.objects.create(
                    yil=yil, sira=sira, no=f"OP-{yil}-{sira:04d}",
                    operasyon=operasyon, uretim_emri=uretim_emri, istasyon_emri=istasyon_emri, depo=depo, tarih=tarih,
                    hedef_cikti_miktari=miktar, aciklama=(aciklama or "").strip(),
                    durum=OperasyonKaydi.Durum.TASLAK, fason_cari=fason_cari, fason_donus=fason_donus,
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if kayit is None:
        raise UretimHatasi("Kayıt numarası üretilemedi; tekrar deneyin.")

    for i, satir in enumerate(girdi_satirlari, start=1):
        gerekli = yuvarla(miktar * satir.miktar / operasyon.cikti_miktar, 6)
        OperasyonKaydiGirdi.objects.create(
            kayit=kayit, girdi=satir.girdi, planlanan_miktar=gerekli,
            gerceklesen_miktar=gerekli, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return kayit


def _tam_boy_kontrol(kayit: OperasyonKaydi, satir: OperasyonKaydiGirdi, miktar: Decimal):
    """Tam boy zorunlu operasyonda BOY birimli girdinin gerçekleşen miktarı tam sayı olmalı."""
    if kayit.operasyon.tam_calistirma and _boy_birimli_mi(satir.girdi) and miktar != miktar.to_integral_value():
        raise UretimHatasi(TAM_BOY_HATASI)


@transaction.atomic
def operasyon_kaydi_girdi_guncelle(satir: OperasyonKaydiGirdi, *, gerceklesen_miktar,
                                   kullanici=None):
    if satir.kayit.durum != OperasyonKaydi.Durum.TASLAK:
        raise UretimHatasi("Yalnız taslak kaydın girdileri düzenlenebilir.")
    m = _sayi_coz(gerceklesen_miktar, "Gerçekleşen miktar geçerli bir sayı olmalı.")
    if m < 0:
        raise UretimHatasi("Gerçekleşen miktar negatif olamaz.")
    _tam_boy_kontrol(satir.kayit, satir, m)
    satir.gerceklesen_miktar = m
    satir.updated_by = kullanici
    satir.save(update_fields=["gerceklesen_miktar", "updated_by", "updated_at"])
    return satir


def kayit_ciktilari(kayit: OperasyonKaydi) -> list:
    """Kaydın çıktıları: [(anahtar, Stok, BEKLENEN miktar, boy_mm)] — ana çıktı + yan çıktılar (çalıştırma başına tanım × çalıştırma)."""
    operasyon = kayit.operasyon
    tanim = tanim_ciktilari(operasyon)
    if not tanim:                                                             # (güvenlik) çıktı satırı yoksa yalnız ana çıktı
        return [("ana", operasyon.cikti, kayit.hedef_cikti_miktari, operasyon.boy_mm)]
    referans = tanim[0]
    calistirma = kayit.hedef_cikti_miktari / referans.miktar
    sonuc = [("ana", referans.stok, kayit.hedef_cikti_miktari, referans.boy_mm)]
    for c in tanim[1:]:
        sonuc.append((f"yan{c.pk}", c.stok, yuvarla(calistirma * c.miktar, 6), c.boy_mm))
    return sonuc


def kayit_gelen_adetleri(kayit: OperasyonKaydi, ciktilar=None) -> dict:
    """{anahtar: stoğa girecek adet}. Fason dönüşte fasoncudan GELEN adet — TEK yapı: ``gelen`` {stok pk: adet} tüm çıktılar için (referans dahil).
    Boş: ÜRET'te beklenen gelmiş sayılır, PARÇALA'da 0. Gelen > beklenen ya da negatif reddedilir; ÜRET'te ana
    çıktı 0 olamaz, PARÇALA'da en az bir çıktı > 0 olmalı. Fasonsuz kayıtta her zaman beklenen adet."""
    ciktilar = ciktilar if ciktilar is not None else kayit_ciktilari(kayit)
    if not kayit.fason_cari_id:
        return {a: m for a, _st, m, _b in ciktilar}
    parcala = kayit.operasyon.tur == Operasyon.Tur.PARCALA
    gelen = kayit.gelen or {}
    sonuc = {}
    for anahtar, stok, beklenen, _boy in ciktilar:
        ham = gelen.get(str(stok.pk))
        try:
            g = (Decimal("0") if parcala else beklenen) if ham in (None, "") else Decimal(str(ham))
        except Exception:
            raise UretimHatasi(f"{stok.kod}: gelen adet geçerli bir sayı olmalı.")
        if g < 0:
            raise UretimHatasi(f"{stok.kod}: gelen adet negatif olamaz.")
        if g > beklenen:
            raise UretimHatasi(f"{stok.kod}: gelen adet ({g.normalize():f}) beklenenden ({beklenen.normalize():f}) fazla olamaz.")
        if anahtar == "ana" and g <= 0 and not parcala:
            raise UretimHatasi(f"{stok.kod}: ana çıktının gelen adedi sıfırdan büyük olmalı.")
        sonuc[anahtar] = g
    if parcala and all(g <= 0 for g in sonuc.values()):
        raise UretimHatasi("En az bir çıktı için gelen adet girin (hepsi 0 olamaz).")
    return sonuc


def parcala_hedef_gelenden(operasyon: Operasyon, gelen: dict) -> Decimal:
    """PARÇALA fason dönüşü: GELEN adetlerden ({stok pk: adet}) çalıştırma = max_i(gelen_i / miktar_i) — tam boy açıksa ⌈·⌉ — ve hedef (referans çıktı)
    = çalıştırma × referans miktarı (6 ondalık, yukarı; beklenen_i = çalıştırma × miktar_i ≥ gelen_i garantisi). Gelen yoksa 0."""
    tanim = tanim_ciktilari(operasyon)
    if not tanim:
        return Decimal("0")
    calistirma = Decimal("0")
    for c in tanim:
        if not c.surucu:
            continue
        g = gelen.get(str(c.stok_id), gelen.get(c.stok_id))
        g = Decimal(str(g)) if g not in (None, "") else Decimal("0")
        calistirma = max(calistirma, g / c.miktar)
    if operasyon.tam_calistirma:
        calistirma = _yukari_yuvarla(calistirma)
    return (calistirma * tanim[0].miktar).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


@transaction.atomic
def kayit_hedefini_guncelle(kayit: OperasyonKaydi, hedef, kullanici=None) -> OperasyonKaydi:
    """TASLAK kaydın hedef çıktı miktarını (referans çıktı) ve girdi satırlarının planlanan miktarını yeniden yazar (PARÇALA fason dönüşünde
    çalıştırma gelen adetlerden türetilince). Elle düzeltilmiş gerçekleşen miktar (planlanandan farklı) korunur, diğerleri plana eşitlenir."""
    if kayit.silindi or kayit.durum != OperasyonKaydi.Durum.TASLAK:
        raise UretimHatasi("Yalnız taslak kaydın hedef miktarı değiştirilebilir.")
    hedef = _sayi_coz(hedef, "Hedef çıktı miktarı geçerli bir sayı olmalı.")
    if hedef <= 0:
        raise UretimHatasi("Hedef çıktı miktarı sıfırdan büyük olmalı.")
    operasyon = kayit.operasyon
    tanim = tanim_ciktilari(operasyon)
    referans_miktar = tanim[0].miktar if tanim else operasyon.cikti_miktar
    tanim_girdi = {g.girdi_id: g.miktar for g in operasyon_girdileri(operasyon)}
    kayit.hedef_cikti_miktari = hedef
    kayit.updated_by = kullanici
    kayit.save(update_fields=["hedef_cikti_miktari", "updated_by", "updated_at"])
    for satir in kaydi_girdi_satirlari(kayit):
        gerekli = yuvarla(hedef * tanim_girdi.get(satir.girdi_id, Decimal("0")) / referans_miktar, 6)
        elle = satir.gerceklesen_miktar != satir.planlanan_miktar
        satir.planlanan_miktar = gerekli
        if not elle:
            satir.gerceklesen_miktar = gerekli
        satir.updated_by = kullanici
        satir.save(update_fields=["planlanan_miktar", "gerceklesen_miktar", "updated_by", "updated_at"])
    return kayit


@transaction.atomic
def operasyon_kaydi_onayla(kayit: OperasyonKaydi, kullanici=None) -> OperasyonKaydi:
    """TASLAK → ONAYLI: her girdi satırı için depo ÇIKIŞ (gerceklesen_miktar==0 olanlar
    hatasız ATLANIR — 'bu girdiye bu seferlik gerek kalmadı' meşru bir durumdur) + çıktı
    için depo GİRİŞ StokHareket'i (Kaynak=URETIM) yazar; hiçbir YevmiyeFisi/YevmiyeSatir
    üretmez. İdempotent (zaten onaylıysa sessiz döner). Yetersiz stok varsa tüm işlem geri
    alınır (atomic).

    Girdilerin ÇIKIŞ'ta tükettiği FIFO maliyet katmanlarının toplamı, çıktının GİRİŞ'ine
    (hedef_cikti_miktari'ne bölünerek) yeni bir katman olarak yazılır — bkz. core.services.
    stok_maliyet, core.services.hareket.hareket_ekle. Bir girdi kısmen/hiç karşılanamazsa
    (katman yetersiz) çıktı 'tahmini' işaretlenir; hiçbir girdi katmanı yoksa çıktı hiç
    katmansız kalır (0 TL YAZILMAZ — bilinmiyor, tahmin edilmiyor)."""
    if kayit.silindi:
        raise UretimHatasi("İptal edilmiş kayıt onaylanamaz.")
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
        return kayit
    if kayit.istasyon_emri_id and (kayit.istasyon_emri.durum == IstasyonEmri.Durum.IPTAL or kayit.uretim_emri.durum != UretimEmri.Durum.ACIK):
        raise UretimHatasi("Bu kaydın üretim siparişi/istasyon emri iptal edilmiş ya da kapanmış; kayıt onaylanamaz.")
    satirlar = list(kaydi_girdi_satirlari(kayit))
    for satir in satirlar:
        _tam_boy_kontrol(kayit, satir, satir.gerceklesen_miktar)
    operasyon = kayit.operasyon
    tam_ciktilar = kayit_ciktilari(kayit)                        # BEKLENEN çıktılar (girdi tüketimi bunlara göre planlıdır)
    gelen = kayit_gelen_adetleri(kayit, tam_ciktilar)            # stoğa girecek / fason bedeli ödenecek adetler (fasonsuzda = beklenen)
    cikti_stok = {a: st for a, st, _m, _b in tam_ciktilar}
    beklenen = {a: m for a, _st, m, _b in tam_ciktilar}
    ciktilar = [(a, st.pk, gelen[a], b) for a, st, _m, b in tam_ciktilar if gelen[a] > 0]
    gelmeyen = [(a, st.pk, b) for a, st, _m, b in tam_ciktilar if gelen[a] <= 0]       # hiç gelmeyen yan çıktı: yalnız fire kaydı
    # FASON dönüş: girdiler carinin fason deposundan düşer, çıktıların her birine kendi FasonFiyat bedeli eklenir (fiyat yoksa onay engellenir).
    girdi_depo_id, fason, fason_bekleyen = kayit.depo_id, None, False
    if kayit.fason_cari_id:
        from core.services import depo as depo_servis
        from core.services import fason_maliyet
        from core.services.fason import FasonHatasi
        fason_deposu = depo_servis.fason_deposu(kayit.fason_cari_id)
        if fason_deposu is None:
            raise UretimHatasi(f"{kayit.fason_cari.unvan} için bağlı bir fason deposu yok (Depolar ekranında fasoncuyu bir depoya bağlayın).")
        girdi_depo_id = fason_deposu.pk
        try:
            fason = fason_maliyet.fiyatlari_coz(kayit, [(a, cikti_stok[a], m) for a, _sid, m, _b in ciktilar])
        except FasonHatasi as e:
            raise UretimHatasi(str(e))
        fason_bekleyen = fason_maliyet.fason_bekliyor(kayit)
    toplam_girdi_maliyeti = Decimal("0")
    toplam_girdi_usd = Decimal("0")
    herhangi_biri_tahmini = False
    for satir in satirlar:
        if satir.gerceklesen_miktar == 0:
            continue
        try:
            girdi_hareketi = hareket_ekle(
                stok_id=satir.girdi_id, depo_id=girdi_depo_id, tarih=kayit.tarih,
                tur=StokHareket.Tur.CIKIS, miktar=satir.gerceklesen_miktar,
                aciklama=f"Operasyon kaydı {kayit.no}", kaynak=StokHareket.Kaynak.URETIM,
                operasyon_kaydi_girdi=satir, operasyon_kaydi=kayit, kullanici=kullanici)
        except HareketHatasi as e:
            raise UretimHatasi(str(e))
        # Girdi, o anki ağırlıklı ortalamayla değerlenir (bkz. core.services.stok_ortalama).
        if girdi_hareketi.maliyet_durumu == StokHareket.MaliyetDurumu.YOK:
            herhangi_biri_tahmini = True
        else:
            toplam_girdi_maliyeti += girdi_hareketi.tutar_try or Decimal("0")
            toplam_girdi_usd += girdi_hareketi.tutar_usd or Decimal("0")
            if girdi_hareketi.maliyet_durumu != StokHareket.MaliyetDurumu.KESIN:
                herhangi_biri_tahmini = True
    # Çıktı girişinin tutarı girdi çıkışlarının ağırlıklı ortalama maliyet toplamıdır; girdi maliyeti sonradan değişirse çıktıya (ve onu
    # kullanan sonraki üretime) zincirleme yayılır ve maliyet aktarım fişi güncellenir (bkz. core.services.stok_fis.uretim_senkronla).
    # YAN ÇIKTI varsa her biri için de GİRİŞ yazılır; toplam girdi maliyeti BOY ORANINA göre (miktar × boy_mm) paylaştırılır — TL ve USD
    # ayrı, yuvarlama farkı ana çıktıya (toplam birebir korunur).
    from core.services import stok_fis
    tanim_satiri = {c.stok_id: c for c in tanim_ciktilari(operasyon)}
    agirlik = {}                                   # paylaştırma ağırlığı: tanımın pay anahtarına göre, GELEN adetten (fire gelenlere yayılır); YÜZDE sabit
    for k, sid, m, boy in ciktilar:
        if operasyon.pay_anahtari == Operasyon.PayAnahtari.YUZDE:
            c = tanim_satiri.get(sid)
            agirlik[k] = (c.yuzde if c is not None and c.yuzde else Decimal("0"))
        elif operasyon.pay_anahtari == Operasyon.PayAnahtari.ESIT:
            agirlik[k] = m
        else:
            agirlik[k] = (m * boy) if boy else Decimal("0")
    bilinen = toplam_girdi_maliyeti > 0
    # ONAY ANI SNAPSHOT'I: çıktı satırları (miktar, boy, pay oranı) kayda yazılır; sonraki yeniden paylaştırmalar bunlara göre yapılır.
    toplam_agirlik = sum(agirlik.values(), Decimal("0"))
    referans_anahtar = "ana" if any(a == "ana" for a, _s, _m, _b in ciktilar) else ciktilar[0][0]     # PARÇALA: referans gelmediyse ilk gelen çıktı
    for anahtar, stok_id, miktar, boy in ciktilar:
        if len(ciktilar) == 1 or not toplam_agirlik or any(a <= 0 for a in agirlik.values()):
            oran = Decimal("1") if anahtar == referans_anahtar else Decimal("0")
        else:
            oran = (agirlik[anahtar] / toplam_agirlik).quantize(Decimal("0.0000000001"))
        f = fason[anahtar] if fason else None
        OperasyonKaydiCikti.objects.create(
            kayit=kayit, stok_id=stok_id, miktar=miktar, beklenen_miktar=beklenen[anahtar], boy_mm=boy, pay_orani=oran, ana_mi=(anahtar == "ana"),
            agirlik=agirlik[anahtar],
            fason_birim_fiyat=f["fiyat"].birim_fiyat if f else None, fason_para_birimi=f["fiyat"].para_birimi if f else "",
            fason_kur=f["kur"] if f else None, fason_tutar=f["tutar_try"] if f else None, fason_tutar_usd=f["tutar_usd"] if f else None,
            created_by=kullanici, updated_by=kullanici)
    for anahtar, stok_id, boy in gelmeyen:
        OperasyonKaydiCikti.objects.create(kayit=kayit, stok_id=stok_id, miktar=Decimal("0"), beklenen_miktar=beklenen[anahtar], boy_mm=boy,
                                           pay_orani=Decimal("0"), agirlik=Decimal("0"), ana_mi=False, fason_tutar=Decimal("0"), fason_tutar_usd=Decimal("0"),
                                           created_by=kullanici, updated_by=kullanici)
    pay_try = stok_fis.maliyet_paylastir(toplam_girdi_maliyeti if bilinen else Decimal("0.00"), agirlik, referans_anahtar)
    pay_usd = stok_fis.maliyet_paylastir(toplam_girdi_usd if bilinen else Decimal("0.00"), agirlik, referans_anahtar)
    for anahtar, stok_id, miktar, _boy in ciktilar:
        f_try = fason[anahtar]["tutar_try"] if fason else Decimal("0")
        f_usd = fason[anahtar]["tutar_usd"] if fason else Decimal("0")
        hareket_ekle(
            stok_id=stok_id, depo_id=kayit.depo_id, tarih=kayit.tarih,
            tur=StokHareket.Tur.GIRIS, miktar=miktar,
            aciklama=f"Operasyon kaydı {kayit.no}" + ("" if anahtar == "ana" else " (yan çıktı)"),
            kaynak=StokHareket.Kaynak.URETIM, operasyon_kaydi=kayit,
            tahmini=herhangi_biri_tahmini or (fason_bekleyen and f_try > 0),
            giris_tutar_try=(pay_try[anahtar] + f_try) if bilinen else None,
            giris_tutar_usd=(pay_usd[anahtar] + f_usd) if bilinen else None,
            kullanici=kullanici)
    kayit.durum = OperasyonKaydi.Durum.ONAYLI
    kayit.updated_by = kullanici
    kayit.save(update_fields=["durum", "updated_by", "updated_at"])
    try:
        stok_fis.uretim_senkronla(kayit, kullanici=kullanici)    # 15x→15x maliyet aktarım fişi
    except stok_fis.MaliyetHatasi as e:
        raise UretimHatasi(str(e))
    _istasyon_emri_onay_etkisi(kayit, kullanici)             # üretim siparişi: ayırma düş/ekle, tamamlanan, durum
    return kayit


@transaction.atomic
def operasyon_kaydi_sil(kayit: OperasyonKaydi, kullanici=None, *, onayli_geri_al=False) -> OperasyonKaydi:
    """Taslak kaydı iptal eder. ONAYLI kayıt varsayılan olarak iptal EDİLEMEZ; ``onayli_geri_al=True`` verilirse (yalnız servis
    çağrısı — ekranda düğmesi yok) kaydın TÜM stok hareketleri (girdi çıkışları + ana ve yan çıktı girişleri) geri alınır, maliyet
    aktarım fişi iptal edilir ve kayıt silinir. Çıktı başka üretimde kullanıldıysa (eldeki yetmez) işlem hata verir ve hiçbir şey
    değişmez."""
    if kayit.silindi:
        return kayit
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
        if not onayli_geri_al:
            raise UretimHatasi("Onaylı kayıt iptal edilemez.")
        from core.services.hareket import hareket_sil
        from core.services.yevmiye import fis_iptal
        hareketler = list(StokHareket.objects.filter(operasyon_kaydi=kayit, silindi=False).order_by("tur", "-pk"))
        try:
            for h in [h for h in hareketler if h.tur == StokHareket.Tur.GIRIS] + \
                     [h for h in hareketler if h.tur == StokHareket.Tur.CIKIS]:
                hareket_sil(h, kullanici=kullanici)             # önce çıktılar (yan çıktılar dâhil), sonra girdi çıkışları
        except HareketHatasi as e:
            raise UretimHatasi(str(e))
        if kayit.fis_id and not kayit.fis.silindi:
            fis_iptal(kayit.fis, kullanici=kullanici)
        _istasyon_emri_geri_al_etkisi(kayit, kullanici)          # üretim siparişi: ayırma izleri + tamamlanan geri sarılır (kayıt hâlâ ONAYLI iken)
    kayit.silindi = True
    kayit.silindi_at = timezone.now()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    if kayit.istasyon_emri_id:
        istasyon_emri_durum_guncelle(kayit.istasyon_emri)
    return kayit
