# Stok.tedarikci (serbest metin) -> Cari FK. Canlida tek stok tedarikcisi bos,
# tasinacak veri yok; serbest metin kaldirilip FK eklenir.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_stok_kdv_tevkifat_fk"),
    ]

    operations = [
        migrations.RemoveField(model_name="stok", name="tedarikci"),
        migrations.AddField(
            model_name="stok",
            name="tedarikci",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tedarik_stoklari", to="core.cari", verbose_name="tedarikçi (cari)"),
        ),
    ]
