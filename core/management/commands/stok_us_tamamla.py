"""Canlı veri düzeltmesi: %100 olmuş ama kapanmamış KAYNAK SİPARİŞSİZ ("stok için üretim") üretim siparişlerini TAMAMLANDI yapar ve tüm ayırmalarını serbest bırakır
(servis: ``uretim.stok_us_tamamla`` — onayda otomatik çalışan kuralın aynısı). Varsayılan KURU ÇALIŞTIRMA (her şey yazılır ve geri alınır; sonuçlar raporlanır).
Siparişe bağlı ÜS / %100 olmayan ÜS reddedilir. Kullanım: stok_us_tamamla --emir ÜS-2026-0003 --emir ÜS-2026-0004 [--uygula]"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Stok, UretimEmri
from core.services import stok_ayirma, uretim
from core.services.hareket import eldeki_miktar


class _Geri(Exception):
    pass


class Command(BaseCommand):
    help = "Yüzde 100 stok ÜS'lerini Tamamlandı yapar ve ayırmalarını serbest bırakır (varsayılan kuru çalıştırma)."

    def add_arguments(self, parser):
        parser.add_argument("--emir", action="append", required=True, dest="emirler")
        parser.add_argument("--uygula", action="store_true")

    def serbest_tablo(self, stoklar):
        return {s.kod: (eldeki_miktar(s), stok_ayirma.kullanilabilir(s)) for s in stoklar}

    def handle(self, *args, **opts):
        w = self.stdout.write
        emirler = []
        for no in opts["emirler"]:
            e = UretimEmri.objects.filter(no=no, silindi=False).first()
            if e is None:
                raise CommandError(f"Üretim siparişi bulunamadı: {no}")
            if not uretim.stok_us_mu(e):
                raise CommandError(f"{no}: kaynak satış siparişi var; bu komut yalnız 'stok için üretim' ÜS'leri içindir.")
            emirler.append(e)
        ilgili = Stok.objects.filter(pk__in=stok_ayirma.acik_ayirmalar().values_list("stok_id", flat=True)).order_by("kod")
        ilgili = list(ilgili)
        once = self.serbest_tablo(ilgili)
        try:
            with transaction.atomic():
                for e in emirler:
                    ilerleme = uretim.uretim_emri_ilerleme(e)
                    w(f"\n{e.no} durum={e.durum} ilerleme {ilerleme['onayli']}/{ilerleme['toplam']} (%{uretim.uretim_emri_yuzde(e):.0f})")
                    if e.durum != UretimEmri.Durum.ACIK:
                        w("  zaten açık değil — atlandı")
                        continue
                    for a in stok_ayirma.emir_ayirmalari(e):
                        w(f"  serbest bırakılacak ayırma: {a.stok.kod} {a.miktar.normalize():f}")
                    if not uretim.stok_us_tamamla(e):
                        raise CommandError(f"{e.no}: %100 değil (tüm istasyon emirleri bitmemiş); tamamlanmadı — hiçbir şey yazılmadı.")
                    w("  → TAMAMLANDI")
                sonra = self.serbest_tablo(ilgili)
                w("\nSERBEST STOK (eldeki / serbest)   önce → sonra")
                for kod in sorted(once):
                    (e1, s1), (e2, s2) = once[kod], sonra[kod]
                    if s1 != s2 or kod.startswith("152"):
                        w(f"  {kod:<14} eldeki {e1.normalize():f} | serbest {s1.normalize():f} → {s2.normalize():f}")
                negatif = [k for k, (_, s) in sonra.items() if s < Decimal("0")]
                if negatif:
                    raise CommandError(f"Düzeltme sonrası negatif serbest stok kalıyor: {negatif} — hiçbir şey yazılmadı.")
                if not opts["uygula"]:
                    raise _Geri()
        except _Geri:
            w(self.style.WARNING("\nKURU ÇALIŞTIRMA: geri alındı. Uygulamak için --uygula."))
            return
        w(self.style.SUCCESS("\nUYGULANDI."))
