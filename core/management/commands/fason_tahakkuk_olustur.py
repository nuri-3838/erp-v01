"""Faturasız fasoncuların (Cari.fason_faturasiz) tahakkuk fişi olmayan ONAYLI fason dönüşleri için cari tahakkuk fişlerini toplu yazar.

Fiş dönüş TARİHİYLE, 151 alt hesabı BORÇ / fasoncu cari ALACAK olarak yazılır (bkz. core.services.fason_donus.tahakkuk_olustur); mükerrer fiş
yazılmaz. ``--dry-run`` hiçbir şey yazmaz, yalnız ne yapılacağını listeler.

    python manage.py fason_tahakkuk_olustur --dry-run
    python manage.py fason_tahakkuk_olustur [--cari <pk>]
"""
from django.core.management.base import BaseCommand

from core.models import FasonDonus
from core.services import fason_donus as fd
from core.services.fason import FasonHatasi


class Command(BaseCommand):
    help = "Faturasız fasoncuların tahakkuk fişi olmayan onaylı fason dönüşleri için cari tahakkuk fişi yazar (--dry-run: yazmadan listeler)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Hiçbir kayıt yazmadan listele.")
        parser.add_argument("--cari", type=int, default=None, help="Yalnız bu cari (pk).")

    def handle(self, *args, **opts):
        kuru = opts["dry_run"]
        qs = (FasonDonus.objects.filter(silindi=False, cari__fason_faturasiz=True, tahakkuk_fis__isnull=True)
              .select_related("cari").order_by("yil", "sira"))
        if opts["cari"]:
            qs = qs.filter(cari_id=opts["cari"])
        yazilan = atlanan = hata = 0
        toplam = 0
        for d in qs:
            if fd.donus_durumu(d) != "ONAYLI":
                continue
            if d.fatura_id:
                self.stdout.write(f"ATLANDI {d.no}: faturaya bağlı.")
                atlanan += 1
                continue
            try:
                satirlar = fd.tahakkuk_satirlari(d)
                tutar = sum((t for _k, t in satirlar), 0)
                if kuru:
                    ozet = ", ".join(f"{k} B {t}" for k, t in satirlar) or "fason bedeli yok"
                    self.stdout.write(f"[dry-run] {d.no} {d.tarih:%d.%m.%Y} {d.cari.unvan}: {ozet} / {d.cari.muhasebe_kodu} A {tutar}")
                    if satirlar:
                        yazilan += 1
                        toplam += tutar
                    else:
                        atlanan += 1
                    continue
                fis = fd.tahakkuk_olustur(d)
            except FasonHatasi as e:
                self.stdout.write(self.style.ERROR(f"HATA {d.no}: {e}"))
                hata += 1
                continue
            if fis is None:
                atlanan += 1
                self.stdout.write(f"ATLANDI {d.no}: fason bedeli yok.")
            else:
                yazilan += 1
                toplam += sum((s.borc for s in fis.satirlar.filter(silindi=False)), 0)
                self.stdout.write(f"OK {d.no}: fiş {fis.yil}/{fis.fis_no}")
        etiket = "yazılacak" if kuru else "yazıldı"
        self.stdout.write(f"{yazilan} dönüş {etiket}, {atlanan} atlandı, {hata} hata. Toplam tahakkuk: {toplam} TL")
