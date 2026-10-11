"""ATÖLYE operatör ekranı (/atolye/) — işçinin kendi telefonundan: mesai başlat → istasyon seç → açık emirlerde "Üretildi" → mesai bitir.

Kurallar (kullanıcı kararları, 2026-10-11):
* Aynı personel hesabı (``Personel.kullanici``) ve aynı fabrika ağı kısıtı (``mesai_ag.ip_izinli_mi``) — mesai başlatmada da üretim girişinde de.
* Mesai açık değilse üretim girilemez; üretim tarihi hep bugün (TR); miktar yalnız tam sayı; kayıt hep giriş yapan personele yazılır
  (``OperasyonKaydi.personel`` + ``mesai_kaydi``) — işçilik/GÜG dağıtımının kaynağı.
* İstasyon seçimi serbest (fason istasyonu listede yok); kalanı aşan miktar ofisteki kuralla: LAZER MARKALAMA (dönüştür) ve PAKETLEME'de
  yasak (``uretim.fazla_uretim_yasak``), diğerlerinde uyarıyla serbest.
* Geri alma: yalnız bugünkü, KENDİ, o emrin SON onaylı kaydı (süre sınırı yok) — ``uretim.operasyon_kaydi_sil(onayli_geri_al=True)``.
* Yetki: ekran kodu ``atolye`` (AYARLAR › Kullanıcı Yetkileri ya da personel kartındaki "Atölye operatörü" düğmesi)."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import EkranYetki, IsIstasyonu, IstasyonEmri, OperasyonKaydi, Personel, UretimEmri
from core.services import mesai as mesai_servis
from core.services import mesai_ag, uretim
from core.services import varyant as varyant_servis
from core.tarih import tr_bugun

EKRAN_KODU = "atolye"


class AtolyeHatasi(ValueError):
    """Atölye kural ihlali (büyük, anlaşılır Türkçe mesaj)."""


def personel_coz(user) -> Personel | None:
    if not getattr(user, "is_authenticated", False):
        return None
    return Personel.objects.filter(kullanici=user, silindi=False).first()


def _personel(user) -> Personel:
    p = personel_coz(user)
    if p is None:
        raise AtolyeHatasi("Bu hesaba bağlı bir personel kartı yok.")
    return p


def istasyonlar():
    """Seçilebilir istasyonlar: fason olmayanlar, akış sırasıyla (11 LAZER MARKALAMA dahil)."""
    return list(IsIstasyonu.objects.filter(silindi=False, fason=False).order_by("sira", "kod"))


def durum(user) -> dict:
    """{personel, acik_kayit, calisiyor, izleme} — ekranın üst durumu. Personel kartı olmayan YÖNETİCİ ekranı yalnız izler (istasyonlar ve
    emirler görünür; mesai/üretim/geri alma personel hesabı ister); personel kartı olmayan diğer kullanıcı için hata."""
    from core.services.personel import aktif_mi
    from core.yetki import yonetici_mi
    p = personel_coz(user)
    if p is None:
        if yonetici_mi(user):
            return {"personel": None, "acik_kayit": None, "calisiyor": False, "izleme": True}
        raise AtolyeHatasi("Bu hesaba bağlı bir personel kartı yok.")
    return {"personel": p, "acik_kayit": mesai_servis.acik_kayit(p), "calisiyor": aktif_mi(p), "izleme": False}


def mesai_baslat(user, ip) -> None:
    p = _personel(user)
    try:
        mesai_servis.baslat(p, ip=ip, kullanici=user)
    except mesai_servis.MesaiHatasi as e:
        raise AtolyeHatasi(str(e))


def mesai_bitir(user, ip) -> None:
    p = _personel(user)
    try:
        mesai_servis.bitir(p, ip=ip, kullanici=user)
    except mesai_servis.MesaiHatasi as e:
        raise AtolyeHatasi(str(e))


def _acik_mesai(user):
    p = _personel(user)
    m = mesai_servis.acik_kayit(p)
    if m is None:
        raise AtolyeHatasi("Önce mesainizi başlatın; mesai açık değilken üretim girilemez.")
    return p, m


def son_kaydim(user, ie: IstasyonEmri):
    """Emrin SON onaylı kaydı bu kullanıcıya ait ve bugün tarihliyse o kayıt (geri alınabilir), değilse None."""
    son = ie.kayitlar.filter(silindi=False, durum=OperasyonKaydi.Durum.ONAYLI).order_by("-pk").first()
    if son is not None and son.created_by_id == getattr(user, "pk", None) and son.tarih == tr_bugun():
        return son
    return None


def acik_emirler(istasyon, user=None) -> list:
    """İstasyonun açık emirleri (açık ÜS'ler, BEKLİYOR/BAŞLADI; ÜS sırası, derin seviye önce): ``uretim.istasyon_emri_satiri`` satırı +
    müşteri, varyant metni (standart dışı), tam sayı kalan, bugünkü kendi son kaydı."""
    qs = (IstasyonEmri.objects.filter(silindi=False, istasyon=istasyon, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
          .select_related("uretim_emri__kaynak_siparis__cari", "operasyon__cikti", "istasyon", "cikti_varyant")
          .order_by("uretim_emri__yil", "uretim_emri__sira", "-seviye", "pk"))
    sonuc = []
    for ie in qs:
        s = uretim.istasyon_emri_satiri(ie, ie.uretim_emri)
        sip = ie.uretim_emri.kaynak_siparis
        s["musteri"] = (sip.cari.kisa_ad or sip.cari.unvan) if (sip is not None and sip.cari_id) else "Stok için üretim"
        s["varyant_metni"] = varyant_servis.standart_disi_metin(ie.cikti_varyant_id, "tr") if ie.cikti_varyant_id else ""
        s["kalan_tam"] = _tam_metin(s["acik"])
        s["son_kaydim"] = son_kaydim(user, ie) if user is not None else None
        sonuc.append(s)
    return sonuc


def _tam_metin(d: Decimal) -> str:
    d = Decimal(d)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def _miktar_coz(ham) -> Decimal:
    s = (ham or "").strip().replace(".", "").replace(" ", "")
    if not s.isdigit() or int(s) <= 0:
        raise AtolyeHatasi("Miktar tam sayı olmalı (ör. 12).")
    return Decimal(int(s))


@transaction.atomic
def uret(user, ie: IstasyonEmri, miktar_ham, ip):
    """Operatör "Üretildi": mesai açık + fabrika ağı + tam sayı; ``uretim.istasyon_emri_uret`` (tarih = bugün, tarih bazlı stok kontrolü,
    lazer/paketlemede fazla üretim reddi) kaydı personel + mesai kaydıyla açar ve onaylar. (kayıt, uyarı, miktar) döner."""
    p, m = _acik_mesai(user)
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi("Üretim girişi yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")
    if ie.silindi or ie.istasyon.fason:
        raise AtolyeHatasi("Bu emre atölyeden üretim girilemez (fason istasyonu).")
    miktar = _miktar_coz(miktar_ham)
    try:
        kayit, uyari = uretim.istasyon_emri_uret(ie, miktar=miktar, tarih=tr_bugun(), kullanici=user, personel=p, mesai_kaydi=m)
    except uretim.UretimHatasi as e:
        raise AtolyeHatasi(str(e))
    return kayit, uyari, miktar


@transaction.atomic
def geri_al(user, kayit: OperasyonKaydi, ip) -> OperasyonKaydi:
    """Bugünkü KENDİ SON kaydını geri alır (stok hareketleri + maliyet fişi geri sarılır, ``geri_alan`` izi); başka bir kayıt reddedilir."""
    _acik_mesai(user)
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi("Geri alma yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")
    ie = kayit.istasyon_emri
    if ie is None or son_kaydim(user, ie) is None or son_kaydim(user, ie).pk != kayit.pk:
        raise AtolyeHatasi("Yalnız bugünkü kendi SON kaydınızı geri alabilirsiniz.")
    if kayit.uretim_emri is None or kayit.uretim_emri.durum != UretimEmri.Durum.ACIK:
        raise AtolyeHatasi("Üretim siparişi kapalı; kayıt atölyeden geri alınamaz.")
    try:
        return uretim.operasyon_kaydi_sil(kayit, kullanici=user, onayli_geri_al=True)
    except uretim.UretimHatasi as e:
        raise AtolyeHatasi(str(e))


# --- yetki: "Atölye operatörü" (personel kartından tek düğme) ---

def operator_mu(personel: Personel) -> bool:
    return bool(personel.kullanici_id and EkranYetki.objects.filter(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU, silindi=False).exists())


@transaction.atomic
def operator_ayarla(personel: Personel, acik: bool, yapan=None) -> bool:
    """Personelin mesai hesabına ``atolye`` ekran yetkisini verir/kaldırır (EkranYetki satırı; soft-delete ile geri alınır)."""
    if not personel.kullanici_id:
        raise AtolyeHatasi("Önce personele mesai hesabı oluşturun.")
    satir = EkranYetki.objects.filter(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU).order_by("-pk").first()
    if acik:
        if satir is None:
            EkranYetki.objects.create(kullanici_id=personel.kullanici_id, ekran_kod=EKRAN_KODU, created_by=yapan, updated_by=yapan)
        elif satir.silindi:
            satir.silindi, satir.silindi_at, satir.updated_by = False, None, yapan
            satir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    elif satir is not None and not satir.silindi:
        satir.silindi, satir.silindi_at, satir.updated_by = True, timezone.now(), yapan
        satir.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return acik
