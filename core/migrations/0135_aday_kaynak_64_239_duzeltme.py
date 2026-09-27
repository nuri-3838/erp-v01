from django.db import migrations


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
        print("UYARI (0135): '01-01' kodlu kaynak (ESKİ FİRMA) bulunamadı; "
              "#64/#239 veri düzeltmesi atlandı.")
        return
    guncellenen = AdayMusteri.objects.filter(pk__in=(64, 239), silindi=False).update(
        kategori=hedef)
    print(f"BİLGİ (0135): {guncellenen} aday (#64/#239) kaynağı '01-01 ESKİ FİRMA' "
          f"olarak güncellendi.")


class Migration(migrations.Migration):
    """0134'ün devamı — bilinçli olarak AYRI migration: 0134 saf DDL, bu migration saf DML
    (RunPython). Aynı transaction'da DML+DDL karışımının Postgres'te "pending trigger
    events" hatasına yol açtığı bu oturumda canlıda doğrulandı (bkz. 0132/0133/0134'ün
    kendi notları) — güvenli taraf: hiçbir migration'da ikisini bir arada tutmamak."""

    dependencies = [
        ('core', '0134_aday_tip_potansiyel_asama_finalize'),
    ]

    operations = [
        migrations.RunPython(_veri_duzeltmesi_64_239, migrations.RunPython.noop),
    ]
