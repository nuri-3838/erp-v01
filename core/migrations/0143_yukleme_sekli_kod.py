from django.db import migrations

# Yükleme Şekli (Incoterm) satırlarına "kod" ataması — teklif PDF'indeki teslim şekli
# notunun hangi cümle kalıbını kullanacağını belirler (bkz. core.views._teslim_sekli_notu).
# Admin yeni bir Yükleme Şekli satırı eklerse kod boş kalır ve PDF güvenli varsayılana
# (statik "navlun dahil" cümlesi) düşer — bkz. görüşler.
KOD_HARITASI = {
    "EXW İZMİR (FABRİKA TESLİM)": "EXW",
    "FOB İZMİR": "FOB",
    "CFR VARIŞ LİMANI": "CFR",
    "CIF VARIŞ LİMANI": "CIF",
    "DAP ALICI ADRESİ": "DAP",
    "NAKLİYE DAHİL (YURT İÇİ)": "NAKLIYE_DAHIL",
    "NAKLİYE HARİÇ (YURT İÇİ)": "NAKLIYE_HARIC",
}


def kod_ata(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for ad, kod in KOD_HARITASI.items():
        TanimSecenegi.objects.filter(
            kategori="YUKLEME_SEKLI", ad=ad, silindi=False, kod="").update(kod=kod)


def kod_geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    TanimSecenegi.objects.filter(
        kategori="YUKLEME_SEKLI", kod__in=KOD_HARITASI.values()).update(kod="")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0142_mesai"),
    ]

    operations = [
        migrations.RunPython(kod_ata, kod_geri_al),
    ]
