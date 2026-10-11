"""Toplu fatura PDF eki: zip / klasör / tek PDF kaynaklarındaki dosyaları, dosya adının ilk parçasına (ilk "_" öncesi; "_" yoksa
uzantısız ad) göre Fatura.fatura_no ile BİREBİR eşleştirip (alış + satış) faturaya EK olarak yükler (mevcut ``fatura_ek.ek_ekle`` servisi).

Kurallar: faturada aynı adlı ek varsa atlanır; eşleşmeyen / birden fazla faturaya uyan dosyalar raporlanır, yüklenmez;
fatura tutarına/fişine/durumuna DOKUNULMAZ (yalnız FaturaEk eklenir). Varsayılan KURU ÇALIŞTIRMA; ``--uygula`` yükler.

    python manage.py fatura_ek_toplu --kaynak A.zip --kaynak KLASOR --kaynak tek.pdf [--uygula]
"""
import hashlib
import os
import zipfile
from collections import defaultdict
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from core.models import Fatura, FaturaEk
from core.services import fatura_ek as ek_servis
from core.services.fatura import _fatura_no_anahtar
from core.services.luca_paket import zip_adi


def fatura_no_cikar(ad):
    """'GHE2026000018702_123_UNVAN.pdf' -> 'GHE2026000018702'; 'SMT2026000000007.pdf' -> 'SMT2026000000007'."""
    govde = os.path.splitext(ad)[0]
    return govde.split("_", 1)[0].strip()


def kaynak_oku(yol):
    """-> [(kaynak_etiketi, dosya_adi, bayt)] (yalnız .pdf)."""
    p = Path(yol)
    sonuc = []
    if p.is_dir():
        for d in sorted(p.rglob("*")):
            if d.is_file() and d.suffix.lower() == ".pdf":
                sonuc.append((p.name, d.name, d.read_bytes()))
    elif p.suffix.lower() == ".zip":
        try:
            z = zipfile.ZipFile(p)
        except (OSError, zipfile.BadZipFile) as e:
            raise CommandError(f"Zip açılamadı: {yol} ({e})")
        with z:
            for b in z.infolist():
                if b.is_dir():
                    continue
                ad = zip_adi(b)
                if ad.lower().endswith(".pdf"):
                    sonuc.append((p.name, ad, z.read(b)))
    elif p.is_file() and p.suffix.lower() == ".pdf":
        sonuc.append((p.parent.name, p.name, p.read_bytes()))
    else:
        raise CommandError(f"Kaynak bulunamadı / desteklenmiyor: {yol}")
    return sonuc


class Command(BaseCommand):
    help = "PDF'leri (zip/klasör/dosya) fatura_no eşleştirmesiyle faturalara ek yükler (varsayılan kuru çalıştırma)."

    def add_arguments(self, parser):
        parser.add_argument("--kaynak", action="append", required=True, dest="kaynaklar", help="zip, klasör veya tek PDF (tekrarlanabilir)")
        parser.add_argument("--uygula", action="store_true", help="Ekleri yükle (verilmezse yalnız rapor).")

    def handle(self, *args, **opts):
        indeks = defaultdict(list)
        for f in Fatura.objects.filter(silindi=False).exclude(fatura_no="").select_related("cari"):
            indeks[_fatura_no_anahtar(f.fatura_no)].append(f)

        gorulen = {}                       # (fatura_no_anahtar, ad) -> sha1 (kaynaklar arası aynı dosya tekrarı)
        eklenecek, var, eslesmeyen, cok_adayli, tekrar, farkli_icerik, hatalar = [], [], [], [], [], [], []
        toplam = 0
        for kaynak in opts["kaynaklar"]:
            for etiket, ad, bayt in kaynak_oku(kaynak):
                toplam += 1
                no = fatura_no_cikar(ad)
                anahtar = _fatura_no_anahtar(no)
                adaylar = indeks.get(anahtar, [])
                if not adaylar:
                    eslesmeyen.append((etiket, ad, no))
                    continue
                if len(adaylar) > 1:
                    cok_adayli.append((etiket, ad, no, ", ".join(f"fatura {f.pk} ({f.cari.unvan[:25]})" for f in adaylar)))
                    continue
                f = adaylar[0]
                sha = hashlib.sha1(bayt).hexdigest()
                onceki = gorulen.get((anahtar, ad))
                if onceki is not None:
                    (tekrar if onceki == sha else farkli_icerik).append((etiket, ad, f.pk))
                    continue
                gorulen[(anahtar, ad)] = sha
                if FaturaEk.objects.filter(fatura=f, silindi=False, orijinal_ad=ad).exists():
                    var.append((f, ad))
                    continue
                if opts["uygula"]:
                    try:
                        ek_servis.ek_ekle(f, dosya=ContentFile(bayt, name=ad))
                    except ek_servis.FaturaEkHatasi as e:
                        hatalar.append((f, ad, str(e)))
                        continue
                eklenecek.append((f, ad))

        w = self.stdout.write
        uygula = opts["uygula"]
        w(f"{'UYGULANDI' if uygula else 'KURU ÇALIŞTIRMA (hiçbir şey yazılmadı)'} — {toplam} PDF tarandı\n")
        w("ÖZET")
        w(f"  {'Eklenen' if uygula else 'Eklenecek'}             : {len(eklenecek)} ek / {len({f.pk for f, _ in eklenecek})} fatura")
        w(f"  Zaten var (atlanan)     : {len(var)}")
        w(f"  Kaynaklar arası tekrar  : {len(tekrar)} (aynı dosya, atlandı); içeriği farklı aynı ad: {len(farkli_icerik)}")
        w(f"  Eşleşmeyen              : {len(eslesmeyen)}")
        w(f"  Birden fazla adaylı     : {len(cok_adayli)}")
        w(f"  Hatalı                  : {len(hatalar)}")
        w(f"\n{'EKLENEN' if uygula else 'EKLENECEK'}  fatura | no | dosya")
        for f, ad in eklenecek:
            w(f"  {f.pk:<5} {f.fatura_no:<20} {ad}")
        if var:
            w("\nZATEN VAR (atlandı)")
            for f, ad in var:
                w(f"  {f.pk:<5} {f.fatura_no:<20} {ad}")
        if eslesmeyen:
            w("\nEŞLEŞMEYEN (dokunulmadı)  kaynak | dosya | çıkarılan no")
            for etiket, ad, no in eslesmeyen:
                w(f"  {etiket} | {ad} | {no}")
        if cok_adayli:
            w("\nBİRDEN FAZLA ADAYLI (yüklenmedi)")
            for etiket, ad, no, aday in cok_adayli:
                w(f"  {etiket} | {ad} | {no} -> {aday}")
        if farkli_icerik:
            w("\nİÇERİĞİ FARKLI AYNI AD (ilki yüklendi, diğeri atlandı)")
            for etiket, ad, pk in farkli_icerik:
                w(f"  {etiket} | {ad} | fatura {pk}")
        if hatalar:
            w("\nHATALI")
            for f, ad, e in hatalar:
                w(f"  fatura {f.pk} {ad}: {e}")
        if not uygula:
            w(self.style.WARNING("\nKURU ÇALIŞTIRMA: yüklemek için --uygula."))
