"""GEÇMİŞ MENTEŞE TÜKETİMİNİ BOYALI MENTEŞEYE ÇEVİRME (bkz. core.services.mentese_boyali): 25.09.2026'dan itibaren ham menteşeyi (151-20-0014/0015) tüketen
tüm onaylı montaj kayıtları boyalı menteşeye (151-20-0016/0017) çevrilir; her tarih için ham menteşe fason deposuna taşınır ve fasoncuya bağlı fason dönüş
belgesi (faturasız fasoncuda tahakkuk fişiyle) açılır; maliyetler baştan hesaplanır. Varsayılan KURU çalıştırma (geri alınır, rapor gerçek numaralarla çıkar);
``--uygula`` kalıcı yazar. İdempotent: çevrilmiş kayıtlar yeniden aday olmaz.

    python manage.py mentese_boyali_gecis                       # kuru, 25.09.2026'dan itibaren
    python manage.py mentese_boyali_gecis --baslangic 25.09.2026 --uygula
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from core.services import mentese_boyali


class Command(BaseCommand):
    help = "Gecmis ham mentese tuketimini boyali menteseye cevirir (fason donus + tahakkuk); varsayilan kuru."

    def add_arguments(self, parser):
        parser.add_argument("--baslangic", default=mentese_boyali.VARSAYILAN_BASLANGIC.strftime("%d.%m.%Y"), help="bu tarih ve sonrası (gg.aa.yyyy)")
        parser.add_argument("--uygula", action="store_true", help="kalıcı yaz (verilmezse kuru çalıştırma, geri alınır)")

    def handle(self, *args, **opts):
        try:
            baslangic = datetime.datetime.strptime(opts["baslangic"], "%d.%m.%Y").date()
        except ValueError:
            raise CommandError(f"--baslangic gg.aa.yyyy olmalı: {opts['baslangic']}")
        try:
            rapor = mentese_boyali.gecis(baslangic=baslangic, uygula=opts["uygula"])
        except mentese_boyali.MenteseGecisHatasi as e:
            raise CommandError(str(e))
        self.stdout.write(mentese_boyali.rapor_metni(rapor))
