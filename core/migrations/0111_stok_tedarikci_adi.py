# Generated manually (elle yazıldı — bkz. core/models.py Stok.tedarikci_adi)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0110_alter_stokhareket_kaynak_uretimemri_uretimemrisatir_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='stok',
            name='tedarikci_adi',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='tedarikçi ürün adı'),
        ),
    ]
