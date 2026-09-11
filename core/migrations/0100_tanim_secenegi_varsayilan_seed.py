from django.db import migrations

# Kullanicinin daha once (metin eslestirmeyle, forms.py'de) belirttigi 4 varsayilan artik
# yeni varsayilan bayragiyla isaretleniyor - davranis aynen korunuyor, mekanizma degisiyor.
# (kategori, ad) ile arar, bulamazsa atlar (yerel dev'de bu satirlar yok, zararsiz no-op).
VARSAYILANLAR = [
    ("YUKLEME_SEKLI", "EXW – KAYSERİ, TÜRKİYE"),
    ("ODEME_KOSULU", "%50 PEŞİN + %50 SEVKİYAT ÖNCESİ"),
    ("YUKLEME_TIPI", "TIR"),
    ("TESLIM_SURESI", "SİPARİŞ ONAYI SONRASI 20 İŞ GÜNÜ"),
]


def seed(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, ad in VARSAYILANLAR:
        TanimSecenegi.objects.filter(
            kategori=kategori, ad=ad, silindi=False).update(varsayilan=True)


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for kategori, ad in VARSAYILANLAR:
        TanimSecenegi.objects.filter(
            kategori=kategori, ad=ad, silindi=False).update(varsayilan=False)


class Migration(migrations.Migration):

    dependencies = [("core", "0099_tanim_secenegi_varsayilan")]
    operations = [migrations.RunPython(seed, geri_al)]
