"""Dönemsel gider (180) aylık dağıtım fişlerini "bu tarihe kadar" üretir (varsayılan: bugün). İdempotent; gelecek aylar için fiş üretmez.

    python manage.py donemsel_dagitim_olustur [--tarih YYYY-MM-DD]
"""
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.services import donemsel_gider


class Command(BaseCommand):
    help = "Donemsel gider (180) aylik dagitim fislerini uretir (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--tarih", help="Bu tarihe kadar (YYYY-MM-DD); varsayılan bugün")

    def handle(self, *args, **opts):
        try:
            tarih = datetime.date.fromisoformat(opts["tarih"]) if opts.get("tarih") else timezone.localdate()
        except ValueError:
            raise CommandError("Tarih YYYY-MM-DD olmalı.")
        try:
            n = donemsel_gider.uret(tarih)
        except donemsel_gider.DonemselGiderHatasi as e:
            raise CommandError(str(e))
        self.stdout.write(f"{tarih:%d.%m.%Y} tarihine kadar {n} dönemsel dağıtım fişi oluşturuldu.")
