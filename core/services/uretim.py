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

from decimal import ROUND_CEILING, Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (
    Depo, IsIstasyonu, Operasyon, OperasyonGirdi, OperasyonKaydi, OperasyonKaydiCikti, OperasyonKaydiGirdi, OperasyonYanCikti,
    Stok, StokHareket, TeklifSiparis, UretimEmri, UretimEmriKalemi,
)
from core.sayi import SayiHatasi, parse_tr, yuvarla
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
                      yan_cikti_sayisi=Count("yan_ciktilar", filter=Q(yan_ciktilar__silindi=False), distinct=True))
            .order_by("istasyon__kod", "cikti__kod"))


# === Operasyon Tanımları LİSTESİ: seri (A/C/ortak), bağlantı, filtre, sekme, özet — tek yükleme, bellekte, sabit sorgu sayısı ===

SERI_ETIKET = {"A": "A tipi", "C": "C tipi", "ORTAK": "Ortak", "DIGER": "Diğer", "BAGLANTISIZ": "Bağlantısız"}
SERI_KOK_ONEKI = {"A": "152-10-", "C": "152-22-"}      # bitmiş ürün (kök) kodu öneki → seri


def operasyonlari_yukle() -> list:
    """Aktif operasyonlar + girdiler + yan çıktılar + stok/birim bilgisi: TOPLAM 4 sorgu (operasyon sayısından bağımsız)."""
    return list(
        Operasyon.objects.filter(silindi=False)
        .select_related("istasyon", "cikti__uretim_birimi")
        .prefetch_related(
            Prefetch("girdiler", queryset=OperasyonGirdi.objects.filter(silindi=False).select_related("girdi__uretim_birimi").order_by("sira", "pk")),
            Prefetch("yan_ciktilar", queryset=OperasyonYanCikti.objects.filter(silindi=False).select_related("stok").order_by("sira", "pk")))
        .order_by("istasyon__kod", "cikti__kod"))


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
        for y in op.yan_ciktilar.all():
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
        baslar = (op.cikti_id,) + tuple(y.stok_id for y in op.yan_ciktilar.all())
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
    yanlar = list(op.yan_ciktilar.all())
    birim = (op.cikti.uretim_birimi.ad or "").lower()
    tam_liste = "\n".join(f"{sade(g.miktar)} {g.girdi.uretim_birimi.kisa_ad or g.girdi.uretim_birimi.ad} {g.girdi.kod}" for g in girdiler)
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
            "tam_boy": op.tam_calistirma, "yan_sayisi": len(op.yan_ciktilar.all()), "kullanan_sayisi": len({o.pk for o in kul}),
            "bitmis_urun": kok, "ozet": _girdi_ozeti(op),
            "ara_metin": buyuk_harf_tr(" ".join([op.cikti.kod, op.cikti.ad] + [f"{g.girdi.kod} {g.girdi.ad}" for g in op.girdiler.all()]
                                                + [f"{y.stok.kod} {y.stok.ad}" for y in op.yan_ciktilar.all()]))}

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
    return aktif_operasyonlar().values_list("cikti_id", flat=True)


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
    return operasyon.yan_ciktilar.filter(silindi=False).select_related("stok").order_by("sira", "pk")


def ana_cikti_payi(cikti_miktar, boy_mm, yanlar) -> Decimal:
    """Bir çalıştırmada ANA çıktının girdi maliyetinden/tüketiminden alacağı pay (0-1): ağırlık = miktar × boy_mm (ana + yan çıktılar).
    Onaydaki maliyet paylaştırmasıyla AYNI kural: yan çıktı yoksa ya da herhangi bir ağırlık eksik/sıfırsa ana çıktı %100. ``yanlar``:
    ``miktar`` ve ``boy_mm`` alanlı satırlar (OperasyonYanCikti), bir çalıştırma başına miktarlarla."""
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


def _yan_ciktilari_yaz(operasyon, yan_ciktilar, kullanici):
    for i, (stok, miktar, boy) in enumerate(yan_ciktilar, start=1):
        OperasyonYanCikti.objects.create(operasyon=operasyon, stok=stok, miktar=miktar, boy_mm=boy, sira=i * 10,
                                         created_by=kullanici, updated_by=kullanici)


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


@transaction.atomic
def operasyon_olustur(*, istasyon_id, cikti_id, cikti_miktar, satirlar, kullanici=None, boy_mm=None, yan_ciktilar=None, tam_boy=None) -> Operasyon:
    """``tam_boy`` None ise varsayılan: BOY birimli girdi varsa True; True/False verilirse kullanıcının seçimi."""
    istasyon = _istasyon_coz(istasyon_id)
    cikti = _cikti_coz(cikti_id)
    if Operasyon.objects.filter(silindi=False, cikti=cikti).exists():
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
    _yan_ciktilari_yaz(operasyon, yan_ciktilar, kullanici)
    for i, (girdi, miktar) in enumerate(satirlar, start=1):
        OperasyonGirdi.objects.create(
            operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return operasyon


@transaction.atomic
def operasyon_guncelle(operasyon: Operasyon, *, istasyon_id, cikti_miktar, satirlar,
                       kullanici=None, boy_mm=_KORU, yan_ciktilar=None, tam_boy=None) -> Operasyon:
    """``tam_boy`` verilmezse (None) mevcut seçim KORUNUR (girdi birimi değişse de otomatik değişmez). ``boy_mm`` verilmezse ana çıktı boyu, ``yan_ciktilar`` verilmezse (None) yan çıktılar KORUNUR; [] verilirse yan çıktılar silinir."""
    if operasyon.silindi:
        raise UretimHatasi("Silinmiş operasyon düzenlenemez.")
    istasyon = _istasyon_coz(istasyon_id)
    cm = _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı.")
    if cm <= 0:
        raise UretimHatasi("Çıktı miktarı sıfırdan büyük olmalı.")
    _girdi_satirlarini_dogrula(operasyon.cikti, satirlar)
    yeni_boy = operasyon.boy_mm if boy_mm is _KORU else _boy_coz(boy_mm, "Ana çıktı boyu (mm) geçerli bir sayı olmalı.")
    yeni_yanlar = ([(y.stok, y.miktar, y.boy_mm) for y in operasyon_yan_ciktilari(operasyon)]
                   if yan_ciktilar is None else list(yan_ciktilar))
    _yan_ciktilari_dogrula(operasyon.cikti, satirlar, yeni_yanlar, yeni_boy)
    operasyon.girdiler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    if yan_ciktilar is not None:
        operasyon.yan_ciktilar.filter(silindi=False).update(
            silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
        _yan_ciktilari_yaz(operasyon, yeni_yanlar, kullanici)
    operasyon.boy_mm = yeni_boy
    operasyon.istasyon = istasyon
    operasyon.cikti_miktar = cm
    if tam_boy is not None:
        operasyon.tam_calistirma = bool(tam_boy)
    operasyon.updated_by = kullanici
    operasyon.save(update_fields=[
        "istasyon", "cikti_miktar", "tam_calistirma", "boy_mm", "updated_by", "updated_at"])
    for i, (girdi, miktar) in enumerate(satirlar, start=1):
        OperasyonGirdi.objects.create(
            operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return operasyon


def operasyon_sil(operasyon: Operasyon, kullanici=None) -> Operasyon:
    if operasyon.silindi:
        return operasyon
    if operasyon.kayitlar.filter(silindi=False).exists():
        raise UretimHatasi("Bu operasyona bağlı kayıt var; silinemez.")
    operasyon.silindi = True
    operasyon.silindi_at = timezone.now()
    operasyon.updated_by = kullanici
    operasyon.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return operasyon


# === İhtiyaç Hesapla — özyinelemeli, salt-okunur, hiçbir kayıt/stok hareketi üretmez ===

def ihtiyac_hesapla(kalemler, *, boy_yuvarla=True, pay_dus=False, graf=None):
    """kalemler: [(Stok hedef, Decimal miktar), ...].

    ``boy_yuvarla=False`` → tam boy yukarı yuvarlaması KAPALI: bütün çalıştırmalar kesirli (gerçek tüketim; maliyet görünümleri için).
    ``pay_dus=True`` → yan çıktılı kesimde girdi talebi ana çıktının boy payı (``ana_cikti_payi``) kadar düşülür. Varsayılanlarla
    davranış eskisiyle BİREBİR aynıdır. ``graf`` (core.services.urun_agaci.Graf) verilirse operasyon/girdi/yan çıktı bilgisi bellekten
    okunur (sorgu yok).

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
    yan_onbellek = {}                                  # stok.pk -> yan çıktı satırları (YALNIZ BİLGİ: hesaba katılmaz)

    def op_bul(stok):
        if stok.pk not in onbellek:
            if graf is not None:
                operasyon = graf.op_of.get(stok.pk)
                satirlar = list(graf.girdiler.get(operasyon.pk, [])) if operasyon else []
                yan_onbellek[stok.pk] = list(graf.yanlar.get(operasyon.pk, [])) if operasyon else []
            else:
                operasyon = (Operasyon.objects.filter(cikti=stok, silindi=False).select_related("istasyon").first())
                satirlar = (list(operasyon.girdiler.filter(silindi=False).select_related("girdi").order_by("sira", "pk"))
                            if operasyon else [])
                yan_onbellek[stok.pk] = list(operasyon_yan_ciktilari(operasyon)) if operasyon else []
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
        _, satirlar = op_bul(stok)
        for satir in satirlar:
            ebeveynler.setdefault(satir.girdi_id, set()).add(stok.pk)
            kesfet(satir.girdi, yol | {stok.pk})
        bitti.add(stok.pk)

    for stok, _ in hedefler:
        kesfet(stok, frozenset())

    seviye = {}

    def seviye_bul(pk):
        if pk not in seviye:
            seviye[pk] = 0 if not ebeveynler.get(pk) else 1 + max(seviye_bul(p) for p in ebeveynler[pk])
        return seviye[pk]

    for pk in stoklar:
        seviye_bul(pk)

    # --- 2. aşama: seviye sırasıyla toplanmış talep → (tam boyda yukarı yuvarlanmış) çalıştırma → girdilere aktarım
    kok_talep = {}
    for stok, miktar in hedefler:
        kok_talep[stok.pk] = kok_talep.get(stok.pk, Decimal("0")) + miktar
    talep = dict(kok_talep)
    plan_map, pay_map = {}, {}
    for pk in sorted(stoklar, key=lambda k: (seviye[k], sira_no[k])):
        operasyon, satirlar = op_bul(stoklar[pk])
        if operasyon is None:
            continue
        ihtiyac = talep.get(pk, Decimal("0"))
        cm = operasyon.cikti_miktar
        tam = operasyon.tam_calistirma and boy_yuvarla
        if tam:
            calistirma = _yukari_yuvarla(ihtiyac / cm)
            uretilecek = calistirma * cm
        else:
            calistirma = ihtiyac / cm
            uretilecek = ihtiyac                     # kesirli çalıştırmada hedef talebin kendisi (bölme artığı yok)
        pay = ana_cikti_payi(cm, operasyon.boy_mm, yan_onbellek.get(pk, [])) if pay_dus else Decimal("1")
        pay_map[pk] = pay
        plan_map[pk] = {"stok": stoklar[pk], "operasyon": operasyon, "ihtiyac": ihtiyac, "calistirma": calistirma,
                        "uretilecek": uretilecek, "fazla": uretilecek - ihtiyac, "tam": tam,
                        "yan_ciktilar": [{"stok": y.stok, "miktar": calistirma * y.miktar, "boy_mm": y.boy_mm}
                                         for y in yan_onbellek.get(pk, [])]}
        for satir in satirlar:
            talep[satir.girdi_id] = talep.get(satir.girdi_id, Decimal("0")) + calistirma * satir.miktar * pay

    # --- ağaç (gösterim) + özet sırası: DFS, tam çalıştırmalı stok yalnız İLK geçtiği yerde açılır
    ozet_sira = []
    acildi = set()

    def gez(stok, miktar, kok=False):
        operasyon, satirlar = op_bul(stok)
        if not kok and stok.pk not in ozet_sira:
            ozet_sira.append(stok.pk)
        if operasyon is None:
            return {"stok": stok, "miktar": miktar, "istasyon": None,
                    "operasyon": None, "yaprak": True, "cocuklar": []}
        p = plan_map[stok.pk]
        dugum = {"stok": stok, "miktar": miktar, "istasyon": operasyon.istasyon, "operasyon": operasyon, "yaprak": False,
                 "tam": p["tam"], "calistirma": p["calistirma"], "uretilecek": p["uretilecek"], "fazla": p["fazla"],
                 "ihtiyac_toplam": p["ihtiyac"], "yan_ciktilar": p["yan_ciktilar"], "cocuklar": []}
        if p["tam"]:
            if stok.pk in acildi:
                dugum["tekrar"] = True
                return dugum
            acildi.add(stok.pk)
            calistirma = p["calistirma"]
        else:
            calistirma = miktar / operasyon.cikti_miktar
        pay = pay_map[stok.pk]
        dugum["cocuklar"] = [gez(satir.girdi, calistirma * satir.miktar * pay) for satir in satirlar]
        return dugum

    agac = [gez(stok, miktar, kok=True) for stok, miktar in hedefler]

    ozet = []
    for pk in ozet_sira:
        operasyon, _ = op_bul(stoklar[pk])
        p = plan_map.get(pk)
        ozet.append({
            "stok": stoklar[pk], "operasyon": operasyon, "istasyon": operasyon.istasyon if operasyon else None,
            "toplam_miktar": talep.get(pk, Decimal("0")) - kok_talep.get(pk, Decimal("0")), "yaprak": operasyon is None,
            "ihtiyac": talep.get(pk, Decimal("0")), "tam": bool(p and p["tam"]),
            "calistirma_sayisi": p["calistirma"] if p else None,
            "uretilecek_miktar": p["uretilecek"] if p else None, "fazla_miktar": p["fazla"] if p else None,
            "yan_ciktilar": p["yan_ciktilar"] if p else []})

    plan, planda = [], set()
    for stok, _ in hedefler:                              # önce kökler (kalem sırası), sonra ara stoklar (özet sırası)
        if stok.pk in plan_map and stok.pk not in planda:
            plan.append(plan_map[stok.pk])
            planda.add(stok.pk)
    for pk in ozet_sira:
        if pk in plan_map and pk not in planda:
            plan.append(plan_map[pk])
            planda.add(pk)
    return {"agac": agac, "ozet": ozet, "plan": plan}


# === Üretim Emirleri — üst-düzey tetikleyici ===

def _sonraki_emir_sira(yil):
    m = UretimEmri.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


@transaction.atomic
def uretim_emri_olustur(*, kalemler, depo_id, tarih, aciklama="", kaynak_siparis=None,
                        kullanici=None) -> UretimEmri:
    """kalemler: [{"hedef_urun_id": int, "hedef_miktar": Decimal|str}, ...] — en az 1 satır.
    depo/tarih EMRİN TÜMÜNE uygulanır (tüm kalemler + türeyen tüm operasyon kayıtları aynı
    depo+tarihte açılır)."""
    if not kalemler:
        raise UretimHatasi("En az bir hedef ürün satırı gerekli.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if not depo:
        raise UretimHatasi("Depo bulunamadı.")

    coz = []                                       # [(Stok, Decimal), ...] — kökler
    gorulen = set()
    for satir in kalemler:
        urun = _cikti_coz(satir["hedef_urun_id"])
        if urun.pk in gorulen:
            raise UretimHatasi(f"{urun.kod} birden fazla satırda tekrarlanamaz.")
        gorulen.add(urun.pk)
        if not Operasyon.objects.filter(silindi=False, cikti=urun).exists():
            raise UretimHatasi(
                f"{urun.kod} için tanımlı bir operasyon yok; önce Operasyon Tanımları'ndan ekleyin.")
        miktar = _sayi_coz(satir["hedef_miktar"], "Hedef miktar geçerli bir sayı olmalı.")
        if miktar <= 0:
            raise UretimHatasi("Hedef miktar sıfırdan büyük olmalı.")
        coz.append((urun, miktar))

    yil = tarih.year
    emir = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_emir_sira(yil)
                emir = UretimEmri.objects.create(
                    yil=yil, sira=sira, no=f"UE-{yil}-{sira:04d}", depo=depo, tarih=tarih,
                    aciklama=(aciklama or "").strip(), kaynak_siparis=kaynak_siparis,
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if emir is None:
        raise UretimHatasi("Emir numarası üretilemedi; tekrar deneyin.")

    for i, (urun, miktar) in enumerate(coz, start=1):
        UretimEmriKalemi.objects.create(
            uretim_emri=emir, hedef_urun=urun, hedef_miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)

    _emir_operasyon_kayitlarini_ac(emir=emir, kokler=coz, depo=depo, tarih=tarih,
                                   kullanici=kullanici)
    return emir


def _emir_operasyon_kayitlarini_ac(*, emir, kokler, depo, tarih, kullanici):
    """kokler: [(Stok, Decimal), ...] — zaten Operasyonu doğrulanmış kök ürünler (emrin kalemleri). ihtiyac_hesapla TEK çağrıda tüm
    kökleri birlikte işler ve operasyonu olan HER stok için (kökler dâhil; bir kök başka bir kökün ağacında da geçiyorsa talepler
    TOPLANIR) tek bir ``plan`` satırı verir: her satır için TEK taslak kayıt, hedef çıktı = ``uretilecek`` (tam boyda yukarı
    yuvarlanmış çalıştırma × çıktı miktarı; fazla parça onayda stoğa girer)."""
    sonuc = ihtiyac_hesapla(kokler)
    for p in sonuc["plan"]:
        operasyon_kaydi_olustur(
            operasyon_id=p["operasyon"].pk, depo_id=depo.pk, tarih=tarih,
            hedef_cikti_miktari=p["uretilecek"], uretim_emri=emir, kullanici=kullanici)


def siparis_uretilebilir_kalemleri(siparis):
    """(uygun, uygun_degil) döner — uygun_degil: [(TeklifSiparisKalem, sebep_metni), ...].
    Üretime uygun = stok.uretim_urunu=True VE o stok için aktif bir Operasyon tanımlı."""
    uygun, uygun_degil = [], []
    for k in siparis.kalemler.filter(silindi=False).select_related("stok"):
        if not k.stok.uretim_urunu:
            uygun_degil.append((k, "üretim ürünü işaretli değil"))
        elif not Operasyon.objects.filter(silindi=False, cikti=k.stok).exists():
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
    if siparis.uretim_emirleri.filter(silindi=False).exists():
        raise UretimHatasi("Bu siparişten zaten bir üretim emri açılmış.")

    uygun, _ = siparis_uretilebilir_kalemleri(siparis)
    uygun_map = {k.pk: k for k in uygun}

    if not kalem_secimleri:
        raise UretimHatasi(
            "Bu siparişte üretime uygun (üretim ürünü + tanımlı operasyonu olan) hiçbir "
            "kalem seçilmedi.")
    secim = []
    for satir in kalem_secimleri:
        kalem = uygun_map.get(satir["kalem_id"])
        if kalem is None:
            raise UretimHatasi("Geçersiz veya üretime uygun olmayan bir sipariş kalemi seçildi.")
        secim.append((kalem, satir["hedef_miktar"]))

    kalemler = [{"hedef_urun_id": kalem.stok_id, "hedef_miktar": miktar}
               for kalem, miktar in secim]
    return uretim_emri_olustur(
        kalemler=kalemler, depo_id=depo_id, tarih=tarih, aciklama=aciklama,
        kaynak_siparis=siparis, kullanici=kullanici)


def uretim_emri_ilerleme(emir: UretimEmri):
    kayitlar = emir.operasyon_kayitlari.filter(silindi=False)
    toplam = kayitlar.count()
    onayli = kayitlar.filter(durum=OperasyonKaydi.Durum.ONAYLI).count()
    return {"toplam": toplam, "onayli": onayli}


@transaction.atomic
def uretim_emri_sil(emir: UretimEmri, kullanici=None) -> None:
    """Üretim Emrini KALICI olarak siler — bağlı (henüz onaylanmamış) taslak Operasyon
    Kayıtları ve onların girdi satırları dahil hiçbir iz kalmaz (CLAUDE.md'nin bu ekrana
    özel BİLİNÇLİ istisnası — kullanıcı isteği, bkz. proje belleği). Emir kendi başına hiç
    stok hareketi üretmez (bkz. sınıf docstring'i); yalnızca bağlı kayıtları AÇAR. Bu
    kayıtlardan biri zaten ONAYLI ise (gerçek stok hareketi/maliyet oluştu) silme
    reddedilir — onaylı bir kaydı geri almanın/silmenin yolu yok (bkz. operasyon_kaydi_sil),
    o yüzden emrin tamamı da silinemez."""
    if emir.operasyon_kayitlari.filter(
            silindi=False, durum=OperasyonKaydi.Durum.ONAYLI).exists():
        raise UretimHatasi(
            "Bu üretim emrine bağlı en az bir operasyon kaydı onaylanmış; emir silinemez.")
    emir.operasyon_kayitlari.all().delete()   # OperasyonKaydiGirdi CASCADE ile gider
    emir.delete()


# === Operasyon Kayıtları — bir operasyonun fiilen çalıştırılması ===

def _sonraki_kayit_sira(yil):
    m = OperasyonKaydi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


def kaydi_girdi_satirlari(kayit: OperasyonKaydi):
    return (kayit.girdi_satirlari.filter(silindi=False)
            .select_related("girdi").order_by("sira", "pk"))


@transaction.atomic
def operasyon_kaydi_olustur(*, operasyon_id, depo_id, tarih, hedef_cikti_miktari,
                            uretim_emri=None, aciklama="", kullanici=None, fason_cari=None, fason_donus=None) -> OperasyonKaydi:
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
                    operasyon=operasyon, uretim_emri=uretim_emri, depo=depo, tarih=tarih,
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
    calistirma = kayit.hedef_cikti_miktari / operasyon.cikti_miktar
    sonuc = [("ana", operasyon.cikti, kayit.hedef_cikti_miktari, operasyon.boy_mm)]
    for y in operasyon_yan_ciktilari(operasyon):
        sonuc.append((f"yan{y.pk}", y.stok, yuvarla(calistirma * y.miktar, 6), y.boy_mm))
    return sonuc


def kayit_gelen_adetleri(kayit: OperasyonKaydi, ciktilar=None) -> dict:
    """{anahtar: stoğa girecek adet}. Fason dönüşte fasoncudan GELEN adet (boşsa beklenen); gelen > beklenen ya da negatif / ana çıktı için 0 reddedilir.
    Fasonsuz kayıtta her zaman beklenen adet."""
    ciktilar = ciktilar if ciktilar is not None else kayit_ciktilari(kayit)
    if not kayit.fason_cari_id:
        return {a: m for a, _st, m, _b in ciktilar}
    gelen_yan = kayit.gelen_yan or {}
    sonuc = {}
    for anahtar, stok, beklenen, _boy in ciktilar:
        ham = kayit.gelen_ana if anahtar == "ana" else gelen_yan.get(str(stok.pk))
        try:
            g = beklenen if ham in (None, "") else Decimal(str(ham))
        except Exception:
            raise UretimHatasi(f"{stok.kod}: gelen adet geçerli bir sayı olmalı.")
        if g < 0:
            raise UretimHatasi(f"{stok.kod}: gelen adet negatif olamaz.")
        if g > beklenen:
            raise UretimHatasi(f"{stok.kod}: gelen adet ({g.normalize():f}) beklenenden ({beklenen.normalize():f}) fazla olamaz.")
        if anahtar == "ana" and g <= 0:
            raise UretimHatasi(f"{stok.kod}: ana çıktının gelen adedi sıfırdan büyük olmalı.")
        sonuc[anahtar] = g
    return sonuc


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
    agirlik = {k: (m * boy if boy else Decimal("0")) for k, _, m, boy in ciktilar}
    bilinen = toplam_girdi_maliyeti > 0
    # ONAY ANI SNAPSHOT'I: çıktı satırları (miktar, boy, pay oranı) kayda yazılır; sonraki yeniden paylaştırmalar bunlara göre yapılır.
    toplam_agirlik = sum(agirlik.values(), Decimal("0"))
    for anahtar, stok_id, miktar, boy in ciktilar:
        if len(ciktilar) == 1 or not toplam_agirlik or any(a <= 0 for a in agirlik.values()):
            oran = Decimal("1") if anahtar == "ana" else Decimal("0")
        else:
            oran = (agirlik[anahtar] / toplam_agirlik).quantize(Decimal("0.0000000001"))
        f = fason[anahtar] if fason else None
        OperasyonKaydiCikti.objects.create(
            kayit=kayit, stok_id=stok_id, miktar=miktar, beklenen_miktar=beklenen[anahtar], boy_mm=boy, pay_orani=oran, ana_mi=(anahtar == "ana"),
            fason_birim_fiyat=f["fiyat"].birim_fiyat if f else None, fason_para_birimi=f["fiyat"].para_birimi if f else "",
            fason_kur=f["kur"] if f else None, fason_tutar=f["tutar_try"] if f else None, fason_tutar_usd=f["tutar_usd"] if f else None,
            created_by=kullanici, updated_by=kullanici)
    for anahtar, stok_id, boy in gelmeyen:
        OperasyonKaydiCikti.objects.create(kayit=kayit, stok_id=stok_id, miktar=Decimal("0"), beklenen_miktar=beklenen[anahtar], boy_mm=boy,
                                           pay_orani=Decimal("0"), ana_mi=False, fason_tutar=Decimal("0"), fason_tutar_usd=Decimal("0"),
                                           created_by=kullanici, updated_by=kullanici)
    pay_try = stok_fis.maliyet_paylastir(toplam_girdi_maliyeti if bilinen else Decimal("0.00"), agirlik, "ana")
    pay_usd = stok_fis.maliyet_paylastir(toplam_girdi_usd if bilinen else Decimal("0.00"), agirlik, "ana")
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
    kayit.silindi = True
    kayit.silindi_at = timezone.now()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kayit
