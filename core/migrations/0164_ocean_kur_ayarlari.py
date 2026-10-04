"""Veri: Ocean Network Express (320.40.0013, konteyner depozitosu = parasal alacak): kur değerlemesi
"Her zaman değerle", kur farkı hedefi "Her zaman 646-656". Geçmişte üretilmiş kur farkı satırlarına
DOKUNMAZ. SADECE veri (şema 0163'te) — DML ile DDL aynı migration'da karıştırılmaz."""
from django.db import migrations


def ocean_ayarla(apps, schema_editor):
    Cari = apps.get_model("core", "Cari")
    Cari.objects.filter(muhasebe_kodu="320.40.0013", silindi=False).update(
        kur_degerleme="HER_ZAMAN", kur_farki_hedefi="HESAP_646_656")


def geri(apps, schema_editor):
    Cari = apps.get_model("core", "Cari")
    Cari.objects.filter(muhasebe_kodu="320.40.0013").update(
        kur_degerleme="OTOMATIK", kur_farki_hedefi="OTOMATIK")


class Migration(migrations.Migration):
    dependencies = [("core", "0163_cari_kur_degerleme_ve_farki_hedefi")]
    operations = [migrations.RunPython(ocean_ayarla, geri)]
