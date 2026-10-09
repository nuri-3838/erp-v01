"""Veri: girdilerinden en az birinin üretim birimi BOY olan aktif operasyonlarda tam_calistirma=True (kesim: tam boy). Diğerleri False kalır
(kayış gibi MT birimli kesimler). Yalnız veri; şema 0177'de (DML+DDL aynı migration'da olmaz)."""
from django.db import migrations


def isaretle(apps, schema_editor):
    Operasyon = apps.get_model("core", "Operasyon")
    OperasyonGirdi = apps.get_model("core", "OperasyonGirdi")
    idler = set(OperasyonGirdi.objects.filter(
        silindi=False, operasyon__silindi=False).filter(
        girdi__uretim_birimi__ad__iexact="BOY").values_list("operasyon_id", flat=True))
    idler |= set(OperasyonGirdi.objects.filter(
        silindi=False, operasyon__silindi=False).filter(
        girdi__uretim_birimi__kisa_ad__iexact="BOY").values_list("operasyon_id", flat=True))
    if idler:
        Operasyon.objects.filter(pk__in=idler, silindi=False).update(tam_calistirma=True)


def geri(apps, schema_editor):
    apps.get_model("core", "Operasyon").objects.update(tam_calistirma=False)


class Migration(migrations.Migration):
    dependencies = [("core", "0177_operasyon_tam_calistirma")]
    operations = [migrations.RunPython(isaretle, geri)]
