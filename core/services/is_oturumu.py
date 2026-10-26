"""ATÖLYE > İş Oturumları — TEK EKİP + SORUMLU modeli (kullanıcı kararları, 2026-10-12; 2026-10-21 yeniden düzenlendi):

* Bir istasyon emrinde aynı anda en çok **BİR açık oturum** olur (kodda + veritabanı kısmi benzersizliği). İlk başlatan **sorumlu**dur; **adet/fire girme, Bitir, İptal,
  Devret yalnız sorumluda**. Emirde açık oturum varken başkası "Başla" yerine **"Ekibe katıl"** der (sorumlu adı + ekip görünür): çalışır, süresi işçiliğe (adam-saat)
  sayılır, adet giremez, yalnız "Ekipten ayrıl" görür. Sorumlu Bitir/İptal deyince ekipteki herkesin üyeliği AYNI ANDA kapanır.
* **Devret**: sorumlu ekipten birini seçer (devreden isterse aynı anda ayrılır). Ekipte başkası varken sorumlu devretmeden ayrılamaz (ayrılamaz: "Ekipten ayrıl" yok);
  tek başınaysa Bitir/İptal yapar.
* **Otomatik devir** (``mesai_kapaniyor`` — elle / ofis düzeltmesi / fabrika dışı / gece / süpürme): sorumlunun mesaisi kapanırsa sorumluluk ekipte EN UZUN SÜREDİR olan,
  mesaisi hâlâ açık kişiye geçer. Ekipte kimse yoksa oturum "İptal" OLMAZ → **"Adet bekliyor"** (süre işçiliğe sayılır): sorumlu sonraki girişinde ana ekranda kart olarak
  görür ve adedi girer, ya da yönetici girer; adet girilince normal üretim kaydı oluşur (kayıt tarihi = oturumun bitiş günü). Elle (kendi) mesai bitirmede tek başına sorumlu
  adet/fire girerse oturum normal Bitir ile kapanır, girmezse onay kutusuyla "Adet bekliyor" olur. Adet bekleyen oturum varken aynı emre yeni oturum açılabilir.
* **Yönetici**: sorumluyu değiştirir; işi "Adet bekliyor" olarak kapatır ya da İPTAL eder (NOT ZORUNLU); "Adet bekliyor" oturuma adet girer. Her işlem ``IsOturumuOlay`` denetim kaydı.
* Bir personel aynı anda yalnız BİR açık oturumda olabilir (sorumlu ya da üye). Zaman hep sunucu saati.
* **Bitir** (SAĞLAM adet, tam sayı > 0; FİRE isteğe bağlı — girdiler sağlam+fire için tüketilir, stoğa yalnız sağlam girer): ``uretim.istasyon_emri_uret`` aynen çalışır (tarih bazlı
  stok kontrolü, LAZER/PAKETLEME fazla üretim yasağı); hata olursa oturum AÇIK kalır. Kayıt personeli = SORUMLU, bitiren ayrı iz. **İptal (iş yapılmadı)**: kayıt yok, süre raporda kalır.
* Önceki günden kalan oturum bitirilirken bitiş saati sorulur (varsayılan: kişinin o günkü mesai çıkışı), kayıt tarihi = bitişin tarihi.
* Mesaisi kapalı kişide açık üyelik KALAMAZ: ofis mesaiyi elle kapatınca/silince üyelik de kapanır (sorumluysa devir/adet bekliyor); cron süpürmesi kalanları toplar.
* Adam-saat = üyelerin kendi katılım sürelerinin toplamı; işçilik/GÜG dağıtımı AYRI adım, burada yalnız veri birikir.
* Ekipten AYRILIP aynı açık oturuma TEKRAR KATILMA serbesttir (birden çok kez): her katılım ayrı ``IsOturumuUyesi`` satırıdır, kapalı satırlar kalır (``uq_is_oturumu_uyesi_aktif`` yalnız
  AÇIK üyeliği tekil tutar). Süre = satırların aralıkları toplamı; ekip/kişi sayısı ve İş Takibi payları KİŞİ bazında tekildir.
* KARMA KESİM oturumu (``karma=True``, bkz. core.services.karma_kesim): emire bağlı DEĞİL, istasyon başına en çok BİR açık oturum, tek profil için. Aynı istasyonda bu profili tüketen
  bir emirde açık tekil oturum varken karma açılamaz (ve tersi); ofis "Üretildi" de aynı profil için engellenir. Bitir = kullanılan boy sayısı + parça adetleri
  (``bitir_karma`` / "Adet bekliyor"da ``adet_gir_karma``); hatada oturum açık kalır. Katıl / ayrıl / devret / iptal / mesai kapanışı kuralları aynıdır. Yönetici karma adedini
  GİREMEZ (sorumlu atölyeden girer); yönetici sorumluyu değiştirir ya da iptal eder."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from core.models import (
    EkranYetki, IsIstasyonu, IsOturumu, IsOturumuOlay, IsOturumuUyesi, IstasyonEmri, KarmaKesim, MesaiKaydi, OperasyonGirdi, Personel, Stok, UretimEmri,
)
from core.services import karma_kesim, uretim
from core.tarih import _TR, tr_bugun

UNUTULMA_SAATI = datetime.time(18, 0)
ATOLYE_EKRAN_KODU = "atolye"
T = IsOturumuOlay.Tur


class OturumHatasi(ValueError):
    """İş oturumu kural ihlali (Türkçe mesaj)."""


# --- denetim izi ---

def olay(oturum, tur, *, personel=None, hedef=None, kullanici=None, ip=None, aciklama="") -> IsOturumuOlay:
    k = kullanici if getattr(kullanici, "pk", None) else None
    return IsOturumuOlay.objects.create(oturum=oturum, tur=tur, personel=personel, hedef=hedef, kullanici=k, ip=ip or None, aciklama=(aciklama or "")[:500],
                                        created_by=k, updated_by=k)


# --- sorgular ---

def _uyeler_prefetch():
    return Prefetch("uyeler", queryset=IsOturumuUyesi.objects.filter(silindi=False).select_related("personel", "mesai_kaydi").order_by("-baslatan", "id"))


def _qs(durum):
    return (IsOturumu.objects.filter(silindi=False, durum=durum)
            .select_related("istasyon_emri__operasyon__cikti", "uretim_emri", "istasyon", "baslatan", "sorumlu", "karma_profil", "karma_kesim")
            .prefetch_related(_uyeler_prefetch()))


def _acik_qs():
    return _qs(IsOturumu.Durum.ACIK)


def aktif_uyelik(personel) -> IsOturumuUyesi | None:
    """Personelin şu an içinde olduğu açık oturum üyeliği (kural: en çok bir)."""
    return (IsOturumuUyesi.objects.filter(personel=personel, silindi=False, ayrilma_zamani__isnull=True, oturum__silindi=False,
                                          oturum__durum=IsOturumu.Durum.ACIK)
            .select_related("oturum__istasyon", "oturum__uretim_emri", "oturum__istasyon_emri__operasyon__cikti", "oturum__karma_profil", "oturum__sorumlu",
                            "oturum__baslatan").order_by("-pk").first())


def acik_oturumlar(ie: IstasyonEmri | None = None) -> list:
    qs = _acik_qs()
    if ie is not None:
        qs = qs.filter(istasyon_emri=ie)
    return list(qs.order_by("baslangic_zamani", "pk"))


def karma_oturumlari(istasyon_id, durum=IsOturumu.Durum.ACIK) -> list:
    """İstasyondaki KARMA kesim oturumları (varsayılan AÇIK; "Adet bekliyor" için ``durum=IsOturumu.Durum.ADET``)."""
    return list(_qs(durum).filter(karma=True, istasyon_id=istasyon_id).order_by("pk"))


def emirde_acik_oturum(ie: IstasyonEmri) -> IsOturumu | None:
    return _acik_qs().filter(istasyon_emri=ie).order_by("pk").first()


def adet_bekleyenler(ie: IstasyonEmri | None = None) -> list:
    qs = _qs(IsOturumu.Durum.ADET)
    if ie is not None:
        qs = qs.filter(istasyon_emri=ie)
    return list(qs.order_by("bitis_zamani", "pk"))


def kisinin_acik_oturumlari(personel) -> list:
    """Personelin aktif üye olduğu açık oturumlar (dünden kalanlar dahil; ekranda en üstte)."""
    pks = IsOturumuUyesi.objects.filter(personel=personel, silindi=False, ayrilma_zamani__isnull=True).values("oturum_id")
    return list(_acik_qs().filter(pk__in=pks).order_by("baslangic_zamani", "pk"))


def kisinin_adet_bekleyenleri(personel) -> list:
    """Sorumlusu bu personel olan "Adet bekliyor" oturumlar (ana ekranda kart; sonraki girişinde adedi girer)."""
    return list(_qs(IsOturumu.Durum.ADET).filter(Q(sorumlu=personel) | Q(sorumlu__isnull=True, baslatan=personel)).order_by("bitis_zamani", "pk"))


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
              .select_related("oturum__istasyon", "oturum__uretim_emri", "oturum__karma_profil")}
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
    """Ekip + sorumlu + süre + adam-saat + adet/saat + adet başı süre (rapor ve ekran satırı). Ekip metni sorumlu önde."""
    sorumlu = oturum.sorumlu_kisi
    uyeler = sorted((u for u in oturum.uyeler.all() if not u.silindi), key=lambda u: (u.personel_id != sorumlu.pk, -int(u.baslatan), u.pk))
    bitis = oturum.bitis_zamani
    sure = oturum.sure_saniye
    adam_sn = sum(u.katilim_saniye(bitis) for u in uyeler)
    adet = oturum.miktar
    adet_saat = adet_basi_sn = None
    if adet and oturum.durum == IsOturumu.Durum.BITTI:
        adet_saat = (adet / Decimal(sure) * 3600).quantize(Decimal("0.1")) if sure else None
        adet_basi_sn = int(Decimal(adam_sn) / adet)
    kisiler = list(dict.fromkeys(u.personel_id for u in uyeler))                       # ayrılıp tekrar katılan kişi birden çok satırdır: kişi/ekip metni TEKİL, süre satırların toplamı
    adlar = list(dict.fromkeys(u.personel.ad_soyad for u in uyeler))
    return {"oturum": oturum, "uyeler": uyeler, "kisi": len(kisiler), "sorumlu": sorumlu, "ekip_metni": ", ".join(adlar),
            "acik_uyeler": [u for u in uyeler if u.ayrilma_zamani is None],
            "sure_saniye": sure, "sure_metni": sure_metni(sure), "adam_saniye": adam_sn,
            "adam_saat": (Decimal(adam_sn) / 3600).quantize(Decimal("0.01")), "adam_saat_metni": sure_metni(adam_sn),
            "adet": adet, "fire": oturum.fire, "adet_saat": adet_saat, "adet_basi_saniye": adet_basi_sn,
            "adet_bekliyor": oturum.durum == IsOturumu.Durum.ADET, "karma": oturum.karma,
            "adet_basi_metni": sure_metni(adet_basi_sn) if adet_basi_sn is not None else "",
            "baslangic_ms": int(oturum.baslangic_zamani.timestamp() * 1000), "dunku": oturum.is_tarihi < tr_bugun()}


def varsayilan_bitis(oturum: IsOturumu, personel):
    """Önceki günden kalan oturum için önerilen bitiş: kişinin o günkü (son) mesai çıkışı, yoksa sorumlunun/başlatanınki, yoksa None."""
    for p in (personel, oturum.sorumlu_kisi, oturum.baslatan):
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


def _sorumlu_sart(personel, oturum: IsOturumu) -> None:
    s = oturum.sorumlu_kisi
    if s.pk != personel.pk:
        raise OturumHatasi(f"Adet girme, Bitir ve İptal yalnızca sorumlu kişiye aittir: {s.ad_soyad}. Siz ekip üyesisiniz (çalışıyorsunuz; süreniz işçiliğe sayılır).")


def _mesgul_metni(uyelik: IsOturumuUyesi) -> str:
    o = uyelik.oturum
    return f"{o.istasyon.kod} {o.istasyon.ad} › {o.us_no}" + (f" · {o.karma_profil.kod}" if o.karma else "")


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


def _kilitle(oturum) -> IsOturumu:
    return (IsOturumu.objects.select_for_update(of=("self",)).select_related("istasyon_emri", "baslatan", "sorumlu", "istasyon", "uretim_emri").get(pk=oturum.pk))


# --- işlemler: başlat / katıl / ayrıl / devret ---

@transaction.atomic
def baslat(personel, ie: IstasyonEmri, ekip, *, ip=None, kullanici=None) -> IsOturumu:
    """Oturum açar: emir açık ÜS'de ve bekliyor/başladı, fason değil; EMİRDE AÇIK OTURUM YOK (varsa "Ekibe katıl"); başlatan + ekip mesaisi açık, atölye yetkili ve
    başka açık oturumda değil (kişi satırları kilitlenir). Başlatan sorumlu doğar."""
    ie = IstasyonEmri.objects.select_for_update(of=("self",)).select_related("istasyon", "uretim_emri", "operasyon__cikti").get(pk=ie.pk)
    if ie.silindi or ie.istasyon.fason:
        raise OturumHatasi("Bu emirde atölyeden iş başlatılamaz (fason istasyonu).")
    if (ie.uretim_emri.silindi or ie.uretim_emri.durum != UretimEmri.Durum.ACIK
            or ie.durum not in (IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI)):
        raise OturumHatasi("Bu emir açık değil; iş başlatılamaz.")
    mevcut = emirde_acik_oturum(ie)
    if mevcut is not None:
        oz = ozet(mevcut)
        raise OturumHatasi(f"Bu emirde {oz['sorumlu'].ad_soyad} sorumluluğunda bir ekip çalışıyor ({oz['ekip_metni']}); yeni iş açılamaz — \"Ekibe katıl\" deyin.")
    _karma_cakisma_emir(ie)
    kisiler = [personel] + [p for p in ekip if p.pk != personel.pk]
    if len({p.pk for p in kisiler}) != len(kisiler):
        raise OturumHatasi("Ekipte aynı kişi iki kez seçilmiş.")
    kilitli = {p.pk: p for p in Personel.objects.select_for_update().filter(pk__in=[p.pk for p in kisiler], silindi=False).order_by("pk")}
    simdi = timezone.now()
    try:
        with transaction.atomic():
            oturum = IsOturumu.objects.create(istasyon_emri=ie, uretim_emri=ie.uretim_emri, istasyon=ie.istasyon, baslatan=personel, sorumlu=personel,
                                              is_tarihi=simdi.astimezone(_TR).date(), baslangic_zamani=simdi, baslangic_ip=ip or None,
                                              created_by=kullanici, updated_by=kullanici)
    except IntegrityError:
        raise OturumHatasi("Bu emirde az önce başka bir iş başlatıldı; \"Ekibe katıl\" deyin.")
    _uyeleri_ekle(oturum, personel, kisiler, kilitli, simdi, kullanici)
    return oturum


def _uyeleri_ekle(oturum: IsOturumu, personel, kisiler, kilitli: dict, simdi, kullanici) -> None:
    """Başlatan + ekip üyelerini oturuma yazar (her biri için: mesai açık, atölye yetkili, başka açık oturumda değil)."""
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


def _profil_idleri(ie: IstasyonEmri) -> list:
    """Emrin operasyonunun girdi stokları (karma kesim çakışma denetimi: emir bir karma kesim profilini tüketiyor mu)."""
    return list(OperasyonGirdi.objects.filter(operasyon_id=ie.operasyon_id, silindi=False).values_list("girdi_id", flat=True))


def _karma_cakisma_emir(ie: IstasyonEmri) -> None:
    """Emirde (tekil) iş başlatmadan önce: aynı istasyonda bu emrin tükettiği bir profilde AÇIK karma kesim oturumu varsa reddedilir (tek ekip kuralı istasyon + profil için)."""
    o = (_acik_qs().filter(karma=True, istasyon_id=ie.istasyon_id, karma_profil_id__in=_profil_idleri(ie)).order_by("pk").first())
    if o is not None:
        oz = ozet(o)
        raise OturumHatasi(f"Bu istasyonda {o.karma_profil.kod} için {oz['sorumlu'].ad_soyad} sorumluluğunda bir KARMA KESİM çalışıyor ({oz['ekip_metni']}); "
                           "bu emirde iş açılamaz. Karma kesim bitince ya da \"Ekibe katıl\" ile devam edin.")


@transaction.atomic
def baslat_karma(personel, istasyon: IsIstasyonu, profil: Stok, ekip, *, ip=None, kullanici=None) -> IsOturumu:
    """KARMA KESİM oturumu açar (``core.services.karma_kesim``): istasyon fason değil, profil bu istasyonda karma kesilebilir (BOY birimli, boy uzunluğu ve boyu bilinen parçalar var);
    İSTASYONDA AÇIK KARMA OTURUM YOK (varsa "Ekibe katıl") ve aynı istasyonda bu profili tüketen bir emirde açık tekil oturum YOK; ekip/mesai/yetki kuralları ``baslat`` ile aynı.
    Başlatan sorumlu doğar. Oturum emire bağlı değildir; Bitir'de parça listesi girilir (``bitir_karma``)."""
    istasyon = IsIstasyonu.objects.select_for_update().get(pk=istasyon.pk)
    if istasyon.silindi or istasyon.fason:
        raise OturumHatasi("Bu istasyonda atölyeden karma kesim başlatılamaz (fason istasyonu).")
    if profil is None or profil.pk not in {x["profil"].pk for x in karma_kesim.profil_secenekleri(istasyon.pk)}:
        raise OturumHatasi("Bu istasyonda bu profil için karma kesim yapılamaz (profil boyu ya da parça boyları tanımlı değil).")
    mevcut = _acik_qs().filter(karma=True, istasyon=istasyon).order_by("pk").first()
    if mevcut is not None:
        oz = ozet(mevcut)
        raise OturumHatasi(f"Bu istasyonda {oz['sorumlu'].ad_soyad} sorumluluğunda bir karma kesim çalışıyor ({oz['ekip_metni']}); yeni iş açılamaz — \"Ekibe katıl\" deyin.")
    tekil = (_acik_qs().filter(karma=False, istasyon=istasyon, istasyon_emri__operasyon__girdiler__girdi=profil, istasyon_emri__operasyon__girdiler__silindi=False)
             .order_by("pk").first())
    if tekil is not None:
        oz = ozet(tekil)
        raise OturumHatasi(f"Bu istasyonda {profil.kod} kullanan bir işte ({tekil.emir_no}) {oz['sorumlu'].ad_soyad}'in ekibi çalışıyor; karma kesim açılamaz. Önce o iş bitmeli.")
    kisiler = [personel] + [p for p in ekip if p.pk != personel.pk]
    if len({p.pk for p in kisiler}) != len(kisiler):
        raise OturumHatasi("Ekipte aynı kişi iki kez seçilmiş.")
    kilitli = {p.pk: p for p in Personel.objects.select_for_update().filter(pk__in=[p.pk for p in kisiler], silindi=False).order_by("pk")}
    simdi = timezone.now()
    try:
        with transaction.atomic():
            oturum = IsOturumu.objects.create(istasyon=istasyon, karma=True, karma_profil=profil, baslatan=personel, sorumlu=personel,
                                              is_tarihi=simdi.astimezone(_TR).date(), baslangic_zamani=simdi, baslangic_ip=ip or None,
                                              created_by=kullanici, updated_by=kullanici)
    except IntegrityError:
        raise OturumHatasi("Bu istasyonda az önce başka bir karma kesim başlatıldı; \"Ekibe katıl\" deyin.")
    _uyeleri_ekle(oturum, personel, kisiler, kilitli, simdi, kullanici)
    return oturum


@transaction.atomic
def katil(personel, oturum: IsOturumu, *, ip=None, kullanici=None) -> IsOturumuUyesi:
    """"Ekibe katıl": emirdeki açık oturuma üye olur — çalışır, süresi işçiliğe sayılır; adet giremez. Mesai açık, başka açık oturumda değil; emir hâlâ açık olmalı."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu artık açık değil.")
    ie = oturum.istasyon_emri
    if not oturum.karma and (ie.silindi or oturum.uretim_emri.silindi or oturum.uretim_emri.durum != UretimEmri.Durum.ACIK
                             or ie.durum not in (IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI)):
        raise OturumHatasi("Bu emir açık değil; ekibe katılınamaz.")
    p = Personel.objects.select_for_update().get(pk=personel.pk)
    mesai = MesaiKaydi.objects.filter(personel=p, silindi=False, cikis_zamani__isnull=True).first()
    if mesai is None:
        raise OturumHatasi("Önce mesainizi başlatın; mesai açık değilken ekibe katılınamaz.")
    u = aktif_uyelik(p)
    if u is not None:
        raise OturumHatasi(f"Şu an başka bir işte olduğunuz için ekibe katılamazsınız: {_mesgul_metni(u)}." if u.oturum_id != oturum.pk else "Bu işin ekibindesiniz.")
    uye = IsOturumuUyesi.objects.create(oturum=oturum, personel=p, mesai_kaydi=mesai, baslatan=False, katilim_zamani=timezone.now(), created_by=kullanici, updated_by=kullanici)
    olay(oturum, T.KATILDI, personel=p, kullanici=kullanici, ip=ip)
    return uye


@transaction.atomic
def ayril(personel, oturum: IsOturumu, *, ip=None, kullanici=None) -> IsOturumu:
    """"Ekipten ayrıl": üye (sorumlu DEĞİL) ekipten çıkar; süresi o ana kadar işçiliğe sayılır. Sorumlu ayrılamaz: devretmeli ya da Bitir/İptal yapmalı."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu artık açık değil.")
    u = _uyelik(personel, oturum)
    if oturum.sorumlu_kisi.pk == personel.pk:
        raise OturumHatasi("Sorumlu ekipten ayrılamaz: önce sorumluluğu devredin; tek başınaysanız Bitir ya da İptal yapın.")
    u.ayrilma_zamani, u.updated_by = timezone.now(), kullanici
    u.save(update_fields=["ayrilma_zamani", "updated_by", "updated_at"])
    olay(oturum, T.AYRILDI, personel=personel, kullanici=kullanici, ip=ip)
    return oturum


@transaction.atomic
def devret(personel, oturum: IsOturumu, yeni, *, ayril_=False, ip=None, kullanici=None) -> IsOturumu:
    """Sorumlu, sorumluluğu ekipten (açık üye) birine devreder; ``ayril_=True`` ise devreden aynı anda ekipten ayrılır (varsayılan: ekipte katılan olarak kalır)."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu artık açık değil.")
    _sorumlu_sart(personel, oturum)
    if yeni is None or yeni.pk == personel.pk:
        raise OturumHatasi("Sorumluluğu devredeceğiniz ekip arkadaşını seçin.")
    hedef_uye = IsOturumuUyesi.objects.filter(oturum=oturum, personel=yeni, silindi=False, ayrilma_zamani__isnull=True).first()
    if hedef_uye is None:
        raise OturumHatasi(f"{yeni.ad_soyad} bu işin ekibinde değil; yalnız ekipten birine devredilebilir.")
    oturum.sorumlu, oturum.updated_by = yeni, kullanici
    oturum.save(update_fields=["sorumlu", "updated_by", "updated_at"])
    if ayril_:
        u = _uyelik(personel, oturum)
        u.ayrilma_zamani, u.updated_by = timezone.now(), kullanici
        u.save(update_fields=["ayrilma_zamani", "updated_by", "updated_at"])
    olay(oturum, T.DEVIR, personel=personel, hedef=yeni, kullanici=kullanici, ip=ip, aciklama="devreden ayrıldı" if ayril_ else "")
    return oturum


# --- işlemler: bitir / iptal / adet ---

def _kapat(oturum: IsOturumu, *, durum, bitis, bitiren, ip, kullanici, miktar=None, kayit=None, fire=None):
    oturum.durum, oturum.bitis_zamani, oturum.bitiren, oturum.bitis_ip = durum, bitis, bitiren, ip or None
    oturum.miktar, oturum.fire, oturum.kayit, oturum.updated_by = miktar, fire, kayit, kullanici
    oturum.save(update_fields=["durum", "bitis_zamani", "bitiren", "bitis_ip", "miktar", "fire", "kayit", "updated_by", "updated_at"])
    IsOturumuUyesi.objects.filter(oturum=oturum, silindi=False, ayrilma_zamani__isnull=True).update(
        ayrilma_zamani=bitis, updated_by=kullanici, updated_at=timezone.now())


def _adet_dogrula(miktar, fire):
    if miktar is None or miktar <= 0:
        raise OturumHatasi("Adet girilmeli (0 adetle bitirilemez; iş yapılmadıysa \"İptal\" kullanın).")
    fire = Decimal(fire or 0)
    if fire < 0:
        raise OturumHatasi("Fire adedi negatif olamaz.")
    return fire


@transaction.atomic
def bitir(personel, oturum: IsOturumu, miktar, *, fire=None, bitis=None, ip=None, kullanici=None):
    """Adetle üretim — YALNIZ SORUMLU: ``istasyon_emri_uret`` (kayıt personeli = sorumlu, mesai = sorumlunun oturumdaki mesaisi, tarih = bitişin TR tarihi) başarılıysa oturum BITTI +
    kayıt bağlı + tüm ekibin üyeliği aynı anda kapanır; üretim hatasında (stok, fazla üretim yasağı…) hiçbir şey değişmez — oturum açık kalır. ``miktar`` = SAĞLAM adet;
    ``fire`` (≥ 0, isteğe bağlı) = fire adet. (kayıt, uyarı) döner."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    _tekil_sart(oturum)
    _uyelik(personel, oturum)
    _sorumlu_sart(personel, oturum)
    fire = _adet_dogrula(miktar, fire)
    simdi = timezone.now()
    bitis = _bitis_coz(oturum, bitis, simdi)
    sorumlu_uye = IsOturumuUyesi.objects.filter(oturum=oturum, personel=personel, silindi=False).order_by("-pk").first()
    try:
        kayit, uyari = uretim.istasyon_emri_uret(oturum.istasyon_emri, miktar=miktar, tarih=bitis.astimezone(_TR).date(), kullanici=kullanici,
                                                 personel=personel, mesai_kaydi=sorumlu_uye.mesai_kaydi if sorumlu_uye else None,
                                                 fire=fire if fire > 0 else None, oturum_kontrol=False)
    except uretim.UretimHatasi as e:
        raise OturumHatasi(str(e))
    _kapat(oturum, durum=IsOturumu.Durum.BITTI, bitis=bitis, bitiren=personel, ip=ip, kullanici=kullanici, miktar=miktar, kayit=kayit, fire=fire)
    return kayit, uyari


@transaction.atomic
def iptal(personel, oturum: IsOturumu, *, bitis=None, ip=None, kullanici=None) -> IsOturumu:
    """İş yapılmadı — YALNIZ SORUMLU: kayıt açılmaz, süre raporda kalır; tüm ekibin üyeliği kapanır."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    _uyelik(personel, oturum)
    _sorumlu_sart(personel, oturum)
    bitis = _bitis_coz(oturum, bitis, timezone.now())
    _kapat(oturum, durum=IsOturumu.Durum.IPTAL, bitis=bitis, bitiren=personel, ip=ip, kullanici=kullanici)
    return oturum


@transaction.atomic
def adet_gir(oturum: IsOturumu, miktar, *, fire=None, personel=None, kullanici=None, ip=None):
    """"Adet bekliyor" oturuma adet/fire girer (sorumlu kendisi ``personel=`` ile, yönetici ``personel=None``): normal üretim kaydı (kayıt tarihi = oturumun bitiş günü, personel =
    sorumlu) oluşur, oturum BITTI olur; bitiş zamanı/süre DEĞİŞMEZ. Üretim hatasında (fazla üretim yasağı, stok…) hiçbir şey değişmez. (kayıt, uyarı) döner."""
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ADET:
        raise OturumHatasi("Bu iş oturumu adet beklemiyor.")
    _tekil_sart(oturum)
    s = oturum.sorumlu_kisi
    if personel is not None and s.pk != personel.pk:
        raise OturumHatasi(f"Adedi yalnızca sorumlu ({s.ad_soyad}) ya da yönetici girebilir.")
    fire = _adet_dogrula(miktar, fire)
    uye = IsOturumuUyesi.objects.filter(oturum=oturum, personel=s, silindi=False).order_by("-pk").first()
    try:
        kayit, uyari = uretim.istasyon_emri_uret(oturum.istasyon_emri, miktar=miktar, tarih=oturum.bitis_zamani.astimezone(_TR).date(), kullanici=kullanici,
                                                 personel=s, mesai_kaydi=uye.mesai_kaydi if uye else None, fire=fire if fire > 0 else None, oturum_kontrol=False)
    except uretim.UretimHatasi as e:
        raise OturumHatasi(str(e))
    oturum.durum, oturum.miktar, oturum.fire, oturum.kayit, oturum.bitiren, oturum.updated_by = IsOturumu.Durum.BITTI, miktar, fire, kayit, personel, kullanici
    oturum.save(update_fields=["durum", "miktar", "fire", "kayit", "bitiren", "updated_by", "updated_at"])
    olay(oturum, T.ADET_GIRILDI, personel=personel, kullanici=kullanici, ip=ip, aciklama=f"{miktar.normalize():f} sağlam" + (f" + {fire.normalize():f} fire" if fire else "") + (" (yönetici)" if personel is None else ""))
    return kayit, uyari


def _tekil_sart(oturum: IsOturumu) -> None:
    """Tek adetli Bitir / Adet gir yalnız emirli oturumda; karma kesim oturumunda parça listesi (``bitir_karma`` / ``adet_gir_karma``) girilir."""
    if oturum.karma:
        raise OturumHatasi("Karma kesim oturumunda tek adet girilemez: kullanılan boy sayısı ve parça adetlerini karma kesim formundan girin.")


def _karma_sart(oturum: IsOturumu) -> None:
    if not oturum.karma:
        raise OturumHatasi("Bu iş karma kesim oturumu değil.")


def _karma_yaz(oturum: IsOturumu, boy_sayisi, satirlar, *, personel, mesai_kaydi, tarih, kullanici):
    """Karma kesimi yazar (tek işlem; hata → ``OturumHatasi``, oturum durumu değişmez). Dönen: ``KarmaKesim``."""
    depo = karma_kesim.varsayilan_depo(oturum.istasyon_id, oturum.karma_profil_id)
    if depo is None:
        raise OturumHatasi("Karma kesim için uygun depo bulunamadı.")
    try:
        return karma_kesim.karma_kes(istasyon_id=oturum.istasyon_id, profil_id=oturum.karma_profil_id, depo_id=depo.pk, boy_sayisi=boy_sayisi, satirlar=satirlar,
                                     tarih=tarih, aciklama=f"Atölye karma kesim oturumu #{oturum.pk}", kullanici=kullanici, personel=personel, mesai_kaydi=mesai_kaydi)
    except uretim.UretimHatasi as e:                       # KarmaKesimHatasi dahil
        raise OturumHatasi(str(e))


@transaction.atomic
def bitir_karma(personel, oturum: IsOturumu, boy_sayisi, satirlar, *, bitis=None, ip=None, kullanici=None) -> KarmaKesim:
    """Karma kesim Bitir — YALNIZ SORUMLU: ``karma_kesim.karma_kes`` (uzunluk bütçesi, fire, emir dağıtımı, stok/maliyet) başarılıysa oturum BITTI + ``karma_kesim`` bağlı
    + tüm ekibin üyeliği kapanır; hata (bütçe aşımı, yetersiz stok…) olursa hiçbir şey değişmez, oturum AÇIK kalır. Kayıt personeli = sorumlu, tarih = bitişin TR tarihi.
    ``satirlar`` = [(parça stok pk, adet)]. Fire hesaplanır (elle girilmez); oturumun ``miktar``ı = toplam parça adedi."""
    oturum = _kilitle(oturum)
    _karma_sart(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    _uyelik(personel, oturum)
    _sorumlu_sart(personel, oturum)
    simdi = timezone.now()
    bitis = _bitis_coz(oturum, bitis, simdi)
    uye = IsOturumuUyesi.objects.filter(oturum=oturum, personel=personel, silindi=False).order_by("-pk").first()
    karma = _karma_yaz(oturum, boy_sayisi, satirlar, personel=personel, mesai_kaydi=uye.mesai_kaydi if uye else None, tarih=bitis.astimezone(_TR).date(), kullanici=kullanici)
    _kapat(oturum, durum=IsOturumu.Durum.BITTI, bitis=bitis, bitiren=personel, ip=ip, kullanici=kullanici, miktar=Decimal(karma.toplam_adet))
    oturum.karma_kesim = karma
    oturum.save(update_fields=["karma_kesim", "updated_at"])
    return karma


@transaction.atomic
def adet_gir_karma(oturum: IsOturumu, boy_sayisi, satirlar, *, personel=None, kullanici=None, ip=None) -> KarmaKesim:
    """"Adet bekliyor" KARMA oturuma boy sayısı + parça adetleri girer (sorumlu kendisi ``personel=`` ile, yönetici ``personel=None``): karma kesim yazılır (tarih = oturumun bitiş
    günü, personel = sorumlu), oturum BITTI olur; bitiş zamanı/süre DEĞİŞMEZ. Hata → hiçbir şey değişmez."""
    oturum = _kilitle(oturum)
    _karma_sart(oturum)
    if oturum.durum != IsOturumu.Durum.ADET:
        raise OturumHatasi("Bu iş oturumu adet beklemiyor.")
    s = oturum.sorumlu_kisi
    if personel is not None and s.pk != personel.pk:
        raise OturumHatasi(f"Adedi yalnızca sorumlu ({s.ad_soyad}) ya da yönetici girebilir.")
    uye = IsOturumuUyesi.objects.filter(oturum=oturum, personel=s, silindi=False).order_by("-pk").first()
    karma = _karma_yaz(oturum, boy_sayisi, satirlar, personel=s, mesai_kaydi=uye.mesai_kaydi if uye else None, tarih=oturum.bitis_zamani.astimezone(_TR).date(), kullanici=kullanici)
    oturum.durum, oturum.miktar, oturum.karma_kesim, oturum.bitiren, oturum.updated_by = IsOturumu.Durum.BITTI, Decimal(karma.toplam_adet), karma, personel, kullanici
    oturum.save(update_fields=["durum", "miktar", "karma_kesim", "bitiren", "updated_by", "updated_at"])
    olay(oturum, T.ADET_GIRILDI, personel=personel, kullanici=kullanici, ip=ip,
         aciklama=f"{karma.no}: {karma.boy_sayisi} boy, {karma.toplam_adet} parça" + (" (yönetici)" if personel is None else ""))
    return karma


def _adete_bagla(oturum: IsOturumu, bitis, kullanici=None, ip=None):
    """Açık oturumu "Adet bekliyor"a çevirir: süre korunur (bitiş = verilen an), kalan açık üyelikler kapanır."""
    bitis = max(bitis, oturum.baslangic_zamani)
    oturum.durum, oturum.bitis_zamani, oturum.bitiren, oturum.bitis_ip = IsOturumu.Durum.ADET, bitis, None, ip or None
    oturum.miktar = oturum.fire = oturum.kayit = None
    oturum.updated_by = kullanici if getattr(kullanici, "pk", None) else None
    oturum.save(update_fields=["durum", "bitis_zamani", "bitiren", "bitis_ip", "miktar", "fire", "kayit", "updated_by", "updated_at"])
    for u in IsOturumuUyesi.objects.filter(oturum=oturum, silindi=False, ayrilma_zamani__isnull=True):
        u.ayrilma_zamani = max(bitis, u.katilim_zamani)
        u.save(update_fields=["ayrilma_zamani", "updated_at"])


# --- mesai kapanışı: otomatik devir / adet bekliyor ---

@transaction.atomic
def mesai_kapaniyor(personel, bitis, *, neden, kullanici=None, ip=None, miktar=None, fire=None, bekle_onay=False):
    """Tek giriş: kişinin MESAİSİ kapanıyor (``neden``: 'elle' kendisi, 'yonetici' ofis düzeltme/silme, 'otomatik' fabrika dışı/gece, 'supurme' cron süpürmesi).
    * Kişi sorumlu DEĞİLSE: üyeliği ``bitis``te kapanır, oturum sürer.
    * SORUMLUYSA ve ekipte mesaisi açık biri varsa: sorumluluk ekipte EN UZUN SÜREDİR olana geçer, üyeliği kapanır (oturum sürer).
    * Ekipte kimse yoksa: oturum "İptal" OLMAZ → "Adet bekliyor" (süre korunur). 'elle'de tek başına sorumlu adet/fire girerse oturum normal Bitir ile kapanır (``miktar``),
      girmezse ``bekle_onay`` ile "Adet bekliyor" olur, onay da yoksa OturumHatasi (mesai bitmez).
    Açık oturumu yoksa None; aksi halde sonucu ('AYRILDI' | 'DEVIR' | 'ADET' | 'BITTI') döner."""
    u = aktif_uyelik(personel)
    if u is None:
        return None
    oturum = _kilitle(u.oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        return None
    ayr = max(bitis, u.katilim_zamani)
    if oturum.sorumlu_kisi.pk != personel.pk:
        u.ayrilma_zamani, u.updated_by = ayr, kullanici if getattr(kullanici, "pk", None) else None
        u.save(update_fields=["ayrilma_zamani", "updated_by", "updated_at"])
        olay(oturum, T.AYRILDI, personel=personel, kullanici=kullanici, ip=ip, aciklama=f"mesai kapandı ({neden})")
        return "AYRILDI"
    if neden == "elle" and miktar is not None and not oturum.karma:                                 # sorumlu mesai bitirirken adedi girdi: normal Bitir (tüm ekip kapanır; karma kesimde parça listesi gerekir)
        bitir(personel, oturum, miktar, fire=fire, ip=ip, kullanici=kullanici)
        return "BITTI"
    digerleri = [x for x in IsOturumuUyesi.objects.filter(oturum=oturum, silindi=False, ayrilma_zamani__isnull=True).exclude(pk=u.pk)
                 .select_related("personel", "mesai_kaydi").order_by("katilim_zamani", "pk")
                 if x.mesai_kaydi is not None and x.mesai_kaydi.cikis_zamani is None]
    if digerleri:
        yeni = digerleri[0].personel
        oturum.sorumlu, oturum.updated_by = yeni, kullanici if getattr(kullanici, "pk", None) else None
        oturum.save(update_fields=["sorumlu", "updated_by", "updated_at"])
        u.ayrilma_zamani = ayr
        u.save(update_fields=["ayrilma_zamani", "updated_at"])
        olay(oturum, T.OTOMATIK_DEVIR, personel=personel, hedef=yeni, kullanici=kullanici, ip=ip, aciklama=f"mesai kapandı ({neden})")
        return "DEVIR"
    if neden == "elle" and not bekle_onay:
        if oturum.karma:
            raise OturumHatasi(f"Açık karma kesiminiz var: {_mesgul_metni(u)}. Kesimi bitirin ya da iptal edin; ya da \"Adet bekliyor olarak bırak\" kutusunu işaretleyin.")
        raise OturumHatasi(f"Açık işiniz var: {_mesgul_metni(u)}. Adet/fire girin ya da \"Adet bekliyor olarak bırak\" kutusunu işaretleyin.")
    _adete_bagla(oturum, ayr, kullanici=kullanici, ip=ip)
    olay(oturum, T.ADET_BEKLIYOR, personel=personel, kullanici=kullanici, ip=ip, aciklama=f"mesai kapandı ({neden})")
    return "ADET"


def supurme(simdi=None, kuru=False) -> list:
    """Cron: üyeliği HÂLÂ açık ama bağlı olduğu mesai kaydı kapanmış üyelikleri kapatır (``mesai_kapaniyor``, neden 'supurme'; bitiş = o mesainin çıkışı). Kapatılanların
    [{uye, bitis}] listesi döner (``kuru=True`` yalnız listeler)."""
    simdi = simdi or timezone.now()
    sonuc = []
    qs = (IsOturumuUyesi.objects.filter(silindi=False, ayrilma_zamani__isnull=True, oturum__silindi=False, oturum__durum=IsOturumu.Durum.ACIK, mesai_kaydi__cikis_zamani__isnull=False)
          .select_related("personel", "mesai_kaydi", "oturum").order_by("mesai_kaydi__cikis_zamani", "pk"))
    for u in list(qs):
        bitis = u.mesai_kaydi.cikis_zamani
        if not kuru:
            if mesai_kapaniyor(u.personel, bitis, neden="supurme") is None:
                continue
        sonuc.append({"uye": u, "bitis": bitis})
    return sonuc


def mesai_kaydi_kapandi(kayit: MesaiKaydi, *, kullanici=None) -> None:
    """Ofis bir mesai kaydını elle kapattı / düzeltti / sildi: o kayda bağlı açık üyelik varsa aynı anda kapanır (sorumluysa devir / adet bekliyor)."""
    simdi = timezone.now()
    bitis = kayit.cikis_zamani or simdi
    for u in list(IsOturumuUyesi.objects.filter(mesai_kaydi=kayit, silindi=False, ayrilma_zamani__isnull=True, oturum__silindi=False, oturum__durum=IsOturumu.Durum.ACIK)
                  .select_related("personel").order_by("katilim_zamani")):
        mesai_kapaniyor(u.personel, bitis, neden="yonetici", kullanici=kullanici)


# --- yönetici işlemleri ---

def _not_zorunlu(aciklama) -> str:
    aciklama = (aciklama or "").strip()
    if not aciklama:
        raise OturumHatasi("Not zorunlu (denetim kaydı için nedeni yazın).")
    return aciklama[:280]


@transaction.atomic
def yonetici_sorumlu_degistir(oturum: IsOturumu, yeni: Personel, *, kullanici, ip=None, aciklama="") -> IsOturumu:
    """Yönetici sorumluyu değiştirir (otomatik devirden bağımsız, istediği an): açık oturumda ekipten (açık üye), "Adet bekliyor" oturumda oturumun geçmiş üyelerinden."""
    oturum = _kilitle(oturum)
    if oturum.durum not in (IsOturumu.Durum.ACIK, IsOturumu.Durum.ADET):
        raise OturumHatasi("Yalnız açık ya da adet bekleyen oturumun sorumlusu değiştirilebilir.")
    if yeni is None:
        raise OturumHatasi("Yeni sorumluyu seçin.")
    uyelikler = IsOturumuUyesi.objects.filter(oturum=oturum, personel=yeni, silindi=False)
    if oturum.durum == IsOturumu.Durum.ACIK:
        uyelikler = uyelikler.filter(ayrilma_zamani__isnull=True)
    if not uyelikler.exists():
        raise OturumHatasi(f"{yeni.ad_soyad} bu işin {'ekibinde değil' if oturum.durum == IsOturumu.Durum.ACIK else 'üyeleri arasında yok'}; yeni sorumlu ekipten seçilmeli.")
    eski = oturum.sorumlu_kisi
    if eski.pk == yeni.pk:
        raise OturumHatasi(f"{yeni.ad_soyad} zaten sorumlu.")
    oturum.sorumlu, oturum.updated_by = yeni, kullanici
    oturum.save(update_fields=["sorumlu", "updated_by", "updated_at"])
    olay(oturum, T.YONETICI_SORUMLU, personel=eski, hedef=yeni, kullanici=kullanici, ip=ip, aciklama=(aciklama or "").strip())
    return oturum


@transaction.atomic
def yonetici_kapat(oturum: IsOturumu, *, adet_bekliyor: bool, aciklama, kullanici, ip=None, bitis=None) -> IsOturumu:
    """Yönetici açık işi kapatır — NOT ZORUNLU: ``adet_bekliyor=True`` → "Adet bekliyor" (süre işçiliğe sayılır, adet sonra girilir); ``False`` → İPTAL (iş yapılmadı, süre sayılmaz).
    ``bitis`` boşsa şimdi (verilirse başlangıçtan sonra, şimdiden önce olmalı). Tüm ekibin üyeliği aynı anda kapanır."""
    aciklama = _not_zorunlu(aciklama)
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ACIK:
        raise OturumHatasi("Bu iş oturumu zaten kapatılmış.")
    simdi = timezone.now()
    if bitis is not None:
        if bitis <= oturum.baslangic_zamani:
            raise OturumHatasi("Bitiş saati başlangıçtan sonra olmalı.")
        if bitis > simdi:
            raise OturumHatasi("Bitiş saati ileri bir zaman olamaz.")
    else:
        bitis = max(simdi, oturum.baslangic_zamani)
    if adet_bekliyor:
        _adete_bagla(oturum, bitis, kullanici=kullanici, ip=ip)
        oturum.aciklama = f"Yönetici adet bekliyor olarak kapattı: {aciklama}"[:300]
        oturum.save(update_fields=["aciklama", "updated_at"])
        olay(oturum, T.YONETICI_ADET_BEKLIYOR, kullanici=kullanici, ip=ip, aciklama=aciklama)
    else:
        oturum.aciklama = f"Yönetici iptal etti: {aciklama}"[:300]
        _kapat(oturum, durum=IsOturumu.Durum.IPTAL, bitis=bitis, bitiren=None, ip=ip, kullanici=kullanici)
        oturum.save(update_fields=["aciklama", "updated_at"])
        olay(oturum, T.YONETICI_IPTAL, kullanici=kullanici, ip=ip, aciklama=aciklama)
    return oturum


@transaction.atomic
def yonetici_adet_bekleyen_iptal(oturum: IsOturumu, *, aciklama, kullanici, ip=None) -> IsOturumu:
    """"Adet bekliyor" oturumu yönetici iptal eder (adet hiç girilemiyorsa — ör. emir başka yoldan tamamlandı): süre artık sayılmaz. Not zorunlu."""
    aciklama = _not_zorunlu(aciklama)
    oturum = _kilitle(oturum)
    if oturum.durum != IsOturumu.Durum.ADET:
        raise OturumHatasi("Bu iş oturumu adet beklemiyor.")
    oturum.durum, oturum.updated_by = IsOturumu.Durum.IPTAL, kullanici
    oturum.aciklama = f"Yönetici iptal etti (adet bekliyordu): {aciklama}"[:300]
    oturum.save(update_fields=["durum", "aciklama", "updated_by", "updated_at"])
    olay(oturum, T.YONETICI_IPTAL, kullanici=kullanici, ip=ip, aciklama=aciklama)
    return oturum


# --- ofis kaydı engeli (kural 9) ---

def ofis_engeli_oturumlari(ie: IstasyonEmri) -> list:
    """Emirde ofis kaydını engelleyen oturumlar: AÇIK (ekip çalışıyor) ve "Adet bekliyor" (adet o oturuma girilecek)."""
    karma = Q(karma=True, istasyon_id=ie.istasyon_id, karma_profil_id__in=_profil_idleri(ie))        # aynı istasyonda bu emrin profilini kesen karma kesim de engeller
    return list(IsOturumu.objects.filter(Q(istasyon_emri=ie) | karma, silindi=False, durum__in=[IsOturumu.Durum.ACIK, IsOturumu.Durum.ADET])
                .select_related("baslatan", "sorumlu").prefetch_related(_uyeler_prefetch()).order_by("pk"))


def ofis_asma_gecerli(asma) -> bool:
    """``asma`` = (kullanıcı, not): yalnız SÜPER KULLANICI ve dolu not geçerlidir (diğer yönetici hesapları aşamaz)."""
    return bool(asma) and bool(getattr(asma[0], "is_superuser", False)) and bool((asma[1] or "").strip())


def ofis_engeli_kontrol(ie: IstasyonEmri, asma=None) -> bool:
    """Ofis üretim kaydı / ÜS "Üretildi" / toplu üretim giriş kapısı: emirde açık ya da adet bekleyen oturum varsa ``UretimHatasi`` ("Bu işte X'in ekibi çalışıyor").
    Süper kullanıcı notla aşabilir (olay kaydı yazılır, True döner); engel yoksa False."""
    oturumlar = ofis_engeli_oturumlari(ie)
    if not oturumlar:
        return False
    if ofis_asma_gecerli(asma):
        for o in oturumlar:
            olay(o, T.OFIS_ASILDI, kullanici=asma[0], aciklama=asma[1].strip())
        return True
    o = oturumlar[0]
    oz = ozet(o)
    if o.durum == IsOturumu.Durum.ACIK:
        raise uretim.UretimHatasi(f"Bu işte {oz['sorumlu'].ad_soyad}'in ekibi çalışıyor ({oz['ekip_metni']}); kayıt girilemez. İşi o ekip bitirmeli ya da yönetici İş Oturumları'ndan kapatmalı (süper kullanıcı notla aşabilir).")
    raise uretim.UretimHatasi(f"Bu işte {oz['sorumlu'].ad_soyad}'in oturumu adet bekliyor; kayıt girilemez. Adedi İş Oturumları ekranından \"Adet gir\" ile girin (süper kullanıcı notla aşabilir).")


def ofis_karma_engelleri(istasyon_id, profil_id) -> list:
    """Ofis KARMA KESİM kaydını engelleyen oturumlar (AÇIK ya da "Adet bekliyor"): aynı istasyonda bu profil için karma oturum, ya da bu profili tüketen bir emirde tekil oturum."""
    karma = Q(karma=True, karma_profil_id=profil_id)
    tekil = Q(karma=False, istasyon_emri__operasyon__girdiler__girdi_id=profil_id, istasyon_emri__operasyon__girdiler__silindi=False)
    return list(IsOturumu.objects.filter(karma | tekil, silindi=False, istasyon_id=istasyon_id, durum__in=[IsOturumu.Durum.ACIK, IsOturumu.Durum.ADET])
                .select_related("baslatan", "sorumlu", "karma_profil", "istasyon_emri").prefetch_related(_uyeler_prefetch()).distinct().order_by("pk"))


def ofis_karma_engel_mesaji(oturumlar) -> str:
    """Engelleyen oturumlardan ilkinin açıklayıcı mesajı ('' = engel yok)."""
    if not oturumlar:
        return ""
    o = oturumlar[0]
    oz = ozet(o)
    ad = oz["sorumlu"].ad_soyad
    if o.karma and o.durum == IsOturumu.Durum.ACIK:
        return (f"Bu istasyonda {ad}'in ekibi karma kesim yapıyor ({oz['ekip_metni']}); ofis karma kesim kaydı girilemez. Karma kesimi o ekip bitirmeli ya da yönetici "
                "İş Oturumları'ndan kapatmalı (süper kullanıcı notla aşabilir).")
    if o.karma:
        return (f"Bu istasyonda {ad}'in karma kesimi adet bekliyor; ofis karma kesim kaydı girilemez. Önce atölyeden adetler girilmeli ya da yönetici İş Oturumları'ndan "
                "kapatmalı (süper kullanıcı notla aşabilir).")
    return (f"Bu istasyonda bu profili kullanan bir işte ({o.emir_no}) {ad}'in ekibi çalışıyor; ofis karma kesim kaydı girilemez. İşi o ekip bitirmeli ya da yönetici "
            "İş Oturumları'ndan kapatmalı (süper kullanıcı notla aşabilir).")


def ofis_karma_engeli_kontrol(istasyon_id, profil_id, asma=None) -> bool:
    """Ofis karma kesim giriş kapısı: engel varsa ``KarmaKesimHatasi``; süper kullanıcı + not (``asma=(kullanıcı, not)``) aşarsa her oturuma OFIS_ASILDI olayı yazılır ve True döner
    (atölye oturumu AÇIK kalır). Engel yoksa False."""
    oturumlar = ofis_karma_engelleri(istasyon_id, profil_id)
    if not oturumlar:
        return False
    if ofis_asma_gecerli(asma):
        for o in oturumlar:
            olay(o, T.OFIS_ASILDI, kullanici=asma[0], aciklama="Ofis karma kesim kaydı: " + asma[1].strip())
        return True
    raise karma_kesim.KarmaKesimHatasi(ofis_karma_engel_mesaji(oturumlar))


def ofis_asma_sonrasi(ie: IstasyonEmri, asma) -> None:
    """Süper kullanıcı engeli aştı ve kayıt yazıldı: emir TAMAMLANDIYSA üzerindeki AÇIK oturumlar otomatik İptal (iş yapılmadı) olur (olay kaydı + not); adet bekleyenlere dokunulmaz."""
    ie.refresh_from_db()
    if ie.durum != IstasyonEmri.Durum.BITTI:
        return
    simdi = timezone.now()
    for o in IsOturumu.objects.select_for_update().filter(istasyon_emri=ie, silindi=False, durum=IsOturumu.Durum.ACIK):
        o.aciklama = f"Emir ofis kaydıyla tamamlandı (süper kullanıcı engeli aştı): {asma[1].strip()}"[:300]
        _kapat(o, durum=IsOturumu.Durum.IPTAL, bitis=max(simdi, o.baslangic_zamani), bitiren=None, ip=None, kullanici=asma[0])
        o.save(update_fields=["aciklama", "updated_at"])
        olay(o, T.OFIS_ASMA_IPTAL, kullanici=asma[0], aciklama=asma[1].strip())


# --- ofis listesi ---

def listele(*, baslangic=None, bitis=None, istasyon_id=None, personel_id=None, durum=None, ara=""):
    qs = (IsOturumu.objects.filter(silindi=False)
          .select_related("istasyon_emri__operasyon__cikti", "uretim_emri__kaynak_siparis__cari", "istasyon", "baslatan", "sorumlu", "bitiren", "kayit",
                          "karma_profil", "karma_kesim")
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
                       | Q(kayit__no__icontains=ara) | Q(karma_kesim__no__icontains=ara) | Q(karma_profil__kod__icontains=ara)
                       | Q(karma=True, karma_profil__ad__contains=buyuk_harf_tr(ara)))
    return qs.order_by("-baslangic_zamani", "-pk")
