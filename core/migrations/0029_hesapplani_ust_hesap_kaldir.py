# HesapPlani.ust_hesap FK kaldırıldı — hiyerarşi tek kaynaktan: hesap_kodu metni.
# Veri kaybı yok: üst/alt ilişki koddan türetilebilir (320.10.0001 -> 320.10 -> 320).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_stok_tedarikci_cari_fk"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="hesapplani",
            name="ust_hesap",
        ),
    ]
