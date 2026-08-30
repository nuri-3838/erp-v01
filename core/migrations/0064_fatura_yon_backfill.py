from django.db import migrations


def yon_geriye_doldur(apps, schema_editor):
    Fatura = apps.get_model("core", "Fatura")
    for f in Fatura.objects.filter(yon__isnull=True, tip__isnull=False).select_related("tip"):
        f.yon = f.tip.yon
        f.save(update_fields=["yon"])


def geri_al(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0063_fatura_durum_yon_tip_nullable'),
    ]

    operations = [
        migrations.RunPython(yon_geriye_doldur, geri_al),
    ]
