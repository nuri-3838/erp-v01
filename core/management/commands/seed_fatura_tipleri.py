"""Başlangıç fatura tiplerini ekler/günceller (idempotent upsert; hiçbir şey silmez).

STOKLAR modülü. Ad'a göre upsert: tekrar çalıştırılabilir. 4 satış + 4 alış.
"""
from django.core.management.base import BaseCommand

from core.metin import buyuk_harf_tr
from core.models import FaturaTipi

# (ad, yön, sıra)
TIPLER = [
    ("SATIŞ FATURASI", "SATIS", 10),
    ("SATIŞ FATURASI-ALIŞ İADE", "SATIS", 20),
    ("SATIŞ FATURASI-İHRACAT", "SATIS", 30),
    ("SATIŞ FATURASI-İHRAÇ KAYITLI", "SATIS", 40),
    ("ALIŞ FATURASI", "ALIS", 50),
    ("ALIŞ FATURASI-SATIŞ İADE", "ALIS", 60),
    ("ALIŞ FATURASI-İHRAÇ KAYITLI", "ALIS", 70),
    ("ALIŞ FATURASI-GİDER", "ALIS", 80),
]


class Command(BaseCommand):
    help = "Başlangıç fatura tiplerini ekler (idempotent)."

    def handle(self, *args, **opts):
        eklenen = guncellenen = 0
        for ad, yon, sira in TIPLER:
            _, olusturuldu = FaturaTipi.objects.update_or_create(
                ad=buyuk_harf_tr(ad),
                defaults={
                    "yon": yon, "sira": sira, "aktif": True,
                    "silindi": False, "silindi_at": None,
                },
            )
            eklenen += int(olusturuldu)
            guncellenen += int(not olusturuldu)
        toplam = FaturaTipi.objects.filter(silindi=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Fatura tipi seed tamam: {eklenen} eklendi, {guncellenen} güncellendi, "
            f"toplam {toplam} aktif fatura tipi."))
