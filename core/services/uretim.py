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
    """kokler: [(Stok, Decimal), ...] — zaten Operasyonu doğrulanmış kök ürünler (emrin
    kalemleri). ihtiyac_hesapla TEK bir çağrıda tüm kökleri birlikte işler:
      - agac[i] her KÖKÜN kendi düğümüdür; agac[i]["operasyon"] o kökün KENDİ operasyonudur
        (ozet KÖKLERİ hariç tutar — bkz. ihtiyac_hesapla docstring'i — bu yüzden kökler için
        ayrıca "kok_talep" biriktirilir, tıpkı eski tek-köklü kodun agac[0] için yaptığı gibi).
      - ozet, KÖKLER HARİÇ zincirde geçilen HER düğümü zaten TEK satırda TOPLAR (iki farklı
        kalem aynı ara parçayı paylaşıyorsa miktarları otomatik toplanır).
    Bu fonksiyonun tek EK işi: bir KÖKÜN kendisi aynı zamanda BAŞKA bir kökün ağacında ARA
    bileşen olarak da geçebilir (örn. sipariş hem X'i hem de X'i içeren Y'yi istiyor) — bu
    durumda X için ayrı/çakışan iki OperasyonKaydi açmak yerine TEK toplam kayıtta
    birleştirilir."""
    sonuc = ihtiyac_hesapla(kokler)

    kok_talep = {}                                 # stok.pk -> {"operasyon", "toplam_miktar"}
    for (urun, miktar), dugum in zip(kokler, sonuc["agac"]):
        kayit = kok_talep.setdefault(
            urun.pk, {"operasyon": dugum["operasyon"], "toplam_miktar": Decimal("0")})
        kayit["toplam_miktar"] += miktar

    ara_talep = {}
    for dugum in sonuc["ozet"]:
        if dugum["yaprak"]:
            continue                                # hammadde/satınalma — zaten stokta varsayılır
        stok_pk = dugum["stok"].pk
        if stok_pk in kok_talep:
            kok_talep[stok_pk]["toplam_miktar"] += dugum["toplam_miktar"]   # PAYLAŞILAN kök
        else:
            ara_talep[stok_pk] = dugum

    for kayit in kok_talep.values():
        operasyon_kaydi_olustur(
            operasyon_id=kayit["operasyon"].pk, depo_id=depo.pk, tarih=tarih,
            hedef_cikti_miktari=kayit["toplam_miktar"], uretim_emri=emir, kullanici=kullanici)
    for dugum in ara_talep.values():
        operasyon_kaydi_olustur(
            operasyon_id=dugum["operasyon"].pk, depo_id=depo.pk, tarih=tarih,
            hedef_cikti_miktari=dugum["toplam_miktar"], uretim_emri=emir, kullanici=kullanici)


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
    toplam_girdi_maliyeti = Decimal("0")
    herhangi_biri_tahmini = False
    for satir in satirlar:
        if satir.gerceklesen_miktar == 0:
            continue
        try:
            girdi_hareketi = hareket_ekle(
                stok_id=satir.girdi_id, depo_id=kayit.depo_id, tarih=kayit.tarih,
                tur=StokHareket.Tur.CIKIS, miktar=satir.gerceklesen_miktar,
                aciklama=f"Operasyon kaydı {kayit.no}", kaynak=StokHareket.Kaynak.URETIM,
                operasyon_kaydi_girdi=satir, kullanici=kullanici)
        except HareketHatasi as e:
            raise UretimHatasi(str(e))
        tuketimler = list(girdi_hareketi.maliyet_tuketimleri.select_related("katman"))
        karsilanan_miktar = sum((t.miktar for t in tuketimler), Decimal("0"))
        toplam_girdi_maliyeti += sum((t.tutar_try for t in tuketimler), Decimal("0"))
        if karsilanan_miktar < satir.gerceklesen_miktar or any(t.katman.tahmini for t in tuketimler):
            herhangi_biri_tahmini = True
    cikti_birim_maliyet = (yuvarla(toplam_girdi_maliyeti / kayit.hedef_cikti_miktari, 6)
                           if toplam_girdi_maliyeti > 0 else None)
    hareket_ekle(
        stok_id=kayit.operasyon.cikti_id, depo_id=kayit.depo_id, tarih=kayit.tarih,
        tur=StokHareket.Tur.GIRIS, miktar=kayit.hedef_cikti_miktari,
        aciklama=f"Operasyon kaydı {kayit.no}", kaynak=StokHareket.Kaynak.URETIM,
        birim_maliyet_try=cikti_birim_maliyet, tahmini=herhangi_biri_tahmini,
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
