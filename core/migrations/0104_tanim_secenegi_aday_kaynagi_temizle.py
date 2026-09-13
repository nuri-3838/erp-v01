from django.db import migrations
from django.utils import timezone

# Aday Kaynağı (ADAY_KAYNAGI) kategorisi kaldırıldı (0103) - kullanıcı Aday Müşteri'yi
# Kategori/Para Birimi/İskonto yapısına basitleştirdi, "kaynak" alanı artık yok. 0102'nin
# seed ettiği 6 satırı burada soft-delete ediyoruz (fiziksel silme yok, invariant).
SEED_ADLAR = ["REFERANS", "WEB SİTESİ", "FUAR / ETKİNLİK", "SOĞUK ARAMA", "SOSYAL MEDYA",
             "DİĞER"]


def temizle(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    TanimSecenegi.objects.filter(
        kategori="ADAY_KAYNAGI", ad__in=SEED_ADLAR, silindi=False
    ).update(silindi=True, silindi_at=timezone.now())


def geri_al(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    TanimSecenegi.objects.filter(
        kategori="ADAY_KAYNAGI", ad__in=SEED_ADLAR).update(silindi=False, silindi_at=None)


class Migration(migrations.Migration):

    dependencies = [("core", "0103_crm_aday_kategori_basitlestir")]
    operations = [migrations.RunPython(temizle, geri_al)]
