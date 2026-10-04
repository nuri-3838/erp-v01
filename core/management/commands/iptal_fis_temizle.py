"""Tek seferlik: veritabanındaki İPTAL durumundaki (silindi=True) tüm yevmiye fişlerini ve bağlı hareket
kayıtlarını KALICI siler (iptal kavramı kaldırıldı — bkz. core.services.fis_sil). Varsayılan DRY-RUN.

Her fiş için: no, tarih, tutar, açıklama, kaynak (banka/kart/kasa/kredi/çek/manuel...), bağlı kayıt.
Fişin kendi parçaları (satırlar, kredi kartı taksit planı) birlikte silinir; BAŞKA kayda bağlı olan
(fatura, bordro, yatırım projesi, kredi taksiti...) fişe DOKUNULMAZ, raporlanır. Her silme
``SilmeKaydi``na (silen boş, ``veri.temizlik`` işaretli) yazılır.

Kontroller (öncesi/sonrası): mizan toplamı + hesap bazlı mizan özeti; tüm banka hesapları, kredi
kartları, kasalar ve kredilerin kapanış bakiyeleri.

    python manage.py iptal_fis_temizle [--uygula]
"""
import datetime
import hashlib
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import router, transaction
from django.db.models import Count, ProtectedError, RestrictedError, Sum
from django.db.models.deletion import Collector

from core.models import (BankaHesap, Kasa, Kredi, KrediKarti, KrediKartiTaksit, SilmeKaydi,
                         YevmiyeFisi, YevmiyeSatir)
from core.services import fis_sil as fs
from core.services import raporlar

KAYNAK_AD = {"BANKA": "banka", "KREDI_KARTI": "kredi kartı", "KASA": "kasa", "KREDI": "kredi",
             "CEK_SENET": "çek/senet", "MANUEL": "manuel"}


def _baglilar(fis):
    """Fişin KENDİ parçaları dışındaki bağlı kayıtlar: {ad: sayı} (boş = bağsız)."""
    c = Collector(using=router.db_for_write(YevmiyeFisi))
    bag = {}
    try:
        c.collect([fis])
    except (ProtectedError, RestrictedError) as e:
        for o in (getattr(e, "protected_objects", None) or getattr(e, "restricted_objects", None) or []):
            ad = o._meta.verbose_name
            if o._meta.model is KrediKartiTaksit:
                continue
            bag[ad] = bag.get(ad, 0) + 1
        return bag
    for m, objs in c.data.items():
        if m.__name__ not in ("YevmiyeFisi", "YevmiyeSatir") and objs:
            bag[m._meta.verbose_name] = len(objs)
    for k, liste in c.field_updates.items():
        bag[f"{k[0].model._meta.verbose_name}.{k[0].verbose_name}"] = sum(len(o) for o in liste)
    return bag


class Command(BaseCommand):
    help = "Iptal durumundaki fisleri ve bagli hareket kayitlarini kalici siler (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(
            n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        m = raporlar.mizan(datetime.date(2000, 1, 1), datetime.date(2100, 12, 31))
        ozet = "|".join(f"{s.hesap_kodu}:{s.borc}:{s.alacak}" for s in sorted(m.satirlar, key=lambda x: x.hesap_kodu))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"],
                hashlib.sha256(ozet.encode()).hexdigest()[:16])

    def _bakiyeler(self):
        son = datetime.date(2100, 1, 1)
        out = {}
        for ad, qs in (("BANKA", BankaHesap.objects.filter(silindi=False)), ("KASA", Kasa.objects.filter(silindi=False)),
                       ("KART", KrediKarti.objects.filter(silindi=False)), ("KREDI", Kredi.objects.filter(silindi=False))):
            for h in qs.order_by("pk"):
                net, dvz = raporlar._devir(h.muhasebe_id, son)
                out[(ad, h.pk)] = (h.ad, h.muhasebe_id, net, {k: v for k, v in dvz.items() if v})
        return out

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        once_mizan, once_bak = self._mizan(), self._bakiyeler()
        w(f"\nÖNCE mizan (aktif fiş, aktif satır, borç, alacak, özet): {once_mizan}")
        silinen, atlanan = Counter(), []
        with transaction.atomic():
            fisler = list(YevmiyeFisi.objects.filter(silindi=True).order_by("yil", "fis_no"))
            w(f"\nİptal durumundaki fiş sayısı: {len(fisler)}  "
              f"(kaynak: {dict(Counter(KAYNAK_AD.get(f.kaynak, f.kaynak) for f in fisler))})")
            for f in fisler:
                ozet = fs.fis_ozeti(f)
                bag = _baglilar(f)
                plan = KrediKartiTaksit.objects.filter(fis=f).count()
                kaynak = KAYNAK_AD.get(f.kaynak, f.kaynak)
                sahip = {"BANKA": f.banka_hesap_id, "KASA": f.kasa_id, "KREDI_KARTI": f.kredi_karti_id,
                         "KREDI": f.kredi_id, "CEK_SENET": f.cek_bordrosu_id}.get(f.kaynak)
                w(f"  {ozet['no']} | id {f.pk} | {f.tarih:%d.%m.%Y} | {ozet['tutar']} TL | {kaynak}"
                  f"{f' #{sahip}' if sahip else ''} | satır {len(ozet['satirlar'])} | "
                  f"'{(f.aciklama or '')[:80]}'")
                w(f"      kendi parçaları: satır {len(ozet['satirlar'])}"
                  f"{f', kredi kartı taksit planı {plan}' if plan else ''}"
                  f" | BAŞKA bağlı kayıt: {bag or 'yok'}")
                if bag:
                    w("      → DOKUNULMAZ (bağlı kayıt var)")
                    atlanan.append(f"{ozet['no']}: {bag}")
                    continue
                try:
                    o, veri = fs.fis_kalici_sil(f)
                    veri["temizlik"] = "iptal_fis_temizle"
                    fs._denetim(SilmeKaydi.Tur.FIS, o, veri, None)
                except fs.SilmeHatasi as e:
                    w(f"      → DOKUNULMAZ: {e}")
                    atlanan.append(f"{ozet['no']}: {e}")
                    continue
                silinen["fiş"] += 1
                silinen["satır"] += len(ozet["satirlar"])
                silinen["kk taksit planı"] += plan
                w("      → silinecek")
            w(f"\nToplam silinecek: {dict(silinen) or '-'}")
            w(f"Dokunulmayan: {atlanan or 'yok'}")
            sonra_mizan, sonra_bak = self._mizan(), self._bakiyeler()
            w(f"\nSONRA mizan: {sonra_mizan}  → {'DEĞİŞMEDİ' if sonra_mizan == once_mizan else 'DEĞİŞTİ!'}")
            degisen = [k for k in once_bak if once_bak[k] != sonra_bak.get(k)]
            w(f"Kapanış bakiyeleri ({len(once_bak)} hesap: banka/kasa/kart/kredi): "
              f"{'HEPSİ DEĞİŞMEDİ' if not degisen else 'DEĞİŞEN: ' + str(degisen)}")
            for (tur, pk), (ad, kod, net, dvz) in sorted(sonra_bak.items()):
                w(f"  {tur} {pk} {ad} [{kod}]: {net} TL" + (f" · döviz {dict((k, str(v)) for k, v in dvz.items())}" if dvz else ""))
            if not uygula:
                transaction.set_rollback(True)
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı silmek için --uygula."))
