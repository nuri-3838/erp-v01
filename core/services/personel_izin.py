"""İNSAN KAYNAKLARI > İzin Takibi servis katmanı.

Yalnız KAYIT: ücret/bordro hesabı yok. Yıllık izin hak edişi İş Kanunu m.53'e göre kıdemden
HESAPLANIR (saklanmaz): her TAMAMLANAN hizmet yılı için 1-5. yıl 14, 6-14. yıl 20, 15+ yıl 26
gün; işçi 18 yaş ve altı ya da 50 yaş ve üzeriyse o yıl için en az 20 gün. Bakiye =
hak edilen − (sisteme geçmeden önce kullanılan + sistemde girilmiş YILLIK izin günleri);
negatif olabilir (engellenmez, kırmızı gösterilir). Rapor/mazeret/ücretsiz/diğer izinler
yalnız kayıttır, bakiyeyi düşürmez.

Gün sayısı Pazar günleri VE resmî tatiller (core.services.resmi_tatil) hariç takvim günü
olarak ÖNERİLİR (4857 s. İş Kanunu md.56); kullanıcı gün sayısını yarım gün adımlarıyla elle
düzeltebilir. Bu yalnız ÖNERİYİ etkiler — mevcut PersonelIzin kayıtlarının `gun` değeri
sonradan tatil takvimi değişse bile YENİDEN hesaplanmaz.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Personel, PersonelIzin
from core.services.resmi_tatil import tatil_gunleri_seti
from core.tarih import tamamlanan_yil, tr_bugun, yil_donumu

SIFIR = Decimal("0.0")


class PersonelIzinHatasi(ValueError):
    """İzin kural ihlali (Türkçe mesaj)."""


# === Yıllık izin hak edişi (saf fonksiyonlar, veritabanı yok) ===

def yillik_hak_gunu(hizmet_yili: int, yas) -> int:
    """hizmet_yili: tamamlanan hizmet yılı sırası (1, 2, ...). yas: o yıl dönümündeki tamamlanmış
    yaş (bilinmiyorsa None → yaş kuralı uygulanmaz)."""
    if hizmet_yili < 1:
        return 0
    temel = 14 if hizmet_yili <= 5 else (20 if hizmet_yili <= 14 else 26)
    if yas is not None and (yas <= 18 or yas >= 50):
        temel = max(temel, 20)
    return temel


def hak_edilen_gun(giris, dogum, tarih) -> int:
    """tarih (dahil) itibarıyla tamamlanan her hizmet yılı için toplam yıllık izin hakkı."""
    if giris is None or tarih < giris:
        return 0
    toplam, n = 0, 1
    while (d := yil_donumu(giris, n)) <= tarih:
        yas = tamamlanan_yil(dogum, d) if dogum else None
        toplam += yillik_hak_gunu(n, yas)
        n += 1
    return toplam


@dataclass(frozen=True)
class IzinBakiye:
    hak_edilen: Decimal
    onceki: Decimal
    kullanilan: Decimal          # sistemde girilmiş YILLIK izinler (planlananlar dahil; saatlik izinlerin gün karşılığı dahil)
    planlanan: Decimal           # kullanilan'ın başlangıcı bugünden SONRA olan kısmı
    kalan: Decimal
    sonraki_hak_tarihi: date | None
    gunluk_saat: Decimal = Decimal("7.5")

    @property
    def eksi_mi(self) -> bool:
        return self.kalan < 0

    @property
    def kalan_metni(self) -> str:
        """"12 gün 3 sa" — kalan gün + saat (günlük çalışma süresine göre)."""
        return gun_saat_metni(self.kalan, self.gunluk_saat)

    @property
    def kullanilan_metni(self) -> str:
        return gun_saat_metni(self.kullanilan, self.gunluk_saat)


# === Saatlik izin: günlük çalışma süresi ve çevrimler (IkAyari.gunluk_calisma_saati, varsayılan 7,5 saat) ===

def gunluk_saat() -> Decimal:
    from core.models import IkAyari
    return Decimal(IkAyari.get().gunluk_calisma_saati)


def gunluk_saat_ayarla(deger, kullanici=None):
    from core.models import IkAyari
    try:
        d = Decimal(str(deger).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise PersonelIzinHatasi("Günlük çalışma süresi geçerli bir sayı olmalı (örn. 7,5).")
    if not (Decimal("1") <= d <= Decimal("24")):
        raise PersonelIzinHatasi("Günlük çalışma süresi 1 ile 24 saat arasında olmalı.")
    ayar = IkAyari.get()
    ayar.gunluk_calisma_saati = d
    ayar.updated_by = kullanici
    ayar.save(update_fields=["gunluk_calisma_saati", "updated_by", "updated_at"])
    return ayar


def dakika_gun(dakika, gunluk=None) -> Decimal:
    """Dakika → gün karşılığı (3 ondalık), günlük çalışma süresine göre."""
    gunluk = gunluk if gunluk is not None else gunluk_saat()
    return (Decimal(int(dakika)) / (gunluk * 60)).quantize(Decimal("0.001"))


def sure_metni(dakika) -> str:
    """"2 sa 30 dk" / "45 dk" / "3 sa"."""
    dakika = int(dakika or 0)
    sa, dk = divmod(dakika, 60)
    if sa and dk:
        return f"{sa} sa {dk} dk"
    return f"{sa} sa" if sa else f"{dk} dk"


def gun_saat_metni(gun, gunluk=None) -> str:
    """Gün (ondalık) → "12 gün 3 sa" / "12 gün" / "3,5 sa" / "0 gün"; eksi bakiyede "-2 gün 4 sa"."""
    gunluk = Decimal(gunluk if gunluk is not None else gunluk_saat())
    g = Decimal(gun or 0)
    isaret = "-" if g < 0 else ""
    g = abs(g)
    tam = int(g)
    saat = ((g - tam) * gunluk).quantize(Decimal("0.1"))
    if saat >= gunluk:                                   # yuvarlama taşması (ör. 0,9999 gün)
        tam, saat = tam + 1, Decimal("0")
    saat_m = format(saat.normalize(), "f").replace(".", ",") if saat else ""
    if tam and saat:
        return f"{isaret}{tam} gün {saat_m} sa"
    if tam:
        return f"{isaret}{tam} gün"
    if saat:
        return f"{isaret}{saat_m} sa"
    return "0 gün"


def _as_of(personel, bugun):
    """Ayrılmış personelin bakiyesi çıkış tarihi itibarıyla hesaplanır."""
    if personel.isten_cikis_tarihi is not None and personel.isten_cikis_tarihi < bugun:
        return personel.isten_cikis_tarihi
    return bugun


def _bakiye_hesapla(personel, kullanilan, planlanan, bugun) -> IzinBakiye:
    tarih = _as_of(personel, bugun)
    hak = Decimal(hak_edilen_gun(personel.ise_giris_tarihi, personel.dogum_tarihi, tarih))
    onceki = Decimal(personel.izin_onceki_kullanilan or 0)
    kullanilan = Decimal(kullanilan or 0)
    planlanan = Decimal(planlanan or 0)
    ayrildi = personel.isten_cikis_tarihi is not None and personel.isten_cikis_tarihi < bugun
    sonraki = None
    if not ayrildi:
        sonraki = yil_donumu(personel.ise_giris_tarihi,
                             tamamlanan_yil(personel.ise_giris_tarihi, tarih) + 1)
    return IzinBakiye(
        hak_edilen=hak, onceki=onceki, kullanilan=kullanilan, planlanan=planlanan,
        kalan=hak - onceki - kullanilan, sonraki_hak_tarihi=sonraki, gunluk_saat=gunluk_saat())


def bakiye(personel: Personel, *, bugun=None) -> IzinBakiye:
    bugun = bugun or tr_bugun()
    yillik = PersonelIzin.objects.filter(
        personel=personel, silindi=False, tur=PersonelIzin.Tur.YILLIK)
    kullanilan = yillik.aggregate(t=Sum("gun"))["t"] or SIFIR
    planlanan = yillik.filter(baslangic__gt=bugun).aggregate(t=Sum("gun"))["t"] or SIFIR
    return _bakiye_hesapla(personel, kullanilan, planlanan, bugun)


def bakiyeler(personeller, *, bugun=None) -> dict:
    """{personel.pk: IzinBakiye} — TEK gruplu sorguyla (kişi başına sorgu yok)."""
    bugun = bugun or tr_bugun()
    liste = list(personeller)
    yillik = (PersonelIzin.objects
              .filter(silindi=False, tur=PersonelIzin.Tur.YILLIK,
                      personel_id__in=[p.pk for p in liste])
              .values("personel_id")
              .annotate(t=Sum("gun"), pl=Sum("gun", filter=Q(baslangic__gt=bugun))))
    toplamlar = {r["personel_id"]: (r["t"], r["pl"]) for r in yillik}
    return {p.pk: _bakiye_hesapla(p, *toplamlar.get(p.pk, (SIFIR, SIFIR)), bugun)
            for p in liste}


# === İzin kayıtları ===

def pazarsiz_gun(baslangic: date, bitis: date) -> Decimal:
    """baslangic..bitis (dahil) arası takvim günü eksi Pazar günleri VE resmî tatiller."""
    tatiller = tatil_gunleri_seti(baslangic, bitis)
    n = 0
    d = baslangic
    while d <= bitis:
        if d.weekday() != 6 and d not in tatiller:
            n += 1
        d += timedelta(days=1)
    return Decimal(n)


def aktif_izinler():
    return (PersonelIzin.objects.filter(silindi=False, personel__silindi=False)
            .select_related("personel"))


def izin_listele(*, personel_id=None, tur="", baslangic=None, bitis=None, izinde_bugun=None):
    """Filtreler: kişi, tür, tarih aralığı (ÇAKIŞMA anlamıyla: aralığa değen her izin) ve
    `izinde_bugun` (verilen günü kapsayan izinler)."""
    qs = aktif_izinler()
    if personel_id:
        qs = qs.filter(personel_id=personel_id)
    if tur:
        qs = qs.filter(tur=tur)
    if baslangic:
        qs = qs.filter(bitis__gte=baslangic)
    if bitis:
        qs = qs.filter(baslangic__lte=bitis)
    if izinde_bugun:
        qs = qs.filter(baslangic__lte=izinde_bugun, bitis__gte=izinde_bugun)
    return qs


def izinde_olanlar(bugun=None):
    bugun = bugun or tr_bugun()
    return izin_listele(izinde_bugun=bugun)


def _saat_coz(deger, etiket):
    if deger in (None, ""):
        raise PersonelIzinHatasi(f"Saatlik izinde {etiket} zorunlu.")
    if isinstance(deger, time):
        return deger.replace(second=0, microsecond=0)
    try:
        sa, dk = str(deger).strip().split(":")[:2]
        return time(int(sa), int(dk))
    except (ValueError, TypeError):
        raise PersonelIzinHatasi(f"{etiket} geçerli değil (örn. 09:30).")


def _dogrula(personel, *, tur, baslangic, bitis, gun, aciklama, saatlik=False, baslangic_saati=None, bitis_saati=None) -> dict:
    if tur not in PersonelIzin.Tur.values:
        raise PersonelIzinHatasi("Geçersiz izin türü.")
    if saatlik:
        if baslangic is None:
            raise PersonelIzinHatasi("Saatlik izinde tarih zorunlu.")
        bitis = baslangic                                                                 # tek gün
        bs, bt = _saat_coz(baslangic_saati, "başlangıç saati"), _saat_coz(bitis_saati, "bitiş saati")
        if bt <= bs:
            raise PersonelIzinHatasi("Bitiş saati başlangıç saatinden sonra olmalı.")
        dakika = (bt.hour * 60 + bt.minute) - (bs.hour * 60 + bs.minute)
        if baslangic < personel.ise_giris_tarihi:
            raise PersonelIzinHatasi(
                f"İzin, işe giriş tarihinden ({personel.ise_giris_tarihi:%d.%m.%Y}) önce başlayamaz.")
        if personel.isten_cikis_tarihi is not None and bitis > personel.isten_cikis_tarihi:
            raise PersonelIzinHatasi(
                f"İzin, işten çıkış tarihinden ({personel.isten_cikis_tarihi:%d.%m.%Y}) sonra sürdürülemez.")
        return {"tur": tur, "baslangic": baslangic, "bitis": bitis, "gun": dakika_gun(dakika), "aciklama": (aciklama or "").strip(),
                "saatlik": True, "baslangic_saati": bs, "bitis_saati": bt, "dakika": dakika}
    if baslangic is None or bitis is None:
        raise PersonelIzinHatasi("Başlangıç ve bitiş tarihi zorunlu.")
    if bitis < baslangic:
        raise PersonelIzinHatasi("Bitiş tarihi başlangıçtan önce olamaz.")
    if baslangic < personel.ise_giris_tarihi:
        raise PersonelIzinHatasi(
            f"İzin, işe giriş tarihinden ({personel.ise_giris_tarihi:%d.%m.%Y}) önce başlayamaz.")
    if personel.isten_cikis_tarihi is not None and bitis > personel.isten_cikis_tarihi:
        raise PersonelIzinHatasi(
            f"İzin, işten çıkış tarihinden ({personel.isten_cikis_tarihi:%d.%m.%Y}) sonra "
            f"sürdürülemez.")
    takvim = Decimal((bitis - baslangic).days + 1)
    if gun is None or gun == "":
        gun = pazarsiz_gun(baslangic, bitis)
        if gun <= 0:
            raise PersonelIzinHatasi(
                "Seçilen aralıkta sayılacak gün yok (yalnız Pazar/resmî tatil). "
                "Gün sayısını elle girin.")
    else:
        gun = Decimal(gun)
        if gun <= 0:
            raise PersonelIzinHatasi("Gün sayısı sıfırdan büyük olmalı.")
        if (gun * 2) % 1 != 0:
            raise PersonelIzinHatasi("Gün sayısı yarım gün adımlarıyla girilmeli (örn. 2 veya 2,5).")
        if gun > takvim:
            raise PersonelIzinHatasi(
                f"Gün sayısı ({gun}) seçilen aralıktaki takvim gününden ({takvim}) fazla olamaz.")
    return {"tur": tur, "baslangic": baslangic, "bitis": bitis, "gun": gun,
            "aciklama": (aciklama or "").strip(), "saatlik": False, "baslangic_saati": None, "bitis_saati": None, "dakika": 0}


def _cakisma_kontrol(personel, baslangic, bitis, *, haric_pk=None, saatlik=False, baslangic_saati=None, bitis_saati=None):
    """Tam gün izin: tarih aralığına değen her izinle (saatlik dahil) çakışır. Saatlik izin: aynı gün tam gün izin varsa çakışır; aynı gün saatlik
    izinle yalnız saat aralıkları kesişiyorsa çakışır."""
    qs = PersonelIzin.objects.filter(
        personel=personel, silindi=False, baslangic__lte=bitis, bitis__gte=baslangic)
    if haric_pk is not None:
        qs = qs.exclude(pk=haric_pk)
    for mevcut in qs.order_by("baslangic"):
        if saatlik and mevcut.saatlik and not (mevcut.baslangic_saati < bitis_saati and mevcut.bitis_saati > baslangic_saati):
            continue
        if mevcut.saatlik:
            raise PersonelIzinHatasi(
                f"Bu personelin {mevcut.baslangic:%d.%m.%Y} {mevcut.baslangic_saati:%H:%M}–{mevcut.bitis_saati:%H:%M} saatlik "
                f"({mevcut.get_tur_display()}) izniyle çakışıyor.")
        raise PersonelIzinHatasi(
            f"Bu personelin {mevcut.baslangic:%d.%m.%Y} – {mevcut.bitis:%d.%m.%Y} tarihli "
            f"({mevcut.get_tur_display()}) izniyle çakışıyor.")


def mesai_cakismasi(personel, tarih, baslangic_saati, bitis_saati) -> list:
    """Saatlik izin aralığına değen mesai kayıtları (uyarı için; engel yok): [(giriş dt, çıkış dt|None)]."""
    from core.models import MesaiKaydi
    from core.tarih import _TR
    bas = datetime.combine(tarih, baslangic_saati, tzinfo=_TR)
    bit = datetime.combine(tarih, bitis_saati, tzinfo=_TR)
    sonuc = []
    for m in MesaiKaydi.objects.filter(personel=personel, silindi=False, is_tarihi=tarih).order_by("giris_zamani"):
        cikis = m.cikis_zamani or timezone.now()
        if m.giris_zamani < bit and cikis > bas:
            sonuc.append(m)
    return sonuc


def ucretsiz_saatlik_ozet(yil, ay) -> list:
    """Bordro için: dönemde ÜCRETSİZ saatlik izinleri olan personeller → [{"personel", "dakika", "saat", "kayit_sayisi"}] (ad soyad sırası)."""
    from core.tarih import ay_araligi
    ilk, son = ay_araligi(yil, ay)
    toplam = {}
    for i in (PersonelIzin.objects.filter(silindi=False, saatlik=True, tur=PersonelIzin.Tur.UCRETSIZ, baslangic__range=(ilk, son))
              .select_related("personel").order_by("personel__ad", "personel__soyad", "baslangic")):
        t = toplam.setdefault(i.personel_id, {"personel": i.personel, "dakika": 0, "kayit_sayisi": 0})
        t["dakika"] += i.dakika
        t["kayit_sayisi"] += 1
    for t in toplam.values():
        t["saat"] = (Decimal(t["dakika"]) / 60).quantize(Decimal("0.01"))
        t["sure_metni"] = sure_metni(t["dakika"])
    return list(toplam.values())


def _kilitle(personel_id):
    """Aynı kişiye eşzamanlı iki izin kaydı çakışma kontrolünü atlatmasın diye satır kilidi."""
    p = Personel.objects.select_for_update().filter(pk=personel_id).first()
    if p is None or p.silindi:
        raise PersonelIzinHatasi("Silinmiş personele izin girilemez / düzenlenemez.")
    return p


def ise_baslama_tarihi(bitis: date) -> date:
    """Bitişten sonraki ilk iş günü (Pazar ve resmî tatil atlanır)."""
    d = bitis + timedelta(days=1)
    tatiller = tatil_gunleri_seti(d, d + timedelta(days=30))
    while d.weekday() == 6 or d in tatiller:
        d += timedelta(days=1)
    return d


def izin_formu_baglam(izin: PersonelIzin) -> dict:
    """"İzin Talep ve Onay Formu" PDF'i: personel, izin bilgileri, yıllık izinde bakiye kutusu (hak edilen, bu izinden önce kullanılan, bu izin,
    kalan — gün + saat), işe başlama tarihi (tam günde). Hesap izin BAŞLANGIÇ tarihi itibarıyladır."""
    p = izin.personel
    gunluk = gunluk_saat()
    d = {"izin": izin, "personel": p, "gunluk_saat": gunluk, "sure_metni": sure_metni(izin.dakika) if izin.saatlik else "",
         "ise_baslama": None if izin.saatlik else ise_baslama_tarihi(izin.bitis), "yillik": izin.tur == PersonelIzin.Tur.YILLIK, "bakiye": None}
    if d["yillik"]:
        tarih = izin.baslangic
        hak = Decimal(hak_edilen_gun(p.ise_giris_tarihi, p.dogum_tarihi, _as_of(p, tarih)))
        onceki = Decimal(p.izin_onceki_kullanilan or 0)
        once = (PersonelIzin.objects.filter(personel=p, silindi=False, tur=PersonelIzin.Tur.YILLIK)
                .filter(Q(baslangic__lt=izin.baslangic) | Q(baslangic=izin.baslangic, pk__lt=izin.pk)).aggregate(t=Sum("gun"))["t"] or SIFIR)
        kalan = hak - onceki - once - izin.gun
        d["bakiye"] = {"hak_edilen": hak, "onceki": onceki, "once_kullanilan": once, "bu_izin": izin.gun, "kalan": kalan,
                       "hak_metni": gun_saat_metni(hak, gunluk), "onceki_metni": gun_saat_metni(onceki, gunluk),
                       "once_metni": gun_saat_metni(once, gunluk), "bu_izin_metni": gun_saat_metni(izin.gun, gunluk),
                       "kalan_metni": gun_saat_metni(kalan, gunluk), "eksi": kalan < 0}
    return d


def _cakisma_arg(veri):
    return {"saatlik": veri["saatlik"], "baslangic_saati": veri["baslangic_saati"], "bitis_saati": veri["bitis_saati"]}


@transaction.atomic
def izin_ekle(personel: Personel, *, tur, baslangic, bitis=None, gun=None, aciklama="",
              kullanici=None, saatlik=False, baslangic_saati=None, bitis_saati=None) -> PersonelIzin:
    p = _kilitle(personel.pk)
    veri = _dogrula(p, tur=tur, baslangic=baslangic, bitis=bitis, gun=gun, aciklama=aciklama, saatlik=saatlik,
                    baslangic_saati=baslangic_saati, bitis_saati=bitis_saati)
    _cakisma_kontrol(p, veri["baslangic"], veri["bitis"], **_cakisma_arg(veri))
    return PersonelIzin.objects.create(
        personel=p, created_by=kullanici, updated_by=kullanici, **veri)


@transaction.atomic
def izin_guncelle(izin: PersonelIzin, *, tur, baslangic, bitis=None, gun=None, aciklama="",
                  kullanici=None, saatlik=False, baslangic_saati=None, bitis_saati=None) -> PersonelIzin:
    if izin.silindi:
        raise PersonelIzinHatasi("Silinmiş izin kaydı düzenlenemez.")
    p = _kilitle(izin.personel_id)
    veri = _dogrula(p, tur=tur, baslangic=baslangic, bitis=bitis, gun=gun, aciklama=aciklama, saatlik=saatlik,
                    baslangic_saati=baslangic_saati, bitis_saati=bitis_saati)
    _cakisma_kontrol(p, veri["baslangic"], veri["bitis"], haric_pk=izin.pk, **_cakisma_arg(veri))
    for alan, deger in veri.items():
        setattr(izin, alan, deger)
    izin.updated_by = kullanici
    izin.save(update_fields=[*veri.keys(), "updated_by", "updated_at"])
    return izin


def izin_sil(izin: PersonelIzin, kullanici=None) -> PersonelIzin:
    if izin.silindi:
        return izin
    izin.silindi = True
    izin.silindi_at = timezone.now()
    izin.updated_by = kullanici
    izin.save(update_fields=["silindi", "silindi_at", "updated_by", "updated_at"])
    return izin
