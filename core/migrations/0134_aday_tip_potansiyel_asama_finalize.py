import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """0132/0133'ün devamı — bilinçli olarak AYRI migration (bkz. 0132'nin sonundaki not):
    0133'teki toplu veri taşımasının kendi transaction'ında COMMIT olmasından SONRA, YENİ
    bir transaction'da şema temizliğine (eski CharField sütunlarını kaldırma) başlar. Yalnız
    şema (DDL) — #64/#239 veri düzeltmesi (DML) aynı sebeple 0135'e ayrıldı."""

    dependencies = [
        ('core', '0133_aday_tip_potansiyel_asama_veri_tasima'),
    ]

    operations = [
        migrations.RemoveField(model_name='adaymusteri', name='tip'),
        migrations.RemoveField(model_name='adaymusteri', name='potansiyel'),
        migrations.RemoveField(model_name='adaymusteri', name='asama'),
        migrations.RenameField(model_name='adaymusteri', old_name='tip_fk', new_name='tip'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='potansiyel_fk', new_name='potansiyel'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='asama_fk', new_name='asama'),
        migrations.AlterField(
            model_name='adaymusteri',
            name='tip',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaytiptanim', verbose_name='tip'),
        ),
        migrations.AlterField(
            model_name='adaymusteri',
            name='asama',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adayasamatanim',
                verbose_name='aşama'),
        ),
    ]
