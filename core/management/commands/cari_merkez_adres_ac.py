"""Mevcut carilerin kendi adresini sevk adresleri listesine "Merkez Adres" olarak
otomatik + varsayılan açar (idempotent — zaten sevk adresi olan cari atlanır).

cari_olustur() artık yeni carilerde bunu otomatik yapıyor (bkz core/services/cari.py);
bu komut, o özellik eklenmeden ÖNCE oluşturulmuş carileri geriye dönük tamamlar.
"""
from django.core.management.base import BaseCommand

from core.models import Cari
from core.services import cari as C


class Command(BaseCommand):
    help = "Mevcut carilerin adresini Merkez Adres olarak sevk adreslerine açar (idempotent)."

    def handle(self, *args, **opts):
        acilan = atlanan = 0
        for c in Cari.objects.filter(silindi=False).order_by("kod"):
            if not (c.ulke_id or c.sehir_id or c.adres):
                atlanan += 1
                continue
            if c.sevk_adresleri.filter(silindi=False).exists():
                atlanan += 1
                continue
            C.sevk_adresi_ekle(c, ad="Merkez Adres", ulke_id=c.ulke_id, sehir_id=c.sehir_id,
                               adres=c.adres, varsayilan=True)
            acilan += 1
        self.stdout.write(self.style.SUCCESS(
            f"Merkez adres açma tamam: {acilan} cari işlendi, "
            f"{atlanan} atlandı (adressiz veya zaten sevk adresi var)."))
