# İş İstasyonu VERİ migration'ı (SAF VERİ — şema 0204'te): eski sabit fason istasyonu kodu "10" → fason=True + varsayılan fasoncu
# 320-30-0040 (FASON GÜRBÜZ LAZER; kodla aranır, yoksa boş kalır); mevcut istasyonlara kod sırasıyla 10, 20, 30… sıra verilir.
from django.db import migrations

FASON_KODU = "10"
FASONCU_KODU = "320-30-0040"


def ileri(apps, schema_editor):
    IsIstasyonu = apps.get_model("core", "IsIstasyonu")
    Cari = apps.get_model("core", "Cari")
    fasoncu = Cari.objects.filter(kod=FASONCU_KODU, silindi=False).first()
    for i, ist in enumerate(IsIstasyonu.objects.filter(silindi=False).order_by("kod", "pk"), start=1):
        ist.sira = i * 10
        if ist.kod == FASON_KODU:
            ist.fason = True
            ist.fasoncu = fasoncu
        ist.save(update_fields=["sira", "fason", "fasoncu"])


def geri(apps, schema_editor):
    IsIstasyonu = apps.get_model("core", "IsIstasyonu")
    IsIstasyonu.objects.update(sira=0, fason=False, fasoncu=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0204_is_istasyonu_fason_sira"),
    ]

    operations = [
        migrations.RunPython(ileri, geri),
    ]
