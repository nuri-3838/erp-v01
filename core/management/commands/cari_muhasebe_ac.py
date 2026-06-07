"""Mevcut carilerin muhasebe hesaplarını hesap planında otomatik açar (idempotent).

Her cari için kodundan (örn. 320-10-0003) muhasebe hesabı (320.10.0003) ve eksik ara
hesaplar (320.10) hesap planında açılır; cari.muhasebe_kodu set edilir. Tekrar çalıştırılabilir.
"""
from django.core.management.base import BaseCommand

from core.models import Cari
from core.services import cari as C


class Command(BaseCommand):
    help = "Carilerin muhasebe hesaplarını hesap planında açar (idempotent)."

    def handle(self, *args, **opts):
        acilan = atlanan = 0
        for c in Cari.objects.filter(silindi=False).order_by("kod"):
            muh = C.muhasebe_hesabi_ac(c)
            if muh:
                if c.muhasebe_kodu != muh:
                    c.muhasebe_kodu = muh
                    c.save(update_fields=["muhasebe_kodu"])
                acilan += 1
            else:
                atlanan += 1
        self.stdout.write(self.style.SUCCESS(
            f"Muhasebe hesabı açma tamam: {acilan} cari işlendi, {atlanan} uygun değil (atlandı)."))
