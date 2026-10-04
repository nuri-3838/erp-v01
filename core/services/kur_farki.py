"""Kur farkı motoru — döviz hesaplarında HAREKETLİ AĞIRLIKLI ORTALAMA kurla maliyet (stoktaki
ortalama maliyet mantığı) ve ÇIKIŞTA otomatik kambiyo kâr/zarar satırı.

Havuz = (muhasebe hesabı, işlem para birimi). Her havuzda döviz bakiyesi ``q`` ve TL değeri ``v``
(işaretli: borç +, alacak −) tutulur; ortalama kur = v / q.

- GİRİŞ (havuzun işaretiyle aynı yönde, ya da havuz boşken): TL = döviz × işlem kuru; havuz büyür,
  ortalama kur güncellenir. (Alacak bakiyeli hesapta — cari — alacak satırı havuzu büyütür.)
- ÇIKIŞ (havuza ters yönde): hesap satırı ORTALAMA kur × döviz ile yazılır (bakiyeyi tamamen
  kapatan çıkışta TL bakiyenin tamamı — yuvarlama farkı dahil — bu çıkışa gider; döviz bakiyesi
  sıfırsa TL bakiye de tam sıfırdır). Karşı taraf işlem kuruyla kalır; fark AYNI FİŞE otomatik
  satır olur: kâr → 646 KAMBİYO KÂRLARI (alacak), zarar → 656 KAMBİYO ZARARLARI (borç).
- Çıkış havuzu aşarsa (bakiye işaret değiştirir) aşan kısım yeni havuzu işlem kuruyla açar.

YATIRIM CARİLERİ: bir cari havuzunun kur farkı, carinin EN SON (fatura tarihi, id) onaylı faturasının
yatırım projeli (258) kalemindeki projeye — proje "Devam Ediyor" ise — 646/656 yerine 258 YAPILMAKTA OLAN
YATIRIMLAR'a ve aynı yatırım projesine yazılır (zarar 258 borç = maliyet artar, kâr 258 alacak). Proje
aktifleşmişse ya da carinin projeli faturası yoksa 646/656. Banka/kasa/çek havuzları hep 646/656.
Cari kartındaki "Kur farkı hedefi" = "Her zaman 646-656" ise hep 646/656. Geçmişte üretilmiş bir kur farkı
satırının hedefi (258+proje ya da 646/656) bir daha DEĞİŞMEZ; yalnız yeni üretilen satırlar güncel kuralı izler.

Havuzlar tarih → fiş no → satır id sırasıyla HER SEFERİNDE baştan hesaplanır (geriye dönük girilen
ya da silinen hareket sonraki çıkışların kur farkını da düzeltir). Fiş oluşturma/güncelleme/iptal/
silme bu motoru tetikler (bkz. yevmiye.py, fis_sil.py). Dönem sonu kur değerleme fişleri
(``islem_tutari = 0``, kaynak KUR_DEGERLEME) yalnız TL değeri ``v``yi değiştirir, ``q``yu değil.

Uygun hesaplar: kendi para birimindeki döviz banka hesabı / döviz kasa muhasebe hesabı; cari
(120/320/121/321) ve çek-senet hesaplarında döviz satırları. TL hesaplar ve gider/gelir hesapları
havuz oluşturmaz.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction

from core.models import (BankaHesap, Cari, CekHesapAyari, FaturaSatir, HesapPlani, Kasa,
                         YatirimProjesi, YevmiyeFisi, YevmiyeSatir)
from core.sayi import yuvarla

SIFIR = Decimal("0.00")
KAR_HESABI = "646"
ZARAR_HESABI = "656"
KUR_FARKI_ACIKLAMA = "KUR FARKI"
YATIRIM_HESABI = "258"
CARI_ONEKLERI = ("120", "121", "320", "321")


class KurFarkiHatasi(ValueError):
    """Kur farkı motoru kural ihlali (ör. 646/656 hesabı tanımlı değil)."""


# ---------------------------------------------------------------- uygun hesap kümesi
@dataclass
class UygunKume:
    sabit: dict = field(default_factory=dict)     # hesap_kodu -> para birimi (banka/kasa)
    cek: set = field(default_factory=set)         # çek-senet muhasebe hesapları

    def uygun_mu(self, hesap_kodu, pb):
        if pb == "TRY":
            return False
        if hesap_kodu in self.sabit:
            return self.sabit[hesap_kodu] == pb
        return hesap_kodu.startswith(CARI_ONEKLERI) or hesap_kodu in self.cek


def uygun_kume() -> UygunKume:
    k = UygunKume()
    for pb, kod in (BankaHesap.objects.filter(silindi=False).exclude(para_birimi="TRY")
                    .values_list("para_birimi", "muhasebe_id")):
        k.sabit[kod] = pb
    for pb, kod in (Kasa.objects.filter(silindi=False).exclude(para_birimi="TRY")
                    .values_list("para_birimi", "muhasebe_id")):
        k.sabit[kod] = pb
    ayar = CekHesapAyari.objects.filter(pk=1).first()
    if ayar is not None:
        for f in CekHesapAyari._meta.get_fields():
            if getattr(f, "related_model", None) is HesapPlani and f.concrete:
                kod = getattr(ayar, f.attname, None)
                if kod:
                    k.cek.add(kod)
    return k


# ---------------------------------------------------------------- saf havuz hesabı
@dataclass
class GirdiSatir:
    pk: object
    yon: int            # +1 borç, -1 alacak
    dvz: Decimal        # işlem (döviz) tutarı
    ham: Decimal        # işlem kuruyla TL (satırın orijinal TL'si)
    degerleme: bool = False   # kur değerleme satırı: yalnız v değişir, q değişmez


@dataclass
class SatirSonucu:
    pk: object
    yeni_tl: Decimal
    ort_kur: Decimal | None     # çıkışta kullanılan ortalama kur (kısmen/tamamen kapatan satırda)
    fark: Decimal               # ham − yeni (satır TL'sindeki azalma); 0 = kur farkı yok


def havuz_hesapla(girdiler):
    """Sıralı satırlardan havuzu yürütür → ([SatirSonucu], q, v). Saf fonksiyon (DB'ye dokunmaz)."""
    q = Decimal("0")
    v = Decimal("0.00")
    sonuclar = []
    for g in girdiler:
        if g.degerleme:
            v += g.yon * g.ham
            sonuclar.append(SatirSonucu(g.pk, g.ham, None, SIFIR))
            continue
        if q == 0 or (q > 0) == (g.yon > 0):
            yeni, ort = g.ham, None                       # GİRİŞ: işlem kuruyla
        else:
            kapat = min(g.dvz, abs(q))
            ort = abs(v) / abs(q)
            kapanis = abs(v) if kapat == abs(q) else yuvarla(ort * kapat, 2)
            kalan = g.dvz - kapat
            if kalan == 0:
                yeni = kapanis
            else:
                yeni = kapanis + yuvarla(g.ham * kalan / g.dvz, 2)
        q += g.yon * g.dvz
        v += g.yon * yeni
        sonuclar.append(SatirSonucu(g.pk, yeni, ort, g.ham - yeni))
    return sonuclar, q, v


# ---------------------------------------------------------------- DB katmanı
def _havuz_satirlari(hesap_kodu, pb):
    return list(
        YevmiyeSatir.objects.filter(
            hesap_id=hesap_kodu, islem_pb=pb, silindi=False, fis__silindi=False,
            ana_satir__isnull=True)
        .select_related("fis").order_by("fis__tarih", "fis__fis_no", "id"))


def _girdi(s):
    yon = 1 if s.borc > 0 else -1
    tl = s.borc if yon > 0 else s.alacak
    degerleme = s.fis.kaynak == YevmiyeFisi.Kaynak.KUR_DEGERLEME and s.islem_tutari == 0
    ham = s.ham_tl if (s.ham_tl is not None and not degerleme) else tl
    return GirdiSatir(pk=s.pk, yon=yon, dvz=s.islem_tutari, ham=ham, degerleme=degerleme)


def yatirim_hedefi(hesap_kodu):
    """Cari havuzu için kur farkının yazılacağı yatırım projesi (``YatirimProjesi``, DEVAM) ya da None.
    Carinin en son (fatura tarihi, id) onaylı faturasının yatırım projeli kalemindeki projeye bakar; carinin
    "Kur farkı hedefi" ayarı "Her zaman 646-656" ise hep None. Hem motor hem dönem sonu değerleme bunu kullanır."""
    if not hesap_kodu or not hesap_kodu.startswith(CARI_ONEKLERI):
        return None
    cari = Cari.objects.filter(muhasebe_kodu=hesap_kodu, silindi=False).first()
    if cari is None or cari.kur_farki_hedefi == Cari.KurFarkiHedefi.HESAP_646_656:
        return None
    satir = (FaturaSatir.objects
             .filter(fatura__cari=cari, fatura__silindi=False, fatura__durum="ONAYLI", silindi=False,
                     yatirim_projesi__isnull=False, yatirim_projesi__silindi=False)
             .select_related("yatirim_projesi", "fatura")
             .order_by("-fatura__tarih", "-fatura_id", "-id").first())
    if satir is None or satir.yatirim_projesi.durum != YatirimProjesi.Durum.DEVAM:
        return None
    return satir.yatirim_projesi


def _hesap(kod):
    h = HesapPlani.objects.filter(hesap_kodu=kod, silindi=False).first()
    if h is None:
        raise KurFarkiHatasi(f"{kod} hesabı hesap planında tanımlı değil; kur farkı yazılamaz.")
    return h


def havuz_plani(hesap_kodu, pb):
    """(satırlar, sonuçlar, q, v) — DB'yi değiştirmeden mevcut durumdan planı çıkarır."""
    satirlar = _havuz_satirlari(hesap_kodu, pb)
    sonuc, q, v = havuz_hesapla([_girdi(s) for s in satirlar])
    return satirlar, sonuc, q, v


@transaction.atomic
def havuz_yeniden_hesapla(hesap_kodu, pb):
    """Havuzu baştan hesaplar; değişen satır TL'lerini ve kur farkı satırlarını yazar.
    Rapor için {degisen, kur_farki_kar, kur_farki_zarar, q, v} döner."""
    satirlar, sonuclar, q, v = havuz_plani(hesap_kodu, pb)
    degisen = 0
    kar = zarar = SIFIR
    proje = yatirim_hedefi(hesap_kodu)
    yatirim = {}                      # proje kodu -> net maliyet etkisi (zarar +, kâr −)
    for s, r in zip(satirlar, sonuclar):
        girdi = _girdi(s)
        if girdi.degerleme:
            continue
        yon = girdi.yon
        mevcut = list(s.kur_farki_satirlari.all())
        guncel_tl = s.borc if yon > 0 else s.alacak
        ham_yeni = girdi.ham if r.yeni_tl != girdi.ham else None
        ort_yeni = yuvarla(r.ort_kur, 6) if (r.ort_kur is not None and r.fark != 0) else None
        if guncel_tl != r.yeni_tl or s.ham_tl != ham_yeni or s.ort_kur != ort_yeni:
            s.borc, s.alacak = (r.yeni_tl, SIFIR) if yon > 0 else (SIFIR, r.yeni_tl)
            s.ham_tl = ham_yeni
            s.ort_kur = ort_yeni
            s.save(update_fields=["borc", "alacak", "ham_tl", "ort_kur", "updated_at"])
            degisen += 1
        fark = r.fark
        # fark > 0: satır TL'si azaldı. Alacak satırda → fiş +fark alacak ister (kâr 646);
        # borç satırda → +fark borç ister (zarar 656). fark < 0 tersi.
        if fark == 0:
            for m in mevcut:
                m.delete()
            continue
        kar_tarafi = (fark > 0) == (yon < 0)          # True → alacak (kâr); False → borç (zarar)
        tutar = abs(fark)
        if kar_tarafi:
            kar += tutar
        else:
            zarar += tutar
        hedef_kod = KAR_HESABI if kar_tarafi else ZARAR_HESABI
        hedef_proje_id = None
        hedef_proje_kod = None
        if mevcut:
            # GEÇMİŞTE üretilmiş kur farkı satırının hedefi DEĞİŞMEZ (yalnız tutar/yön güncellenir): 258'e +
            # projeye yazılmışsa orada kalır (aktifleştirme fişi bozulmaz), 646/656'daysa orada kalır.
            if mevcut[0].yatirim_projesi_id:
                eski = YatirimProjesi.objects.filter(pk=mevcut[0].yatirim_projesi_id).first()
                hedef_kod, hedef_proje_id = mevcut[0].hesap_id, mevcut[0].yatirim_projesi_id
                hedef_proje_kod = eski.kod if eski is not None else None
        elif proje is not None:
            hedef_kod, hedef_proje_id, hedef_proje_kod = YATIRIM_HESABI, proje.pk, proje.kod
        if hedef_proje_kod:
            yatirim[hedef_proje_kod] = yatirim.get(hedef_proje_kod, SIFIR) + (-tutar if kar_tarafi else tutar)
        alanlar = dict(
            hesap=_hesap(hedef_kod), borc=SIFIR if kar_tarafi else tutar, alacak=tutar if kar_tarafi else SIFIR,
            islem_pb="TRY", islem_tutari=tutar, islem_kuru=Decimal("1"), aciklama=KUR_FARKI_ACIKLAMA,
            yatirim_projesi_id=hedef_proje_id)
        if mevcut:
            m = mevcut[0]
            for fazla in mevcut[1:]:
                fazla.delete()
            if any(getattr(m, k) != vv for k, vv in alanlar.items()):
                for k, vv in alanlar.items():
                    setattr(m, k, vv)
                m.save()
        else:
            YevmiyeSatir.objects.create(fis=s.fis, ana_satir=s, **alanlar)
    return {"degisen": degisen, "kar": kar, "zarar": zarar, "q": q, "v": v,
            "proje": proje.kod if proje else None, "yatirim": yatirim}


# ---------------------------------------------------------------- tetikleyiciler
def etkilenen_havuzlar(fis):
    """Fişin (silinmemiş) satırlarından uygun havuz çiftleri {(hesap_kodu, pb)}."""
    cift = set(fis.satirlar.filter(silindi=False, ana_satir__isnull=True)
               .exclude(islem_pb="TRY").values_list("hesap_id", "islem_pb"))
    if not cift:
        return set()
    k = uygun_kume()
    return {c for c in cift if k.uygun_mu(*c)}


def havuzlari_yeniden_hesapla(ciftler):
    for hesap_kodu, pb in sorted(ciftler):
        havuz_yeniden_hesapla(hesap_kodu, pb)


def tum_havuzlar():
    """Veritabanındaki tüm uygun (hesap, pb) çiftleri."""
    k = uygun_kume()
    cift = (YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False, ana_satir__isnull=True)
            .exclude(islem_pb="TRY").order_by().values_list("hesap_id", "islem_pb").distinct())
    return sorted(c for c in cift if k.uygun_mu(*c))


def havuz_bakiyesi(hesap_kodu, pb):
    """(döviz bakiyesi q, TL bakiyesi v) — yevmiyedeki GERÇEK toplamlar (kur farkı satırları
    ve değerleme dahil); ortalama kur = v / q."""
    q = v = Decimal("0")
    for s in YevmiyeSatir.objects.filter(hesap_id=hesap_kodu, islem_pb=pb, silindi=False,
                                         fis__silindi=False):
        yon = 1 if s.borc > 0 else -1
        q += yon * s.islem_tutari
        v += s.borc - s.alacak
    return q, v
