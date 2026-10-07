"""679 → 649 ve 689 → 659 hesap birleştirme (kalıcı; bkz. core.services.hesap_birlestir).

Varsayılan ÖNİZLEME (hiçbir şey yazılmaz); kalıcı için ``--uygula``. Önce canlı DB yedeği alın. Yeniden çalıştırmak zararsızdır.
"""
from django.core.management.base import BaseCommand, CommandError

from core.services import hesap_birlestir as hb


class Command(BaseCommand):
    help = "679 → 649, 689 → 659: fiş satırlarını taşı, KDV mahsubu fark hesabını güncelle, eski hesapları pasife al."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true", help="Kalıcı uygula (yoksa yalnız önizleme).")

    def handle(self, *args, **opts):
        try:
            hb.birlestir(uygula=opts["uygula"], cikti=self.stdout.write)
        except hb.HesapBirlestirHatasi as e:
            raise CommandError(str(e))
