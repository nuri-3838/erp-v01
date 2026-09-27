import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


# --- Tohum veri (spec tablosu) --------------------------------------------------------
# (sistem_kodu, ad, sira, yurtici kod_yolu, yurtdışı kod_yolu, cariye_donusturulebilir)
_TIP_TOHUM = [
    ("ESKI_MUSTERI", "Eski müşteri (Çakmak)", 1, "120-10", "120-20", True),
    ("ADAY", "Aday", 2, "120-10", "120-20", True),
    ("ARACI", "Aracı / Komisyoncu", 3, "120-10", "120-20", True),
    ("LOJISTIK", "Lojistik / Nakliye", 4, "320-40", "320-40", True),
    ("GUMRUK", "Gümrük müşaviri", 5, "320-30", "320-30", True),
    ("TEDARIKCI", "Tedarikçi", 6, "320-10", "320-10", True),
    ("RAKIP", "Rakip", 7, None, None, False),
    ("PAZAR_BILGISI", "Pazar bilgisi (firma değil)", 8, None, None, False),
]
# (sistem_kodu, ad, sira, sicak)
_POTANSIYEL_TOHUM = [
    ("YUKSEK", "Yüksek", 1, True),
    ("ORTA", "Orta", 2, False),
    ("DUSUK", "Düşük", 3, False),
]
# (sistem_kodu, ad, sira, rol)
_ASAMA_TOHUM = [
    ("YENI", "Yeni", 1, "BASLANGIC"),
    ("TEMAS", "Temas kuruldu", 2, "ARA"),
    ("ILGILI", "İlgileniyor", 3, "ARA"),
    ("TEKLIF", "Teklif verildi", 4, "ARA"),
    ("NUMUNE", "Numune", 5, "ARA"),
    ("SIPARIS", "Sipariş", 6, "ARA"),
    ("KAPALI", "Kapalı", 7, "KAPALI"),
]
_TIP_KODLARI = [k for k, *_ in _TIP_TOHUM]
_POTANSIYEL_KODLARI = [k for k, *_ in _POTANSIYEL_TOHUM]
_ASAMA_KODLARI = [k for k, *_ in _ASAMA_TOHUM]


def _cari_kategori_bul(CariKategori, kod_yolu):
    if not kod_yolu:
        return None
    ust_kod, alt_kod = kod_yolu.split("-")
    return CariKategori.objects.filter(
        silindi=False, kod=alt_kod, ust__silindi=False, ust__kod=ust_kod).first()


def _tohum_olustur(apps, schema_editor):
    AdayTipTanim = apps.get_model("core", "AdayTipTanim")
    AdayPotansiyelTanim = apps.get_model("core", "AdayPotansiyelTanim")
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")
    CariKategori = apps.get_model("core", "CariKategori")

    for kod, ad, sira, yi, yd, donusturulebilir in _TIP_TOHUM:
        AdayTipTanim.objects.create(
            ad=ad, sira=sira, sistem_kodu=kod,
            cari_kategori_yurtici=_cari_kategori_bul(CariKategori, yi),
            cari_kategori_yurtdisi=_cari_kategori_bul(CariKategori, yd),
            cariye_donusturulebilir=donusturulebilir)

    for kod, ad, sira, sicak in _POTANSIYEL_TOHUM:
        AdayPotansiyelTanim.objects.create(ad=ad, sira=sira, sistem_kodu=kod, sicak=sicak)

    for kod, ad, sira, rol in _ASAMA_TOHUM:
        AdayAsamaTanim.objects.create(ad=ad, sira=sira, sistem_kodu=kod, rol=rol)


def _tohum_geri_al(apps, schema_editor):
    apps.get_model("core", "AdayTipTanim").objects.filter(
        sistem_kodu__in=_TIP_KODLARI).delete()
    apps.get_model("core", "AdayPotansiyelTanim").objects.filter(
        sistem_kodu__in=_POTANSIYEL_KODLARI).delete()
    apps.get_model("core", "AdayAsamaTanim").objects.filter(
        sistem_kodu__in=_ASAMA_KODLARI).delete()


def _veri_tasi(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayTipTanim = apps.get_model("core", "AdayTipTanim")
    AdayPotansiyelTanim = apps.get_model("core", "AdayPotansiyelTanim")
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")

    for t in AdayTipTanim.objects.all():
        AdayMusteri.objects.filter(tip=t.sistem_kodu).update(tip_fk_id=t.pk)
    for p in AdayPotansiyelTanim.objects.all():
        AdayMusteri.objects.filter(potansiyel=p.sistem_kodu).update(potansiyel_fk_id=p.pk)
    for a in AdayAsamaTanim.objects.all():
        AdayMusteri.objects.filter(asama=a.sistem_kodu).update(asama_fk_id=a.pk)


def _veri_geri_al(apps, schema_editor):
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayTipTanim = apps.get_model("core", "AdayTipTanim")
    AdayPotansiyelTanim = apps.get_model("core", "AdayPotansiyelTanim")
    AdayAsamaTanim = apps.get_model("core", "AdayAsamaTanim")

    for t in AdayTipTanim.objects.all():
        AdayMusteri.objects.filter(tip_fk_id=t.pk).update(tip=t.sistem_kodu)
    for p in AdayPotansiyelTanim.objects.all():
        AdayMusteri.objects.filter(potansiyel_fk_id=p.pk).update(potansiyel=p.sistem_kodu)
    AdayMusteri.objects.filter(potansiyel_fk_id__isnull=True).update(potansiyel="")
    for a in AdayAsamaTanim.objects.all():
        AdayMusteri.objects.filter(asama_fk_id=a.pk).update(asama=a.sistem_kodu)


def _veri_duzeltmesi_64_239(apps, schema_editor):
    """Cariye dönüşmüş, salt okunur iki aday (#64, #239) DATA üst kaynağında kalmıştı
    (ekrandan değiştirilemiyor) — Kaynak kodu 01-01 (ESKİ FİRMA) olan kayda taşınır. Kod ile
    bulunur, id sabitlenmez; bulunamazsa migration hata vermeden atlar."""
    AdayMusteri = apps.get_model("core", "AdayMusteri")
    AdayMusteriKategori = apps.get_model("core", "AdayMusteriKategori")
    hedef = AdayMusteriKategori.objects.filter(
        silindi=False, kod="01", ust__isnull=False, ust__silindi=False,
        ust__kod="01", ust__ust__isnull=True).first()
    if hedef is None:
        print("UYARI (0132): '01-01' kodlu kaynak (ESKİ FİRMA) bulunamadı; "
              "#64/#239 veri düzeltmesi atlandı.")
        return
    guncellenen = AdayMusteri.objects.filter(pk__in=(64, 239), silindi=False).update(
        kategori=hedef)
    print(f"BİLGİ (0132): {guncellenen} aday (#64/#239) kaynağı '01-01 ESKİ FİRMA' "
          f"olarak güncellendi.")


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0131_aday_cari_alani'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='adaymusterikategori',
            options={'ordering': ['kod'], 'verbose_name': 'aday kaynağı',
                     'verbose_name_plural': 'aday kaynakları'},
        ),
        migrations.AlterField(
            model_name='adaymusteri',
            name='kategori',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaymusterikategori',
                verbose_name='kaynak'),
        ),
        migrations.AlterField(
            model_name='adaymusterikategori',
            name='ust',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='alt_kategoriler', to='core.adaymusterikategori',
                verbose_name='üst kaynak'),
        ),
        migrations.CreateModel(
            name='AdayTipTanim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('ad', models.CharField(max_length=100, verbose_name='ad')),
                ('sira', models.PositiveSmallIntegerField(default=0, verbose_name='sıra')),
                ('aktif', models.BooleanField(default=True, verbose_name='aktif')),
                ('renk', models.CharField(choices=[('GRI', 'Gri'), ('MAVI', 'Mavi'), ('YESIL', 'Yeşil'), ('TURUNCU', 'Turuncu'), ('KIRMIZI', 'Kırmızı'), ('MOR', 'Mor')], default='GRI', max_length=10, verbose_name='renk')),
                ('sistem_kodu', models.CharField(blank=True, max_length=20, null=True, verbose_name='sistem kodu')),
                ('cariye_donusturulebilir', models.BooleanField(default=True, verbose_name='cariye dönüştürülebilir')),
                ('cari_kategori_yurtdisi', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.carikategori', verbose_name='yurtdışı cari kategorisi')),
                ('cari_kategori_yurtici', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.carikategori', verbose_name='yurtiçi cari kategorisi')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'aday tipi',
                'verbose_name_plural': 'aday tipleri',
                'db_table': 'aday_tip_tanim',
                'ordering': ['sira', 'ad'],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('ad',), name='uq_adaytiptanim_ad_aktif'),
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('sistem_kodu',), name='uq_adaytiptanim_kod_aktif'),
                ],
            },
        ),
        migrations.CreateModel(
            name='AdayPotansiyelTanim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('ad', models.CharField(max_length=100, verbose_name='ad')),
                ('sira', models.PositiveSmallIntegerField(default=0, verbose_name='sıra')),
                ('aktif', models.BooleanField(default=True, verbose_name='aktif')),
                ('renk', models.CharField(choices=[('GRI', 'Gri'), ('MAVI', 'Mavi'), ('YESIL', 'Yeşil'), ('TURUNCU', 'Turuncu'), ('KIRMIZI', 'Kırmızı'), ('MOR', 'Mor')], default='GRI', max_length=10, verbose_name='renk')),
                ('sistem_kodu', models.CharField(blank=True, max_length=20, null=True, verbose_name='sistem kodu')),
                ('sicak', models.BooleanField(default=False, verbose_name='Sıcak sekmesine girer')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'aday potansiyeli',
                'verbose_name_plural': 'aday potansiyelleri',
                'db_table': 'aday_potansiyel_tanim',
                'ordering': ['sira', 'ad'],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('ad',), name='uq_adaypottanim_ad_aktif'),
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('sistem_kodu',), name='uq_adaypottanim_kod_aktif'),
                ],
            },
        ),
        migrations.CreateModel(
            name='AdayAsamaTanim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='oluşturulma')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='güncellenme')),
                ('silindi', models.BooleanField(default=False, verbose_name='silindi (soft delete)')),
                ('silindi_at', models.DateTimeField(blank=True, null=True, verbose_name='silinme zamanı')),
                ('ad', models.CharField(max_length=100, verbose_name='ad')),
                ('sira', models.PositiveSmallIntegerField(default=0, verbose_name='sıra')),
                ('aktif', models.BooleanField(default=True, verbose_name='aktif')),
                ('renk', models.CharField(choices=[('GRI', 'Gri'), ('MAVI', 'Mavi'), ('YESIL', 'Yeşil'), ('TURUNCU', 'Turuncu'), ('KIRMIZI', 'Kırmızı'), ('MOR', 'Mor')], default='GRI', max_length=10, verbose_name='renk')),
                ('sistem_kodu', models.CharField(blank=True, max_length=20, null=True, verbose_name='sistem kodu')),
                ('rol', models.CharField(choices=[('BASLANGIC', 'Başlangıç'), ('ARA', 'Ara'), ('KAPALI', 'Kapalı')], default='ARA', max_length=10, verbose_name='rol')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='oluşturan')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='güncelleyen')),
            ],
            options={
                'verbose_name': 'aday aşaması',
                'verbose_name_plural': 'aday aşamaları',
                'db_table': 'aday_asama_tanim',
                'ordering': ['sira', 'ad'],
                'constraints': [
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('ad',), name='uq_adayasamatanim_ad_aktif'),
                    models.UniqueConstraint(condition=models.Q(('silindi', False)), fields=('sistem_kodu',), name='uq_adayasamatanim_kod_aktif'),
                ],
            },
        ),
        migrations.RunPython(_tohum_olustur, _tohum_geri_al),
        migrations.AddField(
            model_name='adaymusteri',
            name='tip_fk',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaytiptanim', verbose_name='tip'),
        ),
        migrations.AddField(
            model_name='adaymusteri',
            name='potansiyel_fk',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaypotansiyeltanim',
                verbose_name='potansiyel'),
        ),
        migrations.AddField(
            model_name='adaymusteri',
            name='asama_fk',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adayasamatanim',
                verbose_name='aşama'),
        ),
        migrations.RunPython(_veri_tasi, _veri_geri_al),
        migrations.RemoveField(model_name='adaymusteri', name='tip'),
        migrations.RemoveField(model_name='adaymusteri', name='potansiyel'),
        migrations.RemoveField(model_name='adaymusteri', name='asama'),
        migrations.RenameField(model_name='adaymusteri', old_name='tip_fk', new_name='tip'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='potansiyel_fk', new_name='potansiyel'),
        migrations.RenameField(
            model_name='adaymusteri', old_name='asama_fk', new_name='asama'),
        migrations.AlterField(
            model_name='adaymusteri',
            name='tip',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adaytiptanim', verbose_name='tip'),
        ),
        migrations.AlterField(
            model_name='adaymusteri',
            name='asama',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='aday_musteriler', to='core.adayasamatanim',
                verbose_name='aşama'),
        ),
        migrations.RunPython(_veri_duzeltmesi_64_239, migrations.RunPython.noop),
    ]
