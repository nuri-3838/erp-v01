"""Operasyon ÜRET/PARÇALA geçişi — SAF VERİ: fason kaydında ayrı tutulan referans (ana) çıktı gelen adedi (``gelen_ana``) tek yapıya
(``gelen_yan`` → {stok pk: adet}, referans dahil) taşınır. Sonraki şema migration'ı ``gelen_ana``yı kaldırıp ``gelen_yan``ı ``gelen`` olarak yeniden adlandırır."""
from django.db import migrations


def ileri(apps, schema_editor):
    OperasyonKaydi = apps.get_model("core", "OperasyonKaydi")
    for k in OperasyonKaydi.objects.exclude(gelen_ana__isnull=True).select_related("operasyon").iterator():
        g = dict(k.gelen_yan or {})
        ref = str(k.operasyon.cikti_id)
        if ref not in g:
            g[ref] = format(k.gelen_ana, "f")
            k.gelen_yan = g
            k.save(update_fields=["gelen_yan"])


def geri(apps, schema_editor):
    OperasyonKaydi = apps.get_model("core", "OperasyonKaydi")
    from decimal import Decimal
    for k in OperasyonKaydi.objects.select_related("operasyon").iterator():
        g = k.gelen_yan or {}
        ref = str(k.operasyon.cikti_id)
        if ref in g:
            k.gelen_ana = Decimal(str(g[ref]))
            k.save(update_fields=["gelen_ana"])


class Migration(migrations.Migration):
    dependencies = [("core", "0195_operasyon_cikti_veri")]
    operations = [migrations.RunPython(ileri, geri)]
