# Stok.kdv_orani/tevkifat_orani (serbest decimal) -> KdvOrani/TevkifatOrani FK.
# Mevcut stoklar orana göre eşlenir (veri korunur).

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def ileri(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    KdvOrani = apps.get_model("core", "KdvOrani")
    TevkifatOrani = apps.get_model("core", "TevkifatOrani")
    for s in Stok.objects.all():
        k = KdvOrani.objects.filter(silindi=False, oran=s.kdv_orani).first()
        if k is not None:
            s.kdv_id = k.pk
        if s.tevkifat_orani and s.tevkifat_orani > 0:
            for t in TevkifatOrani.objects.filter(silindi=False):
                if t.payda and (Decimal(t.pay) / Decimal(t.payda) * 100) == s.tevkifat_orani:
                    s.tevkifat_id = t.pk
                    break
        s.save(update_fields=["kdv", "tevkifat"])


def geri(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for s in Stok.objects.all():
        if s.kdv_id:
            s.kdv_orani = s.kdv.oran
        if s.tevkifat_id:
            t = s.tevkifat
            s.tevkifat_orani = Decimal(t.pay) / Decimal(t.payda) * 100
        s.save(update_fields=["kdv_orani", "tevkifat_orani"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_uq_kdv_oran_aktif"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="stok", name="ck_stok_kdv_gte0"),
        migrations.RemoveConstraint(model_name="stok", name="ck_stok_tevkifat_gte0"),
        migrations.AddField(
            model_name="stok",
            name="kdv",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stoklar", to="core.kdvorani", verbose_name="KDV oranı"),
        ),
        migrations.AddField(
            model_name="stok",
            name="tevkifat",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stoklar", to="core.tevkifatorani", verbose_name="tevkifat oranı"),
        ),
        migrations.RunPython(ileri, geri),
        migrations.RemoveField(model_name="stok", name="kdv_orani"),
        migrations.RemoveField(model_name="stok", name="tevkifat_orani"),
    ]
