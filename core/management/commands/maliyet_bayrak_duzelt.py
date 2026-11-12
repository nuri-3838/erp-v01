"""BAYAT MALİYET BAYRAKLARI TEMİZLİĞİ (bkz. core.services.maliyet_bayrak): üretim girişlerinde girdisi artık tamamen KESİN olduğu hâlde 'tahmini/GEÇİCİ' kalmış bayatları temizler
(motor durum değişimini yaymadığı dönemden kalma). YALNIZ ``giris_tahmini`` / ``maliyet_durumu`` bayrakları değişir; hareket tutarı, fiş, mizan değişmez (doğrulanır, biri tutmazsa
geri alınır). Varsayılan KURU çalıştırma (geri alınır); ``--uygula`` kalıcı yazar. İdempotent.

    python manage.py maliyet_bayrak_duzelt            # kuru
    python manage.py maliyet_bayrak_duzelt --uygula
"""
from django.core.management.base import BaseCommand, CommandError

from core.services import maliyet_bayrak


class Command(BaseCommand):
    help = "Bayat maliyet bayraklarini (giris_tahmini / maliyet_durumu) temizler; tutar/fis/mizan degismez; varsayilan kuru."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true", help="kalıcı yaz (verilmezse kuru çalıştırma, geri alınır)")

    def handle(self, *args, **opts):
        try:
            rapor = maliyet_bayrak.duzelt(uygula=opts["uygula"])
        except maliyet_bayrak.MaliyetBayrakHatasi as e:
            raise CommandError(str(e))
        self.stdout.write(maliyet_bayrak.rapor_metni(rapor))
