"""Operasyon ÜRET/PARÇALA geçişi — SAF VERİ: her aktif operasyon için OperasyonCikti satırları (sıra 0 = ana/referans çıktı, surucu=True;
yan çıktılar sıra 10, 20… surucu=False). Tür hepsi ÜRET, pay anahtarı BOY (mevcut davranış). Onaylı kayıt snapshot'larına dokunmaz.
Geri alma: yalnız bu geçişte üretilen tanım satırları silinir (tanım tabloları ve snapshot'lar aynen kalır)."""
from django.db import migrations


def ileri(apps, schema_editor):
    Operasyon = apps.get_model("core", "Operasyon")
    OperasyonCikti = apps.get_model("core", "OperasyonCikti")
    OperasyonYanCikti = apps.get_model("core", "OperasyonYanCikti")
    for op in Operasyon.objects.filter(silindi=False).iterator():
        if OperasyonCikti.objects.filter(operasyon=op, silindi=False).exists():      # idempotent (yeniden koşulsa)
            continue
        OperasyonCikti.objects.create(operasyon=op, stok_id=op.cikti_id, miktar=op.cikti_miktar, boy_mm=op.boy_mm, sira=0, surucu=True,
                                      created_by_id=op.created_by_id, updated_by_id=op.updated_by_id)
        for i, y in enumerate(OperasyonYanCikti.objects.filter(operasyon=op, silindi=False).order_by("sira", "pk"), start=1):
            OperasyonCikti.objects.create(operasyon=op, stok_id=y.stok_id, miktar=y.miktar, boy_mm=y.boy_mm, sira=i * 10, surucu=False,
                                          created_by_id=y.created_by_id, updated_by_id=y.updated_by_id)


def geri(apps, schema_editor):
    apps.get_model("core", "OperasyonCikti").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0194_operasyon_cikti_tur_pay_anahtari")]
    operations = [migrations.RunPython(ileri, geri)]
