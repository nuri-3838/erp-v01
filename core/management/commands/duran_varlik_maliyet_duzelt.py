"""Tek seferlik düzeltme: döviz faturaya bağlı duran varlık kartlarının maliyetini TL
karşılığıyla (bkz. FaturaSatir.tutar_tl, core.services.duran_varlik.baglanti_toplami)
yeniden hesaplar. Bug: baglanti_toplami önceden ``tutar``'ı (döviz faturada fatura PB'si,
TL DEĞİL) doğrudan topluyordu — kart maliyeti döviz kalemlerde yanlış (düşük) çıkıyordu.

Varsayılan: DRY-RUN — hiçbir şey yazmaz, yalnız farkları listeler.
``--uygula`` ile gerçekten günceller.

Kullanım::

    python manage.py duran_varlik_maliyet_duzelt            # dry-run, listeler
    python manage.py duran_varlik_maliyet_duzelt --uygula   # uygular
"""
from django.core.management.base import BaseCommand

from core.models import DuranVarlik
from core.services.duran_varlik import baglanti_toplami


class Command(BaseCommand):
    help = ("Doviz faturaya bagli duran varlik kartlarinin maliyetini TL karsiligiyla "
           "yeniden hesaplar (varsayilan dry-run; --uygula ile gercek gunceller).")

    def add_arguments(self, parser):
        parser.add_argument(
            "--uygula", action="store_true",
            help="Degisiklikleri gercekten kaydet (varsayilan: dry-run, sadece listeler).",
        )

    def handle(self, *args, **options):
        uygula = options["uygula"]
        degisecek = []
        for v in (DuranVarlik.objects.filter(silindi=False)
                 .select_related("hesap").order_by("demirbas_kodu")):
            if not v.fatura_satirlari.filter(silindi=False, fatura__silindi=False).exists():
                continue
            yeni = baglanti_toplami(v)
            if yeni != v.maliyet:
                degisecek.append((v, v.maliyet, yeni))

        if not degisecek:
            self.stdout.write(self.style.SUCCESS(
                "Fark bulunamadı; fatura kalemine bağlı tüm kartların maliyeti güncel."))
            return

        self.stdout.write(f"{'Kart':<10} {'Hesap':<8} {'Eski Maliyet':>16} {'Yeni Maliyet':>16} {'Fark':>14}")
        for v, eski, yeni in degisecek:
            self.stdout.write(
                f"{v.demirbas_kodu:<10} {v.hesap.hesap_kodu:<8} "
                f"{eski:>16} {yeni:>16} {(yeni - eski):>14}"
            )

        if not uygula:
            self.stdout.write(self.style.WARNING(
                f"\nDRY-RUN: {len(degisecek)} kartın maliyeti değişecek. "
                "Uygulamak için --uygula ekleyin."
            ))
            return

        for v, eski, yeni in degisecek:
            v.maliyet = yeni
            v.save(update_fields=["maliyet", "updated_at"])
        self.stdout.write(self.style.SUCCESS(f"{len(degisecek)} kart güncellendi."))
