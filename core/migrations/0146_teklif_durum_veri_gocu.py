from django.db import migrations

# Satış Teklifi (belge_tur=TEKLIF, yon=SATIS) için yeni Gönderildi/Kabul/Red/Süresi Doldu/
# İptal akışına geçiş — yalnız bu belge türü/yönü etkilenir, diğer 6 kombinasyon (Satınalma
# Teklif/Sipariş/İrsaliye, Satış Proforma/Sipariş) TASLAK/ONAYLI ile aynen kalır.
# silindi=True (zaten iptal edilmiş) kayıtlara dokunulmaz — görünürlükleri değişmeyecek.
HARITA = {
    "TASLAK": "GONDERILDI",
    "ONAYLI": "KABUL",
}


def veriyi_tasi(apps, schema_editor):
    TeklifSiparis = apps.get_model("core", "TeklifSiparis")
    for eski, yeni in HARITA.items():
        TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", silindi=False, durum=eski
        ).update(durum=yeni)


def geri_al(apps, schema_editor):
    TeklifSiparis = apps.get_model("core", "TeklifSiparis")
    for eski, yeni in HARITA.items():
        TeklifSiparis.objects.filter(
            belge_tur="TEKLIF", yon="SATIS", silindi=False, durum=yeni
        ).update(durum=eski)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0145_teklif_durum_genisletme"),
    ]

    operations = [
        migrations.RunPython(veriyi_tasi, geri_al),
    ]
