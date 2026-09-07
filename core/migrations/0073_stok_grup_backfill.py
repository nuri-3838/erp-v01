from django.db import migrations


def gruplari_doldur(apps, schema_editor):
    """Var olan stok kartlarını üst kategoriye göre gruplara ata:
    MAMULLER (kod 152) -> Üretim+Satış; diğerleri (Hammadde/Yarı Mamul) -> Satınalma+Üretim."""
    Stok = apps.get_model("core", "Stok")
    for s in Stok.objects.select_related("kategori__ust").all():
        ust_kod = s.kategori.ust.kod if s.kategori and s.kategori.ust else None
        if ust_kod == "152":
            s.uretim_urunu, s.satis_urunu = True, True
        else:
            s.satinalma_urunu, s.uretim_urunu = True, True
        s.save(update_fields=["satinalma_urunu", "uretim_urunu", "satis_urunu"])


def geri_al(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    Stok.objects.update(satinalma_urunu=False, uretim_urunu=False, satis_urunu=False)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0072_stok_grup_ve_teknik_alanlar"),
    ]

    operations = [
        migrations.RunPython(gruplari_doldur, geri_al),
    ]
