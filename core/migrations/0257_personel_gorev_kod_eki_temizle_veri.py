"""TEK SEFERLİK veri: görev alanındaki "- NNNN.NN" meslek kodu eki temizlenir ("BEDEN İŞÇİSİ (GENEL) - 9622.02" → "BEDEN İŞÇİSİ (GENEL)").
Yalnız ``meslek_kodu`` DOLU olan ve görevin sonundaki kod ``meslek_kodu`` ile EŞLEŞEN kayıtlar; kod farklıysa (ya da görev desene uymuyorsa) DOKUNULMAZ,
farklı kodlular uyarı olarak raporlanır. Meslek kodu/adı alanlarına dokunulmaz (0256 doldurdu). ``.update()`` ile yazılır (updated_at/updated_by değişmez).
Yalnız veri; tekrar çalıştırmak güvenlidir (temizlenmiş görev desene uymaz)."""
import re

from django.db import migrations

DESEN = re.compile(r"^(?P<ad>.+?)\s*[-–—]\s*(?P<kod>\d{4}\.\d{2})\s*$")


def temizle(apps, schema_editor):
    Personel = apps.get_model("core", "Personel")
    temizlendi, farkli_kod = [], []
    for p in Personel.objects.order_by("pk"):
        kod = (p.meslek_kodu or "").strip()
        m = DESEN.match((p.gorev or "").strip()) if kod else None
        if not m:
            continue
        if m.group("kod") != kod:
            farkli_kod.append((p.pk, p.gorev, kod))
            continue
        Personel.objects.filter(pk=p.pk).update(gorev=m.group("ad").strip())
        temizlendi.append(p.pk)
    if temizlendi:
        print(f"BİLGİ (0257): görevdeki meslek kodu eki temizlendi: {len(temizlendi)} personel {temizlendi}.")
    for pk, gorev, kod in farkli_kod:
        print(f"UYARI (0257): personel #{pk} görevindeki kod meslek_kodu ({kod}) ile eşleşmiyor ({gorev!r}); dokunulmadı.")


class Migration(migrations.Migration):

    dependencies = [("core", "0256_personel_gorevden_meslek_kodu_veri")]

    operations = [migrations.RunPython(temizle, migrations.RunPython.noop)]
