"""Tek seferlik: stok muhasebe hesabı tanımları (Dilim B önkoşulu).

1. Hesap planında 623 (DİĞER SATIŞLARIN MALİYETİ) yoksa 621'in rapor ayarlarıyla açar
   (150/151 stoğu satılınca satış maliyeti fişi bu hesaba yazılır).
2. YARI MAMULLER ve MAMULLER alt kategorilerini ALIŞ FATURASI tipi altında 151.xx / 152.xx stok
   hesaplarına bağlar (üretim aktarım fişi, sarf ve satış maliyeti fişi stok hesabını buradan bulur).
   Zaten doğru bağlı olanlara dokunmaz. Farklı bir hesaba bağlı olanı, yalnız ``yeniden_bagla``
   işaretliyse (PLASTİK PARÇALAR: 150.30 → 151.40) değiştirir; aksi halde DEĞİŞTİRMEZ, raporlar.
3. (YALNIZ ``--virman`` ile; varsayılan KAPALI) PLASTİK PARÇALAR için bugüne kadar 150.30'a
   işlenmiş bakiyeyi VİRMAN fişiyle 151.40'a taşır
   (151.40 BORÇ / 150.30 ALACAK). Tutar = bu kategorideki kartların stok değeri toplamı (ağırlıklı
   ortalama maliyet önbelleği). 150.30'da kart değeri dışında bakiye veya başka kategori varsa AYRICA
   listelenir. Fiş tarihi ``--virman-tarih`` (varsayılan: bugüne kadarki son USD kuru tarihi — fişin
   USD karşılığı için o tarihe ait kur kaydı gerekir). Aynı açıklamalı fiş varsa tekrar yazmaz.
4. Fatura tiplerinin ``maliyet_fisi`` ayarı: satış tipleri → SATIS, satış iadesi tipi → SATIS_IADE,
   alış iadesi tipi → ALIS_IADE (fişsiz; stok çıkışı iade faturası tutarıyla değerlenir).

Varsayılan DRY-RUN (transaction geri alınır). ``--uygula`` kalıcı yazar.

    python manage.py stok_hesap_tanimla [--virman [--virman-tarih 2026-10-03]] [--uygula]
"""
import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import (FaturaTipi, HesapPlani, Kategori, KategoriHesap, Kur, Stok, StokHareket,
                         YevmiyeFisi)
from core.services import kategori as kategori_servis
from core.services import stok_ortalama
from core.services.yevmiye import SatirGirdi, fis_olustur

SIFIR = Decimal("0.00")
ESLEMELER = (
    # (üst kategori, alt kategori, hesap, mevcut farklı bağı değiştir?)
    ("YARI MAMULLER", "KESİLMİŞ PARÇALAR", "151.10", False),
    ("YARI MAMULLER", "BÜKÜLMÜŞ PARÇALAR", "151.20", False),
    ("YARI MAMULLER", "DARALTILMIŞ PARÇALAR", "151.30", False),
    ("YARI MAMULLER", "PLASTİK PARÇALAR", "151.40", True),
    ("YARI MAMULLER", "ÖN AYAK", "151.80", False),
    ("YARI MAMULLER", "ARKA AYAK", "151.90", False),
    ("MAMULLER", "ALÜMİNYUM MERDİVEN - A TİPİ", "152.10", False),
    ("MAMULLER", "ALÜMİNYUM MERDİVEN - ÇİFT ÇIKIŞ", "152.20", False),
)
STOK_ALIS_TIPI = "ALIŞ FATURASI"
TIP_MALIYET_FISI = (
    ("SATIŞ FATURASI", FaturaTipi.MaliyetFisi.SATIS),
    ("SATIŞ FATURASI-İHRACAT", FaturaTipi.MaliyetFisi.SATIS),
    ("SATIŞ FATURASI-İHRAÇ KAYITLI", FaturaTipi.MaliyetFisi.SATIS),
    ("ALIŞ FATURASI-SATIŞ İADE", FaturaTipi.MaliyetFisi.SATIS_IADE),
    ("SATIŞ FATURASI-ALIŞ İADE", FaturaTipi.MaliyetFisi.ALIS_IADE),
)
VIRMAN_KATEGORI = ("YARI MAMULLER", "PLASTİK PARÇALAR")
VIRMAN_ESKI, VIRMAN_YENI = "150.30", "151.40"
VIRMAN_ACIKLAMA = "PLASTİK PARÇALAR STOK VİRMANI (150.30 → 151.40)"


def _kategori_bul(ust_ad, alt_ad):
    return (Kategori.objects.filter(silindi=False, ad__iexact=buyuk_harf_tr(alt_ad),
                                    ust__ad__iexact=buyuk_harf_tr(ust_ad)).first()
            or Kategori.objects.filter(silindi=False, ad=alt_ad, ust__ad=ust_ad).first())


class Command(BaseCommand):
    help = "Stok hesap tanimlari: 623, kategori eslemesi, plastik virmani, fatura tipi maliyet fisi."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true")
        parser.add_argument("--virman", action="store_true",
                            help="Plastik bakiyesini virmanla 151.40'a tasi (varsayilan: kapali)")
        parser.add_argument("--virman-tarih", default=None, help="YYYY-AA-GG (varsayilan: son kur tarihi)")

    def handle(self, *args, **opts):
        uygula = opts["uygula"]
        try:
            tarih = datetime.date.fromisoformat(opts["virman_tarih"]) if opts["virman_tarih"] else None
        except ValueError as e:
            raise CommandError(str(e))
        satirlar = []
        with transaction.atomic():
            self._623(satirlar)
            self._kategoriler(satirlar)
            self._tipler(satirlar)
            if opts["virman"]:
                self._virman(satirlar, tarih)
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
        for ust_ad, alt_ad, hesap_kodu, yeniden_bagla in ESLEMELER:
            kat = _kategori_bul(ust_ad, alt_ad)
            if kat is None:
                satirlar.append(f"{ust_ad} > {alt_ad}: KATEGORİ BULUNAMADI")
                continue
            mevcut = (KategoriHesap.objects.filter(kategori=kat, fatura_tipi=tip, silindi=False)
                      .select_related("hesap").first())
            n = Stok.objects.filter(kategori=kat, silindi=False).count()
            if mevcut is not None and mevcut.hesap_id == hesap_kodu:
                satirlar.append(f"{ust_ad} > {alt_ad}: zaten {hesap_kodu}")
            elif mevcut is not None and not yeniden_bagla:
                satirlar.append(f"{ust_ad} > {alt_ad}: FARKLI HESABA BAĞLI ({mevcut.hesap_id}); "
                                f"DEĞİŞTİRİLMEDİ (istenen {hesap_kodu})")
            else:
                kategori_servis.kategori_hesaplari_kaydet(kat, eslesmeler={tip.pk: hesap_kodu})
                eski = f" (eski {mevcut.hesap_id})" if mevcut is not None else ""
                satirlar.append(f"{ust_ad} > {alt_ad}: {STOK_ALIS_TIPI} -> {hesap_kodu} "
                                f"TANIMLANDI{eski} ({n} kart)")

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

    def _virman(self, satirlar, tarih):
        kat = _kategori_bul(*VIRMAN_KATEGORI)
        if kat is None:
            satirlar.append("VİRMAN: PLASTİK PARÇALAR kategorisi bulunamadı; atlandı")
            return
        if YevmiyeFisi.objects.filter(aciklama=VIRMAN_ACIKLAMA, silindi=False).exists():
            satirlar.append("VİRMAN: zaten yapılmış (aynı açıklamalı fiş var)")
            return
        kartlar = list(Stok.objects.filter(kategori=kat, silindi=False))
        tutar = sum((s.maliyet_deger_try for s in kartlar), SIFIR)
        gecici = StokHareket.objects.filter(
            stok__in=kartlar, silindi=False).exclude(
            maliyet_durumu=StokHareket.MaliyetDurumu.KESIN).count()
        mizan = stok_ortalama._mizan_bakiyesi(VIRMAN_ESKI)
        satirlar.append(f"VİRMAN: {len(kartlar)} kart, stok değeri toplamı {tutar:,.2f} TL; "
                        f"{VIRMAN_ESKI} mizan bakiyesi {mizan:,.2f} TL")
        if mizan != tutar:
            satirlar.append(f"  UYARI: {VIRMAN_ESKI}'da kart değeri dışında {mizan - tutar:,.2f} TL "
                            "bakiye var (virmana dahil DEĞİL)")
        digerleri = (KategoriHesap.objects.filter(silindi=False, hesap__hesap_kodu__startswith=VIRMAN_ESKI)
                     .exclude(kategori=kat).select_related("kategori", "fatura_tipi"))
        for kh in digerleri:
            n = Stok.objects.filter(kategori=kh.kategori, silindi=False).count()
            satirlar.append(f"  {VIRMAN_ESKI}'a bağlı DİĞER kategori: {kh.kategori.ad} "
                            f"({kh.fatura_tipi.ad}, {n} kart)")
        if gecici:
            satirlar.append(f"  UYARI: bu kartlarda kesin olmayan (geçici/maliyetsiz) {gecici} hareket var")
        if tutar <= 0:
            satirlar.append("VİRMAN: tutar sıfır; fiş yazılmadı")
            return
        if tarih is None:
            son = Kur.objects.filter(silindi=False, usd_alis__isnull=False,
                                     tarih__lte=datetime.date.today()).order_by("-tarih").first()
            if son is None:
                raise CommandError("USD kuru kaydı yok; --virman-tarih verin.")
            tarih = son.tarih
        fis = fis_olustur(
            tarih=tarih, aciklama=VIRMAN_ACIKLAMA, kaynak=YevmiyeFisi.Kaynak.MANUEL,
            satirlar=[SatirGirdi(VIRMAN_YENI, "B", tutar), SatirGirdi(VIRMAN_ESKI, "A", tutar)])
        satirlar.append(f"VİRMAN FİŞİ: {fis.yil}/{fis.fis_no} — tarih {tarih:%d.%m.%Y} — "
                        f"{VIRMAN_YENI} BORÇ / {VIRMAN_ESKI} ALACAK — {tutar:,.2f} TL")
