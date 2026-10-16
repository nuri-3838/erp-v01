"""Fabrika ağı kısıtı + OTOMATİK mesai kapatma (kullanıcı kararları, 2026-10-16).

Kapsam: yalnız **operatör hesaplar** (``core.yetki.fabrika_kisitli_mi`` — personel kartına bağlı, yönetici olmayan, ekran yetkisi yalnız ``atolye`` olan hesap).
Yönetici ve ofis kullanıcıları (başka ekran yetkisi olanlar) ne kapıya ne otomatik kapanışa tabidir.

* Kapı (``core.fabrika_kapisi``): operatör, fabrika ağı (``MesaiIzinliAg``) dışından ``/atolye/*`` ve ``/mesai/*`` sayfalarına giremez (403 bilgi sayfası).
  Ağ listesi boşsa ya da ayar kapalıysa kapı/otomatik kapanış devre dışıdır (kimse kilitlenmez).
* Heartbeat: açık sayfa 2 dk'da bir ``/atolye/ping/`` çağırır; fabrika IP'sinden gelen her atölye/mesai isteği açık mesainin ``son_fabrika_gorulme``'sini
  günceller. Heartbeat'in tek işi budur — gündüz "heartbeat yok" kapanışı YOKTUR (telefon ekranı kilitlenince sayaç durur).
* Kapanış yalnız iki sebeple: ``dis_ip`` (fabrika DIŞINDAN ilk görülmeden ``dis_ip_tolerans_dk`` dk sonra hâlâ fabrikadan görülmediyse; bitiş = son fabrika
  görülme, yoksa giriş) ve ``gece`` (``gece_kapanis_saati``'nden sonra açık kalan her mesai; bitiş = son fabrika görülme, yoksa o günün gece saati).
* Açık iş oturumu: ekipte başkası varsa yalnız üyelik kapanır; tek kişiyse oturum "İptal (iş yapılmadı)" olur (süre kayıt olarak kalır).
* Otomatik kapanan kayıtta ``otomatik_kapandi`` + ``otomatik_sebep``; yönetici düzeltince (not zorunlu) ``otomatik_kapandi`` kalkar.
* Zaman sunucu saatidir; saat dilimi Türkiye (cron UTC olsa da komut TR saatiyle karar verir — bkz. management komutu ``mesai_otomatik_kapat``)."""
from __future__ import annotations

import datetime

from django.db import transaction
from django.utils import timezone

from core.models import IkAyari, MesaiKaydi, Personel
from core.services import is_oturumu, mesai_ag
from core.tarih import _TR

SEBEP_DIS_IP = "dis_ip"
SEBEP_GECE = "gece"
YAZMA_ARALIGI = datetime.timedelta(seconds=20)          # fabrika görülmesi en fazla 20 sn'de bir yazılır
MAKS_TOLERANS_DK = 60
HEARTBEAT_ARALIK = (30, 600)


class MesaiOtomatikHatasi(ValueError):
    """Ayar doğrulama hatası (Türkçe mesaj)."""


def ayar() -> IkAyari:
    return IkAyari.get()


def devrede_mi() -> bool:
    """Kapı + otomatik kapanış çalışır mı: ayar açık ve en az bir izinli fabrika ağı tanımlı."""
    return bool(ayar().fabrika_kisiti_acik) and mesai_ag.aktif_aglar().exists()


def ayar_kaydet(*, acik, tolerans_dk, heartbeat_sn, gece_saati, kullanici=None) -> IkAyari:
    try:
        tolerans = int(tolerans_dk)
        hb = int(heartbeat_sn)
    except (TypeError, ValueError):
        raise MesaiOtomatikHatasi("Tolerans ve heartbeat aralığı tam sayı olmalı.")
    if not (0 <= tolerans <= MAKS_TOLERANS_DK):
        raise MesaiOtomatikHatasi(f"Fabrika dışı tolerans 0 ile {MAKS_TOLERANS_DK} dakika arasında olmalı.")
    if not (HEARTBEAT_ARALIK[0] <= hb <= HEARTBEAT_ARALIK[1]):
        raise MesaiOtomatikHatasi(f"Heartbeat aralığı {HEARTBEAT_ARALIK[0]} ile {HEARTBEAT_ARALIK[1]} saniye arasında olmalı.")
    if not isinstance(gece_saati, datetime.time):
        raise MesaiOtomatikHatasi("Gece kapanış saati geçerli bir saat olmalı (ör. 23:59).")
    a = ayar()
    a.fabrika_kisiti_acik, a.dis_ip_tolerans_dk, a.heartbeat_sn = bool(acik), tolerans, hb
    a.gece_kapanis_saati, a.updated_by = gece_saati.replace(second=0, microsecond=0), kullanici
    a.save(update_fields=["fabrika_kisiti_acik", "dis_ip_tolerans_dk", "heartbeat_sn", "gece_kapanis_saati", "updated_by", "updated_at"])
    return a


def operator_mu(personel: Personel) -> bool:
    from core.yetki import fabrika_kisitli_mi
    return bool(personel.kullanici_id) and fabrika_kisitli_mi(personel.kullanici)


def acik_kayit(personel):
    return MesaiKaydi.objects.filter(personel=personel, silindi=False, cikis_zamani__isnull=True).first()


# --- istek anı: fabrika içi / dışı görülme ---

def fabrika_gorulde(personel: Personel, simdi=None) -> None:
    """Fabrika IP'sinden görüldü: açık mesainin son görülmesi güncellenir, fabrika dışı sayacı sıfırlanır."""
    simdi = simdi or timezone.now()
    k = acik_kayit(personel)
    if k is None:
        return
    if k.dis_ip_ilk_gorulme is None and k.son_fabrika_gorulme is not None and simdi - k.son_fabrika_gorulme < YAZMA_ARALIGI:
        return
    MesaiKaydi.objects.filter(pk=k.pk, cikis_zamani__isnull=True).update(son_fabrika_gorulme=simdi, dis_ip_ilk_gorulme=None)


def dis_ip_gorulde(personel: Personel, simdi=None) -> bool:
    """Fabrika DIŞINDAN görüldü: ilk görülme işaretlenir; tolerans dolmuşsa mesai kapanır. Mesai bu istekle kapandıysa True."""
    simdi = simdi or timezone.now()
    with transaction.atomic():
        k = (MesaiKaydi.objects.select_for_update().filter(personel=personel, silindi=False, cikis_zamani__isnull=True).first())
        if k is None:
            return False
        if k.dis_ip_ilk_gorulme is None:
            k.dis_ip_ilk_gorulme = simdi
            MesaiKaydi.objects.filter(pk=k.pk).update(dis_ip_ilk_gorulme=simdi)
        if simdi - k.dis_ip_ilk_gorulme >= datetime.timedelta(minutes=ayar().dis_ip_tolerans_dk):
            return kapat(k, sebep=SEBEP_DIS_IP, simdi=simdi) is not None
    return False


# --- kapatma ---

def _gece_zamani(is_tarihi, saat: datetime.time):
    return datetime.datetime.combine(is_tarihi, saat, tzinfo=_TR)


def gece_vakti_mi(k: MesaiKaydi, simdi, saat: datetime.time) -> bool:
    """Kaydın gece kapanış zamanı geldi mi: iş günü bugünden önceyse hep; bugünse gece saatinden sonra."""
    return simdi >= _gece_zamani(k.is_tarihi, saat)


def bitis_hesapla(k: MesaiKaydi, sebep: str, simdi, gece_saati: datetime.time):
    """Planlanan çıkış: dis_ip → son fabrika görülme (yoksa giriş); gece → son fabrika görülme (yoksa o günün gece saati). Girişten önce olamaz, gelecekte olamaz."""
    son = k.son_fabrika_gorulme if (k.son_fabrika_gorulme and k.son_fabrika_gorulme >= k.giris_zamani) else None
    if sebep == SEBEP_DIS_IP:
        bitis = son or k.giris_zamani
    else:
        bitis = son or _gece_zamani(k.is_tarihi, gece_saati)
    return min(max(bitis, k.giris_zamani), simdi)


@transaction.atomic
def kapat(kayit: MesaiKaydi, *, sebep: str, simdi=None):
    """Açık mesaiyi otomatik kapatır (iş oturumu kuralı: bkz. modül başı); zaten kapalıysa None."""
    simdi = simdi or timezone.now()
    p = Personel.objects.select_for_update().filter(pk=kayit.personel_id).first()
    k = MesaiKaydi.objects.select_for_update().filter(pk=kayit.pk, silindi=False, cikis_zamani__isnull=True).first()
    if p is None or k is None:
        return None
    bitis = bitis_hesapla(k, sebep, simdi, ayar().gece_kapanis_saati)
    is_oturumu.mesai_otomatik_kapaniyor(p, bitis)
    k.cikis_zamani, k.cikis_ip = bitis, None
    k.otomatik_kapandi, k.otomatik_sebep, k.dis_ip_ilk_gorulme = True, sebep, None
    k.save(update_fields=["cikis_zamani", "cikis_ip", "otomatik_kapandi", "otomatik_sebep", "dis_ip_ilk_gorulme", "updated_at"])
    return k


def toplu_kapat(simdi=None, kuru=False) -> list:
    """Cron girişi: kapanması gereken operatör mesailerini kapatır (``kuru=True`` yalnız listeler). [{kayit, sebep, bitis}] döner."""
    if not devrede_mi():
        return []
    simdi = simdi or timezone.now()
    a = ayar()
    tolerans = datetime.timedelta(minutes=a.dis_ip_tolerans_dk)
    sonuc = []
    adaylar = (MesaiKaydi.objects.filter(silindi=False, cikis_zamani__isnull=True, personel__silindi=False)
               .select_related("personel__kullanici").order_by("giris_zamani"))
    for k in adaylar:
        if not operator_mu(k.personel):
            continue
        sebep = None
        if k.dis_ip_ilk_gorulme and simdi - k.dis_ip_ilk_gorulme >= tolerans:
            sebep = SEBEP_DIS_IP
        elif gece_vakti_mi(k, simdi, a.gece_kapanis_saati):
            sebep = SEBEP_GECE
        if sebep is None:
            continue
        bitis = bitis_hesapla(k, sebep, simdi, a.gece_kapanis_saati)
        if not kuru:
            if kapat(k, sebep=sebep, simdi=simdi) is None:
                continue
        sonuc.append({"kayit": k, "sebep": sebep, "bitis": bitis})
    return sonuc
