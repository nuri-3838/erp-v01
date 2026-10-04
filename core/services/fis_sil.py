"""Kalıcı (fiziksel) silme — fiş + satırları + bağlı hareket kaydı (kullanıcı kararı: "İptal" yok,
kayıt ya durur ya silinir). Banka/kredi kartı/kasa hareketi, manuel fiş ve çek/senet bordrosu bunu kullanır.

Kurallar:
- YALNIZ süper kullanıcı siler.
- Başka bir kayda bağlıysa (fatura, bordro, çek, yatırım projesi, kredi taksiti, stok sarf, üretim...)
  SİLİNMEZ; neden gösterilir. Fişin kendi parçaları (satırlar, kredi kartı taksit planı) birlikte silinir.
- Her silme ``SilmeKaydi``na yazılır (kim, ne zaman, no, tarih, tutar, açıklama, satırlar — JSON);
  denetim kaydı silme ile AYNI transaction'dadır (silme geri alınırsa kayıt da geri alınır).
"""
from collections import Counter
from decimal import Decimal

from django.db import router, transaction
from django.db.models import ProtectedError, RestrictedError
from django.db.models.deletion import Collector

from core.models import KrediKartiTaksit, SilmeKaydi, YevmiyeFisi

# Fişle birlikte silinen kendi parçaları (başka kayıt sayılmaz).
FIS_COCUKLARI = {"YevmiyeSatir"}


class SilmeHatasi(ValueError):
    """Silme engellendi (yetki ya da bağlı kayıt)."""


def yetki_kontrol(kullanici):
    if kullanici is None or not getattr(kullanici, "is_superuser", False):
        raise SilmeHatasi("Silme yalnız süper kullanıcı tarafından yapılabilir.")


def _engel_metni(e):
    objs = getattr(e, "protected_objects", None) or getattr(e, "restricted_objects", None) or []
    sayac = Counter(o._meta.verbose_name_plural for o in objs)
    return ", ".join(f"{n} {ad}" for ad, n in sayac.items()) or str(e.args[0])


def kalici_sil(obj, izinli_cocuklar=()):
    """obj'yi (+ izinli CASCADE çocuklarını) siler; başka bağ varsa SilmeHatasi."""
    c = Collector(using=router.db_for_write(type(obj)))
    try:
        c.collect([obj])
    except (ProtectedError, RestrictedError) as e:
        raise SilmeHatasi(f"Başka kayıtlara bağlı: {_engel_metni(e)}.")
    izin = {type(obj).__name__, *izinli_cocuklar}
    sayi = Counter({m.__name__: len(o) for m, o in c.data.items()})
    for qs in c.fast_deletes:
        sayi[qs.model.__name__] += qs.count()
    fazla = {m: n for m, n in sayi.items() if m not in izin and n}
    if fazla:
        raise SilmeHatasi("Başka kayıtlara bağlı: " + ", ".join(f"{n} {m}" for m, n in fazla.items()) + ".")
    if c.field_updates:
        alanlar = sorted({f"{k[0].model._meta.verbose_name}.{k[0].verbose_name}" for k in c.field_updates})
        raise SilmeHatasi("Başka kayıtlara bağlı: " + ", ".join(alanlar) + ".")
    c.delete()


def fis_ozeti(fis):
    """Onay penceresi + denetim kaydı için fiş özeti (aktif ve pasif tüm satırlar)."""
    satirlar = list(fis.satirlar.select_related("hesap").order_by("id"))
    # İptal (eski) fişte tüm satırlar silindi=True işaretlidir; aktif fişte silindi=True satır, düzenleme
    # geçmişidir (hesaba katılmaz).
    gecerli = [s for s in satirlar if fis.silindi or not s.silindi]
    borc = sum((s.borc for s in gecerli), Decimal("0.00"))
    return {
        "no": f"{fis.yil}/{fis.fis_no}", "tarih": fis.tarih, "tutar": borc,
        "aciklama": fis.aciklama, "kaynak": fis.kaynak,
        "satirlar": [{"hesap": s.hesap_id, "hesap_adi": s.hesap.hesap_adi, "borc": str(s.borc),
                      "alacak": str(s.alacak), "islem_pb": s.islem_pb,
                      "islem_tutari": str(s.islem_tutari), "islem_kuru": str(s.islem_kuru),
                      "aciklama": s.aciklama, "silindi": s.silindi,
                      "gizli": s.silindi and not fis.silindi} for s in satirlar],
    }


def _fis_verisi(fis):
    ozet = fis_ozeti(fis)
    taksitler = [{"taksit_adedi": t.taksit_adedi, "ilk_vade": str(t.ilk_vade),
                  "toplam_tutar": str(t.toplam_tutar), "para_birimi": t.para_birimi}
                 for t in KrediKartiTaksit.objects.filter(fis=fis)]
    veri = {"fis_pk": fis.pk, "kur_usd": str(fis.kur_usd) if fis.kur_usd is not None else None,
            "iptal_durumunda": fis.silindi, "satirlar": ozet["satirlar"]}
    if taksitler:
        veri["kk_taksit_planlari"] = taksitler
    return ozet, veri


def fis_kalici_sil(fis):
    """Yetki/denetim OLMADAN fişi + satırları + (kredi kartı) taksit planını siler (iç kullanım).
    (ozet, veri) döner."""
    ozet, veri = _fis_verisi(fis)
    KrediKartiTaksit.objects.filter(fis=fis).delete()
    kalici_sil(fis, FIS_COCUKLARI)
    return ozet, veri


def _denetim(tur, ozet, veri, kullanici, kaynak=""):
    return SilmeKaydi.objects.create(
        tur=tur, kayit_no=ozet["no"], tarih=ozet["tarih"], tutar=ozet["tutar"],
        aciklama=(ozet["aciklama"] or "")[:500], kaynak=kaynak or ozet.get("kaynak", ""),
        silen=kullanici, created_by=kullanici, updated_by=kullanici, veri=veri)


@transaction.atomic
def fis_sil(fis, *, kullanici, izinli_kaynaklar=None):
    """Fişi KALICI siler + denetim kaydı yazar. ``izinli_kaynaklar``: bu çağrının silebileceği
    fiş kaynakları (ör. {KASA}); dışındaki fişler (fatura/stok/yatırım/çek bordrosu...) başka
    ekranlarından yönetilir."""
    yetki_kontrol(kullanici)
    if izinli_kaynaklar is not None and fis.kaynak not in izinli_kaynaklar:
        raise SilmeHatasi("Bu fiş buradan silinemez; kaynak ekranından yönetilir.")
    ozet, veri = fis_kalici_sil(fis)
    return _denetim(SilmeKaydi.Tur.FIS, ozet, veri, kullanici)


def onizle(fonksiyon, *args, **kwargs):
    """Silmeyi bir transaction içinde dener ve GERİ ALIR: engel metni ya da None (silinebilir)."""
    class _Geri(Exception):
        pass
    try:
        with transaction.atomic():
            fonksiyon(*args, **kwargs)
            raise _Geri
    except _Geri:
        return None
    except ValueError as e:
        return str(e)
