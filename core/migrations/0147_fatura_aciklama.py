from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0146_teklif_durum_veri_gocu"),
    ]

    operations = [
        migrations.AddField(
            model_name="fatura",
            name="aciklama",
            field=models.CharField("açıklama", max_length=300, blank=True, default=""),
        ),
    ]
