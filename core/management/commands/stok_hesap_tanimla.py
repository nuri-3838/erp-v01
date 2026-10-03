"""Tek seferlik: stok muhasebe hesabı tanımları (Dilim B önkoşulu).

1. Hesap planında 623 (DİĞER SATIŞLARIN MALİYETİ) yoksa 621'in rapor ayarlarıyla açar
   (150/151 stoğu satılınca satış maliyeti fişi bu hesaba yazılır).
2. YARI MAMULLER ve MAMULLER alt kategorilerini ALIŞ FATURASI tipi altında 151.xx / 152.xx stok
   hesaplarına bağlar (üretim aktarım fişi, sarf ve satış maliyeti fişi stok hesabını buradan bulur).
   Zaten doğru bağlı olanlara dokunmaz; farklı bir 15x hesabına bağlı olanı DEĞİŞTİRMEZ, raporlar.
3. Fatura tiplerinin ``maliyet_fisi`` ayarı: satış tipleri → SATIS, satış iadesi tipi → SATIS_IADE
   (alış iadesi dahil diğerleri fişsiz kalır).

Varsayılan DRY-RUN (transaction geri alınır). ``--uygula`` kalıcı yazar.

    python manage.py stok_hesap_tanimla [--uygula]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import FaturaTipi, HesapPlani, Kategori, KategoriHesap, Stok
from core.services import kategori as kategori_servis

ESLEMELER = (
    ("YARI MAMULLER", "KESİLMİŞ PARÇALAR", "151.10"),
    ("YARI MAMULLER", "BÜKÜLMÜŞ PARÇALAR", "151.20"),
    ("YARI MAMULLER", "DARALTILMIŞ PARÇALAR", "151.30"),
    ("YARI MAMULLER", "ÖN AYAK", "151.80"),
    ("YARI MAMULLER", "ARKA AYAK", "151.90"),
    ("MAMULLER", "ALÜMİNYUM MERDİVEN - A TİPİ", "152.10"),
    ("MAMULLER", "ALÜMİNYUM MERDİVEN - ÇİFT ÇIKIŞ", "152.20"),
)
STOK_ALIS_TIPI = "ALIŞ FATURASI"
TIP_MALIYET_FISI = (
    ("SATIŞ FATURASI", FaturaTipi.MaliyetFisi.SATIS),
    ("SATIŞ FATURASI-İHRACAT", FaturaTipi.MaliyetFisi.SATIS),
    ("SATIŞ FATURASI-İHRAÇ KAYITLI", FaturaTipi.MaliyetFisi.SATIS),
    ("ALIŞ FATURASI-SATIŞ İADE", FaturaTipi.MaliyetFisi.SATIS_IADE),
)


class Command(BaseCommand):
    help = "Stok hesap tanimlari: 623, yari mamul/mamul kategori eslemesi, fatura tipi maliyet fisi."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        satirlar = []
        with transaction.atomic():
            self._623(satirlar)
            self._kategoriler(satirlar)
            self._tipler(satirlar)
            if not uygula:
                transaction.set_rollback(True)
        w = self.stdout.write
        w(f"{'UYGULANDI' if uygula else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'}\n")
        for s in satirlar:
            w("  " + s)
        if not uygula:
            w(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))

    def _623(self, satirlar):
        if HesapPlani.objects.filter(hesap_kodu="623", silindi=False).exists():
            satirlar.append("623: zaten var")
            return
        ornek = HesapPlani.objects.filter(hesap_kodu="621", silindi=False).first()
        if ornek is None:
            raise CommandError("621 hesabı yok; 623 açılamaz.")
        HesapPlani.objects.update_or_create(
            hesap_kodu="623", defaults={
                "hesap_adi": "DİĞER SATIŞLARIN MALİYETİ (-)", "rapor_grubu": ornek.rapor_grubu,
                "rapor_kalemi": ornek.rapor_kalemi, "parasal": ornek.parasal,
                "aktif": ornek.aktif, "silindi": False, "silindi_at": None})
        satirlar.append("623: AÇILDI (621'in rapor ayarlarıyla)")

    def _kategoriler(self, satirlar):
        tip = FaturaTipi.objects.filter(ad=STOK_ALIS_TIPI, silindi=False).first()
        if tip is None:
            raise CommandError(f"'{STOK_ALIS_TIPI}' fatura tipi bulunamadı.")
        for ust_ad, alt_ad, hesap_kodu in ESLEMELER:
            kat = Kategori.objects.filter(
                silindi=False, ad__iexact=buyuk_harf_tr(alt_ad),
                ust__ad__iexact=buyuk_harf_tr(ust_ad)).first() or Kategori.objects.filter(
                silindi=False, ad=alt_ad, ust__ad=ust_ad).first()
            if kat is None:
                satirlar.append(f"{ust_ad} > {alt_ad}: KATEGORİ BULUNAMADI")
                continue
            mevcut = (KategoriHesap.objects.filter(kategori=kat, fatura_tipi=tip, silindi=False)
                      .select_related("hesap").first())
            if mevcut is not None and mevcut.hesap_id == hesap_kodu:
                satirlar.append(f"{ust_ad} > {alt_ad}: zaten {hesap_kodu}")
                continue
            if mevcut is not None:
                satirlar.append(f"{ust_ad} > {alt_ad}: FARKLI HESABA BAĞLI ({mevcut.hesap_id}); "
                                f"DEĞİŞTİRİLMEDİ (istenen {hesap_kodu})")
                continue
            kategori_servis.kategori_hesaplari_kaydet(kat, eslesmeler={tip.pk: hesap_kodu})
            n = Stok.objects.filter(kategori=kat, silindi=False).count()
            satirlar.append(f"{ust_ad} > {alt_ad}: {STOK_ALIS_TIPI} -> {hesap_kodu} TANIMLANDI ({n} kart)")

    def _tipler(self, satirlar):
        for ad, mod in TIP_MALIYET_FISI:
            t = FaturaTipi.objects.filter(ad=ad, silindi=False).first()
            if t is None:
                satirlar.append(f"fatura tipi '{ad}': BULUNAMADI")
            elif t.maliyet_fisi == mod:
                satirlar.append(f"fatura tipi '{ad}': zaten {mod}")
            else:
                t.maliyet_fisi = mod
                t.save(update_fields=["maliyet_fisi", "updated_at"])
                satirlar.append(f"fatura tipi '{ad}': maliyet fişi = {mod}")
