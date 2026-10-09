"""Operasyon ÜRET/PARÇALA geçişi — SAF ŞEMA: eski yan çıktı tablosu (OperasyonYanCikti; tek kaynak artık OperasyonCikti) kaldırılır,
fason kaydında ``gelen_ana`` kaldırılıp ``gelen_yan`` → ``gelen`` ({stok pk: adet}, referans dahil) olarak yeniden adlandırılır."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0196_operasyon_kaydi_gelen_birlestir")]
    operations = [
        migrations.RemoveField(model_name="operasyonkaydi", name="gelen_ana"),
        migrations.RenameField(model_name="operasyonkaydi", old_name="gelen_yan", new_name="gelen"),
        migrations.AlterField(model_name="operasyonkaydi", name="gelen",
                              field=models.JSONField(blank=True, default=dict, verbose_name="gelen adetler ({stok pk: adet})")),
        migrations.DeleteModel(name="OperasyonYanCikti"),
    ]
