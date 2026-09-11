from django.db import migrations

# Kullanıcı geri bildirimi: A Tipi ürünlerde İngilizce isimde "Platform Stepladder" yerine
# daha sade "Step Ladder" kullanılsın. model_kodu ile arar, eski ad_en tam eşleşiyorsa
# günceller; bulamazsa/değişmişse atlar (yerel dev'de bu stoklar yok, zararsız no-op).
DUZELT = {
    "A21": ("Aluminium Platform Stepladder 2+1", "Aluminium Step Ladder 2+1"),
    "A31": ("Aluminium Platform Stepladder 3+1", "Aluminium Step Ladder 3+1"),
    "A41": ("Aluminium Platform Stepladder 4+1", "Aluminium Step Ladder 4+1"),
    "A51": ("Aluminium Platform Stepladder 5+1", "Aluminium Step Ladder 5+1"),
    "A61": ("Aluminium Platform Stepladder 6+1", "Aluminium Step Ladder 6+1"),
}


def duzelt(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, (eski_ad_en, yeni_ad_en) in DUZELT.items():
        Stok.objects.filter(
            model_kodu__iexact=model_kodu, ad_en=eski_ad_en, silindi=False
        ).update(ad_en=yeni_ad_en)


def geri_al(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, (eski_ad_en, yeni_ad_en) in DUZELT.items():
        Stok.objects.filter(
            model_kodu__iexact=model_kodu, ad_en=yeni_ad_en, silindi=False
        ).update(ad_en=eski_ad_en)


class Migration(migrations.Migration):

    dependencies = [("core", "0093_stok_hs_materyal")]
    operations = [migrations.RunPython(duzelt, geri_al)]
