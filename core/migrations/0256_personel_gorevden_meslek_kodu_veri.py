"""TEK SEFERLİK veri: personel kartlarında görev alanı "MESLEK ADI - 9622.02" biçiminde meslek kodunu zaten içeriyor; sondaki "- NNNN.NN" kodu
``meslek_kodu``na, öncesindeki ad ``meslek_adi``na yazılır (yalnız ``meslek_kodu`` BOŞ olan kayıtlar; ad zaten doluysa korunur). GÖREV alanı olduğu gibi
KALIR. Desene uymayan (ya da görevi boş) kayıtlara DOKUNULMAZ, uyarı olarak raporlanır. ``.update()`` ile yazılır (updated_at/updated_by değişmez).
Yalnız veri; şema 0255'te (Postgres'te DML+DDL aynı migration'da olmaz). Tekrar çalıştırmak güvenlidir (dolu kod atlanır)."""
import re

from django.db import migrations

# "<ad> - NNNN.NN" (tire/kısa tire/uzun tire; çevresinde boşluk serbest)
DESEN = re.compile(r"^(?P<ad>.+?)\s*[-–—]\s*(?P<kod>\d{4}\.\d{2})\s*$")


def doldur(apps, schema_editor):
    Personel = apps.get_model("core", "Personel")
    dolduruldu, uymayan, zaten_dolu = [], [], 0
    for p in Personel.objects.order_by("pk"):
        if (p.meslek_kodu or "").strip():
            zaten_dolu += 1
            continue
        m = DESEN.match((p.gorev or "").strip())
        if not m:
            uymayan.append((p.pk, p.gorev or ""))
            continue
        alanlar = {"meslek_kodu": m.group("kod")}
        if not (p.meslek_adi or "").strip():
            alanlar["meslek_adi"] = m.group("ad").strip()
        Personel.objects.filter(pk=p.pk).update(**alanlar)
        dolduruldu.append(p.pk)
    if dolduruldu:
        print(f"BİLGİ (0256): meslek kodu görevden dolduruldu: {len(dolduruldu)} personel {dolduruldu}; zaten dolu: {zaten_dolu}.")
    for pk, gorev in uymayan:
        print(f"UYARI (0256): personel #{pk} görevi 'AD - NNNN.NN' desenine uymuyor ({gorev!r}); dokunulmadı.")


class Migration(migrations.Migration):

    dependencies = [("core", "0255_sgk_4a_muhasebe_alanlari")]

    operations = [migrations.RunPython(doldur, migrations.RunPython.noop)]
