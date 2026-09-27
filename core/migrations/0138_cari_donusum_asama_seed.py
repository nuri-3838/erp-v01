"""Cariye dönüşünce otomatik aşama — tohum (saf DML, şema değişikliği YOK; bkz. 0137'de
yalnız şema). sistem_kodu='SIPARIS' olan aşamanın rolü CARI yapılır (adı 'Sipariş' olarak
kalır); zaten cariye dönüşmüş #64 (MR. MOP GMBH) ve #239 (5G GROUP) bu aşamaya taşınır.
Eski değerler backups/core_0138_cari_asama_yedek.json'a yazılır, reverse bu dosyadan geri
yükler (bkz. core/migrations/0136_aday_cari_telefon_normalize.py — aynı yedek deseni)."""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.db import migrations

_YEDEK_YOLU = Path(settings.BASE_DIR) / "backups" / "core_0138_cari_asama_yedek.json"
_TASINACAK_ADAY_PK = (64, 239)


def _ileri(apps, schema_editor):
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")
    AdayMusteri = apps.get_model("core", "AdayMusteri")

    siparis = AdayAsamaTanim.objects.filter(silindi=False, sistem_kodu="SIPARIS").first()
    if siparis is None:
        print("UYARI: sistem_kodu='SIPARIS' olan aşama bulunamadı — CARI rol ataması "
              "ve #64/#239 taşıması atlandı.")
        return

    eski_rol = siparis.rol
    siparis.rol = "CARI"
    siparis.save(update_fields=["rol"])

    eski_asamalar = {}
    for pk in _TASINACAK_ADAY_PK:
        aday = AdayMusteri.objects.filter(pk=pk).first()
        if aday is None:
            print(f"UYARI: Aday #{pk} bulunamadı, atlanıyor.")
            continue
        eski_asamalar[pk] = aday.asama_id
        aday.asama_id = siparis.pk
        aday.save(update_fields=["asama"])

    _YEDEK_YOLU.parent.mkdir(parents=True, exist_ok=True)
    _YEDEK_YOLU.write_text(
        json.dumps({"siparis_pk": siparis.pk, "eski_rol": eski_rol,
                    "aday_eski_asama": eski_asamalar}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"OK: aşama #{siparis.pk} '{siparis.ad}' rolü '{eski_rol}' -> 'CARI'. "
          f"Taşınan adaylar: {eski_asamalar}. Yedek: {_YEDEK_YOLU}")


def _geri_al(apps, schema_editor):
    if not _YEDEK_YOLU.exists():
        print(f"UYARI: {_YEDEK_YOLU} bulunamadı — geri alma atlandı.")
        return
    yedek = json.loads(_YEDEK_YOLU.read_text(encoding="utf-8"))
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")
    AdayMusteri = apps.get_model("core", "AdayMusteri")

    siparis = AdayAsamaTanim.objects.filter(pk=yedek["siparis_pk"]).first()
    if siparis is not None:
        siparis.rol = yedek["eski_rol"]
        siparis.save(update_fields=["rol"])

    restore_edilen = 0
    for pk_str, eski_asama_id in yedek["aday_eski_asama"].items():
        n = AdayMusteri.objects.filter(pk=int(pk_str)).update(asama_id=eski_asama_id)
        restore_edilen += n

    print(f"OK: aşama #{yedek['siparis_pk']} rolü '{yedek['eski_rol']}' geri alındı; "
          f"{restore_edilen} aday eski aşamasına döndürüldü.")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0137_asama_cari_rolu'),
    ]

    operations = [
        migrations.RunPython(_ileri, _geri_al),
    ]
