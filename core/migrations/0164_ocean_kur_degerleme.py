"""Veri: Ocean Network Express (320.40.0013, konteyner depozitosu = parasal alacak) kur değerlemesi
"Her zaman değerle". SADECE veri (şema 0163'te) — DML ile DDL aynı migration'da karıştırılmaz."""
from django.db import migrations


def ocean_her_zaman(apps, schema_editor):
    Cari = apps.get_model("core", "Cari")
    Cari.objects.filter(muhasebe_kodu="320.40.0013", silindi=False).update(kur_degerleme="HER_ZAMAN")


def geri(apps, schema_editor):
    Cari = apps.get_model("core", "Cari")
    Cari.objects.filter(muhasebe_kodu="320.40.0013", kur_degerleme="HER_ZAMAN").update(kur_degerleme="OTOMATIK")


class Migration(migrations.Migration):
    dependencies = [("core", "0163_cari_kur_degerleme")]
    operations = [migrations.RunPython(ocean_her_zaman, geri)]
