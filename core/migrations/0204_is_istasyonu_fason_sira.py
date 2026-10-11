# İş İstasyonu: fason bayrağı + varsayılan fasoncu + sıra (SAF ŞEMA — veri 0205'te).
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0203_numune_cikis"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="isistasyonu",
            options={"ordering": ["sira", "kod"], "verbose_name": "iş istasyonu", "verbose_name_plural": "iş istasyonları"},
        ),
        migrations.AddField(
            model_name="isistasyonu",
            name="fason",
            field=models.BooleanField(default=False, verbose_name="fason istasyonu"),
        ),
        migrations.AddField(
            model_name="isistasyonu",
            name="fasoncu",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="fason_istasyonlari",
                                    to="core.cari", verbose_name="varsayılan fasoncu"),
        ),
        migrations.AddField(
            model_name="isistasyonu",
            name="sira",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="sıra"),
        ),
    ]
