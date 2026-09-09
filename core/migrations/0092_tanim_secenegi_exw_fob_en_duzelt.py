from django.db import migrations

# Kullanıcı geri bildirimi: "Exw Kayseri-Factory" yazımı yanlış Title-Case + Türkçe "Türkiye"
# çevrilmemiş — production'da elle girilmiş bu iki satırın ad_en'ini düzelt. ad (TR) ile
# arar, bulamazsa atlar (yerel dev'de bu satırlar farklı isimli olabilir, zararsız no-op).
DUZELT = {
    "EXW KAYSERİ-FABRİKA": "EXW – Kayseri, Türkiye",
    "FOB MERSİN-TÜRKİYE": "FOB – Mersin, Türkiye",
}


def duzelt(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for ad, yeni_ad_en in DUZELT.items():
        TanimSecenegi.objects.filter(
            kategori="YUKLEME_SEKLI", ad=ad, silindi=False).update(ad_en=yeni_ad_en)


def geri_al(apps, schema_editor):
    pass  # eski (yanlış) metni geri yazmaya değmez


class Migration(migrations.Migration):

    dependencies = [("core", "0091_tanim_secenegi_teslim_suresi_seed")]
    operations = [migrations.RunPython(duzelt, geri_al)]
