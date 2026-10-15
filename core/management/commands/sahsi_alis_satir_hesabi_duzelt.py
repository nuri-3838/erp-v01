"""Şahsi (ortak adına) alış faturalarında bayat kalmış kalem hesabını ortak hesabına çeker (2026-10-15, kullanıcı kararı A).

Neden: ortak hareketleri 131.01'den 500.10.0001'e taşınırken (``ortak_hareketleri_tasi``) FİŞLER taşındı, ama ``FaturaSatir.hesap`` 131.01'de kaldı;
131.01 pasif olduğundan Düzenle'den kaydedince satır hesabı sessizce 500.10.0001'e dönüyordu. Kural: şahsi alışta HER kalemin hesabı faturanın
``sahsi_ortak`` hesabıdır (``fatura._satir_coz``). Bu komut yalnız o tutarsızlığı giderir.

Dokunulan: yalnız ``FaturaSatir.hesap`` (+ updated_at). Yevmiye fişi/satırları, tutarlar, stok, cari bakiyeleri DEĞİŞMEZ (fiş zaten ortak hesabında).
Varsayılan ÖNİZLEME; ``--uygula`` yazar. İkinci çalıştırmada 0 iş çıkar. Her satır için önce/sonra dökümü ``logs/sahsi_alis_satir_hesabi_<tarih>.json``.

    python manage.py sahsi_alis_satir_hesabi_duzelt            # önizleme
    python manage.py sahsi_alis_satir_hesabi_duzelt --uygula   # yaz
"""
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Fatura, FaturaSatir


def bayat_satirlar():
    """Şahsi alış faturalarında hesabı faturanın ortak hesabından farklı olan aktif kalemler."""
    adaylar = (FaturaSatir.objects.filter(silindi=False, fatura__silindi=False, fatura__sahsi_alis=True, fatura__sahsi_ortak__isnull=False)
               .select_related("fatura", "hesap").order_by("fatura_id", "id"))
    return [s for s in adaylar if s.hesap_id != s.fatura.sahsi_ortak_id]


class Command(BaseCommand):
    help = "Şahsi alış faturası kalemlerinin bayat hesabını (ör. 131.01) faturanın ortak hesabına (500.10.0001) çeker. Varsayılan önizleme; --uygula yazar."

    def add_arguments(self, parser):
        parser.add_argument("--uygula", action="store_true", help="Gerçekten yaz (yoksa önizleme).")
        parser.add_argument("--log-dizini", default=None, help="Denetim dökümü klasörü (varsayılan: <proje>/logs).")

    def handle(self, *args, **o):
        satirlar = bayat_satirlar()
        self.stdout.write(self.style.WARNING("UYGULAMA MODU" if o["uygula"] else "ÖNİZLEME (hiçbir şey yazılmaz; --uygula ile yazılır)"))
        faturalar = sorted({s.fatura_id for s in satirlar})
        for s in satirlar:
            f = s.fatura
            self.stdout.write(f"  fatura #{f.pk} {f.fatura_no[:22]:22} satır #{s.pk}: {s.hesap_id} → {f.sahsi_ortak_id}")
        self.stdout.write(f"\nDeğişecek kalem: {len(satirlar)} | etkilenen fatura: {len(faturalar)} {faturalar}")
        if not o["uygula"]:
            self.stdout.write("Önizleme bitti — hiçbir kayıt değişmedi.")
            return
        denetim = []
        with transaction.atomic():
            for s in satirlar:
                denetim.append({"satir_id": s.pk, "fatura_id": s.fatura_id, "fatura_no": s.fatura.fatura_no, "onceki_hesap": s.hesap_id, "yeni_hesap": s.fatura.sahsi_ortak_id})
                FaturaSatir.objects.filter(pk=s.pk).update(hesap_id=s.fatura.sahsi_ortak_id, updated_at=timezone.now())
        log_dir = Path(o["log_dizini"]) if o["log_dizini"] else Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = log_dir / f"sahsi_alis_satir_hesabi_{timezone.now():%Y%m%d_%H%M%S}.json"
        log.write_text(json.dumps(denetim, ensure_ascii=False, indent=1), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"UYGULANDI: {len(denetim)} kalem. Denetim dökümü: {log}"))
        self.stdout.write(f"Kalan bayat kalem: {len(bayat_satirlar())} (beklenen 0)")
