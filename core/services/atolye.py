"""ATÖLYE operatör ekranı (/atolye/) — işçinin kendi telefonundan: mesai başlat → istasyon seç → emirde "Başla" (ekip seç) → "Bitir" (adet) /
"İptal" → mesai bitir. Üretim kaydı yalnız Bitir'de açılır (süresiz "Üretildi" atölyeden KALDIRILDI, 2026-10-12; ofis ÜS detayında durur).

Kurallar (kullanıcı kararları, 2026-10-11/12):
* Aynı personel hesabı (``Personel.kullanici``) ve aynı fabrika ağı kısıtı (``mesai_ag.ip_izinli_mi``) — mesai başlatmada da üretim girişinde de.
* Mesai açık değilse iş başlatılamaz/bitirilemez; miktar yalnız tam sayı; kayıt başlatan personele yazılır (``OperasyonKaydi.personel`` +
  ``mesai_kaydi``), ekip/süre ``IsOturumu``'nda — işçilik/GÜG dağıtımının kaynağı (bkz. core.services.is_oturumu).
* İstasyon seçimi serbest (fason istasyonu listede yok); kalanı aşan miktar ofisteki kuralla: LAZER MARKALAMA (dönüştür) ve PAKETLEME'de
  yasak (``uretim.fazla_uretim_yasak``), diğerlerinde uyarıyla serbest.
* Geri alma: yalnız bugünkü, KENDİ, o emrin SON onaylı kaydı (süre sınırı yok) — ``uretim.operasyon_kaydi_sil(onayli_geri_al=True)``; oturum kalır.
* Yetki: ekran kodu ``atolye`` (AYARLAR › Kullanıcı Yetkileri ya da personel kartındaki "Atölye operatörü" düğmesi)."""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.models import EkranYetki, IsIstasyonu, IstasyonEmri, OperasyonKaydi, Personel, UretimEmri
from core.services import is_oturumu
from core.services import mesai as mesai_servis
from core.services import mesai_ag, uretim
from core.services import varyant as varyant_servis
from core.tarih import _TR, tr_bugun

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
    müşteri, varyant metni (standart dışı), tam sayı kalan, bugünkü kendi son kaydı, emrin açık iş oturumları (``oturumlar``: özet + ``benim``)."""
    qs = (IstasyonEmri.objects.filter(silindi=False, istasyon=istasyon, uretim_emri__silindi=False, uretim_emri__durum=UretimEmri.Durum.ACIK,
                                      durum__in=[IstasyonEmri.Durum.BEKLIYOR, IstasyonEmri.Durum.BASLADI])
          .select_related("uretim_emri__kaynak_siparis__cari", "operasyon__cikti", "istasyon", "cikti_varyant")
          .order_by("uretim_emri__yil", "uretim_emri__sira", "-seviye", "pk"))
    p = personel_coz(user) if user is not None else None
    acik = {}
    for o in is_oturumu.acik_oturumlar():
        acik.setdefault(o.istasyon_emri_id, []).append(o)
    sonuc = []
    for ie in qs:
        s = uretim.istasyon_emri_satiri(ie, ie.uretim_emri)
        sip = ie.uretim_emri.kaynak_siparis
        s["musteri"] = (sip.cari.kisa_ad or sip.cari.unvan) if (sip is not None and sip.cari_id) else "Stok için üretim"
        s["varyant_metni"] = varyant_servis.standart_disi_metin(ie.cikti_varyant_id, "tr") if ie.cikti_varyant_id else ""
        s["kalan_tam"] = _tam_metin(s["acik"])
        s["son_kaydim"] = son_kaydim(user, ie) if user is not None else None
        s["oturumlar"] = [oturum_satiri(o, p) for o in acik.get(ie.pk, [])]
        sonuc.append(s)
    return sonuc


def oturum_satiri(oturum, personel=None) -> dict:
    """Ekran satırı: ``is_oturumu.ozet`` + ``benim`` (kişi aktif üye) + dünkü oturum için önerilen bitiş (datetime-local değeri, TR)."""
    s = is_oturumu.ozet(oturum)
    s["benim"] = personel is not None and any(u.personel_id == personel.pk and u.ayrilma_zamani is None for u in s["uyeler"])
    s["bitis_varsayilan"] = ""
    if s["dunku"] and personel is not None:
        vb = is_oturumu.varsayilan_bitis(oturum, personel)
        s["bitis_varsayilan"] = vb.astimezone(_TR).strftime("%Y-%m-%dT%H:%M") if vb else ""
    return s


def acik_islerim(user) -> list:
    """Kişinin açık iş oturumları (dünden kalanlar dahil) — ekranın en üstünde; yönetici izlemede boş."""
    p = personel_coz(user)
    if p is None:
        return []
    return [oturum_satiri(o, p) for o in is_oturumu.kisinin_acik_oturumlari(p)]


def ekip_adaylari(user) -> list:
    p = personel_coz(user)
    return is_oturumu.ekip_adaylari(p) if p is not None else []


def _tam_metin(d: Decimal) -> str:
    d = Decimal(d)
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def _miktar_coz(ham) -> Decimal:
    s = (ham or "").strip().replace(".", "").replace(" ", "")
    if not s.isdigit() or int(s) <= 0:
        raise AtolyeHatasi("Miktar tam sayı olmalı (ör. 12).")
    return Decimal(int(s))


def _bitis_coz(ham):
    """datetime-local ("YYYY-MM-DDTHH:MM", TR saati) → aware datetime; boş → None; bozuk → hata."""
    ham = (ham or "").strip()
    if not ham:
        return None
    try:
        return datetime.datetime.fromisoformat(ham).replace(tzinfo=_TR)
    except ValueError:
        raise AtolyeHatasi("Bitiş saati anlaşılamadı (örn. 17:30).")


def _ag_kontrol(ip, ne):
    if not mesai_ag.ip_izinli_mi(ip):
        raise AtolyeHatasi(f"{ne} yalnızca fabrika Wi-Fi'ına bağlıyken yapılabilir.")


def oturum_baslat(user, ie: IstasyonEmri, ekip_pks, ip):
    """Operatör "Başla": mesai açık + fabrika ağı; ekip = seçilen personel pk'ları (``is_oturumu.baslat`` kuralları)."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "İş başlatma")
    pks = [int(x) for x in (ekip_pks or []) if str(x).isdigit()]
    ekip = list(Personel.objects.filter(pk__in=pks, silindi=False)) if pks else []
    if len(ekip) != len(set(pks)):
        raise AtolyeHatasi("Ekipteki bir personel bulunamadı.")
    try:
        return is_oturumu.baslat(p, ie, ekip, ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


def oturum_bitir(user, oturum, miktar_ham, bitis_ham, ip):
    """Operatör "Bitir": tam sayı adet; (kayıt, uyarı, miktar) döner; üretim hatasında oturum açık kalır (mesaj aynen)."""
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "Üretim girişi")
    miktar = _miktar_coz(miktar_ham)
    try:
        kayit, uyari = is_oturumu.bitir(p, oturum, miktar, bitis=_bitis_coz(bitis_ham), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))
    return kayit, uyari, miktar


def oturum_iptal(user, oturum, bitis_ham, ip):
    p, _m = _acik_mesai(user)
    _ag_kontrol(ip, "İptal")
    try:
        return is_oturumu.iptal(p, oturum, bitis=_bitis_coz(bitis_ham), ip=ip, kullanici=user)
    except is_oturumu.OturumHatasi as e:
        raise AtolyeHatasi(str(e))


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
