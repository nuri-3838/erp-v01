# Atölye Bitir: sağlam + fire (yalnız şema).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0223_fason_siparisi")]
    operations = [
        migrations.AddField(model_name="isoturumu", name="fire",
                            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name="fire adet")),
    ]
