"""Gecelik yerel yedeği ŞİFRELEYİP Google Drive "ERP_YEDEK" klasörüne yükler, saklama kuralını (30 gün günlük + 12 hafta haftalık) uygular — bkz. core.services.drive_yedek ve docs/drive-yedek.md.

    python manage.py drive_yedek                 # yükle + sakla (cron)
    python manage.py drive_yedek --kuru          # ne yapılacağını listele, hiçbir şey yüklenmez/silinmez
    python manage.py drive_yedek --sadece-sakla  # yalnız saklama kuralı
Hata olursa log'a (logs/drive_yedek.log) yazılır, çıkış kodu 1 olur; yüklenemeyen takım sonraki çalışmada tekrar denenir."""
import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services import drive_yedek


class Command(BaseCommand):
    help = "Yerel yedeği şifreleyip Google Drive'a yükler ve saklama kuralını uygular."

    def add_arguments(self, parser):
        parser.add_argument("--kuru", action="store_true", help="Hiçbir şey yükleme/silme; yapılacakları listele.")
        parser.add_argument("--sadece-yukle", action="store_true")
        parser.add_argument("--sadece-sakla", action="store_true")
        parser.add_argument("--bugun", help="Saklama hesabında 'bugün' (YYYY-AA-GG) — yalnız test/prova için.")

    def handle(self, *args, **o):
        ayar = drive_yedek.Ayar.ortamdan(settings.BASE_DIR)
        bugun = datetime.date.fromisoformat(o["bugun"]) if o.get("bugun") else None
        sonuc = drive_yedek.calistir(ayar, bugun, yukle_=not o["sadece_sakla"], sakla=not o["sadece_yukle"], kuru=o["kuru"])
        self.stdout.write(f"Yüklenen: {len(sonuc['yuklenen'])} {sonuc['yuklenen'][:12]}")
        self.stdout.write(f"Silinen (saklama dışı): {len(sonuc['silinen'])} {sonuc['silinen'][:12]}")
        if sonuc["hatalar"]:
            for h in sonuc["hatalar"]:
                self.stderr.write(f"HATA: {h}")
            raise CommandError(f"{len(sonuc['hatalar'])} hata (ayrıntı: {ayar.log_dosyasi}); sonraki çalışmada tekrar denenir.")
        self.stdout.write(self.style.SUCCESS("Tamam."))
