"""Faturalar ekranı Alış/Satış olarak ikiye ayrıldı. Mevcut `faturalar` ekran
yetkisi olan kullanıcılar hem `alis_faturalari` hem `satis_faturalari` yetkisine
taşınır (kimse erişim kaybetmesin); eski `faturalar` satırı silinir."""
from django.db import migrations


def ileri(apps, schema_editor):
    EkranYetki = apps.get_model("core", "EkranYetki")
    kullanici_idleri = list(
        EkranYetki.objects.filter(ekran_kod="faturalar")
        .values_list("kullanici_id", flat=True)
    )
    for kid in kullanici_idleri:
        for yeni in ("alis_faturalari", "satis_faturalari"):
            EkranYetki.objects.get_or_create(kullanici_id=kid, ekran_kod=yeni)
    EkranYetki.objects.filter(ekran_kod="faturalar").delete()


def geri(apps, schema_editor):
    # Geri alınamaz veri dönüşümü; yeni kodlar olduğu gibi kalır.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_fatura_depo_stokhareket_fatura_satir_and_more"),
    ]

    operations = [
        migrations.RunPython(ileri, geri),
    ]
