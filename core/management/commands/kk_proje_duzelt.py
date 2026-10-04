"""Tek seferlik: kredi kartı harcamasının 258 satırına yatırım projesi yazar + manuel "proje bağlama"
mahsup fişini kalıcı siler (varsayılan DRY-RUN; ``--uygula`` kalıcı).

 1. Fiş 2026/574 (id 645, Ziraat KK, "EXPERTİZ - GEZEN ADAM (MEGANE)", 7.000): projesi boş 258 satırına
    YP-0008 (id 8) yazılır.
 2. Manuel mahsup fişi 2026/634 (id 705, "... YP-0008 PROJE BAĞLAMA ...") kalıcı silinir (denetim kaydıyla).
 3. Kredi kartı hareketlerinde 258'e gidip projesi boş olan DİĞER kayıtlar yalnız LİSTELENİR (değiştirilmez).
Kontroller: YP-0008 proje toplamı, 258 bakiyesi, kart bakiyeleri, mizan dengesi (öncesi/sonrası).

    python manage.py kk_proje_duzelt [--uygula]
"""
import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Sum

from core.models import KrediKarti, SilmeKaydi, YatirimProjesi, YevmiyeFisi, YevmiyeSatir
from core.sayi import format_tr
from core.services import fis_sil as fs
from core.services import raporlar
from core.services.yatirim_projesi import proje_toplami

KK_FIS = (645, 2026, 574, "EXPERTİZ - GEZEN ADAM (MEGANE)")
MAHSUP_FIS = (705, 2026, 634, "YP-0008 PROJE BAĞLAMA")
PROJE_PK, PROJE_KOD = 8, "YP-0008"


def _bak(kod):
    return raporlar._devir(kod, datetime.date(2100, 1, 1))[0]


class Command(BaseCommand):
    help = "KK harcamasina yatirim projesi yazar, manuel proje baglama fisini siler (dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def _mizan(self):
        a = YevmiyeSatir.objects.filter(silindi=False, fis__silindi=False).aggregate(
            n=Count("id"), b=Sum("borc"), c=Sum("alacak"))
        return (YevmiyeFisi.objects.filter(silindi=False).count(), a["n"], a["b"], a["c"])

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        w = self.stdout.write
        w("UYGULA (kalıcı)" if uygula else "DRY-RUN (geri alınacak, kalıcı değişiklik YOK)")
        proje = YatirimProjesi.objects.filter(pk=PROJE_PK, silindi=False).first()
        kartlar = list(KrediKarti.objects.filter(silindi=False).order_by("pk"))
        once = {"mizan": self._mizan(), "258": _bak("258"),
                "kartlar": {k.pk: (k.ad, _bak(k.muhasebe_id)) for k in kartlar},
                "proje": proje_toplami(proje) if proje else None}
        w(f"\nÖNCE mizan: {once['mizan']} | 258 bakiyesi: {format_tr(once['258'])} | {PROJE_KOD} toplamı: "
          f"{format_tr(once['proje']) if once['proje'] is not None else '-'}")
        for pk, (ad, v) in once["kartlar"].items():
            w(f"ÖNCE kart {pk} {ad}: {format_tr(v)}")
        sorunlar = []
        with transaction.atomic():
            w("\n== 1) KK harcamasının 258 satırına proje ==")
            fid, yil, no, parca = KK_FIS
            f = YevmiyeFisi.objects.filter(pk=fid).first()
            if proje is None or proje.kod != PROJE_KOD or proje.durum != YatirimProjesi.Durum.DEVAM:
                w(f"  {PROJE_KOD} (id {PROJE_PK}) bulunamadı ya da 'Devam Ediyor' değil → DOKUNULMAZ")
                sorunlar.append("proje")
            elif f is None or (f.yil, f.fis_no) != (yil, no) or f.kaynak != "KREDI_KARTI" or f.silindi \
                    or parca not in f.aciklama:
                w(f"  fiş 2026/{no} (id {fid}) bulunamadı ya da beklenenle uyuşmuyor → DOKUNULMAZ")
                sorunlar.append("kk fişi")
            else:
                satir = list(f.satirlar.filter(silindi=False, hesap_id="258"))
                w(f"  {yil}/{no} | id {fid} | {f.tarih:%d.%m.%Y} | kart {f.kredi_karti_id} | '{f.aciklama}'")
                if len(satir) != 1 or satir[0].borc != 7000 or satir[0].yatirim_projesi_id:
                    w("  → DOKUNULMAZ: 258 satırı bulunamadı / 7.000 borç değil / projesi zaten dolu")
                    sorunlar.append("kk 258 satırı")
                else:
                    w(f"  258 B {satir[0].borc}: proje boş → {PROJE_KOD}")
                    satir[0].yatirim_projesi = proje
                    satir[0].save(update_fields=["yatirim_projesi", "updated_at"])

            w("\n== 2) Manuel mahsup fişinin kalıcı silinmesi ==")
            fid, yil, no, parca = MAHSUP_FIS
            m = YevmiyeFisi.objects.filter(pk=fid).first()
            if m is None or (m.yil, m.fis_no) != (yil, no) or m.kaynak != "MANUEL" or m.silindi \
                    or parca not in m.aciklama:
                w(f"  fiş 2026/{no} (id {fid}) bulunamadı ya da beklenenle uyuşmuyor → DOKUNULMAZ")
                sorunlar.append("mahsup fişi")
            elif "kk fişi" in sorunlar or "proje" in sorunlar or "kk 258 satırı" in sorunlar:
                w("  → DOKUNULMAZ: 1. adım yapılamadığı için mahsup fişi silinmez")
            else:
                ozet = fs.fis_ozeti(m)
                w(f"  {ozet['no']} | id {fid} | {m.tarih:%d.%m.%Y} | {format_tr(ozet['tutar'])} TL | '{m.aciklama}'")
                for s in ozet["satirlar"]:
                    w(f"      {s['hesap']} B {s['borc']} A {s['alacak']}")
                try:
                    o, veri = fs.fis_kalici_sil(m)
                    veri["temizlik"] = "kk_proje_duzelt"
                    fs._denetim(SilmeKaydi.Tur.FIS, o, veri, None)
                    w("  → silinecek")
                except fs.SilmeHatasi as e:
                    w(f"  → DOKUNULMAZ: {e}")
                    sorunlar.append("mahsup silinemedi")

            w("\n== 3) Raporlama: KK hareketlerinde 258'e giden PROJESİ BOŞ diğer kayıtlar (değiştirilmez) ==")
            digerleri = (YevmiyeSatir.objects.filter(
                fis__kaynak="KREDI_KARTI", fis__silindi=False, silindi=False,
                hesap__hesap_kodu__startswith="258", yatirim_projesi__isnull=True)
                .select_related("fis").order_by("fis__tarih", "fis__fis_no"))
            n = 0
            for s in digerleri:
                n += 1
                w(f"  {s.fis.yil}/{s.fis.fis_no} | id {s.fis.pk} | {s.fis.tarih:%d.%m.%Y} | kart {s.fis.kredi_karti_id} | "
                  f"{format_tr(s.borc - s.alacak)} TL | '{s.fis.aciklama[:70]}'")
            if not n:
                w("  yok")

            w("\n== Kontroller ==")
            sonra_mizan = self._mizan()
            dengeli = sonra_mizan[2] == sonra_mizan[3]
            w(f"Mizan: {sonra_mizan} → {'DENGEDE' if dengeli else 'DENGESİZ!'}")
            p2 = YatirimProjesi.objects.get(pk=PROJE_PK) if proje else None
            if p2:
                w(f"{PROJE_KOD} toplamı: {format_tr(once['proje'])} → {format_tr(proje_toplami(p2))}  "
                  f"({'değişmedi' if proje_toplami(p2) == once['proje'] else 'DEĞİŞTİ'})")
            w(f"258 bakiyesi: {format_tr(once['258'])} → {format_tr(_bak('258'))}  "
              f"({'değişmedi' if _bak('258') == once['258'] else 'DEĞİŞTİ'})")
            for k in kartlar:
                v1 = _bak(k.muhasebe_id)
                w(f"Kart {k.pk} {k.ad}: {format_tr(once['kartlar'][k.pk][1])} → {format_tr(v1)}  "
                  f"({'değişmedi' if v1 == once['kartlar'][k.pk][1] else 'DEĞİŞTİ'})")
            if not uygula:
                transaction.set_rollback(True)
        w("\nDokunulmayan/sorunlar: " + (str(sorunlar) if sorunlar else "yok"))
        w("\n" + ("UYGULANDI." if uygula else "DRY-RUN: geri alındı. Kalıcı uygulamak için --uygula."))
