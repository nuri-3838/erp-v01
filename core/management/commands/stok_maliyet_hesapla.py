"""Tek seferlik: mevcut stok hareketlerini hareketli AĞIRLIKLI ORTALAMA maliyetle işler.

1. 2026 başından (``--baslangic``) itibaren GİRİŞ hareketlerine FATURA tutarını atar:
   - doğrudan fatura girişi: fatura satır tutarı (KDV/tevkifat hariç, TL) + USD karşılığı;
   - irsaliye girişi: bağlı ONAYLI faturanın satır tutarları (stok bazında, miktar oranında).
   Faturası olmayan/taslak irsaliye girişleri FİYATSIZ (GEÇİCİ/maliyetsiz) kalır ve raporlanır.
2. Tüm kartları kronolojik yeniden hesaplar (hareket sonuçları + Stok önbelleği).
3. Raporlar: fatura bazında tutar karşılaştırması, maliyetsiz/geçici hareketler, çevirici eksik
   kartlar, kategori muhasebe hesabı tanımsız kartlar, 150-153 mizan karşılaştırması.

Varsayılan DRY-RUN: işlem bir transaction içinde gerçekten yapılır, rapor alınır ve GERİ ALINIR
(kalıcı hiçbir şey yazılmaz). ``--uygula`` aynı işlemi kalıcı yapar. Yalnız StokHareket maliyet
alanları ve Stok önbelleği yazılır; fatura/fiş kayıtlarına DOKUNULMAZ (sarf fişleri güncellenmez,
yalnız güncellenmesi gerekenler sayılır).

    python manage.py stok_maliyet_hesapla [--baslangic 2026-01-01] [--uygula]
"""
import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from core.models import Fatura, FaturaTipi, Stok, StokHareket
from core.services import kategori as kategori_servis
from core.services import stok_ortalama as so
from core.sayi import yuvarla

SIFIR = Decimal("0.00")


def _fatura_tutarlari(fatura):
    """{stok_id: Σ satır tutarı TL} — fatura satırlarında stoklu olanlar."""
    d = defaultdict(lambda: SIFIR)
    for s in fatura.satirlar.filter(silindi=False, stok__isnull=False):
        d[s.stok_id] += s.tutar_tl
    return d


class Command(BaseCommand):
    help = "Mevcut stok hareketlerini agirlikli ortalama maliyetle isler (varsayilan dry-run)."

    def add_arguments(self, parser):
        parser.add_argument("--baslangic", default="2026-01-01")
        parser.add_argument("--uygula", action="store_true")

    def handle(self, *args, **opts):
        try:
            baslangic = datetime.date.fromisoformat(opts["baslangic"])
        except ValueError as e:
            raise CommandError(str(e))
        uygula = opts["uygula"]
        rapor = {}
        with transaction.atomic():
            rapor = self._isle(baslangic)
            if not uygula:
                transaction.set_rollback(True)
        self._yaz(rapor, uygula, baslangic)

    # ------------------------------------------------------------------
    def _isle(self, baslangic):
        r = {"uyari": [], "fiyatlanan_dogrudan": 0, "fiyatlanan_irsaliye": 0}
        girisler = StokHareket.objects.filter(
            silindi=False, tur=StokHareket.Tur.GIRIS, tarih__gte=baslangic).select_related(
            "stok", "fatura_satir__fatura__fis", "teklif_siparis_kalem__teklif_siparis__fatura")
        irsaliyeli_faturalar = {}
        for h in girisler:
            if h.kaynak == StokHareket.Kaynak.FATURA and h.fatura_satir_id:
                f = h.fatura_satir.fatura
                kur_usd = f.fis.kur_usd if f.fis_id else None
                tl = h.fatura_satir.tutar_tl
                h.giris_tutar_try = tl
                h.giris_tutar_usd = yuvarla(tl / kur_usd, 2) if kur_usd else None
                h.maliyet_fatura_satir = h.fatura_satir
                h.save(update_fields=["giris_tutar_try", "giris_tutar_usd",
                                      "maliyet_fatura_satir", "updated_at"])
                r["fiyatlanan_dogrudan"] += 1
            elif h.kaynak == StokHareket.Kaynak.IRSALIYE and h.teklif_siparis_kalem_id:
                f = h.teklif_siparis_kalem.teklif_siparis.fatura
                if f is not None and not f.silindi and f.durum == Fatura.Durum.ONAYLI:
                    irsaliyeli_faturalar[f.pk] = f
        for f in irsaliyeli_faturalar.values():
            r["fiyatlanan_irsaliye"] += so.irsaliye_girislerini_fiyatla(f)

        degisen = so.tum_kartlari_yeniden_hesapla(fis_guncelle=False)
        r["kart_sayisi"] = len(degisen)
        r["guncellenmesi_gereken_cikis_fisi"] = sum(degisen.values())

        # --- fatura bazında karşılaştırma
        satirlar = []
        for f in (Fatura.objects.filter(silindi=False, durum=Fatura.Durum.ONAYLI,
                                        yon=FaturaTipi.Yon.ALIS, satirlar__stok__isnull=False)
                  .distinct().select_related("cari").order_by("pk")):
            beklenen = sum(_fatura_tutarlari(f).values(), SIFIR)
            hs = (StokHareket.objects.filter(silindi=False, tur=StokHareket.Tur.GIRIS)
                  .filter(maliyet_fatura_satir__fatura=f).distinct())
            fiyatli = sum((h.giris_tutar_try or SIFIR for h in hs), SIFIR)
            irsaliyeli = bool(so.irsaliye_girisleri(f))
            satirlar.append({"fatura": f, "beklenen": beklenen, "hareket": fiyatli,
                             "fark": beklenen - fiyatli, "irsaliyeli": irsaliyeli})
        r["faturalar"] = satirlar

        # --- fiyatsız/geçici/maliyetsiz hareketler (başlangıçtan beri)
        r["durumlar"] = Counter(
            (h.kaynak, h.tur, h.maliyet_durumu) for h in StokHareket.objects.filter(
                silindi=False, tarih__gte=baslangic))
        r["sorunlu"] = list(StokHareket.objects.filter(
            silindi=False, tarih__gte=baslangic).exclude(
            maliyet_durumu=StokHareket.MaliyetDurumu.KESIN).select_related(
            "stok", "teklif_siparis_kalem__teklif_siparis__fatura").order_by("tarih", "id"))
        r["sorunlu_neden"] = {}
        for h in r["sorunlu"]:
            if h.tur == StokHareket.Tur.CIKIS:
                neden = "çıkış: stokta fatura tutarlı değer yok / geçici giriş var"
            elif h.kaynak == StokHareket.Kaynak.IRSALIYE:
                f = h.teklif_siparis_kalem.teklif_siparis.fatura if h.teklif_siparis_kalem_id else None
                neden = ("irsaliyenin faturası yok" if f is None else
                         f"fatura {f.pk} {'taslak' if f.durum != Fatura.Durum.ONAYLI else 'onaylı ama bu stoğa satırı yok'}")
            elif h.kaynak == StokHareket.Kaynak.MANUEL:
                neden = "manuel giriş (fiyat yok)"
            else:
                neden = h.get_kaynak_display()
            r["sorunlu_neden"][h.pk] = neden

        # --- çevirici eksik kartlar
        r["cevirici_eksik"] = list(Stok.objects.filter(
            silindi=False, hareketler__silindi=False, cevirici=1).exclude(
            uretim_birimi=F("fatura_birimi")).distinct().select_related(
            "uretim_birimi", "fatura_birimi"))

        # --- kategori muhasebe hesabı tanımsız kartlar
        tanimsiz = []
        for s in Stok.objects.filter(silindi=False, hareketler__silindi=False).distinct() \
                .select_related("kategori"):
            try:
                kategori_servis.stok_muhasebe_hesabi(s)
            except Exception as e:                      # noqa: BLE001 — mesajı raporlamak için
                tanimsiz.append((s, str(e)))
        r["hesap_tanimsiz"] = tanimsiz
        r["degerleme"] = so.degerleme_raporu()
        return r

    # ------------------------------------------------------------------
    def _yaz(self, r, uygula, baslangic):
        w = self.stdout.write
        w(f"{'UYGULANDI' if uygula else 'DRY-RUN (geri alindi, kalici degisiklik YOK)'} — "
          f"{baslangic:%d.%m.%Y} ve sonrasi\n")
        w(f"Fatura tutariyla fiyatlanan giris: dogrudan fatura {r['fiyatlanan_dogrudan']}, "
          f"irsaliye {r['fiyatlanan_irsaliye']}; yeniden hesaplanan kart: {r['kart_sayisi']}")
        w(f"Guncellenmesi gereken sarf cikis fisi (bu komut fise DOKUNMAZ): "
          f"{r['guncellenmesi_gereken_cikis_fisi']}\n")

        w("FATURA BAZINDA (fatura TL = hareket TL olmali)")
        w(f"  {'Fatura':<7} {'No':<20} {'PB':<4} {'Fatura TL':>14} {'Hareket TL':>14} {'Fark':>14}  Not")
        for x in r["faturalar"]:
            f = x["fatura"]
            w(f"  {f.pk:<7} {f.fatura_no[:20]:<20} {f.para_birimi:<4} {x['beklenen']:>14,.2f} "
              f"{x['hareket']:>14,.2f} {x['fark']:>14,.2f}  "
              f"{'irsaliyeli' if x['irsaliyeli'] else ''}{'  <-- FARK' if x['fark'] else ''}")
        w("")
        w("HAREKET DURUMLARI (kaynak, tur, durum -> adet)")
        for (kaynak, tur, durum), n in sorted(r["durumlar"].items()):
            w(f"  {kaynak:<9} {tur:<6} {durum:<7} {n}")
        if r["sorunlu"]:
            w(f"\nMALIYETSIZ / GECICI HAREKETLER ({len(r['sorunlu'])})")
            for h in r["sorunlu"]:
                w(f"  hareket {h.pk} {h.tarih:%d.%m.%Y} {h.stok.kod} {h.tur} {h.miktar} "
                  f"[{h.maliyet_durumu}] {r['sorunlu_neden'][h.pk]}")
        else:
            w("\nMaliyetsiz/gecici hareket YOK.")
        w(f"\nCEVIRICI EKSIK KARTLAR (cevirici=1 ama uretim/fatura birimi farkli): "
          f"{len(r['cevirici_eksik'])}")
        for s in r["cevirici_eksik"]:
            w(f"  {s.kod} {s.ad[:40]} {s.uretim_birimi}/{s.fatura_birimi}")
        w(f"\nKATEGORI MUHASEBE HESABI TANIMSIZ KARTLAR (sarf/uretim/satis fisi icin gerekli): "
          f"{len(r['hesap_tanimsiz'])}")
        for s, mesaj in r["hesap_tanimsiz"]:
            w(f"  {s.kod} {s.ad[:35]} — kategori {s.kategori.ad}: {mesaj[:90]}")
        d = r["degerleme"]
        w("\nMIZAN KARSILASTIRMASI (stok degeri kesin / mizan / fark / gecici)")
        for k in d["karsilastirma"]:
            w(f"  {k['kod']}: {k['stok_degeri']:>16,.2f} / {k['mizan']:>16,.2f} / "
              f"{k['fark']:>14,.2f} / {k['gecici']:>12,.2f}")
        if d["tanimsiz"]:
            w(f"  TANIMSIZ hesap kartlari degeri: {d['tanimsiz']['deger']:,.2f}")
        w(f"  Toplam stok degeri: {d['toplam_deger']:,.2f} TL / {d['toplam_usd']:,.2f} USD")
        if not uygula:
            w(self.style.WARNING("\nDRY-RUN: kalici uygulamak icin --uygula."))
