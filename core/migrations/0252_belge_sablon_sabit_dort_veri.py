"""4 sabit belge şablonunun YENİ alanları (belge türü, imza düzeni, imza notu) doldurulur. Metin/başlık/sıra/aktiflik DEĞİŞMEZ.
Yalnız veri (şema 0251'de; Postgres'te DML+DDL aynı migration'da olmaz)."""
from django.db import migrations

SABIT = {
    "SOZLESME": ("SOZLESME", "iki_taraf", "Bu sözleşmeyi okudum, anladım, bir suretini teslim aldım."),
    "KVKK_AYDINLATMA": ("KVKK", "isci", "Aydınlatma metnini okudum, bir nüshasını teslim aldım."),
    "KVKK_ACIK_RIZA": ("KVKK", "isci", "Açık rızamı özgür irademle veriyorum."),
    "FAZLA_CALISMA": ("FAZLA_MESAI_ONAYI", "iki_taraf", "Fazla çalışmaya yazılı olarak muvafakat ediyorum."),
}


def doldur(apps, schema_editor):
    BelgeSablonu = apps.get_model("core", "BelgeSablonu")
    for kod, (tur, imza, notu) in SABIT.items():
        BelgeSablonu.objects.filter(kod=kod).update(tur=tur, imza=imza, imza_notu=notu)


class Migration(migrations.Migration):

    dependencies = [("core", "0251_belge_sablon_dinamik_sema")]

    operations = [migrations.RunPython(doldur, migrations.RunPython.noop)]
