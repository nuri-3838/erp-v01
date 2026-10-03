"""Tek seferlik düzeltme: İRSALİYEYE bağlı alış faturalarının yanlışlıkla yazdığı (çifte)
stok girişlerini geri alır. Bug: ONAYLI irsaliyeli fatura düzenlenince stok hareketi bir
kez daha yazılıyordu (bkz. core.services.fatura.irsaliyeden_mi).

Varsayılan: DRY-RUN — yalnız listeler, hiçbir şey yazmaz. ``--uygula`` ile hareket_sil
üzerinden geri alır (FIFO katmanı da silinir). Katmanı kısmen tüketilmiş ya da silinince
eldeki miktarı negatife düşürecek hareketler ATLANIR ve raporlanır (elle bakılmalı).

    python manage.py irsaliyeli_fatura_stok_duzelt            # dry-run
    python manage.py irsaliyeli_fatura_stok_duzelt --uygula   # uygular
"""
from django.core.management.base import BaseCommand

from core.models import StokHareket, TeklifSiparis
from core.services.hareket import HareketHatasi, eldeki_miktar, hareket_sil


class Command(BaseCommand):
    help = ("Irsaliyeye bagli faturalarin yazdigi mukerrer stok girisini listeler/geri alir "
           "(varsayilan dry-run; --uygula ile gercek).")

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true",
                            help="Geri al (varsayilan: dry-run).")

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        hareketler = list(
            StokHareket.objects.filter(
                silindi=False, fatura_satir__fatura__kaynak_siparisler__belge_tur=
                TeklifSiparis.BelgeTur.IRSALIYE,
                fatura_satir__fatura__kaynak_siparisler__silindi=False)
            .select_related("stok", "depo", "fatura_satir__fatura")
            .order_by("fatura_satir__fatura_id", "stok__kod").distinct())
        if not hareketler:
            self.stdout.write(self.style.SUCCESS("Mükerrer stok hareketi yok."))
            return
        self.stdout.write(f"{'Fatura':<8} {'Fatura No':<20} {'Stok':<14} {'Mükerrer':>12} "
                          f"{'Mevcut':>12} {'Sonra':>12} {'Tarih':<11} Durum")
        uygulanacak, atlanan = [], 0
        for h in hareketler:
            katman = getattr(h, "maliyet_katmani", None)
            neden = ""
            if h.tur != StokHareket.Tur.GIRIS:
                neden = "giriş değil, elle bak"
            elif katman is not None and not katman.silindi and katman.kalan_miktar != katman.giris_miktar:
                neden = "katman kısmen tüketilmiş"
            elif eldeki_miktar(h.stok, h.depo) - h.miktar < 0:
                neden = "eldeki negatife düşer"
            f = h.fatura_satir.fatura
            mevcut = eldeki_miktar(h.stok, h.depo)
            self.stdout.write(f"{f.pk:<8} {f.fatura_no:<20} {h.stok.kod:<14} {h.miktar:>12} "
                              f"{mevcut:>12} {mevcut - h.miktar:>12} {h.tarih:%d.%m.%Y} "
                              f"{'ATLANIR: ' + neden if neden else 'geri alınır'}")
            if neden:
                atlanan += 1
            else:
                uygulanacak.append(h)
        self.stdout.write(f"\nToplam {len(hareketler)}: {len(uygulanacak)} geri alınacak, "
                          f"{atlanan} atlanacak.")
        if not uygula:
            self.stdout.write(self.style.WARNING("DRY-RUN: --uygula ile uygulayın."))
            return
        for h in uygulanacak:
            try:
                hareket_sil(h)
            except HareketHatasi as e:
                self.stderr.write(f"hareket {h.pk} silinemedi: {e}")
        self.stdout.write(self.style.SUCCESS(f"{len(uygulanacak)} hareket geri alındı."))
