"""Sabit resmî tatillerin 2026-2027 tohumu — yalnız DML (0140'ın şeması üstüne).

Dinî bayramlar (Ramazan/Kurban) burada YOKTUR — yıldan yıla kaydığı için ekrandan elle
girilir (core.services.resmi_tatil.dini_bayram_var_mi bu satırların dışını arar).
"""
from datetime import date

from django.db import migrations

from core.metin import buyuk_harf_tr

_SABIT = (
    (1, 1, "Yılbaşı"),
    (4, 23, "23 Nisan Ulusal Egemenlik ve Çocuk Bayramı"),
    (5, 1, "1 Mayıs Emek ve Dayanışma Günü"),
    (5, 19, "19 Mayıs Atatürk'ü Anma, Gençlik ve Spor Bayramı"),
    (7, 15, "15 Temmuz Demokrasi ve Millî Birlik Günü"),
    (8, 30, "30 Ağustos Zafer Bayramı"),
    (10, 29, "29 Ekim Cumhuriyet Bayramı"),
)
_YILLAR = (2026, 2027)


def _ileri(apps, schema_editor):
    ResmiTatil = apps.get_model("core", "ResmiTatil")
    eklenen = 0
    for yil in _YILLAR:
        for ay, gun, ad in _SABIT:
            _, olusturuldu = ResmiTatil.objects.get_or_create(
                tarih=date(yil, ay, gun), silindi=False,
                defaults={"ad": buyuk_harf_tr(ad)})
            if olusturuldu:
                eklenen += 1
    print(f"Resmî tatil seed tamam: {eklenen} eklendi (sabit tatiller, "
          f"{_YILLAR[0]}-{_YILLAR[-1]}).")


def _geri_al(apps, schema_editor):
    ResmiTatil = apps.get_model("core", "ResmiTatil")
    silinen = 0
    for yil in _YILLAR:
        for ay, gun, ad in _SABIT:
            n, _ = ResmiTatil.objects.filter(
                tarih=date(yil, ay, gun), ad=buyuk_harf_tr(ad)).delete()
            silinen += n
    print(f"Resmî tatil seed geri alındı: {silinen} silindi.")


class Migration(migrations.Migration):

    dependencies = [("core", "0140_resmi_tatil")]

    operations = [migrations.RunPython(_ileri, _geri_al)]
