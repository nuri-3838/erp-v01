# KdvOrani.oran benzersiz (silinmemişler arasında) — aynı oran iki kez tanımlanamaz.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_kdv_borc_alacak"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="kdvorani",
            constraint=models.UniqueConstraint(
                fields=["oran"], condition=models.Q(silindi=False),
                name="uq_kdv_oran_aktif"),
        ),
    ]
