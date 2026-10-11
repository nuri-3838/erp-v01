# Operasyon kaydı geri alma izi: geri alan kullanıcı + zaman (SAF ŞEMA).
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0205_is_istasyonu_fason_veri"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="operasyonkaydi",
            name="geri_alan",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="geri_aldigi_operasyon_kayitlari", to=settings.AUTH_USER_MODEL, verbose_name="geri alan"),
        ),
        migrations.AddField(
            model_name="operasyonkaydi",
            name="geri_alma_zamani",
            field=models.DateTimeField(blank=True, null=True, verbose_name="geri alma zamanı"),
        ),
    ]
