"""HESAP_PLANI'nı docs/hesap_plani_seed.csv'den besler (idempotent).

Yeniden çalıştırılabilir: var olan kayıtları günceller, yenileri ekler.
Hiçbir kaydı silmez (soft-delete invariant'ı). CSV tek doğruluk kaynağıdır.
"""
import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.metin import buyuk_harf_tr
from core.models import HesapPlani

VARSAYILAN_CSV = Path(settings.BASE_DIR) / "docs" / "hesap_plani_seed.csv"

GECERLI_GRUPLAR = set(HesapPlani.RaporGrubu.values)


def _evet_hayir(deger: str, alan: str, satir: int) -> bool:
    v = (deger or "").strip().casefold()
    if v == "evet":
        return True
    if v in ("hayir", "hayır"):
        return False
    raise CommandError(f"Satır {satir}: '{alan}' evet/hayır olmalı, gelen: {deger!r}")


def _parasal(deger: str, satir: int):
    """Parasal alanı: bilanço hesaplarında evet/hayır, diğerlerinde '-' => None."""
    v = (deger or "").strip().casefold()
    if v in ("", "-"):
        return None
    return _evet_hayir(deger, "parasal", satir)


class Command(BaseCommand):
    help = "Hesap planını CSV'den besler (idempotent upsert)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            default=str(VARSAYILAN_CSV),
            help="Seed CSV yolu (varsayılan: docs/hesap_plani_seed.csv)",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        yol = Path(opts["csv"])
        if not yol.exists():
            raise CommandError(f"CSV bulunamadı: {yol}")

        eklenen = guncellenen = 0
        with yol.open(encoding="utf-8", newline="") as f:
            okuyucu = csv.DictReader(f)
            beklenen = {"hesap_kodu", "hesap_adi", "rapor_grubu",
                        "rapor_kalemi", "parasal", "aktif"}
            eksik = beklenen - set(okuyucu.fieldnames or [])
            if eksik:
                raise CommandError(f"CSV'de eksik sütun(lar): {sorted(eksik)}")

            for i, satir in enumerate(okuyucu, start=2):  # 1=başlık
                kod = (satir["hesap_kodu"] or "").strip()
                if not kod:
                    continue
                grup = (satir["rapor_grubu"] or "").strip()
                if grup not in GECERLI_GRUPLAR:
                    raise CommandError(
                        f"Satır {i}: geçersiz rapor_grubu {grup!r} "
                        f"(geçerli: {sorted(GECERLI_GRUPLAR)})"
                    )
                kalem = (satir["rapor_kalemi"] or "").strip()
                if kalem == "-":
                    kalem = ""

                _, olusturuldu = HesapPlani.objects.update_or_create(
                    hesap_kodu=kod,
                    defaults={
                        # TR büyük-harf invariant'ı: sistemde tek biçim (BÜYÜK).
                        "hesap_adi": buyuk_harf_tr((satir["hesap_adi"] or "").strip()),
                        "rapor_grubu": grup,
                        "rapor_kalemi": kalem,
                        "parasal": _parasal(satir["parasal"], i),
                        "aktif": _evet_hayir(satir["aktif"], "aktif", i),
                    },
                )
                if olusturuldu:
                    eklenen += 1
                else:
                    guncellenen += 1

        toplam = HesapPlani.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f"Hesap planı seed tamam: {eklenen} eklendi, "
            f"{guncellenen} güncellendi, toplam {toplam} hesap."
        ))
