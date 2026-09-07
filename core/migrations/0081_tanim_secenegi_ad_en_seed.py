from django.db import migrations

# Seed'lenen Tanım Listesi seçeneklerinin İngilizce karşılıkları (EN teklif PDF'i için).
# Yalnız ad_en boş olan kayıtlar doldurulur — kullanıcı elle girdiyse dokunulmaz.
EN = {
    "YUKLEME_SEKLI": {
        "EXW İZMİR (FABRİKA TESLİM)": "EXW Izmir (Ex Works)",
        "FOB İZMİR": "FOB Izmir",
        "CFR VARIŞ LİMANI": "CFR Port of Destination",
        "CIF VARIŞ LİMANI": "CIF Port of Destination",
        "DAP ALICI ADRESİ": "DAP Buyer's Address",
        "NAKLİYE DAHİL (YURT İÇİ)": "Freight Included (Domestic)",
        "NAKLİYE HARİÇ (YURT İÇİ)": "Freight Excluded (Domestic)",
    },
    "ODEME_KOSULU": {
        "PEŞİN": "Advance Payment (100%)",
        "%50 PEŞİN + %50 SEVKİYAT ÖNCESİ": "50% Advance + 50% Before Shipment",
        "%30 PEŞİN + %70 SEVKİYAT ÖNCESİ": "30% Advance + 70% Before Shipment",
        "30 GÜN VADE": "Net 30 Days",
        "60 GÜN VADE": "Net 60 Days",
        "AKREDİTİF (L/C)": "Letter of Credit (L/C)",
        "VESAİK MUKABİLİ (CAD)": "Cash Against Documents (CAD)",
    },
    "YUKLEME_TIPI": {
        "20' DC KONTEYNER": "20' DC Container",
        "40' HQ KONTEYNER": "40' HQ Container",
        "TIR": "Truck",
    },
}


def doldur(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, esleme in EN.items():
        for ad, ad_en in esleme.items():
            TanimSecenegi.objects.filter(kategori=kategori, ad=ad, ad_en="").update(ad_en=ad_en)


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, esleme in EN.items():
        TanimSecenegi.objects.filter(
            kategori=kategori, ad_en__in=list(esleme.values())).update(ad_en="")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0080_tanim_secenegi_ad_en"),
    ]

    operations = [
        migrations.RunPython(doldur, geri_al),
    ]
