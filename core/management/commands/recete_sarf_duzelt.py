"""REÇETE EKSİK SARF DÜZELTMESİ (bkz. core.services.recete_sarf): tanımı sonradan düzeltilen bir girdinin geçmiş onaylı kayıtlarda eksik tüketilen
miktarını bugün tarihli çıkışlarla düşer, maliyetini oran bazında (stokta kalan → kart maliyeti, satılan → 620, numune → 760.06, ara mamul → kart)
dağıtır ve TEK fiş keser (kaynak STOK_DUZELTME). Varsayılan KURU çalıştırma (geri alınır); ``--uygula`` kalıcı yazar.

    python manage.py recete_sarf_duzelt                                   # 151-10-0042 kayış, HAMMADDE modu 150-40-0006 (kesim reçetesi), bugün, kuru
    python manage.py recete_sarf_duzelt --girdi 151-10-0042 --hammadde - --operasyon 77 --tarih 12.10.2026 --aciklama "..." [--uygula]   # girdiden sarf
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from core.services import recete_sarf

VARSAYILAN_GIRDI = "151-10-0042"
VARSAYILAN_HAMMADDE = "150-40-0006"
VARSAYILAN_ACIKLAMA = "A 5+1/6+1 kayış reçete düzeltmesi – kayıtsız kesim hammadde sarfı"


class Command(BaseCommand):
    help = "Recete duzeltmesi sonrasi gecmis kayitlardaki eksik girdi sarfini duser, maliyeti oranla dagitir, tek fis keser (varsayilan kuru)."

    def add_arguments(self, parser):
        parser.add_argument("--girdi", default=VARSAYILAN_GIRDI, help="eksik tüketilen girdi stok kodu")
        parser.add_argument("--hammadde", default=VARSAYILAN_HAMMADDE,
                            help="HAMMADDE MODU: girdi stoğuna dokunma, sarfı girdiyi üreten reçete oranıyla bu hammaddeden düş; '-' = girdiden sarf")
        parser.add_argument("--operasyon", action="append", type=int, default=[], help="yalnız bu operasyon tanımları (pk); tekrarlanabilir")
        parser.add_argument("--tarih", default=None, help="düzeltme tarihi gg.aa.yyyy (varsayılan bugün)")
        parser.add_argument("--aciklama", default=VARSAYILAN_ACIKLAMA)
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        if opts["tarih"]:
            try:
                tarih = datetime.datetime.strptime(opts["tarih"], "%d.%m.%Y").date()
            except ValueError:
                raise CommandError(f"--tarih gg.aa.yyyy olmalı: {opts['tarih']}")
        else:
            tarih = datetime.date.today()
        try:
            hammadde = opts["hammadde"].strip()
            rapor = recete_sarf.duzelt(opts["girdi"], tarih=tarih, aciklama=opts["aciklama"], operasyon_idler=opts["operasyon"] or None,
                                       hammadde_kodu=None if hammadde in ("", "-") else hammadde, uygula=opts["uygula"])
        except recete_sarf.ReceteDuzeltHatasi as e:
            raise CommandError(str(e))
        self.stdout.write(recete_sarf.rapor_metni(rapor, girdi_kodu=opts["girdi"]))
