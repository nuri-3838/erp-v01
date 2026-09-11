from django.db import migrations

# Kullanicinin guncellenmis TR/EN karsilik tablosu: A-serisi Ingilizce adinda
# "TYPE A" yerine "SINGLE-SIDED" kullanilsin (C-serisiyle simetrik: DOUBLE-SIDED /
# SINGLE-SIDED). C-serisi ve TR ad zaten bu tabloyla ayni (0095'te ayarlandi) -
# degismiyor. model_kodu ile arar, bulamazsa atlar (yerel dev'de bu stoklar yok,
# zararsiz no-op).
AD_EN = {
    "A21": "SINGLE-SIDED ALUMINIUM STEP LADDER 3 (2+1)",
    "A31": "SINGLE-SIDED ALUMINIUM STEP LADDER 4 (3+1)",
    "A41": "SINGLE-SIDED ALUMINIUM STEP LADDER 5 (4+1)",
    "A51": "SINGLE-SIDED ALUMINIUM STEP LADDER 6 (5+1)",
    "A61": "SINGLE-SIDED ALUMINIUM STEP LADDER 7 (6+1)",
}

ESKI_AD_EN = {
    "A21": "TYPE A ALUMINIUM STEP LADDER 3 (2+1)",
    "A31": "TYPE A ALUMINIUM STEP LADDER 4 (3+1)",
    "A41": "TYPE A ALUMINIUM STEP LADDER 5 (4+1)",
    "A51": "TYPE A ALUMINIUM STEP LADDER 6 (5+1)",
    "A61": "TYPE A ALUMINIUM STEP LADDER 7 (6+1)",
}


def duzelt(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, yeni_ad_en in AD_EN.items():
        Stok.objects.filter(model_kodu__iexact=model_kodu, silindi=False).update(ad_en=yeni_ad_en)


def geri_al(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    for model_kodu, eski_ad_en in ESKI_AD_EN.items():
        Stok.objects.filter(model_kodu__iexact=model_kodu, silindi=False).update(ad_en=eski_ad_en)


class Migration(migrations.Migration):

    dependencies = [("core", "0095_stok_ad_en_tip_a_double_sided_duzelt")]
    operations = [migrations.RunPython(duzelt, geri_al)]
