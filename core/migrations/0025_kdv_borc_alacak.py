# KdvOrani.hesap -> hesap_borc + hesap_alacak (veri korunarak).

import django.db.models.deletion
from django.db import migrations, models


def hesap_borca_tasi(apps, schema_editor):
    """Mevcut tek hesap değerini Borç (İndirilecek KDV) alanına taşı."""
    KdvOrani = apps.get_model("core", "KdvOrani")
    for k in KdvOrani.objects.all():
        if k.hesap_id and not k.hesap_borc_id:
            k.hesap_borc_id = k.hesap_id
            k.save(update_fields=["hesap_borc"])


def geri(apps, schema_editor):
    KdvOrani = apps.get_model("core", "KdvOrani")
    for k in KdvOrani.objects.all():
        if k.hesap_borc_id and not k.hesap_id:
            k.hesap_id = k.hesap_borc_id
            k.save(update_fields=["hesap"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_kdvorani_tevkifatorani"),
    ]

    operations = [
        migrations.AddField(
            model_name="kdvorani",
            name="hesap_borc",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="kdv_borc_oranlari", to="core.hesapplani", verbose_name="borç hesabı"),
        ),
        migrations.AddField(
            model_name="kdvorani",
            name="hesap_alacak",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="kdv_alacak_oranlari", to="core.hesapplani", verbose_name="alacak hesabı"),
        ),
        migrations.RunPython(hesap_borca_tasi, geri),
        migrations.RemoveField(
            model_name="kdvorani",
            name="hesap",
        ),
    ]
