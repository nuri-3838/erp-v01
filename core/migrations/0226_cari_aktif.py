# Cariler FAZ 1: Cari.aktif (pasif cari) + VKN/TCKN veritabanı benzersizlik kısıtı kalkar (yalnız şema; kontrol servis katmanında).
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0225_personel_sifre")]
    operations = [
        migrations.AddField(model_name="cari", name="aktif", field=models.BooleanField(default=True, verbose_name="aktif")),
        migrations.RemoveConstraint(model_name="cari", name="uq_cari_vkn_dolu"),
    ]
