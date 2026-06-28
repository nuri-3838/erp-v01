"""Eski sistemden (semta_erp) banka logolarını HAVUZA taşır + isimle eşleşen
mevcut bankalara atar. Havuz `banka_logo/_havuz/` altında; logosuz banka açılınca
finans._havuz_logo bunu otomatik kullanır (Vakıf vb. "ileriye hazır").

Eski sisteme YALNIZ OKUMA. İdempotent: logosu olan banka atlanır (--force ezer)."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.gorsel import kucult_webp
from core.models import Banka
from core.services.finans import _havuz_logo

# havuz dosya adı -> eski sistem dosya adı
KAYNAK_LOGO = {
    "ziraat.webp": "Ziraat-Logo.png",
    "halk.webp": "Halkbankası-Logo.png",
    "vakif.webp": "Vakıfbankası-Logo.png",
}


class Command(BaseCommand):
    help = "Eski banka logolarını havuza taşır + isimle eşleşen bankalara atar."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kaynak", default="/home/nuri/semta_erp/repo/media/finans/banka_logo",
            help="Eski logoların bulunduğu dizin (eski sisteme yalnız okuma).")
        parser.add_argument("--force", action="store_true",
                            help="Logosu olan bankaları da yeniden ata.")

    def handle(self, *args, **o):
        kaynak = Path(o["kaynak"])
        havuz = Path(settings.MEDIA_ROOT) / "banka_logo" / "_havuz"
        havuz.mkdir(parents=True, exist_ok=True)
        n_havuz = 0
        for hedef, eski in KAYNAK_LOGO.items():
            src = kaynak / eski
            if not src.exists():
                self.stdout.write(f"  atla (kaynak yok): {src}")
                continue
            cf = kucult_webp(src, max_kenar=512, ad=hedef[:-5])
            (havuz / hedef).write_bytes(cf.read())
            n_havuz += 1
            self.stdout.write(f"  havuz: {hedef}")
        n_ata = 0
        for b in Banka.objects.filter(silindi=False):
            if b.logo and not o["force"]:
                continue
            cf = _havuz_logo(b.ad)
            if cf:
                b.logo = cf
                b.save(update_fields=["logo", "updated_at"])
                n_ata += 1
                self.stdout.write(f"  ata: {b.ad} <- {cf.name}")
        self.stdout.write(self.style.SUCCESS(
            f"tasi_banka_logo tamam: {n_havuz} havuz, {n_ata} banka atandı."))
