"""Başlangıç birimlerini ekler/günceller (idempotent upsert; hiçbir şey silmez).

STOKLAR modülü ilk aşama. Ad'a göre upsert: tekrar çalıştırılabilir, siler değil.
"""
from django.core.management.base import BaseCommand

from core.metin import buyuk_harf_tr
from core.models import Birim

# (ad, kısa ad, ondalık hane)
BIRIMLER = [
    ("ADET", "AD", 0),
    ("BOY", "BOY", 0),
    ("KİLOGRAM", "KG", 3),
    ("KUTU", "KT", 0),
    ("METRE", "MT", 3),
]


class Command(BaseCommand):
    help = "Başlangıç birimlerini ekler (idempotent)."

    def handle(self, *args, **opts):
        eklenen = guncellenen = 0
        for ad, kisa, ondalik in BIRIMLER:
            _, olusturuldu = Birim.objects.update_or_create(
                ad=buyuk_harf_tr(ad),
                defaults={
                    "kisa_ad": buyuk_harf_tr(kisa), "ondalik": ondalik,
                    "silindi": False, "silindi_at": None,
                },
            )
            eklenen += int(olusturuldu)
            guncellenen += int(not olusturuldu)
        toplam = Birim.objects.filter(silindi=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Birim seed tamam: {eklenen} eklendi, {guncellenen} güncellendi, "
            f"toplam {toplam} aktif birim."))
