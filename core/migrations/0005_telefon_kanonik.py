"""Mevcut Profil.telefon kayıtlarını +90'lı kanonik biçime günceller."""
import re

from django.db import migrations


def _kanonik_plus90(t):
    r = re.sub(r"\D", "", t or "")
    if len(r) == 12 and r.startswith("90"):
        r = r[2:]
    elif len(r) == 11 and r.startswith("0"):
        r = r[1:]
    if len(r) == 10:
        return "+90" + r
    return t  # tanınmayanı olduğu gibi bırak


def ileri(apps, schema_editor):
    Profil = apps.get_model("core", "Profil")
    for p in Profil.objects.all():
        if not p.telefon:
            continue
        yeni = _kanonik_plus90(p.telefon)
        if yeni != p.telefon:
            p.telefon = yeni
            p.save(update_fields=["telefon"])


class Migration(migrations.Migration):
    dependencies = [("core", "0004_profil")]
    operations = [migrations.RunPython(ileri, migrations.RunPython.noop)]
