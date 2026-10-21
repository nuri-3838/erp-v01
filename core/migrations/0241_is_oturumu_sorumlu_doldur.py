"""Eski iş oturumlarında sorumlu = ilk başlatan (yalnız VERİ; şema 0240'ta — Postgres: DML ile DDL aynı migration'da olmaz)."""
from django.db import migrations
from django.db.models import F


def doldur(apps, schema_editor):
    IsOturumu = apps.get_model("core", "IsOturumu")
    IsOturumu.objects.filter(sorumlu__isnull=True).update(sorumlu_id=F("baslatan_id"))


class Migration(migrations.Migration):
    dependencies = [("core", "0240_is_oturumu_tek_ekip_sorumlu")]
    operations = [migrations.RunPython(doldur, migrations.RunPython.noop)]
