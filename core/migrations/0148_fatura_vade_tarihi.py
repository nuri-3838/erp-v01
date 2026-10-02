from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0147_fatura_aciklama"),
    ]

    operations = [
        migrations.AddField(
            model_name="fatura",
            name="vade_tarihi",
            field=models.DateField("vade tarihi", null=True, blank=True),
        ),
    ]
