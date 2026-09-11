from django.db import migrations

# Kullanıcının verdigi TR/EN karşılık tablosuna göre İngilizce ürün adı düzeltmesi:
# A-serisi artık "TYPE A ALUMINIUM STEP LADDER {toplam basamak} ({n}+1)" (0094'teki
# "Aluminium Step Ladder {n}+1" halini de değiştirir), C-serisi "STEPLADDER" (tek kelime)
# yerine "STEP LADDER" (iki kelime) olur. TR ad zaten aynı desende ("A TİPİ ALÜMİNYUM
# MERDİVEN 2+1" / "ÇİFT ÇIKIŞLI ALÜMİNYUM MERDİVEN 2+2") — değişmiyor.
# model_kodu ile arar, bulamazsa atlar (yerel dev'de bu stoklar yok, zararsız no-op).
AD_EN = {
    "A21": "TYPE A ALUMINIUM STEP LADDER 3 (2+1)",
    "A31": "TYPE A ALUMINIUM STEP LADDER 4 (3+1)",
    "A41": "TYPE A ALUMINIUM STEP LADDER 5 (4+1)",
    "A51": "TYPE A ALUMINIUM STEP LADDER 6 (5+1)",
    "A61": "TYPE A ALUMINIUM STEP LADDER 7 (6+1)",
    "C22": "DOUBLE-SIDED ALUMINIUM STEP LADDER 2+2",
    "C33": "DOUBLE-SIDED ALUMINIUM STEP LADDER 3+3",
    "C44": "DOUBLE-SIDED ALUMINIUM STEP LADDER 4+4",
    "C55": "DOUBLE-SIDED ALUMINIUM STEP LADDER 5+5",
    "C66": "DOUBLE-SIDED ALUMINIUM STEP LADDER 6+6",
    "C77": "DOUBLE-SIDED ALUMINIUM STEP LADDER 7+7",
}

ESKI_AD_EN = {
    "A21": "Aluminium Step Ladder 2+1",
    "A31": "Aluminium Step Ladder 3+1",
    "A41": "Aluminium Step Ladder 4+1",
    "A51": "Aluminium Step Ladder 5+1",
    "A61": "Aluminium Step Ladder 6+1",
    "C22": "Double-Sided Aluminium Stepladder 2+2",
    "C33": "Double-Sided Aluminium Stepladder 3+3",
    "C44": "Double-Sided Aluminium Stepladder 4+4",
    "C55": "Double-Sided Aluminium Stepladder 5+5",
    "C66": "Double-Sided Aluminium Stepladder 6+6",
    "C77": "Double-Sided Aluminium Stepladder 7+7",
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

    dependencies = [("core", "0094_stok_ad_en_step_ladder_duzelt")]
    operations = [migrations.RunPython(duzelt, geri_al)]
