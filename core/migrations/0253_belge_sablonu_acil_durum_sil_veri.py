"""TEK SEFERLİK veri: pasif «ACİL DURUM İLETİŞİM VE İLK MÜDAHALE BİLGİ FORMU» şablonu (kod OZEL_0004) kullanıcının isteğiyle TAMAMEN silinir.
Şablon silme özelliği EKLENMEDİ (yalnız bu migration). Güvenlik: şablon pasif değilse ya da bu başlıkla üretilmiş bir özlük belgesi (açıklamasında başlığı
geçen) varsa SİLİNMEZ, uyarı basılır. Yalnız veri (şema 0254'te; Postgres'te DML+DDL aynı migration'da olmaz)."""
from django.db import migrations

KOD = "OZEL_0004"
BASLIK = "ACİL DURUM İLETİŞİM VE İLK MÜDAHALE BİLGİ FORMU"


def sil(apps, schema_editor):
    BelgeSablonu = apps.get_model("core", "BelgeSablonu")
    PersonelBelge = apps.get_model("core", "PersonelBelge")
    sablonlar = BelgeSablonu.objects.filter(kod=KOD, baslik=BASLIK)
    if not sablonlar.exists():
        return                                                     # yok (test/boş/başka veritabanı): sessizce geç
    if sablonlar.filter(aktif=True).exists():
        print(f"UYARI (0253): {KOD} AKTİF; silinmedi.")
        return
    if PersonelBelge.objects.filter(aciklama__icontains=BASLIK[:30]).exists():
        print(f"UYARI (0253): {KOD} başlığıyla üretilmiş özlük belgesi var; şablon silinmedi.")
        return
    sablonlar.delete()
    print(f"BİLGİ (0253): {KOD} ({BASLIK}) silindi.")


class Migration(migrations.Migration):

    dependencies = [("core", "0252_belge_sablon_sabit_dort_veri")]

    operations = [migrations.RunPython(sil, migrations.RunPython.noop)]
