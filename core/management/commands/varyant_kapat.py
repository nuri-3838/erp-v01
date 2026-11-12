"""VARYANTLI KARTI VARYANTSIZ YAPMA (bkz. core.services.varyant_kapat): yanlışlıkla varyantlı (MARKALAMA) açılmış kartların (varsayılan: CS ayak SOL, 12 kart) varyantı
kapatılır; mevcut hareketler/bakiyeler/maliyetler miktar ve tutar değişmeden varyantsız olur; SABİT kurallı operasyon girdileri TASI'ye alınır. Varsayılan KURU çalıştırma
(her şey yapılır, doğrulanır, GERİ ALINIR); ``--uygula`` kalıcı yazar ve denetim dökümü (logs/varyant_kapat_<tarih>.json: değişen hareket pk'leri) bırakır. İdempotent.

    python manage.py varyant_kapat                                   # 12 CS ayak SOL kartı, kuru
    python manage.py varyant_kapat --stok 151-10-0064 --stok ... [--uygula]
"""
from django.core.management.base import BaseCommand, CommandError

from core.services import varyant_kapat


class Command(BaseCommand):
    help = "Varyantli (yanlislikla acilmis) stok kartlarini varyantsiz yapar (varsayilan kuru; --uygula kalici)."

    def add_arguments(self, parser):
        parser.add_argument("--stok", action="append", default=[], help="stok kodu; tekrarlanabilir (verilmezse 12 CS ayak SOL kartı)")
        parser.add_argument("--uygula", action="store_true", help="kalıcı yaz (verilmezse kuru çalıştırma, geri alınır)")

    def handle(self, *args, **opts):
        kodlar = opts["stok"] or list(varyant_kapat.VARSAYILAN_KODLAR)
        try:
            rapor = varyant_kapat.kapat(kodlar, uygula=opts["uygula"])
        except varyant_kapat.VaryantKapatHatasi as e:
            raise CommandError(str(e))
        self.stdout.write(varyant_kapat.rapor_metni(rapor))
