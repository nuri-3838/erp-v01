from django.db import migrations

# AYARLAR > Tanım Listeleri örnek değerleri (kullanıcı: "örneklerini doldur sen").
# Yükleme Tipi kodları Stok'un yukleme_20dc/40hq/tir alanlarına eşlenir (TanimSecenegi.
# YUKLEME_ALANI) — kod değiştirilirse navlun dağıtımı o tip için çalışmaz.
SEED = {
    "YUKLEME_SEKLI": [
        ("", "EXW İZMİR (FABRİKA TESLİM)"),
        ("", "FOB İZMİR"),
        ("", "CFR VARIŞ LİMANI"),
        ("", "CIF VARIŞ LİMANI"),
        ("", "DAP ALICI ADRESİ"),
        ("", "NAKLİYE DAHİL (YURT İÇİ)"),
        ("", "NAKLİYE HARİÇ (YURT İÇİ)"),
    ],
    "ODEME_KOSULU": [
        ("", "PEŞİN"),
        ("", "%50 PEŞİN + %50 SEVKİYAT ÖNCESİ"),
        ("", "%30 PEŞİN + %70 SEVKİYAT ÖNCESİ"),
        ("", "30 GÜN VADE"),
        ("", "60 GÜN VADE"),
        ("", "AKREDİTİF (L/C)"),
        ("", "VESAİK MUKABİLİ (CAD)"),
    ],
    "YUKLEME_TIPI": [
        ("20DC", "20' DC KONTEYNER"),
        ("40HQ", "40' HQ KONTEYNER"),
        ("TIR", "TIR"),
    ],
}


def seed(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, satirlar in SEED.items():
        for sira, (kod, ad) in enumerate(satirlar, start=1):
            TanimSecenegi.objects.get_or_create(
                kategori=kategori, ad=ad, silindi=False,
                defaults={"kod": kod, "sira": sira * 10})


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, satirlar in SEED.items():
        TanimSecenegi.objects.filter(
            kategori=kategori, ad__in=[ad for _, ad in satirlar]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0078_tanim_secenegi_teklif_navlun"),
    ]

    operations = [
        migrations.RunPython(seed, geri_al),
    ]
