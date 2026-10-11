from django.db import migrations

# YALNIZ VERİ (şema yok): satış ürünü işareti ham madde (150) / yarı mamul (151) köklerindeki kartlardan kaldırılır (kural:
# core.services.stok._grup_dogrula / kategori.satis_urunu_olabilir_mi).
# Canlıda yanlışlıkla işaretlenmiş 6 adet 151-99 PAKETSİZ ÇİFT ÇIKIŞLI kart (kopyalamayla çoğalmıştı) teklif/proforma listesinden çıkar.
# Başka grubu olmayan kart (yalnız satış) dokunulmaz (en az bir grup kuralı); kapsam dışı kalan varsa konsola yazılır.


def kok_kodu(kategori):
    k = kategori
    while k.ust_id:
        k = k.ust
    return k.kod


def temizle(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    degisen, atlanan = [], []
    for s in Stok.objects.filter(satis_urunu=True).select_related("kategori__ust__ust"):
        if kok_kodu(s.kategori) not in ("150", "151"):
            continue
        if not (s.satinalma_urunu or s.uretim_urunu):
            atlanan.append(s.kod)
            continue
        Stok.objects.filter(pk=s.pk).update(satis_urunu=False)
        degisen.append(s.kod)
    if degisen or atlanan:
        print(f"0214: satis_urunu kaldirilan {len(degisen)}: {degisen} | atlanan (tek grup) {len(atlanan)}: {atlanan}")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0213_uretim_ayari_varyant_plan"),
    ]

    operations = [
        migrations.RunPython(temizle, migrations.RunPython.noop),
    ]
