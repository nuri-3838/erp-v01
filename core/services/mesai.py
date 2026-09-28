"""İNSAN KAYNAKLARI > Mesai Kayıtları — personelin kendi telefonundan (fabrika ağındayken)
mesai başlatıp bitirmesi + yöneticinin düzeltme/ekleme/silmesi.

YALNIZ KAYIT: saatten ücret/fazla mesai HESABI YOK. Zaman HER ZAMAN sunucu saatidir
(istemciden asla alınmaz — bkz. baslat/bitir, ``ip`` dışında hiçbir istemci verisi
kullanılmaz). Personel kendi başlattığında, o günün PersonelDevam kaydı YOKSA otomatik
"Geldi" yapılır (yönetici zaten girmişse dokunulmaz — bkz. core.services.personel_devam).
Yönetici düzeltmeleri (manuel_ekle/duzelt) AYRI yoldan gider: IP kontrolü YAPILMAZ, yoklamaya
dokunmaz — düzeltme_notu ZORUNLUDUR.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from core.models import MesaiKaydi, Personel, PersonelDevam, PersonelIzin
from core.services import mesai_ag
from core.services.personel import aktif_mi
from core.tarih import tr_bugun

_TR = ZoneInfo("Europe/Istanbul")
MAKS_NOT = 300


class MesaiHatasi(ValueError):
    """Mesai kural ihlali (Türkçe mesaj)."""


def acik_kayit(personel):
    return MesaiKaydi.objects.filter(
        personel=personel, silindi=False, cikis_zamani__isnull=True).first()


def son_kayitlar(personel, *, gun=7, bugun=None):
    bugun = bugun or tr_bugun()
    baslangic = bugun - timezone.timedelta(days=gun - 1)
    return (MesaiKaydi.objects.filter(
                personel=personel, silindi=False, is_tarihi__gte=baslangic)
            .order_by("-giris_zamani"))


def _kilitle(personel_id):
    """Aynı kişiye eşzamanlı iki başlat/bitir isteği çakışmayı atlatmasın diye satır kilidi."""
    p = Personel.objects.select_for_update().filter(pk=personel_id).first()
    if p is None or p.silindi:
        raise MesaiHatasi("Personel bulunamadı.")
    return p


@transaction.atomic
def baslat(personel: Personel, *, ip, kullanici=None) -> MesaiKaydi:
    """Personelin KENDİSİ mesai başlatır — fabrika ağı zorunlu (bkz. core.services.mesai_ag)."""
    p = _kilitle(personel.pk)
    if not aktif_mi(p):
        raise MesaiHatasi("Bu personel artık çalışmıyor; mesai başlatılamaz.")
    if not mesai_ag.ip_izinli_mi(ip):
        raise MesaiHatasi("Mesai yalnızca fabrika Wi-Fi'ına bağlıyken başlatılabilir.")
    if acik_kayit(p) is not None:
        raise MesaiHatasi("Zaten açık bir mesai kaydınız var.")
    simdi = timezone.now()
    is_tarihi = simdi.astimezone(_TR).date()
    kayit = MesaiKaydi.objects.create(
        personel=p, is_tarihi=is_tarihi, giris_zamani=simdi, giris_ip=ip or None,
        kaynak=MesaiKaydi.Kaynak.PERSONEL, created_by=kullanici, updated_by=kullanici)
    if not PersonelDevam.objects.filter(personel=p, tarih=is_tarihi, silindi=False).exists():
        PersonelDevam.objects.create(
            personel=p, tarih=is_tarihi, durum="GELDI", notlar="Mesai girişi",
            created_by=kullanici, updated_by=kullanici)
    return kayit


@transaction.atomic
def bitir(personel: Personel, *, ip, kullanici=None) -> MesaiKaydi:
    p = _kilitle(personel.pk)
    kayit = acik_kayit(p)
    if kayit is None:
        raise MesaiHatasi("Açık bir mesai kaydınız yok.")
    kayit.cikis_zamani = timezone.now()
    kayit.cikis_ip = ip or None
    kayit.updated_by = kullanici
    kayit.save(update_fields=["cikis_zamani", "cikis_ip", "updated_by", "updated_at"])
    return kayit


# === Yönetici: düzeltme / manuel ekleme / silme (IP kontrolü yok, yoklamaya dokunmaz) ===

def _dogrula_zaman(*, giris_zamani, cikis_zamani, duzeltme_notu) -> dict:
    if giris_zamani is None:
        raise MesaiHatasi("Giriş zamanı zorunlu.")
    duzeltme_notu = (duzeltme_notu or "").strip()
    if not duzeltme_notu:
        raise MesaiHatasi("Düzeltme notu zorunlu.")
    if len(duzeltme_notu) > MAKS_NOT:
        raise MesaiHatasi(f"Düzeltme notu en fazla {MAKS_NOT} karakter olabilir.")
    if cikis_zamani is not None and cikis_zamani < giris_zamani:
        raise MesaiHatasi("Çıkış zamanı, giriş zamanından önce olamaz.")
    return {"giris_zamani": giris_zamani, "cikis_zamani": cikis_zamani,
            "duzeltme_notu": duzeltme_notu}


@transaction.atomic
def manuel_ekle(personel: Personel, *, giris_zamani, cikis_zamani=None, duzeltme_notu,
                kullanici=None) -> MesaiKaydi:
    p = _kilitle(personel.pk)
    veri = _dogrula_zaman(
        giris_zamani=giris_zamani, cikis_zamani=cikis_zamani, duzeltme_notu=duzeltme_notu)
    if veri["cikis_zamani"] is None and acik_kayit(p) is not None:
        raise MesaiHatasi("Bu personelin zaten açık bir mesai kaydı var.")
    is_tarihi = veri["giris_zamani"].astimezone(_TR).date()
    return MesaiKaydi.objects.create(
        personel=p, is_tarihi=is_tarihi, kaynak=MesaiKaydi.Kaynak.YONETICI,
        created_by=kullanici, updated_by=kullanici, **veri)


@transaction.atomic
def duzelt(kayit: MesaiKaydi, *, giris_zamani, cikis_zamani=None, duzeltme_notu,
          kullanici=None) -> MesaiKaydi:
    if kayit.silindi:
        raise MesaiHatasi("Silinmiş kayıt düzenlenemez.")
    p = _kilitle(kayit.personel_id)
    veri = _dogrula_zaman(
        giris_zamani=giris_zamani, cikis_zamani=cikis_zamani, duzeltme_notu=duzeltme_notu)
    if veri["cikis_zamani"] is None:
        acik = acik_kayit(p)
        if acik is not None and acik.pk != kayit.pk:
            raise MesaiHatasi("Bu personelin zaten başka bir açık mesai kaydı var.")
    kayit.is_tarihi = veri["giris_zamani"].astimezone(_TR).date()
    kayit.giris_zamani = veri["giris_zamani"]
    kayit.cikis_zamani = veri["cikis_zamani"]
    kayit.duzeltme_notu = veri["duzeltme_notu"]
    kayit.kaynak = MesaiKaydi.Kaynak.YONETICI
    kayit.updated_by = kullanici
    kayit.save(update_fields=["is_tarihi", "giris_zamani", "cikis_zamani", "duzeltme_notu",
                              "kaynak", "updated_by", "updated_at"])
    return kayit


def sil(kayit: MesaiKaydi, kullanici=None) -> MesaiKaydi:
    if kayit.silindi:
        return kayit
    kayit.silindi = True
    kayit.silindi_at = timezone.now()
    kayit.updated_by = kullanici
    kayit.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return kayit


# === Yönetici: liste + uyarılar ===

def kayitlari_listele(*, baslangic=None, bitis=None, personel_id=None):
    qs = (MesaiKaydi.objects.filter(silindi=False, personel__silindi=False)
          .select_related("personel"))
    if personel_id:
        qs = qs.filter(personel_id=personel_id)
    if baslangic:
        qs = qs.filter(is_tarihi__gte=baslangic)
    if bitis:
        qs = qs.filter(is_tarihi__lte=bitis)
    return qs


def cikisi_eksik_kayitlar(*, bugun=None):
    """Geçmiş bir güne ait, hâlâ AÇIK (çıkışsız) kayıtlar — muhtemelen unutulmuş."""
    bugun = bugun or tr_bugun()
    return (MesaiKaydi.objects.filter(
                silindi=False, personel__silindi=False, cikis_zamani__isnull=True,
                is_tarihi__lt=bugun)
            .select_related("personel").order_by("is_tarihi"))


def yoklama_uyusmazliklari(*, baslangic=None, bitis=None):
    """Mesai kaydı olan (personel, gün) için PersonelDevam durumu GELMEDI ise (yönetici
    ayrıca "gelmedi" girmiş, mesai kaydıyla çelişiyor — kayıt korunur, yalnız bildirilir)."""
    kayitlar = list(kayitlari_listele(baslangic=baslangic, bitis=bitis))
    if not kayitlar:
        return []
    kisiler = {k.personel_id for k in kayitlar}
    devam_qs = PersonelDevam.objects.filter(silindi=False, personel_id__in=kisiler)
    if baslangic:
        devam_qs = devam_qs.filter(tarih__gte=baslangic)
    if bitis:
        devam_qs = devam_qs.filter(tarih__lte=bitis)
    devamlar = {(pid, t): d for pid, t, d in
               devam_qs.values_list("personel_id", "tarih", "durum")}
    return [k for k in kayitlar if devamlar.get((k.personel_id, k.is_tarihi)) == "GELMEDI"]


def izinli_gunde_giris_uyarilari(*, baslangic=None, bitis=None):
    """Mesai kaydının iş tarihi, personelin bir izin aralığına denk gelen kayıtlar."""
    kayitlar = list(kayitlari_listele(baslangic=baslangic, bitis=bitis))
    if not kayitlar:
        return []
    kisiler = {k.personel_id for k in kayitlar}
    izinler = {}
    for pid, b, e in PersonelIzin.objects.filter(
            silindi=False, personel_id__in=kisiler).values_list("personel_id", "baslangic", "bitis"):
        izinler.setdefault(pid, []).append((b, e))
    return [k for k in kayitlar
           if any(b <= k.is_tarihi <= e for b, e in izinler.get(k.personel_id, ()))]
