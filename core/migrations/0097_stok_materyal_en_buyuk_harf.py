from django.db import migrations

# Kullanici: materyal_en de materyal (TR, "ALUMINYUM") ve ad_en (tumu buyuk) ile
# ayni sekilde tumu buyuk harf olsun istedi. "Aluminium" -> "ALUMINIUM" (ASCII
# .upper() esdegeri elle yazildi, Turkce karakter yok - buyuk_harf_tr() GEREKMEZ).
# ad ile arar, bulamazsa atlar (yerel dev'de bu stoklar yok, zararsiz no-op).


def duzelt(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    Stok.objects.filter(materyal_en="Aluminium", silindi=False).update(materyal_en="ALUMINIUM")


def geri_al(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    Stok.objects.filter(materyal_en="ALUMINIUM", silindi=False).update(materyal_en="Aluminium")


class Migration(migrations.Migration):

    dependencies = [("core", "0096_stok_ad_en_single_sided_duzelt")]
    operations = [migrations.RunPython(duzelt, geri_al)]
