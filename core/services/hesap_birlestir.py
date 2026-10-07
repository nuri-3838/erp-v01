"""Hesap birleştirme: 679 → 649, 689 → 659 (kalıcı). Olağandışı gelir/gider artık yalnız 649/659'a yazılır.

Yalnız HESAP KODU değişir: fiş no, tarih, tutar, açıklama, kaynak aynen kalır. Fişe bağlı model alanları (KdvMahsup.fark_hesap) da
taşınır — kayıt sonradan Düzenle ile yeniden yazılsa eski hesaba dönmez. Kaynak hesap taşıma sonunda PASİF yapılır (silinmez).
Hepsi TEK transaction; yeniden çalıştırmak zararsızdır (taşınacak bir şey kalmamışsa yalnız doğrular).
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Count, Q, Sum

from core.models import HesapPlani, KdvMahsup, YevmiyeFisi, YevmiyeSatir
from core.services import raporlar

BIRLESTIRMELER = (("679", "649"), ("689", "659"))
SIFIR = Decimal("0.00")


class HesapBirlestirHatasi(ValueError):
    pass


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


def _alt_hesaplar(kod):
    return list(HesapPlani.objects.filter(hesap_kodu__startswith=kod + ".").values_list("hesap_kodu", flat=True))


def _hesap_fk_alanlari():
    """HesapPlani'ya FK veren tüm (model, alan) çiftleri."""
    for m in apps.get_models():
        for f in m._meta.get_fields():
            if f.is_relation and f.concrete and getattr(f, "related_model", None) is HesapPlani:
                yield m, f


def _kalan_referanslar(kod):
    out = {}
    for m, f in _hesap_fk_alanlari():
        n = m.objects.filter(**{f.name + "_id": kod}).count()
        if n:
            out[f"{m.__name__}.{f.name}"] = n
    return out


def dokum(kaynak):
    """Kaynak hesabın satır dökümü: (toplam, kaynak türüne göre dağılım)."""
    qs = YevmiyeSatir.objects.filter(hesap_id=kaynak)
    ozet = qs.aggregate(n=Count("id"), b=Sum("borc"), a=Sum("alacak"))
    dagilim = defaultdict(lambda: [0, SIFIR, SIFIR])
    for s in qs.select_related("fis"):
        x = dagilim[(s.fis.kaynak, "silinmiş" if (s.silindi or s.fis.silindi) else "aktif")]
        x[0] += 1
        x[1] += s.borc
        x[2] += s.alacak
    return {"satir": ozet["n"] or 0, "borc": ozet["b"] or SIFIR, "alacak": ozet["a"] or SIFIR, "dagilim": dict(dagilim)}


def birlestir(*, uygula=False, cikti=print):
    """Önizleme (``uygula=False``: hiçbir şey yazılmaz) ya da kalıcı taşıma. Sonuç sözlüğü döner."""
    sonuc = {"tasinan": {}, "uygulandi": False}
    with transaction.atomic():
        fis_once = YevmiyeFisi.objects.count()
        satir_once = YevmiyeSatir.objects.count()
        bak_once = {k: _bak(k) for pair in BIRLESTIRMELER for k in pair}
        for kaynak, hedef in BIRLESTIRMELER:
            if _alt_hesaplar(kaynak):
                raise HesapBirlestirHatasi(f"{kaynak} altında alt hesap var ({_alt_hesaplar(kaynak)}); elle birleştirin.")
            if not HesapPlani.objects.filter(hesap_kodu=hedef, silindi=False).exists():
                raise HesapBirlestirHatasi(f"Hedef hesap {hedef} bulunamadı.")
        for kaynak, hedef in BIRLESTIRMELER:
            d = dokum(kaynak)
            cikti(f"{kaynak} → {hedef}: {d['satir']} satır, borç {d['borc']}, alacak {d['alacak']}")
            for (kay, durum), (n, b, a) in sorted(d["dagilim"].items()):
                cikti(f"    {kay:<12} {durum:<9} {n} satır  borç {b}  alacak {a}")
            if d["satir"]:
                satir_ids = list(YevmiyeSatir.objects.filter(hesap_id=kaynak).values_list("pk", flat=True))
                sonuc["tasinan"][kaynak] = satir_ids
                YevmiyeSatir.objects.filter(pk__in=satir_ids).update(hesap_id=hedef)
            mh = KdvMahsup.objects.filter(fark_hesap_id=kaynak)
            if mh.exists():
                cikti(f"    KdvMahsup.fark_hesap: {mh.count()} kayıt {kaynak} → {hedef}")
                sonuc.setdefault("kdv_mahsup", []).append(mh.count())
                mh.update(fark_hesap_id=hedef)
            kalan = _kalan_referanslar(kaynak)
            if kalan:
                raise HesapBirlestirHatasi(f"{kaynak} hâlâ kullanılıyor: {kalan}")
            h = HesapPlani.objects.get(hesap_kodu=kaynak)
            if h.aktif:
                h.aktif = False
                h.save(update_fields=["aktif"])
                cikti(f"    {kaynak} pasife alındı")
        bak_sonra = {k: _bak(k) for pair in BIRLESTIRMELER for k in pair}
        for kaynak, hedef in BIRLESTIRMELER:
            cikti(f"bakiye {kaynak}: {bak_once[kaynak]} → {bak_sonra[kaynak]}   |   {hedef}: {bak_once[hedef]} → {bak_sonra[hedef]}")
            if bak_sonra[kaynak] != SIFIR:
                raise HesapBirlestirHatasi(f"{kaynak} bakiyesi 0 değil: {bak_sonra[kaynak]}")
            if bak_sonra[hedef] != bak_once[hedef] + bak_once[kaynak]:
                raise HesapBirlestirHatasi(f"{hedef} bakiyesi beklenen değil.")
        if YevmiyeFisi.objects.count() != fis_once or YevmiyeSatir.objects.count() != satir_once:
            raise HesapBirlestirHatasi("Fiş/satır sayısı değişti.")
        m = raporlar.mizan()
        cikti(f"mizan dengeli: {m.toplam_borc == m.toplam_alacak}" if hasattr(m, "toplam_borc") else "mizan alındı")
        if not uygula:
            transaction.set_rollback(True)
            cikti("ÖNİZLEME — hiçbir şey yazılmadı (--uygula ile kalıcı).")
        else:
            sonuc["uygulandi"] = True
    return sonuc
