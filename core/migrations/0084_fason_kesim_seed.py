from django.db import migrations

# Kullanıcının fasoncuya gönderdiği ayak profillerinin gerçek Stok kartlarına göre
# (kod ile, üretim DB'sinde doğrulandı — kalıp no + boy zaten Stok.ad'de var, burada
# TEKRARLANMIYOR). Yerel/başka bir ortamda bu stok kodları yoksa satır sessizce atlanır
# (crash etmez) — bu yüzden hem local dev'de (stoklar yok, 0 satır) hem üretimde (10 satır)
# güvenle çalışır.
#
# SEED: (profil_kod, parca_adi, adet, [urun_model_kodu, ...])
SEED = [
    ("150-10-0002", "ÖN AYAK", 1, ["A21"]),
    ("150-10-0003", "ÖN AYAK", 1, ["A31"]),
    ("150-10-0004", "ÖN AYAK", 1, ["A41"]),
    ("150-10-0005", "ÖN AYAK", 1, ["A51"]),
    ("150-10-0006", "ÖN AYAK", 1, ["A61"]),
    ("150-10-0007", "SAĞ", 2, ["C22", "C33", "C44", "C55", "C66", "C77"]),
    ("150-10-0007", "SOL", 2, ["C22", "C33", "C44", "C55", "C66", "C77"]),
    ("150-10-0008", "ARKA AYAK", 2, ["A21", "A51"]),
    ("150-10-0009", "ARKA AYAK", 2, ["A31"]),
    ("150-10-0010", "ARKA AYAK", 2, ["A41", "A61"]),
]


def seed(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    FasonKesim = apps.get_model("core", "FasonKesim")
    for sira, (profil_kod, parca_adi, adet, urun_kodlari) in enumerate(SEED, start=1):
        profil = Stok.objects.filter(kod=profil_kod, silindi=False).first()
        if not profil:
            continue                                    # bu ortamda profil stoku yok — atla
        urunler = list(Stok.objects.filter(
            model_kodu__in=urun_kodlari, satis_urunu=True, silindi=False))
        if not urunler:
            continue                                    # bitmiş ürün stokları da yok — atla
        k, _ = FasonKesim.objects.get_or_create(
            profil=profil, parca_adi=parca_adi, silindi=False,
            defaults={"adet": adet, "sira": sira * 10})
        k.urunler.set(urunler)


def geri_al(apps, schema_editor):
    FasonKesim = apps.get_model("core", "FasonKesim")
    for profil_kod, parca_adi, _adet, _urun_kodlari in SEED:
        FasonKesim.objects.filter(
            profil__kod=profil_kod, parca_adi=parca_adi).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0083_fason_kesim"),
    ]

    operations = [
        migrations.RunPython(seed, geri_al),
    ]
