"""Paket içi adet VERİ ataması (2026-11-18): model kodu G02 → 6, G03 → 4, G04 → 3. Diğer tüm kartlar (G02-T, G03-T, G04-T dahil) şema migration'ındaki varsayılan 1'de kalır.

Yalnız VERİ migration'ıdır: şema 0259'dadır (Postgres: aynı transaction'da DDL + DML "pending trigger events" hatası verir)."""
from django.db import migrations

DEGERLER = {"G02": 6, "G03": 4, "G04": 3}


def paket_ici_adet_ver(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, adet in DEGERLER.items():
        Stok.objects.filter(model_kodu=model_kodu).update(paket_ici_adet=adet)      # tam eşleşme: G02-T vb. etkilenmez


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0259_stok_paket_ici_adet"),
    ]

    operations = [
        migrations.RunPython(paket_ici_adet_ver, migrations.RunPython.noop),
    ]
