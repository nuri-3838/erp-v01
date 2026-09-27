from django.db import migrations


def _veri_tasi(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayTipTanim = apps.get_model("core", "AdayTipTanim")
    AdayPotansiyelTanim = apps.get_model("core", "AdayPotansiyelTanim")
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")

    for t in AdayTipTanim.objects.all():
        AdayMusteri.objects.filter(tip=t.sistem_kodu).update(tip_fk_id=t.pk)
    for p in AdayPotansiyelTanim.objects.all():
        AdayMusteri.objects.filter(potansiyel=p.sistem_kodu).update(potansiyel_fk_id=p.pk)
    for a in AdayAsamaTanim.objects.all():
        AdayMusteri.objects.filter(asama=a.sistem_kodu).update(asama_fk_id=a.pk)


def _veri_geri_al(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayTipTanim = apps.get_model("core", "AdayTipTanim")
    AdayPotansiyelTanim = apps.get_model("core", "AdayPotansiyelTanim")
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")

    for t in AdayTipTanim.objects.all():
        AdayMusteri.objects.filter(tip_fk_id=t.pk).update(tip=t.sistem_kodu)
    for p in AdayPotansiyelTanim.objects.all():
        AdayMusteri.objects.filter(potansiyel_fk_id=p.pk).update(potansiyel=p.sistem_kodu)
    AdayMusteri.objects.filter(potansiyel_fk_id__isnull=True).update(potansiyel="")
    for a in AdayAsamaTanim.objects.all():
        AdayMusteri.objects.filter(asama_fk_id=a.pk).update(asama=a.sistem_kodu)


class Migration(migrations.Migration):
    """0132'nin devamı — SADECE veri taşıma (şema değişikliği YOK). Bilinçli olarak ayrı
    migration (bkz. 0132'nin sonundaki not): bu RunPython kendi transaction'ında COMMIT
    olur, sonraki şema temizliği (0134) YENİ bir transaction'da başlar — Postgres'in
    "pending trigger events" kısıtı DML ile DDL'i aynı transaction'da karıştırınca devreye
    giriyor, ikisini ayrı transaction'lara bölmek sorunu çözer."""

    dependencies = [
        ('core', '0132_aday_tip_potansiyel_asama_tanim'),
    ]

    operations = [
        migrations.RunPython(_veri_tasi, _veri_geri_al),
    ]
