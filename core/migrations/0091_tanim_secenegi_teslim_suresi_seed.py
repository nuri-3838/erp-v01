from django.db import migrations

# AYARLAR > Tanım Listeleri > Teslim Süresi — örnek değerler (kullanıcı gerçek değerleri
# kendi girecek/düzenleyecek, bkz. ODEME_KOSULU/YUKLEME_SEKLI ile aynı emsal).
SEED = [
    ("SİPARİŞ ONAYI SONRASI 15 İŞ GÜNÜ", "15 Business Days After Order Confirmation"),
    ("SİPARİŞ ONAYI SONRASI 20 İŞ GÜNÜ", "20 Business Days After Order Confirmation"),
    ("SİPARİŞ ONAYI SONRASI 30 İŞ GÜNÜ", "30 Business Days After Order Confirmation"),
    ("STOKTAN (HAZIR)", "From Stock (Ready)"),
]


def seed(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for sira, (ad, ad_en) in enumerate(SEED, start=1):
        TanimSecenegi.objects.get_or_create(
            kategori="TESLIM_SURESI", ad=ad, silindi=False,
            defaults={"ad_en": ad_en, "sira": sira * 10})


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    TanimSecenegi.objects.filter(
        kategori="TESLIM_SURESI", ad__in=[ad for ad, _ in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0090_stok_ad_en_seed")]
    operations = [migrations.RunPython(seed, geri_al)]
