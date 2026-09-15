# Elle yazıldı (makemigrations otomatik algılayıcısı yeni UretimEmri modelini eskisiyle
# aynı model sayıp ALTER önermeye çalışıyor ve etkileşimli varsayılan değer soruyordu —
# 0110'da GERÇEK veri yok, bu yüzden temiz bir DeleteModel + CreateModel seti elle yazıldı).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0111_stok_tedarikci_adi'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveField(
            model_name='stokhareket',
            name='uretim_emri_satir',
        ),
        migrations.DeleteModel(
            name='UretimEmriSatir',
        ),
        migrations.DeleteModel(
            name='UretimEmri',
        ),
        migrations.DeleteModel(
            name='UrunAgaciSatir',
        ),
        migrations.DeleteModel(
            name='UrunAgaci',
        ),
        migrations.CreateModel(
            name='IsIstasyonu',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('kod', models.CharField(max_length=20, verbose_name='kod')),
                ('ad', models.CharField(max_length=100, verbose_name='ad')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'iş istasyonu',
                'verbose_name_plural': 'iş istasyonları',
                'db_table': 'core_is_istasyonu',
                'ordering': ['kod'],
            },
        ),
        migrations.CreateModel(
            name='Operasyon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('cikti_miktar', models.DecimalField(decimal_places=3, default=1, max_digits=18, verbose_name='çıktı miktarı (1 çalıştırma için)')),
                ('ad', models.CharField(blank=True, default='', max_length=150, verbose_name='ad')),
                ('aciklama', models.CharField(blank=True, default='', max_length=300, verbose_name='açıklama')),
                ('cikti', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operasyonlar', to='core.stok', verbose_name='çıktı')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('istasyon', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operasyonlar', to='core.isistasyonu', verbose_name='iş istasyonu')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'operasyon',
                'verbose_name_plural': 'operasyonlar',
                'db_table': 'core_operasyon',
                'ordering': ['istasyon__kod', 'cikti__kod'],
            },
        ),
        migrations.CreateModel(
            name='OperasyonGirdi',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('miktar', models.DecimalField(decimal_places=3, max_digits=18, verbose_name='miktar (1 çalıştırma için)')),
                ('sira', models.PositiveSmallIntegerField(default=0, verbose_name='sıra')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('girdi', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operasyon_girdi_kullanimlari', to='core.stok', verbose_name='girdi')),
                ('operasyon', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='girdiler', to='core.operasyon')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'operasyon girdisi',
                'verbose_name_plural': 'operasyon girdileri',
                'db_table': 'core_operasyon_girdi',
                'ordering': ['sira', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='UretimEmri',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('yil', models.PositiveSmallIntegerField(editable=False, verbose_name='yıl')),
                ('sira', models.PositiveIntegerField(editable=False, verbose_name='sıra')),
                ('no', models.CharField(editable=False, max_length=20, verbose_name='emir no')),
                ('hedef_miktar', models.DecimalField(decimal_places=3, max_digits=18, verbose_name='hedef miktar')),
                ('tarih', models.DateField(verbose_name='tarih')),
                ('aciklama', models.CharField(blank=True, default='', max_length=300, verbose_name='açıklama')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('depo', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uretim_emirleri', to='core.depo', verbose_name='depo')),
                ('hedef_urun', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uretim_emirleri', to='core.stok', verbose_name='hedef ürün')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'üretim emri',
                'verbose_name_plural': 'üretim emirleri',
                'db_table': 'core_uretim_emri',
                'ordering': ['-yil', '-sira'],
            },
        ),
        migrations.CreateModel(
            name='OperasyonKaydi',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('yil', models.PositiveSmallIntegerField(editable=False, verbose_name='yıl')),
                ('sira', models.PositiveIntegerField(editable=False, verbose_name='sıra')),
                ('no', models.CharField(editable=False, max_length=20, verbose_name='kayıt no')),
                ('tarih', models.DateField(verbose_name='tarih')),
                ('hedef_cikti_miktari', models.DecimalField(decimal_places=3, max_digits=18, verbose_name='hedef çıktı miktarı')),
                ('durum', models.CharField(choices=[('TASLAK', 'Taslak'), ('ONAYLI', 'Onaylı')], default='TASLAK', max_length=6, verbose_name='durum')),
                ('aciklama', models.CharField(blank=True, default='', max_length=300, verbose_name='açıklama')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('depo', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operasyon_kayitlari', to='core.depo', verbose_name='depo')),
                ('operasyon', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='kayitlar', to='core.operasyon', verbose_name='operasyon')),
                ('uretim_emri', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='operasyon_kayitlari', to='core.uretimemri', verbose_name='üretim emri')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'operasyon kaydı',
                'verbose_name_plural': 'operasyon kayıtları',
                'db_table': 'core_operasyon_kaydi',
                'ordering': ['-yil', '-sira'],
            },
        ),
        migrations.CreateModel(
            name='OperasyonKaydiGirdi',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('planlanan_miktar', models.DecimalField(decimal_places=3, max_digits=18, verbose_name='planlanan miktar')),
                ('gerceklesen_miktar', models.DecimalField(decimal_places=3, max_digits=18, verbose_name='gerçekleşen miktar')),
                ('sira', models.PositiveSmallIntegerField(default=0, verbose_name='sıra')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('girdi', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='operasyon_kaydi_kullanimlari', to='core.stok', verbose_name='girdi')),
                ('kayit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='girdi_satirlari', to='core.operasyonkaydi')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'operasyon kaydı girdisi',
                'verbose_name_plural': 'operasyon kaydı girdileri',
                'db_table': 'core_operasyon_kaydi_girdi',
                'ordering': ['sira', 'pk'],
            },
        ),
        migrations.AddField(
            model_name='stokhareket',
            name='operasyon_kaydi_girdi',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stok_hareketleri', to='core.operasyonkaydigirdi', verbose_name='kaynak operasyon kaydı girdisi'),
        ),
        migrations.AddConstraint(
            model_name='isistasyonu',
            constraint=models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('kod',), name='uq_is_istasyonu_kod_aktif'),
        ),
        migrations.AddConstraint(
            model_name='isistasyonu',
            constraint=models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('ad',), name='uq_is_istasyonu_ad_aktif'),
        ),
        migrations.AddConstraint(
            model_name='operasyon',
            constraint=models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('cikti',), name='uq_operasyon_cikti_aktif'),
        ),
        migrations.AddConstraint(
            model_name='operasyon',
            constraint=models.CheckConstraint(condition=models.Q(('cikti_miktar__gt', 0)), name='ck_operasyon_cikti_miktar_gt0'),
        ),
        migrations.AddConstraint(
            model_name='operasyongirdi',
            constraint=models.CheckConstraint(condition=models.Q(('miktar__gt', 0)), name='ck_operasyon_girdi_miktar_gt0'),
        ),
        migrations.AddConstraint(
            model_name='operasyongirdi',
            constraint=models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('operasyon', 'girdi'), name='uq_operasyon_girdi_aktif'),
        ),
        migrations.AddConstraint(
            model_name='uretimemri',
            constraint=models.UniqueConstraint(fields=('yil', 'sira'), name='uq_uretim_emri_yil_sira'),
        ),
        migrations.AddConstraint(
            model_name='uretimemri',
            constraint=models.CheckConstraint(condition=models.Q(('hedef_miktar__gt', 0)), name='ck_uretim_emri_hedef_miktar_gt0'),
        ),
        migrations.AddConstraint(
            model_name='operasyonkaydi',
            constraint=models.UniqueConstraint(fields=('yil', 'sira'), name='uq_operasyon_kaydi_yil_sira'),
        ),
        migrations.AddConstraint(
            model_name='operasyonkaydi',
            constraint=models.CheckConstraint(condition=models.Q(('hedef_cikti_miktari__gt', 0)), name='ck_operasyon_kaydi_hedef_gt0'),
        ),
        migrations.AddConstraint(
            model_name='operasyonkaydigirdi',
            constraint=models.CheckConstraint(condition=models.Q(('planlanan_miktar__gt', 0)), name='ck_operasyon_kaydi_girdi_planlanan_gt0'),
        ),
        migrations.AddConstraint(
            model_name='operasyonkaydigirdi',
            constraint=models.CheckConstraint(condition=models.Q(('gerceklesen_miktar__gte', 0)), name='ck_operasyon_kaydi_girdi_gerceklesen_gte0'),
        ),
    ]
