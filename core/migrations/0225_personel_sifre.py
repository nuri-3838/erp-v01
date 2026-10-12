# Personel mesai hesabı şifre disiplini (yalnız şema).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0224_is_oturumu_fire")]
    operations = [
        migrations.AddField(model_name="personel", name="sifre_degistirilmeli",
                            field=models.BooleanField(default=False, verbose_name="ilk girişte şifre değiştirilmeli")),
        migrations.AddField(model_name="personel", name="sifre_degisiklik_zamani",
                            field=models.DateTimeField(blank=True, null=True, verbose_name="son şifre değişikliği")),
    ]
