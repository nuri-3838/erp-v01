from django.db import migrations

# 0084'te seed edilen 10 satır YANLIŞTI (kullanıcı düzeltti: bitmiş ürün → doğrudan ham
# profil değil, ara katman "kesilmiş parça" (KESİLMİŞ A TİPİ ÖN AYAK 2+1 gibi, kategori
# 151-10-xxxx) üzerinden gitmesi gerekiyordu). Şema 0086'da bu ara katmana göre yeniden
# kuruluyor; burada yalnız eski (yanlış) satırlar fiziksel olarak temizleniyor — henüz
# gerçek kullanıcı verisi/kullanımı yoktu (dakikalar önce seed edilmişti), bu yüzden
# soft-delete değil gerçek silme (aksi halde eski profil/parca_adi/urunler alanları
# kaldırılırken hem eski hem yeni şemayı temsil eden anlamsız yarım satırlar kalırdı).


def temizle(apps, schema_editor):
    FasonKesim = apps.get_model("core", "FasonKesim")
    FasonKesim.objects.all().delete()


def geri_al(apps, schema_editor):
    pass                                    # geri alınamaz — eski (yanlış) veriyi tekrar üretmeye değmez


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0084_fason_kesim_seed"),
    ]

    operations = [
        migrations.RunPython(temizle, geri_al),
    ]
