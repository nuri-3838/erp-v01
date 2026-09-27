import django.db.models.deletion
from django.db import migrations, models


def _veri_duzeltmesi_64_239(apps, schema_editor):
    """Cariye dönüşmüş, salt okunur iki aday (#64, #239) DATA üst kaynağında kalmıştı
    (ekrandan değiştirilemiyor) — Kaynak kodu 01-01 (ESKİ FİRMA) olan kayda taşınır. Kod ile
    bulunur, id sabitlenmez; bulunamazsa migration hata vermeden atlar."""
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayMusteriKategori = apps.get_model("core", "AdayMusteriKategori")
    hedef = AdayMusteriKategori.objects.filter(
        silindi=False, kod="01", ust__isnull=False, ust__silindi=False,
        ust__kod="01", ust__ust__isnull=True).first()
    if hedef is None:
        print("UYARI (0133): '01-01' kodlu kaynak (ESKİ FİRMA) bulunamadı; "
              "#64/#239 veri düzeltmesi atlandı.")
        return
    guncellenen = AdayMusteri.objects.filter(pk__in=(64, 239), silindi=False).update(
        kategori=hedef)
    print(f"BİLGİ (0133): {guncellenen} aday (#64/#239) kaynağı '01-01 ESKİ FİRMA' "
          f"olarak güncellendi.")


class Migration(migrations.Migration):
    """0132'nin devamı — bilinçli olarak AYRI migration (bkz. 0132'nin sonundaki not):
    RunPython'un toplu veri taşımasının kendi transaction'ında COMMIT olmasından SONRA,
    YENİ bir transaction'da şema temizliğine (eski CharField sütunlarını kaldırma) başlar."""

    dependencies = [
        ('core', '0132_aday_tip_potansiyel_asama_tanim'),
    ]

    operations = [
        migrations.RemoveField(model_name='adaymusteri', name='tip'),
        migrations.RemoveField(model_name='adaymusteri', name='potansiyel'),
        migrations.RemoveField(model_name='adaymusteri', name='asama'),
        migrations.RenameField(model_name='adaymusteri', old_name='tip_fk', new_name='tip'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='potansiyel_fk', new_name='potansiyel'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='asama_fk', new_name='asama'),
        migrations.AlterField(
            model_name='adaymusteri',
            name='tip',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaytiptanim', verbose_name='tip'),
        ),
        migrations.AlterField(
            model_name='adaymusteri',
            name='asama',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adayasamatanim',
                verbose_name='aşama'),
        ),
        migrations.RunPython(_veri_duzeltmesi_64_239, migrations.RunPython.noop),
    ]
