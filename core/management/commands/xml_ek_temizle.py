"""Fatura eklerindeki XML (UBL) dosyalarını siler — YALNIZ PDF kalsın (2026-10-15, kullanıcı kararı).

Kapsam: ``FaturaEk`` (alış + satış faturaları; irsaliyelerin ek modeli YOKTUR). Dosyalar özel depoda (``ozel_dosyalar/fatura_ek/``).

GÜVENLİK KURALI: belgede en az bir GEÇERLİ PDF ek (aktif kayıt + diskte var + boş değil + ``%PDF-`` imzası) varsa o belgenin XML'i silinir. PDF'i olmayan (ya da PDF'i
diskte okunamayan) belgelerde XML'e DOKUNULMAZ, ayrı liste olarak raporlanır.

Varsayılan ÖNİZLEME (hiçbir şey yazmaz/silmez); ``--uygula`` siler: ek KAYDI (fiziksel) + diskteki DOSYA — yetim dosya kalmaz. Fatura, fiş, stok ve diğer kayıtlara DOKUNULMAZ.
İkinci çalıştırmada 0 iş çıkar. Silinen her ek için denetim dökümü (ek no, fatura, ad, dosya, boyut, SHA-256) ``logs/xml_ek_temizlik_<tarih>.json`` dosyasına yazılır.
``--silinmis``: kullanıcının eskiden "Sil" ile soft-delete ettiği XML ek kayıtlarının diskte kalan dosyalarını da (PDF kuralı aranmadan) temizler.

    python manage.py xml_ek_temizle            # önizleme
    python manage.py xml_ek_temizle --uygula   # sil
"""
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import FaturaEk


def _uzanti(ek) -> str:
    return os.path.splitext(ek.dosya.name or "")[1].lower() or os.path.splitext(ek.orijinal_ad or "")[1].lower()


def _gecerli_pdf(ek) -> bool:
    """Aktif PDF ek: dosya diskte var, boş değil, PDF imzalı."""
    try:
        if _uzanti(ek) != ".pdf" or not ek.dosya.storage.exists(ek.dosya.name) or ek.dosya.size <= 0:
            return False
        with ek.dosya.open("rb") as f:
            return b"%PDF-" in f.read(1024)
    except Exception:
        return False


def _boyut(ek):
    try:
        return ek.dosya.size
    except Exception:
        return 0


def _sha(ek):
    try:
        with ek.dosya.open("rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def yetim_xml_dosyalari() -> list:
    """Özel depoda ``fatura_ek/`` altında hiçbir ek kaydına bağlı olmayan XML dosyaları."""
    depo = FaturaEk._meta.get_field("dosya").storage
    kok = Path(depo.location)
    klasor = kok / "fatura_ek"
    if not klasor.is_dir():
        return []
    kayitli = set(FaturaEk.objects.values_list("dosya", flat=True))
    return sorted(p.relative_to(kok).as_posix() for p in klasor.rglob("*.xml") if p.relative_to(kok).as_posix() not in kayitli)


class Command(BaseCommand):
    help = "Fatura eklerindeki XML dosyalarını siler (belgede geçerli PDF varsa). Varsayılan önizleme; --uygula siler."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true", help="Gerçekten sil (yoksa önizleme).")
        parser.add_argument("--silinmis", action="store_true", help="Soft-delete edilmiş XML ek kayıtlarının diskte kalan dosyalarını da temizle.")
        parser.add_argument("--log-dizini", default=None, help="Denetim dökümü klasörü (varsayılan: <proje>/logs).")

    def handle(self, *args, **o):
        uygula = o["uygula"]
        ekler = list(FaturaEk.objects.filter(silindi=False, fatura__silindi=False).select_related("fatura__cari").order_by("fatura__tarih", "fatura_id", "id"))
        belge = {}
        for e in ekler:
            belge.setdefault(e.fatura_id, []).append(e)
        silinecek, atlanan = [], []
        for ekler_b in belge.values():
            pdfler = [e for e in ekler_b if _uzanti(e) == ".pdf"]
            gecerli = [e for e in pdfler if _gecerli_pdf(e)]
            for x in (e for e in ekler_b if _uzanti(e) == ".xml"):
                (silinecek if gecerli else atlanan).append((x, [p.orijinal_ad for p in (gecerli or pdfler)]))
        silinmis_xml = list(FaturaEk.objects.filter(silindi=True).select_related("fatura")) if o["silinmis"] else []
        silinmis_xml = [e for e in silinmis_xml if _uzanti(e) == ".xml"]

        self.stdout.write(self.style.WARNING("UYGULAMA MODU" if uygula else "ÖNİZLEME (hiçbir şey silinmez; --uygula ile silinir)"))
        self._rapor("SİLİNECEK (belgede geçerli PDF var)", silinecek)
        self._rapor("ATLANAN — PDF YOK / PDF diskte okunamıyor (XML'e dokunulmaz; PDF sağlanacak)", atlanan)
        sayim = Counter(x.fatura.yon for x, _p in silinecek)
        self.stdout.write(f"\nSilinecek XML      : {len(silinecek)} ({sum(_boyut(x) for x, _p in silinecek) / 1048576:.2f} MB) | ALIŞ {sayim.get('ALIS', 0)} · SATIŞ {sayim.get('SATIS', 0)}")
        self.stdout.write(f"Atlanan (PDF yok)  : {len(atlanan)}")
        if o["silinmis"]:
            self.stdout.write(f"Soft-delete XML ek : {len(silinmis_xml)} (dosyaları diskte duruyor)")

        if not uygula:
            self.stdout.write("\nÖnizleme bitti — hiçbir kayıt/dosya değişmedi.")
            return

        denetim, hatalar = [], []
        for x, _pdfler in silinecek + [(e, []) for e in silinmis_xml]:
            kayit = {"ek_id": x.pk, "fatura_id": x.fatura_id, "fatura_no": x.fatura.fatura_no, "orijinal_ad": x.orijinal_ad, "dosya": x.dosya.name,
                     "boyut": _boyut(x), "sha256": _sha(x), "soft_delete": bool(x.silindi)}
            ad = x.dosya.name
            depo = x.dosya.storage
            with transaction.atomic():
                x.delete()                                       # ek kaydı (fiziksel); fatura/fiş/stok bu kayda bağlı değildir
            try:
                if ad and depo.exists(ad):
                    depo.delete(ad)                              # diskteki dosya (kayıt silindikten SONRA: kayıt hatasında dosya kaybolmaz)
            except OSError as e:
                hatalar.append((ad, str(e)))
            denetim.append(kayit)
        log_dir = Path(o["log_dizini"]) if o["log_dizini"] else Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"xml_ek_temizlik_{timezone.now():%Y%m%d_%H%M%S}.json"
        log.write_text(json.dumps(denetim, ensure_ascii=False, indent=1), encoding="utf-8")
        kalan = [e for e in FaturaEk.objects.filter(silindi=False) if _uzanti(e) == ".xml"]
        yetim = yetim_xml_dosyalari()
        self.stdout.write(self.style.SUCCESS(f"\nUYGULANDI: {len(denetim)} XML ek kaydı + dosyası silindi. Denetim dökümü: {log}"))
        self.stdout.write(f"Kalan aktif XML ek : {len(kalan)} (beklenen: atlananlar = {len(atlanan)})")
        self.stdout.write(f"Yetim XML dosyası  : {len(yetim)}")
        for ad, hata in hatalar:
            self.stdout.write(self.style.ERROR(f"  dosya silinemedi: {ad} — {hata}"))
        for y in yetim:
            self.stdout.write(f"  yetim: {y}")

    def _rapor(self, baslik, satirlar):
        self.stdout.write(f"\n== {baslik} ({len(satirlar)})")
        for x, pdfler in satirlar:
            f = x.fatura
            self.stdout.write(f"  {'ALIŞ ' if f.yon == 'ALIS' else 'SATIŞ'} #{f.pk} {f.fatura_no or '-':22} {f.tarih:%d.%m.%Y} {(f.cari.unvan if f.cari_id else '-')[:34]:34} "
                              f"| XML: {x.orijinal_ad} ({_boyut(x) / 1024:.1f} KB) | PDF: {', '.join(pdfler) if pdfler else 'YOK'}")
