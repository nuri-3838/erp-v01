"""ATÖLYE > İş Oturumları — "Başla / Bitir" mantığı (kullanıcı kararları, 2026-10-12):

* Operatör emir kartında **Başla** der; mesaisi açık ve atölye yetkili arkadaşlarını ekibe seçebilir. Bir personel aynı anda yalnız BİR açık
  oturumda olabilir (başlatan ya da üye). Zaman hep sunucu saati.
* **Bitir** (adet, tam sayı > 0): ``uretim.istasyon_emri_uret`` aynen çalışır (tarih bazlı stok kontrolü, LAZER/PAKETLEME fazla üretim yasağı,
  hata mesajları); hata olursa oturum AÇIK kalır, operatör adedi düzeltip tekrar dener. Ekipteki herkes bitirebilir; kaydın ``personel``'i =
  BAŞLATAN, bitiren ``bitiren`` alanında. **İptal (iş yapılmadı)**: kayıt yok, süre raporda kalır. Kayıt geri alınsa da oturum BITTI kalır.
* Mesai bitirilirken üyelik o anda kapanır (oturum diğerleriyle sürer) — başlatan için de; oturumda tek kişi kaldıysa mesai bitirme ENGELLENİR.
* Unutulan açık oturum otomatik İPTAL edilmez: 18:00'den sonra pano uyarısı; ertesi gün operatörün ekranında "DÜNKÜ AÇIK İŞ" en üstte; önceki
  günden kalan oturum bitirilirken bitiş saati sorulur (varsayılan: o kişinin o günkü mesai çıkışı), kayıt tarihi = bitişin tarihi.
* Adam-saat = üyelerin kendi katılım sürelerinin toplamı; işçilik/GÜG dağıtımı AYRI adım, burada yalnız veri birikir."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from core.models import EkranYetki, IsOturumu, IsOturumuUyesi, IstasyonEmri, MesaiKaydi, Personel, UretimEmri
from core.services import uretim
from core.tarih import _TR, tr_bugun

UNUTULMA_SAATI = datetime.time(18, 0)
ATOLYE_EKRAN_KODU = "atolye"


class OturumHatasi(ValueError):
    """İş oturumu kural ihlali (Türkçe mesaj)."""


# --- sorgular ---

def _uyeler_prefetch():
    return Prefetch("uyeler", queryset=IsOturumuUyesi.objects.filter(silindi=False).select_related("personel").order_by("-baslatan", "id"))


def _acik_qs():
    return (IsOturumu.objects.filter(silindi=False, durum=IsOturumu.Durum.ACIK)
            .select_related("istasyon_emri__operasyon__cikti", "uretim_emri", "istasyon", "baslatan").prefetch_related(_uyeler_prefetch()))


def aktif_uyelik(personel) -> IsOturumuUyesi | None:
    """Personelin şu an içinde olduğu açık oturum üyeliği (kural: en çok bir)."""
    return (IsOturumuUyesi.objects.filter(personel=personel, silindi=False, ayrilma_zamani__isnull=True, oturum__silindi=False,
                                          oturum__durum=IsOturumu.Durum.ACIK)
            .select_related("oturum__istasyon", "oturum__uretim_emri", "oturum__istasyon_emri__operasyon__cikti").order_by("-pk").first())


def acik_oturumlar(ie: IstasyonEmri | None = None) -> list:
    qs = _acik_qs()
    if ie is not None:
        qs = qs.filter(istasyon_emri=ie)
    return list(qs.order_by("baslangic_zamani", "pk"))


def kisinin_acik_oturumlari(personel) -> list:
    """Personelin aktif üye olduğu açık oturumlar (dünden kalanlar dahil; ekranda en üstte)."""
    pks = IsOturumuUyesi.objects.filter(personel=personel, silindi=False, ayrilma_zamani__isnull=True).values("oturum_id")
    return list(_acik_qs().filter(pk__in=pks).order_by("baslangic_zamani", "pk"))


def unutulanlar(simdi=None) -> list:
    """Pano uyarısı: önceki günlerden kalan açık oturumlar her zaman; bugünküler 18:00 TR'den sonra."""
    simdi = (simdi or timezone.now()).astimezone(_TR)
    qs = _acik_qs()
    if simdi.time() < UNUTULMA_SAATI:
        qs = qs.filter(is_tarihi__lt=simdi.date())
    return list(qs.order_by("baslangic_zamani", "pk"))


def ekip_adaylari(personel) -> list:
    """Ekibe seçilebilecekler: mesaisi AÇIK + atölye yetkili + kendisi değil; meşgul olanlar (açık oturumda) işaretli gelir (seçilemez)."""
    acik_mesai = MesaiKaydi.objects.filter(silindi=False, cikis_zamani__isnull=True).values("personel_id")
    yetkili = EkranYetki.objects.filter(silindi=False, ekran_kod=ATOLYE_EKRAN_KODU).values("kullanici_id")
    adaylar = (Personel.objects.filter(silindi=False, pk__in=acik_mesai, kullanici_id__in=yetkili, kullanici__is_active=True)
               .exclude(pk=personel.pk).order_by("ad", "soyad"))
    mesgul = {u.personel_id: u.oturum for u in IsOturumuUyesi.objects.filter(personel__in=adaylar, silindi=False, ayrilma_zamani__isnull=True,
                                                                               oturum__silindi=False, oturum__durum=IsOturumu.Durum.ACIK)
              .select_related("oturum__istasyon", "oturum__uretim_emri")}
    return [{"personel": p, "mesgul": mesgul.get(p.pk)} for p in adaylar]


# --- özet / hesap ---

def sure_metni(saniye) -> str:
    """'1 sa 05 dk' / '42 dk'; None → boş."""
    if saniye is None:
        return ""
    dk = int(saniye) // 60
    sa, dk = divmod(dk, 60)
    return f"{sa} sa {dk:02d} dk" if sa else f"{dk} dk"


def ozet(oturum: IsOturumu) -> dict:
    """Ekip + süre + adam-saat + adet/saat + adet başı süre (rapor ve ekran satırı)."""
    uyeler = [u for u in oturum.uyeler.all() if not u.silindi]
    bitis = oturum.bitis_zamani
    sure = oturum.sure_saniye
    adam_sn = sum(u.katilim_saniye(bitis) for u in uyeler)
    adet = oturum.miktar
    adet_saat = adet_basi_sn = None
    if adet and oturum.durum == IsOturumu.Durum.BITTI:
        adet_saat = (adet / Decimal(sure) * 3600).quantize(Decimal("0.1")) if sure else None
        adet_basi_sn = int(Decimal(adam_sn) / adet)
    return {"oturum": oturum, "uyeler": uyeler, "kisi": len(uyeler), "ekip_metni": ", ".join(u.personel.ad_soyad for u in uyeler),
            "sure_saniye": sure, "sure_metni": sure_metni(sure), "adam_saniye": adam_sn,
            "adam_saat": (Decimal(adam_sn) / 3600).quantize(Decimal("0.01")), "adam_saat_metni": sure_metni(adam_sn),
            "adet": adet, "adet_saat": adet_saat, "adet_basi_saniye": adet_basi_sn,
            "adet_basi_metni": sure_metni(adet_basi_sn) if adet_basi_sn is not None else "",
            "baslangic_ms": int(oturum.baslangic_zamani.timestamp() * 1000), "dunku": oturum.is_tarihi < tr_bugun()}


def varsayilan_bitis(oturum: IsOturumu, personel):
    """Önceki günden kalan oturum için önerilen bitiş: kişinin o günkü (son) mesai çıkışı, yoksa başlatanınki, yoksa None."""
    for p in (personel, oturum.baslatan):
        m = (MesaiKaydi.objects.filter(personel=p, silindi=False, is_tarihi=oturum.is_tarihi, cikis_zamani__isnull=False,
                                       cikis_zamani__gt=oturum.baslangic_zamani).order_by("-cikis_zamani").first())
        if m is not None:
            return m.cikis_zamani
    return None


def _uyelik(personel, oturum: IsOturumu) -> IsOturumuUyesi:
    u = IsOturumuUyesi.objects.filter(oturum=oturum, personel=personel, silindi=False, ayrilma_zamani__isnull=True).first()
    if u is None:
        raise OturumHatasi("Bu iş oturumunun ekibinde değilsiniz.")
    return u


def _mesgul_metni(uyelik: IsOturumuUyesi) -> str:
    o = uyelik.oturum
    return f"{o.istasyon.kod} {o.istasyon.ad} › {o.uretim_emri.no}"


def _bitis_coz(oturum: IsOturumu, bitis, simdi):
    """Bugünkü oturumda bitiş = şimdi; önceki günden kalanda operatörün verdiği saat (başlangıçtan sonra, şimdiden önce) zorunlu."""
    if oturum.is_tarihi >= simdi.astimezone(_TR).date():
        return simdi
    if bitis is None:
        raise OturumHatasi("Önceki günden kalan iş: bitiş saatini girin.")
    if bitis <= oturum.baslangic_zamani:
        raise OturumHatasi("Bitiş saati başlangıçtan sonra olmalı.")
    if bitis > simdi:
        raise OturumHatasi("Bitiş saati ileri bir zaman olamaz.")
    return bitis


# --- işlemler ---

@transaction.atomic
def baslat(personel, ie: IstasyonEmri, ekip, *, ip=None, kullanici=None) -> IsOturumu:
    """Oturum açar: emir açık ÜS'de ve bekliyor/başladı, fason değil; başlatan + ekip mesaisi açık, atölye yetkili ve başka açık oturumda değil
    (kişi satırları kilitlenir: aynı kişi iki oturuma eş zamanlı giremesin)."""
    ie = IstasyonEmri.objects.select_related("istasyon", "uretim_emri", "operasyon__cikti").get(pk=ie.pk)
    if ie.silindi or ie.istasyon.fason:
        raise OturumHatasi("Bu emirde atölyeden iş başlatılamaz (fason istasyonu).")
    if (ie.uretim_emri.silindi or ie.uretim_emri.durum != UretimEmri.Durum.ACIK
            or ie.durum not in (IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI)):
        raise OturumHatasi("Bu emir açık değil; iş başlatılamaz.")
    kisiler = [personel] + [p for p in ekip if p.pk != personel.pk]
    if len({p.pk for p in kisiler}) != len(kisiler):
        raise OturumHatasi("Ekipte aynı kişi iki kez seçilmiş.")
    kilitli = {p.pk: p for p in Personel.objects.select_for_update().filter(pk__in=[p.pk for p in kisiler], silindi=False).order_by("pk")}
    simdi = timezone.now()
    oturum = IsOturumu.objects.create(istasyon_emri=ie, uretim_emri=ie.uretim_emri, istasyon=ie.istasyon, baslatan=personel,
                                      is_tarihi=simdi.astimezone(_TR).date(), baslangic_zamani=simdi, baslangic_ip=ip or None,
                                      created_by=kullanici, updated_by=kullanici)
    for p in kisiler:
        kp = kilitli.get(p.pk)
        if kp is None:
            raise OturumHatasi("Ekipteki bir personel bulunamadı.")
        mesai = MesaiKaydi.objects.filter(personel=kp, silindi=False, cikis_zamani__isnull=True).first()
        if mesai is None:
            raise OturumHatasi(f"{kp.ad_soyad}: mesaisi açık değil; ekibe alınamaz." if kp.pk != personel.pk
                               else "Önce mesainizi başlatın; mesai açık değilken iş başlatılamaz.")
        u = aktif_uyelik(kp)
        if u is not None:
            raise OturumHatasi(f"{kp.ad_soyad} şu an başka bir işte: {_mesgul_metni(u)}. Önce o iş bitirilmeli.")
        if kp.pk != personel.pk and not EkranYetki.objects.filter(kullanici_id=kp.kullanici_id, ekran_kod=ATOLYE_EKRAN_KODU, silindi=False).exists():
            raise OturumHatasi(f"{kp.ad_soyad}: atölye yetkisi yok; ekibe alınamaz.")
        IsOturumuUyesi.objects.create(oturum=oturum, personel=kp, mesai_kaydi=mesai, baslatan=(kp.pk == personel.pk), katilim_zamani=simdi,
                                      created_by=kullanici, updated_by=kullanici)
    return oturum


def _kapat(oturum: IsOturumu, *, durum, bitis, bitiren, ip, kullanici, miktar=None, kayit=None):
    oturum.durum, oturum.bitis_zamani, oturum.bitiren, oturum.bitis_ip = durum, bitis, bitiren, ip or None
    oturum.miktar, oturum.kayit, oturum.updated_by = miktar, kayit, kullanici
    oturum.save(update_fields=["durum", "bitis_zamani", "bitiren", "bitis_ip", "miktar", "kayit", "updated_by", "updated_at"])
    IsOturumuUyesi.objects.filter(oturum=oturum, silindi=False, ayrilma_zamani__isnull=True).update(
        ayrilma_zamani=bitis, updated_by=kullanici, updated_at=timezone.now())


@transaction.atomic
def bitir(personel, oturum: IsOturumu, miktar, *, bitis=None, ip=None, kullanici=None):
    """Adetle üretim: ``istasyon_emri_uret`` (kayıt personeli = başlatan, mesai = başlatanın oturumdaki mesaisi, tarih = bitişin TR tarihi)
    başarılıysa oturum BITTI + kayıt bağlı; üretim hatasında (stok, fazla üretim yasağı…) hiçbir şey değişmez — oturum açık kalır.
    (kayıt, uyarı) döner."""
    oturum = IsOturumu.objects.select_for_update().select_related("istasyon_emri", "baslatan").get(pk=oturum.pk)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    _uyelik(personel, oturum)
    if miktar is None or miktar <= 0:
        raise OturumHatasi("Adet girilmeli (0 adetle bitirilemez; iş yapılmadıysa \"İptal\" kullanın).")
    simdi = timezone.now()
    bitis = _bitis_coz(oturum, bitis, simdi)
    baslatan_uye = IsOturumuUyesi.objects.filter(oturum=oturum, baslatan=True, silindi=False).first()
    try:
        kayit, uyari = uretim.istasyon_emri_uret(oturum.istasyon_emri, miktar=miktar, tarih=bitis.astimezone(_TR).date(), kullanici=kullanici,
                                                 personel=oturum.baslatan, mesai_kaydi=baslatan_uye.mesai_kaydi if baslatan_uye else None)
    except uretim.UretimHatasi as e:
        raise OturumHatasi(str(e))
    _kapat(oturum, durum=IsOturumu.Durum.BITTI, bitis=bitis, bitiren=personel, ip=ip, kullanici=kullanici, miktar=miktar, kayit=kayit)
    return kayit, uyari


@transaction.atomic
def iptal(personel, oturum: IsOturumu, *, bitis=None, ip=None, kullanici=None) -> IsOturumu:
    """İş yapılmadı: kayıt açılmaz, süre raporda kalır (verimsiz saat)."""
    oturum = IsOturumu.objects.select_for_update().get(pk=oturum.pk)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    _uyelik(personel, oturum)
    bitis = _bitis_coz(oturum, bitis, timezone.now())
    _kapat(oturum, durum=IsOturumu.Durum.IPTAL, bitis=bitis, bitiren=personel, ip=ip, kullanici=kullanici)
    return oturum


def mesai_bitiriliyor(personel, simdi, kullanici=None) -> None:
    """``mesai.bitir`` kancası: kişi açık bir oturumdaysa ve ekipte başkası kaldıysa üyeliği kapanır (oturum sürer); tek kişiyse ENGEL."""
    u = aktif_uyelik(personel)
    if u is None:
        return
    baska = IsOturumuUyesi.objects.filter(oturum=u.oturum, silindi=False, ayrilma_zamani__isnull=True).exclude(pk=u.pk).exists()
    if not baska:
        raise OturumHatasi(f"Önce açık işinizi bitirin: {_mesgul_metni(u)}.")
    u.ayrilma_zamani, u.updated_by = simdi, kullanici
    u.save(update_fields=["ayrilma_zamani", "updated_by", "updated_at"])


# --- ofis listesi ---

def listele(*, baslangic=None, bitis=None, istasyon_id=None, personel_id=None, durum=None, ara=""):
    qs = (IsOturumu.objects.filter(silindi=False)
          .select_related("istasyon_emri__operasyon__cikti", "uretim_emri__kaynak_siparis__cari", "istasyon", "baslatan", "bitiren", "kayit")
          .prefetch_related(_uyeler_prefetch()))
    if baslangic:
        qs = qs.filter(is_tarihi__gte=baslangic)
    if bitis:
        qs = qs.filter(is_tarihi__lte=bitis)
    if istasyon_id:
        qs = qs.filter(istasyon_id=istasyon_id)
    if personel_id:
        qs = qs.filter(uyeler__personel_id=personel_id, uyeler__silindi=False).distinct()
    if durum in dict(IsOturumu.Durum.choices):
        qs = qs.filter(durum=durum)
    ara = (ara or "").strip()
    if ara:
        from core.metin import buyuk_harf_tr
        qs = qs.filter(Q(uretim_emri__no__icontains=ara) | Q(istasyon_emri__no__icontains=ara)
                       | Q(istasyon_emri__operasyon__cikti__kod__icontains=ara) | Q(istasyon_emri__operasyon__cikti__ad__contains=buyuk_harf_tr(ara))
                       | Q(kayit__no__icontains=ara))
    return qs.order_by("-baslangic_zamani", "-pk")
