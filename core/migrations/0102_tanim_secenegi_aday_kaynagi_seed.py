from django.db import migrations

# AYARLAR > Tanım Listeleri > Aday Kaynağı — örnek değerler (kullanıcı gerçek değerleri
# kendi girecek/düzenleyecek, bkz. TESLIM_SURESI ile aynı emsal).
SEED = [
    ("REFERANS", "Referral"),
    ("WEB SİTESİ", "Website"),
    ("FUAR / ETKİNLİK", "Trade Fair / Event"),
    ("SOĞUK ARAMA", "Cold Call"),
    ("SOSYAL MEDYA", "Social Media"),
    ("DİĞER", "Other"),
]


def seed(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for sira, (ad, ad_en) in enumerate(SEED, start=1):
        TanimSecenegi.objects.get_or_create(
            kategori="ADAY_KAYNAGI", ad=ad, silindi=False,
            defaults={"ad_en": ad_en, "sira": sira * 10})


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    TanimSecenegi.objects.filter(
        kategori="ADAY_KAYNAGI", ad__in=[ad for ad, _ in SEED]).delete()


class Migration(migrations.Migration):

    dependencies = [("core", "0101_crm_aday_musteri")]
    operations = [migrations.RunPython(seed, geri_al)]
