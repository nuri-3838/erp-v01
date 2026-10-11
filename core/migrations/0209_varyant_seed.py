from django.db import migrations

# AYARLAR > Varyantlar ilk değerleri (docs/varyant_kararlari.md, karar 4): SEMTA varsayılan (standart varyant), YOK, MR. MOP.
# Yalnız VERİ (şema 0208'de).
SEED = [
    ("SEMTA", "SEMTA", "SEMTA", 10, True),
    ("YOK", "YOK (MARKASIZ / KULLANILMAZ)", "NONE (UNBRANDED / NOT USED)", 20, False),
    ("MR. MOP", "MR. MOP", "MR. MOP", 30, False),
]


def seed(apps, schema_editor):
    Varyant = apps.get_model("core", "Varyant")
    for kod, ad, ad_en, sira, varsayilan in SEED:
        if Varyant.objects.filter(kod=kod, silindi=False).exists():
            continue
        if varsayilan and Varyant.objects.filter(varsayilan=True, silindi=False).exists():
            varsayilan = False
        Varyant.objects.create(kod=kod, ad=ad, ad_en=ad_en, sira=sira, aktif=True, varsayilan=varsayilan)


def geri_al(apps, schema_editor):
    Varyant = apps.get_model("core", "Varyant")
    Varyant.objects.filter(kod__in=[k for k, *_ in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0208_varyant"),
    ]

    operations = [
        migrations.RunPython(seed, geri_al),
    ]
