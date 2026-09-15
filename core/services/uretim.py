"""ÜRETİM modülü servis katmanı — İş İstasyonu + Operasyon (rota) modeli.

Bağımsız, sıfırdan kurulan bir Stok↔Stok rota modeli — FASON'daki (kesilmiş parça /
kesildigi_profil) kavramlarıyla hiçbir ilişkisi yoktur. Bitmiş bir ürün, farklı iş
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

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from core.metin import buyuk_harf_tr
from core.models import (
    Depo, IsIstasyonu, Operasyon, OperasyonGirdi, OperasyonKaydi, OperasyonKaydiGirdi,
    Stok, StokHareket, UretimEmri,
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
            .annotate(girdi_sayisi=Count("girdiler", filter=Q(girdiler__silindi=False)))
            .order_by("istasyon__kod", "cikti__kod"))


def operasyon_girdileri(operasyon: Operasyon):
    return (operasyon.girdiler.filter(silindi=False)
            .select_related("girdi").order_by("sira", "pk"))


def operasyonlu_stok_idler():
    return aktif_operasyonlar().values_list("cikti_id", flat=True)


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


@transaction.atomic
def operasyon_olustur(*, istasyon_id, cikti_id, cikti_miktar, satirlar, ad="", aciklama="",
                      kullanici=None) -> Operasyon:
    istasyon = _istasyon_coz(istasyon_id)
    cikti = _cikti_coz(cikti_id)
    if Operasyon.objects.filter(silindi=False, cikti=cikti).exists():
        raise UretimHatasi("Bu çıktı için zaten aktif bir operasyon tanımlı.")
    cm = _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı.")
    if cm <= 0:
        raise UretimHatasi("Çıktı miktarı sıfırdan büyük olmalı.")
    _girdi_satirlarini_dogrula(cikti, satirlar)
    operasyon = Operasyon.objects.create(
        istasyon=istasyon, cikti=cikti, cikti_miktar=cm,
        ad=(ad or "").strip(), aciklama=(aciklama or "").strip(),
        created_by=kullanici, updated_by=kullanici)
    for i, (girdi, miktar) in enumerate(satirlar, start=1):
        OperasyonGirdi.objects.create(
            operasyon=operasyon, girdi=girdi, miktar=miktar, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return operasyon


@transaction.atomic
def operasyon_guncelle(operasyon: Operasyon, *, istasyon_id, cikti_miktar, satirlar, ad="",
                       aciklama="", kullanici=None) -> Operasyon:
    if operasyon.silindi:
        raise UretimHatasi("Silinmiş operasyon düzenlenemez.")
    istasyon = _istasyon_coz(istasyon_id)
    cm = _sayi_coz(cikti_miktar, "Çıktı miktarı geçerli bir sayı olmalı.")
    if cm <= 0:
        raise UretimHatasi("Çıktı miktarı sıfırdan büyük olmalı.")
    _girdi_satirlarini_dogrula(operasyon.cikti, satirlar)
    operasyon.girdiler.filter(silindi=False).update(
        silindi=True, silindi_at=timezone.now(), updated_by=kullanici)
    operasyon.istasyon = istasyon
    operasyon.cikti_miktar = cm
    operasyon.ad = (ad or "").strip()
    operasyon.aciklama = (aciklama or "").strip()
    operasyon.updated_by = kullanici
    operasyon.save(update_fields=[
        "istasyon", "cikti_miktar", "ad", "aciklama", "updated_by", "updated_at"])
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

def ihtiyac_hesapla(kalemler):
    """kalemler: [(Stok hedef, Decimal miktar), ...].

    Döner: {"agac": [...], "ozet": [...]}
      agac — her hedef satırı için, kökten yapraklara iç içe ağaç:
        {"stok", "miktar", "istasyon"(None ise yaprak), "operasyon"(None ise yaprak),
         "yaprak": bool, "cocuklar": [...]}
      ozet — kökler HARİÇ, zincirde geçilen HER stok için TEK satır (bir stoğun en fazla
        1 aktif operasyonu olduğu için istasyon deterministik), toplam miktarları
        biriktirilmiş: {"stok", "istasyon", "operasyon", "toplam_miktar",
        "calistirma_sayisi"(yaprakta None), "yaprak"}.

    Döngü koruması: `yol`, mevcut dal boyunca ziyaret edilen stok id'lerinin frozenset'i
    (dal-yerel — aynı stoğun bağımsız dallarda geçmesi sorun değil). Bir stok kendi ata
    zincirinde tekrar ederse UretimHatasi fırlatılır (sessizce kesilip yanlış/eksik rakam
    göstermek yerine — üretim planlamasında yanıltıcı olur)."""
    ozet_map = {}

    def ozete_ekle(stok, miktar, operasyon):
        kayit = ozet_map.get(stok.pk)
        if kayit is None:
            kayit = {
                "stok": stok, "operasyon": operasyon,
                "istasyon": operasyon.istasyon if operasyon else None,
                "toplam_miktar": Decimal("0"), "yaprak": operasyon is None,
                "_sira": len(ozet_map),
            }
            ozet_map[stok.pk] = kayit
        kayit["toplam_miktar"] += miktar

    def gez(stok, miktar, yol, kok=False):
        if stok.pk in yol:
            raise UretimHatasi(
                f"Operasyon zincirinde döngü tespit edildi: {stok.kod} kendi üretim "
                f"zincirinde tekrar ediyor.")
        operasyon = (Operasyon.objects.filter(cikti=stok, silindi=False)
                    .select_related("istasyon").first())
        if not kok:
            ozete_ekle(stok, miktar, operasyon)
        if operasyon is None:
            return {"stok": stok, "miktar": miktar, "istasyon": None,
                    "operasyon": None, "yaprak": True, "cocuklar": []}
        calistirma = miktar / operasyon.cikti_miktar
        yeni_yol = yol | {stok.pk}
        girdi_satirlari = (operasyon.girdiler.filter(silindi=False)
                           .select_related("girdi").order_by("sira", "pk"))
        cocuklar = [gez(satir.girdi, calistirma * satir.miktar, yeni_yol)
                   for satir in girdi_satirlari]
        return {"stok": stok, "miktar": miktar, "istasyon": operasyon.istasyon,
                "operasyon": operasyon, "yaprak": False, "cocuklar": cocuklar}

    agac = []
    for stok, miktar in kalemler:
        if miktar is None or miktar <= 0:
            continue
        agac.append(gez(stok, miktar, frozenset(), kok=True))

    ozet = [ozet_map[k] for k in sorted(ozet_map, key=lambda k: ozet_map[k]["_sira"])]
    for o in ozet:
        o["calistirma_sayisi"] = (
            o["toplam_miktar"] / o["operasyon"].cikti_miktar if o["operasyon"] else None)
        del o["_sira"]
    return {"agac": agac, "ozet": ozet}


# === Üretim Emirleri — üst-düzey tetikleyici ===

def _sonraki_emir_sira(yil):
    m = UretimEmri.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


@transaction.atomic
def uretim_emri_olustur(*, hedef_urun_id, hedef_miktar, depo_id, tarih, aciklama="",
                        kullanici=None) -> UretimEmri:
    hedef_urun = _cikti_coz(hedef_urun_id)
    if not Operasyon.objects.filter(silindi=False, cikti=hedef_urun).exists():
        raise UretimHatasi("Bu ürün için tanımlı bir operasyon yok; önce Operasyon Tanımları'ndan ekleyin.")
    depo = Depo.objects.filter(pk=depo_id, silindi=False).first()
    if not depo:
        raise UretimHatasi("Depo bulunamadı.")
    miktar = _sayi_coz(hedef_miktar, "Hedef miktar geçerli bir sayı olmalı.")
    if miktar <= 0:
        raise UretimHatasi("Hedef miktar sıfırdan büyük olmalı.")

    sonuc = ihtiyac_hesapla([(hedef_urun, miktar)])

    yil = tarih.year
    emir = None
    for _ in range(10):
        try:
            with transaction.atomic():
                sira = _sonraki_emir_sira(yil)
                emir = UretimEmri.objects.create(
                    yil=yil, sira=sira, no=f"UE-{yil}-{sira:04d}",
                    hedef_urun=hedef_urun, hedef_miktar=miktar, depo=depo, tarih=tarih,
                    aciklama=(aciklama or "").strip(),
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if emir is None:
        raise UretimHatasi("Emir numarası üretilemedi; tekrar deneyin.")

    # İhtiyaç Hesapla'nın "ozet"i KÖKÜ (hedef_urun'ün kendi operasyonu) hariç tutar — bu
    # rapor ekranı için doğru davranış, ama burada hedef_urun'ün KENDİ operasyonu için de
    # bir kayıt açılmalı: agac[0]["operasyon"] kökün operasyonudur (yukarıda varlığı zaten
    # doğrulandı), sonuc["ozet"] ise onun ALTINDAKİ tüm operasyonları verir.
    kok_operasyon = sonuc["agac"][0]["operasyon"]
    operasyon_kaydi_olustur(
        operasyon_id=kok_operasyon.pk, depo_id=depo.pk, tarih=tarih,
        hedef_cikti_miktari=miktar, uretim_emri=emir, kullanici=kullanici)
    for dugum in sonuc["ozet"]:
        if dugum["yaprak"]:
            continue                                # hammadde/satınalma — zaten stokta varsayılır
        operasyon_kaydi_olustur(
            operasyon_id=dugum["operasyon"].pk, depo_id=depo.pk, tarih=tarih,
            hedef_cikti_miktari=dugum["toplam_miktar"], uretim_emri=emir, kullanici=kullanici)
    return emir


def uretim_emri_ilerleme(emir: UretimEmri):
    kayitlar = emir.operasyon_kayitlari.filter(silindi=False)
    toplam = kayitlar.count()
    onayli = kayitlar.filter(durum=OperasyonKaydi.Durum.ONAYLI).count()
    return {"toplam": toplam, "onayli": onayli}


# === Operasyon Kayıtları — bir operasyonun fiilen çalıştırılması ===

def _sonraki_kayit_sira(yil):
    m = OperasyonKaydi.objects.filter(yil=yil).aggregate(m=Max("sira"))["m"]
    return (m or 0) + 1


def kaydi_girdi_satirlari(kayit: OperasyonKaydi):
    return (kayit.girdi_satirlari.filter(silindi=False)
            .select_related("girdi").order_by("sira", "pk"))


@transaction.atomic
def operasyon_kaydi_olustur(*, operasyon_id, depo_id, tarih, hedef_cikti_miktari,
                            uretim_emri=None, aciklama="", kullanici=None) -> OperasyonKaydi:
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
                    durum=OperasyonKaydi.Durum.TASLAK,
                    created_by=kullanici, updated_by=kullanici)
            break
        except IntegrityError:
            continue
    if kayit is None:
        raise UretimHatasi("Kayıt numarası üretilemedi; tekrar deneyin.")

    for i, satir in enumerate(girdi_satirlari, start=1):
        gerekli = yuvarla(miktar * satir.miktar / operasyon.cikti_miktar, 3)
        OperasyonKaydiGirdi.objects.create(
            kayit=kayit, girdi=satir.girdi, planlanan_miktar=gerekli,
            gerceklesen_miktar=gerekli, sira=i * 10,
            created_by=kullanici, updated_by=kullanici)
    return kayit


@transaction.atomic
def operasyon_kaydi_girdi_guncelle(satir: OperasyonKaydiGirdi, *, gerceklesen_miktar,
                                   kullanici=None):
    if satir.kayit.durum != OperasyonKaydi.Durum.TASLAK:
        raise UretimHatasi("Yalnız taslak kaydın girdileri düzenlenebilir.")
    m = _sayi_coz(gerceklesen_miktar, "Gerçekleşen miktar geçerli bir sayı olmalı.")
    if m < 0:
        raise UretimHatasi("Gerçekleşen miktar negatif olamaz.")
    satir.gerceklesen_miktar = m
    satir.updated_by = kullanici
    satir.save(update_fields=["gerceklesen_miktar", "updated_by", "updated_at"])
    return satir


@transaction.atomic
def operasyon_kaydi_onayla(kayit: OperasyonKaydi, kullanici=None) -> OperasyonKaydi:
    """TASLAK → ONAYLI: her girdi satırı için depo ÇIKIŞ (gerceklesen_miktar==0 olanlar
    hatasız ATLANIR — 'bu girdiye bu seferlik gerek kalmadı' meşru bir durumdur) + çıktı
    için depo GİRİŞ StokHareket'i (Kaynak=URETIM) yazar; hiçbir YevmiyeFisi/YevmiyeSatir
    üretmez. İdempotent (zaten onaylıysa sessiz döner). Yetersiz stok varsa tüm işlem geri
    alınır (atomic)."""
    if kayit.silindi:
        raise UretimHatasi("İptal edilmiş kayıt onaylanamaz.")
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
        return kayit
    satirlar = list(kaydi_girdi_satirlari(kayit))
    for satir in satirlar:
        if satir.gerceklesen_miktar == 0:
            continue
        try:
            hareket_ekle(
                stok_id=satir.girdi_id, depo_id=kayit.depo_id, tarih=kayit.tarih,
                tur=StokHareket.Tur.CIKIS, miktar=satir.gerceklesen_miktar,
                aciklama=f"Operasyon kaydı {kayit.no}", kaynak=StokHareket.Kaynak.URETIM,
                operasyon_kaydi_girdi=satir, kullanici=kullanici)
        except HareketHatasi as e:
            raise UretimHatasi(str(e))
    hareket_ekle(
        stok_id=kayit.operasyon.cikti_id, depo_id=kayit.depo_id, tarih=kayit.tarih,
        tur=StokHareket.Tur.GIRIS, miktar=kayit.hedef_cikti_miktari,
        aciklama=f"Operasyon kaydı {kayit.no}", kaynak=StokHareket.Kaynak.URETIM,
        kullanici=kullanici)
    kayit.durum = OperasyonKaydi.Durum.ONAYLI
    kayit.updated_by = kullanici
    kayit.save(update_fields=["durum", "updated_by", "updated_at"])
    return kayit


def operasyon_kaydi_sil(kayit: OperasyonKaydi, kullanici=None) -> OperasyonKaydi:
    if kayit.silindi:
        return kayit
    if kayit.durum == OperasyonKaydi.Durum.ONAYLI:
        raise UretimHatasi("Onaylı kayıt iptal edilemez.")
    kayit.silindi = True
    kayit.silindi_at = timezone.now()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kayit
