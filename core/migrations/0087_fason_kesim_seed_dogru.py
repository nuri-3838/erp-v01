from django.db import migrations

# Kullanıcının düzeltmesi: "152-10-0001 (A21, 2+1) yapmak için 1 adet 151-10-0001 + 2 adet
# 151-10-0006 lazım" — bitmiş ürün (152) doğrudan ham profile (150) değil, ARA KATMAN
# "kesilmiş parça" (151, kategori KESİLMİŞ PARÇALAR) üzerinden gidiyor. İki katman:
#
#   1) HAM_PROFIL_ESLEME: kesilmiş parça (151) -> hangi ham profilden (150) kesildiği
#      (1:1, Stok.kesildigi_profil'e yazılır — o parçanın kendi tanımının sabit özelliği).
#   2) URUN_ESLEME: bitmiş ürün (152) -> hangi kesilmiş parça(lar)dan kaç adet gerektiği
#      (FasonKesim satırları — kullanıcının verdiği örnekten [1 ön+2 arka A Tipi'nde,
#      2 sağ+2 sol Çift Çıkışlı'da] tüm adım sayılarına genellendi; A Tipi'nde 5+1/6+1
#      arka ayağı SAĞ/SOL'a bölünüyor [151-10-0009..0012] — 2+1/3+1/4+1'de bu bölünme yok,
#      gerçek Stok kartı isimlendirmesinden görüldü).
#
# Her iki eşleme de kod ile arar, bulamazsa sessizce atlar (local dev'de bu stoklar yok).

HAM_PROFIL_ESLEME = {
    # kesilmis_parca_kod: ham_profil_kod
    "151-10-0001": "150-10-0002", "151-10-0002": "150-10-0003",
    "151-10-0003": "150-10-0004", "151-10-0004": "150-10-0005",
    "151-10-0005": "150-10-0006",
    "151-10-0006": "150-10-0008", "151-10-0007": "150-10-0009",
    "151-10-0008": "150-10-0010",
    "151-10-0009": "150-10-0008", "151-10-0010": "150-10-0008",   # 5+1 SAĞ/SOL, aynı ham profil
    "151-10-0011": "150-10-0010", "151-10-0012": "150-10-0010",   # 6+1 SAĞ/SOL, aynı ham profil
    "151-10-0013": "150-10-0007", "151-10-0014": "150-10-0007",   # C 2+2 SAĞ/SOL
    "151-10-0015": "150-10-0007", "151-10-0016": "150-10-0007",   # C 3+3
    "151-10-0017": "150-10-0007", "151-10-0018": "150-10-0007",   # C 4+4
    "151-10-0019": "150-10-0007", "151-10-0020": "150-10-0007",   # C 5+5
    "151-10-0021": "150-10-0007", "151-10-0022": "150-10-0007",   # C 6+6
    "151-10-0023": "150-10-0007", "151-10-0024": "150-10-0007",   # C 7+7
}

URUN_ESLEME = [
    # (urun_kod, [(kesilmis_parca_kod, adet), ...])
    ("152-10-0001", [("151-10-0001", 1), ("151-10-0006", 2)]),            # A21
    ("152-10-0002", [("151-10-0002", 1), ("151-10-0007", 2)]),            # A31
    ("152-10-0003", [("151-10-0003", 1), ("151-10-0008", 2)]),            # A41
    ("152-10-0004", [("151-10-0004", 1), ("151-10-0009", 1), ("151-10-0010", 1)]),  # A51
    ("152-10-0005", [("151-10-0005", 1), ("151-10-0011", 1), ("151-10-0012", 1)]),  # A61
    ("152-22-0001", [("151-10-0013", 2), ("151-10-0014", 2)]),            # C22
    ("152-22-0002", [("151-10-0015", 2), ("151-10-0016", 2)]),            # C33
    ("152-22-0003", [("151-10-0017", 2), ("151-10-0018", 2)]),            # C44
    ("152-22-0004", [("151-10-0019", 2), ("151-10-0020", 2)]),            # C55
    ("152-22-0005", [("151-10-0021", 2), ("151-10-0022", 2)]),            # C66
    ("152-22-0006", [("151-10-0023", 2), ("151-10-0024", 2)]),            # C77
]


def seed(apps, schema_editor):
    Stok = apps.get_model("core", "Stok")
    FasonKesim = apps.get_model("core", "FasonKesim")

    for parca_kod, profil_kod in HAM_PROFIL_ESLEME.items():
        parca = Stok.objects.filter(kod=parca_kod, silindi=False).first()
        profil = Stok.objects.filter(kod=profil_kod, silindi=False).first()
        if not parca or not profil:
            continue
        if parca.kesildigi_profil_id != profil.pk:
            parca.kesildigi_profil = profil
            parca.save(update_fields=["kesildigi_profil"])

    sira = 0
    for urun_kod, parcalar in URUN_ESLEME:
        urun = Stok.objects.filter(kod=urun_kod, silindi=False).first()
        if not urun:
            continue
        for parca_kod, adet in parcalar:
            parca = Stok.objects.filter(kod=parca_kod, silindi=False).first()
            if not parca:
                continue
            sira += 1
            FasonKesim.objects.get_or_create(
                urun=urun, kesilmis_parca=parca, silindi=False,
                defaults={"adet": adet, "sira": sira * 10})


def geri_al(apps, schema_editor):
    FasonKesim = apps.get_model("core", "FasonKesim")
    Stok = apps.get_model("core", "Stok")
    for urun_kod, _parcalar in URUN_ESLEME:
        FasonKesim.objects.filter(urun__kod=urun_kod).delete()
    Stok.objects.filter(kod__in=HAM_PROFIL_ESLEME.keys()).update(kesildigi_profil=None)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0086_fason_kesim_restructure"),
    ]

    operations = [
        migrations.RunPython(seed, geri_al),
    ]
