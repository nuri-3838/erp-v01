from django.db import migrations

# 0143, Yükleme Şekli (Incoterm) "kod" atamasını orijinal seed metinleriyle BİREBİR eşleşmeye
# göre yaptı — ama bu liste admin tarafından düzenlenebilir (AYARLAR > Tanım Listeleri) ve
# canlıda satırlar zaten yeniden adlandırılmış ("FOB İZMİR" -> "FOB MERSİN-TÜRKİYE", "EXW
# İZMİR (FABRİKA TESLİM)" -> "EXW – KAYSERİ, TÜRKİYE", "NAKLİYE DAHİL (YURT İÇİ)" ->
# "NAKLİYE DAHİL") — bu yüzden 0143 bu satırları atladı (kod boş kaldı, teklif PDF'i güvenli
# varsayılana düştü). Bu migration, ÖNEK (prefix) eşleşmesiyle aynı atamayı daha dayanıklı
# şekilde tekrarlar — admin ad'ı değiştirse bile Incoterm kodu (FOB/EXW/CFR/CIF/DAP/NAKLİYE
# DAHİL/NAKLİYE HARİÇ) başta kaldığı sürece çalışır.
ONEK_HARITASI = [
    ("FOB", "FOB"),
    ("EXW", "EXW"),
    ("CFR", "CFR"),
    ("CIF", "CIF"),
    ("DAP", "DAP"),
    ("NAKLİYE DAHİL", "NAKLIYE_DAHIL"),
    ("NAKLİYE HARİÇ", "NAKLIYE_HARIC"),
]


def _kod_tespit_et(ad):
    u = (ad or "").upper()
    for onek, kod in ONEK_HARITASI:
        if u == onek or u.startswith(onek + " ") or u.startswith(onek + "-") \
                or u.startswith(onek + "–"):
            return kod
    return None


def kod_ata(apps, schema_editor):
    TanimSecenegi = apps.get_model("core", "TanimSecenegi")
    for satir in TanimSecenegi.objects.filter(
            kategori="YUKLEME_SEKLI", silindi=False, kod=""):
        kod = _kod_tespit_et(satir.ad)
        if kod:
            satir.kod = kod
            satir.save(update_fields=["kod"])


def kod_geri_al(apps, schema_editor):
    # 0143'ün kendi geri alması zaten kendi atadığı kodları temizliyor; burada yalnız bu
    # migration'ın EK atadığı (0143'ün atlamış olduğu, admin tarafından yeniden adlandırılmış)
    # satırları aynı öneklerle tekrar bulup temizlemek pratik değil (ad metninden kodu geri
    # türetmek gerekir) — geri alma bilinçli olarak no-op, kod kalması veri kaybı değildir.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0143_yukleme_sekli_kod"),
    ]

    operations = [
        migrations.RunPython(kod_ata, kod_geri_al),
    ]
