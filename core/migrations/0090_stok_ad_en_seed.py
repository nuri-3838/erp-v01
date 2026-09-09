from django.db import migrations

# model_kodu ile arar, bulamazsa sessizce atlar (yerel dev'de bu stoklar yok, zararsız).
AD_EN = {
    "A21": "Aluminium Platform Stepladder 2+1",
    "A31": "Aluminium Platform Stepladder 3+1",
    "A41": "Aluminium Platform Stepladder 4+1",
    "A51": "Aluminium Platform Stepladder 5+1",
    "A61": "Aluminium Platform Stepladder 6+1",
    "C22": "Double-Sided Aluminium Stepladder 2+2",
    "C33": "Double-Sided Aluminium Stepladder 3+3",
    "C44": "Double-Sided Aluminium Stepladder 4+4",
    "C55": "Double-Sided Aluminium Stepladder 5+5",
    "C66": "Double-Sided Aluminium Stepladder 6+6",
    "C77": "Double-Sided Aluminium Stepladder 7+7",
}


def seed(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, ad_en in AD_EN.items():
        Stok.objects.filter(
            model_kodu__iexact=model_kodu, silindi=False, ad_en=""
        ).update(ad_en=ad_en)


def geri_al(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    Stok.objects.filter(model_kodu__in=AD_EN.keys(), ad_en__in=AD_EN.values()).update(ad_en="")


class Migration(migrations.Migration):

    dependencies = [("core", "0089_stok_en_teslim_suresi")]
    operations = [migrations.RunPython(seed, geri_al)]
