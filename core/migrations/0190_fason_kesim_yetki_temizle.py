"""Kaldırılan Fason Kesim ekranlarına (fason_kayitlari, fason_kesim_tanimlari) verilmiş işlevsiz ekran yetkisi satırlarını siler (saf veri; şema değişmez)."""
from django.db import migrations

KODLAR = ("fason_kayitlari", "fason_kesim_tanimlari")


def temizle(apps, schema_editor):
    apps.get_model("core", "EkranYetki").objects.filter(ekran_kod__in=KODLAR).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0189_fason_kesim_kaldir")]
    operations = [migrations.RunPython(temizle, migrations.RunPython.noop)]
